// Native safety gates for connecting a new GraphEdit node to an existing pin.
//
//  1. PIE active: Preview must fail with graph-edit-pie-active.
//  2. Stale graph after Preview: mutate an existing edge before Apply; Apply
//     must fail closed (plan/edge-set stale) and the cleanup restores the edge.

#include "BlueprintEditor.h"
#include "BlueprintContextSha256.h"
#include "BlueprintGraphEditExperimental.h"

#include "Dom/JsonObject.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "EdGraphSchema_K2.h"
#include "Editor.h"
#include "Engine/Blueprint.h"
#include "HAL/PlatformTime.h"
#include "K2Node_Event.h"
#include "K2Node_IfThenElse.h"
#include "K2Node_VariableGet.h"
#include "Misc/AutomationTest.h"
#include "Misc/Guid.h"
#include "Subsystems/AssetEditorSubsystem.h"
#include "Tests/AutomationEditorCommon.h"
#include "UObject/Package.h"

#if WITH_DEV_AUTOMATION_TESTS

namespace UEAgentKitConnectExistingSafety
{
	const TCHAR* const FixtureAssetPath =
		TEXT("/Game/UEAgentKitAgentE2E/BP_AccessorE2E.BP_AccessorE2E");

	FString GuidLower(const FGuid& Guid)
	{
		return Guid.ToString(EGuidFormats::DigitsWithHyphensLower);
	}

