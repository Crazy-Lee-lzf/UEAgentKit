#include "EditorBridge.h"
#include "EditorBridgeHandlerUtils.h"
#include "LiveWriteTransaction.h"
#include "StructuredPropertyJson.h"

#include "Dom/JsonValue.h"
#include "Editor.h"
#include "Engine/Blueprint.h"
#include "Engine/Texture.h"
#include "MaterialEditingLibrary.h"
#include "Materials/MaterialExpressionScalarParameter.h"
#include "Materials/MaterialExpressionStaticSwitchParameter.h"
#include "Materials/MaterialExpressionTextureSampleParameter.h"
#include "Materials/MaterialExpressionVectorParameter.h"
#include "Materials/MaterialInstanceConstant.h"
#include "ScopedTransaction.h"
#include "StaticParameterSet.h"
#include "UObject/Package.h"
#include "UObject/UnrealType.h"
#include "UObject/SoftObjectPath.h"
#include "UObject/SoftObjectPtr.h"
#include "UObject/UObjectGlobals.h"

using namespace UEAgentKitEditorBridgePrivate;

namespace
{
	bool IsUnsignedIntegerProperty(const FNumericProperty* Property)
	{
		return Property != nullptr && (
			Property->IsA<FByteProperty>()
			|| Property->IsA<FUInt16Property>()
			|| Property->IsA<FUInt32Property>()
			|| Property->IsA<FUInt64Property>());
	}

	bool IsSafeTopLevelPropertyPath(const FString& PropertyPath)
	{
		if (PropertyPath.IsEmpty() || PropertyPath.Len() > 128 || PropertyPath.Contains(TEXT(".")))
		{
			return false;
		}
		for (const TCHAR Character : PropertyPath)
		{
			if (!(FChar::IsAlnum(Character) || Character == TEXT('_')))
			{
				return false;
			}
		}
		return true;
	}

	bool ReadScalarValue(FProperty* Property, const void* ValueAddress, TSharedPtr<FJsonValue>& OutValue)
	{
		if (const FBoolProperty* BoolProperty = CastField<FBoolProperty>(Property))
		{
			OutValue = MakeShared<FJsonValueBoolean>(BoolProperty->GetPropertyValue(ValueAddress));
			return true;
		}
		if (const FEnumProperty* EnumProperty = CastField<FEnumProperty>(Property))
		{
			const int64 Value = EnumProperty->GetUnderlyingProperty()->GetSignedIntPropertyValue(ValueAddress);
			OutValue = MakeShared<FJsonValueString>(EnumProperty->GetEnum()->GetNameStringByValue(Value));
			return true;
		}
		if (const FNumericProperty* NumericProperty = CastField<FNumericProperty>(Property))
		{
			if (const UEnum* Enum = NumericProperty->GetIntPropertyEnum())
			{
				OutValue = MakeShared<FJsonValueString>(Enum->GetNameStringByValue(NumericProperty->GetSignedIntPropertyValue(ValueAddress)));
				return true;
			}
			if (NumericProperty->IsFloatingPoint())
			{
				OutValue = MakeShared<FJsonValueNumber>(NumericProperty->GetFloatingPointPropertyValue(ValueAddress));
			}
			else if (IsUnsignedIntegerProperty(NumericProperty))
			{
				OutValue = MakeShared<FJsonValueNumber>(static_cast<double>(NumericProperty->GetUnsignedIntPropertyValue(ValueAddress)));
			}
			else
			{
				OutValue = MakeShared<FJsonValueNumber>(static_cast<double>(NumericProperty->GetSignedIntPropertyValue(ValueAddress)));
			}
			return true;
		}
		if (const FStrProperty* StringProperty = CastField<FStrProperty>(Property))
		{
			OutValue = MakeShared<FJsonValueString>(StringProperty->GetPropertyValue(ValueAddress));
			return true;
		}
		if (const FNameProperty* NameProperty = CastField<FNameProperty>(Property))
		{
			OutValue = MakeShared<FJsonValueString>(NameProperty->GetPropertyValue(ValueAddress).ToString());
			return true;
		}
		if (const FTextProperty* TextProperty = CastField<FTextProperty>(Property))
		{
			OutValue = MakeShared<FJsonValueString>(TextProperty->GetPropertyValue(ValueAddress).ToString());
			return true;
		}
		return false;
	}

