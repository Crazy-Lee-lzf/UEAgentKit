#pragma once

#include "CoreMinimal.h"

class FJsonObject;

/**
 * Explicitly opted-in, general position-only Blueprint graph layout apply/save.
 *
 * This is the bridge-side counterpart to the experimental MCP tools.  It is
 * not enabled by default and does not use a hardcoded project or asset.  The
 * exact Editor project must opt in with:
 *
 *     [UEAgentKit]
 *     ExperimentalGraphLayoutApply=True
 *
 * Every call still goes through the existing P5 gates: exact loaded Blueprint
 * and GraphGuid, focused Blueprint Editor, clean package before Apply, stopped
 * PIE/SIE, current sha256 revision and sha1 graph fingerprint, one
 * FScopedTransaction, and exact position readback.  Apply then proves Undo and
 * Redo before returning.  Save is a separate explicit call bound to the exact
 * layout hash and exact focused graph.
 */
namespace UEAgentKitBlueprintGraphLayoutExperimental
{
	const TCHAR* const ApplyMethod = TEXT("editor.applyBlueprintGraphLayoutExperimental");
	const TCHAR* const SaveMethod = TEXT("editor.saveBlueprintGraphLayoutExperimental");
	const TCHAR* const ApplyConfirmationPrefix = TEXT("APPLY EXPERIMENTAL LAYOUT ");
	const TCHAR* const SaveConfirmationPrefix = TEXT("SAVE EXPERIMENTAL LAYOUT ");

	/** True only when the current project set the experimental opt-in flag. */
	bool IsExperimentalWriteAvailable();

	/** Apply one canonical P5 ApplyRequest to the exact focused graph. */
	bool TryApplyExperimental(
		const TSharedPtr<FJsonObject>& Params,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/** Explicitly save the exact dirty package, then return an on-disk readback. */
	bool TrySaveExperimental(
		const TSharedPtr<FJsonObject>& Params,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/** Automation-only seam; never used by the bridge or MCP. */
	void SetExperimentalWriteEnabledForTest(bool bEnabled);
	void ResetExperimentalWriteForTest();
}
