// Disposable Blueprint fixtures for the node-coverage slice.
//
// The fixture intentionally writes an editor-side graph inventory before any
// canonical export happens.  That inventory is the ground truth used to
// reconcile the exporter, the SQLite ingestion, and the query layer.

#include "CoreMinimal.h"
#include "Animation/AnimBlueprint.h"
#include "Animation/AnimBlueprintGeneratedClass.h"
#include "Animation/AnimInstance.h"
#include "Animation/Skeleton.h"
#include "BlueprintContextExporter.h"
#include "Dom/JsonObject.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraphSchema_K2.h"
#include "Engine/Blueprint.h"
#include "Engine/BlueprintGeneratedClass.h"
#include "HAL/FileManager.h"
#include "K2Node_CallFunction.h"
#include "K2Node_Composite.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Kismet2/KismetEditorUtilities.h"
#include "Kismet/KismetSystemLibrary.h"
#include "Misc/AutomationTest.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"

#if WITH_DEV_AUTOMATION_TESTS

namespace UEAgentKitNodeCoverageFixtures
{
	constexpr int32 EventNodeTarget = 130;

	UFunction* FindFunction(UClass* Class, const TCHAR* Name)
	{
		return Class != nullptr ? Class->FindFunctionByName(FName(Name)) : nullptr;
	}

	UK2Node_CallFunction* MakeCall(UEdGraph* Graph, UFunction* Function, int32 X, int32 Y)
	{
		if (Graph == nullptr || Function == nullptr)
		{
			return nullptr;
		}

		FGraphNodeCreator<UK2Node_CallFunction> Creator(*Graph);
		UK2Node_CallFunction* Node = Creator.CreateNode(false);
		Node->SetFromFunction(Function);
		Node->NodePosX = X;
		Node->NodePosY = Y;
		Creator.Finalize();
		return Node;
	}

	bool SaveBlueprint(UBlueprint* Blueprint, FString& OutError)
	{
		if (Blueprint == nullptr)
		{
			OutError = TEXT("Node-coverage Blueprint is null.");
			return false;
		}

		UPackage* Package = Blueprint->GetOutermost();
		Package->MarkPackageDirty();
		const FString Filename = FPackageName::LongPackageNameToFilename(
			Package->GetName(), FPackageName::GetAssetPackageExtension());
		FSavePackageArgs SaveArgs;
		SaveArgs.TopLevelFlags = RF_Public | RF_Standalone;
		SaveArgs.SaveFlags = SAVE_NoError;
		if (!UPackage::SavePackage(Package, Blueprint, *Filename, SaveArgs))
		{
			OutError = FString::Printf(TEXT("Could not save %s."), *Package->GetName());
			return false;
		}
		return true;
	}

