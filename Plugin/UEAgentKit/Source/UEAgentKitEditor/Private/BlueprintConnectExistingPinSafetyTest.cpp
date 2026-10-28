// Native safety gates for connecting a new GraphEdit node to an existing pin.
//
//  1. PIE active: Preview must fail with graph-edit-pie-active.
//  2. Stale graph after Preview: mutate an existing edge before Apply; Apply
//     must fail closed (plan/edge-set stale) and the cleanup restores the edge.

#include "BlueprintEditor.h"
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
		const UEdGraphPin* TargetInput)
	{
		TSharedRef<FJsonObject> Connection = MakeShared<FJsonObject>();
		Connection->SetObjectField(TEXT("fromExisting"), MakeExistingPin(SourceNode, SourceOutput));
		Connection->SetObjectField(TEXT("toExisting"), MakeExistingPin(TargetNode, TargetInput));
		TSharedRef<FJsonObject> Params = MakeShared<FJsonObject>();
		Params->SetStringField(TEXT("assetPath"), Blueprint->GetPathName());
		Params->SetStringField(TEXT("graphGuid"), GuidLower(Graph->GraphGuid));
		Params->SetArrayField(TEXT("nodes"), TArray<TSharedPtr<FJsonValue>>());
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
	UK2Node_Event* Event = FindEventWithUnlinkedThen(Graph);
	UK2Node_IfThenElse* Branch = FindBranch(Graph);
	UEdGraphPin* EventThen = Event != nullptr ? Event->FindPin(TEXT("then"), EGPD_Output) : nullptr;
	UEdGraphPin* BranchExecute = Branch != nullptr ? Branch->FindPin(TEXT("execute"), EGPD_Input) : nullptr;
	if (!TestNotNull(TEXT("event then pin exists"), EventThen)
		|| !TestNotNull(TEXT("branch execute pin exists"), BranchExecute))
	{
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}

	// Mutate an existing edge after Preview; the plan must not silently apply.
	const bool bCreatedUnplannedLink =
		Graph->GetSchema()->TryCreateConnection(EventThen, BranchExecute)
		&& EventThen->LinkedTo.Contains(BranchExecute);
	TestTrue(TEXT("test created an unplanned existing edge"), bCreatedUnplannedLink);
	// Keep the package "clean" so the test reaches the plan/fingerprint/edge-set
	// gate instead of stopping at graph-edit-package-dirty.  This is a test-only
	// manipulation; cleanup restores both the edge and the dirty flag.
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

	// Cleanup the test-only edge and dirty state.
	EventThen->BreakLinkTo(BranchExecute);
	TestFalse(TEXT("cleanup removed the test-only link"), EventThen->LinkedTo.Contains(BranchExecute));
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
	UK2Node_IfThenElse* Branch = FindBranch(Graph);
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
	FString Error;
	if (!SetupPreview(this, Blueprint, Graph, PreviewParams, Preview, Error))
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
