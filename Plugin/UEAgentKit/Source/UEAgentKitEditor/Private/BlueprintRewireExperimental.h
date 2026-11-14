#pragma once

#include "CoreMinimal.h"

class FJsonObject;
class UEdGraph;

/**
 * Atomic seam for rewiring exactly one already-connected input pin.
 *
 * The cross-writer atomic patch calls these functions for its `rewirePin` kind
 * instead of carrying its own edge-replacement logic, so the pin resolution,
 * the "exactly one old edge" rule, the schema response judgement and the one
 * break+connect sequence live in exactly one place.  It never calls
 * BreakAllPinLinks on an output pin and never lets the schema break unplanned
 * connections: a response that would break the new source's other links is
 * rejected.
 *
 * Slice 16 removed the standalone preview/apply/save pipeline (and its
 * one-click MCP tool) once the atomic patch covered this kind; the module is
 * now the seam the patch calls plus the live edge-set hash.
 */
namespace UEAgentKitBlueprintRewireExperimental
{
	/** Live canonical edge-set hash used by the patch plan contract. */
	bool TryComputeLiveEdgeSetHash(UEdGraph* Graph, FString& OutHash);

	/**
	 * One single-edge replacement resolved against the live graph (0-3.4 slice 12).
	 *
	 * The cross-writer atomic patch uses these functions for its `rewirePin` kind
	 * instead of carrying its own edge-replacement logic, so the pin resolution,
	 * the "exactly one old edge" rule, the schema response judgement and the one
	 * break+connect sequence are the same ones this writer's own Apply uses.  The
	 * fields are exactly the writer's own request field names.
	 *
	 * Deliberately NOT part of this seam: `expectedEdgeSetHash` (the patch binds
	 * the same live edge-set hash into its own plan and reports it as
	 * `edgesFingerprint`, so a second per-operation copy would add no proof), the
	 * `operation` field (the kind already says "rewire"), the event-graph rule and
	 * Save.  The seam never opens a transaction and never saves.
	 */
	struct FAtomicRewireOperation
	{
		FString TargetNodeGuid;
		FString TargetPinName;
		FString TargetPinGuid;
		FString OldSourceNodeGuid;
		FString OldSourcePinName;
		FString OldSourcePinGuid;
		FString NewSourceNodeGuid;
		FString NewSourcePinName;
		FString NewSourcePinGuid;
		/** Resolved pin kind token (`exec`/`bool`/...), filled by resolve. */
		FString PinKind;
	};

	/**
	 * Validate one replacement against the live graph.  Read-only.
	 *
	 * Resolves the three pins by GUID + name + direction, proves the old source is
	 * the target input's only link, rejects a no-op and an already-linked pair,
	 * and judges the K2 response exactly as Preview does (a response that would
	 * break the new source's own links, insert a conversion node or is disallowed
	 * is refused).
	 */
	bool TryResolveAtomicRewireOperation(
		UEdGraph* Graph,
		FAtomicRewireOperation& InOutOperation,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/**
	 * Break the old edge and make the new one inside the CALLER's transaction.
	 *
	 * On failure the graph is left as it was (the old edge is re-created) and the
	 * operation reports the writer's own code.
	 */
	bool TryApplyAtomicRewireOperation(
		UBlueprint* Blueprint,
		UEdGraph* Graph,
		const FAtomicRewireOperation& Operation,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/**
	 * Read one replacement back: bExpectAfter selects "new source is the target's
	 * only link" (after Apply) or "old source is" (after Undo/rollback).
	 */
	bool TryCheckAtomicRewireOperation(
		UEdGraph* Graph,
		const FAtomicRewireOperation& Operation,
		bool bExpectAfter,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/** Put the old edge back, which is the inverse of Apply. */
	bool TryRestoreAtomicRewireOperation(
		UBlueprint* Blueprint,
		UEdGraph* Graph,
		const FAtomicRewireOperation& Operation,
		FString& OutErrorCode,
		FString& OutErrorMessage);
}
