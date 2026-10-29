#include "NativeLayoutBrokerClient.h"

#include "HAL/PlatformTime.h"
#include "IPAddress.h"
#include "SocketSubsystem.h"
#include "Sockets.h"

namespace
{
	constexpr int32 MaxMessageBytes = 1024 * 1024;
	constexpr double DeadlineSeconds = 5.0;

	FString TruncateDiagnostic(const FString& Text)
	{
		FString Clean = Text;
		Clean.ReplaceInline(TEXT("\r"), TEXT(" "));
		Clean.ReplaceInline(TEXT("\n"), TEXT(" "));
		if (Clean.Len() > 300)
		{
			Clean.LeftInline(300);
		}
		return Clean;
	}
}

FUEAgentKitLayoutBrokerClient::~FUEAgentKitLayoutBrokerClient()
{
	CancelAndWait();
}

bool FUEAgentKitLayoutBrokerClient::Start(const FString& InHost, int32 InPort, const FString& InRequestJson)
{
	if (Thread != nullptr)
	{
		return false;
	}
	if (!InHost.Equals(TEXT("127.0.0.1"), ESearchCase::CaseSensitive) || InPort < 1 || InPort > 65535)
	{
		SetTerminalError(TEXT("native-preview-broker-unavailable"), TEXT("The broker address is not loopback."));
		return false;
	}
	if (InRequestJson.IsEmpty() || InRequestJson.Len() > MaxMessageBytes)
	{
		SetTerminalError(TEXT("native-preview-request-too-large"), TEXT("The request exceeds the 1 MiB limit."));
		return false;
	}
	Host = InHost;
	Port = InPort;
	RequestJson = InRequestJson;
	bDone.store(false);
	bSuccess.store(false);
	bCancel.store(false);
	ErrorCode.Reset();
	ErrorMessage.Reset();
	ResponseJson.Reset();
	Thread = FRunnableThread::Create(this, TEXT("UEAgentKitLayoutBrokerClient"), 0, TPri_Normal);
	if (Thread == nullptr)
	{
		SetTerminalError(TEXT("native-preview-worker-failed"), TEXT("The broker worker thread could not start."));
		return false;
	}
	return true;
}

void FUEAgentKitLayoutBrokerClient::CancelAndWait()
{
	bCancel.store(true);
	if (Thread != nullptr)
	{
		Thread->WaitForCompletion();
		delete Thread;
		Thread = nullptr;
	}
}

void FUEAgentKitLayoutBrokerClient::Stop()
{
	bCancel.store(true);
}

bool FUEAgentKitLayoutBrokerClient::IsDone() const
{
	return bDone.load();
}

bool FUEAgentKitLayoutBrokerClient::IsSuccessful() const
{
	return bSuccess.load();
}

FString FUEAgentKitLayoutBrokerClient::GetResponseJson() const
{
	FScopeLock Lock(&StateLock);
	return ResponseJson;
}

FString FUEAgentKitLayoutBrokerClient::GetErrorCode() const
{
	FScopeLock Lock(&StateLock);
	return ErrorCode;
}

FString FUEAgentKitLayoutBrokerClient::GetErrorMessage() const
{
	FScopeLock Lock(&StateLock);
	return ErrorMessage;
}

bool FUEAgentKitLayoutBrokerClient::IsCancelled() const
{
	return bCancel.load();
}

void FUEAgentKitLayoutBrokerClient::SetTerminalError(const FString& Code, const FString& Message)
{
	FScopeLock Lock(&StateLock);
	if (!bDone.load())
	{
		ErrorCode = Code;
		ErrorMessage = TruncateDiagnostic(Message);
		bSuccess.store(false);
		bDone.store(true);
	}
}

