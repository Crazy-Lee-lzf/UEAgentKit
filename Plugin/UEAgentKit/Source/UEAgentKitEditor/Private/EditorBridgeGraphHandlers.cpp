#include "EditorBridge.h"
#include "EditorBridgeHandlerUtils.h"
#include "BlueprintContextExporter.h"
#include "BlueprintContextSha256.h"
#include "BlueprintGraphLayoutApply.h"
#include "BlueprintGraphLayoutP7TestOnly.h"

#include "BlueprintEditor.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "Editor.h"
#include "Engine/Blueprint.h"
#include "HAL/PlatformProcess.h"
#include "Interfaces/IPluginManager.h"
#include "Misc/App.h"
#include "Misc/DateTime.h"
#include "Misc/Paths.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "Subsystems/AssetEditorSubsystem.h"
#include "UObject/UObjectGlobals.h"

using namespace UEAgentKitEditorBridgePrivate;

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

namespace
{
	FString PluginBinaryHash()
	{
		const TSharedPtr<IPlugin> Plugin = IPluginManager::Get().FindPlugin(TEXT("UEAgentKit"));
		if (!Plugin.IsValid())
		{
			return FString();
		}
		const FString BinaryPath = FPaths::Combine(
			Plugin->GetBaseDir(),
			TEXT("Binaries"),
			FPlatformProcess::GetBinariesSubdirectory(),
			TEXT("UnrealEditor-UEAgentKitEditor.dll"));
		if (!FPaths::FileExists(BinaryPath))
		{
			return FString();
		}
		FString Digest;
		if (!FBlueprintContextSha256::HashFile(BinaryPath, Digest))
		{
			return FString();
		}
		return FString(TEXT("sha256:")) + Digest;
	}
}

TSharedRef<FJsonObject> FUEAgentKitEditorBridge::BuildBlueprintGraphLayoutPreflightResult() const
{
	// The core adapter already performs all fail-closed checks.  This handler
	// only adds bounded transport metadata and a node cap; it never opens,
	// focuses, loads, saves or mutates an asset.
	constexpr int32 MaxReturnedNodes = 512;
	TSharedPtr<FJsonObject> Preflight;
	FString ErrorCode;
	FString ErrorMessage;
	if (!UEAgentKitBlueprintGraphLayout::BuildFocusedEditorPreflight(
		Preflight,
		ErrorCode,
		ErrorMessage))
	{
		TSharedRef<FJsonObject> Failure = MakeShared<FJsonObject>();
		Failure->SetBoolField(TEXT("available"), false);
		Failure->SetBoolField(TEXT("readOnly"), true);
		Failure->SetStringField(TEXT("source"), TEXT("focused-blueprint-editor"));
		Failure->SetStringField(TEXT("selectionPolicy"), TEXT("most-recently-active-blueprint-editor"));
		Failure->SetStringField(TEXT("focusScope"), TEXT("toolkit-graph-tab"));
		Failure->SetStringField(TEXT("reasonCode"), ErrorCode);
		Failure->SetStringField(TEXT("reason"), ErrorMessage);
		return Failure;
	}

	const FString SchemaClassPath = Preflight->GetStringField(TEXT("schemaClassPath"));
	if (!SchemaClassPath.Contains(TEXT("EdGraphSchema_K2")))
	{
		TSharedRef<FJsonObject> Failure = MakeShared<FJsonObject>();
		Failure->SetBoolField(TEXT("available"), false);
		Failure->SetBoolField(TEXT("readOnly"), true);
		Failure->SetStringField(TEXT("source"), TEXT("focused-blueprint-editor"));
		Failure->SetStringField(TEXT("selectionPolicy"), TEXT("most-recently-active-blueprint-editor"));
		Failure->SetStringField(TEXT("focusScope"), TEXT("toolkit-graph-tab"));
		Failure->SetStringField(
			TEXT("assetPath"),
			Preflight->GetStringField(TEXT("assetPath")));
		Failure->SetStringField(
			TEXT("graphGuid"),
			Preflight->GetStringField(TEXT("graphGuid")));
		Failure->SetStringField(TEXT("reasonCode"), TEXT("graph-layout-graph-not-k2"));
		Failure->SetStringField(
			TEXT("reason"),
			TEXT("The focused Blueprint graph does not use EdGraphSchema_K2."));
		return Failure;
	}

	const TArray<TSharedPtr<FJsonValue>>* AllNodes = nullptr;
	Preflight->TryGetArrayField(TEXT("nodes"), AllNodes);
	const int32 TotalNodeCount = AllNodes != nullptr ? AllNodes->Num() : 0;
	TArray<TSharedPtr<FJsonValue>> ReturnedNodes;
	if (AllNodes != nullptr)
	{
		ReturnedNodes.Reserve(FMath::Min(TotalNodeCount, MaxReturnedNodes));
		for (int32 Index = 0; Index < FMath::Min(TotalNodeCount, MaxReturnedNodes); ++Index)
		{
			ReturnedNodes.Add((*AllNodes)[Index]);
		}
	}
	Preflight->SetArrayField(TEXT("nodes"), ReturnedNodes);
	Preflight->SetNumberField(TEXT("totalNodeCount"), TotalNodeCount);
	Preflight->SetNumberField(TEXT("nodesReturned"), ReturnedNodes.Num());
	Preflight->SetBoolField(TEXT("nodesTruncated"), TotalNodeCount > ReturnedNodes.Num());
	Preflight->SetBoolField(TEXT("complete"), TotalNodeCount == ReturnedNodes.Num());
	Preflight->SetBoolField(TEXT("available"), true);
	Preflight->SetStringField(TEXT("selectionPolicy"), TEXT("most-recently-active-blueprint-editor"));
	Preflight->SetStringField(TEXT("focusScope"), TEXT("toolkit-graph-tab"));
	Preflight->SetStringField(TEXT("sessionId"), SessionId);
	Preflight->SetStringField(TEXT("pluginVersion"), PluginVersion);
	Preflight->SetStringField(TEXT("projectName"), FApp::GetProjectName());
	Preflight->SetStringField(
		TEXT("pieState"),
		GEditor != nullptr && GEditor->PlayWorld != nullptr
			? (GEditor->bIsSimulatingInEditor ? TEXT("simulating") : TEXT("playing"))
			: TEXT("stopped"));
	const FString BuildHash = PluginBinaryHash();
	if (!BuildHash.IsEmpty())
	{
		Preflight->SetStringField(TEXT("pluginBuildHash"), BuildHash);
	}
	return Preflight.ToSharedRef();
}

