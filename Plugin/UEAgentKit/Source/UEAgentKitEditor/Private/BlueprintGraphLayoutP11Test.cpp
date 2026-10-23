// P11 disposable automation: real node/pin menu gating, real selection and
// incremental position-only applies, a strict no-op boundary, Undo/Redo,
// explicit Save and a second-process Reload.
//
// Two identical-topology assets are created so the selection and incremental
// plans can both start from a clean revision and be verified independently.
// Requests are produced offline by tests/python/p11_build_scope_probe.py from
// the real exporter/preflight output; this test never hand-writes positions for
// the two positive cases.

#include "BlueprintContextExporter.h"
#include "BlueprintContextSha256.h"
#include "BlueprintGraphLayoutApply.h"
#include "BlueprintGraphLayoutP11Menu.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "BlueprintEditor.h"
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
#include "K2Node_IfThenElse.h"
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
#include "Misc/Paths.h"
#include "Misc/SecureHash.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "Subsystems/AssetEditorSubsystem.h"
#include "ToolMenu.h"
#include "ToolMenuContext.h"
#include "ToolMenuEntry.h"
#include "ToolMenuSection.h"
#include "ToolMenus.h"
#include "Toolkits/IToolkitHost.h"
#include "ToolMenu.h"
#include "ToolMenus.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"

#if WITH_DEV_AUTOMATION_TESTS

#define LOCTEXT_NAMESPACE "UEAgentKitGraphLayoutP11"

namespace UEAgentKitGraphLayoutP11Test
{
	namespace
	{
		const TCHAR* SelectionPackage = TEXT("/Game/UEAgentKitP11/BP_LayoutP11");
		const TCHAR* SelectionName = TEXT("BP_LayoutP11");
		const TCHAR* SelectionPath = TEXT("/Game/UEAgentKitP11/BP_LayoutP11.BP_LayoutP11");
		const TCHAR* IncrementalPackage = TEXT("/Game/UEAgentKitP11/BP_LayoutP11Inc");
		const TCHAR* IncrementalName = TEXT("BP_LayoutP11Inc");
		const TCHAR* IncrementalPath = TEXT("/Game/UEAgentKitP11/BP_LayoutP11Inc.BP_LayoutP11Inc");

		const TCHAR* SelectionContextFile = TEXT("p11-context.json");
		const TCHAR* SelectionPreflightFile = TEXT("p11-preflight.json");
		const TCHAR* IncrementalContextFile = TEXT("p11-inc-context.json");
		const TCHAR* IncrementalPreflightFile = TEXT("p11-inc-preflight.json");
		const TCHAR* FixtureExpectedFile = TEXT("p11-fixture-expected.json");
		const TCHAR* SelectionRequestFile = TEXT("p11-selection-request.json");
		const TCHAR* IncrementalRequestFile = TEXT("p11-incremental-request.json");
		const TCHAR* ScopeResultFile = TEXT("p11-scope-result.json");
		const TCHAR* ReloadResultFile = TEXT("p11-reload-result.json");

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
							Links.Add(GuidLower(Linked->GetOwningNode()->NodeGuid) + TEXT("/") + Linked->PinName.ToString());
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

