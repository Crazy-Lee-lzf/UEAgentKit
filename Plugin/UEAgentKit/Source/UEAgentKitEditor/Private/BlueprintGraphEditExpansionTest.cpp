// Real-Editor acceptance for the expanded deterministic Blueprint graph edit
// slice.  This test creates its own disposable UAsset, opens/focuses its graph,
// applies a typed multi-node plan with output fan-out, verifies Undo/Redo,
// rejects an unsupported node and a stale preview, then explicitly saves.
// Cross-process reload is verified by the external commandlet harness using the
// evidence JSON written here.

#include "BlueprintContextSha256.h"
#include "BlueprintEditor.h"
#include "BlueprintGraphEditExperimental.h"
#include "BlueprintGraphLayoutApply.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphPin.h"
#include "EdGraphSchema_K2.h"
#include "Editor.h"
#include "Engine/Blueprint.h"
#include "GameFramework/Actor.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformProcess.h"
#include "K2Node_Event.h"
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
#include "UObject/Package.h"
#include "UObject/SavePackage.h"

#if WITH_DEV_AUTOMATION_TESTS

namespace UEAgentKitGraphEditExpansion
{
	const TCHAR* const AssetPath = TEXT("/Game/UEAgentKitGraphEditExpansion/BP_GraphEditExpansion.BP_GraphEditExpansion");
	const TCHAR* const PackageName = TEXT("/Game/UEAgentKitGraphEditExpansion/BP_GraphEditExpansion");
	const TCHAR* const AssetName = TEXT("BP_GraphEditExpansion");

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

	bool SaveBlueprint(UBlueprint* Blueprint)
	{		UPackage* Package = Blueprint != nullptr ? Blueprint->GetOutermost() : nullptr;
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
		return UPackage::SavePackage(Package, Blueprint, *Filename, SaveArgs);
	}

	int32 CountTestLinks(UEdGraph* Graph)
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

	UBlueprint* CreateFixture(FString& OutError)
	{
		if (FindObject<UBlueprint>(nullptr, AssetPath) != nullptr)
		{
			OutError = TEXT("GraphEdit expansion fixture already exists; use a fresh project.");
			return nullptr;
		}
		UPackage* Package = CreatePackage(PackageName);
		UBlueprint* Blueprint = FKismetEditorUtilities::CreateBlueprint(
			AActor::StaticClass(),
			Package,
			FName(AssetName),
			BPTYPE_Normal,
			FName(TEXT("UEAgentKitGraphEditExpansion")));
		if (Blueprint == nullptr || Blueprint->UbergraphPages.Num() == 0)
		{
			OutError = TEXT("Could not create the GraphEdit expansion Blueprint.");
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

		FGraphNodeCreator<UK2Node_Event> Creator(*Graph);
		UK2Node_Event* BeginPlay = Creator.CreateNode(false);
		BeginPlay->EventReference.SetExternalMember(
			TEXT("ReceiveBeginPlay"),
			AActor::StaticClass());
		BeginPlay->bOverrideFunction = true;
		Creator.Finalize();
		if (!BeginPlay->NodeGuid.IsValid())
		{
			BeginPlay->CreateNewGuid();
		}

		FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
		FKismetEditorUtilities::CompileBlueprint(Blueprint, EBlueprintCompileOptions::SkipGarbageCollection);
		if (Blueprint->Status == BS_Error)
		{
			OutError = TEXT("GraphEdit expansion fixture failed to compile.");
			return nullptr;
		}
		FAssetRegistryModule::AssetCreated(Blueprint);
		Package->MarkPackageDirty();
		if (!SaveBlueprint(Blueprint))
		{
			OutError = TEXT("Could not save GraphEdit expansion fixture.");
			return nullptr;
		}
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
			OutError = TEXT("No BlueprintEditor for the fixture.");
			return nullptr;
		}
		FBlueprintEditor* Editor = static_cast<FBlueprintEditor*>(Instance);
		if (!Editor->OpenGraphAndBringToFront(Graph, true).IsValid()
			|| Editor->GetFocusedGraph() != Graph)
		{
			OutError = TEXT("Could not focus the fixture graph.");
			return nullptr;
		}
		return Editor;
	}

