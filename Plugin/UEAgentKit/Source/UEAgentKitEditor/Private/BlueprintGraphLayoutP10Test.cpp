// P10 disposable automation: legal knot fan-out/merge through the internal
// position-only apply, with Undo/Redo, explicit Save and a separate-process
// Reload.  The plan is built offline by tests/python/p10_build_plan_probe.py
// from the real P10 exporter/preflight output, so the applied request is the
// canonical ApplyRequest, not a hand-written fixture.
//
// This test never touches Lyra, Reforge or any production asset, and it never
// registers a new MCP/TS write surface.

#include "BlueprintContextExporter.h"
#include "BlueprintContextSha256.h"
#include "BlueprintGraphLayoutApply.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "EdGraphNode_Comment.h"
#include "EdGraphSchema_K2.h"
#include "Editor.h"
#include "Editor/Transactor.h"
#include "Engine/Blueprint.h"
#include "GameFramework/Actor.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformProcess.h"
#include "Interfaces/IPluginManager.h"
#include "K2Node_CallFunction.h"
#include "K2Node_Event.h"
#include "K2Node_ExecutionSequence.h"
#include "K2Node_Knot.h"
#include "Kismet/KismetMathLibrary.h"
#include "Kismet/KismetStringLibrary.h"
#include "Kismet/KismetSystemLibrary.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Kismet2/KismetEditorUtilities.h"
#include "Misc/App.h"
#include "Misc/AutomationTest.h"
#include "Misc/DateTime.h"
#include "Misc/EngineVersion.h"
#include "Misc/FileHelper.h"
#include "Misc/PackageName.h"
#include "Misc/Paths.h"
#include "Misc/SecureHash.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"

#if WITH_DEV_AUTOMATION_TESTS

#define LOCTEXT_NAMESPACE "UEAgentKitGraphLayoutP10"

namespace UEAgentKitGraphLayoutP10Test
{
	namespace
	{
		const TCHAR* FixturePackageName = TEXT("/Game/UEAgentKitP10/BP_LayoutP10");
		const TCHAR* FixtureAssetName = TEXT("BP_LayoutP10");
		const TCHAR* FixtureAssetPath = TEXT("/Game/UEAgentKitP10/BP_LayoutP10.BP_LayoutP10");
		const TCHAR* BrokenPackageName = TEXT("/Game/UEAgentKitP10/BP_LayoutP10Broken");
		const TCHAR* BrokenAssetName = TEXT("BP_LayoutP10Broken");
		const TCHAR* BrokenAssetPath = TEXT("/Game/UEAgentKitP10/BP_LayoutP10Broken.BP_LayoutP10Broken");

		const TCHAR* ContextFile = TEXT("p10-context.json");
		const TCHAR* PreflightFile = TEXT("p10-preflight.json");
		const TCHAR* BrokenPreflightFile = TEXT("p10-broken-preflight.json");
		const TCHAR* FixtureExpectedFile = TEXT("p10-fixture-expected.json");
		const TCHAR* ApplyRequestFile = TEXT("p10-apply-request.json");
		const TCHAR* NegativeRequestFile = TEXT("p10-negative-request.json");
		const TCHAR* ApplyResultFile = TEXT("p10-apply-result.json");
		const TCHAR* ReloadResultFile = TEXT("p10-reload-result.json");

		FString GuidLower(const FGuid& Guid)
		{
			return Guid.ToString(EGuidFormats::DigitsWithHyphensLower);
		}

		FString EvidencePath(const TCHAR* FileName)
		{
			return FPaths::Combine(FPaths::ProjectDir(), TEXT("Evidence"), FileName);
		}

		FString Sha256FileText(const FString& Path)
		{
			FString Digest;
			if (!FBlueprintContextSha256::HashFile(Path, Digest))
			{
				return FString();
			}
			return FString(TEXT("sha256:")) + Digest.ToLower();
		}

		FString Sha1Text(const FString& Text)
		{
			FTCHARToUTF8 Utf8(*Text);
			uint8 Digest[20];
			FSHA1::HashBuffer(Utf8.Get(), static_cast<uint32>(Utf8.Length()), Digest);
			FString Hex;
			Hex.Reserve(40);
			for (const uint8 Byte : Digest)
			{
				Hex += FString::Printf(TEXT("%02x"), static_cast<uint32>(Byte));
			}
			return FString(TEXT("sha1:")) + Hex;
		}

		FString ModuleDllPath()
		{
			const TSharedPtr<IPlugin> Plugin = IPluginManager::Get().FindPlugin(TEXT("UEAgentKit"));
			if (!Plugin.IsValid())
			{
				return FString();
			}
			return FPaths::Combine(
				Plugin->GetBaseDir(),
				TEXT("Binaries"),
				FPlatformProcess::GetBinariesSubdirectory(),
				TEXT("UnrealEditor-UEAgentKitEditor.dll"));
		}

		TSharedPtr<FJsonObject> ReadJsonFile(const FString& Path, FString& OutError)
		{
			FString Text;
			if (!FFileHelper::LoadFileToString(Text, *Path))
			{
				OutError = FString::Printf(TEXT("Could not read %s."), *Path);
				return nullptr;
			}
			TSharedPtr<FJsonObject> Object;
			const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Text);
			if (!FJsonSerializer::Deserialize(Reader, Object) || !Object.IsValid())
			{
				OutError = FString::Printf(TEXT("Could not parse JSON in %s."), *Path);
				return nullptr;
			}
			return Object;
		}

