#pragma once

#include "CoreMinimal.h"

class FJsonObject;
class UBlueprint;
class UEdGraph;
class UEdGraphNode;

/**
 * Explicitly opted-in, bounded Blueprint node removal.
 *
 * One call removes exactly one already-existing node from the exact focused
 * top-level K2 event graph.  It is not a general GraphPatch executor and has no
 * Python, console or arbitrary command fallback.  The exact Editor project must
 * opt in with:
 *
 *     [UEAgentKit]
 *     ExperimentalGraphPatchEdit=True
 *
 * Only deterministic, side-effect-free nodes are removable: an allowlisted pure
 * Kismet math function call, or a branch node.  Comment groups, event entry
 * points, timelines, knots, tunnels, macro instances, variable accessors,
 * sequences and every unknown custom class are rejected.  A node may have at
 * most one incident link, and the request must enumerate every incident edge
 * (node GUID + pin name + pin GUID on both ends) exactly; Preview proves the
 * complete before/after edge sets, pin identity and node identity, Apply breaks
 * only those enumerated edges and removes only that node inside one
 * FScopedTransaction, then proves Undo restores the node and its links and Redo
 * removes them again.  It never calls BreakAllPinLinks and never lets the
 * schema break an unplanned connection.  Save is a separate explicit gate bound
 * to the Apply receipt, post-Apply hashes and disk SHA-256.  Nothing compiles or
 * auto-saves.
 */
namespace UEAgentKitBlueprintNodeRemoveExperimental
{
	const TCHAR* const PreviewMethod = TEXT("editor.previewBlueprintNodeRemoveExperimental");
	const TCHAR* const ApplyMethod = TEXT("editor.applyBlueprintNodeRemoveExperimental");
	const TCHAR* const SaveMethod = TEXT("editor.saveBlueprintNodeRemoveExperimental");
	const TCHAR* const ApplyConfirmationPrefix = TEXT("REMOVE EXPERIMENTAL BLUEPRINT NODE ");
	const TCHAR* const SaveConfirmationPrefix = TEXT("SAVE EXPERIMENTAL BLUEPRINT NODE REMOVE ");

	/** True only when the current project set the experimental opt-in flag. */
	bool IsExperimentalWriteAvailable();

	/** Live canonical edge-set hash used by the node-removal plan contract. */
	bool TryComputeLiveEdgeSetHash(UEdGraph* Graph, FString& OutHash);

	/** Read-only plan: exact deleted-node and removed-edge impact, no mutation. */
	bool TryPreviewExperimental(
		const TSharedPtr<FJsonObject>& Params,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/** Apply the exact previewed single-node removal to the focused graph. */
	bool TryApplyExperimental(
		const TSharedPtr<FJsonObject>& Params,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/** Explicit Save of the exact package produced by the preceding Apply. */
	bool TrySaveExperimental(
		const TSharedPtr<FJsonObject>& Params,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/**
	 * The writer's own per-node bounds, shared with the atomic patch so one
	 * `removeNode` operation can never remove a node the single writer would
	 * refuse: exactly one node per plan, at most this many incident edges.
	 */
	constexpr int32 MaxNodesPerPlan = 1;
	constexpr int32 MaxIncidentEdgesPerNode = 1;

	/**
	 * One incident edge of the node being removed (0-3.4 slice 13 seam).
	 *
	 * The same six identity fields the writer's own `removedEdges` entry carries,
	 * plus the resolved pin kind tokens the writer's result already reports.
	 */
	struct FAtomicRemovedEdge
	{
		FString FromNodeGuid;
		FString FromPinName;
		FString FromPinGuid;
		FString ToNodeGuid;
		FString ToPinName;
		FString ToPinGuid;
		/** Resolved pin kind tokens (`exec`/`bool`/...), filled by resolve. */
		FString FromKind;
		FString ToKind;
	};

	/**
	 * One node removal resolved against the live graph (0-3.4 slice 13).
	 *
	 * The cross-writer atomic patch uses these functions for its `removeNode`
	 * kind instead of keeping a second copy of the removable-class allowlist, the
	 * incident-edge rule or the removal sequence.  The fields are the writer's own
	 * request fields minus `expectedEdgeSetHash`: the patch already binds the same
	 * live edge set into its own plan (its `edgesFingerprint` hashes exactly the
	 * same `node|pin|pinguid>node|pin|pinguid` keys this writer hashes, only under
	 * a different version constant), so a second per-operation copy would add no
	 * proof.
	 *
	 * `DetachedNode` is runtime-only: it is the exact object Apply removed, so the
	 * rollback can put the SAME object (same NodeGuid, same PinIds) back instead of
	 * rebuilding an equivalent node.  It is never part of a plan and never
	 * serialized.
	 */
	struct FAtomicRemoveOperation
	{
		FString NodeGuid;
		FString NodeClass;
		TArray<FAtomicRemovedEdge> RemovedEdges;
		/** Filled by resolve: the node's current title and pin count. */
		FString NodeTitle;
		int32 NodePinCount = 0;
		/** Runtime-only, set by Apply and consumed by the rollback. */
		UEdGraphNode* DetachedNode = nullptr;
	};

	/**
	 * Validate one removal against the live graph.  Read-only.
	 *
	 * Applies the same removable-class allowlist, the same hidden/unsupported pin
	 * rules, the same "at most one incident edge" bound and the same
	 * "removedEdges must equal the live incident edges exactly" rule as Preview,
	 * and fills the resolved class/title/pin count and pin kind tokens.
	 */
	bool TryResolveAtomicRemoveOperation(
		UBlueprint* Blueprint,
		UEdGraph* Graph,
		FAtomicRemoveOperation& InOutOperation,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/**
	 * Break the enumerated edges and remove the node inside the CALLER's
	 * transaction.
	 *
	 * Same sequence as the writer's own Apply and the same codes.  On success the
	 * operation holds the detached node object so the rollback can restore it.
	 */
	bool TryApplyAtomicRemoveOperation(
		UBlueprint* Blueprint,
		UEdGraph* Graph,
		FAtomicRemoveOperation& InOutOperation,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/**
	 * Read one removal back: bExpectAfter selects "the node is gone and its edges
	 * are gone" or "the node is present with the resolved class and every
	 * enumerated edge is linked again".
	 */
	bool TryCheckAtomicRemoveOperation(
		UEdGraph* Graph,
		const FAtomicRemoveOperation& Operation,
		bool bExpectAfter,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/**
	 * Put the detached node back and re-create its enumerated edges.
	 *
	 * This is the whole-point-of-the-seam rollback: a removed node takes its edges
	 * with it, so a clean restore needs BOTH the node object and the edge links.
	 */
	bool TryRestoreAtomicRemoveOperation(
		UBlueprint* Blueprint,
		UEdGraph* Graph,
		FAtomicRemoveOperation& InOutOperation,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/** Automation-only seam; never used by the bridge or MCP. */
	void SetExperimentalWriteEnabledForTest(bool bEnabled);
	void ResetExperimentalWriteForTest();
}
