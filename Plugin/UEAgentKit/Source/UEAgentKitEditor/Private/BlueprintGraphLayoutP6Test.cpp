#include "BlueprintGraphLayoutApply.h"
#include "BlueprintContextExporter.h"
#include "BlueprintContextSha256.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "BlueprintEditor.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "EdGraphSchema_K2.h"
#include "Editor.h"
#include "Engine/Blueprint.h"
#include "GameFramework/Actor.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformProcess.h"
#include "Interfaces/IPluginManager.h"
#include "K2Node_Event.h"
#include "K2Node_ExecutionSequence.h"
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
#include "Subsystems/AssetEditorSubsystem.h"
#include "Toolkits/IToolkitHost.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"

#if WITH_DEV_AUTOMATION_TESTS

#define LOCTEXT_NAMESPACE "UEAgentKitGraphLayoutP6"

namespace UEAgentKitGraphLayoutP6
{
	namespace
	{
		const TCHAR* FixturePackageName = TEXT("/Game/UEAgentKitP6/BP_LayoutP6");
		const TCHAR* FixtureAssetName = TEXT("BP_LayoutP6");
		const TCHAR* FixtureAssetPath = TEXT("/Game/UEAgentKitP6/BP_LayoutP6.BP_LayoutP6");
		const TCHAR* FixtureExpectedFile = TEXT("p6-fixture-expected.json");
		const TCHAR* ContextFile = TEXT("p6-context.json");
		const TCHAR* PreflightFile = TEXT("p6-preflight.json");
		const TCHAR* ExportMetadataFile = TEXT("p6-export-metadata.json");
		const TCHAR* ApplyRequestFile = TEXT("p6-apply-request.json");
		const TCHAR* ApplyResultFile = TEXT("p6-apply-result.json");
		const TCHAR* ReloadResultFile = TEXT("p6-reload-result.json");

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
				OutError = TEXT("Could not create the P6 disposable fixture package.");
				return nullptr;
			}

			UBlueprint* Blueprint = FKismetEditorUtilities::CreateBlueprint(
				AActor::StaticClass(),
				Package,
				FName(FixtureAssetName),
				BPTYPE_Normal,
				FName(TEXT("UEAgentKitP6Fixture")));
			if (Blueprint == nullptr)
			{
				OutError = TEXT("Could not create the P6 disposable K2 Blueprint.");
				return nullptr;
			}

