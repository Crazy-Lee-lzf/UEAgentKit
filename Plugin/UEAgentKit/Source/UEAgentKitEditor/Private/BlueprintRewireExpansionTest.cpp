// Real-Editor acceptance for the P3 bounded Blueprint single-edge rewire slice.
//
// The fixture is one ordinary event graph with five exact edges:
//   NotA.ReturnValue  -> BranchA.Condition   (the edge that is rewired)
//   NotB.ReturnValue  -> BranchB.Condition   (the edge that is disconnected)
//   NotB.ReturnValue  -> BranchC.Condition   (untouched bool fan-out)
//   BranchA.then      -> BranchB.execute     (untouched exec edge)
//   BranchD.then      -> BranchE.execute     (untouched exec edge)
//
// The test proves the default-OFF gate, unknown parameters, invalid
// identifiers, missing nodes/pins, name/direction/kind/type rejections, the
// unsafe "schema would break the new source's other links" rejection, stale
// edge sets, stale plans, dirty packages, wrong confirmations, Save before
// Apply and PIE, then performs Preview -> Apply -> Undo/Redo -> Save for one
// rewire and one explicit disconnect.  The four untouched edges must survive
// both operations byte-for-byte (names + pin GUIDs + link set hash).

#include "BlueprintContextSha256.h"
#include "BlueprintEditor.h"
#include "BlueprintGraphLayoutApply.h"
#include "BlueprintRewireExperimental.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "EdGraphSchema_K2.h"
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

namespace UEAgentKitBlueprintRewireExpansion
{
	const TCHAR* const AssetPath =
		TEXT("/Game/UEAgentKitBlueprintRewire/BP_RewireExpansion.BP_RewireExpansion");
	const TCHAR* const PackageName = TEXT("/Game/UEAgentKitBlueprintRewire/BP_RewireExpansion");
	const TCHAR* const AssetName = TEXT("BP_RewireExpansion");
	const TCHAR* const EvidenceSubdirectory = TEXT("BlueprintRewire");

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

	struct FPinTriple
	{
		UEdGraphNode* Node = nullptr;
		UEdGraphPin* Pin = nullptr;
		FString Name;
	};

	FPinTriple MakeTriple(UEdGraphNode* Node, const TCHAR* PinName, const EEdGraphPinDirection Direction)
	{
		FPinTriple Triple;
		Triple.Node = Node;
		Triple.Name = PinName;
		Triple.Pin = Node != nullptr ? Node->FindPin(FName(PinName), Direction) : nullptr;
		return Triple;
	}

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
		FString Canonical = TEXT("ueak.blueprint-rewire-edge-set/1.0\n");
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
				TEXT("Rewire fixture SavePackage failed package=%s file=%s"),
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

	struct FFixture
	{
		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = nullptr;
		UK2Node_CallFunction* NotA = nullptr;
		UK2Node_CallFunction* NotB = nullptr;
		UK2Node_CallFunction* NotC = nullptr;
		UK2Node_CallFunction* IntAdd = nullptr;
		UK2Node_IfThenElse* BranchA = nullptr;
		UK2Node_IfThenElse* BranchB = nullptr;
		UK2Node_IfThenElse* BranchC = nullptr;
		UK2Node_IfThenElse* BranchD = nullptr;
		UK2Node_IfThenElse* BranchE = nullptr;
		UK2Node_Knot* Knot = nullptr;
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
			OutError = TEXT("Rewire fixture already exists; use a fresh project.");
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
			FName(TEXT("UEAgentKitBlueprintRewireExpansion")));
		if (Blueprint == nullptr || Blueprint->UbergraphPages.IsEmpty())
		{
			OutError = TEXT("Could not create the rewire fixture Blueprint.");
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
		OutFixture.IntAdd = AddFunction(Graph, TEXT("Add_IntInt"), -640, 520);
		OutFixture.BranchA = AddBranch(Graph, -160, -200);
		OutFixture.BranchB = AddBranch(Graph, 160, -200);
		OutFixture.BranchC = AddBranch(Graph, 160, 60);
		OutFixture.BranchD = AddBranch(Graph, -160, 320);
		OutFixture.BranchE = AddBranch(Graph, 160, 320);
		OutFixture.Knot = AddKnot(Graph, -640, 760);
		if (OutFixture.NotA == nullptr || OutFixture.NotB == nullptr
			|| OutFixture.NotC == nullptr || OutFixture.IntAdd == nullptr
			|| OutFixture.BranchA == nullptr || OutFixture.BranchB == nullptr
			|| OutFixture.BranchC == nullptr || OutFixture.BranchD == nullptr
			|| OutFixture.BranchE == nullptr || OutFixture.Knot == nullptr)
		{
			OutError = TEXT("Could not create every rewire fixture node.");
			return nullptr;
		}

		UEdGraphPin* NotAOut = OutFixture.NotA->FindPin(TEXT("ReturnValue"), EGPD_Output);
		UEdGraphPin* NotBOut = OutFixture.NotB->FindPin(TEXT("ReturnValue"), EGPD_Output);
		UEdGraphPin* BranchACondition = OutFixture.BranchA->FindPin(TEXT("Condition"), EGPD_Input);
		UEdGraphPin* BranchBCondition = OutFixture.BranchB->FindPin(TEXT("Condition"), EGPD_Input);
		UEdGraphPin* BranchCCondition = OutFixture.BranchC->FindPin(TEXT("Condition"), EGPD_Input);
		UEdGraphPin* BranchAThen = OutFixture.BranchA->FindPin(TEXT("then"), EGPD_Output);
		UEdGraphPin* BranchBExecute = OutFixture.BranchB->FindPin(TEXT("execute"), EGPD_Input);
		UEdGraphPin* BranchDThen = OutFixture.BranchD->FindPin(TEXT("then"), EGPD_Output);
		UEdGraphPin* BranchEExecute = OutFixture.BranchE->FindPin(TEXT("execute"), EGPD_Input);
		if (!LinkPins(Graph, NotAOut, BranchACondition, OutError)
			|| !LinkPins(Graph, NotBOut, BranchBCondition, OutError)
			|| !LinkPins(Graph, NotBOut, BranchCCondition, OutError)
			|| !LinkPins(Graph, BranchAThen, BranchBExecute, OutError)
			|| !LinkPins(Graph, BranchDThen, BranchEExecute, OutError))
		{
			return nullptr;
		}

		// Fixture-only compile: the fixture itself must start consistent.  The
		// product path under test never compiles.
		FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
		FAssetRegistryModule::AssetCreated(Blueprint);
		if (!SaveBlueprint(Blueprint))
		{
			OutError = TEXT("Could not save the rewire fixture Blueprint.");
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
			OutError = TEXT("No BlueprintEditor for the rewire fixture.");
			return nullptr;
		}
		FBlueprintEditor* Editor = static_cast<FBlueprintEditor*>(Instance);
		if (!Editor->OpenGraphAndBringToFront(Graph, true).IsValid()
			|| Editor->GetFocusedGraph() != Graph)
		{
			OutError = TEXT("Could not focus the rewire fixture graph.");
			return nullptr;
		}
		return Editor;
	}

