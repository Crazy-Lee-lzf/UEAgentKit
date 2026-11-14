#pragma once

#include "CoreMinimal.h"

class FJsonObject;
class UEdGraph;

/**
 * Explicitly opted-in, bounded edit of existing Blueprint input-pin defaults.
 *
 * This is a P1 data-edit slice.  It only targets existing, unlinked, editable
 * input pins in the exact focused K2 graph, and only accepts strict
 * bool/int/double/string encodings.  It never creates nodes, changes links,
 * creates variables, deletes, disconnects, compiles, or saves implicitly.
 *
 * The project must opt in with:
 *
 *     [UEAgentKit]
 *     ExperimentalGraphPatchEdit=True
 */
namespace UEAgentKitBlueprintPinDefaultExperimental
{
	const TCHAR* const PreviewMethod = TEXT("editor.previewBlueprintPinDefaultsExperimental");
	const TCHAR* const ApplyMethod = TEXT("editor.applyBlueprintPinDefaultsExperimental");
	const TCHAR* const SaveMethod = TEXT("editor.saveBlueprintPinDefaultsExperimental");
	const TCHAR* const ApplyConfirmationPrefix = TEXT("SET EXPERIMENTAL BLUEPRINT PIN DEFAULTS ");
	const TCHAR* const SaveConfirmationPrefix = TEXT("SAVE EXPERIMENTAL BLUEPRINT PIN DEFAULTS ");

	/** True only when the current project set the experimental opt-in flag. */
	bool IsExperimentalWriteAvailable();

	bool TryPreviewExperimental(
		const TSharedPtr<FJsonObject>& Params,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	bool TryApplyExperimental(
		const TSharedPtr<FJsonObject>& Params,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	bool TrySaveExperimental(
		const TSharedPtr<FJsonObject>& Params,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/**
	 * One pin-default operation resolved against the live graph.
	 *
	 * 0-3.4 seam: the cross-writer atomic patch (BlueprintPatchExperimental)
	 * applies pin defaults through these functions instead of keeping a second
	 * copy of the pin lookup, schema validation or write loop.  The single-writer
	 * Apply below calls the very same functions, so the two paths cannot drift.
	 */
	struct FAtomicOperation
	{
		FString NodeGuid;
		FString PinName;
		FString PinGuid;
		/** bool / int / double / string; canonicalized by TryResolveAtomicOperation. */
		FString Type;
		/** Requested value; canonicalized by TryResolveAtomicOperation. */
		FString Value;
		/** Filled by TryResolveAtomicOperation from the live pin. Never requested. */
		FString BeforeValue;
	};

	/**
	 * Validate one operation against the live graph.  Read-only.
	 *
	 * Re-canonicalizes Type/Value (same rules as Preview), re-proves the exact
	 * node/pin identity, and records the live BeforeValue.  No mutation, no
	 * transaction.
	 */
	bool TryResolveAtomicOperation(
		UEdGraph* Graph,
		FAtomicOperation& InOutOperation,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/**
	 * Write resolved operations inside the CALLER's transaction.
	 *
	 * Marks the exact nodes RF_Transactional and calls Modify() so the change is
	 * a real undoable transaction record.  No transaction is opened here, and the
	 * package Dirty bit is not touched.  If a later operation fails, every pin
	 * already written by this call is restored to its BeforeValue before the
	 * failure is returned, and OutAppliedCount reports how many were applied
	 * (0 when this call restored everything itself).
	 */
	bool TryApplyAtomicOperations(
		UEdGraph* Graph,
		const TArray<FAtomicOperation>& Operations,
		int32& OutAppliedCount,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/** Read every operation's pin back; bExpectAfter selects Value or BeforeValue. */
	bool TryCheckAtomicOperations(
		UEdGraph* Graph,
		const TArray<FAtomicOperation>& Operations,
		bool bExpectAfter,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/**
	 * Restore the first AppliedCount operations to their BeforeValue, in reverse
	 * order.  Used by the atomic patch rollback.  Returns false (with the exact
	 * pin) when a pin can no longer be resolved, which means the caller cannot
	 * claim the graph was restored.
	 */
	bool TryRestoreAtomicOperations(
		UEdGraph* Graph,
		const TArray<FAtomicOperation>& Operations,
		int32 AppliedCount,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/**
	 * Whole-graph state fingerprint.  For the same live graph this is exactly the
	 * value the pin-default writer reports as `defaultsFingerprint`, so a migrated
	 * single-operation patch can be compared field by field with an unpatched
	 * Apply.
	 */
	FString ComputeAtomicStateFingerprint(UEdGraph* Graph);

	/** Automation-only seam; never used by the bridge or MCP. */
	void SetExperimentalWriteEnabledForTest(bool bEnabled);
	void ResetExperimentalWriteForTest();
}
