#include "BlueprintGraphLayoutApply.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "BlueprintContextExporter.h"
#include "BlueprintContextSha256.h"
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
#include "K2Node_Event.h"
#include "K2Node_ExecutionSequence.h"
#include "K2Node_Knot.h"
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

namespace UEAgentKitGraphLayoutP4
{
	namespace
	{
		const TCHAR* FixturePackageName = TEXT("/Game/UEAgentKitP4/BP_LayoutP4");
		const TCHAR* FixtureAssetName = TEXT("BP_LayoutP4");
		const TCHAR* FixtureAssetPath = TEXT("/Game/UEAgentKitP4/BP_LayoutP4.BP_LayoutP4");
		const TCHAR* PlanFullFile = TEXT("layout-full.json");
		const TCHAR* RequestFullFile = TEXT("apply-request-full.json");
		const TCHAR* RequestSelectionFile = TEXT("apply-request-selection.json");
		const TCHAR* RequestIncrementalFile = TEXT("apply-request-incremental.json");

		FString GuidLower(const FGuid& Guid)
		{
			return Guid.ToString(EGuidFormats::DigitsWithHyphensLower);
		}

		FString NodeId(const FGuid& Guid)
		{
			return FString(TEXT("n:")) + GuidLower(Guid);
		}

		bool ParseNodeId(const FString& Text, FGuid& OutGuid)
		{
			return Text.StartsWith(TEXT("n:"), ESearchCase::CaseSensitive)
				&& FGuid::Parse(Text.Mid(2), OutGuid);
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
				OutError = TEXT("Could not create the P4 disposable fixture package.");
				return nullptr;
			}

			UBlueprint* Blueprint = FKismetEditorUtilities::CreateBlueprint(
				AActor::StaticClass(),
				Package,
				FName(FixtureAssetName),
				BPTYPE_Normal,
				FName(TEXT("UEAgentKitP4Fixture")));
			if (Blueprint == nullptr)
			{
				OutError = TEXT("Could not create the P4 disposable K2 Blueprint.");
				return nullptr;
			}

			UEdGraph* Graph = GetPrimaryGraph(Blueprint);
			if (Graph == nullptr)
			{
				OutError = TEXT("The P4 disposable Blueprint has no ubergraph.");
				return nullptr;
			}

			struct FSequenceSpec
			{
				int32 X;
				int32 Y;
				int32 Width;
				int32 Height;
				const TCHAR* Comment;
			};
			const FSequenceSpec Specs[] = {
				{-780, 20, 200, 120, TEXT("P4 branch root")},
				{-260, -240, 210, 130, TEXT("P4 branch upper")},
				{-220, 280, 190, 110, TEXT("P4 branch lower")},
				{320, -220, 230, 140, TEXT("P4 upper join")},
				{780, 260, 200, 120, TEXT("P4 lower tail")},
			};
			TArray<UK2Node_ExecutionSequence*> Sequences;
			for (const FSequenceSpec& Spec : Specs)
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

			FGraphNodeCreator<UK2Node_Knot> KnotCreator(*Graph);
			UK2Node_Knot* Knot = KnotCreator.CreateNode(false);
			Knot->NodePosX = 40;
			Knot->NodePosY = 60;
			KnotCreator.Finalize();
			Knot->NodeWidth = 30;
			Knot->NodeHeight = 30;
			Knot->NodeComment = TEXT("P4 reroute between the two branches");

			FGraphNodeCreator<UEdGraphNode_Comment> CommentCreator(*Graph);
			UEdGraphNode_Comment* Comment = CommentCreator.CreateNode(false);
			Comment->NodePosX = -860;
			Comment->NodePosY = -360;
			CommentCreator.Finalize();
			Comment->NodeWidth = 760;
			Comment->NodeHeight = 760;
			Comment->NodeComment = TEXT("P4 disposable algorithm fixture");

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

			UEdGraphPin* RootExecute = Sequences[0]->FindPin(UEdGraphSchema_K2::PN_Execute, EGPD_Input);
			UEdGraphPin* EventThen = BeginPlay != nullptr
				? BeginPlay->FindPin(UEdGraphSchema_K2::PN_Then, EGPD_Output)
				: nullptr;
			if (EventThen != nullptr && !LinkPins(Graph, EventThen, RootExecute))
			{
				OutError = TEXT("Could not link BeginPlay to the P4 branch root.");
				return nullptr;
			}

			UEdGraphPin* UpperExecute =
				Sequences[1]->FindPin(UEdGraphSchema_K2::PN_Execute, EGPD_Input);
			UEdGraphPin* LowerExecute =
				Sequences[2]->FindPin(UEdGraphSchema_K2::PN_Execute, EGPD_Input);
			UEdGraphPin* JoinExecute =
				Sequences[3]->FindPin(UEdGraphSchema_K2::PN_Execute, EGPD_Input);
			UEdGraphPin* TailExecute =
				Sequences[4]->FindPin(UEdGraphSchema_K2::PN_Execute, EGPD_Input);

			if (!LinkPins(Graph, Sequences[0]->GetThenPinGivenIndex(0), UpperExecute)
				|| !LinkPins(Graph, Sequences[0]->GetThenPinGivenIndex(1), Knot->GetInputPin())
				|| !LinkPins(Graph, Knot->GetOutputPin(), LowerExecute)
				|| !LinkPins(Graph, Sequences[1]->GetThenPinGivenIndex(0), JoinExecute)
				|| !LinkPins(Graph, Sequences[2]->GetThenPinGivenIndex(0), TailExecute))
			{
				OutError = TEXT("Could not create the P4 branch/reroute link topology.");
				return nullptr;
			}

			FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
			FKismetEditorUtilities::CompileBlueprint(
				Blueprint,
				EBlueprintCompileOptions::SkipGarbageCollection);
			if (Blueprint->Status == BS_Error)
			{
				OutError = TEXT("The P4 disposable fixture Blueprint failed to compile.");
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
					OutError = TEXT("The existing P4 fixture is dirty; rebuild the disposable host.");
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

		TSharedPtr<FJsonObject> CloneJsonObject(
			const TSharedPtr<FJsonObject>& Object,
			FString& OutError)
		{
			if (!Object.IsValid())
			{
				OutError = TEXT("No JSON object to clone.");
				return nullptr;
			}
			FString Text;
			const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Text);
			if (!FJsonSerializer::Serialize(Object.ToSharedRef(), Writer))
			{
				OutError = TEXT("Could not serialize the JSON clone.");
				return nullptr;
			}
			TSharedPtr<FJsonObject> Clone;
			const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Text);
			if (!FJsonSerializer::Deserialize(Reader, Clone) || !Clone.IsValid())
			{
				OutError = TEXT("Could not parse the JSON clone.");
				return nullptr;
			}
			return Clone;
		}

