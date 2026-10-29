// M3.5 disposable stress fixture: 53-node event graph and 22-node function graph.

#include "CoreMinimal.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "EdGraphNode_Comment.h"
#include "EdGraphSchema_K2.h"
#include "Engine/Blueprint.h"
#include "HAL/FileManager.h"
#include "K2Node_CallFunction.h"
#include "K2Node_Event.h"
#include "K2Node_FunctionEntry.h"
#include "K2Node_Knot.h"
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

namespace UEAgentKitM35Stress
{
	UFunction* FindFunction(UClass* Class, const TCHAR* Name)
	{
		return Class != nullptr ? Class->FindFunctionByName(FName(Name)) : nullptr;
	}

	UK2Node_CallFunction* MakeCall(UEdGraph* Graph, int32 Index, int32 X, int32 Y)
	{
		FGraphNodeCreator<UK2Node_CallFunction> Creator(*Graph);
		UK2Node_CallFunction* Node = Creator.CreateNode(false);
		Node->SetFromFunction(FindFunction(UKismetSystemLibrary::StaticClass(), TEXT("PrintString")));
		Node->NodePosX = X;
		Node->NodePosY = Y;
		Creator.Finalize();
		return Node;
	}

	UEdGraph* PrimaryGraph(UBlueprint* Blueprint)
	{
		return Blueprint != nullptr && Blueprint->UbergraphPages.Num() > 0 ? Blueprint->UbergraphPages[0] : nullptr;
	}

	UEdGraph* FindGraph(UBlueprint* Blueprint, const FString& Name)
	{
		for (UEdGraph* Graph : Blueprint->UbergraphPages)
		{
			if (Graph != nullptr && Graph->GetName() == Name)
			{
				return Graph;
			}
		}
		for (UEdGraph* Graph : Blueprint->FunctionGraphs)
		{
			if (Graph != nullptr && Graph->GetName() == Name)
			{
				return Graph;
			}
		}
		return nullptr;
	}

	bool SaveBlueprint(UBlueprint* Blueprint, FString& OutError)
	{
		UPackage* Package = Blueprint->GetOutermost();
		Package->MarkPackageDirty();
		const FString Filename = FPackageName::LongPackageNameToFilename(
			Package->GetName(), FPackageName::GetAssetPackageExtension());
		FSavePackageArgs SaveArgs;
		SaveArgs.TopLevelFlags = RF_Public | RF_Standalone;
		SaveArgs.SaveFlags = SAVE_NoError;
		if (!UPackage::SavePackage(Package, Blueprint, *Filename, SaveArgs))
		{
			OutError = TEXT("Could not save the M35 stress fixture.");
			return false;
		}
		return true;
	}

