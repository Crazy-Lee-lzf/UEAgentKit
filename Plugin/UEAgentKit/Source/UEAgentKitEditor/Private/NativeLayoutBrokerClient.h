#pragma once

#include "CoreMinimal.h"
#include "HAL/Runnable.h"

#include <atomic>

class FSocket;

/** Worker-only outbound loopback client for the M1 layout broker. */
class FUEAgentKitLayoutBrokerClient final : public FRunnable
{
public:
	FUEAgentKitLayoutBrokerClient() = default;
	virtual ~FUEAgentKitLayoutBrokerClient() override;

	bool Start(const FString& InHost, int32 InPort, const FString& InRequestJson);
	void CancelAndWait();

	bool IsDone() const;
	bool IsSuccessful() const;
	FString GetResponseJson() const;
	FString GetErrorCode() const;
	FString GetErrorMessage() const;

	// FRunnable
	virtual uint32 Run() override;
	virtual void Stop() override;

private:
	void SetTerminalError(const FString& Code, const FString& Message);
	bool IsCancelled() const;

	FString Host;
	int32 Port = 0;
	FString RequestJson;
	FSocket* Socket = nullptr;
	FRunnableThread* Thread = nullptr;
	mutable FCriticalSection StateLock;
	FString ResponseJson;
	FString ErrorCode;
	FString ErrorMessage;
	std::atomic<bool> bDone{false};
	std::atomic<bool> bSuccess{false};
	std::atomic<bool> bCancel{false};
};
