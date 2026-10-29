#pragma once

#include "CoreMinimal.h"

class FJsonObject;

/**
 * P7 lab-only position apply/save surface.
 *
 * This is deliberately not a general Blueprint graph-layout write API.  Every
 * entry point checks one canonical disposable project layout (a running
 * P7AutomationHost project located directly under a Build/ directory), the
 * SHA-256 of its exact .uproject file, the exact P7 asset, the exact focused
 * graph, the live preflight, and an explicit confirmation phrase before calling
 * the existing P5 TryApply transaction.
 */
namespace UEAgentKitBlueprintGraphLayoutP7
{
	/**
	 * Canonical disposable host path for the currently running project.
	 *
	 * Returns the normalized absolute path only when the loaded project is named
	 * P7AutomationHost and lives in a Build/P7AutomationHost directory.  This keeps
	 * the gate portable across checkouts without embedding a machine-specific
	 * absolute path; the exact .uproject SHA-256 below remains the identity anchor.
	 * Returns an empty string (fail-closed) for every other project layout.
	 */
	FString GetFixedProjectPath();

	const TCHAR* const FixedProjectName = TEXT("P7AutomationHost");
	const TCHAR* const FixedProjectFileSha256 =
		TEXT("20f86465e2296f232be047c22f7924b24be6c62fd3eeaf13f8c52936964211c2");
	const TCHAR* const FixedAssetPath = TEXT("/Game/UEAgentKitP7/BP_LayoutP7.BP_LayoutP7");
	const TCHAR* const ApplyConfirmationPrefix = TEXT("APPLY P7 TEST-ONLY ");
	const TCHAR* const SaveConfirmation = TEXT("SAVE P7 TEST-ONLY BP_LayoutP7");
	const TCHAR* const ApplyMethod = TEXT("editor.applyBlueprintGraphLayoutTestOnly");
	const TCHAR* const SaveMethod = TEXT("editor.saveBlueprintGraphLayoutTestOnly");

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

	/** Capability/hello visibility is false in every non-P7 project. */
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
