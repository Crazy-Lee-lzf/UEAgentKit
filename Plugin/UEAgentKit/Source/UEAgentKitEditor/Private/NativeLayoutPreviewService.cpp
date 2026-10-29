#include "NativeLayoutPreviewService.h"

#include "BlueprintGraphLayoutApply.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "Editor.h"
#include "EditorBridge.h"
#include "HAL/PlatformTime.h"
#include "Misc/App.h"
#include "Misc/ConfigCacheIni.h"
#include "Misc/DateTime.h"
#include "Misc/Paths.h"
#include "NativeLayoutBrokerClient.h"
#include "NativeLayoutBrokerDescriptor.h"
#include "NativeLayoutBrokerPlanId.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"

namespace
{
	constexpr int32 MaxMessageBytes = 1024 * 1024;
	constexpr int32 DefaultDeadlineMs = 5000;
	const TCHAR* ProtocolSchema = TEXT("ueak.layoutbroker/1.0");
	const TCHAR* AlgorithmId = TEXT("p0-layered");
	const TCHAR* AlgorithmVersion = TEXT("1.0.0");

	TSharedPtr<FJsonObject> MakeAlgorithm()
	{
		TSharedPtr<FJsonObject> Algorithm = MakeShared<FJsonObject>();
		Algorithm->SetStringField(TEXT("id"), AlgorithmId);
		Algorithm->SetStringField(TEXT("version"), AlgorithmVersion);
		return Algorithm;
	}

	bool IsSha256Text(const FString& Value)
	{
		if (Value.Len() != 71 || !Value.StartsWith(TEXT("sha256:"), ESearchCase::CaseSensitive))
		{
			return false;
		}
		for (int32 Index = 7; Index < Value.Len(); ++Index)
		{
			const TCHAR Character = Value[Index];
			if (!((Character >= TEXT('0') && Character <= TEXT('9'))
				|| (Character >= TEXT('a') && Character <= TEXT('f'))))
			{
				return false;
			}
		}
		return true;
	}

	bool TryGetExactInt(const TSharedPtr<FJsonObject>& Object, const TCHAR* Field, int32& Out)
	{
		double Number = 0.0;
		if (!Object.IsValid() || !Object->TryGetNumberField(Field, Number))
		{
			return false;
		}
		const double Rounded = FMath::RoundToDouble(Number);
		if (!FMath::IsNearlyEqual(Number, Rounded))
		{
			return false;
		}
		Out = static_cast<int32>(Rounded);
		return true;
	}

	FString SanitizeMessage(FString Message, const FString& Token)
	{
		if (!Token.IsEmpty())
		{
			Message.ReplaceInline(*Token, TEXT("[redacted]"));
		}
		Message.ReplaceInline(TEXT("\r"), TEXT(" "));
		Message.ReplaceInline(TEXT("\n"), TEXT(" "));
		if (Message.Len() > 300)
		{
			Message.LeftInline(300);
		}
		return Message;
	}

	bool ParseJsonObject(const FString& Json, TSharedPtr<FJsonObject>& OutObject)
	{
		OutObject.Reset();
		const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Json);
		return FJsonSerializer::Deserialize(Reader, OutObject) && OutObject.IsValid();
	}

	bool ExtractNodeIds(const TSharedPtr<FJsonObject>& Envelope, TSet<FString>& OutIds, FString& OutError)
	{
		OutIds.Reset();
		const TSharedPtr<FJsonObject>* Context = nullptr;
		if (!Envelope.IsValid() || !Envelope->TryGetObjectField(TEXT("context"), Context) || Context == nullptr || !Context->IsValid())
		{
			OutError = TEXT("The snapshot has no canonical context.");
			return false;
		}
		const TArray<TSharedPtr<FJsonValue>>* Graphs = nullptr;
		if (!(*Context)->TryGetArrayField(TEXT("graphs"), Graphs) || Graphs == nullptr || Graphs->Num() == 0)
		{
			OutError = TEXT("The canonical context has no graph.");
			return false;
		}
		for (const TSharedPtr<FJsonValue>& GraphValue : *Graphs)
		{
			if (!GraphValue.IsValid() || GraphValue->Type != EJson::Object)
			{
				continue;
			}
			const TSharedPtr<FJsonObject> Graph = GraphValue->AsObject();
			const TArray<TSharedPtr<FJsonValue>>* Nodes = nullptr;
			if (!Graph.IsValid() || !Graph->TryGetArrayField(TEXT("nodes"), Nodes) || Nodes == nullptr)
			{
				continue;
			}
			for (const TSharedPtr<FJsonValue>& NodeValue : *Nodes)
			{
				if (!NodeValue.IsValid() || NodeValue->Type != EJson::Object)
				{
					continue;
				}
				const TSharedPtr<FJsonObject> Node = NodeValue->AsObject();
				FString Guid;
				if (Node.IsValid() && Node->TryGetStringField(TEXT("guid"), Guid) && !Guid.IsEmpty())
				{
					OutIds.Add(FString(TEXT("n:")) + Guid.ToLower());
				}
			}
			if (OutIds.Num() > 0)
			{
				return true;
			}
		}
		OutError = TEXT("The canonical context contains no node ids.");
		return false;
	}
}

