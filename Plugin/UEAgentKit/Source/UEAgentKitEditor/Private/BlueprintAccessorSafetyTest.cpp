// Bounded native safety acceptance for member-variable accessor nodes.
//
// These tests cover the two gates that the MCP probe cannot drive directly:
//   1. Apply then same-type VariableReference retarget must make Save fail with
//      graph-edit-save-variable-binding-mismatch (receipt-side hash).
//   2. Preview while PIE is active must fail with graph-edit-pie-active.
//
// The positive A-path (MCP -> Editor -> Save -> fresh-process Reload) lives in
// tests/python/accessor_native_probe.py and verify_accessor_reload.py.

#include "BlueprintEditor.h"
#include "BlueprintGraphEditExperimental.h"

#include "Dom/JsonObject.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraphSchema_K2.h"
#include "Editor.h"
#include "Engine/Blueprint.h"
#include "HAL/PlatformTime.h"
#include "K2Node_Variable.h"
#include "K2Node_VariableGet.h"
#include "Misc/AutomationTest.h"
#include "Misc/Guid.h"
#include "Subsystems/AssetEditorSubsystem.h"
#include "Tests/AutomationEditorCommon.h"
#include "UObject/Package.h"

#if WITH_DEV_AUTOMATION_TESTS

namespace UEAgentKitAccessorSafety
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

	TSharedPtr<FJsonObject> MakeGetNode(const FBPVariableDescription& Variable, const TCHAR* Type)
	{
		TSharedRef<FJsonObject> Node = MakeShared<FJsonObject>();
		Node->SetStringField(TEXT("tempId"), TEXT("tmp:retarget"));
		Node->SetStringField(TEXT("nodeType"), TEXT("variable_get"));
		Node->SetNumberField(TEXT("x"), 0);
		Node->SetNumberField(TEXT("y"), 0);
		Node->SetStringField(TEXT("variableName"), Variable.VarName.ToString());
		Node->SetStringField(TEXT("variableGuid"), GuidLower(Variable.VarGuid));
		Node->SetStringField(TEXT("variableType"), Type);
		return Node;
	}

	TSharedPtr<FJsonObject> MakePreviewParams(
		UBlueprint* Blueprint,
		UEdGraph* Graph,
		const TSharedPtr<FJsonObject>& Node)
	{
		TSharedRef<FJsonObject> Params = MakeShared<FJsonObject>();
		Params->SetStringField(TEXT("assetPath"), Blueprint->GetPathName());
		Params->SetStringField(TEXT("graphGuid"), GuidLower(Graph->GraphGuid));
		TArray<TSharedPtr<FJsonValue>> Nodes;
		Nodes.Add(MakeShared<FJsonValueObject>(Node));
		Params->SetArrayField(TEXT("nodes"), Nodes);
		Params->SetArrayField(TEXT("connections"), TArray<TSharedPtr<FJsonValue>>());
		return Params;
	}

	bool ApplyPreview(
		const TSharedPtr<FJsonObject>& PreviewParams,
		const TSharedPtr<FJsonObject>& Preview,
		TSharedPtr<FJsonObject>& OutApplied,
		FString& OutError)
	{
		TSharedPtr<FJsonObject> ApplyParams = MakeShared<FJsonObject>();
		ApplyParams->SetStringField(TEXT("assetPath"), PreviewParams->GetStringField(TEXT("assetPath")));
		ApplyParams->SetStringField(TEXT("graphGuid"), PreviewParams->GetStringField(TEXT("graphGuid")));
		ApplyParams->SetArrayField(TEXT("nodes"), PreviewParams->GetArrayField(TEXT("nodes")));
		ApplyParams->SetArrayField(TEXT("connections"), PreviewParams->GetArrayField(TEXT("connections")));
		ApplyParams->SetStringField(TEXT("planId"), Preview->GetStringField(TEXT("planId")));
		ApplyParams->SetStringField(
			TEXT("confirmation"),
			Preview->GetStringField(TEXT("requiredConfirmation")));

		FString ErrorCode;
		FString ErrorMessage;
		if (!UEAgentKitBlueprintGraphEditExperimental::TryApplyExperimental(
			ApplyParams,
			OutApplied,
			ErrorCode,
			ErrorMessage))
		{
			OutError = FString::Printf(TEXT("Apply failed: %s %s"), *ErrorCode, *ErrorMessage);
			return false;
		}
		return true;
	}

	bool FindCreatedNode(
		UEdGraph* Graph,
		const TSharedPtr<FJsonObject>& Applied,
		UEdGraphNode*& OutNode,
		FString& OutError)
	{
		const TArray<TSharedPtr<FJsonValue>>* CreatedNodes = nullptr;
		if (!Applied->TryGetArrayField(TEXT("createdNodes"), CreatedNodes)
			|| CreatedNodes == nullptr
			|| CreatedNodes->Num() != 1)
		{
			OutError = TEXT("Apply did not return exactly one created node.");
			return false;
		}
		const TSharedPtr<FJsonObject> Created = (*CreatedNodes)[0]->AsObject();
		FString NodeId = Created.IsValid() ? Created->GetStringField(TEXT("nodeId")) : FString();
		NodeId.RemoveFromStart(TEXT("n:"));
		FGuid NodeGuid;
		if (!FGuid::ParseExact(NodeId, EGuidFormats::DigitsWithHyphensLower, NodeGuid))
		{
			OutError = TEXT("Created node id is not a canonical GUID.");
			return false;
		}
		for (UEdGraphNode* Node : Graph->Nodes)
		{
			if (Node != nullptr && Node->NodeGuid == NodeGuid)
			{
				OutNode = Node;
				return true;
			}
		}
		OutError = TEXT("Created node is missing from the live graph.");
		return false;
	}
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitAccessorRetargetSaveTest,
	"UEAgentKit.Blueprint.AccessorRetargetSaveGate",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitAccessorRetargetSaveTest::RunTest(const FString& Parameters)
{
	using namespace UEAgentKitAccessorSafety;
	UEAgentKitBlueprintGraphEditExperimental::SetExperimentalWriteEnabledForTest(true);

	FString Error;
	UBlueprint* Blueprint = LoadFixture(Error);
	if (!TestNotNull(TEXT("accessor fixture loads"), Blueprint))
	{
		AddError(Error);
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}
	UEdGraph* Graph = Blueprint->UbergraphPages.Num() > 0 ? Blueprint->UbergraphPages[0] : nullptr;
	if (!TestNotNull(TEXT("accessor event graph exists"), Graph)
		|| OpenAndFocus(Blueprint, Graph, Error) == nullptr)
	{
		AddError(Error);
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}
	const FBPVariableDescription* IntVariable = FindVariable(Blueprint, TEXT("AccessInt"));
	const FBPVariableDescription* Int2Variable = FindVariable(Blueprint, TEXT("AccessInt2"));
	if (!TestNotNull(TEXT("AccessInt fixture variable exists"), IntVariable)
		|| !TestNotNull(TEXT("AccessInt2 fixture variable exists"), Int2Variable))
	{
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}

	const int32 NodesBefore = Graph->Nodes.Num();
	const TSharedPtr<FJsonObject> PreviewParams = MakePreviewParams(
		Blueprint,
		Graph,
		MakeGetNode(*IntVariable, TEXT("int")));
	TSharedPtr<FJsonObject> Preview;
	FString ErrorCode;
	FString ErrorMessage;
	if (!TestTrue(
			TEXT("variable accessor preview succeeds"),
			UEAgentKitBlueprintGraphEditExperimental::TryPreviewExperimental(
				PreviewParams,
				Preview,
				ErrorCode,
				ErrorMessage)))
	{
		AddError(ErrorCode + TEXT(": ") + ErrorMessage);
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}

	TSharedPtr<FJsonObject> Applied;
	if (!TestTrue(TEXT("variable accessor apply succeeds"), ApplyPreview(PreviewParams, Preview, Applied, Error)))
	{
		AddError(Error);
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}
	if (!TestTrue(TEXT("Apply reports undoVerified"), Applied->GetBoolField(TEXT("undoVerified")))
		|| !TestTrue(TEXT("Apply reports redoVerified"), Applied->GetBoolField(TEXT("redoVerified")))
		|| !TestEqual(
			TEXT("Apply created the planned node count"),
			Applied->GetNumberField(TEXT("createdNodeCount")),
			1.0))
	{
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}

	UEdGraphNode* CreatedNode = nullptr;
	if (!TestTrue(TEXT("created accessor node is present"), FindCreatedNode(Graph, Applied, CreatedNode, Error)))
	{
		AddError(Error);
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}
	UK2Node_VariableGet* VariableNode = Cast<UK2Node_VariableGet>(CreatedNode);
	if (!TestNotNull(TEXT("created node is K2Node_VariableGet"), VariableNode))
	{
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}
	if (!TestEqual(
			TEXT("created accessor bound to AccessInt"),
			VariableNode->GetVarName().ToString(),
			FString(TEXT("AccessInt"))))
	{
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}

	// Same-type retarget: the layout fingerprint does not cover FMemberReference,
	// so only the receipt-side variableBindingHash can reject this Save.
	VariableNode->Modify();
	VariableNode->VariableReference.SetSelfMember(Int2Variable->VarName, Int2Variable->VarGuid);
	if (!TestEqual(
			TEXT("live accessor now names AccessInt2"),
			VariableNode->GetVarName().ToString(),
			FString(TEXT("AccessInt2"))))
	{
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}

	TSharedPtr<FJsonObject> SaveParams = MakeShared<FJsonObject>();
	SaveParams->SetStringField(TEXT("assetPath"), Applied->GetStringField(TEXT("assetPath")));
	SaveParams->SetStringField(TEXT("graphGuid"), Applied->GetStringField(TEXT("graphGuid")));
	SaveParams->SetStringField(TEXT("planId"), Applied->GetStringField(TEXT("planId")));
	SaveParams->SetStringField(
		TEXT("expectedGraphFingerprint"),
		Applied->GetStringField(TEXT("graphFingerprintAfter")));
	SaveParams->SetNumberField(TEXT("expectedNodeCount"), Applied->GetNumberField(TEXT("nodeCountAfter")));
	SaveParams->SetNumberField(TEXT("expectedLinkCount"), Applied->GetNumberField(TEXT("linkCountAfter")));
	SaveParams->SetStringField(
		TEXT("confirmation"),
		Applied->GetStringField(TEXT("requiredSaveConfirmation")));

	TSharedPtr<FJsonObject> SaveResult;
	FString SaveCode;
	FString SaveMessage;
	const bool bSaved = UEAgentKitBlueprintGraphEditExperimental::TrySaveExperimental(
		SaveParams,
		SaveResult,
		SaveCode,
		SaveMessage);
	if (!TestFalse(TEXT("same-type retarget Save is rejected"), bSaved)
		|| !TestEqual(
			TEXT("retarget Save error code"),
			SaveCode,
			FString(TEXT("graph-edit-save-variable-binding-mismatch"))))
	{
		AddError(SaveCode + TEXT(": ") + SaveMessage);
	}

	// Cleanup: undo the applied node and clear the disposable package dirty flag.
	const bool bUndoCalled = GEditor != nullptr && GEditor->UndoTransaction();
	bool bNodeGone = true;
	for (UEdGraphNode* Node : Graph->Nodes)
	{
		if (Node != nullptr && CreatedNode != nullptr && Node->NodeGuid == CreatedNode->NodeGuid)
		{
			bNodeGone = false;
			break;
		}
	}
	TestTrue(TEXT("cleanup undo removed the accessor node"), bUndoCalled && bNodeGone);
	TestEqual(TEXT("cleanup restores the original node count"), Graph->Nodes.Num(), NodesBefore);
	Blueprint->GetOutermost()->SetDirtyFlag(false);
	UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
	return true;
}