		bool WriteJson(const FString& Path, const TSharedPtr<FJsonObject>& Object, FString& OutError)
		{
			FString Text;
			const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Text);
			if (!FJsonSerializer::Serialize(Object.ToSharedRef(), Writer))
			{
				OutError = FString::Printf(TEXT("Could not serialize %s."), *Path);
				return false;
			}
			IFileManager::Get().MakeDirectory(*FPaths::GetPath(Path), true);
			if (!FFileHelper::SaveStringToFile(
				Text,
				*Path,
				FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
			{
				OutError = FString::Printf(TEXT("Could not write %s."), *Path);
				return false;
			}
			return true;
		}

		bool SaveBlueprint(UBlueprint* Blueprint, FString& OutError)
		{
			if (Blueprint == nullptr)
			{
				OutError = TEXT("Blueprint is null.");
				return false;
			}
			UPackage* Package = Blueprint->GetOutermost();
			if (Package == nullptr)
			{
				OutError = TEXT("Blueprint has no package.");
				return false;
			}
			const FString Filename = FPackageName::LongPackageNameToFilename(
				Package->GetName(),
				FPackageName::GetAssetPackageExtension());
			FSavePackageArgs SaveArgs;
			SaveArgs.TopLevelFlags = RF_Public | RF_Standalone;
			SaveArgs.SaveFlags = SAVE_NoError;
			if (!UPackage::SavePackage(Package, nullptr, *Filename, SaveArgs))
			{
				OutError = FString::Printf(TEXT("SavePackage failed for %s."), *Filename);
				return false;
			}
			return true;
		}

		UEdGraph* GetPrimaryGraph(UBlueprint* Blueprint)
		{
			if (Blueprint == nullptr || Blueprint->UbergraphPages.Num() == 0)
			{
				return nullptr;
			}
			return Blueprint->UbergraphPages[0];
		}

		bool LinkPins(UEdGraph* Graph, UEdGraphPin* From, UEdGraphPin* To)
		{
			if (Graph == nullptr || From == nullptr || To == nullptr)
			{
				return false;
			}
			if (Graph->GetSchema() != nullptr && Graph->GetSchema()->TryCreateConnection(From, To))
			{
				return From->LinkedTo.Contains(To) || To->LinkedTo.Contains(From);
			}
			From->MakeLinkTo(To);
			return From->LinkedTo.Contains(To) || To->LinkedTo.Contains(From);
		}

		UFunction* FindFunction(UClass* Class, const TCHAR* Name)
		{
			return Class != nullptr ? Class->FindFunctionByName(FName(Name)) : nullptr;
		}

		UK2Node_CallFunction* MakeCall(
			UEdGraph* Graph,
			UFunction* Function,
			int32 X,
			int32 Y)
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

		FString StructuralState(UEdGraph* Graph)
		{
			TArray<UEdGraphNode*> SortedNodes;
			for (UEdGraphNode* Node : Graph->Nodes)
			{
				if (Node != nullptr)
				{
					SortedNodes.Add(Node);
				}
			}
			SortedNodes.Sort([](const UEdGraphNode& Left, const UEdGraphNode& Right)
			{
				return Left.NodeGuid.ToString(EGuidFormats::Digits) < Right.NodeGuid.ToString(EGuidFormats::Digits);
			});
			FString State;
			for (const UEdGraphNode* Node : SortedNodes)
			{
				State += Node->NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower);
				State += TEXT("|");
				State += Node->GetClass() != nullptr ? Node->GetClass()->GetPathName() : FString();
				State += TEXT("|");
				State += Node->NodeComment;
				State += TEXT("|");
				State += FString::Printf(TEXT("%d|%d|"), Node->NodeWidth, Node->NodeHeight);
				TArray<FString> Pins;
				for (const UEdGraphPin* Pin : Node->Pins)
				{
					if (Pin == nullptr)
					{
						continue;
					}
					TArray<FString> Links;
					for (const UEdGraphPin* Linked : Pin->LinkedTo)
					{
						if (Linked != nullptr && Linked->GetOwningNode() != nullptr)
						{
							Links.Add(
								GuidLower(Linked->GetOwningNode()->NodeGuid)
								+ TEXT("/")
								+ Linked->PinName.ToString());
						}
					}
					Links.Sort();
					Pins.Add(
						Pin->PinName.ToString()
						+ TEXT(":")
						+ Pin->PinType.PinCategory.ToString()
						+ TEXT(":")
						+ FString::FromInt(static_cast<int32>(Pin->Direction))
						+ TEXT("{")
						+ FString::Join(Links, TEXT(","))
						+ TEXT("}"));
				}
				Pins.Sort();
				State += FString::Join(Pins, TEXT(","));
				State += TEXT(";");
			}
			return State;
		}