		bool ReadEvidenceText(const TCHAR* FileName, FString& OutText)
		{
			return FFileHelper::LoadFileToString(OutText, *EvidencePath(FileName));
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

		TMap<FGuid, UEdGraphNode*> MapLiveNodes(UEdGraph* Graph)
		{
			TMap<FGuid, UEdGraphNode*> Result;
			if (Graph != nullptr)
			{
				for (UEdGraphNode* Node : Graph->Nodes)
				{
					if (Node != nullptr)
					{
						Result.Add(Node->NodeGuid, Node);
					}
				}
			}
			return Result;
		}

		/**
		 * Position-free structural state.  The algorithm apply must change only
		 * NodePosX/NodePosY: GUIDs, classes, comments, sizes, pin identities and
		 * links must remain byte-identical before and after.
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

		bool ExportFixture(
			UBlueprint* Blueprint,
			UEdGraph* Graph,
			TSharedPtr<FJsonObject>& OutContext,
			TSharedPtr<FJsonObject>& OutPreflight,
			TSharedPtr<FJsonObject>& OutProvenance,
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
			if (!ExportResult.bSuccess || ExportResult.JsonPath.IsEmpty())
			{
				OutError = TEXT("Canonical export reported success without a JSON path.");
				return false;
			}

			FString ContextText;
			if (!FFileHelper::LoadFileToString(ContextText, *ExportResult.JsonPath))
			{
				OutError = FString::Printf(TEXT("Could not read exporter JSON: %s"), *ExportResult.JsonPath);
				return false;
			}
			const FString ContextPath = EvidencePath(TEXT("blueprint-context.json"));
			if (!FFileHelper::SaveStringToFile(
				ContextText,
				*ContextPath,
				FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
			{
				OutError = FString::Printf(TEXT("Could not copy exporter JSON to %s."), *ContextPath);
				return false;
			}

			OutContext = ReadJsonFile(ContextPath, OutError);
			if (!OutContext.IsValid())
			{
				return false;
			}

			FString ErrorCode;
			FString ErrorMessage;
			if (!UEAgentKitBlueprintGraphLayout::BuildPreflight(
				Blueprint,
				Graph,
				OutPreflight,
				ErrorCode,
				ErrorMessage))
			{
				OutError = FString::Printf(TEXT("BuildPreflight failed: %s %s"), *ErrorCode, *ErrorMessage);
				return false;
			}
			const FString PreflightPath = EvidencePath(TEXT("preflight.json"));
			if (!WriteJson(PreflightPath, OutPreflight, OutError))
			{
				return false;
			}

			const FString DllPath = ModuleDllPath();
			if (DllPath.IsEmpty() || !FPaths::FileExists(DllPath))
			{
				OutError = FString::Printf(TEXT("Could not resolve the loaded plugin binary: %s"), *DllPath);
				return false;
			}
			const FString PluginBuildHash = Sha256FileText(DllPath);
			if (!PluginBuildHash.StartsWith(TEXT("sha256:")) || PluginBuildHash.Len() != 71)
			{
				OutError = FString::Printf(TEXT("Could not hash the loaded plugin binary: %s"), *DllPath);
				return false;
			}

			OutProvenance = MakeShared<FJsonObject>();
			OutProvenance->SetStringField(TEXT("schemaVersion"), TEXT("ueak.p4.exportprovenance/1.0"));
			OutProvenance->SetStringField(TEXT("kind"), TEXT("live-editor"));
			OutProvenance->SetStringField(TEXT("sessionId"), GuidLower(FGuid::NewGuid()));
			OutProvenance->SetStringField(TEXT("pluginBuildHash"), PluginBuildHash);
			OutProvenance->SetStringField(TEXT("exportedAtUtc"), FDateTime::UtcNow().ToIso8601());
			OutProvenance->SetStringField(TEXT("engineVersion"), FEngineVersion::Current().ToString());
			OutProvenance->SetStringField(TEXT("projectName"), FApp::GetProjectName());
			OutProvenance->SetStringField(TEXT("exporterSchemaVersion"), TEXT("1.1"));
			OutProvenance->SetStringField(TEXT("exporterVersion"), TEXT("0.8.0"));
			OutProvenance->SetStringField(TEXT("profile"), TEXT("full"));
			OutProvenance->SetStringField(TEXT("assetPath"), Blueprint->GetPathName());
			OutProvenance->SetStringField(TEXT("graphGuid"), GuidLower(Graph->GraphGuid));
			OutProvenance->SetStringField(TEXT("graphName"), Graph->GetName());
			OutProvenance->SetStringField(TEXT("contextFile"), TEXT("blueprint-context.json"));
			OutProvenance->SetStringField(TEXT("contextSha256"), Sha256FileText(ContextPath));
			OutProvenance->SetStringField(TEXT("preflightFile"), TEXT("preflight.json"));
			OutProvenance->SetStringField(TEXT("preflightSha256"), Sha256FileText(PreflightPath));
			OutProvenance->SetStringField(TEXT("pluginBinary"), DllPath);
			OutProvenance->SetNumberField(TEXT("nodeCount"), ExportResult.NodeCount);
			OutProvenance->SetNumberField(TEXT("pinCount"), ExportResult.PinCount);
			OutProvenance->SetNumberField(TEXT("linkCount"), ExportResult.LinkCount);
			if (!WriteEvidenceJson(TEXT("export-provenance.json"), OutProvenance, OutError))
			{
				return false;
			}
			return true;
		}

		bool ValidateContextShape(
			FAutomationTestBase& Test,
			const TSharedPtr<FJsonObject>& Context,
			const TSharedPtr<FJsonObject>& Preflight,
			const FGuid& ExpectedGraphGuid)
		{
			Test.TestEqual(
				TEXT("canonical exporter schemaVersion"),
				Context->GetStringField(TEXT("schemaVersion")),
				FString(TEXT("1.1")));
			Test.TestEqual(
				TEXT("canonical exporter profile"),
				Context->GetStringField(TEXT("profile")),
				FString(TEXT("full")));
			Test.TestEqual(
				TEXT("canonical exporter assetPath"),
				Context->GetStringField(TEXT("assetPath")),
				FString(FixtureAssetPath));

			const TSharedPtr<FJsonObject>* Revision = nullptr;
			if (!Test.TestTrue(
				TEXT("canonical exporter has a revision object"),
				Context->TryGetObjectField(TEXT("revision"), Revision)
					&& Revision != nullptr && (*Revision).IsValid()))
			{
				return false;
			}
			Test.TestTrue(TEXT("canonical revision is available"), (*Revision)->GetBoolField(TEXT("available")));
			Test.TestFalse(TEXT("canonical revision is clean"), (*Revision)->GetBoolField(TEXT("packageDirty")));
			Test.TestEqual(
				TEXT("canonical package revision equals live preflight revision"),
				(*Revision)->GetStringField(TEXT("value")),
				Preflight->GetStringField(TEXT("packageRevision")));

			const TArray<TSharedPtr<FJsonValue>>* Graphs = nullptr;
			if (!Test.TestTrue(
				TEXT("canonical exporter has a graphs array"),
				Context->TryGetArrayField(TEXT("graphs"), Graphs) && Graphs != nullptr))
			{
				return false;
			}
			if (!Test.TestEqual(TEXT("canonical export has exactly one filtered graph"), Graphs->Num(), 1))
			{
				return false;
			}
			const TSharedPtr<FJsonObject> Graph = (*Graphs)[0]->AsObject();
			Test.TestEqual(
				TEXT("canonical graph kind is uber"),
				Graph->GetStringField(TEXT("kind")),
				FString(TEXT("uber")));
			Test.TestEqual(
				TEXT("canonical graph GUID is the live graph"),
				Graph->GetStringField(TEXT("guid")),
				GuidLower(ExpectedGraphGuid));
			Test.TestEqual(
				TEXT("canonical graph schema is K2"),
				Graph->GetStringField(TEXT("schema")),
				FString(TEXT("/Script/BlueprintGraph.EdGraphSchema_K2")));

			const TArray<TSharedPtr<FJsonValue>>* Nodes = nullptr;
			if (!Test.TestTrue(
				TEXT("canonical graph has nodes"),
				Graph->TryGetArrayField(TEXT("nodes"), Nodes) && Nodes != nullptr && !Nodes->IsEmpty()))
			{
				return false;
			}
			const TArray<TSharedPtr<FJsonValue>>* PreflightNodes = nullptr;
			if (!Test.TestTrue(
				TEXT("preflight has nodes"),
				Preflight->TryGetArrayField(TEXT("nodes"), PreflightNodes)
					&& PreflightNodes != nullptr))
			{
				return false;
			}
			Test.TestEqual(
				TEXT("canonical node count equals live preflight node count"),
				Nodes->Num(),
				PreflightNodes->Num());

			TSet<FString> NodeGuids;
			TMap<FString, FString> PinOwnerNode;
			TMap<FString, FString> PinDirection;
			TMap<FString, TSharedPtr<FJsonObject>> PinById;
			for (const TSharedPtr<FJsonValue>& NodeValue : *Nodes)
			{
				const TSharedPtr<FJsonObject> Node = NodeValue->AsObject();
				const FString NodeGuid = Node->GetStringField(TEXT("guid"));
				Test.TestFalse(TEXT("canonical node GUID is unique"), NodeGuids.Contains(NodeGuid));
				NodeGuids.Add(NodeGuid);

				const TSharedPtr<FJsonObject>* Layout = nullptr;
				if (!Test.TestTrue(
					TEXT("canonical node has layout"),
					Node->TryGetObjectField(TEXT("layout"), Layout)
						&& Layout != nullptr && (*Layout).IsValid()))
				{
					return false;
				}
				Test.TestTrue(TEXT("canonical layout x is a number"), (*Layout)->HasField(TEXT("x")));
				Test.TestTrue(TEXT("canonical layout y is a number"), (*Layout)->HasField(TEXT("y")));
				Test.TestTrue(TEXT("canonical layout width is a number"), (*Layout)->HasField(TEXT("width")));
				Test.TestTrue(TEXT("canonical layout height is a number"), (*Layout)->HasField(TEXT("height")));

				const TArray<TSharedPtr<FJsonValue>>* Pins = nullptr;
				if (!Test.TestTrue(
					TEXT("canonical node has a pins array"),
					Node->TryGetArrayField(TEXT("pins"), Pins) && Pins != nullptr))
				{
					return false;
				}
				for (const TSharedPtr<FJsonValue>& PinValue : *Pins)
				{
					const TSharedPtr<FJsonObject> Pin = PinValue->AsObject();
					const FString PinId = Pin->GetStringField(TEXT("id"));
					Test.TestFalse(TEXT("canonical pin id is unique"), PinById.Contains(PinId));
					PinById.Add(PinId, Pin);
					PinOwnerNode.Add(PinId, NodeGuid);
					PinDirection.Add(PinId, Pin->GetStringField(TEXT("direction")));
				}
			}

			int32 OutputLinkCount = 0;
			for (const TSharedPtr<FJsonValue>& NodeValue : *Nodes)
			{
				const TSharedPtr<FJsonObject> Node = NodeValue->AsObject();
				const FString NodeGuid = Node->GetStringField(TEXT("guid"));
				const TArray<TSharedPtr<FJsonValue>>* Pins = nullptr;
				Node->TryGetArrayField(TEXT("pins"), Pins);
				for (const TSharedPtr<FJsonValue>& PinValue : *Pins)
				{
					const TSharedPtr<FJsonObject> Pin = PinValue->AsObject();
					const FString PinId = Pin->GetStringField(TEXT("id"));
					const TArray<TSharedPtr<FJsonValue>>* Links = nullptr;
					if (!Test.TestTrue(
						TEXT("canonical pin has a links array"),
						Pin->TryGetArrayField(TEXT("links"), Links) && Links != nullptr))
					{
						return false;
					}
					for (const TSharedPtr<FJsonValue>& LinkValue : *Links)
					{
						const TSharedPtr<FJsonObject> Link = LinkValue->AsObject();
						const FString TargetPinId = Link->GetStringField(TEXT("targetPinId"));
						const FString TargetNodeGuid = Link->GetStringField(TEXT("targetNodeGuid"));
						if (!Test.TestTrue(
							TEXT("canonical link target pin exists"),
							PinById.Contains(TargetPinId)))
						{
							return false;
						}
						Test.TestEqual(
							TEXT("canonical link target node owns the target pin"),
							PinOwnerNode.FindChecked(TargetPinId),
							TargetNodeGuid);
						const FString SourceDirection = PinDirection.FindChecked(PinId);
						const FString TargetDirection = PinDirection.FindChecked(TargetPinId);
						if (SourceDirection == TEXT("output"))
						{
							Test.TestFalse(
								TEXT("canonical output link points at an input pin"),
								TargetDirection == TEXT("output"));
							++OutputLinkCount;
							const TSharedPtr<FJsonObject> TargetPin = PinById.FindChecked(TargetPinId);
							const TArray<TSharedPtr<FJsonValue>>* BackLinks = nullptr;
							TargetPin->TryGetArrayField(TEXT("links"), BackLinks);
							bool bMirrored = false;
							if (BackLinks != nullptr)
							{
								for (const TSharedPtr<FJsonValue>& BackValue : *BackLinks)
								{
									const TSharedPtr<FJsonObject> Back = BackValue->AsObject();
									if (Back->GetStringField(TEXT("targetPinId")) == PinId)
									{
										bMirrored = true;
										break;
									}
								}
							}
							if (!Test.TestTrue(TEXT("canonical link is mirrored on the input pin"), bMirrored))
							{
								return false;
							}
						}
						else
						{
							Test.TestFalse(
								TEXT("canonical input link points at an output pin"),
								TargetDirection == TEXT("input"));
						}
					}
				}
			}
			Test.TestTrue(TEXT("canonical export contains at least one real link"), OutputLinkCount > 0);

			const TArray<TSharedPtr<FJsonValue>>* CommentNodes = nullptr;
			int32 CommentCount = 0;
			if (Preflight->TryGetArrayField(TEXT("nodes"), CommentNodes) && CommentNodes != nullptr)
			{
				for (const TSharedPtr<FJsonValue>& NodeValue : *CommentNodes)
				{
					if (NodeValue->AsObject()->GetStringField(TEXT("kind")) == TEXT("comment"))
					{
						++CommentCount;
					}
				}
			}
			Test.TestTrue(TEXT("canonical fixture has a real comment node"), CommentCount >= 1);
			Test.TestTrue(
				TEXT("canonical export has a reroute node"),
				Preflight->GetIntegerField(TEXT("rerouteCount")) >= 1);
			return true;
		}

		bool ReadPlanRequest(
			const TCHAR* FileName,
			TSharedPtr<FJsonObject>& OutRequest,
			TSharedPtr<FJsonObject>& OutPlan,
			FString& OutError)
		{
			OutRequest = ReadEvidenceJson(FileName, OutError);
			if (!OutRequest.IsValid())
			{
				return false;
			}
			const TSharedPtr<FJsonObject>* Plan = nullptr;
			if (!OutRequest->TryGetObjectField(TEXT("plan"), Plan)
				|| Plan == nullptr || !(*Plan).IsValid())
			{
				OutError = FString::Printf(TEXT("%s has no plan object."), FileName);
				return false;
			}
			OutPlan = *Plan;
			return true;
		}

		bool GetPlanTargets(
			const TSharedPtr<FJsonObject>& Plan,
			TMap<FGuid, FIntPoint>& OutTargets,
			FString& OutError)
		{
			const TArray<TSharedPtr<FJsonValue>>* Positions = nullptr;
			if (!Plan->TryGetArrayField(TEXT("positions"), Positions) || Positions == nullptr)
			{
				OutError = TEXT("Plan has no positions array.");
				return false;
			}
			for (const TSharedPtr<FJsonValue>& Value : *Positions)
			{
				const TSharedPtr<FJsonObject> Item = Value->AsObject();
				FGuid Guid;
				if (!ParseNodeId(Item->GetStringField(TEXT("nodeId")), Guid))
				{
					OutError = TEXT("Plan position has an invalid nodeId.");
					return false;
				}
				OutTargets.Add(
					Guid,
					FIntPoint(
						static_cast<int32>(Item->GetNumberField(TEXT("x"))),
						static_cast<int32>(Item->GetNumberField(TEXT("y")))));
			}
			return true;
		}

		TSet<FGuid> GetPlanGuidSet(
			const TSharedPtr<FJsonObject>& Plan,
			const TCHAR* FieldName)
		{
			TSet<FGuid> Result;
			const TArray<TSharedPtr<FJsonValue>>* Values = nullptr;
			if (Plan->TryGetArrayField(FieldName, Values) && Values != nullptr)
			{
				for (const TSharedPtr<FJsonValue>& Value : *Values)
				{
					FGuid Guid;
					if (Value->Type == EJson::String && ParseNodeId(Value->AsString(), Guid))
					{
						Result.Add(Guid);
					}
				}
			}
			return Result;
		}

		bool CheckRequestIdentity(
			FAutomationTestBase& Test,
			const TSharedPtr<FJsonObject>& Request,
			const TSharedPtr<FJsonObject>& Preflight,
			FGuid& OutGraphGuid)
		{
			Test.TestEqual(
				TEXT("request schemaVersion"),
				Request->GetStringField(TEXT("schemaVersion")),
				FString(TEXT("ueak.graphlayoutapply/1.0")));
			Test.TestEqual(
				TEXT("request assetPath"),
				Request->GetStringField(TEXT("assetPath")),
				FString(FixtureAssetPath));
			Test.TestEqual(
				TEXT("request expected revision equals live preflight"),
				Request->GetStringField(TEXT("expectedPackageRevision")),
				Preflight->GetStringField(TEXT("packageRevision")));
			Test.TestEqual(
				TEXT("request expected fingerprint equals live preflight"),
				Request->GetStringField(TEXT("expectedGraphFingerprint")),
				Preflight->GetStringField(TEXT("graphFingerprint")));
			Test.TestFalse(TEXT("request does not allow save"), Request->GetBoolField(TEXT("allowSave")));
			Test.TestFalse(TEXT("request does not allow compile"), Request->GetBoolField(TEXT("allowCompile")));
			Test.TestFalse(
				TEXT("request is not acceptance eligible"),
				Request->GetBoolField(TEXT("acceptanceEligible")));
			Test.TestEqual(
				TEXT("request still requires an independent reload"),
				Request->GetStringField(TEXT("requiredAcceptanceEvidence")),
				FString(TEXT("independent-reload")));
			Test.TestTrue(
				TEXT("request graph GUID parses"),
				FGuid::Parse(Request->GetStringField(TEXT("graphGuid")), OutGraphGuid));
			return true;
		}

		bool VerifyPositionsAgainstPlan(
			FAutomationTestBase& Test,
			UEdGraph* Graph,
			const TMap<FGuid, FIntPoint>& Targets,
			const TCHAR* Label)
		{
			const TMap<FGuid, UEdGraphNode*> Live = MapLiveNodes(Graph);
			if (!Test.TestEqual(*FString::Printf(TEXT("%s target count"), Label), Targets.Num(), Live.Num()))
			{
				return false;
			}
			for (const TPair<FGuid, FIntPoint>& Pair : Targets)
			{
				UEdGraphNode* const* Node = Live.Find(Pair.Key);
				if (!Test.TestNotNull(*FString::Printf(TEXT("%s target node exists"), Label), Node))
				{
					return false;
				}
				Test.TestEqual(
					*FString::Printf(TEXT("%s x equals the algorithm target"), Label),
					(*Node)->NodePosX,
					Pair.Value.X);
				Test.TestEqual(
					*FString::Printf(TEXT("%s y equals the algorithm target"), Label),
					(*Node)->NodePosY,
					Pair.Value.Y);
			}
			return true;
		}

		bool VerifyPositionsEqual(
			FAutomationTestBase& Test,
			UEdGraph* Graph,
			const TMap<FGuid, FIntPoint>& Expected,
			const TCHAR* Label)
		{
			const TMap<FGuid, UEdGraphNode*> Live = MapLiveNodes(Graph);
			for (const TPair<FGuid, FIntPoint>& Pair : Expected)
			{
				UEdGraphNode* const* Node = Live.Find(Pair.Key);
				if (!Test.TestNotNull(*FString::Printf(TEXT("%s node exists"), Label), Node))
				{
					return false;
				}
				Test.TestEqual(
					*FString::Printf(TEXT("%s x is restored"), Label),
					(*Node)->NodePosX,
					Pair.Value.X);
				Test.TestEqual(
					*FString::Printf(TEXT("%s y is restored"), Label),
					(*Node)->NodePosY,
					Pair.Value.Y);
			}
			return true;
		}

		void AddPositions(
			const TMap<FGuid, FIntPoint>& Positions,
			TArray<TSharedPtr<FJsonValue>>& OutArray)
		{
			for (const TPair<FGuid, FIntPoint>& Pair : Positions)
			{
				TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
				Item->SetStringField(TEXT("nodeGuid"), GuidLower(Pair.Key));
				Item->SetNumberField(TEXT("x"), Pair.Value.X);
				Item->SetNumberField(TEXT("y"), Pair.Value.Y);
				OutArray.Add(MakeShared<FJsonValueObject>(Item));
			}
		}
	}
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitGraphLayoutP4ExportTest,
	"UEAgentKit.GraphLayout.P4ExportContract",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitGraphLayoutP4ExportTest::RunTest(const FString& Parameters)
{
	(void)Parameters;
	using namespace UEAgentKitGraphLayoutP4;

	FString Error;
	UBlueprint* Blueprint = LoadOrCreateFixture(Error);
	if (!TestNotNull(TEXT("P4 disposable fixture exists"), Blueprint))
	{
		AddError(Error);
		return false;
	}
	UEdGraph* Graph = GetPrimaryGraph(Blueprint);
	if (!TestNotNull(TEXT("P4 fixture has an ubergraph"), Graph))
	{
		return false;
	}
	TestFalse(TEXT("P4 fixture starts clean"), Blueprint->GetOutermost()->IsDirty());

	TSharedPtr<FJsonObject> Context;
	TSharedPtr<FJsonObject> Preflight;
	TSharedPtr<FJsonObject> Provenance;
	if (!TestTrue(
		TEXT("real canonical export and live preflight succeed"),
		ExportFixture(Blueprint, Graph, Context, Preflight, Provenance, Error)))
	{
		AddError(Error);
		return false;
	}
	if (!ValidateContextShape(*this, Context, Preflight, Graph->GraphGuid))
	{
		return false;
	}

	TestEqual(
		TEXT("preflight assetPath"),
		Preflight->GetStringField(TEXT("assetPath")),
		FString(FixtureAssetPath));
	TestEqual(
		TEXT("preflight graphGuid"),
		Preflight->GetStringField(TEXT("graphGuid")),
		GuidLower(Graph->GraphGuid));
	TestEqual(
		TEXT("preflight evidence remains same-process"),
		Preflight->GetStringField(TEXT("evidenceLevel")),
		FString(TEXT("same-process")));
	TestFalse(TEXT("preflight is not acceptance eligible"), Preflight->GetBoolField(TEXT("acceptanceEligible")));
	TestTrue(
		TEXT("preflight fingerprint is sha1"),
		Preflight->GetStringField(TEXT("graphFingerprint")).StartsWith(TEXT("sha1:")));
	TestTrue(
		TEXT("preflight revision is sha256"),
		Preflight->GetStringField(TEXT("packageRevision")).StartsWith(TEXT("sha256:")));
	TestEqual(
		TEXT("preflight node count equals exported node count"),
		Preflight->GetIntegerField(TEXT("nodeCount")),
		static_cast<int32>(Context->GetArrayField(TEXT("graphs"))[0]->AsObject()->GetArrayField(TEXT("nodes")).Num()));
	TestTrue(TEXT("preflight reports the real comment"), Preflight->GetIntegerField(TEXT("commentCount")) >= 1);
	TestTrue(TEXT("preflight reports the real reroute"), Preflight->GetIntegerField(TEXT("rerouteCount")) >= 1);

	TestEqual(
		TEXT("provenance uses live-editor kind"),
		Provenance->GetStringField(TEXT("kind")),
		FString(TEXT("live-editor")));
	TestTrue(
		TEXT("provenance records a real session id"),
		!Provenance->GetStringField(TEXT("sessionId")).IsEmpty());
	TestEqual(
		TEXT("provenance hashes the loaded plugin binary"),
		Provenance->GetStringField(TEXT("pluginBuildHash")),
		Sha256FileText(ModuleDllPath()));
	TestEqual(
		TEXT("provenance hashes the canonical context file"),
		Provenance->GetStringField(TEXT("contextSha256")),
		Sha256FileText(EvidencePath(TEXT("blueprint-context.json"))));
	TestEqual(
		TEXT("provenance hashes the preflight file"),
		Provenance->GetStringField(TEXT("preflightSha256")),
		Sha256FileText(EvidencePath(TEXT("preflight.json"))));

	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("test"), TEXT("UEAgentKit.GraphLayout.P4ExportContract"));
	Result->SetStringField(TEXT("assetPath"), FString(FixtureAssetPath));
	Result->SetStringField(TEXT("graphGuid"), GuidLower(Graph->GraphGuid));
	Result->SetStringField(TEXT("sessionId"), Provenance->GetStringField(TEXT("sessionId")));
	Result->SetStringField(TEXT("pluginBuildHash"), Provenance->GetStringField(TEXT("pluginBuildHash")));
	Result->SetStringField(TEXT("contextSha256"), Provenance->GetStringField(TEXT("contextSha256")));
	Result->SetStringField(TEXT("preflightSha256"), Provenance->GetStringField(TEXT("preflightSha256")));
	Result->SetStringField(TEXT("packageRevision"), Preflight->GetStringField(TEXT("packageRevision")));
	Result->SetStringField(TEXT("graphFingerprint"), Preflight->GetStringField(TEXT("graphFingerprint")));
	Result->SetNumberField(TEXT("nodeCount"), Preflight->GetIntegerField(TEXT("nodeCount")));
	Result->SetNumberField(TEXT("commentCount"), Preflight->GetIntegerField(TEXT("commentCount")));
	Result->SetNumberField(TEXT("rerouteCount"), Preflight->GetIntegerField(TEXT("rerouteCount")));
	Result->SetStringField(TEXT("topologyHash"), TopologyHash(Graph));
	if (!TestTrue(TEXT("P4 export evidence is written"), WriteEvidenceJson(TEXT("export-result.json"), Result, Error)))
	{
		AddError(Error);
		return false;
	}
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitGraphLayoutP4ApplyTest,
	"UEAgentKit.GraphLayout.P4ApplyContract",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitGraphLayoutP4ApplyTest::RunTest(const FString& Parameters)
{
	(void)Parameters;
	using namespace UEAgentKitGraphLayoutP4;

	FString Error;
	TSharedPtr<FJsonObject> Request;
	TSharedPtr<FJsonObject> Plan;
	if (!TestTrue(
		TEXT("algorithm-generated full apply request exists"),
		ReadPlanRequest(RequestFullFile, Request, Plan, Error)))
	{
		AddError(Error);
		return false;
	}

	TestEqual(
		TEXT("full plan mode"),
		Plan->GetStringField(TEXT("mode")),
		FString(TEXT("full")));
	TestEqual(
		TEXT("full plan status"),
		Plan->GetStringField(TEXT("status")),
		FString(TEXT("ok")));
	TestTrue(
		TEXT("full plan is not acceptance eligible"),
		!Plan->GetBoolField(TEXT("acceptanceEligible")));
	TestEqual(
		TEXT("full plan came from the real algorithm"),
		Plan->GetStringField(TEXT("requiredAcceptanceEvidence")),
		FString(TEXT("independent-reload")));

	TMap<FGuid, FIntPoint> Targets;
	if (!TestTrue(TEXT("full plan targets parse"), GetPlanTargets(Plan, Targets, Error)))
	{
		AddError(Error);
		return false;
	}
	const TSet<FGuid> PlanMoved = GetPlanGuidSet(Plan, TEXT("movedNodeIds"));
	TestTrue(TEXT("the algorithm actually moves nodes"), PlanMoved.Num() >= 3);
	TestEqual(
		TEXT("the plan covers every target node"),
		Targets.Num(),
		Plan->GetArrayField(TEXT("positions")).Num());

	UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, FixtureAssetPath);
	if (!TestNotNull(TEXT("P4 fixture loads in the apply process"), Blueprint))
	{
		return false;
	}
	FGuid GraphGuid;
	if (!TestTrue(TEXT("request graph GUID parses"), FGuid::Parse(Request->GetStringField(TEXT("graphGuid")), GraphGuid)))
	{
		return false;
	}
	UEdGraph* Graph = FindExactGraph(Blueprint, GraphGuid);
	if (!TestNotNull(TEXT("execution graph resolves by exact GUID"), Graph))
	{
		return false;
	}

	TSharedPtr<FJsonObject> Preflight;
	FString ErrorCode;
	FString ErrorMessage;
	if (!TestTrue(
		TEXT("apply preflight succeeds"),
		UEAgentKitBlueprintGraphLayout::BuildPreflight(
			Blueprint,
			Graph,
			Preflight,
			ErrorCode,
			ErrorMessage)))
	{
		AddError(FString::Printf(TEXT("apply preflight failed: %s %s"), *ErrorCode, *ErrorMessage));
		return false;
	}
	if (!CheckRequestIdentity(*this, Request, Preflight, GraphGuid))
	{
		return false;
	}
	const FString PreRevision = Preflight->GetStringField(TEXT("packageRevision"));
	const FString PreFingerprint = Preflight->GetStringField(TEXT("graphFingerprint"));
	TestFalse(TEXT("apply starts from a clean package"), Preflight->GetBoolField(TEXT("packageDirty")));

	const TMap<FGuid, FIntPoint> BeforePositions = CapturePositions(Graph);
	const FString BeforeStructure = StructuralState(Graph);
	const FString BeforeTopology = TopologyHash(Graph);
	const FString LayoutHash = Plan->GetStringField(TEXT("layoutHash"));
	const FString PlanId = Plan->GetStringField(TEXT("planId"));

	// Prove the Python plan is not a fabricated uniform shift: at least two
	// distinct deltas and at least one node that keeps its original position.
	TSet<FString> Deltas;
	int32 UnchangedNodes = 0;
	for (const TPair<FGuid, FIntPoint>& Pair : Targets)
	{
		const FIntPoint* Before = BeforePositions.Find(Pair.Key);
		if (Before == nullptr)
		{
			AddError(TEXT("Plan contains a node the live graph does not have."));
			return false;
		}
		const FIntPoint Delta(Pair.Value.X - Before->X, Pair.Value.Y - Before->Y);
		Deltas.Add(FString::Printf(TEXT("%d,%d"), Delta.X, Delta.Y));
		if (Delta.X == 0 && Delta.Y == 0)
		{
			++UnchangedNodes;
		}
	}
	TestTrue(TEXT("algorithm output is not a single uniform shift"), Deltas.Num() >= 2);
	TestTrue(TEXT("algorithm output keeps at least one node fixed"), UnchangedNodes >= 1);

	// The relaxed comment gate must still reject a comment that moves away from
	// a fixed node.  This is the C++ counterpart of the Python negative test.
	FString CommentNodeId;
	const TArray<TSharedPtr<FJsonValue>>* PreflightNodes = nullptr;
	Preflight->TryGetArrayField(TEXT("nodes"), PreflightNodes);
	if (PreflightNodes != nullptr)
	{
		for (const TSharedPtr<FJsonValue>& NodeValue : *PreflightNodes)
		{
			if (NodeValue->AsObject()->GetStringField(TEXT("kind")) == TEXT("comment"))
			{
				CommentNodeId = NodeValue->AsObject()->GetStringField(TEXT("nodeId"));
				break;
			}
		}
	}
	if (!TestTrue(TEXT("the plan contains a real comment node"), !CommentNodeId.IsEmpty()))
	{
		return false;
	}
	TSharedPtr<FJsonObject> TamperedRequest = CloneJsonObject(Request, Error);
	if (!TestTrue(TEXT("the algorithm request can be cloned for a negative gate"), TamperedRequest.IsValid()))
	{
		AddError(Error);
		return false;
	}
	const TSharedPtr<FJsonObject>* TamperedPlanField = nullptr;
	if (!TestTrue(
		TEXT("the cloned request retains its plan"),
		TamperedRequest->TryGetObjectField(TEXT("plan"), TamperedPlanField)
			&& TamperedPlanField != nullptr && (*TamperedPlanField).IsValid()))
	{
		return false;
	}
	TSharedPtr<FJsonObject> TamperedPlan = *TamperedPlanField;
	const TArray<TSharedPtr<FJsonValue>>* TamperedPositions = nullptr;
	TamperedPlan->TryGetArrayField(TEXT("positions"), TamperedPositions);
	bool bShiftedComment = false;
	if (TamperedPositions != nullptr)
	{
		for (const TSharedPtr<FJsonValue>& PositionValue : *TamperedPositions)
		{
			const TSharedPtr<FJsonObject> Position = PositionValue->AsObject();
			if (Position->GetStringField(TEXT("nodeId")) == CommentNodeId)
			{
				Position->SetNumberField(TEXT("x"), Position->GetNumberField(TEXT("x")) + 100000.0);
				bShiftedComment = true;
				break;
			}
		}
	}
	if (!TestTrue(TEXT("the comment target can be moved far away"), bShiftedComment))
	{
		return false;
	}
	FString TamperedHash;
	if (!TestTrue(
		TEXT("the tampered plan layout hash can be recomputed"),
		UEAgentKitBlueprintGraphLayout::ComputeLayoutHash(TamperedPlan, TamperedHash)))
	{
		return false;
	}
	TamperedPlan->SetStringField(TEXT("layoutHash"), TamperedHash);
	TSharedPtr<FJsonObject> TamperedResult;
	TestFalse(
		TEXT("a comment that leaves a fixed node behind is rejected"),
		UEAgentKitBlueprintGraphLayout::ApplyToGraph(
			Blueprint,
			Graph,
			TamperedRequest,
			TamperedResult,
			ErrorCode,
			ErrorMessage));
	TestEqual(
		TEXT("tampered comment fails on the comment gate"),
		ErrorCode,
		FString(TEXT("graph-layout-comment-partial-scope")));
	if (!VerifyPositionsEqual(*this, Graph, BeforePositions, TEXT("comment gate rejection")))
	{
		return false;
	}

	TSharedPtr<FJsonObject> Applied;
	if (!TestTrue(
		TEXT("ApplyToGraph accepts the algorithm request"),
		UEAgentKitBlueprintGraphLayout::ApplyToGraph(
			Blueprint,
			Graph,
			Request,
			Applied,
			ErrorCode,
			ErrorMessage)))
	{
		AddError(FString::Printf(TEXT("algorithm apply failed: %s %s"), *ErrorCode, *ErrorMessage));
		return false;
	}
	TestTrue(TEXT("algorithm apply reports changed"), Applied->GetBoolField(TEXT("changed")));
	TestTrue(TEXT("algorithm apply used one transaction"), Applied->GetBoolField(TEXT("singleTransaction")));
	TestFalse(TEXT("algorithm apply never saved"), Applied->GetBoolField(TEXT("saved")));
	TestFalse(TEXT("algorithm apply never compiled"), Applied->GetBoolField(TEXT("compiled")));
	TestEqual(TEXT("applied planId is echoed"), Applied->GetStringField(TEXT("planId")), PlanId);
	TestEqual(TEXT("applied layoutHash is echoed"), Applied->GetStringField(TEXT("layoutHash")), LayoutHash);
	TestEqual(
		TEXT("moved node count equals the algorithm plan"),
		Applied->GetIntegerField(TEXT("movedNodeCount")),
		static_cast<int32>(PlanMoved.Num()));
	TestEqual(
		TEXT("apply changed no node identity, pin, size, comment or link"),
		StructuralState(Graph),
		BeforeStructure);
	TestEqual(
		TEXT("apply changed no topology"),
		TopologyHash(Graph),
		BeforeTopology);
	if (!VerifyPositionsAgainstPlan(*this, Graph, Targets, TEXT("full apply")))
	{
		return false;
	}

	if (!TestTrue(TEXT("UndoTransaction succeeds for the algorithm apply"), GEditor->UndoTransaction()))
	{
		return false;
	}
	if (!VerifyPositionsEqual(*this, Graph, BeforePositions, TEXT("algorithm undo")))
	{
		return false;
	}
	TestEqual(
		TEXT("undo restores the exact structural state"),
		StructuralState(Graph),
		BeforeStructure);

	if (!TestTrue(TEXT("RedoTransaction succeeds for the algorithm apply"), GEditor->RedoTransaction()))
	{
		return false;
	}
	if (!VerifyPositionsAgainstPlan(*this, Graph, Targets, TEXT("algorithm redo")))
	{
		return false;
	}
	TestEqual(
		TEXT("redo changes no topology"),
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
	const FString AppliedFingerprint = AppliedPreflight->GetStringField(TEXT("graphFingerprint"));
	TestTrue(TEXT("algorithm apply changed the live fingerprint"), AppliedFingerprint != PreFingerprint);

	if (!TestTrue(TEXT("P4 fixture saves after algorithm apply"), SaveBlueprint(Blueprint, Error)))
	{
		AddError(Error);
		return false;
	}
	TestFalse(TEXT("save clears the dirty flag"), Blueprint->GetOutermost()->IsDirty());

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
	TestTrue(TEXT("save changed the package revision"), PostRevision != PreRevision);
	TestFalse(TEXT("saved package is clean"), SavedPreflight->GetBoolField(TEXT("packageDirty")));
	TestEqual(
		TEXT("save preserves the applied fingerprint"),
		SavedPreflight->GetStringField(TEXT("graphFingerprint")),
		AppliedFingerprint);

	// A request built against the pre-save revision must fail closed.
	const TMap<FGuid, FIntPoint> SavedPositions = CapturePositions(Graph);
	TSharedPtr<FJsonObject> StaleResult;
	TestFalse(
		TEXT("stale algorithm request is rejected after save"),
		UEAgentKitBlueprintGraphLayout::ApplyToGraph(
			Blueprint,
			Graph,
			Request,
			StaleResult,
			ErrorCode,
			ErrorMessage));
	TestEqual(
		TEXT("stale request fails on the package revision"),
		ErrorCode,
		FString(TEXT("graph-layout-revision-conflict")));
	if (!VerifyPositionsEqual(*this, Graph, SavedPositions, TEXT("stale rejection")))
	{
		return false;
	}

	TArray<TSharedPtr<FJsonValue>> TargetArray;
	AddPositions(Targets, TargetArray);
	TArray<TSharedPtr<FJsonValue>> BeforeArray;
	AddPositions(BeforePositions, BeforeArray);

	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("test"), TEXT("UEAgentKit.GraphLayout.P4ApplyContract"));
	Result->SetStringField(TEXT("assetPath"), FString(FixtureAssetPath));
	Result->SetStringField(TEXT("graphGuid"), GuidLower(Graph->GraphGuid));
	Result->SetStringField(TEXT("mode"), TEXT("full"));
	Result->SetStringField(TEXT("planId"), PlanId);
	Result->SetStringField(TEXT("layoutHash"), LayoutHash);
	Result->SetStringField(TEXT("baseGraphHash"), Plan->GetStringField(TEXT("baseGraphHash")));
	Result->SetStringField(TEXT("requestSha256"), Sha256FileText(EvidencePath(RequestFullFile)));
	Result->SetStringField(TEXT("planSha256"), Sha256FileText(EvidencePath(PlanFullFile)));
	Result->SetStringField(TEXT("preRevision"), PreRevision);
	Result->SetStringField(TEXT("postRevision"), PostRevision);
	Result->SetStringField(TEXT("preFingerprint"), PreFingerprint);
	Result->SetStringField(TEXT("postFingerprint"), AppliedFingerprint);
	Result->SetStringField(TEXT("topologyHashBefore"), BeforeTopology);
	Result->SetStringField(TEXT("topologyHashAfter"), TopologyHash(Graph));
	Result->SetNumberField(TEXT("beforeTopologyNodeCount"), BeforePositions.Num());
	Result->SetNumberField(TEXT("nodeCount"), Targets.Num());
	Result->SetNumberField(TEXT("movedNodeCount"), PlanMoved.Num());
	Result->SetArrayField(TEXT("movedNodeIds"), Plan->GetArrayField(TEXT("movedNodeIds")));
	Result->SetArrayField(TEXT("targets"), TargetArray);
	Result->SetArrayField(TEXT("beforePositions"), BeforeArray);
	Result->SetBoolField(TEXT("undoRestored"), true);
	Result->SetBoolField(TEXT("redoReapplied"), true);
	Result->SetBoolField(TEXT("saved"), true);
	Result->SetBoolField(TEXT("packageDirtyAfterSave"), false);
	Result->SetBoolField(TEXT("staleAfterSaveRejected"), true);
	Result->SetBoolField(TEXT("acceptanceEligible"), false);
	Result->SetStringField(TEXT("requiredAcceptanceEvidence"), TEXT("independent-reload"));
	if (!TestTrue(TEXT("P4 algorithm apply evidence is written"), WriteEvidenceJson(TEXT("apply-result.json"), Result, Error)))
	{
		AddError(Error);
		return false;
	}
	return true;
}

namespace UEAgentKitGraphLayoutP4
{
	namespace
	{
		bool RunScopedApply(
			FAutomationTestBase& Test,
			const TCHAR* RequestFile,
			const TCHAR* Mode,
			const TCHAR* EvidenceFile)
		{
			FString Error;
			TSharedPtr<FJsonObject> Request;
			TSharedPtr<FJsonObject> Plan;
			if (!Test.TestTrue(
				*FString::Printf(TEXT("%s apply request exists"), Mode),
				ReadPlanRequest(RequestFile, Request, Plan, Error)))
			{
				Test.AddError(Error);
				return false;
			}
			Test.TestEqual(
				*FString::Printf(TEXT("%s plan mode"), Mode),
				Plan->GetStringField(TEXT("mode")),
				FString(Mode));
			Test.TestEqual(
				*FString::Printf(TEXT("%s plan status"), Mode),
				Plan->GetStringField(TEXT("status")),
				FString(TEXT("ok")));

			TMap<FGuid, FIntPoint> Targets;
			if (!Test.TestTrue(
				*FString::Printf(TEXT("%s targets parse"), Mode),
				GetPlanTargets(Plan, Targets, Error)))
			{
				Test.AddError(Error);
				return false;
			}
			const TSet<FGuid> PlanMoved = GetPlanGuidSet(Plan, TEXT("movedNodeIds"));
			Test.TestTrue(
				*FString::Printf(TEXT("%s algorithm moves scoped nodes"), Mode),
				PlanMoved.Num() > 0);
			const TSet<FGuid> Scope = GetPlanGuidSet(Plan, TEXT("scopeNodeIds"));
			bool bMovedInsideScope = true;
			for (const FGuid& MovedGuid : PlanMoved)
			{
				bMovedInsideScope &= Scope.Contains(MovedGuid);
			}
			Test.TestTrue(
				*FString::Printf(TEXT("%s moved nodes stay inside scope"), Mode),
				bMovedInsideScope);

			UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, FixtureAssetPath);
			if (!Test.TestNotNull(*FString::Printf(TEXT("%s fixture loads"), Mode), Blueprint))
			{
				return false;
			}
			FGuid GraphGuid;
			FGuid::Parse(Request->GetStringField(TEXT("graphGuid")), GraphGuid);
			UEdGraph* Graph = FindExactGraph(Blueprint, GraphGuid);
			if (!Test.TestNotNull(*FString::Printf(TEXT("%s graph resolves"), Mode), Graph))
			{
				return false;
			}
			TSharedPtr<FJsonObject> Preflight;
			FString ErrorCode;
			FString ErrorMessage;
			if (!Test.TestTrue(
				*FString::Printf(TEXT("%s preflight succeeds"), Mode),
				UEAgentKitBlueprintGraphLayout::BuildPreflight(
					Blueprint,
					Graph,
					Preflight,
					ErrorCode,
					ErrorMessage)))
			{
				Test.AddError(FString::Printf(TEXT("%s preflight failed: %s %s"), Mode, *ErrorCode, *ErrorMessage));
				return false;
			}
			if (!CheckRequestIdentity(Test, Request, Preflight, GraphGuid))
			{
				return false;
			}
			if (!Test.TestTrue(
				*FString::Printf(TEXT("%s starts from a clean package"), Mode),
				!Preflight->GetBoolField(TEXT("packageDirty"))))
			{
				return false;
			}

			const TMap<FGuid, FIntPoint> Before = CapturePositions(Graph);
			const FString BeforeStructure = StructuralState(Graph);
			const FString BeforeTopology = TopologyHash(Graph);
			const FString PreFingerprint = Preflight->GetStringField(TEXT("graphFingerprint"));

			TSharedPtr<FJsonObject> Applied;
			if (!Test.TestTrue(
				*FString::Printf(TEXT("%s ApplyToGraph succeeds"), Mode),
				UEAgentKitBlueprintGraphLayout::ApplyToGraph(
					Blueprint,
					Graph,
					Request,
					Applied,
					ErrorCode,
					ErrorMessage)))
			{
				Test.AddError(FString::Printf(TEXT("%s apply failed: %s %s"), Mode, *ErrorCode, *ErrorMessage));
				return false;
			}
			Test.TestTrue(
				*FString::Printf(TEXT("%s changed only within scope"), Mode),
				Applied->GetIntegerField(TEXT("movedNodeCount")) == PlanMoved.Num());
			Test.TestEqual(
				*FString::Printf(TEXT("%s changes no topology"), Mode),
				StructuralState(Graph),
				BeforeStructure);
			Test.TestEqual(
				*FString::Printf(TEXT("%s topology hash is stable"), Mode),
				TopologyHash(Graph),
				BeforeTopology);
			if (!VerifyPositionsAgainstPlan(Test, Graph, Targets, Mode))
			{
				return false;
			}
			for (const TPair<FGuid, FIntPoint>& Pair : Before)
			{
				if (PlanMoved.Contains(Pair.Key))
				{
					continue;
				}
				const TMap<FGuid, UEdGraphNode*> Live = MapLiveNodes(Graph);
				UEdGraphNode* const* Node = Live.Find(Pair.Key);
				Test.TestEqual(
					*FString::Printf(TEXT("%s keeps out-of-scope x fixed"), Mode),
					(*Node)->NodePosX,
					Pair.Value.X);
				Test.TestEqual(
					*FString::Printf(TEXT("%s keeps out-of-scope y fixed"), Mode),
					(*Node)->NodePosY,
					Pair.Value.Y);
			}

			if (!Test.TestTrue(*FString::Printf(TEXT("%s UndoTransaction succeeds"), Mode), GEditor->UndoTransaction()))
			{
				return false;
			}
			if (!VerifyPositionsEqual(Test, Graph, Before, Mode))
			{
				return false;
			}
			Test.TestEqual(
				*FString::Printf(TEXT("%s undo restores topology"), Mode),
				StructuralState(Graph),
				BeforeStructure);

			TSharedPtr<FJsonObject> RestoredPreflight;
			if (!Test.TestTrue(
				*FString::Printf(TEXT("%s restored preflight succeeds"), Mode),
				UEAgentKitBlueprintGraphLayout::BuildPreflight(
					Blueprint,
					Graph,
					RestoredPreflight,
					ErrorCode,
					ErrorMessage)))
			{
				return false;
			}
			Test.TestEqual(
				*FString::Printf(TEXT("%s undo restores the fingerprint"), Mode),
				RestoredPreflight->GetStringField(TEXT("graphFingerprint")),
				PreFingerprint);

			TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
			Result->SetStringField(TEXT("test"), FString(Mode));
			Result->SetStringField(TEXT("assetPath"), FString(FixtureAssetPath));
			Result->SetStringField(TEXT("graphGuid"), GuidLower(GraphGuid));
			Result->SetStringField(TEXT("mode"), FString(Mode));
			Result->SetStringField(TEXT("planId"), Plan->GetStringField(TEXT("planId")));
			Result->SetStringField(TEXT("layoutHash"), Plan->GetStringField(TEXT("layoutHash")));
			Result->SetStringField(TEXT("preRevision"), Preflight->GetStringField(TEXT("packageRevision")));
			Result->SetStringField(TEXT("preFingerprint"), PreFingerprint);
			Result->SetStringField(TEXT("topologyHash"), BeforeTopology);
			Result->SetNumberField(TEXT("nodeCount"), Targets.Num());
			Result->SetNumberField(TEXT("movedNodeCount"), PlanMoved.Num());
			Result->SetArrayField(TEXT("movedNodeIds"), Plan->GetArrayField(TEXT("movedNodeIds")));
			Result->SetArrayField(TEXT("scopeNodeIds"), Plan->GetArrayField(TEXT("scopeNodeIds")));
			Result->SetBoolField(TEXT("undoRestored"), true);
			Result->SetBoolField(TEXT("saved"), false);
			Result->SetBoolField(TEXT("discardedOnExit"), true);
			Result->SetBoolField(TEXT("acceptanceEligible"), false);
			if (!Test.TestTrue(
				*FString::Printf(TEXT("%s scoped evidence is written"), Mode),
				WriteEvidenceJson(EvidenceFile, Result, Error)))
			{
				Test.AddError(Error);
				return false;
			}
			return true;
		}
	}
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitGraphLayoutP4SelectionTest,
	"UEAgentKit.GraphLayout.P4SelectionApplyContract",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitGraphLayoutP4SelectionTest::RunTest(const FString& Parameters)
{
	(void)Parameters;
	return UEAgentKitGraphLayoutP4::RunScopedApply(
		*this,
		TEXT("apply-request-selection.json"),
		TEXT("selection"),
		TEXT("apply-selection-result.json"));
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitGraphLayoutP4IncrementalTest,
	"UEAgentKit.GraphLayout.P4IncrementalApplyContract",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitGraphLayoutP4IncrementalTest::RunTest(const FString& Parameters)
{
	(void)Parameters;
	return UEAgentKitGraphLayoutP4::RunScopedApply(
		*this,
		TEXT("apply-request-incremental.json"),
		TEXT("incremental"),
		TEXT("apply-incremental-result.json"));
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitGraphLayoutP4ReloadTest,
	"UEAgentKit.GraphLayout.P4ReloadContract",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitGraphLayoutP4ReloadTest::RunTest(const FString& Parameters)
{
	(void)Parameters;
	using namespace UEAgentKitGraphLayoutP4;

	FString Error;
	const TSharedPtr<FJsonObject> Evidence = ReadEvidenceJson(TEXT("apply-result.json"), Error);
	if (!TestTrue(TEXT("P4 apply evidence exists for the reload process"), Evidence.IsValid()))
	{
		AddError(Error);
		return false;
	}
	const FString AssetPath = Evidence->GetStringField(TEXT("assetPath"));
	const FString GraphGuidText = Evidence->GetStringField(TEXT("graphGuid"));
	const FString PostRevision = Evidence->GetStringField(TEXT("postRevision"));
	const FString PostFingerprint = Evidence->GetStringField(TEXT("postFingerprint"));
	const FString ExpectedTopology = Evidence->GetStringField(TEXT("topologyHashAfter"));

	UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *AssetPath);
	if (!TestNotNull(TEXT("saved P4 fixture loads in the second process"), Blueprint))
	{
		return false;
	}
	FGuid GraphGuid;
	if (!TestTrue(TEXT("saved P4 graph GUID parses"), FGuid::Parse(GraphGuidText, GraphGuid)))
	{
		return false;
	}
	UEdGraph* Graph = FindExactGraph(Blueprint, GraphGuid);
	if (!TestNotNull(TEXT("saved P4 graph is found by exact GUID"), Graph))
	{
		return false;
	}

	TSharedPtr<FJsonObject> Preflight;
	FString ErrorCode;
	FString ErrorMessage;
	if (!TestTrue(
		TEXT("P4 reload preflight succeeds"),
		UEAgentKitBlueprintGraphLayout::BuildPreflight(
			Blueprint,
			Graph,
			Preflight,
			ErrorCode,
			ErrorMessage)))
	{
		AddError(FString::Printf(TEXT("P4 reload preflight failed: %s %s"), *ErrorCode, *ErrorMessage));
		return false;
	}
	TestFalse(TEXT("reloaded P4 package is clean"), Preflight->GetBoolField(TEXT("packageDirty")));
	TestEqual(
		TEXT("reloaded P4 revision equals the saved algorithm result"),
		Preflight->GetStringField(TEXT("packageRevision")),
		PostRevision);
	TestEqual(
		TEXT("reloaded P4 fingerprint equals the post-apply value"),
		Preflight->GetStringField(TEXT("graphFingerprint")),
		PostFingerprint);
	TestEqual(
		TEXT("reloaded P4 topology equals the applied topology"),
		TopologyHash(Graph),
		ExpectedTopology);

	const TArray<TSharedPtr<FJsonValue>>* Targets = nullptr;
	if (!TestTrue(
		TEXT("P4 apply evidence has algorithm targets"),
		Evidence->TryGetArrayField(TEXT("targets"), Targets) && Targets != nullptr))
	{
		return false;
	}
	const TMap<FGuid, UEdGraphNode*> LiveNodes = MapLiveNodes(Graph);
	TestEqual(TEXT("reloaded P4 node count matches evidence"), LiveNodes.Num(), Targets->Num());
	for (const TSharedPtr<FJsonValue>& Entry : *Targets)
	{
		const TSharedPtr<FJsonObject> Target = Entry->AsObject();
		FGuid Guid;
		TestTrue(TEXT("P4 target GUID parses"), FGuid::Parse(Target->GetStringField(TEXT("nodeGuid")), Guid));
		UEdGraphNode* const* Node = LiveNodes.Find(Guid);
		if (!TestNotNull(TEXT("P4 target node exists after reload"), Node))
		{
			return false;
		}
		TestEqual(
			TEXT("reloaded P4 x equals the algorithm target"),
			(*Node)->NodePosX,
			static_cast<int32>(Target->GetNumberField(TEXT("x"))));
		TestEqual(
			TEXT("reloaded P4 y equals the algorithm target"),
			(*Node)->NodePosY,
			static_cast<int32>(Target->GetNumberField(TEXT("y"))));
	}

	TSharedRef<FJsonObject> ReloadEvidence = MakeShared<FJsonObject>();
	ReloadEvidence->SetStringField(TEXT("test"), TEXT("UEAgentKit.GraphLayout.P4ReloadContract"));
	ReloadEvidence->SetStringField(TEXT("assetPath"), AssetPath);
	ReloadEvidence->SetStringField(TEXT("graphGuid"), GraphGuidText);
	ReloadEvidence->SetStringField(TEXT("packageRevision"), PostRevision);
	ReloadEvidence->SetStringField(TEXT("graphFingerprint"), PostFingerprint);
	ReloadEvidence->SetStringField(TEXT("topologyHash"), TopologyHash(Graph));
	ReloadEvidence->SetNumberField(TEXT("nodeCount"), LiveNodes.Num());
	ReloadEvidence->SetBoolField(TEXT("independentReloadVerified"), true);
	if (!TestTrue(
		TEXT("P4 reload evidence is written"),
		WriteEvidenceJson(TEXT("reload-result.json"), ReloadEvidence, Error)))
	{
		AddError(Error);
		return false;
	}
	return true;
}

#endif  // WITH_DEV_AUTOMATION_TESTS
