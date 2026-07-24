#include "EditorBridge.h"
#include "EditorBridgeLogCapture.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "BlueprintEditor.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "Components/ActorComponent.h"
#include "Editor.h"
#include "Engine/Blueprint.h"
#include "Engine/Selection.h"
#include "Engine/World.h"
#include "GameFramework/Actor.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformMisc.h"
#include "HAL/PlatformProcess.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "Misc/App.h"
#include "Misc/DateTime.h"
#include "Misc/EngineVersion.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Misc/SecureHash.h"
#include "Modules/ModuleManager.h"
#include "SocketSubsystem.h"
#include "Sockets.h"
#include "Subsystems/AssetEditorSubsystem.h"
#include "UObject/Package.h"
#include "UObject/SoftObjectPath.h"
#include "UObject/UObjectGlobals.h"
#include "UObject/UObjectIterator.h"

DEFINE_LOG_CATEGORY_STATIC(LogUEAgentKitEditorBridge, Log, All);

namespace UEAgentKitEditorBridgePrivate
{
	constexpr const TCHAR* ProtocolSchemaVersion = TEXT("1.0");
	constexpr const TCHAR* PluginVersion = TEXT("0.5.1");
	constexpr int32 MaxClients = 8;
	constexpr int32 MaxRequestBytes = 64 * 1024;
	constexpr int32 MaxSelectionItems = 200;
	constexpr int32 MaxOpenAssets = 200;
	constexpr int32 MaxDirtyPackages = 200;

	const TCHAR* Capabilities[] = {
		TEXT("editor.status"),
		TEXT("editor.getSelection"),
		TEXT("editor.getOpenAssets"),
		TEXT("editor.getDirtyAssets"),
		TEXT("editor.getCurrentLevel"),
		TEXT("editor.getPieState"),
		TEXT("editor.getOutputLog"),
		TEXT("editor.getCompileErrors"),
		TEXT("editor.inspectAssetLive"),
		TEXT("editor.getBlueprintGraphSelection")
	};

	FString NormalizeProjectPath()
	{
		FString ProjectPath = FPaths::ConvertRelativePathToFull(FPaths::GetProjectFilePath());
		FPaths::NormalizeFilename(ProjectPath);
		ProjectPath.ToLowerInline();
		return ProjectPath;
	}

	FString HashUtf8(const FString& Value)
	{
		FTCHARToUTF8 Utf8(*Value);
		uint8 Hash[FSHA1::DigestSize];
		FSHA1::HashBuffer(Utf8.Get(), Utf8.Length(), Hash);
		return FString::Printf(TEXT("sha1:%s"), *BytesToHex(Hash, UE_ARRAY_COUNT(Hash)).ToLower());
	}

	FString GetWorldTypeName(const EWorldType::Type WorldType)
	{
		switch (WorldType)
		{
		case EWorldType::Editor:
			return TEXT("Editor");
		case EWorldType::EditorPreview:
			return TEXT("EditorPreview");
		case EWorldType::PIE:
			return TEXT("PIE");
		case EWorldType::Game:
			return TEXT("Game");
		case EWorldType::GamePreview:
			return TEXT("GamePreview");
		case EWorldType::Inactive:
			return TEXT("Inactive");
		default:
			return TEXT("Other");
		}
	}

	FString GetPieStateName()
	{
		if (GEditor == nullptr || GEditor->PlayWorld == nullptr)
		{
			return TEXT("stopped");
		}
		return GEditor->bIsSimulatingInEditor ? TEXT("simulating") : TEXT("playing");
	}

	UWorld* GetEditorWorld()
	{
		return GEditor != nullptr ? GEditor->GetEditorWorldContext().World() : nullptr;
	}

	TSharedRef<FJsonObject> DescribeObject(UObject* Object, const FString& Kind)
	{
		TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
		Item->SetStringField(TEXT("kind"), Kind);
		Item->SetStringField(TEXT("name"), Object != nullptr ? Object->GetName() : FString());
		Item->SetStringField(TEXT("objectPath"), Object != nullptr ? Object->GetPathName() : FString());
		Item->SetStringField(TEXT("classPath"), Object != nullptr && Object->GetClass() != nullptr ? Object->GetClass()->GetPathName() : FString());
		UPackage* Package = Object != nullptr ? Object->GetOutermost() : nullptr;
		Item->SetStringField(TEXT("packageName"), Package != nullptr ? Package->GetName() : FString());
		Item->SetBoolField(TEXT("packageDirty"), Package != nullptr && Package->IsDirty());
		if (const AActor* Actor = Cast<AActor>(Object))
		{
			Item->SetStringField(TEXT("label"), Actor->GetActorLabel());
			Item->SetStringField(TEXT("levelPath"), Actor->GetLevel() != nullptr ? Actor->GetLevel()->GetPathName() : FString());
		}
		if (const UActorComponent* Component = Cast<UActorComponent>(Object))
		{
			Item->SetStringField(TEXT("ownerPath"), Component->GetOwner() != nullptr ? Component->GetOwner()->GetPathName() : FString());
		}
		return Item;
	}

	int32 CountDirtyGamePackages()
	{
		int32 Count = 0;
		for (TObjectIterator<UPackage> It; It; ++It)
		{
			const FString PackageName = It->GetName();
			if (It->IsDirty() && PackageName.StartsWith(TEXT("/Game/")))
			{
				++Count;
			}
		}
		return Count;
	}

	FString GetBlueprintStatusName(const EBlueprintStatus Status)
	{
		switch (Status)
		{
		case BS_Dirty:
			return TEXT("dirty");
		case BS_Error:
			return TEXT("error");
		case BS_UpToDate:
			return TEXT("up-to-date");
		case BS_BeingCreated:
			return TEXT("being-created");
		case BS_UpToDateWithWarnings:
			return TEXT("up-to-date-with-warnings");
		case BS_Unknown:
		default:
			return TEXT("unknown");
		}
	}

	FString GetBlueprintTypeName(const EBlueprintType BlueprintType)
	{
		switch (BlueprintType)
		{
		case BPTYPE_Const:
			return TEXT("const");
		case BPTYPE_MacroLibrary:
			return TEXT("macro-library");
		case BPTYPE_Interface:
			return TEXT("interface");
		case BPTYPE_LevelScript:
			return TEXT("level-script");
		case BPTYPE_FunctionLibrary:
			return TEXT("function-library");
		case BPTYPE_Normal:
		default:
			return TEXT("normal");
		}
	}

	ELogVerbosity::Type ParseMinimumVerbosity(const FString& Value)
	{
		if (Value.Equals(TEXT("fatal"), ESearchCase::IgnoreCase))
		{
			return ELogVerbosity::Fatal;
		}
		if (Value.Equals(TEXT("error"), ESearchCase::IgnoreCase))
		{
			return ELogVerbosity::Error;
		}
		if (Value.Equals(TEXT("warning"), ESearchCase::IgnoreCase))
		{
			return ELogVerbosity::Warning;
		}
		if (Value.Equals(TEXT("display"), ESearchCase::IgnoreCase))
		{
			return ELogVerbosity::Display;
		}
		if (Value.Equals(TEXT("verbose"), ESearchCase::IgnoreCase))
		{
			return ELogVerbosity::Verbose;
		}
		if (Value.Equals(TEXT("veryverbose"), ESearchCase::IgnoreCase))
		{
			return ELogVerbosity::VeryVerbose;
		}
		return ELogVerbosity::Log;
	}

