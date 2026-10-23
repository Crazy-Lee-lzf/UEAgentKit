#include "BlueprintGraphLayoutApply.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "BlueprintContextSha256.h"
#include "BlueprintEditor.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "EdGraphSchema_K2.h"
#include "Editor.h"
#include "Editor/Transactor.h"
#include "Engine/Blueprint.h"
#include "GameFramework/Actor.h"
#include "HAL/FileManager.h"
#include "K2Node_Event.h"
#include "K2Node_ExecutionSequence.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Kismet2/KismetEditorUtilities.h"
#include "Misc/AutomationTest.h"
#include "Misc/FileHelper.h"
#include "Misc/PackageName.h"
#include "Misc/Paths.h"
#include "Misc/SecureHash.h"
#include "ScopedTransaction.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "Subsystems/AssetEditorSubsystem.h"
#include "Toolkits/IToolkitHost.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"

#if WITH_DEV_AUTOMATION_TESTS

#define LOCTEXT_NAMESPACE "UEAgentKitGraphLayoutP5"

namespace UEAgentKitGraphLayoutP5
{
	namespace
	{
		const TCHAR* FixturePackageName = TEXT("/Game/UEAgentKitP5/BP_LayoutP5");
		const TCHAR* FixtureAssetName = TEXT("BP_LayoutP5");
		const TCHAR* FixtureAssetPath = TEXT("/Game/UEAgentKitP5/BP_LayoutP5.BP_LayoutP5");
		const TCHAR* ApplyEvidenceFile = TEXT("p5-apply-result.json");

		FString GuidLower(const FGuid& Guid)
		{
			return Guid.ToString(EGuidFormats::DigitsWithHyphensLower);
		}

		FString NodeId(const FGuid& Guid)
		{
			return FString(TEXT("n:")) + GuidLower(Guid);
		}

