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
 *   slice 1  setPinDefault   -- migrated (0-3.4 slice 8)
 *   slice 2  createVariable  -- migrated (0-3.4 slice 9)
 *   slice 3  createNode      -- migrated (0-3.4 slice 10)
 *   slice 4  connect         -- migrated (0-3.4 slice 11)
 *   slice 5  disconnect      -- migrated (0-3.4 slice 12)
 *   slice 6  rewirePin       -- migrated (0-3.4 slice 12)
 *   slice 7  removeNode      -- NOT migrated yet
 *
 * Four state hashes are reported side by side, each owned by the writer that
 * already defines it: `stateFingerprint` (pin defaults and per-pin link counts),
 * `variablesFingerprint` (Blueprint variables), `nodesFingerprint` (node
 * identity, class, position and pin inventory) and `edgesFingerprint` (the exact
 * edge endpoints).  A structural patch therefore proves both "the defaults are
 * back" and "the node/edge set is back"; the pin-default hash alone records only
 * how many links each pin has, never which pins are on the other end.
 *
 * A patch may contain only migrated kinds.  A request that mixes a migrated and
 * an unmigrated kind is rejected before any write with
 * `graph-patch-kind-not-migrated`; that keeps one authorization contract per
 * patch instead of a hybrid.
 *
 * A Blueprint variable is asset-level, not graph-level: `createVariable` is
 * resolved and written against the Blueprint only.  The request still carries a
 * `graphGuid`, and it still has to name a graph that belongs to that Blueprint
 * and is the one the focused editor shows -- that GUID is the authorization and
 * provenance anchor, never a constraint on where the variable lands.  One patch
 * therefore still lands on exactly one graph and one package.
 *
 * `createNode` creates exactly one node and touches no edge.  It accepts only
 * `nodeType`/`x`/`y` plus an optional `tempId`, and only the eight primitive
 * nodeTypes of the graph-edit writer; it creates them through that writer's own
 * atomic seam, so the class, position, pin contract and spawner path are the same
 * as the single writer's.  Owned input literals are not part of this kind.
 *
 * `connect` creates exactly one edge between two existing nodes or between a
 * node this patch creates and another endpoint.  An endpoint is a canonical node
 * GUID or a `tempId` that an EARLIER `createNode` operation of the same patch
 * declared; forward references and unknown labels are refused before any write.
 * The tempId is a patch-local label: it is resolved to a GUID before Apply, it
 * never reaches the writer, and it is bound in the plan hash by the INDEX of its
 * declaring operation (not by its spelling and not by the GUID, which does not
 * exist until the transaction).  That is what makes two spellings of the same
 * intent hash the same while still refusing a plan whose node/edge pairing
 * changed.
 *
 * Explicit limits (never widened by this seam): at most 16 operations per patch,
 * at most 60 KiB request payload at the MCP boundary, one target graph and one
 * loaded package per patch.  `createVariable` keeps the single writer's own bound
 * of at most 8 variables per patch, `createNode` the writer's 16-node bound, and
 * `connect`/`disconnect`/`rewirePin` the writer's 8-connection per-node fan-out
 * bound and 24-edge per-patch bound.
 *
 * `disconnect` and `rewirePin` (0-3.4 slice 12) are the two kinds that CHANGE an
 * existing edge instead of adding one.  `disconnect` carries exactly the
 * `connect` fields but requires the named edge to already be the only link on
 * its target input, so its endpoints must be existing nodes -- a node this patch
 * creates has no edge to remove.  `rewirePin` carries the rewire writer's own
 * nine fields (target / oldSource / newSource node GUID + pin name + pin GUID)
 * and moves exactly one input from its current source to a new one.  Both go
 * through the owning writer's atomic seam, so the old-edge proof, the pin
 * resolution, the schema response judgement and the undo/rollback come from the
 * same code the single-writer tools use.  Both also rely on `edgesFingerprint`:
 * a disconnect leaves the per-pin link COUNTS of every other pin untouched and a
 * rewire leaves the total count unchanged, so the pin-default hash alone could
 * not prove either one.
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
