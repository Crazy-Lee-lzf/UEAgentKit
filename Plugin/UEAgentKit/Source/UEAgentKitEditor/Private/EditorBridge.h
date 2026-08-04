#pragma once

#include "CoreMinimal.h"
#include "Containers/Ticker.h"
#include "HAL/PlatformProcess.h"

class FJsonObject;
class FSocket;
class FUEAgentKitEditorBridgeLogCapture;
namespace UEAgentKitBatchTaskPrivate
{
	class FBatchTaskManager;
}
namespace UEAgentKitLiveWrite
{
	struct FLiveWriteTransactionRecord;
}

class FUEAgentKitEditorBridge final
{
public:
	FUEAgentKitEditorBridge();
	~FUEAgentKitEditorBridge();

	bool Start();
	void Stop();
	bool IsRunning() const;

private:
	struct FClientConnection
	{
		FSocket* Socket = nullptr;
		TArray<uint8> ReceiveBuffer;
		bool bAuthenticated = false;
		bool bCloseAfterResponse = false;
		bool bDeferredResponse = false;
	};

	struct FPendingAutomationRun
	{
		FSocket* Socket = nullptr;
		FString RequestId;
		FString TestName;
		FString StartedAtUtc;
		FString ReportDirectory;
		FString ReportPath;
		FProcHandle ProcessHandle;
		uint32 ProcessId = 0;
		int32 ExitCode = INDEX_NONE;
		double DeadlineSeconds = 0.0;
		int32 MaxEntries = 100;
		bool bActive = false;
	};

	bool Tick(float DeltaTime);
	void TickAutomationTest();
	void CancelAutomationTest();
	void AcceptConnections();
	void PumpConnections();
	void CloseConnection(int32 Index);
	void ProcessLine(FClientConnection& Client, const TArray<uint8>& LineBytes);
	bool SendResponse(FSocket* Socket, const TSharedRef<FJsonObject>& Response) const;
	void SendError(FSocket* Socket, const FString& RequestId, const FString& Code, const FString& Message) const;
	void SendResult(FSocket* Socket, const FString& RequestId, const TSharedRef<FJsonObject>& Result) const;