		// Fixture topology:
		//   BeginPlay -> Sequence
		//   Sequence.then_0 -> KnotExec.in       (exec merge, 2 inputs)
		//   Sequence.then_1 -> KnotExec.in
		//   KnotExec.out -> PrintStringA.execute
		//   RandomInt.ReturnValue -> KnotData.in  (data fan-out, 2 sinks)
		//   KnotData.out -> Conv1.InInt, Conv2.InInt
		//   Conv1.ReturnValue -> PrintStringB.InString
		//   Conv2.ReturnValue -> PrintStringC.InString
		//   BrokenKnot.out -> PrintStringD.execute  (0 inputs: fail-closed case)
		//   Comment (annotation, never moved)
		UBlueprint* CreateFixture(FString& OutError)
		{
			UPackage* Package = CreatePackage(FixturePackageName);
			if (Package == nullptr)
			{
				OutError = TEXT("Could not create the P10 disposable fixture package.");
				return nullptr;
			}
			UBlueprint* Blueprint = FKismetEditorUtilities::CreateBlueprint(
				AActor::StaticClass(),
				Package,
				FName(FixtureAssetName),
				BPTYPE_Normal,
				FName(TEXT("UEAgentKitP10Fixture")));
			if (Blueprint == nullptr)
			{
				OutError = TEXT("Could not create the P10 disposable K2 Blueprint.");
				return nullptr;
			}
			UEdGraph* Graph = GetPrimaryGraph(Blueprint);
			if (Graph == nullptr)
			{
				OutError = TEXT("The P10 disposable Blueprint has no ubergraph.");
				return nullptr;
			}

			UK2Node_Event* BeginPlay = nullptr;
			for (UEdGraphNode* Candidate : Graph->Nodes)
			{
				UK2Node_Event* Event = Cast<UK2Node_Event>(Candidate);
				if (Event != nullptr && Event->EventReference.GetMemberName() == FName(TEXT("ReceiveBeginPlay")))
				{
					BeginPlay = Event;
					break;
				}
			}
			if (BeginPlay == nullptr)
			{
				OutError = TEXT("The P10 disposable Blueprint has no ReceiveBeginPlay event.");
				return nullptr;
			}
			const TArray<UEdGraphNode*> InitialNodes = Graph->Nodes;
			for (UEdGraphNode* Node : InitialNodes)
			{
				if (Node != nullptr && Node != BeginPlay)
				{
					FBlueprintEditorUtils::RemoveNode(Blueprint, Node, true);
				}
			}
			BeginPlay->NodePosX = 0;
			BeginPlay->NodePosY = 0;

			FGraphNodeCreator<UK2Node_ExecutionSequence> SequenceCreator(*Graph);
			UK2Node_ExecutionSequence* Sequence = SequenceCreator.CreateNode(false);
			Sequence->NodePosX = 260;
			Sequence->NodePosY = 0;
			SequenceCreator.Finalize();

			FGraphNodeCreator<UK2Node_Knot> ExecKnotCreator(*Graph);
			UK2Node_Knot* ExecKnot = ExecKnotCreator.CreateNode(false);
			ExecKnot->NodePosX = 560;
			ExecKnot->NodePosY = 0;
			ExecKnotCreator.Finalize();

			UK2Node_CallFunction* PrintA = MakeCall(
				Graph, FindFunction(UKismetSystemLibrary::StaticClass(), TEXT("PrintString")), 820, -120);
			UK2Node_CallFunction* RandomInt = MakeCall(
				Graph, FindFunction(UKismetMathLibrary::StaticClass(), TEXT("RandomIntegerInRange")), 260, 320);
			FGraphNodeCreator<UK2Node_Knot> DataKnotCreator(*Graph);
			UK2Node_Knot* DataKnot = DataKnotCreator.CreateNode(false);
			DataKnot->NodePosX = 560;
			DataKnot->NodePosY = 320;
			DataKnotCreator.Finalize();
			UK2Node_CallFunction* Conv1 = MakeCall(
				Graph, FindFunction(UKismetStringLibrary::StaticClass(), TEXT("Conv_IntToString")), 820, 240);
			UK2Node_CallFunction* Conv2 = MakeCall(
				Graph, FindFunction(UKismetStringLibrary::StaticClass(), TEXT("Conv_IntToString")), 820, 440);
			UK2Node_CallFunction* PrintB = MakeCall(
				Graph, FindFunction(UKismetSystemLibrary::StaticClass(), TEXT("PrintString")), 1120, 200);
			UK2Node_CallFunction* PrintC = MakeCall(
				Graph, FindFunction(UKismetSystemLibrary::StaticClass(), TEXT("PrintString")), 1120, 420);

			if (Sequence == nullptr
				|| ExecKnot == nullptr
				|| DataKnot == nullptr
				|| PrintA == nullptr
				|| PrintB == nullptr
				|| PrintC == nullptr
				|| RandomInt == nullptr
				|| Conv1 == nullptr
				|| Conv2 == nullptr)
			{
				OutError = TEXT("Could not create one of the P10 fixture nodes.");
				return nullptr;
			}

			UEdGraphPin* EventThen = BeginPlay->FindPin(UEdGraphSchema_K2::PN_Then, EGPD_Output);
			UEdGraphPin* SequenceExecute = Sequence->FindPin(UEdGraphSchema_K2::PN_Execute, EGPD_Input);
			UEdGraphPin* RandomReturn = RandomInt->FindPin(TEXT("ReturnValue"), EGPD_Output);
			if (EventThen == nullptr
				|| SequenceExecute == nullptr
				|| RandomReturn == nullptr
				|| !LinkPins(Graph, EventThen, SequenceExecute)
				|| !LinkPins(Graph, Sequence->GetThenPinGivenIndex(0), ExecKnot->GetInputPin())
				|| !LinkPins(Graph, Sequence->GetThenPinGivenIndex(1), ExecKnot->GetInputPin())
				|| !LinkPins(Graph, ExecKnot->GetOutputPin(), PrintA->FindPin(UEdGraphSchema_K2::PN_Execute, EGPD_Input))
				|| !LinkPins(Graph, RandomReturn, DataKnot->GetInputPin())
				|| !LinkPins(Graph, DataKnot->GetOutputPin(), Conv1->FindPin(TEXT("InInt"), EGPD_Input))
				|| !LinkPins(Graph, DataKnot->GetOutputPin(), Conv2->FindPin(TEXT("InInt"), EGPD_Input))
				|| !LinkPins(Graph, Conv1->FindPin(TEXT("ReturnValue"), EGPD_Output), PrintB->FindPin(TEXT("InString"), EGPD_Input))
				|| !LinkPins(Graph, Conv2->FindPin(TEXT("ReturnValue"), EGPD_Output), PrintC->FindPin(TEXT("InString"), EGPD_Input)))
			{
				OutError = TEXT("Could not create the P10 fan-out/merge topology.");
				return nullptr;
			}

			UEdGraphNode_Comment* Comment = NewObject<UEdGraphNode_Comment>(
				Graph,
				NAME_None,
				RF_Transactional);
			Comment->NodePosX = 200;
			Comment->NodePosY = 220;
			Comment->NodeWidth = 1200;
			Comment->NodeHeight = 460;
			Comment->NodeComment = TEXT("P10 knot fan-out / merge fixture region");
			Comment->MoveMode = ECommentBoxMode::NoGroupMovement;
			Comment->CreateNewGuid();
			Graph->AddNode(Comment, true, false);
			Comment->AllocateDefaultPins();

			FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
			FKismetEditorUtilities::CompileBlueprint(Blueprint, EBlueprintCompileOptions::SkipGarbageCollection);
			if (Blueprint->Status == BS_Error)
			{
				OutError = TEXT("The P10 disposable fixture Blueprint failed to compile.");
				return nullptr;
			}
			FAssetRegistryModule::AssetCreated(Blueprint);
			Package->MarkPackageDirty();
			if (!SaveBlueprint(Blueprint, OutError))
			{
				return nullptr;
			}
			return Blueprint;
		}

		UBlueprint* LoadOrCreateFixture(FString& OutError)
		{
			if (UBlueprint* Existing = LoadObject<UBlueprint>(nullptr, FixtureAssetPath))
			{
				return Existing;
			}
			return CreateFixture(OutError);
		}

