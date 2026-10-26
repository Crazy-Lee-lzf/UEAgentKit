#include "BlueprintVariableWriteExperimental.h"

#include "BlueprintContextSha256.h"
#include "BlueprintGraphLayoutApply.h"

#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "EdGraphSchema_K2.h"
#include "Editor.h"
#include "Engine/Blueprint.h"
#include "HAL/FileManager.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Misc/PackageName.h"
#include "ScopedTransaction.h"
#include "UObject/Class.h"
#include "UObject/SavePackage.h"
#include "UObject/UnrealType.h"

DEFINE_LOG_CATEGORY_STATIC(LogUEAgentKitBlueprintVariableExperimental, Log, All);

namespace
{
	constexpr int32 MaxVariables = 8;
	constexpr int32 MaxVariableName = 64;
	constexpr int32 MaxStringDefault = 1024;
	const TCHAR* const PreviewSchemaVersion = TEXT("ueak.blueprint-variable-preview/1.0");
	const TCHAR* const ApplySchemaVersion = TEXT("ueak.blueprint-variable-apply/1.0");
	const TCHAR* const SaveSchemaVersion = TEXT("ueak.blueprint-variable-save/1.0");
	const TCHAR* const VariablesFingerprintVersion = TEXT("ueak.blueprint-variable-state/1");

	bool bTestOverrideSet = false;
	bool bTestOverrideEnabled = false;

	struct FVariableTarget
	{
		FString Name;
		FString Type;
		FString DefaultValue;
		FName NameValue;
		FEdGraphPinType PinType;
		FGuid GuidAfter;
	};

	struct FVariablePlan
	{
		FString AssetPath;
		FString GraphGuid;
		FString PackageRevision;
		FString GraphFingerprint;
		FString VariablesFingerprintBefore;
		FString PlanId;
		int32 VariableCountBefore = 0;
		int32 NodeCount = 0;
		int32 LinkCount = 0;
		TArray<FVariableTarget> Targets;
	};

	struct FPlanContext
	{
		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = nullptr;
		TSharedPtr<FJsonObject> Preflight;
	};

	struct FVariableReceipt
	{
		FString AssetPath;
		FString PackageRevision;
		FString VariablesFingerprint;
		int32 VariableCount = 0;
		TArray<TPair<FString, FString>> CreatedVariables;
	};

	TMap<FString, FVariableReceipt> RecentVariableApplies;

	bool Fail(
		FString& OutErrorCode,
		FString& OutErrorMessage,
		const TCHAR* Code,
		const FString& Message)
	{
		OutErrorCode = Code;
		OutErrorMessage = Message;
		return false;
	}