TSharedRef<FJsonObject> FUEAgentKitEditorBridge::BuildFocusedBlueprintGraphContextResult() const
{
	// Read-only companion to the P6 preflight.  It exports the exact focused
	// graph as the same canonical object used by the commandlet, but never
	// writes it to disk, opens an asset, focuses a graph, or mutates a package.
	constexpr int32 MaxContextBytes = 700 * 1024;
	const TSharedRef<FJsonObject> Preflight = BuildBlueprintGraphLayoutPreflightResult();
	auto Failure = [](const FString& Code, const FString& Message) -> TSharedRef<FJsonObject>
	{
		TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
		Result->SetBoolField(TEXT("available"), false);
		Result->SetBoolField(TEXT("readOnly"), true);
		Result->SetStringField(TEXT("source"), TEXT("focused-blueprint-editor"));
		Result->SetStringField(TEXT("reasonCode"), Code);
		Result->SetStringField(TEXT("reason"), Message);
		return Result;
	};

	if (!Preflight->GetBoolField(TEXT("available")))
	{
		return Failure(
			Preflight->GetStringField(TEXT("reasonCode")),
			Preflight->GetStringField(TEXT("reason")));
	}
	if (!Preflight->GetBoolField(TEXT("complete")))
	{
		return Failure(
			TEXT("graph-layout-context-too-large"),
			TEXT("The focused graph is too large for one complete read-only context response."));
	}

	UBlueprint* Blueprint = nullptr;
	UEdGraph* Graph = nullptr;
	FString ResolveCode;
	FString ResolveMessage;
	if (!UEAgentKitBlueprintGraphLayout::ResolveExactTarget(
		Preflight->GetStringField(TEXT("assetPath")),
		Preflight->GetStringField(TEXT("graphGuid")),
		Blueprint,
		Graph,
		ResolveCode,
		ResolveMessage))
	{
		return Failure(ResolveCode, ResolveMessage);
	}
	if (Blueprint == nullptr
		|| Graph == nullptr
		|| !Graph->GraphGuid.ToString(EGuidFormats::DigitsWithHyphensLower).Equals(
			Preflight->GetStringField(TEXT("graphGuid")),
			ESearchCase::CaseSensitive))
	{
		return Failure(
			TEXT("graph-layout-context-identity-mismatch"),
			TEXT("The focused graph identity changed before the context export."));
	}

	FBlueprintContextExportOptions Options;
	Options.Profile = EBlueprintContextProfile::Full;
	Options.GraphFilter = Graph->GetName();
	Options.bWriteJson = false;
	Options.bWriteBpctx = false;
	Options.bPrettyJson = false;
	FBlueprintContextExporter::ApplyProfileDefaults(Options);
	Options.bIncludeLayout = true;
	Options.bIncludeReflectedNodeProperties = true;
	Options.bIncludeUnchangedDefaults = false;

	FBlueprintContextExportResult ExportResult;
	const TSharedRef<FJsonObject> Context =
		FBlueprintContextExporter::BuildContextObject(Blueprint, Options, ExportResult);
	if (!ExportResult.Error.IsEmpty())
	{
		return Failure(TEXT("graph-layout-context-export-failed"), ExportResult.Error);
	}

	FString CompactContext;
	const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&CompactContext);
	if (!FJsonSerializer::Serialize(Context, Writer))
	{
		return Failure(
			TEXT("graph-layout-context-export-failed"),
			TEXT("The canonical context object could not be serialized."));
	}
	const FTCHARToUTF8 Utf8(*CompactContext);
	if (Utf8.Length() > MaxContextBytes)
	{
		return Failure(
			TEXT("graph-layout-context-too-large"),
			TEXT("The canonical context exceeds the bounded preview transport."));
	}

	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("schemaVersion"), TEXT("ueak.graphlayoutcontext/1.0"));
	Result->SetBoolField(TEXT("available"), true);
	Result->SetBoolField(TEXT("readOnly"), true);
	Result->SetStringField(TEXT("source"), TEXT("focused-blueprint-editor"));
	Result->SetStringField(TEXT("selectionPolicy"), TEXT("most-recently-active-blueprint-editor"));
	Result->SetStringField(TEXT("focusScope"), TEXT("toolkit-graph-tab"));
	Result->SetStringField(TEXT("assetPath"), Blueprint->GetPathName());
	Result->SetStringField(
		TEXT("graphGuid"),
		Graph->GraphGuid.ToString(EGuidFormats::DigitsWithHyphensLower));
	Result->SetStringField(TEXT("graphName"), Graph->GetName());
	Result->SetStringField(
		TEXT("packageRevision"),
		Preflight->GetStringField(TEXT("packageRevision")));
	Result->SetBoolField(
		TEXT("packageDirty"),
		Preflight->GetBoolField(TEXT("packageDirty")));
	Result->SetStringField(
		TEXT("graphFingerprint"),
		Preflight->GetStringField(TEXT("graphFingerprint")));
	Result->SetNumberField(
		TEXT("nodeCount"),
		ExportResult.NodeCount);
	Result->SetNumberField(TEXT("pinCount"), ExportResult.PinCount);
	Result->SetNumberField(TEXT("linkCount"), ExportResult.LinkCount);
	Result->SetNumberField(TEXT("graphCount"), ExportResult.GraphCount);
	Result->SetStringField(TEXT("exportedAtUtc"), FDateTime::UtcNow().ToIso8601());
	Result->SetStringField(TEXT("sessionId"), SessionId);
	Result->SetStringField(TEXT("pluginVersion"), PluginVersion);
	if (Preflight->HasField(TEXT("pluginBuildHash")))
	{
		Result->SetStringField(
			TEXT("pluginBuildHash"),
			Preflight->GetStringField(TEXT("pluginBuildHash")));
	}
	Result->SetStringField(TEXT("projectName"), FApp::GetProjectName());
	Result->SetObjectField(TEXT("context"), Context);
	return Result;
}

