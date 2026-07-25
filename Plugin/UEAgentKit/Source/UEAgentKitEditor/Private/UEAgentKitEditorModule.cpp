#include "EditorBridge.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "Modules/ModuleManager.h"

class FUEAgentKitEditorModule final : public IModuleInterface
{
public:
	virtual void StartupModule() override
	{
		const bool bAutomationChild = FParse::Param(FCommandLine::Get(), TEXT("UEAgentKitAutomationChild"));
		if (GIsEditor && !IsRunningCommandlet() && !bAutomationChild)
		{
			EditorBridge = MakeUnique<FUEAgentKitEditorBridge>();
			EditorBridge->Start();
		}
	}

	virtual void ShutdownModule() override
	{
		if (EditorBridge.IsValid())
		{
			EditorBridge->Stop();
			EditorBridge.Reset();
		}
	}

private:
	TUniquePtr<FUEAgentKitEditorBridge> EditorBridge;
};

IMPLEMENT_MODULE(FUEAgentKitEditorModule, UEAgentKitEditor)