	bool RejectUnknownParams(
		const TSharedPtr<FJsonObject>& Params,
		const TSet<FString>& Allowed,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		if (!Params.IsValid())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-variable-invalid-parameters"),
				TEXT("Blueprint variable parameters must be one JSON object."));
		}
		TArray<FString> Keys;
		Params->Values.GetKeys(Keys);
		for (const FString& Key : Keys)
		{
			if (!Allowed.Contains(Key))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("blueprint-variable-unknown-parameter"),
					FString::Printf(TEXT("Unknown blueprint variable parameter: %s"), *Key));
			}
		}
		return true;
	}

	bool ReadString(
		const TSharedPtr<FJsonObject>& Object,
		const TCHAR* Field,
		FString& OutValue,
		FString& OutErrorCode,
		FString& OutErrorMessage,
		const int32 MaxLength,
		const bool bAllowEmpty = false)
	{
		const TSharedPtr<FJsonValue> Value = Object->TryGetField(Field);
		if (!Value.IsValid() || Value->Type != EJson::String)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-variable-invalid-parameters"),
				FString::Printf(TEXT("%s must be one string."), Field));
		}
		OutValue = Value->AsString();
		if (OutValue.Len() > MaxLength || (!bAllowEmpty && OutValue.IsEmpty()))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-variable-invalid-parameters"),
				FString::Printf(
					TEXT("%s must be %sstring of at most %d characters."),
					Field,
					bAllowEmpty ? TEXT("a ") : TEXT("a non-empty "),
					MaxLength));
		}
		return true;
	}

	bool ReadCount(
		const TSharedPtr<FJsonObject>& Object,
		const TCHAR* Field,
		int32& OutValue,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		const TSharedPtr<FJsonValue> Value = Object->TryGetField(Field);
		if (!Value.IsValid() || Value->Type != EJson::Number)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-variable-invalid-parameters"),
				FString::Printf(TEXT("%s must be one number."), Field));
		}
		const double Number = Value->AsNumber();
		if (!FMath::IsFinite(Number) || Number < 0.0 || Number > 100000.0)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-variable-invalid-parameters"),
				FString::Printf(TEXT("%s must be a bounded non-negative integer."), Field));
		}
		const int32 Casted = static_cast<int32>(Number);
		if (static_cast<double>(Casted) != Number)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-variable-invalid-parameters"),
				FString::Printf(TEXT("%s must be one integer."), Field));
		}
		OutValue = Casted;
		return true;
	}

	bool IsSha256Text(const FString& Value)
	{
		if (!Value.StartsWith(TEXT("sha256:"), ESearchCase::CaseSensitive) || Value.Len() != 71)
		{
			return false;
		}
		for (int32 Index = 7; Index < Value.Len(); ++Index)
		{
			const TCHAR Character = Value[Index];
			const bool bHex =
				(Character >= TEXT('0') && Character <= TEXT('9'))
				|| (Character >= TEXT('a') && Character <= TEXT('f'));
			if (!bHex)
			{
				return false;
			}
		}
		return true;
	}

	bool IsTypeToken(const FString& Value, const TCHAR* Expected)
	{
		return Value.Equals(Expected, ESearchCase::CaseSensitive);
	}

	bool IsCanonicalVariableName(const FString& Name)
	{
		if (Name.IsEmpty() || Name.Len() > MaxVariableName)
		{
			return false;
		}
		const TCHAR First = Name[0];
		const bool bFirstOk =
			(First >= TEXT('A') && First <= TEXT('Z'))
			|| (First >= TEXT('a') && First <= TEXT('z'))
			|| First == TEXT('_');
		if (!bFirstOk)
		{
			return false;
		}
		for (int32 Index = 1; Index < Name.Len(); ++Index)
		{
			const TCHAR Character = Name[Index];
			const bool bOk =
				(Character >= TEXT('A') && Character <= TEXT('Z'))
				|| (Character >= TEXT('a') && Character <= TEXT('z'))
				|| (Character >= TEXT('0') && Character <= TEXT('9'))
				|| Character == TEXT('_');
			if (!bOk)
			{
				return false;
			}
		}
		return true;
	}

	bool IsCanonicalInteger(const FString& Value)
	{
		if (Value.IsEmpty() || Value.Equals(TEXT("-0"), ESearchCase::CaseSensitive))
		{
			return false;
		}
		int32 Index = 0;
		if (Value[0] == TEXT('-'))
		{
			Index = 1;
		}
		if (Index >= Value.Len())
		{
			return false;
		}
		if (Value[Index] == TEXT('0') && Value.Len() > Index + 1)
		{
			return false;
		}
		for (int32 Cursor = Index; Cursor < Value.Len(); ++Cursor)
		{
			const TCHAR Character = Value[Cursor];
			if (Character < TEXT('0') || Character > TEXT('9'))
			{
				return false;
			}
		}
		const int64 Number = FCString::Atoi64(*Value);
		return Number >= MIN_int32 && Number <= MAX_int32;
	}

	bool IsCanonicalDouble(const FString& Value)
	{
		if (Value.IsEmpty())
		{
			return false;
		}
		int32 Index = 0;
		if (Value[0] == TEXT('-'))
		{
			Index = 1;
		}
		if (Index >= Value.Len())
		{
			return false;
		}
		const int32 IntegerStart = Index;
		while (Index < Value.Len() && Value[Index] >= TEXT('0') && Value[Index] <= TEXT('9'))
		{
			++Index;
		}
		const int32 IntegerLength = Index - IntegerStart;
		if (IntegerLength <= 0)
		{
			return false;
		}
		if (Value[IntegerStart] == TEXT('0') && IntegerLength > 1)
		{
			return false;
		}
		if (Index < Value.Len())
		{
			if (Value[Index] != TEXT('.'))
			{
				return false;
			}
			++Index;
			const int32 FractionStart = Index;
			while (Index < Value.Len() && Value[Index] >= TEXT('0') && Value[Index] <= TEXT('9'))
			{
				++Index;
			}
			if (Index == FractionStart || Index != Value.Len())
			{
				return false;
			}
		}
		const double Number = FCString::Atod(*Value);
		if (!FMath::IsFinite(Number))
		{
			return false;
		}
		if (Number == 0.0 && Value[0] == TEXT('-'))
		{
			return false;
		}
		return true;
	}

	bool IsCanonicalStringDefault(const FString& Value)
	{
		if (Value.Len() > MaxStringDefault)
		{
			return false;
		}
		for (const TCHAR Character : Value)
		{
			if (Character < 0x20 || Character == 0x7F)
			{
				return false;
			}
		}
		return true;
	}

	bool CanonicalizeDefaultValue(
		const FString& Type,
		const FString& Value,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		if (IsTypeToken(Type, TEXT("bool")))
		{
			if (Value.Equals(TEXT("true"), ESearchCase::CaseSensitive)
				|| Value.Equals(TEXT("false"), ESearchCase::CaseSensitive))
			{
				return true;
			}
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-variable-value-invalid"),
				TEXT("Boolean default must be exactly 'true' or 'false'."));
		}
		if (IsTypeToken(Type, TEXT("int")))
		{
			if (IsCanonicalInteger(Value))
			{
				return true;
			}
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-variable-value-invalid"),
				TEXT("Integer default must be canonical signed 32-bit decimal."));
		}
		if (IsTypeToken(Type, TEXT("double")))
		{
			if (IsCanonicalDouble(Value))
			{
				return true;
			}
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-variable-value-invalid"),
				TEXT("Double default must be canonical finite decimal without exponent or negative zero."));
		}
		if (IsTypeToken(Type, TEXT("string")))
		{
			if (IsCanonicalStringDefault(Value))
			{
				return true;
			}
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-variable-value-invalid"),
				TEXT("String default must be at most 1024 characters without control characters."));
		}
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-variable-type-not-allowed"),
			TEXT("Supported variable types: bool, int, double, string."));
	}

	bool MakePinType(const FString& Type, FEdGraphPinType& OutType)
	{
		OutType = FEdGraphPinType();
		if (IsTypeToken(Type, TEXT("bool")))
		{
			OutType.PinCategory = UEdGraphSchema_K2::PC_Boolean;
			return true;
		}
		if (IsTypeToken(Type, TEXT("int")))
		{
			OutType.PinCategory = UEdGraphSchema_K2::PC_Int;
			return true;
		}
		if (IsTypeToken(Type, TEXT("double")))
		{
			OutType.PinCategory = UEdGraphSchema_K2::PC_Real;
			OutType.PinSubCategory = UEdGraphSchema_K2::PC_Double;
			return true;
		}
		if (IsTypeToken(Type, TEXT("string")))
		{
			OutType.PinCategory = UEdGraphSchema_K2::PC_String;
			return true;
		}
		return false;
	}

	FString DescribePinType(const FEdGraphPinType& Type)
	{
		FString Label;
		if (Type.PinCategory == UEdGraphSchema_K2::PC_Boolean)
		{
			Label = TEXT("bool");
		}
		else if (Type.PinCategory == UEdGraphSchema_K2::PC_Int)
		{
			Label = TEXT("int");
		}
		else if (
			(Type.PinCategory == UEdGraphSchema_K2::PC_Real
				&& Type.PinSubCategory == UEdGraphSchema_K2::PC_Double)
			|| Type.PinCategory == UEdGraphSchema_K2::PC_Double)
		{
			Label = TEXT("double");
		}
		else if (Type.PinCategory == UEdGraphSchema_K2::PC_String)
		{
			Label = TEXT("string");
		}
		else
		{
			Label = FString::Printf(
				TEXT("unsupported:%s:%s"),
				*Type.PinCategory.ToString(),
				*Type.PinSubCategory.ToString());
		}
		if (Type.ContainerType == EPinContainerType::Array)
		{
			Label += TEXT("[]");
		}
		else if (Type.ContainerType == EPinContainerType::Set)
		{
			Label += TEXT("{}");
		}
		else if (Type.ContainerType == EPinContainerType::Map)
		{
			Label += TEXT("{:}");
		}
		if (Type.bIsReference)
		{
			Label += TEXT("&");
		}
		if (Type.bIsConst)
		{
			Label += TEXT(":const");
		}
		if (Type.bIsWeakPointer)
		{
			Label += TEXT(":weak");
		}
		if (Type.PinSubCategoryObject != nullptr)
		{
			Label += TEXT(":") + Type.PinSubCategoryObject->GetPathName();
		}
		return Label;
	}

	bool MatchesDeclaredType(const FEdGraphPinType& Type, const FString& Declared)
	{
		if (Type.ContainerType != EPinContainerType::None
			|| Type.bIsReference
			|| Type.bIsConst
			|| Type.bIsWeakPointer
			|| Type.PinSubCategoryObject != nullptr)
		{
			return false;
		}
		if (IsTypeToken(Declared, TEXT("bool")))
		{
			return Type.PinCategory == UEdGraphSchema_K2::PC_Boolean;
		}
		if (IsTypeToken(Declared, TEXT("int")))
		{
			return Type.PinCategory == UEdGraphSchema_K2::PC_Int;
		}
		if (IsTypeToken(Declared, TEXT("double")))
		{
			return (Type.PinCategory == UEdGraphSchema_K2::PC_Real
					&& Type.PinSubCategory == UEdGraphSchema_K2::PC_Double)
				|| Type.PinCategory == UEdGraphSchema_K2::PC_Double;
		}
		if (IsTypeToken(Declared, TEXT("string")))
		{
			return Type.PinCategory == UEdGraphSchema_K2::PC_String;
		}
		return false;
	}

	FString AppendField(const FString& Base, const FString& Value)
	{
		return Base + FString::Printf(TEXT("%d:%s|"), Value.Len(), *Value);
	}

	FString HashTextOrEmpty(const FString& Text)
	{
		FString Digest;
		if (!FBlueprintContextSha256::HashText(Text, Digest))
		{
			return FString();
		}
		return TEXT("sha256:") + Digest;
	}

	FString ComputeVariablesFingerprint(const UBlueprint* Blueprint)
	{
		if (Blueprint == nullptr)
		{
			return FString();
		}
		FString Canonical = AppendField(FString(), VariablesFingerprintVersion);
		Canonical = AppendField(Canonical, FString::FromInt(Blueprint->NewVariables.Num()));
		for (const FBPVariableDescription& Variable : Blueprint->NewVariables)
		{
			Canonical = AppendField(
				Canonical,
				Variable.VarGuid.ToString(EGuidFormats::DigitsWithHyphensLower));
			Canonical = AppendField(Canonical, Variable.VarName.ToString());
			Canonical = AppendField(Canonical, DescribePinType(Variable.VarType));
			Canonical = AppendField(Canonical, Variable.DefaultValue);
			Canonical = AppendField(
				Canonical,
				FString::Printf(TEXT("%llu"), Variable.PropertyFlags));
		}
		return HashTextOrEmpty(Canonical);
	}

	TArray<FString> JsonObjectKeys(const TSharedPtr<FJsonObject>& Object)
	{
		TArray<FString> Keys;
		Object->Values.GetKeys(Keys);
		Keys.Sort();
		return Keys;
	}

	bool ParseTargets(
		const TSharedPtr<FJsonObject>& Params,
		TArray<FVariableTarget>& OutTargets,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		const TSharedPtr<FJsonValue> VariablesValue = Params->TryGetField(TEXT("variables"));
		if (!VariablesValue.IsValid() || VariablesValue->Type != EJson::Array)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-variable-invalid-variables"),
				TEXT("variables must be one JSON array."));
		}
		const TArray<TSharedPtr<FJsonValue>>& Items = VariablesValue->AsArray();
		if (Items.Num() < 1 || Items.Num() > MaxVariables)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-variable-invalid-variables"),
				FString::Printf(
					TEXT("variables must contain between 1 and %d objects."),
					MaxVariables));
		}
		TSet<FName> RequestNames;
		for (int32 Index = 0; Index < Items.Num(); ++Index)
		{
			if (!Items[Index].IsValid() || Items[Index]->Type != EJson::Object)
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("blueprint-variable-invalid-variables"),
					FString::Printf(TEXT("variables[%d] must be one object."), Index));
			}
			const TSharedPtr<FJsonObject> Item = Items[Index]->AsObject();
			for (const FString& Key : JsonObjectKeys(Item))
			{
				if (Key != TEXT("name") && Key != TEXT("type") && Key != TEXT("defaultValue"))
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("blueprint-variable-unknown-parameter"),
						FString::Printf(
							TEXT("variables[%d] contains unknown field: %s"),
							Index,
							*Key));
				}
			}

			FVariableTarget Target;
			if (!ReadString(
					Item,
					TEXT("name"),
					Target.Name,
					OutErrorCode,
					OutErrorMessage,
					MaxVariableName)
				|| !ReadString(
					Item,
					TEXT("type"),
					Target.Type,
					OutErrorCode,
					OutErrorMessage,
					16)
				|| !ReadString(
					Item,
					TEXT("defaultValue"),
					Target.DefaultValue,
					OutErrorCode,
					OutErrorMessage,
					MaxStringDefault,
					true))
			{
				return false;
			}
			if (!IsCanonicalVariableName(Target.Name))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("blueprint-variable-name-invalid"),
					FString::Printf(
						TEXT("variables[%d].name must be 1-%d ASCII letters, digits or '_' and start with a letter or '_'."),
						Index,
						MaxVariableName));
			}
			if (!MakePinType(Target.Type, Target.PinType))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("blueprint-variable-type-not-allowed"),
					TEXT("Supported variable types: bool, int, double, string."));
			}
			if (!CanonicalizeDefaultValue(
					Target.Type,
					Target.DefaultValue,
					OutErrorCode,
					OutErrorMessage))
			{
				return false;
			}
			Target.NameValue = FName(*Target.Name);
			if (RequestNames.Contains(Target.NameValue))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("blueprint-variable-duplicate-name"),
					FString::Printf(
						TEXT("variables[%d].name duplicates another requested variable: %s"),
						Index,
						*Target.Name));
			}
			RequestNames.Add(Target.NameValue);
			OutTargets.Add(MoveTemp(Target));
		}
		return true;
	}

	void CollectExistingVariableNames(const UBlueprint* Blueprint, TSet<FName>& OutNames)
	{
		if (Blueprint == nullptr)
		{
			return;
		}
		TSet<FName> ClassVariables;
		FBlueprintEditorUtils::GetClassVariableList(Blueprint, ClassVariables, true);
		OutNames.Append(ClassVariables);
		for (const FBPVariableDescription& Variable : Blueprint->NewVariables)
		{
			OutNames.Add(Variable.VarName);
		}
		if (UClass* ParentClass = Blueprint->ParentClass)
		{
			TArray<UBlueprint*> ParentBlueprints;
			UBlueprint::GetBlueprintHierarchyFromClass(ParentClass, ParentBlueprints);
			for (const UBlueprint* Parent : ParentBlueprints)
			{
				if (Parent == nullptr)
				{
					continue;
				}
				for (const FBPVariableDescription& Variable : Parent->NewVariables)
				{
					OutNames.Add(Variable.VarName);
				}
			}
			for (TFieldIterator<FProperty> PropertyIt(ParentClass, EFieldIteratorFlags::IncludeSuper);
				PropertyIt;
				++PropertyIt)
			{
				OutNames.Add(PropertyIt->GetFName());
			}
		}
	}

	int32 FindVariableIndex(const UBlueprint* Blueprint, const FName Name)
	{
		if (Blueprint == nullptr)
		{
			return INDEX_NONE;
		}
		for (int32 Index = 0; Index < Blueprint->NewVariables.Num(); ++Index)
		{
			if (Blueprint->NewVariables[Index].VarName == Name)
			{
				return Index;
			}
		}
		return INDEX_NONE;
	}

	int32 CountLinks(const UEdGraph* Graph)
	{
		if (Graph == nullptr)
		{
			return 0;
		}
		int32 Count = 0;
		for (const UEdGraphNode* Node : Graph->Nodes)
		{
			if (Node == nullptr)
			{
				continue;
			}
			for (const UEdGraphPin* Pin : Node->Pins)
			{
				if (Pin != nullptr)
				{
					Count += Pin->LinkedTo.Num();
				}
			}
		}
		return Count / 2;
	}

	bool BuildPlan(
		const TSharedPtr<FJsonObject>& Params,
		FVariablePlan& OutPlan,
		FPlanContext& OutContext,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		if (!ReadString(
				Params,
				TEXT("assetPath"),
				OutPlan.AssetPath,
				OutErrorCode,
				OutErrorMessage,
				512)
			|| !ParseTargets(Params, OutPlan.Targets, OutErrorCode, OutErrorMessage))
		{
			return false;
		}
		if (!UEAgentKitBlueprintVariableExperimental::IsExperimentalWriteAvailable())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-variable-experimental-disabled"),
				TEXT("Experimental Blueprint variable creation is not opted in for this project."));
		}
		if (GEditor == nullptr || GEditor->IsPlaySessionInProgress() || GEditor->PlayWorld != nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-variable-pie-active"),
				TEXT("Blueprint variable creation is unavailable while PIE or SIE is active."));
		}

		if (!UEAgentKitBlueprintGraphLayout::BuildFocusedEditorPreflight(
				OutContext.Preflight,
				OutErrorCode,
				OutErrorMessage))
		{
			return false;
		}
		if (!OutContext.Preflight->GetStringField(TEXT("assetPath"))
				.Equals(OutPlan.AssetPath, ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-variable-focused-blueprint-mismatch"),
				TEXT("The focused Blueprint editor is not showing the exact assetPath from the request."));
		}
		if (OutContext.Preflight->GetBoolField(TEXT("packageDirty")))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-variable-package-dirty"),
				TEXT("Blueprint variable creation requires a clean package before Apply."));
		}
		if (!OutContext.Preflight->GetBoolField(TEXT("editable")))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-variable-blueprint-not-editable"),
				TEXT("The focused Blueprint is not editable."));
		}

		OutPlan.GraphGuid = OutContext.Preflight->GetStringField(TEXT("graphGuid"));
		UEdGraph* Graph = nullptr;
		if (!UEAgentKitBlueprintGraphLayout::ResolveExactTarget(
				OutPlan.AssetPath,
				OutPlan.GraphGuid,
				OutContext.Blueprint,
				Graph,
				OutErrorCode,
				OutErrorMessage))
		{
			return false;
		}
		if (OutContext.Blueprint == nullptr || Graph == nullptr
			|| Graph->GetSchema() == nullptr
			|| !Graph->GetSchema()->IsA<UEdGraphSchema_K2>())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-variable-schema-not-supported"),
				TEXT("Blueprint variable creation supports only K2 Blueprint graphs."));
		}
		OutContext.Graph = Graph;

		OutPlan.PackageRevision = OutContext.Preflight->GetStringField(TEXT("packageRevision"));
		OutPlan.GraphFingerprint = OutContext.Preflight->GetStringField(TEXT("graphFingerprint"));
		if (!IsSha256Text(OutPlan.PackageRevision))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-variable-revision-unavailable"),
				TEXT("The exact Blueprint package needs a sha256 disk revision; save it to disk first."));
		}
		if (!OutPlan.GraphFingerprint.StartsWith(TEXT("sha1:"), ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-variable-fingerprint-unavailable"),
				TEXT("The focused graph fingerprint is unavailable."));
		}

		OutPlan.VariablesFingerprintBefore = ComputeVariablesFingerprint(OutContext.Blueprint);
		if (OutPlan.VariablesFingerprintBefore.IsEmpty())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-variable-fingerprint-unavailable"),
				TEXT("Could not fingerprint the current Blueprint variable state."));
		}
		OutPlan.VariableCountBefore = OutContext.Blueprint->NewVariables.Num();
		OutPlan.NodeCount = Graph->Nodes.Num();
		OutPlan.LinkCount = CountLinks(Graph);

		TSet<FName> ExistingNames;
		CollectExistingVariableNames(OutContext.Blueprint, ExistingNames);
		for (const FVariableTarget& Target : OutPlan.Targets)
		{
			if (ExistingNames.Contains(Target.NameValue)
				|| FindVariableIndex(OutContext.Blueprint, Target.NameValue) != INDEX_NONE)
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("blueprint-variable-duplicate-name"),
					FString::Printf(
						TEXT("The Blueprint already declares a variable named %s (including parent classes)."),
						*Target.Name));
			}
		}

		FString Canonical = AppendField(FString(), TEXT("ueak.blueprint-variable/1.0"));
		Canonical = AppendField(Canonical, OutPlan.AssetPath);
		Canonical = AppendField(Canonical, OutPlan.GraphGuid);
		Canonical = AppendField(Canonical, OutPlan.PackageRevision);
		Canonical = AppendField(Canonical, OutPlan.GraphFingerprint);
		Canonical = AppendField(Canonical, OutPlan.VariablesFingerprintBefore);
		Canonical = AppendField(Canonical, FString::FromInt(OutPlan.VariableCountBefore));
		Canonical = AppendField(Canonical, FString::FromInt(OutPlan.Targets.Num()));
		TArray<FVariableTarget> SortedTargets = OutPlan.Targets;
		SortedTargets.Sort([](const FVariableTarget& Left, const FVariableTarget& Right)
		{
			return Left.Name < Right.Name;
		});
		for (const FVariableTarget& Target : SortedTargets)
		{
			Canonical = AppendField(Canonical, Target.Name);
			Canonical = AppendField(Canonical, Target.Type);
			Canonical = AppendField(Canonical, Target.DefaultValue);
		}
		OutPlan.PlanId = HashTextOrEmpty(Canonical);
		if (OutPlan.PlanId.IsEmpty())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-variable-fingerprint-unavailable"),
				TEXT("Could not compute the Blueprint variable plan hash."));
		}
		UE_LOG(
			LogUEAgentKitBlueprintVariableExperimental,
			Display,
			TEXT("Variable plan asset=%s graph=%s revision=%s graphFingerprint=%s variablesFingerprint=%s variables=%d nodes=%d links=%d planId=%s"),
			*OutPlan.AssetPath,
			*OutPlan.GraphGuid,
			*OutPlan.PackageRevision,
			*OutPlan.GraphFingerprint,
			*OutPlan.VariablesFingerprintBefore,
			OutPlan.VariableCountBefore,
			OutPlan.NodeCount,
			OutPlan.LinkCount,
			*OutPlan.PlanId);
		return true;
	}

	TSharedRef<FJsonObject> MakeVariableJson(
		const FString& Name,
		const FString& Type,
		const FString& DefaultValue,
		const FGuid& Guid)
	{
		TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
		Item->SetStringField(TEXT("name"), Name);
		Item->SetStringField(TEXT("type"), Type);
		Item->SetStringField(TEXT("defaultValue"), DefaultValue);
		Item->SetStringField(
			TEXT("guid"),
			Guid.ToString(EGuidFormats::DigitsWithHyphensLower));
		return Item;
	}

	bool SameVariable(const FBPVariableDescription& Left, const FBPVariableDescription& Right)
	{
		return Left.VarGuid == Right.VarGuid
			&& Left.VarName == Right.VarName
			&& Left.DefaultValue.Equals(Right.DefaultValue, ESearchCase::CaseSensitive)
			&& Left.PropertyFlags == Right.PropertyFlags
			&& DescribePinType(Left.VarType) == DescribePinType(Right.VarType);
	}
}

