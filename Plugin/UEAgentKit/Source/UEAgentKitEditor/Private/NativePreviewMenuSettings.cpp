#include "NativePreviewMenuSettings.h"

#include "HAL/FileManager.h"
#include "Misc/App.h"
#include "Misc/CommandLine.h"
#include "Misc/ConfigCacheIni.h"
#include "Misc/Parse.h"
#include "Misc/Paths.h"

namespace
{
	FUEAgentKitNativePreviewSettings GSettings;

	bool IsDisposableP11Host()
	{
		return FApp::GetProjectName() == FString(TEXT("P11AutomationHost"))
			&& FPaths::FileExists(
				FPaths::Combine(FPaths::ProjectDir(), TEXT("P11TestOnlyBuild.json")));
	}
}

void FUEAgentKitNativePreviewSettings::InitializeFromStartup(bool bAutomationChild)
{
	GSettings = {};
	GConfig->GetBool(
		TEXT("UEAgentKit"), TEXT("NativeLayoutPreview"), GSettings.bNativeLayoutPreview, GEngineIni);
	GSettings.bLegacyLayoutPreviewFallback = true;
	GConfig->GetBool(
		TEXT("UEAgentKit"),
		TEXT("LegacyLayoutPreviewFallback"),
		GSettings.bLegacyLayoutPreviewFallback,
		GEngineIni);
	GConfig->GetBool(
		TEXT("UEAgentKit"), TEXT("NativeToolRegistry"), GSettings.bNativeToolRegistry, GEngineIni);

	if (bAutomationChild && IsDisposableP11Host())
	{
		FString Override;
		if (FParse::Value(FCommandLine::Get(), TEXT("UEAgentKitNativePreviewTest="), Override)
			&& !Override.IsEmpty())
		{
			if (Override == TEXT("on") || Override == TEXT("on-no-fallback"))
			{
				GSettings.bNativeLayoutPreview = true;
				GSettings.bLegacyLayoutPreviewFallback = Override != TEXT("on-no-fallback");
				GSettings.bTestOverrideApplied = true;
			}
			else if (Override == TEXT("off"))
			{
				GSettings.bNativeLayoutPreview = false;
				GSettings.bTestOverrideApplied = true;
			}
		}
	}
}

const FUEAgentKitNativePreviewSettings& FUEAgentKitNativePreviewSettings::Get()
{
	return GSettings;
}

void FUEAgentKitNativePreviewSettings::ResetForTests()
{
	GSettings = {};
}
