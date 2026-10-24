#pragma once

#include "CoreMinimal.h"

/** Immutable-at-runtime M3.0 feature settings, read once during module startup. */
struct FUEAgentKitNativePreviewSettings
{
	bool bNativeLayoutPreview = false;
	bool bLegacyLayoutPreviewFallback = true;
	bool bNativeToolRegistry = false;
	bool bTestOverrideApplied = false;

	static void InitializeFromStartup(bool bAutomationChild);
	static const FUEAgentKitNativePreviewSettings& Get();
	static void ResetForTests();
};