bool UEAgentKitBlueprintVariableExperimental::IsExperimentalWriteAvailable()
{
	if (bTestOverrideSet)
	{
		return bTestOverrideEnabled;
	}
	if (GConfig == nullptr)
	{
		return false;
	}
	bool bEnabled = false;
	return GConfig->GetBool(
			TEXT("UEAgentKit"),
			TEXT("ExperimentalGraphPatchEdit"),
			bEnabled,
			GEngineIni)
		&& bEnabled;
}

void UEAgentKitBlueprintVariableExperimental::SetExperimentalWriteEnabledForTest(const bool bEnabled)
{
	bTestOverrideSet = true;
	bTestOverrideEnabled = bEnabled;
}

void UEAgentKitBlueprintVariableExperimental::ResetExperimentalWriteForTest()
{
	bTestOverrideSet = false;
	bTestOverrideEnabled = false;
}

bool UEAgentKitBlueprintVariableExperimental::TryPreviewExperimental(
	const TSharedPtr<FJsonObject>& Params,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	UE_LOG(
		LogUEAgentKitBlueprintVariableExperimental,
		Display,
		TEXT("TryPreviewExperimental received valid=%s fields=%d"),
		Params.IsValid() ? TEXT("true") : TEXT("false"),
		Params.IsValid() ? Params->Values.Num() : -1);

	if (!RejectUnknownParams(
			Params,
			{ TEXT("assetPath"), TEXT("variables") },
			OutErrorCode,
			OutErrorMessage))
	{
		return false;
	}

	FVariablePlan Plan;
	FPlanContext Context;
	if (!BuildPlan(Params, Plan, Context, OutErrorCode, OutErrorMessage))
	{
		return false;
	}

	TArray<TSharedPtr<FJsonValue>> RequestedVariables;
	for (const FVariableTarget& Target : Plan.Targets)
	{
		TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
		Item->SetStringField(TEXT("name"), Target.Name);
		Item->SetStringField(TEXT("type"), Target.Type);
		Item->SetStringField(TEXT("defaultValue"), Target.DefaultValue);
		RequestedVariables.Add(MakeShared<FJsonValueObject>(Item));
	}

	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("schemaVersion"), PreviewSchemaVersion);
	Result->SetStringField(TEXT("action"), TEXT("preview-experimental-blueprint-variables"));
	Result->SetBoolField(TEXT("experimental"), true);
	Result->SetBoolField(TEXT("readOnly"), true);
	Result->SetBoolField(TEXT("wroteBlueprint"), false);
	Result->SetBoolField(TEXT("savedPackage"), false);
	Result->SetBoolField(TEXT("compiled"), false);
	Result->SetBoolField(TEXT("skeletonRegenerated"), false);
	Result->SetBoolField(TEXT("requiresExplicitCompile"), true);
	Result->SetStringField(TEXT("source"), TEXT("focused-blueprint-editor"));
	Result->SetStringField(TEXT("assetPath"), Plan.AssetPath);
	Result->SetStringField(TEXT("graphGuid"), Plan.GraphGuid);
	Result->SetStringField(TEXT("packageRevision"), Plan.PackageRevision);
	Result->SetStringField(TEXT("graphFingerprint"), Plan.GraphFingerprint);
	Result->SetStringField(TEXT("variablesFingerprint"), Plan.VariablesFingerprintBefore);
	Result->SetNumberField(TEXT("variableCountBefore"), Plan.VariableCountBefore);
	Result->SetNumberField(TEXT("nodeCount"), Plan.NodeCount);
	Result->SetNumberField(TEXT("linkCount"), Plan.LinkCount);
	Result->SetNumberField(TEXT("createCount"), Plan.Targets.Num());
	Result->SetArrayField(TEXT("variables"), RequestedVariables);
	Result->SetStringField(TEXT("planId"), Plan.PlanId);
	Result->SetStringField(
		TEXT("requiredConfirmation"),
		FString(ApplyConfirmationPrefix) + Plan.PlanId);
	OutResult = Result;
	return true;
}

