#include "LiveWriteTransaction.h"

#include "Dom/JsonObject.h"
#include "Editor.h"
#include "ScopedTransaction.h"
#include "UObject/Package.h"
#include "UObject/UObjectGlobals.h"

namespace UEAgentKitLiveWrite
{
	FLiveWriteSnapshot::~FLiveWriteSnapshot()
	{
		if (Property != nullptr && Storage != nullptr)
		{
			Property->DestroyValue(Storage);
			FMemory::Free(Storage);
		}
	}

	bool FLiveWriteSnapshot::Capture(const FProperty* InProperty, const void* Source)
	{
		if (Property != nullptr && Storage != nullptr)
		{
			Property->DestroyValue(Storage);
			FMemory::Free(Storage);
			Property = nullptr;
			Storage = nullptr;
		}
		if (InProperty == nullptr || Source == nullptr)
		{
			return false;
		}
		Property = InProperty;
		Storage = FMemory::Malloc(Property->GetSize(), Property->GetMinAlignment());
		Property->InitializeValue(Storage);
		Property->CopyCompleteValue(Storage, Source);
		return true;
	}

	void FLiveWriteSnapshot::Restore(void* Destination) const
	{
		check(Property != nullptr && Storage != nullptr);
		Property->CopyCompleteValue(Destination, Storage);
	}

	bool RunLiveWriteTransaction(
		const FLiveWriteContext& Context,
		ILiveWriteValueIO& IO,
		FLiveWriteEvidence& OutEvidence,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		if (Context.Asset == nullptr || Context.Package == nullptr || Context.Property == nullptr || Context.ValueAddress == nullptr)
		{
			OutErrorCode = TEXT("live-editor-write-apply-failed");
			OutErrorMessage = TEXT("The live write transaction received an invalid target.");
			return false;
		}

		const bool bPackageDirtyBefore = Context.Package->IsDirty();
		OutEvidence.bPackageDirtyBefore = bPackageDirtyBefore;

		TSharedPtr<FJsonValue> BeforeValue;
		if (!IO.ReadBefore(Context.Property, Context.ValueAddress, BeforeValue, OutErrorCode, OutErrorMessage))
		{
			return false;
		}
		OutEvidence.BeforeValue = BeforeValue;

		FLiveWriteSnapshot Snapshot;
		if (!Snapshot.Capture(Context.Property, Context.ValueAddress))
		{
			OutErrorCode = TEXT("live-editor-write-apply-failed");
			OutErrorMessage = TEXT("Could not allocate the live write property snapshot.");
			return false;
		}

		FScopedTransaction Transaction(FText::FromString(Context.TransactionTitle));
		Context.Asset->Modify();

		if (!IO.ApplyValue(Context.Property, Context.ValueAddress, Context.Value, OutErrorCode, OutErrorMessage))
		{
			Snapshot.Restore(Context.ValueAddress);
			FPropertyChangedEvent RestoreEvent(Context.Property, EPropertyChangeType::ValueSet);
			Context.Asset->PostEditChangeProperty(RestoreEvent);
			Context.Package->SetDirtyFlag(bPackageDirtyBefore);
			Transaction.Cancel();
			return false;
		}

		TSharedPtr<FJsonValue> AfterValue;
		if (!IO.ReadAfter(Context.Property, Context.ValueAddress, Context.Value, AfterValue, OutErrorCode, OutErrorMessage))
		{
			Snapshot.Restore(Context.ValueAddress);
			FPropertyChangedEvent RestoreEvent(Context.Property, EPropertyChangeType::ValueSet);
			Context.Asset->PostEditChangeProperty(RestoreEvent);
			Context.Package->SetDirtyFlag(bPackageDirtyBefore);
			Transaction.Cancel();
			return false;
		}
		OutEvidence.AfterValue = AfterValue;

		if (IO.SemanticEqual(BeforeValue, AfterValue))
		{
			Snapshot.Restore(Context.ValueAddress);
			Context.Package->SetDirtyFlag(bPackageDirtyBefore);
			Transaction.Cancel();
			OutEvidence.bChanged = false;
			OutEvidence.bTransactionRecorded = false;
			OutEvidence.TransactionTitle = FString();
			OutEvidence.AfterValue = BeforeValue;
			OutEvidence.bPackageDirtyAfter = Context.Package->IsDirty();
			OutEvidence.bSaved = false;
			return true;
		}

		FPropertyChangedEvent ChangedEvent(Context.Property, EPropertyChangeType::ValueSet);
		Context.Asset->PostEditChangeProperty(ChangedEvent);
		Context.Asset->MarkPackageDirty();
		if (!Context.Package->IsDirty())
		{
			Snapshot.Restore(Context.ValueAddress);
			FPropertyChangedEvent RestoreEvent(Context.Property, EPropertyChangeType::ValueSet);
			Context.Asset->PostEditChangeProperty(RestoreEvent);
			Context.Package->SetDirtyFlag(bPackageDirtyBefore);
			Transaction.Cancel();
			OutErrorCode = TEXT("live-editor-write-apply-failed");
			OutErrorMessage = TEXT("The Editor did not confirm the changed Dirty package state.");
			return false;
		}

		OutEvidence.bChanged = true;
		OutEvidence.bTransactionRecorded = true;
		OutEvidence.TransactionTitle = Context.TransactionTitle;
		OutEvidence.bPackageDirtyAfter = Context.Package->IsDirty();
		OutEvidence.bSaved = false;
		return true;
	}