		// 15 nodes: BeginPlay -> Sequence (3 branches); two Sequence pins merge
		// through an Exec knot into PrintA; RandomIntegerInRange fans out through
		// a Data knot into two conversions; branch 3 goes to a real IfThenElse
		// with a Greater_IntInt condition; one Comment frames the region.
		UBlueprint* CreateFixture(const TCHAR* PackageName, const TCHAR* AssetName, const TCHAR* FactoryName, FString& OutError)
		{
			UPackage* Package = CreatePackage(PackageName);
			if (Package == nullptr)
			{
				OutError = TEXT("Could not create the P11 disposable package.");
				return nullptr;
			}
			UBlueprint* Blueprint = FKismetEditorUtilities::CreateBlueprint(
				AActor::StaticClass(), Package, FName(AssetName), BPTYPE_Normal, FName(FactoryName));
			if (Blueprint == nullptr)
			{
				OutError = TEXT("Could not create the P11 disposable Blueprint.");
				return nullptr;
			}
			UEdGraph* Graph = GetPrimaryGraph(Blueprint);
			if (Graph == nullptr)
			{
				OutError = TEXT("The P11 Blueprint has no ubergraph.");
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
				OutError = TEXT("The P11 Blueprint has no ReceiveBeginPlay event.");
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
			Sequence->NodePosX = 300;
			Sequence->NodePosY = -40;
			SequenceCreator.Finalize();
			// The default Sequence exposes two exec outputs; the fixture needs a
			// third branch for the IfThenElse path.
			Sequence->AddInputPin();

			FGraphNodeCreator<UK2Node_Knot> ExecKnotCreator(*Graph);
			UK2Node_Knot* ExecKnot = ExecKnotCreator.CreateNode(false);
			ExecKnot->NodePosX = 620;
			ExecKnot->NodePosY = 10;
			ExecKnotCreator.Finalize();

			UK2Node_CallFunction* PrintA = MakeCall(
				Graph, FindFunction(UKismetSystemLibrary::StaticClass(), TEXT("PrintString")), 900, -120);
			UK2Node_CallFunction* RandomInt = MakeCall(
				Graph, FindFunction(UKismetMathLibrary::StaticClass(), TEXT("RandomIntegerInRange")), 260, 300);
			FGraphNodeCreator<UK2Node_Knot> DataKnotCreator(*Graph);
			UK2Node_Knot* DataKnot = DataKnotCreator.CreateNode(false);
			DataKnot->NodePosX = 620;
			DataKnot->NodePosY = 330;
			DataKnotCreator.Finalize();
			UK2Node_CallFunction* Conv1 = MakeCall(
				Graph, FindFunction(UKismetStringLibrary::StaticClass(), TEXT("Conv_IntToString")), 880, 240);
			UK2Node_CallFunction* Conv2 = MakeCall(
				Graph, FindFunction(UKismetStringLibrary::StaticClass(), TEXT("Conv_IntToString")), 880, 240);
			UK2Node_CallFunction* PrintB = MakeCall(
				Graph, FindFunction(UKismetSystemLibrary::StaticClass(), TEXT("PrintString")), 1160, 200);
			UK2Node_CallFunction* PrintC = MakeCall(
				Graph, FindFunction(UKismetSystemLibrary::StaticClass(), TEXT("PrintString")), 1160, 200);
			UK2Node_CallFunction* Greater = MakeCall(
				Graph, FindFunction(UKismetMathLibrary::StaticClass(), TEXT("Greater_IntInt")), 620, 560);
			FGraphNodeCreator<UK2Node_IfThenElse> BranchCreator(*Graph);
			UK2Node_IfThenElse* Branch = BranchCreator.CreateNode(false);
			Branch->NodePosX = 900;
			Branch->NodePosY = 620;
			BranchCreator.Finalize();
			UK2Node_CallFunction* PrintTrue = MakeCall(
				Graph, FindFunction(UKismetSystemLibrary::StaticClass(), TEXT("PrintString")), 1180, 600);
			UK2Node_CallFunction* PrintFalse = MakeCall(
				Graph, FindFunction(UKismetSystemLibrary::StaticClass(), TEXT("PrintString")), 1180, 760);

			if (Sequence == nullptr || ExecKnot == nullptr || DataKnot == nullptr || Branch == nullptr
				|| PrintA == nullptr || PrintB == nullptr || PrintC == nullptr || PrintTrue == nullptr
				|| PrintFalse == nullptr || RandomInt == nullptr || Conv1 == nullptr || Conv2 == nullptr
				|| Greater == nullptr)
			{
				OutError = TEXT("Could not create one of the P11 fixture nodes.");
				return nullptr;
			}

			UEdGraphPin* EventThen = BeginPlay->FindPin(UEdGraphSchema_K2::PN_Then, EGPD_Output);
			UEdGraphPin* SequenceExecute = Sequence->FindPin(UEdGraphSchema_K2::PN_Execute, EGPD_Input);
			UEdGraphPin* RandomReturn = RandomInt->FindPin(TEXT("ReturnValue"), EGPD_Output);
			if (EventThen == nullptr || SequenceExecute == nullptr || RandomReturn == nullptr
				|| !LinkPins(Graph, EventThen, SequenceExecute)
				|| !LinkPins(Graph, Sequence->GetThenPinGivenIndex(0), ExecKnot->GetInputPin())
				|| !LinkPins(Graph, Sequence->GetThenPinGivenIndex(1), ExecKnot->GetInputPin())
				|| !LinkPins(Graph, ExecKnot->GetOutputPin(), PrintA->FindPin(UEdGraphSchema_K2::PN_Execute, EGPD_Input))
				|| !LinkPins(Graph, RandomReturn, DataKnot->GetInputPin())
				|| !LinkPins(Graph, DataKnot->GetOutputPin(), Conv1->FindPin(TEXT("InInt"), EGPD_Input))
				|| !LinkPins(Graph, DataKnot->GetOutputPin(), Conv2->FindPin(TEXT("InInt"), EGPD_Input))
				|| !LinkPins(Graph, Conv1->FindPin(TEXT("ReturnValue"), EGPD_Output), PrintB->FindPin(TEXT("InString"), EGPD_Input))
				|| !LinkPins(Graph, Conv2->FindPin(TEXT("ReturnValue"), EGPD_Output), PrintC->FindPin(TEXT("InString"), EGPD_Input))
				|| !LinkPins(Graph, RandomReturn, Greater->FindPin(TEXT("A"), EGPD_Input))
				|| !LinkPins(Graph, Greater->FindPin(TEXT("ReturnValue"), EGPD_Output), Branch->FindPin(TEXT("Condition"), EGPD_Input))
				|| !LinkPins(Graph, Sequence->GetThenPinGivenIndex(2), Branch->FindPin(UEdGraphSchema_K2::PN_Execute, EGPD_Input))
				|| !LinkPins(Graph, Branch->FindPin(UEdGraphSchema_K2::PN_Then, EGPD_Output), PrintTrue->FindPin(UEdGraphSchema_K2::PN_Execute, EGPD_Input))
				|| !LinkPins(Graph, Branch->FindPin(UEdGraphSchema_K2::PN_Else, EGPD_Output), PrintFalse->FindPin(UEdGraphSchema_K2::PN_Execute, EGPD_Input)))
			{
				OutError = TEXT("Could not create the P11 branch/fan-out/merge topology.");
				return nullptr;
			}

			UEdGraphNode_Comment* Comment = NewObject<UEdGraphNode_Comment>(Graph, NAME_None, RF_Transactional);
			Comment->NodePosX = 200;
			Comment->NodePosY = 180;
			Comment->NodeWidth = 1300;
			Comment->NodeHeight = 650;
			Comment->NodeComment = TEXT("P11 selection and incremental scope region");
			Comment->MoveMode = ECommentBoxMode::NoGroupMovement;
			Comment->CreateNewGuid();
			Graph->AddNode(Comment, true, false);
			Comment->AllocateDefaultPins();

			FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
			FKismetEditorUtilities::CompileBlueprint(Blueprint, EBlueprintCompileOptions::SkipGarbageCollection);
			if (Blueprint->Status == BS_Error)
			{
				OutError = TEXT("The P11 fixture Blueprint failed to compile.");
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

		UBlueprint* LoadOrCreate(
			const TCHAR* Path,
			const TCHAR* PackageName,
			const TCHAR* AssetName,
			const TCHAR* FactoryName,
			FString& OutError)
		{
			if (UBlueprint* Existing = LoadObject<UBlueprint>(nullptr, Path))
			{
				return Existing;
			}
			return CreateFixture(PackageName, AssetName, FactoryName, OutError);
		}

		bool ExportContext(UBlueprint* Blueprint, UEdGraph* Graph, const TCHAR* ContextFile, FString& OutError)
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
				OutError = TEXT("Could not read the exporter JSON.");
				return false;
			}
			const FString Target = EvidencePath(ContextFile);
			return FFileHelper::SaveStringToFile(
				ContextText, *Target, FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);
		}

		bool PositionsEqual(UEdGraph* Graph, const TMap<FGuid, FIntPoint>& Expected)
		{
			for (const TPair<FGuid, FIntPoint>& Pair : Expected)
			{
				bool bFound = false;
				for (UEdGraphNode* Candidate : Graph->Nodes)
				{
					if (Candidate != nullptr && Candidate->NodeGuid == Pair.Key)
					{
						bFound = Candidate->NodePosX == Pair.Value.X && Candidate->NodePosY == Pair.Value.Y;
						break;
					}
				}
				if (!bFound)
				{
					return false;
				}
			}
			return true;
		}

		TMap<FGuid, FIntPoint> CurrentPositions(UEdGraph* Graph)
		{
			TMap<FGuid, FIntPoint> Map;
			for (const UEdGraphNode* Node : Graph->Nodes)
			{
				if (Node != nullptr)
				{
					Map.Add(Node->NodeGuid, FIntPoint(Node->NodePosX, Node->NodePosY));
				}
			}
			return Map;
		}

		TMap<FGuid, FIntPoint> RequestPositions(const TSharedPtr<FJsonObject>& Request)
		{
			TMap<FGuid, FIntPoint> Map;
			const TSharedPtr<FJsonObject>* Plan = nullptr;
			const TArray<TSharedPtr<FJsonValue>>* Positions = nullptr;
			if (!Request->TryGetObjectField(TEXT("plan"), Plan) || Plan == nullptr
				|| !(*Plan)->TryGetArrayField(TEXT("positions"), Positions) || Positions == nullptr)
			{
				return Map;
			}
			for (const TSharedPtr<FJsonValue>& Entry : *Positions)
			{
				const TSharedPtr<FJsonObject> Item = Entry->AsObject();
				FGuid Guid;
				if (Item.IsValid() && FGuid::Parse(Item->GetStringField(TEXT("nodeId")).RightChop(2), Guid))
				{
					Map.Add(Guid, FIntPoint(
						static_cast<int32>(Item->GetNumberField(TEXT("x"))),
						static_cast<int32>(Item->GetNumberField(TEXT("y")))));
				}
			}
			return Map;
		}

		FBlueprintEditor* OpenAndFocusEditor(UBlueprint* Blueprint, UEdGraph* Graph, FString& OutError)
		{
			if (GEditor == nullptr)
			{
				OutError = TEXT("GEditor is unavailable.");
				return nullptr;
			}
			UAssetEditorSubsystem* Subsystem = GEditor->GetEditorSubsystem<UAssetEditorSubsystem>();
			if (Subsystem == nullptr)
			{
				OutError = TEXT("The Asset Editor Subsystem is unavailable.");
				return nullptr;
			}
			if (!Subsystem->OpenEditorForAsset(
				Blueprint,
				EToolkitMode::Standalone,
				TSharedPtr<IToolkitHost>(),
				false))
			{
				OutError = TEXT("OpenEditorForAsset returned false.");
				return nullptr;
			}
			IAssetEditorInstance* Instance = Subsystem->FindEditorForAsset(Blueprint, false);
			if (Instance == nullptr || Instance->GetEditorName() != FName(TEXT("BlueprintEditor")))
			{
				OutError = TEXT("FindEditorForAsset did not return a BlueprintEditor.");
				return nullptr;
			}
			FBlueprintEditor* Editor = static_cast<FBlueprintEditor*>(Instance);
			if (Editor->GetBlueprintObj() != Blueprint)
			{
				OutError = TEXT("The Blueprint Editor does not own the fixture.");
				return nullptr;
			}
			if (!Editor->OpenGraphAndBringToFront(Graph, true).IsValid())
			{
				OutError = TEXT("OpenGraphAndBringToFront returned an invalid graph editor.");
				return nullptr;
			}
			if (Editor->GetFocusedGraph() != Graph)
			{
				OutError = TEXT("The fixture graph is not focused.");
				return nullptr;
			}
			return Editor;
		}

		FString P11ReceiptPath()
		{
			return FPaths::Combine(
				FPaths::ProjectSavedDir(),
				TEXT("UEAgentKit"),
				TEXT("P11Preview"),
				TEXT("menu-invocation.json"));
		}

		UToolMenu* GenerateGraphMenu(
			UEdGraph* Graph,
			UEdGraphNode* Node,
			UEdGraphPin* Pin,
			FToolMenuContext& OutContext)
		{
			UGraphNodeContextMenuContext* Context = NewObject<UGraphNodeContextMenuContext>();
			Context->Init(Graph, Node, Pin, false);
			OutContext = FToolMenuContext(TSharedPtr<FUICommandList>(), TSharedPtr<FExtender>(), Context);
			UToolMenus* ToolMenus = UToolMenus::Get();
			if (ToolMenus == nullptr)
			{
				return nullptr;
			}
			// The real SGraphEditor registers the common root before generating a
			// context menu; mirror that headlessly so the hierarchy is complete.
			ToolMenus->RegisterMenu(TEXT("GraphEditor.GraphContextMenu.Common"));
			return ToolMenus->GenerateMenu(TEXT("GraphEditor.GraphContextMenu.Common"), OutContext);
		}

		FToolMenuEntry* FindP11Entry(UToolMenu* Menu)
		{
			if (Menu == nullptr)
			{
				return nullptr;
			}
			FToolMenuSection* Section = Menu->FindSection(TEXT("UEAgentKitGraphLayoutP11"));
			return Section != nullptr ? Section->FindEntry(TEXT("UEAgentKitAutoArrangePreviewP11")) : nullptr;
		}
	}

