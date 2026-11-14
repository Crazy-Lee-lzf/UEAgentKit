#pragma once

#include "CoreMinimal.h"

class FJsonObject;
class UBlueprint;
class UEdGraph;
class UEdGraphNode;

/**
 * Atomic seam for removing exactly one already-existing Blueprint node.
 *
 * Only deterministic, side-effect-free nodes are removable: an allowlisted pure
 * Kismet math function call, or a branch node.  Comment groups, event entry
 * points, timelines, knots, tunnels, macro instances, variable accessors,
 * sequences and every unknown custom class are rejected.  A node may have at
 * most one incident link, and the request must enumerate every incident edge
 * (node GUID + pin name + pin GUID on both ends) exactly.  The seam never calls
 * BreakAllPinLinks and never lets the schema break an unplanned connection.
 *
 * Slice 16 removed the standalone preview/apply/save pipeline (and its
 * one-click MCP tool) once the atomic patch covered this kind; the module is
 * now the seam the patch calls plus the live edge-set hash.
 */
namespace UEAgentKitBlueprintNodeRemoveExperimental
{
	/** Live canonical edge-set hash used by the patch plan contract. */
	bool TryComputeLiveEdgeSetHash(UEdGraph* Graph, FString& OutHash);

	/**
	 * The patch's per-node bounds: exactly one node per plan, at most this many
	 * incident edges.
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
}
