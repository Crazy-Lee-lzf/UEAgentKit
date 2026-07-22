#include "BlueprintPatchCommandlet.h"

#include "BlueprintContextSha256.h"
#include "Components/ActorComponent.h"
#include "Dom/JsonObject.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "EdGraphSchema_K2.h"
#include "Engine/Blueprint.h"
#include "Engine/SCS_Node.h"
#include "Engine/SimpleConstructionScript.h"
#include "HAL/FileManager.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Kismet2/KismetEditorUtilities.h"
#include "Misc/App.h"
#include "Misc/DateTime.h"
#include "Misc/FileHelper.h"
#include "Misc/PackageName.h"
#include "Misc/Parse.h"
#include "Misc/Paths.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"
#include "UObject/UnrealType.h"

DEFINE_LOG_CATEGORY_STATIC(LogBlueprintPatch, Log, All);

namespace BlueprintPatchCommandletPrivate
{
	struct FPatchPolicy
	{
		bool bCommitEnabled = false;
		bool bRejectDirtyPackages = true;
		TArray<FString> AllowedProjectNames;
		TArray<FString> AllowedAssetRoots;
		TArray<FString> AllowedOperations;
		TArray<FString> AllowedAssetClasses;
	};

	enum class EResolvedTargetKind : uint8
	{
		Property,
		Pin
	};

	struct FResolvedTarget
	{
		EResolvedTargetKind Kind = EResolvedTargetKind::Property;
		UObject* OwnerObject = nullptr;
		FProperty* Property = nullptr;
		void* ValueAddress = nullptr;
		UEdGraphPin* Pin = nullptr;
		FString TypeName;
		FString Description;
	};

	bool LoadJsonObject(const FString& Filename, TSharedPtr<FJsonObject>& OutObject, FString& OutError)
	{
		FString JsonText;
		if (!FFileHelper::LoadFileToString(JsonText, *Filename))
		{
			OutError = FString::Printf(TEXT("Could not read JSON file: %s"), *Filename);
			return false;
		}

		const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(JsonText);
		if (!FJsonSerializer::Deserialize(Reader, OutObject) || !OutObject.IsValid())
		{
			OutError = FString::Printf(TEXT("Invalid JSON object: %s"), *Filename);
			return false;
		}
		return true;
	}

	void ReadStringArray(
		const TSharedPtr<FJsonObject>& Object,
		const TCHAR* FieldName,
		TArray<FString>& OutValues)
	{
		const TArray<TSharedPtr<FJsonValue>>* Values = nullptr;
		if (!Object->TryGetArrayField(FieldName, Values) || !Values)
		{
			return;
		}

		for (const TSharedPtr<FJsonValue>& Value : *Values)
		{
			FString StringValue;
			if (Value.IsValid() && Value->TryGetString(StringValue))
			{
				OutValues.Add(StringValue);
			}
		}
	}

	FString NormalizeObjectPath(FString Path)
	{
		Path.TrimStartAndEndInline();
		Path.TrimQuotesInline();
		if (Path.IsEmpty())
		{
			return Path;
		}

		const int32 LastSlash = Path.Find(TEXT("/"), ESearchCase::CaseSensitive, ESearchDir::FromEnd);
		const int32 LastDot = Path.Find(TEXT("."), ESearchCase::CaseSensitive, ESearchDir::FromEnd);
		if (LastDot <= LastSlash)
		{
			Path += TEXT(".") + FPackageName::GetShortName(Path);
		}
		return Path;
	}

	FString GetPackageFilename(const UPackage* Package)
	{
		if (!Package)
		{
			return FString();
		}
		return FPackageName::LongPackageNameToFilename(
			Package->GetName(),
			FPackageName::GetAssetPackageExtension());
	}

	FString HashPackageFile(const UPackage* Package)
	{
		const FString Filename = GetPackageFilename(Package);
		FString Digest;
		if (!Filename.IsEmpty() && FBlueprintContextSha256::HashFile(Filename, Digest))
		{
			return TEXT("sha256:") + Digest;
		}
		return FString();
	}

	bool LoadPolicy(const FString& Filename, FPatchPolicy& OutPolicy, FString& OutError)
	{
		TSharedPtr<FJsonObject> PolicyObject;
		if (!LoadJsonObject(Filename, PolicyObject, OutError))
		{
			return false;
		}

		PolicyObject->TryGetBoolField(TEXT("commitEnabled"), OutPolicy.bCommitEnabled);
		PolicyObject->TryGetBoolField(TEXT("rejectDirtyPackages"), OutPolicy.bRejectDirtyPackages);
		ReadStringArray(PolicyObject, TEXT("allowedProjectNames"), OutPolicy.AllowedProjectNames);
		ReadStringArray(PolicyObject, TEXT("allowedAssetRoots"), OutPolicy.AllowedAssetRoots);
		ReadStringArray(PolicyObject, TEXT("allowedOperations"), OutPolicy.AllowedOperations);
		ReadStringArray(PolicyObject, TEXT("allowedAssetClasses"), OutPolicy.AllowedAssetClasses);

		for (FString& Root : OutPolicy.AllowedAssetRoots)
		{
			Root.RemoveFromEnd(TEXT("/"));
		}

		if (OutPolicy.AllowedProjectNames.IsEmpty())
		{
			OutError = TEXT("Policy must contain allowedProjectNames.");
			return false;
		}
		if (OutPolicy.AllowedAssetRoots.IsEmpty())
		{
			OutError = TEXT("Policy must contain allowedAssetRoots.");
			return false;
		}
		if (OutPolicy.AllowedOperations.IsEmpty())
		{
			OutError = TEXT("Policy must contain allowedOperations.");
			return false;
		}
		if (OutPolicy.AllowedAssetClasses.IsEmpty())
		{
			OutError = TEXT("Policy must contain allowedAssetClasses.");
			return false;
		}
		return true;
	}