	bool SetScalarValue(
		FProperty* Property,
		void* ValueAddress,
		const TSharedPtr<FJsonValue>& JsonValue,
		FString& OutError)
	{
		if (!JsonValue.IsValid())
		{
			OutError = TEXT("value is required.");
			return false;
		}
		if (FBoolProperty* BoolProperty = CastField<FBoolProperty>(Property))
		{
			bool Value = false;
			if (!JsonValue->TryGetBool(Value))
			{
				OutError = TEXT("The property requires a JSON boolean.");
				return false;
			}
			BoolProperty->SetPropertyValue(ValueAddress, Value);
			return true;
		}
		if (FEnumProperty* EnumProperty = CastField<FEnumProperty>(Property))
		{
			FString Value;
			if (!JsonValue->TryGetString(Value))
			{
				OutError = TEXT("The enum property requires a JSON string.");
				return false;
			}
			const int64 EnumValue = EnumProperty->GetEnum()->GetValueByNameString(Value);
			if (EnumValue == INDEX_NONE)
			{
				OutError = TEXT("The enum value is unknown.");
				return false;
			}
			EnumProperty->GetUnderlyingProperty()->SetIntPropertyValue(ValueAddress, EnumValue);
			return true;
		}
		if (FNumericProperty* NumericProperty = CastField<FNumericProperty>(Property))
		{
			if (UEnum* Enum = NumericProperty->GetIntPropertyEnum())
			{
				FString Value;
				if (!JsonValue->TryGetString(Value))
				{
					OutError = TEXT("The enum-backed property requires a JSON string.");
					return false;
				}
				const int64 EnumValue = Enum->GetValueByNameString(Value);
				if (EnumValue == INDEX_NONE || !NumericProperty->CanHoldValue(EnumValue))
				{
					OutError = TEXT("The enum value is unknown or outside the property range.");
					return false;
				}
				NumericProperty->SetIntPropertyValue(ValueAddress, EnumValue);
				return true;
			}
			double Value = 0.0;
			if (!JsonValue->TryGetNumber(Value) || !FMath::IsFinite(Value))
			{
				OutError = TEXT("The numeric property requires a finite JSON number.");
				return false;
			}
			if (NumericProperty->IsFloatingPoint())
			{
				if (!NumericProperty->CanHoldValue(Value))
				{
					OutError = TEXT("The floating-point value is outside the property range.");
					return false;
				}
				NumericProperty->SetFloatingPointPropertyValue(ValueAddress, Value);
				return true;
			}
			if (Value != FMath::TruncToDouble(Value))
			{
				OutError = TEXT("The integer property requires a whole JSON number.");
				return false;
			}
			if (IsUnsignedIntegerProperty(NumericProperty))
			{
				if (Value < 0.0 || Value >= 18446744073709551616.0)
				{
					OutError = TEXT("The unsigned integer value is outside the property range.");
					return false;
				}
				const uint64 IntegerValue = static_cast<uint64>(Value);
				if (!NumericProperty->CanHoldValue(IntegerValue))
				{
					OutError = TEXT("The unsigned integer value is outside the property range.");
					return false;
				}
				NumericProperty->SetIntPropertyValue(ValueAddress, IntegerValue);
				return true;
			}
			if (Value < -9223372036854775808.0 || Value >= 9223372036854775808.0)
			{
				OutError = TEXT("The signed integer value is outside the property range.");
				return false;
			}
			const int64 IntegerValue = static_cast<int64>(Value);
			if (!NumericProperty->CanHoldValue(IntegerValue))
			{
				OutError = TEXT("The signed integer value is outside the property range.");
				return false;
			}
			NumericProperty->SetIntPropertyValue(ValueAddress, IntegerValue);
			return true;
		}
		FString Value;
		if (!JsonValue->TryGetString(Value))
		{
			OutError = TEXT("The property requires a JSON string.");
			return false;
		}
		if (FStrProperty* StringProperty = CastField<FStrProperty>(Property))
		{
			StringProperty->SetPropertyValue(ValueAddress, Value);
			return true;
		}
		if (FNameProperty* NameProperty = CastField<FNameProperty>(Property))
		{
			NameProperty->SetPropertyValue(ValueAddress, FName(*Value));
			return true;
		}
		if (FTextProperty* TextProperty = CastField<FTextProperty>(Property))
		{
			TextProperty->SetPropertyValue(ValueAddress, FText::FromString(Value));
			return true;
		}
		OutError = FString::Printf(TEXT("Unsupported live scalar property type: %s"), *Property->GetClass()->GetName());
		return false;
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

	bool SetAssetReferenceFromJson(
		FProperty* Property,
		void* ValueAddress,
		const TSharedPtr<FJsonValue>& JsonValue,
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
		if (!IsSafeGameAssetPath(RequestedPath))
		{
			OutError = FString::Printf(
				TEXT("Reference path must be an exact /Game object path without a subobject: %s"),
				*RequestedPath);
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

	class FLiveWriteScalarIO final : public UEAgentKitLiveWrite::ILiveWriteValueIO
	{
	public:
		FLiveWriteScalarIO(UObject* InAsset, FProperty* InProperty, void* InValueAddress)
			: Asset(InAsset)
			, Property(InProperty)
			, ValueAddress(InValueAddress)
		{
		}

		bool CaptureSnapshot() override
		{
			return Snapshot.Capture(Property, ValueAddress);
		}

		bool IsSnapshotValid() const override
		{
			return Snapshot.IsValid();
		}

		void RestoreSnapshot() override
		{
			Snapshot.Restore(ValueAddress);
		}

		void ReleaseSnapshot() override
		{
			Snapshot.Reset();
		}

		bool ReadBefore(
			TSharedPtr<FJsonValue>& OutValue,
			FString& OutErrorCode,
			FString& OutErrorMessage) override
		{
			if (!ReadScalarValue(Property, ValueAddress, OutValue))
			{
				OutErrorCode = TEXT("live-editor-write-property-type-unsupported");
				OutErrorMessage = TEXT("The Live Write scalar capability supports only scalar, enum, string, name, and text properties.");
				return false;
			}
			return true;
		}

		bool ApplyValue(
			const TSharedPtr<FJsonValue>& Value,
			FString& OutErrorCode,
			FString& OutErrorMessage) override
		{
			FString SetError;
			if (!SetScalarValue(Property, ValueAddress, Value, SetError))
			{
				OutErrorCode = TEXT("live-editor-write-value-invalid");
				OutErrorMessage = SetError;
				return false;
			}
			return true;
		}

		bool ReadAfter(
			const TSharedPtr<FJsonValue>& Requested,
			TSharedPtr<FJsonValue>& OutValue,
			FString& OutErrorCode,
			FString& OutErrorMessage) override
		{
			if (!ReadScalarValue(Property, ValueAddress, OutValue))
			{
				OutErrorCode = TEXT("live-editor-write-apply-failed");
				OutErrorMessage = TEXT("The Editor did not confirm the changed Dirty package state.");
				return false;
			}
			return true;
		}

		bool SemanticEqual(const TSharedPtr<FJsonValue>& Left, const TSharedPtr<FJsonValue>& Right) override
		{
			return UEAgentKit::StructuredPropertyJson::JsonEqual(Left, Right);
		}

		void NotifyChanged() override
		{
			NotifyPropertyChanged();
		}

		void NotifyRestored() override
		{
			NotifyPropertyChanged();
		}

	private:
		void NotifyPropertyChanged()
		{
			FPropertyChangedEvent ChangedEvent(Property, EPropertyChangeType::ValueSet);
			Asset->PostEditChangeProperty(ChangedEvent);
		}

		UObject* Asset = nullptr;
		FProperty* Property = nullptr;
		void* ValueAddress = nullptr;
		UEAgentKitLiveWrite::FLiveWriteSnapshot Snapshot;
	};

	class FLiveWriteReferenceIO final : public UEAgentKitLiveWrite::ILiveWriteValueIO
	{
	public:
		FLiveWriteReferenceIO(UObject* InAsset, FProperty* InProperty, void* InValueAddress)
			: Asset(InAsset)
			, Property(InProperty)
			, ValueAddress(InValueAddress)
		{
		}

		FString ResolvedClassPath;

		bool CaptureSnapshot() override
		{
			return Snapshot.Capture(Property, ValueAddress);
		}

		bool IsSnapshotValid() const override
		{
			return Snapshot.IsValid();
		}

		void RestoreSnapshot() override
		{
			Snapshot.Restore(ValueAddress);
		}

		void ReleaseSnapshot() override
		{
			Snapshot.Reset();
		}

		bool ReadBefore(
			TSharedPtr<FJsonValue>& OutValue,
			FString& OutErrorCode,
			FString& OutErrorMessage) override
		{
			FString BeforePath;
			if (!ReadAssetReferencePath(Property, ValueAddress, BeforePath))
			{
				OutErrorCode = TEXT("live-editor-write-property-type-unsupported");
				OutErrorMessage = TEXT("The reference property value could not be read.");
				return false;
			}
			if (BeforePath.IsEmpty())
			{
				OutValue = MakeShared<FJsonValueNull>();
			}
			else
			{
				OutValue = MakeShared<FJsonValueString>(BeforePath);
			}
			return true;
		}

		bool ApplyValue(
			const TSharedPtr<FJsonValue>& Value,
			FString& OutErrorCode,
			FString& OutErrorMessage) override
		{
			FString SetError;
			FString SetReferenceType;
			FString SetReferencePath;
			ResolvedClassPath.Reset();
			if (!SetAssetReferenceFromJson(
					Property,
					ValueAddress,
					Value,
					SetReferenceType,
					SetReferencePath,
					ResolvedClassPath,
					SetError))
			{
				OutErrorCode = TEXT("live-editor-write-value-invalid");
				OutErrorMessage = SetError;
				return false;
			}
			return true;
		}

		bool ReadAfter(
			const TSharedPtr<FJsonValue>& Requested,
			TSharedPtr<FJsonValue>& OutValue,
			FString& OutErrorCode,
			FString& OutErrorMessage) override
		{
			FString AfterPath;
			if (!ReadAssetReferencePath(Property, ValueAddress, AfterPath))
			{
				OutErrorCode = TEXT("live-editor-write-apply-failed");
				OutErrorMessage = TEXT("The reference property could not be read back after the write.");
				return false;
			}
			if (AfterPath.IsEmpty())
			{
				OutValue = MakeShared<FJsonValueNull>();
			}
			else
			{
				OutValue = MakeShared<FJsonValueString>(AfterPath);
			}
			return true;
		}

		bool SemanticEqual(const TSharedPtr<FJsonValue>& Left, const TSharedPtr<FJsonValue>& Right) override
		{
			return UEAgentKit::StructuredPropertyJson::JsonEqual(Left, Right);
		}

		void NotifyChanged() override
		{
			NotifyPropertyChanged();
		}

		void NotifyRestored() override
		{
			NotifyPropertyChanged();
		}

	private:
		void NotifyPropertyChanged()
		{
			FPropertyChangedEvent ChangedEvent(Property, EPropertyChangeType::ValueSet);
			Asset->PostEditChangeProperty(ChangedEvent);
		}

		UObject* Asset = nullptr;
		FProperty* Property = nullptr;
		void* ValueAddress = nullptr;
		UEAgentKitLiveWrite::FLiveWriteSnapshot Snapshot;
	};

	class FLiveWriteStructuredIO final : public UEAgentKitLiveWrite::ILiveWriteValueIO
	{
	public:
		FLiveWriteStructuredIO(UObject* InAsset, FProperty* InProperty, void* InValueAddress)
			: Asset(InAsset)
			, Property(InProperty)
			, ValueAddress(InValueAddress)
		{
		}

		bool CaptureSnapshot() override
		{
			return Snapshot.Capture(Property, ValueAddress);
		}

		bool IsSnapshotValid() const override
		{
			return Snapshot.IsValid();
		}

		void RestoreSnapshot() override
		{
			Snapshot.Restore(ValueAddress);
		}

		void ReleaseSnapshot() override
		{
			Snapshot.Reset();
		}

		bool ReadBefore(
			TSharedPtr<FJsonValue>& OutValue,
			FString& OutErrorCode,
			FString& OutErrorMessage) override
		{
			FString Error;
			if (!UEAgentKit::StructuredPropertyJson::ExportValue(Property, ValueAddress, OutValue, Error))
			{
				OutErrorCode = TEXT("live-editor-write-apply-failed");
				OutErrorMessage = TEXT("Structured property could not be exported before the write: ") + Error;
				return false;
			}
			return true;
		}

		bool ApplyValue(
			const TSharedPtr<FJsonValue>& Value,
			FString& OutErrorCode,
			FString& OutErrorMessage) override
		{
			FString Error;
			if (!UEAgentKit::StructuredPropertyJson::ImportValue(Property, ValueAddress, Value, Error))
			{
				OutErrorCode = TEXT("live-editor-write-value-invalid");
				OutErrorMessage = Error;
				return false;
			}
			return true;
		}

		bool ReadAfter(
			const TSharedPtr<FJsonValue>& Requested,
			TSharedPtr<FJsonValue>& OutValue,
			FString& OutErrorCode,
			FString& OutErrorMessage) override
		{
			FString Error;
			if (!UEAgentKit::StructuredPropertyJson::ExportValue(Property, ValueAddress, OutValue, Error)
				|| !UEAgentKit::StructuredPropertyJson::JsonEqual(OutValue, Requested))
			{
				OutErrorCode = TEXT("live-editor-write-apply-failed");
				OutErrorMessage = TEXT("Structured property read-back verification failed: ") + Error;
				return false;
			}
			return true;
		}

		bool SemanticEqual(const TSharedPtr<FJsonValue>& Left, const TSharedPtr<FJsonValue>& Right) override
		{
			return UEAgentKit::StructuredPropertyJson::JsonEqual(Left, Right);
		}

		void NotifyChanged() override
		{
			NotifyPropertyChanged();
		}

		void NotifyRestored() override
		{
			NotifyPropertyChanged();
		}

	private:
		void NotifyPropertyChanged()
		{
			FPropertyChangedEvent ChangedEvent(Property, EPropertyChangeType::ValueSet);
			Asset->PostEditChangeProperty(ChangedEvent);
		}

		UObject* Asset = nullptr;
		FProperty* Property = nullptr;
		void* ValueAddress = nullptr;
		UEAgentKitLiveWrite::FLiveWriteSnapshot Snapshot;
	};

	enum class ELiveMaterialParameterKind : uint8
	{
		Scalar,
		Vector,
		Texture,
		StaticSwitch
	};

	FString LiveMaterialParameterTypeName(const ELiveMaterialParameterKind Kind)
	{
		switch (Kind)
		{
		case ELiveMaterialParameterKind::Scalar:
			return TEXT("Scalar");
		case ELiveMaterialParameterKind::Vector:
			return TEXT("Vector");
		case ELiveMaterialParameterKind::Texture:
			return TEXT("Texture");
		default:
			return TEXT("StaticSwitch");
		}
	}

	// Mirrors the offline AssetPatchCommandlet helpers so the Live Editor Apply reads,
	// applies, and verifies Material Instance parameters exactly like the patch path.
	bool FindLiveGlobalScalarParameter(
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

	bool FindLiveGlobalVectorParameter(
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

	bool FindLiveGlobalTextureParameter(
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

	bool FindLiveGlobalStaticSwitchParameter(
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

	bool ReadLiveStaticSwitchParameter(
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

		const TArray<FStaticSwitchParameter> StaticParameters = Instance->GetStaticParameters().StaticSwitchParameters;
		int32 MatchCount = 0;
		for (const FStaticSwitchParameter& Parameter : StaticParameters)
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
	bool ReadLiveMaterialParameterMetadata(
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

	bool ReadLiveScalarParameter(
		UMaterialInstanceConstant* Instance,
		const FMaterialParameterInfo& ParameterInfo,
		const FGuid& DefaultExpressionGuid,
		float& OutValue,
		bool& OutOverride,
		FGuid& OutExpressionGuid)
	{
		return Instance->GetScalarParameterValue(FHashedMaterialParameterInfo(ParameterInfo), OutValue)
			&& ReadLiveMaterialParameterMetadata(
				Instance->ScalarParameterValues,
				ParameterInfo,
				DefaultExpressionGuid,
				OutOverride,
				OutExpressionGuid);
	}

	bool ReadLiveVectorParameter(
		UMaterialInstanceConstant* Instance,
		const FMaterialParameterInfo& ParameterInfo,
		const FGuid& DefaultExpressionGuid,
		FLinearColor& OutValue,
		bool& OutOverride,
		FGuid& OutExpressionGuid)
	{
		return Instance->GetVectorParameterValue(FHashedMaterialParameterInfo(ParameterInfo), OutValue)
			&& ReadLiveMaterialParameterMetadata(
				Instance->VectorParameterValues,
				ParameterInfo,
				DefaultExpressionGuid,
				OutOverride,
				OutExpressionGuid);
	}

	bool ReadLiveTextureParameter(
		UMaterialInstanceConstant* Instance,
		const FMaterialParameterInfo& ParameterInfo,
		const FGuid& DefaultExpressionGuid,
		UTexture*& OutValue,
		bool& OutOverride,
		FGuid& OutExpressionGuid)
	{
		return Instance->GetTextureParameterValue(FHashedMaterialParameterInfo(ParameterInfo), OutValue)
			&& ReadLiveMaterialParameterMetadata(
				Instance->TextureParameterValues,
				ParameterInfo,
				DefaultExpressionGuid,
				OutOverride,
				OutExpressionGuid);
	}

	TSharedRef<FJsonObject> MakeLiveMaterialVectorValue(const FLinearColor& Value)
	{
		const TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
		Result->SetNumberField(TEXT("r"), Value.R);
		Result->SetNumberField(TEXT("g"), Value.G);
		Result->SetNumberField(TEXT("b"), Value.B);
		Result->SetNumberField(TEXT("a"), Value.A);
		return Result;
	}

	TSharedPtr<FJsonValue> MakeLiveMaterialTextureValue(const UTexture* Value)
	{
		if (Value != nullptr)
		{
			return MakeShared<FJsonValueString>(Value->GetPathName());
		}
		return MakeShared<FJsonValueNull>();
	}

	struct FLiveMaterialSnapshotState
	{
		bool bEntryPresent = false;
		float ScalarValue = 0.0f;
		FLinearColor VectorValue;
		TObjectPtr<UTexture> TextureValue = nullptr;
		bool bSwitchValue = false;
		bool bSwitchOverride = false;
		FGuid SwitchExpressionGuid;
	};

	class FLiveWriteMaterialIO final : public UEAgentKitLiveWrite::ILiveWriteValueIO
	{
	public:
		FLiveWriteMaterialIO(
			UMaterialInstanceConstant* InInstance,
			FName InParameterName,
			ELiveMaterialParameterKind InKind,
			FMaterialParameterInfo InParameterInfo,
			FGuid InExpressionGuid)
			: Instance(InInstance)
			, ParameterName(InParameterName)
			, Kind(InKind)
			, ParameterInfo(InParameterInfo)
			, ExpressionGuid(InExpressionGuid)
		{
		}

		bool CaptureSnapshot() override
		{
			FLiveMaterialSnapshotState State;
			if (Kind == ELiveMaterialParameterKind::Scalar)
			{
				const int32 Index = Instance->ScalarParameterValues.IndexOfByPredicate(
					[&](const FScalarParameterValue& Parameter)
					{
						return Parameter.ParameterInfo == ParameterInfo;
					});
				State.bEntryPresent = Index != INDEX_NONE;
				if (Index != INDEX_NONE)
				{
					State.ScalarValue = Instance->ScalarParameterValues[Index].ParameterValue;
				}
			}
			else if (Kind == ELiveMaterialParameterKind::Vector)
			{
				const int32 Index = Instance->VectorParameterValues.IndexOfByPredicate(
					[&](const FVectorParameterValue& Parameter)
					{
						return Parameter.ParameterInfo == ParameterInfo;
					});
				State.bEntryPresent = Index != INDEX_NONE;
				if (Index != INDEX_NONE)
				{
					State.VectorValue = Instance->VectorParameterValues[Index].ParameterValue;
				}
			}
			else if (Kind == ELiveMaterialParameterKind::Texture)
			{
				const int32 Index = Instance->TextureParameterValues.IndexOfByPredicate(
					[&](const FTextureParameterValue& Parameter)
					{
						return Parameter.ParameterInfo == ParameterInfo;
					});
				State.bEntryPresent = Index != INDEX_NONE;
				if (Index != INDEX_NONE)
				{
					State.TextureValue = Instance->TextureParameterValues[Index].ParameterValue;
				}
			}
			else
			{
				const TArray<FStaticSwitchParameter> SwitchParameters = Instance->GetStaticParameters().StaticSwitchParameters;
				const int32 Index = SwitchParameters.IndexOfByPredicate(
					[&](const FStaticSwitchParameter& Parameter)
					{
						return Parameter.ParameterInfo == ParameterInfo;
					});
				State.bEntryPresent = Index != INDEX_NONE;
				if (Index != INDEX_NONE)
				{
					State.bSwitchValue = SwitchParameters[Index].Value;
					State.bSwitchOverride = SwitchParameters[Index].bOverride;
					State.SwitchExpressionGuid = SwitchParameters[Index].ExpressionGUID;
				}
			}
			SnapshotState = State;
			bSnapshotValid = true;
			return true;
		}

		bool IsSnapshotValid() const override
		{
			return bSnapshotValid;
		}

		void RestoreSnapshot() override
		{
			if (!bSnapshotValid)
			{
				return;
			}
			if (Kind == ELiveMaterialParameterKind::Scalar)
			{
				if (SnapshotState.bEntryPresent)
				{
					UMaterialEditingLibrary::SetMaterialInstanceScalarParameterValue(
						Instance,
						ParameterName,
						SnapshotState.ScalarValue,
						EMaterialParameterAssociation::GlobalParameter);
				}
				else
				{
					Instance->ScalarParameterValues.RemoveAll(
						[&](const FScalarParameterValue& Parameter)
						{
							return Parameter.ParameterInfo == ParameterInfo;
						});
				}
			}
			else if (Kind == ELiveMaterialParameterKind::Vector)
			{
				if (SnapshotState.bEntryPresent)
				{
					UMaterialEditingLibrary::SetMaterialInstanceVectorParameterValue(
						Instance,
						ParameterName,
						SnapshotState.VectorValue,
						EMaterialParameterAssociation::GlobalParameter);
				}
				else
				{
					Instance->VectorParameterValues.RemoveAll(
						[&](const FVectorParameterValue& Parameter)
						{
							return Parameter.ParameterInfo == ParameterInfo;
						});
				}
			}
			else if (Kind == ELiveMaterialParameterKind::Texture)
			{
				if (SnapshotState.bEntryPresent)
				{
					UMaterialEditingLibrary::SetMaterialInstanceTextureParameterValue(
						Instance,
						ParameterName,
						SnapshotState.TextureValue,
						EMaterialParameterAssociation::GlobalParameter);
				}
				else
				{
					Instance->TextureParameterValues.RemoveAll(
						[&](const FTextureParameterValue& Parameter)
						{
							return Parameter.ParameterInfo == ParameterInfo;
						});
				}
			}
			else
			{
				FMaterialInstanceParameterUpdateContext UpdateContext(Instance);
				FStaticParameterSet& StaticParameters = UpdateContext.GetStaticParameters();
				const int32 Index = StaticParameters.StaticSwitchParameters.IndexOfByPredicate(
					[&](const FStaticSwitchParameter& Parameter)
					{
						return Parameter.ParameterInfo == ParameterInfo;
					});
				if (SnapshotState.bEntryPresent)
				{
					if (Index != INDEX_NONE)
					{
						FStaticSwitchParameter& Entry = StaticParameters.StaticSwitchParameters[Index];
						Entry.Value = SnapshotState.bSwitchValue;
						Entry.bOverride = SnapshotState.bSwitchOverride;
						Entry.ExpressionGUID = SnapshotState.SwitchExpressionGuid;
					}
				}
				else if (Index != INDEX_NONE)
				{
					StaticParameters.StaticSwitchParameters.RemoveAt(Index);
				}
			}
		}

		void ReleaseSnapshot() override
		{
			bSnapshotValid = false;
		}

		bool ReadBefore(
			TSharedPtr<FJsonValue>& OutValue,
			FString& OutErrorCode,
			FString& OutErrorMessage) override
		{
			if (Kind == ELiveMaterialParameterKind::Scalar)
			{
				float Value = 0.0f;
				bool bOverride = false;
				FGuid ValueExpressionGuid;
				if (!ReadLiveScalarParameter(
						Instance,
						ParameterInfo,
						ExpressionGuid,
						Value,
						bOverride,
						ValueExpressionGuid))
				{
					OutErrorCode = TEXT("live-editor-write-material-apply-failed");
					OutErrorMessage = TEXT("The material scalar parameter could not be read before the write.");
					return false;
				}
				OutValue = MakeShared<FJsonValueNumber>(Value);
				return true;
			}
			if (Kind == ELiveMaterialParameterKind::Vector)
			{
				FLinearColor Value;
				bool bOverride = false;
				FGuid ValueExpressionGuid;
				if (!ReadLiveVectorParameter(
						Instance,
						ParameterInfo,
						ExpressionGuid,
						Value,
						bOverride,
						ValueExpressionGuid))
				{
					OutErrorCode = TEXT("live-editor-write-material-apply-failed");
					OutErrorMessage = TEXT("The material vector parameter could not be read before the write.");
					return false;
				}
				OutValue = MakeShared<FJsonValueObject>(MakeLiveMaterialVectorValue(Value));
				return true;
			}
			if (Kind == ELiveMaterialParameterKind::Texture)
			{
				UTexture* Value = nullptr;
				bool bOverride = false;
				FGuid ValueExpressionGuid;
				if (!ReadLiveTextureParameter(
						Instance,
						ParameterInfo,
						ExpressionGuid,
						Value,
						bOverride,
						ValueExpressionGuid))
				{
					OutErrorCode = TEXT("live-editor-write-material-apply-failed");
					OutErrorMessage = TEXT("The material texture parameter could not be read before the write.");
					return false;
				}
				OutValue = MakeLiveMaterialTextureValue(Value);
				return true;
			}
			bool bSwitchValue = false;
			FGuid SwitchExpressionGuid;
			bool bOverride = false;
			if (!ReadLiveStaticSwitchParameter(
					Instance,
					ParameterInfo,
					bSwitchValue,
					SwitchExpressionGuid,
					bOverride))
			{
				OutErrorCode = TEXT("live-editor-write-material-apply-failed");
				OutErrorMessage = TEXT("The material static switch parameter could not be read before the write.");
				return false;
			}
			OutValue = MakeShared<FJsonValueBoolean>(bSwitchValue);
			return true;
		}

		bool ApplyValue(
			const TSharedPtr<FJsonValue>& Value,
			FString& OutErrorCode,
			FString& OutErrorMessage) override
		{
			if (Kind == ELiveMaterialParameterKind::Scalar)
			{
				double Number = 0.0;
				if (!Value.IsValid()
					|| !Value->TryGetNumber(Number)
					|| !FMath::IsFinite(Number)
					|| FMath::Abs(Number) > static_cast<double>(FLT_MAX))
				{
					OutErrorCode = TEXT("live-editor-write-material-value-invalid");
					OutErrorMessage = TEXT("Material scalar parameters require a finite JSON number.");
					return false;
				}
				UMaterialEditingLibrary::SetMaterialInstanceScalarParameterValue(
					Instance,
					ParameterName,
					static_cast<float>(Number),
					EMaterialParameterAssociation::GlobalParameter);
				return true;
			}
			if (Kind == ELiveMaterialParameterKind::Vector)
			{
				double R = 0.0;
				double G = 0.0;
				double B = 0.0;
				double A = 0.0;
				const TSharedPtr<FJsonObject> Color = Value.IsValid() ? Value->AsObject() : nullptr;
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
					OutErrorCode = TEXT("live-editor-write-material-value-invalid");
					OutErrorMessage = TEXT("Material vector parameters require finite r, g, b, and a floats.");
					return false;
				}
				UMaterialEditingLibrary::SetMaterialInstanceVectorParameterValue(
					Instance,
					ParameterName,
					FLinearColor(
						static_cast<float>(R),
						static_cast<float>(G),
						static_cast<float>(B),
						static_cast<float>(A)),
					EMaterialParameterAssociation::GlobalParameter);
				return true;
			}
			if (Kind == ELiveMaterialParameterKind::Texture)
			{
				FString TexturePath;
				if (!Value.IsValid() || !Value->TryGetString(TexturePath) || TexturePath.IsEmpty())
				{
					OutErrorCode = TEXT("live-editor-write-material-value-invalid");
					OutErrorMessage = TEXT("Material texture parameters require an object path string.");
					return false;
				}
				UTexture* Texture = LoadObject<UTexture>(nullptr, *TexturePath);
				if (Texture == nullptr)
				{
					OutErrorCode = TEXT("live-editor-write-material-texture-invalid");
					OutErrorMessage = FString::Printf(
						TEXT("The material texture asset could not be loaded: %s"),
						*TexturePath);
					return false;
				}
				UMaterialEditingLibrary::SetMaterialInstanceTextureParameterValue(
					Instance,
					ParameterName,
					Texture,
					EMaterialParameterAssociation::GlobalParameter);
				return true;
			}
			bool bSwitchValue = false;
			if (!Value.IsValid() || !Value->TryGetBool(bSwitchValue))
			{
				OutErrorCode = TEXT("live-editor-write-material-value-invalid");
				OutErrorMessage = TEXT("Material static switch parameters require a JSON boolean.");
				return false;
			}
			UMaterialEditingLibrary::SetMaterialInstanceStaticSwitchParameterValue(
				Instance,
				ParameterName,
				bSwitchValue,
				EMaterialParameterAssociation::GlobalParameter,
				true);
			return true;
		}

		bool ReadAfter(
			const TSharedPtr<FJsonValue>& Requested,
			TSharedPtr<FJsonValue>& OutValue,
			FString& OutErrorCode,
			FString& OutErrorMessage) override
		{
			if (Kind == ELiveMaterialParameterKind::Scalar)
			{
				double RequestedNumber = 0.0;
				float AfterValue = 0.0f;
				bool bAfterOverride = false;
				FGuid AfterExpressionGuid;
				if (!Requested.IsValid()
					|| !Requested->TryGetNumber(RequestedNumber)
					|| !ReadLiveScalarParameter(
						Instance,
						ParameterInfo,
						ExpressionGuid,
						AfterValue,
						bAfterOverride,
						AfterExpressionGuid)
					|| !FMath::IsNearlyEqual(AfterValue, static_cast<float>(RequestedNumber), UE_SMALL_NUMBER)
					|| !bAfterOverride
					|| AfterExpressionGuid != ExpressionGuid)
				{
					OutErrorCode = TEXT("live-editor-write-material-apply-failed");
					OutErrorMessage = TEXT("Material scalar parameter read-back verification failed.");
					return false;
				}
				OutValue = MakeShared<FJsonValueNumber>(AfterValue);
				return true;
			}
			if (Kind == ELiveMaterialParameterKind::Vector)
			{
				const TSharedPtr<FJsonObject> RequestedColor = Requested.IsValid() ? Requested->AsObject() : nullptr;
				FLinearColor AfterValue;
				bool bAfterOverride = false;
				FGuid AfterExpressionGuid;
				if (!RequestedColor.IsValid()
					|| !ReadLiveVectorParameter(
						Instance,
						ParameterInfo,
						ExpressionGuid,
						AfterValue,
						bAfterOverride,
						AfterExpressionGuid)
					|| !AfterValue.Equals(
						FLinearColor(
							static_cast<float>(RequestedColor->GetNumberField(TEXT("r"))),
							static_cast<float>(RequestedColor->GetNumberField(TEXT("g"))),
							static_cast<float>(RequestedColor->GetNumberField(TEXT("b"))),
							static_cast<float>(RequestedColor->GetNumberField(TEXT("a")))),
						UE_SMALL_NUMBER)
					|| !bAfterOverride
					|| AfterExpressionGuid != ExpressionGuid)
				{
					OutErrorCode = TEXT("live-editor-write-material-apply-failed");
					OutErrorMessage = TEXT("Material vector parameter read-back verification failed.");
					return false;
				}
				OutValue = MakeShared<FJsonValueObject>(MakeLiveMaterialVectorValue(AfterValue));
				return true;
			}
			if (Kind == ELiveMaterialParameterKind::Texture)
			{
				FString RequestedPath;
				UTexture* AfterValue = nullptr;
				bool bAfterOverride = false;
				FGuid AfterExpressionGuid;
				UTexture* RequestedTexture = Requested.IsValid()
					? LoadObject<UTexture>(nullptr, *Requested->AsString())
					: nullptr;
				if (!Requested.IsValid()
					|| !Requested->TryGetString(RequestedPath)
					|| RequestedTexture == nullptr
					|| !ReadLiveTextureParameter(
						Instance,
						ParameterInfo,
						ExpressionGuid,
						AfterValue,
						bAfterOverride,
						AfterExpressionGuid)
					|| AfterValue != RequestedTexture
					|| !bAfterOverride
					|| AfterExpressionGuid != ExpressionGuid)
				{
					OutErrorCode = TEXT("live-editor-write-material-apply-failed");
					OutErrorMessage = TEXT("Material texture parameter read-back verification failed.");
					return false;
				}
				OutValue = MakeLiveMaterialTextureValue(AfterValue);
				return true;
			}
			bool bRequestedSwitch = false;
			bool bAfterSwitch = false;
			FGuid AfterExpressionGuid;
			bool bAfterOverride = false;
			FGuid BeforeSwitchGuid = SnapshotState.bEntryPresent
				? SnapshotState.SwitchExpressionGuid
				: ExpressionGuid;
			if (!Requested.IsValid()
				|| !Requested->TryGetBool(bRequestedSwitch)
				|| !ReadLiveStaticSwitchParameter(
					Instance,
					ParameterInfo,
					bAfterSwitch,
					AfterExpressionGuid,
					bAfterOverride)
				|| bAfterSwitch != bRequestedSwitch
				|| AfterExpressionGuid != BeforeSwitchGuid
				|| !bAfterOverride)
			{
				OutErrorCode = TEXT("live-editor-write-material-apply-failed");
				OutErrorMessage = TEXT("Material static switch parameter read-back verification failed.");
				return false;
			}
			OutValue = MakeShared<FJsonValueBoolean>(bAfterSwitch);
			return true;
		}

		bool SemanticEqual(const TSharedPtr<FJsonValue>& Left, const TSharedPtr<FJsonValue>& Right) override
		{
			return UEAgentKit::StructuredPropertyJson::JsonEqual(Left, Right);
		}

		void NotifyChanged() override
		{
		}

		void NotifyRestored() override
		{
			UMaterialEditingLibrary::UpdateMaterialInstance(Instance);
		}

	private:
		UMaterialInstanceConstant* Instance = nullptr;
		FName ParameterName;
		ELiveMaterialParameterKind Kind = ELiveMaterialParameterKind::Scalar;
		FMaterialParameterInfo ParameterInfo;
		FGuid ExpressionGuid;
		FLiveMaterialSnapshotState SnapshotState;
		bool bSnapshotValid = false;
	};

	bool TryApplyScalarPropertyLive(
		UObject* Asset,
		UPackage* Package,
		const FString& AssetPath,
		const FString& PropertyPath,
		FProperty* Property,
		void* ValueAddress,
		const TSharedPtr<FJsonValue>& Value,
		const FString& SessionId,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		FLiveWriteScalarIO IO(Asset, Property, ValueAddress);
		UEAgentKitLiveWrite::FLiveWriteContext Context;
		Context.Asset = Asset;
		Context.Package = Package;
		Context.SessionId = SessionId;
		Context.TransactionTitle = TEXT("UE Agent Kit: Set Asset Property");
		Context.AssetPath = AssetPath;
		Context.PropertyPath = PropertyPath;
		Context.Value = Value;

		UEAgentKitLiveWrite::FLiveWriteEvidence Evidence;
		if (!UEAgentKitLiveWrite::RunLiveWriteTransaction(Context, IO, Evidence, OutErrorCode, OutErrorMessage))
		{
			return false;
		}

		TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
		UEAgentKitLiveWrite::FillLiveWriteEvidence(
			Result,
			Context,
			Evidence,
			TEXT("setAssetProperty"),
			TEXT("scalar"),
			Property->GetClass()->GetName(),
			Evidence.bChanged,
			false);
		OutResult = Result;
		return true;
	}

	bool TryApplyReferencePropertyLive(
		UObject* Asset,
		UPackage* Package,
		const FString& AssetPath,
		const FString& PropertyPath,
		FProperty* Property,
		void* ValueAddress,
		const TSharedPtr<FJsonValue>& Value,
		const FString& SessionId,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		UClass* ConstraintClass = nullptr;
		const EAssetReferenceType ReferenceType = GetAssetReferenceType(Property, ConstraintClass);
		if (ReferenceType == EAssetReferenceType::Invalid || ConstraintClass == nullptr)
		{
			OutErrorCode = TEXT("live-editor-write-property-type-unsupported");
			OutErrorMessage = TEXT("setAssetReferenceProperty requires an Object, Class, SoftObject, or SoftClass reference property.");
			return false;
		}
		const FString ReferenceTypeName = AssetReferenceTypeName(ReferenceType);
		if (!Value.IsValid())
		{
			OutErrorCode = TEXT("live-editor-write-value-invalid");
			OutErrorMessage = TEXT("Asset reference value is required; use JSON null to clear the reference.");
			return false;
		}

		FLiveWriteReferenceIO IO(Asset, Property, ValueAddress);
		UEAgentKitLiveWrite::FLiveWriteContext Context;
		Context.Asset = Asset;
		Context.Package = Package;
		Context.SessionId = SessionId;
		Context.TransactionTitle = TEXT("UE Agent Kit: Set Asset Reference Property");
		Context.AssetPath = AssetPath;
		Context.PropertyPath = PropertyPath;
		Context.Value = Value;

		UEAgentKitLiveWrite::FLiveWriteEvidence Evidence;
		if (!UEAgentKitLiveWrite::RunLiveWriteTransaction(Context, IO, Evidence, OutErrorCode, OutErrorMessage))
		{
			return false;
		}

		TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
		UEAgentKitLiveWrite::FillLiveWriteEvidence(
			Result,
			Context,
			Evidence,
			TEXT("setAssetReferenceProperty"),
			TEXT("reference"),
			Property->GetClass()->GetName(),
			true,
			true);
		Result->SetStringField(TEXT("referenceType"), ReferenceTypeName);
		Result->SetStringField(TEXT("referenceConstraintClass"), ConstraintClass->GetPathName());
		Result->SetField(TEXT("referencePath"), Evidence.AfterValue);
		Result->SetStringField(TEXT("resolvedReferenceClass"), Evidence.bChanged ? IO.ResolvedClassPath : FString());
		OutResult = Result;
		return true;
	}

	bool TryApplyStructuredPropertyLive(
		UObject* Asset,
		UPackage* Package,
		const FString& AssetPath,
		const FString& PropertyPath,
		FProperty* Property,
		void* ValueAddress,
		const TSharedPtr<FJsonValue>& Value,
		const FString& SessionId,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		const UEAgentKit::StructuredPropertyJson::EKind StructuredKind =
			UEAgentKit::StructuredPropertyJson::GetKind(Property);
		if (StructuredKind == UEAgentKit::StructuredPropertyJson::EKind::Invalid)
		{
			OutErrorCode = TEXT("live-editor-write-property-type-unsupported");
			OutErrorMessage = TEXT("setAssetStructuredProperty requires a Struct, Array, Set, or Map property.");
			return false;
		}
		if (!Value.IsValid())
		{
			OutErrorCode = TEXT("live-editor-write-value-invalid");
			OutErrorMessage = TEXT("Structured value is required.");
			return false;
		}
		FString Error;
		TSharedPtr<FJsonValue> StructuredSchema;
		if (!UEAgentKit::StructuredPropertyJson::BuildSchema(Property, StructuredSchema, Error))
		{
			OutErrorCode = TEXT("live-editor-write-property-type-unsupported");
			OutErrorMessage = TEXT("Structured property is unsupported: ") + Error;
			return false;
		}

		FLiveWriteStructuredIO IO(Asset, Property, ValueAddress);
		UEAgentKitLiveWrite::FLiveWriteContext Context;
		Context.Asset = Asset;
		Context.Package = Package;
		Context.SessionId = SessionId;
		Context.TransactionTitle = TEXT("UE Agent Kit: Set Asset Structured Property");
		Context.AssetPath = AssetPath;
		Context.PropertyPath = PropertyPath;
		Context.Value = Value;

		UEAgentKitLiveWrite::FLiveWriteEvidence Evidence;
		if (!UEAgentKitLiveWrite::RunLiveWriteTransaction(Context, IO, Evidence, OutErrorCode, OutErrorMessage))
		{
			return false;
		}

		TArray<TSharedPtr<FJsonValue>> StructuredDiff;
		bool bStructuredDiffTruncated = false;
		if (Evidence.bChanged)
		{
			UEAgentKit::StructuredPropertyJson::BuildDiff(
				Evidence.BeforeValue,
				Evidence.AfterValue,
				StructuredDiff,
				bStructuredDiffTruncated);
		}

		TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
		UEAgentKitLiveWrite::FillLiveWriteEvidence(
			Result,
			Context,
			Evidence,
			TEXT("setAssetStructuredProperty"),
			TEXT("structured"),
			Property->GetClass()->GetName(),
			true,
			true);
		Result->SetStringField(
			TEXT("structuredKind"),
			UEAgentKit::StructuredPropertyJson::KindName(StructuredKind));
		Result->SetField(TEXT("structuredSchema"), StructuredSchema);
		Result->SetArrayField(TEXT("diff"), StructuredDiff);
		Result->SetBoolField(TEXT("diffTruncated"), bStructuredDiffTruncated);
		OutResult = Result;
		return true;
	}

	bool TryApplyMaterialParameterLive(
		UObject* Asset,
		UPackage* Package,
		const FString& AssetPath,
		const FString& ParameterName,
		const TSharedPtr<FJsonValue>& Value,
		const FString& SessionId,
		const ELiveMaterialParameterKind Kind,
		const FString& Operation,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		UMaterialInstanceConstant* MaterialInstance = Cast<UMaterialInstanceConstant>(Asset);
		if (MaterialInstance == nullptr)
		{
			OutErrorCode = TEXT("live-editor-write-material-instance-required");
			OutErrorMessage = TEXT("Material parameter writes require a loaded MaterialInstanceConstant asset.");
			return false;
		}
		if (ParameterName.IsEmpty() || ParameterName.Len() > 256 || ParameterName.Contains(TEXT(".")))
		{
			OutErrorCode = TEXT("live-editor-write-material-parameter-invalid");
			OutErrorMessage = TEXT("parameterName must be a non-empty name without dots.");
			return false;
		}
		if (!Value.IsValid())
		{
			OutErrorCode = TEXT("live-editor-write-material-value-invalid");
			OutErrorMessage = TEXT("Material parameter value is required.");
			return false;
		}

		const FName ParameterFName = FName(*ParameterName);
		FMaterialParameterInfo ParameterInfo;
		FGuid ParameterExpressionGuid;
		FString ResolveError;
		bool bResolved = false;
		switch (Kind)
		{
		case ELiveMaterialParameterKind::Scalar:
			bResolved = FindLiveGlobalScalarParameter(
				MaterialInstance,
				ParameterFName,
				ParameterInfo,
				ParameterExpressionGuid,
				ResolveError);
			break;
		case ELiveMaterialParameterKind::Vector:
			bResolved = FindLiveGlobalVectorParameter(
				MaterialInstance,
				ParameterFName,
				ParameterInfo,
				ParameterExpressionGuid,
				ResolveError);
			break;
		case ELiveMaterialParameterKind::Texture:
			bResolved = FindLiveGlobalTextureParameter(
				MaterialInstance,
				ParameterFName,
				ParameterInfo,
				ParameterExpressionGuid,
				ResolveError);
			break;
		default:
			bResolved = FindLiveGlobalStaticSwitchParameter(
				MaterialInstance,
				ParameterFName,
				ParameterInfo,
				ResolveError);
			break;
		}
		if (!bResolved)
		{
			OutErrorCode = TEXT("live-editor-write-material-parameter-not-found");
			OutErrorMessage = TEXT("Material parameter was not found on the loaded asset: ") + ResolveError;
			return false;
		}

		FLiveWriteMaterialIO IO(MaterialInstance, ParameterFName, Kind, ParameterInfo, ParameterExpressionGuid);
		UEAgentKitLiveWrite::FLiveWriteContext Context;
		Context.Asset = Asset;
		Context.Package = Package;
		Context.SessionId = SessionId;
		Context.TransactionTitle = TEXT("UE Agent Kit: Set Material Instance Parameter");
		Context.AssetPath = AssetPath;
		Context.PropertyPath = ParameterName;
		Context.Value = Value;

		UEAgentKitLiveWrite::FLiveWriteEvidence Evidence;
		if (!UEAgentKitLiveWrite::RunLiveWriteTransaction(Context, IO, Evidence, OutErrorCode, OutErrorMessage))
		{
			return false;
		}

		const FString ParameterType = LiveMaterialParameterTypeName(Kind);
		FString ValueKind;
		switch (Kind)
		{
		case ELiveMaterialParameterKind::Scalar:
			ValueKind = TEXT("material-scalar");
			break;
		case ELiveMaterialParameterKind::Vector:
			ValueKind = TEXT("material-vector");
			break;
		case ELiveMaterialParameterKind::Texture:
			ValueKind = TEXT("material-texture");
			break;
		default:
			ValueKind = TEXT("material-static-switch");
			break;
		}
		TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
		UEAgentKitLiveWrite::FillLiveWriteEvidence(
			Result,
			Context,
			Evidence,
			Operation,
			ValueKind,
			TEXT("MaterialInstanceParameter"),
			true,
			true);
		Result->RemoveField(TEXT("propertyPath"));
		Result->SetStringField(TEXT("parameterName"), ParameterName);
		Result->SetStringField(TEXT("parameterType"), ParameterType);
		Result->SetStringField(TEXT("parameterAssociation"), TEXT("Global"));
		OutResult = Result;
		return true;
	}
}
bool FUEAgentKitEditorBridge::TryApplyAssetPropertyLiveResult(
	const FString& Operation,
	const FString& AssetPath,
	const FString& PropertyPath,
	const FString& ParameterName,
	const TSharedPtr<FJsonValue>& Value,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage) const
{
	if (GEditor == nullptr)
	{
		OutErrorCode = TEXT("live-editor-unavailable");
		OutErrorMessage = TEXT("The Unreal Editor is unavailable.");
		return false;
	}
	if (GEditor->PlayWorld != nullptr)
	{
		OutErrorCode = TEXT("live-editor-pie-active");
		OutErrorMessage = TEXT("Live writes are unavailable while PIE or SIE is active.");
		return false;
	}
	const bool bMaterialOperation =
		Operation.Equals(TEXT("setMaterialInstanceScalarParameter"), ESearchCase::CaseSensitive)
		|| Operation.Equals(TEXT("setMaterialInstanceVectorParameter"), ESearchCase::CaseSensitive)
		|| Operation.Equals(TEXT("setMaterialInstanceTextureParameter"), ESearchCase::CaseSensitive)
		|| Operation.Equals(TEXT("setMaterialInstanceStaticSwitchParameter"), ESearchCase::CaseSensitive);
	const bool bPropertyOperation =
		Operation.Equals(TEXT("setAssetProperty"), ESearchCase::CaseSensitive)
		|| Operation.Equals(TEXT("setAssetReferenceProperty"), ESearchCase::CaseSensitive)
		|| Operation.Equals(TEXT("setAssetStructuredProperty"), ESearchCase::CaseSensitive);
	if (!bMaterialOperation && !bPropertyOperation)
	{
		OutErrorCode = TEXT("live-editor-write-operation-unsupported");
		OutErrorMessage = TEXT("Live Editor writes accept only setAssetProperty, setAssetReferenceProperty, setAssetStructuredProperty, setMaterialInstanceScalarParameter, setMaterialInstanceVectorParameter, setMaterialInstanceTextureParameter, and setMaterialInstanceStaticSwitchParameter operations.");
		return false;
	}
	if (!IsSafeGameAssetPath(AssetPath)
		|| (bPropertyOperation && !IsSafeTopLevelPropertyPath(PropertyPath))
		|| (bMaterialOperation && (ParameterName.IsEmpty() || ParameterName.Contains(TEXT(".")))))
	{
		OutErrorCode = TEXT("live-editor-invalid-parameters");
		OutErrorMessage = bMaterialOperation
			? TEXT("assetPath and one exact parameterName are required.")
			: TEXT("assetPath and one top-level propertyPath are required.");
		return false;
	}
	UObject* Asset = StaticFindObject(UObject::StaticClass(), nullptr, *AssetPath, false);
	if (Asset == nullptr || !Asset->IsAsset())
	{
		OutErrorCode = TEXT("live-editor-write-asset-not-loaded");
		OutErrorMessage = TEXT("Live write accepts only an already loaded exact asset.");
		return false;
	}
	if (!IsAssetOpenInEditor(Asset))
	{
		OutErrorCode = TEXT("live-editor-write-asset-not-open");
		OutErrorMessage = TEXT("Open the exact asset in the Editor before applying a live write.");
		return false;
	}
	if (Asset->IsA<UBlueprint>())
	{
		OutErrorCode = TEXT("live-editor-write-blueprint-unsupported");
		OutErrorMessage = TEXT("Live Editor writes accept only non-Blueprint assets.");
		return false;
	}
	UPackage* Package = Asset->GetOutermost();
	if (Package == nullptr || !Package->GetName().StartsWith(TEXT("/Game/")) || Package->ContainsMap())
	{
		OutErrorCode = TEXT("live-editor-write-package-invalid");
		OutErrorMessage = TEXT("Live write accepts only one non-map project Content package.");
		return false;
	}
	if (Package->IsDirty())
	{
		OutErrorCode = TEXT("live-editor-write-package-dirty");
		OutErrorMessage = TEXT("Save or revert the target package before applying an AI live write.");
		return false;
	}
	if (bMaterialOperation)
	{
		ELiveMaterialParameterKind Kind = ELiveMaterialParameterKind::Scalar;
		if (Operation.Equals(TEXT("setMaterialInstanceScalarParameter"), ESearchCase::CaseSensitive))
		{
			Kind = ELiveMaterialParameterKind::Scalar;
		}
		else if (Operation.Equals(TEXT("setMaterialInstanceVectorParameter"), ESearchCase::CaseSensitive))
		{
			Kind = ELiveMaterialParameterKind::Vector;
		}
		else if (Operation.Equals(TEXT("setMaterialInstanceTextureParameter"), ESearchCase::CaseSensitive))
		{
			Kind = ELiveMaterialParameterKind::Texture;
		}
		else
		{
			Kind = ELiveMaterialParameterKind::StaticSwitch;
		}
		return TryApplyMaterialParameterLive(
			Asset,
			Package,
			AssetPath,
			ParameterName,
			Value,
			SessionId,
			Kind,
			Operation,
			OutResult,
			OutErrorCode,
			OutErrorMessage);
	}
	FProperty* Property = FindFProperty<FProperty>(Asset->GetClass(), FName(*PropertyPath));
	if (Property == nullptr)
	{
		OutErrorCode = TEXT("live-editor-write-property-not-found");
		OutErrorMessage = TEXT("The exact top-level property was not found on the loaded asset.");
		return false;
	}
	if (!Property->HasAnyPropertyFlags(CPF_Edit)
		|| Property->HasAnyPropertyFlags(CPF_EditConst | CPF_Transient | CPF_DisableEditOnInstance))
	{
		OutErrorCode = TEXT("live-editor-write-property-not-editable");
		OutErrorMessage = TEXT("The exact property is not editable on this asset instance.");
		return false;
	}
	if (Property->ArrayDim != 1)
	{
		OutErrorCode = TEXT("live-editor-write-property-type-unsupported");
		OutErrorMessage = TEXT("Live Editor writes do not support native fixed-array properties.");
		return false;
	}
	void* ValueAddress = Property->ContainerPtrToValuePtr<void>(Asset);
	if (Operation.Equals(TEXT("setAssetProperty"), ESearchCase::CaseSensitive))
	{
		return TryApplyScalarPropertyLive(
			Asset,
			Package,
			AssetPath,
			PropertyPath,
			Property,
			ValueAddress,
			Value,
			SessionId,
			OutResult,
			OutErrorCode,
			OutErrorMessage);
	}
	if (Operation.Equals(TEXT("setAssetReferenceProperty"), ESearchCase::CaseSensitive))
	{
		return TryApplyReferencePropertyLive(
			Asset,
			Package,
			AssetPath,
			PropertyPath,
			Property,
			ValueAddress,
			Value,
			SessionId,
			OutResult,
			OutErrorCode,
			OutErrorMessage);
	}
	return TryApplyStructuredPropertyLive(
		Asset,
		Package,
		AssetPath,
		PropertyPath,
		Property,
		ValueAddress,
		Value,
		SessionId,
		OutResult,
		OutErrorCode,
		OutErrorMessage);
}
