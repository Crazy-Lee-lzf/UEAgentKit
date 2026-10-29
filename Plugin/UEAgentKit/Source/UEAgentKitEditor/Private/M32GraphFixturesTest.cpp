// M3.2 disposable multi-graph fixtures: event, function, and a cast/comment/knot graph.

#include "CoreMinimal.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "EdGraphNode_Comment.h"
#include "EdGraphSchema_K2.h"
#include "Engine/Blueprint.h"
#include "Engine/BlueprintGeneratedClass.h"
#include "HAL/FileManager.h"
#include "K2Node_CallFunction.h"
#include "K2Node_DynamicCast.h"
#include "K2Node_Event.h"
#include "K2Node_ExecutionSequence.h"
#include "K2Node_FunctionEntry.h"
#include "K2Node_FunctionResult.h"
#include "K2Node_Knot.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Kismet2/KismetEditorUtilities.h"
#include "Kismet/KismetMathLibrary.h"
#include "Kismet/KismetSystemLibrary.h"
#include "Misc/AutomationTest.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"

#if WITH_DEV_AUTOMATION_TESTS

namespace UEAgentKitM32Fixtures
{
	UFunction* FindFunction(UClass* Class, const TCHAR* Name)
	{
		return Class != nullptr ? Class->FindFunctionByName(FName(Name)) : nullptr;
	}