	bool ContainsExact(const TArray<FString>& Values, const FString& Candidate)
	{
		return Values.ContainsByPredicate(
			[&Candidate](const FString& Value)
			{
				return Value.Equals(Candidate, ESearchCase::CaseSensitive);
			});
	}

	bool IsProjectAllowed(const FPatchPolicy& Policy)
	{
		return ContainsExact(Policy.AllowedProjectNames, FApp::GetProjectName());
	}

	bool IsAssetAllowed(const FPatchPolicy& Policy, const FString& ObjectPath)
	{
		FString PackagePath = ObjectPath;
		int32 DotIndex = INDEX_NONE;
		if (PackagePath.FindChar(TEXT('.'), DotIndex))
		{
			PackagePath.LeftInline(DotIndex, EAllowShrinking::No);
		}

		for (const FString& Root : Policy.AllowedAssetRoots)
		{
			if (PackagePath.Equals(Root, ESearchCase::CaseSensitive)
				|| PackagePath.StartsWith(Root + TEXT("/"), ESearchCase::CaseSensitive))
			{
				return true;
			}
		}
		return false;
	}

	bool SetPropertyFromJson(
		FProperty* Property,
		void* ValueAddress,
		const TSharedPtr<FJsonValue>& JsonValue,
		FString& OutError)
	{
		if (!Property || !ValueAddress || !JsonValue.IsValid())
		{
			OutError = TEXT("Property, value address, or JSON value is invalid.");
			return false;
		}

		if (FBoolProperty* BoolProperty = CastField<FBoolProperty>(Property))
		{
			bool Value = false;
			if (!JsonValue->TryGetBool(Value))
			{
				OutError = TEXT("Expected a JSON boolean.");
				return false;
			}
			BoolProperty->SetPropertyValue(ValueAddress, Value);
			return true;
		}

		if (FNumericProperty* NumericProperty = CastField<FNumericProperty>(Property))
		{
			double Value = 0.0;
			if (!JsonValue->TryGetNumber(Value) || !FMath::IsFinite(Value))
			{
				OutError = TEXT("Expected a finite JSON number.");
				return false;
			}
			if (NumericProperty->IsFloatingPoint())
			{
				NumericProperty->SetFloatingPointPropertyValue(ValueAddress, Value);
			}
			else if (NumericProperty->IsInteger())
			{
				NumericProperty->SetIntPropertyValue(ValueAddress, static_cast<int64>(Value));
			}
			else
			{
				OutError = TEXT("Unsupported numeric property type.");
				return false;
			}
			return true;
		}

		FString StringValue;
		if (FStrProperty* StringProperty = CastField<FStrProperty>(Property))
		{
			if (!JsonValue->TryGetString(StringValue))
			{
				OutError = TEXT("Expected a JSON string.");
				return false;
			}
			StringProperty->SetPropertyValue(ValueAddress, StringValue);
			return true;
		}
		if (FNameProperty* NameProperty = CastField<FNameProperty>(Property))
		{
			if (!JsonValue->TryGetString(StringValue))
			{
				OutError = TEXT("Expected a JSON string.");
				return false;
			}
			NameProperty->SetPropertyValue(ValueAddress, FName(*StringValue));
			return true;
		}
		if (FTextProperty* TextProperty = CastField<FTextProperty>(Property))
		{
			if (!JsonValue->TryGetString(StringValue))
			{
				OutError = TEXT("Expected a JSON string.");
				return false;
			}
			TextProperty->SetPropertyValue(ValueAddress, FText::FromString(StringValue));
			return true;
		}

		OutError = FString::Printf(TEXT("Unsupported property type: %s"), *Property->GetClass()->GetName());
		return false;
	}

	bool JsonValueToPinDefault(
		const TSharedPtr<FJsonValue>& JsonValue,
		FString& OutDefaultValue,
		FString& OutError)
	{
		if (!JsonValue.IsValid())
		{
			OutError = TEXT("Pin value is missing.");
			return false;
		}

		if (JsonValue->Type == EJson::Boolean)
		{
			bool Value = false;
			JsonValue->TryGetBool(Value);
			OutDefaultValue = Value ? TEXT("true") : TEXT("false");
			return true;
		}
		if (JsonValue->Type == EJson::Number)
		{
			double Value = 0.0;
			if (!JsonValue->TryGetNumber(Value) || !FMath::IsFinite(Value))
			{
				OutError = TEXT("Expected a finite pin number.");
				return false;
			}
			OutDefaultValue = FString::Printf(TEXT("%.17g"), Value);
			return true;
		}
		if (JsonValue->Type == EJson::String)
		{
			return JsonValue->TryGetString(OutDefaultValue);
		}

		OutError = TEXT("Pin defaults currently support JSON boolean, number, and string values.");
		return false;
	}

