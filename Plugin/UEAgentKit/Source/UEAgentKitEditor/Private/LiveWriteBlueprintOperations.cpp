#include "LiveWriteOperationRegistry.h"
#include "LiveWriteOperationCommon.h"
#include "LiveWriteTransaction.h"
#include "BlueprintWriteCommon.h"
#include "StructuredPropertyJson.h"

#include "Dom/JsonValue.h"
#include "EdGraph/EdGraphPin.h"
#include "Engine/Blueprint.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "UObject/Package.h"
#include "UObject/UnrealType.h"

using namespace UEAgentKitLiveWrite;
using namespace UEAgentKitBlueprintWrite;

namespace
{
	class FLiveWriteBlueprintPropertyIO final : public ILiveWriteValueIO
	{
	public:
		FLiveWriteBlueprintPropertyIO(
			UBlueprint* InBlueprint,
			const FString& InOperation,
			const TSharedPtr<FJsonObject>& InTargetObject,
			UObject* InOwnerObject,
			FProperty* InProperty,
			void* InValueAddress)
			: Blueprint(InBlueprint)
			, Operation(InOperation)
			, TargetObject(InTargetObject)
			, OwnerObject(InOwnerObject)
			, Property(InProperty)
			, ValueAddress(InValueAddress)
		{
		}

		bool RefreshTarget(FString& OutError) override
		{
			UBlueprint* BlueprintPtr = Blueprint.Get();
			if (BlueprintPtr == nullptr || !TargetObject.IsValid())
			{
				OutError = TEXT("Blueprint or target identity is no longer available.");
				return false;
			}
			FResolvedBlueprintTarget Resolved;
			if (!ResolveBlueprintTarget(BlueprintPtr, Operation, TargetObject, Resolved, OutError))
			{
				return false;
			}
			if (Resolved.Kind != FResolvedBlueprintTarget::EKind::Property
				|| Resolved.OwnerObject == nullptr
				|| Resolved.Property == nullptr
				|| Resolved.ValueAddress == nullptr)
			{
				OutError = TEXT("The re-resolved Blueprint target is no longer a complete property target.");
				return false;
			}
			OwnerObject = Resolved.OwnerObject;
			Property = Resolved.Property;
			ValueAddress = Resolved.ValueAddress;
			return true;
		}

		bool CaptureSnapshot() override
		{
			FString RefreshError;
			if (!RefreshTarget(RefreshError))
			{
				return false;
			}
			return ReadScalarValue(Property, ValueAddress, SnapshotValue);
		}

		bool IsSnapshotValid() const override
		{
			return SnapshotValue.IsValid();
		}

		void RestoreSnapshot() override
		{
			if (!SnapshotValue.IsValid())
			{
				return;
			}
			FString RefreshError;
			if (!RefreshTarget(RefreshError))
			{
				return;
			}
			FString SetError;
			SetScalarValue(Property, ValueAddress, SnapshotValue, SetError);
		}

		void ReleaseSnapshot() override
		{
			SnapshotValue.Reset();
		}

		bool ReadBefore(
			TSharedPtr<FJsonValue>& OutValue,
			FString& OutErrorCode,
			FString& OutErrorMessage) override
		{
			FString RefreshError;
			if (!RefreshTarget(RefreshError))
			{
				OutErrorCode = TEXT("live-editor-write-blueprint-target-invalid");
				OutErrorMessage = TEXT("The Blueprint target could not be re-resolved: ") + RefreshError;
				return false;
			}
			if (!ReadScalarValue(Property, ValueAddress, OutValue))
			{
				OutErrorCode = TEXT("live-editor-write-blueprint-property-type-unsupported");
				OutErrorMessage = TEXT("Blueprint live property writes support scalar, enum, string, name, and text properties.");
				return false;
			}
			return true;
		}