		// A separate, tiny disposable graph whose only knot has zero inputs and
		// one output.  The layout engine (correctly) refuses to plan it, so it
		// lives outside the positive fixture; it exists to prove the C++ live
		// apply gate rejects a moved broken knot on its own.
		UBlueprint* CreateBrokenFixture(FString& OutError)
		{
			UPackage* Package = CreatePackage(BrokenPackageName);
			if (Package == nullptr)
			{
				OutError = TEXT("Could not create the P10 broken fixture package.");
				return nullptr;
			}
			UBlueprint* Blueprint = FKismetEditorUtilities::CreateBlueprint(
				AActor::StaticClass(),
				Package,
				FName(BrokenAssetName),
				BPTYPE_Normal,
				FName(TEXT("UEAgentKitP10BrokenFixture")));
			if (Blueprint == nullptr)
			{
				OutError = TEXT("Could not create the P10 broken K2 Blueprint.");
				return nullptr;
			}
			UEdGraph* Graph = GetPrimaryGraph(Blueprint);
			if (Graph == nullptr)
			{
				OutError = TEXT("The P10 broken Blueprint has no ubergraph.");
				return nullptr;
			}
			UK2Node_Event* BeginPlay = nullptr;
			for (UEdGraphNode* Candidate : Graph->Nodes)
			{
				UK2Node_Event* Event = Cast<UK2Node_Event>(Candidate);
				if (Event != nullptr && Event->EventReference.GetMemberName() == FName(TEXT("ReceiveBeginPlay")))
				{
					BeginPlay = Event;
					break;
				}
			}
			if (BeginPlay == nullptr)
			{
				OutError = TEXT("The P10 broken Blueprint has no ReceiveBeginPlay event.");
				return nullptr;
			}
			const TArray<UEdGraphNode*> InitialNodes = Graph->Nodes;
			for (UEdGraphNode* Node : InitialNodes)
			{
				if (Node != nullptr && Node != BeginPlay)
				{
					FBlueprintEditorUtils::RemoveNode(Blueprint, Node, true);
				}
			}
			BeginPlay->NodePosX = 0;
			BeginPlay->NodePosY = 0;

			FGraphNodeCreator<UK2Node_Knot> KnotCreator(*Graph);
			UK2Node_Knot* Knot = KnotCreator.CreateNode(false);
			Knot->NodePosX = 320;
			Knot->NodePosY = 0;
			KnotCreator.Finalize();
			UK2Node_CallFunction* Print = MakeCall(
				Graph, FindFunction(UKismetSystemLibrary::StaticClass(), TEXT("PrintString")), 620, 0);
			if (Knot == nullptr || Print == nullptr
				|| !LinkPins(
					Graph,
					Knot->GetOutputPin(),
					Print->FindPin(UEdGraphSchema_K2::PN_Execute, EGPD_Input)))
			{
				OutError = TEXT("Could not create the P10 broken-knot topology.");
				return nullptr;
			}
			FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
			FKismetEditorUtilities::CompileBlueprint(Blueprint, EBlueprintCompileOptions::SkipGarbageCollection);
			if (Blueprint->Status == BS_Error)
			{
				OutError = TEXT("The P10 broken fixture Blueprint failed to compile.");
				return nullptr;
			}
			FAssetRegistryModule::AssetCreated(Blueprint);
			Package->MarkPackageDirty();
			if (!SaveBlueprint(Blueprint, OutError))
			{
				return nullptr;
			}
			return Blueprint;
		}

		UBlueprint* LoadOrCreateBrokenFixture(FString& OutError)
		{
			if (UBlueprint* Existing = LoadObject<UBlueprint>(nullptr, BrokenAssetPath))
			{
				return Existing;
			}
			return CreateBrokenFixture(OutError);
		}

