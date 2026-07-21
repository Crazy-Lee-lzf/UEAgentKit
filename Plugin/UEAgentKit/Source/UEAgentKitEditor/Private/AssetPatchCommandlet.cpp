#include "AssetPatchCommandlet.h"

#include "BlueprintContextSha256.h"
#include "Dom/JsonObject.h"
#include "Engine/Blueprint.h"
#include "HAL/FileManager.h"
#include "MaterialEditingLibrary.h"
#include "Materials/MaterialInstanceConstant.h"
#include "Misc/App.h"
#include "Misc/DateTime.h"
#include "Misc/FileHelper.h"
#include "Misc/PackageName.h"
#include "Misc/PackagePath.h"
#include "Misc/PackageSegment.h"
#include "Misc/Parse.h"
#include "Misc/Paths.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"
#include "UObject/UnrealType.h"

DEFINE_LOG_CATEGORY_STATIC(LogAssetPatch, Log, All);

namespace AssetPatchCommandletPrivate
{
	struct FPatchPolicy
	{
		bool bCommitEnabled = false;
		bool bRejectDirtyPackages = true;
		TArray<FString> AllowedProjectNames;
		TArray<FString> AllowedAssetRoots;
		TArray<FString> AllowedOperations;
		TArray<FString> AllowedAssetClasses;
		TArray<FString> AllowedAssetProperties;
		TArray<FString> AllowedMaterialParameters;
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
			FString Text;
			if (Value.IsValid() && Value->TryGetString(Text))
			{
				OutValues.Add(Text);
			}
		}
	}

	bool ContainsExact(const TArray<FString>& Values, const FString& Candidate)
	{
		return Values.ContainsByPredicate(
			[&Candidate](const FString& Value)
			{
				return Value.Equals(Candidate, ESearchCase::CaseSensitive);
			});
	}

	bool LoadPolicy(const FString& Filename, FPatchPolicy& OutPolicy, FString& OutError)
	{
		TSharedPtr<FJsonObject> Object;
		if (!LoadJsonObject(Filename, Object, OutError))
		{
			return false;
		}

		Object->TryGetBoolField(TEXT("commitEnabled"), OutPolicy.bCommitEnabled);
		Object->TryGetBoolField(TEXT("rejectDirtyPackages"), OutPolicy.bRejectDirtyPackages);
		ReadStringArray(Object, TEXT("allowedProjectNames"), OutPolicy.AllowedProjectNames);
		ReadStringArray(Object, TEXT("allowedAssetRoots"), OutPolicy.AllowedAssetRoots);
		ReadStringArray(Object, TEXT("allowedOperations"), OutPolicy.AllowedOperations);
		ReadStringArray(Object, TEXT("allowedAssetClasses"), OutPolicy.AllowedAssetClasses);
		ReadStringArray(Object, TEXT("allowedAssetProperties"), OutPolicy.AllowedAssetProperties);
		ReadStringArray(Object, TEXT("allowedMaterialParameters"), OutPolicy.AllowedMaterialParameters);
		for (FString& Root : OutPolicy.AllowedAssetRoots)
		{
			Root.RemoveFromEnd(TEXT("/"));
			if (Root.Equals(TEXT("/Game"), ESearchCase::CaseSensitive)
				|| !Root.StartsWith(TEXT("/Game/"), ESearchCase::CaseSensitive)
				|| Root.Contains(TEXT("."))
				|| Root.Contains(TEXT("\\"))
				|| Root.Contains(TEXT("//")))
			{
				OutError = FString::Printf(TEXT("Policy asset root is invalid or too broad: %s"), *Root);
				return false;
			}
		}

		if (OutPolicy.AllowedProjectNames.IsEmpty()
			|| OutPolicy.AllowedAssetRoots.IsEmpty()
			|| OutPolicy.AllowedOperations.IsEmpty()
			|| OutPolicy.AllowedAssetClasses.IsEmpty())
		{
			OutError = TEXT("Policy authorization arrays must not be empty.");
			return false;
		}
		if (ContainsExact(OutPolicy.AllowedOperations, TEXT("setAssetProperty"))
			&& OutPolicy.AllowedAssetProperties.IsEmpty())
		{
			OutError = TEXT("setAssetProperty requires allowedAssetProperties authorization.");
			return false;
		}
		const bool bUsesMaterialParameterOperations =
			ContainsExact(OutPolicy.AllowedOperations, TEXT("setMaterialInstanceScalarParameter"))
			|| ContainsExact(OutPolicy.AllowedOperations, TEXT("setMaterialInstanceVectorParameter"));
		if (bUsesMaterialParameterOperations && OutPolicy.AllowedMaterialParameters.IsEmpty())
		{
			OutError = TEXT(
				"Material Instance parameter operations require allowedMaterialParameters authorization.");
			return false;
		}
		return true;
	}

	FString NormalizeObjectPath(FString Path)
	{
		Path.TrimStartAndEndInline();
		Path.TrimQuotesInline();
		const int32 LastSlash = Path.Find(TEXT("/"), ESearchCase::CaseSensitive, ESearchDir::FromEnd);
		const int32 LastDot = Path.Find(TEXT("."), ESearchCase::CaseSensitive, ESearchDir::FromEnd);
		if (!Path.IsEmpty() && LastDot <= LastSlash)
		{
			Path += TEXT(".") + FPackageName::GetShortName(Path);
		}
		return Path;
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

	FString GetPackageFilename(const UPackage* Package)
	{
		return Package
			? FPackageName::LongPackageNameToFilename(
				Package->GetName(),
				FPackageName::GetAssetPackageExtension())
			: FString();
	}

	FString HashPackageFile(const UPackage* Package)
	{
		FString Digest;
		const FString Filename = GetPackageFilename(Package);
		if (!Filename.IsEmpty() && FBlueprintContextSha256::HashFile(Filename, Digest))
		{
			return TEXT("sha256:") + Digest;
		}
		return FString();
	}

	bool FindExistingPackageSidecar(const FString& PackageFilename, FString& OutFilename)
	{
		const FPackagePath PackagePath = FPackagePath::FromLocalPath(PackageFilename);
		constexpr EPackageSegment SidecarSegments[] = {
			EPackageSegment::Exports,
			EPackageSegment::BulkDataDefault,
			EPackageSegment::BulkDataOptional,
			EPackageSegment::BulkDataMemoryMapped,
			EPackageSegment::PayloadSidecar,
		};
		for (const EPackageSegment Segment : SidecarSegments)
		{
			const FString Candidate = PackagePath.GetLocalFullPath(Segment);
			if (!Candidate.IsEmpty() && IFileManager::Get().FileExists(*Candidate))
			{
				OutFilename = Candidate;
				return true;
			}
		}
		return false;
	}

	bool ResolvePropertyPath(
		UObject* Asset,
		const FString& PropertyPath,
		FProperty*& OutProperty,
		void*& OutValueAddress,
		FString& OutError)
	{
		TArray<FString> Segments;
		PropertyPath.ParseIntoArray(Segments, TEXT("."), true);
		if (!Asset || Segments.IsEmpty())
		{
			OutError = TEXT("Asset or property path is invalid.");
			return false;
		}

		UStruct* CurrentStruct = Asset->GetClass();
		void* CurrentContainer = Asset;
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

	bool SetPropertyFromJson(
		FProperty* Property,
		void* ValueAddress,
		const TSharedPtr<FJsonValue>& JsonValue,
		FString& OutError)
	{
		if (!Property || !ValueAddress || !JsonValue.IsValid())
		{
			OutError = TEXT("Property, address, or JSON value is invalid.");
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

		if (FEnumProperty* EnumProperty = CastField<FEnumProperty>(Property))
		{
			FString StringValue;
			if (!JsonValue->TryGetString(StringValue))
			{
				OutError = TEXT("Expected a JSON enum-name string.");
				return false;
			}
			const int64 EnumValue = EnumProperty->GetEnum()->GetValueByNameString(StringValue);
			if (EnumValue == INDEX_NONE)
			{
				OutError = FString::Printf(TEXT("Unknown enum value: %s"), *StringValue);
				return false;
			}
			EnumProperty->GetUnderlyingProperty()->SetIntPropertyValue(ValueAddress, EnumValue);
			return true;
		}

		if (FNumericProperty* NumericProperty = CastField<FNumericProperty>(Property))
		{
			if (UEnum* Enum = NumericProperty->GetIntPropertyEnum())
			{
				FString StringValue;
				if (!JsonValue->TryGetString(StringValue))
				{
					OutError = TEXT("Expected a JSON enum-name string.");
					return false;
				}
				const int64 EnumValue = Enum->GetValueByNameString(StringValue);
				if (EnumValue == INDEX_NONE || !NumericProperty->CanHoldValue(EnumValue))
				{
					OutError = FString::Printf(TEXT("Unknown or out-of-range enum value: %s"), *StringValue);
					return false;
				}
				NumericProperty->SetIntPropertyValue(ValueAddress, EnumValue);
				return true;
			}

			double Value = 0.0;
			if (!JsonValue->TryGetNumber(Value) || !FMath::IsFinite(Value))
			{
				OutError = TEXT("Expected a finite JSON number.");
				return false;
			}
			if (NumericProperty->IsFloatingPoint())
			{
				if (!NumericProperty->CanHoldValue(Value))
				{
					OutError = TEXT("Floating-point value is outside the property range.");
					return false;
				}
				NumericProperty->SetFloatingPointPropertyValue(ValueAddress, Value);
				return true;
			}

			constexpr double MinInt64AsDouble = -9223372036854775808.0;
			constexpr double MaxExclusiveInt64AsDouble = 9223372036854775808.0;
			if (Value != FMath::TruncToDouble(Value)
				|| Value < MinInt64AsDouble
				|| Value >= MaxExclusiveInt64AsDouble)
			{
				OutError = TEXT("Integer properties require an in-range whole JSON number.");
				return false;
			}
			const int64 IntegerValue = static_cast<int64>(Value);
			if (!NumericProperty->CanHoldValue(IntegerValue))
			{
				OutError = TEXT("Integer value is outside the property range.");
				return false;
			}
			NumericProperty->SetIntPropertyValue(ValueAddress, IntegerValue);
			return true;
		}

		FString StringValue;
		if (!JsonValue->TryGetString(StringValue))
		{
			OutError = TEXT("Expected a JSON string.");
			return false;
		}
		if (FStrProperty* StringProperty = CastField<FStrProperty>(Property))
		{
			StringProperty->SetPropertyValue(ValueAddress, StringValue);
			return true;
		}
		if (FNameProperty* NameProperty = CastField<FNameProperty>(Property))
		{
			NameProperty->SetPropertyValue(ValueAddress, FName(*StringValue));
			return true;
		}
		if (FTextProperty* TextProperty = CastField<FTextProperty>(Property))
		{
			TextProperty->SetPropertyValue(ValueAddress, FText::FromString(StringValue));
			return true;
		}

		OutError = FString::Printf(TEXT("Unsupported property type: %s"), *Property->GetClass()->GetName());
		return false;
	}

	bool ReadPropertyValue(
		UObject* Asset,
		FProperty* Property,
		void* ValueAddress,
		FString& OutValue)
	{
		if (!Asset || !Property || !ValueAddress)
		{
			return false;
		}
		Property->ExportTextItem_Direct(
			OutValue,
			ValueAddress,
			ValueAddress,
			Asset,
			PPF_SerializedAsImportText);
		return true;
	}

	bool RestorePropertyValue(
		UObject* Asset,
		FProperty* Property,
		void* ValueAddress,
		const FString& Text,
		FString& OutError)
	{
		if (!Property->ImportText_Direct(*Text, ValueAddress, Asset, PPF_SerializedAsImportText))
		{
			OutError = TEXT("Failed to restore original property value.");
			return false;
		}
		return true;
	}

	bool SaveAssetPackage(UObject* Asset, const FString& Filename, FString& OutError)
	{
		FSavePackageArgs SaveArgs;
		SaveArgs.TopLevelFlags = RF_Public | RF_Standalone;
		SaveArgs.SaveFlags = SAVE_NoError;
		SaveArgs.Error = GError;
		if (!UPackage::SavePackage(Asset->GetOutermost(), Asset, *Filename, SaveArgs))
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

	bool ScalarParameterArraysEqualExact(
		const TArray<FScalarParameterValue>& A,
		const TArray<FScalarParameterValue>& B)
	{
		if (A.Num() != B.Num())
		{
			return false;
		}
		for (int32 Index = 0; Index < A.Num(); ++Index)
		{
			if (A[Index] != B[Index])
			{
				return false;
			}
#if WITH_EDITORONLY_DATA
			if (A[Index].ParameterName_DEPRECATED != B[Index].ParameterName_DEPRECATED
				|| A[Index].AtlasData != B[Index].AtlasData)
			{
				return false;
			}
#endif
		}
		return true;
	}

	bool VectorParameterArraysEqualExact(
		const TArray<FVectorParameterValue>& A,
		const TArray<FVectorParameterValue>& B)
	{
		if (A.Num() != B.Num())
		{
			return false;
		}
		for (int32 Index = 0; Index < A.Num(); ++Index)
		{
			if (A[Index] != B[Index])
			{
				return false;
			}
#if WITH_EDITORONLY_DATA
			if (A[Index].ParameterName_DEPRECATED != B[Index].ParameterName_DEPRECATED)
			{
				return false;
			}
#endif
		}
		return true;
	}

	bool FindGlobalScalarParameter(
		UMaterialInstanceConstant* Instance,
		const FName ParameterName,
		FMaterialParameterInfo& OutInfo,
		FString& OutError)
	{
		TArray<FMaterialParameterInfo> ParameterInfos;
		TArray<FGuid> ParameterIds;
		Instance->GetAllScalarParameterInfo(ParameterInfos, ParameterIds);
		int32 MatchCount = 0;
		for (const FMaterialParameterInfo& Info : ParameterInfos)
		{
			if (Info.Name == ParameterName
				&& Info.Association == EMaterialParameterAssociation::GlobalParameter)
			{
				OutInfo = Info;
				++MatchCount;
			}
		}
		if (MatchCount != 1)
		{
			OutError = FString::Printf(
				TEXT("Expected exactly one global scalar parameter named %s; found %d."),
				*ParameterName.ToString(),
				MatchCount);
			return false;
		}
		return true;
	}

	bool FindGlobalVectorParameter(
		UMaterialInstanceConstant* Instance,
		const FName ParameterName,
		FMaterialParameterInfo& OutInfo,
		FString& OutError)
	{
		TArray<FMaterialParameterInfo> ParameterInfos;
		TArray<FGuid> ParameterIds;
		Instance->GetAllVectorParameterInfo(ParameterInfos, ParameterIds);
		int32 MatchCount = 0;
		for (const FMaterialParameterInfo& Info : ParameterInfos)
		{
			if (Info.Name == ParameterName
				&& Info.Association == EMaterialParameterAssociation::GlobalParameter)
			{
				OutInfo = Info;
				++MatchCount;
			}
		}
		if (MatchCount != 1)
		{
			OutError = FString::Printf(
				TEXT("Expected exactly one global vector parameter named %s; found %d."),
				*ParameterName.ToString(),
				MatchCount);
			return false;
		}
		return true;
	}

	bool ReadScalarParameter(
		UMaterialInstanceConstant* Instance,
		const FMaterialParameterInfo& ParameterInfo,
		float& OutValue)
	{
		return Instance->GetScalarParameterValue(FHashedMaterialParameterInfo(ParameterInfo), OutValue);
	}

	FString FormatScalarParameterValue(const float Value)
	{
		return FString::Printf(TEXT("%.9g"), static_cast<double>(Value));
	}

	bool ReadVectorParameter(
		UMaterialInstanceConstant* Instance,
		const FMaterialParameterInfo& ParameterInfo,
		FLinearColor& OutValue)
	{
		return Instance->GetVectorParameterValue(FHashedMaterialParameterInfo(ParameterInfo), OutValue);
	}

	FString FormatVectorParameterValue(const FLinearColor& Value)
	{
		return FString::Printf(
			TEXT("{\"r\":%.9g,\"g\":%.9g,\"b\":%.9g,\"a\":%.9g}"),
			static_cast<double>(Value.R),
			static_cast<double>(Value.G),
			static_cast<double>(Value.B),
			static_cast<double>(Value.A));
	}

	bool SaveReport(const FString& Filename, const TSharedRef<FJsonObject>& Report, FString& OutError)
	{
		FString JsonText;
		const TSharedRef<TJsonWriter<TCHAR, TPrettyJsonPrintPolicy<TCHAR>>> Writer =
			TJsonWriterFactory<TCHAR, TPrettyJsonPrintPolicy<TCHAR>>::Create(&JsonText);
		if (!FJsonSerializer::Serialize(Report, Writer))
		{
			OutError = TEXT("Failed to serialize patch report.");
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
}

UAssetPatchCommandlet::UAssetPatchCommandlet()
{
	IsClient = false;
	IsEditor = true;
	IsServer = false;
	LogToConsole = true;
	ShowErrorCount = true;
}

int32 UAssetPatchCommandlet::Main(const FString& Params)
{
	using namespace AssetPatchCommandletPrivate;

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
	ReportFilename = FPaths::ConvertRelativePathToFull(ReportFilename);
	BackupDirectory = FPaths::ConvertRelativePathToFull(BackupDirectory);
	const bool bCommit = Mode.Equals(TEXT("Commit"), ESearchCase::IgnoreCase);
	if (!bCommit && !Mode.Equals(TEXT("DryRun"), ESearchCase::IgnoreCase))
	{
		UE_LOG(LogAssetPatch, Error, TEXT("Mode must be DryRun or Commit."));
		return 1;
	}

	FString Error;
	FPatchPolicy Policy;
	if (!LoadPolicy(PolicyFilename, Policy, Error))
	{
		UE_LOG(LogAssetPatch, Error, TEXT("%s"), *Error);
		return 2;
	}
	if (!ContainsExact(Policy.AllowedProjectNames, FApp::GetProjectName()))
	{
		UE_LOG(LogAssetPatch, Error, TEXT("Project is not authorized by policy."));
		return 3;
	}
	if (bCommit && !Policy.bCommitEnabled)
	{
		UE_LOG(LogAssetPatch, Error, TEXT("Commit is disabled by policy."));
		return 4;
	}

	TSharedPtr<FJsonObject> PatchObject;
	if (!LoadJsonObject(PatchFilename, PatchObject, Error))
	{
		UE_LOG(LogAssetPatch, Error, TEXT("%s"), *Error);
		return 5;
	}

	FString PatchId;
	FString ProjectName;
	PatchObject->TryGetStringField(TEXT("patchId"), PatchId);
	PatchObject->TryGetStringField(TEXT("projectName"), ProjectName);
	if (!ProjectName.Equals(FApp::GetProjectName(), ESearchCase::CaseSensitive))
	{
		UE_LOG(LogAssetPatch, Error, TEXT("Patch projectName does not match the current project."));
		return 6;
	}

	const TArray<TSharedPtr<FJsonValue>>* AssetValues = nullptr;
	if (!PatchObject->TryGetArrayField(TEXT("assets"), AssetValues)
		|| !AssetValues
		|| AssetValues->Num() != 1)
	{
		UE_LOG(LogAssetPatch, Error, TEXT("Exactly one asset is required per patch."));
		return 7;
	}

	const TSharedPtr<FJsonObject> AssetObject = (*AssetValues)[0]->AsObject();
	if (!AssetObject.IsValid())
	{
		UE_LOG(LogAssetPatch, Error, TEXT("Patch asset entry is invalid."));
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
		UE_LOG(LogAssetPatch, Error, TEXT("Asset is outside authorized roots: %s"), *AssetPath);
		return 9;
	}

	UObject* Asset = LoadObject<UObject>(nullptr, *AssetPath);
	if (!Asset)
	{
		UE_LOG(LogAssetPatch, Error, TEXT("Could not load asset: %s"), *AssetPath);
		return 10;
	}

	const FString ActualAssetClass = Asset->GetClass()->GetPathName();
	if (!ExpectedAssetClass.Equals(ActualAssetClass, ESearchCase::CaseSensitive)
		|| !ContainsExact(Policy.AllowedAssetClasses, ActualAssetClass))
	{
		UE_LOG(
			LogAssetPatch,
			Error,
			TEXT("Asset class is not authorized. Expected=%s Actual=%s"),
			*ExpectedAssetClass,
			*ActualAssetClass);
		return 11;
	}

	if (Asset->IsA<UBlueprint>())
	{
		UE_LOG(LogAssetPatch, Error, TEXT("AssetPatch only supports non-Blueprint assets."));
		return 11;
	}

	UPackage* Package = Asset->GetOutermost();
	const bool bOriginalDirty = Package->IsDirty();
	if (Policy.bRejectDirtyPackages && bOriginalDirty)
	{
		UE_LOG(LogAssetPatch, Error, TEXT("Dirty packages are rejected by policy."));
		return 12;
	}

	const FString PackageFilename = GetPackageFilename(Package);
	FString ExistingSidecar;
	if (FindExistingPackageSidecar(PackageFilename, ExistingSidecar))
	{
		UE_LOG(
			LogAssetPatch,
			Error,
			TEXT("Packages with sidecar files are not supported yet: %s"),
			*ExistingSidecar);
		return 24;
	}
	const FString BeforeRevision = HashPackageFile(Package);
	if (!ExpectedRevision.Equals(BeforeRevision, ESearchCase::IgnoreCase))
	{
		UE_LOG(
			LogAssetPatch,
			Error,
			TEXT("Revision conflict. Expected=%s Current=%s"),
			*ExpectedRevision,
			*BeforeRevision);
		return 13;
	}

	const TArray<TSharedPtr<FJsonValue>>* OperationValues = nullptr;
	if (!AssetObject->TryGetArrayField(TEXT("operations"), OperationValues)
		|| !OperationValues
		|| OperationValues->Num() != 1)
	{
		UE_LOG(LogAssetPatch, Error, TEXT("Exactly one operation is required per patch."));
		return 14;
	}

	const TSharedPtr<FJsonObject> OperationObject = (*OperationValues)[0]->AsObject();
	if (!OperationObject.IsValid())
	{
		UE_LOG(LogAssetPatch, Error, TEXT("Patch operation entry is invalid."));
		return 15;
	}
	FString Operation;
	OperationObject->TryGetStringField(TEXT("operation"), Operation);
	const bool bAssetPropertyOperation =
		Operation.Equals(TEXT("setAssetProperty"), ESearchCase::CaseSensitive);
	const bool bMaterialScalarOperation =
		Operation.Equals(TEXT("setMaterialInstanceScalarParameter"), ESearchCase::CaseSensitive);
	const bool bMaterialVectorOperation =
		Operation.Equals(TEXT("setMaterialInstanceVectorParameter"), ESearchCase::CaseSensitive);
	if ((!bAssetPropertyOperation && !bMaterialScalarOperation && !bMaterialVectorOperation)
		|| !ContainsExact(Policy.AllowedOperations, Operation))
	{
		UE_LOG(LogAssetPatch, Error, TEXT("Operation is not authorized or implemented: %s"), *Operation);
		return 15;
	}

	const TSharedPtr<FJsonObject>* TargetObjectPtr = nullptr;
	if (!OperationObject->TryGetObjectField(TEXT("target"), TargetObjectPtr)
		|| !TargetObjectPtr
		|| !TargetObjectPtr->IsValid())
	{
		UE_LOG(LogAssetPatch, Error, TEXT("Operation target is invalid."));
		return 16;
	}
	const TSharedPtr<FJsonObject> TargetObject = *TargetObjectPtr;

	if (bMaterialScalarOperation)
	{
		UMaterialInstanceConstant* MaterialInstance = Cast<UMaterialInstanceConstant>(Asset);
		if (!MaterialInstance)
		{
			UE_LOG(LogAssetPatch, Error, TEXT("Operation requires MaterialInstanceConstant."));
			return 17;
		}

		FString ParameterNameText;
		TargetObject->TryGetStringField(TEXT("parameterName"), ParameterNameText);
		if (ParameterNameText.IsEmpty())
		{
			UE_LOG(LogAssetPatch, Error, TEXT("Material scalar parameterName is required."));
			return 17;
		}
		const FString ParameterAuthorization =
			ActualAssetClass + TEXT("#Scalar#") + ParameterNameText;
		if (!ContainsExact(Policy.AllowedMaterialParameters, ParameterAuthorization))
		{
			UE_LOG(
				LogAssetPatch,
				Error,
				TEXT("Material parameter is not authorized by policy: %s"),
				*ParameterAuthorization);
			return 17;
		}

		const TSharedPtr<FJsonValue> NewValue = OperationObject->TryGetField(TEXT("value"));
		double NewValueDouble = 0.0;
		if (!NewValue.IsValid()
			|| !NewValue->TryGetNumber(NewValueDouble)
			|| !FMath::IsFinite(NewValueDouble)
			|| FMath::Abs(NewValueDouble) > static_cast<double>(FLT_MAX))
		{
			UE_LOG(LogAssetPatch, Error, TEXT("Material scalar value must be a finite float."));
			return 18;
		}
		const float NewScalarValue = static_cast<float>(NewValueDouble);
		if (!FMath::IsFinite(NewScalarValue))
		{
			UE_LOG(LogAssetPatch, Error, TEXT("Material scalar value is outside float range."));
			return 18;
		}

		FMaterialParameterInfo ParameterInfo;
		if (!FindGlobalScalarParameter(MaterialInstance, FName(*ParameterNameText), ParameterInfo, Error))
		{
			UE_LOG(LogAssetPatch, Error, TEXT("%s"), *Error);
			return 17;
		}

		float BeforeScalarValue = 0.0f;
		if (!ReadScalarParameter(MaterialInstance, ParameterInfo, BeforeScalarValue))
		{
			UE_LOG(LogAssetPatch, Error, TEXT("Could not read material scalar parameter."));
			return 17;
		}
		const TArray<FScalarParameterValue> OriginalScalarParameters =
			MaterialInstance->ScalarParameterValues;

		FString BackupFilename;
		if (bCommit)
		{
			IFileManager::Get().MakeDirectory(*BackupDirectory, true);
			BackupFilename = CreateBackupFilename(BackupDirectory, PatchId, PackageFilename);
			if (IFileManager::Get().Copy(*BackupFilename, *PackageFilename, true, true) != COPY_OK)
			{
				UE_LOG(LogAssetPatch, Error, TEXT("Could not create package backup: %s"), *BackupFilename);
				return 19;
			}
		}

		MaterialInstance->Modify();
		UMaterialEditingLibrary::SetMaterialInstanceScalarParameterValue(
			MaterialInstance,
			FName(*ParameterNameText),
			NewScalarValue,
			EMaterialParameterAssociation::GlobalParameter);

		float AfterScalarValue = 0.0f;
		if (!ReadScalarParameter(MaterialInstance, ParameterInfo, AfterScalarValue)
			|| !FMath::IsNearlyEqual(AfterScalarValue, NewScalarValue, UE_SMALL_NUMBER))
		{
			MaterialInstance->ScalarParameterValues = OriginalScalarParameters;
			UMaterialEditingLibrary::UpdateMaterialInstance(MaterialInstance);
			Package->SetDirtyFlag(bOriginalDirty);
			UE_LOG(LogAssetPatch, Error, TEXT("Material scalar parameter read-back verification failed."));
			return 20;
		}

		bool bSaved = false;
		bool bRolledBack = false;
		bool bStructureMatch = true;
		float RestoredScalarValue = BeforeScalarValue;
		if (bCommit)
		{
			if (!SaveAssetPackage(MaterialInstance, PackageFilename, Error))
			{
				IFileManager::Get().Copy(*PackageFilename, *BackupFilename, true, true);
				UE_LOG(LogAssetPatch, Error, TEXT("%s Backup restored."), *Error);
				return 21;
			}
			bSaved = true;
		}
		else
		{
			MaterialInstance->ScalarParameterValues = OriginalScalarParameters;
			UMaterialEditingLibrary::UpdateMaterialInstance(MaterialInstance);
			Package->SetDirtyFlag(bOriginalDirty);
			bStructureMatch = ScalarParameterArraysEqualExact(
				OriginalScalarParameters,
				MaterialInstance->ScalarParameterValues);
			if (!ReadScalarParameter(MaterialInstance, ParameterInfo, RestoredScalarValue))
			{
				UE_LOG(LogAssetPatch, Error, TEXT("Could not read restored material scalar parameter."));
				return 22;
			}
			bRolledBack = true;
		}

		const FString AfterRevision = HashPackageFile(Package);
		const bool bRestoredValueMatch =
			!bRolledBack
			|| FMath::IsNearlyEqual(RestoredScalarValue, BeforeScalarValue, UE_SMALL_NUMBER);
		const TSharedRef<FJsonObject> Report = MakeShared<FJsonObject>();
		Report->SetStringField(TEXT("schemaVersion"), TEXT("1.0"));
		Report->SetStringField(TEXT("executorVersion"), TEXT("0.3.5"));
		Report->SetStringField(TEXT("mode"), bCommit ? TEXT("Commit") : TEXT("DryRun"));
		Report->SetStringField(TEXT("patchId"), PatchId);
		Report->SetStringField(TEXT("projectName"), FApp::GetProjectName());
		Report->SetStringField(TEXT("assetPath"), AssetPath);
		Report->SetStringField(TEXT("assetClass"), ActualAssetClass);
		Report->SetStringField(TEXT("operation"), Operation);
		Report->SetObjectField(TEXT("target"), TargetObject);
		Report->SetStringField(
			TEXT("targetDescription"),
			TEXT("material-instance-scalar:") + ParameterNameText);
		Report->SetStringField(TEXT("targetType"), TEXT("MaterialScalarParameter(float)"));
		Report->SetStringField(TEXT("beforeValue"), FormatScalarParameterValue(BeforeScalarValue));
		Report->SetStringField(TEXT("afterValue"), FormatScalarParameterValue(AfterScalarValue));
		Report->SetStringField(TEXT("restoredValue"), FormatScalarParameterValue(RestoredScalarValue));
		Report->SetStringField(TEXT("beforeRevision"), BeforeRevision);
		Report->SetStringField(TEXT("afterRevision"), AfterRevision);
		Report->SetBoolField(TEXT("compiled"), false);
		Report->SetBoolField(TEXT("saved"), bSaved);
		Report->SetBoolField(TEXT("rolledBack"), bRolledBack);
		Report->SetBoolField(TEXT("rollbackValueMatch"), bRestoredValueMatch);
		Report->SetBoolField(TEXT("rollbackStructureMatch"), !bRolledBack || bStructureMatch);
		Report->SetBoolField(
			TEXT("diskUnchanged"),
			BeforeRevision.Equals(AfterRevision, ESearchCase::IgnoreCase));
		Report->SetStringField(TEXT("backupPath"), BackupFilename);
		if (!SaveReport(ReportFilename, Report, Error))
		{
			if (bCommit && !BackupFilename.IsEmpty())
			{
				IFileManager::Get().Copy(*PackageFilename, *BackupFilename, true, true);
			}
			UE_LOG(LogAssetPatch, Error, TEXT("%s Disk backup restored."), *Error);
			return 23;
		}

		UE_LOG(
			LogAssetPatch,
			Display,
			TEXT("Material scalar patch succeeded. Mode=%s Asset=%s Parameter=%s Before=%s After=%s"),
			bCommit ? TEXT("Commit") : TEXT("DryRun"),
			*AssetPath,
			*ParameterNameText,
			*FormatScalarParameterValue(BeforeScalarValue),
			*FormatScalarParameterValue(AfterScalarValue));
		return 0;
	}


	if (bMaterialVectorOperation)
	{
		UMaterialInstanceConstant* MaterialInstance = Cast<UMaterialInstanceConstant>(Asset);
		if (!MaterialInstance)
		{
			UE_LOG(LogAssetPatch, Error, TEXT("Operation requires MaterialInstanceConstant."));
			return 17;
		}

		FString ParameterNameText;
		TargetObject->TryGetStringField(TEXT("parameterName"), ParameterNameText);
		if (ParameterNameText.IsEmpty())
		{
			UE_LOG(LogAssetPatch, Error, TEXT("Material vector parameterName is required."));
			return 17;
		}
		const FString ParameterAuthorization =
			ActualAssetClass + TEXT("#Vector#") + ParameterNameText;
		if (!ContainsExact(Policy.AllowedMaterialParameters, ParameterAuthorization))
		{
			UE_LOG(
				LogAssetPatch,
				Error,
				TEXT("Material parameter is not authorized by policy: %s"),
				*ParameterAuthorization);
			return 17;
		}

		const TSharedPtr<FJsonObject>* ColorObjectPtr = nullptr;
		if (!OperationObject->TryGetObjectField(TEXT("value"), ColorObjectPtr)
			|| !ColorObjectPtr
			|| !ColorObjectPtr->IsValid())
		{
			UE_LOG(LogAssetPatch, Error, TEXT("Material vector value must be an RGBA object."));
			return 18;
		}
		const TSharedPtr<FJsonObject> ColorObject = *ColorObjectPtr;
		if (ColorObject->Values.Num() != 4
			|| !ColorObject->HasField(TEXT("r"))
			|| !ColorObject->HasField(TEXT("g"))
			|| !ColorObject->HasField(TEXT("b"))
			|| !ColorObject->HasField(TEXT("a")))
		{
			UE_LOG(LogAssetPatch, Error, TEXT("Material vector value must contain only r, g, b, and a."));
			return 18;
		}
		double R = 0.0;
		double G = 0.0;
		double B = 0.0;
		double A = 0.0;
		if (!ColorObject->TryGetNumberField(TEXT("r"), R)
			|| !ColorObject->TryGetNumberField(TEXT("g"), G)
			|| !ColorObject->TryGetNumberField(TEXT("b"), B)
			|| !ColorObject->TryGetNumberField(TEXT("a"), A)
			|| !FMath::IsFinite(R)
			|| !FMath::IsFinite(G)
			|| !FMath::IsFinite(B)
			|| !FMath::IsFinite(A)
			|| FMath::Abs(R) > static_cast<double>(FLT_MAX)
			|| FMath::Abs(G) > static_cast<double>(FLT_MAX)
			|| FMath::Abs(B) > static_cast<double>(FLT_MAX)
			|| FMath::Abs(A) > static_cast<double>(FLT_MAX))
		{
			UE_LOG(LogAssetPatch, Error, TEXT("Material vector components must be finite floats."));
			return 18;
		}
		const FLinearColor NewVectorValue(
			static_cast<float>(R),
			static_cast<float>(G),
			static_cast<float>(B),
			static_cast<float>(A));

		FMaterialParameterInfo ParameterInfo;
		if (!FindGlobalVectorParameter(MaterialInstance, FName(*ParameterNameText), ParameterInfo, Error))
		{
			UE_LOG(LogAssetPatch, Error, TEXT("%s"), *Error);
			return 17;
		}

		FLinearColor BeforeVectorValue = FLinearColor::Black;
		if (!ReadVectorParameter(MaterialInstance, ParameterInfo, BeforeVectorValue))
		{
			UE_LOG(LogAssetPatch, Error, TEXT("Could not read material vector parameter."));
			return 17;
		}
		const TArray<FVectorParameterValue> OriginalVectorParameters =
			MaterialInstance->VectorParameterValues;

		FString BackupFilename;
		if (bCommit)
		{
			IFileManager::Get().MakeDirectory(*BackupDirectory, true);
			BackupFilename = CreateBackupFilename(BackupDirectory, PatchId, PackageFilename);
			if (IFileManager::Get().Copy(*BackupFilename, *PackageFilename, true, true) != COPY_OK)
			{
				UE_LOG(LogAssetPatch, Error, TEXT("Could not create package backup: %s"), *BackupFilename);
				return 19;
			}
		}

		MaterialInstance->Modify();
		UMaterialEditingLibrary::SetMaterialInstanceVectorParameterValue(
			MaterialInstance,
			FName(*ParameterNameText),
			NewVectorValue,
			EMaterialParameterAssociation::GlobalParameter);

		FLinearColor AfterVectorValue = FLinearColor::Black;
		if (!ReadVectorParameter(MaterialInstance, ParameterInfo, AfterVectorValue)
			|| !AfterVectorValue.Equals(NewVectorValue, UE_SMALL_NUMBER))
		{
			MaterialInstance->VectorParameterValues = OriginalVectorParameters;
			UMaterialEditingLibrary::UpdateMaterialInstance(MaterialInstance);
			Package->SetDirtyFlag(bOriginalDirty);
			UE_LOG(LogAssetPatch, Error, TEXT("Material vector parameter read-back verification failed."));
			return 20;
		}

		bool bSaved = false;
		bool bRolledBack = false;
		bool bStructureMatch = true;
		FLinearColor RestoredVectorValue = BeforeVectorValue;
		if (bCommit)
		{
			if (!SaveAssetPackage(MaterialInstance, PackageFilename, Error))
			{
				IFileManager::Get().Copy(*PackageFilename, *BackupFilename, true, true);
				UE_LOG(LogAssetPatch, Error, TEXT("%s Backup restored."), *Error);
				return 21;
			}
			bSaved = true;
		}
		else
		{
			MaterialInstance->VectorParameterValues = OriginalVectorParameters;
			UMaterialEditingLibrary::UpdateMaterialInstance(MaterialInstance);
			Package->SetDirtyFlag(bOriginalDirty);
			bStructureMatch = VectorParameterArraysEqualExact(
				OriginalVectorParameters,
				MaterialInstance->VectorParameterValues);
			if (!ReadVectorParameter(MaterialInstance, ParameterInfo, RestoredVectorValue))
			{
				UE_LOG(LogAssetPatch, Error, TEXT("Could not read restored material vector parameter."));
				return 22;
			}
			bRolledBack = true;
		}

		const FString AfterRevision = HashPackageFile(Package);
		const bool bRestoredValueMatch =
			!bRolledBack || RestoredVectorValue.Equals(BeforeVectorValue, UE_SMALL_NUMBER);
		const TSharedRef<FJsonObject> Report = MakeShared<FJsonObject>();
		Report->SetStringField(TEXT("schemaVersion"), TEXT("1.0"));
		Report->SetStringField(TEXT("executorVersion"), TEXT("0.3.5"));
		Report->SetStringField(TEXT("mode"), bCommit ? TEXT("Commit") : TEXT("DryRun"));
		Report->SetStringField(TEXT("patchId"), PatchId);
		Report->SetStringField(TEXT("projectName"), FApp::GetProjectName());
		Report->SetStringField(TEXT("assetPath"), AssetPath);
		Report->SetStringField(TEXT("assetClass"), ActualAssetClass);
		Report->SetStringField(TEXT("operation"), Operation);
		Report->SetObjectField(TEXT("target"), TargetObject);
		Report->SetStringField(
			TEXT("targetDescription"),
			TEXT("material-instance-vector:") + ParameterNameText);
		Report->SetStringField(TEXT("targetType"), TEXT("MaterialVectorParameter(FLinearColor)"));
		Report->SetStringField(TEXT("beforeValue"), FormatVectorParameterValue(BeforeVectorValue));
		Report->SetStringField(TEXT("afterValue"), FormatVectorParameterValue(AfterVectorValue));
		Report->SetStringField(TEXT("restoredValue"), FormatVectorParameterValue(RestoredVectorValue));
		Report->SetStringField(TEXT("beforeRevision"), BeforeRevision);
		Report->SetStringField(TEXT("afterRevision"), AfterRevision);
		Report->SetBoolField(TEXT("compiled"), false);
		Report->SetBoolField(TEXT("saved"), bSaved);
		Report->SetBoolField(TEXT("rolledBack"), bRolledBack);
		Report->SetBoolField(TEXT("rollbackValueMatch"), bRestoredValueMatch);
		Report->SetBoolField(TEXT("rollbackStructureMatch"), !bRolledBack || bStructureMatch);
		Report->SetBoolField(
			TEXT("diskUnchanged"),
			BeforeRevision.Equals(AfterRevision, ESearchCase::IgnoreCase));
		Report->SetStringField(TEXT("backupPath"), BackupFilename);
		if (!SaveReport(ReportFilename, Report, Error))
		{
			if (bCommit && !BackupFilename.IsEmpty())
			{
				IFileManager::Get().Copy(*PackageFilename, *BackupFilename, true, true);
			}
			UE_LOG(LogAssetPatch, Error, TEXT("%s Disk backup restored."), *Error);
			return 23;
		}

		UE_LOG(
			LogAssetPatch,
			Display,
			TEXT("Material vector patch succeeded. Mode=%s Asset=%s Parameter=%s Before=%s After=%s"),
			bCommit ? TEXT("Commit") : TEXT("DryRun"),
			*AssetPath,
			*ParameterNameText,
			*FormatVectorParameterValue(BeforeVectorValue),
			*FormatVectorParameterValue(AfterVectorValue));
		return 0;
	}

	FString PropertyPath;
	TargetObject->TryGetStringField(TEXT("propertyPath"), PropertyPath);
	const FString PropertyAuthorization = ActualAssetClass + TEXT("#") + PropertyPath;
	if (!ContainsExact(Policy.AllowedAssetProperties, PropertyAuthorization))
	{
		UE_LOG(
			LogAssetPatch,
			Error,
			TEXT("Asset property is not authorized by policy: %s"),
			*PropertyAuthorization);
		return 17;
	}

	FProperty* Property = nullptr;
	void* ValueAddress = nullptr;
	if (!ResolvePropertyPath(Asset, PropertyPath, Property, ValueAddress, Error))
	{
		UE_LOG(LogAssetPatch, Error, TEXT("%s"), *Error);
		return 17;
	}
	const EPropertyFlags DisallowedFlags =
		CPF_Transient | CPF_DuplicateTransient | CPF_NonPIEDuplicateTransient;
	if (!Property->HasAnyPropertyFlags(CPF_Edit) || Property->HasAnyPropertyFlags(DisallowedFlags))
	{
		UE_LOG(
			LogAssetPatch,
			Error,
			TEXT("Asset property must be editable and non-transient: %s"),
			*PropertyPath);
		return 17;
	}

	const TSharedPtr<FJsonValue> NewValue = OperationObject->TryGetField(TEXT("value"));
	if (!NewValue.IsValid())
	{
		UE_LOG(LogAssetPatch, Error, TEXT("Operation value is missing."));
		return 18;
	}

	FString BeforeValue;
	ReadPropertyValue(Asset, Property, ValueAddress, BeforeValue);
	FString BackupFilename;
	if (bCommit)
	{
		IFileManager::Get().MakeDirectory(*BackupDirectory, true);
		BackupFilename = CreateBackupFilename(BackupDirectory, PatchId, PackageFilename);
		if (IFileManager::Get().Copy(*BackupFilename, *PackageFilename, true, true) != COPY_OK)
		{
			UE_LOG(LogAssetPatch, Error, TEXT("Could not create package backup: %s"), *BackupFilename);
			return 19;
		}
	}

	Asset->Modify();
	if (!SetPropertyFromJson(Property, ValueAddress, NewValue, Error))
	{
		UE_LOG(LogAssetPatch, Error, TEXT("%s"), *Error);
		return 20;
	}
	Asset->PostEditChange();
	Package->MarkPackageDirty();

	FString AfterValue;
	ReadPropertyValue(Asset, Property, ValueAddress, AfterValue);
	bool bSaved = false;
	bool bRolledBack = false;
	FString RestoredValue;
	if (bCommit)
	{
		if (!SaveAssetPackage(Asset, PackageFilename, Error))
		{
			IFileManager::Get().Copy(*PackageFilename, *BackupFilename, true, true);
			UE_LOG(LogAssetPatch, Error, TEXT("%s Backup restored."), *Error);
			return 21;
		}
		bSaved = true;
	}
	else
	{
		if (!RestorePropertyValue(Asset, Property, ValueAddress, BeforeValue, Error))
		{
			UE_LOG(LogAssetPatch, Error, TEXT("%s"), *Error);
			return 22;
		}
		Asset->PostEditChange();
		Package->SetDirtyFlag(bOriginalDirty);
		ReadPropertyValue(Asset, Property, ValueAddress, RestoredValue);
		bRolledBack = true;
	}

	const FString AfterRevision = HashPackageFile(Package);
	const TSharedRef<FJsonObject> Report = MakeShared<FJsonObject>();
	Report->SetStringField(TEXT("schemaVersion"), TEXT("1.0"));
	Report->SetStringField(TEXT("executorVersion"), TEXT("0.3.5"));
	Report->SetStringField(TEXT("mode"), bCommit ? TEXT("Commit") : TEXT("DryRun"));
	Report->SetStringField(TEXT("patchId"), PatchId);
	Report->SetStringField(TEXT("projectName"), FApp::GetProjectName());
	Report->SetStringField(TEXT("assetPath"), AssetPath);
	Report->SetStringField(TEXT("assetClass"), ActualAssetClass);
	Report->SetStringField(TEXT("operation"), Operation);
	Report->SetObjectField(TEXT("target"), TargetObject);
	Report->SetStringField(TEXT("targetDescription"), TEXT("asset-property:") + PropertyPath);
	Report->SetStringField(TEXT("targetType"), Property->GetClass()->GetName());
	Report->SetStringField(TEXT("beforeValue"), BeforeValue);
	Report->SetStringField(TEXT("afterValue"), AfterValue);
	Report->SetStringField(TEXT("restoredValue"), RestoredValue);
	Report->SetStringField(TEXT("beforeRevision"), BeforeRevision);
	Report->SetStringField(TEXT("afterRevision"), AfterRevision);
	Report->SetBoolField(TEXT("compiled"), false);
	Report->SetBoolField(TEXT("saved"), bSaved);
	Report->SetBoolField(TEXT("rolledBack"), bRolledBack);
	Report->SetBoolField(
		TEXT("rollbackValueMatch"),
		!bRolledBack || RestoredValue.Equals(BeforeValue, ESearchCase::IgnoreCase));
	Report->SetBoolField(TEXT("diskUnchanged"), BeforeRevision.Equals(AfterRevision, ESearchCase::IgnoreCase));
	Report->SetStringField(TEXT("backupPath"), BackupFilename);
	if (!SaveReport(ReportFilename, Report, Error))
	{
		if (bCommit && !BackupFilename.IsEmpty())
		{
			IFileManager::Get().Copy(*PackageFilename, *BackupFilename, true, true);
		}
		UE_LOG(LogAssetPatch, Error, TEXT("%s Disk backup restored."), *Error);
		return 23;
	}

	UE_LOG(
		LogAssetPatch,
		Display,
		TEXT("Asset patch succeeded. Mode=%s Asset=%s Property=%s Before=%s After=%s"),
		bCommit ? TEXT("Commit") : TEXT("DryRun"),
		*AssetPath,
		*PropertyPath,
		*BeforeValue,
		*AfterValue);
	return 0;
}