bool FUEAgentKitEditorBridge::TryBuildBlueprintGraphLayoutProjectionResult(
	const TSharedPtr<FJsonObject>& Params,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage) const
{
	constexpr int32 MaxProjectionPageBytes = 512 * 1024;
	constexpr int32 MaxProjectionNodes = 256;
	if (!Params.IsValid())
	{
		OutErrorCode = TEXT("live-editor-invalid-parameters");
		OutErrorMessage = TEXT("The layout projection requires one JSON parameter object.");
		return false;
	}
	for (const TPair<FString, TSharedPtr<FJsonValue>>& Pair : Params->Values)
	{
		if (Pair.Key != TEXT("offset")
			&& Pair.Key != TEXT("limit")
			&& Pair.Key != TEXT("expectedPackageRevision")
			&& Pair.Key != TEXT("expectedGraphFingerprint"))
		{
			OutErrorCode = TEXT("live-editor-invalid-parameters");
			OutErrorMessage = FString::Printf(
				TEXT("Unsupported layout projection parameter: %s."),
				*Pair.Key);
			return false;
		}
	}

	auto ReadInt = [&OutErrorCode, &OutErrorMessage](
		const TSharedPtr<FJsonObject>& Object,
		const TCHAR* Name,
		int32 DefaultValue,
		int32 MinValue,
		int32 MaxValue,
		int32& OutValue) -> bool
	{
		const TSharedPtr<FJsonValue>* Field = Object->Values.Find(Name);
		if (Field == nullptr)
		{
			OutValue = DefaultValue;
			return true;
		}
		double Number = 0.0;
		if (!(*Field).IsValid() || !(*Field)->TryGetNumber(Number)
			|| !FMath::IsFinite(Number)
			|| Number != FMath::FloorToDouble(Number)
			|| Number < MinValue
			|| Number > MaxValue)
		{
			OutErrorCode = TEXT("graph-layout-projection-page-invalid");
			OutErrorMessage = FString::Printf(
				TEXT("%s must be an integer from %d through %d."),
				Name,
				MinValue,
				MaxValue);
			return false;
		}
		OutValue = static_cast<int32>(Number);
		return true;
	};

	int32 Offset = 0;
	int32 Limit = 64;
	if (!ReadInt(Params, TEXT("offset"), 0, 0, MAX_int32, Offset)
		|| !ReadInt(Params, TEXT("limit"), 64, 1, MaxProjectionNodes, Limit))
	{
		return false;
	}

	FString ExpectedRevision;
	FString ExpectedFingerprint;
	for (const TCHAR* Name : { TEXT("expectedPackageRevision"), TEXT("expectedGraphFingerprint") })
	{
		const TSharedPtr<FJsonValue>* Field = Params->Values.Find(Name);
		if (Field == nullptr)
		{
			continue;
		}
		FString Value;
		if (!(*Field).IsValid() || !(*Field)->TryGetString(Value))
		{
			OutErrorCode = TEXT("live-editor-invalid-parameters");
			OutErrorMessage = FString::Printf(TEXT("%s must be a string."), Name);
			return false;
		}
		if (FString(Name) == TEXT("expectedPackageRevision"))
		{
			ExpectedRevision = Value;
		}
		else
		{
			ExpectedFingerprint = Value;
		}
	}

	TSharedPtr<FJsonObject> Preflight;
	if (!UEAgentKitBlueprintGraphLayout::BuildFocusedEditorPreflight(
		Preflight,
		OutErrorCode,
		OutErrorMessage))
	{
		return false;
	}
	FString AssetPath;
	FString GraphGuid;
	if (!Preflight->TryGetStringField(TEXT("assetPath"), AssetPath)
		|| !Preflight->TryGetStringField(TEXT("graphGuid"), GraphGuid))
	{
		OutErrorCode = TEXT("graph-layout-context-unavailable");
		OutErrorMessage = TEXT("The focused graph preflight has no exact identity.");
		return false;
	}

	UBlueprint* Blueprint = nullptr;
	UEdGraph* Graph = nullptr;
	if (!UEAgentKitBlueprintGraphLayout::ResolveExactTarget(
		AssetPath,
		GraphGuid,
		Blueprint,
		Graph,
		OutErrorCode,
		OutErrorMessage))
	{
		return false;
	}

	TSharedPtr<FJsonObject> Projection;
	if (!UEAgentKitBlueprintGraphLayout::BuildLayoutProjection(
		Blueprint,
		Graph,
		Offset,
		Limit,
		ExpectedRevision,
		ExpectedFingerprint,
		Projection,
		OutErrorCode,
		OutErrorMessage))
	{
		return false;
	}

	FString Serialized;
	const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Serialized);
	if (!FJsonSerializer::Serialize(Projection.ToSharedRef(), Writer))
	{
		OutErrorCode = TEXT("graph-layout-projection-serialize-failed");
		OutErrorMessage = TEXT("The layout projection page could not be serialized.");
		return false;
	}
	FTCHARToUTF8 Utf8(*Serialized);
	if (Utf8.Length() > MaxProjectionPageBytes)
	{
		OutErrorCode = TEXT("graph-layout-projection-page-too-large");
		OutErrorMessage = FString::Printf(
			TEXT("The layout projection page exceeds the %d byte page budget; retry with a smaller limit."),
			MaxProjectionPageBytes);
		return false;
	}
	OutResult = Projection;
	return true;
}

