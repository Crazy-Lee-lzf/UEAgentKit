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

	void FLiveWriteSnapshot::Reset()
	{
		if (Property != nullptr && Storage != nullptr)
		{
			Property->DestroyValue(Storage);
			FMemory::Free(Storage);
			Property = nullptr;
			Storage = nullptr;
		}
	}

	bool RunLiveWriteTransaction(
		FLiveWriteContext& Context,
		TUniquePtr<ILiveWriteValueIO>& IO,
		FLiveWriteEvidence& OutEvidence,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		if (Context.Asset == nullptr || Context.Package == nullptr)
		{
			OutErrorCode = TEXT("live-editor-write-apply-failed");
			OutErrorMessage = TEXT("The live write transaction received an invalid target.");
			return false;
		}

		const bool bPackageDirtyBefore = Context.Package->IsDirty();
		OutEvidence.bPackageDirtyBefore = bPackageDirtyBefore;

		if (!IO->CaptureSnapshot())
		{
			OutErrorCode = TEXT("live-editor-write-apply-failed");
			OutErrorMessage = TEXT("Could not capture the live write target snapshot.");
			return false;
		}
		if (!IO->IsSnapshotValid())
		{
			IO->ReleaseSnapshot();
			OutErrorCode = TEXT("live-editor-write-apply-failed");
			OutErrorMessage = TEXT("The live write target snapshot is not valid.");
			return false;
		}

		TSharedPtr<FJsonValue> BeforeValue;
		if (!IO->ReadBefore(BeforeValue, OutErrorCode, OutErrorMessage))
		{
			IO->ReleaseSnapshot();
			return false;
		}
		OutEvidence.BeforeValue = BeforeValue;

		FScopedTransaction Transaction(TEXT("UEAgentKitLiveWrite"), FText::FromString(Context.TransactionTitle), Context.Asset);
		Context.Asset->Modify();

		if (!IO->ApplyValue(Context.Value, OutErrorCode, OutErrorMessage))
		{
			IO->RestoreSnapshot();
			IO->NotifyRestored();
			Context.Package->SetDirtyFlag(bPackageDirtyBefore);
			Transaction.Cancel();
			IO->ReleaseSnapshot();
			return false;
		}

		TSharedPtr<FJsonValue> AfterValue;
		if (!IO->ReadAfter(Context.Value, AfterValue, OutErrorCode, OutErrorMessage))
		{
			IO->RestoreSnapshot();
			IO->NotifyRestored();
			Context.Package->SetDirtyFlag(bPackageDirtyBefore);
			Transaction.Cancel();
			IO->ReleaseSnapshot();
			return false;
		}
		OutEvidence.AfterValue = AfterValue;

		if (IO->SemanticEqual(BeforeValue, AfterValue))
		{
			IO->RestoreSnapshot();
			Context.Package->SetDirtyFlag(bPackageDirtyBefore);
			Transaction.Cancel();
			IO->ReleaseSnapshot();
			OutEvidence.bChanged = false;
			OutEvidence.bTransactionRecorded = false;
			OutEvidence.TransactionTitle = FString();
			OutEvidence.AfterValue = BeforeValue;
			OutEvidence.bPackageDirtyAfter = Context.Package->IsDirty();
			OutEvidence.bSaved = false;
			return true;
		}

		if (Context.CompileAfterWrite)
		{
			Context.bCompileAttempted = true;
			FString CompileError;
			if (!Context.CompileAfterWrite(CompileError))
			{
				Context.bCompileSucceeded = false;
				Context.CompileErrors.Add(CompileError);
				{
					FString RecoveryRefreshError;
					if (!IO->RefreshTarget(RecoveryRefreshError))
					{
						IO->ReleaseSnapshot();
						OutErrorCode = TEXT("live-editor-write-recovery-failed");
						OutErrorMessage = TEXT("Could not re-resolve the live write target after compile failure: ") + RecoveryRefreshError;
						return false;
					}
					IO->RestoreSnapshot();
				}
				IO->NotifyRestored();
				Context.Package->SetDirtyFlag(bPackageDirtyBefore);
				Transaction.Cancel();
				if (Context.RecompileBaselineAfterRestore)
				{
					FString RecompileError;
					if (!Context.RecompileBaselineAfterRestore(RecompileError))
					{
						IO->ReleaseSnapshot();
						OutErrorCode = TEXT("live-editor-write-recovery-failed");
						OutErrorMessage = TEXT("Compile failed and baseline recompile could not be proven: ") + RecompileError;
						return false;
					}
				}
				IO->ReleaseSnapshot();
				OutErrorCode = TEXT("live-editor-write-compile-failed");
				OutErrorMessage = CompileError;
				return false;
			}
			Context.bCompileSucceeded = true;
			TSharedPtr<FJsonValue> CompiledAfterValue;
			if (!IO->ReadAfter(Context.Value, CompiledAfterValue, OutErrorCode, OutErrorMessage)
				|| !IO->SemanticEqual(CompiledAfterValue, AfterValue))
			{
				Context.CompileErrors.Add(TEXT("Blueprint compile succeeded but the target value did not remain exact after compile."));
				{
					FString RecoveryRefreshError;
					if (!IO->RefreshTarget(RecoveryRefreshError))
					{
						IO->ReleaseSnapshot();
						OutErrorCode = TEXT("live-editor-write-recovery-failed");
						OutErrorMessage = TEXT("Could not re-resolve the live write target after compile read-back failure: ") + RecoveryRefreshError;
						return false;
					}
					IO->RestoreSnapshot();
				}
				IO->NotifyRestored();
				Context.Package->SetDirtyFlag(bPackageDirtyBefore);
				Transaction.Cancel();
				if (Context.RecompileBaselineAfterRestore)
				{
					FString RecompileError;
					if (!Context.RecompileBaselineAfterRestore(RecompileError))
					{
						IO->ReleaseSnapshot();
						OutErrorCode = TEXT("live-editor-write-recovery-failed");
						OutErrorMessage = TEXT("Compile failed and baseline recompile could not be proven: ") + RecompileError;
						return false;
					}
				}
				IO->ReleaseSnapshot();
				if (OutErrorMessage.IsEmpty())
				{
					OutErrorMessage = TEXT("Blueprint compile succeeded but the target value did not remain exact after compile.");
				}
				OutErrorCode = TEXT("live-editor-write-compile-readback-failed");
				return false;
			}
			OutEvidence.AfterValue = CompiledAfterValue;
		}

		IO->NotifyChanged();
		Context.Asset->MarkPackageDirty();
		if (!Context.Package->IsDirty())
		{
			IO->RestoreSnapshot();
			IO->NotifyRestored();
			Context.Package->SetDirtyFlag(bPackageDirtyBefore);
			Transaction.Cancel();
			IO->ReleaseSnapshot();
			OutErrorCode = TEXT("live-editor-write-apply-failed");
			OutErrorMessage = TEXT("The Editor did not confirm the changed Dirty package state.");
			return false;
		}

		// The IO keeps its pre-write snapshot so the caller can retain it for an
		// explicit Undo/Discard of this confirmed write.
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
		if (!Evidence.TransactionId.IsEmpty())
		{
			Result->SetStringField(TEXT("transactionId"), Evidence.TransactionId);
		}
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