		bool ApplyValue(
			const TSharedPtr<FJsonValue>& Value,
			FString& OutErrorCode,
			FString& OutErrorMessage) override
		{
			FString RefreshError;
			if (!RefreshTarget(RefreshError))
			{
				OutErrorCode = TEXT("live-editor-write-blueprint-target-invalid");
				OutErrorMessage = TEXT("The Blueprint target could not be re-resolved: ") + RefreshError;
				return false;
			}
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
			FString RefreshError;
			if (!RefreshTarget(RefreshError))
			{
				OutErrorCode = TEXT("live-editor-write-blueprint-target-invalid");
				OutErrorMessage = TEXT("The Blueprint target could not be re-resolved: ") + RefreshError;
				return false;
			}
			if (!ReadScalarValue(Property, ValueAddress, OutValue))
			{
				OutErrorCode = TEXT("live-editor-write-apply-failed");
				OutErrorMessage = TEXT("The Blueprint property could not be read back after the write.");
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
			if (OwnerObject == nullptr || Property == nullptr)
			{
				return;
			}
			FPropertyChangedEvent ChangedEvent(Property, EPropertyChangeType::ValueSet);
			OwnerObject->PostEditChangeProperty(ChangedEvent);
		}

		TWeakObjectPtr<UBlueprint> Blueprint;
		FString Operation;
		TSharedPtr<FJsonObject> TargetObject;
		UObject* OwnerObject = nullptr;
		FProperty* Property = nullptr;
		void* ValueAddress = nullptr;
		TSharedPtr<FJsonValue> SnapshotValue;
	};

	class FLiveWriteBlueprintPinIO final : public ILiveWriteValueIO
	{
	public:
		FLiveWriteBlueprintPinIO(
			UBlueprint* InBlueprint,
			const FString& InOperation,
			const TSharedPtr<FJsonObject>& InTargetObject,
			UEdGraphPin* InPin)
			: Blueprint(InBlueprint)
			, Operation(InOperation)
			, TargetObject(InTargetObject)
			, Pin(InPin)
		{
		}

		bool RefreshTarget(FString& OutError) override
		{
			UBlueprint* BlueprintPtr = Blueprint.Get();
			if (BlueprintPtr == nullptr || !TargetObject.IsValid())
			{
				OutError = TEXT("Blueprint or target identity is no longer available.");
				return false;
			}
			FResolvedBlueprintTarget Resolved;
			if (!ResolveBlueprintTarget(BlueprintPtr, Operation, TargetObject, Resolved, OutError))
			{
				return false;
			}
			if (Resolved.Kind != FResolvedBlueprintTarget::EKind::Pin || Resolved.Pin == nullptr)
			{
				OutError = TEXT("The re-resolved Blueprint target is no longer a complete pin target.");
				return false;
			}
			Pin = Resolved.Pin;
			return true;
		}

		bool CaptureSnapshot() override
		{
			FString RefreshError;
			if (!RefreshTarget(RefreshError) || Pin == nullptr)
			{
				return false;
			}
			OldDefaultValue = Pin->DefaultValue;
			return true;
		}

		bool IsSnapshotValid() const override
		{
			return Pin != nullptr && !OldDefaultValue.IsEmpty();
		}

		void RestoreSnapshot() override
		{
			FString RefreshError;
			if (!RefreshTarget(RefreshError) || Pin == nullptr)
			{
				return;
			}
			const UEdGraphSchema* Schema = Pin->GetSchema();
			if (Schema != nullptr)
			{
				Schema->TrySetDefaultValue(*Pin, OldDefaultValue, true);
			}
			Pin->DefaultValue = OldDefaultValue;
		}

		void ReleaseSnapshot() override
		{
			OldDefaultValue.Reset();
		}

		bool ReadBefore(
			TSharedPtr<FJsonValue>& OutValue,
			FString& OutErrorCode,
			FString& OutErrorMessage) override
		{
			FString RefreshError;
			if (!RefreshTarget(RefreshError) || Pin == nullptr)
			{
				OutErrorCode = TEXT("live-editor-write-blueprint-pin-invalid");
				OutErrorMessage = TEXT("The target pin is no longer valid: ") + RefreshError;
				return false;
			}
			OutValue = MakeShared<FJsonValueString>(Pin->DefaultValue);
			return true;
		}