		FString EvidencePath(const TCHAR* FileName)
		{
			return FPaths::Combine(FPaths::ProjectDir(), TEXT("Evidence"), FileName);
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
			IFileManager::Get().MakeDirectory(*FPaths::GetPath(Filename), true);

			FSavePackageArgs SaveArgs;
			SaveArgs.TopLevelFlags = RF_Public | RF_Standalone;
			SaveArgs.SaveFlags = SAVE_NoError;
			SaveArgs.Error = GError;
			if (!UPackage::SavePackage(Package, Blueprint, *Filename, SaveArgs))
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

		UEdGraph* FindExactGraph(UBlueprint* Blueprint, const FGuid& Wanted)
		{
			if (Blueprint == nullptr)
			{
				return nullptr;
			}
			UEdGraph* Found = nullptr;
			auto Search = [&Wanted, &Found](const TArray<TObjectPtr<UEdGraph>>& Graphs)
			{
				for (UEdGraph* Candidate : Graphs)
				{
					if (Candidate != nullptr && Candidate->GraphGuid == Wanted)
					{
						Found = Candidate;
					}
				}
			};
			Search(Blueprint->UbergraphPages);
			Search(Blueprint->FunctionGraphs);
			Search(Blueprint->MacroGraphs);
			Search(Blueprint->DelegateSignatureGraphs);
			return Found;
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

		UBlueprint* CreateFixture(FString& OutError)
		{
			UPackage* Package = CreatePackage(FixturePackageName);
			if (Package == nullptr)
			{
				OutError = TEXT("Could not create the P5 disposable fixture package.");
				return nullptr;
			}

			UBlueprint* Blueprint = FKismetEditorUtilities::CreateBlueprint(
				AActor::StaticClass(),
				Package,
				FName(FixtureAssetName),
				BPTYPE_Normal,
				FName(TEXT("UEAgentKitP5Fixture")));
			if (Blueprint == nullptr)
			{
				OutError = TEXT("Could not create the P5 disposable K2 Blueprint.");
				return nullptr;
			}

			UEdGraph* Graph = GetPrimaryGraph(Blueprint);
			if (Graph == nullptr)
			{
				OutError = TEXT("The P5 disposable Blueprint has no ubergraph.");
				return nullptr;
			}

			// A newly created Actor Blueprint can contain more than BeginPlay.
			// Keep exactly one real entry event so the fixture topology and its
			// expected node count are deterministic.
			UK2Node_Event* BeginPlay = nullptr;
			for (UEdGraphNode* Candidate : Graph->Nodes)
			{
				UK2Node_Event* Event = Cast<UK2Node_Event>(Candidate);
				if (Event != nullptr
					&& Event->EventReference.GetMemberName() == FName(TEXT("ReceiveBeginPlay")))
				{
					BeginPlay = Event;
					break;
				}
			}
			if (BeginPlay == nullptr)
			{
				OutError = TEXT("The P5 disposable Blueprint has no ReceiveBeginPlay event.");
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

			struct FNodeSpec
			{
				int32 X;
				int32 Y;
				int32 Width;
				int32 Height;
				const TCHAR* Comment;
			};
			const FNodeSpec Specs[] = {
				{-720, 40, 210, 120, TEXT("P5 focus chain root")},
				{-300, -140, 200, 110, TEXT("P5 focus chain two")},
				{120, 160, 220, 130, TEXT("P5 focus chain three")},
				{560, -40, 200, 120, TEXT("P5 focus chain tail")},
			};
			TArray<UK2Node_ExecutionSequence*> Sequences;
			for (const FNodeSpec& Spec : Specs)
			{
				FGraphNodeCreator<UK2Node_ExecutionSequence> Creator(*Graph);
				UK2Node_ExecutionSequence* Node = Creator.CreateNode(false);
				Node->NodePosX = Spec.X;
				Node->NodePosY = Spec.Y;
				Creator.Finalize();
				Node->NodeWidth = Spec.Width;
				Node->NodeHeight = Spec.Height;
				Node->NodeComment = Spec.Comment;
				Sequences.Add(Node);
			}

			UEdGraphPin* EventThen = BeginPlay != nullptr
				? BeginPlay->FindPin(UEdGraphSchema_K2::PN_Then, EGPD_Output)
				: nullptr;
			if (EventThen == nullptr
				|| !LinkPins(
					Graph,
					EventThen,
					Sequences[0]->FindPin(UEdGraphSchema_K2::PN_Execute, EGPD_Input))
				|| !LinkPins(
					Graph,
					Sequences[0]->GetThenPinGivenIndex(0),
					Sequences[1]->FindPin(UEdGraphSchema_K2::PN_Execute, EGPD_Input))
				|| !LinkPins(
					Graph,
					Sequences[1]->GetThenPinGivenIndex(0),
					Sequences[2]->FindPin(UEdGraphSchema_K2::PN_Execute, EGPD_Input))
				|| !LinkPins(
					Graph,
					Sequences[2]->GetThenPinGivenIndex(0),
					Sequences[3]->FindPin(UEdGraphSchema_K2::PN_Execute, EGPD_Input)))
			{
				OutError = TEXT("Could not create the P5 linear execution topology.");
				return nullptr;
			}

			FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
			FKismetEditorUtilities::CompileBlueprint(
				Blueprint,
				EBlueprintCompileOptions::SkipGarbageCollection);
			if (Blueprint->Status == BS_Error)
			{
				OutError = TEXT("The P5 disposable fixture Blueprint failed to compile.");
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
				if (Existing->GetOutermost()->IsDirty())
				{
					OutError = TEXT("The existing P5 fixture is dirty; rebuild the disposable host.");
					return nullptr;
				}
				return Existing;
			}
			return CreateFixture(OutError);
		}

		bool WriteJson(
			const FString& Path,
			const TSharedPtr<FJsonObject>& Object,
			FString& OutError)
		{
			if (!Object.IsValid())
			{
				OutError = TEXT("No JSON object to write.");
				return false;
			}
			IFileManager::Get().MakeDirectory(*FPaths::GetPath(Path), true);
			FString Text;
			const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Text);
			if (!FJsonSerializer::Serialize(Object.ToSharedRef(), Writer))
			{
				OutError = FString::Printf(TEXT("Could not serialize %s."), *Path);
				return false;
			}
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

		bool WriteEvidenceJson(
			const TCHAR* FileName,
			const TSharedPtr<FJsonObject>& Object,
			FString& OutError)
		{
			return WriteJson(EvidencePath(FileName), Object, OutError);
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
				OutError = FString::Printf(TEXT("Could not parse %s."), *Path);
				return nullptr;
			}
			return Object;
		}

		TSharedPtr<FJsonObject> ReadEvidenceJson(const TCHAR* FileName, FString& OutError)
		{
			return ReadJsonFile(EvidencePath(FileName), OutError);
		}

		FString Sha256Text(const FString& Text)
		{
			FString Digest;
			if (!FBlueprintContextSha256::HashText(Text, Digest))
			{
				return FString();
			}
			return FString(TEXT("sha256:")) + Digest;
		}

		FString Sha256FileText(const FString& Path)
		{
			FString Digest;
			if (!FBlueprintContextSha256::HashFile(Path, Digest))
			{
				return FString();
			}
			return FString(TEXT("sha256:")) + Digest;
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

		TMap<FGuid, FIntPoint> CapturePositions(UEdGraph* Graph)
		{
			TMap<FGuid, FIntPoint> Result;
			if (Graph != nullptr)
			{
				for (UEdGraphNode* Node : Graph->Nodes)
				{
					if (Node != nullptr)
					{
						Result.Add(Node->NodeGuid, FIntPoint(Node->NodePosX, Node->NodePosY));
					}
				}
			}
			return Result;
		}

		bool PositionsEqual(
			const TMap<FGuid, FIntPoint>& Left,
			const TMap<FGuid, FIntPoint>& Right)
		{
			if (Left.Num() != Right.Num())
			{
				return false;
			}
			for (const TPair<FGuid, FIntPoint>& Pair : Left)
			{
				const FIntPoint* Other = Right.Find(Pair.Key);
				if (Other == nullptr || *Other != Pair.Value)
				{
					return false;
				}
			}
			return true;
		}

		/**
		 * Position-free structural state.  A successful TryApply may change only
		 * NodePosX/NodePosY: GUIDs, classes, comments, sizes, pin identities and
		 * links must remain identical.
		 */
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
				return Left.NodeGuid.ToString(EGuidFormats::Digits)
					< Right.NodeGuid.ToString(EGuidFormats::Digits);
			});

			FString State;
			for (const UEdGraphNode* Node : SortedNodes)
			{
				State += GuidLower(Node->NodeGuid);
				State += TEXT("|");
				State += Node->GetClass() != nullptr ? Node->GetClass()->GetPathName() : FString();
				State += TEXT("|");
				State += Node->NodeComment;
				State += TEXT("|");
				State += FString::FromInt(Node->NodeWidth);
				State += TEXT("x");
				State += FString::FromInt(Node->NodeHeight);
				State += TEXT("|");

				TArray<UEdGraphPin*> Pins;
				for (UEdGraphPin* Pin : Node->Pins)
				{
					if (Pin != nullptr)
					{
						Pins.Add(Pin);
					}
				}
				Pins.Sort([](const UEdGraphPin& Left, const UEdGraphPin& Right)
				{
					return Left.PinId.ToString(EGuidFormats::Digits)
						< Right.PinId.ToString(EGuidFormats::Digits);
				});
				for (const UEdGraphPin* Pin : Pins)
				{
					State += Pin->PinId.ToString(EGuidFormats::DigitsWithHyphensLower);
					State += TEXT(":");
					State += Pin->PinName.ToString();
					State += TEXT(":");
					State += Pin->Direction == EGPD_Input ? TEXT("in") : TEXT("out");
					State += TEXT(":");
					State += Pin->PinType.PinCategory.ToString();
					State += TEXT(":");
					State += Pin->PinType.PinSubCategory.ToString();
					State += TEXT(":");
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
					for (const FString& Link : Links)
					{
						State += Link;
						State += TEXT(",");
					}
					State += TEXT(";");
				}
				State += TEXT("||");
			}
			return State;
		}

		FString TopologyHash(UEdGraph* Graph)
		{
			return Sha1Text(StructuralState(Graph));
		}

		TArray<UEdGraphNode*> SortedNodes(UEdGraph* Graph)
		{
			TArray<UEdGraphNode*> Result;
			for (UEdGraphNode* Node : Graph->Nodes)
			{
				if (Node != nullptr)
				{
					Result.Add(Node);
				}
			}
			Result.Sort([](const UEdGraphNode& Left, const UEdGraphNode& Right)
			{
				return Left.NodeGuid.ToString(EGuidFormats::Digits)
					< Right.NodeGuid.ToString(EGuidFormats::Digits);
			});
			return Result;
		}