	UBlueprint* CreateFixture(FString& OutError)
	{
		const TCHAR* Path = TEXT("/Game/M35/BP_M35_Stress.BP_M35_Stress");
		UBlueprint* Existing = LoadObject<UBlueprint>(nullptr, Path);
		if (Existing != nullptr)
		{
			return Existing;
		}
		UPackage* Package = CreatePackage(TEXT("/Game/M35/BP_M35_Stress"));
		UBlueprint* Blueprint = FKismetEditorUtilities::CreateBlueprint(
			AActor::StaticClass(),
			Package,
			FName(TEXT("BP_M35_Stress")),
			BPTYPE_Normal,
			FName(TEXT("UEAgentKitM35StressFixture")));
		if (Blueprint == nullptr)
		{
			OutError = TEXT("Could not create the M35 stress Blueprint.");
			return nullptr;
		}
		UEdGraph* Graph = PrimaryGraph(Blueprint);
		if (Graph == nullptr)
		{
			OutError = TEXT("M35 stress Blueprint has no event graph.");
			return nullptr;
		}
		UK2Node_Event* BeginPlay = nullptr;
		for (UEdGraphNode* Node : Graph->Nodes)
		{
			UK2Node_Event* Event = Cast<UK2Node_Event>(Node);
			if (Event != nullptr && Event->EventReference.GetMemberName() == FName(TEXT("ReceiveBeginPlay")))
			{
				BeginPlay = Event;
				break;
			}
		}
		if (BeginPlay == nullptr)
		{
			OutError = TEXT("M35 stress Blueprint has no BeginPlay.");
			return nullptr;
		}
		const TArray<UEdGraphNode*> Initial = Graph->Nodes;
		for (UEdGraphNode* Node : Initial)
		{
			if (Node != nullptr && Node != BeginPlay)
			{
				FBlueprintEditorUtils::RemoveNode(Blueprint, Node, true);
			}
		}
		BeginPlay->NodePosX = 0;
		BeginPlay->NodePosY = 0;

		TArray<UK2Node_CallFunction*> Prints;
		for (int32 Index = 0; Index < 50; ++Index)
		{
			const int32 Column = Index % 10;
			const int32 Row = Index / 10;
			Prints.Add(MakeCall(Graph, Index, 360 + Column * 300, 80 + Row * 200));
		}
		if (Prints.Num() != 50)
		{
			OutError = TEXT("Could not create 50 M35 stress calls.");
			return nullptr;
		}
		UEdGraphPin* Previous = BeginPlay->FindPin(TEXT("then"));
		for (UK2Node_CallFunction* Print : Prints)
		{
			UEdGraphPin* Next = Print != nullptr ? Print->FindPin(TEXT("execute")) : nullptr;
			if (Previous == nullptr || Next == nullptr)
			{
				OutError = TEXT("M35 stress exec pins are unavailable.");
				return nullptr;
			}
			Previous->MakeLinkTo(Next);
			Previous = Print->FindPin(TEXT("then"));
		}
		UEdGraphNode_Comment* Comment = NewObject<UEdGraphNode_Comment>(Graph, NAME_None, RF_Transactional);
		Comment->NodePosX = 300;
		Comment->NodePosY = 20;
		Comment->NodeWidth = 3100;
		Comment->NodeHeight = 1150;
		Comment->NodeComment = TEXT("M35 50-call stress chain");
		Comment->CreateNewGuid();
		Graph->AddNode(Comment, true, false);
		Comment->AllocateDefaultPins();
		UEdGraphNode_Comment* Comment2 = NewObject<UEdGraphNode_Comment>(Graph, NAME_None, RF_Transactional);
		Comment2->NodePosX = 300;
		Comment2->NodePosY = 1200;
		Comment2->NodeWidth = 3100;
		Comment2->NodeHeight = 260;
		Comment2->NodeComment = TEXT("M35 stress tail");
		Comment2->CreateNewGuid();
		Graph->AddNode(Comment2, true, false);
		Comment2->AllocateDefaultPins();

		UEdGraph* FunctionGraph = FBlueprintEditorUtils::CreateNewGraph(
			Blueprint,
			FName(TEXT("M35Function")),
			UEdGraph::StaticClass(),
			UEdGraphSchema_K2::StaticClass());
		if (FunctionGraph == nullptr)
		{
			OutError = TEXT("Could not create the M35 function graph.");
			return nullptr;
		}
		FBlueprintEditorUtils::AddFunctionGraph<UClass>(Blueprint, FunctionGraph, true, nullptr);
		UK2Node_FunctionEntry* Entry = nullptr;
		for (UEdGraphNode* Node : FunctionGraph->Nodes)
		{
			Entry = Cast<UK2Node_FunctionEntry>(Node);
			if (Entry != nullptr)
			{
				break;
			}
		}
		UEdGraphPin* FunctionPrevious = Entry != nullptr ? Entry->FindPin(TEXT("then")) : nullptr;
		for (int32 Index = 0; Index < 20; ++Index)
		{
			UK2Node_CallFunction* Call = MakeCall(
				FunctionGraph, Index, 360 + (Index % 10) * 300, 80 + (Index / 10) * 200);
			if (FunctionPrevious != nullptr && Call != nullptr)
			{
				FunctionPrevious->MakeLinkTo(Call->FindPin(TEXT("execute")));
				FunctionPrevious = Call->FindPin(TEXT("then"));
			}
		}

		FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
		FKismetEditorUtilities::CompileBlueprint(Blueprint, EBlueprintCompileOptions::SkipGarbageCollection);
		return Blueprint;
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
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitM35StressFixtureTest,
	"UEAgentKit.Native.M35StressFixtures",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitM35StressFixtureTest::RunTest(const FString& Parameters)
{
	(void)Parameters;
	FString Error;
	UBlueprint* Blueprint = UEAgentKitM35Stress::CreateFixture(Error);
	if (!TestNotNull(TEXT("M35 stress fixture"), Blueprint))
	{
		AddError(Error);
		return false;
	}
	UEdGraph* Event = UEAgentKitM35Stress::PrimaryGraph(Blueprint);
	UEdGraph* Function = UEAgentKitM35Stress::FindGraph(Blueprint, TEXT("M35Function"));
	if (!TestNotNull(TEXT("M35 event graph"), Event)
		|| !TestNotNull(TEXT("M35 function graph"), Function))
	{
		return false;
	}
	if (!TestTrue(TEXT("M35 stress fixture saves"), UEAgentKitM35Stress::SaveBlueprint(Blueprint, Error)))
	{
		AddError(Error);
		return false;
	}
	auto GraphFacts = [](UEdGraph* Graph) -> TSharedPtr<FJsonObject>
	{
		TSharedPtr<FJsonObject> Facts = MakeShared<FJsonObject>();
		int32 Linked = 0;
		int32 InvalidGuids = 0;
		int32 InvalidPins = 0;
		for (UEdGraphNode* Node : Graph->Nodes)
		{
			if (Node == nullptr)
			{
				continue;
			}
			if (!Node->NodeGuid.IsValid())
			{
				++InvalidGuids;
			}
			for (UEdGraphPin* Pin : Node->Pins)
			{
				if (Pin == nullptr || !Pin->PinId.IsValid())
				{
					++InvalidPins;
				}
				else if (Pin->LinkedTo.Num() > 0)
				{
					++Linked;
				}
			}
		}
		Facts->SetStringField(TEXT("graphName"), Graph->GetName());
		Facts->SetStringField(TEXT("graphGuid"), Graph->GraphGuid.ToString(EGuidFormats::DigitsWithHyphensLower));
		Facts->SetNumberField(TEXT("nodeCount"), Graph->Nodes.Num());
		Facts->SetNumberField(TEXT("linkedPins"), Linked);
		Facts->SetNumberField(TEXT("edgeCount"), Linked / 2);
		Facts->SetNumberField(TEXT("invalidNodeGuids"), InvalidGuids);
		Facts->SetNumberField(TEXT("invalidPins"), InvalidPins);
		return Facts;
	};
	TSharedPtr<FJsonObject> Manifest = MakeShared<FJsonObject>();
	Manifest->SetStringField(TEXT("schemaVersion"), TEXT("ueak.m35.stress-fixture/1.0"));
	Manifest->SetStringField(TEXT("assetPath"), Blueprint->GetPathName());
	Manifest->SetObjectField(TEXT("eventGraph"), GraphFacts(Event));
	Manifest->SetObjectField(TEXT("functionGraph"), GraphFacts(Function));
	const FString Path = FPaths::Combine(FPaths::ProjectDir(), TEXT("Evidence"), TEXT("m35-stress-fixture.json"));
	IFileManager::Get().MakeDirectory(*FPaths::GetPath(Path), true);
	return TestTrue(TEXT("M35 stress manifest writes"), UEAgentKitM35Stress::WriteJson(Path, Manifest, Error));
}

#endif // WITH_DEV_AUTOMATION_TESTS