		bool ApplyValue(
			const TSharedPtr<FJsonValue>& Value,
			FString& OutErrorCode,
			FString& OutErrorMessage) override
		{
			FString RefreshError;
			if (!RefreshTarget(RefreshError) || Pin == nullptr)
			{
				OutErrorCode = TEXT("live-editor-write-blueprint-pin-invalid");
				OutErrorMessage = TEXT("The target pin is no longer valid: ") + RefreshError;
				return false;
			}
			FString DefaultValue;
			if (!JsonValueToPinDefault(Value, DefaultValue, OutErrorMessage))
			{
				OutErrorCode = TEXT("live-editor-write-value-invalid");
				return false;
			}
			const UEdGraphSchema* Schema = Pin->GetSchema();
			if (!Schema)
			{
				OutErrorCode = TEXT("live-editor-write-blueprint-pin-schema");
				OutErrorMessage = TEXT("Pin schema is unavailable.");
				return false;
			}
			Schema->TrySetDefaultValue(*Pin, DefaultValue, true);
			if (!Pin->DefaultValue.Equals(DefaultValue, ESearchCase::IgnoreCase))
			{
				OutErrorCode = TEXT("live-editor-write-value-invalid");
				OutErrorMessage = FString::Printf(
					TEXT("Pin schema rejected or normalized the value unexpectedly. Requested=%s Actual=%s"),
					*DefaultValue,
					*Pin->DefaultValue);
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
			FString RefreshError;
			if (!RefreshTarget(RefreshError) || Pin == nullptr)
			{
				OutErrorCode = TEXT("live-editor-write-blueprint-pin-invalid");
				OutErrorMessage = TEXT("The target pin is no longer valid: ") + RefreshError;
				return false;
			}
			OutValue = MakeShared<FJsonValueString>(Pin->DefaultValue);
			return true;
		}

		bool SemanticEqual(const TSharedPtr<FJsonValue>& Left, const TSharedPtr<FJsonValue>& Right) override
		{
			FString LeftString;
			FString RightString;
			if (!Left.IsValid() || !Right.IsValid()
				|| !Left->TryGetString(LeftString)
				|| !Right->TryGetString(RightString))
			{
				return false;
			}
			return LeftString.Equals(RightString, ESearchCase::IgnoreCase);
		}

		void NotifyChanged() override
		{
			NotifyBlueprintModified();
		}

		void NotifyRestored() override
		{
			NotifyBlueprintModified();
		}

	private:
		void NotifyBlueprintModified()
		{
			if (Blueprint.IsValid())
			{
				FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint.Get());
			}
		}

		TWeakObjectPtr<UBlueprint> Blueprint;
		FString Operation;
		TSharedPtr<FJsonObject> TargetObject;
		UEdGraphPin* Pin = nullptr;
		FString OldDefaultValue;
	};

	bool CompileBlueprintLambda(UBlueprint* Blueprint, FString& OutError)
	{
		return UEAgentKitBlueprintWrite::CompileBlueprint(Blueprint, OutError);
	}

	void AddCompileEvidence(
		TSharedRef<FJsonObject>& Result,
		const FLiveWriteContext& Context)
	{
		Result->SetBoolField(TEXT("compileAttempted"), Context.bCompileAttempted);
		Result->SetBoolField(TEXT("compileSucceeded"), Context.bCompileSucceeded);
		TArray<TSharedPtr<FJsonValue>> Errors;
		const int32 MaxErrors = 20;
		for (int32 Index = 0; Index < Context.CompileErrors.Num() && Index < MaxErrors; ++Index)
		{
			const FString& Error = Context.CompileErrors[Index];
			Errors.Add(MakeShared<FJsonValueString>(Error.Left(512)));
		}
		Result->SetArrayField(TEXT("compileErrors"), Errors);
	}