	UBlueprint* CreateNodeBlueprint(FString& OutError)
	{
		const TCHAR* PackageName = TEXT("/Game/NodeCoverage/BP_NodeCoverage");
		if (UBlueprint* Existing = LoadObject<UBlueprint>(nullptr, TEXT("/Game/NodeCoverage/BP_NodeCoverage.BP_NodeCoverage")))
		{
			return Existing;
		}

		UPackage* Package = CreatePackage(PackageName);
		if (Package == nullptr)
		{
			OutError = TEXT("Could not create the node-coverage package.");
			return nullptr;
		}

		UBlueprint* Blueprint = FKismetEditorUtilities::CreateBlueprint(
			AActor::StaticClass(),
			Package,
			FName(TEXT("BP_NodeCoverage")),
			BPTYPE_Normal,
			FName(TEXT("UEAgentKitNodeCoverageFixture")));
		if (Blueprint == nullptr || Blueprint->UbergraphPages.Num() == 0)
		{
			OutError = TEXT("Could not create the node-coverage Blueprint event graph.");
			return nullptr;
		}

		UEdGraph* EventGraph = Blueprint->UbergraphPages[0];
		UFunction* PrintString = FindFunction(UKismetSystemLibrary::StaticClass(), TEXT("PrintString"));
		for (int32 Index = 0; Index < EventNodeTarget; ++Index)
		{
			if (MakeCall(EventGraph, PrintString, 200 + (Index % 10) * 260, 60 + (Index / 10) * 120) == nullptr)
			{
				OutError = FString::Printf(TEXT("Could not create event node %d."), Index);
				return nullptr;
			}
		}

		UEdGraph* FunctionGraph = FBlueprintEditorUtils::CreateNewGraph(
			Blueprint,
			FName(TEXT("CoverageFunction")),
			UEdGraph::StaticClass(),
			UEdGraphSchema_K2::StaticClass());
		if (FunctionGraph == nullptr)
		{
			OutError = TEXT("Could not create the node-coverage function graph.");
			return nullptr;
		}
		FBlueprintEditorUtils::AddFunctionGraph<UClass>(Blueprint, FunctionGraph, true, nullptr);
		if (MakeCall(FunctionGraph, PrintString, 420, 120) == nullptr)
		{
			OutError = TEXT("Could not create the function graph node.");
			return nullptr;
		}

		FGraphNodeCreator<UK2Node_Composite> CompositeCreator(*EventGraph);
		UK2Node_Composite* Composite = CompositeCreator.CreateNode(false);
		Composite->NodePosX = 120;
		Composite->NodePosY = 1800;
		CompositeCreator.Finalize();
		if (Composite->BoundGraph == nullptr)
		{
			OutError = TEXT("Collapsed node did not create a bound graph.");
			return nullptr;
		}
		if (MakeCall(Composite->BoundGraph, PrintString, 360, 120) == nullptr)
		{
			OutError = TEXT("Could not create the collapsed graph node.");
			return nullptr;
		}

		FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
		FKismetEditorUtilities::CompileBlueprint(Blueprint, EBlueprintCompileOptions::SkipGarbageCollection);
		if (!SaveBlueprint(Blueprint, OutError))
		{
			return nullptr;
		}
		return Blueprint;
	}

	UBlueprint* CreateAnimBlueprint(FString& OutError)
	{
		const TCHAR* PackageName = TEXT("/Game/NodeCoverage/BP_NodeCoverage_Anim");
		if (UBlueprint* Existing = LoadObject<UBlueprint>(nullptr, TEXT("/Game/NodeCoverage/BP_NodeCoverage_Anim.BP_NodeCoverage_Anim")))
		{
			return Existing;
		}

		UPackage* Package = CreatePackage(PackageName);
		if (Package == nullptr)
		{
			OutError = TEXT("Could not create the anim-coverage package.");
			return nullptr;
		}

		UBlueprint* Blueprint = FKismetEditorUtilities::CreateBlueprint(
			UAnimInstance::StaticClass(),
			Package,
			FName(TEXT("BP_NodeCoverage_Anim")),
			BPTYPE_Normal,
			UAnimBlueprint::StaticClass(),
			UAnimBlueprintGeneratedClass::StaticClass(),
			FName(TEXT("UEAgentKitNodeCoverageFixture")));
		if (Blueprint == nullptr)
		{
			OutError = TEXT("Could not create the anim-coverage Blueprint.");
			return nullptr;
		}

		if (UAnimBlueprint* AnimBlueprint = Cast<UAnimBlueprint>(Blueprint))
		{
			AnimBlueprint->TargetSkeleton = LoadObject<USkeleton>(
				nullptr, TEXT("/Engine/EngineMeshes/SkeletalCube_Skeleton.SkeletalCube_Skeleton"));
		}

		UEdGraph* AnimGraph = nullptr;
		for (UEdGraph* Graph : Blueprint->FunctionGraphs)
		{
			if (Graph != nullptr && Graph->GetName() == TEXT("AnimGraph"))
			{
				AnimGraph = Graph;
				break;
			}
		}
		if (AnimGraph == nullptr)
		{
			OutError = TEXT("Anim-coverage Blueprint has no AnimGraph.");
			return nullptr;
		}

		UClass* StateMachineClass = FindObject<UClass>(nullptr, TEXT("/Script/AnimGraph.AnimGraphNode_StateMachine"));
		if (StateMachineClass == nullptr)
		{
			OutError = TEXT("AnimGraphNode_StateMachine is unavailable.");
			return nullptr;
		}

		FGraphNodeCreator<UEdGraphNode> StateMachineCreator(*AnimGraph);
		UEdGraphNode* StateMachineNode = StateMachineCreator.CreateNode(false, StateMachineClass);
		StateMachineNode->NodePosX = 360;
		StateMachineNode->NodePosY = 120;
		StateMachineCreator.Finalize();
		if (AnimGraph->SubGraphs.Num() == 0)
		{
			OutError = TEXT("Anim state machine did not create a child graph.");
			return nullptr;
		}

		// A state machine's state graph is another UEdGraph::SubGraphs level.
		// Creating it here proves the discovery walks nested subgraphs rather
		// than only one level below the four historical top-level arrays.
		UEdGraph* StateMachineGraph = AnimGraph->SubGraphs[0];
		UClass* StateClass = FindObject<UClass>(nullptr, TEXT("/Script/AnimGraph.AnimStateNode"));
		if (StateClass == nullptr)
		{
			OutError = TEXT("AnimStateNode is unavailable.");
			return nullptr;
		}
		FGraphNodeCreator<UEdGraphNode> StateCreator(*StateMachineGraph);
		UEdGraphNode* StateNode = StateCreator.CreateNode(false, StateClass);
		StateNode->NodePosX = 260;
		StateNode->NodePosY = 120;
		StateCreator.Finalize();
		if (StateMachineGraph->SubGraphs.Num() == 0)
		{
			OutError = TEXT("Anim state did not create a state graph.");
			return nullptr;
		}

		FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
		FKismetEditorUtilities::CompileBlueprint(Blueprint, EBlueprintCompileOptions::SkipGarbageCollection);
		if (!SaveBlueprint(Blueprint, OutError))
		{
			return nullptr;
		}
		return Blueprint;
	}

