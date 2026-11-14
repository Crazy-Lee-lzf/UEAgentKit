#pragma once

#include "CoreMinimal.h"

class FJsonObject;
class UBlueprint;

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

	/**
	 * One variable-creation operation resolved against the live Blueprint.
	 *
	 * 0-3.4 seam: the cross-writer atomic patch (BlueprintPatchExperimental)
	 * creates variables through these functions instead of keeping a second copy
	 * of the name/type/default validation or of the FBPVariableDescription build.
	 * The single-writer Apply below calls the very same functions, so the two
	 * paths cannot drift.
	 *
	 * A variable is an asset-level object: it belongs to the Blueprint, not to a
	 * graph.  These functions therefore take the Blueprint only.  A patch still
	 * carries the graphGuid that authorized it, but that GUID is an identity
	 * anchor and never a constraint on where the variable lands.
	 */
	struct FAtomicVariableOperation
	{
		FString Name;
		/** bool / int / double / string; canonicalized by the resolve step. */
		FString Type;
		/** Requested default; canonicalized by the resolve step. */
		FString DefaultValue;
		/** True when the live Blueprint already owned the name. Never true after a
		 *  successful resolve; the restore path uses it to refuse to delete a
		 *  variable this operation did not create. */
		bool bExistedBefore = false;
	};

	/**
	 * Maximum variables one Apply may create.
	 *
	 * The atomic patch enforces the same number for `createVariable` operations so
	 * one patch can never create more variables than the single-writer tool.
	 */
	constexpr int32 MaxVariablesPerApply = 8;

	/**
	 * Validate one operation against the live Blueprint.  Read-only.
	 *
	 * Applies the same identifier rule, type whitelist and default canonicalization
	 * as Preview, records whether the name already exists (including parent classes)
	 * and refuses the operation when it does.  No mutation, no transaction.
	 */
	bool TryResolveAtomicVariableOperation(
		UBlueprint* Blueprint,
		FAtomicVariableOperation& InOutOperation,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/**
	 * Create resolved variables inside the CALLER's transaction.
	 *
	 * Builds exactly the FBPVariableDescription fields the single-writer Apply
	 * builds, verifies each appended entry read back, and marks the Blueprint as
	 * modified once.  No transaction is opened here and the package Dirty bit is
	 * not restored here.  If any operation fails, every variable this call already
	 * created is removed again and OutCreatedGuids is emptied, so an empty result
	 * always means "this call wrote nothing".
	 */
	bool TryApplyAtomicVariableOperations(
		UBlueprint* Blueprint,
		const TArray<FAtomicVariableOperation>& Operations,
		TArray<FGuid>& OutCreatedGuids,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/**
	 * Read every operation's variable back; bExpectAfter selects "exists with the
	 * requested type/default" or "does not exist because it did not exist before".
	 */
	bool TryCheckAtomicVariableOperations(
		UBlueprint* Blueprint,
		const TArray<FAtomicVariableOperation>& Operations,
		bool bExpectAfter,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/**
	 * Remove the first AppliedCount operations' variables, in reverse order.
	 *
	 * Used by the atomic patch rollback.  A variable that this operation did not
	 * create (bExistedBefore) is never deleted.  Returns false when a variable
	 * survives its own removal, which means the caller cannot claim a clean restore.
	 */
	bool TryRestoreAtomicVariableOperations(
		UBlueprint* Blueprint,
		const TArray<FAtomicVariableOperation>& Operations,
		int32 AppliedCount,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/**
	 * Blueprint-level variable state fingerprint (names, GUIDs, types, defaults,
	 * property flags, in declaration order).  This is exactly the value the
	 * variable writer reports as `variablesFingerprint`, so a migrated
	 * single-operation patch can be compared field by field with an unpatched
	 * Apply.
	 */
	FString ComputeAtomicVariableStateFingerprint(UBlueprint* Blueprint);

	/** Automation-only seam; never used by the bridge or MCP. */
	void SetExperimentalWriteEnabledForTest(bool bEnabled);
	void ResetExperimentalWriteForTest();
}
