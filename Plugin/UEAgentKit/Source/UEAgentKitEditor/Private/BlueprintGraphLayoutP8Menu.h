#pragma once

#include "CoreMinimal.h"

class FUEAgentKitEditorBridge;
class FExtender;
class FMenuBuilder;
class FUICommandList;
class UEdGraph;
class UEdGraphNode;
class UEdGraphPin;

/**
 * P8 disposable-host graph context-menu prototype.
 *
 * The entry is visible only while the exact P8 project/build/asset gate passes.
 * It computes a read-only plan through a bounded local Python worker and never
 * applies positions, saves a package, compiles a Blueprint, or calls the MCP
 * write surface.
 */
class FUEAgentKitGraphLayoutP8Menu final : public TSharedFromThis<FUEAgentKitGraphLayoutP8Menu>
{
public:
	explicit FUEAgentKitGraphLayoutP8Menu(FUEAgentKitEditorBridge* InBridge);
	void Startup();
	void Shutdown();

private:
	TSharedRef<FExtender> ExtendGraphContextMenu(
		TSharedRef<FUICommandList> CommandList,
		const UEdGraph* Graph,
		const UEdGraphNode* Node,
		const UEdGraphPin* Pin,
		bool bReadOnly);
	void AddMenuEntry(FMenuBuilder& MenuBuilder, const UEdGraph* Graph);
	void ExecutePreview(const UEdGraph* Graph);

	FUEAgentKitEditorBridge* Bridge = nullptr;
	FDelegateHandle ExtenderHandle;
};
