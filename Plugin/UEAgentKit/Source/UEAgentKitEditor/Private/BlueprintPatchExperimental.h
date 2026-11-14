#pragma once

#include "CoreMinimal.h"

class FJsonObject;
class UBlueprint;
class UEdGraph;

/**
 * Cross-writer atomic Blueprint patch (0-3.4).
 *
 * One patch submission is one `FScopedTransaction`.  Every operation is
 * validated against the live graph before the transaction opens, then applied in
 * request order; any failure restores every operation already applied, cancels
 * the transaction and reports `writeOccurred=false` together with one result per
 * operation (`opId`, `status`, resolved GUIDs, exact error code).  No operation
 * is ever silently skipped.
 *
 * Writer migration is incremental (one writer per commit):
 *
 *   slice 1  setPinDefault   -- migrated here
 *   slice 2  createVariable  -- reserved, rejected until migrated
 *   slice 3  createNode/connect
 *   slice 4  rewirePin
 *   slice 5  removeNode
 *
 * A patch may contain only migrated kinds.  A request that mixes a migrated and
 * an unmigrated kind is rejected before any write with
 * `graph-patch-kind-not-migrated`; that keeps one authorization contract per
 * patch instead of a hybrid.
 *
 * Explicit limits (never widened by this seam): at most 16 operations per patch,
 * at most 60 KiB request payload at the MCP boundary, one target graph and one
 * loaded package per patch.
 *
 * The project must opt in with:
 *
 *     [UEAgentKit]
 *     ExperimentalGraphPatchEdit=True
 */
namespace UEAgentKitBlueprintPatchExperimental
{
	const TCHAR* const PreviewMethod = TEXT("editor.previewBlueprintPatchExperimental");
	const TCHAR* const ApplyMethod = TEXT("editor.applyBlueprintPatchExperimental");
	const TCHAR* const SaveMethod = TEXT("editor.saveBlueprintPatchExperimental");
	const TCHAR* const ApplyConfirmationPrefix = TEXT("SET EXPERIMENTAL BLUEPRINT PATCH ");
	const TCHAR* const SaveConfirmationPrefix = TEXT("SAVE EXPERIMENTAL BLUEPRINT PATCH ");

	/** True only when the current project set the experimental opt-in flag. */
	bool IsExperimentalWriteAvailable();

	/**
	 * Read-only preview.
	 *
	 * Resolves and validates every operation against the live graph, binds the
	 * exact before values/fingerprints and returns the required Apply
	 * confirmation.  Nothing is written and no transaction is opened.  On failure
	 * the error details still carry one entry per operation.
	 */
	bool TryPreviewExperimental(
		const TSharedPtr<FJsonObject>& Params,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/**
	 * Apply the exact previewed patch in one transaction, never saving.
	 *
	 * On success every operation's own writer read-back must agree and one
	 * Undo/Redo round-trip must reproduce the before and after state.  On failure
	 * every applied operation is restored, the transaction is cancelled, the
	 * package Dirty bit and Blueprint status are put back, and OutResult carries
	 * the per-operation results (used as the bridge error details).
	 */
	bool TryApplyExperimental(
		const TSharedPtr<FJsonObject>& Params,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/**
	 * Save the exact package produced by the preceding Apply of this planId.
	 *
	 * Save stays a separate, explicit action: Apply never saves, and Save requires
	 * the Apply receipt recorded in this Editor session plus the live graph state
	 * it describes.
	 */
	bool TrySaveExperimental(
		const TSharedPtr<FJsonObject>& Params,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage);

#if WITH_DEV_AUTOMATION_TESTS
	/**
	 * Test-only fault seam (0-3.4).
	 *
	 * Makes the next Apply fail on the operation at `OperationIndex` *after* the
	 * operations before it were written, which is the only way to reach the
	 * in-transaction rollback path deterministically: plan-time validation rejects
	 * every externally constructible failure before the transaction opens.
	 * Compiled only into builds that enable dev automation tests; it is not
	 * reachable from MCP/socket parameters, the environment or the project config,
	 * and the armed index is consumed once.
	 */
	void SetNextApplyFaultForTest(int32 OperationIndex);
#endif

	/** Automation-only seam; never used by the bridge or MCP. */
	void SetExperimentalWriteEnabledForTest(bool bEnabled);
	void ResetExperimentalWriteForTest();
}
