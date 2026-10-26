#pragma once

#include "CoreMinimal.h"

class FJsonObject;

/**
 * P8 lab-only position apply/save surface.
 *
 * This is deliberately not a general Blueprint graph-layout write API.  Every
 * entry point checks one canonical disposable project layout (a running
 * P8AutomationHost project located directly under a Build/ directory), the
 * SHA-256 of its exact .uproject file, the exact P8 asset, the exact focused
 * graph, the live preflight, and an explicit confirmation phrase before calling
 * the existing P5 TryApply transaction.
 */
namespace UEAgentKitBlueprintGraphLayoutP8
{
	/**
	 * Canonical disposable host path for the currently running project.
	 *
	 * Returns the normalized absolute path only when the loaded project is named
	 * P8AutomationHost and lives in a Build/P8AutomationHost directory.  This keeps
	 * the gate portable across checkouts without embedding a machine-specific
	 * absolute path; the exact .uproject SHA-256 below remains the identity anchor.
	 * Returns an empty string (fail-closed) for every other project layout.
	 */
	FString GetFixedProjectPath();

	const TCHAR* const FixedProjectName = TEXT("P8AutomationHost");
	const TCHAR* const FixedProjectFileSha256 =
		TEXT("3c9ea038dd3cb184ef4b6c1f76cf4c2de8c1c08e34536a43b7618493dace1624");
	const TCHAR* const FixedAssetPath = TEXT("/Game/UEAgentKitP8/BP_LayoutP8.BP_LayoutP8");
	const TCHAR* const ApplyConfirmationPrefix = TEXT("APPLY P8 TEST-ONLY ");
	const TCHAR* const SaveConfirmation = TEXT("SAVE P8 TEST-ONLY BP_LayoutP8");
	const TCHAR* const ApplyMethod = TEXT("editor.applyBlueprintGraphLayoutP8TestOnly");
	const TCHAR* const SaveMethod = TEXT("editor.saveBlueprintGraphLayoutP8TestOnly");

	/** True only for the exact canonical project path/name/.uproject SHA-256. */
	bool IsFixedProjectIdentity(FString& OutErrorCode, FString& OutErrorMessage);

	/** Exposed for the negative gate test; never used to select a write target. */
	bool IsFixedProjectIdentityForTest(
		const FString& ProjectFilePath,
		const FString& ProjectName,
		const FString& ProjectFileSha256,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	bool IsFixedAssetPath(const FString& AssetPath);

	/** Capability/hello visibility is false in every non-P8 project. */
	bool IsTestOnlyWriteAvailable();

	bool TryApplyTestOnly(
		const TSharedPtr<FJsonObject>& Params,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	bool TrySaveTestOnly(
		const TSharedPtr<FJsonObject>& Params,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage);
}
