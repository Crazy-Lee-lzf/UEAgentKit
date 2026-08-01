#include "EditorBridge.h"
#include "EditorBridgeHandlerUtils.h"
#include "LiveWriteOperationRegistry.h"
#include "LiveWriteTransaction.h"

#include "Editor.h"
#include "Editor/Transactor.h"
#include "Engine/Blueprint.h"
#include "Misc/ITransaction.h"
#include "UObject/Package.h"
#include "UObject/UObjectGlobals.h"

using namespace UEAgentKitEditorBridgePrivate;
using namespace UEAgentKitLiveWrite;

namespace
{
	bool HasRequirement(
		const FLiveWriteOperationDescriptor& Descriptor,
		const ELiveWriteAssetRequirement Requirement)
	{
		return EnumHasAnyFlags(Descriptor.AssetRequirements, Requirement);
	}
}

bool FUEAgentKitEditorBridge::TryApplyAssetPropertyLiveResult(
	const FString& Operation,
	const FString& AssetPath,
	const TSharedPtr<FJsonObject>& Target,
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

	const FLiveWriteOperationRegistry& Registry = FLiveWriteOperationRegistry::Get();
	const FLiveWriteOperationDescriptor* Descriptor = Registry.Find(Operation);
	if (Descriptor == nullptr)
	{
		OutErrorCode = TEXT("live-editor-write-operation-unsupported");
		OutErrorMessage = FString::Printf(
			TEXT("Unsupported Live Editor write operation. Registered operations: %s."),
			*Registry.GetSupportedOperationSummary());
		return false;
	}

	FLiveWriteOperationRequest Request;
	Request.Operation = Operation;
	Request.AssetPath = AssetPath;
	Request.Target = Target;
	if (Target.IsValid())
	{
		Target->TryGetStringField(TEXT("propertyPath"), Request.PropertyPath);
		Target->TryGetStringField(TEXT("parameterName"), Request.ParameterName);
		Target->TryGetStringField(TEXT("rowName"), Request.RowName);
		Target->TryGetStringField(TEXT("newRowName"), Request.NewRowName);
		Target->TryGetStringField(TEXT("fieldName"), Request.FieldName);
	}
	Request.SessionId = SessionId;
	Request.Value = Value;
	if (!ValidateLiveWriteOperationRequest(*Descriptor, Request, OutErrorCode, OutErrorMessage))
	{
		return false;
	}
	if (HasRequirement(*Descriptor, ELiveWriteAssetRequirement::ProjectContent) && !IsSafeGameAssetPath(AssetPath))
	{
		OutErrorCode = TEXT("live-editor-invalid-parameters");
		OutErrorMessage = TEXT("assetPath must be one exact /Game asset Object Path.");
		return false;
	}

	UObject* Asset = StaticFindObject(UObject::StaticClass(), nullptr, *AssetPath, false);
	if (HasRequirement(*Descriptor, ELiveWriteAssetRequirement::LoadedAsset)
		&& (Asset == nullptr || !Asset->IsAsset()))
	{
		OutErrorCode = TEXT("live-editor-write-asset-not-loaded");
		OutErrorMessage = TEXT("Live write accepts only an already loaded exact asset.");
		return false;
	}
	if (Asset == nullptr)
	{
		OutErrorCode = TEXT("live-editor-write-asset-not-loaded");
		OutErrorMessage = TEXT("The registered operation requires a loaded target asset.");
		return false;
	}
	if (HasRequirement(*Descriptor, ELiveWriteAssetRequirement::OpenInEditor) && !IsAssetOpenInEditor(Asset))
	{
		OutErrorCode = TEXT("live-editor-write-asset-not-open");
		OutErrorMessage = TEXT("Open the exact asset in the Editor before applying a live write.");
		return false;
	}
	if (HasRequirement(*Descriptor, ELiveWriteAssetRequirement::NonBlueprint) && Asset->IsA<UBlueprint>())
	{
		OutErrorCode = TEXT("live-editor-write-blueprint-unsupported");
		OutErrorMessage = TEXT("This registered Live Editor write operation does not accept Blueprint assets.");
		return false;
	}

	UPackage* Package = Asset->GetOutermost();
	if (Package == nullptr
		|| (HasRequirement(*Descriptor, ELiveWriteAssetRequirement::ProjectContent)
			&& !Package->GetName().StartsWith(TEXT("/Game/")))
		|| (HasRequirement(*Descriptor, ELiveWriteAssetRequirement::NonMap) && Package->ContainsMap()))
	{
		OutErrorCode = TEXT("live-editor-write-package-invalid");
		OutErrorMessage = TEXT("The registered operation requires one non-map project Content package.");
		return false;
	}
	if (HasRequirement(*Descriptor, ELiveWriteAssetRequirement::CleanPackage) && Package->IsDirty())
	{
		OutErrorCode = TEXT("live-editor-write-package-dirty");
		OutErrorMessage = TEXT("Save or revert the target package before applying an AI live write.");
		return false;
	}

	// A clean package means any older transaction record for this asset was already
	// saved or otherwise left the undoable live-write lifecycle. Drop those stale
	// records before retaining the next confirmed write.
	LiveWriteTransactionRecords.Remove(AssetPath);

	FLiveWriteOperationContext Context;
	Context.Asset = Asset;
	Context.Package = Package;
	TSharedPtr<FLiveWriteTransactionRecord> Record;
	const bool bApplied = Descriptor->Apply(
		Context,
		Request,
		Record,
		OutResult,
		OutErrorCode,
		OutErrorMessage);
	if (bApplied && Record.IsValid())
	{
		LiveWriteTransactionRecords.FindOrAdd(AssetPath).Add(Record->TransactionId, Record);
	}
	return bApplied;
}