FUEAgentKitNativeLayoutPreviewConfig FUEAgentKitNativeLayoutPreviewConfig::Load()
{
	FUEAgentKitNativeLayoutPreviewConfig Config;
	GConfig->GetBool(TEXT("UEAgentKit"), TEXT("NativeLayoutPreview"), Config.bNativeLayoutPreview, GEngineIni);
	GConfig->GetBool(TEXT("UEAgentKit"), TEXT("NativeToolRegistry"), Config.bNativeToolRegistry, GEngineIni);
	return Config;
}

FUEAgentKitNativeLayoutPreviewService::FUEAgentKitNativeLayoutPreviewService(FUEAgentKitEditorBridge* InBridge)
	: Bridge(InBridge)
{
}

FUEAgentKitNativeLayoutPreviewService::~FUEAgentKitNativeLayoutPreviewService()
{
	Cancel();
}

bool FUEAgentKitNativeLayoutPreviewService::StartPreview()
{
	if (!FUEAgentKitNativeLayoutPreviewConfig::Load().bNativeLayoutPreview)
	{
		Fail(TEXT("native-preview-disabled"), TEXT("NativeLayoutPreview is off."));
		return false;
	}
	if (Bridge == nullptr)
	{
		Fail(TEXT("native-preview-worker-failed"), TEXT("The Editor Bridge snapshot provider is unavailable."));
		return false;
	}
	TSharedPtr<FJsonObject> Preflight;
	TSharedPtr<FJsonObject> Envelope;
	FString ErrorCode;
	FString ErrorMessage;
	if (!Bridge->BuildP9MenuPreviewInputs(Preflight, Envelope, ErrorCode, ErrorMessage))
	{
		Fail(ErrorCode.IsEmpty() ? TEXT("native-preview-worker-failed") : ErrorCode, ErrorMessage);
		return false;
	}
	return BeginFromSnapshot(Preflight, Envelope);
}

bool FUEAgentKitNativeLayoutPreviewService::StartPreviewForMenu()
{
	if (Bridge == nullptr)
	{
		Fail(TEXT("native-preview-worker-failed"), TEXT("The Editor Bridge snapshot provider is unavailable."));
		return false;
	}
	TSharedPtr<FJsonObject> Preflight;
	TSharedPtr<FJsonObject> Envelope;
	FString ErrorCode;
	FString ErrorMessage;
	if (!Bridge->BuildP9MenuPreviewInputs(Preflight, Envelope, ErrorCode, ErrorMessage))
	{
		Fail(ErrorCode.IsEmpty() ? TEXT("native-preview-worker-failed") : ErrorCode, ErrorMessage);
		return false;
	}
	return BeginFromSnapshot(Preflight, Envelope);
}

bool FUEAgentKitNativeLayoutPreviewService::StartPreviewWithSnapshot(
	const TSharedPtr<FJsonObject>& Preflight,
	const TSharedPtr<FJsonObject>& Envelope,
	bool bTestOverride)
{
	if (!bTestOverride && !FUEAgentKitNativeLayoutPreviewConfig::Load().bNativeLayoutPreview)
	{
		Fail(TEXT("native-preview-disabled"), TEXT("NativeLayoutPreview is off."));
		return false;
	}
	return BeginFromSnapshot(Preflight, Envelope);
}

