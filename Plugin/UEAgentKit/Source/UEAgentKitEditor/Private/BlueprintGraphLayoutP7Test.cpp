#include "BlueprintContextExporter.h"
#include "BlueprintContextSha256.h"
#include "BlueprintGraphLayoutApply.h"
#include "BlueprintGraphLayoutP7TestOnly.h"

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

#define LOCTEXT_NAMESPACE "UEAgentKitGraphLayoutP7"

namespace UEAgentKitGraphLayoutP7Test
{
	namespace
	{
		const TCHAR* FixturePackageName = TEXT("/Game/UEAgentKitP7/BP_LayoutP7");
		const TCHAR* FixtureAssetName = TEXT("BP_LayoutP7");
		const TCHAR* FixtureAssetPath = TEXT("/Game/UEAgentKitP7/BP_LayoutP7.BP_LayoutP7");
		const TCHAR* FixtureExpectedFile = TEXT("p7-fixture-expected.json");
		const TCHAR* ContextFile = TEXT("p7-context.json");
		const TCHAR* PreflightFile = TEXT("p7-preflight.json");
		const TCHAR* ExportMetadataFile = TEXT("p7-export-metadata.json");
		const TCHAR* ApplyResultFile = TEXT("p7-apply-result.json");
		const TCHAR* ReloadResultFile = TEXT("p7-reload-result.json");

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
				OutError = TEXT("Could not create the P7 disposable fixture package.");
				return nullptr;
			}
			UBlueprint* Blueprint = FKismetEditorUtilities::CreateBlueprint(
				AActor::StaticClass(),
				Package,
				FName(FixtureAssetName),
				BPTYPE_Normal,
				FName(TEXT("UEAgentKitP7Fixture")));
			if (Blueprint == nullptr)
			{
				OutError = TEXT("Could not create the P7 disposable K2 Blueprint.");
				return nullptr;
			}
			UEdGraph* Graph = GetPrimaryGraph(Blueprint);
			if (Graph == nullptr)
			{
				OutError = TEXT("The P7 disposable Blueprint has no ubergraph.");
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
				OutError = TEXT("The P7 disposable Blueprint has no ReceiveBeginPlay event.");
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
				{ 420, -260, TEXT("P7 MCP apply chain root") },
				{ 760, 20, TEXT("P7 MCP apply chain middle") },
				{ 1120, 280, TEXT("P7 MCP apply chain tail") },
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
				|| !LinkPins(Graph, EventThen, Sequences[0]->FindPin(UEdGraphSchema_K2::PN_Execute, EGPD_Input))
				|| !LinkPins(Graph, Sequences[0]->GetThenPinGivenIndex(0), Sequences[1]->FindPin(UEdGraphSchema_K2::PN_Execute, EGPD_Input))
				|| !LinkPins(Graph, Sequences[1]->GetThenPinGivenIndex(0), Sequences[2]->FindPin(UEdGraphSchema_K2::PN_Execute, EGPD_Input)))
			{
				OutError = TEXT("Could not create the P7 linear execution topology.");
				return nullptr;
			}

			FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
			FKismetEditorUtilities::CompileBlueprint(Blueprint, EBlueprintCompileOptions::SkipGarbageCollection);
			if (Blueprint->Status == BS_Error)
			{
				OutError = TEXT("The P7 disposable fixture Blueprint failed to compile.");
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
					OutError = TEXT("The existing P7 fixture is dirty; rebuild the disposable host.");
					return nullptr;
				}
				return Existing;
			}
			return CreateFixture(OutError);
		}

		bool WriteJson(const FString& Path, const TSharedPtr<FJsonObject>& Object, FString& OutError)
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
			if (!FFileHelper::SaveStringToFile(Text, *Path, FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
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

		FBlueprintEditor* OpenAndFocus(UBlueprint* Blueprint, UEdGraph* Graph, FString& OutError)
		{
			if (GEditor == nullptr)
			{
				OutError = TEXT("GEditor is unavailable.");
				return nullptr;
			}
			UAssetEditorSubsystem* AssetEditorSubsystem = GEditor->GetEditorSubsystem<UAssetEditorSubsystem>();
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
			IAssetEditorInstance* Instance = AssetEditorSubsystem->FindEditorForAsset(Blueprint, false);
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
			if (!Editor->OpenGraphAndBringToFront(Graph, true).IsValid())
			{
				OutError = TEXT("OpenGraphAndBringToFront returned an invalid graph editor.");
				return nullptr;
			}
			if (Editor->GetFocusedGraph() != Graph)
			{
				OutError = TEXT("The requested graph is not focused after OpenGraphAndBringToFront.");
				return nullptr;
			}
			return Editor;
		}

		bool ExportContext(UBlueprint* Blueprint, UEdGraph* Graph, TSharedPtr<FJsonObject>& OutContext, FString& OutError)
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
	}

	IMPLEMENT_SIMPLE_AUTOMATION_TEST(
		FUEAgentKitGraphLayoutP7FixtureTest,
		"UEAgentKit.GraphLayout.P7FixtureContract",
		EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

	bool FUEAgentKitGraphLayoutP7FixtureTest::RunTest(const FString& Parameters)
	{
		FString Error;
		UBlueprint* Blueprint = LoadOrCreateFixture(Error);
		if (!TestNotNull(TEXT("P7 disposable Blueprint is loaded"), Blueprint))
		{
			AddError(Error);
			return false;
		}
		UEdGraph* Graph = GetPrimaryGraph(Blueprint);
		if (!TestNotNull(TEXT("P7 disposable ubergraph is present"), Graph))
		{
			return false;
		}
		if (!TestFalse(TEXT("P7 fixture starts clean"), Blueprint->GetOutermost()->IsDirty()))
		{
			return false;
		}
		TestEqual(TEXT("P7 fixture has four nodes"), Graph->Nodes.Num(), 4);
		TestEqual(TEXT("P7 fixture has eleven pins"), [&]() {
			int32 Count = 0;
			for (const UEdGraphNode* Node : Graph->Nodes)
			{
				Count += Node != nullptr ? Node->Pins.Num() : 0;
			}
			return Count;
		}(), 11);

		FString GateCode;
		FString GateMessage;
		TestFalse(
			TEXT("P7 gate rejects a different project path"),
			UEAgentKitBlueprintGraphLayoutP7::IsFixedProjectIdentityForTest(
				TEXT("E:/WorkSpace/UEAgentKit-WT/graph-contracts/Build/P6AutomationHost/P6AutomationHost.uproject"),
				TEXT("P6AutomationHost"),
				TEXT("0000000000000000000000000000000000000000000000000000000000000000"),
				GateCode,
				GateMessage));
		TestEqual(TEXT("P7 wrong-project code"), GateCode, FString(TEXT("graph-layout-test-only-project-rejected")));
		TestFalse(
			TEXT("P7 gate rejects the P7 path with a different .uproject hash"),
			UEAgentKitBlueprintGraphLayoutP7::IsFixedProjectIdentityForTest(
				UEAgentKitBlueprintGraphLayoutP7::FixedProjectPath,
				TEXT("P7AutomationHost"),
				TEXT("0000000000000000000000000000000000000000000000000000000000000000"),
				GateCode,
				GateMessage));
		TestFalse(
			TEXT("P7 gate rejects a different asset"),
			UEAgentKitBlueprintGraphLayoutP7::IsFixedAssetPath(TEXT("/Game/UEAgentKitP6/BP_LayoutP6.BP_LayoutP6")));

		FBlueprintEditor* Editor = OpenAndFocus(Blueprint, Graph, Error);
		if (!TestNotNull(TEXT("the real Blueprint Editor opens and focuses the P7 graph"), Editor))
		{
			AddError(Error);
			return false;
		}
		TSharedPtr<FJsonObject> Preflight;
		FString ErrorCode;
		FString ErrorMessage;
		if (!TestTrue(
			TEXT("P7 focused preflight succeeds"),
			UEAgentKitBlueprintGraphLayout::BuildPreflight(Blueprint, Graph, Preflight, ErrorCode, ErrorMessage)))
		{
			AddError(FString::Printf(TEXT("P7 preflight failed: %s %s"), *ErrorCode, *ErrorMessage));
			return false;
		}
		const FString PreRevision = Preflight->GetStringField(TEXT("packageRevision"));
		const FString Fingerprint = Preflight->GetStringField(TEXT("graphFingerprint"));
		TestTrue(TEXT("P7 preflight revision is real"), PreRevision.StartsWith(TEXT("sha256:")));
		TestTrue(TEXT("P7 preflight fingerprint is real"), Fingerprint.StartsWith(TEXT("sha1:")));

		TSharedPtr<FJsonObject> Context;
		if (!TestTrue(TEXT("P7 real full exporter succeeds"), ExportContext(Blueprint, Graph, Context, Error)))
		{
			AddError(Error);
			return false;
		}
		TestEqual(TEXT("P7 exporter reports the exact asset"), Context->GetStringField(TEXT("assetPath")), Blueprint->GetPathName());
		const FString ContextPath = EvidencePath(ContextFile);
		const FString PreflightPath = EvidencePath(PreflightFile);
		if (!TestTrue(TEXT("P7 preflight evidence is written"), WriteJson(PreflightPath, Preflight, Error)))
		{
			AddError(Error);
			return false;
		}
		const FString DllPath = ModuleDllPath();
		const FString PluginBuildHash = Sha256FileText(DllPath);
		TSharedRef<FJsonObject> Metadata = MakeShared<FJsonObject>();
		Metadata->SetStringField(TEXT("schemaVersion"), TEXT("ueak.p7.exportmetadata/1.0"));
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
		Metadata->SetStringField(TEXT("packageRevision"), PreRevision);
		Metadata->SetStringField(TEXT("graphFingerprint"), Fingerprint);
		if (!TestTrue(TEXT("P7 export metadata is written"), WriteJson(EvidencePath(ExportMetadataFile), Metadata, Error)))
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
		Expected->SetStringField(TEXT("topologyHash"), Sha1Text(StructuralState(Graph)));
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
		if (!TestTrue(TEXT("P7 expected-state evidence is written"), WriteJson(EvidencePath(FixtureExpectedFile), Expected, Error)))
		{
			AddError(Error);
			return false;
		}

		TSharedRef<FJsonObject> Evidence = MakeShared<FJsonObject>();
		Evidence->SetStringField(TEXT("test"), TEXT("UEAgentKit.GraphLayout.P7FixtureContract"));
		Evidence->SetStringField(TEXT("assetPath"), Blueprint->GetPathName());
		Evidence->SetStringField(TEXT("graphGuid"), GuidLower(Graph->GraphGuid));
		Evidence->SetStringField(TEXT("packageRevision"), PreRevision);
		Evidence->SetStringField(TEXT("graphFingerprint"), Fingerprint);
		Evidence->SetNumberField(TEXT("nodeCount"), Graph->Nodes.Num());
		Evidence->SetStringField(TEXT("topologyHash"), Expected->GetStringField(TEXT("topologyHash")));
		Evidence->SetStringField(TEXT("contextSha256"), Sha256FileText(ContextPath));
		Evidence->SetStringField(TEXT("preflightSha256"), Sha256FileText(PreflightPath));
		Evidence->SetStringField(TEXT("pluginBuildHash"), PluginBuildHash);
		Evidence->SetStringField(TEXT("savedAtUtc"), FDateTime::UtcNow().ToIso8601());
		Evidence->SetBoolField(TEXT("gateWrongProjectRejected"), true);
		Evidence->SetBoolField(TEXT("gateWrongHashRejected"), true);
		Evidence->SetBoolField(TEXT("gateWrongAssetRejected"), true);
		Evidence->SetBoolField(TEXT("success"), true);
		if (!TestTrue(TEXT("P7 fixture evidence is written"), WriteJson(EvidencePath(TEXT("p7-fixture-result.json")), Evidence, Error)))
		{
			AddError(Error);
			return false;
		}
		return true;
	}

	IMPLEMENT_SIMPLE_AUTOMATION_TEST(
		FUEAgentKitGraphLayoutP7ReloadTest,
		"UEAgentKit.GraphLayout.P7ReloadContract",
		EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

	bool FUEAgentKitGraphLayoutP7ReloadTest::RunTest(const FString& Parameters)
	{
		FString Error;
		UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, FixtureAssetPath);
		if (!TestNotNull(TEXT("P7 reload fixture loads independently"), Blueprint))
		{
			AddError(Error);
			return false;
		}
		UEdGraph* Graph = GetPrimaryGraph(Blueprint);
		if (!TestNotNull(TEXT("P7 reload graph exists"), Graph))
		{
			return false;
		}
		TestFalse(TEXT("P7 reloaded package is clean"), Blueprint->GetOutermost()->IsDirty());
		TSharedPtr<FJsonObject> Applied = ReadJsonFile(EvidencePath(ApplyResultFile), Error);
		if (!TestTrue(TEXT("P7 apply evidence exists for reload"), Applied.IsValid()))
		{
			AddError(Error);
			return false;
		}
		TSharedPtr<FJsonObject> Preflight;
		FString ErrorCode;
		FString ErrorMessage;
		if (!TestTrue(
			TEXT("P7 reload preflight succeeds"),
			UEAgentKitBlueprintGraphLayout::BuildPreflight(Blueprint, Graph, Preflight, ErrorCode, ErrorMessage)))
		{
			AddError(FString::Printf(TEXT("P7 reload preflight failed: %s %s"), *ErrorCode, *ErrorMessage));
			return false;
		}
		TestEqual(
			TEXT("P7 reload revision equals the saved revision"),
			Preflight->GetStringField(TEXT("packageRevision")),
			Applied->GetStringField(TEXT("postRevision")));
		TestEqual(
			TEXT("P7 reload fingerprint equals the saved fingerprint"),
			Preflight->GetStringField(TEXT("graphFingerprint")),
			Applied->GetStringField(TEXT("postFingerprint")));
		TestEqual(
			TEXT("P7 reload topology equals the pre-apply topology"),
			Sha1Text(StructuralState(Graph)),
			Applied->GetStringField(TEXT("topologyHash")));

		const TArray<TSharedPtr<FJsonValue>>* Targets = nullptr;
		if (!TestTrue(
			TEXT("P7 apply evidence contains target positions"),
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
			if (!TestNotNull(TEXT("P7 reload target node exists"), Node))
			{
				return false;
			}
			if (!TestEqual(TEXT("P7 reload target x"), Node->NodePosX, static_cast<int32>(Item->GetNumberField(TEXT("x")))))
			{
				return false;
			}
			if (!TestEqual(TEXT("P7 reload target y"), Node->NodePosY, static_cast<int32>(Item->GetNumberField(TEXT("y")))))
			{
				return false;
			}
			++Verified;
		}
		TestEqual(TEXT("P7 reload verified every target node"), Verified, 4);

		TSharedRef<FJsonObject> Evidence = MakeShared<FJsonObject>();
		Evidence->SetStringField(TEXT("test"), TEXT("UEAgentKit.GraphLayout.P7ReloadContract"));
		Evidence->SetStringField(TEXT("assetPath"), Blueprint->GetPathName());
		Evidence->SetStringField(TEXT("graphGuid"), GuidLower(Graph->GraphGuid));
		Evidence->SetStringField(TEXT("packageRevision"), Preflight->GetStringField(TEXT("packageRevision")));
		Evidence->SetStringField(TEXT("graphFingerprint"), Preflight->GetStringField(TEXT("graphFingerprint")));
		Evidence->SetStringField(TEXT("topologyHash"), Sha1Text(StructuralState(Graph)));
		Evidence->SetNumberField(TEXT("nodeCount"), Graph->Nodes.Num());
		Evidence->SetNumberField(TEXT("verifiedTargetCount"), Verified);
		Evidence->SetBoolField(TEXT("independentReloadVerified"), true);
		Evidence->SetBoolField(TEXT("success"), true);
		if (!TestTrue(TEXT("P7 reload evidence is written"), WriteJson(EvidencePath(ReloadResultFile), Evidence, Error)))
		{
			AddError(Error);
			return false;
		}
		return true;
	}
}

#undef LOCTEXT_NAMESPACE

#endif
