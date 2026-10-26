// Real-Editor acceptance for the P4 bounded Blueprint single-node removal slice.
//
// The fixture is one ordinary event graph with nine nodes and four exact edges:
//   NotA              (isolated pure function node -> removed first)
//   NotB.ReturnValue  -> BranchB.Condition   (removed second with its one edge)
//   NotC.ReturnValue  -> BranchC.Condition   (untouched bool fan-out)
//   NotC.ReturnValue  -> BranchD.Condition   (untouched bool fan-out)
//   BranchA.then      -> BranchB.execute     (untouched exec edge)
//   Knot              (wildcard class -> must be rejected)
//   Comment           (comment group -> must be rejected)
//
// The test proves the default-OFF gate, unknown parameters, invalid identifiers,
// missing node, class mismatch, forbidden node classes (comment group, knot),
// the too-many-connections bound, malformed/mismatched removedEdges, stale edge
// set, stale plan, dirty package, non-focused asset, wrong confirmations, Save
// before Apply and PIE, then performs Preview -> Apply -> Undo/Redo -> Save for
// an isolated node and for a node with exactly one enumerated edge.  Every
// untouched fan-out and execution edge must survive.

#include "BlueprintContextSha256.h"
#include "BlueprintEditor.h"
#include "BlueprintGraphLayoutApply.h"
#include "BlueprintNodeRemoveExperimental.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "EdGraphSchema_K2.h"
#include "EdGraphNode_Comment.h"
#include "Editor.h"
#include "Engine/Blueprint.h"
#include "GameFramework/Actor.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformProcess.h"
#include "HAL/PlatformTime.h"
#include "K2Node_CallFunction.h"
#include "K2Node_IfThenElse.h"
#include "K2Node_Knot.h"
#include "Kismet/KismetMathLibrary.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Kismet2/KismetEditorUtilities.h"
#include "Misc/AutomationTest.h"
#include "Misc/DateTime.h"
#include "Misc/FileHelper.h"
#include "Misc/PackageName.h"
#include "Misc/Paths.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "Subsystems/AssetEditorSubsystem.h"
#include "Tests/AutomationEditorCommon.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"

#if WITH_DEV_AUTOMATION_TESTS

namespace UEAgentKitBlueprintNodeRemoveExpansion
{
	const TCHAR* const AssetPath =
		TEXT("/Game/UEAgentKitBlueprintNodeRemove/BP_NodeRemoveExpansion.BP_NodeRemoveExpansion");
	const TCHAR* const PackageName = TEXT("/Game/UEAgentKitBlueprintNodeRemove/BP_NodeRemoveExpansion");
	const TCHAR* const AssetName = TEXT("BP_NodeRemoveExpansion");
	const TCHAR* const EvidenceSubdirectory = TEXT("BlueprintNodeRemove");

	FString GuidLower(const FGuid& Guid)
	{
		return Guid.ToString(EGuidFormats::DigitsWithHyphensLower);
	}