	FString GraphKind(const UBlueprint* Blueprint, const UEdGraph* Graph)
	{
		if (Blueprint == nullptr || Graph == nullptr)
		{
			return FString();
		}
		for (const UEdGraph* Top : Blueprint->UbergraphPages)
		{
			if (Top == Graph)
			{
				return TEXT("uber");
			}
		}
		for (const UEdGraph* Top : Blueprint->FunctionGraphs)
		{
			if (Top == Graph)
			{
				return TEXT("function");
			}
		}
		for (const UEdGraph* Top : Blueprint->MacroGraphs)
		{
			if (Top == Graph)
			{
				return TEXT("macro");
			}
		}
		for (const UEdGraph* Top : Blueprint->DelegateSignatureGraphs)
		{
			if (Top == Graph)
			{
				return TEXT("delegate-signature");
			}
		}
		return TEXT("subgraph");
	}

	bool WriteJson(const FString& Path, const TSharedPtr<FJsonObject>& Object, FString& OutError)
	{
		FString Text;
		const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Text);
		if (!FJsonSerializer::Serialize(Object.ToSharedRef(), Writer)
			|| !FFileHelper::SaveStringToFile(Text, *Path, FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
		{
			OutError = FString::Printf(TEXT("Could not write %s."), *Path);
			return false;
		}
		return true;
	}

	void AddBlueprintInventory(UBlueprint* Blueprint, TArray<TSharedPtr<FJsonValue>>& OutAssets)
	{
		TArray<TSharedPtr<FJsonValue>> Graphs;
		TArray<UEdGraph*> AllGraphs;
		Blueprint->GetAllGraphs(AllGraphs);
		for (UEdGraph* Graph : AllGraphs)
		{
			if (Graph == nullptr)
			{
				continue;
			}
			TSharedPtr<FJsonObject> Entry = MakeShared<FJsonObject>();
			Entry->SetStringField(TEXT("name"), Graph->GetName());
			Entry->SetStringField(TEXT("guid"), Graph->GraphGuid.ToString(EGuidFormats::DigitsWithHyphensLower));
			Entry->SetStringField(TEXT("kind"), GraphKind(Blueprint, Graph));
			Entry->SetStringField(TEXT("path"), Graph->GetPathName());
			Entry->SetNumberField(TEXT("nodes"), Graph->Nodes.Num());
			Entry->SetBoolField(TEXT("isSubGraph"), Graph->GetOuter() != Blueprint);
			Entry->SetStringField(TEXT("outer"), Graph->GetOuter() ? Graph->GetOuter()->GetPathName() : FString());
			Graphs.Add(MakeShared<FJsonValueObject>(Entry));
		}

		TSharedPtr<FJsonObject> Asset = MakeShared<FJsonObject>();
		Asset->SetStringField(TEXT("assetPath"), Blueprint->GetPathName());
		Asset->SetStringField(TEXT("class"), Blueprint->GetClass()->GetPathName());
		Asset->SetNumberField(TEXT("graphCount"), AllGraphs.Num());
		int32 NodeCount = 0;
		for (UEdGraph* Graph : AllGraphs)
		{
			NodeCount += Graph != nullptr ? Graph->Nodes.Num() : 0;
		}
		Asset->SetNumberField(TEXT("nodeCount"), NodeCount);
		Asset->SetArrayField(TEXT("graphs"), Graphs);
		OutAssets.Add(MakeShared<FJsonValueObject>(Asset));
	}
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitNodeCoverageFixtureTest,
	"UEAgentKit.Native.NodeCoverageFixture",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitNodeCoverageFixtureTest::RunTest(const FString& Parameters)
{
	(void)Parameters;
	FString Error;
	UBlueprint* NodeBlueprint = UEAgentKitNodeCoverageFixtures::CreateNodeBlueprint(Error);
	UBlueprint* AnimBlueprint = UEAgentKitNodeCoverageFixtures::CreateAnimBlueprint(Error);
	if (!TestNotNull(TEXT("node coverage Blueprint"), NodeBlueprint)
		|| !TestNotNull(TEXT("anim coverage Blueprint"), AnimBlueprint))
	{
		AddError(Error);
		return false;
	}

	UEdGraph* CollapsedGraph = NodeBlueprint->UbergraphPages.Num() > 0
		&& NodeBlueprint->UbergraphPages[0]->SubGraphs.Num() > 0
		? NodeBlueprint->UbergraphPages[0]->SubGraphs[0]
		: nullptr;
	if (!TestNotNull(TEXT("collapsed graph fixture"), CollapsedGraph))
	{
		return false;
	}

	FBlueprintContextExportOptions FilterOptions;
	FilterOptions.Profile = EBlueprintContextProfile::Logic;
	FilterOptions.GraphFilter = CollapsedGraph->GetName();
	FBlueprintContextExportResult FilterResult;
	const TSharedRef<FJsonObject> Filtered = FBlueprintContextExporter::BuildContextObject(
		NodeBlueprint, FilterOptions, FilterResult);
	const TArray<TSharedPtr<FJsonValue>>* FilteredGraphs = nullptr;
	if (!TestTrue(TEXT("filtered context has graphs"), Filtered->TryGetArrayField(TEXT("graphs"), FilteredGraphs))
		|| !TestNotNull(TEXT("filtered graph array"), FilteredGraphs))
	{
		return false;
	}
	TestEqual(TEXT("graph filter returns exactly the subgraph"), FilteredGraphs->Num(), 1);
	TestEqual(TEXT("graph filter exports subgraph nodes"), FilterResult.NodeCount, 3);
	if (FilteredGraphs->Num() == 1)
	{
		const TSharedPtr<FJsonObject> GraphObject = (*FilteredGraphs)[0]->AsObject();
		TestEqual(TEXT("subgraph kind"), GraphObject->GetStringField(TEXT("kind")), FString(TEXT("subgraph")));
		TestEqual(TEXT("subgraph parent"), GraphObject->GetStringField(TEXT("parentGraphName")), FString(TEXT("EventGraph")));
	}

	auto CheckFullCoverage = [this](UBlueprint* Blueprint, const TCHAR* Label, int32 ExpectedGraphs, int32 ExpectedNodes)
	{
		FBlueprintContextExportOptions FullOptions;
		FullOptions.Profile = EBlueprintContextProfile::Logic;
		FBlueprintContextExportResult FullResult;
		const TSharedRef<FJsonObject> Full = FBlueprintContextExporter::BuildContextObject(
			Blueprint, FullOptions, FullResult);
		const TArray<TSharedPtr<FJsonValue>>* FullGraphs = nullptr;
		TestTrue(
			*FString::Printf(TEXT("%s full context has graphs"), Label),
			Full->TryGetArrayField(TEXT("graphs"), FullGraphs) && FullGraphs != nullptr);
		if (FullGraphs == nullptr)
		{
			return;
		}
		TestEqual(*FString::Printf(TEXT("%s graph count"), Label), FullGraphs->Num(), ExpectedGraphs);
		TestEqual(*FString::Printf(TEXT("%s node count"), Label), FullResult.NodeCount, ExpectedNodes);

		bool bFoundSubgraph = false;
		for (const TSharedPtr<FJsonValue>& GraphValue : *FullGraphs)
		{
			const TSharedPtr<FJsonObject> GraphObject = GraphValue->AsObject();
			if (GraphObject.IsValid() && GraphObject->GetStringField(TEXT("kind")) == TEXT("subgraph"))
			{
				bFoundSubgraph = true;
				TestFalse(
					*FString::Printf(TEXT("%s subgraph has parent"), Label),
					GraphObject->GetStringField(TEXT("parentGraphName")).IsEmpty());
			}
		}
		TestTrue(*FString::Printf(TEXT("%s contains a subgraph"), Label), bFoundSubgraph);
	};

	CheckFullCoverage(NodeBlueprint, TEXT("node fixture"), 4, 140);
	CheckFullCoverage(AnimBlueprint, TEXT("anim fixture"), 4, 7);

	FBlueprintContextExportOptions StructureOptions;
	StructureOptions.Profile = EBlueprintContextProfile::Structure;
	FBlueprintContextExportResult StructureResult;
	const TSharedRef<FJsonObject> StructureContext = FBlueprintContextExporter::BuildContextObject(
		NodeBlueprint, StructureOptions, StructureResult);
	const TSharedPtr<FJsonObject>* StructureSummary = nullptr;
	const TArray<TSharedPtr<FJsonValue>>* StructureGraphs = nullptr;
	if (TestTrue(TEXT("structure summary exists"),
			StructureContext->TryGetObjectField(TEXT("summary"), StructureSummary)
			&& StructureSummary != nullptr)
		&& TestTrue(TEXT("structure graphs exist"),
			StructureContext->TryGetArrayField(TEXT("graphs"), StructureGraphs)
			&& StructureGraphs != nullptr))
	{
		TestEqual(TEXT("structure does not export nodes"), StructureResult.NodeCount, 0);
		TestEqual(TEXT("structure keeps graph inventory"), StructureGraphs->Num(), 4);
		TestFalse(TEXT("structure summary disclaims node completeness"),
			(*StructureSummary)->GetBoolField(TEXT("graphNodesIncluded")));
		TestEqual(TEXT("structure coverage is count-only"),
			(*StructureSummary)->GetStringField(TEXT("graphNodeCoverage")),
			FString(TEXT("count-only")));
		if (StructureGraphs->Num() > 0)
		{
			TestEqual(TEXT("structure graph coverage is count-only"),
				(*StructureGraphs)[0]->AsObject()->GetStringField(TEXT("nodeCoverage")),
				FString(TEXT("count-only")));
			TestFalse(TEXT("structure graph does not include nodes"),
				(*StructureGraphs)[0]->AsObject()->GetBoolField(TEXT("nodesIncluded")));
		}
	}

	TArray<TSharedPtr<FJsonValue>> Assets;
	UEAgentKitNodeCoverageFixtures::AddBlueprintInventory(NodeBlueprint, Assets);
	UEAgentKitNodeCoverageFixtures::AddBlueprintInventory(AnimBlueprint, Assets);

	TSharedPtr<FJsonObject> Manifest = MakeShared<FJsonObject>();
	Manifest->SetStringField(TEXT("schemaVersion"), TEXT("ueak.node-coverage-fixture/1.0"));
	Manifest->SetArrayField(TEXT("assets"), Assets);
	const FString Path = FPaths::Combine(
		FPaths::ProjectSavedDir(), TEXT("NodeCoverageFixture"), TEXT("editor-counts.json"));
	IFileManager::Get().MakeDirectory(*FPaths::GetPath(Path), true);
	return TestTrue(TEXT("node coverage fixture manifest writes"),
		UEAgentKitNodeCoverageFixtures::WriteJson(Path, Manifest, Error));
}

#endif // WITH_DEV_AUTOMATION_TESTS
