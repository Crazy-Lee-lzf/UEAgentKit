#pragma once

#include "CoreMinimal.h"

class FJsonObject;
class FUEAgentKitEditorBridge;
class FUEAgentKitLayoutBrokerClient;

struct FUEAgentKitNativeLayoutPreviewConfig
{
	bool bNativeLayoutPreview = false;
	bool bNativeToolRegistry = false;

	/** Reads [UEAgentKit] flags; both default off. Never enables the service. */
	static FUEAgentKitNativeLayoutPreviewConfig Load();
};

struct FUEAgentKitNativeLayoutPreviewOutcome
{
	bool bTerminal = false;
	bool bOk = false;
	FString Code;
	FString Message;
	TSharedPtr<FJsonObject> Plan;
	TSharedPtr<FJsonObject> Receipt;
};

/**
 * M2.2 read-only game-thread service.  It is never created by module startup
 * and has no menu.  Callers opt in explicitly (future M3); the automation test
 * uses the test seam with a real P9 snapshot.
 */
class FUEAgentKitNativeLayoutPreviewService final
{
public:
	explicit FUEAgentKitNativeLayoutPreviewService(FUEAgentKitEditorBridge* InBridge);
	~FUEAgentKitNativeLayoutPreviewService();

	/** Production-ish entry: requires the NativeLayoutPreview flag. */
	bool StartPreview();

	/** M3 menu entry: feature gating already decided by startup settings. */
	bool StartPreviewForMenu();

	/**
	 * Test seam: inject the exact P9 preflight/envelope captured on the game
	 * thread.  No UObject is retained; only JSON and identity strings are kept.
	 */
	bool StartPreviewWithSnapshot(
		const TSharedPtr<FJsonObject>& Preflight,
		const TSharedPtr<FJsonObject>& Envelope,
		bool bTestOverride);

	void Tick();
	void Cancel();
	bool IsTerminal() const;
	bool HasWorkerResponseForTest() const;
	FUEAgentKitNativeLayoutPreviewOutcome GetOutcome() const;

private:
	bool BeginFromSnapshot(const TSharedPtr<FJsonObject>& Preflight, const TSharedPtr<FJsonObject>& Envelope);
	void Fail(const FString& Code, const FString& Message);
	void Complete();
	bool ValidateResponse(const FString& ResponseJson, TSharedPtr<FJsonObject>& OutPlan, FString& OutCode, FString& OutMessage);
	bool RecheckLiveState(FString& OutCode, FString& OutMessage) const;
	void PublishReceipt(bool bOk, const FString& Code, const FString& Message, const TSharedPtr<FJsonObject>& Plan);

	FUEAgentKitEditorBridge* Bridge = nullptr;
	TUniquePtr<FUEAgentKitLayoutBrokerClient> Client;
	TSharedPtr<FJsonObject> SnapshotPreflight;
	TSharedPtr<FJsonObject> SnapshotEnvelope;
	FString AssetPath;
	FString GraphGuid;
	FString SessionId;
	FString PackageRevision;
	FString GraphFingerprint;
	int32 NodeCount = 0;
	TSet<FString> SnapshotNodeIds;
	int32 SpacingX = 96;
	int32 SpacingY = 64;
	int32 TargetWidth = 1800;
	int32 Seed = 0;
	FString RequestJson;
	FString RequestId;
	FString BrokerToken;
	FString DescriptorVersion;
	FString DescriptorAlgorithm;
	FString TerminalCode;
	FString TerminalMessage;
	FUEAgentKitNativeLayoutPreviewOutcome Outcome;
	bool bRunning = false;
	bool bTerminal = false;
};