	FUEAgentKitLogQuery BuildLogQuery(const TSharedPtr<FJsonObject>& Params, const bool bCompileOnly)
	{
		FUEAgentKitLogQuery Query;
		Query.bCompileOnly = bCompileOnly;
		if (!Params.IsValid())
		{
			if (bCompileOnly)
			{
				Query.MinimumVerbosity = ELogVerbosity::Warning;
			}
			return Query;
		}
		Params->TryGetStringField(TEXT("category"), Query.Category);
		Params->TryGetStringField(TEXT("keyword"), Query.Keyword);
		Params->TryGetStringField(TEXT("assetPath"), Query.AssetFilter);
		Query.Category.LeftInline(128, EAllowShrinking::No);
		Query.Keyword.LeftInline(256, EAllowShrinking::No);
		Query.AssetFilter.LeftInline(512, EAllowShrinking::No);
		FString MinimumVerbosity;
		if (Params->TryGetStringField(TEXT("minimumVerbosity"), MinimumVerbosity))
		{
			Query.MinimumVerbosity = ParseMinimumVerbosity(MinimumVerbosity);
		}
		double NumberValue = 0.0;
		if (Params->TryGetNumberField(TEXT("sinceSequence"), NumberValue))
		{
			Query.SinceSequence = static_cast<uint64>(FMath::Max(0.0, NumberValue));
		}
		if (Params->TryGetNumberField(TEXT("pieSessionId"), NumberValue))
		{
			Query.PieSessionId = FMath::Max(-1, static_cast<int32>(NumberValue));
		}
		if (Params->TryGetNumberField(TEXT("limit"), NumberValue))
		{
			Query.Limit = FMath::Clamp(static_cast<int32>(NumberValue), 1, 100);
		}
		FString Timestamp;
		FDateTime ParsedTimestamp;
		if (Params->TryGetStringField(TEXT("sinceUtc"), Timestamp) && FDateTime::ParseIso8601(*Timestamp, ParsedTimestamp))
		{
			Query.SinceUtc = ParsedTimestamp;
		}
		if (Params->TryGetStringField(TEXT("untilUtc"), Timestamp) && FDateTime::ParseIso8601(*Timestamp, ParsedTimestamp))
		{
			Query.UntilUtc = ParsedTimestamp;
		}
		if (bCompileOnly)
		{
			Query.MinimumVerbosity = ELogVerbosity::Warning;
		}
		return Query;
	}

	TSharedRef<FJsonObject> DescribeCapturedLogEntry(const FUEAgentKitCapturedLogEntry& Entry)
	{
		TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
		Item->SetNumberField(TEXT("sequence"), static_cast<double>(Entry.Sequence));
		Item->SetStringField(TEXT("timestampUtc"), Entry.TimestampUtc.ToIso8601());
		Item->SetStringField(TEXT("category"), Entry.Category);
		Item->SetStringField(TEXT("verbosity"), ToString(Entry.Verbosity));
		Item->SetStringField(TEXT("message"), Entry.Message);
		Item->SetNumberField(TEXT("threadId"), Entry.ThreadId);
		Item->SetNumberField(TEXT("pieSessionId"), Entry.PieSessionId);
		Item->SetStringField(TEXT("pieState"), Entry.PieState);
		Item->SetBoolField(TEXT("fromBacklog"), Entry.bFromBacklog);
		Item->SetBoolField(TEXT("messageTruncated"), Entry.bMessageTruncated);
		return Item;
	}

	TSharedRef<FJsonObject> DescribeBlueprintState(UBlueprint* Blueprint)
	{
		TSharedRef<FJsonObject> Item = DescribeObject(Blueprint, TEXT("Blueprint"));
		if (Blueprint == nullptr)
		{
			return Item;
		}
		Item->SetStringField(TEXT("blueprintStatus"), GetBlueprintStatusName(Blueprint->Status));
		Item->SetStringField(TEXT("blueprintType"), GetBlueprintTypeName(Blueprint->BlueprintType));
		Item->SetBoolField(TEXT("upToDate"), Blueprint->IsUpToDate());
		Item->SetBoolField(TEXT("possiblyDirty"), Blueprint->IsPossiblyDirty());
		Item->SetStringField(TEXT("generatedClassPath"), Blueprint->GeneratedClass != nullptr ? Blueprint->GeneratedClass->GetPathName() : FString());
		Item->SetStringField(TEXT("skeletonGeneratedClassPath"), Blueprint->SkeletonGeneratedClass != nullptr ? Blueprint->SkeletonGeneratedClass->GetPathName() : FString());
		Item->SetStringField(TEXT("parentClassPath"), Blueprint->ParentClass != nullptr ? Blueprint->ParentClass->GetPathName() : FString());
		Item->SetNumberField(TEXT("variableCount"), Blueprint->NewVariables.Num());
		Item->SetNumberField(TEXT("ubergraphCount"), Blueprint->UbergraphPages.Num());
		Item->SetNumberField(TEXT("functionGraphCount"), Blueprint->FunctionGraphs.Num());
		Item->SetNumberField(TEXT("macroGraphCount"), Blueprint->MacroGraphs.Num());
		return Item;
	}

	bool IsSafeGameAssetPath(const FString& AssetPath)
	{
		if (
			AssetPath.IsEmpty() ||
			AssetPath.Len() > 512 ||
			!AssetPath.StartsWith(TEXT("/Game/"), ESearchCase::CaseSensitive) ||
			AssetPath.Contains(TEXT("\\")) ||
			AssetPath.Contains(TEXT(":")) ||
			AssetPath.Contains(TEXT("..")))
		{
			return false;
		}
		for (const TCHAR Character : AssetPath)
		{
			if (Character < 32)
			{
				return false;
			}
		}
		int32 SlashIndex = INDEX_NONE;
		int32 DotIndex = INDEX_NONE;
		if (
			!AssetPath.FindLastChar(TEXT('/'), SlashIndex) ||
			!AssetPath.FindLastChar(TEXT('.'), DotIndex) ||
			DotIndex <= SlashIndex + 1 ||
			DotIndex >= AssetPath.Len() - 1)
		{
			return false;
		}
		const FSoftObjectPath ObjectPath(AssetPath);
		return ObjectPath.IsValid() && ObjectPath.GetSubPathString().IsEmpty();
	}

	bool IsObjectSelected(UObject* Object)
	{
		if (GEditor == nullptr || Object == nullptr)
		{
			return false;
		}
		if (USelection* SelectedObjects = GEditor->GetSelectedObjects())
		{
			for (FSelectionIterator It(*SelectedObjects); It; ++It)
			{
				if (*It == Object)
				{
					return true;
				}
			}
		}
		return false;
	}

	bool IsAssetOpenInEditor(UObject* Asset)
	{
		if (GEditor == nullptr || Asset == nullptr)
		{
			return false;
		}
		if (UAssetEditorSubsystem* AssetEditorSubsystem = GEditor->GetEditorSubsystem<UAssetEditorSubsystem>())
		{
			for (UObject* OpenAsset : AssetEditorSubsystem->GetAllEditedAssets())
			{
				if (OpenAsset == Asset)
				{
					return true;
				}
			}
		}
		return false;
	}
}

using namespace UEAgentKitEditorBridgePrivate;

FUEAgentKitEditorBridge::FUEAgentKitEditorBridge() = default;

