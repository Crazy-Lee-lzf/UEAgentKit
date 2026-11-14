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
	constexpr int32 MaxVariables = UEAgentKitBlueprintVariableExperimental::MaxVariablesPerApply;
	constexpr int32 MaxVariableName = 64;
	constexpr int32 MaxStringDefault = 1024;
	const TCHAR* const PreviewSchemaVersion = TEXT("ueak.blueprint-variable-preview/1.0");
	const TCHAR* const ApplySchemaVersion = TEXT("ueak.blueprint-variable-apply/1.0");
	const TCHAR* const SaveSchemaVersion = TEXT("ueak.blueprint-variable-save/1.0");
	const TCHAR* const VariablesFingerprintVersion = TEXT("ueak.blueprint-variable-state/1");


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

	FBPVariableDescription MakeVariableDescription(
		const UEAgentKitBlueprintVariableExperimental::FAtomicVariableOperation& Operation)
	{
		FBPVariableDescription Variable;
		Variable.VarName = FName(*Operation.Name);
		Variable.VarGuid = FGuid::NewGuid();
		FEdGraphPinType PinType;
		MakePinType(Operation.Type, PinType);
		Variable.VarType = PinType;
		Variable.FriendlyName = FName::NameToDisplayString(
			Operation.Name,
			IsTypeToken(Operation.Type, TEXT("bool")));
		Variable.PropertyFlags = CPF_Edit | CPF_BlueprintVisible | CPF_DisableEditOnInstance;
		Variable.ReplicationCondition = COND_None;
		Variable.Category = UEdGraphSchema_K2::VR_DefaultCategory;
		Variable.DefaultValue = Operation.DefaultValue;
		if (IsTypeToken(Operation.Type, TEXT("string")))
		{
			Variable.SetMetaData(TEXT("MultiLine"), TEXT("true"));
		}
		return Variable;
	}
}

bool UEAgentKitBlueprintVariableExperimental::TryResolveAtomicVariableOperation(
	UBlueprint* Blueprint,
	FAtomicVariableOperation& InOutOperation,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	if (Blueprint == nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-variable-asset-not-loaded"),
			TEXT("The exact Blueprint must be loaded before a variable can be resolved."));
	}
	if (!IsCanonicalVariableName(InOutOperation.Name))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-variable-name-invalid"),
			FString::Printf(
				TEXT("name must be 1-%d ASCII letters, digits or '_' and start with a letter or '_'."),
				MaxVariableName));
	}
	FEdGraphPinType PinType;
	if (!MakePinType(InOutOperation.Type, PinType))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-variable-type-not-allowed"),
			TEXT("Supported variable types: bool, int, double, string."));
	}
	if (!CanonicalizeDefaultValue(
			InOutOperation.Type,
			InOutOperation.DefaultValue,
			OutErrorCode,
			OutErrorMessage))
	{
		return false;
	}
	// Same duplicate rule as the single writer's BuildPlan: own variables, class
	// variables, parent Blueprint variables and parent-class properties.
	TSet<FName> ExistingNames;
	CollectExistingVariableNames(Blueprint, ExistingNames);
	const FName NameValue(*InOutOperation.Name);
	InOutOperation.bExistedBefore =
		ExistingNames.Contains(NameValue) || FindVariableIndex(Blueprint, NameValue) != INDEX_NONE;
	if (InOutOperation.bExistedBefore)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-variable-duplicate-name"),
			FString::Printf(
				TEXT("The Blueprint already declares a variable named %s (including parent classes)."),
				*InOutOperation.Name));
	}
	return true;
}

bool UEAgentKitBlueprintVariableExperimental::TryApplyAtomicVariableOperations(
	UBlueprint* Blueprint,
	const TArray<FAtomicVariableOperation>& Operations,
	TArray<FGuid>& OutCreatedGuids,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	OutCreatedGuids.Reset();
	if (Blueprint == nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-variable-asset-not-loaded"),
			TEXT("The exact Blueprint must be loaded before a variable can be created."));
	}
	TArray<FName> AddedNames;
	auto RestoreApplied = [Blueprint, &AddedNames, &OutCreatedGuids]()
	{
		for (int32 Index = Blueprint->NewVariables.Num() - 1; Index >= 0; --Index)
		{
			if (AddedNames.Contains(Blueprint->NewVariables[Index].VarName))
			{
				Blueprint->NewVariables.RemoveAt(Index);
			}
		}
		AddedNames.Reset();
		// The seam restored everything it wrote itself, so the caller was left
		// with nothing applied.
		OutCreatedGuids.Reset();
	};

	for (const FAtomicVariableOperation& Operation : Operations)
	{
		const FName NameValue(*Operation.Name);
		if (FindVariableIndex(Blueprint, NameValue) != INDEX_NONE)
		{
			RestoreApplied();
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-variable-duplicate-name"),
				FString::Printf(
					TEXT("The Blueprint already declares a variable named %s."),
					*Operation.Name));
		}
		FEdGraphPinType PinType;
		if (!MakePinType(Operation.Type, PinType))
		{
			RestoreApplied();
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-variable-type-not-allowed"),
				TEXT("Supported variable types: bool, int, double, string."));
		}
		const FBPVariableDescription Variable = MakeVariableDescription(Operation);
		Blueprint->NewVariables.Add(Variable);
		AddedNames.Add(NameValue);
		const FBPVariableDescription& Added = Blueprint->NewVariables.Last();
		if (Added.VarName != NameValue
			|| Added.VarGuid != Variable.VarGuid
			|| !MatchesDeclaredType(Added.VarType, Operation.Type)
			|| !Added.DefaultValue.Equals(Operation.DefaultValue, ESearchCase::CaseSensitive))
		{
			RestoreApplied();
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-variable-apply-rejected"),
				FString::Printf(
					TEXT("The Blueprint did not store the requested variable %s."),
					*Operation.Name));
		}
		OutCreatedGuids.Add(Variable.VarGuid);
	}
	FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
	return true;
}