	UBlueprint* LoadFixture(FString& OutError)
	{
		UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, FixtureAssetPath);
		if (Blueprint == nullptr)
		{
			OutError = FString::Printf(TEXT("Could not load %s."), FixtureAssetPath);
		}
		return Blueprint;
	}

	FBlueprintEditor* OpenAndFocus(UBlueprint* Blueprint, UEdGraph* Graph, FString& OutError)
	{
		UAssetEditorSubsystem* Subsystem = GEditor != nullptr
			? GEditor->GetEditorSubsystem<UAssetEditorSubsystem>()
			: nullptr;
		if (Subsystem == nullptr)
		{
			OutError = TEXT("No AssetEditorSubsystem.");
			return nullptr;
		}
		Subsystem->OpenEditorForAsset(Blueprint);
		IAssetEditorInstance* Instance = Subsystem->FindEditorForAsset(Blueprint, false);
		if (Instance == nullptr || Instance->GetEditorName() != FName(TEXT("BlueprintEditor")))
		{
			OutError = TEXT("No BlueprintEditor for the fixture.");
			return nullptr;
		}
		FBlueprintEditor* Editor = static_cast<FBlueprintEditor*>(Instance);
		if (!Editor->OpenGraphAndBringToFront(Graph, true).IsValid()
			|| Editor->GetFocusedGraph() != Graph)
		{
			OutError = TEXT("Could not focus the fixture event graph.");
			return nullptr;
		}
		return Editor;
	}

	UK2Node_IfThenElse* FindBranch(UEdGraph* Graph)
	{
		for (UEdGraphNode* Node : Graph->Nodes)
		{
			if (UK2Node_IfThenElse* Branch = Cast<UK2Node_IfThenElse>(Node))
			{
				return Branch;
			}
		}
		return nullptr;
	}

	UK2Node_IfThenElse* FindBranchWithUnlinkedExecute(UEdGraph* Graph)
	{
		for (UEdGraphNode* Node : Graph->Nodes)
		{
			UK2Node_IfThenElse* Branch = Cast<UK2Node_IfThenElse>(Node);
			if (Branch == nullptr)
			{
				continue;
			}
			const UEdGraphPin* Execute = Branch->FindPin(TEXT("execute"), EGPD_Input);
			if (Execute != nullptr && Execute->LinkedTo.Num() == 0)
			{
				return Branch;
			}
		}
		return nullptr;
	}

	UK2Node_Event* FindEventWithUnlinkedThen(UEdGraph* Graph)
	{
		for (UEdGraphNode* Node : Graph->Nodes)
		{
			UK2Node_Event* Event = Cast<UK2Node_Event>(Node);
			if (Event == nullptr)
			{
				continue;
			}
			const UEdGraphPin* ThenPin = Event->FindPin(TEXT("then"), EGPD_Output);
			if (ThenPin != nullptr && ThenPin->LinkedTo.Num() == 0)
			{
				return Event;
			}
		}
		return nullptr;
	}

	const FBPVariableDescription* FindVariable(UBlueprint* Blueprint, const FName Name)
	{
		return Blueprint != nullptr
			? Blueprint->NewVariables.FindByPredicate(
				[Name](const FBPVariableDescription& Variable)
				{
					return Variable.VarName == Name;
				})
			: nullptr;
	}

	TSharedPtr<FJsonObject> MakeExistingPin(
		const UEdGraphNode* Node,
		const UEdGraphPin* Pin)
	{
		TSharedRef<FJsonObject> Object = MakeShared<FJsonObject>();
		Object->SetStringField(TEXT("nodeGuid"), GuidLower(Node->NodeGuid));
		Object->SetStringField(TEXT("pinGuid"), GuidLower(Pin->PinId));
		Object->SetStringField(TEXT("pinName"), Pin->PinName.ToString());
		Object->SetStringField(
			TEXT("direction"),
			Pin->Direction == EGPD_Input ? TEXT("input") : TEXT("output"));
		Object->SetStringField(TEXT("category"), Pin->PinType.PinCategory.ToString());
		Object->SetStringField(TEXT("subCategory"), Pin->PinType.PinSubCategory.ToString());
		return Object;
	}

	TSharedPtr<FJsonObject> MakePreviewParams(
		UBlueprint* Blueprint,
		UEdGraph* Graph,
		const FBPVariableDescription& Variable,
		const UEdGraphNode* ExistingNode,
		const UEdGraphPin* ExistingInput)
	{
		TSharedRef<FJsonObject> Node = MakeShared<FJsonObject>();
		Node->SetStringField(TEXT("tempId"), TEXT("tmp:connect"));
		Node->SetStringField(TEXT("nodeType"), TEXT("variable_get"));
		Node->SetNumberField(TEXT("x"), 0);
		Node->SetNumberField(TEXT("y"), 0);
		Node->SetStringField(TEXT("variableName"), Variable.VarName.ToString());
		Node->SetStringField(TEXT("variableGuid"), GuidLower(Variable.VarGuid));
		Node->SetStringField(TEXT("variableType"), TEXT("bool"));

		TSharedRef<FJsonObject> Connection = MakeShared<FJsonObject>();
		Connection->SetStringField(TEXT("from"), TEXT("tmp:connect"));
		Connection->SetStringField(TEXT("fromPin"), Variable.VarName.ToString());
		Connection->SetObjectField(TEXT("toExisting"), MakeExistingPin(ExistingNode, ExistingInput));

		TSharedRef<FJsonObject> Params = MakeShared<FJsonObject>();
		Params->SetStringField(TEXT("assetPath"), Blueprint->GetPathName());
		Params->SetStringField(TEXT("graphGuid"), GuidLower(Graph->GraphGuid));
		TArray<TSharedPtr<FJsonValue>> Nodes;
		Nodes.Add(MakeShared<FJsonValueObject>(Node));
		Params->SetArrayField(TEXT("nodes"), Nodes);
		TArray<TSharedPtr<FJsonValue>> Connections;
		Connections.Add(MakeShared<FJsonValueObject>(Connection));
		Params->SetArrayField(TEXT("connections"), Connections);
		return Params;
	}

	TSharedPtr<FJsonObject> MakePureExistingPreviewParams(
		UBlueprint* Blueprint,
		UEdGraph* Graph,
		const UEdGraphNode* SourceNode,
		const UEdGraphPin* SourceOutput,
		const UEdGraphNode* TargetNode,
		const UEdGraphPin* TargetInput,
		const TCHAR* Operation = TEXT("connect"))
	{
		TSharedRef<FJsonObject> Connection = MakeShared<FJsonObject>();
		Connection->SetObjectField(TEXT("fromExisting"), MakeExistingPin(SourceNode, SourceOutput));
		Connection->SetObjectField(TEXT("toExisting"), MakeExistingPin(TargetNode, TargetInput));
		TSharedRef<FJsonObject> Params = MakeShared<FJsonObject>();
		Params->SetStringField(TEXT("assetPath"), Blueprint->GetPathName());
		Params->SetStringField(TEXT("graphGuid"), GuidLower(Graph->GraphGuid));
		Params->SetStringField(TEXT("operation"), Operation);
		Params->SetArrayField(TEXT("nodes"), TArray<TSharedPtr<FJsonValue>>());
		TArray<TSharedPtr<FJsonValue>> Connections;
		Connections.Add(MakeShared<FJsonValueObject>(Connection));
		Params->SetArrayField(TEXT("connections"), Connections);
		return Params;
	}

	struct FExistingEdge
	{
		UEdGraphPin* From = nullptr;
		UEdGraphPin* To = nullptr;
	};

	void CollectExistingEdges(UEdGraph* Graph, TArray<FExistingEdge>& OutEdges)
	{
		OutEdges.Reset();
		if (Graph == nullptr)
		{
			return;
		}
		for (UEdGraphNode* Node : Graph->Nodes)
		{
			if (Node == nullptr)
			{
				continue;
			}
			for (UEdGraphPin* Pin : Node->Pins)
			{
				if (Pin == nullptr || Pin->Direction != EGPD_Output)
				{
					continue;
				}
				for (UEdGraphPin* Linked : Pin->LinkedTo)
				{
					if (Linked != nullptr)
					{
						OutEdges.Add({ Pin, Linked });
					}
				}
			}
		}
	}

	bool SetupDisconnectPreview(
		FAutomationTestBase* Test,
		UBlueprint*& OutBlueprint,
		UEdGraph*& OutGraph,
		TSharedPtr<FJsonObject>& OutPreviewParams,
		TSharedPtr<FJsonObject>& OutPreview,
		FExistingEdge& OutTarget,
		FExistingEdge& OutProtected,
		FString& OutError)
	{
		OutBlueprint = LoadFixture(OutError);
		if (OutBlueprint == nullptr)
		{
			Test->AddError(OutError);
			return false;
		}
		OutGraph = OutBlueprint->UbergraphPages.Num() > 0
			? OutBlueprint->UbergraphPages[0]
			: nullptr;
		if (OutGraph == nullptr || OpenAndFocus(OutBlueprint, OutGraph, OutError) == nullptr)
		{
			Test->AddError(OutError);
			return false;
		}
		TArray<FExistingEdge> Edges;
		CollectExistingEdges(OutGraph, Edges);
		if (Edges.Num() < 2)
		{
			Test->AddError(TEXT("The fixture must contain at least two existing edges."));
			return false;
		}
		OutTarget = Edges[0];
		OutProtected = Edges[1];
		OutPreviewParams = MakePureExistingPreviewParams(
			OutBlueprint,
			OutGraph,
			OutTarget.From->GetOwningNode(),
			OutTarget.From,
			OutTarget.To->GetOwningNode(),
			OutTarget.To,
			TEXT("disconnect"));
		FString ErrorCode;
		FString ErrorMessage;
		if (!UEAgentKitBlueprintGraphEditExperimental::TryPreviewExperimental(
			OutPreviewParams,
			OutPreview,
			ErrorCode,
			ErrorMessage))
		{
			Test->AddError(FString::Printf(TEXT("Disconnect Preview failed: %s %s"), *ErrorCode, *ErrorMessage));
			return false;
		}
		return true;
	}

	TSharedPtr<FJsonObject> MakeCallFunctionPreviewParams(
		UBlueprint* Blueprint,
		UEdGraph* Graph,
		const UEdGraphNode* TargetNode,
		const UEdGraphPin* TargetInput)
	{
		TSharedRef<FJsonObject> CallFunction = MakeShared<FJsonObject>();
		CallFunction->SetStringField(
			TEXT("ownerClassPath"),
			TEXT("/Script/Engine.KismetMathLibrary"));
		CallFunction->SetStringField(TEXT("functionName"), TEXT("Greater_IntInt"));
		CallFunction->SetStringField(
			TEXT("signatureDigest"),
			TEXT("sha256:0f41d587fab8cfbbc431b8dcf4b6d150b0f3dff6438ae3e5e731a932ad26ea09"));
		TArray<TSharedPtr<FJsonValue>> Pins;
		auto AddPin = [&Pins](const TCHAR* Name, const TCHAR* Direction, const TCHAR* Type)
		{
			TSharedRef<FJsonObject> Pin = MakeShared<FJsonObject>();
			Pin->SetStringField(TEXT("name"), Name);
			Pin->SetStringField(TEXT("direction"), Direction);
			Pin->SetStringField(TEXT("type"), Type);
			Pins.Add(MakeShared<FJsonValueObject>(Pin));
		};
		AddPin(TEXT("A"), TEXT("input"), TEXT("int"));
		AddPin(TEXT("B"), TEXT("input"), TEXT("int"));
		AddPin(TEXT("ReturnValue"), TEXT("output"), TEXT("bool"));
		CallFunction->SetArrayField(TEXT("pins"), Pins);

		TSharedRef<FJsonObject> Node = MakeShared<FJsonObject>();
		Node->SetStringField(TEXT("tempId"), TEXT("tmp:greater"));
		Node->SetStringField(TEXT("nodeType"), TEXT("call_function"));
		Node->SetNumberField(TEXT("x"), 300);
		Node->SetNumberField(TEXT("y"), 600);
		Node->SetObjectField(TEXT("callFunction"), CallFunction);

		TSharedRef<FJsonObject> Connection = MakeShared<FJsonObject>();
		Connection->SetStringField(TEXT("from"), TEXT("tmp:greater"));
		Connection->SetStringField(TEXT("fromPin"), TEXT("ReturnValue"));
		Connection->SetObjectField(
			TEXT("toExisting"),
			MakeExistingPin(TargetNode, TargetInput));

		TSharedRef<FJsonObject> Params = MakeShared<FJsonObject>();
		Params->SetStringField(TEXT("assetPath"), Blueprint->GetPathName());
		Params->SetStringField(TEXT("graphGuid"), GuidLower(Graph->GraphGuid));
		TArray<TSharedPtr<FJsonValue>> Nodes;
		Nodes.Add(MakeShared<FJsonValueObject>(Node));
		Params->SetArrayField(TEXT("nodes"), Nodes);
		TArray<TSharedPtr<FJsonValue>> Connections;
		Connections.Add(MakeShared<FJsonValueObject>(Connection));
		Params->SetArrayField(TEXT("connections"), Connections);
		return Params;
	}

	TSharedPtr<FJsonObject> MakeOwnedPurePreviewParams(
		UBlueprint* Blueprint,
		UEdGraph* Graph,
		const UEdGraphNode* TargetNode,
		const UEdGraphPin* TargetInput)
	{
		UEdGraph* FunctionGraph = nullptr;
		for (UEdGraph* Candidate : Blueprint->FunctionGraphs)
		{
			if (Candidate != nullptr && Candidate->GetName() == TEXT("OwnedPureFlag"))
			{
				FunctionGraph = Candidate;
				break;
			}
		}
		if (FunctionGraph == nullptr)
		{
			return nullptr;
		}
		const FString GraphGuid = FunctionGraph->GraphGuid.ToString(
			EGuidFormats::DigitsWithHyphensLower);
		const FString Canonical = Blueprint->GetPathName()
			+ TEXT("|")
			+ GraphGuid
			+ TEXT("|OwnedPureFlag|A:input:int|ReturnValue:output:bool");
		FString Digest;
		if (!FBlueprintContextSha256::HashText(Canonical, Digest))
		{
			return nullptr;
		}

		TSharedRef<FJsonObject> OwnedCall = MakeShared<FJsonObject>();
		OwnedCall->SetStringField(TEXT("owningBlueprintPath"), Blueprint->GetPathName());
		OwnedCall->SetStringField(TEXT("functionGraphGuid"), GraphGuid);
		OwnedCall->SetStringField(TEXT("functionName"), TEXT("OwnedPureFlag"));
		OwnedCall->SetStringField(TEXT("signatureDigest"), TEXT("sha256:") + Digest);
		TArray<TSharedPtr<FJsonValue>> Pins;
		auto AddPin = [&Pins](const TCHAR* Name, const TCHAR* Direction, const TCHAR* Type)
		{
			TSharedRef<FJsonObject> Pin = MakeShared<FJsonObject>();
			Pin->SetStringField(TEXT("name"), Name);
			Pin->SetStringField(TEXT("direction"), Direction);
			Pin->SetStringField(TEXT("type"), Type);
			Pins.Add(MakeShared<FJsonValueObject>(Pin));
		};
		AddPin(TEXT("A"), TEXT("input"), TEXT("int"));
		AddPin(TEXT("ReturnValue"), TEXT("output"), TEXT("bool"));
		OwnedCall->SetArrayField(TEXT("pins"), Pins);

		TSharedRef<FJsonObject> Node = MakeShared<FJsonObject>();
		Node->SetStringField(TEXT("tempId"), TEXT("tmp:owned"));
		Node->SetStringField(TEXT("nodeType"), TEXT("call_owned_pure_function"));
		Node->SetNumberField(TEXT("x"), 300);
		Node->SetNumberField(TEXT("y"), 700);
		Node->SetObjectField(TEXT("ownedCall"), OwnedCall);
		TArray<TSharedPtr<FJsonValue>> Literals;
		TSharedRef<FJsonObject> Literal = MakeShared<FJsonObject>();
		Literal->SetStringField(TEXT("pin"), TEXT("A"));
		Literal->SetStringField(TEXT("type"), TEXT("int"));
		Literal->SetStringField(TEXT("value"), TEXT("42"));
		Literals.Add(MakeShared<FJsonValueObject>(Literal));
		Node->SetArrayField(TEXT("inputLiterals"), Literals);

		TSharedRef<FJsonObject> Connection = MakeShared<FJsonObject>();
		Connection->SetStringField(TEXT("from"), TEXT("tmp:owned"));
		Connection->SetStringField(TEXT("fromPin"), TEXT("ReturnValue"));
		Connection->SetObjectField(
			TEXT("toExisting"),
			MakeExistingPin(TargetNode, TargetInput));

		TSharedRef<FJsonObject> Params = MakeShared<FJsonObject>();
		Params->SetStringField(TEXT("assetPath"), Blueprint->GetPathName());
		Params->SetStringField(TEXT("graphGuid"), GuidLower(Graph->GraphGuid));
		TArray<TSharedPtr<FJsonValue>> Nodes;
		Nodes.Add(MakeShared<FJsonValueObject>(Node));
		Params->SetArrayField(TEXT("nodes"), Nodes);
		TArray<TSharedPtr<FJsonValue>> Connections;
		Connections.Add(MakeShared<FJsonValueObject>(Connection));
		Params->SetArrayField(TEXT("connections"), Connections);
		return Params;
	}

	bool SetupPreview(
		FAutomationTestBase* Test,
		UBlueprint*& OutBlueprint,
		UEdGraph*& OutGraph,
		TSharedPtr<FJsonObject>& OutPreviewParams,
		TSharedPtr<FJsonObject>& OutPreview,
		FString& OutError)
	{
		OutBlueprint = LoadFixture(OutError);
		if (OutBlueprint == nullptr)
		{
			Test->AddError(OutError);
			return false;
		}
		OutGraph = OutBlueprint->UbergraphPages.Num() > 0
			? OutBlueprint->UbergraphPages[0]
			: nullptr;
		if (OutGraph == nullptr || OpenAndFocus(OutBlueprint, OutGraph, OutError) == nullptr)
		{
			Test->AddError(OutError);
			return false;
		}
		UK2Node_IfThenElse* Branch = FindBranch(OutGraph);
		const FBPVariableDescription* Variable = FindVariable(OutBlueprint, TEXT("AccessBool"));
		UEdGraphPin* Condition = Branch != nullptr
			? Branch->FindPin(TEXT("Condition"), EGPD_Input)
			: nullptr;
		if (Branch == nullptr || Variable == nullptr || Condition == nullptr)
		{
			Test->AddError(TEXT("The fixture lacks the Branch/AccessBool endpoint."));
			return false;
		}
		OutPreviewParams = MakePreviewParams(
			OutBlueprint,
			OutGraph,
			*Variable,
			Branch,
			Condition);
		FString ErrorCode;
		FString ErrorMessage;
		if (!UEAgentKitBlueprintGraphEditExperimental::TryPreviewExperimental(
			OutPreviewParams,
			OutPreview,
			ErrorCode,
			ErrorMessage))
		{
			Test->AddError(FString::Printf(TEXT("Preview failed: %s %s"), *ErrorCode, *ErrorMessage));
			return false;
		}
		return true;
	}
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitConnectExistingStalePlanTest,
	"UEAgentKit.Blueprint.ConnectExistingAStalePlanGate",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitConnectExistingStalePlanTest::RunTest(const FString& Parameters)
{
	using namespace UEAgentKitConnectExistingSafety;
	UEAgentKitBlueprintGraphEditExperimental::SetExperimentalWriteEnabledForTest(true);

	UBlueprint* Blueprint = nullptr;
	UEdGraph* Graph = nullptr;
	TSharedPtr<FJsonObject> PreviewParams;
	TSharedPtr<FJsonObject> Preview;
	FString Error;
	if (!SetupPreview(this, Blueprint, Graph, PreviewParams, Preview, Error))
	{
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}
	UK2Node_IfThenElse* Branch = FindBranch(Graph);
	if (!TestNotNull(TEXT("branch exists"), Branch))
	{
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}

	// Mutate the graph after Preview; the plan must not silently apply.
	const int32 OldX = Branch->NodePosX;
	Branch->Modify();
	Branch->NodePosX = OldX + 5;
	// Keep the package "clean" so the test reaches the plan/fingerprint/edge-set
	// gate instead of stopping at graph-edit-package-dirty.
	Blueprint->GetOutermost()->SetDirtyFlag(false);

	TSharedPtr<FJsonObject> ApplyParams = MakeShared<FJsonObject>();
	ApplyParams->SetStringField(TEXT("assetPath"), PreviewParams->GetStringField(TEXT("assetPath")));
	ApplyParams->SetStringField(TEXT("graphGuid"), PreviewParams->GetStringField(TEXT("graphGuid")));
	ApplyParams->SetArrayField(TEXT("nodes"), PreviewParams->GetArrayField(TEXT("nodes")));
	ApplyParams->SetArrayField(TEXT("connections"), PreviewParams->GetArrayField(TEXT("connections")));
	ApplyParams->SetStringField(TEXT("planId"), Preview->GetStringField(TEXT("planId")));
	ApplyParams->SetStringField(
		TEXT("confirmation"),
		Preview->GetStringField(TEXT("requiredConfirmation")));

	TSharedPtr<FJsonObject> Applied;
	FString ApplyCode;
	FString ApplyMessage;
	const bool bApplied = UEAgentKitBlueprintGraphEditExperimental::TryApplyExperimental(
		ApplyParams,
		Applied,
		ApplyCode,
		ApplyMessage);
	TestFalse(TEXT("stale connect-existing plan is rejected"), bApplied);
	TestTrue(
		TEXT("stale plan uses a stale/edge-set code"),
		ApplyCode == TEXT("graph-edit-plan-stale")
			|| ApplyCode == TEXT("graph-edit-edge-set-stale"));
	if (!bApplied
		&& ApplyCode != TEXT("graph-edit-plan-stale")
		&& ApplyCode != TEXT("graph-edit-edge-set-stale"))
	{
		AddError(FString::Printf(
			TEXT("Unexpected stale Apply code: %s %s"),
			*ApplyCode,
			*ApplyMessage));
	}
	if (bApplied)
	{
		AddError(TEXT("Stale plan unexpectedly applied."));
	}

	// Cleanup the test-only position change and dirty state.
	Branch->NodePosX = OldX;
	Blueprint->GetOutermost()->SetDirtyFlag(false);
	UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitConnectExistingExistingStaleTest,
	"UEAgentKit.Blueprint.ConnectExistingBExistingStaleGate",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitConnectExistingExistingStaleTest::RunTest(const FString& Parameters)
{
	using namespace UEAgentKitConnectExistingSafety;
	UEAgentKitBlueprintGraphEditExperimental::SetExperimentalWriteEnabledForTest(true);

	FString Error;
	UBlueprint* Blueprint = LoadFixture(Error);
	if (Blueprint == nullptr)
	{
		AddError(Error);
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}
	UEdGraph* Graph = Blueprint->UbergraphPages.Num() > 0 ? Blueprint->UbergraphPages[0] : nullptr;
	if (Graph == nullptr || OpenAndFocus(Blueprint, Graph, Error) == nullptr)
	{
		AddError(Error);
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}
	UK2Node_Event* Event = FindEventWithUnlinkedThen(Graph);
	UK2Node_IfThenElse* Branch = FindBranchWithUnlinkedExecute(Graph);
	UEdGraphPin* EventThen = Event != nullptr ? Event->FindPin(TEXT("then"), EGPD_Output) : nullptr;
	UEdGraphPin* BranchExecute = Branch != nullptr ? Branch->FindPin(TEXT("execute"), EGPD_Input) : nullptr;
	if (!TestNotNull(TEXT("event then pin exists"), EventThen)
		|| !TestNotNull(TEXT("branch execute pin exists"), BranchExecute))
	{
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}

	const TSharedPtr<FJsonObject> PreviewParams = MakePureExistingPreviewParams(
		Blueprint,
		Graph,
		Event,
		EventThen,
		Branch,
		BranchExecute);
	TSharedPtr<FJsonObject> Preview;
	FString PreviewCode;
	FString PreviewMessage;
	if (!TestTrue(
			TEXT("pure existing preview succeeds"),
			UEAgentKitBlueprintGraphEditExperimental::TryPreviewExperimental(
				PreviewParams,
				Preview,
				PreviewCode,
				PreviewMessage)))
	{
		AddError(PreviewCode + TEXT(": ") + PreviewMessage);
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}
	if (!TestEqual(
			TEXT("pure existing preview creates no nodes"),
			Preview->GetNumberField(TEXT("nodeCountAfter")),
			Preview->GetNumberField(TEXT("nodeCountBefore"))))
	{
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}

	// Mutate the graph after Preview (position changes fingerprint, not the edge
	// set) and keep it test-clean so Apply reaches the stale-plan gate.
	const int32 OldX = Branch->NodePosX;
	Branch->Modify();
	Branch->NodePosX = OldX + 7;
	Blueprint->GetOutermost()->SetDirtyFlag(false);

	TSharedPtr<FJsonObject> ApplyParams = MakeShared<FJsonObject>();
	ApplyParams->SetStringField(TEXT("assetPath"), PreviewParams->GetStringField(TEXT("assetPath")));
	ApplyParams->SetStringField(TEXT("graphGuid"), PreviewParams->GetStringField(TEXT("graphGuid")));
	ApplyParams->SetArrayField(TEXT("nodes"), PreviewParams->GetArrayField(TEXT("nodes")));
	ApplyParams->SetArrayField(TEXT("connections"), PreviewParams->GetArrayField(TEXT("connections")));
	ApplyParams->SetStringField(TEXT("planId"), Preview->GetStringField(TEXT("planId")));
	ApplyParams->SetStringField(
		TEXT("confirmation"),
		Preview->GetStringField(TEXT("requiredConfirmation")));

	TSharedPtr<FJsonObject> Applied;
	FString ApplyCode;
	FString ApplyMessage;
	const bool bApplied = UEAgentKitBlueprintGraphEditExperimental::TryApplyExperimental(
		ApplyParams,
		Applied,
		ApplyCode,
		ApplyMessage);
	TestFalse(TEXT("stale pure existing plan is rejected"), bApplied);
	TestTrue(
		TEXT("stale pure existing plan uses a stale code"),
		ApplyCode == TEXT("graph-edit-plan-stale")
			|| ApplyCode == TEXT("graph-edit-edge-set-stale"));
	if (!bApplied
		&& ApplyCode != TEXT("graph-edit-plan-stale")
		&& ApplyCode != TEXT("graph-edit-edge-set-stale"))
	{
		AddError(FString::Printf(TEXT("Unexpected pure stale code: %s %s"), *ApplyCode, *ApplyMessage));
	}

	Branch->NodePosX = OldX;
	Blueprint->GetOutermost()->SetDirtyFlag(false);
	UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitExactDisconnectStaleTest,
	"UEAgentKit.Blueprint.ConnectExistingCExactDisconnectStaleGate",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitExactDisconnectStaleTest::RunTest(const FString& Parameters)
{
	using namespace UEAgentKitConnectExistingSafety;
	UEAgentKitBlueprintGraphEditExperimental::SetExperimentalWriteEnabledForTest(true);

	UBlueprint* Blueprint = nullptr;
	UEdGraph* Graph = nullptr;
	TSharedPtr<FJsonObject> PreviewParams;
	TSharedPtr<FJsonObject> Preview;
	FExistingEdge Target;
	FExistingEdge Protected;
	FString Error;
	if (!SetupDisconnectPreview(
		this,
		Blueprint,
		Graph,
		PreviewParams,
		Preview,
		Target,
		Protected,
		Error))
	{
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}
	TestEqual(
		TEXT("disconnect preview reports one removed edge"),
		Preview->GetNumberField(TEXT("disconnectCount")),
		1.0);

	// Change the protected edge after Preview; the plan must become stale and
	// must never remove the target while silently accepting the new graph.
	Protected.From->BreakLinkTo(Protected.To);
	Blueprint->GetOutermost()->SetDirtyFlag(false);

	TSharedPtr<FJsonObject> ApplyParams = MakeShared<FJsonObject>();
	ApplyParams->SetStringField(TEXT("assetPath"), PreviewParams->GetStringField(TEXT("assetPath")));
	ApplyParams->SetStringField(TEXT("graphGuid"), PreviewParams->GetStringField(TEXT("graphGuid")));
	ApplyParams->SetStringField(TEXT("operation"), TEXT("disconnect"));
	ApplyParams->SetArrayField(TEXT("nodes"), PreviewParams->GetArrayField(TEXT("nodes")));
	ApplyParams->SetArrayField(TEXT("connections"), PreviewParams->GetArrayField(TEXT("connections")));
	ApplyParams->SetStringField(TEXT("planId"), Preview->GetStringField(TEXT("planId")));
	ApplyParams->SetStringField(
		TEXT("confirmation"),
		Preview->GetStringField(TEXT("requiredConfirmation")));

	TSharedPtr<FJsonObject> Applied;
	FString ApplyCode;
	FString ApplyMessage;
	const bool bApplied = UEAgentKitBlueprintGraphEditExperimental::TryApplyExperimental(
		ApplyParams,
		Applied,
		ApplyCode,
		ApplyMessage);
	TestFalse(TEXT("stale disconnect plan is rejected"), bApplied);
	TestTrue(
		TEXT("stale disconnect uses a stale/edge-set code"),
		ApplyCode == TEXT("graph-edit-plan-stale")
			|| ApplyCode == TEXT("graph-edit-edge-set-stale")
			|| ApplyCode == TEXT("graph-edit-disconnect-edge-not-found"));
	if (!bApplied
		&& ApplyCode != TEXT("graph-edit-plan-stale")
		&& ApplyCode != TEXT("graph-edit-edge-set-stale")
		&& ApplyCode != TEXT("graph-edit-disconnect-edge-not-found"))
	{
		AddError(FString::Printf(TEXT("Unexpected disconnect stale code: %s %s"), *ApplyCode, *ApplyMessage));
	}

	// Restore the fixture: protected edge back, and target edge if it was
	// unexpectedly removed by a failing implementation.
	if (!Protected.From->LinkedTo.Contains(Protected.To))
	{
		Protected.From->MakeLinkTo(Protected.To);
	}
	if (!Target.From->LinkedTo.Contains(Target.To))
	{
		Target.From->MakeLinkTo(Target.To);
	}
	Blueprint->GetOutermost()->SetDirtyFlag(false);
	UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitCallFunctionStaleTest,
	"UEAgentKit.Blueprint.CallFunctionAStaleGate",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitCallFunctionStaleTest::RunTest(const FString& Parameters)
{
	using namespace UEAgentKitConnectExistingSafety;
	UEAgentKitBlueprintGraphEditExperimental::SetExperimentalWriteEnabledForTest(true);

	FString Error;
	UBlueprint* Blueprint = LoadFixture(Error);
	if (Blueprint == nullptr)
	{
		AddError(Error);
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}
	UEdGraph* Graph = Blueprint->UbergraphPages.Num() > 0 ? Blueprint->UbergraphPages[0] : nullptr;
	if (Graph == nullptr || OpenAndFocus(Blueprint, Graph, Error) == nullptr)
	{
		AddError(Error);
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}
	UK2Node_IfThenElse* Branch = FindBranch(Graph);
	UEdGraphPin* Condition = Branch != nullptr
		? Branch->FindPin(TEXT("Condition"), EGPD_Input)
		: nullptr;
	if (!TestNotNull(TEXT("branch condition exists"), Condition))
	{
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}
	const TSharedPtr<FJsonObject> PreviewParams = MakeCallFunctionPreviewParams(
		Blueprint,
		Graph,
		Branch,
		Condition);
	TSharedPtr<FJsonObject> Preview;
	FString PreviewCode;
	FString PreviewMessage;
	if (!TestTrue(
			TEXT("CallFunction preview succeeds"),
			UEAgentKitBlueprintGraphEditExperimental::TryPreviewExperimental(
				PreviewParams,
				Preview,
				PreviewCode,
				PreviewMessage)))
	{
		AddError(PreviewCode + TEXT(": ") + PreviewMessage);
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}
	const int32 OldX = Branch->NodePosX;
	Branch->Modify();
	Branch->NodePosX = OldX + 9;
	Blueprint->GetOutermost()->SetDirtyFlag(false);

	TSharedPtr<FJsonObject> ApplyParams = MakeShared<FJsonObject>();
	ApplyParams->SetStringField(TEXT("assetPath"), PreviewParams->GetStringField(TEXT("assetPath")));
	ApplyParams->SetStringField(TEXT("graphGuid"), PreviewParams->GetStringField(TEXT("graphGuid")));
	ApplyParams->SetArrayField(TEXT("nodes"), PreviewParams->GetArrayField(TEXT("nodes")));
	ApplyParams->SetArrayField(TEXT("connections"), PreviewParams->GetArrayField(TEXT("connections")));
	ApplyParams->SetStringField(TEXT("planId"), Preview->GetStringField(TEXT("planId")));
	ApplyParams->SetStringField(
		TEXT("confirmation"),
		Preview->GetStringField(TEXT("requiredConfirmation")));

	TSharedPtr<FJsonObject> Applied;
	FString ApplyCode;
	FString ApplyMessage;
	const bool bApplied = UEAgentKitBlueprintGraphEditExperimental::TryApplyExperimental(
		ApplyParams,
		Applied,
		ApplyCode,
		ApplyMessage);
	TestFalse(TEXT("stale CallFunction plan is rejected"), bApplied);
	TestTrue(
		TEXT("stale CallFunction uses a stale code"),
		ApplyCode == TEXT("graph-edit-plan-stale")
			|| ApplyCode == TEXT("graph-edit-edge-set-stale"));
	if (!bApplied
		&& ApplyCode != TEXT("graph-edit-plan-stale")
		&& ApplyCode != TEXT("graph-edit-edge-set-stale"))
	{
		AddError(FString::Printf(TEXT("Unexpected CallFunction stale code: %s %s"), *ApplyCode, *ApplyMessage));
	}

	Branch->NodePosX = OldX;
	Blueprint->GetOutermost()->SetDirtyFlag(false);
	UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitOwnedPureStaleTest,
	"UEAgentKit.Blueprint.OwnedPureCallAStaleGate",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitOwnedPureStaleTest::RunTest(const FString& Parameters)
{
	using namespace UEAgentKitConnectExistingSafety;
	UEAgentKitBlueprintGraphEditExperimental::SetExperimentalWriteEnabledForTest(true);

	FString Error;
	UBlueprint* Blueprint = LoadFixture(Error);
	if (Blueprint == nullptr)
	{
		AddError(Error);
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}
	UEdGraph* Graph = Blueprint->UbergraphPages.Num() > 0 ? Blueprint->UbergraphPages[0] : nullptr;
	if (Graph == nullptr || OpenAndFocus(Blueprint, Graph, Error) == nullptr)
	{
		AddError(Error);
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}
	UK2Node_IfThenElse* Branch = FindBranch(Graph);
	UEdGraphPin* Condition = Branch != nullptr
		? Branch->FindPin(TEXT("Condition"), EGPD_Input)
		: nullptr;
	if (!TestNotNull(TEXT("branch condition exists"), Condition))
	{
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}
	const TSharedPtr<FJsonObject> PreviewParams = MakeOwnedPurePreviewParams(
		Blueprint,
		Graph,
		Branch,
		Condition);
	if (!TestNotNull(TEXT("owned pure preview params exist"), PreviewParams.Get()))
	{
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}
	TSharedPtr<FJsonObject> Preview;
	FString PreviewCode;
	FString PreviewMessage;
	if (!TestTrue(
			TEXT("owned pure Preview succeeds"),
			UEAgentKitBlueprintGraphEditExperimental::TryPreviewExperimental(
				PreviewParams,
				Preview,
				PreviewCode,
				PreviewMessage)))
	{
		AddError(PreviewCode + TEXT(": ") + PreviewMessage);
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}
	const int32 OldX = Branch->NodePosX;
	Branch->Modify();
	Branch->NodePosX = OldX + 11;
	Blueprint->GetOutermost()->SetDirtyFlag(false);

	TSharedPtr<FJsonObject> ApplyParams = MakeShared<FJsonObject>();
	ApplyParams->SetStringField(TEXT("assetPath"), PreviewParams->GetStringField(TEXT("assetPath")));
	ApplyParams->SetStringField(TEXT("graphGuid"), PreviewParams->GetStringField(TEXT("graphGuid")));
	ApplyParams->SetArrayField(TEXT("nodes"), PreviewParams->GetArrayField(TEXT("nodes")));
	ApplyParams->SetArrayField(TEXT("connections"), PreviewParams->GetArrayField(TEXT("connections")));
	ApplyParams->SetStringField(TEXT("planId"), Preview->GetStringField(TEXT("planId")));
	ApplyParams->SetStringField(
		TEXT("confirmation"),
		Preview->GetStringField(TEXT("requiredConfirmation")));

	TSharedPtr<FJsonObject> Applied;
	FString ApplyCode;
	FString ApplyMessage;
	const bool bApplied = UEAgentKitBlueprintGraphEditExperimental::TryApplyExperimental(
		ApplyParams,
		Applied,
		ApplyCode,
		ApplyMessage);
	TestFalse(TEXT("stale owned pure plan is rejected"), bApplied);
	TestTrue(
		TEXT("stale owned pure uses a stale code"),
		ApplyCode == TEXT("graph-edit-plan-stale")
			|| ApplyCode == TEXT("graph-edit-edge-set-stale"));
	if (!bApplied
		&& ApplyCode != TEXT("graph-edit-plan-stale")
		&& ApplyCode != TEXT("graph-edit-edge-set-stale"))
	{
		AddError(FString::Printf(TEXT("Unexpected owned pure stale code: %s %s"), *ApplyCode, *ApplyMessage));
	}

	Branch->NodePosX = OldX;
	Blueprint->GetOutermost()->SetDirtyFlag(false);
	UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitOwnedLiteralSaveTamperTest,
	"UEAgentKit.Blueprint.OwnedPureCallSaveTamperGate",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitOwnedLiteralSaveTamperTest::RunTest(const FString& Parameters)
{
	using namespace UEAgentKitConnectExistingSafety;
	UEAgentKitBlueprintGraphEditExperimental::SetExperimentalWriteEnabledForTest(true);

	FString Error;
	UBlueprint* Blueprint = LoadFixture(Error);
	if (Blueprint == nullptr)
	{
		AddError(Error);
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}
	UEdGraph* Graph = Blueprint->UbergraphPages.Num() > 0 ? Blueprint->UbergraphPages[0] : nullptr;
	if (Graph == nullptr || OpenAndFocus(Blueprint, Graph, Error) == nullptr)
	{
		AddError(Error);
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}
	UK2Node_IfThenElse* Branch = FindBranch(Graph);
	UEdGraphPin* Condition = Branch != nullptr
		? Branch->FindPin(TEXT("Condition"), EGPD_Input)
		: nullptr;
	TSharedPtr<FJsonObject> PreviewParams;
	if (Branch != nullptr && Condition != nullptr)
	{
		PreviewParams = MakeOwnedPurePreviewParams(Blueprint, Graph, Branch, Condition);
	}
	if (!TestNotNull(TEXT("owned literal params exist"), PreviewParams.Get()))
	{
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}
	TSharedPtr<FJsonObject> Preview;
	FString PreviewCode;
	FString PreviewMessage;
	if (!TestTrue(
			TEXT("owned literal Preview succeeds"),
			UEAgentKitBlueprintGraphEditExperimental::TryPreviewExperimental(
				PreviewParams,
				Preview,
				PreviewCode,
				PreviewMessage)))
	{
		AddError(PreviewCode + TEXT(": ") + PreviewMessage);
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}
	TSharedPtr<FJsonObject> Applied;
	FString ApplyCode;
	FString ApplyMessage;
	if (!TestTrue(
			TEXT("owned literal Apply succeeds"),
			UEAgentKitBlueprintGraphEditExperimental::TryApplyExperimental(
				[&]()
				{
					TSharedPtr<FJsonObject> Params = MakeShared<FJsonObject>();
					Params->SetStringField(
						TEXT("assetPath"),
						PreviewParams->GetStringField(TEXT("assetPath")));
					Params->SetStringField(
						TEXT("graphGuid"),
						PreviewParams->GetStringField(TEXT("graphGuid")));
					Params->SetArrayField(
						TEXT("nodes"),
						PreviewParams->GetArrayField(TEXT("nodes")));
					Params->SetArrayField(
						TEXT("connections"),
						PreviewParams->GetArrayField(TEXT("connections")));
					Params->SetStringField(
						TEXT("planId"),
						Preview->GetStringField(TEXT("planId")));
					Params->SetStringField(
						TEXT("confirmation"),
						Preview->GetStringField(TEXT("requiredConfirmation")));
					return Params;
				}(),
				Applied,
				ApplyCode,
				ApplyMessage)))
	{
		AddError(ApplyCode + TEXT(": ") + ApplyMessage);
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}

	const TArray<TSharedPtr<FJsonValue>>& CreatedNodes =
		Applied->GetArrayField(TEXT("createdNodes"));
	if (CreatedNodes.IsEmpty())
	{
		AddError(TEXT("Owned literal Apply created no node."));
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}
	const TSharedPtr<FJsonObject> CreatedNodeObject = CreatedNodes[0]->AsObject();
	const FString NodeId = CreatedNodeObject->GetStringField(TEXT("nodeId"));
	FGuid OwnedGuid;
	if (!FGuid::ParseExact(
			NodeId.RightChop(2),
			EGuidFormats::DigitsWithHyphensLower,
			OwnedGuid))
	{
		AddError(TEXT("Could not parse the created owned node GUID."));
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}
	UEdGraphNode* OwnedNode = nullptr;
	for (UEdGraphNode* Candidate : Graph->Nodes)
	{
		if (Candidate != nullptr && Candidate->NodeGuid == OwnedGuid)
		{
			OwnedNode = Candidate;
			break;
		}
	}
	UEdGraphPin* LiteralPin = OwnedNode != nullptr
		? OwnedNode->FindPin(TEXT("A"), EGPD_Input)
		: nullptr;
	if (!TestNotNull(TEXT("owned literal A pin exists"), LiteralPin))
	{
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}
	LiteralPin->Modify();
	LiteralPin->DefaultValue = TEXT("7");

	TSharedPtr<FJsonObject> SaveParams = MakeShared<FJsonObject>();
	SaveParams->SetStringField(TEXT("assetPath"), Applied->GetStringField(TEXT("assetPath")));
	SaveParams->SetStringField(TEXT("graphGuid"), Applied->GetStringField(TEXT("graphGuid")));
	SaveParams->SetStringField(TEXT("planId"), Applied->GetStringField(TEXT("planId")));
	SaveParams->SetStringField(
		TEXT("expectedGraphFingerprint"),
		Applied->GetStringField(TEXT("graphFingerprintAfter")));
	SaveParams->SetNumberField(
		TEXT("expectedNodeCount"),
		Applied->GetNumberField(TEXT("nodeCountAfter")));
	SaveParams->SetNumberField(
		TEXT("expectedLinkCount"),
		Applied->GetNumberField(TEXT("linkCountAfter")));
	SaveParams->SetStringField(
		TEXT("confirmation"),
		Applied->GetStringField(TEXT("requiredSaveConfirmation")));

	TSharedPtr<FJsonObject> Saved;
	FString SaveCode;
	FString SaveMessage;
	const bool bSaved = UEAgentKitBlueprintGraphEditExperimental::TrySaveExperimental(
		SaveParams,
		Saved,
		SaveCode,
		SaveMessage);
	TestFalse(TEXT("tampered owned literal Save is rejected"), bSaved);
	TestEqual(
		TEXT("tampered owned literal uses the save receipt code"),
		SaveCode,
		FString(TEXT("graph-edit-save-owned-literal-mismatch")));
	if (bSaved)
	{
		AddError(TEXT("Tampered owned literal unexpectedly saved."));
	}

	// Remove the applied node and restore the fixture baseline.
	GEditor->UndoTransaction();
	Blueprint->GetOutermost()->SetDirtyFlag(false);
	UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
	return true;
}

namespace UEAgentKitConnectExistingSafety
{
	struct FPieState
	{
		FAutomationTestBase* Test = nullptr;
		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = nullptr;
		TSharedPtr<FJsonObject> PreviewParams;
		double StartedAt = 0.0;
		bool bCompleted = false;
	};

	class FWaitForPieAndRejectConnect final : public IAutomationLatentCommand
	{
	public:
		explicit FWaitForPieAndRejectConnect(TSharedRef<FPieState> InState)
			: State(InState)
		{
		}

		virtual bool Update() override
		{
			if (State->bCompleted)
			{
				return true;
			}
			if (State->StartedAt <= 0.0)
			{
				State->StartedAt = FPlatformTime::Seconds();
			}
			if (GEditor != nullptr && GEditor->IsPlaySessionInProgress() && GEditor->PlayWorld != nullptr)
			{
				TSharedPtr<FJsonObject> Preview;
				FString Code;
				FString Message;
				const bool bPreviewed = UEAgentKitBlueprintGraphEditExperimental::TryPreviewExperimental(
					State->PreviewParams,
					Preview,
					Code,
					Message);
				State->Test->TestFalse(TEXT("PIE connect-existing Preview is rejected"), bPreviewed);
				State->Test->TestEqual(
					TEXT("PIE connect-existing error code"),
					Code,
					FString(TEXT("graph-edit-pie-active")));
				State->bCompleted = true;
				return true;
			}
			if (FPlatformTime::Seconds() - State->StartedAt > 60.0)
			{
				State->Test->AddError(TEXT("PIE did not start within 60 seconds."));
				State->bCompleted = true;
				return true;
			}
			return false;
		}

	private:
		TSharedRef<FPieState> State;
	};

	class FWaitForPieStop final : public IAutomationLatentCommand
	{
	public:
		explicit FWaitForPieStop(TSharedRef<FPieState> InState)
			: State(InState)
		{
		}

		virtual bool Update() override
		{
			return GEditor == nullptr
				|| (!GEditor->IsPlaySessionInProgress() && GEditor->PlayWorld == nullptr);
		}

	private:
		TSharedRef<FPieState> State;
	};
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitCallFunctionPieTest,
	"UEAgentKit.Blueprint.CallFunctionZPieGate",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitCallFunctionPieTest::RunTest(const FString& Parameters)
{
	using namespace UEAgentKitConnectExistingSafety;
	UEAgentKitBlueprintGraphEditExperimental::SetExperimentalWriteEnabledForTest(true);

	FString Error;
	UBlueprint* Blueprint = LoadFixture(Error);
	if (Blueprint == nullptr)
	{
		AddError(Error);
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}
	UEdGraph* Graph = Blueprint->UbergraphPages.Num() > 0 ? Blueprint->UbergraphPages[0] : nullptr;
	if (Graph == nullptr || OpenAndFocus(Blueprint, Graph, Error) == nullptr)
	{
		AddError(Error);
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}
	UK2Node_IfThenElse* Branch = FindBranch(Graph);
	UEdGraphPin* Condition = Branch != nullptr
		? Branch->FindPin(TEXT("Condition"), EGPD_Input)
		: nullptr;
	if (!TestNotNull(TEXT("branch condition exists"), Condition))
	{
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}
	const TSharedPtr<FJsonObject> PreviewParams = MakeCallFunctionPreviewParams(
		Blueprint,
		Graph,
		Branch,
		Condition);
	TSharedRef<FPieState> State = MakeShared<FPieState>();
	State->Test = this;
	State->Blueprint = Blueprint;
	State->Graph = Graph;
	State->PreviewParams = PreviewParams;
	ADD_LATENT_AUTOMATION_COMMAND(FStartPIECommand(false));
	ADD_LATENT_AUTOMATION_COMMAND(FWaitForPieAndRejectConnect(State));
	ADD_LATENT_AUTOMATION_COMMAND(FEndPlayMapCommand());
	ADD_LATENT_AUTOMATION_COMMAND(FWaitForPieStop(State));
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitOwnedPurePieTest,
	"UEAgentKit.Blueprint.OwnedPureCallZPieGate",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitOwnedPurePieTest::RunTest(const FString& Parameters)
{
	using namespace UEAgentKitConnectExistingSafety;
	UEAgentKitBlueprintGraphEditExperimental::SetExperimentalWriteEnabledForTest(true);

	FString Error;
	UBlueprint* Blueprint = LoadFixture(Error);
	if (Blueprint == nullptr)
	{
		AddError(Error);
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}
	UEdGraph* Graph = Blueprint->UbergraphPages.Num() > 0 ? Blueprint->UbergraphPages[0] : nullptr;
	if (Graph == nullptr || OpenAndFocus(Blueprint, Graph, Error) == nullptr)
	{
		AddError(Error);
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}
	UK2Node_IfThenElse* Branch = FindBranch(Graph);
	UEdGraphPin* Condition = Branch != nullptr
		? Branch->FindPin(TEXT("Condition"), EGPD_Input)
		: nullptr;
	if (!TestNotNull(TEXT("branch condition exists"), Condition))
	{
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}
	const TSharedPtr<FJsonObject> PreviewParams = MakeOwnedPurePreviewParams(
		Blueprint,
		Graph,
		Branch,
		Condition);
	if (!TestNotNull(TEXT("owned pure preview params exist"), PreviewParams.Get()))
	{
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}
	TSharedRef<FPieState> State = MakeShared<FPieState>();
	State->Test = this;
	State->Blueprint = Blueprint;
	State->Graph = Graph;
	State->PreviewParams = PreviewParams;
	ADD_LATENT_AUTOMATION_COMMAND(FStartPIECommand(false));
	ADD_LATENT_AUTOMATION_COMMAND(FWaitForPieAndRejectConnect(State));
	ADD_LATENT_AUTOMATION_COMMAND(FEndPlayMapCommand());
	ADD_LATENT_AUTOMATION_COMMAND(FWaitForPieStop(State));
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitConnectExistingPieTest,
	"UEAgentKit.Blueprint.ConnectExistingPieGate",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitConnectExistingPieTest::RunTest(const FString& Parameters)
{
	using namespace UEAgentKitConnectExistingSafety;
	UEAgentKitBlueprintGraphEditExperimental::SetExperimentalWriteEnabledForTest(true);

	UBlueprint* Blueprint = nullptr;
	UEdGraph* Graph = nullptr;
	TSharedPtr<FJsonObject> PreviewParams;
	TSharedPtr<FJsonObject> Preview;
	FExistingEdge Target;
	FExistingEdge Protected;
	FString Error;
	if (!SetupDisconnectPreview(
		this,
		Blueprint,
		Graph,
		PreviewParams,
		Preview,
		Target,
		Protected,
		Error))
	{
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}
	TSharedRef<FPieState> State = MakeShared<FPieState>();
	State->Test = this;
	State->Blueprint = Blueprint;
	State->Graph = Graph;
	State->PreviewParams = PreviewParams;
	ADD_LATENT_AUTOMATION_COMMAND(FStartPIECommand(false));
	ADD_LATENT_AUTOMATION_COMMAND(FWaitForPieAndRejectConnect(State));
	ADD_LATENT_AUTOMATION_COMMAND(FEndPlayMapCommand());
	ADD_LATENT_AUTOMATION_COMMAND(FWaitForPieStop(State));
	return true;
}

#endif