bool FUEAgentKitEditorBridge::BuildP8MenuPreviewInputs(
	TSharedPtr<FJsonObject>& OutPreflight,
	TSharedPtr<FJsonObject>& OutEnvelope,
	FString& OutErrorCode,
	FString& OutErrorMessage) const
{
	OutPreflight.Reset();
	OutEnvelope.Reset();
	const TSharedRef<FJsonObject> Context = BuildFocusedBlueprintGraphContextResult();
	if (!Context->GetBoolField(TEXT("available")))
	{
		OutErrorCode = Context->GetStringField(TEXT("reasonCode"));
		OutErrorMessage = Context->GetStringField(TEXT("reason"));
		return false;
	}
	const TSharedRef<FJsonObject> Preflight = BuildBlueprintGraphLayoutPreflightResult();
	if (!Preflight->GetBoolField(TEXT("available")))
	{
		OutErrorCode = Preflight->GetStringField(TEXT("reasonCode"));
		OutErrorMessage = Preflight->GetStringField(TEXT("reason"));
		return false;
	}
	// The P6 preflight intentionally has no session/build transport metadata.
	// The context response does; copy it into the worker's preflight object so
	// build_preview can prove live-editor provenance rather than invent one.
	Preflight->SetStringField(
		TEXT("sessionId"),
		Context->GetStringField(TEXT("sessionId")));
	Preflight->SetStringField(
		TEXT("pluginBuildHash"),
		Context->GetStringField(TEXT("pluginBuildHash")));

	const TSharedRef<FJsonObject> Envelope = MakeShared<FJsonObject>();
	Envelope->SetStringField(TEXT("schemaVersion"), TEXT("ueak.graphlayoutcontext/1.0"));
	Envelope->SetBoolField(TEXT("available"), true);
	Envelope->SetBoolField(TEXT("readOnly"), true);
	Envelope->SetStringField(TEXT("assetPath"), Context->GetStringField(TEXT("assetPath")));
	Envelope->SetStringField(TEXT("graphGuid"), Context->GetStringField(TEXT("graphGuid")));
	Envelope->SetStringField(TEXT("packageRevision"), Context->GetStringField(TEXT("packageRevision")));
	Envelope->SetBoolField(TEXT("packageDirty"), Context->GetBoolField(TEXT("packageDirty")));
	Envelope->SetStringField(TEXT("graphFingerprint"), Context->GetStringField(TEXT("graphFingerprint")));
	Envelope->SetStringField(TEXT("exportedAtUtc"), Context->GetStringField(TEXT("exportedAtUtc")));
	const TSharedPtr<FJsonObject>* ContextObject = nullptr;
	if (!Context->TryGetObjectField(TEXT("context"), ContextObject)
		|| ContextObject == nullptr
		|| !ContextObject->IsValid())
	{
		OutErrorCode = TEXT("graph-layout-context-missing");
		OutErrorMessage = TEXT("The read-only context has no canonical export object.");
		return false;
	}
	Envelope->SetObjectField(TEXT("context"), *ContextObject);
	OutPreflight = Preflight;
	OutEnvelope = Envelope;
	OutErrorCode.Reset();
	OutErrorMessage.Reset();
	return true;
}