	bool CompileBlueprint(UBlueprint* Blueprint, FString& OutError)
	{
		FKismetEditorUtilities::CompileBlueprint(Blueprint, EBlueprintCompileOptions::SkipGarbageCollection);
		if (Blueprint->Status == BS_Error)
		{
			OutError = TEXT("Blueprint compilation failed.");
			return false;
		}
		return true;
	}

	bool ResolvePropertyPath(
		UObject* OwnerObject,
		const FString& PropertyPath,
		FProperty*& OutProperty,
		void*& OutValueAddress,
		FString& OutError)
	{
		if (!OwnerObject || PropertyPath.IsEmpty())
		{
			OutError = TEXT("Property owner or property path is invalid.");
			return false;
		}

		TArray<FString> Segments;
		PropertyPath.ParseIntoArray(Segments, TEXT("."), true);
		if (Segments.IsEmpty())
		{
			OutError = TEXT("Property path is empty.");
			return false;
		}

		UStruct* CurrentStruct = OwnerObject->GetClass();
		void* CurrentContainer = OwnerObject;
		for (int32 Index = 0; Index < Segments.Num(); ++Index)
		{
			FProperty* Property = FindFProperty<FProperty>(CurrentStruct, FName(*Segments[Index]));
			if (!Property)
			{
				OutError = FString::Printf(
					TEXT("Property path segment was not found: %s"),
					*Segments[Index]);
				return false;
			}

			void* ValueAddress = Property->ContainerPtrToValuePtr<void>(CurrentContainer);
			if (Index == Segments.Num() - 1)
			{
				OutProperty = Property;
				OutValueAddress = ValueAddress;
				return true;
			}

			FStructProperty* StructProperty = CastField<FStructProperty>(Property);
			if (!StructProperty)
			{
				OutError = FString::Printf(
					TEXT("Intermediate property is not a struct: %s"),
					*Segments[Index]);
				return false;
			}
			CurrentStruct = StructProperty->Struct;
			CurrentContainer = ValueAddress;
		}
		return false;
	}

	USCS_Node* FindSCSNode(UBlueprint* Blueprint, const FString& ComponentName)
	{
		if (!Blueprint || !Blueprint->SimpleConstructionScript)
		{
			return nullptr;
		}

		for (USCS_Node* Node : Blueprint->SimpleConstructionScript->GetAllNodes())
		{
			if (Node && Node->GetVariableName().ToString().Equals(ComponentName, ESearchCase::CaseSensitive))
			{
				return Node;
			}
		}
		return nullptr;
	}

	UEdGraphPin* FindGraphPin(
		UBlueprint* Blueprint,
		const FString& GraphGuidText,
		const FString& NodeGuidText,
		const FString& PinName,
		FString& OutError)
	{
		FGuid GraphGuid;
		FGuid NodeGuid;
		if (!FGuid::Parse(GraphGuidText, GraphGuid) || !FGuid::Parse(NodeGuidText, NodeGuid))
		{
			OutError = TEXT("graphGuid or nodeGuid is invalid.");
			return nullptr;
		}

		TArray<UEdGraph*> Graphs;
		Blueprint->GetAllGraphs(Graphs);
		for (UEdGraph* Graph : Graphs)
		{
			if (!Graph || Graph->GraphGuid != GraphGuid)
			{
				continue;
			}

			for (UEdGraphNode* Node : Graph->Nodes)
			{
				if (!Node || Node->NodeGuid != NodeGuid)
				{
					continue;
				}

				for (UEdGraphPin* Pin : Node->Pins)
				{
					if (Pin && Pin->PinName.ToString().Equals(PinName, ESearchCase::CaseSensitive))
					{
						return Pin;
					}
				}
				OutError = FString::Printf(TEXT("Pin was not found: %s"), *PinName);
				return nullptr;
			}
			OutError = FString::Printf(TEXT("Node was not found: %s"), *NodeGuidText);
			return nullptr;
		}

		OutError = FString::Printf(TEXT("Graph was not found: %s"), *GraphGuidText);
		return nullptr;
	}

