#pragma once

#include "CoreMinimal.h"

class FJsonObject;

/**
 * Explicitly opted-in, bounded edit of existing Blueprint input-pin defaults.
 *
 * This is a P1 data-edit slice.  It only targets existing, unlinked, editable
 * input pins in the exact focused K2 graph, and only accepts strict
 * bool/int/double/string encodings.  It never creates nodes, changes links,
 * creates variables, deletes, disconnects, compiles, or saves implicitly.
 *
 * The project must opt in with:
 *
 *     [UEAgentKit]
 *     ExperimentalGraphPatchEdit=True
 */
namespace UEAgentKitBlueprintPinDefaultExperimental
{
	const TCHAR* const PreviewMethod = TEXT("editor.previewBlueprintPinDefaultsExperimental");
	const TCHAR* const ApplyMethod = TEXT("editor.applyBlueprintPinDefaultsExperimental");
	const TCHAR* const SaveMethod = TEXT("editor.saveBlueprintPinDefaultsExperimental");
	const TCHAR* const ApplyConfirmationPrefix = TEXT("SET EXPERIMENTAL BLUEPRINT PIN DEFAULTS ");
	const TCHAR* const SaveConfirmationPrefix = TEXT("SAVE EXPERIMENTAL BLUEPRINT PIN DEFAULTS ");

	/** True only when the current project set the experimental opt-in flag. */
	bool IsExperimentalWriteAvailable();

	bool TryPreviewExperimental(
		const TSharedPtr<FJsonObject>& Params,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	bool TryApplyExperimental(
		const TSharedPtr<FJsonObject>& Params,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	bool TrySaveExperimental(
		const TSharedPtr<FJsonObject>& Params,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/** Automation-only seam; never used by the bridge or MCP. */
	void SetExperimentalWriteEnabledForTest(bool bEnabled);
	void ResetExperimentalWriteForTest();
}