namespace UEAgentKitAccessorSafety
{
	struct FPieGateState
	{
		FAutomationTestBase* Test = nullptr;
		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = nullptr;
		double StartedAt = 0.0;
		bool bCompleted = false;
	};

	class FWaitForPieAndReject final : public IAutomationLatentCommand
	{
	public:
		explicit FWaitForPieAndReject(TSharedRef<FPieGateState> InState)
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
			const bool bPlaying = GEditor != nullptr
				&& GEditor->IsPlaySessionInProgress()
				&& GEditor->PlayWorld != nullptr;
			if (bPlaying)
			{
				State->bCompleted = true;
				const FBPVariableDescription* Variable = FindVariable(
					State->Blueprint,
					TEXT("AccessBool"));
				if (Variable == nullptr)
				{
					State->Test->AddError(TEXT("AccessBool is missing from the fixture."));
					return true;
				}
				const TSharedPtr<FJsonObject> Params = MakePreviewParams(
					State->Blueprint,
					State->Graph,
					MakeGetNode(*Variable, TEXT("bool")));
				TSharedPtr<FJsonObject> Preview;
				FString ErrorCode;
				FString ErrorMessage;
				const bool bPreviewed =
					UEAgentKitBlueprintGraphEditExperimental::TryPreviewExperimental(
						Params,
						Preview,
						ErrorCode,
						ErrorMessage);
				State->Test->TestFalse(TEXT("PIE Preview is rejected"), bPreviewed);
				State->Test->TestEqual(
					TEXT("PIE Preview error code"),
					ErrorCode,
					FString(TEXT("graph-edit-pie-active")));
				if (bPreviewed)
				{
					State->Test->AddError(TEXT("PIE Preview unexpectedly succeeded."));
				}
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
		TSharedRef<FPieGateState> State;
	};