	bool ResolveTarget(
		UBlueprint* Blueprint,
		const FString& Operation,
		const TSharedPtr<FJsonObject>& TargetObject,
		FResolvedTarget& OutTarget,
		FString& OutError)
	{
		if (!Blueprint || !TargetObject.IsValid())
		{
			OutError = TEXT("Blueprint or operation target is invalid.");
			return false;
		}

		if (Operation.Equals(TEXT("setBlueprintDescription"), ESearchCase::CaseSensitive))
		{
			FProperty* Property = FindFProperty<FProperty>(
				UBlueprint::StaticClass(),
				GET_MEMBER_NAME_CHECKED(UBlueprint, BlueprintDescription));
			if (!Property)
			{
				OutError = TEXT("BlueprintDescription property is unavailable.");
				return false;
			}

			OutTarget.Kind = EResolvedTargetKind::Property;
			OutTarget.OwnerObject = Blueprint;
			OutTarget.Property = Property;
			OutTarget.ValueAddress = Property->ContainerPtrToValuePtr<void>(Blueprint);
			OutTarget.TypeName = Property->GetClass()->GetName();
			OutTarget.Description = TEXT("blueprint:description");
			return true;
		}

		if (Operation.Equals(TEXT("setVariableDefault"), ESearchCase::CaseSensitive))
		{
			FString VariableName;
			TargetObject->TryGetStringField(TEXT("variableName"), VariableName);
			FProperty* Property = FindFProperty<FProperty>(Blueprint->GeneratedClass, FName(*VariableName));
			UObject* DefaultObject = Blueprint->GeneratedClass->GetDefaultObject();
			if (!Property || !DefaultObject)
			{
				OutError = FString::Printf(TEXT("Variable was not found: %s"), *VariableName);
				return false;
			}
			if (!FBlueprintEditorUtils::IsVariableCreatedByBlueprint(Blueprint, Property))
			{
				OutError = TEXT("Only variables declared by the target Blueprint can be changed.");
				return false;
			}

			OutTarget.Kind = EResolvedTargetKind::Property;
			OutTarget.OwnerObject = DefaultObject;
			OutTarget.Property = Property;
			OutTarget.ValueAddress = Property->ContainerPtrToValuePtr<void>(DefaultObject);
			OutTarget.TypeName = Property->GetClass()->GetName();
			OutTarget.Description = TEXT("variable:") + VariableName;
			return true;
		}

		if (Operation.Equals(TEXT("setComponentProperty"), ESearchCase::CaseSensitive))
		{
			FString ComponentName;
			FString PropertyPath;
			TargetObject->TryGetStringField(TEXT("componentName"), ComponentName);
			TargetObject->TryGetStringField(TEXT("propertyPath"), PropertyPath);

			USCS_Node* Node = FindSCSNode(Blueprint, ComponentName);
			if (!Node || !Node->ComponentTemplate)
			{
				OutError = FString::Printf(TEXT("SCS component was not found: %s"), *ComponentName);
				return false;
			}

			FProperty* Property = nullptr;
			void* ValueAddress = nullptr;
			if (!ResolvePropertyPath(Node->ComponentTemplate, PropertyPath, Property, ValueAddress, OutError))
			{
				return false;
			}

			OutTarget.Kind = EResolvedTargetKind::Property;
			OutTarget.OwnerObject = Node->ComponentTemplate;
			OutTarget.Property = Property;
			OutTarget.ValueAddress = ValueAddress;
			OutTarget.TypeName = Property->GetClass()->GetName();
			OutTarget.Description = FString::Printf(
				TEXT("component:%s.%s"),
				*ComponentName,
				*PropertyPath);
			return true;
		}

		if (Operation.Equals(TEXT("setPinDefault"), ESearchCase::CaseSensitive))
		{
			FString GraphGuid;
			FString NodeGuid;
			FString PinName;
			TargetObject->TryGetStringField(TEXT("graphGuid"), GraphGuid);
			TargetObject->TryGetStringField(TEXT("nodeGuid"), NodeGuid);
			TargetObject->TryGetStringField(TEXT("pinName"), PinName);

			UEdGraphPin* Pin = FindGraphPin(Blueprint, GraphGuid, NodeGuid, PinName, OutError);
			if (!Pin)
			{
				return false;
			}
			if (Pin->Direction != EGPD_Input)
			{
				OutError = TEXT("Only input pin defaults can be changed.");
				return false;
			}
			if (!Pin->LinkedTo.IsEmpty())
			{
				OutError = TEXT("Connected pins cannot receive a default value.");
				return false;
			}
			if (Pin->bDefaultValueIsReadOnly || Pin->bDefaultValueIsIgnored)
			{
				OutError = TEXT("The target pin does not accept an editable default value.");
				return false;
			}

			OutTarget.Kind = EResolvedTargetKind::Pin;
			OutTarget.Pin = Pin;
			OutTarget.TypeName = Pin->PinType.PinCategory.ToString();
			OutTarget.Description = FString::Printf(
				TEXT("pin:%s/%s/%s"),
				*GraphGuid,
				*NodeGuid,
				*PinName);
			return true;
		}

		OutError = FString::Printf(TEXT("Unsupported operation: %s"), *Operation);
		return false;
	}

