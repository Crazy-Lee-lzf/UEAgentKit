#include "BlueprintEditor.h"
#include "BlueprintContextSha256.h"
#include "BlueprintGraphEditExperimental.h"
#include "BlueprintPatchExperimental.h"
#include "BlueprintPinDefaultEditExperimental.h"
#include "BlueprintVariableWriteExperimental.h"
#include "Native/NativeNodeRegistry.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "EdGraphSchema_K2.h"
#include "Editor.h"
#include "Engine/Blueprint.h"
#include "GameFramework/Actor.h"
#include "HAL/FileManager.h"
#include "K2Node_CallFunction.h"
#include "K2Node_DynamicCast.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Kismet2/KismetEditorUtilities.h"
#include "Misc/AutomationTest.h"
#include "Misc/PackageName.h"
#include "Misc/Paths.h"
#include "Subsystems/AssetEditorSubsystem.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"

#if WITH_DEV_AUTOMATION_TESTS

/**
 * Phase A acceptance: create Blueprint nodes through the Action Registry.
 *
 * Three runs, one per claim:
 *   Function  -- a PROJECT UFUNCTION (Host module N1AcceptanceHost) becomes a
 *                K2Node_CallFunction whose pins are read from the live node;
 *   Cast      -- a Cast To Pawn action becomes a K2Node_DynamicCast whose
 *                TargetType and pins are read from the live node;
 *   Refusals  -- latent / requiresWorldContext / variadic / requires-binding each
 *                get their own error code, and nothing is written.
 *
 * Every run uses the real patch writer (Preview then Apply), so the transaction,
 * per-operation results, double fingerprint reset and Undo/Redo are the
 * production ones, not a test-only shortcut.
 */
namespace UEAgentKitNodeSpawn
{
	const TCHAR* const FunctionAssetPath =
		TEXT("/Game/UEAgentKitNodeSpawn/BP_NodeSpawnFunction.BP_NodeSpawnFunction");
	const TCHAR* const FunctionPackageName = TEXT("/Game/UEAgentKitNodeSpawn/BP_NodeSpawnFunction");
	const TCHAR* const CastAssetPath =
		TEXT("/Game/UEAgentKitNodeSpawn/BP_NodeSpawnCast.BP_NodeSpawnCast");
	const TCHAR* const CastPackageName = TEXT("/Game/UEAgentKitNodeSpawn/BP_NodeSpawnCast");
	const TCHAR* const RefusalAssetPath =
		TEXT("/Game/UEAgentKitNodeSpawn/BP_NodeSpawnRefusal.BP_NodeSpawnRefusal");
	const TCHAR* const RefusalPackageName = TEXT("/Game/UEAgentKitNodeSpawn/BP_NodeSpawnRefusal");

	const TCHAR* const CallFunctionClassPath = TEXT("/Script/BlueprintGraph.K2Node_CallFunction");
	const TCHAR* const DynamicCastClassPath = TEXT("/Script/BlueprintGraph.K2Node_DynamicCast");
	const TCHAR* const ProjectFunctionPath =
		TEXT("/Script/N1AcceptanceHost.N1FunctionLibrary:AddNumbers");

	FString GuidLower(const FGuid& Guid)
	{
		return Guid.ToString(EGuidFormats::DigitsWithHyphensLower);
	}

	FString ComputeAssetSha(UBlueprint* Blueprint)
	{
		UPackage* Package = Blueprint != nullptr ? Blueprint->GetOutermost() : nullptr;
		if (Package == nullptr)
		{
			return FString();
		}
		const FString Filename = FPackageName::LongPackageNameToFilename(
			Package->GetName(),
			FPackageName::GetAssetPackageExtension());
		FString Digest;
		if (!FBlueprintContextSha256::HashFile(Filename, Digest))
		{
			return FString();
		}
		return FString(TEXT("sha256:")) + Digest.ToLower();
	}

	bool RemoveStaleFiles(const TCHAR* PackageNameText, FString& OutError)
	{
		const FString Filename = FPackageName::LongPackageNameToFilename(
			PackageNameText,
			FPackageName::GetAssetPackageExtension());
		const TArray<FString> Candidates = {
			Filename,
			FPaths::ChangeExtension(Filename, TEXT("uexp")),
			FPaths::ChangeExtension(Filename, TEXT("ubulk")),
		};
		for (const FString& Candidate : Candidates)
		{
			if (IFileManager::Get().FileExists(*Candidate)
				&& !IFileManager::Get().Delete(*Candidate, false, true, true))
			{
				OutError = FString::Printf(
					TEXT("Could not delete the stale disposable fixture file %s."),
					*Candidate);
				return false;
			}
		}
		return true;
	}

