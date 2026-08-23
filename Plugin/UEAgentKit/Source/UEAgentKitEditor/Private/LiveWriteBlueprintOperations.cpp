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
		FLiveWriteBlueprintPropertyIO(UObject* InOwnerObject, FProperty* InProperty, void* InValueAddress)
			: OwnerObject(InOwnerObject)
			, Property(InProperty)
			, ValueAddress(InValueAddress)
		{
		}

		void UpdateTarget(const FResolvedBlueprintTarget& NewTarget)
		{
			OwnerObject = NewTarget.OwnerObject;
			Property = NewTarget.Property;
			ValueAddress = NewTarget.ValueAddress;
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

		UObject* OwnerObject = nullptr;
		FProperty* Property = nullptr;
		void* ValueAddress = nullptr;
		FLiveWriteSnapshot Snapshot;
	};

	class FLiveWriteBlueprintPinIO final : public ILiveWriteValueIO
	{
	public:
		FLiveWriteBlueprintPinIO(UBlueprint* InBlueprint, UEdGraphPin* InPin)
			: Blueprint(InBlueprint)
			, Pin(InPin)
		{
		}

		void UpdatePin(UEdGraphPin* NewPin)
		{
			Pin = NewPin;
		}

		bool CaptureSnapshot() override
		{
			if (Pin == nullptr)
			{
				return false;
			}
			OldDefaultValue = Pin->DefaultValue;
			return true;
		}

		bool IsSnapshotValid() const override
		{
			return Pin != nullptr;
		}

		void RestoreSnapshot() override
		{
			if (Pin == nullptr)
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
			if (Pin == nullptr)
			{
				OutErrorCode = TEXT("live-editor-write-blueprint-pin-invalid");
				OutErrorMessage = TEXT("The target pin is no longer valid.");
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
			if (Pin == nullptr)
			{
				OutErrorCode = TEXT("live-editor-write-blueprint-pin-invalid");
				OutErrorMessage = TEXT("The target pin is no longer valid.");
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
			if (Pin == nullptr)
			{
				OutErrorCode = TEXT("live-editor-write-blueprint-pin-invalid");
				OutErrorMessage = TEXT("The target pin is no longer valid.");
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
			static_cast<FLiveWriteBlueprintPropertyIO*>(IO.Get())->UpdateTarget(RefreshedTarget);
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
			Target.Property->GetClass()->GetName(),
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

		TUniquePtr<ILiveWriteValueIO> IO = MakeUnique<FLiveWriteBlueprintPinIO>(Blueprint, Target.Pin);

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
			static_cast<FLiveWriteBlueprintPinIO*>(IO.Get())->UpdatePin(RefreshedTarget.Pin);
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