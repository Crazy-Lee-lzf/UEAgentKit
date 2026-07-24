#include "EditorBridge.h"
#include "EditorBridgeHandlerUtils.h"

#include "BlueprintEditor.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "Editor.h"
#include "Engine/Blueprint.h"
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
