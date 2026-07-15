#include "Modules/ModuleManager.h"

class FBlueprintContextToolEditorModule final : public IModuleInterface
{
public:
	virtual void StartupModule() override
	{
	}

	virtual void ShutdownModule() override
	{
	}
};

IMPLEMENT_MODULE(FBlueprintContextToolEditorModule, BlueprintContextToolEditor)
