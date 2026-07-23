#pragma once

#include "CoreMinimal.h"
#include "Containers/Ticker.h"

class FJsonObject;
class FSocket;

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
	};

	bool Tick(float DeltaTime);
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

	bool WriteDescriptor();
	void RemoveDescriptor();
	FString ComputeProjectPathHash() const;
	TArray<TSharedPtr<class FJsonValue>> BuildCapabilityValues() const;

	FSocket* ListenSocket = nullptr;
	TArray<FClientConnection> Clients;
	FTSTicker::FDelegateHandle TickerHandle;
	FString AuthToken;
	FString SessionId;
	FString ProjectPathHash;
	FString DescriptorPath;
	int32 ListenPort = 0;
};
