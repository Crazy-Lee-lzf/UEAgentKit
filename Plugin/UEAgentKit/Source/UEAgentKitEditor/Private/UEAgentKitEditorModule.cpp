#include "EditorBridge.h"
#include "BlueprintGraphLayoutP8Menu.h"
#include "BlueprintGraphLayoutP8TestOnly.h"
#include "BlueprintGraphLayoutP9Menu.h"
#include "BlueprintGraphLayoutP11Menu.h"
#include "BlueprintGraphLayoutP9TestOnly.h"
#include "NativePreviewMenuSettings.h"
#include "Native/NativeGraphMenuBinding.h"
#include "Native/NativeNodeRegistry.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "Modules/ModuleManager.h"

class FUEAgentKitEditorModule final : public IModuleInterface
{
public:
	virtual void StartupModule() override
	{
		const bool bAutomationChild = FParse::Param(FCommandLine::Get(), TEXT("UEAgentKitAutomationChild"));
		FUEAgentKitNativePreviewSettings::InitializeFromStartup(bAutomationChild);
		if (GIsEditor && !IsRunningCommandlet() && !bAutomationChild)
		{
			// Read-only native registry.  Off by default; a missing/invalid
			// manifest only disables the native surface (legacy P12 is untouched).
			FNativeNodeRegistry& NativeRegistry = FNativeNodeRegistry::Get();
			NativeRegistry.Initialize(FNativeNodeRegistry::ParseMode(FNativeNodeRegistry::ReadConfiguredModeText()));

			EditorBridge = MakeUnique<FUEAgentKitEditorBridge>();
			EditorBridge->Start();
			if (UEAgentKitBlueprintGraphLayoutP8::IsTestOnlyWriteAvailable())
			{
				GraphLayoutP8Menu = MakeShared<FUEAgentKitGraphLayoutP8Menu>(EditorBridge.Get());
				GraphLayoutP8Menu->Startup();
			}
			if (UEAgentKitBlueprintGraphLayoutP9::IsTestOnlyWriteAvailable())
			{
				GraphLayoutP9Menu = MakeShared<FUEAgentKitGraphLayoutP9Menu>(EditorBridge.Get());
				GraphLayoutP9Menu->Startup();
			}
			if (FUEAgentKitGraphLayoutP11Menu::IsAvailable())
			{
				GraphLayoutP11Menu = MakeShared<FUEAgentKitGraphLayoutP11Menu>(EditorBridge.Get());
				GraphLayoutP11Menu->Startup();
			}
			// T7 is opt-in only in active mode.  Shadow deliberately has no UI;
			// its read-only registry remains available through native.invoke.
			if (FUEAgentKitNativeGraphMenu::IsModeEligible(NativeRegistry.GetMode())
				&& NativeRegistry.IsAvailable())
			{
				NativeGraphMenu = MakeShared<FUEAgentKitNativeGraphMenu>();
				NativeGraphMenu->Startup();
			}
		}
	}

	virtual void ShutdownModule() override
	{
		if (GraphLayoutP11Menu.IsValid())
		{
			GraphLayoutP11Menu->Shutdown();
			GraphLayoutP11Menu.Reset();
		}
		if (NativeGraphMenu.IsValid())
		{
			NativeGraphMenu->Shutdown();
			NativeGraphMenu.Reset();
		}
		if (GraphLayoutP9Menu.IsValid())
		{
			GraphLayoutP9Menu->Shutdown();
			GraphLayoutP9Menu.Reset();
		}
		if (GraphLayoutP8Menu.IsValid())
		{
			GraphLayoutP8Menu->Shutdown();
			GraphLayoutP8Menu.Reset();
		}
		if (EditorBridge.IsValid())
		{
			EditorBridge->Stop();
			EditorBridge.Reset();
		}
		FNativeNodeRegistry::Get().Shutdown();
	}

private:
	TUniquePtr<FUEAgentKitEditorBridge> EditorBridge;
	TSharedPtr<FUEAgentKitGraphLayoutP8Menu> GraphLayoutP8Menu;
	TSharedPtr<FUEAgentKitGraphLayoutP9Menu> GraphLayoutP9Menu;
	TSharedPtr<FUEAgentKitGraphLayoutP11Menu> GraphLayoutP11Menu;
	TSharedPtr<FUEAgentKitNativeGraphMenu> NativeGraphMenu;
};

IMPLEMENT_MODULE(FUEAgentKitEditorModule, UEAgentKitEditor)
