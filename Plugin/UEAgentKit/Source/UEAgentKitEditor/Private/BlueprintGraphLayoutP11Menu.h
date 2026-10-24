#pragma once

#include "CoreMinimal.h"
#include "Containers/Ticker.h"

class FUEAgentKitEditorBridge;
class FUEAgentKitNativeLayoutPreviewService;
class FJsonObject;
class UEdGraph;
class UToolMenu;
struct FToolMenuContext;

/**
 * P11 disposable-host graph node/pin context-menu entry.
 *
 * UE5.6 builds the Blueprint node/pin menu through ToolMenus
 * ("GraphEditor.GraphNodeContextMenu.<Class>").  The legacy path stays exactly
 * as it was when NativeLayoutPreview=off.  With it on, mode=full uses the M2
 * native service asynchronously; selection/incremental remain legacy.
 */
class FUEAgentKitGraphLayoutP11Menu final : public TSharedFromThis<FUEAgentKitGraphLayoutP11Menu>
{
public:
	explicit FUEAgentKitGraphLayoutP11Menu(FUEAgentKitEditorBridge* InBridge);
	~FUEAgentKitGraphLayoutP11Menu();
	void Startup();
	void Shutdown();

	static bool IsAvailable();
	static bool IsAvailableFor(const FString& ProjectName, bool bMarkerExists);
	static void SetTestDialogSuppression(bool bSuppress);
	static bool ShouldAddEntry(const UToolMenu* Menu);

	/** Test seams for the isolated M3 automation. */
	static FUEAgentKitGraphLayoutP11Menu* GetActiveForTest();
	FUEAgentKitEditorBridge* GetBridgeForTest() const;
	bool WaitForNativePreviewForTest(double TimeoutSeconds);
	bool WaitForNativeWorkerResponseForTest(double TimeoutSeconds);
	TSharedPtr<FJsonObject> GetLastReceiptForTest() const;
	bool IsNativePreviewInFlightForTest() const;
	int32 GetBusyRejectCountForTest() const;
	double GetLastWaitSecondsForTest() const;
	double GetLastFallbackSecondsForTest() const;

private:
	void AddContextMenuSection(UToolMenu* Menu);
	void ExecutePreviewFromContext(const FToolMenuContext& Context);
	void ExecutePreview(
		TWeakObjectPtr<UEdGraph> Graph,
		FGuid ContextNodeGuid,
		FString ContextPinName,
		bool bFromPin);
	void ExecuteLegacyPreview(
		TWeakObjectPtr<UEdGraph> Graph,
		FGuid ContextNodeGuid,
		FString ContextPinName,
		bool bFromPin,
		bool bShowMessage);
	bool StartNativePreview(
		TWeakObjectPtr<UEdGraph> Graph,
		FGuid ContextNodeGuid,
		FString ContextPinName,
		bool bFromPin);
	bool TickNativePreview(float DeltaTime);
	void CompleteNativePreview();
	void SaveNativeReceipt(
		bool bOk,
		bool bFallback,
		const FString& NativeCode,
		const TSharedPtr<FJsonObject>& LegacyReceipt);
	TSharedPtr<FJsonObject> MakeBaseReceipt() const;
	void Notify(const FString& Text) const;

	FUEAgentKitEditorBridge* Bridge = nullptr;
	bool bRegistered = false;

	TUniquePtr<FUEAgentKitNativeLayoutPreviewService> NativeService;
	FTSTicker::FDelegateHandle NativeTickerHandle;
	TWeakObjectPtr<UEdGraph> PendingGraph;
	FGuid PendingContextNodeGuid;
	FString PendingContextPinName;
	bool bPendingFromPin = false;
	bool bNativeInFlight = false;
	bool bPackageDirtyBefore = false;
	FString PendingInvokedAtUtc;
	TSharedPtr<FJsonObject> LastReceipt;
	int32 BusyRejectCount = 0;
	double LastWaitSeconds = 0.0;
	double LastFallbackSeconds = 0.0;
};