bool UEAgentKitBlueprintVariableExperimental::TryApplyExperimental(
	const TSharedPtr<FJsonObject>& Params,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	UE_LOG(
		LogUEAgentKitBlueprintVariableExperimental,
		Display,
		TEXT("TryApplyExperimental received valid=%s fields=%d"),
		Params.IsValid() ? TEXT("true") : TEXT("false"),
		Params.IsValid() ? Params->Values.Num() : -1);

	if (!RejectUnknownParams(
			Params,
			{ TEXT("assetPath"), TEXT("planId"), TEXT("variables"), TEXT("confirmation") },
			OutErrorCode,
			OutErrorMessage))
	{
		return false;
	}

	FString RequestedPlanId;
	FString Confirmation;
	if (!ReadString(
			Params,
			TEXT("planId"),
			RequestedPlanId,
			OutErrorCode,
			OutErrorMessage,
			80)
		|| !IsSha256Text(RequestedPlanId)
		|| !ReadString(
			Params,
			TEXT("confirmation"),
			Confirmation,
			OutErrorCode,
			OutErrorMessage,
			256))
	{
		if (OutErrorCode.IsEmpty())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-variable-invalid-identifier"),
				TEXT("planId must be one canonical sha256 plan hash."));
		}
		return false;
	}

	FVariablePlan Plan;
	FPlanContext Context;
	if (!BuildPlan(Params, Plan, Context, OutErrorCode, OutErrorMessage))
	{
		return false;
	}

	const FString ExpectedConfirmation = FString(ApplyConfirmationPrefix) + RequestedPlanId;
	if (!Confirmation.Equals(ExpectedConfirmation, ESearchCase::CaseSensitive))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-variable-confirmation-required"),
			FString::Printf(
				TEXT("Explicit Apply confirmation must be exactly %s."),
				*ExpectedConfirmation));
	}
	if (!Plan.PlanId.Equals(RequestedPlanId, ESearchCase::CaseSensitive))
	{
		UE_LOG(
			LogUEAgentKitBlueprintVariableExperimental,
			Warning,
			TEXT("Variable plan stale: requested=%s computed=%s revision=%s graphFingerprint=%s variablesFingerprint=%s"),
			*RequestedPlanId,
			*Plan.PlanId,
			*Plan.PackageRevision,
			*Plan.GraphFingerprint,
			*Plan.VariablesFingerprintBefore);
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-variable-plan-stale"),
			TEXT("The Blueprint variable plan is stale; run Preview again."));
	}

	UBlueprint* Blueprint = Context.Blueprint;
	UEdGraph* Graph = Context.Graph;
	TSet<FName> ExistingNames;
	CollectExistingVariableNames(Blueprint, ExistingNames);
	for (const FVariableTarget& Target : Plan.Targets)
	{
		if (ExistingNames.Contains(Target.NameValue))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-variable-duplicate-name"),
				FString::Printf(
					TEXT("The Blueprint already declares a variable named %s."),
					*Target.Name));
		}
	}

	const TArray<FBPVariableDescription> BeforeVariables = Blueprint->NewVariables;
	const FString FingerprintBefore = ComputeVariablesFingerprint(Blueprint);
	const int32 NodeCountBefore = Graph->Nodes.Num();
	const int32 LinkCountBefore = CountLinks(Graph);
	TArray<FGuid> CreatedGuids;
	CreatedGuids.Reserve(Plan.Targets.Num());

	{
		FScopedTransaction Transaction(
			FText::FromString(TEXT("UEAgentKit Create Blueprint Variables")));
		Blueprint->Modify();
		TArray<FName> AddedNames;
		auto Rollback = [Blueprint, &AddedNames]()
		{
			for (int32 Index = Blueprint->NewVariables.Num() - 1; Index >= 0; --Index)
			{
				if (AddedNames.Contains(Blueprint->NewVariables[Index].VarName))
				{
					Blueprint->NewVariables.RemoveAt(Index);
				}
			}
			AddedNames.Reset();
		};

		for (const FVariableTarget& Target : Plan.Targets)
		{
			if (FindVariableIndex(Blueprint, Target.NameValue) != INDEX_NONE)
			{
				Rollback();
				Transaction.Cancel();
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("blueprint-variable-duplicate-name"),
					FString::Printf(
						TEXT("The Blueprint already declares a variable named %s."),
						*Target.Name));
			}
			FBPVariableDescription Variable;
			Variable.VarName = Target.NameValue;
			Variable.VarGuid = FGuid::NewGuid();
			Variable.VarType = Target.PinType;
			Variable.FriendlyName = FName::NameToDisplayString(
				Target.Name,
				IsTypeToken(Target.Type, TEXT("bool")));
			Variable.PropertyFlags = CPF_Edit | CPF_BlueprintVisible | CPF_DisableEditOnInstance;
			Variable.ReplicationCondition = COND_None;
			Variable.Category = UEdGraphSchema_K2::VR_DefaultCategory;
			Variable.DefaultValue = Target.DefaultValue;
			if (IsTypeToken(Target.Type, TEXT("string")))
			{
				Variable.SetMetaData(TEXT("MultiLine"), TEXT("true"));
			}
			Blueprint->NewVariables.Add(Variable);
			AddedNames.Add(Target.NameValue);
			CreatedGuids.Add(Variable.VarGuid);
		}
		FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
	}

	const FString FingerprintAfter = ComputeVariablesFingerprint(Blueprint);
	const int32 VariableCountAfter = Blueprint->NewVariables.Num();
	const int32 ExpectedCountAfter = BeforeVariables.Num() + Plan.Targets.Num();
	auto FailReadback = [&OutErrorCode, &OutErrorMessage]() -> bool
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-variable-readback-failed"),
			TEXT("Post-Apply Blueprint variable readback failed."));
	};
	if (FingerprintAfter.IsEmpty() || VariableCountAfter != ExpectedCountAfter)
	{
		return FailReadback();
	}
	for (int32 Index = 0; Index < Plan.Targets.Num(); ++Index)
	{
		const FVariableTarget& Target = Plan.Targets[Index];
		const FBPVariableDescription& Created = Blueprint->NewVariables[BeforeVariables.Num() + Index];
		if (Created.VarName != Target.NameValue
			|| Created.VarGuid != CreatedGuids[Index]
			|| !MatchesDeclaredType(Created.VarType, Target.Type)
			|| !Created.DefaultValue.Equals(Target.DefaultValue, ESearchCase::CaseSensitive)
			|| (Created.PropertyFlags & (CPF_Edit | CPF_BlueprintVisible)) != (CPF_Edit | CPF_BlueprintVisible))
		{
			return FailReadback();
		}
		if (IsTypeToken(Target.Type, TEXT("string"))
			&& Created.FindMetaDataEntryIndexForKey(TEXT("MultiLine")) == INDEX_NONE)
		{
			return FailReadback();
		}
	}
	for (int32 Index = 0; Index < BeforeVariables.Num(); ++Index)
	{
		if (!SameVariable(BeforeVariables[Index], Blueprint->NewVariables[Index]))
		{
			return FailReadback();
		}
	}

	bool bUndoVerified = GEditor->UndoTransaction()
		&& ComputeVariablesFingerprint(Blueprint).Equals(FingerprintBefore, ESearchCase::CaseSensitive)
		&& Blueprint->NewVariables.Num() == BeforeVariables.Num();
	if (!bUndoVerified)
	{
		if (GEditor->Trans != nullptr)
		{
			GEditor->UndoTransaction();
		}
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-variable-undo-redo-failed"),
			TEXT("Undo did not restore the exact Blueprint variable state."));
	}
	bool bRedoVerified = GEditor->RedoTransaction()
		&& ComputeVariablesFingerprint(Blueprint).Equals(FingerprintAfter, ESearchCase::CaseSensitive)
		&& Blueprint->NewVariables.Num() == VariableCountAfter;
	if (bRedoVerified)
	{
		for (int32 Index = 0; Index < Plan.Targets.Num(); ++Index)
		{
			const FBPVariableDescription& Created = Blueprint->NewVariables[BeforeVariables.Num() + Index];
			if (Created.VarGuid != CreatedGuids[Index]
				|| !Created.DefaultValue.Equals(
					Plan.Targets[Index].DefaultValue,
					ESearchCase::CaseSensitive))
			{
				bRedoVerified = false;
				break;
			}
		}
	}
	if (!bRedoVerified)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-variable-undo-redo-failed"),
			TEXT("Redo did not restore the exact created Blueprint variables."));
	}

	TSharedPtr<FJsonObject> PostPreflight;
	if (!UEAgentKitBlueprintGraphLayout::BuildFocusedEditorPreflight(
			PostPreflight,
			OutErrorCode,
			OutErrorMessage))
	{
		return false;
	}
	if (!PostPreflight->GetBoolField(TEXT("packageDirty")))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-variable-readback-failed"),
			TEXT("The Blueprint package is not dirty after the variable Apply."));
	}
	if (!PostPreflight->GetStringField(TEXT("graphFingerprint"))
			.Equals(Plan.GraphFingerprint, ESearchCase::CaseSensitive)
		|| Graph->Nodes.Num() != NodeCountBefore
		|| CountLinks(Graph) != LinkCountBefore)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-variable-graph-changed"),
			TEXT("The focused graph changed while creating variables."));
	}

	if (RecentVariableApplies.Num() > 16)
	{
		RecentVariableApplies.Reset();
	}
	FVariableReceipt Receipt;
	Receipt.AssetPath = Plan.AssetPath;
	Receipt.PackageRevision = Plan.PackageRevision;
	Receipt.VariablesFingerprint = FingerprintAfter;
	Receipt.VariableCount = VariableCountAfter;
	for (int32 Index = 0; Index < Plan.Targets.Num(); ++Index)
	{
		Receipt.CreatedVariables.Emplace(
			Plan.Targets[Index].Name,
			CreatedGuids[Index].ToString(EGuidFormats::DigitsWithHyphensLower));
	}
	RecentVariableApplies.Add(Plan.PlanId, Receipt);

	TArray<TSharedPtr<FJsonValue>> CreatedVariables;
	for (int32 Index = 0; Index < Plan.Targets.Num(); ++Index)
	{
		CreatedVariables.Add(MakeShared<FJsonValueObject>(MakeVariableJson(
			Plan.Targets[Index].Name,
			Plan.Targets[Index].Type,
			Plan.Targets[Index].DefaultValue,
			CreatedGuids[Index])));
	}

	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("schemaVersion"), ApplySchemaVersion);
	Result->SetStringField(TEXT("action"), TEXT("apply-experimental-blueprint-variables"));
	Result->SetBoolField(TEXT("experimental"), true);
	Result->SetBoolField(TEXT("applied"), true);
	Result->SetBoolField(TEXT("singleTransaction"), true);
	Result->SetBoolField(TEXT("undoVerified"), bUndoVerified);
	Result->SetBoolField(TEXT("redoVerified"), bRedoVerified);
	Result->SetBoolField(TEXT("saved"), false);
	Result->SetBoolField(TEXT("compiled"), false);
	Result->SetBoolField(TEXT("skeletonRegenerated"), false);
	Result->SetBoolField(TEXT("requiresExplicitCompile"), true);
	Result->SetBoolField(TEXT("autoSave"), false);
	Result->SetStringField(TEXT("source"), TEXT("focused-blueprint-editor"));
	Result->SetStringField(TEXT("planId"), Plan.PlanId);
	Result->SetStringField(TEXT("assetPath"), Plan.AssetPath);
	Result->SetStringField(TEXT("graphGuid"), Plan.GraphGuid);
	Result->SetStringField(TEXT("confirmation"), Confirmation);
	Result->SetStringField(TEXT("packageRevision"), Plan.PackageRevision);
	Result->SetStringField(TEXT("graphFingerprintAfter"), Plan.GraphFingerprint);
	Result->SetStringField(TEXT("variablesFingerprintBefore"), FingerprintBefore);
	Result->SetStringField(TEXT("variablesFingerprintAfter"), FingerprintAfter);
	Result->SetNumberField(TEXT("variableCountBefore"), BeforeVariables.Num());
	Result->SetNumberField(TEXT("variableCountAfter"), VariableCountAfter);
	Result->SetNumberField(TEXT("createCount"), Plan.Targets.Num());
	Result->SetBoolField(TEXT("packageDirty"), true);
	Result->SetStringField(TEXT("blueprintStatus"), TEXT("dirty"));
	Result->SetNumberField(TEXT("nodeCount"), NodeCountBefore);
	Result->SetNumberField(TEXT("linkCount"), LinkCountBefore);
	Result->SetArrayField(TEXT("createdVariables"), CreatedVariables);
	Result->SetStringField(
		TEXT("requiredSaveConfirmation"),
		FString(SaveConfirmationPrefix) + Plan.PlanId);
	OutResult = Result;
	return true;
}

