#include "AssetPatchCommandlet.h"

#include "AssetRegistry/AssetIdentifier.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetRegistry/IAssetRegistry.h"
#include "BlueprintContextSha256.h"
#include "Dom/JsonObject.h"
#include "Engine/Blueprint.h"
#include "Engine/DataTable.h"
#include "Engine/Texture.h"
#include "HAL/FileManager.h"
#include "MaterialEditingLibrary.h"
#include "Materials/MaterialInstanceConstant.h"
#include "StaticParameterSet.h"
#include "Misc/App.h"
#include "Misc/DateTime.h"
#include "Misc/FileHelper.h"
#include "Misc/PackageName.h"
#include "Misc/PackagePath.h"
#include "Misc/PackageSegment.h"
#include "Misc/Parse.h"
#include "Misc/Paths.h"
#include "Modules/ModuleManager.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"
#include "UObject/StructOnScope.h"
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
		TArray<FString> AllowedReferenceRoots;
		TArray<FString> AllowedReferenceClasses;
		TArray<FString> AllowedOperations;
		TArray<FString> AllowedAssetClasses;
		TArray<FString> AllowedAssetProperties;
		TArray<FString> AllowedMaterialParameters;
		TArray<FString> AllowedDataTableFields;
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

	void FindDataTableRowReferencers(
		UDataTable* DataTable,
		const FName RowName,
		TArray<FAssetIdentifier>& OutReferencers)
	{
		OutReferencers.Reset();
		if (!DataTable || !DataTable->GetOutermost())
		{
			return;
		}

		IAssetRegistry& AssetRegistry = FModuleManager::LoadModuleChecked<FAssetRegistryModule>(
			TEXT("AssetRegistry")).Get();
		AssetRegistry.SearchAllAssets(true);
		AssetRegistry.GetReferencers(
			FAssetIdentifier(DataTable->GetOutermost()->GetFName(), DataTable->GetFName(), RowName),
			OutReferencers,
			UE::AssetRegistry::EDependencyCategory::SearchableName);
		OutReferencers.Sort([](const FAssetIdentifier& Left, const FAssetIdentifier& Right)
		{
			return Left.ToString() < Right.ToString();
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
		ReadStringArray(Object, TEXT("allowedReferenceRoots"), OutPolicy.AllowedReferenceRoots);
		ReadStringArray(Object, TEXT("allowedReferenceClasses"), OutPolicy.AllowedReferenceClasses);
		ReadStringArray(Object, TEXT("allowedOperations"), OutPolicy.AllowedOperations);
		ReadStringArray(Object, TEXT("allowedAssetClasses"), OutPolicy.AllowedAssetClasses);
		ReadStringArray(Object, TEXT("allowedAssetProperties"), OutPolicy.AllowedAssetProperties);
		ReadStringArray(Object, TEXT("allowedMaterialParameters"), OutPolicy.AllowedMaterialParameters);
		ReadStringArray(Object, TEXT("allowedDataTableFields"), OutPolicy.AllowedDataTableFields);
		auto NormalizeAndValidateRoots = [&OutError](
			TArray<FString>& Roots,
			const TCHAR* RootKind) -> bool
		{
			for (FString& Root : Roots)
			{
				Root.RemoveFromEnd(TEXT("/"));
				if (Root.Equals(TEXT("/Game"), ESearchCase::CaseSensitive)
					|| !Root.StartsWith(TEXT("/Game/"), ESearchCase::CaseSensitive)
					|| Root.Contains(TEXT("."))
					|| Root.Contains(TEXT("\\"))
					|| Root.Contains(TEXT("//")))
				{
					OutError = FString::Printf(
						TEXT("Policy %s root is invalid or too broad: %s"),
						RootKind,
						*Root);
					return false;
				}
			}
			return true;
		};
		if (!NormalizeAndValidateRoots(OutPolicy.AllowedAssetRoots, TEXT("asset"))
			|| !NormalizeAndValidateRoots(OutPolicy.AllowedReferenceRoots, TEXT("reference")))
		{
			return false;
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
			|| ContainsExact(OutPolicy.AllowedOperations, TEXT("setMaterialInstanceVectorParameter"))
			|| ContainsExact(OutPolicy.AllowedOperations, TEXT("setMaterialInstanceTextureParameter"))
			|| ContainsExact(OutPolicy.AllowedOperations, TEXT("setMaterialInstanceStaticSwitchParameter"));
		if (bUsesMaterialParameterOperations && OutPolicy.AllowedMaterialParameters.IsEmpty())
		{
			OutError = TEXT(
				"Material Instance parameter operations require allowedMaterialParameters authorization.");
			return false;
		}
		const bool bUsesDataTableFieldOperations =
			ContainsExact(OutPolicy.AllowedOperations, TEXT("setDataTableCell"))
			|| ContainsExact(OutPolicy.AllowedOperations, TEXT("setDataTableRowFields"))
			|| ContainsExact(OutPolicy.AllowedOperations, TEXT("addDataTableRow"));
		if (bUsesDataTableFieldOperations && OutPolicy.AllowedDataTableFields.IsEmpty())
		{
			OutError = TEXT("DataTable field operations require allowedDataTableFields authorization.");
			return false;
		}
		if (ContainsExact(OutPolicy.AllowedOperations, TEXT("setMaterialInstanceTextureParameter"))
			&& (OutPolicy.AllowedReferenceRoots.IsEmpty()
				|| OutPolicy.AllowedReferenceClasses.IsEmpty()))
		{
			OutError = TEXT(
				"Texture parameter writes require allowedReferenceRoots and allowedReferenceClasses authorization.");
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

	bool IsObjectPathAllowed(const TArray<FString>& AllowedRoots, const FString& ObjectPath)
	{
		FString PackagePath = ObjectPath;
		int32 DotIndex = INDEX_NONE;
		if (PackagePath.FindChar(TEXT('.'), DotIndex))
		{
			PackagePath.LeftInline(DotIndex, EAllowShrinking::No);
		}

		for (const FString& Root : AllowedRoots)
		{
			if (PackagePath.Equals(Root, ESearchCase::CaseSensitive)
				|| PackagePath.StartsWith(Root + TEXT("/"), ESearchCase::CaseSensitive))
			{
				return true;
			}
		}
		return false;
	}

	bool IsAssetAllowed(const FPatchPolicy& Policy, const FString& ObjectPath)
	{
		return IsObjectPathAllowed(Policy.AllowedAssetRoots, ObjectPath);
	}

	bool IsReferenceAllowed(const FPatchPolicy& Policy, const FString& ObjectPath)
	{
		return IsObjectPathAllowed(Policy.AllowedReferenceRoots, ObjectPath);
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
		OutValue.Reset();
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

	bool TextureParameterArraysEqualExact(
		const TArray<FTextureParameterValue>& A,
		const TArray<FTextureParameterValue>& B)
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

	bool StaticParameterSetsEqualExact(
		const FStaticParameterSet& A,
		const FStaticParameterSet& B)
	{
		return A.Equivalent(B)
			&& A.StaticSwitchParameters == B.StaticSwitchParameters;
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

	bool FindGlobalTextureParameter(
		UMaterialInstanceConstant* Instance,
		const FName ParameterName,
		FMaterialParameterInfo& OutInfo,
		FString& OutError)
	{
		TArray<FMaterialParameterInfo> ParameterInfos;
		TArray<FGuid> ParameterIds;
		Instance->GetAllTextureParameterInfo(ParameterInfos, ParameterIds);
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
				TEXT("Expected exactly one global texture parameter named %s; found %d."),
				*ParameterName.ToString(),
				MatchCount);
			return false;
		}
		return true;
	}

	bool FindGlobalStaticSwitchParameter(
		UMaterialInstanceConstant* Instance,
		const FName ParameterName,
		FMaterialParameterInfo& OutInfo,
		FString& OutError)
	{
		TArray<FMaterialParameterInfo> ParameterInfos;
		TArray<FGuid> ParameterIds;
		Instance->GetAllStaticSwitchParameterInfo(ParameterInfos, ParameterIds);
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
				TEXT("Expected exactly one global static switch parameter named %s; found %d."),
				*ParameterName.ToString(),
				MatchCount);
			return false;
		}
		return true;
	}

	bool ReadStaticSwitchParameter(
		UMaterialInstanceConstant* Instance,
		const FMaterialParameterInfo& ParameterInfo,
		bool& OutValue,
		FGuid& OutExpressionGuid,
		bool& OutOverride)
	{
		if (!Instance->GetStaticSwitchParameterValue(
			FHashedMaterialParameterInfo(ParameterInfo),
			OutValue,
			OutExpressionGuid,
			false))
		{
			return false;
		}

		const FStaticParameterSet StaticParameters = Instance->GetStaticParameters();
		int32 MatchCount = 0;
		for (const FStaticSwitchParameter& Parameter : StaticParameters.StaticSwitchParameters)
		{
			if (Parameter.ParameterInfo == ParameterInfo)
			{
				OutOverride = Parameter.bOverride;
				++MatchCount;
			}
		}
		return MatchCount == 1;
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

	bool ReadTextureParameter(
		UMaterialInstanceConstant* Instance,
		const FMaterialParameterInfo& ParameterInfo,
		UTexture*& OutValue)
	{
		return Instance->GetTextureParameterValue(FHashedMaterialParameterInfo(ParameterInfo), OutValue);
	}

	FString FormatTextureParameterValue(const UTexture* Value)
	{
		return Value ? Value->GetPathName() : FString();
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
	FString TestFailureInjection;
	FParse::Value(*Params, TEXT("Patch="), PatchFilename);
	FParse::Value(*Params, TEXT("Policy="), PolicyFilename);
	FParse::Value(*Params, TEXT("Report="), ReportFilename);
	FParse::Value(*Params, TEXT("BackupDir="), BackupDirectory);
	FParse::Value(*Params, TEXT("Mode="), Mode);
	FParse::Value(*Params, TEXT("TestFailureInjection="), TestFailureInjection);

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

	const bool bDirtyPackageInjection =
		TestFailureInjection.Equals(TEXT("DirtyPackage"), ESearchCase::CaseSensitive);
	const bool bSaveFailureInjection =
		TestFailureInjection.Equals(TEXT("SaveFailure"), ESearchCase::CaseSensitive);
	const bool bTestFailureInjectionRequested = !TestFailureInjection.IsEmpty();
	const bool bAuthorizedFailureFixture =
		AssetPath.StartsWith(
			TEXT("/Game/UEAgentKitWriteTests/ScalarRegression/"),
			ESearchCase::CaseSensitive)
		&& ActualAssetClass.Equals(
			TEXT("/Script/UEAgentKitEditor.UEAgentKitScalarWriteFixtureAsset"),
			ESearchCase::CaseSensitive)
		&& PatchId.StartsWith(TEXT("scalar-failure-"), ESearchCase::CaseSensitive);
	if (bTestFailureInjectionRequested
		&& ((!bDirtyPackageInjection && !bSaveFailureInjection) || !bAuthorizedFailureFixture))
	{
		UE_LOG(
			LogAssetPatch,
			Error,
			TEXT("Test failure injection is restricted to the native scalar regression fixture."));
		return 25;
	}
	if (bSaveFailureInjection && !bCommit)
	{
		UE_LOG(LogAssetPatch, Error, TEXT("SaveFailure injection requires Commit mode."));
		return 25;
	}

	UPackage* Package = Asset->GetOutermost();
	if (bDirtyPackageInjection)
	{
		Package->MarkPackageDirty();
	}
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
	const bool bMaterialTextureOperation =
		Operation.Equals(TEXT("setMaterialInstanceTextureParameter"), ESearchCase::CaseSensitive);
	const bool bMaterialStaticSwitchOperation =
		Operation.Equals(TEXT("setMaterialInstanceStaticSwitchParameter"), ESearchCase::CaseSensitive);
	const bool bDataTableCellOperation =
		Operation.Equals(TEXT("setDataTableCell"), ESearchCase::CaseSensitive);
	const bool bDataTableRowFieldsOperation =
		Operation.Equals(TEXT("setDataTableRowFields"), ESearchCase::CaseSensitive);
	const bool bDataTableAddRowOperation =
		Operation.Equals(TEXT("addDataTableRow"), ESearchCase::CaseSensitive);
	const bool bDataTableRemoveRowOperation =
		Operation.Equals(TEXT("removeDataTableRow"), ESearchCase::CaseSensitive);
	const bool bDataTableRenameRowOperation =
		Operation.Equals(TEXT("renameDataTableRow"), ESearchCase::CaseSensitive);
	if ((!bAssetPropertyOperation
			&& !bMaterialScalarOperation
			&& !bMaterialVectorOperation
			&& !bMaterialTextureOperation
			&& !bMaterialStaticSwitchOperation
			&& !bDataTableCellOperation
			&& !bDataTableRowFieldsOperation
			&& !bDataTableAddRowOperation
			&& !bDataTableRemoveRowOperation
			&& !bDataTableRenameRowOperation)
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
		Report->SetStringField(TEXT("executorVersion"), TEXT("0.5.1"));
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
		Report->SetStringField(TEXT("executorVersion"), TEXT("0.5.1"));
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


	if (bMaterialTextureOperation)
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
			UE_LOG(LogAssetPatch, Error, TEXT("Material texture parameterName is required."));
			return 17;
		}
		const FString ParameterAuthorization =
			ActualAssetClass + TEXT("#Texture#") + ParameterNameText;
		if (!ContainsExact(Policy.AllowedMaterialParameters, ParameterAuthorization))
		{
			UE_LOG(
				LogAssetPatch,
				Error,
				TEXT("Material parameter is not authorized by policy: %s"),
				*ParameterAuthorization);
			return 17;
		}

		FString NewTexturePath;
		if (!OperationObject->TryGetStringField(TEXT("value"), NewTexturePath))
		{
			UE_LOG(LogAssetPatch, Error, TEXT("Material texture value must be an asset object path string."));
			return 18;
		}
		NewTexturePath = NormalizeObjectPath(NewTexturePath);
		if (!IsReferenceAllowed(Policy, NewTexturePath))
		{
			UE_LOG(LogAssetPatch, Error, TEXT("Referenced texture is outside authorized roots: %s"), *NewTexturePath);
			return 18;
		}
		UTexture* NewTexture = LoadObject<UTexture>(nullptr, *NewTexturePath);
		if (!NewTexture)
		{
			UE_LOG(LogAssetPatch, Error, TEXT("Could not load referenced texture: %s"), *NewTexturePath);
			return 18;
		}
		const FString NewTextureClass = NewTexture->GetClass()->GetPathName();
		if (!ContainsExact(Policy.AllowedReferenceClasses, NewTextureClass))
		{
			UE_LOG(
				LogAssetPatch,
				Error,
				TEXT("Referenced texture class is not authorized: %s"),
				*NewTextureClass);
			return 18;
		}

		FMaterialParameterInfo ParameterInfo;
		if (!FindGlobalTextureParameter(MaterialInstance, FName(*ParameterNameText), ParameterInfo, Error))
		{
			UE_LOG(LogAssetPatch, Error, TEXT("%s"), *Error);
			return 17;
		}

		UTexture* BeforeTextureValue = nullptr;
		if (!ReadTextureParameter(MaterialInstance, ParameterInfo, BeforeTextureValue))
		{
			UE_LOG(LogAssetPatch, Error, TEXT("Could not read material texture parameter."));
			return 17;
		}
		const TArray<FTextureParameterValue> OriginalTextureParameters =
			MaterialInstance->TextureParameterValues;

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
		// UE 5.6 applies and updates the value but its library function always returns false.
		// Treat the exact parameter read-back below as the authoritative result.
		UMaterialEditingLibrary::SetMaterialInstanceTextureParameterValue(
			MaterialInstance,
			FName(*ParameterNameText),
			NewTexture,
			EMaterialParameterAssociation::GlobalParameter);

		UTexture* AfterTextureValue = nullptr;
		if (!ReadTextureParameter(MaterialInstance, ParameterInfo, AfterTextureValue)
			|| AfterTextureValue != NewTexture)
		{
			MaterialInstance->TextureParameterValues = OriginalTextureParameters;
			UMaterialEditingLibrary::UpdateMaterialInstance(MaterialInstance);
			Package->SetDirtyFlag(bOriginalDirty);
			UE_LOG(LogAssetPatch, Error, TEXT("Material texture parameter read-back verification failed."));
			return 20;
		}

		bool bSaved = false;
		bool bRolledBack = false;
		bool bStructureMatch = true;
		UTexture* RestoredTextureValue = BeforeTextureValue;
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
			MaterialInstance->TextureParameterValues = OriginalTextureParameters;
			UMaterialEditingLibrary::UpdateMaterialInstance(MaterialInstance);
			Package->SetDirtyFlag(bOriginalDirty);
			bStructureMatch = TextureParameterArraysEqualExact(
				OriginalTextureParameters,
				MaterialInstance->TextureParameterValues);
			if (!ReadTextureParameter(MaterialInstance, ParameterInfo, RestoredTextureValue))
			{
				UE_LOG(LogAssetPatch, Error, TEXT("Could not read restored material texture parameter."));
				return 22;
			}
			bRolledBack = true;
		}

		const FString AfterRevision = HashPackageFile(Package);
		const bool bRestoredValueMatch = !bRolledBack || RestoredTextureValue == BeforeTextureValue;
		const TSharedRef<FJsonObject> Report = MakeShared<FJsonObject>();
		Report->SetStringField(TEXT("schemaVersion"), TEXT("1.0"));
		Report->SetStringField(TEXT("executorVersion"), TEXT("0.5.1"));
		Report->SetStringField(TEXT("mode"), bCommit ? TEXT("Commit") : TEXT("DryRun"));
		Report->SetStringField(TEXT("patchId"), PatchId);
		Report->SetStringField(TEXT("projectName"), FApp::GetProjectName());
		Report->SetStringField(TEXT("assetPath"), AssetPath);
		Report->SetStringField(TEXT("assetClass"), ActualAssetClass);
		Report->SetStringField(TEXT("operation"), Operation);
		Report->SetObjectField(TEXT("target"), TargetObject);
		Report->SetStringField(
			TEXT("targetDescription"),
			TEXT("material-instance-texture:") + ParameterNameText);
		Report->SetStringField(TEXT("targetType"), TEXT("MaterialTextureParameter(UTexture)"));
		Report->SetStringField(TEXT("beforeValue"), FormatTextureParameterValue(BeforeTextureValue));
		Report->SetStringField(TEXT("afterValue"), FormatTextureParameterValue(AfterTextureValue));
		Report->SetStringField(TEXT("restoredValue"), FormatTextureParameterValue(RestoredTextureValue));
		Report->SetStringField(TEXT("referencedAssetPath"), NewTexturePath);
		Report->SetStringField(TEXT("referencedAssetClass"), NewTextureClass);
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
			TEXT("Material texture patch succeeded. Mode=%s Asset=%s Parameter=%s Before=%s After=%s"),
			bCommit ? TEXT("Commit") : TEXT("DryRun"),
			*AssetPath,
			*ParameterNameText,
			*FormatTextureParameterValue(BeforeTextureValue),
			*FormatTextureParameterValue(AfterTextureValue));
		return 0;
	}


	if (bMaterialStaticSwitchOperation)
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
			UE_LOG(LogAssetPatch, Error, TEXT("Material static switch parameterName is required."));
			return 17;
		}
		const FString ParameterAuthorization =
			ActualAssetClass + TEXT("#StaticSwitch#") + ParameterNameText;
		if (!ContainsExact(Policy.AllowedMaterialParameters, ParameterAuthorization))
		{
			UE_LOG(
				LogAssetPatch,
				Error,
				TEXT("Material parameter is not authorized by policy: %s"),
				*ParameterAuthorization);
			return 17;
		}

		bool NewSwitchValue = false;
		if (!OperationObject->TryGetBoolField(TEXT("value"), NewSwitchValue))
		{
			UE_LOG(LogAssetPatch, Error, TEXT("Material static switch value must be a JSON boolean."));
			return 18;
		}

		FMaterialParameterInfo ParameterInfo;
		if (!FindGlobalStaticSwitchParameter(
			MaterialInstance,
			FName(*ParameterNameText),
			ParameterInfo,
			Error))
		{
			UE_LOG(LogAssetPatch, Error, TEXT("%s"), *Error);
			return 17;
		}

		bool BeforeSwitchValue = false;
		bool bBeforeOverride = false;
		FGuid BeforeExpressionGuid;
		if (!ReadStaticSwitchParameter(
			MaterialInstance,
			ParameterInfo,
			BeforeSwitchValue,
			BeforeExpressionGuid,
			bBeforeOverride))
		{
			UE_LOG(LogAssetPatch, Error, TEXT("Could not read material static switch parameter."));
			return 17;
		}
		const FStaticParameterSet OriginalStaticParameters = MaterialInstance->GetStaticParameters();

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
		// UE 5.6 applies and updates the value but its library function always returns false.
		// Verify the exact value, Expression GUID, and override state instead.
		UMaterialEditingLibrary::SetMaterialInstanceStaticSwitchParameterValue(
			MaterialInstance,
			FName(*ParameterNameText),
			NewSwitchValue,
			EMaterialParameterAssociation::GlobalParameter,
			true);

		bool AfterSwitchValue = false;
		bool bAfterOverride = false;
		FGuid AfterExpressionGuid;
		if (!ReadStaticSwitchParameter(
				MaterialInstance,
				ParameterInfo,
				AfterSwitchValue,
				AfterExpressionGuid,
				bAfterOverride)
			|| AfterSwitchValue != NewSwitchValue
			|| AfterExpressionGuid != BeforeExpressionGuid
			|| !bAfterOverride)
		{
			MaterialInstance->UpdateStaticPermutation(OriginalStaticParameters);
			UMaterialEditingLibrary::UpdateMaterialInstance(MaterialInstance);
			Package->SetDirtyFlag(bOriginalDirty);
			UE_LOG(LogAssetPatch, Error, TEXT("Material static switch read-back verification failed."));
			return 20;
		}

		bool bSaved = false;
		bool bRolledBack = false;
		bool bStructureMatch = true;
		bool RestoredSwitchValue = BeforeSwitchValue;
		bool bRestoredOverride = bBeforeOverride;
		FGuid RestoredExpressionGuid = BeforeExpressionGuid;
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
			MaterialInstance->UpdateStaticPermutation(OriginalStaticParameters);
			UMaterialEditingLibrary::UpdateMaterialInstance(MaterialInstance);
			Package->SetDirtyFlag(bOriginalDirty);
			const FStaticParameterSet RestoredStaticParameters = MaterialInstance->GetStaticParameters();
			bStructureMatch = StaticParameterSetsEqualExact(
				OriginalStaticParameters,
				RestoredStaticParameters);
			if (!ReadStaticSwitchParameter(
				MaterialInstance,
				ParameterInfo,
				RestoredSwitchValue,
				RestoredExpressionGuid,
				bRestoredOverride))
			{
				UE_LOG(LogAssetPatch, Error, TEXT("Could not read restored material static switch parameter."));
				return 22;
			}
			bRolledBack = true;
		}

		const FString AfterRevision = HashPackageFile(Package);
		const bool bRestoredValueMatch =
			!bRolledBack
			|| (RestoredSwitchValue == BeforeSwitchValue
				&& RestoredExpressionGuid == BeforeExpressionGuid
				&& bRestoredOverride == bBeforeOverride);
		const TSharedRef<FJsonObject> Report = MakeShared<FJsonObject>();
		Report->SetStringField(TEXT("schemaVersion"), TEXT("1.0"));
		Report->SetStringField(TEXT("executorVersion"), TEXT("0.5.1"));
		Report->SetStringField(TEXT("mode"), bCommit ? TEXT("Commit") : TEXT("DryRun"));
		Report->SetStringField(TEXT("patchId"), PatchId);
		Report->SetStringField(TEXT("projectName"), FApp::GetProjectName());
		Report->SetStringField(TEXT("assetPath"), AssetPath);
		Report->SetStringField(TEXT("assetClass"), ActualAssetClass);
		Report->SetStringField(TEXT("operation"), Operation);
		Report->SetObjectField(TEXT("target"), TargetObject);
		Report->SetStringField(
			TEXT("targetDescription"),
			TEXT("material-instance-static-switch:") + ParameterNameText);
		Report->SetStringField(TEXT("targetType"), TEXT("MaterialStaticSwitchParameter(bool)"));
		Report->SetBoolField(TEXT("beforeValue"), BeforeSwitchValue);
		Report->SetBoolField(TEXT("afterValue"), AfterSwitchValue);
		Report->SetBoolField(TEXT("restoredValue"), RestoredSwitchValue);
		Report->SetStringField(TEXT("beforeExpressionGuid"), BeforeExpressionGuid.ToString(EGuidFormats::DigitsWithHyphensLower));
		Report->SetStringField(TEXT("afterExpressionGuid"), AfterExpressionGuid.ToString(EGuidFormats::DigitsWithHyphensLower));
		Report->SetStringField(TEXT("restoredExpressionGuid"), RestoredExpressionGuid.ToString(EGuidFormats::DigitsWithHyphensLower));
		Report->SetBoolField(TEXT("beforeOverride"), bBeforeOverride);
		Report->SetBoolField(TEXT("afterOverride"), bAfterOverride);
		Report->SetBoolField(TEXT("restoredOverride"), bRestoredOverride);
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
			TEXT("Material static switch patch succeeded. Mode=%s Asset=%s Parameter=%s Before=%s After=%s"),
			bCommit ? TEXT("Commit") : TEXT("DryRun"),
			*AssetPath,
			*ParameterNameText,
			BeforeSwitchValue ? TEXT("true") : TEXT("false"),
			AfterSwitchValue ? TEXT("true") : TEXT("false"));
		return 0;
	}

	if (bDataTableAddRowOperation || bDataTableRemoveRowOperation || bDataTableRenameRowOperation)
	{
		UDataTable* DataTable = Cast<UDataTable>(Asset);
		if (!DataTable)
		{
			UE_LOG(LogAssetPatch, Error, TEXT("DataTable row operations require a DataTable asset."));
			return 17;
		}

		UScriptStruct* RowStruct = const_cast<UScriptStruct*>(DataTable->GetRowStruct());
		if (!RowStruct)
		{
			UE_LOG(LogAssetPatch, Error, TEXT("DataTable has no valid row struct."));
			return 17;
		}
		const FString RowStructPath = RowStruct->GetPathName();

		FString RowNameText;
		TargetObject->TryGetStringField(TEXT("rowName"), RowNameText);
		if (RowNameText.IsEmpty() || RowNameText.Len() > 256)
		{
			UE_LOG(LogAssetPatch, Error, TEXT("DataTable rowName is required and must not exceed 256 characters."));
			return 17;
		}
		const FName RowName(*RowNameText);
		FString NewRowNameText;
		FName NewRowName = NAME_None;
		if (bDataTableRenameRowOperation)
		{
			TargetObject->TryGetStringField(TEXT("newRowName"), NewRowNameText);
			if (NewRowNameText.IsEmpty() || NewRowNameText.Len() > 256 || NewRowNameText == RowNameText)
			{
				UE_LOG(LogAssetPatch, Error, TEXT("renameDataTableRow requires a distinct newRowName."));
				return 17;
			}
			NewRowName = FName(*NewRowNameText);
		}

		const uint8* ExistingRowData = DataTable->FindRowUnchecked(RowName);
		if (bDataTableAddRowOperation && ExistingRowData)
		{
			UE_LOG(LogAssetPatch, Error, TEXT("DataTable row already exists: %s"), *RowNameText);
			return 17;
		}
		if ((bDataTableRemoveRowOperation || bDataTableRenameRowOperation) && !ExistingRowData)
		{
			UE_LOG(LogAssetPatch, Error, TEXT("DataTable source row was not found: %s"), *RowNameText);
			return 17;
		}
		if (bDataTableRenameRowOperation && DataTable->FindRowUnchecked(NewRowName))
		{
			UE_LOG(LogAssetPatch, Error, TEXT("DataTable destination row already exists: %s"), *NewRowNameText);
			return 17;
		}

		const TSharedPtr<FJsonValue> ConfirmationValue = OperationObject->TryGetField(TEXT("value"));
		if ((bDataTableRemoveRowOperation || bDataTableRenameRowOperation)
			&& (!ConfirmationValue.IsValid() || ConfirmationValue->Type != EJson::Boolean || !ConfirmationValue->AsBool()))
		{
			UE_LOG(LogAssetPatch, Error, TEXT("Structural DataTable row operations require value=true."));
			return 18;
		}

		TArray<FAssetIdentifier> RowReferencers;
		const bool bReferenceImpactChecked = bDataTableRemoveRowOperation || bDataTableRenameRowOperation;
		if (bReferenceImpactChecked)
		{
			FindDataTableRowReferencers(DataTable, RowName, RowReferencers);
			if (!RowReferencers.IsEmpty())
			{
				TArray<FString> ReferencerNames;
				const int32 DisplayCount = FMath::Min(RowReferencers.Num(), 8);
				ReferencerNames.Reserve(DisplayCount);
				for (int32 Index = 0; Index < DisplayCount; ++Index)
				{
					ReferencerNames.Add(RowReferencers[Index].ToString());
				}
				UE_LOG(
					LogAssetPatch,
					Error,
					TEXT("DataTable row is referenced and cannot be removed or renamed. Row=%s ReferenceCount=%d Referencers=%s"),
					*RowNameText,
					RowReferencers.Num(),
					*FString::Join(ReferencerNames, TEXT(", ")));
				return 17;
			}
		}

		FStructOnScope RowSnapshot(RowStruct);
		if (!RowSnapshot.IsValid())
		{
			UE_LOG(LogAssetPatch, Error, TEXT("Could not allocate DataTable row snapshot."));
			return 18;
		}
		if (ExistingRowData)
		{
			RowStruct->CopyScriptStruct(RowSnapshot.GetStructMemory(), ExistingRowData);
		}

		TArray<FString> FieldNames;
		TArray<FProperty*> Fields;
		TSharedRef<FJsonObject> AppliedValues = MakeShared<FJsonObject>();
		if (bDataTableAddRowOperation)
		{
			const TSharedPtr<FJsonObject>* ValuesObjectPtr = nullptr;
			if (!OperationObject->TryGetObjectField(TEXT("value"), ValuesObjectPtr)
				|| !ValuesObjectPtr || !ValuesObjectPtr->IsValid() || (*ValuesObjectPtr)->Values.Num() > 32)
			{
				UE_LOG(LogAssetPatch, Error, TEXT("addDataTableRow requires an object containing at most 32 fields."));
				return 18;
			}
			const TSharedPtr<FJsonObject> ValuesObject = *ValuesObjectPtr;
			ValuesObject->Values.GetKeys(FieldNames);
			FieldNames.Sort();
			for (const FString& FieldName : FieldNames)
			{
				const TSharedPtr<FJsonValue> FieldValue = ValuesObject->Values.FindChecked(FieldName);
				if (FieldName.IsEmpty() || FieldName.Len() > 256 || FieldName.Contains(TEXT("."))
					|| !FieldValue.IsValid() || FieldValue->Type == EJson::Null
					|| FieldValue->Type == EJson::Array || FieldValue->Type == EJson::Object)
				{
					UE_LOG(LogAssetPatch, Error, TEXT("Invalid DataTable row field: %s"), *FieldName);
					return 18;
				}
				const FString Authorization = ActualAssetClass + TEXT("#") + RowStructPath + TEXT("#") + FieldName;
				if (!ContainsExact(Policy.AllowedDataTableFields, Authorization))
				{
					UE_LOG(LogAssetPatch, Error, TEXT("DataTable field is not authorized by policy: %s"), *Authorization);
					return 17;
				}
				FProperty* Field = FindFProperty<FProperty>(RowStruct, FName(*FieldName));
				if (!Field || Field->ArrayDim != 1 || Field->HasAnyPropertyFlags(CPF_Transient | CPF_DuplicateTransient | CPF_NonPIEDuplicateTransient))
				{
					UE_LOG(LogAssetPatch, Error, TEXT("DataTable field is missing, fixed-array, or transient: %s"), *FieldName);
					return 17;
				}
				void* Address = Field->ContainerPtrToValuePtr<void>(RowSnapshot.GetStructMemory());
				if (!SetPropertyFromJson(Field, Address, FieldValue, Error))
				{
					UE_LOG(LogAssetPatch, Error, TEXT("Invalid DataTable value for field %s: %s"), *FieldName, *Error);
					return 18;
				}
				Fields.Add(Field);
				FString ValueText;
				ReadPropertyValue(DataTable, Field, Address, ValueText);
				AppliedValues->SetStringField(FieldName, ValueText);
			}
		}

		TArray<FName> OriginalRowNames = DataTable->GetRowNames();
		OriginalRowNames.Sort(FNameLexicalLess());
		TMap<FName, TUniquePtr<FStructOnScope>> OriginalRows;
		for (const FName& OriginalName : OriginalRowNames)
		{
			const uint8* OriginalData = DataTable->FindRowUnchecked(OriginalName);
			TUniquePtr<FStructOnScope> Snapshot = MakeUnique<FStructOnScope>(RowStruct);
			if (!OriginalData || !Snapshot->IsValid())
			{
				UE_LOG(LogAssetPatch, Error, TEXT("Could not snapshot DataTable row: %s"), *OriginalName.ToString());
				return 18;
			}
			RowStruct->CopyScriptStruct(Snapshot->GetStructMemory(), OriginalData);
			OriginalRows.Add(OriginalName, MoveTemp(Snapshot));
		}
		auto RestoreOriginalTable = [&]()
		{
			DataTable->EmptyTable();
			for (const FName& OriginalName : OriginalRowNames)
			{
				const TUniquePtr<FStructOnScope>* Snapshot = OriginalRows.Find(OriginalName);
				if (!Snapshot || !Snapshot->IsValid())
				{
					return false;
				}
				DataTable->AddRow(
					OriginalName,
					*reinterpret_cast<const FTableRowBase*>((*Snapshot)->GetStructMemory()));
			}
			return true;
		};
		auto TableMatchesOriginal = [&]()
		{
			TArray<FName> CurrentNames = DataTable->GetRowNames();
			CurrentNames.Sort(FNameLexicalLess());
			if (CurrentNames != OriginalRowNames)
			{
				return false;
			}
			for (const FName& OriginalName : OriginalRowNames)
			{
				const uint8* CurrentData = DataTable->FindRowUnchecked(OriginalName);
				const TUniquePtr<FStructOnScope>* Snapshot = OriginalRows.Find(OriginalName);
				if (!CurrentData || !Snapshot
					|| !RowStruct->CompareScriptStruct(CurrentData, (*Snapshot)->GetStructMemory(), PPF_None))
				{
					return false;
				}
			}
			return true;
		};
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

		DataTable->Modify();
		if (bDataTableAddRowOperation)
		{
			DataTable->AddRow(RowName, *reinterpret_cast<const FTableRowBase*>(RowSnapshot.GetStructMemory()));
		}
		else if (bDataTableRemoveRowOperation)
		{
			// UDataTable::RemoveRow notifies with the removed key. GameplayTag-backed tables can
			// synchronously query that now-missing key, so detach and notify only the stable final table.
			TMap<FName, uint8*>& MutableRowMap =
				const_cast<TMap<FName, uint8*>&>(DataTable->GetRowMap());
			uint8* RemovedRowData = nullptr;
			if (!MutableRowMap.RemoveAndCopyValue(RowName, RemovedRowData) || !RemovedRowData)
			{
				RestoreOriginalTable();
				Package->SetDirtyFlag(bOriginalDirty);
				UE_LOG(LogAssetPatch, Error, TEXT("Could not detach DataTable row for removal."));
				return 20;
			}
			RowStruct->DestroyStruct(RemovedRowData);
			FMemory::Free(RemovedRowData);
			DataTable->HandleDataTableChanged(NAME_None);
		}
		else
		{
			// AddRow + RemoveRow exposes an invalid intermediate state to DataTable listeners. Move
			// the owned row allocation between keys, then publish the completed rename exactly once.
			TMap<FName, uint8*>& MutableRowMap =
				const_cast<TMap<FName, uint8*>&>(DataTable->GetRowMap());
			uint8* MovedRowData = nullptr;
			if (!MutableRowMap.RemoveAndCopyValue(RowName, MovedRowData) || !MovedRowData)
			{
				RestoreOriginalTable();
				Package->SetDirtyFlag(bOriginalDirty);
				UE_LOG(LogAssetPatch, Error, TEXT("Could not detach DataTable source row for rename."));
				return 20;
			}
			MutableRowMap.Add(NewRowName, MovedRowData);
			DataTable->HandleDataTableChanged(NewRowName);
		}
		Package->MarkPackageDirty();

		const uint8* AppliedRowData = bDataTableRenameRowOperation
			? DataTable->FindRowUnchecked(NewRowName)
			: DataTable->FindRowUnchecked(RowName);
		bool bAppliedStructureMatch = true;
		if (bDataTableAddRowOperation || bDataTableRenameRowOperation)
		{
			bAppliedStructureMatch = AppliedRowData
				&& RowStruct->CompareScriptStruct(AppliedRowData, RowSnapshot.GetStructMemory(), PPF_None);
		}
		else
		{
			bAppliedStructureMatch = AppliedRowData == nullptr;
		}
		if (bDataTableRenameRowOperation)
		{
			bAppliedStructureMatch = bAppliedStructureMatch && DataTable->FindRowUnchecked(RowName) == nullptr;
		}
		for (const FName& OriginalName : OriginalRowNames)
		{
			if ((bDataTableRemoveRowOperation || bDataTableRenameRowOperation) && OriginalName == RowName)
			{
				continue;
			}
			const uint8* CurrentData = DataTable->FindRowUnchecked(OriginalName);
			const TUniquePtr<FStructOnScope>* Snapshot = OriginalRows.Find(OriginalName);
			bAppliedStructureMatch = bAppliedStructureMatch && CurrentData && Snapshot
				&& RowStruct->CompareScriptStruct(CurrentData, (*Snapshot)->GetStructMemory(), PPF_None);
		}
		const int32 AppliedRowCount = DataTable->GetRowNames().Num();
		if (!bAppliedStructureMatch)
		{
			RestoreOriginalTable();
			Package->SetDirtyFlag(bOriginalDirty);
			UE_LOG(LogAssetPatch, Error, TEXT("DataTable row operation read-back verification failed."));
			return 20;
		}

		bool bSaved = false;
		bool bRolledBack = false;
		bool bRollbackStructureMatch = true;
		if (bCommit)
		{
			if (!SaveAssetPackage(DataTable, PackageFilename, Error))
			{
				IFileManager::Get().Copy(*PackageFilename, *BackupFilename, true, true);
				UE_LOG(LogAssetPatch, Error, TEXT("%s Backup restored."), *Error);
				return 21;
			}
			bSaved = true;
		}
		else
		{
			const bool bRestoreSucceeded = RestoreOriginalTable();
			Package->SetDirtyFlag(bOriginalDirty);
			bRollbackStructureMatch = bRestoreSucceeded && TableMatchesOriginal();
			bRolledBack = true;
		}

		const FString AfterRevision = HashPackageFile(Package);
		const TSharedRef<FJsonObject> Report = MakeShared<FJsonObject>();
		Report->SetStringField(TEXT("schemaVersion"), TEXT("1.0"));
		Report->SetStringField(TEXT("executorVersion"), TEXT("0.5.1"));
		Report->SetStringField(TEXT("mode"), bCommit ? TEXT("Commit") : TEXT("DryRun"));
		Report->SetStringField(TEXT("patchId"), PatchId);
		Report->SetStringField(TEXT("projectName"), FApp::GetProjectName());
		Report->SetStringField(TEXT("assetPath"), AssetPath);
		Report->SetStringField(TEXT("assetClass"), ActualAssetClass);
		Report->SetStringField(TEXT("operation"), Operation);
		Report->SetObjectField(TEXT("target"), TargetObject);
		Report->SetStringField(TEXT("rowStructPath"), RowStructPath);
		Report->SetStringField(TEXT("sourceRowName"), RowNameText);
		Report->SetStringField(TEXT("destinationRowName"), NewRowNameText);
		Report->SetBoolField(TEXT("referenceImpactChecked"), bReferenceImpactChecked);
		if (bReferenceImpactChecked)
		{
			Report->SetStringField(TEXT("referenceImpactSource"), TEXT("asset-registry-searchable-name"));
			Report->SetNumberField(TEXT("referenceCount"), RowReferencers.Num());
		}
		Report->SetObjectField(TEXT("appliedValues"), AppliedValues);
		Report->SetNumberField(TEXT("beforeRowCount"), OriginalRowNames.Num());
		Report->SetNumberField(TEXT("afterRowCount"), AppliedRowCount);
		Report->SetStringField(TEXT("targetDescription"), TEXT("data-table-row-operation:") + RowNameText);
		Report->SetStringField(TEXT("targetType"), TEXT("DataTableRow"));
		Report->SetStringField(TEXT("beforeRevision"), BeforeRevision);
		Report->SetStringField(TEXT("afterRevision"), AfterRevision);
		Report->SetBoolField(TEXT("compiled"), false);
		Report->SetBoolField(TEXT("saved"), bSaved);
		Report->SetBoolField(TEXT("rolledBack"), bRolledBack);
		Report->SetBoolField(TEXT("appliedStructureMatch"), bAppliedStructureMatch);
		Report->SetBoolField(TEXT("rollbackStructureMatch"), !bRolledBack || bRollbackStructureMatch);
		Report->SetBoolField(TEXT("diskUnchanged"), BeforeRevision.Equals(AfterRevision, ESearchCase::IgnoreCase));
		Report->SetStringField(TEXT("backupPath"), BackupFilename);
		if (!SaveReport(ReportFilename, Report, Error))
		{
			if (bCommit && !BackupFilename.IsEmpty()) IFileManager::Get().Copy(*PackageFilename, *BackupFilename, true, true);
			UE_LOG(LogAssetPatch, Error, TEXT("%s Disk backup restored."), *Error);
			return 23;
		}
		if (bRolledBack && (!bRollbackStructureMatch || !BeforeRevision.Equals(AfterRevision, ESearchCase::IgnoreCase)))
		{
			UE_LOG(LogAssetPatch, Error, TEXT("DataTable row operation Dry Run rollback verification failed."));
			return 22;
		}
		UE_LOG(LogAssetPatch, Display, TEXT("DataTable row operation succeeded. Mode=%s Operation=%s Row=%s"),
			bCommit ? TEXT("Commit") : TEXT("DryRun"), *Operation, *RowNameText);
		return 0;
	}

	if (bDataTableCellOperation || bDataTableRowFieldsOperation)
	{
		UDataTable* DataTable = Cast<UDataTable>(Asset);
		if (!DataTable)
		{
			UE_LOG(LogAssetPatch, Error, TEXT("DataTable field operations require a DataTable asset."));
			return 17;
		}

		FString RowNameText;
		TargetObject->TryGetStringField(TEXT("rowName"), RowNameText);
		if (RowNameText.IsEmpty())
		{
			UE_LOG(LogAssetPatch, Error, TEXT("DataTable rowName is required."));
			return 17;
		}

		UScriptStruct* RowStruct = const_cast<UScriptStruct*>(DataTable->GetRowStruct());
		if (!RowStruct)
		{
			UE_LOG(LogAssetPatch, Error, TEXT("DataTable has no valid row struct."));
			return 17;
		}
		const FString RowStructPath = RowStruct->GetPathName();
		const FName RowName(*RowNameText);
		uint8* RowData = DataTable->FindRowUnchecked(RowName);
		if (!RowData)
		{
			UE_LOG(LogAssetPatch, Error, TEXT("DataTable row was not found: %s"), *RowNameText);
			return 17;
		}

		TArray<FString> FieldNames;
		TArray<TSharedPtr<FJsonValue>> NewValues;
		if (bDataTableCellOperation)
		{
			FString FieldNameText;
			TargetObject->TryGetStringField(TEXT("fieldName"), FieldNameText);
			const TSharedPtr<FJsonValue> NewValue = OperationObject->TryGetField(TEXT("value"));
			if (FieldNameText.IsEmpty()
				|| FieldNameText.Contains(TEXT("."))
				|| !NewValue.IsValid()
				|| NewValue->Type == EJson::Null
				|| NewValue->Type == EJson::Array
				|| NewValue->Type == EJson::Object)
			{
				UE_LOG(LogAssetPatch, Error, TEXT("DataTable cell requires one top-level field and a non-null JSON scalar."));
				return 18;
			}
			FieldNames.Add(FieldNameText);
			NewValues.Add(NewValue);
		}
		else
		{
			const TSharedPtr<FJsonObject>* FieldValuesObjectPtr = nullptr;
			if (!OperationObject->TryGetObjectField(TEXT("value"), FieldValuesObjectPtr)
				|| !FieldValuesObjectPtr
				|| !FieldValuesObjectPtr->IsValid()
				|| (*FieldValuesObjectPtr)->Values.Num() < 1
				|| (*FieldValuesObjectPtr)->Values.Num() > 32)
			{
				UE_LOG(LogAssetPatch, Error, TEXT("setDataTableRowFields requires an object containing 1 through 32 fields."));
				return 18;
			}
			const TSharedPtr<FJsonObject> FieldValuesObject = *FieldValuesObjectPtr;
			FieldValuesObject->Values.GetKeys(FieldNames);
			FieldNames.Sort();
			for (const FString& FieldName : FieldNames)
			{
				const TSharedPtr<FJsonValue> FieldValue = FieldValuesObject->Values.FindChecked(FieldName);
				if (FieldName.IsEmpty()
					|| FieldName.Len() > 256
					|| FieldName.Contains(TEXT("."))
					|| !FieldValue.IsValid()
					|| FieldValue->Type == EJson::Null
					|| FieldValue->Type == EJson::Array
					|| FieldValue->Type == EJson::Object)
				{
					UE_LOG(LogAssetPatch, Error, TEXT("DataTable row fields require valid top-level names and non-null JSON scalar values."));
					return 18;
				}
				NewValues.Add(FieldValue);
			}
		}

		TArray<FProperty*> Fields;
		Fields.Reserve(FieldNames.Num());
		for (const FString& FieldName : FieldNames)
		{
			const FString FieldAuthorization =
				ActualAssetClass + TEXT("#") + RowStructPath + TEXT("#") + FieldName;
			if (!ContainsExact(Policy.AllowedDataTableFields, FieldAuthorization))
			{
				UE_LOG(
					LogAssetPatch,
					Error,
					TEXT("DataTable field is not authorized by policy: %s"),
					*FieldAuthorization);
				return 17;
			}
			FProperty* Field = FindFProperty<FProperty>(RowStruct, FName(*FieldName));
			if (!Field
				|| Field->ArrayDim != 1
				|| Field->HasAnyPropertyFlags(
					CPF_Transient | CPF_DuplicateTransient | CPF_NonPIEDuplicateTransient))
			{
				UE_LOG(LogAssetPatch, Error, TEXT("DataTable field is missing, fixed-array, or transient: %s"), *FieldName);
				return 17;
			}
			Fields.Add(Field);
		}

		FStructOnScope OriginalRow(RowStruct);
		FStructOnScope ExpectedRow(RowStruct);
		if (!OriginalRow.IsValid() || !ExpectedRow.IsValid())
		{
			UE_LOG(LogAssetPatch, Error, TEXT("Could not allocate DataTable row snapshots."));
			return 18;
		}
		RowStruct->CopyScriptStruct(OriginalRow.GetStructMemory(), RowData);
		RowStruct->CopyScriptStruct(ExpectedRow.GetStructMemory(), RowData);
		for (int32 Index = 0; Index < Fields.Num(); ++Index)
		{
			void* ExpectedValueAddress = Fields[Index]->ContainerPtrToValuePtr<void>(ExpectedRow.GetStructMemory());
			if (!SetPropertyFromJson(Fields[Index], ExpectedValueAddress, NewValues[Index], Error))
			{
				UE_LOG(LogAssetPatch, Error, TEXT("Invalid DataTable value for field %s: %s"), *FieldNames[Index], *Error);
				return 18;
			}
		}

		TSharedRef<FJsonObject> BeforeValues = MakeShared<FJsonObject>();
		TSharedRef<FJsonObject> AfterValues = MakeShared<FJsonObject>();
		TSharedRef<FJsonObject> RestoredValues = MakeShared<FJsonObject>();
		TSharedRef<FJsonObject> FieldTypes = MakeShared<FJsonObject>();
		TArray<FString> BeforeValueTexts;
		TArray<FString> AfterValueTexts;
		TArray<FString> RestoredValueTexts;
		BeforeValueTexts.SetNum(Fields.Num());
		AfterValueTexts.SetNum(Fields.Num());
		RestoredValueTexts.SetNum(Fields.Num());
		for (int32 Index = 0; Index < Fields.Num(); ++Index)
		{
			void* ValueAddress = Fields[Index]->ContainerPtrToValuePtr<void>(RowData);
			if (!ReadPropertyValue(DataTable, Fields[Index], ValueAddress, BeforeValueTexts[Index]))
			{
				UE_LOG(LogAssetPatch, Error, TEXT("Could not read original DataTable field value: %s"), *FieldNames[Index]);
				return 18;
			}
			BeforeValues->SetStringField(FieldNames[Index], BeforeValueTexts[Index]);
			FieldTypes->SetStringField(FieldNames[Index], Fields[Index]->GetClass()->GetName());
		}

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

		DataTable->Modify();
		for (int32 Index = 0; Index < Fields.Num(); ++Index)
		{
			void* ValueAddress = Fields[Index]->ContainerPtrToValuePtr<void>(RowData);
			if (!SetPropertyFromJson(Fields[Index], ValueAddress, NewValues[Index], Error))
			{
				RowStruct->CopyScriptStruct(RowData, OriginalRow.GetStructMemory());
				DataTable->HandleDataTableChanged(RowName);
				Package->SetDirtyFlag(bOriginalDirty);
				UE_LOG(LogAssetPatch, Error, TEXT("Could not apply DataTable field %s: %s"), *FieldNames[Index], *Error);
				return 20;
			}
		}
		DataTable->HandleDataTableChanged(RowName);
		Package->MarkPackageDirty();

		bool bAppliedValueMatch = true;
		const bool bAppliedStructureMatch = RowStruct->CompareScriptStruct(
			RowData,
			ExpectedRow.GetStructMemory(),
			PPF_None);
		for (int32 Index = 0; Index < Fields.Num(); ++Index)
		{
			void* ValueAddress = Fields[Index]->ContainerPtrToValuePtr<void>(RowData);
			void* ExpectedValueAddress = Fields[Index]->ContainerPtrToValuePtr<void>(ExpectedRow.GetStructMemory());
			bAppliedValueMatch = bAppliedValueMatch
				&& Fields[Index]->Identical(ValueAddress, ExpectedValueAddress, PPF_None);
		}
		if (!bAppliedStructureMatch || !bAppliedValueMatch)
		{
			RowStruct->CopyScriptStruct(RowData, OriginalRow.GetStructMemory());
			DataTable->HandleDataTableChanged(RowName);
			Package->SetDirtyFlag(bOriginalDirty);
			UE_LOG(LogAssetPatch, Error, TEXT("DataTable row read-back verification failed."));
			return 20;
		}

		for (int32 Index = 0; Index < Fields.Num(); ++Index)
		{
			void* ValueAddress = Fields[Index]->ContainerPtrToValuePtr<void>(RowData);
			if (!ReadPropertyValue(DataTable, Fields[Index], ValueAddress, AfterValueTexts[Index]))
			{
				RowStruct->CopyScriptStruct(RowData, OriginalRow.GetStructMemory());
				DataTable->HandleDataTableChanged(RowName);
				Package->SetDirtyFlag(bOriginalDirty);
				UE_LOG(LogAssetPatch, Error, TEXT("Could not read updated DataTable field value: %s"), *FieldNames[Index]);
				return 20;
			}
			AfterValues->SetStringField(FieldNames[Index], AfterValueTexts[Index]);
		}

		bool bSaved = false;
		bool bRolledBack = false;
		bool bStructureMatch = true;
		bool bRestoredValueMatch = true;
		if (bCommit)
		{
			if (!SaveAssetPackage(DataTable, PackageFilename, Error))
			{
				IFileManager::Get().Copy(*PackageFilename, *BackupFilename, true, true);
				UE_LOG(LogAssetPatch, Error, TEXT("%s Backup restored."), *Error);
				return 21;
			}
			bSaved = true;
		}
		else
		{
			RowStruct->CopyScriptStruct(RowData, OriginalRow.GetStructMemory());
			DataTable->HandleDataTableChanged(RowName);
			Package->SetDirtyFlag(bOriginalDirty);
			bStructureMatch = RowStruct->CompareScriptStruct(
				RowData,
				OriginalRow.GetStructMemory(),
				PPF_None);
			for (int32 Index = 0; Index < Fields.Num(); ++Index)
			{
				void* ValueAddress = Fields[Index]->ContainerPtrToValuePtr<void>(RowData);
				void* OriginalValueAddress = Fields[Index]->ContainerPtrToValuePtr<void>(OriginalRow.GetStructMemory());
				bRestoredValueMatch = bRestoredValueMatch
					&& Fields[Index]->Identical(ValueAddress, OriginalValueAddress, PPF_None);
				if (!ReadPropertyValue(DataTable, Fields[Index], ValueAddress, RestoredValueTexts[Index]))
				{
					UE_LOG(LogAssetPatch, Error, TEXT("Could not read restored DataTable field value: %s"), *FieldNames[Index]);
					return 22;
				}
				RestoredValues->SetStringField(FieldNames[Index], RestoredValueTexts[Index]);
			}
			bRolledBack = true;
		}

		const FString AfterRevision = HashPackageFile(Package);
		const TSharedRef<FJsonObject> Report = MakeShared<FJsonObject>();
		Report->SetStringField(TEXT("schemaVersion"), TEXT("1.0"));
		Report->SetStringField(TEXT("executorVersion"), TEXT("0.5.1"));
		Report->SetStringField(TEXT("mode"), bCommit ? TEXT("Commit") : TEXT("DryRun"));
		Report->SetStringField(TEXT("patchId"), PatchId);
		Report->SetStringField(TEXT("projectName"), FApp::GetProjectName());
		Report->SetStringField(TEXT("assetPath"), AssetPath);
		Report->SetStringField(TEXT("assetClass"), ActualAssetClass);
		Report->SetStringField(TEXT("operation"), Operation);
		Report->SetObjectField(TEXT("target"), TargetObject);
		Report->SetStringField(TEXT("rowStructPath"), RowStructPath);
		Report->SetNumberField(TEXT("fieldCount"), Fields.Num());
		Report->SetObjectField(TEXT("beforeValues"), BeforeValues);
		Report->SetObjectField(TEXT("afterValues"), AfterValues);
		Report->SetObjectField(TEXT("restoredValues"), RestoredValues);
		Report->SetObjectField(TEXT("fieldTypes"), FieldTypes);
		if (bDataTableCellOperation)
		{
			Report->SetStringField(
				TEXT("targetDescription"),
				TEXT("data-table-cell:") + RowNameText + TEXT(".") + FieldNames[0]);
			Report->SetStringField(TEXT("targetType"), Fields[0]->GetClass()->GetName());
			Report->SetStringField(TEXT("beforeValue"), BeforeValueTexts[0]);
			Report->SetStringField(TEXT("afterValue"), AfterValueTexts[0]);
			Report->SetStringField(
				TEXT("restoredValue"),
				bRolledBack ? RestoredValueTexts[0] : BeforeValueTexts[0]);
		}
		else
		{
			Report->SetStringField(
				TEXT("targetDescription"),
				TEXT("data-table-row-fields:") + RowNameText);
			Report->SetStringField(TEXT("targetType"), TEXT("DataTableRowFields"));
		}
		Report->SetStringField(TEXT("beforeRevision"), BeforeRevision);
		Report->SetStringField(TEXT("afterRevision"), AfterRevision);
		Report->SetBoolField(TEXT("compiled"), false);
		Report->SetBoolField(TEXT("saved"), bSaved);
		Report->SetBoolField(TEXT("rolledBack"), bRolledBack);
		Report->SetBoolField(TEXT("appliedValueMatch"), bAppliedValueMatch);
		Report->SetBoolField(TEXT("appliedStructureMatch"), bAppliedStructureMatch);
		Report->SetBoolField(TEXT("rollbackValueMatch"), !bRolledBack || bRestoredValueMatch);
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

		if (bRolledBack && (!bRestoredValueMatch
			|| !bStructureMatch
			|| !BeforeRevision.Equals(AfterRevision, ESearchCase::IgnoreCase)))
		{
			UE_LOG(LogAssetPatch, Error, TEXT("DataTable Dry Run rollback verification failed."));
			return 22;
		}
		UE_LOG(
			LogAssetPatch,
			Display,
			TEXT("DataTable field patch succeeded. Mode=%s Asset=%s Row=%s FieldCount=%d"),
			bCommit ? TEXT("Commit") : TEXT("DryRun"),
			*AssetPath,
			*RowNameText,
			Fields.Num());
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
		const bool bSaveSucceeded =
			!bSaveFailureInjection && SaveAssetPackage(Asset, PackageFilename, Error);
		if (!bSaveSucceeded)
		{
			if (bSaveFailureInjection)
			{
				Error = TEXT("Injected save failure for scalar regression.");
			}
			FString RestoredRevision = HashPackageFile(Package);
			bool bBackupCopied = false;
			if (!RestoredRevision.Equals(BeforeRevision, ESearchCase::IgnoreCase))
			{
				const uint32 BackupRestoreResult =
					IFileManager::Get().Copy(*PackageFilename, *BackupFilename, true, true);
				if (BackupRestoreResult != COPY_OK)
				{
					UE_LOG(
						LogAssetPatch,
						Error,
						TEXT("%s Backup restoration also failed: %s"),
						*Error,
						*BackupFilename);
					return 23;
				}
				bBackupCopied = true;
				RestoredRevision = HashPackageFile(Package);
			}
			if (!RestoredRevision.Equals(BeforeRevision, ESearchCase::IgnoreCase))
			{
				UE_LOG(
					LogAssetPatch,
					Error,
					TEXT("%s Restored Revision does not match the pre-Commit Revision."),
					*Error);
				return 23;
			}
			UE_LOG(
				LogAssetPatch,
				Error,
				TEXT("%s %s"),
				*Error,
				bBackupCopied
					? TEXT("Backup restored.")
					: TEXT("Disk revision was already unchanged."));
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
	Report->SetStringField(TEXT("executorVersion"), TEXT("0.5.1"));
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
