#include "AssetPatchCommandlet.h"

#include "AssetRegistry/AssetIdentifier.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetRegistry/IAssetRegistry.h"
#include "BlueprintContextSha256.h"
#include "Dom/JsonObject.h"
#include "Engine/Blueprint.h"
#include "Engine/DataAsset.h"
#include "Engine/DataTable.h"
#include "Engine/Texture.h"
#include "HAL/FileManager.h"
#include "MaterialEditingLibrary.h"
#include "Materials/MaterialInstanceConstant.h"
#include "StaticParameterSet.h"
#include "StructuredPropertyJson.h"
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
		const bool bUsesAssetPropertyOperations =
			ContainsExact(OutPolicy.AllowedOperations, TEXT("setAssetProperty"))
			|| ContainsExact(OutPolicy.AllowedOperations, TEXT("setAssetReferenceProperty"))
			|| ContainsExact(OutPolicy.AllowedOperations, TEXT("setAssetStructuredProperty"));
		if (bUsesAssetPropertyOperations && OutPolicy.AllowedAssetProperties.IsEmpty())
		{
			OutError = TEXT("Asset property operations require allowedAssetProperties authorization.");
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
		const bool bUsesReferenceWrites =
			ContainsExact(OutPolicy.AllowedOperations, TEXT("setMaterialInstanceTextureParameter"))
			|| ContainsExact(OutPolicy.AllowedOperations, TEXT("setAssetReferenceProperty"));
		if (bUsesReferenceWrites
			&& (OutPolicy.AllowedReferenceRoots.IsEmpty()
				|| OutPolicy.AllowedReferenceClasses.IsEmpty()))
		{
			OutError = TEXT(
				"Reference writes require allowedReferenceRoots and allowedReferenceClasses authorization.");
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

	enum class EAssetReferenceType : uint8
	{
		Invalid,
		Object,
		Class,
		SoftObject,
		SoftClass
	};

	FString AssetReferenceTypeName(const EAssetReferenceType Type)
	{
		switch (Type)
		{
		case EAssetReferenceType::Object:
			return TEXT("Object");
		case EAssetReferenceType::Class:
			return TEXT("Class");
		case EAssetReferenceType::SoftObject:
			return TEXT("SoftObject");
		case EAssetReferenceType::SoftClass:
			return TEXT("SoftClass");
		default:
			return FString();
		}
	}

	EAssetReferenceType GetAssetReferenceType(FProperty* Property, UClass*& OutConstraintClass)
	{
		OutConstraintClass = nullptr;
		if (FSoftClassProperty* SoftClassProperty = CastField<FSoftClassProperty>(Property))
		{
			OutConstraintClass = SoftClassProperty->MetaClass;
			return EAssetReferenceType::SoftClass;
		}
		if (FSoftObjectProperty* SoftObjectProperty = CastField<FSoftObjectProperty>(Property))
		{
			OutConstraintClass = SoftObjectProperty->PropertyClass;
			return EAssetReferenceType::SoftObject;
		}
		if (FClassProperty* ClassProperty = CastField<FClassProperty>(Property))
		{
			OutConstraintClass = ClassProperty->MetaClass;
			return EAssetReferenceType::Class;
		}
		if (FObjectPropertyBase* ObjectProperty = CastField<FObjectPropertyBase>(Property))
		{
			if (CastField<FWeakObjectProperty>(Property) || CastField<FLazyObjectProperty>(Property))
			{
				return EAssetReferenceType::Invalid;
			}
			OutConstraintClass = ObjectProperty->PropertyClass;
			return EAssetReferenceType::Object;
		}
		return EAssetReferenceType::Invalid;
	}

	bool ReadAssetReferencePath(FProperty* Property, void* ValueAddress, FString& OutPath)
	{
		OutPath.Reset();
		if (FSoftObjectProperty* SoftObjectProperty = CastField<FSoftObjectProperty>(Property))
		{
			OutPath = SoftObjectProperty->GetPropertyValue(ValueAddress).ToSoftObjectPath().ToString();
			return true;
		}
		if (FObjectPropertyBase* ObjectProperty = CastField<FObjectPropertyBase>(Property))
		{
			const UObject* Object = ObjectProperty->GetObjectPropertyValue(ValueAddress);
			OutPath = Object != nullptr ? Object->GetPathName() : FString();
			return true;
		}
		return false;
	}

	class FScopedPropertyValueBackup
	{
	public:
		FScopedPropertyValueBackup(const FProperty* InProperty, const void* Source)
			: Property(InProperty)
		{
			if (!Property || !Source)
			{
				return;
			}
			Storage = FMemory::Malloc(Property->GetSize(), Property->GetMinAlignment());
			Property->InitializeValue(Storage);
			Property->CopyCompleteValue(Storage, Source);
		}

		~FScopedPropertyValueBackup()
		{
			if (Property && Storage)
			{
				Property->DestroyValue(Storage);
				FMemory::Free(Storage);
			}
		}

		FScopedPropertyValueBackup(const FScopedPropertyValueBackup&) = delete;
		FScopedPropertyValueBackup& operator=(const FScopedPropertyValueBackup&) = delete;

		bool IsValid() const
		{
			return Property != nullptr && Storage != nullptr;
		}

		void Restore(void* Destination) const
		{
			check(IsValid());
			Property->CopyCompleteValue(Destination, Storage);
		}

	private:
		const FProperty* Property = nullptr;
		void* Storage = nullptr;
	};

	bool SetAssetReferenceFromJson(
		FProperty* Property,
		void* ValueAddress,
		const TSharedPtr<FJsonValue>& JsonValue,
		const FPatchPolicy& Policy,
		FString& OutReferenceType,
		FString& OutReferencePath,
		FString& OutResolvedClassPath,
		FString& OutError)
	{
		UClass* ConstraintClass = nullptr;
		const EAssetReferenceType PropertyType = GetAssetReferenceType(Property, ConstraintClass);
		if (PropertyType == EAssetReferenceType::Invalid || ConstraintClass == nullptr)
		{
			OutError = FString::Printf(TEXT("Unsupported asset reference property type: %s"), *Property->GetClass()->GetName());
			return false;
		}
		OutReferenceType = AssetReferenceTypeName(PropertyType);
		OutReferencePath.Reset();
		OutResolvedClassPath.Reset();

		if (JsonValue->Type == EJson::Null)
		{
			if (FSoftObjectProperty* SoftObjectProperty = CastField<FSoftObjectProperty>(Property))
			{
				SoftObjectProperty->SetPropertyValue(ValueAddress, FSoftObjectPtr());
			}
			else if (FObjectPropertyBase* ObjectProperty = CastField<FObjectPropertyBase>(Property))
			{
				ObjectProperty->SetObjectPropertyValue(ValueAddress, nullptr);
			}
			return true;
		}

		if (JsonValue->Type != EJson::Object)
		{
			OutError = TEXT("Asset reference value must be null or an object.");
			return false;
		}
		const TSharedPtr<FJsonObject> ReferenceObject = JsonValue->AsObject();
		if (!ReferenceObject.IsValid() || ReferenceObject->Values.Num() != 2)
		{
			OutError = TEXT("Asset reference object must contain exactly referenceType and path.");
			return false;
		}
		FString RequestedType;
		FString RequestedPath;
		if (!ReferenceObject->TryGetStringField(TEXT("referenceType"), RequestedType)
			|| !ReferenceObject->TryGetStringField(TEXT("path"), RequestedPath)
			|| RequestedPath.IsEmpty())
		{
			OutError = TEXT("Asset reference object requires non-empty string referenceType and path.");
			return false;
		}
		if (!RequestedType.Equals(OutReferenceType, ESearchCase::CaseSensitive))
		{
			OutError = FString::Printf(
				TEXT("Reference type %s does not match property type %s."),
				*RequestedType,
				*OutReferenceType);
			return false;
		}
		const FSoftObjectPath SoftPath(RequestedPath);
		if (!SoftPath.IsValid() || !SoftPath.GetSubPathString().IsEmpty())
		{
			OutError = FString::Printf(TEXT("Reference path is invalid or contains a subobject: %s"), *RequestedPath);
			return false;
		}
		if (!IsReferenceAllowed(Policy, RequestedPath))
		{
			OutError = FString::Printf(TEXT("Reference path is not authorized by policy: %s"), *RequestedPath);
			return false;
		}

		if (PropertyType == EAssetReferenceType::Class || PropertyType == EAssetReferenceType::SoftClass)
		{
			UClass* ReferencedClass = LoadObject<UClass>(nullptr, *RequestedPath);
			if (ReferencedClass == nullptr || !ReferencedClass->IsChildOf(ConstraintClass))
			{
				OutError = FString::Printf(
					TEXT("Referenced class is missing or not a child of %s: %s"),
					*ConstraintClass->GetPathName(),
					*RequestedPath);
				return false;
			}
			OutResolvedClassPath = ReferencedClass->GetPathName();
			if (!ContainsExact(Policy.AllowedReferenceClasses, OutResolvedClassPath))
			{
				OutError = FString::Printf(TEXT("Referenced class is not authorized by policy: %s"), *OutResolvedClassPath);
				return false;
			}
			if (PropertyType == EAssetReferenceType::Class)
			{
				CastFieldChecked<FClassProperty>(Property)->SetObjectPropertyValue(ValueAddress, ReferencedClass);
			}
			else
			{
				CastFieldChecked<FSoftClassProperty>(Property)->SetPropertyValue(ValueAddress, FSoftObjectPtr(SoftPath));
			}
		}
		else
		{
			UObject* ReferencedObject = StaticLoadObject(ConstraintClass, nullptr, *RequestedPath);
			if (ReferencedObject == nullptr)
			{
				OutError = FString::Printf(TEXT("Referenced object is missing or has an incompatible class: %s"), *RequestedPath);
				return false;
			}
			OutResolvedClassPath = ReferencedObject->GetClass()->GetPathName();
			if (!ContainsExact(Policy.AllowedReferenceClasses, OutResolvedClassPath))
			{
				OutError = FString::Printf(TEXT("Referenced object class is not authorized by policy: %s"), *OutResolvedClassPath);
				return false;
			}
			if (PropertyType == EAssetReferenceType::Object)
			{
				CastFieldChecked<FObjectPropertyBase>(Property)->SetObjectPropertyValue(ValueAddress, ReferencedObject);
			}
			else
			{
				CastFieldChecked<FSoftObjectProperty>(Property)->SetPropertyValue(ValueAddress, FSoftObjectPtr(SoftPath));
			}
		}
		OutReferencePath = RequestedPath;
		return true;
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
		FGuid& OutExpressionGuid,
		FString& OutError)
	{
		TArray<FMaterialParameterInfo> ParameterInfos;
		TArray<FGuid> ParameterIds;
		Instance->GetAllScalarParameterInfo(ParameterInfos, ParameterIds);
		if (ParameterInfos.Num() != ParameterIds.Num())
		{
			OutError = TEXT("Scalar parameter metadata is inconsistent.");
			return false;
		}
		int32 MatchCount = 0;
		for (int32 Index = 0; Index < ParameterInfos.Num(); ++Index)
		{
			const FMaterialParameterInfo& Info = ParameterInfos[Index];
			if (Info.Name == ParameterName
				&& Info.Association == EMaterialParameterAssociation::GlobalParameter)
			{
				OutInfo = Info;
				OutExpressionGuid = ParameterIds[Index];
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
		FGuid& OutExpressionGuid,
		FString& OutError)
	{
		TArray<FMaterialParameterInfo> ParameterInfos;
		TArray<FGuid> ParameterIds;
		Instance->GetAllVectorParameterInfo(ParameterInfos, ParameterIds);
		if (ParameterInfos.Num() != ParameterIds.Num())
		{
			OutError = TEXT("Vector parameter metadata is inconsistent.");
			return false;
		}
		int32 MatchCount = 0;
		for (int32 Index = 0; Index < ParameterInfos.Num(); ++Index)
		{
			const FMaterialParameterInfo& Info = ParameterInfos[Index];
			if (Info.Name == ParameterName
				&& Info.Association == EMaterialParameterAssociation::GlobalParameter)
			{
				OutInfo = Info;
				OutExpressionGuid = ParameterIds[Index];
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
		FGuid& OutExpressionGuid,
		FString& OutError)
	{
		TArray<FMaterialParameterInfo> ParameterInfos;
		TArray<FGuid> ParameterIds;
		Instance->GetAllTextureParameterInfo(ParameterInfos, ParameterIds);
		if (ParameterInfos.Num() != ParameterIds.Num())
		{
			OutError = TEXT("Texture parameter metadata is inconsistent.");
			return false;
		}
		int32 MatchCount = 0;
		for (int32 Index = 0; Index < ParameterInfos.Num(); ++Index)
		{
			const FMaterialParameterInfo& Info = ParameterInfos[Index];
			if (Info.Name == ParameterName
				&& Info.Association == EMaterialParameterAssociation::GlobalParameter)
			{
				OutInfo = Info;
				OutExpressionGuid = ParameterIds[Index];
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

	template<typename TParameterValue>
	bool ReadMaterialParameterMetadata(
		const TArray<TParameterValue>& Parameters,
		const FMaterialParameterInfo& ParameterInfo,
		const FGuid& DefaultExpressionGuid,
		bool& OutOverride,
		FGuid& OutExpressionGuid)
	{
		OutOverride = false;
		OutExpressionGuid = DefaultExpressionGuid;
		int32 MatchCount = 0;
		for (const TParameterValue& Parameter : Parameters)
		{
			if (Parameter.ParameterInfo == ParameterInfo)
			{
				OutOverride = true;
				OutExpressionGuid = Parameter.ExpressionGUID;
				++MatchCount;
			}
		}
		return MatchCount <= 1;
	}

	bool ReadScalarParameter(
		UMaterialInstanceConstant* Instance,
		const FMaterialParameterInfo& ParameterInfo,
		const FGuid& DefaultExpressionGuid,
		float& OutValue,
		bool& OutOverride,
		FGuid& OutExpressionGuid)
	{
		return Instance->GetScalarParameterValue(FHashedMaterialParameterInfo(ParameterInfo), OutValue)
			&& ReadMaterialParameterMetadata(
				Instance->ScalarParameterValues,
				ParameterInfo,
				DefaultExpressionGuid,
				OutOverride,
				OutExpressionGuid);
	}

	FString FormatScalarParameterValue(const float Value)
	{
		return FString::Printf(TEXT("%.9g"), static_cast<double>(Value));
	}

	bool ReadVectorParameter(
		UMaterialInstanceConstant* Instance,
		const FMaterialParameterInfo& ParameterInfo,
		const FGuid& DefaultExpressionGuid,
		FLinearColor& OutValue,
		bool& OutOverride,
		FGuid& OutExpressionGuid)
	{
		return Instance->GetVectorParameterValue(FHashedMaterialParameterInfo(ParameterInfo), OutValue)
			&& ReadMaterialParameterMetadata(
				Instance->VectorParameterValues,
				ParameterInfo,
				DefaultExpressionGuid,
				OutOverride,
				OutExpressionGuid);
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
		const FGuid& DefaultExpressionGuid,
		UTexture*& OutValue,
		bool& OutOverride,
		FGuid& OutExpressionGuid)
	{
		return Instance->GetTextureParameterValue(FHashedMaterialParameterInfo(ParameterInfo), OutValue)
			&& ReadMaterialParameterMetadata(
				Instance->TextureParameterValues,
				ParameterInfo,
				DefaultExpressionGuid,
				OutOverride,
				OutExpressionGuid);
	}

	FString FormatTextureParameterValue(const UTexture* Value)
	{
		return Value ? Value->GetPathName() : FString();
	}

	FString FormatMaterialExpressionGuid(const FGuid& Value)
	{
		return Value.IsValid()
			? Value.ToString(EGuidFormats::DigitsWithHyphensLower)
			: FString();
	}

	TSharedRef<FJsonObject> MakeMaterialVectorValue(const FLinearColor& Value)
	{
		const TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
		Result->SetNumberField(TEXT("r"), Value.R);
		Result->SetNumberField(TEXT("g"), Value.G);
		Result->SetNumberField(TEXT("b"), Value.B);
		Result->SetNumberField(TEXT("a"), Value.A);
		return Result;
	}

	TSharedPtr<FJsonValue> MakeMaterialTextureValue(const UTexture* Value)
	{
		if (Value)
		{
			return MakeShared<FJsonValueString>(Value->GetPathName());
		}
		return MakeShared<FJsonValueNull>();
	}

	TSharedRef<FJsonObject> MakeMaterialParameterState(
		const TSharedPtr<FJsonValue>& Value,
		const bool bOverride,
		const FGuid& ExpressionGuid)
	{
		const TSharedRef<FJsonObject> State = MakeShared<FJsonObject>();
		State->SetField(TEXT("value"), Value);
		State->SetBoolField(TEXT("override"), bOverride);
		State->SetStringField(TEXT("source"), bOverride ? TEXT("override") : TEXT("inherited"));
		State->SetStringField(TEXT("expressionGuid"), FormatMaterialExpressionGuid(ExpressionGuid));
		return State;
	}

	TSharedRef<FJsonObject> MakeMaterialParameterChange(
		const bool bValueChanged,
		const bool bOverrideChanged,
		const bool bExpressionGuidChanged)
	{
		const TSharedRef<FJsonObject> Change = MakeShared<FJsonObject>();
		Change->SetBoolField(TEXT("valueChanged"), bValueChanged);
		Change->SetBoolField(TEXT("overrideChanged"), bOverrideChanged);
		Change->SetBoolField(TEXT("expressionGuidChanged"), bExpressionGuidChanged);
		Change->SetBoolField(
			TEXT("changed"),
			bValueChanged || bOverrideChanged || bExpressionGuidChanged);
		return Change;
	}

	void AddMaterialParameterReport(
		const TSharedRef<FJsonObject>& Report,
		const FString& ParameterName,
		const FString& ParameterType,
		const TSharedPtr<FJsonValue>& BeforeValue,
		const TSharedPtr<FJsonValue>& AfterValue,
		const TSharedPtr<FJsonValue>& RestoredValue,
		const bool bBeforeOverride,
		const bool bAfterOverride,
		const bool bRestoredOverride,
		const FGuid& BeforeExpressionGuid,
		const FGuid& AfterExpressionGuid,
		const FGuid& RestoredExpressionGuid,
		const bool bValueChanged,
		const bool bRestoredValueMatch,
		const bool bRolledBack)
	{
		const bool bRestoredMetadataMatch =
			bRestoredOverride == bBeforeOverride
			&& RestoredExpressionGuid == BeforeExpressionGuid;
		const bool bRollbackValueMatch = !bRolledBack || bRestoredValueMatch;
		const bool bRollbackMetadataMatch = !bRolledBack || bRestoredMetadataMatch;
		const bool bRollbackStateMatch = bRollbackValueMatch && bRollbackMetadataMatch;

		Report->SetStringField(
			TEXT("targetDescription"),
			TEXT("material-instance-parameter:") + ParameterType + TEXT(":") + ParameterName);
		Report->SetStringField(TEXT("targetType"), TEXT("MaterialInstanceParameter"));
		Report->SetStringField(TEXT("parameterName"), ParameterName);
		Report->SetStringField(TEXT("parameterType"), ParameterType);
		Report->SetStringField(TEXT("parameterAssociation"), TEXT("Global"));
		Report->SetField(TEXT("beforeValue"), BeforeValue);
		Report->SetField(TEXT("afterValue"), AfterValue);
		Report->SetField(TEXT("restoredValue"), RestoredValue);
		Report->SetBoolField(TEXT("beforeOverride"), bBeforeOverride);
		Report->SetBoolField(TEXT("afterOverride"), bAfterOverride);
		Report->SetBoolField(TEXT("restoredOverride"), bRestoredOverride);
		Report->SetStringField(
			TEXT("beforeExpressionGuid"),
			FormatMaterialExpressionGuid(BeforeExpressionGuid));
		Report->SetStringField(
			TEXT("afterExpressionGuid"),
			FormatMaterialExpressionGuid(AfterExpressionGuid));
		Report->SetStringField(
			TEXT("restoredExpressionGuid"),
			FormatMaterialExpressionGuid(RestoredExpressionGuid));
		Report->SetBoolField(TEXT("rollbackValueMatch"), bRollbackValueMatch);
		Report->SetBoolField(TEXT("rollbackMetadataMatch"), bRollbackMetadataMatch);
		Report->SetBoolField(TEXT("rollbackStateMatch"), bRollbackStateMatch);

		const TSharedRef<FJsonObject> Parameter = MakeShared<FJsonObject>();
		Parameter->SetStringField(TEXT("name"), ParameterName);
		Parameter->SetStringField(TEXT("type"), ParameterType);
		Parameter->SetStringField(TEXT("association"), TEXT("Global"));
		Parameter->SetObjectField(
			TEXT("before"),
			MakeMaterialParameterState(BeforeValue, bBeforeOverride, BeforeExpressionGuid));
		Parameter->SetObjectField(
			TEXT("after"),
			MakeMaterialParameterState(AfterValue, bAfterOverride, AfterExpressionGuid));
		Parameter->SetObjectField(
			TEXT("restored"),
			MakeMaterialParameterState(RestoredValue, bRestoredOverride, RestoredExpressionGuid));
		Parameter->SetObjectField(
			TEXT("change"),
			MakeMaterialParameterChange(
				bValueChanged,
				bAfterOverride != bBeforeOverride,
				AfterExpressionGuid != BeforeExpressionGuid));
		Parameter->SetObjectField(
			TEXT("rollbackChange"),
			MakeMaterialParameterChange(
				!bRestoredValueMatch,
				bRestoredOverride != bBeforeOverride,
				RestoredExpressionGuid != BeforeExpressionGuid));
		Report->SetObjectField(TEXT("materialParameter"), Parameter);
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


	class FScopedPropertyScratch
	{
	public:
		FScopedPropertyScratch(const FProperty* InProperty, const void* Source)
			: Property(InProperty)
		{
			if (!Property || !Source)
			{
				return;
			}
			Storage = FMemory::Malloc(Property->GetSize(), Property->GetMinAlignment());
			Property->InitializeValue(Storage);
			Property->CopyCompleteValue(Storage, Source);
		}

		~FScopedPropertyScratch()
		{
			if (Property && Storage)
			{
				Property->DestroyValue(Storage);
				FMemory::Free(Storage);
			}
		}

		FScopedPropertyScratch(const FScopedPropertyScratch&) = delete;
		FScopedPropertyScratch& operator=(const FScopedPropertyScratch&) = delete;

		bool IsValid() const
		{
			return Property != nullptr && Storage != nullptr;
		}

		void* Get() const
		{
			return Storage;
		}

	private:
		const FProperty* Property = nullptr;
		void* Storage = nullptr;
	};

	enum class EAssetTransactionKind : uint8
	{
		AssetProperty,
		AssetReference,
		AssetStructured,
		MaterialScalar,
		MaterialVector,
		MaterialTexture,
		MaterialStaticSwitch,
		DataTableFields
	};

	struct FAssetTransactionOperation
	{
		FString OperationId;
		FString Operation;
		TSharedPtr<FJsonObject> Target;
		TSharedPtr<FJsonValue> Value;
		EAssetTransactionKind Kind = EAssetTransactionKind::AssetProperty;
		FString TargetDescription;
		FString TargetType;
		TArray<FString> TargetKeys;
		TArray<FString> AuthorizationKeys;
		TSharedPtr<FJsonValue> BeforeValue;
		TSharedPtr<FJsonValue> ExpectedValue;
		TSharedPtr<FJsonValue> AfterValue;

		FProperty* Property = nullptr;
		void* ValueAddress = nullptr;
		FString PropertyPath;
		FString ReferenceType;
		FString ReferencePath;
		FString ResolvedReferenceClass;

		FString ParameterName;
		FMaterialParameterInfo ParameterInfo;
		FGuid ParameterExpressionGuid;
		bool bBeforeOverride = false;
		FGuid BeforeExpressionGuid;
		bool bAfterOverride = false;
		FGuid AfterExpressionGuid;
		float BeforeScalar = 0.0f;
		float NewScalar = 0.0f;
		float AfterScalar = 0.0f;
		FLinearColor BeforeVector = FLinearColor::Black;
		FLinearColor NewVector = FLinearColor::Black;
		FLinearColor AfterVector = FLinearColor::Black;
		UTexture* BeforeTexture = nullptr;
		UTexture* NewTexture = nullptr;
		UTexture* AfterTexture = nullptr;
		bool bBeforeSwitch = false;
		bool bNewSwitch = false;
		bool bAfterSwitch = false;

		UDataTable* DataTable = nullptr;
		UScriptStruct* RowStruct = nullptr;
		FName RowName;
		FString RowNameText;
		uint8* RowData = nullptr;
		TArray<FString> FieldNames;
		TArray<FProperty*> Fields;
		TArray<TSharedPtr<FJsonValue>> FieldValues;
		TArray<FString> ExpectedFieldTexts;
	};

	bool IsEditableTransactionProperty(FProperty* Property)
	{
		const EPropertyFlags DisallowedFlags =
			CPF_Transient | CPF_DuplicateTransient | CPF_NonPIEDuplicateTransient;
		return Property != nullptr
			&& Property->HasAnyPropertyFlags(CPF_Edit)
			&& !Property->HasAnyPropertyFlags(DisallowedFlags)
			&& Property->ArrayDim == 1;
	}

	TSharedPtr<FJsonValue> StringOrNull(const FString& Value)
	{
		if (Value.IsEmpty())
		{
			return MakeShared<FJsonValueNull>();
		}
		return MakeShared<FJsonValueString>(Value);
	}

	bool AddUniqueTransactionTarget(
		TSet<FString>& TargetKeys,
		const FString& TargetKey,
		FString& OutError)
	{
		if (TargetKey.IsEmpty() || TargetKeys.Contains(TargetKey))
		{
			OutError = FString::Printf(TEXT("Transaction contains a duplicate or empty target: %s"), *TargetKey);
			return false;
		}
		TargetKeys.Add(TargetKey);
		return true;
	}

	bool PrepareDataAssetTransactionOperation(
		UObject* Asset,
		const FString& ActualAssetClass,
		const FPatchPolicy& Policy,
		FAssetTransactionOperation& Prepared,
		FString& OutError)
	{
		if (Cast<UDataAsset>(Asset) == nullptr)
		{
			OutError = TEXT("Data Asset transaction operations require a Data Asset.");
			return false;
		}
		Prepared.Target->TryGetStringField(TEXT("propertyPath"), Prepared.PropertyPath);
		if (Prepared.PropertyPath.IsEmpty() || Prepared.PropertyPath.Contains(TEXT(".")))
		{
			OutError = TEXT("Data Asset transaction propertyPath must be one top-level property name.");
			return false;
		}
		const FString Authorization = ActualAssetClass + TEXT("#") + Prepared.PropertyPath;
		Prepared.AuthorizationKeys.Add(Authorization);
		if (!ContainsExact(Policy.AllowedAssetProperties, Authorization)
			|| !ResolvePropertyPath(Asset, Prepared.PropertyPath, Prepared.Property, Prepared.ValueAddress, OutError)
			|| !IsEditableTransactionProperty(Prepared.Property))
		{
			if (OutError.IsEmpty())
			{
				OutError = FString::Printf(TEXT("Data Asset transaction property is not authorized or editable: %s"), *Authorization);
			}
			return false;
		}

		FScopedPropertyScratch Scratch(Prepared.Property, Prepared.ValueAddress);
		if (!Scratch.IsValid())
		{
			OutError = TEXT("Could not allocate Data Asset transaction scratch value.");
			return false;
		}

		Prepared.TargetDescription = TEXT("asset-property:") + Prepared.PropertyPath;
		Prepared.TargetType = Prepared.Property->GetClass()->GetName();
		if (Prepared.Kind == EAssetTransactionKind::AssetProperty)
		{
			FString BeforeText;
			FString ExpectedText;
			if (!ReadPropertyValue(Asset, Prepared.Property, Prepared.ValueAddress, BeforeText)
				|| !SetPropertyFromJson(Prepared.Property, Scratch.Get(), Prepared.Value, OutError)
				|| !ReadPropertyValue(Asset, Prepared.Property, Scratch.Get(), ExpectedText))
			{
				return false;
			}
			Prepared.BeforeValue = MakeShared<FJsonValueString>(BeforeText);
			Prepared.ExpectedValue = MakeShared<FJsonValueString>(ExpectedText);
			return true;
		}
		if (Prepared.Kind == EAssetTransactionKind::AssetReference)
		{
			FString BeforePath;
			if (!ReadAssetReferencePath(Prepared.Property, Prepared.ValueAddress, BeforePath)
				|| !SetAssetReferenceFromJson(
					Prepared.Property,
					Scratch.Get(),
					Prepared.Value,
					Policy,
					Prepared.ReferenceType,
					Prepared.ReferencePath,
					Prepared.ResolvedReferenceClass,
					OutError))
			{
				return false;
			}
			FString ExpectedPath;
			if (!ReadAssetReferencePath(Prepared.Property, Scratch.Get(), ExpectedPath))
			{
				OutError = TEXT("Could not read expected Data Asset reference value.");
				return false;
			}
			Prepared.TargetDescription = TEXT("asset-reference-property:") + Prepared.PropertyPath;
			Prepared.TargetType = TEXT("AssetReference(") + Prepared.ReferenceType + TEXT(")");
			Prepared.BeforeValue = StringOrNull(BeforePath);
			Prepared.ExpectedValue = StringOrNull(ExpectedPath);
			return true;
		}

		if (UEAgentKit::StructuredPropertyJson::GetKind(Prepared.Property)
			== UEAgentKit::StructuredPropertyJson::EKind::Invalid)
		{
			OutError = FString::Printf(TEXT("Property is not Struct, Array, Set, or Map: %s"), *Prepared.PropertyPath);
			return false;
		}
		TSharedPtr<FJsonValue> Schema;
		TSharedPtr<FJsonValue> Before;
		TSharedPtr<FJsonValue> Expected;
		if (!UEAgentKit::StructuredPropertyJson::BuildSchema(Prepared.Property, Schema, OutError)
			|| !UEAgentKit::StructuredPropertyJson::ExportValue(
				Prepared.Property,
				Prepared.ValueAddress,
				Before,
				OutError)
			|| !UEAgentKit::StructuredPropertyJson::ImportValue(
				Prepared.Property,
				Scratch.Get(),
				Prepared.Value,
				OutError)
			|| !UEAgentKit::StructuredPropertyJson::ExportValue(
				Prepared.Property,
				Scratch.Get(),
				Expected,
				OutError))
		{
			return false;
		}
		Prepared.TargetDescription = TEXT("asset-structured-property:") + Prepared.PropertyPath;
		Prepared.TargetType = TEXT("AssetStructuredProperty(") + Prepared.Property->GetClass()->GetName() + TEXT(")");
		Prepared.BeforeValue = Before;
		Prepared.ExpectedValue = Expected;
		return true;
	}

	bool PrepareMaterialTransactionOperation(
		UObject* Asset,
		const FString& ActualAssetClass,
		const FPatchPolicy& Policy,
		FAssetTransactionOperation& Prepared,
		FString& OutError)
	{
		UMaterialInstanceConstant* MaterialInstance = Cast<UMaterialInstanceConstant>(Asset);
		if (!MaterialInstance)
		{
			OutError = TEXT("Material transaction operations require MaterialInstanceConstant.");
			return false;
		}
		Prepared.Target->TryGetStringField(TEXT("parameterName"), Prepared.ParameterName);
		if (Prepared.ParameterName.IsEmpty())
		{
			OutError = TEXT("Material transaction parameterName is required.");
			return false;
		}

		FString ParameterType;
		if (Prepared.Kind == EAssetTransactionKind::MaterialScalar)
		{
			ParameterType = TEXT("Scalar");
		}
		else if (Prepared.Kind == EAssetTransactionKind::MaterialVector)
		{
			ParameterType = TEXT("Vector");
		}
		else if (Prepared.Kind == EAssetTransactionKind::MaterialTexture)
		{
			ParameterType = TEXT("Texture");
		}
		else
		{
			ParameterType = TEXT("StaticSwitch");
		}
		const FString Authorization =
			ActualAssetClass + TEXT("#") + ParameterType + TEXT("#") + Prepared.ParameterName;
		Prepared.AuthorizationKeys.Add(Authorization);
		if (!ContainsExact(Policy.AllowedMaterialParameters, Authorization))
		{
			OutError = FString::Printf(TEXT("Material transaction parameter is not authorized: %s"), *Authorization);
			return false;
		}
		Prepared.TargetDescription =
			TEXT("material-instance-parameter:") + ParameterType + TEXT(":") + Prepared.ParameterName;
		Prepared.TargetType = TEXT("MaterialInstanceParameter");

		if (Prepared.Kind == EAssetTransactionKind::MaterialScalar)
		{
			double Number = 0.0;
			if (!Prepared.Value->TryGetNumber(Number)
				|| !FMath::IsFinite(Number)
				|| FMath::Abs(Number) > static_cast<double>(FLT_MAX))
			{
				OutError = TEXT("Material transaction scalar value must be a finite float.");
				return false;
			}
			Prepared.NewScalar = static_cast<float>(Number);
			if (!FindGlobalScalarParameter(
					MaterialInstance,
					FName(*Prepared.ParameterName),
					Prepared.ParameterInfo,
					Prepared.ParameterExpressionGuid,
					OutError)
				|| !ReadScalarParameter(
					MaterialInstance,
					Prepared.ParameterInfo,
					Prepared.ParameterExpressionGuid,
					Prepared.BeforeScalar,
					Prepared.bBeforeOverride,
					Prepared.BeforeExpressionGuid))
			{
				return false;
			}
			Prepared.BeforeValue = MakeShared<FJsonValueNumber>(Prepared.BeforeScalar);
			Prepared.ExpectedValue = MakeShared<FJsonValueNumber>(Prepared.NewScalar);
			return true;
		}
		if (Prepared.Kind == EAssetTransactionKind::MaterialVector)
		{
			const TSharedPtr<FJsonObject> Color = Prepared.Value->AsObject();
			double R = 0.0;
			double G = 0.0;
			double B = 0.0;
			double A = 0.0;
			if (!Color.IsValid()
				|| Color->Values.Num() != 4
				|| !Color->TryGetNumberField(TEXT("r"), R)
				|| !Color->TryGetNumberField(TEXT("g"), G)
				|| !Color->TryGetNumberField(TEXT("b"), B)
				|| !Color->TryGetNumberField(TEXT("a"), A)
				|| !FMath::IsFinite(R) || !FMath::IsFinite(G)
				|| !FMath::IsFinite(B) || !FMath::IsFinite(A)
				|| FMath::Abs(R) > static_cast<double>(FLT_MAX)
				|| FMath::Abs(G) > static_cast<double>(FLT_MAX)
				|| FMath::Abs(B) > static_cast<double>(FLT_MAX)
				|| FMath::Abs(A) > static_cast<double>(FLT_MAX))
			{
				OutError = TEXT("Material transaction vector value must contain finite r, g, b, and a floats.");
				return false;
			}
			Prepared.NewVector = FLinearColor(
				static_cast<float>(R),
				static_cast<float>(G),
				static_cast<float>(B),
				static_cast<float>(A));
			if (!FindGlobalVectorParameter(
					MaterialInstance,
					FName(*Prepared.ParameterName),
					Prepared.ParameterInfo,
					Prepared.ParameterExpressionGuid,
					OutError)
				|| !ReadVectorParameter(
					MaterialInstance,
					Prepared.ParameterInfo,
					Prepared.ParameterExpressionGuid,
					Prepared.BeforeVector,
					Prepared.bBeforeOverride,
					Prepared.BeforeExpressionGuid))
			{
				return false;
			}
			Prepared.BeforeValue = MakeShared<FJsonValueObject>(MakeMaterialVectorValue(Prepared.BeforeVector));
			Prepared.ExpectedValue = MakeShared<FJsonValueObject>(MakeMaterialVectorValue(Prepared.NewVector));
			return true;
		}
		if (Prepared.Kind == EAssetTransactionKind::MaterialTexture)
		{
			FString TexturePath;
			if (!Prepared.Value->TryGetString(TexturePath))
			{
				OutError = TEXT("Material transaction texture value must be an object path string.");
				return false;
			}
			TexturePath = NormalizeObjectPath(TexturePath);
			if (!IsReferenceAllowed(Policy, TexturePath))
			{
				OutError = FString::Printf(TEXT("Material transaction texture is outside authorized roots: %s"), *TexturePath);
				return false;
			}
			Prepared.NewTexture = LoadObject<UTexture>(nullptr, *TexturePath);
			if (!Prepared.NewTexture
				|| !ContainsExact(Policy.AllowedReferenceClasses, Prepared.NewTexture->GetClass()->GetPathName()))
			{
				OutError = FString::Printf(TEXT("Material transaction texture is missing or unauthorized: %s"), *TexturePath);
				return false;
			}
			if (!FindGlobalTextureParameter(
					MaterialInstance,
					FName(*Prepared.ParameterName),
					Prepared.ParameterInfo,
					Prepared.ParameterExpressionGuid,
					OutError)
				|| !ReadTextureParameter(
					MaterialInstance,
					Prepared.ParameterInfo,
					Prepared.ParameterExpressionGuid,
					Prepared.BeforeTexture,
					Prepared.bBeforeOverride,
					Prepared.BeforeExpressionGuid))
			{
				return false;
			}
			Prepared.BeforeValue = MakeMaterialTextureValue(Prepared.BeforeTexture);
			Prepared.ExpectedValue = MakeMaterialTextureValue(Prepared.NewTexture);
			return true;
		}

		if (!Prepared.Value->TryGetBool(Prepared.bNewSwitch)
			|| !FindGlobalStaticSwitchParameter(
				MaterialInstance,
				FName(*Prepared.ParameterName),
				Prepared.ParameterInfo,
				OutError)
			|| !ReadStaticSwitchParameter(
				MaterialInstance,
				Prepared.ParameterInfo,
				Prepared.bBeforeSwitch,
				Prepared.BeforeExpressionGuid,
				Prepared.bBeforeOverride))
		{
			if (OutError.IsEmpty())
			{
				OutError = TEXT("Material transaction static switch value or parameter is invalid.");
			}
			return false;
		}
		Prepared.BeforeValue = MakeShared<FJsonValueBoolean>(Prepared.bBeforeSwitch);
		Prepared.ExpectedValue = MakeShared<FJsonValueBoolean>(Prepared.bNewSwitch);
		return true;
	}

	bool PrepareDataTableTransactionOperation(
		UObject* Asset,
		const FString& ActualAssetClass,
		const FPatchPolicy& Policy,
		FAssetTransactionOperation& Prepared,
		FString& OutError)
	{
		Prepared.DataTable = Cast<UDataTable>(Asset);
		if (!Prepared.DataTable)
		{
			OutError = TEXT("DataTable transaction operations require a DataTable.");
			return false;
		}
		Prepared.RowStruct = const_cast<UScriptStruct*>(Prepared.DataTable->GetRowStruct());
		Prepared.Target->TryGetStringField(TEXT("rowName"), Prepared.RowNameText);
		if (!Prepared.RowStruct || Prepared.RowNameText.IsEmpty())
		{
			OutError = TEXT("DataTable transaction row struct or rowName is invalid.");
			return false;
		}
		Prepared.RowName = FName(*Prepared.RowNameText);
		Prepared.RowData = Prepared.DataTable->FindRowUnchecked(Prepared.RowName);
		if (!Prepared.RowData)
		{
			OutError = FString::Printf(TEXT("DataTable transaction row was not found: %s"), *Prepared.RowNameText);
			return false;
		}

		if (Prepared.Operation.Equals(TEXT("setDataTableCell"), ESearchCase::CaseSensitive))
		{
			FString FieldName;
			Prepared.Target->TryGetStringField(TEXT("fieldName"), FieldName);
			if (FieldName.IsEmpty() || FieldName.Contains(TEXT("."))
				|| Prepared.Value->Type == EJson::Null
				|| Prepared.Value->Type == EJson::Array
				|| Prepared.Value->Type == EJson::Object)
			{
				OutError = TEXT("DataTable transaction cell requires a top-level field and scalar value.");
				return false;
			}
			Prepared.FieldNames.Add(FieldName);
			Prepared.FieldValues.Add(Prepared.Value);
			Prepared.TargetDescription = TEXT("data-table-cell:") + Prepared.RowNameText + TEXT(".") + FieldName;
		}
		else
		{
			const TSharedPtr<FJsonObject> FieldsObject = Prepared.Value->AsObject();
			if (!FieldsObject.IsValid() || FieldsObject->Values.IsEmpty() || FieldsObject->Values.Num() > 32)
			{
				OutError = TEXT("DataTable transaction row-fields value must contain 1-32 fields.");
				return false;
			}
			FieldsObject->Values.GetKeys(Prepared.FieldNames);
			Prepared.FieldNames.Sort();
			for (const FString& FieldName : Prepared.FieldNames)
			{
				const TSharedPtr<FJsonValue> FieldValue = FieldsObject->Values.FindChecked(FieldName);
				if (FieldName.IsEmpty() || FieldName.Contains(TEXT("."))
					|| !FieldValue.IsValid()
					|| FieldValue->Type == EJson::Null
					|| FieldValue->Type == EJson::Array
					|| FieldValue->Type == EJson::Object)
				{
					OutError = TEXT("DataTable transaction row fields require top-level names and scalar values.");
					return false;
				}
				Prepared.FieldValues.Add(FieldValue);
			}
			Prepared.TargetDescription = TEXT("data-table-row-fields:") + Prepared.RowNameText;
		}

		const FString RowStructPath = Prepared.RowStruct->GetPathName();
		TSharedRef<FJsonObject> BeforeValues = MakeShared<FJsonObject>();
		TSharedRef<FJsonObject> ExpectedValues = MakeShared<FJsonObject>();
		for (int32 Index = 0; Index < Prepared.FieldNames.Num(); ++Index)
		{
			const FString& FieldName = Prepared.FieldNames[Index];
			const FString Authorization =
				ActualAssetClass + TEXT("#") + RowStructPath + TEXT("#") + FieldName;
			Prepared.AuthorizationKeys.Add(Authorization);
			FProperty* Field = FindFProperty<FProperty>(Prepared.RowStruct, FName(*FieldName));
			if (!ContainsExact(Policy.AllowedDataTableFields, Authorization)
				|| !Field
				|| Field->ArrayDim != 1
				|| Field->HasAnyPropertyFlags(
					CPF_Transient | CPF_DuplicateTransient | CPF_NonPIEDuplicateTransient))
			{
				OutError = FString::Printf(TEXT("DataTable transaction field is missing or unauthorized: %s"), *Authorization);
				return false;
			}
			void* ValueAddress = Field->ContainerPtrToValuePtr<void>(Prepared.RowData);
			FScopedPropertyScratch Scratch(Field, ValueAddress);
			FString BeforeText;
			FString ExpectedText;
			if (!Scratch.IsValid()
				|| !ReadPropertyValue(Prepared.DataTable, Field, ValueAddress, BeforeText)
				|| !SetPropertyFromJson(Field, Scratch.Get(), Prepared.FieldValues[Index], OutError)
				|| !ReadPropertyValue(Prepared.DataTable, Field, Scratch.Get(), ExpectedText))
			{
				return false;
			}
			Prepared.Fields.Add(Field);
			Prepared.ExpectedFieldTexts.Add(ExpectedText);
			BeforeValues->SetStringField(FieldName, BeforeText);
			ExpectedValues->SetStringField(FieldName, ExpectedText);
		}
		Prepared.BeforeValue = MakeShared<FJsonValueObject>(BeforeValues);
		Prepared.ExpectedValue = MakeShared<FJsonValueObject>(ExpectedValues);
		Prepared.TargetType = Prepared.Operation.Equals(TEXT("setDataTableCell"), ESearchCase::CaseSensitive)
			? Prepared.Fields[0]->GetClass()->GetName()
			: TEXT("DataTableRowFields");
		return true;
	}

	bool ApplyAssetTransactionOperation(
		UObject* Asset,
		const FPatchPolicy& Policy,
		FAssetTransactionOperation& Prepared,
		FString& OutError)
	{
		if (Prepared.Kind == EAssetTransactionKind::AssetProperty)
		{
			if (!SetPropertyFromJson(Prepared.Property, Prepared.ValueAddress, Prepared.Value, OutError))
			{
				return false;
			}
			FString AfterText;
			if (!ReadPropertyValue(Asset, Prepared.Property, Prepared.ValueAddress, AfterText))
			{
				OutError = TEXT("Could not read Data Asset transaction value after apply.");
				return false;
			}
			Prepared.AfterValue = MakeShared<FJsonValueString>(AfterText);
			return Prepared.ExpectedValue->AsString() == AfterText;
		}
		if (Prepared.Kind == EAssetTransactionKind::AssetReference)
		{
			FString ReferenceType;
			FString ReferencePath;
			FString ResolvedClass;
			if (!SetAssetReferenceFromJson(
					Prepared.Property,
					Prepared.ValueAddress,
					Prepared.Value,
					Policy,
					ReferenceType,
					ReferencePath,
					ResolvedClass,
					OutError))
			{
				return false;
			}
			FString AfterPath;
			if (!ReadAssetReferencePath(Prepared.Property, Prepared.ValueAddress, AfterPath))
			{
				OutError = TEXT("Could not read Data Asset reference after apply.");
				return false;
			}
			Prepared.AfterValue = StringOrNull(AfterPath);
			return UEAgentKit::StructuredPropertyJson::JsonEqual(Prepared.AfterValue, Prepared.ExpectedValue);
		}
		if (Prepared.Kind == EAssetTransactionKind::AssetStructured)
		{
			if (!UEAgentKit::StructuredPropertyJson::ImportValue(
					Prepared.Property,
					Prepared.ValueAddress,
					Prepared.Value,
					OutError)
				|| !UEAgentKit::StructuredPropertyJson::ExportValue(
					Prepared.Property,
					Prepared.ValueAddress,
					Prepared.AfterValue,
					OutError))
			{
				return false;
			}
			return UEAgentKit::StructuredPropertyJson::JsonEqual(Prepared.AfterValue, Prepared.ExpectedValue);
		}

		UMaterialInstanceConstant* MaterialInstance = Cast<UMaterialInstanceConstant>(Asset);
		if (Prepared.Kind == EAssetTransactionKind::MaterialScalar)
		{
			UMaterialEditingLibrary::SetMaterialInstanceScalarParameterValue(
				MaterialInstance,
				FName(*Prepared.ParameterName),
				Prepared.NewScalar,
				EMaterialParameterAssociation::GlobalParameter);
			if (!ReadScalarParameter(
					MaterialInstance,
					Prepared.ParameterInfo,
					Prepared.ParameterExpressionGuid,
					Prepared.AfterScalar,
					Prepared.bAfterOverride,
					Prepared.AfterExpressionGuid)
				|| !FMath::IsNearlyEqual(Prepared.AfterScalar, Prepared.NewScalar, UE_SMALL_NUMBER)
				|| !Prepared.bAfterOverride
				|| Prepared.AfterExpressionGuid != Prepared.ParameterExpressionGuid)
			{
				OutError = TEXT("Material scalar transaction read-back failed.");
				return false;
			}
			Prepared.AfterValue = MakeShared<FJsonValueNumber>(Prepared.AfterScalar);
			return true;
		}
		if (Prepared.Kind == EAssetTransactionKind::MaterialVector)
		{
			UMaterialEditingLibrary::SetMaterialInstanceVectorParameterValue(
				MaterialInstance,
				FName(*Prepared.ParameterName),
				Prepared.NewVector,
				EMaterialParameterAssociation::GlobalParameter);
			if (!ReadVectorParameter(
					MaterialInstance,
					Prepared.ParameterInfo,
					Prepared.ParameterExpressionGuid,
					Prepared.AfterVector,
					Prepared.bAfterOverride,
					Prepared.AfterExpressionGuid)
				|| !Prepared.AfterVector.Equals(Prepared.NewVector, UE_SMALL_NUMBER)
				|| !Prepared.bAfterOverride
				|| Prepared.AfterExpressionGuid != Prepared.ParameterExpressionGuid)
			{
				OutError = TEXT("Material vector transaction read-back failed.");
				return false;
			}
			Prepared.AfterValue = MakeShared<FJsonValueObject>(MakeMaterialVectorValue(Prepared.AfterVector));
			return true;
		}
		if (Prepared.Kind == EAssetTransactionKind::MaterialTexture)
		{
			UMaterialEditingLibrary::SetMaterialInstanceTextureParameterValue(
				MaterialInstance,
				FName(*Prepared.ParameterName),
				Prepared.NewTexture,
				EMaterialParameterAssociation::GlobalParameter);
			if (!ReadTextureParameter(
					MaterialInstance,
					Prepared.ParameterInfo,
					Prepared.ParameterExpressionGuid,
					Prepared.AfterTexture,
					Prepared.bAfterOverride,
					Prepared.AfterExpressionGuid)
				|| Prepared.AfterTexture != Prepared.NewTexture
				|| !Prepared.bAfterOverride
				|| Prepared.AfterExpressionGuid != Prepared.ParameterExpressionGuid)
			{
				OutError = TEXT("Material texture transaction read-back failed.");
				return false;
			}
			Prepared.AfterValue = MakeMaterialTextureValue(Prepared.AfterTexture);
			return true;
		}
		if (Prepared.Kind == EAssetTransactionKind::MaterialStaticSwitch)
		{
			UMaterialEditingLibrary::SetMaterialInstanceStaticSwitchParameterValue(
				MaterialInstance,
				FName(*Prepared.ParameterName),
				Prepared.bNewSwitch,
				EMaterialParameterAssociation::GlobalParameter,
				true);
			if (!ReadStaticSwitchParameter(
					MaterialInstance,
					Prepared.ParameterInfo,
					Prepared.bAfterSwitch,
					Prepared.AfterExpressionGuid,
					Prepared.bAfterOverride)
				|| Prepared.bAfterSwitch != Prepared.bNewSwitch
				|| Prepared.AfterExpressionGuid != Prepared.BeforeExpressionGuid
				|| !Prepared.bAfterOverride)
			{
				OutError = TEXT("Material static switch transaction read-back failed.");
				return false;
			}
			Prepared.AfterValue = MakeShared<FJsonValueBoolean>(Prepared.bAfterSwitch);
			return true;
		}

		TSharedRef<FJsonObject> AfterValues = MakeShared<FJsonObject>();
		for (int32 Index = 0; Index < Prepared.Fields.Num(); ++Index)
		{
			void* ValueAddress = Prepared.Fields[Index]->ContainerPtrToValuePtr<void>(Prepared.RowData);
			if (!SetPropertyFromJson(Prepared.Fields[Index], ValueAddress, Prepared.FieldValues[Index], OutError))
			{
				return false;
			}
			FString AfterText;
			if (!ReadPropertyValue(Prepared.DataTable, Prepared.Fields[Index], ValueAddress, AfterText)
				|| AfterText != Prepared.ExpectedFieldTexts[Index])
			{
				OutError = FString::Printf(
					TEXT("DataTable transaction read-back failed for field: %s"),
					*Prepared.FieldNames[Index]);
				return false;
			}
			AfterValues->SetStringField(Prepared.FieldNames[Index], AfterText);
		}
		Prepared.AfterValue = MakeShared<FJsonValueObject>(AfterValues);
		return true;
	}

	int32 ExecuteAssetTransaction(
		UObject* Asset,
		UPackage* Package,
		const TArray<TSharedPtr<FJsonValue>>& OperationValues,
		const FPatchPolicy& Policy,
		const bool bCommit,
		const FString& PatchId,
		const FString& AssetPath,
		const FString& ActualAssetClass,
		const FString& PackageFilename,
		const FString& BeforeRevision,
		const FString& BackupDirectory,
		const FString& ReportFilename,
		const bool bOriginalDirty)
	{
		if (OperationValues.Num() < 2 || OperationValues.Num() > 32)
		{
			UE_LOG(LogAssetPatch, Error, TEXT("Asset transactions require 2-32 operations."));
			return 14;
		}

		TArray<FAssetTransactionOperation> Operations;
		Operations.Reserve(OperationValues.Num());
		TSet<FString> OperationIds;
		TSet<FString> TargetKeys;
		FString Error;
		for (const TSharedPtr<FJsonValue>& OperationValue : OperationValues)
		{
			const TSharedPtr<FJsonObject> OperationObject = OperationValue.IsValid()
				? OperationValue->AsObject()
				: nullptr;
			const TSharedPtr<FJsonObject>* TargetPtr = nullptr;
			if (!OperationObject.IsValid())
			{
				UE_LOG(LogAssetPatch, Error, TEXT("Asset transaction operation entry is invalid."));
				return 15;
			}

			FAssetTransactionOperation Prepared;
			OperationObject->TryGetStringField(TEXT("operationId"), Prepared.OperationId);
			OperationObject->TryGetStringField(TEXT("operation"), Prepared.Operation);
			if (Prepared.OperationId.IsEmpty()
				|| OperationIds.Contains(Prepared.OperationId)
				|| !ContainsExact(Policy.AllowedOperations, Prepared.Operation)
				|| !OperationObject->TryGetObjectField(TEXT("target"), TargetPtr)
				|| !TargetPtr || !TargetPtr->IsValid())
			{
				UE_LOG(LogAssetPatch, Error, TEXT("Asset transaction operation is unauthorized, duplicated, or invalid."));
				return 15;
			}
			Prepared.Target = *TargetPtr;
			Prepared.Value = OperationObject->TryGetField(TEXT("value"));
			if (!Prepared.Value.IsValid())
			{
				UE_LOG(LogAssetPatch, Error, TEXT("Asset transaction operation value is missing."));
				return 16;
			}

			bool bPrepared = false;
			if (Prepared.Operation.Equals(TEXT("setAssetProperty"), ESearchCase::CaseSensitive))
			{
				Prepared.Kind = EAssetTransactionKind::AssetProperty;
				bPrepared = PrepareDataAssetTransactionOperation(Asset, ActualAssetClass, Policy, Prepared, Error);
				Prepared.TargetKeys.Add(TEXT("property:") + Prepared.PropertyPath);
			}
			else if (Prepared.Operation.Equals(TEXT("setAssetReferenceProperty"), ESearchCase::CaseSensitive))
			{
				Prepared.Kind = EAssetTransactionKind::AssetReference;
				bPrepared = PrepareDataAssetTransactionOperation(Asset, ActualAssetClass, Policy, Prepared, Error);
				Prepared.TargetKeys.Add(TEXT("property:") + Prepared.PropertyPath);
			}
			else if (Prepared.Operation.Equals(TEXT("setAssetStructuredProperty"), ESearchCase::CaseSensitive))
			{
				Prepared.Kind = EAssetTransactionKind::AssetStructured;
				bPrepared = PrepareDataAssetTransactionOperation(Asset, ActualAssetClass, Policy, Prepared, Error);
				Prepared.TargetKeys.Add(TEXT("property:") + Prepared.PropertyPath);
			}
			else if (Prepared.Operation.Equals(TEXT("setMaterialInstanceScalarParameter"), ESearchCase::CaseSensitive))
			{
				Prepared.Kind = EAssetTransactionKind::MaterialScalar;
				bPrepared = PrepareMaterialTransactionOperation(Asset, ActualAssetClass, Policy, Prepared, Error);
				Prepared.TargetKeys.Add(TEXT("material:Scalar:") + Prepared.ParameterName);
			}
			else if (Prepared.Operation.Equals(TEXT("setMaterialInstanceVectorParameter"), ESearchCase::CaseSensitive))
			{
				Prepared.Kind = EAssetTransactionKind::MaterialVector;
				bPrepared = PrepareMaterialTransactionOperation(Asset, ActualAssetClass, Policy, Prepared, Error);
				Prepared.TargetKeys.Add(TEXT("material:Vector:") + Prepared.ParameterName);
			}
			else if (Prepared.Operation.Equals(TEXT("setMaterialInstanceTextureParameter"), ESearchCase::CaseSensitive))
			{
				Prepared.Kind = EAssetTransactionKind::MaterialTexture;
				bPrepared = PrepareMaterialTransactionOperation(Asset, ActualAssetClass, Policy, Prepared, Error);
				Prepared.TargetKeys.Add(TEXT("material:Texture:") + Prepared.ParameterName);
			}
			else if (Prepared.Operation.Equals(TEXT("setMaterialInstanceStaticSwitchParameter"), ESearchCase::CaseSensitive))
			{
				Prepared.Kind = EAssetTransactionKind::MaterialStaticSwitch;
				bPrepared = PrepareMaterialTransactionOperation(Asset, ActualAssetClass, Policy, Prepared, Error);
				Prepared.TargetKeys.Add(TEXT("material:StaticSwitch:") + Prepared.ParameterName);
			}
			else if (Prepared.Operation.Equals(TEXT("setDataTableCell"), ESearchCase::CaseSensitive)
				|| Prepared.Operation.Equals(TEXT("setDataTableRowFields"), ESearchCase::CaseSensitive))
			{
				Prepared.Kind = EAssetTransactionKind::DataTableFields;
				bPrepared = PrepareDataTableTransactionOperation(Asset, ActualAssetClass, Policy, Prepared, Error);
				for (const FString& FieldName : Prepared.FieldNames)
				{
					Prepared.TargetKeys.Add(
						TEXT("datatable:") + Prepared.RowNameText + TEXT(":") + FieldName);
				}
			}
			else
			{
				Error = TEXT("Structural DataTable row operations and unknown operations are not supported in a multi-operation transaction.");
			}
			if (!bPrepared)
			{
				UE_LOG(LogAssetPatch, Error, TEXT("Asset transaction prevalidation failed: %s"), *Error);
				return 17;
			}
			for (const FString& TargetKey : Prepared.TargetKeys)
			{
				if (!AddUniqueTransactionTarget(TargetKeys, TargetKey, Error))
				{
					UE_LOG(LogAssetPatch, Error, TEXT("%s"), *Error);
					return 17;
				}
			}
			OperationIds.Add(Prepared.OperationId);
			Operations.Add(MoveTemp(Prepared));
		}

		FString BackupFilename;
		if (bCommit)
		{
			IFileManager::Get().MakeDirectory(*BackupDirectory, true);
			BackupFilename = CreateBackupFilename(BackupDirectory, PatchId, PackageFilename);
			if (IFileManager::Get().Copy(*BackupFilename, *PackageFilename, true, true) != COPY_OK)
			{
				UE_LOG(LogAssetPatch, Error, TEXT("Could not create transaction package backup: %s"), *BackupFilename);
				return 19;
			}
		}

		Asset->Modify();
		TSet<FName> ChangedRows;
		bool bDataAssetChanged = false;
		for (FAssetTransactionOperation& Prepared : Operations)
		{
			if (!ApplyAssetTransactionOperation(Asset, Policy, Prepared, Error))
			{
				UE_LOG(LogAssetPatch, Error, TEXT("Asset transaction apply failed: %s"), *Error);
				return 20;
			}
			if (Prepared.Kind == EAssetTransactionKind::AssetProperty
				|| Prepared.Kind == EAssetTransactionKind::AssetReference
				|| Prepared.Kind == EAssetTransactionKind::AssetStructured)
			{
				bDataAssetChanged = true;
			}
			if (Prepared.Kind == EAssetTransactionKind::DataTableFields)
			{
				ChangedRows.Add(Prepared.RowName);
			}
		}
		if (bDataAssetChanged)
		{
			Asset->PostEditChange();
		}
		if (UDataTable* DataTable = Cast<UDataTable>(Asset))
		{
			TArray<FName> SortedRows = ChangedRows.Array();
			SortedRows.Sort(FNameLexicalLess());
			for (const FName RowName : SortedRows)
			{
				DataTable->HandleDataTableChanged(RowName);
			}
		}
		Package->MarkPackageDirty();

		bool bSaved = false;
		if (bCommit)
		{
			if (!SaveAssetPackage(Asset, PackageFilename, Error))
			{
				IFileManager::Get().Copy(*PackageFilename, *BackupFilename, true, true);
				UE_LOG(LogAssetPatch, Error, TEXT("%s Transaction backup restored."), *Error);
				return 21;
			}
			bSaved = true;
		}

		const FString AfterRevision = HashPackageFile(Package);
		const bool bDiskUnchanged = BeforeRevision.Equals(AfterRevision, ESearchCase::IgnoreCase);
		const bool bRolledBack = !bCommit;
		TArray<TSharedPtr<FJsonValue>> OperationReports;
		for (const FAssetTransactionOperation& Prepared : Operations)
		{
			TSharedRef<FJsonObject> OperationReport = MakeShared<FJsonObject>();
			OperationReport->SetStringField(TEXT("operationId"), Prepared.OperationId);
			OperationReport->SetStringField(TEXT("operation"), Prepared.Operation);
			OperationReport->SetObjectField(TEXT("target"), Prepared.Target);
			OperationReport->SetStringField(TEXT("targetDescription"), Prepared.TargetDescription);
			OperationReport->SetStringField(TEXT("targetType"), Prepared.TargetType);
			OperationReport->SetField(TEXT("beforeValue"), Prepared.BeforeValue);
			OperationReport->SetField(TEXT("afterValue"), Prepared.AfterValue);
			OperationReport->SetField(
				TEXT("restoredValue"),
				bRolledBack ? Prepared.BeforeValue : MakeShared<FJsonValueNull>());
			TArray<TSharedPtr<FJsonValue>> AuthorizationValues;
			for (const FString& AuthorizationKey : Prepared.AuthorizationKeys)
			{
				AuthorizationValues.Add(MakeShared<FJsonValueString>(AuthorizationKey));
			}
			OperationReport->SetArrayField(TEXT("authorizationKeys"), AuthorizationValues);
			OperationReport->SetBoolField(TEXT("applied"), true);
			OperationReport->SetBoolField(TEXT("rollbackValueMatch"), !bRolledBack || bDiskUnchanged);
			if (Prepared.Kind >= EAssetTransactionKind::MaterialScalar
				&& Prepared.Kind <= EAssetTransactionKind::MaterialStaticSwitch)
			{
				OperationReport->SetBoolField(TEXT("beforeOverride"), Prepared.bBeforeOverride);
				OperationReport->SetBoolField(TEXT("afterOverride"), Prepared.bAfterOverride);
				OperationReport->SetStringField(
					TEXT("beforeExpressionGuid"),
					FormatMaterialExpressionGuid(Prepared.BeforeExpressionGuid));
				OperationReport->SetStringField(
					TEXT("afterExpressionGuid"),
					FormatMaterialExpressionGuid(Prepared.AfterExpressionGuid));
			}
			OperationReports.Add(MakeShared<FJsonValueObject>(OperationReport));
		}

		TSharedRef<FJsonObject> Report = MakeShared<FJsonObject>();
		Report->SetStringField(TEXT("schemaVersion"), TEXT("1.0"));
		Report->SetStringField(TEXT("executorVersion"), TEXT("0.7.0"));
		Report->SetStringField(TEXT("mode"), bCommit ? TEXT("Commit") : TEXT("DryRun"));
		Report->SetStringField(TEXT("patchId"), PatchId);
		Report->SetStringField(TEXT("projectName"), FApp::GetProjectName());
		Report->SetStringField(TEXT("assetPath"), AssetPath);
		Report->SetStringField(TEXT("assetClass"), ActualAssetClass);
		Report->SetStringField(TEXT("operation"), TEXT("transaction"));
		Report->SetObjectField(TEXT("target"), MakeShared<FJsonObject>());
		Report->SetStringField(TEXT("targetDescription"), TEXT("single-asset-multi-operation"));
		Report->SetStringField(TEXT("targetType"), TEXT("AssetTransaction"));
		Report->SetStringField(TEXT("transactionKind"), TEXT("single-asset-multi-operation"));
		Report->SetStringField(TEXT("rollbackStrategy"), bRolledBack ? TEXT("process-discard") : TEXT("package-backup"));
		Report->SetNumberField(TEXT("operationCount"), Operations.Num());
		Report->SetArrayField(TEXT("operations"), OperationReports);
		Report->SetStringField(TEXT("beforeRevision"), BeforeRevision);
		Report->SetStringField(TEXT("afterRevision"), AfterRevision);
		Report->SetBoolField(TEXT("compiled"), false);
		Report->SetBoolField(TEXT("saved"), bSaved);
		Report->SetBoolField(TEXT("rolledBack"), bRolledBack);
		Report->SetBoolField(TEXT("atomic"), true);
		Report->SetBoolField(TEXT("rollbackValueMatch"), !bRolledBack || bDiskUnchanged);
		Report->SetBoolField(TEXT("diskUnchanged"), bDiskUnchanged);
		Report->SetBoolField(TEXT("originalPackageDirty"), bOriginalDirty);
		Report->SetStringField(TEXT("backupPath"), BackupFilename);
		if (!SaveReport(ReportFilename, Report, Error))
		{
			if (bCommit && !BackupFilename.IsEmpty())
			{
				IFileManager::Get().Copy(*PackageFilename, *BackupFilename, true, true);
			}
			UE_LOG(LogAssetPatch, Error, TEXT("%s Transaction backup restored."), *Error);
			return 23;
		}
		if (bRolledBack && !bDiskUnchanged)
		{
			UE_LOG(LogAssetPatch, Error, TEXT("Asset transaction Dry Run changed the package on disk."));
			return 22;
		}

		UE_LOG(
			LogAssetPatch,
			Display,
			TEXT("Asset transaction succeeded. Mode=%s Asset=%s Operations=%d"),
			bCommit ? TEXT("Commit") : TEXT("DryRun"),
			*AssetPath,
			Operations.Num());
		return 0;
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
		|| OperationValues->IsEmpty()
		|| OperationValues->Num() > 32)
	{
		UE_LOG(LogAssetPatch, Error, TEXT("One to 32 operations are required per patch."));
		return 14;
	}
	if (OperationValues->Num() > 1)
	{
		if (bTestFailureInjectionRequested)
		{
			UE_LOG(LogAssetPatch, Error, TEXT("Test failure injection is not supported for transactions."));
			return 25;
		}
		return ExecuteAssetTransaction(
			Asset,
			Package,
			*OperationValues,
			Policy,
			bCommit,
			PatchId,
			AssetPath,
			ActualAssetClass,
			PackageFilename,
			BeforeRevision,
			BackupDirectory,
			ReportFilename,
			bOriginalDirty);
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
	const bool bAssetReferencePropertyOperation =
		Operation.Equals(TEXT("setAssetReferenceProperty"), ESearchCase::CaseSensitive);
	const bool bAssetStructuredPropertyOperation =
		Operation.Equals(TEXT("setAssetStructuredProperty"), ESearchCase::CaseSensitive);
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
			&& !bAssetReferencePropertyOperation
			&& !bAssetStructuredPropertyOperation
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
		FGuid ScalarExpressionGuid;
		if (!FindGlobalScalarParameter(
			MaterialInstance,
			FName(*ParameterNameText),
			ParameterInfo,
			ScalarExpressionGuid,
			Error))
		{
			UE_LOG(LogAssetPatch, Error, TEXT("%s"), *Error);
			return 17;
		}

		float BeforeScalarValue = 0.0f;
		bool bBeforeOverride = false;
		FGuid BeforeExpressionGuid;
		if (!ReadScalarParameter(
			MaterialInstance,
			ParameterInfo,
			ScalarExpressionGuid,
			BeforeScalarValue,
			bBeforeOverride,
			BeforeExpressionGuid))
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
		bool bAfterOverride = false;
		FGuid AfterExpressionGuid;
		if (!ReadScalarParameter(
				MaterialInstance,
				ParameterInfo,
				ScalarExpressionGuid,
				AfterScalarValue,
				bAfterOverride,
				AfterExpressionGuid)
			|| !FMath::IsNearlyEqual(AfterScalarValue, NewScalarValue, UE_SMALL_NUMBER)
			|| !bAfterOverride
			|| AfterExpressionGuid != ScalarExpressionGuid)
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
			MaterialInstance->ScalarParameterValues = OriginalScalarParameters;
			UMaterialEditingLibrary::UpdateMaterialInstance(MaterialInstance);
			Package->SetDirtyFlag(bOriginalDirty);
			bStructureMatch = ScalarParameterArraysEqualExact(
				OriginalScalarParameters,
				MaterialInstance->ScalarParameterValues);
			if (!ReadScalarParameter(
				MaterialInstance,
				ParameterInfo,
				ScalarExpressionGuid,
				RestoredScalarValue,
				bRestoredOverride,
				RestoredExpressionGuid))
			{
				UE_LOG(LogAssetPatch, Error, TEXT("Could not read restored material scalar parameter."));
				return 22;
			}
			bRolledBack = true;
		}

		const FString AfterRevision = HashPackageFile(Package);
		const bool bRestoredValueMatch =
			FMath::IsNearlyEqual(RestoredScalarValue, BeforeScalarValue, UE_SMALL_NUMBER);
		const TSharedRef<FJsonObject> Report = MakeShared<FJsonObject>();
		Report->SetStringField(TEXT("schemaVersion"), TEXT("1.0"));
		Report->SetStringField(TEXT("executorVersion"), TEXT("0.7.0"));
		Report->SetStringField(TEXT("mode"), bCommit ? TEXT("Commit") : TEXT("DryRun"));
		Report->SetStringField(TEXT("patchId"), PatchId);
		Report->SetStringField(TEXT("projectName"), FApp::GetProjectName());
		Report->SetStringField(TEXT("assetPath"), AssetPath);
		Report->SetStringField(TEXT("assetClass"), ActualAssetClass);
		Report->SetStringField(TEXT("operation"), Operation);
		Report->SetObjectField(TEXT("target"), TargetObject);
		AddMaterialParameterReport(
			Report,
			ParameterNameText,
			TEXT("Scalar"),
			MakeShared<FJsonValueNumber>(BeforeScalarValue),
			MakeShared<FJsonValueNumber>(AfterScalarValue),
			MakeShared<FJsonValueNumber>(RestoredScalarValue),
			bBeforeOverride,
			bAfterOverride,
			bRestoredOverride,
			BeforeExpressionGuid,
			AfterExpressionGuid,
			RestoredExpressionGuid,
			!FMath::IsNearlyEqual(AfterScalarValue, BeforeScalarValue, UE_SMALL_NUMBER),
			bRestoredValueMatch,
			bRolledBack);
		Report->SetStringField(TEXT("beforeRevision"), BeforeRevision);
		Report->SetStringField(TEXT("afterRevision"), AfterRevision);
		Report->SetBoolField(TEXT("compiled"), false);
		Report->SetBoolField(TEXT("saved"), bSaved);
		Report->SetBoolField(TEXT("rolledBack"), bRolledBack);
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
		if (bRolledBack
			&& (!Report->GetBoolField(TEXT("rollbackStateMatch"))
				|| !bStructureMatch
				|| !BeforeRevision.Equals(AfterRevision, ESearchCase::IgnoreCase)))
		{
			UE_LOG(LogAssetPatch, Error, TEXT("Material parameter Dry Run rollback verification failed."));
			return 22;
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
		FGuid VectorExpressionGuid;
		if (!FindGlobalVectorParameter(
			MaterialInstance,
			FName(*ParameterNameText),
			ParameterInfo,
			VectorExpressionGuid,
			Error))
		{
			UE_LOG(LogAssetPatch, Error, TEXT("%s"), *Error);
			return 17;
		}

		FLinearColor BeforeVectorValue = FLinearColor::Black;
		bool bBeforeOverride = false;
		FGuid BeforeExpressionGuid;
		if (!ReadVectorParameter(
			MaterialInstance,
			ParameterInfo,
			VectorExpressionGuid,
			BeforeVectorValue,
			bBeforeOverride,
			BeforeExpressionGuid))
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
		bool bAfterOverride = false;
		FGuid AfterExpressionGuid;
		if (!ReadVectorParameter(
				MaterialInstance,
				ParameterInfo,
				VectorExpressionGuid,
				AfterVectorValue,
				bAfterOverride,
				AfterExpressionGuid)
			|| !AfterVectorValue.Equals(NewVectorValue, UE_SMALL_NUMBER)
			|| !bAfterOverride
			|| AfterExpressionGuid != VectorExpressionGuid)
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
			MaterialInstance->VectorParameterValues = OriginalVectorParameters;
			UMaterialEditingLibrary::UpdateMaterialInstance(MaterialInstance);
			Package->SetDirtyFlag(bOriginalDirty);
			bStructureMatch = VectorParameterArraysEqualExact(
				OriginalVectorParameters,
				MaterialInstance->VectorParameterValues);
			if (!ReadVectorParameter(
				MaterialInstance,
				ParameterInfo,
				VectorExpressionGuid,
				RestoredVectorValue,
				bRestoredOverride,
				RestoredExpressionGuid))
			{
				UE_LOG(LogAssetPatch, Error, TEXT("Could not read restored material vector parameter."));
				return 22;
			}
			bRolledBack = true;
		}

		const FString AfterRevision = HashPackageFile(Package);
		const bool bRestoredValueMatch =
			RestoredVectorValue.Equals(BeforeVectorValue, UE_SMALL_NUMBER);
		const TSharedRef<FJsonObject> Report = MakeShared<FJsonObject>();
		Report->SetStringField(TEXT("schemaVersion"), TEXT("1.0"));
		Report->SetStringField(TEXT("executorVersion"), TEXT("0.7.0"));
		Report->SetStringField(TEXT("mode"), bCommit ? TEXT("Commit") : TEXT("DryRun"));
		Report->SetStringField(TEXT("patchId"), PatchId);
		Report->SetStringField(TEXT("projectName"), FApp::GetProjectName());
		Report->SetStringField(TEXT("assetPath"), AssetPath);
		Report->SetStringField(TEXT("assetClass"), ActualAssetClass);
		Report->SetStringField(TEXT("operation"), Operation);
		Report->SetObjectField(TEXT("target"), TargetObject);
		AddMaterialParameterReport(
			Report,
			ParameterNameText,
			TEXT("Vector"),
			MakeShared<FJsonValueObject>(MakeMaterialVectorValue(BeforeVectorValue)),
			MakeShared<FJsonValueObject>(MakeMaterialVectorValue(AfterVectorValue)),
			MakeShared<FJsonValueObject>(MakeMaterialVectorValue(RestoredVectorValue)),
			bBeforeOverride,
			bAfterOverride,
			bRestoredOverride,
			BeforeExpressionGuid,
			AfterExpressionGuid,
			RestoredExpressionGuid,
			!AfterVectorValue.Equals(BeforeVectorValue, UE_SMALL_NUMBER),
			bRestoredValueMatch,
			bRolledBack);
		Report->SetStringField(TEXT("beforeRevision"), BeforeRevision);
		Report->SetStringField(TEXT("afterRevision"), AfterRevision);
		Report->SetBoolField(TEXT("compiled"), false);
		Report->SetBoolField(TEXT("saved"), bSaved);
		Report->SetBoolField(TEXT("rolledBack"), bRolledBack);
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
		if (bRolledBack
			&& (!Report->GetBoolField(TEXT("rollbackStateMatch"))
				|| !bStructureMatch
				|| !BeforeRevision.Equals(AfterRevision, ESearchCase::IgnoreCase)))
		{
			UE_LOG(LogAssetPatch, Error, TEXT("Material parameter Dry Run rollback verification failed."));
			return 22;
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
		FGuid TextureExpressionGuid;
		if (!FindGlobalTextureParameter(
			MaterialInstance,
			FName(*ParameterNameText),
			ParameterInfo,
			TextureExpressionGuid,
			Error))
		{
			UE_LOG(LogAssetPatch, Error, TEXT("%s"), *Error);
			return 17;
		}

		UTexture* BeforeTextureValue = nullptr;
		bool bBeforeOverride = false;
		FGuid BeforeExpressionGuid;
		if (!ReadTextureParameter(
			MaterialInstance,
			ParameterInfo,
			TextureExpressionGuid,
			BeforeTextureValue,
			bBeforeOverride,
			BeforeExpressionGuid))
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
		bool bAfterOverride = false;
		FGuid AfterExpressionGuid;
		if (!ReadTextureParameter(
				MaterialInstance,
				ParameterInfo,
				TextureExpressionGuid,
				AfterTextureValue,
				bAfterOverride,
				AfterExpressionGuid)
			|| AfterTextureValue != NewTexture
			|| !bAfterOverride
			|| AfterExpressionGuid != TextureExpressionGuid)
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
			MaterialInstance->TextureParameterValues = OriginalTextureParameters;
			UMaterialEditingLibrary::UpdateMaterialInstance(MaterialInstance);
			Package->SetDirtyFlag(bOriginalDirty);
			bStructureMatch = TextureParameterArraysEqualExact(
				OriginalTextureParameters,
				MaterialInstance->TextureParameterValues);
			if (!ReadTextureParameter(
				MaterialInstance,
				ParameterInfo,
				TextureExpressionGuid,
				RestoredTextureValue,
				bRestoredOverride,
				RestoredExpressionGuid))
			{
				UE_LOG(LogAssetPatch, Error, TEXT("Could not read restored material texture parameter."));
				return 22;
			}
			bRolledBack = true;
		}

		const FString AfterRevision = HashPackageFile(Package);
		const bool bRestoredValueMatch = RestoredTextureValue == BeforeTextureValue;
		const TSharedRef<FJsonObject> Report = MakeShared<FJsonObject>();
		Report->SetStringField(TEXT("schemaVersion"), TEXT("1.0"));
		Report->SetStringField(TEXT("executorVersion"), TEXT("0.7.0"));
		Report->SetStringField(TEXT("mode"), bCommit ? TEXT("Commit") : TEXT("DryRun"));
		Report->SetStringField(TEXT("patchId"), PatchId);
		Report->SetStringField(TEXT("projectName"), FApp::GetProjectName());
		Report->SetStringField(TEXT("assetPath"), AssetPath);
		Report->SetStringField(TEXT("assetClass"), ActualAssetClass);
		Report->SetStringField(TEXT("operation"), Operation);
		Report->SetObjectField(TEXT("target"), TargetObject);
		AddMaterialParameterReport(
			Report,
			ParameterNameText,
			TEXT("Texture"),
			MakeMaterialTextureValue(BeforeTextureValue),
			MakeMaterialTextureValue(AfterTextureValue),
			MakeMaterialTextureValue(RestoredTextureValue),
			bBeforeOverride,
			bAfterOverride,
			bRestoredOverride,
			BeforeExpressionGuid,
			AfterExpressionGuid,
			RestoredExpressionGuid,
			AfterTextureValue != BeforeTextureValue,
			bRestoredValueMatch,
			bRolledBack);
		Report->SetStringField(TEXT("referencedAssetPath"), NewTexturePath);
		Report->SetStringField(TEXT("referencedAssetClass"), NewTextureClass);
		Report->SetStringField(TEXT("beforeRevision"), BeforeRevision);
		Report->SetStringField(TEXT("afterRevision"), AfterRevision);
		Report->SetBoolField(TEXT("compiled"), false);
		Report->SetBoolField(TEXT("saved"), bSaved);
		Report->SetBoolField(TEXT("rolledBack"), bRolledBack);
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
		if (bRolledBack
			&& (!Report->GetBoolField(TEXT("rollbackStateMatch"))
				|| !bStructureMatch
				|| !BeforeRevision.Equals(AfterRevision, ESearchCase::IgnoreCase)))
		{
			UE_LOG(LogAssetPatch, Error, TEXT("Material parameter Dry Run rollback verification failed."));
			return 22;
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
		const bool bRestoredValueMatch = RestoredSwitchValue == BeforeSwitchValue;
		const TSharedRef<FJsonObject> Report = MakeShared<FJsonObject>();
		Report->SetStringField(TEXT("schemaVersion"), TEXT("1.0"));
		Report->SetStringField(TEXT("executorVersion"), TEXT("0.7.0"));
		Report->SetStringField(TEXT("mode"), bCommit ? TEXT("Commit") : TEXT("DryRun"));
		Report->SetStringField(TEXT("patchId"), PatchId);
		Report->SetStringField(TEXT("projectName"), FApp::GetProjectName());
		Report->SetStringField(TEXT("assetPath"), AssetPath);
		Report->SetStringField(TEXT("assetClass"), ActualAssetClass);
		Report->SetStringField(TEXT("operation"), Operation);
		Report->SetObjectField(TEXT("target"), TargetObject);
		AddMaterialParameterReport(
			Report,
			ParameterNameText,
			TEXT("StaticSwitch"),
			MakeShared<FJsonValueBoolean>(BeforeSwitchValue),
			MakeShared<FJsonValueBoolean>(AfterSwitchValue),
			MakeShared<FJsonValueBoolean>(RestoredSwitchValue),
			bBeforeOverride,
			bAfterOverride,
			bRestoredOverride,
			BeforeExpressionGuid,
			AfterExpressionGuid,
			RestoredExpressionGuid,
			AfterSwitchValue != BeforeSwitchValue,
			bRestoredValueMatch,
			bRolledBack);
		Report->SetStringField(TEXT("beforeRevision"), BeforeRevision);
		Report->SetStringField(TEXT("afterRevision"), AfterRevision);
		Report->SetBoolField(TEXT("compiled"), false);
		Report->SetBoolField(TEXT("saved"), bSaved);
		Report->SetBoolField(TEXT("rolledBack"), bRolledBack);
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
		if (bRolledBack
			&& (!Report->GetBoolField(TEXT("rollbackStateMatch"))
				|| !bStructureMatch
				|| !BeforeRevision.Equals(AfterRevision, ESearchCase::IgnoreCase)))
		{
			UE_LOG(LogAssetPatch, Error, TEXT("Material parameter Dry Run rollback verification failed."));
			return 22;
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
		Report->SetStringField(TEXT("executorVersion"), TEXT("0.7.0"));
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
		Report->SetStringField(TEXT("executorVersion"), TEXT("0.7.0"));
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



	if (bAssetStructuredPropertyOperation)
	{
		if (Cast<UDataAsset>(Asset) == nullptr)
		{
			UE_LOG(LogAssetPatch, Error, TEXT("setAssetStructuredProperty requires a Data Asset."));
			return 17;
		}
		FString PropertyPath;
		TargetObject->TryGetStringField(TEXT("propertyPath"), PropertyPath);
		if (PropertyPath.IsEmpty() || PropertyPath.Contains(TEXT(".")))
		{
			UE_LOG(LogAssetPatch, Error, TEXT("Asset structured propertyPath must be one top-level property name."));
			return 17;
		}
		const FString PropertyAuthorization = ActualAssetClass + TEXT("#") + PropertyPath;
		if (!ContainsExact(Policy.AllowedAssetProperties, PropertyAuthorization))
		{
			UE_LOG(LogAssetPatch, Error, TEXT("Asset structured property is not authorized by policy: %s"), *PropertyAuthorization);
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
		if (!Property->HasAnyPropertyFlags(CPF_Edit)
			|| Property->HasAnyPropertyFlags(DisallowedFlags)
			|| Property->ArrayDim != 1)
		{
			UE_LOG(LogAssetPatch, Error, TEXT("Asset structured property must be editable, non-transient, and non-fixed-array: %s"), *PropertyPath);
			return 17;
		}
		const UEAgentKit::StructuredPropertyJson::EKind StructuredKind =
			UEAgentKit::StructuredPropertyJson::GetKind(Property);
		if (StructuredKind == UEAgentKit::StructuredPropertyJson::EKind::Invalid)
		{
			UE_LOG(LogAssetPatch, Error, TEXT("Property is not Struct, Array, Set, or Map: %s"), *PropertyPath);
			return 17;
		}
		TSharedPtr<FJsonValue> StructuredSchema;
		if (!UEAgentKit::StructuredPropertyJson::BuildSchema(Property, StructuredSchema, Error))
		{
			UE_LOG(LogAssetPatch, Error, TEXT("Structured property is unsupported: %s"), *Error);
			return 17;
		}

		const TSharedPtr<FJsonValue> NewValue = OperationObject->TryGetField(TEXT("value"));
		if (!NewValue.IsValid())
		{
			UE_LOG(LogAssetPatch, Error, TEXT("Operation value is missing."));
			return 18;
		}
		TSharedPtr<FJsonValue> BeforeValue;
		if (!UEAgentKit::StructuredPropertyJson::ExportValue(Property, ValueAddress, BeforeValue, Error))
		{
			UE_LOG(LogAssetPatch, Error, TEXT("Could not export structured property before modification: %s"), *Error);
			return 18;
		}
		FScopedPropertyValueBackup ValueBackup(Property, ValueAddress);
		if (!ValueBackup.IsValid())
		{
			UE_LOG(LogAssetPatch, Error, TEXT("Could not allocate structured property backup."));
			return 18;
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

		Asset->Modify();
		if (!UEAgentKit::StructuredPropertyJson::ImportValue(Property, ValueAddress, NewValue, Error))
		{
			ValueBackup.Restore(ValueAddress);
			Asset->PostEditChange();
			Package->SetDirtyFlag(bOriginalDirty);
			UE_LOG(LogAssetPatch, Error, TEXT("Could not import structured property: %s"), *Error);
			return 20;
		}
		Asset->PostEditChange();
		Package->MarkPackageDirty();

		TSharedPtr<FJsonValue> AfterValue;
		if (!UEAgentKit::StructuredPropertyJson::ExportValue(Property, ValueAddress, AfterValue, Error)
			|| !UEAgentKit::StructuredPropertyJson::JsonEqual(AfterValue, NewValue))
		{
			ValueBackup.Restore(ValueAddress);
			Asset->PostEditChange();
			Package->SetDirtyFlag(bOriginalDirty);
			UE_LOG(LogAssetPatch, Error, TEXT("Asset structured property read-back verification failed: %s"), *Error);
			return 20;
		}

		TArray<TSharedPtr<FJsonValue>> StructuredDiff;
		bool bStructuredDiffTruncated = false;
		UEAgentKit::StructuredPropertyJson::BuildDiff(
			BeforeValue,
			AfterValue,
			StructuredDiff,
			bStructuredDiffTruncated);

		bool bSaved = false;
		bool bRolledBack = false;
		TSharedPtr<FJsonValue> RestoredValue;
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
			ValueBackup.Restore(ValueAddress);
			Asset->PostEditChange();
			Package->SetDirtyFlag(bOriginalDirty);
			if (!UEAgentKit::StructuredPropertyJson::ExportValue(Property, ValueAddress, RestoredValue, Error))
			{
				UE_LOG(LogAssetPatch, Error, TEXT("Could not export restored structured property: %s"), *Error);
				return 22;
			}
			bRolledBack = true;
		}

		const FString AfterRevision = HashPackageFile(Package);
		const bool bRollbackValueMatch = !bRolledBack
			|| UEAgentKit::StructuredPropertyJson::JsonEqual(RestoredValue, BeforeValue);
		const TSharedRef<FJsonObject> Report = MakeShared<FJsonObject>();
		Report->SetStringField(TEXT("schemaVersion"), TEXT("1.0"));
		Report->SetStringField(TEXT("executorVersion"), TEXT("0.7.0"));
		Report->SetStringField(TEXT("mode"), bCommit ? TEXT("Commit") : TEXT("DryRun"));
		Report->SetStringField(TEXT("patchId"), PatchId);
		Report->SetStringField(TEXT("projectName"), FApp::GetProjectName());
		Report->SetStringField(TEXT("assetPath"), AssetPath);
		Report->SetStringField(TEXT("assetClass"), ActualAssetClass);
		Report->SetStringField(TEXT("operation"), Operation);
		Report->SetObjectField(TEXT("target"), TargetObject);
		Report->SetStringField(TEXT("targetDescription"), TEXT("asset-structured-property:") + PropertyPath);
		Report->SetStringField(TEXT("targetType"), Property->GetClass()->GetName());
		Report->SetStringField(
			TEXT("structuredType"),
			UEAgentKit::StructuredPropertyJson::KindName(StructuredKind));
		Report->SetField(TEXT("structuredSchema"), StructuredSchema);
		Report->SetField(TEXT("beforeStructuredValue"), BeforeValue);
		Report->SetField(TEXT("afterStructuredValue"), AfterValue);
		Report->SetField(TEXT("restoredStructuredValue"), bRolledBack ? RestoredValue : BeforeValue);
		Report->SetArrayField(TEXT("structuredDiff"), StructuredDiff);
		Report->SetNumberField(TEXT("structuredDiffCount"), StructuredDiff.Num());
		Report->SetBoolField(TEXT("structuredDiffTruncated"), bStructuredDiffTruncated);
		Report->SetStringField(TEXT("beforeValue"), UEAgentKit::StructuredPropertyJson::CanonicalJson(BeforeValue));
		Report->SetStringField(TEXT("afterValue"), UEAgentKit::StructuredPropertyJson::CanonicalJson(AfterValue));
		Report->SetStringField(
			TEXT("restoredValue"),
			UEAgentKit::StructuredPropertyJson::CanonicalJson(bRolledBack ? RestoredValue : BeforeValue));
		Report->SetStringField(TEXT("beforeRevision"), BeforeRevision);
		Report->SetStringField(TEXT("afterRevision"), AfterRevision);
		Report->SetBoolField(TEXT("compiled"), false);
		Report->SetBoolField(TEXT("saved"), bSaved);
		Report->SetBoolField(TEXT("rolledBack"), bRolledBack);
		Report->SetBoolField(TEXT("appliedValueMatch"), true);
		Report->SetBoolField(TEXT("rollbackValueMatch"), bRollbackValueMatch);
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
		if (bRolledBack
			&& (!bRollbackValueMatch || !BeforeRevision.Equals(AfterRevision, ESearchCase::IgnoreCase)))
		{
			UE_LOG(LogAssetPatch, Error, TEXT("Asset structured Dry Run rollback verification failed."));
			return 22;
		}
		UE_LOG(
			LogAssetPatch,
			Display,
			TEXT("Asset structured patch succeeded. Mode=%s Asset=%s Property=%s Type=%s Diff=%d"),
			bCommit ? TEXT("Commit") : TEXT("DryRun"),
			*AssetPath,
			*PropertyPath,
			*UEAgentKit::StructuredPropertyJson::KindName(StructuredKind),
			StructuredDiff.Num());
		return 0;
	}


	if (bAssetReferencePropertyOperation)
	{
		if (Cast<UDataAsset>(Asset) == nullptr)
		{
			UE_LOG(LogAssetPatch, Error, TEXT("setAssetReferenceProperty requires a Data Asset."));
			return 17;
		}
		FString PropertyPath;
		TargetObject->TryGetStringField(TEXT("propertyPath"), PropertyPath);
		if (PropertyPath.IsEmpty() || PropertyPath.Contains(TEXT(".")))
		{
			UE_LOG(LogAssetPatch, Error, TEXT("Asset reference propertyPath must be one top-level property name."));
			return 17;
		}
		const FString PropertyAuthorization = ActualAssetClass + TEXT("#") + PropertyPath;
		if (!ContainsExact(Policy.AllowedAssetProperties, PropertyAuthorization))
		{
			UE_LOG(LogAssetPatch, Error, TEXT("Asset reference property is not authorized by policy: %s"), *PropertyAuthorization);
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
			UE_LOG(LogAssetPatch, Error, TEXT("Asset reference property must be editable and non-transient: %s"), *PropertyPath);
			return 17;
		}
		UClass* ConstraintClass = nullptr;
		const EAssetReferenceType ReferencePropertyType = GetAssetReferenceType(Property, ConstraintClass);
		if (ReferencePropertyType == EAssetReferenceType::Invalid || ConstraintClass == nullptr)
		{
			UE_LOG(LogAssetPatch, Error, TEXT("Property is not a supported Object/Class/Soft Object/Soft Class reference: %s"), *PropertyPath);
			return 17;
		}

		const TSharedPtr<FJsonValue> NewValue = OperationObject->TryGetField(TEXT("value"));
		if (!NewValue.IsValid())
		{
			UE_LOG(LogAssetPatch, Error, TEXT("Operation value is missing."));
			return 18;
		}
		FString BeforeValue;
		FString BeforeReferencePath;
		ReadPropertyValue(Asset, Property, ValueAddress, BeforeValue);
		ReadAssetReferencePath(Property, ValueAddress, BeforeReferencePath);

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

		FString ReferenceType;
		FString RequestedReferencePath;
		FString ResolvedReferenceClassPath;
		Asset->Modify();
		if (!SetAssetReferenceFromJson(
				Property,
				ValueAddress,
				NewValue,
				Policy,
				ReferenceType,
				RequestedReferencePath,
				ResolvedReferenceClassPath,
				Error))
		{
			UE_LOG(LogAssetPatch, Error, TEXT("%s"), *Error);
			return 20;
		}
		Asset->PostEditChange();
		Package->MarkPackageDirty();

		FString AfterValue;
		FString AfterReferencePath;
		ReadPropertyValue(Asset, Property, ValueAddress, AfterValue);
		ReadAssetReferencePath(Property, ValueAddress, AfterReferencePath);
		if (!AfterReferencePath.Equals(RequestedReferencePath, ESearchCase::CaseSensitive))
		{
			RestorePropertyValue(Asset, Property, ValueAddress, BeforeValue, Error);
			Asset->PostEditChange();
			Package->SetDirtyFlag(bOriginalDirty);
			UE_LOG(LogAssetPatch, Error, TEXT("Asset reference read-back verification failed."));
			return 20;
		}

		bool bSaved = false;
		bool bRolledBack = false;
		FString RestoredValue;
		FString RestoredReferencePath;
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
			ReadAssetReferencePath(Property, ValueAddress, RestoredReferencePath);
			bRolledBack = true;
		}

		const FString AfterRevision = HashPackageFile(Package);
		const bool bRollbackValueMatch = !bRolledBack
			|| (RestoredValue.Equals(BeforeValue, ESearchCase::CaseSensitive)
				&& RestoredReferencePath.Equals(BeforeReferencePath, ESearchCase::CaseSensitive));
		const TSharedRef<FJsonObject> Report = MakeShared<FJsonObject>();
		Report->SetStringField(TEXT("schemaVersion"), TEXT("1.0"));
		Report->SetStringField(TEXT("executorVersion"), TEXT("0.7.0"));
		Report->SetStringField(TEXT("mode"), bCommit ? TEXT("Commit") : TEXT("DryRun"));
		Report->SetStringField(TEXT("patchId"), PatchId);
		Report->SetStringField(TEXT("projectName"), FApp::GetProjectName());
		Report->SetStringField(TEXT("assetPath"), AssetPath);
		Report->SetStringField(TEXT("assetClass"), ActualAssetClass);
		Report->SetStringField(TEXT("operation"), Operation);
		Report->SetObjectField(TEXT("target"), TargetObject);
		Report->SetStringField(TEXT("targetDescription"), TEXT("asset-reference-property:") + PropertyPath);
		Report->SetStringField(TEXT("targetType"), Property->GetClass()->GetName());
		Report->SetStringField(TEXT("referenceType"), ReferenceType);
		Report->SetStringField(TEXT("referenceConstraintClass"), ConstraintClass->GetPathName());
		Report->SetStringField(TEXT("referencePath"), RequestedReferencePath);
		Report->SetStringField(TEXT("resolvedReferenceClass"), ResolvedReferenceClassPath);
		Report->SetStringField(TEXT("beforeValue"), BeforeValue);
		Report->SetStringField(TEXT("afterValue"), AfterValue);
		Report->SetStringField(TEXT("restoredValue"), RestoredValue);
		Report->SetStringField(TEXT("beforeRevision"), BeforeRevision);
		Report->SetStringField(TEXT("afterRevision"), AfterRevision);
		Report->SetBoolField(TEXT("compiled"), false);
		Report->SetBoolField(TEXT("saved"), bSaved);
		Report->SetBoolField(TEXT("rolledBack"), bRolledBack);
		Report->SetBoolField(TEXT("appliedValueMatch"), true);
		Report->SetBoolField(TEXT("rollbackValueMatch"), bRollbackValueMatch);
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
		if (bRolledBack
			&& (!bRollbackValueMatch || !BeforeRevision.Equals(AfterRevision, ESearchCase::IgnoreCase)))
		{
			UE_LOG(LogAssetPatch, Error, TEXT("Asset reference Dry Run rollback verification failed."));
			return 22;
		}
		UE_LOG(
			LogAssetPatch,
			Display,
			TEXT("Asset reference patch succeeded. Mode=%s Asset=%s Property=%s Type=%s Path=%s"),
			bCommit ? TEXT("Commit") : TEXT("DryRun"),
			*AssetPath,
			*PropertyPath,
			*ReferenceType,
			*RequestedReferencePath);
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
	Report->SetStringField(TEXT("executorVersion"), TEXT("0.7.0"));
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