FUEAgentKitEditorBridge::~FUEAgentKitEditorBridge()
{
	Stop();
}

bool FUEAgentKitEditorBridge::Start()
{
	if (ListenSocket != nullptr)
	{
		return true;
	}
	if (FPlatformMisc::GetEnvironmentVariable(TEXT("UE_AGENT_KIT_DISABLE_EDITOR_BRIDGE")) == TEXT("1"))
	{
		UE_LOG(LogUEAgentKitEditorBridge, Display, TEXT("Editor Bridge disabled by UE_AGENT_KIT_DISABLE_EDITOR_BRIDGE."));
		return false;
	}
	if (FPaths::GetProjectFilePath().IsEmpty())
	{
		UE_LOG(LogUEAgentKitEditorBridge, Warning, TEXT("Editor Bridge requires a project file."));
		return false;
	}

	ISocketSubsystem* SocketSubsystem = ISocketSubsystem::Get(PLATFORM_SOCKETSUBSYSTEM);
	if (SocketSubsystem == nullptr)
	{
		UE_LOG(LogUEAgentKitEditorBridge, Error, TEXT("Socket subsystem is unavailable."));
		return false;
	}

	ListenSocket = SocketSubsystem->CreateSocket(NAME_Stream, TEXT("UEAgentKitEditorBridge"), false);
	if (ListenSocket == nullptr)
	{
		UE_LOG(LogUEAgentKitEditorBridge, Error, TEXT("Failed to create the Editor Bridge listen socket."));
		return false;
	}
	ListenSocket->SetReuseAddr(false);
	ListenSocket->SetNonBlocking(true);

	TSharedRef<FInternetAddr> ListenAddress = SocketSubsystem->CreateInternetAddr();
	bool bAddressValid = false;
	ListenAddress->SetIp(TEXT("127.0.0.1"), bAddressValid);
	ListenAddress->SetPort(0);
	if (!bAddressValid || !ListenSocket->Bind(*ListenAddress) || !ListenSocket->Listen(MaxClients))
	{
		UE_LOG(LogUEAgentKitEditorBridge, Error, TEXT("Failed to bind the Editor Bridge to localhost."));
		Stop();
		return false;
	}

	TSharedRef<FInternetAddr> BoundAddress = SocketSubsystem->CreateInternetAddr();
	ListenSocket->GetAddress(*BoundAddress);
	ListenPort = BoundAddress->GetPort();
	if (ListenPort <= 0)
	{
		UE_LOG(LogUEAgentKitEditorBridge, Error, TEXT("Failed to read the Editor Bridge bound port."));
		Stop();
		return false;
	}
	AuthToken = FGuid::NewGuid().ToString(EGuidFormats::Digits) + FGuid::NewGuid().ToString(EGuidFormats::Digits);
	SessionId = FGuid::NewGuid().ToString(EGuidFormats::DigitsWithHyphensLower);
	ProjectPathHash = ComputeProjectPathHash();
	LogCapture = MakeUnique<FUEAgentKitEditorBridgeLogCapture>();
	LogCapture->Start();
	DescriptorPath = FPaths::Combine(FPaths::ProjectSavedDir(), TEXT("UEAgentKit"), TEXT("EditorBridge.json"));
	if (!WriteDescriptor())
	{
		Stop();
		return false;
	}

	TickerHandle = FTSTicker::GetCoreTicker().AddTicker(
		FTickerDelegate::CreateRaw(this, &FUEAgentKitEditorBridge::Tick),
		0.01f);
	UE_LOG(
		LogUEAgentKitEditorBridge,
		Display,
		TEXT("Editor Bridge listening on localhost for project %s."),
		FApp::GetProjectName());
	return true;
}

void FUEAgentKitEditorBridge::Stop()
{
	if (TickerHandle.IsValid())
	{
		FTSTicker::GetCoreTicker().RemoveTicker(TickerHandle);
		TickerHandle.Reset();
	}
	ISocketSubsystem* SocketSubsystem = ISocketSubsystem::Get(PLATFORM_SOCKETSUBSYSTEM);
	for (FClientConnection& Client : Clients)
	{
		if (Client.Socket != nullptr)
		{
			Client.Socket->Close();
			if (SocketSubsystem != nullptr)
			{
				SocketSubsystem->DestroySocket(Client.Socket);
			}
		}
	}
	Clients.Reset();
	if (ListenSocket != nullptr)
	{
		ListenSocket->Close();
		if (SocketSubsystem != nullptr)
		{
			SocketSubsystem->DestroySocket(ListenSocket);
		}
		ListenSocket = nullptr;
	}
	if (LogCapture.IsValid())
	{
		LogCapture->Stop();
		LogCapture.Reset();
	}
	RemoveDescriptor();
	ListenPort = 0;
	AuthToken.Reset();
	SessionId.Reset();
	ProjectPathHash.Reset();
}

bool FUEAgentKitEditorBridge::IsRunning() const
{
	return ListenSocket != nullptr;
}

bool FUEAgentKitEditorBridge::Tick(float DeltaTime)
{
	(void)DeltaTime;
	AcceptConnections();
	PumpConnections();
	return true;
}

void FUEAgentKitEditorBridge::AcceptConnections()
{
	if (ListenSocket == nullptr)
	{
		return;
	}
	bool bPendingConnection = false;
	while (Clients.Num() < MaxClients && ListenSocket->HasPendingConnection(bPendingConnection) && bPendingConnection)
	{
		FSocket* ClientSocket = ListenSocket->Accept(TEXT("UEAgentKitEditorBridgeClient"));
		if (ClientSocket == nullptr)
		{
			break;
		}
		ClientSocket->SetNonBlocking(true);
		ClientSocket->SetNoDelay(true);
		FClientConnection& Client = Clients.AddDefaulted_GetRef();
		Client.Socket = ClientSocket;
	}
}

void FUEAgentKitEditorBridge::PumpConnections()
{
	for (int32 Index = Clients.Num() - 1; Index >= 0; --Index)
	{
		FClientConnection& Client = Clients[Index];
		bool bClose = Client.Socket == nullptr;
		if (!bClose)
		{
			uint32 PendingBytes = 0;
			while (Client.Socket->HasPendingData(PendingBytes) && PendingBytes > 0)
			{
				const int32 ReadSize = FMath::Min<int32>(static_cast<int32>(PendingBytes), 16 * 1024);
				TArray<uint8> Temporary;
				Temporary.SetNumUninitialized(ReadSize);
				int32 BytesRead = 0;
				if (!Client.Socket->Recv(Temporary.GetData(), Temporary.Num(), BytesRead) || BytesRead <= 0)
				{
					bClose = true;
					break;
				}
				Client.ReceiveBuffer.Append(Temporary.GetData(), BytesRead);
				if (Client.ReceiveBuffer.Num() > MaxRequestBytes)
				{
					SendError(Client.Socket, FString(), TEXT("request-too-large"), TEXT("Editor Bridge request exceeded the size limit."));
					bClose = true;
					break;
				}
			}
		}

		while (!bClose)
		{
			const int32 NewlineIndex = Client.ReceiveBuffer.IndexOfByKey(static_cast<uint8>('\n'));
			if (NewlineIndex == INDEX_NONE)
			{
				break;
			}
			TArray<uint8> LineBytes;
			LineBytes.Append(Client.ReceiveBuffer.GetData(), NewlineIndex);
			Client.ReceiveBuffer.RemoveAt(0, NewlineIndex + 1, EAllowShrinking::No);
			if (!LineBytes.IsEmpty() && LineBytes.Last() == static_cast<uint8>('\r'))
			{
				LineBytes.Pop(EAllowShrinking::No);
			}
			if (!LineBytes.IsEmpty())
			{
				ProcessLine(Client, LineBytes);
				if (Client.bCloseAfterResponse)
				{
					bClose = true;
					break;
				}
			}
		}

		if (!bClose && Client.Socket->GetConnectionState() == SCS_ConnectionError)
		{
			bClose = true;
		}
		if (bClose)
		{
			CloseConnection(Index);
		}
	}
}