		TSharedPtr<FJsonObject> BuildPlan(UBlueprint* Blueprint, UEdGraph* Graph, FString& OutError)
		{
			if (Blueprint == nullptr || Graph == nullptr)
			{
				OutError = TEXT("No Blueprint or graph for the P5 plan.");
				return nullptr;
			}
			const TArray<UEdGraphNode*> Nodes = SortedNodes(Graph);
			if (Nodes.Num() != 5)
			{
				OutError = FString::Printf(
					TEXT("The P5 fixture must contain exactly 5 nodes; found %d."),
					Nodes.Num());
				return nullptr;
			}

			const int32 DeltaX[] = {180, 140, 220, 160, 200};
			const int32 DeltaY[] = {60, -40, 90, -70, 30};
			TArray<TSharedPtr<FJsonValue>> Positions;
			TArray<TSharedPtr<FJsonValue>> Moved;
			TArray<TSharedPtr<FJsonValue>> Scope;
			for (int32 Index = 0; Index < Nodes.Num(); ++Index)
			{
				UEdGraphNode* Node = Nodes[Index];
				const int32 TargetX = Node->NodePosX + DeltaX[Index % UE_ARRAY_COUNT(DeltaX)];
				const int32 TargetY = Node->NodePosY + DeltaY[Index % UE_ARRAY_COUNT(DeltaY)];
				if (TargetX == Node->NodePosX && TargetY == Node->NodePosY)
				{
					OutError = TEXT("The P5 plan produced a non-moving node.");
					return nullptr;
				}
				Moved.Add(MakeShared<FJsonValueString>(NodeId(Node->NodeGuid)));
				Scope.Add(MakeShared<FJsonValueString>(NodeId(Node->NodeGuid)));

				TSharedRef<FJsonObject> Position = MakeShared<FJsonObject>();
				Position->SetStringField(TEXT("nodeId"), NodeId(Node->NodeGuid));
				Position->SetNumberField(TEXT("x"), TargetX);
				Position->SetNumberField(TEXT("y"), TargetY);
				TSharedRef<FJsonObject> Size = MakeShared<FJsonObject>();
				Size->SetNumberField(TEXT("width"), FMath::Max(0, Node->NodeWidth));
				Size->SetNumberField(TEXT("height"), FMath::Max(0, Node->NodeHeight));
				Size->SetStringField(TEXT("source"), TEXT("stored"));
				Position->SetObjectField(TEXT("size"), Size);
				Positions.Add(MakeShared<FJsonValueObject>(Position));
			}

			TSharedRef<FJsonObject> Plan = MakeShared<FJsonObject>();
			Plan->SetStringField(TEXT("schemaVersion"), TEXT("ueak.layoutplan/1.0"));
			Plan->SetStringField(TEXT("status"), TEXT("ok"));
			Plan->SetStringField(TEXT("errorCode"), TEXT(""));
			Plan->SetStringField(TEXT("mode"), TEXT("full"));
			Plan->SetStringField(TEXT("evidenceLevel"), TEXT("same-process"));
			Plan->SetBoolField(TEXT("acceptanceEligible"), false);
			Plan->SetStringField(TEXT("requiredAcceptanceEvidence"), TEXT("independent-reload"));
			Plan->SetStringField(TEXT("baseGraphHash"), Sha256Text(StructuralState(Graph)));
			Plan->SetArrayField(TEXT("positions"), Positions);
			Plan->SetArrayField(TEXT("movedNodeIds"), Moved);
			Plan->SetArrayField(TEXT("scopeNodeIds"), Scope);
			Plan->SetArrayField(TEXT("fixedNodeIds"), TArray<TSharedPtr<FJsonValue>>());

			FString LayoutHash;
			if (!UEAgentKitBlueprintGraphLayout::ComputeLayoutHash(Plan, LayoutHash))
			{
				OutError = TEXT("Could not compute the P5 layout hash.");
				return nullptr;
			}
			Plan->SetStringField(TEXT("layoutHash"), LayoutHash);
			Plan->SetStringField(
				TEXT("planId"),
				FString(TEXT("layout:p5-")) + LayoutHash.Mid(7, 16));
			return Plan;
		}

		TSharedPtr<FJsonObject> BuildRequest(
			UBlueprint* Blueprint,
			UEdGraph* Graph,
			const TSharedPtr<FJsonObject>& Plan,
			FString& OutError)
		{
			TSharedPtr<FJsonObject> Preflight;
			FString ErrorCode;
			FString ErrorMessage;
			if (!UEAgentKitBlueprintGraphLayout::BuildPreflight(
				Blueprint,
				Graph,
				Preflight,
				ErrorCode,
				ErrorMessage))
			{
				OutError = FString::Printf(TEXT("P5 preflight failed: %s %s"), *ErrorCode, *ErrorMessage);
				return nullptr;
			}
			if (Preflight->GetBoolField(TEXT("packageDirty")))
			{
				OutError = TEXT("The P5 fixture is dirty while building an apply request.");
				return nullptr;
			}

			TSharedRef<FJsonObject> Request = MakeShared<FJsonObject>();
			Request->SetStringField(TEXT("schemaVersion"), TEXT("ueak.graphlayoutapply/1.0"));
			Request->SetStringField(TEXT("assetPath"), Blueprint->GetPathName());
			Request->SetStringField(
				TEXT("graphGuid"),
				GuidLower(Graph->GraphGuid));
			Request->SetStringField(
				TEXT("expectedPackageRevision"),
				Preflight->GetStringField(TEXT("packageRevision")));
			Request->SetStringField(
				TEXT("expectedGraphFingerprint"),
				Preflight->GetStringField(TEXT("graphFingerprint")));
			Request->SetObjectField(TEXT("plan"), Plan);
			Request->SetStringField(TEXT("requestedMode"), TEXT("position-only"));
			Request->SetBoolField(TEXT("allowSave"), false);
			Request->SetBoolField(TEXT("allowCompile"), false);
			Request->SetBoolField(TEXT("acceptanceEligible"), false);
			Request->SetStringField(TEXT("requiredAcceptanceEvidence"), TEXT("independent-reload"));
			return Request;
		}