	bool SaveBlueprint(UBlueprint* Blueprint)
	{
		UPackage* Package = Blueprint != nullptr ? Blueprint->GetOutermost() : nullptr;
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

	/** An empty Actor Blueprint whose EventGraph has no nodes at all. */
	UBlueprint* CreateFixture(
		const TCHAR* AssetPath,
		const TCHAR* PackageNameText,
		const TCHAR* AssetName,
		FString& OutError)
	{
		if (FindObject<UBlueprint>(nullptr, AssetPath) != nullptr)
		{
			OutError = TEXT("NodeSpawn fixture already exists; use a fresh project.");
			return nullptr;
		}
		if (!RemoveStaleFiles(PackageNameText, OutError))
		{
			return nullptr;
		}
		UPackage* Package = CreatePackage(PackageNameText);
		UBlueprint* Blueprint = FKismetEditorUtilities::CreateBlueprint(
			AActor::StaticClass(),
			Package,
			FName(AssetName),
			BPTYPE_Normal,
			FName(TEXT("UEAgentKitNodeSpawn")));
		if (Blueprint == nullptr || Blueprint->UbergraphPages.IsEmpty())
		{
			OutError = TEXT("Could not create the NodeSpawn fixture Blueprint.");
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
		FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
		FKismetEditorUtilities::CompileBlueprint(
			Blueprint,
			EBlueprintCompileOptions::SkipGarbageCollection);
		if (Blueprint->Status == BS_Error)
		{
			OutError = TEXT("NodeSpawn fixture failed to compile.");
			return nullptr;
		}
		FAssetRegistryModule::AssetCreated(Blueprint);
		Package->MarkPackageDirty();
		if (!SaveBlueprint(Blueprint))
		{
			OutError = TEXT("Could not save the NodeSpawn fixture.");
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

	TSharedPtr<FJsonObject> BuildPatchParams(
		UBlueprint* Blueprint,
		UEdGraph* Graph,
		const TArray<TSharedPtr<FJsonObject>>& Operations)
	{
		TArray<TSharedPtr<FJsonValue>> Values;
		for (const TSharedPtr<FJsonObject>& Operation : Operations)
		{
			Values.Add(MakeShared<FJsonValueObject>(Operation));
		}
		TSharedRef<FJsonObject> Params = MakeShared<FJsonObject>();
		Params->SetStringField(TEXT("assetPath"), Blueprint->GetPathName());
		Params->SetStringField(TEXT("graphGuid"), GuidLower(Graph->GraphGuid));
		Params->SetArrayField(TEXT("operations"), Values);
		return Params;
	}

	TSharedPtr<FJsonObject> BuildPatchApplyParams(
		const TSharedPtr<FJsonObject>& Preview,
		const TArray<TSharedPtr<FJsonObject>>& Operations)
	{
		TArray<TSharedPtr<FJsonValue>> Values;
		for (const TSharedPtr<FJsonObject>& Operation : Operations)
		{
			Values.Add(MakeShared<FJsonValueObject>(Operation));
		}
		TSharedRef<FJsonObject> Params = MakeShared<FJsonObject>();
		Params->SetStringField(TEXT("assetPath"), Preview->GetStringField(TEXT("assetPath")));
		Params->SetStringField(TEXT("graphGuid"), Preview->GetStringField(TEXT("graphGuid")));
		Params->SetArrayField(TEXT("operations"), Values);
		Params->SetStringField(TEXT("planId"), Preview->GetStringField(TEXT("planId")));
		Params->SetStringField(
			TEXT("confirmation"),
			Preview->GetStringField(TEXT("requiredConfirmation")));
		return Params;
	}

	TSharedPtr<FJsonObject> MakeActionOperation(
		const TCHAR* OpId,
		const FString& ActionId,
		int32 X,
		int32 Y)
	{
		TSharedRef<FJsonObject> Operation = MakeShared<FJsonObject>();
		Operation->SetStringField(TEXT("opId"), OpId);
		Operation->SetStringField(TEXT("kind"), TEXT("createNode"));
		Operation->SetStringField(TEXT("actionId"), ActionId);
		Operation->SetNumberField(TEXT("x"), X);
		Operation->SetNumberField(TEXT("y"), Y);
		return Operation;
	}

	UEdGraphNode* FindNodeByGuid(UEdGraph* Graph, const FString& GuidText)
	{
		FGuid Guid;
		if (Graph == nullptr || !FGuid::Parse(GuidText, Guid))
		{
			return nullptr;
		}
		for (UEdGraphNode* Node : Graph->Nodes)
		{
			if (Node != nullptr && Node->NodeGuid == Guid)
			{
				return Node;
			}
		}
		return nullptr;
	}

	/** First described action matching the filters, or null. */
	TSharedPtr<FJsonObject> FindAction(
		const FString& Query,
		const FString& NodeClass,
		const FString& PayloadNeedle,
		FString& OutError)
	{
		FNativeCatalogOptions Options;
		Options.MaxItems = 64;
		Options.FilterQuery = Query;
		Options.FilterNodeClass = NodeClass;
		// The filtered search is a targeted description: only candidates are
		// described, with their own budget.  The unfiltered catalog describes a
		// bounded slice of the database and would not contain these actions.
		Options.bTargetedDescription = true;
		Options.MaxTemplateSpawns = 4096;
		Options.TemplateBudgetMs = 15000.0;
		const FNativeCatalogResult Catalog = FNativeNodeRegistry::Get().BuildCatalog(Options);
		if (!Catalog.bOk)
		{
			OutError = FString::Printf(
				TEXT("Catalog build failed: %s (%s)"),
				*Catalog.ErrorCode,
				*Catalog.ErrorMessage);
			return nullptr;
		}
		for (const FNativeNodeDescriptorRecord& Record : Catalog.Descriptors)
		{
			if (!Record.Descriptor.IsValid())
			{
				continue;
			}
			if (PayloadNeedle.IsEmpty())
			{
				return Record.Descriptor;
			}
			const TSharedPtr<FJsonObject>* DiscoveryKey = nullptr;
			if (!Record.Descriptor->TryGetObjectField(TEXT("discoveryKey"), DiscoveryKey)
				|| DiscoveryKey == nullptr
				|| !DiscoveryKey->IsValid())
			{
				continue;
			}
			const TSharedPtr<FJsonObject>* Payload = nullptr;
			if (!(*DiscoveryKey)->TryGetObjectField(TEXT("payload"), Payload)
				|| Payload == nullptr
				|| !Payload->IsValid())
			{
				continue;
			}
			for (const TPair<FString, TSharedPtr<FJsonValue>>& Pair : (*Payload)->Values)
			{
				if (Pair.Value.IsValid() && Pair.Value->Type == EJson::String)
				{
					const FString Value = Pair.Value->AsString();
					// An exact object path wins over a prefix match: "Pawn" is a
					// prefix of "PawnMovementComponent", and this helper must not
					// silently pick a different class.
					if (Value.Equals(PayloadNeedle, ESearchCase::CaseSensitive))
					{
						return Record.Descriptor;
					}
				}
			}
		}
		for (const FNativeNodeDescriptorRecord& Record : Catalog.Descriptors)
		{
			if (!Record.Descriptor.IsValid())
			{
				continue;
			}
			if (PayloadNeedle.IsEmpty())
			{
				return Record.Descriptor;
			}
			const TSharedPtr<FJsonObject>* DiscoveryKey = nullptr;
			if (!Record.Descriptor->TryGetObjectField(TEXT("discoveryKey"), DiscoveryKey)
				|| DiscoveryKey == nullptr
				|| !DiscoveryKey->IsValid())
			{
				continue;
			}
			const TSharedPtr<FJsonObject>* Payload = nullptr;
			if (!(*DiscoveryKey)->TryGetObjectField(TEXT("payload"), Payload)
				|| Payload == nullptr
				|| !Payload->IsValid())
			{
				continue;
			}
			for (const TPair<FString, TSharedPtr<FJsonValue>>& Pair : (*Payload)->Values)
			{
				if (Pair.Value.IsValid()
					&& Pair.Value->Type == EJson::String
					&& Pair.Value->AsString().Contains(PayloadNeedle))
				{
					return Record.Descriptor;
				}
			}
		}
		OutError = FString::Printf(
			TEXT("No described action matched query '%s' with payload '%s' (catalog matched %d)."),
			*Query,
			*PayloadNeedle,
			Catalog.Descriptors.Num());
		int32 Printed = 0;
		for (const FNativeNodeDescriptorRecord& Record : Catalog.Descriptors)
		{
			if (Printed >= 5 || !Record.Descriptor.IsValid())
			{
				continue;
			}
			FString Payload;
			const TSharedPtr<FJsonObject>* DiscoveryKey = nullptr;
			const TSharedPtr<FJsonObject>* PayloadObject = nullptr;
			if (Record.Descriptor->TryGetObjectField(TEXT("discoveryKey"), DiscoveryKey)
				&& DiscoveryKey != nullptr
				&& (*DiscoveryKey)->TryGetObjectField(TEXT("payload"), PayloadObject)
				&& PayloadObject != nullptr)
			{
				for (const TPair<FString, TSharedPtr<FJsonValue>>& Pair : (*PayloadObject)->Values)
				{
					if (Pair.Value.IsValid() && Pair.Value->Type == EJson::String)
					{
						Payload = Pair.Value->AsString();
						break;
					}
				}
			}
			OutError += FString::Printf(
				TEXT(" [%s | %s | %s]"),
				*Record.Descriptor->GetStringField(TEXT("menuName")),
				*Payload,
				*(*DiscoveryKey)->GetStringField(TEXT("actionKind")));
			++Printed;
		}
		return nullptr;
	}

	const TSharedPtr<FJsonValue>* FindPin(
		const TArray<TSharedPtr<FJsonValue>>& Pins,
		const FString& Name)
	{
		for (const TSharedPtr<FJsonValue>& Value : Pins)
		{
			if (!Value.IsValid() || Value->Type != EJson::Object)
			{
				continue;
			}
			const TSharedPtr<FJsonObject> Pin = Value->AsObject();
			if (Pin.IsValid() && Pin->GetStringField(TEXT("name")).Equals(Name, ESearchCase::CaseSensitive))
			{
				return &Value;
			}
		}
		return nullptr;
	}

	/**
	 * Compare the descriptor's own pin list with the live node's pins.
	 *
	 * This is the phase A "are registry pins trustworthy" check: the descriptor is
	 * built from a transient template node, so if it disagrees with the created
	 * node, the catalog pins are not a contract and search must say so.
	 */
	bool DescriptorPinsMatchResult(
		const TSharedPtr<FJsonObject>& Descriptor,
		const TSharedPtr<FJsonObject>& OperationResult,
		FString& OutReason)
	{
		const TArray<TSharedPtr<FJsonValue>>* DescriptorPins = nullptr;
		const TArray<TSharedPtr<FJsonValue>>* ActualPins = nullptr;
		if (!Descriptor->TryGetArrayField(TEXT("pins"), DescriptorPins) || DescriptorPins == nullptr)
		{
			OutReason = TEXT("The descriptor has no pin list.");
			return false;
		}
		if (!OperationResult->TryGetArrayField(TEXT("pins"), ActualPins) || ActualPins == nullptr)
		{
			OutReason = TEXT("The createNode result has no actual pin list.");
			return false;
		}
		if (DescriptorPins->Num() != ActualPins->Num())
		{
			OutReason = FString::Printf(
				TEXT("Descriptor lists %d pins, the created node has %d."),
				DescriptorPins->Num(),
				ActualPins->Num());
			return false;
		}
		for (const TSharedPtr<FJsonValue>& Value : *DescriptorPins)
		{
			if (!Value.IsValid() || Value->Type != EJson::Object)
			{
				OutReason = TEXT("A descriptor pin is not an object.");
				return false;
			}
			const TSharedPtr<FJsonObject> DescriptorPin = Value->AsObject();
			const FString Name = DescriptorPin->GetStringField(TEXT("name"));
			const TSharedPtr<FJsonValue>* FoundPin = FindPin(*ActualPins, Name);
			const TSharedPtr<FJsonObject> ActualPin =
				FoundPin != nullptr ? (*FoundPin)->AsObject() : nullptr;
			if (!ActualPin.IsValid())
			{
				OutReason = FString::Printf(TEXT("The created node has no pin %s."), *Name);
				return false;
			}
			const FString ExpectedDirection = DescriptorPin->GetStringField(TEXT("direction"));
			if (!ActualPin->GetStringField(TEXT("direction")).Equals(
					ExpectedDirection,
					ESearchCase::CaseSensitive))
			{
				OutReason = FString::Printf(TEXT("Pin %s has the wrong direction."), *Name);
				return false;
			}
			const TSharedPtr<FJsonObject> DescriptorType = DescriptorPin->GetObjectField(TEXT("type"));
			const FString ExpectedCategory = DescriptorType->GetStringField(TEXT("category"));
			if (!ActualPin->GetStringField(TEXT("category")).Equals(
					ExpectedCategory,
					ESearchCase::CaseSensitive))
			{
				OutReason = FString::Printf(
					TEXT("Pin %s has category %s, descriptor says %s."),
					*Name,
					*ActualPin->GetStringField(TEXT("category")),
					*ExpectedCategory);
				return false;
			}
		}
		return true;
	}

	/** The per-operation entry of a preview ("operations") or apply ("results") result. */
	TSharedPtr<FJsonObject> FirstOperationResult(const TSharedPtr<FJsonObject>& Result)
	{
		const TArray<TSharedPtr<FJsonValue>>* Results = nullptr;
		if (!Result.IsValid())
		{
			return nullptr;
		}
		if (!Result->TryGetArrayField(TEXT("results"), Results) || Results == nullptr)
		{
			if (!Result->TryGetArrayField(TEXT("operations"), Results) || Results == nullptr)
			{
				return nullptr;
			}
		}
		if (Results->Num() != 1)
		{
			return nullptr;
		}
		return (*Results)[0].IsValid() ? (*Results)[0]->AsObject() : nullptr;
	}

	/** Shared fixture + gate setup; returns the focused graph or null. */
	UEdGraph* PrepareGraph(
		FAutomationTestBase& Test,
		const TCHAR* AssetPath,
		const TCHAR* PackageName,
		UBlueprint*& OutBlueprint)
	{
		FString Error;
		OutBlueprint = CreateFixture(
			AssetPath,
			PackageName,
			*FPaths::GetBaseFilename(AssetPath),
			Error);
		if (OutBlueprint == nullptr)
		{
			Test.AddError(Error);
			return nullptr;
		}
		UEdGraph* Graph = OutBlueprint->UbergraphPages[0];
		if (Test.TestNotNull(TEXT("the NodeSpawn fixture is created"), OutBlueprint) == false)
		{
			return nullptr;
		}
		if (OpenAndFocus(OutBlueprint, Graph, Error) == nullptr)
		{
			Test.AddError(Error);
			return nullptr;
		}
		return Graph;
	}
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitNodeSpawnFunctionTest,
	"UEAgentKit.NodeSpawn.Function",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitNodeSpawnFunctionTest::RunTest(const FString& Parameters)
{
	using namespace UEAgentKitNodeSpawn;

	UEAgentKitBlueprintPatchExperimental::ResetExperimentalWriteForTest();
	UEAgentKitBlueprintPatchExperimental::ResetNodeSpawnForTest();
	UEAgentKitBlueprintPatchExperimental::SetExperimentalWriteEnabledForTest(true);

	UBlueprint* Blueprint = nullptr;
	UEdGraph* Graph = PrepareGraph(
		*this,
		FunctionAssetPath,
		FunctionPackageName,
		Blueprint);
	if (Graph == nullptr)
	{
		return false;
	}
	if (!TestTrue(
			TEXT("the native registry is available to automation"),
			FNativeNodeRegistry::Get().InitializeForAutomation()))
	{
		return false;
	}

	FString Error;
	TSharedPtr<FJsonObject> Descriptor = FindAction(
		TEXT("AddNumbers"),
		CallFunctionClassPath,
		TEXT("N1FunctionLibrary:AddNumbers"),
		Error);
	if (!TestTrue(TEXT("the project UFUNCTION action is described"), Descriptor.IsValid()))
	{
		AddError(Error);
		return false;
	}
	const FString ActionId = Descriptor->GetStringField(TEXT("actionId"));
	TestTrue(TEXT("the action id has the registry shape"), ActionId.StartsWith(TEXT("action:")));
	TestEqual(
		TEXT("the action is a project-owned function spawner"),
		Descriptor->GetStringField(TEXT("trustLevel")),
		FString(TEXT("project")));

	const TArray<TSharedPtr<FJsonObject>> Operations = {
		MakeActionOperation(TEXT("n1"), ActionId, 9000, 8000),
	};
	const TSharedPtr<FJsonObject> Params = BuildPatchParams(Blueprint, Graph, Operations);

	// Gate 1 of 2: the node-spawn key is a separate opt-in from the patch writer.
	{
		TSharedPtr<FJsonObject> BlockedResult;
		FString BlockedCode;
		FString BlockedMessage;
		TestFalse(
			TEXT("action creation is refused while the node-spawn key is closed"),
			UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
				Params,
				BlockedResult,
				BlockedCode,
				BlockedMessage));
		TestEqual(
			TEXT("the closed second gate has its own error code"),
			BlockedCode,
			FString(TEXT("graph-spawn-experimental-disabled")));
	}
	TestFalse(
		TEXT("the second gate is closed by default"),
		UEAgentKitBlueprintPatchExperimental::IsNodeSpawnWriteAvailable());
	UEAgentKitBlueprintPatchExperimental::SetNodeSpawnEnabledForTest(true);
	TestTrue(
		TEXT("the second gate can be opened"),
		UEAgentKitBlueprintPatchExperimental::IsNodeSpawnWriteAvailable());

	const FString ShaBefore = ComputeAssetSha(Blueprint);
	const int32 NodeCountBefore = Graph->Nodes.Num();
	TestTrue(TEXT("the fixture has no nodes"), NodeCountBefore == 0);

	TSharedPtr<FJsonObject> Preview;
	FString PreviewCode;
	FString PreviewMessage;
	if (!TestTrue(
			TEXT("the project function action previews"),
			UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
				Params,
				Preview,
				PreviewCode,
				PreviewMessage)))
	{
		AddError(FString::Printf(TEXT("%s: %s"), *PreviewCode, *PreviewMessage));
		return false;
	}
	const TSharedPtr<FJsonObject> PreviewOperation = FirstOperationResult(Preview);
	if (!TestNotNull(TEXT("the preview has one operation result"), PreviewOperation.Get()))
	{
		return false;
	}
	TestEqual(
		TEXT("the preview binds the action id"),
		PreviewOperation->GetStringField(TEXT("actionId")),
		ActionId);
	TestEqual(
		TEXT("the preview resolved the CallFunction class"),
		PreviewOperation->GetStringField(TEXT("nodeClass")),
		FString(CallFunctionClassPath));
	TestFalse(
		TEXT("the preview does not predict pins"),
		PreviewOperation->HasField(TEXT("pins")));
	TestEqual(TEXT("the preview wrote nothing"), Graph->Nodes.Num(), NodeCountBefore);

	TSharedPtr<FJsonObject> Apply;
	FString ApplyCode;
	FString ApplyMessage;
	if (!TestTrue(
			TEXT("the project function action applies"),
			UEAgentKitBlueprintPatchExperimental::TryApplyExperimental(
				BuildPatchApplyParams(Preview, Operations),
				Apply,
				ApplyCode,
				ApplyMessage)))
	{
		AddError(FString::Printf(TEXT("%s: %s"), *ApplyCode, *ApplyMessage));
		return false;
	}
	TestEqual(TEXT("one transaction"), static_cast<int32>(Apply->GetNumberField(TEXT("transactionCount"))), 1);
	TestTrue(TEXT("undo verified"), Apply->GetBoolField(TEXT("undoVerified")));
	TestTrue(TEXT("redo verified"), Apply->GetBoolField(TEXT("redoVerified")));
	TestFalse(TEXT("apply never saves"), Apply->GetBoolField(TEXT("saved")));
	TestEqual(
		TEXT("exactly one node was created"),
		static_cast<int32>(Apply->GetNumberField(TEXT("createdNodeCount"))),
		1);
	TestEqual(
		TEXT("the graph gained exactly one node"),
		static_cast<int32>(Apply->GetNumberField(TEXT("nodeCountAfter"))),
		NodeCountBefore + 1);

	const TSharedPtr<FJsonObject> AppliedOperation = FirstOperationResult(Apply);
	if (!TestNotNull(TEXT("the apply result has one operation"), AppliedOperation.Get()))
	{
		return false;
	}
	const FString NodeGuid = AppliedOperation->GetStringField(TEXT("nodeGuid"));
	TestTrue(TEXT("the created node has a GUID"), !NodeGuid.IsEmpty());
	TestEqual(
		TEXT("the created node is a CallFunction"),
		AppliedOperation->GetStringField(TEXT("nodeClass")),
		FString(CallFunctionClassPath));
	TestEqual(
		TEXT("the created node calls the project UFUNCTION"),
		AppliedOperation->GetStringField(TEXT("function")),
		FString(ProjectFunctionPath));

	// The node really exists in the live graph with that class and GUID.
	UEdGraphNode* CreatedNode = FindNodeByGuid(Graph, NodeGuid);
	if (!TestNotNull(TEXT("the created node exists in the graph"), CreatedNode))
	{
		return false;
	}
	TestEqual(
		TEXT("the live node class matches"),
		CreatedNode->GetClass()->GetPathName(),
		FString(CallFunctionClassPath));
	const UK2Node_CallFunction* CallNode = Cast<UK2Node_CallFunction>(CreatedNode);
	const UFunction* TargetFunction = CallNode != nullptr ? CallNode->GetTargetFunction() : nullptr;
	TestNotNull(TEXT("the CallFunction node has a target function"), TargetFunction);
	if (TargetFunction != nullptr)
	{
		TestEqual(
			TEXT("the target function is the project UFUNCTION"),
			TargetFunction->GetPathName(),
			FString(ProjectFunctionPath));
		TestEqual(
			TEXT("the target function belongs to the project module"),
			TargetFunction->GetOuterUClass() != nullptr
				? TargetFunction->GetOuterUClass()->GetPathName()
				: FString(),
			FString(TEXT("/Script/N1AcceptanceHost.N1FunctionLibrary")));
	}

	// Actual pins: A and B are int inputs, ReturnValue is the int output.
	const TArray<TSharedPtr<FJsonValue>>* ActualPins = nullptr;
	if (!TestTrue(
			TEXT("the result carries the actual pins"),
			AppliedOperation->TryGetArrayField(TEXT("pins"), ActualPins) && ActualPins != nullptr))
	{
		return false;
	}
	auto ExpectPin = [this, ActualPins](const TCHAR* Name, const TCHAR* Direction, const TCHAR* Category)
	{
		const TSharedPtr<FJsonValue>* Found = FindPin(*ActualPins, Name);
		if (!TestNotNull(
				*FString::Printf(TEXT("the created node has pin %s"), Name),
				Found))
		{
			return;
		}
		const TSharedPtr<FJsonObject> Pin = (*Found)->AsObject();
		TestEqual(
			FString::Printf(TEXT("pin %s direction"), Name),
			Pin->GetStringField(TEXT("direction")),
			FString(Direction));
		TestEqual(
			FString::Printf(TEXT("pin %s category"), Name),
			Pin->GetStringField(TEXT("category")),
			FString(Category));
	};
	ExpectPin(TEXT("A"), TEXT("input"), TEXT("int"));
	ExpectPin(TEXT("B"), TEXT("input"), TEXT("int"));
	ExpectPin(TEXT("ReturnValue"), TEXT("output"), TEXT("int"));

	// Verification point 0: the registry pins (from a transient template) must be
	// the same pins the created node actually has.
	FString PinMismatch;
	if (!TestTrue(
			TEXT("the registry pins match the created node pins"),
			DescriptorPinsMatchResult(Descriptor, AppliedOperation, PinMismatch)))
	{
		AddError(PinMismatch);
	}

	TestTrue(
		TEXT("the package is dirty (the patch wrote in memory)"),
		Blueprint->GetOutermost()->IsDirty());
	TestEqual(
		TEXT("apply did not save: the disk hash is unchanged"),
		ComputeAssetSha(Blueprint),
		ShaBefore);

	UEAgentKitBlueprintPatchExperimental::ResetNodeSpawnForTest();
	UEAgentKitBlueprintPatchExperimental::ResetExperimentalWriteForTest();
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitNodeSpawnCastTest,
	"UEAgentKit.NodeSpawn.Cast",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitNodeSpawnCastTest::RunTest(const FString& Parameters)
{
	using namespace UEAgentKitNodeSpawn;

	UEAgentKitBlueprintPatchExperimental::ResetExperimentalWriteForTest();
	UEAgentKitBlueprintPatchExperimental::ResetNodeSpawnForTest();
	UEAgentKitBlueprintPatchExperimental::SetExperimentalWriteEnabledForTest(true);
	UEAgentKitBlueprintPatchExperimental::SetNodeSpawnEnabledForTest(true);

	UBlueprint* Blueprint = nullptr;
	UEdGraph* Graph = PrepareGraph(*this, CastAssetPath, CastPackageName, Blueprint);
	if (Graph == nullptr)
	{
		return false;
	}
	if (!TestTrue(
			TEXT("the native registry is available to automation"),
			FNativeNodeRegistry::Get().InitializeForAutomation()))
	{
		return false;
	}

	FString Error;
	// The payload path is a single AND token so the search narrows to the exact
	// APawn cast instead of every "Cast To Pawn ..." entry.
	TSharedPtr<FJsonObject> Descriptor = FindAction(
		TEXT("/Script/Engine.Pawn"),
		DynamicCastClassPath,
		TEXT("/Script/Engine.Pawn"),
		Error);
	if (!TestTrue(TEXT("the Cast To Pawn action is described"), Descriptor.IsValid()))
	{
		AddError(Error);
		return false;
	}
	const FString ActionId = Descriptor->GetStringField(TEXT("actionId"));
	const TSharedPtr<FJsonObject>* DiscoveryKey = nullptr;
	if (!TestTrue(
			TEXT("the cast action has a discovery key"),
			Descriptor->TryGetObjectField(TEXT("discoveryKey"), DiscoveryKey)
				&& DiscoveryKey != nullptr
				&& DiscoveryKey->IsValid()))
	{
		return false;
	}
	TestEqual(
		TEXT("the cast action is classified as cast"),
		(*DiscoveryKey)->GetStringField(TEXT("actionKind")),
		FString(TEXT("cast")));

	const TArray<TSharedPtr<FJsonObject>> Operations = {
		MakeActionOperation(TEXT("c1"), ActionId, 9100, 8100),
	};
	const TSharedPtr<FJsonObject> Params = BuildPatchParams(Blueprint, Graph, Operations);
	const FString ShaBefore = ComputeAssetSha(Blueprint);

	TSharedPtr<FJsonObject> Preview;
	FString PreviewCode;
	FString PreviewMessage;
	if (!TestTrue(
			TEXT("the cast action previews"),
			UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
				Params,
				Preview,
				PreviewCode,
				PreviewMessage)))
	{
		AddError(FString::Printf(TEXT("%s: %s"), *PreviewCode, *PreviewMessage));
		return false;
	}
	TestEqual(
		TEXT("the preview resolved the DynamicCast class"),
		FirstOperationResult(Preview)->GetStringField(TEXT("nodeClass")),
		FString(DynamicCastClassPath));

	TSharedPtr<FJsonObject> Apply;
	FString ApplyCode;
	FString ApplyMessage;
	if (!TestTrue(
			TEXT("the cast action applies"),
			UEAgentKitBlueprintPatchExperimental::TryApplyExperimental(
				BuildPatchApplyParams(Preview, Operations),
				Apply,
				ApplyCode,
				ApplyMessage)))
	{
		AddError(FString::Printf(TEXT("%s: %s"), *ApplyCode, *ApplyMessage));
		return false;
	}
	TestEqual(TEXT("one transaction"), static_cast<int32>(Apply->GetNumberField(TEXT("transactionCount"))), 1);
	TestTrue(TEXT("undo verified"), Apply->GetBoolField(TEXT("undoVerified")));
	TestTrue(TEXT("redo verified"), Apply->GetBoolField(TEXT("redoVerified")));

	const TSharedPtr<FJsonObject> AppliedOperation = FirstOperationResult(Apply);
	if (!TestNotNull(TEXT("the apply result has one operation"), AppliedOperation.Get()))
	{
		return false;
	}
	const FString NodeGuid = AppliedOperation->GetStringField(TEXT("nodeGuid"));
	TestEqual(
		TEXT("the created node is a DynamicCast"),
		AppliedOperation->GetStringField(TEXT("nodeClass")),
		FString(DynamicCastClassPath));
	TestEqual(
		TEXT("the customization set the target class"),
		AppliedOperation->GetStringField(TEXT("targetClass")),
		FString(TEXT("/Script/Engine.Pawn")));

	UEdGraphNode* CreatedNode = FindNodeByGuid(Graph, NodeGuid);
	if (!TestNotNull(TEXT("the created cast node exists"), CreatedNode))
	{
		return false;
	}
	const UK2Node_DynamicCast* CastNode = Cast<UK2Node_DynamicCast>(CreatedNode);
	TestNotNull(TEXT("the node is a K2Node_DynamicCast"), CastNode);
	if (CastNode != nullptr)
	{
		TestEqual(
			TEXT("the live node TargetType is Pawn"),
			CastNode->TargetType != nullptr ? CastNode->TargetType->GetPathName() : FString(),
			FString(TEXT("/Script/Engine.Pawn")));
	}

	const TArray<TSharedPtr<FJsonValue>>* ActualPins = nullptr;
	if (!TestTrue(
			TEXT("the result carries the actual pins"),
			AppliedOperation->TryGetArrayField(TEXT("pins"), ActualPins) && ActualPins != nullptr))
	{
		return false;
	}
	TestNotNull(TEXT("the cast has an Object input pin"), FindPin(*ActualPins, TEXT("Object")));
	TestNotNull(TEXT("the cast has an AsPawn output pin"), FindPin(*ActualPins, TEXT("AsPawn")));
	TestNotNull(TEXT("the cast has a bSuccess output pin"), FindPin(*ActualPins, TEXT("bSuccess")));

	FString PinMismatch;
	if (!TestTrue(
			TEXT("the registry cast pins match the created node pins"),
			DescriptorPinsMatchResult(Descriptor, AppliedOperation, PinMismatch)))
	{
		AddError(PinMismatch);
	}

	TestEqual(
		TEXT("apply did not save: the disk hash is unchanged"),
		ComputeAssetSha(Blueprint),
		ShaBefore);

	UEAgentKitBlueprintPatchExperimental::ResetNodeSpawnForTest();
	UEAgentKitBlueprintPatchExperimental::ResetExperimentalWriteForTest();
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitNodeSpawnRefusalTest,
	"UEAgentKit.NodeSpawn.Refusals",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitNodeSpawnRefusalTest::RunTest(const FString& Parameters)
{
	using namespace UEAgentKitNodeSpawn;

	UEAgentKitBlueprintPatchExperimental::ResetExperimentalWriteForTest();
	UEAgentKitBlueprintPatchExperimental::ResetNodeSpawnForTest();
	UEAgentKitBlueprintPatchExperimental::SetExperimentalWriteEnabledForTest(true);
	UEAgentKitBlueprintPatchExperimental::SetNodeSpawnEnabledForTest(true);

	UBlueprint* Blueprint = nullptr;
	UEdGraph* Graph = PrepareGraph(*this, RefusalAssetPath, RefusalPackageName, Blueprint);
	if (Graph == nullptr)
	{
		return false;
	}
	if (!TestTrue(
			TEXT("the native registry is available to automation"),
			FNativeNodeRegistry::Get().InitializeForAutomation()))
	{
		return false;
	}

	const FString ShaBefore = ComputeAssetSha(Blueprint);
	const int32 NodeCountBefore = Graph->Nodes.Num();
	const FString StateBefore =
		UEAgentKitBlueprintPinDefaultExperimental::ComputeAtomicStateFingerprint(Graph);
	const FString NodesBefore =
		UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicNodeStateFingerprint(Graph);
	const FString VariablesBefore =
		UEAgentKitBlueprintVariableExperimental::ComputeAtomicVariableStateFingerprint(Blueprint);
	const FString EdgesBefore =
		UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(Graph);
	if (!TestTrue(
			TEXT("the refusal fixture has an empty graph"),
			NodeCountBefore == 0
				&& !StateBefore.IsEmpty()
				&& !NodesBefore.IsEmpty()
				&& !VariablesBefore.IsEmpty()
				&& !EdgesBefore.IsEmpty()))
	{
		return false;
	}

	struct FRefusalCase
	{
		const TCHAR* Label;
		const TCHAR* Query;
		const TCHAR* NodeClass;
		const TCHAR* PayloadNeedle;
		const TCHAR* ExpectedCode;
		const TCHAR* ExpectedMessagePart;
	};
	const TArray<FRefusalCase> Cases = {
		{ TEXT("latent"), TEXT("Delay"), CallFunctionClassPath, TEXT(":Delay"),
			TEXT("graph-spawn-action-unsupported"), TEXT("latent") },
		{ TEXT("world context"), TEXT("Print String"), CallFunctionClassPath, TEXT(":PrintString"),
			TEXT("graph-spawn-action-unsupported"), TEXT("requiresWorldContext") },
		{ TEXT("variadic"), TEXT("SumVariadicTagged"), CallFunctionClassPath,
			TEXT("SumVariadicTagged"),
			TEXT("graph-spawn-action-unsupported"), TEXT("variadic") },
		// A real engine Actor-bound event spawner: it needs a level actor to bind
		// to, which this surface does not carry -- the refusal is about the
		// context, not about the node type.
		{ TEXT("requires binding"), TEXT("OnActorBeginOverlap"), TEXT(""), TEXT("OnActorBeginOverlap"),
			TEXT("graph-spawn-requires-binding"), TEXT("binding") },
	};

	for (const FRefusalCase& Case : Cases)
	{
		FString Error;
		TSharedPtr<FJsonObject> Descriptor = FindAction(
			Case.Query,
			Case.NodeClass,
			Case.PayloadNeedle,
			Error);
		if (!TestTrue(
				*FString::Printf(TEXT("the %s action is described"), Case.Label),
				Descriptor.IsValid()))
		{
			AddError(Error);
			continue;
		}
		const FString ActionId = Descriptor->GetStringField(TEXT("actionId"));
		const TArray<TSharedPtr<FJsonObject>> Operations = {
			MakeActionOperation(TEXT("r1"), ActionId, 9200, 8200),
		};
		const TSharedPtr<FJsonObject> Params = BuildPatchParams(Blueprint, Graph, Operations);

		TSharedPtr<FJsonObject> PreviewResult;
		FString PreviewCode;
		FString PreviewMessage;
		const bool bPreviewed = UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
			Params,
			PreviewResult,
			PreviewCode,
			PreviewMessage);
		TestFalse(
			*FString::Printf(TEXT("the %s action is refused at preview"), Case.Label),
			bPreviewed);
		TestEqual(
			*FString::Printf(TEXT("the %s refusal code"), Case.Label),
			PreviewCode,
			FString(Case.ExpectedCode));
		TestTrue(
			*FString::Printf(TEXT("the %s refusal names the field"), Case.Label),
			PreviewMessage.Contains(Case.ExpectedMessagePart, ESearchCase::IgnoreCase));

		// Apply must refuse too, with the same code, and must not write anything.
		// planId/confirmation are well formed but do not match a real preview; the
		// operation parse fails first, which is exactly the refusal being proved.
		TSharedPtr<FJsonObject> ApplyResult;
		FString ApplyCode;
		FString ApplyMessage;
		const TSharedPtr<FJsonObject> ApplyParams = BuildPatchParams(Blueprint, Graph, Operations);
		const FString PlaceholderPlanId = FString(TEXT("sha256:")) + FString::ChrN(64, TEXT('0'));
		ApplyParams->SetStringField(TEXT("planId"), PlaceholderPlanId);
		ApplyParams->SetStringField(
			TEXT("confirmation"),
			FString(UEAgentKitBlueprintPatchExperimental::ApplyConfirmationPrefix) + PlaceholderPlanId);
		const bool bApplied = UEAgentKitBlueprintPatchExperimental::TryApplyExperimental(
			ApplyParams,
			ApplyResult,
			ApplyCode,
			ApplyMessage);
		TestFalse(
			*FString::Printf(TEXT("the %s action is refused at apply"), Case.Label),
			bApplied);
		TestEqual(
			*FString::Printf(TEXT("the %s apply refusal code"), Case.Label),
			ApplyCode,
			FString(Case.ExpectedCode));

		TestEqual(
			*FString::Printf(TEXT("the %s refusal left the node count alone"), Case.Label),
			Graph->Nodes.Num(),
			NodeCountBefore);
		TestEqual(
			*FString::Printf(TEXT("the %s refusal left the state fingerprint alone"), Case.Label),
			UEAgentKitBlueprintPinDefaultExperimental::ComputeAtomicStateFingerprint(Graph),
			StateBefore);
		TestEqual(
			*FString::Printf(TEXT("the %s refusal left the node fingerprint alone"), Case.Label),
			UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicNodeStateFingerprint(Graph),
			NodesBefore);
		TestEqual(
			*FString::Printf(TEXT("the %s refusal left the variable fingerprint alone"), Case.Label),
			UEAgentKitBlueprintVariableExperimental::ComputeAtomicVariableStateFingerprint(Blueprint),
			VariablesBefore);
		TestEqual(
			*FString::Printf(TEXT("the %s refusal left the edge fingerprint alone"), Case.Label),
			UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(Graph),
			EdgesBefore);
		TestFalse(
			*FString::Printf(TEXT("the %s refusal left the package clean"), Case.Label),
			Blueprint->GetOutermost()->IsDirty());
		TestEqual(
			*FString::Printf(TEXT("the %s refusal left the disk hash alone"), Case.Label),
			ComputeAssetSha(Blueprint),
			ShaBefore);
	}

	UEAgentKitBlueprintPatchExperimental::ResetNodeSpawnForTest();
	UEAgentKitBlueprintPatchExperimental::ResetExperimentalWriteForTest();
	return true;
}

#endif