void FUEAgentKitEditorBridge::CloseConnection(const int32 Index)
{
	if (!Clients.IsValidIndex(Index))
	{
		return;
	}
	ISocketSubsystem* SocketSubsystem = ISocketSubsystem::Get(PLATFORM_SOCKETSUBSYSTEM);
	if (Clients[Index].Socket != nullptr)
	{
		Clients[Index].Socket->Close();
		if (SocketSubsystem != nullptr)
		{
			SocketSubsystem->DestroySocket(Clients[Index].Socket);
		}
	}
	Clients.RemoveAtSwap(Index, 1, EAllowShrinking::No);
}

void FUEAgentKitEditorBridge::ProcessLine(FClientConnection& Client, const TArray<uint8>& LineBytes)
{
	Client.bCloseAfterResponse = true;
	FUTF8ToTCHAR Converted(reinterpret_cast<const ANSICHAR*>(LineBytes.GetData()), LineBytes.Num());
	const FString JsonText(Converted.Length(), Converted.Get());
	TSharedPtr<FJsonObject> Request;
	const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(JsonText);
	if (!FJsonSerializer::Deserialize(Reader, Request) || !Request.IsValid())
	{
		SendError(Client.Socket, FString(), TEXT("invalid-json"), TEXT("Editor Bridge request is not valid JSON."));
		return;
	}

	FString RequestId;
	FString SchemaVersion;
	FString Method;
	Request->TryGetStringField(TEXT("requestId"), RequestId);
	Request->TryGetStringField(TEXT("schemaVersion"), SchemaVersion);
	Request->TryGetStringField(TEXT("method"), Method);
	if (RequestId.IsEmpty() || SchemaVersion != ProtocolSchemaVersion || Method.IsEmpty())
	{
		SendError(Client.Socket, RequestId, TEXT("invalid-request"), TEXT("Editor Bridge request fields are invalid."));
		return;
	}

	if (Method == TEXT("hello"))
	{
		FString RequestToken;
		FString ServerVersion;
		FString RequestProjectHash;
		Request->TryGetStringField(TEXT("authToken"), RequestToken);
		Request->TryGetStringField(TEXT("serverVersion"), ServerVersion);
		Request->TryGetStringField(TEXT("projectPathHash"), RequestProjectHash);
		if (RequestToken != AuthToken)
		{
			SendError(Client.Socket, RequestId, TEXT("live-editor-authentication-failed"), TEXT("Editor Bridge authentication failed."));
			return;
		}
		if (ServerVersion != PluginVersion)
		{
			SendError(Client.Socket, RequestId, TEXT("live-editor-version-mismatch"), TEXT("Editor Bridge and MCP Server versions do not match."));
			return;
		}
		if (RequestProjectHash != ProjectPathHash)
		{
			SendError(Client.Socket, RequestId, TEXT("live-editor-project-mismatch"), TEXT("Editor Bridge project identity does not match."));
			return;
		}
		Client.bAuthenticated = true;
		Client.bCloseAfterResponse = false;
		SendResult(Client.Socket, RequestId, BuildHelloResult());
		return;
	}

	if (!Client.bAuthenticated)
	{
		SendError(Client.Socket, RequestId, TEXT("live-editor-authentication-required"), TEXT("Call hello before using Editor Bridge capabilities."));
		return;
	}

	TSharedPtr<FJsonObject> Params = MakeShared<FJsonObject>();
	const TSharedPtr<FJsonObject>* ParamsField = nullptr;
	if (Request->TryGetObjectField(TEXT("params"), ParamsField) && ParamsField != nullptr && ParamsField->IsValid())
	{
		Params = *ParamsField;
	}

	if (Method == TEXT("editor.status"))
	{
		SendResult(Client.Socket, RequestId, BuildStatusResult());
	}
	else if (Method == TEXT("editor.getSelection"))
	{
		SendResult(Client.Socket, RequestId, BuildSelectionResult());
	}
	else if (Method == TEXT("editor.getOpenAssets"))
	{
		SendResult(Client.Socket, RequestId, BuildOpenAssetsResult());
	}
	else if (Method == TEXT("editor.getDirtyAssets"))
	{
		SendResult(Client.Socket, RequestId, BuildDirtyAssetsResult());
	}
	else if (Method == TEXT("editor.getCurrentLevel"))
	{
		SendResult(Client.Socket, RequestId, BuildCurrentLevelResult());
	}
	else if (Method == TEXT("editor.getPieState"))
	{
		SendResult(Client.Socket, RequestId, BuildPieStateResult());
	}
	else if (Method == TEXT("editor.getOutputLog"))
	{
		SendResult(Client.Socket, RequestId, BuildOutputLogResult(Params));
	}
	else if (Method == TEXT("editor.getCompileErrors"))
	{
		SendResult(Client.Socket, RequestId, BuildCompileErrorsResult(Params));
	}
	else if (Method == TEXT("editor.inspectAssetLive"))
	{
		FString AssetPath;
		Params->TryGetStringField(TEXT("assetPath"), AssetPath);
		if (!UEAgentKitEditorBridgePrivate::IsSafeGameAssetPath(AssetPath))
		{
			SendError(Client.Socket, RequestId, TEXT("live-editor-invalid-parameters"), TEXT("assetPath must be an exact /Game Object Path."));
		}
		else
		{
			SendResult(Client.Socket, RequestId, BuildInspectAssetLiveResult(AssetPath));
		}
	}
	else if (Method == TEXT("editor.getBlueprintGraphSelection"))
	{
		SendResult(Client.Socket, RequestId, BuildBlueprintGraphSelectionResult());
	}
	else
	{
		SendError(Client.Socket, RequestId, TEXT("live-editor-capability-unavailable"), TEXT("The requested Editor Bridge capability is not registered."));
	}
	Client.bCloseAfterResponse = true;
}

bool FUEAgentKitEditorBridge::SendResponse(FSocket* Socket, const TSharedRef<FJsonObject>& Response) const
{
	if (Socket == nullptr)
	{
		return false;
	}
	FString JsonText;
	const TSharedRef<TJsonWriter<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>> Writer =
		TJsonWriterFactory<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>::Create(&JsonText);
	if (!FJsonSerializer::Serialize(Response, Writer))
	{
		return false;
	}
	JsonText.AppendChar(TEXT('\n'));
	FTCHARToUTF8 Utf8(*JsonText);
	int32 TotalSent = 0;
	while (TotalSent < Utf8.Length())
	{
		int32 BytesSent = 0;
		if (!Socket->Send(
			reinterpret_cast<const uint8*>(Utf8.Get()) + TotalSent,
			Utf8.Length() - TotalSent,
			BytesSent) || BytesSent <= 0)
		{
			return false;
		}
		TotalSent += BytesSent;
	}
	return true;
}

