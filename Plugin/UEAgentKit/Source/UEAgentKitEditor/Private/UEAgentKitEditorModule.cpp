#include "Modules/ModuleManager.h"

class FUEAgentKitEditorModule final : public IModuleInterface
{
public:
	virtual void StartupModule() override
	{
	}

	virtual void ShutdownModule() override
	{
	}
};

IMPLEMENT_MODULE(FUEAgentKitEditorModule, UEAgentKitEditor)