	TSharedPtr<FJsonObject> MakeNode(const TCHAR* TempId, const TCHAR* Type, int32 X, int32 Y)
	{
		TSharedRef<FJsonObject> Node = MakeShared<FJsonObject>();
		Node->SetStringField(TEXT("tempId"), TempId);
		Node->SetStringField(TEXT("nodeType"), Type);
		Node->SetNumberField(TEXT("x"), X);
		Node->SetNumberField(TEXT("y"), Y);
		return Node;
	}

	TSharedPtr<FJsonObject> MakeConnection(
		const TCHAR* From,
		const TCHAR* FromPin,
		const TCHAR* To,
		const TCHAR* ToPin)
	{
		TSharedRef<FJsonObject> Connection = MakeShared<FJsonObject>();
		Connection->SetStringField(TEXT("from"), From);
		Connection->SetStringField(TEXT("fromPin"), FromPin);
		Connection->SetStringField(TEXT("to"), To);
		Connection->SetStringField(TEXT("toPin"), ToPin);
		return Connection;
	}

	TSharedPtr<FJsonObject> BuildPreviewParams(UBlueprint* Blueprint, UEdGraph* Graph)
	{
		TArray<TSharedPtr<FJsonValue>> Nodes;
		Nodes.Add(MakeShared<FJsonValueObject>(MakeNode(TEXT("tmp:seq1"), TEXT("sequence"), 0, 0)));
		Nodes.Add(MakeShared<FJsonValueObject>(MakeNode(TEXT("tmp:seq2"), TEXT("sequence"), 0, 260)));
		Nodes.Add(MakeShared<FJsonValueObject>(MakeNode(TEXT("tmp:seq3"), TEXT("sequence"), 0, 520)));
		Nodes.Add(MakeShared<FJsonValueObject>(MakeNode(TEXT("tmp:branch"), TEXT("branch"), 340, 0)));
		Nodes.Add(MakeShared<FJsonValueObject>(MakeNode(TEXT("tmp:int_add"), TEXT("int_add"), 700, 0)));
		Nodes.Add(MakeShared<FJsonValueObject>(MakeNode(TEXT("tmp:eq1"), TEXT("int_equal"), 1000, 0)));
		Nodes.Add(MakeShared<FJsonValueObject>(MakeNode(TEXT("tmp:eq2"), TEXT("int_equal"), 1000, 240)));
		Nodes.Add(MakeShared<FJsonValueObject>(MakeNode(TEXT("tmp:bool_or"), TEXT("bool_or"), 1320, 0)));
		Nodes.Add(MakeShared<FJsonValueObject>(MakeNode(TEXT("tmp:not_bool"), TEXT("not_bool"), 1640, 0)));
		Nodes.Add(MakeShared<FJsonValueObject>(MakeNode(TEXT("tmp:string1"), TEXT("string_concat"), 700, 480)));
		Nodes.Add(MakeShared<FJsonValueObject>(MakeNode(TEXT("tmp:string2"), TEXT("string_concat"), 1000, 480)));
		Nodes.Add(MakeShared<FJsonValueObject>(MakeNode(TEXT("tmp:float_add"), TEXT("double_add"), 700, 720)));

		TArray<TSharedPtr<FJsonValue>> Connections;
		Connections.Add(MakeShared<FJsonValueObject>(
			MakeConnection(TEXT("tmp:seq1"), TEXT("then_0"), TEXT("tmp:branch"), TEXT("execute"))));
		Connections.Add(MakeShared<FJsonValueObject>(
			MakeConnection(TEXT("tmp:seq1"), TEXT("then_1"), TEXT("tmp:seq2"), TEXT("execute"))));
		Connections.Add(MakeShared<FJsonValueObject>(
			MakeConnection(TEXT("tmp:branch"), TEXT("then"), TEXT("tmp:seq3"), TEXT("execute"))));
		Connections.Add(MakeShared<FJsonValueObject>(
			MakeConnection(TEXT("tmp:int_add"), TEXT("ReturnValue"), TEXT("tmp:eq1"), TEXT("A"))));
		Connections.Add(MakeShared<FJsonValueObject>(
			MakeConnection(TEXT("tmp:int_add"), TEXT("ReturnValue"), TEXT("tmp:eq2"), TEXT("A"))));
		Connections.Add(MakeShared<FJsonValueObject>(
			MakeConnection(TEXT("tmp:eq1"), TEXT("ReturnValue"), TEXT("tmp:bool_or"), TEXT("A"))));
		Connections.Add(MakeShared<FJsonValueObject>(
			MakeConnection(TEXT("tmp:eq2"), TEXT("ReturnValue"), TEXT("tmp:bool_or"), TEXT("B"))));
		Connections.Add(MakeShared<FJsonValueObject>(
			MakeConnection(TEXT("tmp:bool_or"), TEXT("ReturnValue"), TEXT("tmp:not_bool"), TEXT("A"))));
		Connections.Add(MakeShared<FJsonValueObject>(
			MakeConnection(TEXT("tmp:not_bool"), TEXT("ReturnValue"), TEXT("tmp:branch"), TEXT("Condition"))));
		Connections.Add(MakeShared<FJsonValueObject>(
			MakeConnection(TEXT("tmp:string1"), TEXT("ReturnValue"), TEXT("tmp:string2"), TEXT("A"))));

		TSharedRef<FJsonObject> Params = MakeShared<FJsonObject>();
		Params->SetStringField(TEXT("assetPath"), Blueprint->GetPathName());
		Params->SetStringField(TEXT("graphGuid"), GuidLower(Graph->GraphGuid));
		Params->SetArrayField(TEXT("nodes"), Nodes);
		Params->SetArrayField(TEXT("connections"), Connections);
		return Params;
	}