	bool ReadTargetValue(const FResolvedTarget& Target, FString& OutValue, FString& OutError)
	{
		if (Target.Kind == EResolvedTargetKind::Pin)
		{
			if (!Target.Pin)
			{
				OutError = TEXT("Resolved pin is invalid.");
				return false;
			}
			OutValue = Target.Pin->DefaultValue;
			return true;
		}

		if (!Target.OwnerObject || !Target.Property || !Target.ValueAddress)
		{
			OutError = TEXT("Resolved property is invalid.");
			return false;
		}
		Target.Property->ExportTextItem_Direct(
			OutValue,
			Target.ValueAddress,
			Target.ValueAddress,
			Target.OwnerObject,
			PPF_SerializedAsImportText);
		return true;
	}

	bool WriteTargetJsonValue(
		FResolvedTarget& Target,
		const TSharedPtr<FJsonValue>& JsonValue,
		FString& OutError)
	{
		if (Target.Kind == EResolvedTargetKind::Pin)
		{
			FString DefaultValue;
			if (!JsonValueToPinDefault(JsonValue, DefaultValue, OutError))
			{
				return false;
			}

			const UEdGraphSchema* Schema = Target.Pin ? Target.Pin->GetSchema() : nullptr;
			if (!Schema)
			{
				OutError = TEXT("Pin schema is unavailable.");
				return false;
			}
			Schema->TrySetDefaultValue(*Target.Pin, DefaultValue, true);
			if (!Target.Pin->DefaultValue.Equals(DefaultValue, ESearchCase::IgnoreCase))
			{
				OutError = FString::Printf(
					TEXT("Pin schema rejected or normalized the value unexpectedly. Requested=%s Actual=%s"),
					*DefaultValue,
					*Target.Pin->DefaultValue);
				return false;
			}
			return true;
		}

		Target.OwnerObject->Modify();
		if (!SetPropertyFromJson(Target.Property, Target.ValueAddress, JsonValue, OutError))
		{
			return false;
		}
		Target.OwnerObject->PostEditChange();
		return true;
	}

	bool WriteTargetTextValue(FResolvedTarget& Target, const FString& TextValue, FString& OutError)
	{
		if (Target.Kind == EResolvedTargetKind::Pin)
		{
			const UEdGraphSchema* Schema = Target.Pin ? Target.Pin->GetSchema() : nullptr;
			if (!Schema)
			{
				OutError = TEXT("Pin schema is unavailable during rollback.");
				return false;
			}
			Schema->TrySetDefaultValue(*Target.Pin, TextValue, true);
			if (!Target.Pin->DefaultValue.Equals(TextValue, ESearchCase::IgnoreCase))
			{
				OutError = TEXT("Pin rollback value was rejected by its schema.");
				return false;
			}
			return true;
		}

		Target.OwnerObject->Modify();
		const TCHAR* Result = Target.Property->ImportText_Direct(
			*TextValue,
			Target.ValueAddress,
			Target.OwnerObject,
			PPF_SerializedAsImportText);
		if (!Result)
		{
			OutError = TEXT("Failed to restore the original property value.");
			return false;
		}
		Target.OwnerObject->PostEditChange();
		return true;
	}