bool UEAgentKitBlueprintVariableExperimental::TrySaveExperimental(
	const TSharedPtr<FJsonObject>& Params,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	UE_LOG(
		LogUEAgentKitBlueprintVariableExperimental,
		Display,
		TEXT("TrySaveExperimental received valid=%s fields=%d"),
		Params.IsValid() ? TEXT("true") : TEXT("false"),
		Params.IsValid() ? Params->Values.Num() : -1);

	if (!RejectUnknownParams(
			Params,
			{
				TEXT("assetPath"),
				TEXT("planId"),
				TEXT("expectedVariablesFingerprint"),
				TEXT("expectedPackageRevision"),
				TEXT("expectedVariableCount"),
				TEXT("confirmation"),
			},
			OutErrorCode,
			OutErrorMessage))
	{
		return false;
	}

	FString AssetPath;
	FString PlanId;
	FString ExpectedFingerprint;
	FString ExpectedRevision;
	int32 ExpectedCount = 0;
	FString Confirmation;
	if (!ReadString(Params, TEXT("assetPath"), AssetPath, OutErrorCode, OutErrorMessage, 512)
		|| !ReadString(Params, TEXT("planId"), PlanId, OutErrorCode, OutErrorMessage, 80)
		|| !ReadString(
			Params,
			TEXT("expectedVariablesFingerprint"),
			ExpectedFingerprint,
			OutErrorCode,
			OutErrorMessage,
			71)
		|| !ReadString(
			Params,
			TEXT("expectedPackageRevision"),
			ExpectedRevision,
			OutErrorCode,
			OutErrorMessage,
			71)
		|| !ReadCount(
			Params,
			TEXT("expectedVariableCount"),
			ExpectedCount,
			OutErrorCode,
			OutErrorMessage)
		|| !ReadString(Params, TEXT("confirmation"), Confirmation, OutErrorCode, OutErrorMessage, 256))
	{
		return false;
	}
	if (!IsSha256Text(PlanId) || !IsSha256Text(ExpectedFingerprint) || !IsSha256Text(ExpectedRevision))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-variable-invalid-identifier"),
			TEXT("planId, expectedVariablesFingerprint and expectedPackageRevision must be canonical sha256 values."));
	}
	if (!UEAgentKitBlueprintVariableExperimental::IsExperimentalWriteAvailable())
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-variable-experimental-disabled"),
			TEXT("Experimental Blueprint variable Save is not opted in for this project."));
	}
	const FString ExpectedConfirmation = FString(SaveConfirmationPrefix) + PlanId;
	if (!Confirmation.Equals(ExpectedConfirmation, ESearchCase::CaseSensitive))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-variable-confirmation-required"),
			FString::Printf(
				TEXT("Explicit Save confirmation must be exactly %s."),
				*ExpectedConfirmation));
	}
	const FVariableReceipt* Receipt = RecentVariableApplies.Find(PlanId);
	if (Receipt == nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-variable-save-requires-apply"),
			TEXT("Save requires the exact successful Apply receipt in this Editor process."));
	}
	if (!Receipt->AssetPath.Equals(AssetPath, ESearchCase::CaseSensitive))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-variable-save-plan-mismatch"),
			TEXT("The Save assetPath does not match the Apply receipt."));
	}
	if (GEditor == nullptr || GEditor->IsPlaySessionInProgress() || GEditor->PlayWorld != nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-variable-pie-active"),
			TEXT("Blueprint variable Save is unavailable while PIE or SIE is active."));
	}

	TSharedPtr<FJsonObject> Preflight;
	if (!UEAgentKitBlueprintGraphLayout::BuildFocusedEditorPreflight(
			Preflight,
			OutErrorCode,
			OutErrorMessage))
	{
		return false;
	}
	if (!Preflight->GetStringField(TEXT("assetPath")).Equals(AssetPath, ESearchCase::CaseSensitive))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-variable-focused-blueprint-mismatch"),
			TEXT("The focused Blueprint editor is not showing the exact assetPath from the request."));
	}
	if (!Preflight->GetBoolField(TEXT("packageDirty")))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-variable-save-plan-mismatch"),
			TEXT("The Blueprint package has no unsaved Apply to save."));
	}
	if (!Preflight->GetStringField(TEXT("packageRevision"))
			.Equals(ExpectedRevision, ESearchCase::CaseSensitive)
		|| !Receipt->PackageRevision.Equals(ExpectedRevision, ESearchCase::CaseSensitive))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-variable-save-revision-mismatch"),
			TEXT("The on-disk Blueprint package revision changed after Apply."));
	}

	UBlueprint* Blueprint = nullptr;
	UEdGraph* Graph = nullptr;
	if (!UEAgentKitBlueprintGraphLayout::ResolveExactTarget(
			AssetPath,
			Preflight->GetStringField(TEXT("graphGuid")),
			Blueprint,
			Graph,
			OutErrorCode,
			OutErrorMessage))
	{
		return false;
	}
	if (Blueprint == nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-variable-asset-not-loaded"),
			TEXT("The exact Blueprint asset is not loaded."));
	}
	const FString CurrentFingerprint = ComputeVariablesFingerprint(Blueprint);
	if (!CurrentFingerprint.Equals(ExpectedFingerprint, ESearchCase::CaseSensitive)
		|| !Receipt->VariablesFingerprint.Equals(ExpectedFingerprint, ESearchCase::CaseSensitive))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-variable-save-plan-mismatch"),
			TEXT("The live Blueprint variable state no longer matches the Apply receipt."));
	}
	if (Blueprint->NewVariables.Num() != ExpectedCount || Receipt->VariableCount != ExpectedCount)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-variable-save-plan-mismatch"),
			TEXT("The live Blueprint variable count no longer matches the Apply receipt."));
	}
	for (const TPair<FString, FString>& Created : Receipt->CreatedVariables)
	{
		const int32 Index = FindVariableIndex(Blueprint, FName(*Created.Key));
		if (Index == INDEX_NONE
			|| !Blueprint->NewVariables[Index]
					.VarGuid.ToString(EGuidFormats::DigitsWithHyphensLower)
					.Equals(Created.Value, ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-variable-save-plan-mismatch"),
				TEXT("A created Blueprint variable was renamed, removed or recreated after Apply."));
		}
	}

	UPackage* Package = Blueprint->GetOutermost();
	if (Package == nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-variable-package-invalid"),
			TEXT("The exact Blueprint has no owner package."));
	}
	FString Filename;
	if (!FPackageName::TryConvertLongPackageNameToFilename(
			Package->GetName(),
			Filename,
			FPackageName::GetAssetPackageExtension()))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-variable-package-invalid"),
			TEXT("The exact Blueprint package filename cannot be resolved."));
	}
	if (!IFileManager::Get().FileExists(*Filename))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-variable-save-package-file-missing"),
			TEXT("The exact Blueprint package file does not exist on disk."));
	}
	const int64 FileSizeBefore = IFileManager::Get().FileSize(*Filename);
	FString DigestBefore;
	if (!FBlueprintContextSha256::HashFile(Filename, DigestBefore))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-variable-save-hash-failed"),
			TEXT("The Blueprint package could not be hashed before saving."));
	}

	FSavePackageArgs SaveArgs;
	SaveArgs.TopLevelFlags = RF_Public | RF_Standalone;
	SaveArgs.SaveFlags = SAVE_NoError;
	if (!UPackage::SavePackage(Package, Blueprint, *Filename, SaveArgs) || Package->IsDirty())
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-variable-save-failed"),
			TEXT("SavePackage did not confirm a clean saved package."));
	}
	FString AssetDigest;
	if (!FBlueprintContextSha256::HashFile(Filename, AssetDigest))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-variable-save-hash-failed"),
			TEXT("The saved Blueprint package could not be hashed."));
	}
	const int64 FileSizeAfter = IFileManager::Get().FileSize(*Filename);
	if (FileSizeAfter <= 0)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-variable-save-readback-failed"),
			TEXT("The saved Blueprint package file is empty."));
	}
	const FString PostSaveFingerprint = ComputeVariablesFingerprint(Blueprint);
	if (!PostSaveFingerprint.Equals(ExpectedFingerprint, ESearchCase::CaseSensitive)
		|| Blueprint->NewVariables.Num() != ExpectedCount)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-variable-save-readback-failed"),
			TEXT("The live Blueprint variable state changed while saving."));
	}

	TSharedPtr<FJsonObject> PostSavePreflight;
	if (!UEAgentKitBlueprintGraphLayout::BuildFocusedEditorPreflight(
			PostSavePreflight,
			OutErrorCode,
			OutErrorMessage))
	{
		return false;
	}
	if (PostSavePreflight->GetBoolField(TEXT("packageDirty")))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-variable-save-readback-failed"),
			TEXT("The Blueprint package is still dirty after saving."));
	}

	TArray<TSharedPtr<FJsonValue>> SavedVariables;
	for (const TPair<FString, FString>& Created : Receipt->CreatedVariables)
	{
		const int32 Index = FindVariableIndex(Blueprint, FName(*Created.Key));
		if (Index != INDEX_NONE)
		{
			const FBPVariableDescription& Saved = Blueprint->NewVariables[Index];
			SavedVariables.Add(MakeShared<FJsonValueObject>(MakeVariableJson(
				Saved.VarName.ToString(),
				DescribePinType(Saved.VarType),
				Saved.DefaultValue,
				Saved.VarGuid)));
		}
	}

	TSharedRef<FJsonObject> Readback = MakeShared<FJsonObject>();
	Readback->SetStringField(TEXT("assetPath"), AssetPath);
	Readback->SetStringField(TEXT("graphGuid"), Preflight->GetStringField(TEXT("graphGuid")));
	Readback->SetStringField(TEXT("assetSha256"), FString(TEXT("sha256:")) + AssetDigest);
	Readback->SetStringField(
		TEXT("packageRevision"),
		PostSavePreflight->GetStringField(TEXT("packageRevision")));
	Readback->SetStringField(TEXT("variablesFingerprint"), PostSaveFingerprint);
	Readback->SetNumberField(TEXT("variableCount"), Blueprint->NewVariables.Num());
	Readback->SetStringField(TEXT("sha256Before"), FString(TEXT("sha256:")) + DigestBefore);
	Readback->SetBoolField(TEXT("packageDirty"), false);
	Readback->SetBoolField(TEXT("independentReloadVerified"), false);

	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("schemaVersion"), SaveSchemaVersion);
	Result->SetStringField(TEXT("action"), TEXT("save-experimental-blueprint-variables"));
	Result->SetBoolField(TEXT("experimental"), true);
	Result->SetStringField(TEXT("assetPath"), AssetPath);
	Result->SetStringField(TEXT("graphGuid"), Preflight->GetStringField(TEXT("graphGuid")));
	Result->SetStringField(TEXT("planId"), PlanId);
	Result->SetStringField(TEXT("confirmation"), Confirmation);
	Result->SetStringField(TEXT("assetSha256"), FString(TEXT("sha256:")) + AssetDigest);
	Result->SetStringField(
		TEXT("packageRevision"),
		PostSavePreflight->GetStringField(TEXT("packageRevision")));
	Result->SetStringField(TEXT("variablesFingerprint"), PostSaveFingerprint);
	Result->SetNumberField(TEXT("variableCount"), Blueprint->NewVariables.Num());
	Result->SetNumberField(TEXT("createCount"), SavedVariables.Num());
	Result->SetBoolField(TEXT("saved"), true);
	Result->SetBoolField(TEXT("packageDirty"), false);
	Result->SetBoolField(TEXT("readbackVerified"), true);
	Result->SetBoolField(TEXT("independentReloadVerified"), false);
	Result->SetStringField(TEXT("requiredAcceptanceEvidence"), TEXT("independent-reload"));
	Result->SetNumberField(TEXT("fileSizeBefore"), static_cast<double>(FileSizeBefore));
	Result->SetNumberField(TEXT("fileSizeAfter"), static_cast<double>(FileSizeAfter));
	Result->SetBoolField(TEXT("autoSave"), false);
	Result->SetBoolField(TEXT("compiled"), false);
	Result->SetBoolField(TEXT("skeletonRegenerated"), false);
	Result->SetBoolField(TEXT("requiresExplicitCompile"), true);
	Result->SetArrayField(TEXT("variables"), SavedVariables);
	Result->SetObjectField(TEXT("readback"), Readback);
	RecentVariableApplies.Remove(PlanId);
	OutResult = Result;
	return true;
}