		FBlueprintEditor* OpenAndFocus(
			UBlueprint* Blueprint,
			UEdGraph* Graph,
			FString& OutError)
		{
			if (GEditor == nullptr)
			{
				OutError = TEXT("GEditor is unavailable.");
				return nullptr;
			}
			UAssetEditorSubsystem* AssetEditorSubsystem =
				GEditor->GetEditorSubsystem<UAssetEditorSubsystem>();
			if (AssetEditorSubsystem == nullptr)
			{
				OutError = TEXT("The Asset Editor Subsystem is unavailable.");
				return nullptr;
			}
			if (!AssetEditorSubsystem->OpenEditorForAsset(
				Blueprint,
				EToolkitMode::Standalone,
				TSharedPtr<IToolkitHost>(),
				false))
			{
				OutError = TEXT("OpenEditorForAsset returned false.");
				return nullptr;
			}
			IAssetEditorInstance* Instance =
				AssetEditorSubsystem->FindEditorForAsset(Blueprint, false);
			if (Instance == nullptr || Instance->GetEditorName() != FName(TEXT("BlueprintEditor")))
			{
				OutError = TEXT("FindEditorForAsset did not return a BlueprintEditor.");
				return nullptr;
			}
			FBlueprintEditor* Editor = static_cast<FBlueprintEditor*>(Instance);
			if (Editor->GetBlueprintObj() != Blueprint)
			{
				OutError = TEXT("The Blueprint Editor does not own the requested Blueprint.");
				return nullptr;
			}
			const TSharedPtr<SGraphEditor> GraphEditor =
				Editor->OpenGraphAndBringToFront(Graph, true);
			if (!GraphEditor.IsValid())
			{
				OutError = TEXT("OpenGraphAndBringToFront returned an invalid graph editor.");
				return nullptr;
			}
			if (Editor->GetFocusedGraph() != Graph)
			{
				OutError = TEXT("The requested graph is not the focused graph after OpenGraphAndBringToFront.");
				return nullptr;
			}
			return Editor;
		}

		bool VerifyPositions(
			FAutomationTestBase& Test,
			UEdGraph* Graph,
			const TMap<FGuid, FIntPoint>& Expected,
			const TCHAR* Label)
		{
			const TMap<FGuid, FIntPoint> Actual = CapturePositions(Graph);
			if (!Test.TestTrue(
				*FString::Printf(TEXT("%s covers every node exactly once"), Label),
				Actual.Num() == Expected.Num()))
			{
				return false;
			}
			for (const TPair<FGuid, FIntPoint>& Pair : Expected)
			{
				const FIntPoint* Found = Actual.Find(Pair.Key);
				if (!Test.TestNotNull(
					*FString::Printf(TEXT("%s node exists"), Label),
					Found))
				{
					return false;
				}
				if (!Test.TestEqual(
					*FString::Printf(TEXT("%s node x"), Label),
					Found->X,
					Pair.Value.X)
					|| !Test.TestEqual(
						*FString::Printf(TEXT("%s node y"), Label),
						Found->Y,
						Pair.Value.Y))
				{
					return false;
				}
			}
			return true;
		}

		bool ExpectFailure(
			FAutomationTestBase& Test,
			const TCHAR* Label,
			const TSharedPtr<FJsonObject>& Request,
			const TCHAR* ExpectedCode,
			UEdGraph* Graph,
			const TMap<FGuid, FIntPoint>& Before)
		{
			TSharedPtr<FJsonObject> Result;
			FString ErrorCode;
			FString ErrorMessage;
			const bool bApplied = UEAgentKitBlueprintGraphLayout::TryApply(
				Request,
				Result,
				ErrorCode,
				ErrorMessage);
			Test.TestFalse(
				*FString::Printf(TEXT("%s is rejected"), Label),
				bApplied);
			Test.TestEqual(
				*FString::Printf(TEXT("%s reports the expected error"), Label),
				ErrorCode,
				FString(ExpectedCode));
			if (!bApplied)
			{
				Test.TestTrue(
					*FString::Printf(TEXT("%s does not move any node"), Label),
					PositionsEqual(CapturePositions(Graph), Before));
			}
			return !bApplied && ErrorCode == ExpectedCode;
		}
	}