bool FUEAgentKitNativeLayoutPreviewService::BeginFromSnapshot(
	const TSharedPtr<FJsonObject>& Preflight,
	const TSharedPtr<FJsonObject>& Envelope)
{
	if (bRunning || bTerminal)
	{
		Fail(TEXT("native-preview-busy"), TEXT("A native preview is already active."));
		return false;
	}
	if (!Preflight.IsValid() || !Envelope.IsValid())
	{
		Fail(TEXT("native-preview-request-invalid"), TEXT("The snapshot is missing."));
		return false;
	}
	bool bAvailable = false;
	bool bComplete = false;
	if (!Preflight->TryGetBoolField(TEXT("available"), bAvailable) || !bAvailable
		|| !Preflight->TryGetBoolField(TEXT("complete"), bComplete) || !bComplete)
	{
		Fail(TEXT("native-preview-worker-failed"), TEXT("The snapshot is unavailable or incomplete."));
		return false;
	}
	bool bSnapshotDirty = false;
	if (Preflight->TryGetBoolField(TEXT("packageDirty"), bSnapshotDirty) && bSnapshotDirty)
	{
		Fail(TEXT("native-preview-package-dirty"), TEXT("The package is dirty; native preview is fail-closed."));
		return false;
	}
	FString SnapshotPieState;
	if (Preflight->TryGetStringField(TEXT("pieState"), SnapshotPieState)
		&& !SnapshotPieState.Equals(TEXT("stopped"), ESearchCase::CaseSensitive))
	{
		Fail(TEXT("native-preview-pie-active"), TEXT("PIE/SIE is active; native preview is fail-closed."));
		return false;
	}
	if (!Preflight->TryGetStringField(TEXT("assetPath"), AssetPath)
		|| !Preflight->TryGetStringField(TEXT("graphGuid"), GraphGuid)
		|| !Preflight->TryGetStringField(TEXT("sessionId"), SessionId)
		|| !Preflight->TryGetStringField(TEXT("packageRevision"), PackageRevision)
		|| !Preflight->TryGetStringField(TEXT("graphFingerprint"), GraphFingerprint)
		|| !TryGetExactInt(Preflight, TEXT("nodeCount"), NodeCount))
	{
		Fail(TEXT("native-preview-worker-failed"), TEXT("The snapshot identity is incomplete."));
		return false;
	}
	if (!ExtractNodeIds(Envelope, SnapshotNodeIds, TerminalMessage))
	{
		Fail(TEXT("native-preview-worker-failed"), TerminalMessage);
		return false;
	}

	FUEAgentKitLayoutBrokerDescriptor Descriptor;
	FString DescriptorCode;
	FString DescriptorMessage;
	if (!FUEAgentKitLayoutBrokerDescriptor::Load(Descriptor, DescriptorCode, DescriptorMessage))
	{
		Fail(DescriptorCode, DescriptorMessage);
		return false;
	}
	BrokerToken = Descriptor.Token;
	DescriptorVersion = Descriptor.BrokerVersion;
	DescriptorAlgorithm = Descriptor.AlgorithmId + TEXT("/") + Descriptor.AlgorithmVersion;

	SnapshotPreflight = Preflight;
	SnapshotEnvelope = Envelope;
	RequestId = FGuid::NewGuid().ToString(EGuidFormats::DigitsWithHyphensLower);
	SpacingX = 96;
	SpacingY = 64;
	TargetWidth = 1800;
	Seed = 0;

	TSharedPtr<FJsonObject> Options = MakeShared<FJsonObject>();
	Options->SetStringField(TEXT("mode"), TEXT("full"));
	Options->SetNumberField(TEXT("spacingX"), SpacingX);
	Options->SetNumberField(TEXT("spacingY"), SpacingY);
	Options->SetNumberField(TEXT("targetWidth"), TargetWidth);
	Options->SetNumberField(TEXT("seed"), Seed);

	TSharedPtr<FJsonObject> Snapshot = MakeShared<FJsonObject>();
	Snapshot->SetObjectField(TEXT("preflight"), SnapshotPreflight);
	Snapshot->SetObjectField(TEXT("contextEnvelope"), SnapshotEnvelope);

	TSharedPtr<FJsonObject> Request = MakeShared<FJsonObject>();
	Request->SetStringField(TEXT("schemaVersion"), ProtocolSchema);
	Request->SetStringField(TEXT("requestId"), RequestId);
	Request->SetStringField(TEXT("sessionId"), SessionId);
	Request->SetStringField(TEXT("projectName"), FApp::GetProjectName());
	Request->SetStringField(TEXT("projectPathHash"), Descriptor.ProjectPathHash);
	Request->SetStringField(TEXT("token"), Descriptor.Token);
	Request->SetStringField(TEXT("sentAtUtc"), FDateTime::UtcNow().ToIso8601());
	Request->SetNumberField(TEXT("deadlineMs"), DefaultDeadlineMs);
	Request->SetObjectField(TEXT("algorithm"), MakeAlgorithm());
	Request->SetObjectField(TEXT("options"), Options);
	Request->SetObjectField(TEXT("snapshot"), Snapshot);

	FString RequestText;
	const TSharedRef<TJsonWriter<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>> Writer =
		TJsonWriterFactory<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>::Create(&RequestText);
	if (!FJsonSerializer::Serialize(Request.ToSharedRef(), Writer))
	{
		Fail(TEXT("native-preview-worker-failed"), TEXT("The broker request could not be serialized."));
		return false;
	}
	FTCHARToUTF8 Utf8(*RequestText);
	if (Utf8.Length() + 1 > MaxMessageBytes)
	{
		Fail(TEXT("native-preview-request-too-large"), TEXT("The broker request exceeds the 1 MiB limit."));
		return false;
	}
	RequestJson = RequestText + TEXT("\n");

	Client = MakeUnique<FUEAgentKitLayoutBrokerClient>();
	if (!Client->Start(Descriptor.Address, Descriptor.Port, RequestJson))
	{
		Fail(Client->GetErrorCode(), Client->GetErrorMessage());
		return false;
	}
	bRunning = true;
	bTerminal = false;
	Outcome = {};
	return true;
}