	bool WriteJson(const FString& Path, const TSharedPtr<FJsonObject>& Object)
	{
		FString Text;
		const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Text);
		if (!FJsonSerializer::Serialize(Object.ToSharedRef(), Writer))
		{
			return false;
		}
		IFileManager::Get().MakeDirectory(*FPaths::GetPath(Path), true);
		return FFileHelper::SaveStringToFile(
			Text,
			*Path,
			FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);
	}

	struct FEdgeDesc
	{
		UEdGraphNode* FromNode = nullptr;
		UEdGraphPin* FromPin = nullptr;
		UEdGraphNode* ToNode = nullptr;
		UEdGraphPin* ToPin = nullptr;
	};

	FString EdgeKey(const UEdGraphPin* From, const UEdGraphPin* To)
	{
		return GuidLower(From->GetOwningNode()->NodeGuid) + TEXT("|")
			+ From->PinName.ToString() + TEXT("|") + GuidLower(From->PinId) + TEXT(">")
			+ GuidLower(To->GetOwningNode()->NodeGuid) + TEXT("|")
			+ To->PinName.ToString() + TEXT("|") + GuidLower(To->PinId);
	}

	void BuildEdgeKeys(const UEdGraph* Graph, TArray<FString>& OutKeys)
	{
		OutKeys.Reset();
		for (const UEdGraphNode* Node : Graph->Nodes)
		{
			if (Node == nullptr)
			{
				continue;
			}
			for (const UEdGraphPin* Pin : Node->Pins)
			{
				if (Pin == nullptr || Pin->Direction != EGPD_Output)
				{
					continue;
				}
				for (const UEdGraphPin* Linked : Pin->LinkedTo)
				{
					if (Linked != nullptr && Linked->GetOwningNode() != nullptr)
					{
						OutKeys.Add(EdgeKey(Pin, Linked));
					}
				}
			}
		}
		OutKeys.Sort();
	}

	bool ComputeEdgeHash(const UEdGraph* Graph, FString& OutHash)
	{
		TArray<FString> Keys;
		BuildEdgeKeys(Graph, Keys);
		FString Canonical = TEXT("ueak.blueprint-node-remove-edge-set/1.0\n");
		for (const FString& Key : Keys)
		{
			Canonical += Key + TEXT("\n");
		}
		FString Digest;
		if (!FBlueprintContextSha256::HashText(Canonical, Digest))
		{
			return false;
		}
		OutHash = FString(TEXT("sha256:")) + Digest.ToLower();
		return true;
	}

	int32 CountLinks(const UEdGraph* Graph)
	{
		int32 Count = 0;
		for (const UEdGraphNode* Node : Graph->Nodes)
		{
			if (Node == nullptr)
			{
				continue;
			}
			for (const UEdGraphPin* Pin : Node->Pins)
			{
				if (Pin != nullptr && Pin->Direction == EGPD_Output)
				{
					Count += Pin->LinkedTo.Num();
				}
			}
		}
		return Count;
	}

	bool RemoveStaleFixtureFiles(FString& OutError)
	{
		const FString Filename = FPackageName::LongPackageNameToFilename(
			PackageName,
			FPackageName::GetAssetPackageExtension());
		const TArray<FString> Candidates = {
			Filename,
			FPaths::ChangeExtension(Filename, TEXT("uexp")),
			FPaths::ChangeExtension(Filename, TEXT("ubulk")),
		};
		for (const FString& Candidate : Candidates)
		{
			if (IFileManager::Get().FileExists(*Candidate)
				&& !IFileManager::Get().Delete(*Candidate, false, true, true))
			{
				OutError = FString::Printf(
					TEXT("Could not delete the stale disposable fixture file %s."),
					*Candidate);
				return false;
			}
		}
		return true;
	}

	bool SaveBlueprint(UBlueprint* Blueprint)
	{
		UPackage* Package = Blueprint != nullptr ? Blueprint->GetOutermost() : nullptr;
		if (Package == nullptr)
		{
			return false;
		}
		const FString Filename = FPackageName::LongPackageNameToFilename(
			Package->GetName(),
			FPackageName::GetAssetPackageExtension());
		FSavePackageArgs SaveArgs;
		SaveArgs.TopLevelFlags = RF_Public | RF_Standalone;
		SaveArgs.SaveFlags = SAVE_NoError;
		if (!UPackage::SavePackage(Package, Blueprint, *Filename, SaveArgs))
		{
			UE_LOG(
				LogTemp,
				Error,
				TEXT("Node-remove fixture SavePackage failed package=%s file=%s"),
				*Package->GetName(),
				*Filename);
			return false;
		}
		return true;
	}

	UK2Node_IfThenElse* AddBranch(UEdGraph* Graph, const int32 X, const int32 Y)
	{
		UK2Node_IfThenElse* Node = NewObject<UK2Node_IfThenElse>(Graph);
		if (Node == nullptr)
		{
			return nullptr;
		}
		Graph->AddNode(Node, true, false);
		Node->AllocateDefaultPins();
		Node->CreateNewGuid();
		Node->NodePosX = X;
		Node->NodePosY = Y;
		return Node;
	}

	UK2Node_CallFunction* AddFunction(
		UEdGraph* Graph,
		const TCHAR* FunctionName,
		const int32 X,
		const int32 Y)
	{
		UFunction* Function = UKismetMathLibrary::StaticClass()->FindFunctionByName(
			FName(FunctionName));
		if (Function == nullptr)
		{
			return nullptr;
		}
		UK2Node_CallFunction* Node = NewObject<UK2Node_CallFunction>(Graph);
		if (Node == nullptr)
		{
			return nullptr;
		}
		Graph->AddNode(Node, true, false);
		Node->SetFromFunction(Function);
		if (Node->Pins.Num() == 0)
		{
			Node->AllocateDefaultPins();
		}
		Node->CreateNewGuid();
		Node->NodePosX = X;
		Node->NodePosY = Y;
		return Node;
	}

	UK2Node_Knot* AddKnot(UEdGraph* Graph, const int32 X, const int32 Y)
	{
		UK2Node_Knot* Node = NewObject<UK2Node_Knot>(Graph);
		if (Node == nullptr)
		{
			return nullptr;
		}
		Graph->AddNode(Node, true, false);
		Node->AllocateDefaultPins();
		Node->CreateNewGuid();
		Node->NodePosX = X;
		Node->NodePosY = Y;
		return Node;
	}

	UEdGraphNode_Comment* AddComment(UEdGraph* Graph, const int32 X, const int32 Y)
	{
		UEdGraphNode_Comment* Node = NewObject<UEdGraphNode_Comment>(Graph);
		if (Node == nullptr)
		{
			return nullptr;
		}
		Graph->AddNode(Node, true, false);
		Node->AllocateDefaultPins();
		Node->CreateNewGuid();
		Node->NodePosX = X;
		Node->NodePosY = Y;
		return Node;
	}

	struct FFixture
	{
		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = nullptr;
		UK2Node_CallFunction* NotA = nullptr;
		UK2Node_CallFunction* NotB = nullptr;
		UK2Node_CallFunction* NotC = nullptr;
		UK2Node_IfThenElse* BranchA = nullptr;
		UK2Node_IfThenElse* BranchB = nullptr;
		UK2Node_IfThenElse* BranchC = nullptr;
		UK2Node_IfThenElse* BranchD = nullptr;
		UK2Node_Knot* Knot = nullptr;
		UEdGraphNode_Comment* Comment = nullptr;
	};

	bool LinkPins(UEdGraph* Graph, UEdGraphPin* From, UEdGraphPin* To, FString& OutError)
	{
		if (From == nullptr || To == nullptr)
		{
			OutError = TEXT("A fixture link references a missing pin.");
			return false;
		}
		if (!Graph->GetSchema()->TryCreateConnection(From, To))
		{
			OutError = FString::Printf(
				TEXT("The fixture could not link %s.%s -> %s.%s."),
				*From->GetOwningNode()->GetName(),
				*From->PinName.ToString(),
				*To->GetOwningNode()->GetName(),
				*To->PinName.ToString());
			return false;
		}
		return true;
	}

	UBlueprint* CreateFixture(FFixture& OutFixture, FString& OutError)
	{
		if (FindObject<UBlueprint>(nullptr, AssetPath) != nullptr)
		{
			OutError = TEXT("Node-removal fixture already exists; use a fresh project.");
			return nullptr;
		}
		if (!RemoveStaleFixtureFiles(OutError))
		{
			return nullptr;
		}
		UPackage* Package = CreatePackage(PackageName);
		UBlueprint* Blueprint = FKismetEditorUtilities::CreateBlueprint(
			AActor::StaticClass(),
			Package,
			FName(AssetName),
			BPTYPE_Normal,
			FName(TEXT("UEAgentKitBlueprintNodeRemoveExpansion")));
		if (Blueprint == nullptr || Blueprint->UbergraphPages.IsEmpty())
		{
			OutError = TEXT("Could not create the node-removal fixture Blueprint.");
			return nullptr;
		}
		UEdGraph* Graph = Blueprint->UbergraphPages[0];
		const TArray<UEdGraphNode*> InitialNodes = Graph->Nodes;
		for (UEdGraphNode* Node : InitialNodes)
		{
			if (Node != nullptr)
			{
				FBlueprintEditorUtils::RemoveNode(Blueprint, Node, true);
			}
		}

		OutFixture = FFixture();
		OutFixture.Blueprint = Blueprint;
		OutFixture.Graph = Graph;
		OutFixture.NotA = AddFunction(Graph, TEXT("Not_PreBool"), -640, -200);
		OutFixture.NotB = AddFunction(Graph, TEXT("Not_PreBool"), -640, 40);
		OutFixture.NotC = AddFunction(Graph, TEXT("Not_PreBool"), -640, 280);
		OutFixture.BranchA = AddBranch(Graph, -160, -200);
		OutFixture.BranchB = AddBranch(Graph, 160, -200);
		OutFixture.BranchC = AddBranch(Graph, 160, 60);
		OutFixture.BranchD = AddBranch(Graph, 160, 320);
		OutFixture.Knot = AddKnot(Graph, -640, 560);
		OutFixture.Comment = AddComment(Graph, -960, 760);
		if (OutFixture.NotA == nullptr || OutFixture.NotB == nullptr
			|| OutFixture.NotC == nullptr || OutFixture.BranchA == nullptr
			|| OutFixture.BranchB == nullptr || OutFixture.BranchC == nullptr
			|| OutFixture.BranchD == nullptr || OutFixture.Knot == nullptr
			|| OutFixture.Comment == nullptr)
		{
			OutError = TEXT("Could not create every node-removal fixture node.");
			return nullptr;
		}

		UEdGraphPin* NotBOut = OutFixture.NotB->FindPin(TEXT("ReturnValue"), EGPD_Output);
		UEdGraphPin* NotCOut = OutFixture.NotC->FindPin(TEXT("ReturnValue"), EGPD_Output);
		UEdGraphPin* BranchBCondition = OutFixture.BranchB->FindPin(TEXT("Condition"), EGPD_Input);
		UEdGraphPin* BranchCCondition = OutFixture.BranchC->FindPin(TEXT("Condition"), EGPD_Input);
		UEdGraphPin* BranchDCondition = OutFixture.BranchD->FindPin(TEXT("Condition"), EGPD_Input);
		UEdGraphPin* BranchAThen = OutFixture.BranchA->FindPin(TEXT("then"), EGPD_Output);
		UEdGraphPin* BranchBExecute = OutFixture.BranchB->FindPin(TEXT("execute"), EGPD_Input);
		if (!LinkPins(Graph, NotBOut, BranchBCondition, OutError)
			|| !LinkPins(Graph, NotCOut, BranchCCondition, OutError)
			|| !LinkPins(Graph, NotCOut, BranchDCondition, OutError)
			|| !LinkPins(Graph, BranchAThen, BranchBExecute, OutError))
		{
			return nullptr;
		}

		// Fixture-only compile: the fixture itself must start consistent.  The
		// product path under test never compiles.
		FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
		FAssetRegistryModule::AssetCreated(Blueprint);
		if (!SaveBlueprint(Blueprint))
		{
			OutError = TEXT("Could not save the node-removal fixture Blueprint.");
			return nullptr;
		}
		Package->SetDirtyFlag(false);
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
			OutError = TEXT("No BlueprintEditor for the node-removal fixture.");
			return nullptr;
		}
		FBlueprintEditor* Editor = static_cast<FBlueprintEditor*>(Instance);
		if (!Editor->OpenGraphAndBringToFront(Graph, true).IsValid()
			|| Editor->GetFocusedGraph() != Graph)
		{
			OutError = TEXT("Could not focus the node-removal fixture graph.");
			return nullptr;
		}
		return Editor;
	}

	TSharedPtr<FJsonObject> MakePreviewParams(
		const FString& NodeGuid,
		const FString& NodeClass,
		const FString& EdgeSetHash,
		const TArray<FEdgeDesc>& RemovedEdges)
	{
		TSharedRef<FJsonObject> Params = MakeShared<FJsonObject>();
		Params->SetStringField(TEXT("assetPath"), AssetPath);
		Params->SetStringField(TEXT("graphGuid"), TEXT("__GRAPH__"));
		Params->SetStringField(TEXT("nodeGuid"), NodeGuid);
		Params->SetStringField(TEXT("expectedNodeClass"), NodeClass);
		Params->SetStringField(TEXT("expectedEdgeSetHash"), EdgeSetHash);
		TArray<TSharedPtr<FJsonValue>> Edges;
		for (const FEdgeDesc& Edge : RemovedEdges)
		{
			TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
			Item->SetStringField(TEXT("fromNodeGuid"), GuidLower(Edge.FromNode->NodeGuid));
			Item->SetStringField(TEXT("fromPinName"), Edge.FromPin->PinName.ToString());
			Item->SetStringField(TEXT("fromPinGuid"), GuidLower(Edge.FromPin->PinId));
			Item->SetStringField(TEXT("toNodeGuid"), GuidLower(Edge.ToNode->NodeGuid));
			Item->SetStringField(TEXT("toPinName"), Edge.ToPin->PinName.ToString());
			Item->SetStringField(TEXT("toPinGuid"), GuidLower(Edge.ToPin->PinId));
			Edges.Add(MakeShared<FJsonValueObject>(Item));
		}
		Params->SetArrayField(TEXT("removedEdges"), Edges);
		return Params;
	}

	TSharedPtr<FJsonObject> MakePreviewParams(
		const UEdGraph* Graph,
		const FString& NodeGuid,
		const FString& NodeClass,
		const FString& EdgeSetHash,
		const TArray<FEdgeDesc>& RemovedEdges)
	{
		TSharedPtr<FJsonObject> Params = MakePreviewParams(
			NodeGuid,
			NodeClass,
			EdgeSetHash,
			RemovedEdges);
		Params->SetStringField(TEXT("graphGuid"), GuidLower(Graph->GraphGuid));
		return Params;
	}

	TSharedPtr<FJsonObject> BuildApplyParams(
		const TSharedPtr<FJsonObject>& Preview,
		const FString& Confirmation)
	{
		TSharedRef<FJsonObject> Params = MakeShared<FJsonObject>();
		Params->SetStringField(TEXT("assetPath"), Preview->GetStringField(TEXT("assetPath")));
		Params->SetStringField(TEXT("graphGuid"), Preview->GetStringField(TEXT("graphGuid")));
		Params->SetStringField(TEXT("nodeGuid"), Preview->GetStringField(TEXT("nodeGuid")));
		Params->SetStringField(
			TEXT("expectedNodeClass"),
			Preview->GetStringField(TEXT("nodeClass")));
		Params->SetStringField(
			TEXT("expectedEdgeSetHash"),
			Preview->GetStringField(TEXT("edgeSetHashBefore")));
		TArray<TSharedPtr<FJsonValue>> Edges;
		for (const TSharedPtr<FJsonValue>& Value :
			Preview->GetArrayField(TEXT("removedEdges")))
		{
			const TSharedPtr<FJsonObject> Edge = Value->AsObject();
			TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
			Item->SetStringField(TEXT("fromNodeGuid"), Edge->GetStringField(TEXT("fromNodeGuid")));
			Item->SetStringField(TEXT("fromPinName"), Edge->GetStringField(TEXT("fromPinName")));
			Item->SetStringField(TEXT("fromPinGuid"), Edge->GetStringField(TEXT("fromPinGuid")));
			Item->SetStringField(TEXT("toNodeGuid"), Edge->GetStringField(TEXT("toNodeGuid")));
			Item->SetStringField(TEXT("toPinName"), Edge->GetStringField(TEXT("toPinName")));
			Item->SetStringField(TEXT("toPinGuid"), Edge->GetStringField(TEXT("toPinGuid")));
			Edges.Add(MakeShared<FJsonValueObject>(Item));
		}
		Params->SetArrayField(TEXT("removedEdges"), Edges);
		Params->SetStringField(TEXT("planId"), Preview->GetStringField(TEXT("planId")));
		Params->SetStringField(TEXT("confirmation"), Confirmation);
		return Params;
	}

	TSharedPtr<FJsonObject> BuildSaveParams(
		const TSharedPtr<FJsonObject>& Apply,
		const FString& Confirmation)
	{
		TSharedRef<FJsonObject> Params = MakeShared<FJsonObject>();
		Params->SetStringField(TEXT("assetPath"), Apply->GetStringField(TEXT("assetPath")));
		Params->SetStringField(TEXT("graphGuid"), Apply->GetStringField(TEXT("graphGuid")));
		Params->SetStringField(TEXT("planId"), Apply->GetStringField(TEXT("planId")));
		Params->SetStringField(
			TEXT("expectedEdgeSetHashAfter"),
			Apply->GetStringField(TEXT("edgeSetHashAfter")));
		Params->SetStringField(
			TEXT("expectedPackageRevision"),
			Apply->GetStringField(TEXT("packageRevision")));
		Params->SetNumberField(
			TEXT("expectedNodeCount"),
			Apply->GetNumberField(TEXT("nodeCount")));
		Params->SetNumberField(
			TEXT("expectedLinkCount"),
			Apply->GetNumberField(TEXT("linkCount")));
		Params->SetStringField(TEXT("confirmation"), Confirmation);
		return Params;
	}

	bool ExpectPreviewError(
		FAutomationTestBase& Test,
		const TSharedPtr<FJsonObject>& Params,
		const TCHAR* ExpectedCode,
		const TCHAR* Label)
	{
		TSharedPtr<FJsonObject> Result;
		FString Code;
		FString Message;
		const bool bRejected = !UEAgentKitBlueprintNodeRemoveExperimental::TryPreviewExperimental(
			Params,
			Result,
			Code,
			Message);
		Test.TestTrue(Label, bRejected);
		Test.TestEqual(FString(Label) + TEXT(" error code"), Code, FString(ExpectedCode));
		if (!bRejected || !Code.Equals(ExpectedCode, ESearchCase::CaseSensitive))
		{
			Test.AddError(FString::Printf(TEXT("%s detail: %s"), Label, *Message));
		}
		return bRejected && Code.Equals(ExpectedCode, ESearchCase::CaseSensitive);
	}

	bool ExpectApplyError(
		FAutomationTestBase& Test,
		const TSharedPtr<FJsonObject>& Params,
		const TCHAR* ExpectedCode,
		const TCHAR* Label)
	{
		TSharedPtr<FJsonObject> Result;
		FString Code;
		FString Message;
		const bool bRejected = !UEAgentKitBlueprintNodeRemoveExperimental::TryApplyExperimental(
			Params,
			Result,
			Code,
			Message);
		Test.TestTrue(Label, bRejected);
		Test.TestEqual(FString(Label) + TEXT(" error code"), Code, FString(ExpectedCode));
		if (!bRejected || !Code.Equals(ExpectedCode, ESearchCase::CaseSensitive))
		{
			Test.AddError(FString::Printf(TEXT("%s detail: %s"), Label, *Message));
		}
		return bRejected && Code.Equals(ExpectedCode, ESearchCase::CaseSensitive);
	}

	bool ExpectSaveError(
		FAutomationTestBase& Test,
		const TSharedPtr<FJsonObject>& Params,
		const TCHAR* ExpectedCode,
		const TCHAR* Label)
	{
		TSharedPtr<FJsonObject> Result;
		FString Code;
		FString Message;
		const bool bRejected = !UEAgentKitBlueprintNodeRemoveExperimental::TrySaveExperimental(
			Params,
			Result,
			Code,
			Message);
		Test.TestTrue(Label, bRejected);
		Test.TestEqual(FString(Label) + TEXT(" error code"), Code, FString(ExpectedCode));
		if (!bRejected || !Code.Equals(ExpectedCode, ESearchCase::CaseSensitive))
		{
			Test.AddError(FString::Printf(TEXT("%s detail: %s"), Label, *Message));
		}
		return bRejected && Code.Equals(ExpectedCode, ESearchCase::CaseSensitive);
	}

	TArray<TSharedPtr<FJsonValue>> BuildPinInventory(const UEdGraph* Graph)
	{
		TArray<TSharedPtr<FJsonValue>> Nodes;
		for (const UEdGraphNode* Node : Graph->Nodes)
		{
			if (Node == nullptr)
			{
				continue;
			}
			TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
			Item->SetStringField(TEXT("nodeGuid"), GuidLower(Node->NodeGuid));
			Item->SetStringField(
				TEXT("nodeClass"),
				Node->GetClass() != nullptr ? Node->GetClass()->GetPathName() : FString());
			TArray<TSharedPtr<FJsonValue>> Pins;
			for (const UEdGraphPin* Pin : Node->Pins)
			{
				if (Pin == nullptr)
				{
					continue;
				}
				TSharedRef<FJsonObject> PinItem = MakeShared<FJsonObject>();
				PinItem->SetStringField(TEXT("pinName"), Pin->PinName.ToString());
				PinItem->SetStringField(TEXT("pinGuid"), GuidLower(Pin->PinId));
				PinItem->SetStringField(
					TEXT("direction"),
					Pin->Direction == EGPD_Input ? TEXT("input") : TEXT("output"));
				PinItem->SetStringField(TEXT("category"), Pin->PinType.PinCategory.ToString());
				Pins.Add(MakeShared<FJsonValueObject>(PinItem));
			}
			Item->SetArrayField(TEXT("pins"), Pins);
			Nodes.Add(MakeShared<FJsonValueObject>(Item));
		}
		return Nodes;
	}

	TSharedPtr<FJsonObject> EdgeJson(const FEdgeDesc& Edge, const TCHAR* Kind)
	{
		TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
		Item->SetStringField(TEXT("fromNodeGuid"), GuidLower(Edge.FromNode->NodeGuid));
		Item->SetStringField(TEXT("fromPinName"), Edge.FromPin->PinName.ToString());
		Item->SetStringField(TEXT("fromPinGuid"), GuidLower(Edge.FromPin->PinId));
		Item->SetStringField(TEXT("toNodeGuid"), GuidLower(Edge.ToNode->NodeGuid));
		Item->SetStringField(TEXT("toPinName"), Edge.ToPin->PinName.ToString());
		Item->SetStringField(TEXT("toPinGuid"), GuidLower(Edge.ToPin->PinId));
		Item->SetStringField(TEXT("kind"), Kind);
		return Item;
	}
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitBlueprintNodeRemoveExpansionTest,
	"UEAgentKit.NodeRemove.Expansion",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitBlueprintNodeRemoveExpansionTest::RunTest(const FString& Parameters)
{
	using namespace UEAgentKitBlueprintNodeRemoveExpansion;
	UEAgentKitBlueprintNodeRemoveExperimental::ResetExperimentalWriteForTest();
	if (!TestFalse(
			TEXT("experimental Blueprint node removal is off unless the project opts in"),
			UEAgentKitBlueprintNodeRemoveExperimental::IsExperimentalWriteAvailable()))
	{
		return false;
	}

	bool bGateOffRejected = false;
	{
		TSharedPtr<FJsonObject> Params = MakePreviewParams(
			TEXT("11111111-2222-4333-8444-555555555555"),
			TEXT("/Script/BlueprintGraph.K2Node_CallFunction"),
			FString(TEXT("sha256:")) + FString::ChrN(64, TEXT('a')),
			TArray<FEdgeDesc>());
		Params->SetStringField(
			TEXT("graphGuid"),
			TEXT("00000000-1111-4222-8333-444444444444"));
		bGateOffRejected = ExpectPreviewError(
			*this,
			Params,
			TEXT("graph-remove-experimental-disabled"),
			TEXT("gate-off node-removal preview is rejected"));
		if (!bGateOffRejected)
		{
			return false;
		}
	}

	UEAgentKitBlueprintNodeRemoveExperimental::SetExperimentalWriteEnabledForTest(true);
	auto Fail = []()
	{
		UEAgentKitBlueprintNodeRemoveExperimental::ResetExperimentalWriteForTest();
		return false;
	};

	FString Error;
	FFixture Fixture;
	UBlueprint* Blueprint = CreateFixture(Fixture, Error);
	if (!TestNotNull(TEXT("node-removal fixture is created"), Blueprint))
	{
		AddError(Error);
		return Fail();
	}
	FBlueprintEditor* Editor = OpenAndFocus(Blueprint, Fixture.Graph, Error);
	if (!TestNotNull(TEXT("node-removal fixture Blueprint is focused"), Editor))
	{
		AddError(Error);
		return Fail();
	}
	UPackage* Package = Blueprint->GetOutermost();

	const int32 RootNodeCount = Fixture.Graph->Nodes.Num();
	const int32 RootLinkCount = CountLinks(Fixture.Graph);
	if (!TestEqual(TEXT("fixture starts with nine nodes"), RootNodeCount, 9)
		|| !TestEqual(TEXT("fixture starts with four edges"), RootLinkCount, 4))
	{
		return Fail();
	}

	const FString GraphGuid = GuidLower(Fixture.Graph->GraphGuid);
	const FString NotAGuid = GuidLower(Fixture.NotA->NodeGuid);
	const FString NotBGuid = GuidLower(Fixture.NotB->NodeGuid);
	auto ContainsNodeGuid = [&Fixture](const FString& NodeGuidText)
	{
		FGuid NodeGuid;
		if (!FGuid::Parse(NodeGuidText, NodeGuid))
		{
			return false;
		}
		for (const UEdGraphNode* Node : Fixture.Graph->Nodes)
		{
			if (Node != nullptr && Node->NodeGuid == NodeGuid)
			{
				return true;
			}
		}
		return false;
	};
	const FString NotAClass = Fixture.NotA->GetClass()->GetPathName();
	const FString BranchClass = Fixture.BranchA->GetClass()->GetPathName();
	const FString KnotClass = Fixture.Knot->GetClass()->GetPathName();
	const FString CommentClass = Fixture.Comment->GetClass()->GetPathName();

	UEdGraphPin* NotBOut = Fixture.NotB->FindPin(TEXT("ReturnValue"), EGPD_Output);
	UEdGraphPin* NotCOut = Fixture.NotC->FindPin(TEXT("ReturnValue"), EGPD_Output);
	UEdGraphPin* BranchBCondition = Fixture.BranchB->FindPin(TEXT("Condition"), EGPD_Input);
	UEdGraphPin* BranchCCondition = Fixture.BranchC->FindPin(TEXT("Condition"), EGPD_Input);
	UEdGraphPin* BranchAThen = Fixture.BranchA->FindPin(TEXT("then"), EGPD_Output);
	UEdGraphPin* BranchBExecute = Fixture.BranchB->FindPin(TEXT("execute"), EGPD_Input);
	if (!TestNotNull(TEXT("NotB output resolves"), NotBOut)
		|| !TestNotNull(TEXT("NotC output resolves"), NotCOut)
		|| !TestNotNull(TEXT("BranchB condition resolves"), BranchBCondition)
		|| !TestNotNull(TEXT("BranchC condition resolves"), BranchCCondition)
		|| !TestNotNull(TEXT("BranchA then resolves"), BranchAThen)
		|| !TestNotNull(TEXT("BranchB execute resolves"), BranchBExecute))
	{
		return Fail();
	}

	FEdgeDesc NotBEdge;
	NotBEdge.FromNode = Fixture.NotB;
	NotBEdge.FromPin = NotBOut;
	NotBEdge.ToNode = Fixture.BranchB;
	NotBEdge.ToPin = BranchBCondition;
	const FString NotBOutPinGuid = GuidLower(NotBOut->PinId);
	const FString BranchBConditionPinGuid = GuidLower(BranchBCondition->PinId);
	FEdgeDesc FanoutC;
	FanoutC.FromNode = Fixture.NotC;
	FanoutC.FromPin = NotCOut;
	FanoutC.ToNode = Fixture.BranchC;
	FanoutC.ToPin = BranchCCondition;
	FEdgeDesc ExecEdge;
	ExecEdge.FromNode = Fixture.BranchA;
	ExecEdge.FromPin = BranchAThen;
	ExecEdge.ToNode = Fixture.BranchB;
	ExecEdge.ToPin = BranchBExecute;

	FString EdgeHashBefore;
	if (!TestTrue(TEXT("fixture edge hash computes"), ComputeEdgeHash(Fixture.Graph, EdgeHashBefore)))
	{
		return Fail();
	}

	bool bUnknownParameterRejected = false;
	bool bInvalidIdentifierRejected = false;
	bool bNodeNotFoundRejected = false;
	bool bClassMismatchRejected = false;
	bool bKnotRejected = false;
	bool bCommentRejected = false;
	bool bTooManyConnectionsRejected = false;
	bool bRemovedEdgesMismatchRejected = false;
	bool bMalformedRemovedEdgesRejected = false;
	bool bEdgeSetStaleRejected = false;
	bool bDirtyRejected = false;
	bool bFocusedMismatchRejected = false;
	bool bPlanStaleRejected = false;
	bool bEdgeSetStaleAtApplyRejected = false;

	{
		TSharedPtr<FJsonObject> Params = MakePreviewParams(
			Fixture.Graph,
			GuidLower(Fixture.NotA->NodeGuid),
			NotAClass,
			EdgeHashBefore,
			TArray<FEdgeDesc>());
		Params->SetStringField(TEXT("planId"), TEXT("sha256:ignored"));
		bUnknownParameterRejected = ExpectPreviewError(
			*this, Params,
			TEXT("graph-remove-unknown-parameter"),
			TEXT("unknown preview parameter is rejected"));
	}
	{
		TSharedPtr<FJsonObject> Params = MakePreviewParams(
			Fixture.Graph,
			TEXT("not-a-guid"),
			NotAClass,
			EdgeHashBefore,
			TArray<FEdgeDesc>());
		bInvalidIdentifierRejected = ExpectPreviewError(
			*this, Params,
			TEXT("graph-remove-invalid-identifier"),
			TEXT("malformed nodeGuid is rejected"));
	}
	{
		TSharedPtr<FJsonObject> Params = MakePreviewParams(
			Fixture.Graph,
			TEXT("99999999-9999-4999-8999-999999999999"),
			NotAClass,
			EdgeHashBefore,
			TArray<FEdgeDesc>());
		bNodeNotFoundRejected = ExpectPreviewError(
			*this, Params,
			TEXT("graph-remove-node-not-found"),
			TEXT("unknown node GUID is rejected"));
	}
	{
		TSharedPtr<FJsonObject> Params = MakePreviewParams(
			Fixture.Graph,
			GuidLower(Fixture.NotA->NodeGuid),
			BranchClass,
			EdgeHashBefore,
			TArray<FEdgeDesc>());
		bClassMismatchRejected = ExpectPreviewError(
			*this, Params,
			TEXT("graph-remove-node-class-mismatch"),
			TEXT("wrong expected class is rejected"));
	}
	{
		TSharedPtr<FJsonObject> Params = MakePreviewParams(
			Fixture.Graph,
			GuidLower(Fixture.Knot->NodeGuid),
			KnotClass,
			EdgeHashBefore,
			TArray<FEdgeDesc>());
		bKnotRejected = ExpectPreviewError(
			*this, Params,
			TEXT("graph-remove-node-type-not-allowed"),
			TEXT("knot node is rejected"));
	}
	{
		TSharedPtr<FJsonObject> Params = MakePreviewParams(
			Fixture.Graph,
			GuidLower(Fixture.Comment->NodeGuid),
			CommentClass,
			EdgeHashBefore,
			TArray<FEdgeDesc>());
		bCommentRejected = ExpectPreviewError(
			*this, Params,
			TEXT("graph-remove-node-type-not-allowed"),
			TEXT("comment group node is rejected"));
	}
	{
		TArray<FEdgeDesc> OneOfTwo;
		OneOfTwo.Add(NotBEdge);
		TSharedPtr<FJsonObject> Params = MakePreviewParams(
			Fixture.Graph,
			GuidLower(Fixture.BranchB->NodeGuid),
			BranchClass,
			EdgeHashBefore,
			OneOfTwo);
		bTooManyConnectionsRejected = ExpectPreviewError(
			*this, Params,
			TEXT("graph-remove-node-too-many-connections"),
			TEXT("node with two incident edges is rejected even with one listed"));
	}
	{
		TSharedPtr<FJsonObject> Params = MakePreviewParams(
			Fixture.Graph,
			GuidLower(Fixture.NotB->NodeGuid),
			NotAClass,
			EdgeHashBefore,
			TArray<FEdgeDesc>());
		bRemovedEdgesMismatchRejected = ExpectPreviewError(
			*this, Params,
			TEXT("graph-remove-removed-edges-mismatch"),
			TEXT("missing removedEdges entry is rejected"));
	}
	{
		TSharedPtr<FJsonObject> Params = MakePreviewParams(
			Fixture.Graph,
			GuidLower(Fixture.NotB->NodeGuid),
			NotAClass,
			EdgeHashBefore,
			TArray<FEdgeDesc>{FanoutC});
		bMalformedRemovedEdgesRejected = ExpectPreviewError(
			*this, Params,
			TEXT("graph-remove-removed-edges-mismatch"),
			TEXT("wrong removedEdges entry is rejected"));
	}
	{
		TSharedPtr<FJsonObject> Params = MakePreviewParams(
			Fixture.Graph,
			GuidLower(Fixture.NotA->NodeGuid),
			NotAClass,
			FString(TEXT("sha256:")) + FString::ChrN(64, TEXT('0')),
			TArray<FEdgeDesc>());
		bEdgeSetStaleRejected = ExpectPreviewError(
			*this, Params,
			TEXT("graph-remove-edge-set-stale"),
			TEXT("wrong before edge-set hash is rejected"));
	}
	{
		Package->SetDirtyFlag(true);
		TSharedPtr<FJsonObject> Params = MakePreviewParams(
			Fixture.Graph,
			GuidLower(Fixture.NotA->NodeGuid),
			NotAClass,
			EdgeHashBefore,
			TArray<FEdgeDesc>());
		bDirtyRejected = ExpectPreviewError(
			*this, Params,
			TEXT("graph-remove-package-dirty"),
			TEXT("dirty package is rejected"));
		Package->SetDirtyFlag(false);
	}
	{
		TSharedPtr<FJsonObject> Params = MakePreviewParams(
			Fixture.Graph,
			GuidLower(Fixture.NotA->NodeGuid),
			NotAClass,
			EdgeHashBefore,
			TArray<FEdgeDesc>());
		Params->SetStringField(
			TEXT("assetPath"),
			TEXT("/Game/UEAgentKitBlueprintNodeRemove/BP_Other.BP_Other"));
		bFocusedMismatchRejected = ExpectPreviewError(
			*this, Params,
			TEXT("graph-remove-focused-graph-mismatch"),
			TEXT("non-focused assetPath is rejected"));
	}

	// Plan staleness: Preview binds the live graph fingerprint, so moving an
	// unrelated node after Preview must invalidate the plan.
	{
		TSharedPtr<FJsonObject> Params = MakePreviewParams(
			Fixture.Graph,
			GuidLower(Fixture.NotA->NodeGuid),
			NotAClass,
			EdgeHashBefore,
			TArray<FEdgeDesc>());
		TSharedPtr<FJsonObject> Preview;
		FString Code;
		FString Message;
		const bool bPreviewed = UEAgentKitBlueprintNodeRemoveExperimental::TryPreviewExperimental(
			Params, Preview, Code, Message);
		TestTrue(TEXT("plan-stale preview succeeds"), bPreviewed);
		if (bPreviewed)
		{
			TSharedPtr<FJsonObject> ApplyParams = BuildApplyParams(
				Preview,
				FString(UEAgentKitBlueprintNodeRemoveExperimental::ApplyConfirmationPrefix)
					+ Preview->GetStringField(TEXT("planId")));
			Fixture.NotC->NodePosX += 64;
			bPlanStaleRejected = ExpectApplyError(
				*this, ApplyParams,
				TEXT("graph-remove-plan-stale"),
				TEXT("plan made stale by a node move is rejected"));
			Fixture.NotC->NodePosX -= 64;
		}
	}

	// Edge-set staleness at Apply: break an unrelated edge after Preview and
	// restore it exactly afterwards.
	{
		TSharedPtr<FJsonObject> Params = MakePreviewParams(
			Fixture.Graph,
			GuidLower(Fixture.NotA->NodeGuid),
			NotAClass,
			EdgeHashBefore,
			TArray<FEdgeDesc>());
		TSharedPtr<FJsonObject> Preview;
		FString Code;
		FString Message;
		const bool bPreviewed = UEAgentKitBlueprintNodeRemoveExperimental::TryPreviewExperimental(
			Params, Preview, Code, Message);
		TestTrue(TEXT("edge-set-stale preview succeeds"), bPreviewed);
		if (bPreviewed)
		{
			TSharedPtr<FJsonObject> ApplyParams = BuildApplyParams(
				Preview,
				FString(UEAgentKitBlueprintNodeRemoveExperimental::ApplyConfirmationPrefix)
					+ Preview->GetStringField(TEXT("planId")));
			NotCOut->BreakLinkTo(BranchCCondition);
			Package->SetDirtyFlag(false);
			bEdgeSetStaleAtApplyRejected = ExpectApplyError(
				*this, ApplyParams,
				TEXT("graph-remove-edge-set-stale"),
				TEXT("edge-set change after Preview is rejected at Apply"));
			NotCOut->MakeLinkTo(BranchCCondition);
			Package->SetDirtyFlag(false);
			if (!bEdgeSetStaleAtApplyRejected)
			{
				return Fail();
			}
		}
	}

	// 1) Isolated node removal.
	TSharedPtr<FJsonObject> IsolatedPreview;
	FString IsolatedPreviewCode;
	FString IsolatedPreviewMessage;
	if (!TestTrue(
			TEXT("isolated-node preview succeeds"),
			UEAgentKitBlueprintNodeRemoveExperimental::TryPreviewExperimental(
				MakePreviewParams(
					Fixture.Graph,
					GuidLower(Fixture.NotA->NodeGuid),
					NotAClass,
					EdgeHashBefore,
					TArray<FEdgeDesc>()),
				IsolatedPreview,
				IsolatedPreviewCode,
				IsolatedPreviewMessage)))
	{
		AddError(IsolatedPreviewCode + TEXT(": ") + IsolatedPreviewMessage);
		return Fail();
	}
	const FString IsolatedPlanId = IsolatedPreview->GetStringField(TEXT("planId"));
	if (!TestTrue(
			TEXT("isolated preview is read-only"),
			IsolatedPreview->GetBoolField(TEXT("readOnly")))
		|| !TestFalse(
			TEXT("isolated preview does not write"),
			IsolatedPreview->GetBoolField(TEXT("wroteBlueprint")))
		|| !TestTrue(
			TEXT("isolated preview reports an isolated node"),
			IsolatedPreview->GetBoolField(TEXT("isolatedNode")))
		|| !TestEqual(
			TEXT("isolated preview removes no edge"),
			static_cast<int32>(IsolatedPreview->GetNumberField(TEXT("removedEdgeCount"))),
			0)
		|| !TestEqual(
			TEXT("isolated preview keeps the edge count"),
			static_cast<int32>(IsolatedPreview->GetNumberField(TEXT("linkCountAfter"))),
			RootLinkCount)
		|| !TestEqual(
			TEXT("isolated preview drops one node"),
			static_cast<int32>(IsolatedPreview->GetNumberField(TEXT("nodeCountAfter"))),
			RootNodeCount - 1)
		|| !TestEqual(
			TEXT("isolated preview keeps the edge hash"),
			IsolatedPreview->GetStringField(TEXT("edgeSetHashAfter")),
			EdgeHashBefore))
	{
		return Fail();
	}

	bool bSaveBeforeApplyRejected = false;
	{
		TSharedPtr<FJsonObject> Params = MakeShared<FJsonObject>();
		Params->SetStringField(TEXT("assetPath"), AssetPath);
		Params->SetStringField(TEXT("graphGuid"), GraphGuid);
		Params->SetStringField(TEXT("planId"), IsolatedPlanId);
		Params->SetStringField(
			TEXT("expectedEdgeSetHashAfter"),
			IsolatedPreview->GetStringField(TEXT("edgeSetHashAfter")));
		Params->SetStringField(
			TEXT("expectedPackageRevision"),
			IsolatedPreview->GetStringField(TEXT("packageRevision")));
		Params->SetNumberField(
			TEXT("expectedNodeCount"),
			IsolatedPreview->GetNumberField(TEXT("nodeCountAfter")));
		Params->SetNumberField(
			TEXT("expectedLinkCount"),
			IsolatedPreview->GetNumberField(TEXT("linkCountAfter")));
		Params->SetStringField(
			TEXT("confirmation"),
			FString(UEAgentKitBlueprintNodeRemoveExperimental::SaveConfirmationPrefix)
				+ IsolatedPlanId);
		bSaveBeforeApplyRejected = ExpectSaveError(
			*this, Params,
			TEXT("graph-remove-save-requires-apply"),
			TEXT("Save before Apply is rejected"));
	}

	bool bWrongApplyConfirmationRejected = false;
	{
		TSharedPtr<FJsonObject> Params = BuildApplyParams(IsolatedPreview, TEXT("REMOVE"));
		bWrongApplyConfirmationRejected = ExpectApplyError(
			*this, Params,
			TEXT("graph-remove-confirmation-required"),
			TEXT("wrong Apply confirmation is rejected"));
	}

	TSharedPtr<FJsonObject> IsolatedApply;
	FString IsolatedApplyCode;
	FString IsolatedApplyMessage;
	if (!TestTrue(
			TEXT("isolated-node Apply succeeds"),
			UEAgentKitBlueprintNodeRemoveExperimental::TryApplyExperimental(
				BuildApplyParams(
					IsolatedPreview,
					FString(UEAgentKitBlueprintNodeRemoveExperimental::ApplyConfirmationPrefix)
						+ IsolatedPlanId),
				IsolatedApply,
				IsolatedApplyCode,
				IsolatedApplyMessage)))
	{
		AddError(IsolatedApplyCode + TEXT(": ") + IsolatedApplyMessage);
		return Fail();
	}
	if (!TestTrue(TEXT("isolated Apply reports applied"), IsolatedApply->GetBoolField(TEXT("applied")))
		|| !TestTrue(TEXT("isolated Apply verified Undo"), IsolatedApply->GetBoolField(TEXT("undoVerified")))
		|| !TestTrue(TEXT("isolated Apply verified Redo"), IsolatedApply->GetBoolField(TEXT("redoVerified")))
		|| !TestFalse(TEXT("isolated Apply did not save"), IsolatedApply->GetBoolField(TEXT("saved")))
		|| !TestFalse(TEXT("isolated Apply did not compile"), IsolatedApply->GetBoolField(TEXT("compiled")))
		|| !TestEqual(
			TEXT("isolated Apply node count"),
			static_cast<int32>(IsolatedApply->GetNumberField(TEXT("nodeCount"))),
			RootNodeCount - 1)
		|| !TestEqual(
			TEXT("isolated Apply edge count"),
			static_cast<int32>(IsolatedApply->GetNumberField(TEXT("linkCount"))),
			RootLinkCount))
	{
		return Fail();
	}
	if (!TestEqual(
			TEXT("isolated node is gone after Apply"),
			ContainsNodeGuid(NotAGuid),
			false))
	{
		return Fail();
	}

	bool bWrongSaveConfirmationRejected = false;
	bool bWrongSaveHashRejected = false;
	bool bWrongSaveRevisionRejected = false;
	bool bSaveLiveEdgeMismatchRejected = false;
	{
		bWrongSaveConfirmationRejected = ExpectSaveError(
			*this,
			BuildSaveParams(IsolatedApply, TEXT("SAVE")),
			TEXT("graph-remove-confirmation-required"),
			TEXT("wrong Save confirmation is rejected"));
	}
	{
		// The Apply receipt already binds the exact after state, so a request that
		// disagrees with it must fail at the receipt comparison.
		TSharedPtr<FJsonObject> Params = BuildSaveParams(
			IsolatedApply,
			FString(UEAgentKitBlueprintNodeRemoveExperimental::SaveConfirmationPrefix)
				+ IsolatedPlanId);
		Params->SetStringField(
			TEXT("expectedEdgeSetHashAfter"),
			FString(TEXT("sha256:")) + FString::ChrN(64, TEXT('9')));
		bWrongSaveHashRejected = ExpectSaveError(
			*this, Params,
			TEXT("graph-remove-save-plan-mismatch"),
			TEXT("Save with a wrong post-Apply edge hash is rejected"));
	}
	{
		TSharedPtr<FJsonObject> Params = BuildSaveParams(
			IsolatedApply,
			FString(UEAgentKitBlueprintNodeRemoveExperimental::SaveConfirmationPrefix)
				+ IsolatedPlanId);
		Params->SetStringField(
			TEXT("expectedPackageRevision"),
			FString(TEXT("sha256:")) + FString::ChrN(64, TEXT('8')));
		bWrongSaveRevisionRejected = ExpectSaveError(
			*this, Params,
			TEXT("graph-remove-save-plan-mismatch"),
			TEXT("Save with a wrong package revision is rejected"));
	}
	{
		// A live topology change after Apply must fail even when the request
		// echoes the receipt exactly.  The untouched edge is restored afterwards.
		NotCOut->BreakLinkTo(BranchCCondition);
		bSaveLiveEdgeMismatchRejected = ExpectSaveError(
			*this,
			BuildSaveParams(
				IsolatedApply,
				FString(UEAgentKitBlueprintNodeRemoveExperimental::SaveConfirmationPrefix)
					+ IsolatedPlanId),
			TEXT("graph-remove-save-edge-set-mismatch"),
			TEXT("Save after an unplanned live edge change is rejected"));
		NotCOut->MakeLinkTo(BranchCCondition);
		if (!bSaveLiveEdgeMismatchRejected)
		{
			return Fail();
		}
	}

	TSharedPtr<FJsonObject> IsolatedSave;
	FString IsolatedSaveCode;
	FString IsolatedSaveMessage;
	if (!TestTrue(
			TEXT("isolated-node Save succeeds"),
			UEAgentKitBlueprintNodeRemoveExperimental::TrySaveExperimental(
				BuildSaveParams(
					IsolatedApply,
					FString(UEAgentKitBlueprintNodeRemoveExperimental::SaveConfirmationPrefix)
						+ IsolatedPlanId),
				IsolatedSave,
				IsolatedSaveCode,
				IsolatedSaveMessage)))
	{
		AddError(IsolatedSaveCode + TEXT(": ") + IsolatedSaveMessage);
		return Fail();
	}
	if (!TestTrue(TEXT("isolated Save reports saved"), IsolatedSave->GetBoolField(TEXT("saved")))
		|| !TestFalse(
			TEXT("isolated Save leaves the package clean"),
			IsolatedSave->GetBoolField(TEXT("packageDirty")))
		|| !TestFalse(
			TEXT("isolated Save did not compile"),
			IsolatedSave->GetBoolField(TEXT("compiled"))))
	{
		return Fail();
	}

	// 2) Node with exactly one enumerated edge.
	const int32 AfterIsolatedNodes = Fixture.Graph->Nodes.Num();
	const int32 AfterIsolatedLinks = CountLinks(Fixture.Graph);
	FString EdgeHashAfterIsolated;
	if (!TestTrue(
			TEXT("post-isolated edge hash computes"),
			ComputeEdgeHash(Fixture.Graph, EdgeHashAfterIsolated))
		|| !TestEqual(
			TEXT("post-isolated edge hash equals the Apply result"),
			EdgeHashAfterIsolated,
			IsolatedApply->GetStringField(TEXT("edgeSetHashAfter"))))
	{
		return Fail();
	}

	TSharedPtr<FJsonObject> ConnectedPreview;
	FString ConnectedPreviewCode;
	FString ConnectedPreviewMessage;
	{
		TArray<FEdgeDesc> Removed;
		Removed.Add(NotBEdge);
		if (!TestTrue(
				TEXT("single-edge node preview succeeds"),
				UEAgentKitBlueprintNodeRemoveExperimental::TryPreviewExperimental(
					MakePreviewParams(
						Fixture.Graph,
						GuidLower(Fixture.NotB->NodeGuid),
						NotAClass,
						EdgeHashAfterIsolated,
						Removed),
					ConnectedPreview,
					ConnectedPreviewCode,
					ConnectedPreviewMessage)))
		{
			AddError(ConnectedPreviewCode + TEXT(": ") + ConnectedPreviewMessage);
			return Fail();
		}
	}
	const FString ConnectedPlanId = ConnectedPreview->GetStringField(TEXT("planId"));
	if (!TestFalse(
			TEXT("single-edge preview is not isolated"),
			ConnectedPreview->GetBoolField(TEXT("isolatedNode")))
		|| !TestEqual(
			TEXT("single-edge preview removes one edge"),
			static_cast<int32>(ConnectedPreview->GetNumberField(TEXT("removedEdgeCount"))),
			1)
		|| !TestEqual(
			TEXT("single-edge preview drops one link"),
			static_cast<int32>(ConnectedPreview->GetNumberField(TEXT("linkCountAfter"))),
			AfterIsolatedLinks - 1)
		|| !TestEqual(
			TEXT("single-edge preview drops one node"),
			static_cast<int32>(ConnectedPreview->GetNumberField(TEXT("nodeCountAfter"))),
			AfterIsolatedNodes - 1)
		|| !TestNotEqual(
			TEXT("single-edge preview changes the edge hash"),
			ConnectedPreview->GetStringField(TEXT("edgeSetHashAfter")),
			EdgeHashAfterIsolated))
	{
		return Fail();
	}

	TSharedPtr<FJsonObject> ConnectedApply;
	FString ConnectedApplyCode;
	FString ConnectedApplyMessage;
	if (!TestTrue(
			TEXT("single-edge node Apply succeeds"),
			UEAgentKitBlueprintNodeRemoveExperimental::TryApplyExperimental(
				BuildApplyParams(
					ConnectedPreview,
					FString(UEAgentKitBlueprintNodeRemoveExperimental::ApplyConfirmationPrefix)
						+ ConnectedPlanId),
				ConnectedApply,
				ConnectedApplyCode,
				ConnectedApplyMessage)))
	{
		AddError(ConnectedApplyCode + TEXT(": ") + ConnectedApplyMessage);
		return Fail();
	}
	{
		TArray<FString> LiveKeys;
		BuildEdgeKeys(Fixture.Graph, LiveKeys);
		const FString RemovedKey = EdgeKey(NotBOut, BranchBCondition);
		const FString FanoutKey = EdgeKey(NotCOut, BranchCCondition);
		const FString ExecKey = EdgeKey(BranchAThen, BranchBExecute);
		if (!TestFalse(TEXT("removed edge is gone"), LiveKeys.Contains(RemovedKey))
			|| !TestTrue(TEXT("bool fan-out edge survives"), LiveKeys.Contains(FanoutKey))
			|| !TestTrue(TEXT("exec edge survives"), LiveKeys.Contains(ExecKey))
			|| !TestEqual(TEXT("final edge count"), LiveKeys.Num(), AfterIsolatedLinks - 1)
			|| !TestEqual(
				TEXT("single-edge node is gone"),
				ContainsNodeGuid(NotBGuid),
				false))
		{
			return Fail();
		}
	}

	TSharedPtr<FJsonObject> ConnectedSave;
	FString ConnectedSaveCode;
	FString ConnectedSaveMessage;
	if (!TestTrue(
			TEXT("single-edge node Save succeeds"),
			UEAgentKitBlueprintNodeRemoveExperimental::TrySaveExperimental(
				BuildSaveParams(
					ConnectedApply,
					FString(UEAgentKitBlueprintNodeRemoveExperimental::SaveConfirmationPrefix)
						+ ConnectedPlanId),
				ConnectedSave,
				ConnectedSaveCode,
				ConnectedSaveMessage)))
	{
		AddError(ConnectedSaveCode + TEXT(": ") + ConnectedSaveMessage);
		return Fail();
	}
	const FString FinalDiskHash = ConnectedSave->GetStringField(TEXT("assetSha256"));
	const FString AssetFilename = FPackageName::LongPackageNameToFilename(
		Package->GetName(),
		FPackageName::GetAssetPackageExtension());
	{
		FString DiskDigest;
		if (!TestTrue(
				TEXT("final saved package can be hashed"),
				FBlueprintContextSha256::HashFile(AssetFilename, DiskDigest))
			|| !TestEqual(
				TEXT("final asset hash equals disk"),
				FinalDiskHash,
				FString(TEXT("sha256:")) + DiskDigest.ToLower()))
		{
			return Fail();
		}
	}

	TArray<TSharedPtr<FJsonValue>> FinalEdges;
	for (const UEdGraphNode* Node : Fixture.Graph->Nodes)
	{
		if (Node == nullptr)
		{
			continue;
		}
		for (const UEdGraphPin* Pin : Node->Pins)
		{
			if (Pin == nullptr || Pin->Direction != EGPD_Output)
			{
				continue;
			}
			for (const UEdGraphPin* Linked : Pin->LinkedTo)
			{
				if (Linked == nullptr || Linked->GetOwningNode() == nullptr)
				{
					continue;
				}
				TSharedRef<FJsonObject> Edge = MakeShared<FJsonObject>();
				Edge->SetStringField(TEXT("fromNodeGuid"), GuidLower(Node->NodeGuid));
				Edge->SetStringField(TEXT("fromPinName"), Pin->PinName.ToString());
				Edge->SetStringField(TEXT("fromPinGuid"), GuidLower(Pin->PinId));
				Edge->SetStringField(
					TEXT("toNodeGuid"),
					GuidLower(Linked->GetOwningNode()->NodeGuid));
				Edge->SetStringField(TEXT("toPinName"), Linked->PinName.ToString());
				Edge->SetStringField(TEXT("toPinGuid"), GuidLower(Linked->PinId));
				Edge->SetStringField(
					TEXT("kind"),
					Pin->PinType.PinCategory == UEdGraphSchema_K2::PC_Exec
						? TEXT("exec")
						: TEXT("bool"));
				FinalEdges.Add(MakeShared<FJsonValueObject>(Edge));
			}
		}
	}

	TArray<TSharedPtr<FJsonValue>> DeletedNodes;
	DeletedNodes.Add(MakeShared<FJsonValueString>(NotAGuid));
	DeletedNodes.Add(MakeShared<FJsonValueString>(NotBGuid));
	TArray<TSharedPtr<FJsonValue>> RemovedEdges;
	{
		TSharedRef<FJsonObject> RemovedEdge = MakeShared<FJsonObject>();
		RemovedEdge->SetStringField(TEXT("fromNodeGuid"), NotBGuid);
		RemovedEdge->SetStringField(TEXT("fromPinName"), TEXT("ReturnValue"));
		RemovedEdge->SetStringField(TEXT("fromPinGuid"), NotBOutPinGuid);
		RemovedEdge->SetStringField(
			TEXT("toNodeGuid"),
			GuidLower(Fixture.BranchB->NodeGuid));
		RemovedEdge->SetStringField(TEXT("toPinName"), TEXT("Condition"));
		RemovedEdge->SetStringField(TEXT("toPinGuid"), BranchBConditionPinGuid);
		RemovedEdge->SetStringField(TEXT("kind"), TEXT("bool"));
		RemovedEdges.Add(MakeShared<FJsonValueObject>(RemovedEdge));
	}
	TArray<TSharedPtr<FJsonValue>> UntouchedEdges;
	UntouchedEdges.Add(MakeShared<FJsonValueObject>(EdgeJson(FanoutC, TEXT("bool"))));
	UntouchedEdges.Add(MakeShared<FJsonValueObject>(EdgeJson(ExecEdge, TEXT("exec"))));

	TSharedRef<FJsonObject> Checks = MakeShared<FJsonObject>();
	Checks->SetBoolField(TEXT("defaultGateOffRejected"), bGateOffRejected);
	Checks->SetBoolField(TEXT("unknownParameterRejected"), bUnknownParameterRejected);
	Checks->SetBoolField(TEXT("invalidNodeGuidRejected"), bInvalidIdentifierRejected);
	Checks->SetBoolField(TEXT("nodeNotFoundRejected"), bNodeNotFoundRejected);
	Checks->SetBoolField(TEXT("nodeClassMismatchRejected"), bClassMismatchRejected);
	Checks->SetBoolField(TEXT("knotNodeRejected"), bKnotRejected);
	Checks->SetBoolField(TEXT("commentGroupRejected"), bCommentRejected);
	Checks->SetBoolField(TEXT("tooManyConnectionsRejected"), bTooManyConnectionsRejected);
	Checks->SetBoolField(TEXT("missingRemovedEdgeRejected"), bRemovedEdgesMismatchRejected);
	Checks->SetBoolField(TEXT("wrongRemovedEdgeRejected"), bMalformedRemovedEdgesRejected);
	Checks->SetBoolField(TEXT("edgeSetStaleRejected"), bEdgeSetStaleRejected);
	Checks->SetBoolField(TEXT("dirtyPackageRejected"), bDirtyRejected);
	Checks->SetBoolField(TEXT("nonFocusedAssetRejected"), bFocusedMismatchRejected);
	Checks->SetBoolField(TEXT("planStaleRejected"), bPlanStaleRejected);
	Checks->SetBoolField(TEXT("edgeSetStaleAtApplyRejected"), bEdgeSetStaleAtApplyRejected);
	Checks->SetBoolField(TEXT("wrongApplyConfirmationRejected"), bWrongApplyConfirmationRejected);
	Checks->SetBoolField(TEXT("saveBeforeApplyRejected"), bSaveBeforeApplyRejected);
	Checks->SetBoolField(TEXT("wrongSaveConfirmationRejected"), bWrongSaveConfirmationRejected);
	Checks->SetBoolField(TEXT("wrongSaveEdgeHashRejected"), bWrongSaveHashRejected);
	Checks->SetBoolField(TEXT("wrongSaveRevisionRejected"), bWrongSaveRevisionRejected);
	Checks->SetBoolField(
		TEXT("saveAfterLiveEdgeChangeRejected"),
		bSaveLiveEdgeMismatchRejected);
	Checks->SetBoolField(
		TEXT("isolatedPreviewReadOnly"),
		IsolatedPreview->GetBoolField(TEXT("readOnly")));
	Checks->SetBoolField(
		TEXT("isolatedUndoRedoVerified"),
		IsolatedApply->GetBoolField(TEXT("undoVerified"))
			&& IsolatedApply->GetBoolField(TEXT("redoVerified")));
	Checks->SetBoolField(
		TEXT("connectedUndoRedoVerified"),
		ConnectedApply->GetBoolField(TEXT("undoVerified"))
			&& ConnectedApply->GetBoolField(TEXT("redoVerified")));
	Checks->SetBoolField(
		TEXT("deletedNodesGone"),
		!ContainsNodeGuid(NotAGuid) && !ContainsNodeGuid(NotBGuid));
	Checks->SetBoolField(
		TEXT("boolFanoutPreserved"),
		BranchCCondition->LinkedTo.Num() == 1
			&& BranchCCondition->LinkedTo[0] == NotCOut);
	Checks->SetBoolField(
		TEXT("execEdgePreserved"),
		BranchBExecute->LinkedTo.Num() == 1
			&& BranchBExecute->LinkedTo[0] == BranchAThen);
	Checks->SetBoolField(
		TEXT("removedEdgeGone"),
		BranchBCondition->LinkedTo.Num() == 0);
	Checks->SetBoolField(
		TEXT("packageCleanAfterSave"),
		!ConnectedSave->GetBoolField(TEXT("packageDirty")));
	Checks->SetBoolField(
		TEXT("independentReloadPendingCommandlet"),
		!ConnectedSave->GetBoolField(TEXT("independentReloadVerified")));

	TSharedRef<FJsonObject> Evidence = MakeShared<FJsonObject>();
	Evidence->SetStringField(TEXT("schemaVersion"), TEXT("ueak.blueprint-node-remove-evidence/1.0"));
	Evidence->SetNumberField(
		TEXT("processId"),
		static_cast<double>(FPlatformProcess::GetCurrentProcessId()));
	Evidence->SetStringField(TEXT("writtenAtUtc"), FDateTime::UtcNow().ToIso8601());
	Evidence->SetStringField(TEXT("assetPath"), Blueprint->GetPathName());
	Evidence->SetStringField(TEXT("assetFilename"), FPaths::ConvertRelativePathToFull(AssetFilename));
	Evidence->SetStringField(TEXT("graphGuid"), GraphGuid);
	Evidence->SetNumberField(TEXT("expectedNodeCount"), Fixture.Graph->Nodes.Num());
	Evidence->SetNumberField(TEXT("expectedEdgeCount"), 3);
	Evidence->SetNumberField(TEXT("initialNodeCount"), RootNodeCount);
	Evidence->SetNumberField(TEXT("initialEdgeCount"), RootLinkCount);
	Evidence->SetStringField(TEXT("assetSha256"), FinalDiskHash);
	Evidence->SetStringField(TEXT("isolatedPlanId"), IsolatedPlanId);
	Evidence->SetStringField(TEXT("connectedPlanId"), ConnectedPlanId);
	Evidence->SetStringField(TEXT("edgeSetHashBefore"), EdgeHashBefore);
	Evidence->SetStringField(
		TEXT("edgeSetHashAfterIsolated"),
		IsolatedApply->GetStringField(TEXT("edgeSetHashAfter")));
	Evidence->SetStringField(
		TEXT("edgeSetHashAfterConnected"),
		ConnectedApply->GetStringField(TEXT("edgeSetHashAfter")));
	Evidence->SetObjectField(TEXT("isolatedPreview"), IsolatedPreview);
	Evidence->SetObjectField(TEXT("isolatedApply"), IsolatedApply);
	Evidence->SetObjectField(TEXT("isolatedSave"), IsolatedSave);
	Evidence->SetObjectField(TEXT("connectedPreview"), ConnectedPreview);
	Evidence->SetObjectField(TEXT("connectedApply"), ConnectedApply);
	Evidence->SetObjectField(TEXT("connectedSave"), ConnectedSave);
	Evidence->SetArrayField(TEXT("expectedEdges"), FinalEdges);
	Evidence->SetArrayField(TEXT("deletedNodeGuids"), DeletedNodes);
	Evidence->SetArrayField(TEXT("removedEdges"), RemovedEdges);
	Evidence->SetArrayField(TEXT("untouchedEdges"), UntouchedEdges);
	Evidence->SetArrayField(TEXT("pins"), BuildPinInventory(Fixture.Graph));
	Evidence->SetObjectField(TEXT("checks"), Checks);

	const FString EvidencePath = FPaths::Combine(
		FPaths::ProjectSavedDir(),
		EvidenceSubdirectory,
		TEXT("evidence.json"));
	if (!TestTrue(
			TEXT("node-removal evidence is written"),
			WriteJson(EvidencePath, Evidence)))
	{
		return Fail();
	}

	UEAgentKitBlueprintNodeRemoveExperimental::ResetExperimentalWriteForTest();
	return true;
}