	TSharedPtr<FJsonObject> MakePreviewParams(
		const TCHAR* Operation,
		const FPinTriple& OldSource,
		const FPinTriple& Target,
		const FPinTriple* NewSource,
		const FString& EdgeSetHash)
	{
		TSharedRef<FJsonObject> Params = MakeShared<FJsonObject>();
		Params->SetStringField(TEXT("assetPath"), AssetPath);
		Params->SetStringField(TEXT("graphGuid"), GuidLower(Target.Node->GetGraph()->GraphGuid));
		Params->SetStringField(TEXT("operation"), Operation);
		Params->SetStringField(TEXT("targetNodeGuid"), GuidLower(Target.Node->NodeGuid));
		Params->SetStringField(TEXT("targetPinName"), Target.Name);
		Params->SetStringField(TEXT("targetPinGuid"), GuidLower(Target.Pin->PinId));
		Params->SetStringField(TEXT("oldSourceNodeGuid"), GuidLower(OldSource.Node->NodeGuid));
		Params->SetStringField(TEXT("oldSourcePinName"), OldSource.Name);
		Params->SetStringField(TEXT("oldSourcePinGuid"), GuidLower(OldSource.Pin->PinId));
		Params->SetStringField(TEXT("expectedEdgeSetHash"), EdgeSetHash);
		if (NewSource != nullptr)
		{
			Params->SetStringField(TEXT("newSourceNodeGuid"), GuidLower(NewSource->Node->NodeGuid));
			Params->SetStringField(TEXT("newSourcePinName"), NewSource->Name);
			Params->SetStringField(TEXT("newSourcePinGuid"), GuidLower(NewSource->Pin->PinId));
		}
		return Params;
	}

