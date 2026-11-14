#include "BlueprintPatchExperimental.h"

#include "BlueprintContextSha256.h"
#include "BlueprintGraphEditExperimental.h"
#include "BlueprintPinDefaultEditExperimental.h"
#include "BlueprintVariableWriteExperimental.h"
#include "Native/NativeNodeRegistry.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "BlueprintNodeBinder.h"
#include "BlueprintNodeSpawner.h"
#include "BlueprintEditor.h"
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
#include "K2Node_IfThenElse.h"
#include "K2Node_PromotableOperator.h"
#include "K2Node_Select.h"
#include "Kismet/KismetMathLibrary.h"
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
 * Phase B acceptance: connect a PC_Wildcard endpoint and read back what it became.
 *
 * The wildcard nodes themselves are fixtures here (they are spawned through the
 * engine's own UBlueprintNodeSpawner, exactly how the palette would), because
 * createNode deliberately does not grow a "create any special node" path.  What
 * is under test is the writer: a wildcard endpoint is "type not derived yet", the
 * K2 schema derives it, exactly one edge appears, no conversion/promotion node is
 * inserted, and the operation result carries the post-write pin inventory.
 */
namespace UEAgentKitNodeSpawnWildcard
{
	const TCHAR* const SelectAssetPath =
		TEXT("/Game/UEAgentKitNodeSpawn/BP_WildcardSelect.BP_WildcardSelect");
	const TCHAR* const SelectPackageName = TEXT("/Game/UEAgentKitNodeSpawn/BP_WildcardSelect");
	const TCHAR* const PromotableAssetPath =
		TEXT("/Game/UEAgentKitNodeSpawn/BP_WildcardPromotable.BP_WildcardPromotable");
	const TCHAR* const PromotablePackageName = TEXT("/Game/UEAgentKitNodeSpawn/BP_WildcardPromotable");

	const TCHAR* const CallFunctionClassPath = TEXT("/Script/BlueprintGraph.K2Node_CallFunction");

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

	UBlueprint* CreateFixture(
		const TCHAR* AssetPath,
		const TCHAR* PackageNameText,
		FString& OutError)
	{
		if (FindObject<UBlueprint>(nullptr, AssetPath) != nullptr)
		{
			OutError = TEXT("Wildcard fixture already exists; use a fresh project.");
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
			FName(*FPaths::GetBaseFilename(PackageNameText)),
			BPTYPE_Normal,
			FName(TEXT("UEAgentKitNodeSpawnWildcard")));
		if (Blueprint == nullptr || Blueprint->UbergraphPages.IsEmpty())
		{
			OutError = TEXT("Could not create the wildcard fixture Blueprint.");
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
			OutError = TEXT("Wildcard fixture failed to compile.");
			return nullptr;
		}
		FAssetRegistryModule::AssetCreated(Blueprint);
		Package->MarkPackageDirty();
		if (!SaveBlueprint(Blueprint))
		{
			OutError = TEXT("Could not save the wildcard fixture.");
			return nullptr;
		}
		return Blueprint;
	}

	/** Persist the fixture graph so the package starts clean for the patch. */
	bool SaveFixture(UBlueprint* Blueprint)
	{
		if (Blueprint == nullptr || Blueprint->GetOutermost() == nullptr)
		{
			return false;
		}
		Blueprint->GetOutermost()->MarkPackageDirty();
		return SaveBlueprint(Blueprint);
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

	UEdGraphNode* AddSpawnedNode(UEdGraph* Graph, UClass* NodeClass, int32 X, int32 Y)
	{
		UBlueprintNodeSpawner* Spawner = UBlueprintNodeSpawner::Create(NodeClass, GetTransientPackage());
		if (Spawner == nullptr)
		{
			return nullptr;
		}
		return Spawner->Invoke(Graph, IBlueprintNodeBinder::FBindingSet(), FVector2D(X, Y));
	}

	UEdGraphNode* AddCallFunctionNode(
		UEdGraph* Graph,
		UClass* Owner,
		const TCHAR* FunctionName,
		int32 X,
		int32 Y)
	{
		UFunction* Function = Owner != nullptr ? Owner->FindFunctionByName(FName(FunctionName)) : nullptr;
		if (Function == nullptr)
		{
			return nullptr;
		}
		UK2Node_CallFunction* Node = NewObject<UK2Node_CallFunction>(Graph);
		if (Node == nullptr)
		{
			return nullptr;
		}
		// Transactional like a palette-created node: these fixtures exist to be
		// connected, and Undo can only restore a node it recorded.
		Node->SetFlags(RF_Transactional);
		Graph->AddNode(Node, true, false);
		Node->SetFromFunction(Function);
		if (Node->Pins.Num() == 0)
		{
			Node->AllocateDefaultPins();
		}
		Node->NodePosX = X;
		Node->NodePosY = Y;
		if (!Node->NodeGuid.IsValid())
		{
			Node->CreateNewGuid();
		}
		return Node;
	}

	UEdGraphNode* AddPromotableOperatorNode(UEdGraph* Graph, UFunction* Function, int32 X, int32 Y)
	{
		UK2Node_PromotableOperator* Node = NewObject<UK2Node_PromotableOperator>(Graph);
		if (Node == nullptr)
		{
			return nullptr;
		}
		Node->SetFlags(RF_Transactional);
		Graph->AddNode(Node, true, false);
		if (Function != nullptr)
		{
			Node->SetFromFunction(Function);
		}
		if (Node->Pins.Num() == 0)
		{
			Node->AllocateDefaultPins();
		}
		Node->NodePosX = X;
		Node->NodePosY = Y;
		if (!Node->NodeGuid.IsValid())
		{
			Node->CreateNewGuid();
		}
		return Node;
	}

	UEdGraphPin* FindPin(UEdGraphNode* Node, const TCHAR* Name, EEdGraphPinDirection Direction)
	{
		if (Node == nullptr)
		{
			return nullptr;
		}
		for (UEdGraphPin* Pin : Node->Pins)
		{
			if (Pin != nullptr
				&& Pin->Direction == Direction
				&& Pin->PinName.ToString().Equals(Name, ESearchCase::CaseSensitive))
			{
				return Pin;
			}
		}
		return nullptr;
	}

	int32 CountLinks(UEdGraph* Graph)
	{
		int32 Total = 0;
		for (UEdGraphNode* Node : Graph->Nodes)
		{
			if (Node == nullptr)
			{
				continue;
			}
			for (UEdGraphPin* Pin : Node->Pins)
			{
				if (Pin != nullptr)
				{
					Total += Pin->LinkedTo.Num();
				}
			}
		}
		return Total / 2;
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

	TSharedPtr<FJsonObject> MakeConnectOp(
		const TCHAR* OpId,
		const FString& From,
		const TCHAR* FromPin,
		const FString& To,
		const TCHAR* ToPin)
	{
		TSharedRef<FJsonObject> Operation = MakeShared<FJsonObject>();
		Operation->SetStringField(TEXT("opId"), OpId);
		Operation->SetStringField(TEXT("kind"), TEXT("connect"));
		Operation->SetStringField(TEXT("from"), From);
		Operation->SetStringField(TEXT("fromPin"), FromPin);
		Operation->SetStringField(TEXT("to"), To);
		Operation->SetStringField(TEXT("toPin"), ToPin);
		return Operation;
	}

	TSharedPtr<FJsonObject> MakeCreateNodeOp(
		const TCHAR* OpId,
		const TCHAR* NodeType,
		const TCHAR* TempId,
		int32 X,
		int32 Y)
	{
		TSharedRef<FJsonObject> Operation = MakeShared<FJsonObject>();
		Operation->SetStringField(TEXT("opId"), OpId);
		Operation->SetStringField(TEXT("kind"), TEXT("createNode"));
		Operation->SetStringField(TEXT("nodeType"), NodeType);
		Operation->SetStringField(TEXT("tempId"), TempId);
		Operation->SetNumberField(TEXT("x"), X);
		Operation->SetNumberField(TEXT("y"), Y);
		return Operation;
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

	TSharedPtr<FJsonObject> BuildApplyParams(
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

	TArray<TSharedPtr<FJsonValue>> OperationList(const TSharedPtr<FJsonObject>& Result)
	{
		const TArray<TSharedPtr<FJsonValue>>* Values = nullptr;
		if (Result.IsValid() && Result->TryGetArrayField(TEXT("results"), Values) && Values != nullptr)
		{
			return *Values;
		}
		if (Result.IsValid() && Result->TryGetArrayField(TEXT("operations"), Values) && Values != nullptr)
		{
			return *Values;
		}
		return {};
	}

	TSharedPtr<FJsonObject> OperationResult(const TSharedPtr<FJsonObject>& Result, int32 Index)
	{
		const TArray<TSharedPtr<FJsonValue>> Values = OperationList(Result);
		if (!Values.IsValidIndex(Index) || !Values[Index].IsValid())
		{
			return nullptr;
		}
		return Values[Index]->AsObject();
	}

	/** One pin of a resolvedPins snapshot, or null. */
	TSharedPtr<FJsonObject> SnapshotPin(
		const TSharedPtr<FJsonObject>& OperationResultObject,
		const TCHAR* Side,
		const TCHAR* PinName)
	{
		if (!OperationResultObject.IsValid())
		{
			return nullptr;
		}
		const TSharedPtr<FJsonObject> Resolved = OperationResultObject->GetObjectField(TEXT("resolvedPins"));
		if (!Resolved.IsValid() || !Resolved->HasField(Side))
		{
			return nullptr;
		}
		const TSharedPtr<FJsonObject> Node = Resolved->GetObjectField(Side);
		const TArray<TSharedPtr<FJsonValue>>* Pins = nullptr;
		if (!Node.IsValid() || !Node->TryGetArrayField(TEXT("pins"), Pins) || Pins == nullptr)
		{
			return nullptr;
		}
		for (const TSharedPtr<FJsonValue>& Value : *Pins)
		{
			if (!Value.IsValid() || Value->Type != EJson::Object)
			{
				continue;
			}
			const TSharedPtr<FJsonObject> Pin = Value->AsObject();
			if (Pin->GetStringField(TEXT("name")).Equals(PinName, ESearchCase::CaseSensitive))
			{
				return Pin;
			}
		}
		return nullptr;
	}

	/** Pin category inside a resolvedPins snapshot, or empty when it is missing. */
	FString SnapshotPinCategory(
		const TSharedPtr<FJsonObject>& OperationResultObject,
		const TCHAR* Side,
		const TCHAR* PinName)
	{
		const TSharedPtr<FJsonObject> Pin = SnapshotPin(OperationResultObject, Side, PinName);
		return Pin.IsValid() ? Pin->GetStringField(TEXT("category")).ToLower() : FString();
	}

	/** Pin category of an existing node pin, without touching a cached pointer. */
	FString PinCategory(UEdGraphNode* Node, const TCHAR* PinName, EEdGraphPinDirection Direction)
	{
		UEdGraphPin* Pin = FindPin(Node, PinName, Direction);
		return Pin != nullptr ? Pin->PinType.PinCategory.ToString().ToLower() : FString();
	}

	struct FGraphSnapshot
	{
		int32 NodeCount = 0;
		int32 LinkCount = 0;
		FString State;
		FString Variables;
		FString Nodes;
		FString Edges;
		FString AssetSha;
	};

	FGraphSnapshot TakeSnapshot(UBlueprint* Blueprint, UEdGraph* Graph)
	{
		FGraphSnapshot Snapshot;
		Snapshot.NodeCount = Graph->Nodes.Num();
		Snapshot.LinkCount = CountLinks(Graph);
		Snapshot.State = UEAgentKitBlueprintPinDefaultExperimental::ComputeAtomicStateFingerprint(Graph);
		Snapshot.Variables =
			UEAgentKitBlueprintVariableExperimental::ComputeAtomicVariableStateFingerprint(Blueprint);
		Snapshot.Nodes = UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicNodeStateFingerprint(Graph);
		Snapshot.Edges = UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(Graph);
		Snapshot.AssetSha = ComputeAssetSha(Blueprint);
		return Snapshot;
	}

	void TestSnapshotEquals(
		FAutomationTestBase& Test,
		const FGraphSnapshot& Before,
		const FGraphSnapshot& After,
		const TCHAR* Label)
	{
		Test.TestEqual(*FString::Printf(TEXT("%s: node count"), Label), After.NodeCount, Before.NodeCount);
		Test.TestEqual(*FString::Printf(TEXT("%s: link count"), Label), After.LinkCount, Before.LinkCount);
		Test.TestEqual(*FString::Printf(TEXT("%s: state fingerprint"), Label), After.State, Before.State);
		Test.TestEqual(*FString::Printf(TEXT("%s: variables fingerprint"), Label), After.Variables, Before.Variables);
		Test.TestEqual(*FString::Printf(TEXT("%s: nodes fingerprint"), Label), After.Nodes, Before.Nodes);
		Test.TestEqual(*FString::Printf(TEXT("%s: edges fingerprint"), Label), After.Edges, Before.Edges);
		Test.TestEqual(*FString::Printf(TEXT("%s: disk sha"), Label), After.AssetSha, Before.AssetSha);
	}
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitNodeSpawnWildcardSelectTest,
	"UEAgentKit.NodeSpawn.Wildcard",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitNodeSpawnWildcardSelectTest::RunTest(const FString& Parameters)
{
	using namespace UEAgentKitNodeSpawnWildcard;

	UEAgentKitBlueprintPatchExperimental::ResetExperimentalWriteForTest();
	UEAgentKitBlueprintPatchExperimental::ResetNodeSpawnForTest();
	UEAgentKitBlueprintPatchExperimental::SetExperimentalWriteEnabledForTest(true);

	FString Error;
	UBlueprint* Blueprint = CreateFixture(SelectAssetPath, SelectPackageName, Error);
	if (!TestNotNull(TEXT("the wildcard fixture is created"), Blueprint))
	{
		AddError(Error);
		return false;
	}
	UEdGraph* Graph = Blueprint->UbergraphPages[0];
	FBlueprintEditor* Editor = OpenAndFocus(Blueprint, Graph, Error);
	if (Editor == nullptr)
	{
		AddError(Error);
		return false;
	}
	if (!TestTrue(
			TEXT("the native registry is available to automation"),
			FNativeNodeRegistry::Get().InitializeForAutomation()))
	{
		return false;
	}

	// Fixtures: two int sources, one bool source, one Select, one Branch.
	UEdGraphNode* IntA = AddCallFunctionNode(
		Graph, UKismetMathLibrary::StaticClass(), TEXT("Add_IntInt"), 0, 0);
	UEdGraphNode* IntB = AddCallFunctionNode(
		Graph, UKismetMathLibrary::StaticClass(), TEXT("Add_IntInt"), 0, 200);
	UEdGraphNode* BoolSource = AddCallFunctionNode(
		Graph, UKismetMathLibrary::StaticClass(), TEXT("Not_PreBool"), 0, 400);
	UEdGraphNode* Select = AddSpawnedNode(Graph, UK2Node_Select::StaticClass(), 600, 0);
	UEdGraphNode* Branch = AddSpawnedNode(Graph, UK2Node_IfThenElse::StaticClass(), 600, 300);
	if (!TestNotNull(TEXT("the int sources exist"), IntA)
		|| !TestNotNull(TEXT("the second int source exists"), IntB)
		|| !TestNotNull(TEXT("the bool source exists"), BoolSource)
		|| !TestNotNull(TEXT("the Select node is spawned"), Select)
		|| !TestNotNull(TEXT("the Branch node is spawned"), Branch))
	{
		return false;
	}
	if (!TestTrue(TEXT("the fixture graph is saved"), SaveFixture(Blueprint)))
	{
		return false;
	}

	// The engine really does create the wildcard pins; otherwise this test would
	// prove nothing about wildcard connections.
	TestEqual(TEXT("Option 0 is wildcard before any edge"), PinCategory(Select, TEXT("Option 0"), EGPD_Input), FString(TEXT("wildcard")));
	TestEqual(TEXT("Option 1 is wildcard before any edge"), PinCategory(Select, TEXT("Option 1"), EGPD_Input), FString(TEXT("wildcard")));
	TestEqual(TEXT("ReturnValue is wildcard before any edge"), PinCategory(Select, TEXT("ReturnValue"), EGPD_Output), FString(TEXT("wildcard")));

	const int32 NodeCountWithFixtures = Graph->Nodes.Num();
	const int32 LinkCountWithFixtures = CountLinks(Graph);

	// ---------------------------------------------------------------------
	// 1. One int edge types the whole Select; no extra node appears.
	// ---------------------------------------------------------------------
	{
		const FGraphSnapshot Before = TakeSnapshot(Blueprint, Graph);
		const TArray<TSharedPtr<FJsonObject>> Operations = {
			MakeConnectOp(TEXT("c1"), GuidLower(IntA->NodeGuid), TEXT("ReturnValue"), GuidLower(Select->NodeGuid), TEXT("Option 0")),
		};
		const TSharedPtr<FJsonObject> Params = BuildPatchParams(Blueprint, Graph, Operations);

		TSharedPtr<FJsonObject> Preview;
		FString PreviewCode;
		FString PreviewMessage;
		if (!TestTrue(
				TEXT("the wildcard edge previews"),
				UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
					Params, Preview, PreviewCode, PreviewMessage)))
		{
			AddError(FString::Printf(TEXT("%s: %s"), *PreviewCode, *PreviewMessage));
			return false;
		}
		const TSharedPtr<FJsonObject> PreviewOperation = OperationResult(Preview, 0);
		if (!TestNotNull(TEXT("the preview has one operation"), PreviewOperation.Get()))
		{
			return false;
		}
		TestEqual(
			TEXT("the plan honestly reports the wildcard target"),
			PreviewOperation->GetStringField(TEXT("toType")),
			FString(TEXT("wildcard")));

		TSharedPtr<FJsonObject> Apply;
		FString ApplyCode;
		FString ApplyMessage;
		const bool bWildcardApplied = UEAgentKitBlueprintPatchExperimental::TryApplyExperimental(
			BuildApplyParams(Preview, Operations), Apply, ApplyCode, ApplyMessage);
		AddInfo(FString::Printf(
			TEXT("wildcard apply=%s code=%s linkCount=%d Option0=%s Option1=%s ReturnValue=%s"),
			bWildcardApplied ? TEXT("true") : TEXT("false"),
			*ApplyCode,
			CountLinks(Graph),
			*PinCategory(Select, TEXT("Option 0"), EGPD_Input),
			*PinCategory(Select, TEXT("Option 1"), EGPD_Input),
			*PinCategory(Select, TEXT("ReturnValue"), EGPD_Output)));
		if (!TestTrue(TEXT("the wildcard edge applies"), bWildcardApplied))
		{
			AddError(FString::Printf(TEXT("%s: %s"), *ApplyCode, *ApplyMessage));
			return false;
		}
		TestEqual(TEXT("one transaction"), static_cast<int32>(Apply->GetNumberField(TEXT("transactionCount"))), 1);
		TestTrue(TEXT("undo verified"), Apply->GetBoolField(TEXT("undoVerified")));
		TestTrue(TEXT("redo verified"), Apply->GetBoolField(TEXT("redoVerified")));
		TestFalse(TEXT("apply never saves"), Apply->GetBoolField(TEXT("saved")));
		TestEqual(
			TEXT("no conversion or promotion node was inserted"),
			static_cast<int32>(Apply->GetNumberField(TEXT("nodeCountAfter"))),
			Before.NodeCount);
		TestEqual(
			TEXT("exactly one edge was created"),
			static_cast<int32>(Apply->GetNumberField(TEXT("linkCountAfter"))),
			Before.LinkCount + 1);
		TestEqual(
			TEXT("the disk was not written"),
			ComputeAssetSha(Blueprint),
			Before.AssetSha);

		// Read-back: the node rebuilt its pins, and the result reports the actual
		// post-write types (not the plan-time wildcard).
		const TSharedPtr<FJsonObject> Applied = OperationResult(Apply, 0);
		if (!TestNotNull(TEXT("the apply result has one operation"), Applied.Get()))
		{
			return false;
		}
		TestEqual(
			TEXT("the result reports the derived target type"),
			Applied->GetStringField(TEXT("toType")),
			FString(TEXT("int")));

		UEdGraphPin* Option0 = FindPin(Select, TEXT("Option 0"), EGPD_Input);
		UEdGraphPin* Option1 = FindPin(Select, TEXT("Option 1"), EGPD_Input);
		UEdGraphPin* SelectReturn = FindPin(Select, TEXT("ReturnValue"), EGPD_Output);
		if (!TestNotNull(TEXT("Option 0 still exists after the rebuild"), Option0)
			|| !TestNotNull(TEXT("Option 1 still exists after the rebuild"), Option1)
			|| !TestNotNull(TEXT("ReturnValue still exists after the rebuild"), SelectReturn))
		{
			return false;
		}
		TestEqual(TEXT("Option 0 became int"), Option0->PinType.PinCategory.ToString().ToLower(), FString(TEXT("int")));
		TestEqual(TEXT("Option 1 became int"), Option1->PinType.PinCategory.ToString().ToLower(), FString(TEXT("int")));
		TestEqual(TEXT("ReturnValue became int"), SelectReturn->PinType.PinCategory.ToString().ToLower(), FString(TEXT("int")));
		TestTrue(TEXT("the edge is present"), Option0->LinkedTo.Num() == 1);

		TestEqual(
			TEXT("resolvedPins reports Option 0 as int"),
			SnapshotPinCategory(Applied, TEXT("to"), TEXT("Option 0")),
			FString(TEXT("int")));
		TestEqual(
			TEXT("resolvedPins reports Option 1 as int"),
			SnapshotPinCategory(Applied, TEXT("to"), TEXT("Option 1")),
			FString(TEXT("int")));
		TestEqual(
			TEXT("resolvedPins reports ReturnValue as int"),
			SnapshotPinCategory(Applied, TEXT("to"), TEXT("ReturnValue")),
			FString(TEXT("int")));
		TestNotNull(
			TEXT("resolvedPins carries the source node"),
			SnapshotPin(Applied, TEXT("from"), TEXT("ReturnValue")).Get());
	}

	TestEqual(TEXT("the graph still has only the fixtures"), Graph->Nodes.Num(), NodeCountWithFixtures);
	TestEqual(TEXT("one edge exists"), CountLinks(Graph), LinkCountWithFixtures + 1);

	// ---------------------------------------------------------------------
	// 2. A second same-type edge on the already-typed node.
	// ---------------------------------------------------------------------
	{
		// The previous apply wrote in memory (never saved), and the patch requires a
		// clean package: persist it so this scenario starts from a clean baseline.
		if (!TestTrue(TEXT("the fixture is saved between patches"), SaveFixture(Blueprint)))
		{
			return false;
		}
		const FGraphSnapshot Before = TakeSnapshot(Blueprint, Graph);
		const TArray<TSharedPtr<FJsonObject>> Operations = {
			MakeConnectOp(TEXT("c2"), GuidLower(IntB->NodeGuid), TEXT("ReturnValue"), GuidLower(Select->NodeGuid), TEXT("Option 1")),
		};
		const TSharedPtr<FJsonObject> Params = BuildPatchParams(Blueprint, Graph, Operations);
		TSharedPtr<FJsonObject> Preview;
		FString Code;
		FString Message;
		if (!TestTrue(
				TEXT("the second edge previews"),
				UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(Params, Preview, Code, Message)))
		{
			AddError(FString::Printf(TEXT("%s: %s"), *Code, *Message));
			return false;
		}
		TSharedPtr<FJsonObject> Apply;
		if (!TestTrue(
				TEXT("the second edge applies"),
				UEAgentKitBlueprintPatchExperimental::TryApplyExperimental(
					BuildApplyParams(Preview, Operations), Apply, Code, Message)))
		{
			AddError(FString::Printf(TEXT("%s: %s"), *Code, *Message));
			return false;
		}
		TestEqual(
			TEXT("the second edge did not add a node"),
			static_cast<int32>(Apply->GetNumberField(TEXT("nodeCountAfter"))),
			Before.NodeCount);
		TestEqual(
			TEXT("the second edge was created"),
			CountLinks(Graph),
			Before.LinkCount + 1);
		TestTrue(
			TEXT("the first edge survived the rebuild"),
			FindPin(Select, TEXT("Option 0"), EGPD_Input)->LinkedTo.Num() == 1);
		TestTrue(
			TEXT("the second edge landed"),
			FindPin(Select, TEXT("Option 1"), EGPD_Input)->LinkedTo.Num() == 1);
	}

	// ---------------------------------------------------------------------
	// 3. A concrete mismatch on a typed node is refused and writes nothing.
	//    Two operations in one patch: the first types the node, the second uses
	//    the wrong type, so the refusal only exists at apply time.
	// ---------------------------------------------------------------------
	{
		UEdGraphNode* Select2 = AddSpawnedNode(Graph, UK2Node_Select::StaticClass(), 600, 500);
		if (!TestNotNull(TEXT("the second Select is spawned"), Select2))
		{
			return false;
		}
		if (!TestTrue(TEXT("the fixture is saved before the refusal patch"), SaveFixture(Blueprint)))
		{
			return false;
		}
		const int32 NodesBefore = Graph->Nodes.Num();
		const int32 LinksBefore = CountLinks(Graph);
		const FString StateBefore =
			UEAgentKitBlueprintPinDefaultExperimental::ComputeAtomicStateFingerprint(Graph);
		const FString NodesFingerprintBefore =
			UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicNodeStateFingerprint(Graph);
		const FString EdgesBefore =
			UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(Graph);
		const FString ShaBefore = ComputeAssetSha(Blueprint);

		const TArray<TSharedPtr<FJsonObject>> Operations = {
			MakeConnectOp(TEXT("ok"), GuidLower(IntA->NodeGuid), TEXT("ReturnValue"), GuidLower(Select2->NodeGuid), TEXT("Option 0")),
			MakeConnectOp(TEXT("bad"), GuidLower(BoolSource->NodeGuid), TEXT("ReturnValue"), GuidLower(Select2->NodeGuid), TEXT("Option 1")),
		};
		const TSharedPtr<FJsonObject> Params = BuildPatchParams(Blueprint, Graph, Operations);
		TSharedPtr<FJsonObject> Preview;
		FString Code;
		FString Message;
		const bool bPreviewed = UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
			Params, Preview, Code, Message);
		TestTrue(TEXT("both operations are plan-time valid (wildcard target)"), bPreviewed);
		if (!bPreviewed)
		{
			AddError(FString::Printf(TEXT("%s: %s"), *Code, *Message));
			return false;
		}

		TSharedPtr<FJsonObject> Apply;
		const bool bApplied = UEAgentKitBlueprintPatchExperimental::TryApplyExperimental(
			BuildApplyParams(Preview, Operations), Apply, Code, Message);
		TestFalse(TEXT("the mismatched second edge is refused"), bApplied);
		// The schema decides the code; conversion/promotion stay refused and a
		// plain disallow is the schema's own answer.  Whichever it is, the patch
		// must name it instead of silently dropping the edge.
		TestTrue(
			*FString::Printf(TEXT("the refusal carries a schema code (got %s: %s)"), *Code, *Message),
			Code == TEXT("graph-edit-promotion-rejected")
				|| Code == TEXT("graph-edit-conversion-rejected")
				|| Code == TEXT("graph-edit-existing-pin-connection-rejected")
				|| Code == TEXT("graph-edit-pin-type-mismatch"));

		TestEqual(TEXT("the refused patch did not change the node count"), Graph->Nodes.Num(), NodesBefore);
		TestEqual(TEXT("the refused patch did not change the link count"), CountLinks(Graph), LinksBefore);
		TestEqual(
			TEXT("the refused patch restored the state fingerprint"),
			UEAgentKitBlueprintPinDefaultExperimental::ComputeAtomicStateFingerprint(Graph),
			StateBefore);
		TestEqual(
			TEXT("the refused patch restored the node fingerprint"),
			UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicNodeStateFingerprint(Graph),
			NodesFingerprintBefore);
		TestEqual(
			TEXT("the refused patch restored the edge fingerprint"),
			UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(Graph),
			EdgesBefore);
		TestFalse(TEXT("the refused patch left the package clean"), Blueprint->GetOutermost()->IsDirty());
		TestEqual(TEXT("the refused patch left the disk alone"), ComputeAssetSha(Blueprint), ShaBefore);
	}

	// ---------------------------------------------------------------------
	// 4. Concrete (non-wildcard) type mismatch keeps its old refusal.
	// ---------------------------------------------------------------------
	{
		const FGraphSnapshot Before = TakeSnapshot(Blueprint, Graph);
		const TArray<TSharedPtr<FJsonObject>> Operations = {
			MakeConnectOp(TEXT("mismatch"), GuidLower(IntA->NodeGuid), TEXT("ReturnValue"), GuidLower(Branch->NodeGuid), TEXT("Condition")),
		};
		TSharedPtr<FJsonObject> Preview;
		FString Code;
		FString Message;
		TestFalse(
			TEXT("an int output does not connect to a bool condition"),
			UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
				BuildPatchParams(Blueprint, Graph, Operations), Preview, Code, Message));
		TestEqual(
			TEXT("the concrete mismatch keeps its own code"),
			Code,
			FString(TEXT("graph-edit-pin-type-mismatch")));
		TestSnapshotEquals(*this, Before, TakeSnapshot(Blueprint, Graph), TEXT("concrete mismatch"));
	}

	// ---------------------------------------------------------------------
	// 5. tempId endpoint -> wildcard input (the other validation path).
	// ---------------------------------------------------------------------
	{
		UEdGraphNode* Select3 = AddSpawnedNode(Graph, UK2Node_Select::StaticClass(), 600, 700);
		if (!TestNotNull(TEXT("the third Select is spawned"), Select3))
		{
			return false;
		}
		if (!TestTrue(TEXT("the fixture is saved before the tempId patch"), SaveFixture(Blueprint)))
		{
			return false;
		}
		const int32 NodesBefore = Graph->Nodes.Num();
		const int32 LinksBefore = CountLinks(Graph);
		const TArray<TSharedPtr<FJsonObject>> Operations = {
			MakeCreateNodeOp(TEXT("n1"), TEXT("int_add"), TEXT("tmp:add"), 900, 900),
			MakeConnectOp(TEXT("c1"), TEXT("tmp:add"), TEXT("ReturnValue"), GuidLower(Select3->NodeGuid), TEXT("Option 0")),
		};
		const TSharedPtr<FJsonObject> Params = BuildPatchParams(Blueprint, Graph, Operations);
		TSharedPtr<FJsonObject> Preview;
		FString Code;
		FString Message;
		if (!TestTrue(
				TEXT("the tempId edge previews"),
				UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(Params, Preview, Code, Message)))
		{
			AddError(FString::Printf(TEXT("%s: %s"), *Code, *Message));
			return false;
		}
		TSharedPtr<FJsonObject> Apply;
		if (!TestTrue(
				TEXT("the tempId edge applies"),
				UEAgentKitBlueprintPatchExperimental::TryApplyExperimental(
					BuildApplyParams(Preview, Operations), Apply, Code, Message)))
		{
			AddError(FString::Printf(TEXT("%s: %s"), *Code, *Message));
			return false;
		}
		TestEqual(
			TEXT("only the created node was added"),
			static_cast<int32>(Apply->GetNumberField(TEXT("nodeCountAfter"))),
			NodesBefore + 1);
		TestEqual(
			TEXT("the tempId edge was created"),
			CountLinks(Graph),
			LinksBefore + 1);
		TestEqual(
			TEXT("the tempId edge typed the Select"),
			PinCategory(Select3, TEXT("Option 0"), EGPD_Input),
			FString(TEXT("int")));
		const TSharedPtr<FJsonObject> ConnectResult = OperationResult(Apply, 1);
		if (!TestNotNull(TEXT("the tempId connect result exists"), ConnectResult.Get()))
		{
			return false;
		}
		TestEqual(
			TEXT("the tempId edge reports its derived type"),
			ConnectResult->GetStringField(TEXT("toType")),
			FString(TEXT("int")));
		TestNotNull(
			TEXT("the tempId edge reports resolved pins"),
			SnapshotPin(ConnectResult, TEXT("to"), TEXT("ReturnValue")).Get());
	}

	// ---------------------------------------------------------------------
	// 6. Two edges on the SAME wildcard node inside ONE patch: the rebuild that
	//    the first edge triggers must not drop the first edge.
	// ---------------------------------------------------------------------
	{
		UEdGraphNode* Select5 = AddSpawnedNode(Graph, UK2Node_Select::StaticClass(), 600, 1200);
		if (!TestNotNull(TEXT("the fifth Select is spawned"), Select5))
		{
			return false;
		}
		if (!TestTrue(TEXT("the fixture is saved before the two-edge patch"), SaveFixture(Blueprint)))
		{
			return false;
		}
		const FGraphSnapshot Before = TakeSnapshot(Blueprint, Graph);
		const TArray<TSharedPtr<FJsonObject>> Operations = {
			MakeConnectOp(TEXT("a"), GuidLower(IntA->NodeGuid), TEXT("ReturnValue"), GuidLower(Select5->NodeGuid), TEXT("Option 0")),
			MakeConnectOp(TEXT("b"), GuidLower(IntB->NodeGuid), TEXT("ReturnValue"), GuidLower(Select5->NodeGuid), TEXT("Option 1")),
		};
		const TSharedPtr<FJsonObject> Params = BuildPatchParams(Blueprint, Graph, Operations);
		TSharedPtr<FJsonObject> Preview;
		FString Code;
		FString Message;
		if (!TestTrue(
				TEXT("the two-edge patch previews"),
				UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(Params, Preview, Code, Message)))
		{
			AddError(FString::Printf(TEXT("%s: %s"), *Code, *Message));
			return false;
		}
		TSharedPtr<FJsonObject> Apply;
		if (!TestTrue(
				TEXT("the two-edge patch applies"),
				UEAgentKitBlueprintPatchExperimental::TryApplyExperimental(
					BuildApplyParams(Preview, Operations), Apply, Code, Message)))
		{
			AddError(FString::Printf(TEXT("%s: %s"), *Code, *Message));
			return false;
		}
		TestTrue(TEXT("the two-edge patch is one transaction"), Apply->GetBoolField(TEXT("singleTransaction")));
		TestEqual(
			TEXT("the two-edge patch added no node"),
			static_cast<int32>(Apply->GetNumberField(TEXT("nodeCountAfter"))),
			Before.NodeCount);
		TestEqual(
			TEXT("the two-edge patch created two edges"),
			static_cast<int32>(Apply->GetNumberField(TEXT("linkCountAfter"))),
			Before.LinkCount + 2);
		TestTrue(
			TEXT("the first edge survived the second connection"),
			FindPin(Select5, TEXT("Option 0"), EGPD_Input)->LinkedTo.Num() == 1);
		TestTrue(
			TEXT("the second edge is present"),
			FindPin(Select5, TEXT("Option 1"), EGPD_Input)->LinkedTo.Num() == 1);
		TestEqual(
			TEXT("both option pins are int after the patch"),
			PinCategory(Select5, TEXT("Option 0"), EGPD_Input) + TEXT("/")
				+ PinCategory(Select5, TEXT("Option 1"), EGPD_Input),
			FString(TEXT("int/int")));
	}

	// ---------------------------------------------------------------------
	// 7. Injected fault on the second edge: the first edge must be rolled back.
	// ---------------------------------------------------------------------
	{
		UEdGraphNode* Select4 = AddSpawnedNode(Graph, UK2Node_Select::StaticClass(), 600, 1000);
		if (!TestNotNull(TEXT("the fourth Select is spawned"), Select4))
		{
			return false;
		}
		if (!TestTrue(TEXT("the fixture is saved before the rollback patch"), SaveFixture(Blueprint)))
		{
			return false;
		}
		const FGraphSnapshot Before = TakeSnapshot(Blueprint, Graph);
		const TArray<TSharedPtr<FJsonObject>> Operations = {
			MakeConnectOp(TEXT("a"), GuidLower(IntA->NodeGuid), TEXT("ReturnValue"), GuidLower(Select4->NodeGuid), TEXT("Option 0")),
			MakeConnectOp(TEXT("b"), GuidLower(IntB->NodeGuid), TEXT("ReturnValue"), GuidLower(Select4->NodeGuid), TEXT("Option 1")),
		};
		const TSharedPtr<FJsonObject> Params = BuildPatchParams(Blueprint, Graph, Operations);
		TSharedPtr<FJsonObject> Preview;
		FString Code;
		FString Message;
		if (!TestTrue(
				TEXT("the rollback patch previews"),
				UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(Params, Preview, Code, Message)))
		{
			AddError(FString::Printf(TEXT("%s: %s"), *Code, *Message));
			return false;
		}
		UEAgentKitBlueprintPatchExperimental::SetNextApplyFaultForTest(1);
		TSharedPtr<FJsonObject> Apply;
		const bool bApplied = UEAgentKitBlueprintPatchExperimental::TryApplyExperimental(
			BuildApplyParams(Preview, Operations), Apply, Code, Message);
		TestFalse(TEXT("the injected fault fails the patch"), bApplied);
		TestEqual(TEXT("the fault has its own code"), Code, FString(TEXT("graph-patch-apply-fault-injected")));
		if (Apply.IsValid())
		{
			TestFalse(TEXT("no write remains"), Apply->GetBoolField(TEXT("writeOccurred")));
			TestTrue(TEXT("the state was restored"), Apply->GetBoolField(TEXT("stateRestored")));
		}
		TestSnapshotEquals(*this, Before, TakeSnapshot(Blueprint, Graph), TEXT("fault rollback"));
		TestEqual(
			TEXT("the rolled-back Select has no links"),
			FindPin(Select4, TEXT("Option 0"), EGPD_Input)->LinkedTo.Num(),
			0);
	}

	UEAgentKitBlueprintPatchExperimental::ResetNodeSpawnForTest();
	UEAgentKitBlueprintPatchExperimental::ResetExperimentalWriteForTest();
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitNodeSpawnPromotableTest,
	"UEAgentKit.NodeSpawn.Promotable",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitNodeSpawnPromotableTest::RunTest(const FString& Parameters)
{
	using namespace UEAgentKitNodeSpawnWildcard;

	UEAgentKitBlueprintPatchExperimental::ResetExperimentalWriteForTest();
	UEAgentKitBlueprintPatchExperimental::ResetNodeSpawnForTest();
	UEAgentKitBlueprintPatchExperimental::SetExperimentalWriteEnabledForTest(true);

	FString Error;
	UBlueprint* Blueprint = CreateFixture(PromotableAssetPath, PromotablePackageName, Error);
	if (!TestNotNull(TEXT("the promotable fixture is created"), Blueprint))
	{
		AddError(Error);
		return false;
	}
	UEdGraph* Graph = Blueprint->UbergraphPages[0];
	if (OpenAndFocus(Blueprint, Graph, Error) == nullptr)
	{
		AddError(Error);
		return false;
	}
	if (!TestTrue(
			TEXT("the native registry is available to automation"),
			FNativeNodeRegistry::Get().InitializeForAutomation()))
	{
		return false;
	}

	UEdGraphNode* IntA = AddCallFunctionNode(
		Graph, UKismetMathLibrary::StaticClass(), TEXT("Add_IntInt"), 0, 0);
	UEdGraphNode* IntB = AddCallFunctionNode(
		Graph, UKismetMathLibrary::StaticClass(), TEXT("Add_IntInt"), 0, 200);
	// A comparison operator carries a function, exactly like the palette's "=="
	// entry: AllocateDefaultPins still creates wildcard A/B pins and only the
	// ReturnValue is bool, which is the shape phase B has to connect into.
	UFunction* EqualInt = UKismetMathLibrary::StaticClass()->FindFunctionByName(
		FName(TEXT("EqualEqual_IntInt")));
	UEdGraphNode* Operator = AddPromotableOperatorNode(Graph, EqualInt, 600, 0);
	if (!TestNotNull(TEXT("the int sources exist"), IntA)
		|| !TestNotNull(TEXT("the second int source exists"), IntB)
		|| !TestNotNull(TEXT("the comparison function exists"), EqualInt)
		|| !TestNotNull(TEXT("the promotable operator is spawned"), Operator))
	{
		return false;
	}
	if (!TestTrue(TEXT("the fixture graph is saved"), SaveFixture(Blueprint)))
	{
		return false;
	}
	TestEqual(TEXT("A is wildcard before any edge"), PinCategory(Operator, TEXT("A"), EGPD_Input), FString(TEXT("wildcard")));
	TestEqual(TEXT("B is wildcard before any edge"), PinCategory(Operator, TEXT("B"), EGPD_Input), FString(TEXT("wildcard")));

	const FGraphSnapshot Before = TakeSnapshot(Blueprint, Graph);
	const TArray<TSharedPtr<FJsonObject>> Operations = {
		MakeConnectOp(TEXT("a"), GuidLower(IntA->NodeGuid), TEXT("ReturnValue"), GuidLower(Operator->NodeGuid), TEXT("A")),
	};
	const TSharedPtr<FJsonObject> Params = BuildPatchParams(Blueprint, Graph, Operations);
	TSharedPtr<FJsonObject> Preview;
	FString Code;
	FString Message;
	if (!TestTrue(
			TEXT("the promotable edge previews"),
			UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(Params, Preview, Code, Message)))
	{
		AddError(FString::Printf(TEXT("%s: %s"), *Code, *Message));
		return false;
	}
	TestEqual(
		TEXT("the plan reports the wildcard input"),
		OperationResult(Preview, 0)->GetStringField(TEXT("toType")),
		FString(TEXT("wildcard")));

	TSharedPtr<FJsonObject> Apply;
	if (!TestTrue(
			TEXT("the promotable edge applies"),
			UEAgentKitBlueprintPatchExperimental::TryApplyExperimental(
				BuildApplyParams(Preview, Operations), Apply, Code, Message)))
	{
		AddError(FString::Printf(TEXT("%s: %s"), *Code, *Message));
		return false;
	}
	TestEqual(TEXT("one transaction"), static_cast<int32>(Apply->GetNumberField(TEXT("transactionCount"))), 1);
	TestTrue(TEXT("undo verified"), Apply->GetBoolField(TEXT("undoVerified")));
	TestTrue(TEXT("redo verified"), Apply->GetBoolField(TEXT("redoVerified")));
	TestEqual(
		TEXT("no conversion or promotion node was inserted"),
		static_cast<int32>(Apply->GetNumberField(TEXT("nodeCountAfter"))),
		Before.NodeCount);
	TestEqual(
		TEXT("exactly one edge was created"),
		static_cast<int32>(Apply->GetNumberField(TEXT("linkCountAfter"))),
		Before.LinkCount + 1);

	const TSharedPtr<FJsonObject> Applied = OperationResult(Apply, 0);
	if (!TestNotNull(TEXT("the apply result has one operation"), Applied.Get()))
	{
		return false;
	}
	TestEqual(
		TEXT("the result reports the derived input type"),
		Applied->GetStringField(TEXT("toType")),
		FString(TEXT("int")));
	TestEqual(TEXT("A became int"), PinCategory(Operator, TEXT("A"), EGPD_Input), FString(TEXT("int")));

	// Diagnostic value: report what B and ReturnValue became, without inventing a
	// contract the engine does not promise for a functionless operator node.
	AddInfo(FString::Printf(
		TEXT("promotable after connect: A=%s B=%s ReturnValue=%s"),
		*PinCategory(Operator, TEXT("A"), EGPD_Input),
		*PinCategory(Operator, TEXT("B"), EGPD_Input),
		*PinCategory(Operator, TEXT("ReturnValue"), EGPD_Output)));

	TestEqual(
		TEXT("resolvedPins reports the A pin"),
		SnapshotPinCategory(Applied, TEXT("to"), TEXT("A")),
		FString(TEXT("int")));

	// A second same-type edge lands on B and keeps the first one.
	{
		if (!TestTrue(TEXT("the fixture is saved before the second edge"), SaveFixture(Blueprint)))
		{
			return false;
		}
		const TArray<TSharedPtr<FJsonObject>> Second = {
			MakeConnectOp(TEXT("b"), GuidLower(IntB->NodeGuid), TEXT("ReturnValue"), GuidLower(Operator->NodeGuid), TEXT("B")),
		};
		TSharedPtr<FJsonObject> SecondPreview;
		if (!TestTrue(
				TEXT("the second promotable edge previews"),
				UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
					BuildPatchParams(Blueprint, Graph, Second), SecondPreview, Code, Message)))
		{
			AddError(FString::Printf(TEXT("%s: %s"), *Code, *Message));
			return false;
		}
		TSharedPtr<FJsonObject> SecondApply;
		if (!TestTrue(
				TEXT("the second promotable edge applies"),
				UEAgentKitBlueprintPatchExperimental::TryApplyExperimental(
					BuildApplyParams(SecondPreview, Second), SecondApply, Code, Message)))
		{
			AddError(FString::Printf(TEXT("%s: %s"), *Code, *Message));
			return false;
		}
		TestTrue(TEXT("the first promotable edge survived"), FindPin(Operator, TEXT("A"), EGPD_Input)->LinkedTo.Num() == 1);
		TestTrue(TEXT("the second promotable edge landed"), FindPin(Operator, TEXT("B"), EGPD_Input)->LinkedTo.Num() == 1);
		TestEqual(
			TEXT("the second edge added no node"),
			static_cast<int32>(SecondApply->GetNumberField(TEXT("nodeCountAfter"))),
			Before.NodeCount);
	}

	UEAgentKitBlueprintPatchExperimental::ResetNodeSpawnForTest();
	UEAgentKitBlueprintPatchExperimental::ResetExperimentalWriteForTest();
	return true;
}

#endif
