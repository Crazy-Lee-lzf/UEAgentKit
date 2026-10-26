#pragma once

#include "CoreMinimal.h"

class FJsonObject;

/**
 * Explicitly opted-in, bounded creation of Blueprint member variables.
 *
 * This is a P2 structural slice.  It only appends new top-level variables to
 * one exact focused Blueprint that is already saved on disk, and only for the
 * strict bool/int/double/string encodings below.  It never renames, removes or
 * retypes existing variables, never creates nodes, never changes links, never
 * compiles any Blueprint, and never saves implicitly.
 *
 * The engine's FBlueprintEditorUtils::AddMemberVariable is deliberately not
 * used: it synchronously regenerates the edited Blueprint's skeleton class and
 * may rewrite child Blueprint variables, which is outside this slice's
 * no-compile / no-collateral contract.  This module builds the same
 * FBPVariableDescription fields for the four supported primitive types and
 * serializes them through the ordinary package save path.
 *
 * The project must opt in with:
 *
 *     [UEAgentKit]
 *     ExperimentalGraphPatchEdit=True
 */
namespace UEAgentKitBlueprintVariableExperimental
{
	const TCHAR* const PreviewMethod = TEXT("editor.previewBlueprintVariableCreateExperimental");
	const TCHAR* const ApplyMethod = TEXT("editor.applyBlueprintVariableCreateExperimental");
	const TCHAR* const SaveMethod = TEXT("editor.saveBlueprintVariableCreateExperimental");
	const TCHAR* const ApplyConfirmationPrefix = TEXT("CREATE EXPERIMENTAL BLUEPRINT VARIABLES ");
	const TCHAR* const SaveConfirmationPrefix = TEXT("SAVE EXPERIMENTAL BLUEPRINT VARIABLES ");

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