	TSharedPtr<FJsonObject> BuildApplyParams(
		const TSharedPtr<FJsonObject>& Preview,
		const FString& Confirmation)
	{
		TSharedRef<FJsonObject> Params = MakeShared<FJsonObject>();
		Params->SetStringField(TEXT("assetPath"), Preview->GetStringField(TEXT("assetPath")));
		Params->SetStringField(TEXT("graphGuid"), Preview->GetStringField(TEXT("graphGuid")));
		Params->SetStringField(TEXT("operation"), Preview->GetStringField(TEXT("operation")));
		const TSharedPtr<FJsonObject> OldSource = Preview->GetObjectField(TEXT("oldSourcePin"));
		Params->SetStringField(TEXT("oldSourceNodeGuid"), OldSource->GetStringField(TEXT("nodeGuid")));
		Params->SetStringField(TEXT("oldSourcePinName"), OldSource->GetStringField(TEXT("pinName")));
		Params->SetStringField(TEXT("oldSourcePinGuid"), OldSource->GetStringField(TEXT("pinGuid")));
		const TSharedPtr<FJsonObject> Target = Preview->GetObjectField(TEXT("targetPin"));
		Params->SetStringField(TEXT("targetNodeGuid"), Target->GetStringField(TEXT("nodeGuid")));
		Params->SetStringField(TEXT("targetPinName"), Target->GetStringField(TEXT("pinName")));
		Params->SetStringField(TEXT("targetPinGuid"), Target->GetStringField(TEXT("pinGuid")));
		if (Preview->HasTypedField<EJson::Object>(TEXT("newSourcePin")))
		{
			const TSharedPtr<FJsonObject> NewSource =
				Preview->GetObjectField(TEXT("newSourcePin"));
			Params->SetStringField(TEXT("newSourceNodeGuid"), NewSource->GetStringField(TEXT("nodeGuid")));
			Params->SetStringField(TEXT("newSourcePinName"), NewSource->GetStringField(TEXT("pinName")));
			Params->SetStringField(TEXT("newSourcePinGuid"), NewSource->GetStringField(TEXT("pinGuid")));
		}
		Params->SetStringField(
			TEXT("expectedEdgeSetHash"),
			Preview->GetStringField(TEXT("edgeSetHashBefore")));
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

	TSharedPtr<FJsonObject> EdgeJson(const FPinTriple& From, const FPinTriple& To, const TCHAR* Kind)
	{
		TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
		Item->SetStringField(TEXT("fromNodeGuid"), GuidLower(From.Node->NodeGuid));
		Item->SetStringField(TEXT("fromPinName"), From.Name);
		Item->SetStringField(TEXT("fromPinGuid"), GuidLower(From.Pin->PinId));
		Item->SetStringField(TEXT("toNodeGuid"), GuidLower(To.Node->NodeGuid));
		Item->SetStringField(TEXT("toPinName"), To.Name);
		Item->SetStringField(TEXT("toPinGuid"), GuidLower(To.Pin->PinId));
		Item->SetStringField(TEXT("kind"), Kind);
		return Item;
	}

	TSharedPtr<FJsonObject> PinJson(const FPinTriple& Triple, const TCHAR* Direction, const TCHAR* Category)
	{
		TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
		Item->SetStringField(TEXT("nodeGuid"), GuidLower(Triple.Node->NodeGuid));
		Item->SetStringField(TEXT("pinName"), Triple.Name);
		Item->SetStringField(TEXT("pinGuid"), GuidLower(Triple.Pin->PinId));
		Item->SetStringField(TEXT("direction"), Direction);
		Item->SetStringField(TEXT("category"), Category);
		return Item;
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
		const bool bRejected =
			!UEAgentKitBlueprintRewireExperimental::TryPreviewExperimental(
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
		const bool bRejected =
			!UEAgentKitBlueprintRewireExperimental::TryApplyExperimental(
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
		const bool bRejected =
			!UEAgentKitBlueprintRewireExperimental::TrySaveExperimental(
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
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitBlueprintRewireExpansionTest,
	"UEAgentKit.Rewire.Expansion",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitBlueprintRewireExpansionTest::RunTest(const FString& Parameters)
{
	using namespace UEAgentKitBlueprintRewireExpansion;
	UEAgentKitBlueprintRewireExperimental::ResetExperimentalWriteForTest();
	if (!TestFalse(
			TEXT("experimental Blueprint rewire gate is off unless the project opts in"),
			UEAgentKitBlueprintRewireExperimental::IsExperimentalWriteAvailable()))
	{
		return false;
	}

	bool bGateOffRejected = false;
	{
		TSharedPtr<FJsonObject> Params = MakeShared<FJsonObject>();
		Params->SetStringField(TEXT("assetPath"), AssetPath);
		Params->SetStringField(
			TEXT("graphGuid"),
			TEXT("00000000-1111-4222-8333-444444444444"));
		Params->SetStringField(TEXT("operation"), TEXT("disconnect"));
		Params->SetStringField(
			TEXT("targetNodeGuid"),
			TEXT("11111111-2222-4333-8444-555555555555"));
		Params->SetStringField(TEXT("targetPinName"), TEXT("Condition"));
		Params->SetStringField(
			TEXT("targetPinGuid"),
			TEXT("12222222-2222-4333-8444-555555555555"));
		Params->SetStringField(
			TEXT("oldSourceNodeGuid"),
			TEXT("21111111-2222-4333-8444-555555555555"));
		Params->SetStringField(TEXT("oldSourcePinName"), TEXT("ReturnValue"));
		Params->SetStringField(
			TEXT("oldSourcePinGuid"),
			TEXT("22222222-2222-4333-8444-555555555555"));
		Params->SetStringField(
			TEXT("expectedEdgeSetHash"),
			FString(TEXT("sha256:")) + FString::ChrN(64, TEXT('a')));
		bGateOffRejected = ExpectPreviewError(
			*this,
			Params,
			TEXT("graph-rewire-experimental-disabled"),
			TEXT("gate-off rewire preview is rejected"));
		if (!bGateOffRejected)
		{
			return false;
		}
	}

	UEAgentKitBlueprintRewireExperimental::SetExperimentalWriteEnabledForTest(true);
	auto Fail = []()
	{
		UEAgentKitBlueprintRewireExperimental::ResetExperimentalWriteForTest();
		return false;
	};

	FString Error;
	FFixture Fixture;
	UBlueprint* Blueprint = CreateFixture(Fixture, Error);
	if (!TestNotNull(TEXT("rewire fixture is created"), Blueprint))
	{
		AddError(Error);
		return Fail();
	}
	FBlueprintEditor* Editor = OpenAndFocus(Blueprint, Fixture.Graph, Error);
	if (!TestNotNull(TEXT("rewire fixture Blueprint is focused"), Editor))
	{
		AddError(Error);
		return Fail();
	}
	UPackage* Package = Blueprint->GetOutermost();

	const int32 RootNodeCount = Fixture.Graph->Nodes.Num();
	const int32 RootLinkCount = CountLinks(Fixture.Graph);
	if (!TestEqual(TEXT("fixture starts with ten nodes"), RootNodeCount, 10)
		|| !TestEqual(TEXT("fixture starts with five edges"), RootLinkCount, 5))
	{
		return Fail();
	}

	const FPinTriple NotAOut = MakeTriple(Fixture.NotA, TEXT("ReturnValue"), EGPD_Output);
	const FPinTriple NotBOut = MakeTriple(Fixture.NotB, TEXT("ReturnValue"), EGPD_Output);
	const FPinTriple NotCOut = MakeTriple(Fixture.NotC, TEXT("ReturnValue"), EGPD_Output);
	const FPinTriple IntAddOut = MakeTriple(Fixture.IntAdd, TEXT("ReturnValue"), EGPD_Output);
	const FPinTriple NotCInput = MakeTriple(Fixture.NotC, TEXT("A"), EGPD_Input);
	const FPinTriple BranchACondition = MakeTriple(Fixture.BranchA, TEXT("Condition"), EGPD_Input);
	const FPinTriple BranchBCondition = MakeTriple(Fixture.BranchB, TEXT("Condition"), EGPD_Input);
	const FPinTriple BranchCCondition = MakeTriple(Fixture.BranchC, TEXT("Condition"), EGPD_Input);
	const FPinTriple BranchAThen = MakeTriple(Fixture.BranchA, TEXT("then"), EGPD_Output);
	const FPinTriple BranchBExecute = MakeTriple(Fixture.BranchB, TEXT("execute"), EGPD_Input);
	const FPinTriple BranchDThen = MakeTriple(Fixture.BranchD, TEXT("then"), EGPD_Output);
	const FPinTriple BranchEExecute = MakeTriple(Fixture.BranchE, TEXT("execute"), EGPD_Input);
	const FPinTriple KnotOutput = MakeTriple(Fixture.Knot, TEXT("OutputPin"), EGPD_Output);
	if (!TestNotNull(TEXT("fixture pins resolve"), NotAOut.Pin)
		|| !TestNotNull(TEXT("not B output resolves"), NotBOut.Pin)
		|| !TestNotNull(TEXT("not C output resolves"), NotCOut.Pin)
		|| !TestNotNull(TEXT("int add output resolves"), IntAddOut.Pin)
		|| !TestNotNull(TEXT("not C input resolves"), NotCInput.Pin)
		|| !TestNotNull(TEXT("branch A condition resolves"), BranchACondition.Pin)
		|| !TestNotNull(TEXT("branch B condition resolves"), BranchBCondition.Pin)
		|| !TestNotNull(TEXT("branch C condition resolves"), BranchCCondition.Pin)
		|| !TestNotNull(TEXT("branch A then resolves"), BranchAThen.Pin)
		|| !TestNotNull(TEXT("branch B execute resolves"), BranchBExecute.Pin)
		|| !TestNotNull(TEXT("branch D then resolves"), BranchDThen.Pin)
		|| !TestNotNull(TEXT("branch E execute resolves"), BranchEExecute.Pin)
		|| !TestNotNull(TEXT("knot output resolves"), KnotOutput.Pin))
	{
		return Fail();
	}

	FString EdgeHashBefore;
	if (!TestTrue(TEXT("fixture edge hash computes"), ComputeEdgeHash(Fixture.Graph, EdgeHashBefore)))
	{
		return Fail();
	}
	if (!TestTrue(
			TEXT("fixture edge hash matches the product hash"),
			EdgeHashBefore.Len() == 71))
	{
		return Fail();
	}

	bool bUnknownParameterRejected = false;
	bool bInvalidOperationRejected = false;
	bool bNodeNotFoundRejected = false;
	bool bPinNotFoundRejected = false;
	bool bPinNameMismatchRejected = false;
	bool bDirectionMismatchRejected = false;
	bool bOldEdgeMismatchRejected = false;
	bool bNoOpRejected = false;
	bool bKindMismatchRejected = false;
	bool bTypeNotAllowedRejected = false;
	bool bBreakOthersRejected = false;
	bool bTargetUnlinkedRejected = false;
	bool bEdgeSetStaleRejected = false;
	bool bDirtyRejected = false;
	bool bFocusedMismatchRejected = false;
	bool bPlanStaleRejected = false;

	{
		TSharedPtr<FJsonObject> Params = MakePreviewParams(
			TEXT("rewire"), NotAOut, BranchACondition, &NotCOut, EdgeHashBefore);
		Params->SetStringField(TEXT("planId"), TEXT("sha256:ignored"));
		bUnknownParameterRejected = ExpectPreviewError(
			*this, Params,
			TEXT("graph-rewire-unknown-parameter"),
			TEXT("unknown preview parameter is rejected"));
	}
	{
		TSharedPtr<FJsonObject> Params = MakePreviewParams(
			TEXT("move"), NotAOut, BranchACondition, &NotCOut, EdgeHashBefore);
		bInvalidOperationRejected = ExpectPreviewError(
			*this, Params,
			TEXT("graph-rewire-invalid-parameters"),
			TEXT("unknown operation is rejected"));
	}
	{
		TSharedPtr<FJsonObject> Params = MakePreviewParams(
			TEXT("rewire"), NotAOut, BranchACondition, &NotCOut, EdgeHashBefore);
		Params->SetStringField(
			TEXT("targetNodeGuid"),
			TEXT("99999999-9999-4999-8999-999999999999"));
		bNodeNotFoundRejected = ExpectPreviewError(
			*this, Params,
			TEXT("graph-rewire-node-not-found"),
			TEXT("unknown target node is rejected"));
	}
	{
		TSharedPtr<FJsonObject> Params = MakePreviewParams(
			TEXT("rewire"), NotAOut, BranchACondition, &NotCOut, EdgeHashBefore);
		Params->SetStringField(
			TEXT("targetPinGuid"),
			TEXT("99999999-9999-4999-8999-999999999999"));
		bPinNotFoundRejected = ExpectPreviewError(
			*this, Params,
			TEXT("graph-rewire-pin-not-found"),
			TEXT("unknown target pin GUID is rejected"));
	}
	{
		TSharedPtr<FJsonObject> Params = MakePreviewParams(
			TEXT("rewire"), NotAOut, BranchACondition, &NotCOut, EdgeHashBefore);
		Params->SetStringField(TEXT("targetPinName"), TEXT("Execute"));
		bPinNameMismatchRejected = ExpectPreviewError(
			*this, Params,
			TEXT("graph-rewire-pin-name-mismatch"),
			TEXT("target pin name mismatch is rejected"));
	}
	{
		TSharedPtr<FJsonObject> Params = MakePreviewParams(
			TEXT("rewire"), NotAOut, BranchAThen, &NotCOut, EdgeHashBefore);
		bDirectionMismatchRejected = ExpectPreviewError(
			*this, Params,
			TEXT("graph-rewire-pin-direction-mismatch"),
			TEXT("output pin as target is rejected"));
	}
	{
		TSharedPtr<FJsonObject> Params = MakePreviewParams(
			TEXT("rewire"), NotCOut, BranchACondition, &NotBOut, EdgeHashBefore);
		bOldEdgeMismatchRejected = ExpectPreviewError(
			*this, Params,
			TEXT("graph-rewire-old-edge-mismatch"),
			TEXT("wrong old source is rejected"));
	}
	{
		TSharedPtr<FJsonObject> Params = MakePreviewParams(
			TEXT("rewire"), NotAOut, BranchACondition, &NotAOut, EdgeHashBefore);
		bNoOpRejected = ExpectPreviewError(
			*this, Params,
			TEXT("graph-rewire-no-op"),
			TEXT("rewire to the current source is rejected"));
	}
	{
		TSharedPtr<FJsonObject> Params = MakePreviewParams(
			TEXT("rewire"), NotAOut, BranchACondition, &IntAddOut, EdgeHashBefore);
		bKindMismatchRejected = ExpectPreviewError(
			*this, Params,
			TEXT("graph-rewire-pin-kind-mismatch"),
			TEXT("int source into a bool target is rejected"));
	}
	{
		TSharedPtr<FJsonObject> Params = MakePreviewParams(
			TEXT("rewire"), NotAOut, BranchACondition, &KnotOutput, EdgeHashBefore);
		bTypeNotAllowedRejected = ExpectPreviewError(
			*this, Params,
			TEXT("graph-rewire-pin-type-not-allowed"),
			TEXT("wildcard knot pin is rejected"));
	}
	{
		TSharedPtr<FJsonObject> Params = MakePreviewParams(
			TEXT("rewire"), BranchAThen, BranchBExecute, &BranchDThen, EdgeHashBefore);
		bBreakOthersRejected = ExpectPreviewError(
			*this, Params,
			TEXT("graph-rewire-schema-would-break-other-links"),
			TEXT("schema response that would break the new source's other links is rejected"));
	}
	{
		TSharedPtr<FJsonObject> Params = MakePreviewParams(
			TEXT("rewire"), NotAOut, NotCInput, &NotBOut, EdgeHashBefore);
		bTargetUnlinkedRejected = ExpectPreviewError(
			*this, Params,
			TEXT("graph-rewire-target-unlinked"),
			TEXT("unlinked target pin is rejected"));
	}
	{
		TSharedPtr<FJsonObject> Params = MakePreviewParams(
			TEXT("rewire"),
			NotAOut,
			BranchACondition,
			&NotCOut,
			FString(TEXT("sha256:")) + FString::ChrN(64, TEXT('0')));
		bEdgeSetStaleRejected = ExpectPreviewError(
			*this, Params,
			TEXT("graph-rewire-edge-set-stale"),
			TEXT("wrong before edge-set hash is rejected"));
	}
	{
		Package->SetDirtyFlag(true);
		TSharedPtr<FJsonObject> Params = MakePreviewParams(
			TEXT("rewire"), NotAOut, BranchACondition, &NotCOut, EdgeHashBefore);
		bDirtyRejected = ExpectPreviewError(
			*this, Params,
			TEXT("graph-rewire-package-dirty"),
			TEXT("dirty package is rejected"));
		Package->SetDirtyFlag(false);
	}
	{
		TSharedPtr<FJsonObject> Params = MakePreviewParams(
			TEXT("rewire"), NotAOut, BranchACondition, &NotCOut, EdgeHashBefore);
		Params->SetStringField(
			TEXT("assetPath"),
			TEXT("/Game/UEAgentKitBlueprintRewire/BP_Other.BP_Other"));
		bFocusedMismatchRejected = ExpectPreviewError(
			*this, Params,
			TEXT("graph-rewire-focused-graph-mismatch"),
			TEXT("non-focused assetPath is rejected"));
	}

	// Plan staleness: the preview binds the live graph fingerprint, so moving a
	// node after Preview must invalidate the plan without changing one edge.
	{
		TSharedPtr<FJsonObject> Params = MakePreviewParams(
			TEXT("rewire"), NotAOut, BranchACondition, &NotCOut, EdgeHashBefore);
		TSharedPtr<FJsonObject> Preview;
		FString Code;
		FString Message;
		const bool bPreviewed = UEAgentKitBlueprintRewireExperimental::TryPreviewExperimental(
			Params, Preview, Code, Message);
		TestTrue(TEXT("plan-stale preview succeeds"), bPreviewed);
		if (bPreviewed)
		{
			TSharedPtr<FJsonObject> ApplyParams = BuildApplyParams(
				Preview,
				FString(UEAgentKitBlueprintRewireExperimental::ApplyConfirmationPrefix)
					+ Preview->GetStringField(TEXT("planId")));
			Fixture.BranchB->NodePosX += 64;
			bPlanStaleRejected = ExpectApplyError(
				*this, ApplyParams,
				TEXT("graph-rewire-plan-stale"),
				TEXT("plan made stale by a node move is rejected"));
			Fixture.BranchB->NodePosX -= 64;
		}
	}

	// Edge-set staleness at Apply: break an unrelated untouched edge after
	// Preview, then restore it exactly.
	{
		TSharedPtr<FJsonObject> Params = MakePreviewParams(
			TEXT("rewire"), NotAOut, BranchACondition, &NotCOut, EdgeHashBefore);
		TSharedPtr<FJsonObject> Preview;
		FString Code;
		FString Message;
		const bool bPreviewed = UEAgentKitBlueprintRewireExperimental::TryPreviewExperimental(
			Params, Preview, Code, Message);
		TestTrue(TEXT("edge-set-stale preview succeeds"), bPreviewed);
		if (bPreviewed)
		{
			TSharedPtr<FJsonObject> ApplyParams = BuildApplyParams(
				Preview,
				FString(UEAgentKitBlueprintRewireExperimental::ApplyConfirmationPrefix)
					+ Preview->GetStringField(TEXT("planId")));
			NotBOut.Pin->BreakLinkTo(BranchCCondition.Pin);
			Package->SetDirtyFlag(false);
			bEdgeSetStaleRejected = ExpectApplyError(
				*this, ApplyParams,
				TEXT("graph-rewire-edge-set-stale"),
				TEXT("edge-set change after Preview is rejected at Apply"));
			NotBOut.Pin->MakeLinkTo(BranchCCondition.Pin);
			Package->SetDirtyFlag(false);
			if (!bEdgeSetStaleRejected)
			{
				return Fail();
			}
		}
	}

	const TSharedPtr<FJsonObject> RewirePreview = MakePreviewParams(
		TEXT("rewire"), NotAOut, BranchACondition, &NotCOut, EdgeHashBefore);
	TSharedPtr<FJsonObject> Preview;
	FString PreviewCode;
	FString PreviewMessage;
	if (!TestTrue(
			TEXT("rewire preview succeeds on the exact focused graph"),
			UEAgentKitBlueprintRewireExperimental::TryPreviewExperimental(
				RewirePreview, Preview, PreviewCode, PreviewMessage)))
	{
		AddError(PreviewCode + TEXT(": ") + PreviewMessage);
		return Fail();
	}
	const FString RewirePlanId = Preview->GetStringField(TEXT("planId"));
	const TArray<TSharedPtr<FJsonValue>> RemovedEdges =
		Preview->GetArrayField(TEXT("removedEdges"));
	const TArray<TSharedPtr<FJsonValue>> AddedEdges =
		Preview->GetArrayField(TEXT("addedEdges"));
	if (!TestTrue(TEXT("preview is read-only"), Preview->GetBoolField(TEXT("readOnly")))
		|| !TestFalse(TEXT("preview does not write"), Preview->GetBoolField(TEXT("wroteBlueprint")))
		|| !TestFalse(TEXT("preview does not compile"), Preview->GetBoolField(TEXT("compiled")))
		|| !TestEqual(TEXT("preview removes one edge"), RemovedEdges.Num(), 1)
		|| !TestEqual(TEXT("preview adds one edge"), AddedEdges.Num(), 1)
		|| !TestEqual(
			TEXT("preview keeps the edge count"),
			static_cast<int32>(Preview->GetNumberField(TEXT("linkCountAfter"))),
			5)
		|| !TestEqual(
			TEXT("preview counts four untouched edges"),
			static_cast<int32>(Preview->GetNumberField(TEXT("untouchedEdgeCount"))),
			4)
		|| !TestEqual(
			TEXT("preview keeps the node count"),
			static_cast<int32>(Preview->GetNumberField(TEXT("nodeCountAfter"))),
			RootNodeCount)
		|| !TestTrue(
			TEXT("preview edge hashes differ"),
			Preview->GetStringField(TEXT("edgeSetHashBefore"))
				!= Preview->GetStringField(TEXT("edgeSetHashAfter"))))
	{
		return Fail();
	}

	bool bWrongApplyConfirmationRejected = false;
	bool bMalformedPlanIdRejected = false;
	bool bSaveBeforeApplyRejected = false;
	{
		TSharedPtr<FJsonObject> Params = BuildApplyParams(Preview, TEXT("REWIRE"));
		bWrongApplyConfirmationRejected = ExpectApplyError(
			*this, Params,
			TEXT("graph-rewire-confirmation-required"),
			TEXT("wrong Apply confirmation is rejected"));
	}
	{
		TSharedPtr<FJsonObject> Params = BuildApplyParams(Preview, TEXT("REWIRE"));
		Params->SetStringField(TEXT("planId"), TEXT("sha256:short"));
		Params->SetStringField(
			TEXT("confirmation"),
			TEXT("REWIRE EXPERIMENTAL BLUEPRINT PIN sha256:short"));
		bMalformedPlanIdRejected = ExpectApplyError(
			*this, Params,
			TEXT("graph-rewire-invalid-identifier"),
			TEXT("malformed planId is rejected"));
	}
	{
		TSharedPtr<FJsonObject> Params = MakeShared<FJsonObject>();
		Params->SetStringField(TEXT("assetPath"), AssetPath);
		Params->SetStringField(TEXT("graphGuid"), Preview->GetStringField(TEXT("graphGuid")));
		Params->SetStringField(TEXT("planId"), RewirePlanId);
		Params->SetStringField(
			TEXT("expectedEdgeSetHashAfter"),
			Preview->GetStringField(TEXT("edgeSetHashAfter")));
		Params->SetStringField(
			TEXT("expectedPackageRevision"),
			Preview->GetStringField(TEXT("packageRevision")));
		Params->SetNumberField(
			TEXT("expectedNodeCount"),
			Preview->GetNumberField(TEXT("nodeCountAfter")));
		Params->SetNumberField(
			TEXT("expectedLinkCount"),
			Preview->GetNumberField(TEXT("linkCountAfter")));
		Params->SetStringField(
			TEXT("confirmation"),
			FString(UEAgentKitBlueprintRewireExperimental::SaveConfirmationPrefix) + RewirePlanId);
		bSaveBeforeApplyRejected = ExpectSaveError(
			*this, Params,
			TEXT("graph-rewire-save-requires-apply"),
			TEXT("Save before Apply is rejected"));
	}

	TSharedPtr<FJsonObject> RewireApply;
	FString RewireApplyCode;
	FString RewireApplyMessage;
	if (!TestTrue(
			TEXT("rewire Apply succeeds"),
			UEAgentKitBlueprintRewireExperimental::TryApplyExperimental(
				BuildApplyParams(
					Preview,
					FString(UEAgentKitBlueprintRewireExperimental::ApplyConfirmationPrefix)
						+ RewirePlanId),
				RewireApply,
				RewireApplyCode,
				RewireApplyMessage)))
	{
		AddError(RewireApplyCode + TEXT(": ") + RewireApplyMessage);
		return Fail();
	}
	if (!TestTrue(TEXT("rewire Apply reports applied"), RewireApply->GetBoolField(TEXT("applied")))
		|| !TestTrue(TEXT("rewire Apply verified Undo"), RewireApply->GetBoolField(TEXT("undoVerified")))
		|| !TestTrue(TEXT("rewire Apply verified Redo"), RewireApply->GetBoolField(TEXT("redoVerified")))
		|| !TestFalse(TEXT("rewire Apply did not save"), RewireApply->GetBoolField(TEXT("saved")))
		|| !TestFalse(TEXT("rewire Apply did not compile"), RewireApply->GetBoolField(TEXT("compiled")))
		|| !TestEqual(
			TEXT("rewire Apply node count"),
			static_cast<int32>(RewireApply->GetNumberField(TEXT("nodeCount"))),
			RootNodeCount)
		|| !TestEqual(
			TEXT("rewire Apply link count"),
			static_cast<int32>(RewireApply->GetNumberField(TEXT("linkCount"))),
			5))
	{
		return Fail();
	}
	bool bRewireOldGone = false;
	bool bRewireNewPresent = false;
	bool bFanoutKept = false;
	bool bExecKept = false;
	{
		TArray<FString> LiveKeys;
		BuildEdgeKeys(Fixture.Graph, LiveKeys);
		const FString OldKey = EdgeKey(NotAOut.Pin, BranchACondition.Pin);
		const FString NewKey = EdgeKey(NotCOut.Pin, BranchACondition.Pin);
		const FString FanoutKey = EdgeKey(NotBOut.Pin, BranchCCondition.Pin);
		const FString ExecKey = EdgeKey(BranchAThen.Pin, BranchBExecute.Pin);
		bRewireOldGone = !LiveKeys.Contains(OldKey);
		bRewireNewPresent = LiveKeys.Contains(NewKey);
		bFanoutKept = LiveKeys.Contains(FanoutKey);
		bExecKept = LiveKeys.Contains(ExecKey);
		if (!TestTrue(TEXT("live edge set no longer has the old edge"), bRewireOldGone)
			|| !TestTrue(TEXT("live edge set has the new edge"), bRewireNewPresent)
			|| !TestTrue(TEXT("live edge set kept the bool fan-out"), bFanoutKept)
			|| !TestTrue(TEXT("live edge set kept the exec edge"), bExecKept)
			|| !TestEqual(TEXT("live edge count after rewire"), LiveKeys.Num(), 5)
			|| !TestEqual(
				TEXT("target now links exactly the new source"),
				BranchACondition.Pin->LinkedTo.Num(),
				1)
			|| !TestTrue(
				TEXT("target links the new source pin"),
				BranchACondition.Pin->LinkedTo.Contains(NotCOut.Pin))
			|| !TestEqual(
				TEXT("the old source lost only the planned link"),
				NotAOut.Pin->LinkedTo.Num(),
				0))
		{
			return Fail();
		}
	}

	// Save negative cases.
	bool bWrongSaveConfirmationRejected = false;
	bool bWrongSaveHashRejected = false;
	bool bWrongSaveRevisionRejected = false;
	const FString RewireSaveConfirmation =
		FString(UEAgentKitBlueprintRewireExperimental::SaveConfirmationPrefix) + RewirePlanId;
	{
		bWrongSaveConfirmationRejected = ExpectSaveError(
			*this,
			BuildSaveParams(RewireApply, TEXT("SAVE")),
			TEXT("graph-rewire-confirmation-required"),
			TEXT("wrong Save confirmation is rejected"));
	}
	{
		TSharedPtr<FJsonObject> Params = BuildSaveParams(RewireApply, RewireSaveConfirmation);
		Params->SetStringField(
			TEXT("expectedEdgeSetHashAfter"),
			FString(TEXT("sha256:")) + FString::ChrN(64, TEXT('9')));
		bWrongSaveHashRejected = ExpectSaveError(
			*this, Params,
			TEXT("graph-rewire-save-edge-set-mismatch"),
			TEXT("Save with a wrong post-Apply edge hash is rejected"));
	}
	{
		TSharedPtr<FJsonObject> Params = BuildSaveParams(RewireApply, RewireSaveConfirmation);
		Params->SetStringField(
			TEXT("expectedPackageRevision"),
			FString(TEXT("sha256:")) + FString::ChrN(64, TEXT('8')));
		bWrongSaveRevisionRejected = ExpectSaveError(
			*this, Params,
			TEXT("graph-rewire-save-revision-mismatch"),
			TEXT("Save with a wrong package revision is rejected"));
	}

	TSharedPtr<FJsonObject> RewireSave;
	FString RewireSaveCode;
	FString RewireSaveMessage;
	if (!TestTrue(
			TEXT("rewire Save succeeds"),
			UEAgentKitBlueprintRewireExperimental::TrySaveExperimental(
				BuildSaveParams(RewireApply, RewireSaveConfirmation),
				RewireSave,
				RewireSaveCode,
				RewireSaveMessage)))
	{
		AddError(RewireSaveCode + TEXT(": ") + RewireSaveMessage);
		return Fail();
	}
	const FString RewireDiskHash = RewireSave->GetStringField(TEXT("assetSha256"));
	if (!TestTrue(TEXT("rewire Save reports saved"), RewireSave->GetBoolField(TEXT("saved")))
		|| !TestTrue(
			TEXT("rewire Save verified readback"),
			RewireSave->GetBoolField(TEXT("readbackVerified")))
		|| !TestFalse(
			TEXT("rewire Save leaves the package clean"),
			RewireSave->GetBoolField(TEXT("packageDirty")))
		|| !TestFalse(
			TEXT("rewire Save did not compile"),
			RewireSave->GetBoolField(TEXT("compiled")))
		|| !TestFalse(
			TEXT("rewire Save awaits the independent reload"),
			RewireSave->GetBoolField(TEXT("independentReloadVerified"))))
	{
		return Fail();
	}
	{
		FString DiskDigest;
		const FString Filename = FPackageName::LongPackageNameToFilename(
			Package->GetName(),
			FPackageName::GetAssetPackageExtension());
		if (!TestTrue(
				TEXT("saved rewire package can be hashed"),
				FBlueprintContextSha256::HashFile(Filename, DiskDigest))
			|| !TestEqual(
				TEXT("rewire Save asset hash equals disk"),
				RewireDiskHash,
				FString(TEXT("sha256:")) + DiskDigest.ToLower()))
		{
			return Fail();
		}
	}

	// Explicit disconnect of the second known edge.
	FString EdgeHashAfterRewire;
	if (!TestTrue(
			TEXT("post-rewire edge hash computes"),
			ComputeEdgeHash(Fixture.Graph, EdgeHashAfterRewire))
		|| !TestEqual(
			TEXT("post-rewire edge hash equals the Apply result"),
			EdgeHashAfterRewire,
			RewireApply->GetStringField(TEXT("edgeSetHashAfter"))))
	{
		return Fail();
	}

	TSharedPtr<FJsonObject> DisconnectPreview;
	FString DisconnectPreviewCode;
	FString DisconnectPreviewMessage;
	if (!TestTrue(
			TEXT("disconnect preview succeeds"),
			UEAgentKitBlueprintRewireExperimental::TryPreviewExperimental(
				MakePreviewParams(
					TEXT("disconnect"),
					NotBOut,
					BranchBCondition,
					nullptr,
					EdgeHashAfterRewire),
				DisconnectPreview,
				DisconnectPreviewCode,
				DisconnectPreviewMessage)))
	{
		AddError(DisconnectPreviewCode + TEXT(": ") + DisconnectPreviewMessage);
		return Fail();
	}
	const FString DisconnectPlanId = DisconnectPreview->GetStringField(TEXT("planId"));
	if (!TestEqual(
			TEXT("disconnect preview removes one edge"),
			static_cast<int32>(DisconnectPreview->GetArrayField(TEXT("removedEdges")).Num()),
			1)
		|| !TestEqual(
			TEXT("disconnect preview adds no edge"),
			static_cast<int32>(DisconnectPreview->GetArrayField(TEXT("addedEdges")).Num()),
			0)
		|| !TestEqual(
			TEXT("disconnect preview drops one link"),
			static_cast<int32>(DisconnectPreview->GetNumberField(TEXT("linkCountAfter"))),
			4)
		|| !TestEqual(
			TEXT("disconnect preview reports four untouched edges"),
			static_cast<int32>(DisconnectPreview->GetNumberField(TEXT("untouchedEdgeCount"))),
			4))
	{
		return Fail();
	}

	TSharedPtr<FJsonObject> DisconnectApply;
	FString DisconnectApplyCode;
	FString DisconnectApplyMessage;
	if (!TestTrue(
			TEXT("disconnect Apply succeeds"),
			UEAgentKitBlueprintRewireExperimental::TryApplyExperimental(
				BuildApplyParams(
					DisconnectPreview,
					FString(UEAgentKitBlueprintRewireExperimental::ApplyConfirmationPrefix)
						+ DisconnectPlanId),
				DisconnectApply,
				DisconnectApplyCode,
				DisconnectApplyMessage)))
	{
		AddError(DisconnectApplyCode + TEXT(": ") + DisconnectApplyMessage);
		return Fail();
	}
	{
		TArray<FString> LiveKeys;
		BuildEdgeKeys(Fixture.Graph, LiveKeys);
		const FString DisconnectedKey = EdgeKey(NotBOut.Pin, BranchBCondition.Pin);
		const FString FanoutKey = EdgeKey(NotBOut.Pin, BranchCCondition.Pin);
		if (!TestFalse(
				TEXT("disconnected edge is gone"),
				LiveKeys.Contains(DisconnectedKey))
			|| !TestTrue(TEXT("remaining fan-out edge survives"), LiveKeys.Contains(FanoutKey))
			|| !TestEqual(TEXT("final edge count is four"), LiveKeys.Num(), 4)
			|| !TestTrue(
				TEXT("disconnect Apply verified Undo"),
				DisconnectApply->GetBoolField(TEXT("undoVerified")))
			|| !TestTrue(
				TEXT("disconnect Apply verified Redo"),
				DisconnectApply->GetBoolField(TEXT("redoVerified"))))
		{
			return Fail();
		}
	}

	TSharedPtr<FJsonObject> DisconnectSave;
	FString DisconnectSaveCode;
	FString DisconnectSaveMessage;
	if (!TestTrue(
			TEXT("disconnect Save succeeds"),
			UEAgentKitBlueprintRewireExperimental::TrySaveExperimental(
				BuildSaveParams(
					DisconnectApply,
					FString(UEAgentKitBlueprintRewireExperimental::SaveConfirmationPrefix)
						+ DisconnectPlanId),
				DisconnectSave,
				DisconnectSaveCode,
				DisconnectSaveMessage)))
	{
		AddError(DisconnectSaveCode + TEXT(": ") + DisconnectSaveMessage);
		return Fail();
	}
	const FString FinalDiskHash = DisconnectSave->GetStringField(TEXT("assetSha256"));
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

	TArray<TSharedPtr<FJsonValue>> RemovedForReload;
	RemovedForReload.Add(MakeShared<FJsonValueObject>(
		EdgeJson(NotAOut, BranchACondition, TEXT("bool"))));
	RemovedForReload.Add(MakeShared<FJsonValueObject>(
		EdgeJson(NotBOut, BranchBCondition, TEXT("bool"))));
	TArray<TSharedPtr<FJsonValue>> AddedForReload;
	AddedForReload.Add(MakeShared<FJsonValueObject>(
		EdgeJson(NotCOut, BranchACondition, TEXT("bool"))));
	TArray<TSharedPtr<FJsonValue>> UntouchedForReload;
	UntouchedForReload.Add(MakeShared<FJsonValueObject>(
		EdgeJson(NotBOut, BranchCCondition, TEXT("bool"))));
	UntouchedForReload.Add(MakeShared<FJsonValueObject>(
		EdgeJson(BranchAThen, BranchBExecute, TEXT("exec"))));
	UntouchedForReload.Add(MakeShared<FJsonValueObject>(
		EdgeJson(BranchDThen, BranchEExecute, TEXT("exec"))));

	TSharedRef<FJsonObject> Checks = MakeShared<FJsonObject>();
	Checks->SetBoolField(TEXT("defaultGateOffRejected"), bGateOffRejected);
	Checks->SetBoolField(TEXT("unknownParameterRejected"), bUnknownParameterRejected);
	Checks->SetBoolField(TEXT("invalidOperationRejected"), bInvalidOperationRejected);
	Checks->SetBoolField(TEXT("nodeNotFoundRejected"), bNodeNotFoundRejected);
	Checks->SetBoolField(TEXT("pinNotFoundRejected"), bPinNotFoundRejected);
	Checks->SetBoolField(TEXT("pinNameMismatchRejected"), bPinNameMismatchRejected);
	Checks->SetBoolField(TEXT("directionMismatchRejected"), bDirectionMismatchRejected);
	Checks->SetBoolField(TEXT("oldEdgeMismatchRejected"), bOldEdgeMismatchRejected);
	Checks->SetBoolField(TEXT("noOpRejected"), bNoOpRejected);
	Checks->SetBoolField(TEXT("pinKindMismatchRejected"), bKindMismatchRejected);
	Checks->SetBoolField(TEXT("wildcardPinRejected"), bTypeNotAllowedRejected);
	Checks->SetBoolField(TEXT("schemaBreakOthersRejected"), bBreakOthersRejected);
	Checks->SetBoolField(TEXT("targetUnlinkedRejected"), bTargetUnlinkedRejected);
	Checks->SetBoolField(TEXT("edgeSetStaleRejected"), bEdgeSetStaleRejected);
	Checks->SetBoolField(TEXT("dirtyPackageRejected"), bDirtyRejected);
	Checks->SetBoolField(TEXT("nonFocusedAssetRejected"), bFocusedMismatchRejected);
	Checks->SetBoolField(TEXT("planStaleRejected"), bPlanStaleRejected);
	Checks->SetBoolField(TEXT("wrongApplyConfirmationRejected"), bWrongApplyConfirmationRejected);
	Checks->SetBoolField(TEXT("malformedPlanIdRejected"), bMalformedPlanIdRejected);
	Checks->SetBoolField(TEXT("saveBeforeApplyRejected"), bSaveBeforeApplyRejected);
	Checks->SetBoolField(TEXT("wrongSaveConfirmationRejected"), bWrongSaveConfirmationRejected);
	Checks->SetBoolField(TEXT("wrongSaveEdgeHashRejected"), bWrongSaveHashRejected);
	Checks->SetBoolField(TEXT("wrongSaveRevisionRejected"), bWrongSaveRevisionRejected);
	Checks->SetBoolField(TEXT("previewReadOnly"), Preview->GetBoolField(TEXT("readOnly")));
	Checks->SetBoolField(
		TEXT("applyUndoRedoVerified"),
		RewireApply->GetBoolField(TEXT("undoVerified"))
			&& RewireApply->GetBoolField(TEXT("redoVerified"))
			&& DisconnectApply->GetBoolField(TEXT("undoVerified"))
			&& DisconnectApply->GetBoolField(TEXT("redoVerified")));
	Checks->SetBoolField(
		TEXT("rewireOldEdgeGone"),
		bRewireOldGone);
	Checks->SetBoolField(
		TEXT("rewireNewEdgePresent"),
		bRewireNewPresent);
	Checks->SetBoolField(TEXT("boolFanoutPreserved"), bFanoutKept);
	Checks->SetBoolField(TEXT("execEdgePreserved"), bExecKept);
	Checks->SetBoolField(
		TEXT("untouchedEdgesPreserved"),
		BranchCCondition.Pin->LinkedTo.Num() == 1
			&& BranchCCondition.Pin->LinkedTo[0] == NotBOut.Pin);
	Checks->SetBoolField(
		TEXT("disconnectRemovedOnlyItsEdge"),
		BranchBCondition.Pin->LinkedTo.Num() == 0);
	Checks->SetBoolField(TEXT("packageCleanAfterSave"), !DisconnectSave->GetBoolField(TEXT("packageDirty")));
	Checks->SetBoolField(
		TEXT("independentReloadPendingCommandlet"),
		!DisconnectSave->GetBoolField(TEXT("independentReloadVerified")));

	TSharedRef<FJsonObject> Evidence = MakeShared<FJsonObject>();
	Evidence->SetStringField(TEXT("schemaVersion"), TEXT("ueak.blueprint-rewire-evidence/1.0"));
	Evidence->SetNumberField(
		TEXT("processId"),
		static_cast<double>(FPlatformProcess::GetCurrentProcessId()));
	Evidence->SetStringField(TEXT("writtenAtUtc"), FDateTime::UtcNow().ToIso8601());
	Evidence->SetStringField(TEXT("assetPath"), Blueprint->GetPathName());
	Evidence->SetStringField(TEXT("assetFilename"), FPaths::ConvertRelativePathToFull(AssetFilename));
	Evidence->SetStringField(TEXT("graphGuid"), GuidLower(Fixture.Graph->GraphGuid));
	Evidence->SetNumberField(TEXT("expectedNodeCount"), RootNodeCount);
	Evidence->SetNumberField(TEXT("expectedEdgeCount"), 4);
	Evidence->SetNumberField(TEXT("initialEdgeCount"), RootLinkCount);
	Evidence->SetStringField(TEXT("assetSha256"), FinalDiskHash);
	Evidence->SetStringField(TEXT("rewireAssetSha256"), RewireDiskHash);
	Evidence->SetStringField(TEXT("rewirePlanId"), RewirePlanId);
	Evidence->SetStringField(TEXT("disconnectPlanId"), DisconnectPlanId);
	Evidence->SetStringField(
		TEXT("edgeSetHashBefore"),
		Preview->GetStringField(TEXT("edgeSetHashBefore")));
	Evidence->SetStringField(
		TEXT("edgeSetHashAfterRewire"),
		RewireApply->GetStringField(TEXT("edgeSetHashAfter")));
	Evidence->SetStringField(
		TEXT("edgeSetHashAfterDisconnect"),
		DisconnectApply->GetStringField(TEXT("edgeSetHashAfter")));
	Evidence->SetObjectField(TEXT("rewirePreview"), Preview);
	Evidence->SetObjectField(TEXT("rewireApply"), RewireApply);
	Evidence->SetObjectField(TEXT("rewireSave"), RewireSave);
	Evidence->SetObjectField(TEXT("disconnectPreview"), DisconnectPreview);
	Evidence->SetObjectField(TEXT("disconnectApply"), DisconnectApply);
	Evidence->SetObjectField(TEXT("disconnectSave"), DisconnectSave);
	Evidence->SetArrayField(TEXT("expectedEdges"), FinalEdges);
	Evidence->SetArrayField(TEXT("removedEdges"), RemovedForReload);
	Evidence->SetArrayField(TEXT("addedEdges"), AddedForReload);
	Evidence->SetArrayField(TEXT("untouchedEdges"), UntouchedForReload);
	Evidence->SetArrayField(TEXT("pins"), BuildPinInventory(Fixture.Graph));
	Evidence->SetObjectField(TEXT("checks"), Checks);

	const FString EvidencePath = FPaths::Combine(
		FPaths::ProjectSavedDir(),
		EvidenceSubdirectory,
		TEXT("evidence.json"));
	if (!TestTrue(
			TEXT("rewire evidence is written"),
			WriteJson(EvidencePath, Evidence)))
	{
		return Fail();
	}

	UEAgentKitBlueprintRewireExperimental::ResetExperimentalWriteForTest();
	return true;
}

namespace UEAgentKitBlueprintRewirePieGate
{
	const TCHAR* const EvidenceSubdirectory = TEXT("BlueprintRewire");

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
			UEAgentKitBlueprintRewireExpansion::AssetPath);
		if (Blueprint == nullptr || Blueprint->UbergraphPages.IsEmpty())
		{
			OutError = TEXT("The saved rewire fixture is missing; run UEAgentKit.Rewire.Expansion first.");
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
			UEAgentKitBlueprintRewireExpansion::AssetPath);
		Params->SetStringField(
			TEXT("graphGuid"),
			TEXT("00000000-0000-0000-0000-000000000000"));
		Params->SetStringField(TEXT("operation"), TEXT("disconnect"));
		Params->SetStringField(
			TEXT("targetNodeGuid"),
			TEXT("00000000-0000-0000-0000-000000000000"));
		Params->SetStringField(TEXT("targetPinName"), TEXT("Condition"));
		Params->SetStringField(
			TEXT("targetPinGuid"),
			TEXT("00000000-0000-0000-0000-000000000000"));
		Params->SetStringField(
			TEXT("oldSourceNodeGuid"),
			TEXT("00000000-0000-0000-0000-000000000000"));
		Params->SetStringField(TEXT("oldSourcePinName"), TEXT("ReturnValue"));
		Params->SetStringField(
			TEXT("oldSourcePinGuid"),
			TEXT("00000000-0000-0000-0000-000000000000"));
		Params->SetStringField(
			TEXT("expectedEdgeSetHash"),
			FString(TEXT("sha256:")) + FString::ChrN(64, TEXT('a')));
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
				UEAgentKitBlueprintRewireExpansion::BuildEdgeKeys(State->Graph, BeforeKeys);

				TSharedPtr<FJsonObject> PreviewResult;
				FString PreviewCode;
				FString PreviewMessage;
				const bool bPreviewRejected =
					!UEAgentKitBlueprintRewireExperimental::TryPreviewExperimental(
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
					FString(UEAgentKitBlueprintRewireExperimental::ApplyConfirmationPrefix)
						+ FabricatedPlanId);
				TSharedPtr<FJsonObject> ApplyResult;
				FString ApplyCode;
				FString ApplyMessage;
				const bool bApplyRejected =
					!UEAgentKitBlueprintRewireExperimental::TryApplyExperimental(
						ApplyParams,
						ApplyResult,
						ApplyCode,
						ApplyMessage);

				TArray<FString> AfterKeys;
				UEAgentKitBlueprintRewireExpansion::BuildEdgeKeys(State->Graph, AfterKeys);
				const bool bEdgesUnchanged = BeforeKeys == AfterKeys;
				const bool bOk = bPreviewRejected
					&& PreviewCode == TEXT("graph-rewire-pie-active")
					&& bApplyRejected
					&& ApplyCode == TEXT("graph-rewire-pie-active")
					&& bEdgesUnchanged;

				TSharedRef<FJsonObject> Evidence = MakeShared<FJsonObject>();
				Evidence->SetStringField(
					TEXT("schemaVersion"),
					TEXT("ueak.blueprint-rewire-pie-gate/1.0"));
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
				Evidence->SetBoolField(TEXT("success"), bOk);
				const FString Path = FPaths::Combine(
					FPaths::ProjectSavedDir(),
					EvidenceSubdirectory,
					TEXT("pie-gate.json"));
				if (!WriteJson(Path, Evidence))
				{
					Test->AddError(TEXT("Could not write the rewire PIE gate evidence."));
				}
				Test->TestTrue(
					TEXT("PIE rejects rewire preview and apply with pie-active"),
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
				UEAgentKitBlueprintRewireExperimental::ResetExperimentalWriteForTest();
				State->bCompleted = true;
				return true;
			}
			if (FPlatformTime::Seconds() - State->StartedAt > 60.0)
			{
				TSharedRef<FJsonObject> Evidence = MakeShared<FJsonObject>();
				Evidence->SetStringField(
					TEXT("schemaVersion"),
					TEXT("ueak.blueprint-rewire-pie-gate/1.0"));
				Evidence->SetNumberField(
					TEXT("processId"),
					static_cast<double>(FPlatformProcess::GetCurrentProcessId()));
				Evidence->SetStringField(TEXT("writtenAtUtc"), FDateTime::UtcNow().ToIso8601());
				Evidence->SetBoolField(TEXT("pieStarted"), false);
				Evidence->SetBoolField(TEXT("success"), false);
				Evidence->SetStringField(
					TEXT("note"),
					TEXT("PIE did not start within 60 seconds; the rewire PIE gate was not exercised."));
				WriteJson(
					FPaths::Combine(
						FPaths::ProjectSavedDir(),
						EvidenceSubdirectory,
						TEXT("pie-gate.json")),
					Evidence);
				Test->AddError(
					TEXT("PIE did not start within 60 seconds; rewire PIE rejection was not run."));
				UEAgentKitBlueprintRewireExperimental::ResetExperimentalWriteForTest();
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
	FUEAgentKitBlueprintRewirePieGateTest,
	"UEAgentKit.Rewire.PieGate",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitBlueprintRewirePieGateTest::RunTest(const FString& Parameters)
{
	using namespace UEAgentKitBlueprintRewirePieGate;
	FString Error;
	TSharedRef<FState> State = MakeShared<FState>();
	State->Test = this;
	if (!ResolveFixture(*State, Error))
	{
		AddError(Error);
		return false;
	}
	UEAgentKitBlueprintRewireExperimental::SetExperimentalWriteEnabledForTest(true);
	ADD_LATENT_AUTOMATION_COMMAND(FStartPIECommand(false));
	ADD_LATENT_AUTOMATION_COMMAND(FWaitForPieThenReject(State));
	ADD_LATENT_AUTOMATION_COMMAND(FEndPlayMapCommand());
	ADD_LATENT_AUTOMATION_COMMAND(FWaitForPieStop(State));
	return true;
}

#endif // WITH_DEV_AUTOMATION_TESTS
