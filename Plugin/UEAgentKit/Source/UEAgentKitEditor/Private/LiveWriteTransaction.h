#pragma once

#include "CoreMinimal.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "UObject/UnrealType.h"
#include "UObject/WeakObjectPtr.h"

class UObject;
class UPackage;

namespace UEAgentKitLiveWrite
{
	// Deep-copy snapshot of a property value used to restore the exact pre-write state.
	class FLiveWriteSnapshot
	{
	public:
		FLiveWriteSnapshot() = default;
		~FLiveWriteSnapshot();

		FLiveWriteSnapshot(const FLiveWriteSnapshot&) = delete;
		FLiveWriteSnapshot& operator=(const FLiveWriteSnapshot&) = delete;

		bool Capture(const FProperty* InProperty, const void* Source);
		bool IsValid() const { return Property != nullptr && Storage != nullptr; }
		void Restore(void* Destination) const;
		void Reset();

	private:
		const FProperty* Property = nullptr;
		void* Storage = nullptr;
	};

	struct FLiveWriteContext
	{
		UObject* Asset = nullptr;
		UPackage* Package = nullptr;
		FString SessionId;
		FString TransactionTitle;
		FString AssetPath;
		FString PropertyPath;
		TSharedPtr<FJsonValue> Value;
		TFunction<bool(FString&)> CompileAfterWrite;
		TFunction<bool(FString&)> RecompileBaselineAfterRestore;
		TArray<FString> CompileErrors;
		bool bCompileAttempted = false;
		bool bCompileSucceeded = false;
	};

	struct FLiveWriteEvidence
	{
		bool bChanged = false;
		bool bTransactionRecorded = false;
		FString TransactionTitle;
		FString TransactionId;
		bool bPackageDirtyBefore = false;
		bool bPackageDirtyAfter = false;
		bool bSaved = false;
		TSharedPtr<FJsonValue> BeforeValue;
		TSharedPtr<FJsonValue> AfterValue;
	};

	// Per-target IO contract. The IO owns the exact pre-write target state: it
	// captures a snapshot before the transaction, applies the requested JSON value,
	// verifies the read-back, and restores the snapshot on failure or semantic no-op.
	class ILiveWriteValueIO
	{
	public:
		virtual ~ILiveWriteValueIO() = default;

		// Captures the exact pre-write target state; called before the transaction.
		virtual bool CaptureSnapshot() = 0;
		virtual bool IsSnapshotValid() const = 0;
		// Restores the captured state after a failed apply, failed read-back, or
		// a semantic no-op. Must be safe to call even after ReleaseSnapshot.
		virtual void RestoreSnapshot() = 0;
		// Releases the captured state after a confirmed successful write.
		virtual void ReleaseSnapshot() = 0;

		virtual bool ReadBefore(
			TSharedPtr<FJsonValue>& OutValue,
			FString& OutErrorCode,
			FString& OutErrorMessage) = 0;

		virtual bool ApplyValue(
			const TSharedPtr<FJsonValue>& Value,
			FString& OutErrorCode,
			FString& OutErrorMessage) = 0;

		virtual bool ReadAfter(
			const TSharedPtr<FJsonValue>& Requested,
			TSharedPtr<FJsonValue>& OutValue,
			FString& OutErrorCode,
			FString& OutErrorMessage) = 0;

		virtual bool SemanticEqual(const TSharedPtr<FJsonValue>& Left, const TSharedPtr<FJsonValue>& Right) = 0;

		// Notifies the target after a confirmed successful write (change policy).
		virtual void NotifyChanged() = 0;
		// Notifies the target after restoring the pre-write snapshot on failure.
		virtual void NotifyRestored() = 0;
	};

	// Retains one confirmed live write per target asset so the Editor Bridge can
	// explicitly Undo or Discard exactly that transaction later. The IO keeps the
	// pre-write snapshot; the record is removed after a successful revert, a
	// newer confirmed write to the same asset, or bridge stop.
	struct FLiveWriteTransactionRecord
	{
		FString SessionId;
		FString PackageName;
		FString AssetPath;
		FString ClassPath;
		FString Operation;
		FString ValueKind;
		FString TransactionTitle;
		FGuid TransactionId;
		bool bDirtyBefore = false;
		bool bDirtyAfter = false;
		TWeakObjectPtr<UObject> Asset;
		TSharedPtr<FJsonValue> BeforeValue;
		TSharedPtr<FJsonValue> AfterValue;
		TUniquePtr<ILiveWriteValueIO> IO;
	};

	// Runs the unified live write transaction lifecycle.
	// Returns true on success (changed or semantic no-op); false on failure with a stable error.
	// On a confirmed changed write the IO keeps its pre-write snapshot and its
	// ownership moves to the caller so it can be retained for explicit Undo/Discard.
	bool RunLiveWriteTransaction(
		FLiveWriteContext& Context,
		TUniquePtr<ILiveWriteValueIO>& IO,
		FLiveWriteEvidence& OutEvidence,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	// Fills the evidence fields shared by the live write operations.
	// bIncludeContext adds packageName/classPath/transactionTitle/assetOpen/loadedByBridge.
	// bIncludeDirtyPair adds dirtyBefore/dirtyAfter.
	void FillLiveWriteEvidence(
		TSharedRef<FJsonObject>& Result,
		const FLiveWriteContext& Context,
		const FLiveWriteEvidence& Evidence,
		const FString& Operation,
		const FString& ValueKind,
		const FString& PropertyType,
		bool bIncludeContext,
		bool bIncludeDirtyPair);
}