void FUEAgentKitNativeLayoutPreviewService::Tick()
{
	if (!bRunning || bTerminal || !Client.IsValid())
	{
		return;
	}
	if (!Client->IsDone())
	{
		return;
	}
	bRunning = false;
	if (!Client->IsSuccessful())
	{
		Fail(Client->GetErrorCode(), Client->GetErrorMessage());
		return;
	}
	TSharedPtr<FJsonObject> Plan;
	FString ErrorCode;
	FString ErrorMessage;
	if (!ValidateResponse(Client->GetResponseJson(), Plan, ErrorCode, ErrorMessage))
	{
		Fail(ErrorCode, ErrorMessage);
		return;
	}
	PublishReceipt(true, FString(), FString(), Plan);
	Outcome.Plan = Plan;
	bTerminal = true;
	Outcome.bTerminal = true;
	Outcome.bOk = true;
}

void FUEAgentKitNativeLayoutPreviewService::Cancel()
{
	if (Client.IsValid())
	{
		Client->CancelAndWait();
	}
	if (bRunning && !bTerminal)
	{
		Fail(TEXT("native-preview-cancelled"), TEXT("The native preview was cancelled."));
	}
}

bool FUEAgentKitNativeLayoutPreviewService::IsTerminal() const
{
	return bTerminal;
}

bool FUEAgentKitNativeLayoutPreviewService::HasWorkerResponseForTest() const
{
	return Client.IsValid() && Client->IsDone();
}

FUEAgentKitNativeLayoutPreviewOutcome FUEAgentKitNativeLayoutPreviewService::GetOutcome() const
{
	return Outcome;
}

void FUEAgentKitNativeLayoutPreviewService::Fail(const FString& Code, const FString& Message)
{
	bRunning = false;
	bTerminal = true;
	Outcome = {};
	Outcome.bTerminal = true;
	Outcome.bOk = false;
	Outcome.Code = Code.IsEmpty() ? TEXT("native-preview-worker-failed") : Code;
	TerminalCode = Outcome.Code;
	TerminalMessage = SanitizeMessage(Message, BrokerToken);
	Outcome.Message = TerminalMessage;
	PublishReceipt(false, Outcome.Code, TerminalMessage, nullptr);
}