	TSharedRef<FJsonObject> BuildHelloResult() const;
	TSharedRef<FJsonObject> BuildStatusResult() const;
	TSharedRef<FJsonObject> BuildSelectionResult() const;
	TSharedRef<FJsonObject> BuildOpenAssetsResult() const;
	TSharedRef<FJsonObject> BuildDirtyAssetsResult() const;
	TSharedRef<FJsonObject> BuildCurrentLevelResult() const;
	TSharedRef<FJsonObject> BuildPieStateResult() const;
	TSharedRef<FJsonObject> BuildOutputLogResult(const TSharedPtr<FJsonObject>& Params) const;
	TSharedRef<FJsonObject> BuildCompileErrorsResult(const TSharedPtr<FJsonObject>& Params) const;
	TSharedRef<FJsonObject> BuildInspectAssetLiveResult(const FString& AssetPath) const;
	TSharedRef<FJsonObject> BuildBlueprintGraphSelectionResult() const;
	TSharedRef<FJsonObject> BuildEditorContextResult() const;
	bool TryStartBatchTask(
		const FString& Operation,
		int32 MaxActors,
		int32 MaxComponentsPerActor,
		int32 TimeoutSeconds,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage);
	bool BuildBatchTaskStatusResult(
		const FString& TaskId,
		bool bIncludeDetails,
		int32 DetailOffset,
		int32 DetailLimit,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage);
	bool BuildBatchTaskCancelResult(
		const FString& TaskId,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	bool TryOpenAssetResult(const FString& AssetPath, TSharedPtr<FJsonObject>& OutResult, FString& OutErrorCode, FString& OutErrorMessage) const;
	bool TryFocusAssetResult(const FString& AssetPath, TSharedPtr<FJsonObject>& OutResult, FString& OutErrorCode, FString& OutErrorMessage) const;
	bool TrySyncContentBrowserResult(const FString& AssetPath, TSharedPtr<FJsonObject>& OutResult, FString& OutErrorCode, FString& OutErrorMessage) const;
	bool TryFocusActorResult(const FString& ActorGuid, TSharedPtr<FJsonObject>& OutResult, FString& OutErrorCode, FString& OutErrorMessage) const;
	bool TryCompileBlueprintResult(const FString& AssetPath, TSharedPtr<FJsonObject>& OutResult, FString& OutErrorCode, FString& OutErrorMessage) const;
	bool TryValidateAssetResult(const FString& AssetPath, int32 MaxIssues, TSharedPtr<FJsonObject>& OutResult, FString& OutErrorCode, FString& OutErrorMessage) const;
	bool TryValidateFolderResult(const FString& PackagePath, bool bRecursive, int32 MaxAssets, int32 MaxIssues, TSharedPtr<FJsonObject>& OutResult, FString& OutErrorCode, FString& OutErrorMessage) const;
	bool TrySaveAuthorizedAssetResult(const FString& AssetPath, TSharedPtr<FJsonObject>& OutResult, FString& OutErrorCode, FString& OutErrorMessage) const;
	bool TryAnalyzeAnimationRetargetResult(
		const FString& SourceMeshPath,
		const FString& TargetMeshPath,
		bool bIncludeOptionalChains,
		int32 MaxBoneDetails,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage) const;
	bool TryPlanAnimationRetargetResult(
		const FString& SourceMeshPath,
		const FString& TargetMeshPath,
		bool bIncludeOptionalChains,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage) const;
	bool TryApplyAnimationRetargetSetupResult(
		const FString& SourceMeshPath,
		const FString& TargetMeshPath,
		const FString& SourceRigName,
		const FString& TargetRigName,
		const FString& SourceRetargetRoot,
		const FString& TargetRetargetRoot,
		const TArray<TSharedPtr<FJsonValue>>& SourceChains,
		const TArray<TSharedPtr<FJsonValue>>& TargetChains,
		bool bUpdateExisting,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage) const;
	bool TryApplyAssetPropertyLiveResult(
		const FString& Operation,
		const FString& AssetPath,
		const TSharedPtr<FJsonObject>& Target,
		const TSharedPtr<class FJsonValue>& Value,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage) const;
	bool TryUndoAssetPropertyLiveResult(
		const FString& AssetPath,
		const FString& TransactionId,
		const FString& ExpectedSessionId,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage) const;
	bool TryDiscardAssetPropertyLiveResult(
		const FString& AssetPath,
		const FString& TransactionId,
		const FString& ExpectedSessionId,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage) const;
	bool RevertLiveWriteTransaction(
		const bool bRedoable,
		const FString& AssetPath,
		const FString& TransactionId,
		const FString& ExpectedSessionId,
		const FString& Action,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage) const;
	bool TryPrepareAuthorizedSaveFixtureResult(const FString& AssetPath, TSharedPtr<FJsonObject>& OutResult, FString& OutErrorCode, FString& OutErrorMessage) const;
	bool TryStartAutomationTest(
		const FString& TestName,
		int32 TimeoutSeconds,
		int32 MaxEntries,
		FSocket* Socket,
		const FString& RequestId,
		FString& OutErrorCode,
		FString& OutErrorMessage);
	void CompleteAutomationTest(bool bTimedOut);

	bool WriteDescriptor();
	void RemoveDescriptor();
	FString ComputeProjectPathHash() const;
	TSharedRef<FJsonObject> BuildValidationEvidence(
		const FString& Scope,
		const FString& StartedAtUtc,
		const FString& CompletedAtUtc,
		const FString& RevisionCoverage) const;
	TArray<TSharedPtr<class FJsonValue>> BuildCapabilityValues() const;

	FSocket* ListenSocket = nullptr;
	TArray<FClientConnection> Clients;
	FTSTicker::FDelegateHandle TickerHandle;
	FString AuthToken;
	FString SessionId;
	FString ProjectPathHash;
	FString DescriptorPath;
	int32 ListenPort = 0;
	TUniquePtr<FUEAgentKitEditorBridgeLogCapture> LogCapture;
	// One frame-stepped Batch Task at a time; cleared on bridge start.
	TUniquePtr<UEAgentKitBatchTaskPrivate::FBatchTaskManager> BatchTaskManager;
	FPendingAutomationRun PendingAutomation;
	// Confirmed live write transactions per exact asset path; cleared on bridge start.
	mutable TMap<FString, TMap<FGuid, TSharedPtr<UEAgentKitLiveWrite::FLiveWriteTransactionRecord>>> LiveWriteTransactionRecords;
};
