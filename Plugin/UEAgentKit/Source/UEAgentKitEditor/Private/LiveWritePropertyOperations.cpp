#include "LiveWriteOperationRegistry.h"
#include "LiveWriteOperationCommon.h"
#include "EditorBridgeHandlerUtils.h"
#include "StructuredPropertyJson.h"

#include "Dom/JsonValue.h"
#include "UObject/Package.h"
#include "UObject/SoftObjectPath.h"
#include "UObject/SoftObjectPtr.h"
#include "UObject/UObjectGlobals.h"
#include "UObject/UnrealType.h"

using namespace UEAgentKitEditorBridgePrivate;
using namespace UEAgentKitLiveWrite;

namespace
{
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

	// Captures the exact identity of the just-committed live write transaction and
	// retains the IO (with its pre-write snapshot) so the Bridge can later Undo or
	// Discard only this confirmed write. Returns null for no-op writes.
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

	bool TryApplyScalarPropertyLive(
		UObject* Asset,
		UPackage* Package,
		const FString& AssetPath,
		const FString& PropertyPath,
		FProperty* Property,
		void* ValueAddress,
		const TSharedPtr<FJsonValue>& Value,
		const FString& SessionId,
		TSharedPtr<UEAgentKitLiveWrite::FLiveWriteTransactionRecord>& OutRecord,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		TUniquePtr<UEAgentKitLiveWrite::ILiveWriteValueIO> IO = MakeUnique<FLiveWriteScalarIO>(Asset, Property, ValueAddress);
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
		OutRecord = BuildLiveWriteTransactionRecord(
			Asset,
			Package,
			AssetPath,
			TEXT("setAssetProperty"),
			TEXT("scalar"),
			SessionId,
			Evidence,
			IO);

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
		TSharedPtr<UEAgentKitLiveWrite::FLiveWriteTransactionRecord>& OutRecord,
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

		TUniquePtr<UEAgentKitLiveWrite::ILiveWriteValueIO> IO = MakeUnique<FLiveWriteReferenceIO>(Asset, Property, ValueAddress);
		FLiveWriteReferenceIO* ReferenceIO = static_cast<FLiveWriteReferenceIO*>(IO.Get());
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
		OutRecord = BuildLiveWriteTransactionRecord(
			Asset,
			Package,
			AssetPath,
			TEXT("setAssetReferenceProperty"),
			TEXT("reference"),
			SessionId,
			Evidence,
			IO);

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
		Result->SetStringField(TEXT("resolvedReferenceClass"), Evidence.bChanged ? ReferenceIO->ResolvedClassPath : FString());
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
		TSharedPtr<UEAgentKitLiveWrite::FLiveWriteTransactionRecord>& OutRecord,
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

		TUniquePtr<UEAgentKitLiveWrite::ILiveWriteValueIO> IO = MakeUnique<FLiveWriteStructuredIO>(Asset, Property, ValueAddress);
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
		OutRecord = BuildLiveWriteTransactionRecord(
			Asset,
			Package,
			AssetPath,
			TEXT("setAssetStructuredProperty"),
			TEXT("structured"),
			SessionId,
			Evidence,
			IO);

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

	bool ResolveEditableProperty(
		const FLiveWriteOperationContext& Context,
		const FLiveWriteOperationRequest& Request,
		FProperty*& OutProperty,
		void*& OutValueAddress,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		if (!IsSafeTopLevelPropertyPath(Request.PropertyPath))
		{
			OutErrorCode = TEXT("live-editor-invalid-parameters");
			OutErrorMessage = TEXT("target.propertyPath must be one exact top-level property name.");
			return false;
		}
		OutProperty = FindFProperty<FProperty>(Context.Asset->GetClass(), FName(*Request.PropertyPath));
		if (OutProperty == nullptr)
		{
			OutErrorCode = TEXT("live-editor-write-property-not-found");
			OutErrorMessage = TEXT("The exact top-level property was not found on the loaded asset.");
			return false;
		}
		if (!OutProperty->HasAnyPropertyFlags(CPF_Edit)
			|| OutProperty->HasAnyPropertyFlags(CPF_EditConst | CPF_Transient | CPF_DisableEditOnInstance))
		{
			OutErrorCode = TEXT("live-editor-write-property-not-editable");
			OutErrorMessage = TEXT("The exact property is not editable on this asset instance.");
			return false;
		}
		if (OutProperty->ArrayDim != 1)
		{
			OutErrorCode = TEXT("live-editor-write-property-type-unsupported");
			OutErrorMessage = TEXT("Live Editor writes do not support native fixed-array properties.");
			return false;
		}
		OutValueAddress = OutProperty->ContainerPtrToValuePtr<void>(Context.Asset);
		return true;
	}

	bool ApplyScalarOperation(
		const FLiveWriteOperationContext& Context,
		const FLiveWriteOperationRequest& Request,
		TSharedPtr<FLiveWriteTransactionRecord>& OutRecord,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		FProperty* Property = nullptr;
		void* ValueAddress = nullptr;
		return ResolveEditableProperty(Context, Request, Property, ValueAddress, OutErrorCode, OutErrorMessage)
			&& TryApplyScalarPropertyLive(
				Context.Asset, Context.Package, Request.AssetPath, Request.PropertyPath,
				Property, ValueAddress, Request.Value, Request.SessionId,
				OutRecord, OutResult, OutErrorCode, OutErrorMessage);
	}

	bool ApplyReferenceOperation(
		const FLiveWriteOperationContext& Context,
		const FLiveWriteOperationRequest& Request,
		TSharedPtr<FLiveWriteTransactionRecord>& OutRecord,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		FProperty* Property = nullptr;
		void* ValueAddress = nullptr;
		return ResolveEditableProperty(Context, Request, Property, ValueAddress, OutErrorCode, OutErrorMessage)
			&& TryApplyReferencePropertyLive(
				Context.Asset, Context.Package, Request.AssetPath, Request.PropertyPath,
				Property, ValueAddress, Request.Value, Request.SessionId,
				OutRecord, OutResult, OutErrorCode, OutErrorMessage);
	}

	bool ApplyStructuredOperation(
		const FLiveWriteOperationContext& Context,
		const FLiveWriteOperationRequest& Request,
		TSharedPtr<FLiveWriteTransactionRecord>& OutRecord,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		FProperty* Property = nullptr;
		void* ValueAddress = nullptr;
		return ResolveEditableProperty(Context, Request, Property, ValueAddress, OutErrorCode, OutErrorMessage)
			&& TryApplyStructuredPropertyLive(
				Context.Asset, Context.Package, Request.AssetPath, Request.PropertyPath,
				Property, ValueAddress, Request.Value, Request.SessionId,
				OutRecord, OutResult, OutErrorCode, OutErrorMessage);
	}
}

namespace UEAgentKitLiveWrite
{
	void RegisterPropertyLiveWriteOperations(FLiveWriteOperationRegistry& Registry)
	{
		Registry.Register({TEXT("setAssetProperty"), ELiveWriteTargetKind::Property,
			{TEXT("propertyPath")}, StandardAssetRequirements, &ApplyScalarOperation});
		Registry.Register({TEXT("setAssetReferenceProperty"), ELiveWriteTargetKind::Property,
			{TEXT("propertyPath")}, StandardAssetRequirements, &ApplyReferenceOperation});
		Registry.Register({TEXT("setAssetStructuredProperty"), ELiveWriteTargetKind::Property,
			{TEXT("propertyPath")}, StandardAssetRequirements, &ApplyStructuredOperation});
	}
}