bool FUEAgentKitEditorBridge::BuildP9MenuPreviewInputs(
	TSharedPtr<FJsonObject>& OutPreflight,
	TSharedPtr<FJsonObject>& OutEnvelope,
	FString& OutErrorCode,
	FString& OutErrorMessage) const
{
	// The canonical read-only preflight/context envelope is host-agnostic.
	// The P9 menu still gates startup and execution on its own exact project,
	// marker, binary hash, asset, and GraphGuid policy.
	return BuildP8MenuPreviewInputs(
		OutPreflight,
		OutEnvelope,
		OutErrorCode,
		OutErrorMessage);
}

bool FUEAgentKitEditorBridge::TryBuildBlueprintGraphLayoutApplyTestOnlyResult(
	const TSharedPtr<FJsonObject>& Params,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage) const
{
	return UEAgentKitBlueprintGraphLayoutP7::TryApplyTestOnly(
		Params,
		OutResult,
		OutErrorCode,
		OutErrorMessage);
}

bool FUEAgentKitEditorBridge::TryBuildBlueprintGraphLayoutSaveTestOnlyResult(
	const TSharedPtr<FJsonObject>& Params,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage) const
{
	return UEAgentKitBlueprintGraphLayoutP7::TrySaveTestOnly(
		Params,
		OutResult,
		OutErrorCode,
		OutErrorMessage);
}