	IMPLEMENT_SIMPLE_AUTOMATION_TEST(
		FUEAgentKitGraphLayoutP5EditorTest,
		"UEAgentKit.GraphLayout.P5EditorFocusContract",
		EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

	bool FUEAgentKitGraphLayoutP5EditorTest::RunTest(const FString& Parameters)
	{
		FString Error;
		UBlueprint* Blueprint = LoadOrCreateFixture(Error);
		if (!TestNotNull(TEXT("P5 disposable Blueprint is loaded"), Blueprint))
		{
			AddError(Error);
			return false;
		}
		UEdGraph* Graph = GetPrimaryGraph(Blueprint);
		if (!TestNotNull(TEXT("P5 disposable ubergraph is present"), Graph))
		{
			return false;
		}
		if (!TestFalse(
			TEXT("P5 fixture starts clean"),
			Blueprint->GetOutermost()->IsDirty()))
		{
			return false;
		}

		TSharedPtr<FJsonObject> Plan = BuildPlan(Blueprint, Graph, Error);
		if (!TestTrue(TEXT("P5 plan is built"), Plan.IsValid()))
		{
			AddError(Error);
			return false;
		}
		TSharedPtr<FJsonObject> Request = BuildRequest(Blueprint, Graph, Plan, Error);
		if (!TestTrue(TEXT("P5 request is built"), Request.IsValid()))
		{
			AddError(Error);
			return false;
		}

		const TMap<FGuid, FIntPoint> BeforePositions = CapturePositions(Graph);
		const FString BeforeStructure = StructuralState(Graph);
		const FString BeforeTopology = TopologyHash(Graph);
		const int32 BeforePinCount = [&Graph]()
		{
			int32 Count = 0;
			for (const UEdGraphNode* Node : Graph->Nodes)
			{
				Count += Node != nullptr ? Node->Pins.Num() : 0;
			}
			return Count;
		}();
		const int32 BeforeLinkCount = [&Graph]()
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
					Count += Pin != nullptr ? Pin->LinkedTo.Num() : 0;
				}
			}
			return Count;
		}();
		TestEqual(TEXT("P5 fixture has five nodes"), BeforePositions.Num(), 5);

		// No editor is open yet: the exact target exists but cannot be applied.
		if (!ExpectFailure(
			*this,
			TEXT("TryApply without an open Blueprint Editor"),
			Request,
			TEXT("graph-layout-editor-not-open"),
			Graph,
			BeforePositions))
		{
			return false;
		}

		TSharedPtr<FJsonObject> NoEditorPreflight;
		FString NoEditorCode;
		FString NoEditorMessage;
		TestFalse(
			TEXT("focused-editor preflight without an open editor is rejected"),
			UEAgentKitBlueprintGraphLayout::BuildFocusedEditorPreflight(
				NoEditorPreflight,
				NoEditorCode,
				NoEditorMessage));
		TestEqual(
			TEXT("focused-editor preflight reports no open editor"),
			NoEditorCode,
			FString(TEXT("graph-layout-editor-not-open")));

		// A well-formed but unknown graph GUID is rejected before the editor gate.
		TSharedPtr<FJsonObject> WrongGuidRequest;
		{
			FString Text;
			const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Text);
			FJsonSerializer::Serialize(Request.ToSharedRef(), Writer);
			const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Text);
			FJsonSerializer::Deserialize(Reader, WrongGuidRequest);
		}
		WrongGuidRequest->SetStringField(
			TEXT("graphGuid"),
			GuidLower(FGuid::NewGuid()));
		if (!ExpectFailure(
			*this,
			TEXT("TryApply with an unknown graph GUID"),
			WrongGuidRequest,
			TEXT("graph-layout-graph-not-found"),
			Graph,
			BeforePositions))
		{
			return false;
		}

		FBlueprintEditor* Editor = OpenAndFocus(Blueprint, Graph, Error);
		if (!TestNotNull(TEXT("the real Blueprint Editor opens and focuses the exact graph"), Editor))
		{
			AddError(FString::Printf(
				TEXT("P5 could not create a focused Blueprint Editor: %s"),
				*Error));
			return false;
		}

		TSharedPtr<FJsonObject> FocusedPreflight;
		FString ErrorCode;
		FString ErrorMessage;
		if (!TestTrue(
			TEXT("focused-editor read-only preflight succeeds"),
			UEAgentKitBlueprintGraphLayout::BuildFocusedEditorPreflight(
				FocusedPreflight,
				ErrorCode,
				ErrorMessage)))
		{
			AddError(FString::Printf(
				TEXT("BuildFocusedEditorPreflight failed: %s %s"),
				*ErrorCode,
				*ErrorMessage));
			return false;
		}
		TestEqual(
			TEXT("focused preflight reports the exact Blueprint"),
			FocusedPreflight->GetStringField(TEXT("assetPath")),
			Blueprint->GetPathName());
		TestEqual(
			TEXT("focused preflight reports the exact focused graph"),
			FocusedPreflight->GetStringField(TEXT("graphGuid")),
			GuidLower(Graph->GraphGuid));
		TestTrue(
			TEXT("focused preflight is marked read-only"),
			FocusedPreflight->GetBoolField(TEXT("readOnly")));
		TestTrue(
			TEXT("focused preflight reports an editable graph"),
			FocusedPreflight->GetBoolField(TEXT("editable")));
		TestEqual(
			TEXT("focused preflight is the BlueprintEditor toolkit"),
			FocusedPreflight->GetStringField(TEXT("editorName")),
			FString(TEXT("BlueprintEditor")));

		// Dirty package: TryApply must refuse before any transaction.
		UEdGraphNode* DirtyNode = Graph->Nodes.Num() > 0 ? Graph->Nodes[0] : nullptr;
		if (!TestNotNull(TEXT("P5 dirty probe node exists"), DirtyNode))
		{
			return false;
		}
		const int32 DirtyOriginalX = DirtyNode->NodePosX;
		Blueprint->Modify();
		Graph->Modify();
		DirtyNode->Modify();
		DirtyNode->NodePosX = DirtyOriginalX + 1;
		Blueprint->GetOutermost()->MarkPackageDirty();
		if (!ExpectFailure(
			*this,
			TEXT("TryApply on a dirty package"),
			Request,
			TEXT("graph-layout-package-dirty"),
			Graph,
			CapturePositions(Graph)))
		{
			return false;
		}
		DirtyNode->NodePosX = DirtyOriginalX;
		if (!TestTrue(TEXT("P5 dirty probe is restored and saved"), SaveBlueprint(Blueprint, Error)))
		{
			AddError(Error);
			return false;
		}
		TestFalse(
			TEXT("P5 dirty probe save clears the dirty flag"),
			Blueprint->GetOutermost()->IsDirty());

		// Another Editor transaction already active: layout must not nest.
		{
			FScopedTransaction Barrier(
				LOCTEXT("P5Barrier", "P5 Active Transaction Barrier"));
			if (!TestTrue(TEXT("the P5 barrier transaction is outstanding"), Barrier.IsOutstanding()))
			{
				return false;
			}
			if (!ExpectFailure(
				*this,
				TEXT("TryApply while another transaction is active"),
				Request,
				TEXT("graph-layout-transaction-active"),
				Graph,
				BeforePositions))
			{
				Barrier.Cancel();
				return false;
			}
			Barrier.Cancel();
		}

		// Wrong focused graph: the Blueprint editor is open, but on the UCS graph.
		UEdGraph* SecondaryGraph = Blueprint->FunctionGraphs.Num() > 0
			? Blueprint->FunctionGraphs[0]
			: nullptr;
		if (!TestNotNull(TEXT("the P5 Blueprint has a secondary function graph"), SecondaryGraph))
		{
			return false;
		}
		const TSharedPtr<SGraphEditor> SecondaryEditor =
			Editor->OpenGraphAndBringToFront(SecondaryGraph, true);
		if (!TestTrue(TEXT("the secondary graph can be focused"), SecondaryEditor.IsValid()))
		{
			return false;
		}
		if (!TestTrue(
			TEXT("the secondary graph is now focused"),
			Editor->GetFocusedGraph() == SecondaryGraph))
		{
			return false;
		}
		if (!ExpectFailure(
			*this,
			TEXT("TryApply while the wrong graph is focused"),
			Request,
			TEXT("graph-layout-graph-not-focused"),
			Graph,
			BeforePositions))
		{
			return false;
		}

		TSharedPtr<FJsonObject> SecondaryPreflight;
		FString SecondaryCode;
		FString SecondaryMessage;
		if (!TestTrue(
			TEXT("focused-editor preflight reads the secondary focused graph"),
			UEAgentKitBlueprintGraphLayout::BuildFocusedEditorPreflight(
				SecondaryPreflight,
				SecondaryCode,
				SecondaryMessage)))
		{
			AddError(FString::Printf(
				TEXT("BuildFocusedEditorPreflight on the secondary graph failed: %s %s"),
				*SecondaryCode,
				*SecondaryMessage));
			return false;
		}
		TestEqual(
			TEXT("focused-editor preflight follows the exact focused graph"),
			SecondaryPreflight->GetStringField(TEXT("graphGuid")),
			GuidLower(SecondaryGraph->GraphGuid));
		TestTrue(
			TEXT("secondary focused-editor preflight is read-only"),
			SecondaryPreflight->GetBoolField(TEXT("readOnly")));

		Editor = OpenAndFocus(Blueprint, Graph, Error);
		if (!TestNotNull(TEXT("the exact target graph can be focused again"), Editor))
		{
			AddError(Error);
			return false;
		}

		// Rebuild the request against the post-save clean revision.
		Plan = BuildPlan(Blueprint, Graph, Error);
		if (!TestTrue(TEXT("P5 plan is rebuilt after fail-closed probes"), Plan.IsValid()))
		{
			AddError(Error);
			return false;
		}
		Request = BuildRequest(Blueprint, Graph, Plan, Error);
		if (!TestTrue(TEXT("P5 request is rebuilt after fail-closed probes"), Request.IsValid()))
		{
			AddError(Error);
			return false;
		}

		TSharedPtr<FJsonObject> Preflight;
		if (!TestTrue(
			TEXT("pre-apply preflight succeeds"),
			UEAgentKitBlueprintGraphLayout::BuildPreflight(
				Blueprint,
				Graph,
				Preflight,
				ErrorCode,
				ErrorMessage)))
		{
			AddError(FString::Printf(TEXT("pre-apply preflight failed: %s %s"), *ErrorCode, *ErrorMessage));
			return false;
		}
		const FString PreRevision = Preflight->GetStringField(TEXT("packageRevision"));
		const FString PreFingerprint = Preflight->GetStringField(TEXT("graphFingerprint"));

		TMap<FGuid, FIntPoint> Targets;
		{
			const TArray<TSharedPtr<FJsonValue>>* Positions = nullptr;
			if (!TestTrue(
				TEXT("P5 plan has positions"),
				Plan->TryGetArrayField(TEXT("positions"), Positions) && Positions != nullptr))
			{
				return false;
			}
			for (const TSharedPtr<FJsonValue>& Entry : *Positions)
			{
				const TSharedPtr<FJsonObject> Position = Entry->AsObject();
				FGuid Guid;
				FGuid::Parse(Position->GetStringField(TEXT("nodeId")).Mid(2), Guid);
				Targets.Add(
					Guid,
					FIntPoint(
						static_cast<int32>(Position->GetNumberField(TEXT("x"))),
						static_cast<int32>(Position->GetNumberField(TEXT("y")))));
			}
		}
		TestEqual(TEXT("P5 plan covers all five nodes"), Targets.Num(), 5);
		TestFalse(
			TEXT("P5 plan does not accidentally match the live positions"),
			PositionsEqual(Targets, BeforePositions));

		TSharedPtr<FJsonObject> Applied;
		if (!TestTrue(
			TEXT("TryApply succeeds on the open focused exact target"),
			UEAgentKitBlueprintGraphLayout::TryApply(
				Request,
				Applied,
				ErrorCode,
				ErrorMessage)))
		{
			AddError(FString::Printf(
				TEXT("P5 TryApply failed on the focused editor: %s %s"),
				*ErrorCode,
				*ErrorMessage));
			return false;
		}

		TestTrue(TEXT("TryApply reports changed"), Applied->GetBoolField(TEXT("changed")));
		TestTrue(TEXT("TryApply used one transaction"), Applied->GetBoolField(TEXT("singleTransaction")));
		TestFalse(TEXT("TryApply never saved"), Applied->GetBoolField(TEXT("saved")));
		TestFalse(TEXT("TryApply never compiled"), Applied->GetBoolField(TEXT("compiled")));
		TestEqual(
			TEXT("TryApply moved all five P5 nodes"),
			Applied->GetIntegerField(TEXT("movedNodeCount")),
			5);
		TestEqual(
			TEXT("TryApply did not change node identity, pins, sizes, comments or links"),
			StructuralState(Graph),
			BeforeStructure);
		TestEqual(
			TEXT("TryApply did not change topology"),
			TopologyHash(Graph),
			BeforeTopology);
		TestEqual(
			TEXT("TryApply preserved the total pin count"),
			[&Graph]()
			{
				int32 Count = 0;
				for (const UEdGraphNode* Node : Graph->Nodes)
				{
					Count += Node != nullptr ? Node->Pins.Num() : 0;
				}
				return Count;
			}(),
			BeforePinCount);
		TestEqual(
			TEXT("TryApply preserved the total link count"),
			[&Graph]()
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
						Count += Pin != nullptr ? Pin->LinkedTo.Num() : 0;
					}
				}
				return Count;
			}(),
			BeforeLinkCount);

		if (!VerifyPositions(*this, Graph, Targets, TEXT("P5 TryApply targets")))
		{
			return false;
		}
		TestTrue(
			TEXT("TryApply marks the package dirty"),
			Blueprint->GetOutermost()->IsDirty());

		if (!TestTrue(TEXT("P5 UndoTransaction succeeds"), GEditor->UndoTransaction()))
		{
			return false;
		}
		if (!VerifyPositions(*this, Graph, BeforePositions, TEXT("P5 Undo")))
		{
			return false;
		}
		TestEqual(
			TEXT("P5 Undo restores the exact structural state"),
			StructuralState(Graph),
			BeforeStructure);

		if (!TestTrue(TEXT("P5 RedoTransaction succeeds"), GEditor->RedoTransaction()))
		{
			return false;
		}
		if (!VerifyPositions(*this, Graph, Targets, TEXT("P5 Redo")))
		{
			return false;
		}
		TestEqual(
			TEXT("P5 Redo preserves topology"),
			TopologyHash(Graph),
			BeforeTopology);

		TSharedPtr<FJsonObject> AppliedPreflight;
		if (!TestTrue(
			TEXT("post-apply preflight succeeds"),
			UEAgentKitBlueprintGraphLayout::BuildPreflight(
				Blueprint,
				Graph,
				AppliedPreflight,
				ErrorCode,
				ErrorMessage)))
		{
			AddError(FString::Printf(TEXT("post-apply preflight failed: %s %s"), *ErrorCode, *ErrorMessage));
			return false;
		}
		const FString PostApplyFingerprint =
			AppliedPreflight->GetStringField(TEXT("graphFingerprint"));
		TestTrue(
			TEXT("P5 apply changed the live graph fingerprint"),
			PostApplyFingerprint != PreFingerprint);

		if (!TestTrue(TEXT("P5 fixture saves after TryApply"), SaveBlueprint(Blueprint, Error)))
		{
			AddError(Error);
			return false;
		}
		TestFalse(
			TEXT("P5 save clears the dirty flag"),
			Blueprint->GetOutermost()->IsDirty());

		TSharedPtr<FJsonObject> SavedPreflight;
		if (!TestTrue(
			TEXT("post-save preflight succeeds"),
			UEAgentKitBlueprintGraphLayout::BuildPreflight(
				Blueprint,
				Graph,
				SavedPreflight,
				ErrorCode,
				ErrorMessage)))
		{
			AddError(FString::Printf(TEXT("post-save preflight failed: %s %s"), *ErrorCode, *ErrorMessage));
			return false;
		}
		const FString PostRevision = SavedPreflight->GetStringField(TEXT("packageRevision"));
		TestTrue(TEXT("P5 save changed the package revision"), PostRevision != PreRevision);
		TestFalse(TEXT("P5 saved package is clean"), SavedPreflight->GetBoolField(TEXT("packageDirty")));
		TestEqual(
			TEXT("P5 save preserves the applied fingerprint"),
			SavedPreflight->GetStringField(TEXT("graphFingerprint")),
			PostApplyFingerprint);

		const FString AssetFilename = FPackageName::LongPackageNameToFilename(
			Blueprint->GetOutermost()->GetName(),
			FPackageName::GetAssetPackageExtension());
		const FString SavedFileHash = Sha256FileText(AssetFilename);
		TestTrue(TEXT("P5 saved asset hash is available"), SavedFileHash.StartsWith(TEXT("sha256:")));
		TestEqual(
			TEXT("P5 save revision equals the on-disk asset hash"),
			PostRevision,
			SavedFileHash);

		// Stale request after save must fail closed.
		if (!ExpectFailure(
			*this,
			TEXT("stale P5 request after save"),
			Request,
			TEXT("graph-layout-revision-conflict"),
			Graph,
			Targets))
		{
			return false;
		}

		// Tampered position without a matching layout hash is rejected by shape.
		TSharedPtr<FJsonObject> TamperedRequest;
		{
			FString Text;
			const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Text);
			FJsonSerializer::Serialize(Request.ToSharedRef(), Writer);
			const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Text);
			FJsonSerializer::Deserialize(Reader, TamperedRequest);
		}
		TSharedPtr<FJsonObject> TamperedPlan =
			TamperedRequest->GetObjectField(TEXT("plan"));
		const TArray<TSharedPtr<FJsonValue>>* TamperedPositions = nullptr;
		TamperedPlan->TryGetArrayField(TEXT("positions"), TamperedPositions);
		if (!TestNotNull(TEXT("P5 tampered plan has positions"), TamperedPositions))
		{
			return false;
		}
		(*TamperedPositions)[0]->AsObject()->SetNumberField(
			TEXT("x"),
			(*TamperedPositions)[0]->AsObject()->GetNumberField(TEXT("x")) + 5000.0);
		if (!ExpectFailure(
			*this,
			TEXT("tampered P5 position without a new layout hash"),
			TamperedRequest,
			TEXT("graph-layout-layout-hash-mismatch"),
			Graph,
			Targets))
		{
			return false;
		}

		// Closing the Blueprint Editor under -NullRHI tears down a Slate window
		// and trips GetRestoredDimensions; the no-editor path was already
		// exercised before the editor was opened, so the test leaves it open
		// and lets process shutdown own teardown.

		TSharedRef<FJsonObject> Evidence = MakeShared<FJsonObject>();
		Evidence->SetStringField(TEXT("test"), TEXT("UEAgentKit.GraphLayout.P5EditorFocusContract"));
		Evidence->SetStringField(TEXT("assetPath"), Blueprint->GetPathName());
		Evidence->SetStringField(TEXT("graphGuid"), GuidLower(Graph->GraphGuid));
		Evidence->SetStringField(TEXT("editorName"), TEXT("BlueprintEditor"));
		Evidence->SetBoolField(TEXT("editorWasOpened"), true);
		Evidence->SetBoolField(TEXT("graphFocusVerified"), true);
		Evidence->SetBoolField(TEXT("focusedPreflightReadOnly"), true);
		Evidence->SetStringField(TEXT("prePackageRevision"), PreRevision);
		Evidence->SetStringField(TEXT("postPackageRevision"), PostRevision);
		Evidence->SetStringField(TEXT("preGraphFingerprint"), PreFingerprint);
		Evidence->SetStringField(TEXT("postGraphFingerprint"), PostApplyFingerprint);
		Evidence->SetStringField(TEXT("topologyHashBefore"), BeforeTopology);
		Evidence->SetStringField(TEXT("topologyHashAfter"), TopologyHash(Graph));
		Evidence->SetStringField(TEXT("savedAssetSha256"), SavedFileHash);
		Evidence->SetNumberField(TEXT("nodeCount"), BeforePositions.Num());
		Evidence->SetNumberField(TEXT("movedNodeCount"), Applied->GetIntegerField(TEXT("movedNodeCount")));
		Evidence->SetNumberField(TEXT("pinCountBefore"), BeforePinCount);
		Evidence->SetNumberField(TEXT("linkCountBefore"), BeforeLinkCount);
		Evidence->SetBoolField(TEXT("undoVerified"), true);
		Evidence->SetBoolField(TEXT("redoVerified"), true);
		Evidence->SetBoolField(TEXT("saved"), true);
		Evidence->SetBoolField(TEXT("wrongFocusRejected"), true);
		Evidence->SetBoolField(TEXT("dirtyRejected"), true);
		Evidence->SetBoolField(TEXT("activeTransactionRejected"), true);
		Evidence->SetBoolField(TEXT("staleRevisionRejected"), true);
		Evidence->SetBoolField(TEXT("tamperedPlanRejected"), true);
		Evidence->SetBoolField(TEXT("noEditorRejected"), true);
		Evidence->SetObjectField(TEXT("preflight"), Preflight);
		Evidence->SetObjectField(TEXT("focusedPreflight"), FocusedPreflight);
		Evidence->SetObjectField(TEXT("request"), Request);
		Evidence->SetObjectField(TEXT("applyResult"), Applied);

		TArray<TSharedPtr<FJsonValue>> BeforeItems;
		for (const TPair<FGuid, FIntPoint>& Pair : BeforePositions)
		{
			TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
			Item->SetStringField(TEXT("nodeGuid"), GuidLower(Pair.Key));
			Item->SetNumberField(TEXT("x"), Pair.Value.X);
			Item->SetNumberField(TEXT("y"), Pair.Value.Y);
			BeforeItems.Add(MakeShared<FJsonValueObject>(Item));
		}
		TArray<TSharedPtr<FJsonValue>> TargetItems;
		for (const TPair<FGuid, FIntPoint>& Pair : Targets)
		{
			TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
			Item->SetStringField(TEXT("nodeGuid"), GuidLower(Pair.Key));
			Item->SetNumberField(TEXT("x"), Pair.Value.X);
			Item->SetNumberField(TEXT("y"), Pair.Value.Y);
			TargetItems.Add(MakeShared<FJsonValueObject>(Item));
		}
		Evidence->SetArrayField(TEXT("beforePositions"), BeforeItems);
		Evidence->SetArrayField(TEXT("targets"), TargetItems);

		if (!TestTrue(
			TEXT("P5 apply evidence is written"),
			WriteEvidenceJson(ApplyEvidenceFile, Evidence, Error)))
		{
			AddError(Error);
			return false;
		}
		return true;
	}

	IMPLEMENT_SIMPLE_AUTOMATION_TEST(
		FUEAgentKitGraphLayoutP5ReloadTest,
		"UEAgentKit.GraphLayout.P5ReloadContract",
		EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

	bool FUEAgentKitGraphLayoutP5ReloadTest::RunTest(const FString& Parameters)
	{
		FString Error;
		TSharedPtr<FJsonObject> Evidence = ReadEvidenceJson(ApplyEvidenceFile, Error);
		if (!TestTrue(TEXT("P5 apply evidence is readable"), Evidence.IsValid()))
		{
			AddError(Error);
			return false;
		}
		const FString AssetPath = Evidence->GetStringField(TEXT("assetPath"));
		UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *AssetPath);
		if (!TestNotNull(TEXT("P5 saved Blueprint reloads in an independent process"), Blueprint))
		{
			return false;
		}
		TestFalse(
			TEXT("independently reloaded P5 package is clean"),
			Blueprint->GetOutermost()->IsDirty());

		FGuid GraphGuid;
		if (!TestTrue(
			TEXT("P5 evidence graph GUID parses"),
			FGuid::Parse(Evidence->GetStringField(TEXT("graphGuid")), GraphGuid)))
		{
			return false;
		}
		UEdGraph* Graph = FindExactGraph(Blueprint, GraphGuid);
		if (!TestNotNull(TEXT("P5 graph is found by exact GUID after reload"), Graph))
		{
			return false;
		}

		TSharedPtr<FJsonObject> Preflight;
		FString ErrorCode;
		FString ErrorMessage;
		if (!TestTrue(
			TEXT("P5 reload preflight succeeds"),
			UEAgentKitBlueprintGraphLayout::BuildPreflight(
				Blueprint,
				Graph,
				Preflight,
				ErrorCode,
				ErrorMessage)))
		{
			AddError(FString::Printf(TEXT("P5 reload preflight failed: %s %s"), *ErrorCode, *ErrorMessage));
			return false;
		}
		TestEqual(
			TEXT("reloaded P5 revision equals the saved apply revision"),
			Preflight->GetStringField(TEXT("packageRevision")),
			Evidence->GetStringField(TEXT("postPackageRevision")));
		TestEqual(
			TEXT("reloaded P5 fingerprint equals the applied fingerprint"),
			Preflight->GetStringField(TEXT("graphFingerprint")),
			Evidence->GetStringField(TEXT("postGraphFingerprint")));
		TestEqual(
			TEXT("reloaded P5 topology equals the applied topology"),
			TopologyHash(Graph),
			Evidence->GetStringField(TEXT("topologyHashAfter")));

		const TArray<TSharedPtr<FJsonValue>>* Targets = nullptr;
		if (!TestTrue(
			TEXT("P5 evidence has target positions"),
			Evidence->TryGetArrayField(TEXT("targets"), Targets) && Targets != nullptr))
		{
			return false;
		}
		const TMap<FGuid, FIntPoint> LivePositions = CapturePositions(Graph);
		TestEqual(
			TEXT("reloaded P5 node count equals the evidence"),
			LivePositions.Num(),
			Targets->Num());
		for (const TSharedPtr<FJsonValue>& Entry : *Targets)
		{
			const TSharedPtr<FJsonObject> Target = Entry->AsObject();
			FGuid NodeGuid;
			TestTrue(
				TEXT("P5 target GUID parses"),
				FGuid::Parse(Target->GetStringField(TEXT("nodeGuid")), NodeGuid));
			const FIntPoint* Live = LivePositions.Find(NodeGuid);
			if (!TestNotNull(TEXT("P5 target node exists after reload"), Live))
			{
				return false;
			}
			TestEqual(
				TEXT("reloaded P5 node x equals the TryApply target"),
				Live->X,
				static_cast<int32>(Target->GetNumberField(TEXT("x"))));
			TestEqual(
				TEXT("reloaded P5 node y equals the TryApply target"),
				Live->Y,
				static_cast<int32>(Target->GetNumberField(TEXT("y"))));
		}

		TSharedRef<FJsonObject> ReloadEvidence = MakeShared<FJsonObject>();
		ReloadEvidence->SetStringField(TEXT("test"), TEXT("UEAgentKit.GraphLayout.P5ReloadContract"));
		ReloadEvidence->SetStringField(TEXT("assetPath"), AssetPath);
		ReloadEvidence->SetStringField(TEXT("graphGuid"), GuidLower(Graph->GraphGuid));
		ReloadEvidence->SetStringField(
			TEXT("packageRevision"),
			Preflight->GetStringField(TEXT("packageRevision")));
		ReloadEvidence->SetStringField(
			TEXT("graphFingerprint"),
			Preflight->GetStringField(TEXT("graphFingerprint")));
		ReloadEvidence->SetStringField(TEXT("topologyHash"), TopologyHash(Graph));
		ReloadEvidence->SetNumberField(TEXT("nodeCount"), LivePositions.Num());
		ReloadEvidence->SetBoolField(TEXT("independentReloadVerified"), true);
		if (!TestTrue(
			TEXT("P5 reload evidence is written"),
			WriteEvidenceJson(TEXT("p5-reload-result.json"), ReloadEvidence, Error)))
		{
			AddError(Error);
			return false;
		}
		return true;
	}
}

#undef LOCTEXT_NAMESPACE

#endif  // WITH_DEV_AUTOMATION_TESTS