bool FUEAgentKitEditorBridge::TryUndoAssetPropertyLiveResult(
	const FString& AssetPath,
	const FString& TransactionId,
	const FString& ExpectedSessionId,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage) const
{
	return RevertLiveWriteTransaction(
		true,
		AssetPath,
		TransactionId,
		ExpectedSessionId,
		TEXT("undo-asset-property-live"),
		OutResult,
		OutErrorCode,
		OutErrorMessage);
}

bool FUEAgentKitEditorBridge::TryDiscardAssetPropertyLiveResult(
	const FString& AssetPath,
	const FString& TransactionId,
	const FString& ExpectedSessionId,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage) const
{
	return RevertLiveWriteTransaction(
		false,
		AssetPath,
		TransactionId,
		ExpectedSessionId,
		TEXT("discard-asset-property-live"),
		OutResult,
		OutErrorCode,
		OutErrorMessage);
}

bool FUEAgentKitEditorBridge::RevertLiveWriteTransaction(
	const bool bRedoable,
	const FString& AssetPath,
	const FString& TransactionId,
	const FString& ExpectedSessionId,
	const FString& Action,
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
		OutErrorMessage = TEXT("Live write Undo and Discard are unavailable while PIE or SIE is active.");
		return false;
	}
	if (!IsSafeGameAssetPath(AssetPath) || ExpectedSessionId.IsEmpty())
	{
		OutErrorCode = TEXT("live-editor-invalid-parameters");
		OutErrorMessage = TEXT("assetPath and one non-empty editorSessionId are required.");
		return false;
	}
	FGuid RequestedTransactionId;
	if (!FGuid::Parse(TransactionId, RequestedTransactionId) || !RequestedTransactionId.IsValid())
	{
		OutErrorCode = TEXT("live-editor-write-undo-invalid-transaction-id");
		OutErrorMessage = TEXT("transactionId must be the exact transactionId returned by the confirmed live write.");
		return false;
	}
	const TMap<FGuid, TSharedPtr<UEAgentKitLiveWrite::FLiveWriteTransactionRecord>>* AssetRecords =
		LiveWriteTransactionRecords.Find(AssetPath);
	if (AssetRecords == nullptr || AssetRecords->IsEmpty())
	{
		OutErrorCode = TEXT("live-editor-write-undo-not-found");
		OutErrorMessage = TEXT("No confirmed live write transaction is pending for this asset.");
		return false;
	}
	const TSharedPtr<UEAgentKitLiveWrite::FLiveWriteTransactionRecord>* Found = AssetRecords->Find(RequestedTransactionId);
	if (Found == nullptr)
	{
		OutErrorCode = TEXT("live-editor-write-undo-transaction-mismatch");
		OutErrorMessage = TEXT("The transactionId does not match a pending live write transaction for this asset.");
		return false;
	}
	const TSharedPtr<UEAgentKitLiveWrite::FLiveWriteTransactionRecord>& Record = *Found;
	if (Record->SessionId != ExpectedSessionId || ExpectedSessionId != SessionId)
	{
		OutErrorCode = TEXT("live-editor-write-undo-session-mismatch");
		OutErrorMessage = TEXT("The Editor session changed after the live write; re-plan the write instead of undoing.");
		return false;
	}
	if (!Record->Asset.IsValid() || Record->Asset.Get() != StaticFindObject(UObject::StaticClass(), nullptr, *AssetPath, false)
		|| !Record->Asset->IsAsset())
	{
		OutErrorCode = TEXT("live-editor-write-undo-asset-mismatch");
		OutErrorMessage = TEXT("The target asset changed after the live write; re-plan the write instead of undoing.");
		return false;
	}
	UObject* Asset = Record->Asset.Get();
	if (Asset->GetClass()->GetPathName() != Record->ClassPath)
	{
		OutErrorCode = TEXT("live-editor-write-undo-asset-mismatch");
		OutErrorMessage = TEXT("The target asset Class changed after the live write; re-plan the write instead of undoing.");
		return false;
	}
	UPackage* Package = Asset->GetOutermost();
	if (Package == nullptr || Package->GetName() != Record->PackageName)
	{
		OutErrorCode = TEXT("live-editor-write-undo-asset-mismatch");
		OutErrorMessage = TEXT("The target asset package changed after the live write; re-plan the write instead of undoing.");
		return false;
	}
	if (Record->bDirtyAfter && !Package->IsDirty())
	{
		OutErrorCode = TEXT("live-editor-write-undo-package-saved");
		OutErrorMessage = TEXT("The target package was saved after the live write; the write can no longer be undone.");
		return false;
	}
	if (GEditor->Trans == nullptr)
	{
		OutErrorCode = TEXT("live-editor-write-undo-stack-mismatch");
		OutErrorMessage = TEXT("The Editor transaction history is unavailable; re-plan the write instead of undoing.");
		return false;
	}
	const FTransactionContext UndoContext = GEditor->Trans->GetUndoContext(false);
	if (!UndoContext.TransactionId.IsValid() || UndoContext.TransactionId != Record->TransactionId)
	{
		OutErrorCode = TEXT("live-editor-write-undo-stack-mismatch");
		OutErrorMessage = TEXT("Other Editor changes are on top of the live write; undo them first or re-plan the write.");
		return false;
	}

	TSharedPtr<FJsonValue> WrittenValue;
	if (!Record->IO->ReadBefore(WrittenValue, OutErrorCode, OutErrorMessage))
	{
		OutErrorCode = TEXT("live-editor-write-undo-verify-failed");
		OutErrorMessage = TEXT("The written value could not be read back before reverting.");
		return false;
	}
	if (!Record->AfterValue.IsValid() || !Record->IO->SemanticEqual(WrittenValue, Record->AfterValue))
	{
		OutErrorCode = TEXT("live-editor-write-undo-target-changed");
		OutErrorMessage = TEXT("The live write target changed after the confirmed write; re-plan instead of overwriting the newer value.");
		return false;
	}

	if (!GEditor->UndoTransaction(bRedoable))
	{
		OutErrorCode = TEXT("live-editor-write-undo-failed");
		OutErrorMessage = TEXT("The Editor could not execute the exact live write transaction.");
		return false;
	}
	Package->SetDirtyFlag(Record->bDirtyBefore);

	TSharedPtr<FJsonValue> RestoredValue;
	bool bRestored = Record->IO->ReadBefore(RestoredValue, OutErrorCode, OutErrorMessage)
		&& Record->IO->SemanticEqual(RestoredValue, Record->BeforeValue);
	if (!bRestored)
	{
		Record->IO->RestoreSnapshot();
		Record->IO->NotifyRestored();
		Package->SetDirtyFlag(Record->bDirtyBefore);
		bRestored = Record->IO->ReadBefore(RestoredValue, OutErrorCode, OutErrorMessage)
			&& Record->IO->SemanticEqual(RestoredValue, Record->BeforeValue);
	}
	if (!bRestored)
	{
		OutErrorCode = TEXT("live-editor-write-undo-verify-failed");
		OutErrorMessage = TEXT("The Editor could not verify the restored live write target value.");
		return false;
	}
	const TSharedPtr<UEAgentKitLiveWrite::FLiveWriteTransactionRecord> RetainedRecord = Record;
	if (TMap<FGuid, TSharedPtr<UEAgentKitLiveWrite::FLiveWriteTransactionRecord>>* MutableRecords =
		LiveWriteTransactionRecords.Find(AssetPath))
	{
		MutableRecords->Remove(RequestedTransactionId);
		if (MutableRecords->IsEmpty())
		{
			LiveWriteTransactionRecords.Remove(AssetPath);
		}
	}

	const bool bChanged = !RetainedRecord->IO->SemanticEqual(WrittenValue, RestoredValue);
	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("action"), Action);
	Result->SetStringField(TEXT("operation"), RetainedRecord->Operation);
	Result->SetStringField(TEXT("valueKind"), RetainedRecord->ValueKind);
	Result->SetStringField(TEXT("assetPath"), RetainedRecord->AssetPath);
	Result->SetStringField(TEXT("transactionId"), RetainedRecord->TransactionId.ToString(EGuidFormats::DigitsWithHyphens));
	Result->SetBoolField(TEXT("changed"), bChanged);
	Result->SetBoolField(TEXT("transactionRecorded"), false);
	Result->SetBoolField(TEXT("assetOpen"), true);
	Result->SetBoolField(TEXT("loadedByBridge"), false);
	Result->SetBoolField(TEXT("packageDirtyBefore"), RetainedRecord->bDirtyAfter);
	Result->SetBoolField(TEXT("packageDirtyAfter"), Package->IsDirty());
	Result->SetBoolField(TEXT("dirtyBefore"), RetainedRecord->bDirtyAfter);
	Result->SetBoolField(TEXT("dirtyAfter"), Package->IsDirty());
	Result->SetBoolField(TEXT("saved"), false);
	Result->SetField(TEXT("beforeValue"), WrittenValue);
	Result->SetField(TEXT("afterValue"), RestoredValue);
	Result->SetStringField(TEXT("editorSessionId"), RetainedRecord->SessionId);
	OutResult = Result;
	return true;
}