void FUEAgentKitEditorBridge::SendError(
	FSocket* Socket,
	const FString& RequestId,
	const FString& Code,
	const FString& Message) const
{
	TSharedRef<FJsonObject> Error = MakeShared<FJsonObject>();
	Error->SetStringField(TEXT("code"), Code);
	Error->SetStringField(TEXT("message"), Message);
	TSharedRef<FJsonObject> Response = MakeShared<FJsonObject>();
	Response->SetStringField(TEXT("schemaVersion"), ProtocolSchemaVersion);
	Response->SetStringField(TEXT("requestId"), RequestId);
	Response->SetBoolField(TEXT("ok"), false);
	Response->SetObjectField(TEXT("error"), Error);
	SendResponse(Socket, Response);
}

void FUEAgentKitEditorBridge::SendResult(
	FSocket* Socket,
	const FString& RequestId,
	const TSharedRef<FJsonObject>& Result) const
{
	TSharedRef<FJsonObject> Response = MakeShared<FJsonObject>();
	Response->SetStringField(TEXT("schemaVersion"), ProtocolSchemaVersion);
	Response->SetStringField(TEXT("requestId"), RequestId);
	Response->SetBoolField(TEXT("ok"), true);
	Response->SetObjectField(TEXT("result"), Result);
	SendResponse(Socket, Response);
}

TSharedRef<FJsonObject> FUEAgentKitEditorBridge::BuildHelloResult() const
{
	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("pluginVersion"), PluginVersion);
	Result->SetStringField(TEXT("projectName"), FApp::GetProjectName());
	Result->SetStringField(TEXT("projectPathHash"), ProjectPathHash);
	Result->SetStringField(TEXT("sessionId"), SessionId);
	Result->SetArrayField(TEXT("capabilities"), BuildCapabilityValues());
	return Result;
}

TSharedRef<FJsonObject> FUEAgentKitEditorBridge::BuildStatusResult() const
{
	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("state"), TEXT("available"));
	Result->SetStringField(TEXT("pluginVersion"), PluginVersion);
	Result->SetStringField(TEXT("projectName"), FApp::GetProjectName());
	Result->SetStringField(TEXT("engineVersion"), FEngineVersion::Current().ToString());
	Result->SetNumberField(TEXT("processId"), static_cast<double>(FPlatformProcess::GetCurrentProcessId()));
	Result->SetStringField(TEXT("sessionId"), SessionId);
	Result->SetArrayField(TEXT("capabilities"), BuildCapabilityValues());
	Result->SetStringField(TEXT("pieState"), UEAgentKitEditorBridgePrivate::GetPieStateName());
	UWorld* World = UEAgentKitEditorBridgePrivate::GetEditorWorld();
	Result->SetStringField(TEXT("currentLevel"), World != nullptr && World->GetCurrentLevel() != nullptr ? World->GetCurrentLevel()->GetPathName() : FString());
	Result->SetNumberField(TEXT("dirtyPackageCount"), UEAgentKitEditorBridgePrivate::CountDirtyGamePackages());
	Result->SetNumberField(TEXT("currentPieSessionId"), LogCapture.IsValid() ? LogCapture->GetCurrentPieSessionId() : 0);
	Result->SetStringField(TEXT("capturedPieState"), LogCapture.IsValid() ? LogCapture->GetCurrentPieState() : TEXT("unavailable"));
	return Result;
}

TSharedRef<FJsonObject> FUEAgentKitEditorBridge::BuildSelectionResult() const
{
	TArray<TSharedPtr<FJsonValue>> Items;
	TSet<FString> SeenPaths;
	auto AddSelection = [&Items, &SeenPaths](UObject* Object, const FString& Kind)
	{
		if (Object == nullptr || Items.Num() >= UEAgentKitEditorBridgePrivate::MaxSelectionItems)
		{
			return;
		}
		const FString ObjectPath = Object->GetPathName();
		if (SeenPaths.Contains(ObjectPath))
		{
			return;
		}
		SeenPaths.Add(ObjectPath);
		Items.Add(MakeShared<FJsonValueObject>(UEAgentKitEditorBridgePrivate::DescribeObject(Object, Kind)));
	};

	if (GEditor != nullptr)
	{
		if (USelection* SelectedActors = GEditor->GetSelectedActors())
		{
			for (FSelectionIterator It(*SelectedActors); It; ++It)
			{
				AddSelection(*It, TEXT("Actor"));
			}
		}
		if (USelection* SelectedComponents = GEditor->GetSelectedComponents())
		{
			for (FSelectionIterator It(*SelectedComponents); It; ++It)
			{
				AddSelection(*It, TEXT("Component"));
			}
		}
		if (USelection* SelectedObjects = GEditor->GetSelectedObjects())
		{
			for (FSelectionIterator It(*SelectedObjects); It; ++It)
			{
				UObject* Object = *It;
				AddSelection(Object, Object != nullptr && Object->IsAsset() ? TEXT("Asset") : TEXT("Object"));
			}
		}
	}

	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetNumberField(TEXT("count"), Items.Num());
	Result->SetBoolField(TEXT("truncated"), Items.Num() >= UEAgentKitEditorBridgePrivate::MaxSelectionItems);
	Result->SetArrayField(TEXT("items"), Items);
	return Result;
}

TSharedRef<FJsonObject> FUEAgentKitEditorBridge::BuildOpenAssetsResult() const
{
	TArray<UObject*> EditedAssets;
	if (GEditor != nullptr)
	{
		if (UAssetEditorSubsystem* AssetEditorSubsystem = GEditor->GetEditorSubsystem<UAssetEditorSubsystem>())
		{
			EditedAssets = AssetEditorSubsystem->GetAllEditedAssets();
		}
	}
	EditedAssets.Sort([](const UObject& Left, const UObject& Right)
	{
		return Left.GetPathName() < Right.GetPathName();
	});

	TArray<TSharedPtr<FJsonValue>> Items;
	const int32 TotalCount = EditedAssets.Num();
	for (UObject* Asset : EditedAssets)
	{
		if (Asset == nullptr || Items.Num() >= UEAgentKitEditorBridgePrivate::MaxOpenAssets)
		{
			continue;
		}
		Items.Add(MakeShared<FJsonValueObject>(UEAgentKitEditorBridgePrivate::DescribeObject(Asset, TEXT("Asset"))));
	}
	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetNumberField(TEXT("count"), TotalCount);
	Result->SetBoolField(TEXT("truncated"), TotalCount > Items.Num());
	Result->SetArrayField(TEXT("items"), Items);
	return Result;
}