void FUEAgentKitNativeLayoutPreviewService::PublishReceipt(
	bool bOk,
	const FString& Code,
	const FString& Message,
	const TSharedPtr<FJsonObject>& Plan)
{
	TSharedPtr<FJsonObject> Receipt = MakeShared<FJsonObject>();
	Receipt->SetStringField(TEXT("schemaVersion"), TEXT("ueak.layoutbroker-receipt/1.0"));
	Receipt->SetBoolField(TEXT("ok"), bOk);
	Receipt->SetBoolField(TEXT("planProduced"), bOk && Plan.IsValid());
	Receipt->SetBoolField(TEXT("readOnly"), true);
	Receipt->SetBoolField(TEXT("saved"), false);
	Receipt->SetBoolField(TEXT("compiled"), false);
	Receipt->SetBoolField(TEXT("stale"), false);
	Receipt->SetStringField(TEXT("workerMode"), TEXT("layout-broker"));
	Receipt->SetStringField(TEXT("previewPath"), TEXT("native-broker"));
	Receipt->SetStringField(TEXT("requestId"), RequestId);
	Receipt->SetStringField(TEXT("brokerVersion"), DescriptorVersion);
	Receipt->SetStringField(TEXT("algorithm"), DescriptorAlgorithm);
	Receipt->SetStringField(TEXT("assetPath"), AssetPath);
	Receipt->SetStringField(TEXT("graphGuid"), GraphGuid);
	Receipt->SetStringField(TEXT("packageRevision"), PackageRevision);
	Receipt->SetStringField(TEXT("graphFingerprint"), GraphFingerprint);
	Receipt->SetNumberField(TEXT("nodeCount"), NodeCount);
	if (!bOk && !Code.IsEmpty())
	{
		Receipt->SetStringField(TEXT("errorCode"), Code);
		Receipt->SetStringField(TEXT("errorMessage"), SanitizeMessage(Message, BrokerToken));
	}
	if (bOk && Plan.IsValid())
	{
		FString PlanId;
		FString LayoutHash;
		int32 MovedCount = 0;
		const TArray<TSharedPtr<FJsonValue>>* Moved = nullptr;
		Plan->TryGetStringField(TEXT("planId"), PlanId);
		Plan->TryGetStringField(TEXT("layoutHash"), LayoutHash);
		if (Plan->TryGetArrayField(TEXT("movedNodeIds"), Moved) && Moved != nullptr)
		{
			MovedCount = Moved->Num();
		}
		Receipt->SetStringField(TEXT("planId"), PlanId);
		Receipt->SetStringField(TEXT("layoutHash"), LayoutHash);
		Receipt->SetNumberField(TEXT("movedCount"), MovedCount);
	}
	Outcome.Receipt = Receipt;
	Outcome.Code = Code;
	Outcome.Message = SanitizeMessage(Message, BrokerToken);
}

