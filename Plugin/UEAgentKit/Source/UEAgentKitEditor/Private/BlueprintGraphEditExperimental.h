#pragma once

#include "CoreMinimal.h"

class FJsonObject;

/**
 * Explicitly opted-in, bounded Blueprint graph structure edit.
 *
 * This is the first vertical create/connect slice.  It is not enabled by
 * default, is not a general GraphPatch executor, and has no Python, console or
 * arbitrary command fallback.  The exact Editor project must opt in with:
 *
 *     [UEAgentKit]
 *     ExperimentalGraphPatchEdit=True
 *
 * The only supported nodes are Branch (K2Node_IfThenElse) and Sequence
 * (K2Node_ExecutionSequence), and the only supported connections are exec
 * connections from a supported output pin to a supported node's execute input.
 * Every call still requires the exact focused Blueprint graph, clean package,
 * stopped PIE/SIE, current revision and graph fingerprint, one
 * FScopedTransaction, exact readback, and Undo/Redo verification.
 */
namespace UEAgentKitBlueprintGraphEditExperimental
{
	const TCHAR* const PreviewMethod = TEXT("editor.previewBlueprintGraphEditExperimental");
	const TCHAR* const ApplyMethod = TEXT("editor.applyBlueprintGraphEditExperimental");
	const TCHAR* const ApplyConfirmationPrefix = TEXT("CREATE EXPERIMENTAL GRAPH NODES ");

	/** True only when the current project set the experimental opt-in flag. */
	bool IsExperimentalWriteAvailable();

	/** Read-only plan for a bounded createNode/connectPins request. */
	bool TryPreviewExperimental(
		const TSharedPtr<FJsonObject>& Params,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/** Apply the exact previewed plan to the exact focused graph. */
	bool TryApplyExperimental(
		const TSharedPtr<FJsonObject>& Params,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/** Automation-only seam; never used by the bridge or MCP. */
	void SetExperimentalWriteEnabledForTest(bool bEnabled);
	void ResetExperimentalWriteForTest();
}
