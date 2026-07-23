#include "EditorBridge.h"
#include "Modules/ModuleManager.h"

class FUEAgentKitEditorModule final : public IModuleInterface
{
public:
	virtual void StartupModule() override
	{
		if (GIsEditor && !IsRunningCommandlet())
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