			UEdGraph* Graph = GetPrimaryGraph(Blueprint);
			if (Graph == nullptr)
			{
				OutError = TEXT("The P6 disposable Blueprint has no ubergraph.");
				return nullptr;
			}

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
				OutError = TEXT("The P6 disposable Blueprint has no ReceiveBeginPlay event.");
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
				const TCHAR* Comment;
			};
			const FNodeSpec Specs[] = {
				{-640, 40, TEXT("P6 bridge chain root")},
				{-260, -120, TEXT("P6 bridge chain middle")},
				{160, 120, TEXT("P6 bridge chain tail")},
			};
			TArray<UK2Node_ExecutionSequence*> Sequences;
			for (const FNodeSpec& Spec : Specs)
			{
				FGraphNodeCreator<UK2Node_ExecutionSequence> Creator(*Graph);
				UK2Node_ExecutionSequence* Node = Creator.CreateNode(false);
				Node->NodePosX = Spec.X;
				Node->NodePosY = Spec.Y;
				Creator.Finalize();
				Node->NodeComment = Spec.Comment;
				Sequences.Add(Node);
			}

			UEdGraphPin* EventThen = BeginPlay->FindPin(UEdGraphSchema_K2::PN_Then, EGPD_Output);
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
					Sequences[2]->FindPin(UEdGraphSchema_K2::PN_Execute, EGPD_Input)))
			{
				OutError = TEXT("Could not create the P6 linear execution topology.");
				return nullptr;
			}

			FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
			FKismetEditorUtilities::CompileBlueprint(
				Blueprint,
				EBlueprintCompileOptions::SkipGarbageCollection);
			if (Blueprint->Status == BS_Error)
			{
				OutError = TEXT("The P6 disposable fixture Blueprint failed to compile.");
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
					OutError = TEXT("The existing P6 fixture is dirty; rebuild the disposable host.");
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

		FString TopologyHash(UEdGraph* Graph)
		{
			return Sha1Text(StructuralState(Graph));
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

		bool ReadPlanTargets(
			const TSharedPtr<FJsonObject>& Request,
			TMap<FGuid, FIntPoint>& OutTargets,
			FString& OutError)
		{
			const TSharedPtr<FJsonObject>* PlanField = nullptr;
			if (!Request->TryGetObjectField(TEXT("plan"), PlanField)
				|| PlanField == nullptr
				|| !PlanField->IsValid())
			{
				OutError = TEXT("Request has no plan object.");
				return false;
			}
			const TArray<TSharedPtr<FJsonValue>>* Positions = nullptr;
			if (!(*PlanField)->TryGetArrayField(TEXT("positions"), Positions) || Positions == nullptr)
			{
				OutError = TEXT("Request plan has no positions.");
				return false;
			}
			for (const TSharedPtr<FJsonValue>& Entry : *Positions)
			{
				const TSharedPtr<FJsonObject> Position = Entry->AsObject();
				if (!Position.IsValid())
				{
					OutError = TEXT("Request plan position is not an object.");
					return false;
				}
				const FString NodeId = Position->GetStringField(TEXT("nodeId"));
				FGuid Guid;
				if (!NodeId.StartsWith(TEXT("n:")) || !FGuid::Parse(NodeId.Mid(2), Guid))
				{
					OutError = FString::Printf(TEXT("Request plan nodeId is invalid: %s"), *NodeId);
					return false;
				}
				OutTargets.Add(
					Guid,
					FIntPoint(
						static_cast<int32>(Position->GetNumberField(TEXT("x"))),
						static_cast<int32>(Position->GetNumberField(TEXT("y")))));
			}
			return OutTargets.Num() > 0;
		}

		TSharedPtr<FJsonObject> ReadApplyRequest(FString& OutError)
		{
			return ReadJsonFile(EvidencePath(ApplyRequestFile), OutError);
		}
	}

	IMPLEMENT_SIMPLE_AUTOMATION_TEST(
		FUEAgentKitGraphLayoutP6BridgeFixtureTest,
		"UEAgentKit.GraphLayout.P6BridgeFixtureContract",
		EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

	bool FUEAgentKitGraphLayoutP6BridgeFixtureTest::RunTest(const FString& Parameters)
	{
		FString Error;
		UBlueprint* Blueprint = LoadOrCreateFixture(Error);
		if (!TestNotNull(TEXT("P6 disposable Blueprint is loaded"), Blueprint))
		{
			AddError(Error);
			return false;
		}
		UEdGraph* Graph = GetPrimaryGraph(Blueprint);
		if (!TestNotNull(TEXT("P6 disposable ubergraph is present"), Graph))
		{
			return false;
		}
		if (!TestFalse(
			TEXT("P6 fixture starts clean"),
			Blueprint->GetOutermost()->IsDirty()))
		{
			return false;
		}
		TestEqual(TEXT("P6 fixture has four nodes"), Graph->Nodes.Num(), 4);

		// This process has no Blueprint Editor yet: the read surface must fail
		// closed and must not open or focus anything on its own.
		TSharedPtr<FJsonObject> NoEditorPreflight;
		FString NoEditorCode;
		FString NoEditorMessage;
		TestFalse(
			TEXT("P6 focused preflight without an editor is rejected"),
			UEAgentKitBlueprintGraphLayout::BuildFocusedEditorPreflight(
				NoEditorPreflight,
				NoEditorCode,
				NoEditorMessage));
		TestEqual(
			TEXT("P6 focused preflight reports no open editor"),
			NoEditorCode,
			FString(TEXT("graph-layout-editor-not-open")));

		FBlueprintEditor* Editor = OpenAndFocus(Blueprint, Graph, Error);
		if (!TestNotNull(TEXT("the real Blueprint Editor opens and focuses the P6 graph"), Editor))
		{
			AddError(Error);
			return false;
		}

		TSharedPtr<FJsonObject> FocusedPreflight;
		FString ErrorCode;
		FString ErrorMessage;
		if (!TestTrue(
			TEXT("P6 focused read-only preflight succeeds"),
			UEAgentKitBlueprintGraphLayout::BuildFocusedEditorPreflight(
				FocusedPreflight,
				ErrorCode,
				ErrorMessage)))
		{
			AddError(FString::Printf(
				TEXT("P6 BuildFocusedEditorPreflight failed: %s %s"),
				*ErrorCode,
				*ErrorMessage));
			return false;
		}
		TestEqual(
			TEXT("P6 focused preflight reports the exact Blueprint"),
			FocusedPreflight->GetStringField(TEXT("assetPath")),
			Blueprint->GetPathName());
		TestEqual(
			TEXT("P6 focused preflight reports the exact graph"),
			FocusedPreflight->GetStringField(TEXT("graphGuid")),
			GuidLower(Graph->GraphGuid));
		TestEqual(
			TEXT("P6 focused preflight reports four nodes"),
			FocusedPreflight->GetIntegerField(TEXT("nodeCount")),
			4);
		TestTrue(
			TEXT("P6 focused preflight is marked read-only"),
			FocusedPreflight->GetBoolField(TEXT("readOnly")));
		TestEqual(
			TEXT("P6 focused preflight uses the BlueprintEditor toolkit"),
			FocusedPreflight->GetStringField(TEXT("editorName")),
			FString(TEXT("BlueprintEditor")));

		const FString PreRevision = FocusedPreflight->GetStringField(TEXT("packageRevision"));
		const FString Fingerprint = FocusedPreflight->GetStringField(TEXT("graphFingerprint"));
		TestTrue(
			TEXT("P6 preflight has a real package revision"),
			PreRevision.StartsWith(TEXT("sha256:")));
		TestTrue(
			TEXT("P6 preflight has a real graph fingerprint"),
			Fingerprint.StartsWith(TEXT("sha1:")));

		TSharedPtr<FJsonObject> Context;
		if (!TestTrue(
			TEXT("P6 real BlueprintContext full export succeeds"),
			ExportContext(Blueprint, Graph, Context, Error)))
		{
			AddError(Error);
			return false;
		}
		TestEqual(
			TEXT("P6 exporter reports the exact asset"),
			Context->GetStringField(TEXT("assetPath")),
			Blueprint->GetPathName());
		TestEqual(
			TEXT("P6 exporter profile is full"),
			Context->GetStringField(TEXT("profile")),
			FString(TEXT("full")));

		const FString ContextPath = EvidencePath(ContextFile);
		const FString PreflightPath = EvidencePath(PreflightFile);
		if (!TestTrue(
			TEXT("P6 focused preflight is written for the offline handoff"),
			WriteJson(PreflightPath, FocusedPreflight, Error)))
		{
			AddError(Error);
			return false;
		}

		const FString DllPath = ModuleDllPath();
		if (!TestTrue(
			TEXT("P6 loaded plugin binary is resolvable"),
			!DllPath.IsEmpty() && FPaths::FileExists(DllPath)))
		{
			return false;
		}
		const FString PluginBuildHash = Sha256FileText(DllPath);
		TestTrue(
			TEXT("P6 loaded plugin binary hash is available"),
			PluginBuildHash.StartsWith(TEXT("sha256:")));

		TSharedRef<FJsonObject> Metadata = MakeShared<FJsonObject>();
		Metadata->SetStringField(TEXT("schemaVersion"), TEXT("ueak.p6.exportmetadata/1.0"));
		Metadata->SetStringField(TEXT("kind"), TEXT("live-editor"));
		Metadata->SetStringField(TEXT("assetPath"), Blueprint->GetPathName());
		Metadata->SetStringField(TEXT("graphGuid"), GuidLower(Graph->GraphGuid));
		Metadata->SetStringField(TEXT("graphName"), Graph->GetName());
		Metadata->SetStringField(TEXT("contextFile"), ContextFile);
		Metadata->SetStringField(TEXT("contextSha256"), Sha256FileText(ContextPath));
		Metadata->SetStringField(TEXT("preflightFile"), PreflightFile);
		Metadata->SetStringField(TEXT("preflightSha256"), Sha256FileText(PreflightPath));
		Metadata->SetStringField(TEXT("pluginBuildHash"), PluginBuildHash);
		Metadata->SetStringField(TEXT("exportedAtUtc"), FDateTime::UtcNow().ToIso8601());
		Metadata->SetStringField(TEXT("engineVersion"), FEngineVersion::Current().ToString());
		Metadata->SetStringField(TEXT("projectName"), FApp::GetProjectName());
		Metadata->SetStringField(TEXT("exporterSchemaVersion"), TEXT("1.1"));
		Metadata->SetStringField(TEXT("exporterVersion"), TEXT("0.8.0"));
		Metadata->SetStringField(TEXT("profile"), TEXT("full"));
		Metadata->SetNumberField(TEXT("nodeCount"), Graph->Nodes.Num());
		Metadata->SetStringField(
			TEXT("packageRevision"),
			FocusedPreflight->GetStringField(TEXT("packageRevision")));
		Metadata->SetStringField(
			TEXT("graphFingerprint"),
			FocusedPreflight->GetStringField(TEXT("graphFingerprint")));
		if (!TestTrue(
			TEXT("P6 export metadata is written"),
			WriteJson(EvidencePath(ExportMetadataFile), Metadata, Error)))
		{
			AddError(Error);
			return false;
		}

		TSharedRef<FJsonObject> Expected = MakeShared<FJsonObject>();
		Expected->SetStringField(TEXT("assetPath"), Blueprint->GetPathName());
		Expected->SetStringField(TEXT("graphGuid"), GuidLower(Graph->GraphGuid));
		Expected->SetNumberField(TEXT("nodeCount"), Graph->Nodes.Num());
		Expected->SetStringField(TEXT("packageRevision"), PreRevision);
		Expected->SetStringField(TEXT("graphFingerprint"), Fingerprint);
		Expected->SetBoolField(TEXT("packageDirty"), false);
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
		Expected->SetStringField(TEXT("contextSha256"), Sha256FileText(ContextPath));
		Expected->SetStringField(TEXT("preflightSha256"), Sha256FileText(PreflightPath));
		if (!TestTrue(
			TEXT("P6 fixture expected-state evidence is written"),
			WriteJson(EvidencePath(FixtureExpectedFile), Expected, Error)))
		{
			AddError(Error);
			return false;
		}

		TSharedRef<FJsonObject> Evidence = MakeShared<FJsonObject>();
		Evidence->SetStringField(TEXT("test"), TEXT("UEAgentKit.GraphLayout.P6BridgeFixtureContract"));
		Evidence->SetStringField(TEXT("assetPath"), Blueprint->GetPathName());
		Evidence->SetStringField(TEXT("graphGuid"), GuidLower(Graph->GraphGuid));
		Evidence->SetStringField(TEXT("packageRevision"), PreRevision);
		Evidence->SetStringField(TEXT("graphFingerprint"), Fingerprint);
		Evidence->SetNumberField(TEXT("nodeCount"), Graph->Nodes.Num());
		Evidence->SetStringField(TEXT("noEditorFailureCode"), NoEditorCode);
		Evidence->SetStringField(TEXT("source"), TEXT("focused-blueprint-editor"));
		Evidence->SetBoolField(TEXT("readOnly"), true);
		Evidence->SetStringField(TEXT("contextSha256"), Sha256FileText(ContextPath));
		Evidence->SetStringField(TEXT("preflightSha256"), Sha256FileText(PreflightPath));
		Evidence->SetStringField(TEXT("pluginBuildHash"), PluginBuildHash);
		Evidence->SetStringField(TEXT("savedAtUtc"), FDateTime::UtcNow().ToIso8601());
		Evidence->SetBoolField(TEXT("success"), true);
		if (!TestTrue(
			TEXT("P6 fixture evidence is written"),
			WriteJson(EvidencePath(TEXT("p6-fixture-result.json")), Evidence, Error)))
		{
			AddError(Error);
			return false;
		}
		return true;
	}

	IMPLEMENT_SIMPLE_AUTOMATION_TEST(
		FUEAgentKitGraphLayoutP6ApplyTest,
		"UEAgentKit.GraphLayout.P6ApplyContract",
		EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

	bool FUEAgentKitGraphLayoutP6ApplyTest::RunTest(const FString& Parameters)
	{
		FString Error;
		UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, FixtureAssetPath);
		if (!TestNotNull(TEXT("P6 apply fixture loads from disk"), Blueprint))
		{
			AddError(Error);
			return false;
		}
		UEdGraph* Graph = GetPrimaryGraph(Blueprint);
		if (!TestNotNull(TEXT("P6 apply graph exists"), Graph))
		{
			return false;
		}
		if (!TestFalse(
			TEXT("P6 apply fixture starts clean"),
			Blueprint->GetOutermost()->IsDirty()))
		{
			return false;
		}

		TSharedPtr<FJsonObject> Request = ReadApplyRequest(Error);
		if (!TestTrue(TEXT("P6 Python handoff request exists"), Request.IsValid()))
		{
			AddError(Error);
			return false;
		}
		TestEqual(
			TEXT("P6 request targets the exact fixture asset"),
			Request->GetStringField(TEXT("assetPath")),
			Blueprint->GetPathName());
		TestEqual(
			TEXT("P6 request targets the exact fixture graph"),
			Request->GetStringField(TEXT("graphGuid")),
			GuidLower(Graph->GraphGuid));

		TMap<FGuid, FIntPoint> Targets;
		if (!TestTrue(
			TEXT("P6 Python request has exact targets"),
			ReadPlanTargets(Request, Targets, Error)))
		{
			AddError(Error);
			return false;
		}
		TestEqual(TEXT("P6 Python plan covers all four nodes"), Targets.Num(), 4);
		const int32 ExpectedMovedCount =
			Request->GetObjectField(TEXT("plan"))->GetArrayField(TEXT("movedNodeIds")).Num();
		TestTrue(TEXT("P6 Python plan moves at least one node"), ExpectedMovedCount > 0);

		FBlueprintEditor* Editor = OpenAndFocus(Blueprint, Graph, Error);
		if (!TestNotNull(TEXT("the real Blueprint Editor opens and focuses the P6 apply target"), Editor))
		{
			AddError(Error);
			return false;
		}

		TSharedPtr<FJsonObject> Preflight;
		FString ErrorCode;
		FString ErrorMessage;
		if (!TestTrue(
			TEXT("P6 apply preflight succeeds"),
			UEAgentKitBlueprintGraphLayout::BuildPreflight(
				Blueprint,
				Graph,
				Preflight,
				ErrorCode,
				ErrorMessage)))
		{
			AddError(FString::Printf(TEXT("P6 apply preflight failed: %s %s"), *ErrorCode, *ErrorMessage));
			return false;
		}
		TestEqual(
			TEXT("P6 request revision equals the live revision"),
			Request->GetStringField(TEXT("expectedPackageRevision")),
			Preflight->GetStringField(TEXT("packageRevision")));
		TestEqual(
			TEXT("P6 request fingerprint equals the live fingerprint"),
			Request->GetStringField(TEXT("expectedGraphFingerprint")),
			Preflight->GetStringField(TEXT("graphFingerprint")));

		const TMap<FGuid, FIntPoint> BeforePositions = CapturePositions(Graph);
		const FString BeforeStructure = StructuralState(Graph);
		const FString BeforeTopology = TopologyHash(Graph);
		TestFalse(
			TEXT("P6 Python plan does not equal the live positions"),
			PositionsEqual(Targets, BeforePositions));

		TSharedPtr<FJsonObject> Applied;
		if (!TestTrue(
			TEXT("P6 TryApply succeeds on the focused exact target"),
			UEAgentKitBlueprintGraphLayout::TryApply(
				Request,
				Applied,
				ErrorCode,
				ErrorMessage)))
		{
			AddError(FString::Printf(
				TEXT("P6 TryApply failed: %s %s"),
				*ErrorCode,
				*ErrorMessage));
			return false;
		}
		TestTrue(TEXT("P6 TryApply reports changed"), Applied->GetBoolField(TEXT("changed")));
		TestTrue(TEXT("P6 TryApply used one transaction"), Applied->GetBoolField(TEXT("singleTransaction")));
		TestFalse(TEXT("P6 TryApply never saved"), Applied->GetBoolField(TEXT("saved")));
		TestFalse(TEXT("P6 TryApply never compiled"), Applied->GetBoolField(TEXT("compiled")));
		TestEqual(
			TEXT("P6 TryApply moved every planned node"),
			Applied->GetIntegerField(TEXT("movedNodeCount")),
			ExpectedMovedCount);
		TestEqual(
			TEXT("P6 TryApply preserved node identity, pins, sizes and links"),
			StructuralState(Graph),
			BeforeStructure);
		TestEqual(TEXT("P6 TryApply preserved topology"), TopologyHash(Graph), BeforeTopology);

		for (const TPair<FGuid, FIntPoint>& Pair : Targets)
		{
			const FIntPoint* Actual = CapturePositions(Graph).Find(Pair.Key);
			if (!TestNotNull(TEXT("P6 target node exists"), Actual))
			{
				return false;
			}
			TestEqual(TEXT("P6 target node x"), Actual->X, Pair.Value.X);
			TestEqual(TEXT("P6 target node y"), Actual->Y, Pair.Value.Y);
		}
		TestTrue(TEXT("P6 TryApply dirties the package"), Blueprint->GetOutermost()->IsDirty());

		if (!TestTrue(TEXT("P6 UndoTransaction succeeds"), GEditor->UndoTransaction()))
		{
			return false;
		}
		TestTrue(
			TEXT("P6 Undo restores every original position"),
			PositionsEqual(CapturePositions(Graph), BeforePositions));
		TestEqual(TEXT("P6 Undo restores structural state"), StructuralState(Graph), BeforeStructure);
		if (!TestTrue(TEXT("P6 RedoTransaction succeeds"), GEditor->RedoTransaction()))
		{
			return false;
		}
		TestTrue(
			TEXT("P6 Redo restores every algorithm target"),
			PositionsEqual(CapturePositions(Graph), Targets));

		if (!TestTrue(TEXT("P6 fixture saves after TryApply"), SaveBlueprint(Blueprint, Error)))
		{
			AddError(Error);
			return false;
		}
		TestFalse(TEXT("P6 save clears dirty"), Blueprint->GetOutermost()->IsDirty());

		TSharedPtr<FJsonObject> SavedPreflight;
		if (!TestTrue(
			TEXT("P6 post-save preflight succeeds"),
			UEAgentKitBlueprintGraphLayout::BuildPreflight(
				Blueprint,
				Graph,
				SavedPreflight,
				ErrorCode,
				ErrorMessage)))
		{
			AddError(Error);
			return false;
		}
		const FString PostRevision = SavedPreflight->GetStringField(TEXT("packageRevision"));
		const FString PostFingerprint = SavedPreflight->GetStringField(TEXT("graphFingerprint"));
		TestTrue(
			TEXT("P6 save changed the package revision"),
			PostRevision != Preflight->GetStringField(TEXT("packageRevision")));
		const FString AssetFilename = FPackageName::LongPackageNameToFilename(
			Blueprint->GetOutermost()->GetName(),
			FPackageName::GetAssetPackageExtension());
		TestEqual(
			TEXT("P6 saved revision equals the on-disk asset hash"),
			PostRevision,
			Sha256FileText(AssetFilename));

		// The same request is now stale and must be rejected without moving a node.
		TSharedPtr<FJsonObject> StaleResult;
		FString StaleCode;
		FString StaleMessage;
		TestFalse(
			TEXT("P6 stale request after save is rejected"),
			UEAgentKitBlueprintGraphLayout::TryApply(
				Request,
				StaleResult,
				StaleCode,
				StaleMessage));
		TestEqual(
			TEXT("P6 stale request reports a revision conflict"),
			StaleCode,
			FString(TEXT("graph-layout-revision-conflict")));
		TestTrue(
			TEXT("P6 stale request moved nothing"),
			PositionsEqual(CapturePositions(Graph), Targets));

		TArray<TSharedPtr<FJsonValue>> TargetValues;
		for (const TPair<FGuid, FIntPoint>& Pair : Targets)
		{
			TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
			Item->SetStringField(TEXT("nodeId"), FString(TEXT("n:")) + GuidLower(Pair.Key));
			Item->SetNumberField(TEXT("x"), Pair.Value.X);
			Item->SetNumberField(TEXT("y"), Pair.Value.Y);
			TargetValues.Add(MakeShared<FJsonValueObject>(Item));
		}

		TSharedRef<FJsonObject> Evidence = MakeShared<FJsonObject>();
		Evidence->SetStringField(TEXT("test"), TEXT("UEAgentKit.GraphLayout.P6ApplyContract"));
		Evidence->SetStringField(TEXT("assetPath"), Blueprint->GetPathName());
		Evidence->SetStringField(TEXT("graphGuid"), GuidLower(Graph->GraphGuid));
		Evidence->SetStringField(TEXT("planId"), Request->GetObjectField(TEXT("plan"))->GetStringField(TEXT("planId")));
		Evidence->SetStringField(TEXT("layoutHash"), Request->GetObjectField(TEXT("plan"))->GetStringField(TEXT("layoutHash")));
		Evidence->SetNumberField(TEXT("nodeCount"), Targets.Num());
		Evidence->SetNumberField(TEXT("movedNodeCount"), Applied->GetIntegerField(TEXT("movedNodeCount")));
		Evidence->SetBoolField(TEXT("changed"), true);
		Evidence->SetBoolField(TEXT("singleTransaction"), true);
		Evidence->SetBoolField(TEXT("saved"), true);
		Evidence->SetBoolField(TEXT("compiled"), false);
		Evidence->SetStringField(TEXT("preRevision"), Preflight->GetStringField(TEXT("packageRevision")));
		Evidence->SetStringField(TEXT("postRevision"), PostRevision);
		Evidence->SetStringField(TEXT("preFingerprint"), Preflight->GetStringField(TEXT("graphFingerprint")));
		Evidence->SetStringField(TEXT("postFingerprint"), PostFingerprint);
		Evidence->SetStringField(TEXT("topologyHash"), BeforeTopology);
		Evidence->SetBoolField(TEXT("undoRedoVerified"), true);
		Evidence->SetBoolField(TEXT("staleRejected"), true);
		Evidence->SetStringField(TEXT("staleCode"), StaleCode);
		Evidence->SetStringField(TEXT("assetSha256"), Sha256FileText(AssetFilename));
		Evidence->SetArrayField(TEXT("targets"), TargetValues);
		Evidence->SetArrayField(TEXT("positions"), Request->GetObjectField(TEXT("plan"))->GetArrayField(TEXT("positions")));
		Evidence->SetBoolField(TEXT("success"), true);
		if (!TestTrue(
			TEXT("P6 apply evidence is written"),
			WriteJson(EvidencePath(ApplyResultFile), Evidence, Error)))
		{
			AddError(Error);
			return false;
		}
		return true;
	}

	IMPLEMENT_SIMPLE_AUTOMATION_TEST(
		FUEAgentKitGraphLayoutP6ReloadTest,
		"UEAgentKit.GraphLayout.P6ReloadContract",
		EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

	bool FUEAgentKitGraphLayoutP6ReloadTest::RunTest(const FString& Parameters)
	{
		FString Error;
		UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, FixtureAssetPath);
		if (!TestNotNull(TEXT("P6 reload fixture loads independently"), Blueprint))
		{
			AddError(Error);
			return false;
		}
		UEdGraph* Graph = GetPrimaryGraph(Blueprint);
		if (!TestNotNull(TEXT("P6 reload graph exists"), Graph))
		{
			return false;
		}
		TestFalse(TEXT("P6 reloaded package is clean"), Blueprint->GetOutermost()->IsDirty());

		TSharedPtr<FJsonObject> Applied = ReadJsonFile(EvidencePath(ApplyResultFile), Error);
		if (!TestTrue(TEXT("P6 apply evidence exists for reload"), Applied.IsValid()))
		{
			AddError(Error);
			return false;
		}

		TSharedPtr<FJsonObject> Preflight;
		FString ErrorCode;
		FString ErrorMessage;
		if (!TestTrue(
			TEXT("P6 reload preflight succeeds"),
			UEAgentKitBlueprintGraphLayout::BuildPreflight(
				Blueprint,
				Graph,
				Preflight,
				ErrorCode,
				ErrorMessage)))
		{
			AddError(FString::Printf(TEXT("P6 reload preflight failed: %s %s"), *ErrorCode, *ErrorMessage));
			return false;
		}
		TestEqual(
			TEXT("P6 reload revision equals the saved revision"),
			Preflight->GetStringField(TEXT("packageRevision")),
			Applied->GetStringField(TEXT("postRevision")));
		TestEqual(
			TEXT("P6 reload fingerprint equals the saved fingerprint"),
			Preflight->GetStringField(TEXT("graphFingerprint")),
			Applied->GetStringField(TEXT("postFingerprint")));
		TestEqual(
			TEXT("P6 reload topology equals the pre-apply topology"),
			TopologyHash(Graph),
			Applied->GetStringField(TEXT("topologyHash")));

		const TArray<TSharedPtr<FJsonValue>>* Targets = nullptr;
		if (!TestTrue(
			TEXT("P6 apply evidence contains target positions"),
			Applied->TryGetArrayField(TEXT("targets"), Targets) && Targets != nullptr))
		{
			return false;
		}
		int32 Verified = 0;
		for (const TSharedPtr<FJsonValue>& Entry : *Targets)
		{
			const TSharedPtr<FJsonObject> Item = Entry->AsObject();
			if (!Item.IsValid())
			{
				return false;
			}
			const FString NodeId = Item->GetStringField(TEXT("nodeId"));
			FGuid Guid;
			if (!NodeId.StartsWith(TEXT("n:")) || !FGuid::Parse(NodeId.Mid(2), Guid))
			{
				return false;
			}
			UEdGraphNode* Node = nullptr;
			for (UEdGraphNode* Candidate : Graph->Nodes)
			{
				if (Candidate != nullptr && Candidate->NodeGuid == Guid)
				{
					Node = Candidate;
					break;
				}
			}
			if (!TestNotNull(TEXT("P6 reload target node exists"), Node))
			{
				return false;
			}
			if (!TestEqual(TEXT("P6 reload target x"), Node->NodePosX, static_cast<int32>(Item->GetNumberField(TEXT("x")))))
			{
				return false;
			}
			if (!TestEqual(TEXT("P6 reload target y"), Node->NodePosY, static_cast<int32>(Item->GetNumberField(TEXT("y")))))
			{
				return false;
			}
			++Verified;
		}
		TestEqual(TEXT("P6 reload verified every target node"), Verified, 4);

		TSharedRef<FJsonObject> Evidence = MakeShared<FJsonObject>();
		Evidence->SetStringField(TEXT("test"), TEXT("UEAgentKit.GraphLayout.P6ReloadContract"));
		Evidence->SetStringField(TEXT("assetPath"), Blueprint->GetPathName());
		Evidence->SetStringField(TEXT("graphGuid"), GuidLower(Graph->GraphGuid));
		Evidence->SetStringField(TEXT("packageRevision"), Preflight->GetStringField(TEXT("packageRevision")));
		Evidence->SetStringField(TEXT("graphFingerprint"), Preflight->GetStringField(TEXT("graphFingerprint")));
		Evidence->SetStringField(TEXT("topologyHash"), TopologyHash(Graph));
		Evidence->SetNumberField(TEXT("nodeCount"), Graph->Nodes.Num());
		Evidence->SetNumberField(TEXT("verifiedTargetCount"), Verified);
		Evidence->SetBoolField(TEXT("independentReloadVerified"), true);
		Evidence->SetBoolField(TEXT("success"), true);
		if (!TestTrue(
			TEXT("P6 reload evidence is written"),
			WriteJson(EvidencePath(ReloadResultFile), Evidence, Error)))
		{
			AddError(Error);
			return false;
		}
		return true;
	}
}

#undef LOCTEXT_NAMESPACE

#endif