	bool ApplyBlueprintPropertyLive(
		UBlueprint* Blueprint,
		UPackage* Package,
		const FString& AssetPath,
		const FString& Operation,
		const FString& ValueKind,
		const TSharedPtr<FJsonObject>& TargetObject,
		const TSharedPtr<FJsonValue>& Value,
		const FString& SessionId,
		TSharedPtr<FLiveWriteTransactionRecord>& OutRecord,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		FResolvedBlueprintTarget Target;
		if (!ResolveBlueprintTarget(Blueprint, Operation, TargetObject, Target, OutErrorMessage))
		{
			OutErrorCode = TEXT("live-editor-write-target-resolution-failed");
			return false;
		}
		if (Target.Kind != FResolvedBlueprintTarget::EKind::Property
			|| Target.OwnerObject == nullptr
			|| Target.Property == nullptr
			|| Target.ValueAddress == nullptr)
		{
			OutErrorCode = TEXT("live-editor-write-target-resolution-failed");
			OutErrorMessage = TEXT("Blueprint live property target is incomplete.");
			return false;
		}

		TUniquePtr<ILiveWriteValueIO> IO = MakeUnique<FLiveWriteBlueprintPropertyIO>(
			Blueprint,
			Operation,
			TargetObject,
			Target.OwnerObject,
			Target.Property,
			Target.ValueAddress);

		FLiveWriteContext Context;
		Context.Asset = Blueprint;
		Context.Package = Package;
		Context.SessionId = SessionId;
		Context.TransactionTitle = FString::Printf(TEXT("UE Agent Kit: %s"), *Operation);
		Context.AssetPath = AssetPath;
		Context.PropertyPath = Target.Description;
		Context.Value = Value;
		Context.CompileAfterWrite = [Blueprint, &IO, Operation, TargetObject](FString& CompileError) -> bool
		{
			if (!CompileBlueprintLambda(Blueprint, CompileError))
			{
				return false;
			}
			FResolvedBlueprintTarget RefreshedTarget;
			if (!ResolveBlueprintTarget(Blueprint, Operation, TargetObject, RefreshedTarget, CompileError))
			{
				CompileError = TEXT("Blueprint compile succeeded but the target could not be re-resolved: ") + CompileError;
				return false;
			}
			if (RefreshedTarget.Kind != FResolvedBlueprintTarget::EKind::Property)
			{
				CompileError = TEXT("Blueprint compile succeeded but the target kind changed.");
				return false;
			}
			FString RefreshError;
			if (!static_cast<FLiveWriteBlueprintPropertyIO*>(IO.Get())->RefreshTarget(RefreshError))
			{
				CompileError = TEXT("Blueprint compile succeeded but the target could not be refreshed: ") + RefreshError;
				return false;
			}
			return true;
		};
		Context.RecompileBaselineAfterRestore = [Blueprint](FString& CompileError) -> bool
		{
			return CompileBlueprintLambda(Blueprint, CompileError);
		};

		FLiveWriteEvidence Evidence;
		if (!RunLiveWriteTransaction(Context, IO, Evidence, OutErrorCode, OutErrorMessage))
		{
			return false;
		}

		OutRecord = BuildLiveWriteTransactionRecord(
			Blueprint,
			Package,
			AssetPath,
			Operation,
			ValueKind,
			SessionId,
			Evidence,
			IO);

		TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
		FillLiveWriteEvidence(
			Result,
			Context,
			Evidence,
			Operation,
			ValueKind,
			Target.TypeName,
			true,
			true);
		AddCompileEvidence(Result, Context);
		OutResult = Result;
		return true;
	}

	bool ApplyVariableDefaultOperation(
		const FLiveWriteOperationContext& Context,
		const FLiveWriteOperationRequest& Request,
		TSharedPtr<FLiveWriteTransactionRecord>& OutRecord,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		UBlueprint* Blueprint = Cast<UBlueprint>(Context.Asset);
		if (Blueprint == nullptr || Blueprint->GeneratedClass == nullptr)
		{
			OutErrorCode = TEXT("live-editor-write-blueprint-invalid");
			OutErrorMessage = TEXT("setVariableDefault requires a loaded generated Blueprint.");
			return false;
		}
		return ApplyBlueprintPropertyLive(
			Blueprint,
			Context.Package,
			Request.AssetPath,
			TEXT("setVariableDefault"),
			TEXT("blueprint-variable-default"),
			Request.Target,
			Request.Value,
			Request.SessionId,
			OutRecord,
			OutResult,
			OutErrorCode,
			OutErrorMessage);
	}

	bool ApplyComponentPropertyOperation(
		const FLiveWriteOperationContext& Context,
		const FLiveWriteOperationRequest& Request,
		TSharedPtr<FLiveWriteTransactionRecord>& OutRecord,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		UBlueprint* Blueprint = Cast<UBlueprint>(Context.Asset);
		if (Blueprint == nullptr)
		{
			OutErrorCode = TEXT("live-editor-write-blueprint-invalid");
			OutErrorMessage = TEXT("setComponentProperty requires a loaded Blueprint.");
			return false;
		}
		return ApplyBlueprintPropertyLive(
			Blueprint,
			Context.Package,
			Request.AssetPath,
			TEXT("setComponentProperty"),
			TEXT("blueprint-component-property"),
			Request.Target,
			Request.Value,
			Request.SessionId,
			OutRecord,
			OutResult,
			OutErrorCode,
			OutErrorMessage);
	}

