#pragma once

#include "CoreMinimal.h"

class FUEAgentKitEditorBridge;
class UEdGraph;
class UToolMenu;
struct FToolMenuContext;

/**
 * P11 disposable-host graph node/pin context-menu entry.
 *
 * UE5.6 builds the Blueprint node/pin menu through ToolMenus
 * ("GraphEditor.GraphNodeContextMenu.<Class>") and extends the legacy extender
 * chain only through FExtender sections that actually exist.  P9 registered an
 * extender under "GraphEditorGraphContextMenu", which no longer exists in
 * UE5.6, so the entry was silently dropped even on a real node right-click.
 *
 * This class attaches one dynamic section to the common graph-menu root and
 * adds the entry only when the generated menu carries a node or pin context.
 * The action computes a read-only LayoutPlan through the existing bounded
 * Python worker; it never applies, saves or compiles anything.
 */
class FUEAgentKitGraphLayoutP11Menu final : public TSharedFromThis<FUEAgentKitGraphLayoutP11Menu>
{
public:
	explicit FUEAgentKitGraphLayoutP11Menu(FUEAgentKitEditorBridge* InBridge);
	void Startup();
	void Shutdown();

	/** True only inside the disposable P11 host with its build marker. */
	static bool IsAvailable();

	/** Test seam: the same project/marker policy without touching global state. */
	static bool IsAvailableFor(const FString& ProjectName, bool bMarkerExists);

	/**
	 * Test-only: suppress the informational modal so a headless Automation test
	 * can invoke the real menu action without blocking.  Never enabled outside a
	 * test and never changes the normal Editor behaviour.
	 */
	static void SetTestDialogSuppression(bool bSuppress);

	/** Test seam: whether a generated menu should receive the entry. */
	static bool ShouldAddEntry(const UToolMenu* Menu);

private:
	void AddContextMenuSection(UToolMenu* Menu);
	void ExecutePreviewFromContext(const FToolMenuContext& Context);
	void ExecutePreview(
		TWeakObjectPtr<UEdGraph> Graph,
		FGuid ContextNodeGuid,
		FString ContextPinName,
		bool bFromPin);

	FUEAgentKitEditorBridge* Bridge = nullptr;
	bool bRegistered = false;
};