		bool ExportContext(
			UBlueprint* Blueprint,
			UEdGraph* Graph,
			TSharedPtr<FJsonObject>& OutContext,
			FString& OutError)
		{
			FBlueprintContextExportOptions Options;
			Options.OutputDirectory = FPaths::Combine(FPaths::ProjectDir(), TEXT("Evidence"), TEXT("export"));
			Options.Profile = EBlueprintContextProfile::Full;
			Options.GraphFilter = Graph->GetName();
			Options.bWriteJson = true;
			Options.bWriteBpctx = false;
			Options.bPrettyJson = true;
			FBlueprintContextExporter::ApplyProfileDefaults(Options);
			Options.bIncludeLayout = true;
			Options.bIncludeReflectedNodeProperties = true;
			Options.bIncludeUnchangedDefaults = false;
			FBlueprintContextExportResult ExportResult;
			if (!FBlueprintContextExporter::ExportBlueprint(Blueprint, Options, ExportResult))
			{
				OutError = FString::Printf(TEXT("Canonical export failed: %s"), *ExportResult.Error);
				return false;
			}
			FString ContextText;
			if (!FFileHelper::LoadFileToString(ContextText, *ExportResult.JsonPath))
			{
				OutError = FString::Printf(TEXT("Could not read exporter JSON: %s"), *ExportResult.JsonPath);
				return false;
			}
			const FString ContextPath = EvidencePath(ContextFile);
			if (!FFileHelper::SaveStringToFile(
				ContextText,
				*ContextPath,
				FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
			{
				OutError = FString::Printf(TEXT("Could not copy exporter JSON to %s."), *ContextPath);
				return false;
			}
			OutContext = ReadJsonFile(ContextPath, OutError);
			return OutContext.IsValid();
		}

		const TSharedPtr<FJsonObject>* FindKnotEvidence(const TSharedPtr<FJsonObject>& Preflight, const FString& NodeId)
		{
			const TArray<TSharedPtr<FJsonValue>>* Nodes = nullptr;
			if (!Preflight->TryGetArrayField(TEXT("nodes"), Nodes) || Nodes == nullptr)
			{
				return nullptr;
			}
			for (const TSharedPtr<FJsonValue>& Entry : *Nodes)
			{
				const TSharedPtr<FJsonObject> Node = Entry.IsValid() ? Entry->AsObject() : nullptr;
				if (!Node.IsValid() || Node->GetStringField(TEXT("nodeId")) != NodeId)
				{
					continue;
				}
				const TSharedPtr<FJsonObject>* Knot = nullptr;
				if (Node->TryGetObjectField(TEXT("knot"), Knot) && Knot != nullptr)
				{
					return Knot;
				}
				return nullptr;
			}
			return nullptr;
		}

		bool PositionsEqual(
			UEdGraph* Graph,
			const TMap<FGuid, FIntPoint>& Expected)
		{
			for (const TPair<FGuid, FIntPoint>& Pair : Expected)
			{
				UEdGraphNode* Node = nullptr;
				for (UEdGraphNode* Candidate : Graph->Nodes)
				{
					if (Candidate != nullptr && Candidate->NodeGuid == Pair.Key)
					{
						Node = Candidate;
						break;
					}
				}
				if (Node == nullptr || Node->NodePosX != Pair.Value.X || Node->NodePosY != Pair.Value.Y)
				{
					return false;
				}
			}
			return true;
		}
	}

	IMPLEMENT_SIMPLE_AUTOMATION_TEST(
		FUEAgentKitGraphLayoutP10FixtureTest,
		"UEAgentKit.GraphLayout.P10FixtureContract",
		EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

	bool FUEAgentKitGraphLayoutP10FixtureTest::RunTest(const FString& Parameters)
	{
		FString Error;
		UBlueprint* Blueprint = LoadOrCreateFixture(Error);
		if (!TestNotNull(TEXT("P10 disposable Blueprint is loaded"), Blueprint))
		{
			AddError(Error);
			return false;
		}
		UEdGraph* Graph = GetPrimaryGraph(Blueprint);
		if (!TestNotNull(TEXT("P10 disposable ubergraph is present"), Graph))
		{
			return false;
		}
		if (!TestFalse(TEXT("P10 fixture starts clean"), Blueprint->GetOutermost()->IsDirty()))
		{
			return false;
		}
		TestEqual(TEXT("P10 fixture has eleven nodes"), Graph->Nodes.Num(), 11);

		TSharedPtr<FJsonObject> Preflight;
		FString ErrorCode;
		FString ErrorMessage;
		if (!TestTrue(
			TEXT("P10 preflight succeeds"),
			UEAgentKitBlueprintGraphLayout::BuildPreflight(Blueprint, Graph, Preflight, ErrorCode, ErrorMessage)))
		{
			AddError(FString::Printf(TEXT("P10 preflight failed: %s %s"), *ErrorCode, *ErrorMessage));
			return false;
		}
		TestEqual(TEXT("P10 fixture has two reroutes"), Preflight->GetNumberField(TEXT("rerouteCount")), 2.0);
		TestEqual(TEXT("P10 fixture has one comment"), Preflight->GetNumberField(TEXT("commentCount")), 1.0);

		// Every reroute must carry exact pin evidence; the shapes are the ones we
		// authored, so a regression in the emitter is caught here.
		int32 FanOutSources = -1;
		int32 FanOutSinks = -1;
		int32 MergeSources = -1;
		int32 MergeSinks = -1;
		for (UEdGraphNode* Node : Graph->Nodes)
		{
			UK2Node_Knot* Knot = Cast<UK2Node_Knot>(Node);
			if (Knot == nullptr)
			{
				continue;
			}
			const TSharedPtr<FJsonObject>* Evidence = FindKnotEvidence(Preflight, TEXT("n:") + GuidLower(Knot->NodeGuid));
			if (!TestNotNull(TEXT("every P10 reroute has knot evidence"), Evidence))
			{
				return false;
			}
			const int32 Sources = static_cast<int32>((*Evidence)->GetArrayField(TEXT("inputSources")).Num());
			const int32 Sinks = static_cast<int32>((*Evidence)->GetArrayField(TEXT("outputSinks")).Num());
			const FString InputType = (*Evidence)->GetStringField(TEXT("inputType"));
			const FString OutputType = (*Evidence)->GetStringField(TEXT("outputType"));
			TestEqual(TEXT("P10 knot input/output types match"), InputType, OutputType);
			if (Sources == 1 && Sinks == 2)
			{
				FanOutSources = Sources;
				FanOutSinks = Sinks;
				TestEqual(TEXT("P10 data fan-out is typed as int"), InputType, FString(TEXT("int")));
			}
			else if (Sources == 2 && Sinks == 1)
			{
				MergeSources = Sources;
				MergeSinks = Sinks;
				TestEqual(TEXT("P10 exec merge is typed as exec"), InputType, FString(TEXT("exec")));
			}
		}
		TestEqual(TEXT("P10 data fan-out shape is 1->2"), FanOutSources, 1);
		TestEqual(TEXT("P10 data fan-out sinks"), FanOutSinks, 2);
		TestEqual(TEXT("P10 exec merge sources"), MergeSources, 2);
		TestEqual(TEXT("P10 exec merge shape is 2->1"), MergeSinks, 1);

		TSharedPtr<FJsonObject> Context;
		if (!TestTrue(TEXT("P10 real full exporter succeeds"), ExportContext(Blueprint, Graph, Context, Error)))
		{
			AddError(Error);
			return false;
		}
		const FString PreflightPath = EvidencePath(PreflightFile);
		if (!TestTrue(TEXT("P10 preflight evidence is written"), WriteJson(PreflightPath, Preflight, Error)))
		{
			AddError(Error);
			return false;
		}

		// Broken-knot fixture: a separate asset because the layout engine
		// correctly refuses to plan a 0-input knot.  Only the live C++ apply
		// gate is exercised against it.
		UBlueprint* BrokenBlueprint = LoadOrCreateBrokenFixture(Error);
		if (!TestNotNull(TEXT("P10 broken fixture is loaded"), BrokenBlueprint))
		{
			AddError(Error);
			return false;
		}
		UEdGraph* BrokenGraph = GetPrimaryGraph(BrokenBlueprint);
		if (!TestNotNull(TEXT("P10 broken fixture graph is present"), BrokenGraph))
		{
			return false;
		}
		TSharedPtr<FJsonObject> BrokenPreflight;
		if (!TestTrue(
			TEXT("P10 broken fixture preflight succeeds"),
			UEAgentKitBlueprintGraphLayout::BuildPreflight(
				BrokenBlueprint, BrokenGraph, BrokenPreflight, ErrorCode, ErrorMessage)))
		{
			AddError(FString::Printf(TEXT("P10 broken preflight failed: %s %s"), *ErrorCode, *ErrorMessage));
			return false;
		}
		TestEqual(TEXT("P10 broken fixture has one reroute"), BrokenPreflight->GetNumberField(TEXT("rerouteCount")), 1.0);
		UK2Node_Knot* BrokenKnot = nullptr;
		for (UEdGraphNode* Node : BrokenGraph->Nodes)
		{
			if (UK2Node_Knot* Knot = Cast<UK2Node_Knot>(Node))
			{
				BrokenKnot = Knot;
				break;
			}
		}
		const TSharedPtr<FJsonObject>* BrokenEvidence = BrokenKnot != nullptr
			? FindKnotEvidence(BrokenPreflight, TEXT("n:") + GuidLower(BrokenKnot->NodeGuid))
			: nullptr;
		if (!TestNotNull(TEXT("P10 broken knot evidence exists"), BrokenEvidence))
		{
			return false;
		}
		TestEqual(
			TEXT("P10 broken knot has no source"),
			static_cast<int32>((*BrokenEvidence)->GetArrayField(TEXT("inputSources")).Num()),
			0);
		TestEqual(
			TEXT("P10 broken knot has one sink"),
			static_cast<int32>((*BrokenEvidence)->GetArrayField(TEXT("outputSinks")).Num()),
			1);
		if (!TestTrue(
			TEXT("P10 broken preflight is written"),
			WriteJson(EvidencePath(BrokenPreflightFile), BrokenPreflight, Error)))
		{
			AddError(Error);
			return false;
		}

		TSharedRef<FJsonObject> Expected = MakeShared<FJsonObject>();
		Expected->SetStringField(TEXT("schemaVersion"), TEXT("ueak.p10.fixture/1.0"));
		Expected->SetStringField(TEXT("assetPath"), Blueprint->GetPathName());
		Expected->SetStringField(TEXT("graphGuid"), GuidLower(Graph->GraphGuid));
		Expected->SetNumberField(TEXT("nodeCount"), Graph->Nodes.Num());
		Expected->SetStringField(TEXT("packageRevision"), Preflight->GetStringField(TEXT("packageRevision")));
		Expected->SetStringField(TEXT("graphFingerprint"), Preflight->GetStringField(TEXT("graphFingerprint")));
		Expected->SetStringField(TEXT("topologyHash"), Sha1Text(StructuralState(Graph)));
		Expected->SetStringField(TEXT("dllSha256"), Sha256FileText(ModuleDllPath()));
		Expected->SetStringField(TEXT("engineVersion"), FEngineVersion::Current().ToString());
		Expected->SetStringField(TEXT("exportedAtUtc"), FDateTime::UtcNow().ToIso8601());
		TArray<TSharedPtr<FJsonValue>> Nodes;
		for (const UEdGraphNode* Node : Graph->Nodes)
		{
			if (Node == nullptr)
			{
				continue;
			}
			TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
			Item->SetStringField(TEXT("nodeId"), FString(TEXT("n:")) + GuidLower(Node->NodeGuid));
			Item->SetNumberField(TEXT("x"), Node->NodePosX);
			Item->SetNumberField(TEXT("y"), Node->NodePosY);
			Nodes.Add(MakeShared<FJsonValueObject>(Item));
		}
		Expected->SetArrayField(TEXT("nodes"), Nodes);
		Expected->SetStringField(TEXT("contextSha256"), Sha256FileText(EvidencePath(ContextFile)));
		Expected->SetStringField(TEXT("preflightSha256"), Sha256FileText(PreflightPath));
		if (!TestTrue(TEXT("P10 expected-state evidence is written"), WriteJson(EvidencePath(FixtureExpectedFile), Expected, Error)))
		{
			AddError(Error);
			return false;
		}
		return true;
	}

	IMPLEMENT_SIMPLE_AUTOMATION_TEST(
		FUEAgentKitGraphLayoutP10ApplyTest,
		"UEAgentKit.GraphLayout.P10ApplyContract",
		EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

	bool FUEAgentKitGraphLayoutP10ApplyTest::RunTest(const FString& Parameters)
	{
		FString Error;
		UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, FixtureAssetPath);
		if (!TestNotNull(TEXT("P10 fixture is loaded for apply"), Blueprint))
		{
			return false;
		}
		UEdGraph* Graph = GetPrimaryGraph(Blueprint);
		if (!TestNotNull(TEXT("P10 fixture graph is present"), Graph))
		{
			return false;
		}
		TSharedPtr<FJsonObject> Request = ReadJsonFile(EvidencePath(ApplyRequestFile), Error);
		if (!TestTrue(TEXT("P10 canonical apply request exists"), Request.IsValid()))
		{
			AddError(Error);
			return false;
		}
		const FString RevisionBeforeApply = Request->GetStringField(TEXT("expectedPackageRevision"));
		const TSharedPtr<FJsonObject>* PlanField = nullptr;
		const TArray<TSharedPtr<FJsonValue>>* Positions = nullptr;
		if (!Request->TryGetObjectField(TEXT("plan"), PlanField)
			|| PlanField == nullptr
			|| !PlanField->IsValid()
			|| !(*PlanField)->TryGetArrayField(TEXT("positions"), Positions)
			|| Positions == nullptr)
		{
			AddError(TEXT("P10 apply request has no plan.positions."));
			return false;
		}

		TMap<FGuid, FIntPoint> Before;
		TMap<FGuid, FIntPoint> After;
		for (const UEdGraphNode* Node : Graph->Nodes)
		{
			if (Node != nullptr)
			{
				Before.Add(Node->NodeGuid, FIntPoint(Node->NodePosX, Node->NodePosY));
			}
		}
		for (const TSharedPtr<FJsonValue>& Entry : *Positions)
		{
			const TSharedPtr<FJsonObject> Position = Entry.IsValid() ? Entry->AsObject() : nullptr;
			FGuid Guid;
			if (!Position.IsValid()
				|| !FGuid::Parse(Position->GetStringField(TEXT("nodeId")).RightChop(2), Guid))
			{
				AddError(TEXT("P10 plan position is malformed."));
				return false;
			}
			After.Add(Guid, FIntPoint(
				static_cast<int32>(Position->GetNumberField(TEXT("x"))),
				static_cast<int32>(Position->GetNumberField(TEXT("y")))));
		}
		const FString StructureBefore = StructuralState(Graph);

		TSharedPtr<FJsonObject> Result;
		FString ErrorCode;
		FString ErrorMessage;
		if (!TestTrue(
			TEXT("P10 fan-out/merge apply succeeds"),
			UEAgentKitBlueprintGraphLayout::ApplyToGraph(
				Blueprint, Graph, Request, Result, ErrorCode, ErrorMessage)))
		{
			AddError(FString::Printf(TEXT("P10 apply failed: %s %s"), *ErrorCode, *ErrorMessage));
			return false;
		}
		const int32 MovedCount = static_cast<int32>(Result->GetNumberField(TEXT("movedNodeCount")));
		TestTrue(TEXT("P10 apply moved nodes"), MovedCount > 0);
		TestTrue(TEXT("P10 single transaction"), Result->GetBoolField(TEXT("singleTransaction")));
		TestTrue(TEXT("P10 positions equal the plan"), PositionsEqual(Graph, After));
		const bool bStructureHeld = StructuralState(Graph).Equals(StructureBefore, ESearchCase::CaseSensitive);
		TestTrue(TEXT("P10 topology is unchanged by apply"), bStructureHeld);

		const bool bUndoCalled = GEditor != nullptr && GEditor->UndoTransaction();
		const bool bUndoPositions = PositionsEqual(Graph, Before);
		const bool bUndoStructure = StructuralState(Graph).Equals(StructureBefore, ESearchCase::CaseSensitive);
		TestTrue(TEXT("P10 Undo restores exact positions"), bUndoCalled && bUndoPositions);
		TestTrue(TEXT("P10 Undo preserves topology"), bUndoStructure);

		const bool bRedoCalled = bUndoCalled && GEditor != nullptr && GEditor->RedoTransaction();
		const bool bRedoPositions = PositionsEqual(Graph, After);
		const bool bRedoStructure = StructuralState(Graph).Equals(StructureBefore, ESearchCase::CaseSensitive);
		TestTrue(TEXT("P10 Redo restores the target positions"), bRedoCalled && bRedoPositions);
		TestTrue(TEXT("P10 Redo preserves topology"), bRedoStructure);

		if (!TestTrue(TEXT("P10 explicit Save succeeds"), SaveBlueprint(Blueprint, Error)))
		{
			AddError(Error);
			return false;
		}
		const bool bCleanAfterSave = !Blueprint->GetOutermost()->IsDirty();
		TestTrue(TEXT("P10 Save leaves a clean package"), bCleanAfterSave);

		TSharedPtr<FJsonObject> PostSavePreflight;
		if (!TestTrue(
			TEXT("P10 post-save preflight succeeds"),
			UEAgentKitBlueprintGraphLayout::BuildPreflight(
				Blueprint, Graph, PostSavePreflight, ErrorCode, ErrorMessage)))
		{
			AddError(FString::Printf(TEXT("P10 post-save preflight failed: %s %s"), *ErrorCode, *ErrorMessage));
			return false;
		}
		const FString RevisionAfterSave = PostSavePreflight->GetStringField(TEXT("packageRevision"));
		TestTrue(
			TEXT("P10 save changed the package revision"),
			!RevisionAfterSave.Equals(RevisionBeforeApply, ESearchCase::CaseSensitive));

		// Negative: the tiny broken-knot asset must be rejected by the live C++
		// gate, with no position change and no dirt.
		TSharedPtr<FJsonObject> Negative = ReadJsonFile(EvidencePath(NegativeRequestFile), Error);
		bool bNegativeRejected = false;
		bool bNegativeUntouched = false;
		FString NegativeCode;
		if (Negative.IsValid())
		{
			UBlueprint* BrokenBlueprint = LoadObject<UBlueprint>(nullptr, BrokenAssetPath);
			UEdGraph* BrokenGraph = BrokenBlueprint != nullptr ? GetPrimaryGraph(BrokenBlueprint) : nullptr;
			if (!TestNotNull(TEXT("P10 broken fixture loads for the negative gate"), BrokenBlueprint)
				|| !TestNotNull(TEXT("P10 broken fixture graph loads for the negative gate"), BrokenGraph))
			{
				AddError(TEXT("The broken fixture is unavailable; the negative gate cannot run."));
				NegativeCode = TEXT("fixture-missing");
			}
			else
			{
				TSharedPtr<FJsonObject> BrokenPreflight;
				if (!TestTrue(
					TEXT("P10 broken preflight succeeds before the negative gate"),
					UEAgentKitBlueprintGraphLayout::BuildPreflight(
						BrokenBlueprint, BrokenGraph, BrokenPreflight, ErrorCode, ErrorMessage)))
				{
					AddError(FString::Printf(TEXT("P10 broken preflight failed: %s %s"), *ErrorCode, *ErrorMessage));
					return false;
				}
				Negative->SetStringField(
					TEXT("expectedPackageRevision"),
					BrokenPreflight->GetStringField(TEXT("packageRevision")));
				Negative->SetStringField(
					TEXT("expectedGraphFingerprint"),
					BrokenPreflight->GetStringField(TEXT("graphFingerprint")));
				TMap<FGuid, FIntPoint> BeforeNegative;
				for (const UEdGraphNode* Node : BrokenGraph->Nodes)
				{
					if (Node != nullptr)
					{
						BeforeNegative.Add(Node->NodeGuid, FIntPoint(Node->NodePosX, Node->NodePosY));
					}
				}
				TSharedPtr<FJsonObject> NegativeResult;
				const bool bNegativeApplied = UEAgentKitBlueprintGraphLayout::ApplyToGraph(
					BrokenBlueprint, BrokenGraph, Negative, NegativeResult, NegativeCode, ErrorMessage);
				bNegativeRejected = !bNegativeApplied
					&& NegativeCode.Equals(TEXT("graph-layout-reroute-broken"), ESearchCase::CaseSensitive);
				bNegativeUntouched = PositionsEqual(BrokenGraph, BeforeNegative)
					&& !BrokenBlueprint->GetOutermost()->IsDirty();
				TestTrue(TEXT("P10 broken knot is rejected by the live gate"), bNegativeRejected);
				TestTrue(TEXT("P10 rejected apply changed nothing"), bNegativeUntouched);
			}
		}
		else
		{
			AddError(Error);
		}

		TSharedRef<FJsonObject> Evidence = MakeShared<FJsonObject>();
		Evidence->SetStringField(TEXT("schemaVersion"), TEXT("ueak.p10.apply/1.0"));
		Evidence->SetStringField(TEXT("test"), TEXT("UEAgentKit.GraphLayout.P10ApplyContract"));
		Evidence->SetStringField(TEXT("assetPath"), Blueprint->GetPathName());
		Evidence->SetStringField(TEXT("graphGuid"), GuidLower(Graph->GraphGuid));
		Evidence->SetNumberField(TEXT("movedNodeCount"), MovedCount);
		Evidence->SetBoolField(TEXT("topologyHeld"), bStructureHeld);
		Evidence->SetBoolField(TEXT("undoCalled"), bUndoCalled);
		Evidence->SetBoolField(TEXT("undoPositions"), bUndoPositions);
		Evidence->SetBoolField(TEXT("undoVerified"), bUndoCalled && bUndoPositions && bUndoStructure);
		Evidence->SetBoolField(TEXT("redoCalled"), bRedoCalled);
		Evidence->SetBoolField(TEXT("redoPositions"), bRedoPositions);
		Evidence->SetBoolField(TEXT("redoVerified"), bRedoCalled && bRedoPositions && bRedoStructure);
		Evidence->SetBoolField(TEXT("saved"), bCleanAfterSave);
		Evidence->SetStringField(TEXT("packageRevisionAfterSave"), RevisionAfterSave);
		Evidence->SetStringField(TEXT("topologyHashBefore"), Sha1Text(StructureBefore));
		Evidence->SetStringField(TEXT("topologyHashAfter"), Sha1Text(StructuralState(Graph)));
		Evidence->SetStringField(TEXT("negativeErrorCode"), NegativeCode);
		Evidence->SetBoolField(TEXT("negativeRejected"), bNegativeRejected);
		Evidence->SetBoolField(TEXT("negativeUntouched"), bNegativeUntouched);
		Evidence->SetStringField(TEXT("dllSha256"), Sha256FileText(ModuleDllPath()));
		Evidence->SetStringField(TEXT("savedAtUtc"), FDateTime::UtcNow().ToIso8601());
		TArray<TSharedPtr<FJsonValue>> NodeStates;
		for (const UEdGraphNode* Node : Graph->Nodes)
		{
			if (Node == nullptr)
			{
				continue;
			}
			TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
			Item->SetStringField(TEXT("nodeId"), FString(TEXT("n:")) + GuidLower(Node->NodeGuid));
			Item->SetNumberField(TEXT("x"), Node->NodePosX);
			Item->SetNumberField(TEXT("y"), Node->NodePosY);
			NodeStates.Add(MakeShared<FJsonValueObject>(Item));
		}
		Evidence->SetArrayField(TEXT("positions"), NodeStates);
		if (!TestTrue(TEXT("P10 apply evidence is written"), WriteJson(EvidencePath(ApplyResultFile), Evidence, Error)))
		{
			AddError(Error);
			return false;
		}
		return bStructureHeld && bUndoCalled && bUndoPositions && bRedoCalled && bRedoPositions
			&& bNegativeRejected && bNegativeUntouched;
	}

	IMPLEMENT_SIMPLE_AUTOMATION_TEST(
		FUEAgentKitGraphLayoutP10ReloadTest,
		"UEAgentKit.GraphLayout.P10ReloadContract",
		EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

	bool FUEAgentKitGraphLayoutP10ReloadTest::RunTest(const FString& Parameters)
	{
		FString Error;
		TSharedPtr<FJsonObject> ApplyResult = ReadJsonFile(EvidencePath(ApplyResultFile), Error);
		if (!TestTrue(TEXT("P10 apply evidence exists for reload"), ApplyResult.IsValid()))
		{
			AddError(Error);
			return false;
		}
		UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, FixtureAssetPath);
		if (!TestNotNull(TEXT("P10 fixture reloads in a fresh process"), Blueprint))
		{
			return false;
		}
		UEdGraph* Graph = GetPrimaryGraph(Blueprint);
		if (!TestNotNull(TEXT("P10 reloaded graph is present"), Graph))
		{
			return false;
		}
		TMap<FGuid, FIntPoint> Expected;
		for (const TSharedPtr<FJsonValue>& Entry : ApplyResult->GetArrayField(TEXT("positions")))
		{
			const TSharedPtr<FJsonObject> Item = Entry->AsObject();
			FGuid Guid;
			if (!FGuid::Parse(Item->GetStringField(TEXT("nodeId")).RightChop(2), Guid))
			{
				AddError(TEXT("P10 reload evidence has a malformed nodeId."));
				return false;
			}
			Expected.Add(Guid, FIntPoint(
				static_cast<int32>(Item->GetNumberField(TEXT("x"))),
				static_cast<int32>(Item->GetNumberField(TEXT("y")))));
		}
		const bool bPositions = PositionsEqual(Graph, Expected);
		const bool bTopology = Sha1Text(StructuralState(Graph)).Equals(
			ApplyResult->GetStringField(TEXT("topologyHashAfter")), ESearchCase::CaseSensitive);

		TSharedPtr<FJsonObject> Preflight;
		FString ErrorCode;
		FString ErrorMessage;
		if (!TestTrue(
			TEXT("P10 reload preflight succeeds"),
			UEAgentKitBlueprintGraphLayout::BuildPreflight(Blueprint, Graph, Preflight, ErrorCode, ErrorMessage)))
		{
			AddError(FString::Printf(TEXT("P10 reload preflight failed: %s %s"), *ErrorCode, *ErrorMessage));
			return false;
		}
		const bool bRevision = Preflight->GetStringField(TEXT("packageRevision")).Equals(
			ApplyResult->GetStringField(TEXT("packageRevisionAfterSave")), ESearchCase::CaseSensitive);
		const bool bDll = Sha256FileText(ModuleDllPath()).Equals(
			ApplyResult->GetStringField(TEXT("dllSha256")), ESearchCase::CaseSensitive);
		TestTrue(TEXT("P10 reload restores exact positions"), bPositions);
		TestTrue(TEXT("P10 reload preserves topology"), bTopology);
		TestTrue(TEXT("P10 reload revision matches the saved revision"), bRevision);
		TestTrue(TEXT("P10 reload uses the same DLL hash"), bDll);

		TSharedRef<FJsonObject> Evidence = MakeShared<FJsonObject>();
		Evidence->SetStringField(TEXT("schemaVersion"), TEXT("ueak.p10.reload/1.0"));
		Evidence->SetStringField(TEXT("test"), TEXT("UEAgentKit.GraphLayout.P10ReloadContract"));
		Evidence->SetStringField(TEXT("assetPath"), Blueprint->GetPathName());
		Evidence->SetStringField(TEXT("graphGuid"), GuidLower(Graph->GraphGuid));
		Evidence->SetBoolField(TEXT("positionsVerified"), bPositions);
		Evidence->SetBoolField(TEXT("topologyVerified"), bTopology);
		Evidence->SetBoolField(TEXT("revisionVerified"), bRevision);
		Evidence->SetBoolField(TEXT("dllVerified"), bDll);
		Evidence->SetBoolField(TEXT("packageDirty"), Blueprint->GetOutermost()->IsDirty());
		Evidence->SetStringField(TEXT("packageRevision"), Preflight->GetStringField(TEXT("packageRevision")));
		Evidence->SetStringField(TEXT("topologyHash"), Sha1Text(StructuralState(Graph)));
		Evidence->SetStringField(TEXT("dllSha256"), Sha256FileText(ModuleDllPath()));
		Evidence->SetStringField(TEXT("engineVersion"), FEngineVersion::Current().ToString());
		Evidence->SetStringField(TEXT("reloadedAtUtc"), FDateTime::UtcNow().ToIso8601());
		if (!TestTrue(TEXT("P10 reload evidence is written"), WriteJson(EvidencePath(ReloadResultFile), Evidence, Error)))
		{
			AddError(Error);
			return false;
		}
		return bPositions && bTopology && bRevision && bDll;
	}
}

#undef LOCTEXT_NAMESPACE

#endif // WITH_DEV_AUTOMATION_TESTS