TSharedRef<FJsonObject> FUEAgentKitEditorBridge::BuildDirtyAssetsResult() const
{
	TArray<UPackage*> DirtyPackages;
	for (TObjectIterator<UPackage> It; It; ++It)
	{
		const FString PackageName = It->GetName();
		if (It->IsDirty() && PackageName.StartsWith(TEXT("/Game/")))
		{
			DirtyPackages.Add(*It);
		}
	}
	DirtyPackages.Sort([](const UPackage& Left, const UPackage& Right)
	{
		return Left.GetName() < Right.GetName();
	});

	FAssetRegistryModule& AssetRegistryModule = FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry"));
	TArray<TSharedPtr<FJsonValue>> Items;
	const int32 TotalCount = DirtyPackages.Num();
	for (UPackage* Package : DirtyPackages)
	{
		if (Package == nullptr || Items.Num() >= UEAgentKitEditorBridgePrivate::MaxDirtyPackages)
		{
			continue;
		}
		TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
		const FString PackageName = Package->GetName();
		Item->SetStringField(TEXT("packageName"), PackageName);
		TArray<FAssetData> Assets;
		AssetRegistryModule.Get().GetAssetsByPackageName(FName(*PackageName), Assets);
		Assets.Sort([](const FAssetData& Left, const FAssetData& Right)
		{
			return Left.GetObjectPathString() < Right.GetObjectPathString();
		});
		TArray<TSharedPtr<FJsonValue>> AssetPaths;
		for (const FAssetData& Asset : Assets)
		{
			AssetPaths.Add(MakeShared<FJsonValueString>(Asset.GetObjectPathString()));
		}
		Item->SetArrayField(TEXT("assetPaths"), AssetPaths);
		Items.Add(MakeShared<FJsonValueObject>(Item));
	}
	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetNumberField(TEXT("count"), TotalCount);
	Result->SetBoolField(TEXT("truncated"), TotalCount > Items.Num());
	Result->SetArrayField(TEXT("items"), Items);
	return Result;
}

TSharedRef<FJsonObject> FUEAgentKitEditorBridge::BuildCurrentLevelResult() const
{
	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	UWorld* World = UEAgentKitEditorBridgePrivate::GetEditorWorld();
	Result->SetBoolField(TEXT("available"), World != nullptr);
	if (World == nullptr)
	{
		return Result;
	}
	Result->SetStringField(TEXT("worldPath"), World->GetPathName());
	Result->SetStringField(TEXT("worldType"), UEAgentKitEditorBridgePrivate::GetWorldTypeName(World->WorldType));
	Result->SetStringField(TEXT("persistentLevelPath"), World->PersistentLevel != nullptr ? World->PersistentLevel->GetPathName() : FString());
	Result->SetStringField(TEXT("currentLevelPath"), World->GetCurrentLevel() != nullptr ? World->GetCurrentLevel()->GetPathName() : FString());
	Result->SetBoolField(TEXT("packageDirty"), World->GetOutermost() != nullptr && World->GetOutermost()->IsDirty());
	Result->SetBoolField(TEXT("worldPartitioned"), World->GetWorldPartition() != nullptr);
	return Result;
}

TSharedRef<FJsonObject> FUEAgentKitEditorBridge::BuildPieStateResult() const
{
	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	const FString State = UEAgentKitEditorBridgePrivate::GetPieStateName();
	Result->SetStringField(TEXT("state"), State);
	Result->SetBoolField(TEXT("playing"), State == TEXT("playing"));
	Result->SetBoolField(TEXT("simulating"), State == TEXT("simulating"));
	UWorld* PlayWorld = GEditor != nullptr ? GEditor->PlayWorld : nullptr;
	Result->SetStringField(TEXT("worldPath"), PlayWorld != nullptr ? PlayWorld->GetPathName() : FString());
	Result->SetStringField(TEXT("worldType"), PlayWorld != nullptr ? UEAgentKitEditorBridgePrivate::GetWorldTypeName(PlayWorld->WorldType) : FString());
	Result->SetNumberField(TEXT("netMode"), PlayWorld != nullptr ? static_cast<int32>(PlayWorld->GetNetMode()) : -1);
	return Result;
}

TSharedRef<FJsonObject> FUEAgentKitEditorBridge::BuildOutputLogResult(const TSharedPtr<FJsonObject>& Params) const
{
	const FUEAgentKitLogQuery Query = UEAgentKitEditorBridgePrivate::BuildLogQuery(Params, false);
	const FUEAgentKitLogQueryResult QueryResult = LogCapture.IsValid()
		? LogCapture->Query(Query)
		: FUEAgentKitLogQueryResult();
	TArray<TSharedPtr<FJsonValue>> Items;
	for (const FUEAgentKitCapturedLogEntry& Entry : QueryResult.Entries)
	{
		Items.Add(MakeShared<FJsonValueObject>(UEAgentKitEditorBridgePrivate::DescribeCapturedLogEntry(Entry)));
	}
	TSharedRef<FJsonObject> Filters = MakeShared<FJsonObject>();
	Filters->SetStringField(TEXT("category"), Query.Category);
	Filters->SetStringField(TEXT("minimumVerbosity"), ToString(Query.MinimumVerbosity));
	Filters->SetStringField(TEXT("keyword"), Query.Keyword);
	Filters->SetNumberField(TEXT("sinceSequence"), static_cast<double>(Query.SinceSequence));
	Filters->SetNumberField(TEXT("pieSessionId"), Query.PieSessionId);
	Filters->SetStringField(TEXT("sinceUtc"), Query.SinceUtc.IsSet() ? Query.SinceUtc.GetValue().ToIso8601() : FString());
	Filters->SetStringField(TEXT("untilUtc"), Query.UntilUtc.IsSet() ? Query.UntilUtc.GetValue().ToIso8601() : FString());
	Filters->SetNumberField(TEXT("limit"), Query.Limit);

	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetBoolField(TEXT("available"), LogCapture.IsValid());
	Result->SetStringField(TEXT("captureStartedUtc"), QueryResult.CaptureStartedUtc.ToIso8601());
	Result->SetNumberField(TEXT("oldestSequence"), static_cast<double>(QueryResult.OldestSequence));
	Result->SetNumberField(TEXT("newestSequence"), static_cast<double>(QueryResult.NewestSequence));
	Result->SetNumberField(TEXT("nextSequence"), static_cast<double>(QueryResult.NextSequence));
	Result->SetNumberField(TEXT("droppedCount"), static_cast<double>(QueryResult.DroppedCount));
	Result->SetNumberField(TEXT("matchedCount"), QueryResult.MatchedCount);
	Result->SetNumberField(TEXT("resultCount"), Items.Num());
	Result->SetBoolField(TEXT("truncated"), QueryResult.bTruncated);
	Result->SetObjectField(TEXT("filters"), Filters);
	Result->SetArrayField(TEXT("items"), Items);
	return Result;
}