namespace UEAgentKitBlueprintNodeRemovePieGate
{
	const TCHAR* const EvidenceSubdirectory = TEXT("BlueprintNodeRemove");

	FString GuidLower(const FGuid& Guid)
	{
		return Guid.ToString(EGuidFormats::DigitsWithHyphensLower);
	}

	bool WriteJson(const FString& Path, const TSharedPtr<FJsonObject>& Object)
	{
		FString Text;
		const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Text);
		if (!FJsonSerializer::Serialize(Object.ToSharedRef(), Writer))
		{
			return false;
		}
		IFileManager::Get().MakeDirectory(*FPaths::GetPath(Path), true);
		return FFileHelper::SaveStringToFile(
			Text,
			*Path,
			FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);
	}

	struct FState
	{
		FAutomationTestBase* Test = nullptr;
		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = nullptr;
		double StartedAt = 0.0;
		bool bCompleted = false;
	};

	bool ResolveFixture(FState& OutState, FString& OutError)
	{
		UBlueprint* Blueprint = LoadObject<UBlueprint>(
			nullptr,
			UEAgentKitBlueprintNodeRemoveExpansion::AssetPath);
		if (Blueprint == nullptr || Blueprint->UbergraphPages.IsEmpty())
		{
			OutError = TEXT("The saved node-removal fixture is missing; run UEAgentKit.NodeRemove.Expansion first.");
			return false;
		}
		UEdGraph* Graph = Blueprint->UbergraphPages[0];
		UAssetEditorSubsystem* Subsystem = GEditor != nullptr
			? GEditor->GetEditorSubsystem<UAssetEditorSubsystem>()
			: nullptr;
		if (Subsystem == nullptr)
		{
			OutError = TEXT("No AssetEditorSubsystem for the PIE gate fixture.");
			return false;
		}
		Subsystem->OpenEditorForAsset(Blueprint);
		IAssetEditorInstance* Instance = Subsystem->FindEditorForAsset(Blueprint, false);
		if (Instance == nullptr || Instance->GetEditorName() != FName(TEXT("BlueprintEditor")))
		{
			OutError = TEXT("No BlueprintEditor for the PIE gate fixture.");
			return false;
		}
		FBlueprintEditor* Editor = static_cast<FBlueprintEditor*>(Instance);
		if (!Editor->OpenGraphAndBringToFront(Graph, true).IsValid()
			|| Editor->GetFocusedGraph() != Graph)
		{
			OutError = TEXT("Could not focus the PIE gate fixture graph.");
			return false;
		}
		OutState.Blueprint = Blueprint;
		OutState.Graph = Graph;
		return true;
	}

	TSharedPtr<FJsonObject> MakePreviewParams()
	{
		TSharedRef<FJsonObject> Params = MakeShared<FJsonObject>();
		Params->SetStringField(
			TEXT("assetPath"),
			UEAgentKitBlueprintNodeRemoveExpansion::AssetPath);
		Params->SetStringField(
			TEXT("graphGuid"),
			TEXT("00000000-0000-0000-0000-000000000000"));
		Params->SetStringField(
			TEXT("nodeGuid"),
			TEXT("00000000-0000-0000-0000-000000000000"));
		Params->SetStringField(
			TEXT("expectedNodeClass"),
			TEXT("/Script/BlueprintGraph.K2Node_CallFunction"));
		Params->SetStringField(
			TEXT("expectedEdgeSetHash"),
			FString(TEXT("sha256:")) + FString::ChrN(64, TEXT('a')));
		Params->SetArrayField(TEXT("removedEdges"), TArray<TSharedPtr<FJsonValue>>());
		return Params;
	}

	class FWaitForPieThenReject : public IAutomationLatentCommand
	{
	public:
		explicit FWaitForPieThenReject(const TSharedRef<FState>& InState)
			: State(InState)
		{
		}

		virtual bool Update() override
		{
			FAutomationTestBase* Test = State->Test;
			if (!State->bCompleted && State->StartedAt == 0.0)
			{
				State->StartedAt = FPlatformTime::Seconds();
			}
			const bool bPlaying = GEditor != nullptr
				&& GEditor->IsPlaySessionInProgress()
				&& GEditor->PlayWorld != nullptr;
			if (bPlaying)
			{
				TArray<FString> BeforeKeys;
				UEAgentKitBlueprintNodeRemoveExpansion::BuildEdgeKeys(State->Graph, BeforeKeys);

				TSharedPtr<FJsonObject> PreviewResult;
				FString PreviewCode;
				FString PreviewMessage;
				const bool bPreviewRejected =
					!UEAgentKitBlueprintNodeRemoveExperimental::TryPreviewExperimental(
						MakePreviewParams(),
						PreviewResult,
						PreviewCode,
						PreviewMessage);

				TSharedPtr<FJsonObject> ApplyParams = MakePreviewParams();
				const FString FabricatedPlanId =
					FString(TEXT("sha256:")) + FString::ChrN(64, TEXT('a'));
				ApplyParams->SetStringField(TEXT("planId"), FabricatedPlanId);
				ApplyParams->SetStringField(
					TEXT("confirmation"),
					FString(UEAgentKitBlueprintNodeRemoveExperimental::ApplyConfirmationPrefix)
						+ FabricatedPlanId);
				TSharedPtr<FJsonObject> ApplyResult;
				FString ApplyCode;
				FString ApplyMessage;
				const bool bApplyRejected =
					!UEAgentKitBlueprintNodeRemoveExperimental::TryApplyExperimental(
						ApplyParams,
						ApplyResult,
						ApplyCode,
						ApplyMessage);

				TArray<FString> AfterKeys;
				UEAgentKitBlueprintNodeRemoveExpansion::BuildEdgeKeys(State->Graph, AfterKeys);
				const bool bEdgesUnchanged = BeforeKeys == AfterKeys;
				const bool bOk = bPreviewRejected
					&& PreviewCode == TEXT("graph-remove-pie-active")
					&& bApplyRejected
					&& ApplyCode == TEXT("graph-remove-pie-active")
					&& bEdgesUnchanged;

				TSharedRef<FJsonObject> Evidence = MakeShared<FJsonObject>();
				Evidence->SetStringField(
					TEXT("schemaVersion"),
					TEXT("ueak.blueprint-node-remove-pie-gate/1.0"));
				Evidence->SetNumberField(
					TEXT("processId"),
					static_cast<double>(FPlatformProcess::GetCurrentProcessId()));
				Evidence->SetStringField(TEXT("writtenAtUtc"), FDateTime::UtcNow().ToIso8601());
				Evidence->SetStringField(TEXT("assetPath"), State->Blueprint->GetPathName());
				Evidence->SetStringField(TEXT("graphGuid"), GuidLower(State->Graph->GraphGuid));
				Evidence->SetBoolField(TEXT("pieStarted"), true);
				Evidence->SetBoolField(TEXT("previewRejected"), bPreviewRejected);
				Evidence->SetStringField(TEXT("previewErrorCode"), PreviewCode);
				Evidence->SetStringField(TEXT("previewErrorMessage"), PreviewMessage);
				Evidence->SetBoolField(TEXT("applyRejected"), bApplyRejected);
				Evidence->SetStringField(TEXT("applyErrorCode"), ApplyCode);
				Evidence->SetStringField(TEXT("applyErrorMessage"), ApplyMessage);
				Evidence->SetBoolField(TEXT("edgesUnchanged"), bEdgesUnchanged);
				Evidence->SetNumberField(TEXT("edgeCount"), AfterKeys.Num());
				Evidence->SetNumberField(TEXT("nodeCount"), State->Graph->Nodes.Num());
				Evidence->SetBoolField(TEXT("success"), bOk);
				const FString Path = FPaths::Combine(
					FPaths::ProjectSavedDir(),
					EvidenceSubdirectory,
					TEXT("pie-gate.json"));
				if (!WriteJson(Path, Evidence))
				{
					Test->AddError(TEXT("Could not write the node-removal PIE gate evidence."));
				}
				Test->TestTrue(
					TEXT("PIE rejects node-removal preview and apply with pie-active"),
					bOk);
				if (!bOk)
				{
					Test->AddError(FString::Printf(
						TEXT("PIE gate detail: preview=%s/%s apply=%s/%s unchanged=%s"),
						bPreviewRejected ? TEXT("rejected") : TEXT("accepted"),
						*PreviewCode,
						bApplyRejected ? TEXT("rejected") : TEXT("accepted"),
						*ApplyCode,
						bEdgesUnchanged ? TEXT("true") : TEXT("false")));
				}
				UEAgentKitBlueprintNodeRemoveExperimental::ResetExperimentalWriteForTest();
				State->bCompleted = true;
				return true;
			}
			if (FPlatformTime::Seconds() - State->StartedAt > 60.0)
			{
				TSharedRef<FJsonObject> Evidence = MakeShared<FJsonObject>();
				Evidence->SetStringField(
					TEXT("schemaVersion"),
					TEXT("ueak.blueprint-node-remove-pie-gate/1.0"));
				Evidence->SetNumberField(
					TEXT("processId"),
					static_cast<double>(FPlatformProcess::GetCurrentProcessId()));
				Evidence->SetStringField(TEXT("writtenAtUtc"), FDateTime::UtcNow().ToIso8601());
				Evidence->SetBoolField(TEXT("pieStarted"), false);
				Evidence->SetBoolField(TEXT("success"), false);
				Evidence->SetStringField(
					TEXT("note"),
					TEXT("PIE did not start within 60 seconds; the node-removal PIE gate was not exercised."));
				WriteJson(
					FPaths::Combine(
						FPaths::ProjectSavedDir(),
						EvidenceSubdirectory,
						TEXT("pie-gate.json")),
					Evidence);
				Test->AddError(
					TEXT("PIE did not start within 60 seconds; node-removal PIE rejection was not run."));
				UEAgentKitBlueprintNodeRemoveExperimental::ResetExperimentalWriteForTest();
				State->bCompleted = true;
				return true;
			}
			return false;
		}

	private:
		TSharedRef<FState> State;
	};

	class FWaitForPieStop : public IAutomationLatentCommand
	{
	public:
		explicit FWaitForPieStop(const TSharedRef<FState>& InState)
			: State(InState)
		{
		}

		virtual bool Update() override
		{
			return GEditor == nullptr
				|| (!GEditor->IsPlaySessionInProgress() && GEditor->PlayWorld == nullptr);
		}

	private:
		TSharedRef<FState> State;
	};
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitBlueprintNodeRemovePieGateTest,
	"UEAgentKit.NodeRemove.PieGate",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitBlueprintNodeRemovePieGateTest::RunTest(const FString& Parameters)
{
	using namespace UEAgentKitBlueprintNodeRemovePieGate;
	FString Error;
	TSharedRef<FState> State = MakeShared<FState>();
	State->Test = this;
	if (!ResolveFixture(*State, Error))
	{
		AddError(Error);
		return false;
	}
	UEAgentKitBlueprintNodeRemoveExperimental::SetExperimentalWriteEnabledForTest(true);
	ADD_LATENT_AUTOMATION_COMMAND(FStartPIECommand(false));
	ADD_LATENT_AUTOMATION_COMMAND(FWaitForPieThenReject(State));
	ADD_LATENT_AUTOMATION_COMMAND(FEndPlayMapCommand());
	ADD_LATENT_AUTOMATION_COMMAND(FWaitForPieStop(State));
	return true;
}

#endif // WITH_DEV_AUTOMATION_TESTS
