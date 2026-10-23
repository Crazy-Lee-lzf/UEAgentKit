#pragma once

#include "CoreMinimal.h"

class FJsonObject;

/**
 * P9 lab-only position apply/save surface.
 *
 * This is deliberately not a general Blueprint graph-layout write API.  Every
 * entry point checks one canonical disposable project path, the SHA-256 of its
 * exact .uproject file, the exact P9 asset, the exact focused graph, the live
 * preflight, and an explicit confirmation phrase before calling the existing
 * P5 TryApply transaction.
 */
namespace UEAgentKitBlueprintGraphLayoutP9
{
	const TCHAR* const FixedProjectPath =
		TEXT("E:/WorkSpace/UEAgentKit-WT/graph-contracts/Build/P9AutomationHost/P9AutomationHost.uproject");
	const TCHAR* const FixedProjectName = TEXT("P9AutomationHost");
	const TCHAR* const FixedProjectFileSha256 =
		TEXT("6b094c65b66b795ed056ce10b5e4ded576ffb1f7b1bab9c78c588d87a4fbbbec");
	const TCHAR* const FixedAssetPath = TEXT("/Game/UEAgentKitP9/BP_LayoutP9.BP_LayoutP9");
	/** Exact ubergraph identity, populated after the P9 fixture is created. */
	const TCHAR* const FixedGraphGuid = TEXT("51e9a5e4-4654-f8a3-8179-8d83fc702c90");
	const TCHAR* const ApplyConfirmationPrefix = TEXT("APPLY P9 TEST-ONLY ");
	const TCHAR* const SaveConfirmation = TEXT("SAVE P9 TEST-ONLY BP_LayoutP9");
	const TCHAR* const ApplyMethod = TEXT("editor.applyBlueprintGraphLayoutP9TestOnly");
	const TCHAR* const SaveMethod = TEXT("editor.saveBlueprintGraphLayoutP9TestOnly");

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

	/** Capability/hello visibility is false in every non-P9 project. */
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