	TSharedPtr<FJsonObject> BuildApplyParams(
		const TSharedPtr<FJsonObject>& Preview,
		const FString& Confirmation,
		const FString& RequestPlanId = FString())
	{
		TArray<TSharedPtr<FJsonValue>> Nodes;
		for (const TSharedPtr<FJsonValue>& Value : Preview->GetArrayField(TEXT("nodes")))
		{
			const TSharedPtr<FJsonObject> Node = Value->AsObject();
			if (!Node.IsValid())
			{
				continue;
			}
			TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
			Item->SetStringField(TEXT("tempId"), Node->GetStringField(TEXT("tempId")));
			Item->SetStringField(TEXT("nodeType"), Node->GetStringField(TEXT("nodeType")));
			const TSharedPtr<FJsonObject> Position = Node->GetObjectField(TEXT("position"));
			Item->SetNumberField(TEXT("x"), Position->GetNumberField(TEXT("x")));
			Item->SetNumberField(TEXT("y"), Position->GetNumberField(TEXT("y")));
			Nodes.Add(MakeShared<FJsonValueObject>(Item));
		}
		TArray<TSharedPtr<FJsonValue>> Connections;
		for (const TSharedPtr<FJsonValue>& Value : Preview->GetArrayField(TEXT("connections")))
		{
			const TSharedPtr<FJsonObject> Connection = Value->AsObject();
			if (!Connection.IsValid())
			{
				continue;
			}
			TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
			Item->SetStringField(TEXT("from"), Connection->GetStringField(TEXT("from")));
			Item->SetStringField(TEXT("fromPin"), Connection->GetStringField(TEXT("fromPin")));
			Item->SetStringField(TEXT("to"), Connection->GetStringField(TEXT("to")));
			Item->SetStringField(TEXT("toPin"), Connection->GetStringField(TEXT("toPin")));
			Connections.Add(MakeShared<FJsonValueObject>(Item));
		}

		TSharedRef<FJsonObject> Params = MakeShared<FJsonObject>();
		Params->SetStringField(TEXT("assetPath"), Preview->GetStringField(TEXT("assetPath")));
		Params->SetStringField(TEXT("graphGuid"), Preview->GetStringField(TEXT("graphGuid")));
		Params->SetArrayField(TEXT("nodes"), Nodes);
		Params->SetArrayField(TEXT("connections"), Connections);
		Params->SetStringField(
			TEXT("planId"),
			RequestPlanId.IsEmpty() ? Preview->GetStringField(TEXT("planId")) : RequestPlanId);
		Params->SetStringField(TEXT("confirmation"), Confirmation);
		return Params;
	}

