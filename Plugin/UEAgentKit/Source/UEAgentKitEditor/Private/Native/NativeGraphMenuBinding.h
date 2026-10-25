#pragma once

#include "CoreMinimal.h"
#include "Dom/JsonObject.h"

class UGraphNodeContextMenuContext;
class UToolMenu;
struct FToolMenuContext;
enum class ENativeRegistryMode : uint8;

/**
 * R1-T7 opt-in Editor menu binding.
 *
 * This is deliberately an inspector, not a node-creation surface.  It only
 * calls the existing read-only ``native.invoke`` dispatch
 * (graph.nodeRegistry.catalog) and never creates a node, opens a transaction,
 * saves, or dirties a package.
 *
 * Visibility:
 *   Off     -> no menu object is created and no section is registered.
 *   Shadow  -> same as Off; the read-only registry stays callable over the
 *              existing native.invoke surface only.
 *   Active  -> a dynamic section is registered under
 *              GraphEditor.GraphContextMenu.Common.  The section is added
 *              only for a real graph plus node/pin context and never in PIE.
 *
 * The P11 NativeLayoutPreview flag is independent: this class reads only
 * NativeToolRegistry.
 */
class FUEAgentKitNativeGraphMenu final : public TSharedFromThis<FUEAgentKitNativeGraphMenu>
{
public:
	FUEAgentKitNativeGraphMenu();
	~FUEAgentKitNativeGraphMenu();

	/** Idempotent.  Returns false for off/shadow/unavailable or no ToolMenus. */
	bool Startup();
	/** Idempotent.  Removes the dynamic section and releases the delegate. */
	void Shutdown();

	bool IsRegistered() const { return bRegistered; }

	static bool IsModeEligible(ENativeRegistryMode InMode);
	static bool ShouldAddEntry(const UToolMenu* Menu);
	static void SetTestDialogSuppression(bool bSuppress);

	/**
	 * Test seam: runs the same read-only dispatch used by the menu action.
	 * Production code only passes graph.nodeRegistry.catalog.
	 */
	bool ExecuteMenuActionForTest(
		const FToolMenuContext& Context,
		const FString& ToolId,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage);

private:
	void AddContextMenuSection(UToolMenu* Menu);
	void ExecuteFromContext(const FToolMenuContext& Context);
	bool ExecuteReadOnlyTool(
		const FString& ToolId,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage) const;
	void Notify(const FString& Text) const;

	bool bRegistered = false;
};