	UK2Node_CallFunction* MakeCall(UEdGraph* Graph, UFunction* Function, int32 X, int32 Y)
	{
		if (Function == nullptr)
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

	UEdGraph* PrimaryGraph(UBlueprint* Blueprint)
	{
		return Blueprint != nullptr && Blueprint->UbergraphPages.Num() > 0 ? Blueprint->UbergraphPages[0] : nullptr;
	}

	UEdGraph* FindGraph(UBlueprint* Blueprint, const FString& Name)
	{
		if (Blueprint == nullptr)
		{
			return nullptr;
		}
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

	UBlueprint* CreateBase(const FString& PackageName, const FString& AssetName, FString& OutError)
	{
		UPackage* Package = CreatePackage(*PackageName);
		if (Package == nullptr)
		{
			OutError = TEXT("Could not create the M32 package.");
			return nullptr;
		}
		UBlueprint* Blueprint = FKismetEditorUtilities::CreateBlueprint(
			AActor::StaticClass(), Package, FName(*AssetName), BPTYPE_Normal, FName(TEXT("UEAgentKitM32Fixture")));
		if (Blueprint == nullptr)
		{
			OutError = TEXT("Could not create the M32 Blueprint.");
			return nullptr;
		}
		return Blueprint;
	}

	bool SaveBlueprint(UBlueprint* Blueprint, FString& OutError)
	{
		if (Blueprint == nullptr)
		{
			OutError = TEXT("M32 Blueprint is null.");
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
			OutError = TEXT("Could not save the M32 Blueprint.");
			return false;
		}
		return true;
	}

	UBlueprint* CreateEventFixture(FString& OutError)
	{
		UBlueprint* Existing = LoadObject<UBlueprint>(
			nullptr, TEXT("/Game/M32/BP_M32_EventV5.BP_M32_EventV5"));
		if (Existing != nullptr)
		{
			return Existing;
		}
		UBlueprint* Blueprint = CreateBase(
			TEXT("/Game/M32/BP_M32_EventV5"), TEXT("BP_M32_EventV5"), OutError);
		UEdGraph* Graph = PrimaryGraph(Blueprint);
		if (Blueprint == nullptr || Graph == nullptr)
		{
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
		if (BeginPlay != nullptr)
		{
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
		}
		UK2Node_CallFunction* PrintNode = MakeCall(
			Graph, FindFunction(UKismetSystemLibrary::StaticClass(), TEXT("PrintString")), 420, 80);
		if (BeginPlay != nullptr && PrintNode != nullptr)
		{
			UEdGraphPin* From = BeginPlay->FindPin(TEXT("then"));
			UEdGraphPin* To = PrintNode->FindPin(TEXT("execute"));
			if (From != nullptr && To != nullptr)
			{
				From->MakeLinkTo(To);
			}
		}
		FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
		FKismetEditorUtilities::CompileBlueprint(Blueprint, EBlueprintCompileOptions::SkipGarbageCollection);
		return Blueprint;
	}

	UBlueprint* CreateFunctionFixture(FString& OutError)
	{
		UBlueprint* Existing = LoadObject<UBlueprint>(
			nullptr, TEXT("/Game/M32/BP_M32_FunctionV2.BP_M32_FunctionV2"));
		if (Existing != nullptr)
		{
			return Existing;
		}
		UBlueprint* Blueprint = CreateBase(
			TEXT("/Game/M32/BP_M32_FunctionV2"), TEXT("BP_M32_FunctionV2"), OutError);
		UEdGraph* EventGraph = PrimaryGraph(Blueprint);
		if (Blueprint == nullptr || EventGraph == nullptr)
		{
			return nullptr;
		}
		UEdGraph* FunctionGraph = FBlueprintEditorUtils::CreateNewGraph(
			Blueprint,
			FName(TEXT("M32Function")),
			UEdGraph::StaticClass(),
			UEdGraphSchema_K2::StaticClass());
		if (!FunctionGraph)
		{
			OutError = TEXT("Could not create the M32 function graph.");
			return nullptr;
		}
		FBlueprintEditorUtils::AddFunctionGraph<UClass>(Blueprint, FunctionGraph, true, nullptr);
		MakeCall(FunctionGraph, FindFunction(UKismetSystemLibrary::StaticClass(), TEXT("PrintString")), 520, 120);
		FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
		FKismetEditorUtilities::CompileBlueprint(Blueprint, EBlueprintCompileOptions::SkipGarbageCollection);
		return Blueprint;
	}

	UBlueprint* CreateComplexFixture(FString& OutError)
	{
		UBlueprint* Existing = LoadObject<UBlueprint>(
			nullptr, TEXT("/Game/M32/BP_M32_ComplexV2.BP_M32_ComplexV2"));
		if (Existing != nullptr)
		{
			return Existing;
		}
		UBlueprint* Blueprint = CreateBase(
			TEXT("/Game/M32/BP_M32_ComplexV2"), TEXT("BP_M32_ComplexV2"), OutError);
		UEdGraph* Graph = PrimaryGraph(Blueprint);
		if (Blueprint == nullptr || Graph == nullptr)
		{
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
		if (BeginPlay != nullptr)
		{
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
		}
		FGraphNodeCreator<UK2Node_ExecutionSequence> SequenceCreator(*Graph);
		UK2Node_ExecutionSequence* Sequence = SequenceCreator.CreateNode(false);
		Sequence->NodePosX = 320;
		Sequence->NodePosY = 40;
		SequenceCreator.Finalize();

		FGraphNodeCreator<UK2Node_Knot> KnotCreator(*Graph);
		UK2Node_Knot* Knot = KnotCreator.CreateNode(false);
		Knot->NodePosX = 640;
		Knot->NodePosY = 80;
		KnotCreator.Finalize();

		FGraphNodeCreator<UK2Node_DynamicCast> CastCreator(*Graph);
		UK2Node_DynamicCast* CastNode = CastCreator.CreateNode(false);
		CastNode->TargetType = AActor::StaticClass();
		CastNode->NodePosX = 860;
		CastNode->NodePosY = 220;
		CastCreator.Finalize();

		UK2Node_CallFunction* PrintNode = MakeCall(
			Graph, FindFunction(UKismetSystemLibrary::StaticClass(), TEXT("PrintString")), 1160, 260);
		UEdGraphNode_Comment* Comment = NewObject<UEdGraphNode_Comment>(Graph, NAME_None, RF_Transactional);
		Comment->NodePosX = 240;
		Comment->NodePosY = 0;
		Comment->NodeWidth = 1100;
		Comment->NodeHeight = 420;
		Comment->NodeComment = TEXT("M32 cast/comment/knot region");
		Comment->MoveMode = ECommentBoxMode::NoGroupMovement;
		Comment->CreateNewGuid();
		Graph->AddNode(Comment, true, false);
		Comment->AllocateDefaultPins();

		if (BeginPlay != nullptr && Sequence != nullptr && Knot != nullptr && PrintNode != nullptr)
		{
			const UEdGraphSchema_K2* Schema = Cast<UEdGraphSchema_K2>(Graph->GetSchema());
			UEdGraphPin* BeginThen = BeginPlay->FindPin(TEXT("then"));
			UEdGraphPin* SeqExec = Sequence->FindPin(TEXT("execute"));
			UEdGraphPin* SeqThen0 = Sequence->FindPin(TEXT("then_0"));
			UEdGraphPin* PrintExec = PrintNode->FindPin(TEXT("execute"));
			if (Schema != nullptr)
			{
				Schema->TryCreateConnection(BeginThen, SeqExec);
				Schema->TryCreateConnection(SeqThen0, Knot->GetInputPin());
				Schema->TryCreateConnection(Knot->GetOutputPin(), PrintExec);
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
	FUEAgentKitM32FixturesTest,
	"UEAgentKit.Native.M32Fixtures",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitM32FixturesTest::RunTest(const FString& Parameters)
{
	(void)Parameters;
	FString Error;
	UBlueprint* EventBlueprint = UEAgentKitM32Fixtures::CreateEventFixture(Error);
	UBlueprint* FunctionBlueprint = UEAgentKitM32Fixtures::CreateFunctionFixture(Error);
	UBlueprint* ComplexBlueprint = UEAgentKitM32Fixtures::CreateComplexFixture(Error);
	if (!TestNotNull(TEXT("M32 event fixture"), EventBlueprint)
		|| !TestNotNull(TEXT("M32 function fixture"), FunctionBlueprint)
		|| !TestNotNull(TEXT("M32 complex fixture"), ComplexBlueprint))
	{
		AddError(Error);
		return false;
	}
	UEdGraph* EventGraph = UEAgentKitM32Fixtures::PrimaryGraph(EventBlueprint);
	UEdGraph* FunctionGraph = UEAgentKitM32Fixtures::FindGraph(FunctionBlueprint, TEXT("M32Function"));
	UEdGraph* ComplexGraph = UEAgentKitM32Fixtures::PrimaryGraph(ComplexBlueprint);
	if (!TestNotNull(TEXT("M32 event graph"), EventGraph)
		|| !TestNotNull(TEXT("M32 function graph"), FunctionGraph)
		|| !TestNotNull(TEXT("M32 complex graph"), ComplexGraph))
	{
		return false;
	}
	for (UBlueprint* Blueprint : {EventBlueprint, FunctionBlueprint, ComplexBlueprint})
	{
		if (Blueprint->GetOutermost()->IsDirty()
			&& !TestTrue(TEXT("M32 fixture saves"), UEAgentKitM32Fixtures::SaveBlueprint(Blueprint, Error)))
		{
			AddError(Error);
			return false;
		}
	}
	TArray<TSharedPtr<FJsonValue>> Graphs;
	auto AddGraph = [&Graphs](const TCHAR* Kind, UBlueprint* Blueprint, UEdGraph* Graph)
	{
		TSharedPtr<FJsonObject> Item = MakeShared<FJsonObject>();
		Item->SetStringField(TEXT("kind"), Kind);
		Item->SetStringField(TEXT("assetPath"), Blueprint->GetPathName());
		Item->SetStringField(TEXT("graphName"), Graph->GetName());
		Item->SetStringField(TEXT("graphGuid"), Graph->GraphGuid.ToString(EGuidFormats::DigitsWithHyphensLower));
		Item->SetNumberField(TEXT("nodeCount"), Graph->Nodes.Num());
		int32 LinkedPins = 0;
		for (UEdGraphNode* Node : Graph->Nodes)
		{
			if (Node != nullptr)
			{
				for (UEdGraphPin* Pin : Node->Pins)
				{
					if (Pin != nullptr && Pin->LinkedTo.Num() > 0)
					{
						++LinkedPins;
					}
				}
			}
		}
		Item->SetNumberField(TEXT("linkedPins"), LinkedPins);
		Graphs.Add(MakeShared<FJsonValueObject>(Item));
	};
	AddGraph(TEXT("event"), EventBlueprint, EventGraph);
	AddGraph(TEXT("function"), FunctionBlueprint, FunctionGraph);
	AddGraph(TEXT("cast-comment-knot"), ComplexBlueprint, ComplexGraph);
	TSharedPtr<FJsonObject> Manifest = MakeShared<FJsonObject>();
	Manifest->SetStringField(TEXT("schemaVersion"), TEXT("ueak.m32.fixtures/1.0"));
	Manifest->SetArrayField(TEXT("graphs"), Graphs);
	const FString Path = FPaths::Combine(FPaths::ProjectDir(), TEXT("Evidence"), TEXT("m32-fixtures.json"));
	IFileManager::Get().MakeDirectory(*FPaths::GetPath(Path), true);
	return TestTrue(TEXT("M32 fixture manifest writes"), UEAgentKitM32Fixtures::WriteJson(Path, Manifest, Error));
}

#endif // WITH_DEV_AUTOMATION_TESTS