TSharedRef<FJsonObject> FUEAgentKitEditorBridge::BuildCompileErrorsResult(const TSharedPtr<FJsonObject>& Params) const
{
	const FUEAgentKitLogQuery Query = UEAgentKitEditorBridgePrivate::BuildLogQuery(Params, true);
	const FUEAgentKitLogQueryResult QueryResult = LogCapture.IsValid()
		? LogCapture->Query(Query)
		: FUEAgentKitLogQueryResult();
	TArray<TSharedPtr<FJsonValue>> Diagnostics;
	for (const FUEAgentKitCapturedLogEntry& Entry : QueryResult.Entries)
	{
		Diagnostics.Add(MakeShared<FJsonValueObject>(UEAgentKitEditorBridgePrivate::DescribeCapturedLogEntry(Entry)));
	}

	TArray<UBlueprint*> Blueprints;
	for (TObjectIterator<UBlueprint> It; It; ++It)
	{
		UBlueprint* Blueprint = *It;
		if (Blueprint == nullptr || !Blueprint->GetPathName().StartsWith(TEXT("/Game/")))
		{
			continue;
		}
		if (!Query.AssetFilter.IsEmpty() && !Blueprint->GetPathName().Equals(Query.AssetFilter, ESearchCase::IgnoreCase))
		{
			continue;
		}
		if (
			Query.AssetFilter.IsEmpty() &&
			Blueprint->Status != BS_Error &&
			Blueprint->Status != BS_UpToDateWithWarnings)
		{
			continue;
		}
		Blueprints.Add(Blueprint);
	}
	Blueprints.Sort([](const UBlueprint& Left, const UBlueprint& Right)
	{
		return Left.GetPathName() < Right.GetPathName();
	});
	const int32 MatchedBlueprintCount = Blueprints.Num();
	constexpr int32 MaxBlueprintStates = 100;
	TArray<TSharedPtr<FJsonValue>> BlueprintStates;
	for (int32 Index = 0; Index < FMath::Min(MatchedBlueprintCount, MaxBlueprintStates); ++Index)
	{
		BlueprintStates.Add(MakeShared<FJsonValueObject>(UEAgentKitEditorBridgePrivate::DescribeBlueprintState(Blueprints[Index])));
	}

	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("diagnosticSource"), TEXT("captured-output-log"));
	Result->SetBoolField(TEXT("historyComplete"), false);
	Result->SetStringField(TEXT("historyScope"), TEXT("Bridge backlog plus messages received during the current Editor Bridge session."));
	Result->SetStringField(TEXT("assetPath"), Query.AssetFilter);
	Result->SetNumberField(TEXT("diagnosticCount"), Diagnostics.Num());
	Result->SetNumberField(TEXT("matchedDiagnosticCount"), QueryResult.MatchedCount);
	Result->SetBoolField(TEXT("diagnosticsTruncated"), QueryResult.bTruncated);
	Result->SetNumberField(TEXT("nextSequence"), static_cast<double>(QueryResult.NextSequence));
	Result->SetNumberField(TEXT("matchedLoadedBlueprintCount"), MatchedBlueprintCount);
	Result->SetNumberField(TEXT("loadedBlueprintCount"), BlueprintStates.Num());
	Result->SetBoolField(TEXT("loadedBlueprintsTruncated"), MatchedBlueprintCount > BlueprintStates.Num());
	Result->SetArrayField(TEXT("diagnostics"), Diagnostics);
	Result->SetArrayField(TEXT("loadedBlueprints"), BlueprintStates);
	return Result;
}


TSharedRef<FJsonObject> FUEAgentKitEditorBridge::BuildBlueprintGraphSelectionResult() const
{
	constexpr int32 MaxSelectedNodes = 100;
	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("scope"), TEXT("ordinary-blueprint-editor"));
	Result->SetBoolField(TEXT("available"), false);
	Result->SetBoolField(TEXT("loadedByBridge"), false);

	if (GEditor == nullptr)
	{
		Result->SetStringField(TEXT("reasonCode"), TEXT("editor-unavailable"));
		return Result;
	}
	UAssetEditorSubsystem* AssetEditorSubsystem = GEditor->GetEditorSubsystem<UAssetEditorSubsystem>();
	if (AssetEditorSubsystem == nullptr)
	{
		Result->SetStringField(TEXT("reasonCode"), TEXT("asset-editor-subsystem-unavailable"));
		return Result;
	}

	IAssetEditorInstance* SelectedInstance = nullptr;
	double LatestActivation = -1.0;
	for (IAssetEditorInstance* Instance : AssetEditorSubsystem->GetAllOpenEditors())
	{
		if (Instance == nullptr || Instance->GetEditorName() != FName(TEXT("BlueprintEditor")))
		{
			continue;
		}
		const double Activation = Instance->GetLastActivationTime();
		if (SelectedInstance == nullptr || Activation > LatestActivation)
		{
			SelectedInstance = Instance;
			LatestActivation = Activation;
		}
	}
	if (SelectedInstance == nullptr)
	{
		Result->SetStringField(TEXT("reasonCode"), TEXT("no-ordinary-blueprint-editor"));
		return Result;
	}

	FBlueprintEditor* BlueprintEditor = static_cast<FBlueprintEditor*>(SelectedInstance);
	UBlueprint* Blueprint = BlueprintEditor->GetBlueprintObj();
	Result->SetStringField(TEXT("editorName"), SelectedInstance->GetEditorName().ToString());
	Result->SetStringField(TEXT("blueprintPath"), Blueprint != nullptr ? Blueprint->GetPathName() : FString());
	if (Blueprint == nullptr || !Blueprint->GetPathName().StartsWith(TEXT("/Game/")))
	{
		Result->SetStringField(TEXT("reasonCode"), TEXT("blueprint-asset-unavailable"));
		return Result;
	}

	UEdGraph* Graph = BlueprintEditor->GetFocusedGraph();
	if (Graph == nullptr)
	{
		Result->SetStringField(TEXT("reasonCode"), TEXT("no-focused-blueprint-graph"));
		return Result;
	}

	TSharedRef<FJsonObject> GraphState = MakeShared<FJsonObject>();
	GraphState->SetStringField(TEXT("graphPath"), Graph->GetPathName());
	GraphState->SetStringField(TEXT("graphName"), Graph->GetName());
	GraphState->SetStringField(TEXT("graphGuid"), Graph->GraphGuid.ToString(EGuidFormats::DigitsWithHyphensLower));
	GraphState->SetStringField(TEXT("classPath"), Graph->GetClass() != nullptr ? Graph->GetClass()->GetPathName() : FString());
	GraphState->SetStringField(TEXT("schemaClassPath"), Graph->GetSchema() != nullptr ? Graph->GetSchema()->GetClass()->GetPathName() : FString());
	GraphState->SetBoolField(TEXT("editable"), BlueprintEditor->IsEditable(Graph));

	const FGraphPanelSelectionSet Selection = BlueprintEditor->GetSelectedNodes();
	TArray<UEdGraphNode*> Nodes;
	for (UObject* Object : Selection)
	{
		if (UEdGraphNode* Node = Cast<UEdGraphNode>(Object))
		{
			if (Node->GetGraph() == Graph)
			{
				Nodes.Add(Node);
			}
		}
	}
	Nodes.Sort([](const UEdGraphNode& Left, const UEdGraphNode& Right)
	{
		return Left.NodeGuid.ToString() < Right.NodeGuid.ToString();
	});

	TArray<TSharedPtr<FJsonValue>> Items;
	for (int32 Index = 0; Index < FMath::Min(Nodes.Num(), MaxSelectedNodes); ++Index)
	{
		const UEdGraphNode* Node = Nodes[Index];
		TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
		Item->SetStringField(TEXT("nodePath"), Node->GetPathName());
		Item->SetStringField(TEXT("nodeName"), Node->GetName());
		Item->SetStringField(TEXT("nodeGuid"), Node->NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower));
		Item->SetStringField(TEXT("classPath"), Node->GetClass() != nullptr ? Node->GetClass()->GetPathName() : FString());
		Item->SetStringField(TEXT("title"), Node->GetNodeTitle(ENodeTitleType::FullTitle).ToString());
		Item->SetNumberField(TEXT("nodePosX"), Node->NodePosX);
		Item->SetNumberField(TEXT("nodePosY"), Node->NodePosY);
		Items.Add(MakeShared<FJsonValueObject>(Item));
	}

	Result->SetBoolField(TEXT("available"), true);
	Result->SetStringField(TEXT("reasonCode"), TEXT(""));
	Result->SetObjectField(TEXT("graph"), GraphState);
	Result->SetNumberField(TEXT("selectedNodeCount"), Nodes.Num());
	Result->SetBoolField(TEXT("selectedNodesTruncated"), Nodes.Num() > Items.Num());
	Result->SetArrayField(TEXT("selectedNodes"), Items);
	return Result;
}

