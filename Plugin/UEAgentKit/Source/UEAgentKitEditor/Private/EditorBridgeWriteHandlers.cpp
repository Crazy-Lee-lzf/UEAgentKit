#include "EditorBridge.h"
#include "EditorBridgeHandlerUtils.h"
#include "LiveWriteTransaction.h"
#include "StructuredPropertyJson.h"

#include "Dom/JsonValue.h"
#include "Editor.h"
#include "Engine/Blueprint.h"
#include "ScopedTransaction.h"
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
		bool ReadBefore(
			FProperty* Property,
			void* ValueAddress,
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
			FProperty* Property,
			void* ValueAddress,
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
			FProperty* Property,
			void* ValueAddress,
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
	};

	class FLiveWriteReferenceIO final : public UEAgentKitLiveWrite::ILiveWriteValueIO
	{
	public:
		FString ResolvedClassPath;

		bool ReadBefore(
			FProperty* Property,
			void* ValueAddress,
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
			FProperty* Property,
			void* ValueAddress,
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
			FProperty* Property,
			void* ValueAddress,
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
	};

	class FLiveWriteStructuredIO final : public UEAgentKitLiveWrite::ILiveWriteValueIO
	{
	public:
		bool ReadBefore(
			FProperty* Property,
			void* ValueAddress,
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
			FProperty* Property,
			void* ValueAddress,
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
			FProperty* Property,
			void* ValueAddress,
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
		FLiveWriteScalarIO IO;
		UEAgentKitLiveWrite::FLiveWriteContext Context;
		Context.Asset = Asset;
		Context.Package = Package;
		Context.Property = Property;
		Context.ValueAddress = ValueAddress;
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

		FLiveWriteReferenceIO IO;
		UEAgentKitLiveWrite::FLiveWriteContext Context;
		Context.Asset = Asset;
		Context.Package = Package;
		Context.Property = Property;
		Context.ValueAddress = ValueAddress;
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

		FLiveWriteStructuredIO IO;
		UEAgentKitLiveWrite::FLiveWriteContext Context;
		Context.Asset = Asset;
		Context.Package = Package;
		Context.Property = Property;
		Context.ValueAddress = ValueAddress;
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
}
bool FUEAgentKitEditorBridge::TryApplyAssetPropertyLiveResult(
	const FString& Operation,
	const FString& AssetPath,
	const FString& PropertyPath,
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
	if (!Operation.Equals(TEXT("setAssetProperty"), ESearchCase::CaseSensitive)
		&& !Operation.Equals(TEXT("setAssetReferenceProperty"), ESearchCase::CaseSensitive)
		&& !Operation.Equals(TEXT("setAssetStructuredProperty"), ESearchCase::CaseSensitive))
	{
		OutErrorCode = TEXT("live-editor-write-operation-unsupported");
		OutErrorMessage = TEXT("Live Editor writes accept only setAssetProperty, setAssetReferenceProperty, and setAssetStructuredProperty operations.");
		return false;
	}
	if (!IsSafeGameAssetPath(AssetPath) || !IsSafeTopLevelPropertyPath(PropertyPath))
	{
		OutErrorCode = TEXT("live-editor-invalid-parameters");
		OutErrorMessage = TEXT("assetPath and one top-level propertyPath are required.");
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
