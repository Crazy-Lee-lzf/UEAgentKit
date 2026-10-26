#pragma once

#include "CoreMinimal.h"

class FJsonObject;
class UEdGraph;

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

	/** Automation-only seam; never used by the bridge or MCP. */
	void SetExperimentalWriteEnabledForTest(bool bEnabled);
	void ResetExperimentalWriteForTest();
}
