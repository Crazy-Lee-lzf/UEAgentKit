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

	/** Automation-only seam; never used by the bridge or MCP. */
	void SetExperimentalWriteEnabledForTest(bool bEnabled);
	void ResetExperimentalWriteForTest();
}