uint32 FUEAgentKitLayoutBrokerClient::Run()
{
	const double StartedAt = FPlatformTime::Seconds();
	ISocketSubsystem* SocketSubsystem = ISocketSubsystem::Get(PLATFORM_SOCKETSUBSYSTEM);
	if (SocketSubsystem == nullptr)
	{
		SetTerminalError(TEXT("native-preview-worker-failed"), TEXT("The socket subsystem is unavailable."));
		return 0;
	}
	Socket = SocketSubsystem->CreateSocket(NAME_Stream, TEXT("UEAgentKitLayoutBrokerClient"), false);
	if (Socket == nullptr)
	{
		SetTerminalError(TEXT("native-preview-worker-failed"), TEXT("The broker socket could not be created."));
		return 0;
	}
	Socket->SetNoDelay(true);

	TSharedRef<FInternetAddr> Address = SocketSubsystem->CreateInternetAddr();
	bool bValidAddress = false;
	Address->SetIp(*Host, bValidAddress);
	Address->SetPort(Port);
	if (!bValidAddress)
	{
		SetTerminalError(TEXT("native-preview-broker-unavailable"), TEXT("The broker address is invalid."));
		SocketSubsystem->DestroySocket(Socket);
		Socket = nullptr;
		return 0;
	}
	if (!Socket->Connect(*Address))
	{
		SetTerminalError(TEXT("native-preview-broker-unavailable"), TEXT("The broker refused the loopback connection."));
		SocketSubsystem->DestroySocket(Socket);
		Socket = nullptr;
		return 0;
	}

	FTCHARToUTF8 Utf8(*RequestJson);
	const uint8* Data = reinterpret_cast<const uint8*>(Utf8.Get());
	int32 TotalSent = 0;
	while (TotalSent < Utf8.Length())
	{
		if (IsCancelled())
		{
			SetTerminalError(TEXT("native-preview-cancelled"), TEXT("The preview was cancelled."));
			SocketSubsystem->DestroySocket(Socket);
			Socket = nullptr;
			return 0;
		}
		if (FPlatformTime::Seconds() - StartedAt > DeadlineSeconds)
		{
			SetTerminalError(TEXT("native-preview-timeout"), TEXT("The broker request timed out."));
			SocketSubsystem->DestroySocket(Socket);
			Socket = nullptr;
			return 0;
		}
		int32 BytesSent = 0;
		if (!Socket->Send(Data + TotalSent, Utf8.Length() - TotalSent, BytesSent) || BytesSent <= 0)
		{
			SetTerminalError(TEXT("native-preview-broker-unavailable"), TEXT("The broker connection failed while sending."));
			SocketSubsystem->DestroySocket(Socket);
			Socket = nullptr;
			return 0;
		}
		TotalSent += BytesSent;
	}

	TArray<uint8> ReceiveBuffer;
	ReceiveBuffer.Reserve(64 * 1024);
	while (true)
	{
		if (IsCancelled())
		{
			SetTerminalError(TEXT("native-preview-cancelled"), TEXT("The preview was cancelled."));
			SocketSubsystem->DestroySocket(Socket);
			Socket = nullptr;
			return 0;
		}
		if (FPlatformTime::Seconds() - StartedAt > DeadlineSeconds)
		{
			SetTerminalError(TEXT("native-preview-timeout"), TEXT("The broker response timed out."));
			SocketSubsystem->DestroySocket(Socket);
			Socket = nullptr;
			return 0;
		}
		if (!Socket->Wait(ESocketWaitConditions::WaitForRead, FTimespan::FromMilliseconds(50)))
		{
			continue;
		}
		uint8 Chunk[16 * 1024];
		int32 BytesRead = 0;
		if (!Socket->Recv(Chunk, UE_ARRAY_COUNT(Chunk), BytesRead) || BytesRead <= 0)
		{
			SetTerminalError(TEXT("native-preview-response-invalid"), TEXT("The broker closed the connection without a response line."));
			SocketSubsystem->DestroySocket(Socket);
			Socket = nullptr;
			return 0;
		}
		ReceiveBuffer.Append(Chunk, BytesRead);
		if (ReceiveBuffer.Num() > MaxMessageBytes + 1)
		{
			SetTerminalError(TEXT("native-preview-response-too-large"), TEXT("The broker response exceeds the 1 MiB limit."));
			SocketSubsystem->DestroySocket(Socket);
			Socket = nullptr;
			return 0;
		}
		int32 NewlineIndex = INDEX_NONE;
		for (int32 Index = 0; Index < ReceiveBuffer.Num(); ++Index)
		{
			if (ReceiveBuffer[Index] == '\n')
			{
				NewlineIndex = Index;
				break;
			}
		}
		if (NewlineIndex == INDEX_NONE)
		{
			continue;
		}
		if (NewlineIndex > MaxMessageBytes)
		{
			SetTerminalError(TEXT("native-preview-response-too-large"), TEXT("The broker response exceeds the 1 MiB limit."));
			SocketSubsystem->DestroySocket(Socket);
			Socket = nullptr;
			return 0;
		}
		for (int32 Index = NewlineIndex + 1; Index < ReceiveBuffer.Num(); ++Index)
		{
			if (ReceiveBuffer[Index] != '\r' && ReceiveBuffer[Index] != '\n' && ReceiveBuffer[Index] != ' ' && ReceiveBuffer[Index] != '\t')
			{
				SetTerminalError(TEXT("native-preview-response-invalid"), TEXT("The broker sent more than one response line."));
				SocketSubsystem->DestroySocket(Socket);
				Socket = nullptr;
				return 0;
			}
		}
		FString Line;
		{
			FUTF8ToTCHAR Converter(reinterpret_cast<const ANSICHAR*>(ReceiveBuffer.GetData()), NewlineIndex);
			Line = FString(Converter.Length(), Converter.Get());
		}
		SocketSubsystem->DestroySocket(Socket);
		Socket = nullptr;
		{
			FScopeLock Lock(&StateLock);
			ResponseJson = Line;
			ErrorCode.Reset();
			ErrorMessage.Reset();
			bSuccess.store(true);
			bDone.store(true);
		}
		return 0;
	}
}