	IMPLEMENT_SIMPLE_AUTOMATION_TEST(
		FUEAgentKitGraphLayoutP11FixtureTest,
		"UEAgentKit.GraphLayout.P11FixtureContract",
		EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

	bool FUEAgentKitGraphLayoutP11FixtureTest::RunTest(const FString& Parameters)
	{
		FString Error;
		UBlueprint* Selection = LoadOrCreate(
			SelectionPath, SelectionPackage, SelectionName, TEXT("UEAgentKitP11SelectionFixture"), Error);
		UBlueprint* Incremental = LoadOrCreate(
			IncrementalPath, IncrementalPackage, IncrementalName, TEXT("UEAgentKitP11IncrementalFixture"), Error);
		if (!TestNotNull(TEXT("P11 selection fixture is loaded"), Selection)
			|| !TestNotNull(TEXT("P11 incremental fixture is loaded"), Incremental))
		{
			AddError(Error);
			return false;
		}
		UEdGraph* SelectionGraph = GetPrimaryGraph(Selection);
		UEdGraph* IncrementalGraph = GetPrimaryGraph(Incremental);
		if (!TestNotNull(TEXT("P11 selection graph is present"), SelectionGraph)
			|| !TestNotNull(TEXT("P11 incremental graph is present"), IncrementalGraph))
		{
			return false;
		}
		TestEqual(TEXT("P11 fixture has fifteen nodes"), SelectionGraph->Nodes.Num(), 15);

		TSharedPtr<FJsonObject> SelectionPreflight;
		TSharedPtr<FJsonObject> IncrementalPreflight;
		FString ErrorCode;
		FString ErrorMessage;
		if (!TestTrue(
			TEXT("P11 selection preflight succeeds"),
			UEAgentKitBlueprintGraphLayout::BuildPreflight(
				Selection, SelectionGraph, SelectionPreflight, ErrorCode, ErrorMessage)))
		{
			AddError(FString::Printf(TEXT("P11 selection preflight failed: %s %s"), *ErrorCode, *ErrorMessage));
			return false;
		}
		if (!TestTrue(
			TEXT("P11 incremental preflight succeeds"),
			UEAgentKitBlueprintGraphLayout::BuildPreflight(
				Incremental, IncrementalGraph, IncrementalPreflight, ErrorCode, ErrorMessage)))
		{
			AddError(FString::Printf(TEXT("P11 incremental preflight failed: %s %s"), *ErrorCode, *ErrorMessage));
			return false;
		}
		TestEqual(TEXT("P11 fixture has two reroutes"), SelectionPreflight->GetNumberField(TEXT("rerouteCount")), 2.0);
		TestEqual(TEXT("P11 fixture has one comment"), SelectionPreflight->GetNumberField(TEXT("commentCount")), 1.0);

		if (!TestTrue(TEXT("P11 selection export succeeds"), ExportContext(Selection, SelectionGraph, SelectionContextFile, Error))
			|| !TestTrue(TEXT("P11 incremental export succeeds"), ExportContext(Incremental, IncrementalGraph, IncrementalContextFile, Error)))
		{
			AddError(Error);
			return false;
		}
		const FString SelectionPreflightPath = EvidencePath(SelectionPreflightFile);
		const FString IncrementalPreflightPath = EvidencePath(IncrementalPreflightFile);
		if (!TestTrue(TEXT("P11 selection preflight is written"), WriteJson(SelectionPreflightPath, SelectionPreflight, Error))
			|| !TestTrue(TEXT("P11 incremental preflight is written"), WriteJson(IncrementalPreflightPath, IncrementalPreflight, Error)))
		{
			AddError(Error);
			return false;
		}

		TSharedRef<FJsonObject> Expected = MakeShared<FJsonObject>();
		Expected->SetStringField(TEXT("schemaVersion"), TEXT("ueak.p11.fixture/1.0"));
		Expected->SetNumberField(TEXT("nodeCount"), SelectionGraph->Nodes.Num());
		Expected->SetStringField(TEXT("selectionAsset"), Selection->GetPathName());
		Expected->SetStringField(TEXT("selectionGraphGuid"), GuidLower(SelectionGraph->GraphGuid));
		Expected->SetStringField(TEXT("selectionRevision"), SelectionPreflight->GetStringField(TEXT("packageRevision")));
		Expected->SetStringField(TEXT("incrementalAsset"), Incremental->GetPathName());
		Expected->SetStringField(TEXT("incrementalGraphGuid"), GuidLower(IncrementalGraph->GraphGuid));
		Expected->SetStringField(TEXT("incrementalRevision"), IncrementalPreflight->GetStringField(TEXT("packageRevision")));
		Expected->SetStringField(TEXT("selectionTopologyHash"), Sha1Text(StructuralState(SelectionGraph)));
		Expected->SetStringField(TEXT("incrementalTopologyHash"), Sha1Text(StructuralState(IncrementalGraph)));
		Expected->SetStringField(TEXT("dllSha256"), Sha256FileText(ModuleDllPath()));
		Expected->SetStringField(TEXT("engineVersion"), FEngineVersion::Current().ToString());
		Expected->SetStringField(TEXT("exportedAtUtc"), FDateTime::UtcNow().ToIso8601());
		Expected->SetStringField(TEXT("selectionContextSha256"), Sha256FileText(EvidencePath(SelectionContextFile)));
		Expected->SetStringField(TEXT("incrementalContextSha256"), Sha256FileText(EvidencePath(IncrementalContextFile)));
		if (!TestTrue(TEXT("P11 fixture evidence is written"), WriteJson(EvidencePath(FixtureExpectedFile), Expected, Error)))
		{
			AddError(Error);
			return false;
		}

		// Menu registration seam: the P11 entry is gated by project + marker and
		// must require a node or pin context.  This is not a GUI click.
		TestTrue(TEXT("P11 menu gate is open in this host"), FUEAgentKitGraphLayoutP11Menu::IsAvailable());
		return true;
	}

	IMPLEMENT_SIMPLE_AUTOMATION_TEST(
		FUEAgentKitGraphLayoutP11MenuTest,
		"UEAgentKit.GraphLayout.P11MenuContract",
		EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

	bool FUEAgentKitGraphLayoutP11MenuTest::RunTest(const FString& Parameters)
	{
		FString Error;
		UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, SelectionPath);
		UEdGraph* Graph = Blueprint != nullptr ? GetPrimaryGraph(Blueprint) : nullptr;
		if (!TestNotNull(TEXT("P11 menu fixture loads"), Blueprint)
			|| !TestNotNull(TEXT("P11 menu fixture graph loads"), Graph))
		{
			return false;
		}

		UEdGraphNode* ContextNode = nullptr;
		UEdGraphPin* ContextPin = nullptr;
		for (UEdGraphNode* Node : Graph->Nodes)
		{
			if (Node == nullptr)
			{
				continue;
			}
			for (UEdGraphPin* Pin : Node->Pins)
			{
				if (Pin != nullptr && Pin->Direction == EGPD_Input && Pin->PinName == FName(TEXT("InString")))
				{
					ContextNode = Node;
					ContextPin = Pin;
					break;
				}
			}
			if (ContextNode != nullptr)
			{
				break;
			}
		}
		if (!TestNotNull(TEXT("P11 menu context node exists"), ContextNode)
			|| !TestNotNull(TEXT("P11 menu context pin exists"), ContextPin))
		{
			return false;
		}

		FBlueprintEditor* Editor = OpenAndFocusEditor(Blueprint, Graph, Error);
		if (!TestNotNull(TEXT("P11 Blueprint Editor opens headlessly"), Editor))
		{
			AddError(Error);
			return false;
		}
		if (!TestFalse(TEXT("P11 fixture is clean before the menu test"), Blueprint->GetOutermost()->IsDirty()))
		{
			return false;
		}

		TestTrue(TEXT("P11 menu gate is open in this host"), FUEAgentKitGraphLayoutP11Menu::IsAvailable());
		TestFalse(
			TEXT("P11 menu rejects a wrong project"),
			FUEAgentKitGraphLayoutP11Menu::IsAvailableFor(TEXT("OtherHost"), true));
		TestFalse(
			TEXT("P11 menu rejects a missing marker"),
			FUEAgentKitGraphLayoutP11Menu::IsAvailableFor(TEXT("P11AutomationHost"), false));

		FToolMenuContext NodeContext;
		UToolMenu* NodeMenu = GenerateGraphMenu(Graph, ContextNode, nullptr, NodeContext);
		FToolMenuEntry* NodeEntry = FindP11Entry(NodeMenu);
		TestTrue(TEXT("P11 node context menu contains the generated section and entry"), NodeEntry != nullptr);

		FToolMenuContext PinContext;
		UToolMenu* PinMenu = GenerateGraphMenu(Graph, ContextNode, ContextPin, PinContext);
		FToolMenuEntry* PinEntry = FindP11Entry(PinMenu);
		TestTrue(TEXT("P11 pin context menu contains the generated section and entry"), PinEntry != nullptr);

		FToolMenuContext BlankContext;
		UToolMenu* BlankMenu = GenerateGraphMenu(Graph, nullptr, nullptr, BlankContext);
		TestNull(
			TEXT("P11 blank canvas menu has no P11 section"),
			BlankMenu != nullptr ? BlankMenu->FindSection(TEXT("UEAgentKitGraphLayoutP11")) : nullptr);

		if (NodeEntry == nullptr || PinEntry == nullptr)
		{
			return false;
		}

		FUEAgentKitGraphLayoutP11Menu::SetTestDialogSuppression(true);
		IFileManager::Get().Delete(*P11ReceiptPath(), false, true, true);
		const bool bNodeInvoked = NodeEntry->TryExecuteToolUIAction(NodeContext);
		TSharedPtr<FJsonObject> NodeReceipt = ReadJsonFile(P11ReceiptPath(), Error);
		FUEAgentKitGraphLayoutP11Menu::SetTestDialogSuppression(false);

		TestTrue(TEXT("P11 node menu action executes"), bNodeInvoked);
		if (!TestTrue(TEXT("P11 node action wrote a receipt"), NodeReceipt.IsValid()))
		{
			AddError(Error);
			return false;
		}
		TestTrue(TEXT("P11 node receipt ok"), NodeReceipt->GetBoolField(TEXT("ok")));
		TestTrue(TEXT("P11 node receipt produced a plan"), NodeReceipt->GetBoolField(TEXT("planProduced")));
		TestTrue(TEXT("P11 node receipt is read-only"), NodeReceipt->GetBoolField(TEXT("readOnly")));
		TestFalse(TEXT("P11 node receipt did not save"), NodeReceipt->GetBoolField(TEXT("saved")));
		TestFalse(TEXT("P11 node receipt did not compile"), NodeReceipt->GetBoolField(TEXT("compiled")));
		TestFalse(TEXT("P11 node receipt reports clean before"), NodeReceipt->GetBoolField(TEXT("packageDirtyBefore")));
		TestFalse(TEXT("P11 node receipt reports clean after"), NodeReceipt->GetBoolField(TEXT("packageDirtyAfter")));
		TestEqual(TEXT("P11 node context kind"), NodeReceipt->GetStringField(TEXT("contextKind")), FString(TEXT("node")));
		TestEqual(
			TEXT("P11 node context guid"),
			NodeReceipt->GetStringField(TEXT("contextNodeGuid")),
			GuidLower(ContextNode->NodeGuid));
		TestEqual(TEXT("P11 node receipt asset"), NodeReceipt->GetStringField(TEXT("assetPath")), Blueprint->GetPathName());
		TestEqual(TEXT("P11 node receipt graph"), NodeReceipt->GetStringField(TEXT("graphGuid")), GuidLower(Graph->GraphGuid));
		TestTrue(
			TEXT("P11 node receipt plan id"),
			NodeReceipt->GetStringField(TEXT("planId")).StartsWith(TEXT("layout:")));
		TestTrue(
			TEXT("P11 node receipt layout hash"),
			NodeReceipt->GetStringField(TEXT("layoutHash")).StartsWith(TEXT("sha256:")));
		TestTrue(TEXT("P11 node receipt node count is positive"), NodeReceipt->GetNumberField(TEXT("nodeCount")) > 0.0);

		FUEAgentKitGraphLayoutP11Menu::SetTestDialogSuppression(true);
		IFileManager::Get().Delete(*P11ReceiptPath(), false, true, true);
		const bool bPinInvoked = PinEntry->TryExecuteToolUIAction(PinContext);
		TSharedPtr<FJsonObject> PinReceipt = ReadJsonFile(P11ReceiptPath(), Error);
		FUEAgentKitGraphLayoutP11Menu::SetTestDialogSuppression(false);

		TestTrue(TEXT("P11 pin menu action executes"), bPinInvoked);
		if (!TestTrue(TEXT("P11 pin action wrote a receipt"), PinReceipt.IsValid()))
		{
			AddError(Error);
			return false;
		}
		TestTrue(TEXT("P11 pin receipt ok"), PinReceipt->GetBoolField(TEXT("ok")));
		TestTrue(TEXT("P11 pin receipt produced a plan"), PinReceipt->GetBoolField(TEXT("planProduced")));
		TestEqual(TEXT("P11 pin context kind"), PinReceipt->GetStringField(TEXT("contextKind")), FString(TEXT("pin")));
		TestEqual(
			TEXT("P11 pin context pin name"),
			PinReceipt->GetStringField(TEXT("contextPinName")),
			ContextPin->PinName.ToString());
		TestEqual(
			TEXT("P11 pin context node guid"),
			PinReceipt->GetStringField(TEXT("contextNodeGuid")),
			GuidLower(ContextNode->NodeGuid));
		TestFalse(TEXT("P11 pin receipt did not save"), PinReceipt->GetBoolField(TEXT("saved")));
		TestFalse(TEXT("P11 pin receipt did not compile"), PinReceipt->GetBoolField(TEXT("compiled")));
		TestFalse(TEXT("P11 menu action left the package clean"), Blueprint->GetOutermost()->IsDirty());

		return true;
	}

	IMPLEMENT_SIMPLE_AUTOMATION_TEST(
		FUEAgentKitGraphLayoutP11ScopeTest,
		"UEAgentKit.GraphLayout.P11ScopeContract",
		EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

	bool FUEAgentKitGraphLayoutP11ScopeTest::RunTest(const FString& Parameters)
	{
		FString Error;
		TSharedPtr<FJsonObject> SelectionRequest = ReadJsonFile(EvidencePath(SelectionRequestFile), Error);
		TSharedPtr<FJsonObject> IncrementalRequest = ReadJsonFile(EvidencePath(IncrementalRequestFile), Error);
		if (!TestTrue(TEXT("P11 selection request exists"), SelectionRequest.IsValid())
			|| !TestTrue(TEXT("P11 incremental request exists"), IncrementalRequest.IsValid()))
		{
			AddError(Error);
			return false;
		}
		UBlueprint* Selection = LoadObject<UBlueprint>(nullptr, SelectionPath);
		UBlueprint* Incremental = LoadObject<UBlueprint>(nullptr, IncrementalPath);
		UEdGraph* SelectionGraph = Selection != nullptr ? GetPrimaryGraph(Selection) : nullptr;
		UEdGraph* IncrementalGraph = Incremental != nullptr ? GetPrimaryGraph(Incremental) : nullptr;
		if (!TestNotNull(TEXT("P11 selection asset loads"), Selection)
			|| !TestNotNull(TEXT("P11 incremental asset loads"), Incremental)
			|| !TestNotNull(TEXT("P11 selection graph loads"), SelectionGraph)
			|| !TestNotNull(TEXT("P11 incremental graph loads"), IncrementalGraph))
		{
			return false;
		}

		const TMap<FGuid, FIntPoint> SelectionBefore = CurrentPositions(SelectionGraph);
		const TMap<FGuid, FIntPoint> IncrementalBefore = CurrentPositions(IncrementalGraph);
		const FString SelectionTopology = StructuralState(SelectionGraph);
		const FString IncrementalTopology = StructuralState(IncrementalGraph);
		const TMap<FGuid, FIntPoint> SelectionTargets = RequestPositions(SelectionRequest);
		const TMap<FGuid, FIntPoint> IncrementalTargets = RequestPositions(IncrementalRequest);

		TSharedPtr<FJsonObject> SelectionResult;
		FString ErrorCode;
		FString ErrorMessage;
		if (!TestTrue(
			TEXT("P11 real selection apply succeeds"),
			UEAgentKitBlueprintGraphLayout::ApplyToGraph(
				Selection, SelectionGraph, SelectionRequest, SelectionResult, ErrorCode, ErrorMessage)))
		{
			AddError(FString::Printf(TEXT("P11 selection apply failed: %s %s"), *ErrorCode, *ErrorMessage));
			return false;
		}
		const int32 SelectionMoved = static_cast<int32>(SelectionResult->GetNumberField(TEXT("movedNodeCount")));
		TestTrue(TEXT("P11 selection moved at least one node"), SelectionMoved > 0);
		const bool bSelectionTargets = PositionsEqual(SelectionGraph, SelectionTargets);
		const bool bSelectionTopologyHeld = StructuralState(SelectionGraph).Equals(SelectionTopology, ESearchCase::CaseSensitive);

		// Every node outside the explicit scope must keep its exact position.
		bool bOutsideUnchanged = true;
		for (const TPair<FGuid, FIntPoint>& Pair : SelectionBefore)
		{
			if (SelectionTargets.Contains(Pair.Key) && SelectionTargets[Pair.Key] != Pair.Value)
			{
				continue; // a moved (in-scope) node
			}
			for (const UEdGraphNode* Node : SelectionGraph->Nodes)
			{
				if (Node != nullptr && Node->NodeGuid == Pair.Key
					&& (Node->NodePosX != Pair.Value.X || Node->NodePosY != Pair.Value.Y))
				{
					bOutsideUnchanged = false;
					break;
				}
			}
		}
		TestTrue(TEXT("P11 selection reaches the plan targets"), bSelectionTargets);
		TestTrue(TEXT("P11 selection topology is unchanged"), bSelectionTopologyHeld);
		TestTrue(TEXT("P11 selection leaves out-of-scope nodes untouched"), bOutsideUnchanged);

		const bool bSelectionUndo = GEditor != nullptr && GEditor->UndoTransaction()
			&& PositionsEqual(SelectionGraph, SelectionBefore)
			&& StructuralState(SelectionGraph).Equals(SelectionTopology, ESearchCase::CaseSensitive);
		TestTrue(TEXT("P11 selection Undo restores the exact graph"), bSelectionUndo);
		const bool bSelectionRedo = bSelectionUndo && GEditor != nullptr && GEditor->RedoTransaction()
			&& PositionsEqual(SelectionGraph, SelectionTargets);
		TestTrue(TEXT("P11 selection Redo restores the targets"), bSelectionRedo);
		if (!TestTrue(TEXT("P11 selection Save succeeds"), SaveBlueprint(Selection, Error)))
		{
			AddError(Error);
			return false;
		}

		TSharedPtr<FJsonObject> IncrementalResult;
		if (!TestTrue(
			TEXT("P11 real incremental apply succeeds"),
			UEAgentKitBlueprintGraphLayout::ApplyToGraph(
				Incremental, IncrementalGraph, IncrementalRequest, IncrementalResult, ErrorCode, ErrorMessage)))
		{
			AddError(FString::Printf(TEXT("P11 incremental apply failed: %s %s"), *ErrorCode, *ErrorMessage));
			return false;
		}
		const int32 IncrementalMoved = static_cast<int32>(IncrementalResult->GetNumberField(TEXT("movedNodeCount")));
		TestTrue(TEXT("P11 incremental moved at least one node"), IncrementalMoved > 0);
		const bool bIncrementalTargets = PositionsEqual(IncrementalGraph, IncrementalTargets);
		const bool bIncrementalTopologyHeld = StructuralState(IncrementalGraph).Equals(IncrementalTopology, ESearchCase::CaseSensitive);
		bool bFixedUnchanged = true;
		for (const TPair<FGuid, FIntPoint>& Pair : IncrementalBefore)
		{
			if (IncrementalTargets.Contains(Pair.Key) && IncrementalTargets[Pair.Key] != Pair.Value)
			{
				continue;
			}
			for (const UEdGraphNode* Node : IncrementalGraph->Nodes)
			{
				if (Node != nullptr && Node->NodeGuid == Pair.Key
					&& (Node->NodePosX != Pair.Value.X || Node->NodePosY != Pair.Value.Y))
				{
					bFixedUnchanged = false;
					break;
				}
			}
		}
		TestTrue(TEXT("P11 incremental reaches the plan targets"), bIncrementalTargets);
		TestTrue(TEXT("P11 incremental topology is unchanged"), bIncrementalTopologyHeld);
		TestTrue(TEXT("P11 incremental keeps fixed nodes untouched"), bFixedUnchanged);
		const bool bIncrementalUndo = GEditor != nullptr && GEditor->UndoTransaction()
			&& PositionsEqual(IncrementalGraph, IncrementalBefore);
		TestTrue(TEXT("P11 incremental Undo restores the exact graph"), bIncrementalUndo);
		const bool bIncrementalRedo = bIncrementalUndo && GEditor != nullptr && GEditor->RedoTransaction()
			&& PositionsEqual(IncrementalGraph, IncrementalTargets);
		TestTrue(TEXT("P11 incremental Redo restores the targets"), bIncrementalRedo);
		if (!TestTrue(TEXT("P11 incremental Save succeeds"), SaveBlueprint(Incremental, Error)))
		{
			AddError(Error);
			return false;
		}

		// Strict no-op boundary: a plan whose positions are the current ones and
		// whose movedNodeIds is empty must not open a transaction or dirty the
		// package.  This plan is hand-built on purpose; the two positive plans
		// above are the algorithm outputs.
		TSharedPtr<FJsonObject> NoOpPreflight;
		if (!TestTrue(
			TEXT("P11 no-op preflight succeeds"),
			UEAgentKitBlueprintGraphLayout::BuildPreflight(
				Incremental, IncrementalGraph, NoOpPreflight, ErrorCode, ErrorMessage)))
		{
			AddError(FString::Printf(TEXT("P11 no-op preflight failed: %s %s"), *ErrorCode, *ErrorMessage));
			return false;
		}
		TSharedRef<FJsonObject> NoOpPlan = MakeShared<FJsonObject>();
		NoOpPlan->SetStringField(TEXT("schemaVersion"), TEXT("ueak.layoutplan/1.0"));
		NoOpPlan->SetStringField(TEXT("planId"), TEXT("layout:p11noop"));
		NoOpPlan->SetStringField(TEXT("status"), TEXT("ok"));
		NoOpPlan->SetStringField(TEXT("errorCode"), TEXT(""));
		NoOpPlan->SetStringField(TEXT("mode"), TEXT("selection"));
		NoOpPlan->SetStringField(TEXT("baseGraphHash"), NoOpPreflight->GetStringField(TEXT("packageRevision")));
		NoOpPlan->SetStringField(TEXT("evidenceLevel"), TEXT("same-process"));
		NoOpPlan->SetBoolField(TEXT("acceptanceEligible"), false);
		NoOpPlan->SetStringField(TEXT("requiredAcceptanceEvidence"), TEXT("independent-reload"));
		TArray<TSharedPtr<FJsonValue>> Positions;
		TArray<TSharedPtr<FJsonValue>> Scope;
		for (const UEdGraphNode* Node : IncrementalGraph->Nodes)
		{
			if (Node == nullptr)
			{
				continue;
			}
			TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
			Item->SetStringField(TEXT("nodeId"), FString(TEXT("n:")) + GuidLower(Node->NodeGuid));
			Item->SetNumberField(TEXT("x"), Node->NodePosX);
			Item->SetNumberField(TEXT("y"), Node->NodePosY);
			TSharedRef<FJsonObject> Size = MakeShared<FJsonObject>();
			Size->SetNumberField(TEXT("width"), Node->NodeWidth);
			Size->SetNumberField(TEXT("height"), Node->NodeHeight);
			Size->SetStringField(TEXT("source"), TEXT("stored"));
			Item->SetObjectField(TEXT("size"), Size);
			Positions.Add(MakeShared<FJsonValueObject>(Item));
			if (Cast<UEdGraphNode_Comment>(Node) == nullptr)
			{
				Scope.Add(MakeShared<FJsonValueString>(FString(TEXT("n:")) + GuidLower(Node->NodeGuid)));
			}
		}
		NoOpPlan->SetArrayField(TEXT("positions"), Positions);
		TArray<TSharedPtr<FJsonValue>> EmptyList;
		NoOpPlan->SetArrayField(TEXT("movedNodeIds"), EmptyList);
		NoOpPlan->SetArrayField(TEXT("scopeNodeIds"), Scope);
		NoOpPlan->SetArrayField(TEXT("fixedNodeIds"), EmptyList);
		NoOpPlan->SetArrayField(TEXT("islands"), EmptyList);
		NoOpPlan->SetArrayField(TEXT("warnings"), EmptyList);
		FString NoOpHash;
		if (!TestTrue(TEXT("P11 no-op layout hash computes"), UEAgentKitBlueprintGraphLayout::ComputeLayoutHash(NoOpPlan, NoOpHash)))
		{
			return false;
		}
		NoOpPlan->SetStringField(TEXT("layoutHash"), NoOpHash);

		TSharedRef<FJsonObject> NoOpRequest = MakeShared<FJsonObject>();
		NoOpRequest->SetStringField(TEXT("schemaVersion"), TEXT("ueak.graphlayoutapply/1.0"));
		NoOpRequest->SetStringField(TEXT("assetPath"), Incremental->GetPathName());
		NoOpRequest->SetStringField(TEXT("graphGuid"), GuidLower(IncrementalGraph->GraphGuid));
		NoOpRequest->SetStringField(TEXT("expectedPackageRevision"), NoOpPreflight->GetStringField(TEXT("packageRevision")));
		NoOpRequest->SetStringField(TEXT("expectedGraphFingerprint"), NoOpPreflight->GetStringField(TEXT("graphFingerprint")));
		NoOpRequest->SetObjectField(TEXT("plan"), NoOpPlan);
		NoOpRequest->SetStringField(TEXT("requestedMode"), TEXT("position-only"));
		NoOpRequest->SetBoolField(TEXT("allowSave"), false);
		NoOpRequest->SetBoolField(TEXT("allowCompile"), false);
		NoOpRequest->SetBoolField(TEXT("acceptanceEligible"), false);
		NoOpRequest->SetStringField(TEXT("requiredAcceptanceEvidence"), TEXT("independent-reload"));

		TSharedPtr<FJsonObject> NoOpResult;
		const bool bNoOpApplied = UEAgentKitBlueprintGraphLayout::ApplyToGraph(
			Incremental, IncrementalGraph, NoOpRequest, NoOpResult, ErrorCode, ErrorMessage);
		const bool bNoOpChanged = NoOpResult.IsValid() && NoOpResult->GetBoolField(TEXT("changed"));
		const bool bNoOpClean = !Incremental->GetOutermost()->IsDirty();
		TestTrue(TEXT("P11 no-op apply succeeds"), bNoOpApplied);
		TestFalse(TEXT("P11 no-op reports no change"), bNoOpChanged);
		TestTrue(TEXT("P11 no-op leaves the package clean"), bNoOpClean);

		TSharedRef<FJsonObject> Evidence = MakeShared<FJsonObject>();
		Evidence->SetStringField(TEXT("schemaVersion"), TEXT("ueak.p11.scope/1.0"));
		Evidence->SetStringField(TEXT("test"), TEXT("UEAgentKit.GraphLayout.P11ScopeContract"));
		Evidence->SetStringField(TEXT("selectionAsset"), Selection->GetPathName());
		Evidence->SetStringField(TEXT("incrementalAsset"), Incremental->GetPathName());
		Evidence->SetNumberField(TEXT("selectionMoved"), SelectionMoved);
		Evidence->SetNumberField(TEXT("selectionScoped"), SelectionRequest->GetObjectField(TEXT("plan"))->GetArrayField(TEXT("scopeNodeIds")).Num());
		Evidence->SetBoolField(TEXT("selectionTargets"), bSelectionTargets);
		Evidence->SetBoolField(TEXT("selectionTopologyHeld"), bSelectionTopologyHeld);
		Evidence->SetBoolField(TEXT("selectionOutsideUnchanged"), bOutsideUnchanged);
		Evidence->SetBoolField(TEXT("selectionUndo"), bSelectionUndo);
		Evidence->SetBoolField(TEXT("selectionRedo"), bSelectionRedo);
		Evidence->SetNumberField(TEXT("incrementalMoved"), IncrementalMoved);
		Evidence->SetNumberField(TEXT("incrementalScoped"), IncrementalRequest->GetObjectField(TEXT("plan"))->GetArrayField(TEXT("scopeNodeIds")).Num());
		Evidence->SetBoolField(TEXT("incrementalTargets"), bIncrementalTargets);
		Evidence->SetBoolField(TEXT("incrementalTopologyHeld"), bIncrementalTopologyHeld);
		Evidence->SetBoolField(TEXT("incrementalFixedUnchanged"), bFixedUnchanged);
		Evidence->SetBoolField(TEXT("incrementalUndo"), bIncrementalUndo);
		Evidence->SetBoolField(TEXT("incrementalRedo"), bIncrementalRedo);
		Evidence->SetBoolField(TEXT("noOpApplied"), bNoOpApplied);
		Evidence->SetBoolField(TEXT("noOpChanged"), bNoOpChanged);
		Evidence->SetBoolField(TEXT("noOpClean"), bNoOpClean);
		Evidence->SetStringField(TEXT("selectionRevisionAfterSave"), [&]() {
			TSharedPtr<FJsonObject> P;
			FString C, M;
			UEAgentKitBlueprintGraphLayout::BuildPreflight(Selection, SelectionGraph, P, C, M);
			return P.IsValid() ? P->GetStringField(TEXT("packageRevision")) : FString();
		}());
		Evidence->SetStringField(TEXT("incrementalRevisionAfterSave"), [&]() {
			TSharedPtr<FJsonObject> P;
			FString C, M;
			UEAgentKitBlueprintGraphLayout::BuildPreflight(Incremental, IncrementalGraph, P, C, M);
			return P.IsValid() ? P->GetStringField(TEXT("packageRevision")) : FString();
		}());
		Evidence->SetStringField(TEXT("dllSha256"), Sha256FileText(ModuleDllPath()));
		Evidence->SetStringField(TEXT("savedAtUtc"), FDateTime::UtcNow().ToIso8601());
		TArray<TSharedPtr<FJsonValue>> SelectionPositions;
		for (const UEdGraphNode* Node : SelectionGraph->Nodes)
		{
			if (Node == nullptr) continue;
			TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
			Item->SetStringField(TEXT("nodeId"), FString(TEXT("n:")) + GuidLower(Node->NodeGuid));
			Item->SetNumberField(TEXT("x"), Node->NodePosX);
			Item->SetNumberField(TEXT("y"), Node->NodePosY);
			SelectionPositions.Add(MakeShared<FJsonValueObject>(Item));
		}
		Evidence->SetArrayField(TEXT("selectionPositions"), SelectionPositions);
		TArray<TSharedPtr<FJsonValue>> IncrementalPositions;
		for (const UEdGraphNode* Node : IncrementalGraph->Nodes)
		{
			if (Node == nullptr) continue;
			TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
			Item->SetStringField(TEXT("nodeId"), FString(TEXT("n:")) + GuidLower(Node->NodeGuid));
			Item->SetNumberField(TEXT("x"), Node->NodePosX);
			Item->SetNumberField(TEXT("y"), Node->NodePosY);
			IncrementalPositions.Add(MakeShared<FJsonValueObject>(Item));
		}
		Evidence->SetArrayField(TEXT("incrementalPositions"), IncrementalPositions);
		Evidence->SetStringField(TEXT("selectionTopologyHash"), Sha1Text(StructuralState(SelectionGraph)));
		Evidence->SetStringField(TEXT("incrementalTopologyHash"), Sha1Text(StructuralState(IncrementalGraph)));
		if (!TestTrue(TEXT("P11 scope evidence is written"), WriteJson(EvidencePath(ScopeResultFile), Evidence, Error)))
		{
			AddError(Error);
			return false;
		}
		return bSelectionTargets && bSelectionTopologyHeld && bOutsideUnchanged && bSelectionUndo && bSelectionRedo
			&& bIncrementalTargets && bIncrementalTopologyHeld && bFixedUnchanged && bIncrementalUndo && bIncrementalRedo
			&& bNoOpApplied && !bNoOpChanged && bNoOpClean;
	}

	IMPLEMENT_SIMPLE_AUTOMATION_TEST(
		FUEAgentKitGraphLayoutP11ReloadTest,
		"UEAgentKit.GraphLayout.P11ReloadContract",
		EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

	bool FUEAgentKitGraphLayoutP11ReloadTest::RunTest(const FString& Parameters)
	{
		FString Error;
		TSharedPtr<FJsonObject> Scope = ReadJsonFile(EvidencePath(ScopeResultFile), Error);
		if (!TestTrue(TEXT("P11 scope evidence exists for reload"), Scope.IsValid()))
		{
			AddError(Error);
			return false;
		}
		UBlueprint* Selection = LoadObject<UBlueprint>(nullptr, SelectionPath);
		UBlueprint* Incremental = LoadObject<UBlueprint>(nullptr, IncrementalPath);
		UEdGraph* SelectionGraph = Selection != nullptr ? GetPrimaryGraph(Selection) : nullptr;
		UEdGraph* IncrementalGraph = Incremental != nullptr ? GetPrimaryGraph(Incremental) : nullptr;
		if (!TestNotNull(TEXT("P11 selection reloads"), Selection)
			|| !TestNotNull(TEXT("P11 incremental reloads"), Incremental)
			|| !TestNotNull(TEXT("P11 selection graph reloads"), SelectionGraph)
			|| !TestNotNull(TEXT("P11 incremental graph reloads"), IncrementalGraph))
		{
			return false;
		}
		TMap<FGuid, FIntPoint> SelectionExpected;
		for (const TSharedPtr<FJsonValue>& Entry : Scope->GetArrayField(TEXT("selectionPositions")))
		{
			const TSharedPtr<FJsonObject> Item = Entry->AsObject();
			FGuid Guid;
			if (FGuid::Parse(Item->GetStringField(TEXT("nodeId")).RightChop(2), Guid))
			{
				SelectionExpected.Add(Guid, FIntPoint(
					static_cast<int32>(Item->GetNumberField(TEXT("x"))),
					static_cast<int32>(Item->GetNumberField(TEXT("y")))));
			}
		}
		TMap<FGuid, FIntPoint> IncrementalExpected;
		for (const TSharedPtr<FJsonValue>& Entry : Scope->GetArrayField(TEXT("incrementalPositions")))
		{
			const TSharedPtr<FJsonObject> Item = Entry->AsObject();
			FGuid Guid;
			if (FGuid::Parse(Item->GetStringField(TEXT("nodeId")).RightChop(2), Guid))
			{
				IncrementalExpected.Add(Guid, FIntPoint(
					static_cast<int32>(Item->GetNumberField(TEXT("x"))),
					static_cast<int32>(Item->GetNumberField(TEXT("y")))));
			}
		}
		const bool bSelectionPositions = PositionsEqual(SelectionGraph, SelectionExpected);
		const bool bIncrementalPositions = PositionsEqual(IncrementalGraph, IncrementalExpected);
		const bool bSelectionTopology = Sha1Text(StructuralState(SelectionGraph)).Equals(
			Scope->GetStringField(TEXT("selectionTopologyHash")), ESearchCase::CaseSensitive);
		const bool bIncrementalTopology = Sha1Text(StructuralState(IncrementalGraph)).Equals(
			Scope->GetStringField(TEXT("incrementalTopologyHash")), ESearchCase::CaseSensitive);
		const bool bDll = Sha256FileText(ModuleDllPath()).Equals(
			Scope->GetStringField(TEXT("dllSha256")), ESearchCase::CaseSensitive);
		TestTrue(TEXT("P11 selection reload restores exact positions"), bSelectionPositions);
		TestTrue(TEXT("P11 incremental reload restores exact positions"), bIncrementalPositions);
		TestTrue(TEXT("P11 selection reload keeps topology"), bSelectionTopology);
		TestTrue(TEXT("P11 incremental reload keeps topology"), bIncrementalTopology);
		TestTrue(TEXT("P11 reload uses the same DLL"), bDll);

		TSharedRef<FJsonObject> Evidence = MakeShared<FJsonObject>();
		Evidence->SetStringField(TEXT("schemaVersion"), TEXT("ueak.p11.reload/1.0"));
		Evidence->SetStringField(TEXT("test"), TEXT("UEAgentKit.GraphLayout.P11ReloadContract"));
		Evidence->SetBoolField(TEXT("selectionPositions"), bSelectionPositions);
		Evidence->SetBoolField(TEXT("incrementalPositions"), bIncrementalPositions);
		Evidence->SetBoolField(TEXT("selectionTopology"), bSelectionTopology);
		Evidence->SetBoolField(TEXT("incrementalTopology"), bIncrementalTopology);
		Evidence->SetBoolField(TEXT("dllVerified"), bDll);
		Evidence->SetBoolField(TEXT("selectionDirty"), Selection->GetOutermost()->IsDirty());
		Evidence->SetBoolField(TEXT("incrementalDirty"), Incremental->GetOutermost()->IsDirty());
		Evidence->SetStringField(TEXT("selectionRevision"), [&]() {
			TSharedPtr<FJsonObject> P; FString C, M;
			UEAgentKitBlueprintGraphLayout::BuildPreflight(Selection, SelectionGraph, P, C, M);
			return P.IsValid() ? P->GetStringField(TEXT("packageRevision")) : FString();
		}());
		Evidence->SetStringField(TEXT("incrementalRevision"), [&]() {
			TSharedPtr<FJsonObject> P; FString C, M;
			UEAgentKitBlueprintGraphLayout::BuildPreflight(Incremental, IncrementalGraph, P, C, M);
			return P.IsValid() ? P->GetStringField(TEXT("packageRevision")) : FString();
		}());
		Evidence->SetStringField(TEXT("dllSha256"), Sha256FileText(ModuleDllPath()));
		Evidence->SetStringField(TEXT("engineVersion"), FEngineVersion::Current().ToString());
		Evidence->SetStringField(TEXT("reloadedAtUtc"), FDateTime::UtcNow().ToIso8601());
		if (!TestTrue(TEXT("P11 reload evidence is written"), WriteJson(EvidencePath(ReloadResultFile), Evidence, Error)))
		{
			AddError(Error);
			return false;
		}
		return bSelectionPositions && bIncrementalPositions && bSelectionTopology && bIncrementalTopology && bDll;
	}
}

#undef LOCTEXT_NAMESPACE

#endif // WITH_DEV_AUTOMATION_TESTS