	TSharedPtr<FJsonObject> BuildSaveParams(
		const TSharedPtr<FJsonObject>& ApplyResult,
		const FString& Confirmation)
	{
		TSharedRef<FJsonObject> Params = MakeShared<FJsonObject>();
		Params->SetStringField(TEXT("assetPath"), ApplyResult->GetStringField(TEXT("assetPath")));
		Params->SetStringField(TEXT("graphGuid"), ApplyResult->GetStringField(TEXT("graphGuid")));
		Params->SetStringField(TEXT("planId"), ApplyResult->GetStringField(TEXT("planId")));
		Params->SetStringField(
			TEXT("expectedGraphFingerprint"),
			ApplyResult->GetStringField(TEXT("graphFingerprintAfter")));
		Params->SetNumberField(
			TEXT("expectedNodeCount"),
			ApplyResult->GetNumberField(TEXT("nodeCountAfter")));
		Params->SetNumberField(
			TEXT("expectedLinkCount"),
			ApplyResult->GetNumberField(TEXT("linkCountAfter")));
		Params->SetStringField(TEXT("confirmation"), Confirmation);
		return Params;
	}
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitGraphEditExpansionTest,
	"UEAgentKit.GraphEdit.Expansion",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitGraphEditExpansionTest::RunTest(const FString& Parameters)
{
	using namespace UEAgentKitGraphEditExpansion;
	UEAgentKitBlueprintGraphEditExperimental::SetExperimentalWriteEnabledForTest(true);

	FString Error;
	UBlueprint* Blueprint = CreateFixture(Error);
	if (!TestNotNull(TEXT("GraphEdit expansion fixture is created"), Blueprint))
	{
		AddError(Error);
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}
	UEdGraph* Graph = Blueprint->UbergraphPages.Num() > 0 ? Blueprint->UbergraphPages[0] : nullptr;
	FBlueprintEditor* Editor = OpenAndFocus(Blueprint, Graph, Error);
	if (!TestNotNull(TEXT("GraphEdit expansion graph is focused"), Editor))
	{
		AddError(Error);
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}
	const int32 InitialNodes = Graph->Nodes.Num();
	const int32 InitialLinks = CountTestLinks(Graph);

	TSharedPtr<FJsonObject> Params = BuildPreviewParams(Blueprint, Graph);

	// Unsupported node type must fail before any mutation.
	TSharedPtr<FJsonObject> Invalid = MakeShared<FJsonObject>();
	Invalid->SetStringField(TEXT("assetPath"), Params->GetStringField(TEXT("assetPath")));
	Invalid->SetStringField(TEXT("graphGuid"), Params->GetStringField(TEXT("graphGuid")));
	TArray<TSharedPtr<FJsonValue>> InvalidNodes;
	InvalidNodes.Add(MakeShared<FJsonValueObject>(MakeNode(TEXT("tmp:bad"), TEXT("print_string"), 0, 0)));
	Invalid->SetArrayField(TEXT("nodes"), InvalidNodes);
	Invalid->SetArrayField(TEXT("connections"), TArray<TSharedPtr<FJsonValue>>());
	TSharedPtr<FJsonObject> InvalidResult;
	FString InvalidCode;
	FString InvalidMessage;
	const bool bInvalidRejected =
		!UEAgentKitBlueprintGraphEditExperimental::TryPreviewExperimental(
			Invalid,
			InvalidResult,
			InvalidCode,
			InvalidMessage);
	if (!TestTrue(TEXT("unsupported node type is rejected"), bInvalidRejected)
		|| !TestEqual(TEXT("unsupported node type error"), InvalidCode, FString(TEXT("graph-edit-node-type-not-allowed")))
		|| !TestEqual(TEXT("invalid preview did not mutate nodes"), Graph->Nodes.Num(), InitialNodes))
	{
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}

	TSharedPtr<FJsonObject> Preview;
	FString PreviewCode;
	FString PreviewMessage;
	if (!TestTrue(
		TEXT("typed multi-node preview succeeds"),
		UEAgentKitBlueprintGraphEditExperimental::TryPreviewExperimental(
			Params,
			Preview,
			PreviewCode,
			PreviewMessage)))
	{
		AddError(PreviewCode + TEXT(": ") + PreviewMessage);
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}
	const FString PlanId = Preview->GetStringField(TEXT("planId"));
	const FString ApplyConfirmation =
		FString(UEAgentKitBlueprintGraphEditExperimental::ApplyConfirmationPrefix) + PlanId;

	// Stale preview: preview a second request that only changes a requested
	// node position, then replay the old preview.  The graph itself is never
	// mutated here, so this isolates stale-plan rejection from stale-package
	// rejection.
	TSharedPtr<FJsonObject> StaleParams = BuildPreviewParams(Blueprint, Graph);
	TArray<TSharedPtr<FJsonValue>> StaleNodes = StaleParams->GetArrayField(TEXT("nodes"));
	if (StaleNodes.Num() > 0)
	{
		TSharedPtr<FJsonObject> FirstNode = StaleNodes[0]->AsObject();
		FirstNode->SetNumberField(TEXT("x"), FirstNode->GetNumberField(TEXT("x")) + 1.0);
	}
	StaleParams->SetArrayField(TEXT("nodes"), StaleNodes);
	TSharedPtr<FJsonObject> StalePreview;
	if (!TestTrue(
		TEXT("second distinct preview succeeds"),
		UEAgentKitBlueprintGraphEditExperimental::TryPreviewExperimental(
			StaleParams,
			StalePreview,
			PreviewCode,
			PreviewMessage)))
	{
		AddError(PreviewCode + TEXT(": ") + PreviewMessage);
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}
	TSharedPtr<FJsonObject> StaleResult;
	FString StaleCode;
	FString StaleMessage;
	const bool bStaleRejected =
		!UEAgentKitBlueprintGraphEditExperimental::TryApplyExperimental(
			BuildApplyParams(StalePreview, ApplyConfirmation, PlanId),
			StaleResult,
			StaleCode,
			StaleMessage);
	if (!TestTrue(TEXT("stale preview is rejected"), bStaleRejected)
		|| !TestEqual(TEXT("stale preview error"), StaleCode, FString(TEXT("graph-edit-plan-stale")))
		|| !TestEqual(TEXT("stale apply did not mutate nodes"), Graph->Nodes.Num(), InitialNodes))
	{
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}

	// Apply the second, currently valid preview.
	const FString FreshPlanId = StalePreview->GetStringField(TEXT("planId"));
	const FString FreshConfirmation =
		FString(UEAgentKitBlueprintGraphEditExperimental::ApplyConfirmationPrefix) + FreshPlanId;

	TSharedPtr<FJsonObject> ApplyParams = BuildApplyParams(StalePreview, FreshConfirmation);

	TSharedPtr<FJsonObject> ApplyResult;
	FString ApplyCode;
	FString ApplyMessage;
	if (!TestTrue(
		TEXT("typed multi-connection apply succeeds"),
		UEAgentKitBlueprintGraphEditExperimental::TryApplyExperimental(
			ApplyParams,
			ApplyResult,
			ApplyCode,
			ApplyMessage)))
	{
		AddError(ApplyCode + TEXT(": ") + ApplyMessage);
		AddError(TEXT("preview packageRevision=") + StalePreview->GetStringField(TEXT("packageRevision"))
			+ TEXT(" graphFingerprint=") + StalePreview->GetStringField(TEXT("graphFingerprint"))
			+ TEXT(" planId=") + FreshPlanId);
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}
	if (!TestTrue(TEXT("Apply reports applied"), ApplyResult->GetBoolField(TEXT("applied")))
		|| !TestTrue(TEXT("Apply verifies Undo"), ApplyResult->GetBoolField(TEXT("undoVerified")))
		|| !TestTrue(TEXT("Apply verifies Redo"), ApplyResult->GetBoolField(TEXT("redoVerified")))
		|| !TestEqual(
			TEXT("Apply created the exact node count"),
			static_cast<int32>(ApplyResult->GetNumberField(TEXT("nodeCountAfter"))),
			InitialNodes + 12)
		|| !TestEqual(
			TEXT("Apply created ten connections"),
			static_cast<int32>(ApplyResult->GetNumberField(TEXT("createdConnectionCount"))),
			10)
		|| !TestEqual(
			TEXT("Apply link count is exact"),
			static_cast<int32>(ApplyResult->GetNumberField(TEXT("linkCountAfter"))),
			InitialLinks + 10))
	{
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}

	// The typed fan-out must be present as two distinct int connections.
	int32 Fanout = 0;
	for (const TSharedPtr<FJsonValue>& Value : ApplyResult->GetArrayField(TEXT("createdConnections")))
	{
		const TSharedPtr<FJsonObject> Connection = Value->AsObject();
		if (Connection.IsValid()
			&& Connection->GetStringField(TEXT("from")) == TEXT("tmp:int_add")
			&& Connection->GetStringField(TEXT("fromPin")) == TEXT("ReturnValue"))
		{
			++Fanout;
		}
	}
	if (!TestEqual(TEXT("int output fans out to two distinct inputs"), Fanout, 2))
	{
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}

	// Wrong Save fingerprint must fail before touching the package.
	TSharedPtr<FJsonObject> WrongSave = BuildSaveParams(
		ApplyResult,
		FString(UEAgentKitBlueprintGraphEditExperimental::SaveConfirmationPrefix) + FreshPlanId);
	WrongSave->SetStringField(TEXT("expectedGraphFingerprint"), TEXT("sha1:") + FString::ChrN(40, TEXT('0')));
	TSharedPtr<FJsonObject> WrongSaveResult;
	FString WrongSaveCode;
	FString WrongSaveMessage;
	const bool bWrongSaveRejected =
		!UEAgentKitBlueprintGraphEditExperimental::TrySaveExperimental(
			WrongSave,
			WrongSaveResult,
			WrongSaveCode,
			WrongSaveMessage);
	if (!TestTrue(TEXT("wrong Save fingerprint is rejected"), bWrongSaveRejected)
		|| !TestEqual(TEXT("wrong Save error"), WrongSaveCode, FString(TEXT("graph-edit-save-plan-mismatch"))))
	{
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}

	TSharedPtr<FJsonObject> SaveResult;
	FString SaveCode;
	FString SaveMessage;
	const FString SaveConfirmation =
		FString(UEAgentKitBlueprintGraphEditExperimental::SaveConfirmationPrefix) + FreshPlanId;
	const bool bSaved = UEAgentKitBlueprintGraphEditExperimental::TrySaveExperimental(
		BuildSaveParams(ApplyResult, SaveConfirmation),
		SaveResult,
		SaveCode,
		SaveMessage);
	if (!TestTrue(TEXT("explicit Save succeeds"), bSaved))
	{
		AddError(SaveCode + TEXT(": ") + SaveMessage);
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}
	if (!TestTrue(TEXT("Save reports saved"), SaveResult->GetBoolField(TEXT("saved")))
		|| !TestTrue(TEXT("Save readback is verified"), SaveResult->GetBoolField(TEXT("readbackVerified")))
		|| !TestFalse(TEXT("Save leaves package clean"), SaveResult->GetBoolField(TEXT("packageDirty"))))
	{
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}

	const FString AssetFilename = FPackageName::LongPackageNameToFilename(
		Blueprint->GetOutermost()->GetName(),
		FPackageName::GetAssetPackageExtension());
	FString DiskDigest;
	if (!TestTrue(TEXT("saved package can be hashed"), FBlueprintContextSha256::HashFile(AssetFilename, DiskDigest)))
	{
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}
	DiskDigest = FString(TEXT("sha256:")) + DiskDigest.ToLower();
	if (!TestEqual(
		TEXT("Save asset hash equals disk"),
		SaveResult->GetStringField(TEXT("assetSha256")),
		DiskDigest))
	{
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	}

	TSharedRef<FJsonObject> Evidence = MakeShared<FJsonObject>();
	Evidence->SetStringField(TEXT("schemaVersion"), TEXT("ueak.graph-edit-expansion-evidence/1.0"));
	Evidence->SetNumberField(TEXT("processId"), static_cast<double>(FPlatformProcess::GetCurrentProcessId()));
	Evidence->SetStringField(TEXT("writtenAtUtc"), FDateTime::UtcNow().ToIso8601());
	Evidence->SetStringField(TEXT("assetPath"), Blueprint->GetPathName());
	Evidence->SetStringField(TEXT("assetFilename"), FPaths::ConvertRelativePathToFull(AssetFilename));
	Evidence->SetStringField(TEXT("graphGuid"), GuidLower(Graph->GraphGuid));
	Evidence->SetStringField(TEXT("planId"), FreshPlanId);
	Evidence->SetNumberField(TEXT("rootNodeCount"), InitialNodes);
	Evidence->SetNumberField(TEXT("nodeCountAfter"), ApplyResult->GetNumberField(TEXT("nodeCountAfter")));
	Evidence->SetNumberField(TEXT("linkCountAfter"), ApplyResult->GetNumberField(TEXT("linkCountAfter")));
	Evidence->SetNumberField(TEXT("createdConnectionCount"), 10);
	Evidence->SetStringField(TEXT("assetSha256"), DiskDigest);
	Evidence->SetStringField(TEXT("graphFingerprintAfter"), ApplyResult->GetStringField(TEXT("graphFingerprintAfter")));
	Evidence->SetBoolField(TEXT("undoVerified"), ApplyResult->GetBoolField(TEXT("undoVerified")));
	Evidence->SetBoolField(TEXT("redoVerified"), ApplyResult->GetBoolField(TEXT("redoVerified")));
	Evidence->SetObjectField(TEXT("preview"), StalePreview);
	Evidence->SetObjectField(TEXT("apply"), ApplyResult);
	Evidence->SetObjectField(TEXT("save"), SaveResult);

	TArray<TSharedPtr<FJsonValue>> ExpectedNodeGuids;
	TSet<FString> ExpectedNodeSet;
	for (UEdGraphNode* Node : Graph->Nodes)
	{
		if (Node == nullptr || !Node->NodeGuid.IsValid())
		{
			continue;
		}
		const FString NodeGuid = GuidLower(Node->NodeGuid);
		ExpectedNodeSet.Add(NodeGuid);
		ExpectedNodeGuids.Add(MakeShared<FJsonValueString>(NodeGuid));
	}
	TArray<TSharedPtr<FJsonValue>> ExpectedEdges;
	for (UEdGraphNode* Node : Graph->Nodes)
	{
		if (Node == nullptr || !Node->NodeGuid.IsValid())
		{
			continue;
		}
		const FString NodeGuid = GuidLower(Node->NodeGuid);
		for (UEdGraphPin* Pin : Node->Pins)
		{
			if (Pin == nullptr || Pin->Direction != EGPD_Output)
			{
				continue;
			}
			for (UEdGraphPin* Linked : Pin->LinkedTo)
			{
				if (Linked == nullptr || Linked->GetOwningNode() == nullptr)
				{
					continue;
				}
				ExpectedEdges.Add(MakeShared<FJsonValueString>(
					NodeGuid + TEXT(".") + Pin->PinName.ToString()
					+ TEXT("->") + GuidLower(Linked->GetOwningNode()->NodeGuid)
					+ TEXT(".") + Linked->PinName.ToString()));
			}
		}
	}
	Evidence->SetArrayField(TEXT("expectedNodeGuids"), ExpectedNodeGuids);
	Evidence->SetArrayField(TEXT("expectedEdges"), ExpectedEdges);
	const FString EvidencePath = FPaths::Combine(
		FPaths::ProjectSavedDir(),
		TEXT("GraphEditExpansion"),
		TEXT("evidence.json"));
	TestTrue(TEXT("GraphEdit expansion evidence is written"), WriteJson(EvidencePath, Evidence));

	UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
	return true;
}

#endif // WITH_DEV_AUTOMATION_TESTS