bool FUEAgentKitNativeLayoutPreviewService::ValidateResponse(
	const FString& ResponseJson,
	TSharedPtr<FJsonObject>& OutPlan,
	FString& OutCode,
	FString& OutMessage)
{
	if (!BrokerToken.IsEmpty() && ResponseJson.Contains(BrokerToken))
	{
		OutCode = TEXT("native-preview-response-invalid");
		OutMessage = TEXT("The broker response contains the session token.");
		return false;
	}
	TSharedPtr<FJsonObject> Root;
	if (!ParseJsonObject(ResponseJson, Root))
	{
		OutCode = TEXT("native-preview-response-invalid");
		OutMessage = TEXT("The broker response is not valid JSON.");
		return false;
	}
	FString SchemaVersion;
	FString ResponseRequestId;
	bool bOk = false;
	if (!Root->TryGetStringField(TEXT("schemaVersion"), SchemaVersion)
		|| SchemaVersion != ProtocolSchema
		|| !Root->TryGetStringField(TEXT("requestId"), ResponseRequestId)
		|| ResponseRequestId != RequestId
		|| !Root->TryGetBoolField(TEXT("ok"), bOk))
	{
		OutCode = TEXT("native-preview-response-invalid");
		OutMessage = TEXT("The broker response identity is invalid.");
		return false;
	}
	if (!bOk)
	{
		const TSharedPtr<FJsonObject>* Error = nullptr;
		FString ErrorCode;
		FString ErrorMessage;
		if (!Root->TryGetObjectField(TEXT("error"), Error) || Error == nullptr || !Error->IsValid()
			|| !(*Error)->TryGetStringField(TEXT("code"), ErrorCode)
			|| !(*Error)->TryGetStringField(TEXT("message"), ErrorMessage)
			|| ErrorCode.IsEmpty())
		{
			OutCode = TEXT("native-preview-response-invalid");
			OutMessage = TEXT("The broker error response is invalid.");
			return false;
		}
		OutCode = ErrorCode.Left(96);
		OutMessage = SanitizeMessage(ErrorMessage, FString());
		return false;
	}
	bool bReadOnly = false;
	if (!Root->TryGetBoolField(TEXT("readOnly"), bReadOnly) || !bReadOnly)
	{
		OutCode = TEXT("native-preview-response-invalid");
		OutMessage = TEXT("The broker response is not read-only.");
		return false;
	}
	const TSharedPtr<FJsonObject>* Algorithm = nullptr;
	FString AlgorithmName;
	FString AlgorithmVersionValue;
	if (!Root->TryGetObjectField(TEXT("algorithm"), Algorithm) || Algorithm == nullptr || !Algorithm->IsValid()
		|| !(*Algorithm)->TryGetStringField(TEXT("id"), AlgorithmName)
		|| !(*Algorithm)->TryGetStringField(TEXT("version"), AlgorithmVersionValue)
		|| AlgorithmName != AlgorithmId
		|| AlgorithmVersionValue != AlgorithmVersion)
	{
		OutCode = TEXT("native-preview-response-invalid");
		OutMessage = TEXT("The broker algorithm identity is invalid.");
		return false;
	}
	const TSharedPtr<FJsonObject>* Result = nullptr;
	if (!Root->TryGetObjectField(TEXT("result"), Result) || Result == nullptr || !Result->IsValid())
	{
		OutCode = TEXT("native-preview-response-invalid");
		OutMessage = TEXT("The broker response has no result object.");
		return false;
	}
	const TSharedPtr<FJsonObject> Preview = *Result;

	FString ResultSchema;
	FString ResultAssetPath;
	FString ResultGraphGuid;
	FString ResultSessionId;
	FString ResultRevision;
	FString ResultFingerprint;
	FString PlanId;
	FString LayoutHash;
	FString BaseGraphHash;
	int32 ResultNodeCount = 0;
	int32 ResultMovedCount = 0;
	bool bResultReadOnly = false;
	bool bPackageDirty = true;
	if (!Preview->TryGetStringField(TEXT("schemaVersion"), ResultSchema)
		|| ResultSchema != TEXT("ueak.graphlayoutpreview/1.0")
		|| !Preview->TryGetBoolField(TEXT("readOnly"), bResultReadOnly) || !bResultReadOnly
		|| !Preview->TryGetStringField(TEXT("assetPath"), ResultAssetPath)
		|| !Preview->TryGetStringField(TEXT("graphGuid"), ResultGraphGuid)
		|| !Preview->TryGetStringField(TEXT("sessionId"), ResultSessionId)
		|| !Preview->TryGetStringField(TEXT("packageRevision"), ResultRevision)
		|| !Preview->TryGetStringField(TEXT("graphFingerprint"), ResultFingerprint)
		|| !Preview->TryGetBoolField(TEXT("packageDirty"), bPackageDirty) || bPackageDirty
		|| !TryGetExactInt(Preview, TEXT("nodeCount"), ResultNodeCount)
		|| !TryGetExactInt(Preview, TEXT("movedCount"), ResultMovedCount)
		|| !Preview->TryGetStringField(TEXT("planId"), PlanId)
		|| !Preview->TryGetStringField(TEXT("layoutHash"), LayoutHash)
		|| !Preview->TryGetStringField(TEXT("baseGraphHash"), BaseGraphHash))
	{
		OutCode = TEXT("native-preview-plan-invalid");
		OutMessage = TEXT("The broker result identity or shape is invalid.");
		return false;
	}
	if (ResultAssetPath != AssetPath || ResultGraphGuid != GraphGuid || ResultSessionId != SessionId
		|| ResultRevision != PackageRevision || ResultFingerprint != GraphFingerprint
		|| ResultNodeCount != NodeCount || !IsSha256Text(LayoutHash) || !IsSha256Text(BaseGraphHash))
	{
		OutCode = TEXT("native-preview-identity-mismatch");
		OutMessage = TEXT("The broker result does not match the snapshot identity.");
		return false;
	}
	const TSharedPtr<FJsonObject>* Plan = nullptr;
	if (!Preview->TryGetObjectField(TEXT("plan"), Plan) || Plan == nullptr || !Plan->IsValid())
	{
		OutCode = TEXT("native-preview-plan-invalid");
		OutMessage = TEXT("The broker result has no plan object.");
		return false;
	}
	FString RecomputedLayoutHash;
	if (!UEAgentKitBlueprintGraphLayout::ComputeLayoutHash(*Plan, RecomputedLayoutHash)
		|| RecomputedLayoutHash != LayoutHash)
	{
		OutCode = TEXT("native-preview-plan-hash-mismatch");
		OutMessage = TEXT("The layoutHash is not the canonical recomputation.");
		return false;
	}
	FString PlanIdError;
	if (!FUEAgentKitLayoutBrokerPlanId::Validate(
			Preview, SpacingX, SpacingY, TargetWidth, Seed, PlanIdError))
	{
		OutCode = TEXT("native-preview-plan-id-mismatch");
		OutMessage = PlanIdError;
		return false;
	}

	const TArray<TSharedPtr<FJsonValue>>* ResultPositions = nullptr;
	const TArray<TSharedPtr<FJsonValue>>* PlanPositions = nullptr;
	const TArray<TSharedPtr<FJsonValue>>* MovedNodeIds = nullptr;
	if (!Preview->TryGetArrayField(TEXT("positions"), ResultPositions) || ResultPositions == nullptr
		|| !(*Plan)->TryGetArrayField(TEXT("positions"), PlanPositions) || PlanPositions == nullptr
		|| !(*Plan)->TryGetArrayField(TEXT("movedNodeIds"), MovedNodeIds) || MovedNodeIds == nullptr)
	{
		OutCode = TEXT("native-preview-plan-invalid");
		OutMessage = TEXT("The broker result is missing positions or movedNodeIds.");
		return false;
	}
	TSet<FString> ResultNodeIds;
	TMap<FString, FIntPoint> ResultAfter;
	for (const TSharedPtr<FJsonValue>& Entry : *ResultPositions)
	{
		if (!Entry.IsValid() || Entry->Type != EJson::Object)
		{
			OutCode = TEXT("native-preview-plan-invalid");
			OutMessage = TEXT("A result position is not an object.");
			return false;
		}
		const TSharedPtr<FJsonObject> Position = Entry->AsObject();
		FString NodeId;
		int32 AfterX = 0;
		int32 AfterY = 0;
		if (!Position.IsValid()
			|| !Position->TryGetStringField(TEXT("nodeId"), NodeId)
			|| !TryGetExactInt(Position, TEXT("afterX"), AfterX)
			|| !TryGetExactInt(Position, TEXT("afterY"), AfterY)
			|| ResultNodeIds.Contains(NodeId))
		{
			OutCode = TEXT("native-preview-plan-invalid");
			OutMessage = TEXT("A result position has an invalid shape.");
			return false;
		}
		ResultNodeIds.Add(NodeId);
		ResultAfter.Add(NodeId, FIntPoint(AfterX, AfterY));
	}
	if (ResultNodeIds.Num() != NodeCount
		|| ResultNodeIds.Num() != SnapshotNodeIds.Num()
		|| SnapshotNodeIds.Difference(ResultNodeIds).Num() != 0
		|| ResultNodeIds.Difference(SnapshotNodeIds).Num() != 0)
	{
		OutCode = TEXT("native-preview-plan-invalid");
		OutMessage = TEXT("The result node set differs from the snapshot.");
		return false;
	}
	TSet<FString> PlanNodeIds;
	for (const TSharedPtr<FJsonValue>& Entry : *PlanPositions)
	{
		if (!Entry.IsValid() || Entry->Type != EJson::Object)
		{
			OutCode = TEXT("native-preview-plan-invalid");
			OutMessage = TEXT("A plan position is not an object.");
			return false;
		}
		const TSharedPtr<FJsonObject> Position = Entry->AsObject();
		FString NodeId;
		int32 X = 0;
		int32 Y = 0;
		if (!Position.IsValid()
			|| !Position->TryGetStringField(TEXT("nodeId"), NodeId)
			|| !TryGetExactInt(Position, TEXT("x"), X)
			|| !TryGetExactInt(Position, TEXT("y"), Y)
			|| PlanNodeIds.Contains(NodeId))
		{
			OutCode = TEXT("native-preview-plan-invalid");
			OutMessage = TEXT("A plan position has an invalid shape.");
			return false;
		}
		PlanNodeIds.Add(NodeId);
		const FIntPoint* After = ResultAfter.Find(NodeId);
		if (After == nullptr || After->X != X || After->Y != Y)
		{
			OutCode = TEXT("native-preview-plan-invalid");
			OutMessage = TEXT("The plan positions disagree with the result positions.");
			return false;
		}
	}
	if (PlanNodeIds.Num() != ResultNodeIds.Num() || ResultNodeIds.Difference(PlanNodeIds).Num() != 0)
	{
		OutCode = TEXT("native-preview-plan-invalid");
		OutMessage = TEXT("The plan node set differs from the result.");
		return false;
	}
	TSet<FString> Moved;
	for (const TSharedPtr<FJsonValue>& Entry : *MovedNodeIds)
	{
		if (!Entry.IsValid() || Entry->Type != EJson::String || !ResultNodeIds.Contains(Entry->AsString()))
		{
			OutCode = TEXT("native-preview-plan-invalid");
			OutMessage = TEXT("movedNodeIds contains an unknown node.");
			return false;
		}
		Moved.Add(Entry->AsString());
	}
	if (Moved.Num() != ResultMovedCount || Moved.Num() > ResultNodeIds.Num())
	{
		OutCode = TEXT("native-preview-plan-invalid");
		OutMessage = TEXT("movedCount does not match movedNodeIds.");
		return false;
	}
	FString LiveCode;
	FString LiveMessage;
	if (!RecheckLiveState(LiveCode, LiveMessage))
	{
		OutCode = LiveCode;
		OutMessage = LiveMessage;
		return false;
	}
	OutPlan = Preview;
	OutCode.Reset();
	OutMessage.Reset();
	return true;
}

