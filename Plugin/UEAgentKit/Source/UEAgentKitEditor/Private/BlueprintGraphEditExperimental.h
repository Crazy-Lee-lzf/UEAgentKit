#pragma once

#include "CoreMinimal.h"

class FJsonObject;

/**
 * Explicitly opted-in, bounded Blueprint graph structure edit.
 *
 * This is a create/connect slice with deterministic node/schema contracts.  It
 * is not enabled by default, is not a general GraphPatch executor, and has no
 * Python, console or arbitrary command fallback.  The exact Editor project must
 * opt in with:
 *
 *     [UEAgentKit]
 *     ExperimentalGraphPatchEdit=True
 *
 * Supported nodes are Branch, Sequence, Boolean NOT/OR, integer add/equal,
 * double add, string concat, and self-context member-variable Get/Set accessors
 * for existing plain bool/int/double/string variables.  Supported connections
 * are exact exec or exact bool/int/double/string data links; one output may fan
 * out to distinct inputs.  One endpoint may be an exact existing K2 pin
 * (nodeGuid + pinGuid + name + direction + category); an existing input must be
 * unlinked and the schema must return MAKE.  Every call still requires the exact
 * focused Blueprint graph, clean package, stopped PIE/SIE, current revision and
 * graph fingerprint, one FScopedTransaction, exact readback, and Undo/Redo
 * verification.  Save is a separate explicit gate bound to the Apply plan,
 * post-Apply fingerprint and exact node/link counts.
 */
namespace UEAgentKitBlueprintGraphEditExperimental
{
	const TCHAR* const PreviewMethod = TEXT("editor.previewBlueprintGraphEditExperimental");
	const TCHAR* const ApplyMethod = TEXT("editor.applyBlueprintGraphEditExperimental");
	const TCHAR* const SaveMethod = TEXT("editor.saveBlueprintGraphEditExperimental");
	const TCHAR* const ApplyConfirmationPrefix = TEXT("CREATE EXPERIMENTAL GRAPH NODES ");
	const TCHAR* const SaveConfirmationPrefix = TEXT("SAVE EXPERIMENTAL GRAPH NODES ");

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