TSharedRef<FJsonObject> FUEAgentKitEditorBridge::BuildInspectAssetLiveResult(const FString& AssetPath) const
{
	FAssetRegistryModule& AssetRegistryModule = FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry"));
	const FAssetData AssetData = AssetRegistryModule.Get().GetAssetByObjectPath(FSoftObjectPath(AssetPath));
	UObject* LoadedObject = StaticFindObject(UObject::StaticClass(), nullptr, *AssetPath, false);
	if (LoadedObject != nullptr && !LoadedObject->IsAsset())
	{
		LoadedObject = nullptr;
	}

	TSharedRef<FJsonObject> RegistryState = MakeShared<FJsonObject>();
	RegistryState->SetBoolField(TEXT("found"), AssetData.IsValid());
	RegistryState->SetStringField(TEXT("assetPath"), AssetData.IsValid() ? AssetData.GetObjectPathString() : AssetPath);
	RegistryState->SetStringField(TEXT("packageName"), AssetData.IsValid() ? AssetData.PackageName.ToString() : FString());
	RegistryState->SetStringField(TEXT("assetName"), AssetData.IsValid() ? AssetData.AssetName.ToString() : FString());
	RegistryState->SetStringField(TEXT("classPath"), AssetData.IsValid() ? AssetData.AssetClassPath.ToString() : FString());

	TSharedRef<FJsonObject> MemoryState = MakeShared<FJsonObject>();
	MemoryState->SetBoolField(TEXT("loaded"), LoadedObject != nullptr);
	MemoryState->SetBoolField(TEXT("loadedByBridge"), false);
	MemoryState->SetBoolField(TEXT("packageDirty"), LoadedObject != nullptr && LoadedObject->GetOutermost() != nullptr && LoadedObject->GetOutermost()->IsDirty());
	MemoryState->SetBoolField(TEXT("openInAssetEditor"), UEAgentKitEditorBridgePrivate::IsAssetOpenInEditor(LoadedObject));
	MemoryState->SetBoolField(TEXT("selected"), UEAgentKitEditorBridgePrivate::IsObjectSelected(LoadedObject));
	MemoryState->SetBoolField(TEXT("rooted"), LoadedObject != nullptr && LoadedObject->IsRooted());
	MemoryState->SetStringField(TEXT("objectPath"), LoadedObject != nullptr ? LoadedObject->GetPathName() : FString());
	MemoryState->SetStringField(TEXT("classPath"), LoadedObject != nullptr && LoadedObject->GetClass() != nullptr ? LoadedObject->GetClass()->GetPathName() : FString());
	MemoryState->SetStringField(
		TEXT("state"),
		LoadedObject == nullptr
			? TEXT("not-loaded")
			: (LoadedObject->GetOutermost() != nullptr && LoadedObject->GetOutermost()->IsDirty() ? TEXT("loaded-unsaved") : TEXT("loaded-saved")));

	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("assetPath"), AssetPath);
	Result->SetStringField(TEXT("editorSessionId"), SessionId);
	Result->SetNumberField(TEXT("pieSessionId"), LogCapture.IsValid() ? LogCapture->GetCurrentPieSessionId() : 0);
	Result->SetStringField(TEXT("pieState"), LogCapture.IsValid() ? LogCapture->GetCurrentPieState() : TEXT("unavailable"));
	Result->SetObjectField(TEXT("assetRegistry"), RegistryState);
	Result->SetObjectField(TEXT("memory"), MemoryState);
	Result->SetBoolField(TEXT("hasBlueprintState"), Cast<UBlueprint>(LoadedObject) != nullptr);
	if (UBlueprint* Blueprint = Cast<UBlueprint>(LoadedObject))
	{
		Result->SetObjectField(TEXT("blueprint"), UEAgentKitEditorBridgePrivate::DescribeBlueprintState(Blueprint));
	}
	return Result;
}

bool FUEAgentKitEditorBridge::WriteDescriptor()
{
	IFileManager::Get().MakeDirectory(*FPaths::GetPath(DescriptorPath), true);
	TSharedRef<FJsonObject> Descriptor = MakeShared<FJsonObject>();
	Descriptor->SetStringField(TEXT("schemaVersion"), ProtocolSchemaVersion);
	Descriptor->SetStringField(TEXT("address"), TEXT("127.0.0.1"));
	Descriptor->SetNumberField(TEXT("port"), ListenPort);
	Descriptor->SetStringField(TEXT("authToken"), AuthToken);
	Descriptor->SetStringField(TEXT("projectName"), FApp::GetProjectName());
	Descriptor->SetStringField(TEXT("projectPathHash"), ProjectPathHash);
	Descriptor->SetStringField(TEXT("pluginVersion"), PluginVersion);
	Descriptor->SetNumberField(TEXT("processId"), static_cast<double>(FPlatformProcess::GetCurrentProcessId()));
	Descriptor->SetStringField(TEXT("sessionId"), SessionId);
	Descriptor->SetStringField(TEXT("startedUtc"), FDateTime::UtcNow().ToIso8601());
	Descriptor->SetArrayField(TEXT("capabilities"), BuildCapabilityValues());

	FString JsonText;
	const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&JsonText);
	if (!FJsonSerializer::Serialize(Descriptor, Writer))
	{
		return false;
	}
	const FString TemporaryPath = DescriptorPath + TEXT(".tmp");
	if (!FFileHelper::SaveStringToFile(JsonText, *TemporaryPath, FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
	{
		UE_LOG(LogUEAgentKitEditorBridge, Error, TEXT("Failed to write the Editor Bridge descriptor."));
		return false;
	}
	if (!IFileManager::Get().Move(*DescriptorPath, *TemporaryPath, true, true, false, true))
	{
		IFileManager::Get().Delete(*TemporaryPath, false, true, true);
		UE_LOG(LogUEAgentKitEditorBridge, Error, TEXT("Failed to publish the Editor Bridge descriptor."));
		return false;
	}
	return true;
}

void FUEAgentKitEditorBridge::RemoveDescriptor()
{
	if (DescriptorPath.IsEmpty() || AuthToken.IsEmpty() || !IFileManager::Get().FileExists(*DescriptorPath))
	{
		return;
	}
	FString JsonText;
	TSharedPtr<FJsonObject> Descriptor;
	if (FFileHelper::LoadFileToString(JsonText, *DescriptorPath))
	{
		const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(JsonText);
		if (FJsonSerializer::Deserialize(Reader, Descriptor) && Descriptor.IsValid())
		{
			FString DescriptorToken;
			if (Descriptor->TryGetStringField(TEXT("authToken"), DescriptorToken) && DescriptorToken == AuthToken)
			{
				IFileManager::Get().Delete(*DescriptorPath, false, true, true);
			}
		}
	}
}

FString FUEAgentKitEditorBridge::ComputeProjectPathHash() const
{
	return UEAgentKitEditorBridgePrivate::HashUtf8(UEAgentKitEditorBridgePrivate::NormalizeProjectPath());
}

TArray<TSharedPtr<FJsonValue>> FUEAgentKitEditorBridge::BuildCapabilityValues() const
{
	TArray<TSharedPtr<FJsonValue>> Values;
	for (const TCHAR* Capability : UEAgentKitEditorBridgePrivate::Capabilities)
	{
		Values.Add(MakeShared<FJsonValueString>(Capability));
	}
	return Values;
}