bool FUEAgentKitEditorBridge::TryBuildBlueprintGraphContextResult(
	const TSharedPtr<FJsonObject>& Params,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage) const
{
	// Read any Blueprint's graph context by asset path without requiring an open
	// or focused Blueprint Editor.  The asset may be loaded on demand, but this
	// handler never dirties, saves, opens a tab, focuses a graph or mutates a
	// package.  Node pagination keeps the response bounded.
	constexpr int32 MaxOffset = 1000000;
	constexpr int32 MaxLimit = 500;
	constexpr int32 DefaultLimit = 200;

	auto Reject = [&OutErrorCode, &OutErrorMessage](
		const TCHAR* Code,
		const FString& Message) -> bool
	{
		OutErrorCode = Code;
		OutErrorMessage = Message;
		return false;
	};

	if (!Params.IsValid())
	{
		return Reject(
			TEXT("blueprint-graph-context-invalid-parameters"),
			TEXT("The request must be a JSON object."));
	}
	for (const TPair<FString, TSharedPtr<FJsonValue>>& Entry : Params->Values)
	{
		if (Entry.Key != TEXT("assetPath")
			&& Entry.Key != TEXT("graphGuid")
			&& Entry.Key != TEXT("offset")
			&& Entry.Key != TEXT("limit"))
		{
			return Reject(
				TEXT("blueprint-graph-context-invalid-parameters"),
				FString::Printf(TEXT("Unsupported field: %s"), *Entry.Key));
		}
	}

	FString AssetPath;
	if (!Params->TryGetStringField(TEXT("assetPath"), AssetPath)
		|| !UEAgentKitEditorBridgePrivate::IsSafeGameAssetPath(AssetPath))
	{
		return Reject(
			TEXT("blueprint-graph-context-invalid-parameters"),
			TEXT("assetPath must be one exact /Game Object Path."));
	}

	FString GraphGuidText;
	Params->TryGetStringField(TEXT("graphGuid"), GraphGuidText);
	FGuid RequestedGraphGuid;
	if (!GraphGuidText.IsEmpty() && !FGuid::Parse(GraphGuidText, RequestedGraphGuid))
	{
		return Reject(
			TEXT("blueprint-graph-context-invalid-parameters"),
			TEXT("graphGuid must be one canonical hyphenated GUID."));
	}

	int32 Offset = 0;
	int32 Limit = DefaultLimit;
	Params->TryGetNumberField(TEXT("offset"), Offset);
	Params->TryGetNumberField(TEXT("limit"), Limit);
	if (Offset < 0 || Offset > MaxOffset || Limit < 1 || Limit > MaxLimit)
	{
		return Reject(
			TEXT("blueprint-graph-context-invalid-parameters"),
			FString::Printf(
				TEXT("offset must be 0..%d and limit must be 1..%d."),
				MaxOffset,
				MaxLimit));
	}

	UBlueprint* Blueprint = Cast<UBlueprint>(
		StaticFindObject(UObject::StaticClass(), nullptr, *AssetPath, false));
	const bool bLoadedByRequest = Blueprint == nullptr;
	if (Blueprint == nullptr)
	{
		Blueprint = LoadObject<UBlueprint>(nullptr, *AssetPath);
	}
	if (Blueprint == nullptr)
	{
		return Reject(
			TEXT("blueprint-graph-context-asset-unavailable"),
			TEXT("The requested Blueprint could not be found or loaded."));
	}

	UEdGraph* TargetGraph = nullptr;
	if (RequestedGraphGuid.IsValid())
	{
		auto SearchGraphs = [&TargetGraph, &RequestedGraphGuid](const auto& Graphs)
		{
			for (UEdGraph* Candidate : Graphs)
			{
				if (Candidate != nullptr && Candidate->GraphGuid == RequestedGraphGuid)
				{
					TargetGraph = Candidate;
					return;
				}
			}
		};
		SearchGraphs(Blueprint->UbergraphPages);
		if (TargetGraph == nullptr)
		{
			SearchGraphs(Blueprint->FunctionGraphs);
		}
		if (TargetGraph == nullptr)
		{
			SearchGraphs(Blueprint->MacroGraphs);
		}
		if (TargetGraph == nullptr)
		{
			SearchGraphs(Blueprint->DelegateSignatureGraphs);
		}
		if (TargetGraph == nullptr)
		{
			return Reject(
				TEXT("blueprint-graph-context-graph-not-found"),
				TEXT("The requested GraphGuid is not part of this Blueprint."));
		}
	}

	FBlueprintContextExportOptions Options;
	Options.Profile = EBlueprintContextProfile::Full;
	Options.bWriteJson = false;
	Options.bWriteBpctx = false;
	Options.bIncludeLayout = false;
	if (TargetGraph != nullptr)
	{
		Options.GraphFilter = TargetGraph->GetName();
	}
	FBlueprintContextExportResult ExportResult;
	const TSharedRef<FJsonObject> Context =
		FBlueprintContextExporter::BuildContextObject(Blueprint, Options, ExportResult);
	// The exporter signals failure with a non-empty Error; bSuccess is only set on the
	// file-export path (ExportBlueprint). Gate on Error, matching the layout handler above.
	if (!ExportResult.Error.IsEmpty())
	{
		UE_LOG(
			LogTemp,
			Warning,
			TEXT("UEAgentKit getBlueprintGraphContext failed for '%s': %s"),
			*AssetPath,
			*ExportResult.Error);
		return Reject(TEXT("blueprint-graph-context-export-failed"), ExportResult.Error);
	}

	// Per-graph node pagination.  The object is the same canonical context the
	// offline exporter writes; only the node arrays are sliced.
	int32 TotalNodeCount = 0;
	int32 ReturnedNodeCount = 0;
	const TArray<TSharedPtr<FJsonValue>>* Graphs = nullptr;
	if (Context->TryGetArrayField(TEXT("graphs"), Graphs) && Graphs != nullptr)
	{
		for (const TSharedPtr<FJsonValue>& GraphValue : *Graphs)
		{
			const TSharedPtr<FJsonObject> GraphObject =
				GraphValue.IsValid() ? GraphValue->AsObject() : nullptr;
			if (!GraphObject.IsValid())
			{
				continue;
			}
			const TArray<TSharedPtr<FJsonValue>>* Nodes = nullptr;
			if (!GraphObject->TryGetArrayField(TEXT("nodes"), Nodes) || Nodes == nullptr)
			{
				continue;
			}
			TotalNodeCount += Nodes->Num();
			TArray<TSharedPtr<FJsonValue>> Page;
			for (int32 Index = Offset; Index < Nodes->Num() && Page.Num() < Limit; ++Index)
			{
				Page.Add((*Nodes)[Index]);
			}
			ReturnedNodeCount += Page.Num();
			GraphObject->SetArrayField(TEXT("nodes"), Page);
		}
	}

	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("schemaVersion"), TEXT("ueak.blueprintgraphcontext/1.0"));
	Result->SetBoolField(TEXT("available"), true);
	Result->SetBoolField(TEXT("readOnly"), true);
	Result->SetStringField(TEXT("editorSessionId"), SessionId);
	Result->SetStringField(TEXT("assetPath"), AssetPath);
	Result->SetStringField(
		TEXT("graphGuid"),
		TargetGraph != nullptr
			? TargetGraph->GraphGuid.ToString(EGuidFormats::DigitsWithHyphensLower)
			: FString());
	Result->SetBoolField(TEXT("loadedByRequest"), bLoadedByRequest);
	Result->SetBoolField(
		TEXT("packageDirty"),
		Blueprint->GetOutermost() != nullptr && Blueprint->GetOutermost()->IsDirty());
	Result->SetNumberField(TEXT("nodeOffset"), Offset);
	Result->SetNumberField(TEXT("nodeLimit"), Limit);
	Result->SetNumberField(TEXT("totalNodeCount"), TotalNodeCount);
	Result->SetNumberField(TEXT("returnedNodeCount"), ReturnedNodeCount);
	Result->SetBoolField(TEXT("truncated"), ReturnedNodeCount < TotalNodeCount);
	Result->SetObjectField(TEXT("context"), Context);
	OutResult = Result;
	OutErrorCode.Reset();
	OutErrorMessage.Reset();
	return true;
}