	bool SaveReport(const FString& Filename, const TSharedRef<FJsonObject>& Report, FString& OutError)
	{
		FString JsonText;
		const TSharedRef<TJsonWriter<TCHAR, TPrettyJsonPrintPolicy<TCHAR>>> Writer =
			TJsonWriterFactory<TCHAR, TPrettyJsonPrintPolicy<TCHAR>>::Create(&JsonText);
		if (!FJsonSerializer::Serialize(Report, Writer))
		{
			OutError = TEXT("Failed to serialize the patch report.");
			return false;
		}

		IFileManager::Get().MakeDirectory(*FPaths::GetPath(Filename), true);
		if (!FFileHelper::SaveStringToFile(
			JsonText,
			*Filename,
			FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
		{
			OutError = FString::Printf(TEXT("Failed to write report: %s"), *Filename);
			return false;
		}
		return true;
	}

	bool SaveBlueprintPackage(UBlueprint* Blueprint, const FString& Filename, FString& OutError)
	{
		FSavePackageArgs SaveArgs;
		SaveArgs.TopLevelFlags = RF_Public | RF_Standalone;
		SaveArgs.SaveFlags = SAVE_NoError;
		SaveArgs.Error = GError;
		if (!UPackage::SavePackage(Blueprint->GetOutermost(), Blueprint, *Filename, SaveArgs))
		{
			OutError = FString::Printf(TEXT("Failed to save package: %s"), *Filename);
			return false;
		}
		return true;
	}

	FString MakeSafeFilename(FString Value)
	{
		for (TCHAR& Character : Value)
		{
			if (Character == TEXT('/') || Character == TEXT('\\') || Character == TEXT(':')
				|| Character == TEXT('*') || Character == TEXT('?') || Character == TEXT('"')
				|| Character == TEXT('<') || Character == TEXT('>') || Character == TEXT('|'))
			{
				Character = TEXT('_');
			}
		}
		return Value.IsEmpty() ? TEXT("patch") : Value;
	}

	FString CreateBackupFilename(
		const FString& BackupDirectory,
		const FString& PatchId,
		const FString& PackageFilename)
	{
		return FPaths::Combine(
			BackupDirectory,
			FString::Printf(
				TEXT("%s_%lld_%s.bak"),
				*MakeSafeFilename(PatchId),
				FDateTime::UtcNow().GetTicks(),
				*FPaths::GetCleanFilename(PackageFilename)));
	}
}

UBlueprintPatchCommandlet::UBlueprintPatchCommandlet()
{
	IsClient = false;
	IsEditor = true;
	IsServer = false;
	LogToConsole = true;
	ShowErrorCount = true;
}

int32 UBlueprintPatchCommandlet::Main(const FString& Params)
{
	using namespace BlueprintPatchCommandletPrivate;

	FString PatchFilename;
	FString PolicyFilename;
	FString ReportFilename;
	FString BackupDirectory;
	FString Mode = TEXT("DryRun");
	FParse::Value(*Params, TEXT("Patch="), PatchFilename);
	FParse::Value(*Params, TEXT("Policy="), PolicyFilename);
	FParse::Value(*Params, TEXT("Report="), ReportFilename);
	FParse::Value(*Params, TEXT("BackupDir="), BackupDirectory);
	FParse::Value(*Params, TEXT("Mode="), Mode);

	PatchFilename = FPaths::ConvertRelativePathToFull(PatchFilename);
	PolicyFilename = FPaths::ConvertRelativePathToFull(PolicyFilename);
	ReportFilename = ReportFilename.IsEmpty()
		? FPaths::Combine(FPaths::ProjectSavedDir(), TEXT("UEAgentKit"), TEXT("patch-report.json"))
		: FPaths::ConvertRelativePathToFull(ReportFilename);
	BackupDirectory = BackupDirectory.IsEmpty()
		? FPaths::Combine(FPaths::ProjectSavedDir(), TEXT("UEAgentKit"), TEXT("Backups"))
		: FPaths::ConvertRelativePathToFull(BackupDirectory);

	const bool bCommit = Mode.Equals(TEXT("Commit"), ESearchCase::IgnoreCase);
	if (!bCommit && !Mode.Equals(TEXT("DryRun"), ESearchCase::IgnoreCase))
	{
		UE_LOG(LogBlueprintPatch, Error, TEXT("Mode must be DryRun or Commit."));
		return 1;
	}
	if (PatchFilename.IsEmpty() || PolicyFilename.IsEmpty())
	{
		UE_LOG(LogBlueprintPatch, Error, TEXT("Specify -Patch and -Policy."));
		return 1;
	}

	FString Error;
	FPatchPolicy Policy;
	if (!LoadPolicy(PolicyFilename, Policy, Error))
	{
		UE_LOG(LogBlueprintPatch, Error, TEXT("%s"), *Error);
		return 2;
	}
	if (!IsProjectAllowed(Policy))
	{
		UE_LOG(LogBlueprintPatch, Error, TEXT("Project is not authorized by policy."));
		return 3;
	}
	if (bCommit && !Policy.bCommitEnabled)
	{
		UE_LOG(LogBlueprintPatch, Error, TEXT("Commit is disabled by policy."));
		return 4;
	}

	TSharedPtr<FJsonObject> PatchObject;
	if (!LoadJsonObject(PatchFilename, PatchObject, Error))
	{
		UE_LOG(LogBlueprintPatch, Error, TEXT("%s"), *Error);
		return 5;
	}

	FString PatchId;
	FString PatchProjectName;
	PatchObject->TryGetStringField(TEXT("patchId"), PatchId);
	if (!PatchObject->TryGetStringField(TEXT("projectName"), PatchProjectName)
		|| !PatchProjectName.Equals(FApp::GetProjectName(), ESearchCase::CaseSensitive))
	{
		UE_LOG(LogBlueprintPatch, Error, TEXT("Patch projectName does not match the current project."));
		return 6;
	}

	const TArray<TSharedPtr<FJsonValue>>* AssetValues = nullptr;
	if (!PatchObject->TryGetArrayField(TEXT("assets"), AssetValues) || !AssetValues || AssetValues->Num() != 1)
	{
		UE_LOG(LogBlueprintPatch, Error, TEXT("Exactly one Blueprint asset is required per patch."));
		return 7;
	}

	const TSharedPtr<FJsonObject> AssetObject = (*AssetValues)[0]->AsObject();
	if (!AssetObject.IsValid())
	{
		UE_LOG(LogBlueprintPatch, Error, TEXT("Patch asset entry is invalid."));
		return 8;
	}

	FString AssetPath;
	FString ExpectedRevision;
	FString ExpectedAssetClass;
	AssetObject->TryGetStringField(TEXT("assetPath"), AssetPath);
	AssetObject->TryGetStringField(TEXT("expectedRevision"), ExpectedRevision);
	AssetObject->TryGetStringField(TEXT("expectedAssetClass"), ExpectedAssetClass);
	AssetPath = NormalizeObjectPath(AssetPath);
	if (!IsAssetAllowed(Policy, AssetPath))
	{
		UE_LOG(LogBlueprintPatch, Error, TEXT("Asset is outside authorized roots: %s"), *AssetPath);
		return 9;
	}

	UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *AssetPath);
	if (!Blueprint || !Blueprint->GeneratedClass)
	{
		UE_LOG(LogBlueprintPatch, Error, TEXT("Could not load Blueprint: %s"), *AssetPath);
		return 10;
	}

	const FString ActualAssetClass = Blueprint->GetClass()->GetPathName();
	if (!ExpectedAssetClass.Equals(ActualAssetClass, ESearchCase::CaseSensitive))
	{
		UE_LOG(
			LogBlueprintPatch,
			Error,
			TEXT("Asset class mismatch. Expected=%s Actual=%s"),
			*ExpectedAssetClass,
			*ActualAssetClass);
		return 11;
	}
	if (!ContainsExact(Policy.AllowedAssetClasses, ActualAssetClass))
	{
		UE_LOG(LogBlueprintPatch, Error, TEXT("Asset class is not authorized by policy: %s"), *ActualAssetClass);
		return 12;
	}

	UPackage* Package = Blueprint->GetOutermost();
	const bool bOriginalPackageDirty = Package->IsDirty();
	if (Policy.bRejectDirtyPackages && bOriginalPackageDirty)
	{
		UE_LOG(LogBlueprintPatch, Error, TEXT("Dirty packages are rejected by policy."));
		return 13;
	}

	const FString PackageFilename = GetPackageFilename(Package);
	const FString BeforeRevision = HashPackageFile(Package);
	if (ExpectedRevision.IsEmpty() || !ExpectedRevision.Equals(BeforeRevision, ESearchCase::IgnoreCase))
	{
		UE_LOG(
			LogBlueprintPatch,
			Error,
			TEXT("Revision conflict. Expected=%s Current=%s"),
			*ExpectedRevision,
			*BeforeRevision);
		return 14;
	}

	const TArray<TSharedPtr<FJsonValue>>* OperationValues = nullptr;
	if (!AssetObject->TryGetArrayField(TEXT("operations"), OperationValues)
		|| !OperationValues
		|| OperationValues->Num() != 1)
	{
		UE_LOG(LogBlueprintPatch, Error, TEXT("Exactly one operation is required per patch."));
		return 15;
	}

	const TSharedPtr<FJsonObject> OperationObject = (*OperationValues)[0]->AsObject();
	if (!OperationObject.IsValid())
	{
		UE_LOG(LogBlueprintPatch, Error, TEXT("Patch operation is invalid."));
		return 16;
	}

	FString Operation;
	OperationObject->TryGetStringField(TEXT("operation"), Operation);
	if (!ContainsExact(Policy.AllowedOperations, Operation))
	{
		UE_LOG(LogBlueprintPatch, Error, TEXT("Operation is not authorized by policy: %s"), *Operation);
		return 17;
	}
	if (!Operation.Equals(TEXT("setVariableDefault"), ESearchCase::CaseSensitive)
		&& !Operation.Equals(TEXT("setComponentProperty"), ESearchCase::CaseSensitive)
		&& !Operation.Equals(TEXT("setPinDefault"), ESearchCase::CaseSensitive)
		&& !Operation.Equals(TEXT("setBlueprintDescription"), ESearchCase::CaseSensitive))
	{
		UE_LOG(LogBlueprintPatch, Error, TEXT("Operation is not implemented: %s"), *Operation);
		return 18;
	}

	const TSharedPtr<FJsonObject>* TargetObjectPtr = nullptr;
	if (!OperationObject->TryGetObjectField(TEXT("target"), TargetObjectPtr)
		|| !TargetObjectPtr
		|| !TargetObjectPtr->IsValid())
	{
		UE_LOG(LogBlueprintPatch, Error, TEXT("Operation target is invalid."));
		return 19;
	}
	const TSharedPtr<FJsonObject> TargetObject = *TargetObjectPtr;

	const TSharedPtr<FJsonValue> NewValue = OperationObject->TryGetField(TEXT("value"));
	if (!NewValue.IsValid())
	{
		UE_LOG(LogBlueprintPatch, Error, TEXT("Operation value is missing."));
		return 20;
	}

	FResolvedTarget Target;
	if (!ResolveTarget(Blueprint, Operation, TargetObject, Target, Error))
	{
		UE_LOG(LogBlueprintPatch, Error, TEXT("%s"), *Error);
		return 21;
	}

	FString BeforeValue;
	if (!ReadTargetValue(Target, BeforeValue, Error))
	{
		UE_LOG(LogBlueprintPatch, Error, TEXT("%s"), *Error);
		return 22;
	}

	FString BackupFilename;
	if (bCommit)
	{
		IFileManager::Get().MakeDirectory(*BackupDirectory, true);
		BackupFilename = CreateBackupFilename(BackupDirectory, PatchId, PackageFilename);
		if (IFileManager::Get().Copy(*BackupFilename, *PackageFilename, true, true) != COPY_OK)
		{
			UE_LOG(LogBlueprintPatch, Error, TEXT("Could not create package backup: %s"), *BackupFilename);
			return 23;
		}
	}

	if (!WriteTargetJsonValue(Target, NewValue, Error))
	{
		UE_LOG(LogBlueprintPatch, Error, TEXT("%s"), *Error);
		return 24;
	}
	FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
	if (!CompileBlueprint(Blueprint, Error))
	{
		FResolvedTarget RollbackTarget;
		if (ResolveTarget(Blueprint, Operation, TargetObject, RollbackTarget, Error))
		{
			WriteTargetTextValue(RollbackTarget, BeforeValue, Error);
			FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
			CompileBlueprint(Blueprint, Error);
		}
		Package->SetDirtyFlag(bOriginalPackageDirty);
		UE_LOG(LogBlueprintPatch, Error, TEXT("Blueprint compilation failed; in-memory change was rolled back."));
		return 25;
	}

	FResolvedTarget CompiledTarget;
	if (!ResolveTarget(Blueprint, Operation, TargetObject, CompiledTarget, Error))
	{
		UE_LOG(LogBlueprintPatch, Error, TEXT("Target could not be resolved after compilation: %s"), *Error);
		return 26;
	}

	FString AfterValue;
	if (!ReadTargetValue(CompiledTarget, AfterValue, Error))
	{
		UE_LOG(LogBlueprintPatch, Error, TEXT("%s"), *Error);
		return 27;
	}

	bool bSaved = false;
	bool bRolledBack = false;
	FString RestoredValue;
	if (bCommit)
	{
		if (!SaveBlueprintPackage(Blueprint, PackageFilename, Error))
		{
			IFileManager::Get().Copy(*PackageFilename, *BackupFilename, true, true);
			UE_LOG(LogBlueprintPatch, Error, TEXT("%s Backup restored."), *Error);
			return 28;
		}
		bSaved = true;
	}
	else
	{
		if (!WriteTargetTextValue(CompiledTarget, BeforeValue, Error))
		{
			UE_LOG(LogBlueprintPatch, Error, TEXT("Rollback failed: %s"), *Error);
			return 29;
		}
		FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
		if (!CompileBlueprint(Blueprint, Error))
		{
			UE_LOG(LogBlueprintPatch, Error, TEXT("Rollback compilation failed: %s"), *Error);
			return 30;
		}
		Package->SetDirtyFlag(bOriginalPackageDirty);
		bRolledBack = true;

		FResolvedTarget RestoredTarget;
		if (!ResolveTarget(Blueprint, Operation, TargetObject, RestoredTarget, Error)
			|| !ReadTargetValue(RestoredTarget, RestoredValue, Error))
		{
			UE_LOG(LogBlueprintPatch, Error, TEXT("Rollback verification failed: %s"), *Error);
			return 31;
		}
	}

	const FString AfterRevision = HashPackageFile(Package);
	const TSharedRef<FJsonObject> Report = MakeShared<FJsonObject>();
	Report->SetStringField(TEXT("schemaVersion"), TEXT("1.0"));
	Report->SetStringField(TEXT("executorVersion"), TEXT("0.5.0"));
	Report->SetStringField(TEXT("mode"), bCommit ? TEXT("Commit") : TEXT("DryRun"));
	Report->SetStringField(TEXT("patchId"), PatchId);
	Report->SetStringField(TEXT("projectName"), FApp::GetProjectName());
	Report->SetStringField(TEXT("assetPath"), AssetPath);
	Report->SetStringField(TEXT("assetClass"), ActualAssetClass);
	Report->SetStringField(TEXT("operation"), Operation);
	Report->SetObjectField(TEXT("target"), TargetObject);
	Report->SetStringField(TEXT("targetDescription"), CompiledTarget.Description);
	Report->SetStringField(TEXT("targetType"), CompiledTarget.TypeName);
	Report->SetStringField(TEXT("beforeValue"), BeforeValue);
	Report->SetStringField(TEXT("afterValue"), AfterValue);
	Report->SetStringField(TEXT("restoredValue"), RestoredValue);
	Report->SetStringField(TEXT("beforeRevision"), BeforeRevision);
	Report->SetStringField(TEXT("afterRevision"), AfterRevision);
	Report->SetBoolField(TEXT("compiled"), true);
	Report->SetBoolField(TEXT("saved"), bSaved);
	Report->SetBoolField(TEXT("rolledBack"), bRolledBack);
	Report->SetBoolField(
		TEXT("rollbackValueMatch"),
		!bRolledBack || RestoredValue.Equals(BeforeValue, ESearchCase::IgnoreCase));
	Report->SetBoolField(TEXT("diskUnchanged"), BeforeRevision.Equals(AfterRevision, ESearchCase::IgnoreCase));
	Report->SetStringField(TEXT("backupPath"), BackupFilename);

	if (!SaveReport(ReportFilename, Report, Error))
	{
		UE_LOG(LogBlueprintPatch, Error, TEXT("%s"), *Error);
		return 32;
	}

	UE_LOG(
		LogBlueprintPatch,
		Display,
		TEXT("Blueprint patch succeeded. Mode=%s Asset=%s Target=%s Before=%s After=%s"),
		bCommit ? TEXT("Commit") : TEXT("DryRun"),
		*AssetPath,
		*CompiledTarget.Description,
		*BeforeValue,
		*AfterValue);
	return 0;
}