bool FUEAgentKitNativeLayoutPreviewService::RecheckLiveState(FString& OutCode, FString& OutMessage) const
{
	UBlueprint* Blueprint = nullptr;
	UEdGraph* Graph = nullptr;
	FString ErrorCode;
	FString ErrorMessage;
	if (!UEAgentKitBlueprintGraphLayout::ResolveExactTarget(
			AssetPath, GraphGuid, Blueprint, Graph, ErrorCode, ErrorMessage))
	{
		OutCode = TEXT("native-preview-stale-snapshot");
		OutMessage = TEXT("The exact snapshot graph is no longer loaded.");
		return false;
	}
	TSharedPtr<FJsonObject> Current;
	if (!UEAgentKitBlueprintGraphLayout::BuildPreflight(Blueprint, Graph, Current, ErrorCode, ErrorMessage)
		|| !Current.IsValid())
	{
		OutCode = TEXT("native-preview-stale-snapshot");
		OutMessage = TEXT("The live preflight could not be rebuilt.");
		return false;
	}
	FString CurrentRevision;
	FString CurrentFingerprint;
	bool bDirty = true;
	Current->TryGetStringField(TEXT("packageRevision"), CurrentRevision);
	Current->TryGetStringField(TEXT("graphFingerprint"), CurrentFingerprint);
	Current->TryGetBoolField(TEXT("packageDirty"), bDirty);
	if (bDirty)
	{
		OutCode = TEXT("native-preview-package-dirty");
		OutMessage = TEXT("The package became dirty during the preview.");
		return false;
	}
	if (CurrentRevision != PackageRevision)
	{
		OutCode = TEXT("native-preview-revision-conflict");
		OutMessage = TEXT("The package revision changed during the preview.");
		return false;
	}
	if (CurrentFingerprint != GraphFingerprint)
	{
		OutCode = TEXT("native-preview-fingerprint-conflict");
		OutMessage = TEXT("The graph fingerprint changed during the preview.");
		return false;
	}
	if (GEditor != nullptr && GEditor->PlayWorld != nullptr)
	{
		OutCode = TEXT("native-preview-pie-active");
		OutMessage = TEXT("PIE/SIE started during the preview.");
		return false;
	}
	return true;
}