	bool ApplyPinDefaultOperation(
		const FLiveWriteOperationContext& Context,
		const FLiveWriteOperationRequest& Request,
		TSharedPtr<FLiveWriteTransactionRecord>& OutRecord,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		UBlueprint* Blueprint = Cast<UBlueprint>(Context.Asset);
		if (Blueprint == nullptr)
		{
			OutErrorCode = TEXT("live-editor-write-blueprint-invalid");
			OutErrorMessage = TEXT("setPinDefault requires a loaded Blueprint.");
			return false;
		}

		FResolvedBlueprintTarget Target;
		if (!ResolveBlueprintTarget(Blueprint, TEXT("setPinDefault"), Request.Target, Target, OutErrorMessage))
		{
			OutErrorCode = TEXT("live-editor-write-target-resolution-failed");
			return false;
		}
		if (Target.Kind != FResolvedBlueprintTarget::EKind::Pin || Target.Pin == nullptr)
		{
			OutErrorCode = TEXT("live-editor-write-target-resolution-failed");
			OutErrorMessage = TEXT("setPinDefault target must resolve to an editable input pin.");
			return false;
		}

		TUniquePtr<ILiveWriteValueIO> IO = MakeUnique<FLiveWriteBlueprintPinIO>(
			Blueprint,
			TEXT("setPinDefault"),
			Request.Target,
			Target.Pin);

		FLiveWriteContext TransactionContext;
		TransactionContext.Asset = Blueprint;
		TransactionContext.Package = Context.Package;
		TransactionContext.SessionId = Request.SessionId;
		TransactionContext.TransactionTitle = TEXT("UE Agent Kit: setPinDefault");
		TransactionContext.AssetPath = Request.AssetPath;
		TransactionContext.PropertyPath = Target.Description;
		TransactionContext.Value = Request.Value;
		TransactionContext.CompileAfterWrite = [Blueprint, &IO, Request](FString& CompileError) -> bool
		{
			if (!CompileBlueprintLambda(Blueprint, CompileError))
			{
				return false;
			}
			FResolvedBlueprintTarget RefreshedTarget;
			if (!ResolveBlueprintTarget(Blueprint, TEXT("setPinDefault"), Request.Target, RefreshedTarget, CompileError))
			{
				CompileError = TEXT("Blueprint compile succeeded but the pin could not be re-resolved: ") + CompileError;
				return false;
			}
			if (RefreshedTarget.Kind != FResolvedBlueprintTarget::EKind::Pin || RefreshedTarget.Pin == nullptr)
			{
				CompileError = TEXT("Blueprint compile succeeded but the pin target kind changed.");
				return false;
			}
			FString RefreshError;
			if (!static_cast<FLiveWriteBlueprintPinIO*>(IO.Get())->RefreshTarget(RefreshError))
			{
				CompileError = TEXT("Blueprint compile succeeded but the pin could not be refreshed: ") + RefreshError;
				return false;
			}
			return true;
		};
		TransactionContext.RecompileBaselineAfterRestore = [Blueprint](FString& CompileError) -> bool
		{
			return CompileBlueprintLambda(Blueprint, CompileError);
		};

		FLiveWriteEvidence Evidence;
		if (!RunLiveWriteTransaction(TransactionContext, IO, Evidence, OutErrorCode, OutErrorMessage))
		{
			return false;
		}

		OutRecord = BuildLiveWriteTransactionRecord(
			Blueprint,
			Context.Package,
			Request.AssetPath,
			TEXT("setPinDefault"),
			TEXT("blueprint-pin-default"),
			Request.SessionId,
			Evidence,
			IO);

		TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
		FillLiveWriteEvidence(
			Result,
			TransactionContext,
			Evidence,
			TEXT("setPinDefault"),
			TEXT("blueprint-pin-default"),
			Target.TypeName,
			true,
			true);
		AddCompileEvidence(Result, TransactionContext);
		OutResult = Result;
		return true;
	}
}

namespace UEAgentKitLiveWrite
{
	void RegisterBlueprintLiveWriteOperations(FLiveWriteOperationRegistry& Registry)
	{
		const ELiveWriteAssetRequirement BlueprintRequirements =
			ELiveWriteAssetRequirement::LoadedAsset
			| ELiveWriteAssetRequirement::OpenInEditor
			| ELiveWriteAssetRequirement::ProjectContent
			| ELiveWriteAssetRequirement::NonMap
			| ELiveWriteAssetRequirement::CleanPackage;

		Registry.Register({TEXT("setVariableDefault"), ELiveWriteTargetKind::Property,
			{TEXT("variableName")}, BlueprintRequirements, &ApplyVariableDefaultOperation});
		Registry.Register({TEXT("setComponentProperty"), ELiveWriteTargetKind::Property,
			{TEXT("componentName"), TEXT("propertyPath")}, BlueprintRequirements, &ApplyComponentPropertyOperation});
		Registry.Register({TEXT("setPinDefault"), ELiveWriteTargetKind::Property,
			{TEXT("graphGuid"), TEXT("nodeGuid"), TEXT("pinName")}, BlueprintRequirements, &ApplyPinDefaultOperation});
	}
}