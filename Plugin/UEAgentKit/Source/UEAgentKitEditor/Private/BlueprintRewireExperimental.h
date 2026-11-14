#pragma once

#include "CoreMinimal.h"

class FJsonObject;
class UEdGraph;

/**
 * Explicitly opted-in, bounded Blueprint rewire.
 *
 * One call rewires exactly one already-connected input pin to exactly one new
 * output pin (or explicitly disconnects that single known edge).  It is not a
 * general GraphPatch executor and has no Python, console or arbitrary command
 * fallback.  The exact Editor project must opt in with:
 *
 *     [UEAgentKit]
 *     ExperimentalGraphPatchEdit=True
 *
 * Every request binds the exact focused top-level K2 event graph, the on-disk
 * package revision, the live graph fingerprint, the complete before edge set
 * hash, and the exact old-source/target/new-source node GUID + pin name + pin
 * GUID triples.  Apply breaks only the one planned edge and makes only the one
 * planned edge inside a single FScopedTransaction, then proves the complete
 * after edge set, all node/pin identities, Undo and Redo.  It never calls
 * BreakAllPinLinks on an output pin and never lets the schema break unplanned
 * connections: a response that would break the new source's other links is
 * rejected.  Save is a separate explicit gate bound to the Apply receipt,
 * post-Apply edge-set hash and disk SHA-256.  Nothing compiles or auto-saves.
 */
namespace UEAgentKitBlueprintRewireExperimental
{
	const TCHAR* const PreviewMethod = TEXT("editor.previewBlueprintRewireExperimental");
	const TCHAR* const ApplyMethod = TEXT("editor.applyBlueprintRewireExperimental");
	const TCHAR* const SaveMethod = TEXT("editor.saveBlueprintRewireExperimental");
	const TCHAR* const ApplyConfirmationPrefix = TEXT("REWIRE EXPERIMENTAL BLUEPRINT PIN ");
	const TCHAR* const SaveConfirmationPrefix = TEXT("SAVE EXPERIMENTAL BLUEPRINT REWIRE ");

	/** True only when the current project set the experimental opt-in flag. */
	bool IsExperimentalWriteAvailable();

	/** Live canonical edge-set hash used by the rewire plan contract. */
	bool TryComputeLiveEdgeSetHash(UEdGraph* Graph, FString& OutHash);

	/** Read-only plan: exact edge difference, no mutation. */
	bool TryPreviewExperimental(
		const TSharedPtr<FJsonObject>& Params,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/** Apply the exact previewed single-edge replacement to the focused graph. */
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

	/** Automation-only seam; never used by the bridge or MCP. */
	void SetExperimentalWriteEnabledForTest(bool bEnabled);
	void ResetExperimentalWriteForTest();
}