	class FWaitForPieStop final : public IAutomationLatentCommand
	{
	public:
		explicit FWaitForPieStop(TSharedRef<FPieGateState> InState)
			: State(InState)
		{
		}

		virtual bool Update() override
		{
			return GEditor == nullptr
				|| (!GEditor->IsPlaySessionInProgress() && GEditor->PlayWorld == nullptr);
		}

	private:
		TSharedRef<FPieGateState> State;
	};
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitAccessorPieGateTest,
	"UEAgentKit.Blueprint.AccessorPieGate",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitAccessorPieGateTest::RunTest(const FString& Parameters)
{
	using namespace UEAgentKitAccessorSafety;
	UEAgentKitBlueprintGraphEditExperimental::SetExperimentalWriteEnabledForTest(true);

	FString Error;
	UBlueprint* Blueprint = LoadFixture(Error);
	if (!TestNotNull(TEXT("accessor fixture loads"), Blueprint))
	{
		AddError(Error);
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}
	UEdGraph* Graph = Blueprint->UbergraphPages.Num() > 0 ? Blueprint->UbergraphPages[0] : nullptr;
	if (!TestNotNull(TEXT("accessor event graph exists"), Graph)
		|| OpenAndFocus(Blueprint, Graph, Error) == nullptr)
	{
		AddError(Error);
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}

	TSharedRef<FPieGateState> State = MakeShared<FPieGateState>();
	State->Test = this;
	State->Blueprint = Blueprint;
	State->Graph = Graph;
	ADD_LATENT_AUTOMATION_COMMAND(FStartPIECommand(false));
	ADD_LATENT_AUTOMATION_COMMAND(FWaitForPieAndReject(State));
	ADD_LATENT_AUTOMATION_COMMAND(FEndPlayMapCommand());
	ADD_LATENT_AUTOMATION_COMMAND(FWaitForPieStop(State));
	return true;
}

#endif