bool UEAgentKitBlueprintVariableExperimental::TryCheckAtomicVariableOperations(
	UBlueprint* Blueprint,
	const TArray<FAtomicVariableOperation>& Operations,
	bool bExpectAfter,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	if (Blueprint == nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-variable-asset-not-loaded"),
			TEXT("The exact Blueprint must be loaded before a variable can be read back."));
	}
	for (const FAtomicVariableOperation& Operation : Operations)
	{
		const int32 Index = FindVariableIndex(Blueprint, FName(*Operation.Name));
		if (!bExpectAfter)
		{
			if (Operation.bExistedBefore)
			{
				// The variable belongs to the Blueprint and predates this operation;
				// nothing about it is this layer's to check.
				continue;
			}
			if (Index != INDEX_NONE)
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("blueprint-variable-readback-failed"),
					FString::Printf(
						TEXT("Variable %s still exists, but it did not exist before."),
						*Operation.Name));
			}
			continue;
		}
		if (Index == INDEX_NONE)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-variable-readback-failed"),
				FString::Printf(
					TEXT("Variable %s does not exist after the write."),
					*Operation.Name));
		}
		const FBPVariableDescription& Variable = Blueprint->NewVariables[Index];
		if (!MatchesDeclaredType(Variable.VarType, Operation.Type))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-variable-readback-failed"),
				FString::Printf(
					TEXT("Variable %s reads back as %s; expected %s."),
					*Operation.Name,
					*DescribePinType(Variable.VarType),
					*Operation.Type));
		}
		if (!Variable.DefaultValue.Equals(Operation.DefaultValue, ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-variable-readback-failed"),
				FString::Printf(
					TEXT("Variable %s reads back with default '%s'; expected '%s'."),
					*Operation.Name,
					*Variable.DefaultValue,
					*Operation.DefaultValue));
		}
		if (IsTypeToken(Operation.Type, TEXT("string"))
			&& Variable.FindMetaDataEntryIndexForKey(TEXT("MultiLine")) == INDEX_NONE)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-variable-readback-failed"),
				FString::Printf(
					TEXT("String variable %s lost its MultiLine metadata."),
					*Operation.Name));
		}
	}
	return true;
}

bool UEAgentKitBlueprintVariableExperimental::TryRestoreAtomicVariableOperations(
	UBlueprint* Blueprint,
	const TArray<FAtomicVariableOperation>& Operations,
	int32 AppliedCount,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	const int32 Count = FMath::Clamp(AppliedCount, 0, Operations.Num());
	if (Blueprint == nullptr || Count == 0)
	{
		return true;
	}
	for (int32 Index = Count - 1; Index >= 0; --Index)
	{
		const FAtomicVariableOperation& Operation = Operations[Index];
		if (Operation.bExistedBefore)
		{
			// Never delete a variable this operation did not create.
			continue;
		}
		const FName NameValue(*Operation.Name);
		const int32 VariableIndex = FindVariableIndex(Blueprint, NameValue);
		if (VariableIndex == INDEX_NONE)
		{
			// Already gone: there is nothing left to delete for this operation.
			continue;
		}
		Blueprint->NewVariables.RemoveAt(VariableIndex);
		if (FindVariableIndex(Blueprint, NameValue) != INDEX_NONE)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-variable-restore-failed"),
				FString::Printf(TEXT("Variable %s could not be removed."), *Operation.Name));
		}
	}
	return true;
}

FString UEAgentKitBlueprintVariableExperimental::ComputeAtomicVariableStateFingerprint(
	UBlueprint* Blueprint)
{
	return ComputeVariablesFingerprint(Blueprint);
}