	void FillLiveWriteEvidence(
		TSharedRef<FJsonObject>& Result,
		const FLiveWriteContext& Context,
		const FLiveWriteEvidence& Evidence,
		const FString& Operation,
		const FString& ValueKind,
		const FString& PropertyType,
		bool bIncludeContext,
		bool bIncludeDirtyPair)
	{
		Result->SetStringField(TEXT("action"), TEXT("apply-asset-property-live"));
		Result->SetStringField(TEXT("operation"), Operation);
		Result->SetStringField(TEXT("assetPath"), Context.AssetPath);
		if (bIncludeContext)
		{
			Result->SetStringField(TEXT("packageName"), Context.Package != nullptr ? Context.Package->GetName() : FString());
			Result->SetStringField(TEXT("classPath"), Context.Asset != nullptr ? Context.Asset->GetClass()->GetPathName() : FString());
		}
		Result->SetStringField(TEXT("propertyPath"), Context.PropertyPath);
		Result->SetStringField(TEXT("propertyType"), PropertyType);
		Result->SetStringField(TEXT("valueKind"), ValueKind);
		if (Evidence.BeforeValue.IsValid())
		{
			Result->SetField(TEXT("beforeValue"), Evidence.BeforeValue);
		}
		if (Evidence.AfterValue.IsValid())
		{
			Result->SetField(TEXT("afterValue"), Evidence.AfterValue);
		}
		Result->SetBoolField(TEXT("changed"), Evidence.bChanged);
		Result->SetBoolField(TEXT("transactionRecorded"), Evidence.bTransactionRecorded);
		if (bIncludeContext)
		{
			Result->SetStringField(TEXT("transactionTitle"), Evidence.TransactionTitle);
			Result->SetBoolField(TEXT("assetOpen"), true);
			Result->SetBoolField(TEXT("loadedByBridge"), false);
		}
		Result->SetBoolField(TEXT("packageDirtyBefore"), Evidence.bPackageDirtyBefore);
		Result->SetBoolField(TEXT("packageDirtyAfter"), Evidence.bPackageDirtyAfter);
		if (bIncludeDirtyPair)
		{
			Result->SetBoolField(TEXT("dirtyBefore"), Evidence.bPackageDirtyBefore);
			Result->SetBoolField(TEXT("dirtyAfter"), Evidence.bPackageDirtyAfter);
		}
		Result->SetBoolField(TEXT("saved"), Evidence.bSaved);
		Result->SetStringField(TEXT("editorSessionId"), Context.SessionId);
	}
}
