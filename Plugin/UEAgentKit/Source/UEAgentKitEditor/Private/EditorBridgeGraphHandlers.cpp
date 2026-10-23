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
