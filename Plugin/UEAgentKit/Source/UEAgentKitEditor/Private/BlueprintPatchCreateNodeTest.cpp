// Real-Editor acceptance for 0-3.4 slice 10: `createNode` inside the atomic
// cross-writer Blueprint patch.
//
// One disposable fixture is used: a K2 event graph with one unlinked bool input
// (Not_PreBool.A), saved on disk.  The SINGLE-WRITER graph edit tool first
// creates branch / sequence / int_add / string_concat there; the test records the
// exact node contracts, removes them again and re-saves, so the PATCH then runs
// against the same clean package and creates the same four nodes at the same
// positions.  Both paths therefore execute against the identical focused graph,
// which is also what the writers themselves require.
// The test proves:
//
//  - the product gate stays off unless the project opts in;
//  - plan-time refusals (non-migrated nodeType, out-of-range coordinate, foreign
//    field, unmigrated `connect`, more operations than the bound) happen before
//    any write and leave the package clean;
//  - a createNode patch derives the same node class / function / position / pin
//    inventory / pin types the single-writer graph edit tool derives;
//  - the nodes the patch creates are field-for-field identical to the nodes the
//    single writer creates (same class, position, pin contract), including the
//    CallFunction owner (KismetMathLibrary vs KismetStringLibrary);
//  - the graph-level NODE fingerprint actually covers the node set: it changes
//    after Apply, returns to the previewed value after Undo, and returns to the
//    applied value after Redo (slice 10 §4 ①);
//  - a failure injected on the SECOND operation removes the FIRST operation's
//    node, leaves the package clean, leaves the disk SHA unchanged and restores
//    the exact before node fingerprint;
//  - each failing operation keeps one result row (applied+rolledBack / failed /
//    notAttempted), and the same previewed plan can be applied afterwards;
//  - the returned nodeGuid identifies the exact live node, so a later patch can
//    target it (connect itself is deliberately a separate slice);
//  - one patch is one Undo/Redo unit, and the explicit Save is the only disk
//    write, after which a fresh createNode preview sees the saved state;
//  - a patch mixing setPinDefault + createVariable + createNode applies all
//    three kinds in one transaction.
//
// The failure on operation 2 uses the WITH_DEV_AUTOMATION_TESTS-only fault seam:
// plan-time validation rejects every externally constructible failure before the
// transaction opens, so this is the only deterministic way to reach the
// in-transaction rollback path.

#include "BlueprintEditor.h"
#include "BlueprintContextSha256.h"
#include "BlueprintGraphEditExperimental.h"
#include "BlueprintGraphLayoutApply.h"
#include "BlueprintPatchExperimental.h"
#include "BlueprintPinDefaultEditExperimental.h"
#include "BlueprintVariableWriteExperimental.h"

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
#include "K2Node_ExecutionSequence.h"
#include "K2Node_IfThenElse.h"
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

namespace UEAgentKitPatchCreateNode
{
	const TCHAR* const PatchAssetPath =
		TEXT("/Game/UEAgentKitPatchExpansion/BP_PatchCreateNode.BP_PatchCreateNode");
	const TCHAR* const PatchPackageName =
		TEXT("/Game/UEAgentKitPatchExpansion/BP_PatchCreateNode");
	const TCHAR* const PatchAssetName = TEXT("BP_PatchCreateNode");

	const TCHAR* const BranchClassPath = TEXT("/Script/BlueprintGraph.K2Node_IfThenElse");
	const TCHAR* const SequenceClassPath =
		TEXT("/Script/BlueprintGraph.K2Node_ExecutionSequence");
	const TCHAR* const CallFunctionClassPath =
		TEXT("/Script/BlueprintGraph.K2Node_CallFunction");

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

	UEdGraphNode* AddCallFunctionNode(
		UEdGraph* Graph,
		UClass* Owner,
		const TCHAR* FunctionName,
		int32 X,
		int32 Y)
	{
		UFunction* Function = Owner != nullptr
			? Owner->FindFunctionByName(FName(FunctionName))
			: nullptr;
		if (Function == nullptr)
		{
			return nullptr;
		}
		UK2Node_CallFunction* Node = NewObject<UK2Node_CallFunction>(Graph);
		if (Node == nullptr)
		{
			return nullptr;
		}
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

	/** One shared fixture builder; bWithBoolInput decides the pin target. */
	UBlueprint* CreateFixture(
		const TCHAR* AssetPath,
		const TCHAR* PackageNameText,
		const TCHAR* AssetName,
		const TCHAR* WidgetName,
		bool bWithBoolInput,
		FString& OutError)
	{
		if (FindObject<UBlueprint>(nullptr, AssetPath) != nullptr)
		{
			OutError = TEXT("CreateNode fixture already exists; use a fresh project.");
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
			FName(WidgetName));
		if (Blueprint == nullptr || Blueprint->UbergraphPages.IsEmpty())
		{
			OutError = TEXT("Could not create the createNode fixture Blueprint.");
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
		if (bWithBoolInput
			&& AddCallFunctionNode(
				Graph,
				UKismetMathLibrary::StaticClass(),
				TEXT("Not_PreBool"),
				0,
				520) == nullptr)
		{
			OutError = TEXT("Could not create the fixture bool function node.");
			return nullptr;
		}
		FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
		FKismetEditorUtilities::CompileBlueprint(
			Blueprint,
			EBlueprintCompileOptions::SkipGarbageCollection);
		if (Blueprint->Status == BS_Error)
		{
			OutError = TEXT("CreateNode fixture failed to compile.");
			return nullptr;
		}
		FAssetRegistryModule::AssetCreated(Blueprint);
		Package->MarkPackageDirty();
		if (!SaveBlueprint(Blueprint))
		{
			OutError = TEXT("Could not save the createNode fixture.");
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

	TSharedPtr<FJsonObject> MakeNode(
		const TCHAR* TempId,
		const TCHAR* NodeType,
		int32 X,
		int32 Y)
	{
		TSharedRef<FJsonObject> Node = MakeShared<FJsonObject>();
		Node->SetStringField(TEXT("tempId"), TempId);
		Node->SetStringField(TEXT("nodeType"), NodeType);
		Node->SetNumberField(TEXT("x"), X);
		Node->SetNumberField(TEXT("y"), Y);
		return Node;
	}

	TSharedPtr<FJsonObject> BuildGraphEditParams(
		UBlueprint* Blueprint,
		UEdGraph* Graph,
		const TArray<TSharedPtr<FJsonObject>>& Nodes)
	{
		TArray<TSharedPtr<FJsonValue>> NodeValues;
		for (const TSharedPtr<FJsonObject>& Node : Nodes)
		{
			NodeValues.Add(MakeShared<FJsonValueObject>(Node));
		}
		TSharedRef<FJsonObject> Params = MakeShared<FJsonObject>();
		Params->SetStringField(TEXT("assetPath"), Blueprint->GetPathName());
		Params->SetStringField(TEXT("graphGuid"), GuidLower(Graph->GraphGuid));
		Params->SetArrayField(TEXT("nodes"), NodeValues);
		Params->SetArrayField(TEXT("connections"), {});
		return Params;
	}

	TSharedPtr<FJsonObject> MakeNodeOperation(
		const TCHAR* OpId,
		const TCHAR* NodeType,
		int32 X,
		int32 Y)
	{
		TSharedRef<FJsonObject> Operation = MakeShared<FJsonObject>();
		Operation->SetStringField(TEXT("opId"), OpId);
		Operation->SetStringField(TEXT("kind"), TEXT("createNode"));
		Operation->SetStringField(TEXT("nodeType"), NodeType);
		Operation->SetNumberField(TEXT("x"), X);
		Operation->SetNumberField(TEXT("y"), Y);
		return Operation;
	}

	TSharedPtr<FJsonObject> BuildGraphEditApplyParams(
		const TSharedPtr<FJsonObject>& Preview,
		const TArray<TSharedPtr<FJsonObject>>& Nodes)
	{
		TSharedRef<FJsonObject> Params = MakeShared<FJsonObject>();
		Params->SetStringField(TEXT("assetPath"), Preview->GetStringField(TEXT("assetPath")));
		Params->SetStringField(TEXT("graphGuid"), Preview->GetStringField(TEXT("graphGuid")));
		TArray<TSharedPtr<FJsonValue>> NodeValues;
		for (const TSharedPtr<FJsonObject>& Node : Nodes)
		{
			NodeValues.Add(MakeShared<FJsonValueObject>(Node));
		}
		Params->SetArrayField(TEXT("nodes"), NodeValues);
		Params->SetArrayField(TEXT("connections"), {});
		Params->SetStringField(TEXT("planId"), Preview->GetStringField(TEXT("planId")));
		Params->SetStringField(
			TEXT("confirmation"),
			Preview->GetStringField(TEXT("requiredConfirmation")));
		return Params;
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
		TSharedRef<FJsonObject> Params = MakeShared<FJsonObject>();
		Params->SetStringField(TEXT("assetPath"), Preview->GetStringField(TEXT("assetPath")));
		Params->SetStringField(TEXT("graphGuid"), Preview->GetStringField(TEXT("graphGuid")));
		TArray<TSharedPtr<FJsonValue>> Values;
		for (const TSharedPtr<FJsonObject>& Operation : Operations)
		{
			Values.Add(MakeShared<FJsonValueObject>(Operation));
		}
		Params->SetArrayField(TEXT("operations"), Values);
		Params->SetStringField(TEXT("planId"), Preview->GetStringField(TEXT("planId")));
		Params->SetStringField(
			TEXT("confirmation"),
			Preview->GetStringField(TEXT("requiredConfirmation")));
		return Params;
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

	/**
	 * Canonical live node contract, used to prove the two creation paths agree.
	 * NodeGuid is deliberately absent (it is unique by construction).
	 */
	FString CanonicalNodeContract(const UEdGraphNode* Node)
	{
		if (Node == nullptr)
		{
			return TEXT("<null>");
		}
		TArray<FString> Pins;
		for (const UEdGraphPin* Pin : Node->Pins)
		{
			if (Pin == nullptr || Pin->bHidden)
			{
				continue;
			}
			Pins.Add(FString::Printf(
				TEXT("%s:%s:%s:%s"),
				*Pin->PinName.ToString(),
				Pin->Direction == EGPD_Input ? TEXT("in") : TEXT("out"),
				*Pin->PinType.PinCategory.ToString(),
				*Pin->PinType.PinSubCategory.ToString()));
		}
		Pins.Sort();
		FString FunctionName;
		if (const UK2Node_CallFunction* CallNode = Cast<UK2Node_CallFunction>(Node))
		{
			FunctionName = CallNode->GetTargetFunction() != nullptr
				? CallNode->GetTargetFunction()->GetName()
				: FString();
		}
		return FString::Printf(
			TEXT("%s|%d|%d|%s|%s"),
			*Node->GetClass()->GetPathName(),
			Node->NodePosX,
			Node->NodePosY,
			*FunctionName,
			*FString::Join(Pins, TEXT(",")));
	}

	FString JoinStrings(const TArray<TSharedPtr<FJsonValue>>& Values)
	{
		TArray<FString> Parts;
		for (const TSharedPtr<FJsonValue>& Value : Values)
		{
			Parts.Add(Value.IsValid() ? Value->AsString() : FString());
		}
		return FString::Join(Parts, TEXT(","));
	}

	FString JoinPinTypes(const TSharedPtr<FJsonObject>& Types)
	{
		if (!Types.IsValid())
		{
			return FString();
		}
		TArray<FString> Keys;
		Types->Values.GetKeys(Keys);
		Keys.Sort();
		FString Joined;
		for (const FString& Key : Keys)
		{
			Joined += FString::Printf(
				TEXT("%s=%s;"),
				*Key,
				*Types->GetStringField(Key));
		}
		return Joined;
	}

	/** One op's plan-time node contract from a patch preview. */
	FString PlanContractFromPatchOperation(const TSharedPtr<FJsonObject>& Operation)
	{
		FString Function;
		if (Operation->HasField(TEXT("function")))
		{
			Function = Operation->GetStringField(TEXT("function"));
		}
		return FString::Printf(
			TEXT("%s|%s|%.0f|%.0f|%s|%s|%s|%s"),
			*Operation->GetStringField(TEXT("nodeType")),
			*Operation->GetStringField(TEXT("nodeClass")),
			Operation->GetNumberField(TEXT("x")),
			Operation->GetNumberField(TEXT("y")),
			*Function,
			*JoinStrings(Operation->GetArrayField(TEXT("inputPins"))),
			*JoinStrings(Operation->GetArrayField(TEXT("outputPins"))),
			*JoinPinTypes(Operation->GetObjectField(TEXT("inputPinTypes"))));
	}

	/** The same op's plan-time contract from a single-writer graph edit preview. */
	FString PlanContractFromWriterNode(const TSharedPtr<FJsonObject>& Node)
	{
		FString Function;
		if (Node->HasField(TEXT("function")))
		{
			Function = Node->GetStringField(TEXT("function"));
		}
		const TSharedPtr<FJsonObject> Position = Node->GetObjectField(TEXT("position"));
		return FString::Printf(
			TEXT("%s|%s|%.0f|%.0f|%s|%s|%s|%s"),
			*Node->GetStringField(TEXT("nodeType")),
			*Node->GetStringField(TEXT("nodeClass")),
			Position->GetNumberField(TEXT("x")),
			Position->GetNumberField(TEXT("y")),
			*Function,
			*JoinStrings(Node->GetArrayField(TEXT("inputPins"))),
			*JoinStrings(Node->GetArrayField(TEXT("outputPins"))),
			*JoinPinTypes(Node->GetObjectField(TEXT("inputPinTypes"))));
	}
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitPatchCreateNodeTest,
	"UEAgentKit.AtomicPatch.CreateNode",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitPatchCreateNodeTest::RunTest(const FString& Parameters)
{
	using namespace UEAgentKitPatchCreateNode;

	UEAgentKitBlueprintPatchExperimental::ResetExperimentalWriteForTest();
	UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
	if (!TestFalse(
			TEXT("experimental atomic patch gate is off unless the project opts in"),
			UEAgentKitBlueprintPatchExperimental::IsExperimentalWriteAvailable()))
	{
		return false;
	}
	UEAgentKitBlueprintPatchExperimental::SetExperimentalWriteEnabledForTest(true);
	UEAgentKitBlueprintGraphEditExperimental::SetExperimentalWriteEnabledForTest(true);
	auto Fail = []()
	{
		UEAgentKitBlueprintPatchExperimental::ResetExperimentalWriteForTest();
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		return false;
	};

	FString Error;
	UBlueprint* Blueprint = CreateFixture(
		PatchAssetPath,
		PatchPackageName,
		PatchAssetName,
		TEXT("UEAgentKitPatchCreateNode"),
		/*bWithBoolInput=*/true,
		Error);
	if (!TestNotNull(TEXT("createNode fixture is created"), Blueprint))
	{
		AddError(Error);
		return false;
	}
	UEdGraph* Graph = Blueprint->UbergraphPages[0];
	UPackage* Package = Blueprint->GetOutermost();
	const int32 NodeCountBefore = Graph->Nodes.Num();
	const int32 LinkCountBefore = 0;
	UEdGraphNode* BoolNode = nullptr;
	for (UEdGraphNode* Node : Graph->Nodes)
	{
		if (Node != nullptr
			&& Node->GetClass()->GetPathName().Equals(CallFunctionClassPath, ESearchCase::CaseSensitive))
		{
			BoolNode = Node;
		}
	}
	if (!TestNotNull(TEXT("the fixture bool function node exists"), BoolNode)
		|| !TestEqual(TEXT("the fixture graph has exactly the bool node"), NodeCountBefore, 1))
	{
		return false;
	}
	FString AssetShaBefore = ComputeAssetSha(Blueprint);
	FString NodesFingerprintBefore;
	if (!TestTrue(TEXT("the fixture asset exists on disk"), !AssetShaBefore.IsEmpty()))
	{
		return false;
	}
	if (!TestNotNull(TEXT("the fixture graph is focused"), OpenAndFocus(Blueprint, Graph, Error)))
	{
		AddError(Error);
		return false;
	}

	// ---- Plan-time refusals: every one must leave the package untouched. ----
	{
		const TArray<TPair<FString, TSharedPtr<FJsonObject>>> Refusals = {
			{
				TEXT("graph-edit-node-type-not-allowed"),
				MakeNodeOperation(TEXT("n1"), TEXT("call_function"), 0, 0),
			},
			{
				TEXT("graph-edit-invalid-parameters"),
				MakeNodeOperation(TEXT("n1"), TEXT("branch"), 100001, 0),
			},
			{
				TEXT("graph-edit-invalid-parameters"),
				MakeNodeOperation(TEXT("n1"), TEXT("branch"), 0, -100001),
			},
		};
		for (const TPair<FString, TSharedPtr<FJsonObject>>& Refusal : Refusals)
		{
			TSharedPtr<FJsonObject> RefusedResult;
			FString RefusedCode;
			FString RefusedMessage;
			if (!TestFalse(
					FString::Printf(
						TEXT("a createNode patch is refused with %s"),
						*Refusal.Key),
					UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
						BuildPatchParams(Blueprint, Graph, { Refusal.Value }),
						RefusedResult,
						RefusedCode,
						RefusedMessage))
				|| !TestEqual(
					FString::Printf(TEXT("refusal code for %s"), *Refusal.Key),
					RefusedCode,
					Refusal.Key))
			{
				return Fail();
			}
		}
		// A foreign field is a typo, not something to ignore.
		{
			TSharedPtr<FJsonObject> BadField =
				MakeNodeOperation(TEXT("n1"), TEXT("branch"), 0, 0);
			BadField->SetStringField(TEXT("tempId"), TEXT("tmp:a"));
			TSharedPtr<FJsonObject> RefusedResult;
			FString RefusedCode;
			FString RefusedMessage;
			if (!TestFalse(
					TEXT("a createNode operation refuses a tempId"),
					UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
						BuildPatchParams(Blueprint, Graph, { BadField }),
						RefusedResult,
						RefusedCode,
						RefusedMessage))
				|| !TestEqual(
					TEXT("foreign-field code"),
					RefusedCode,
					FString(TEXT("graph-patch-unknown-parameter"))))
			{
				return Fail();
			}
		}
		// `connect` is still unmigrated and must be refused before any write.
		{
			TSharedRef<FJsonObject> Connect = MakeShared<FJsonObject>();
			Connect->SetStringField(TEXT("opId"), TEXT("c1"));
			Connect->SetStringField(TEXT("kind"), TEXT("connect"));
			TSharedPtr<FJsonObject> RefusedResult;
			FString RefusedCode;
			FString RefusedMessage;
			if (!TestFalse(
					TEXT("connect is still refused as unmigrated"),
					UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
						BuildPatchParams(Blueprint, Graph, { Connect }),
						RefusedResult,
						RefusedCode,
						RefusedMessage))
				|| !TestEqual(
					TEXT("connect refusal code"),
					RefusedCode,
					FString(TEXT("graph-patch-kind-not-migrated"))))
			{
				return Fail();
			}
		}
		// The 16-operation patch bound is the createNode bound too.
		{
			TArray<TSharedPtr<FJsonObject>> TooMany;
			for (int32 Index = 0; Index < 17; ++Index)
			{
				TooMany.Add(MakeNodeOperation(
					*FString::Printf(TEXT("n%d"), Index),
					TEXT("branch"),
					Index * 40,
					0));
			}
			TSharedPtr<FJsonObject> RefusedResult;
			FString RefusedCode;
			FString RefusedMessage;
			if (!TestFalse(
					TEXT("17 createNode operations are refused"),
					UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
						BuildPatchParams(Blueprint, Graph, TooMany),
						RefusedResult,
						RefusedCode,
						RefusedMessage))
				|| !TestEqual(
					TEXT("operation-bound code"),
					RefusedCode,
					FString(TEXT("graph-patch-invalid-operations"))))
			{
				return Fail();
			}
		}
		if (!TestEqual(
				TEXT("refusals left the graph unchanged"),
				Graph->Nodes.Num(),
				NodeCountBefore)
			|| !TestFalse(TEXT("refusals left the package clean"), Package->IsDirty())
			|| !TestEqual(
				TEXT("refusals left the disk asset unchanged"),
				ComputeAssetSha(Blueprint),
				AssetShaBefore))
		{
			return Fail();
		}
	}

	// ---- Single-writer equivalence: create the same four nodes both ways. ----
	const TArray<TSharedPtr<FJsonObject>> NodeSpecs = {
		MakeNode(TEXT("tmp:branch"), TEXT("branch"), 0, 0),
		MakeNode(TEXT("tmp:sequence"), TEXT("sequence"), 0, 200),
		MakeNode(TEXT("tmp:intadd"), TEXT("int_add"), 340, 0),
		MakeNode(TEXT("tmp:concat"), TEXT("string_concat"), 340, 200),
	};
	const TArray<TSharedPtr<FJsonObject>> NodeOperations = {
		MakeNodeOperation(TEXT("n1"), TEXT("branch"), 0, 0),
		MakeNodeOperation(TEXT("n2"), TEXT("sequence"), 0, 200),
		MakeNodeOperation(TEXT("n3"), TEXT("int_add"), 340, 0),
		MakeNodeOperation(TEXT("n4"), TEXT("string_concat"), 340, 200),
	};

	TArray<FString> WriterLiveContracts;
	TArray<FString> WriterPlanContracts;
	{
		// The single writer needs the exact focused graph too; the fixture is
		// already focused, so both paths run against the same live graph.
		UEAgentKitBlueprintGraphEditExperimental::SetExperimentalWriteEnabledForTest(true);
		TSharedPtr<FJsonObject> WriterPreview;
		FString WriterCode;
		FString WriterMessage;
		if (!TestTrue(
				TEXT("the single-writer graph edit preview succeeds"),
				UEAgentKitBlueprintGraphEditExperimental::TryPreviewExperimental(
					BuildGraphEditParams(Blueprint, Graph, NodeSpecs),
					WriterPreview,
					WriterCode,
					WriterMessage)))
		{
			AddError(WriterCode + TEXT(" ") + WriterMessage);
			return Fail();
		}
		TSharedPtr<FJsonObject> WriterApply;
		if (!TestTrue(
				TEXT("the single-writer graph edit apply succeeds"),
				UEAgentKitBlueprintGraphEditExperimental::TryApplyExperimental(
					BuildGraphEditApplyParams(WriterPreview, NodeSpecs),
					WriterApply,
					WriterCode,
					WriterMessage)))
		{
			AddError(WriterCode + TEXT(" ") + WriterMessage);
			return Fail();
		}
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		for (const TSharedPtr<FJsonObject>& Spec : NodeSpecs)
		{
			UEdGraphNode* Node = nullptr;
			for (UEdGraphNode* Candidate : Graph->Nodes)
			{
				if (Candidate != nullptr
					&& Candidate->NodePosX == static_cast<int32>(Spec->GetNumberField(TEXT("x")))
					&& Candidate->NodePosY == static_cast<int32>(Spec->GetNumberField(TEXT("y"))))
				{
					Node = Candidate;
				}
			}
			if (!TestNotNull(
					*FString::Printf(
						TEXT("single-writer created %s"),
						*Spec->GetStringField(TEXT("nodeType"))),
					Node))
			{
				return Fail();
			}
			WriterLiveContracts.Add(CanonicalNodeContract(Node));
		}
		if (!TestEqual(
				TEXT("the single-writer plan node contracts are captured"),
				WriterLiveContracts.Num(),
				NodeSpecs.Num()))
		{
			return Fail();
		}
		for (int32 Index = 0; Index < NodeSpecs.Num(); ++Index)
		{
			const TSharedPtr<FJsonObject> WriterNode = WriterPreview->GetArrayField(TEXT("nodes"))
				[Index]
					->AsObject();
			WriterPlanContracts.Add(PlanContractFromWriterNode(WriterNode));
		}

		// Put the fixture back to its single-node state so the patch runs against
		// the same clean package the writer started from.  This removal is test
		// setup, not a product write, and it is followed by an explicit save.
		TArray<UEdGraphNode*> WriterNodes;
		for (UEdGraphNode* Node : Graph->Nodes)
		{
			if (Node == nullptr)
			{
				continue;
			}
			for (const TSharedPtr<FJsonObject>& Spec : NodeSpecs)
			{
				if (Node->NodePosX == static_cast<int32>(Spec->GetNumberField(TEXT("x")))
					&& Node->NodePosY == static_cast<int32>(Spec->GetNumberField(TEXT("y"))))
				{
					WriterNodes.Add(Node);
				}
			}
		}
		if (!TestEqual(
				TEXT("the single-writer nodes are found for cleanup"),
				WriterNodes.Num(),
				NodeSpecs.Num()))
		{
			return Fail();
		}
		for (UEdGraphNode* Node : WriterNodes)
		{
			FBlueprintEditorUtils::RemoveNode(Blueprint, Node, true);
		}
		if (!TestEqual(
				TEXT("the fixture is back to one node after cleanup"),
				Graph->Nodes.Num(),
				NodeCountBefore))
		{
			return Fail();
		}
		Package->MarkPackageDirty();
		if (!TestTrue(
				TEXT("the cleaned fixture saves again"),
				SaveBlueprint(Blueprint))
			|| !TestFalse(TEXT("the cleaned fixture is clean"), Package->IsDirty()))
		{
			return Fail();
		}
		AssetShaBefore = ComputeAssetSha(Blueprint);
		if (!TestTrue(
				TEXT("the cleaned fixture has an on-disk baseline"),
				!AssetShaBefore.IsEmpty()))
		{
			return Fail();
		}
		NodesFingerprintBefore =
			UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicNodeStateFingerprint(Graph);
		if (!TestTrue(
				TEXT("the node fingerprint covers the focused graph"),
				!NodesFingerprintBefore.IsEmpty()))
		{
			return Fail();
		}
	}

	TSharedPtr<FJsonObject> Preview;
	{
		UEAgentKitBlueprintPatchExperimental::SetExperimentalWriteEnabledForTest(true);
		FString PreviewCode;
		FString PreviewMessage;
		if (!TestTrue(
				TEXT("the createNode patch preview succeeds"),
				UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
					BuildPatchParams(Blueprint, Graph, NodeOperations),
					Preview,
					PreviewCode,
					PreviewMessage))
			|| !TestTrue(TEXT("the createNode preview result exists"), Preview.IsValid()))
		{
			AddError(PreviewCode + TEXT(" ") + PreviewMessage);
			return Fail();
		}
		const TArray<TSharedPtr<FJsonValue>>& MigratedKinds =
			Preview->GetArrayField(TEXT("migratedKinds"));
		if (!TestEqual(
				TEXT("the preview advertises the three migrated kinds"),
				JoinStrings(MigratedKinds),
				FString(TEXT("setPinDefault,createVariable,createNode")))
			|| !TestEqual(
				TEXT("the preview binds the live node fingerprint"),
				Preview->GetStringField(TEXT("nodesFingerprint")),
				NodesFingerprintBefore)
			|| !TestEqual(
				TEXT("the preview binds the live node count"),
				Preview->GetNumberField(TEXT("nodeCount")),
				static_cast<double>(NodeCountBefore)))
		{
			return Fail();
		}
		const TArray<TSharedPtr<FJsonValue>>& PlannedOperations =
			Preview->GetArrayField(TEXT("operations"));
		if (!TestEqual(
				TEXT("the preview has one operation per createNode"),
				PlannedOperations.Num(),
				NodeOperations.Num()))
		{
			return Fail();
		}
		for (int32 Index = 0; Index < PlannedOperations.Num(); ++Index)
		{
			const TSharedPtr<FJsonObject> Operation = PlannedOperations[Index]->AsObject();
			const FString PatchPlan = PlanContractFromPatchOperation(Operation);
			const FString WriterPlan = WriterPlanContracts[Index];
			if (!TestEqual(
					*FString::Printf(
						TEXT("plan-time node contract %d matches the single writer"),
						Index),
					PatchPlan,
					WriterPlan)
				|| !TestEqual(
					*FString::Printf(TEXT("preview operation %d is validated"), Index),
					Operation->GetStringField(TEXT("status")),
					FString(TEXT("validated")))
				|| !TestFalse(
					*FString::Printf(
						TEXT("preview operation %d has no created nodeGuid yet"),
						Index),
					Operation->HasField(TEXT("nodeGuid"))))
			{
				AddError(FString::Printf(
					TEXT("patch=%s writer=%s"),
					*PatchPlan,
					*WriterPlan));
				return Fail();
			}
		}
	}

	// ---- Atomic rollback: fail on operation 2, the branch must disappear. ----
	{
		UEAgentKitBlueprintPatchExperimental::SetNextApplyFaultForTest(1);
		TSharedPtr<FJsonObject> FailedResult;
		FString FailedCode;
		FString FailedMessage;
		if (!TestFalse(
				TEXT("the fault-injected createNode patch fails"),
				UEAgentKitBlueprintPatchExperimental::TryApplyExperimental(
					BuildPatchApplyParams(Preview, NodeOperations),
					FailedResult,
					FailedCode,
					FailedMessage)))
		{
			return Fail();
		}
		if (!TestEqual(
				TEXT("the injected fault code is reported"),
				FailedCode,
				FString(TEXT("graph-patch-apply-fault-injected")))
			|| !TestTrue(TEXT("the failed apply result exists"), FailedResult.IsValid()))
		{
			return Fail();
		}
		const TArray<TSharedPtr<FJsonValue>>& Results =
			FailedResult->GetArrayField(TEXT("results"));
		if (!TestFalse(
				TEXT("a rolled-back patch reports writeOccurred=false"),
				FailedResult->GetBoolField(TEXT("writeOccurred")))
			|| !TestTrue(
				TEXT("a rolled-back patch reports transactionOpened=true"),
				FailedResult->GetBoolField(TEXT("transactionOpened")))
			|| !TestTrue(
				TEXT("the rollback reports rolledBack=true"),
				FailedResult->GetBoolField(TEXT("rolledBack")))
			|| !TestTrue(
				TEXT("the rollback reports stateRestored=true"),
				FailedResult->GetBoolField(TEXT("stateRestored")))
			|| !TestEqual(TEXT("one result per operation"), Results.Num(), 4))
		{
			return Fail();
		}
		const TSharedPtr<FJsonObject> FirstResult = Results[0]->AsObject();
		const TSharedPtr<FJsonObject> SecondResult = Results[1]->AsObject();
		const TSharedPtr<FJsonObject> ThirdResult = Results[2]->AsObject();
		const TSharedPtr<FJsonObject> FourthResult = Results[3]->AsObject();
		if (!TestEqual(
				TEXT("the first operation was applied before the fault"),
				FirstResult->GetStringField(TEXT("status")),
				FString(TEXT("applied")))
			|| !TestTrue(
				TEXT("the first operation is marked rolledBack"),
				FirstResult->GetBoolField(TEXT("rolledBack")))
			|| !TestEqual(
				TEXT("the second operation is the failed one"),
				SecondResult->GetStringField(TEXT("status")),
				FString(TEXT("failed")))
			|| !TestEqual(
				TEXT("the failed operation carries the injected code"),
				SecondResult->GetStringField(TEXT("errorCode")),
				FString(TEXT("graph-patch-apply-fault-injected")))
			|| !TestEqual(
				TEXT("the third operation was never attempted"),
				ThirdResult->GetStringField(TEXT("status")),
				FString(TEXT("notAttempted")))
			|| !TestEqual(
				TEXT("the fourth operation was never attempted"),
				FourthResult->GetStringField(TEXT("status")),
				FString(TEXT("notAttempted"))))
		{
			return Fail();
		}
		// The created branch must be gone, not just "not reported".
		bool bBranchAtOrigin = false;
		for (const UEdGraphNode* Node : Graph->Nodes)
		{
			if (Node != nullptr
				&& Node->GetClass()->GetPathName().Equals(
					BranchClassPath,
					ESearchCase::CaseSensitive)
				&& Node->NodePosX == 0
				&& Node->NodePosY == 0)
			{
				bBranchAtOrigin = true;
			}
		}
		if (!TestFalse(TEXT("the first operation's node was removed"), bBranchAtOrigin)
			|| !TestEqual(TEXT("the node count is back to before"), Graph->Nodes.Num(), NodeCountBefore)
			|| !TestEqual(
				TEXT("the node fingerprint is back to before"),
				UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicNodeStateFingerprint(Graph),
				NodesFingerprintBefore)
			|| !TestFalse(TEXT("the package is clean after the rollback"), Package->IsDirty())
			|| !TestEqual(
				TEXT("the disk asset is unchanged by the rollback"),
				ComputeAssetSha(Blueprint),
				AssetShaBefore))
		{
			return Fail();
		}
		// Nothing was left behind: the same plan is still valid.
		TSharedPtr<FJsonObject> RePreview;
		FString RePreviewCode;
		FString RePreviewMessage;
		if (!TestTrue(
				TEXT("the same createNode plan can be previewed again"),
				UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
					BuildPatchParams(Blueprint, Graph, NodeOperations),
					RePreview,
					RePreviewCode,
					RePreviewMessage))
			|| !TestEqual(
				TEXT("the planId is stable after the rollback"),
				RePreview->GetStringField(TEXT("planId")),
				Preview->GetStringField(TEXT("planId"))))
		{
			AddError(RePreviewCode + TEXT(" ") + RePreviewMessage);
			return Fail();
		}
	}

	// ---- The same patch now applies as one transaction. ----
	TSharedPtr<FJsonObject> ApplyResult;
	{
		FString ApplyCode;
		FString ApplyMessage;
		if (!TestTrue(
				TEXT("the createNode patch applies"),
				UEAgentKitBlueprintPatchExperimental::TryApplyExperimental(
					BuildPatchApplyParams(Preview, NodeOperations),
					ApplyResult,
					ApplyCode,
					ApplyMessage))
			|| !TestTrue(TEXT("the apply result exists"), ApplyResult.IsValid()))
		{
			AddError(ApplyCode + TEXT(" ") + ApplyMessage);
			return Fail();
		}
		const FString NodesFingerprintAfter =
			UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicNodeStateFingerprint(Graph);
		if (!TestTrue(TEXT("applied"), ApplyResult->GetBoolField(TEXT("applied")))
			|| !TestTrue(
				TEXT("single transaction"),
				ApplyResult->GetBoolField(TEXT("singleTransaction")))
			|| !TestEqual(
				TEXT("transaction count"),
				ApplyResult->GetNumberField(TEXT("transactionCount")),
				1.0)
			|| !TestTrue(TEXT("undo verified"), ApplyResult->GetBoolField(TEXT("undoVerified")))
			|| !TestTrue(TEXT("redo verified"), ApplyResult->GetBoolField(TEXT("redoVerified")))
			|| !TestFalse(TEXT("never saves"), ApplyResult->GetBoolField(TEXT("saved")))
			|| !TestFalse(TEXT("never compiles"), ApplyResult->GetBoolField(TEXT("compiled")))
			|| !TestEqual(
				TEXT("created node count"),
				ApplyResult->GetNumberField(TEXT("createdNodeCount")),
				4.0)
			|| !TestEqual(
				TEXT("node count after"),
				ApplyResult->GetNumberField(TEXT("nodeCountAfter")),
				static_cast<double>(NodeCountBefore + 4))
			|| !TestEqual(
				TEXT("link count is unchanged"),
				ApplyResult->GetNumberField(TEXT("linkCountAfter")),
				static_cast<double>(LinkCountBefore))
			|| !TestFalse(
				TEXT("the node fingerprint after differs from before"),
				ApplyResult->GetStringField(TEXT("nodesFingerprintAfter")).Equals(
					NodesFingerprintBefore,
					ESearchCase::CaseSensitive))
			|| !TestEqual(
				TEXT("the reported node fingerprint after is the live one"),
				ApplyResult->GetStringField(TEXT("nodesFingerprintAfter")),
				NodesFingerprintAfter)
			|| !TestFalse(
				TEXT("the graph-level state fingerprint also changes with the node set"),
				ApplyResult->GetStringField(TEXT("stateFingerprintAfter")).Equals(
					Preview->GetStringField(TEXT("stateFingerprint")),
					ESearchCase::CaseSensitive))
			|| !TestEqual(
				TEXT("the disk asset is still unchanged before Save"),
				ComputeAssetSha(Blueprint),
				AssetShaBefore))
		{
			return Fail();
		}
		// Every operation result carries its own created nodeGuid, and that GUID
		// names the exact live node (that is what a later connect patch needs).
		const TArray<TSharedPtr<FJsonValue>>& Results =
			ApplyResult->GetArrayField(TEXT("results"));
		const TArray<TSharedPtr<FJsonObject>> Expected = {
			MakeNode(TEXT("tmp:branch"), TEXT("branch"), 0, 0),
			MakeNode(TEXT("tmp:sequence"), TEXT("sequence"), 0, 200),
			MakeNode(TEXT("tmp:intadd"), TEXT("int_add"), 340, 0),
			MakeNode(TEXT("tmp:concat"), TEXT("string_concat"), 340, 200),
		};
		if (!TestEqual(TEXT("one result per operation"), Results.Num(), Expected.Num()))
		{
			return Fail();
		}
		for (int32 Index = 0; Index < Results.Num(); ++Index)
		{
			const TSharedPtr<FJsonObject> Result = Results[Index]->AsObject();
			const FString NodeGuid = Result->GetStringField(TEXT("nodeGuid"));
			UEdGraphNode* Node = FindNodeByGuid(Graph, NodeGuid);
			if (!TestEqual(
					*FString::Printf(TEXT("operation %d status"), Index),
					Result->GetStringField(TEXT("status")),
					FString(TEXT("applied")))
				|| !TestFalse(
					*FString::Printf(TEXT("operation %d not rolled back"), Index),
					Result->GetBoolField(TEXT("rolledBack")))
				|| !TestEqual(
					*FString::Printf(TEXT("operation %d nodeType"), Index),
					Result->GetStringField(TEXT("nodeType")),
					Expected[Index]->GetStringField(TEXT("nodeType")))
				|| !TestNotNull(
					*FString::Printf(
						TEXT("operation %d nodeGuid names a live node"),
						Index),
					Node))
			{
				return Fail();
			}
			if (!TestEqual(
					*FString::Printf(TEXT("operation %d position x"), Index),
					Node->NodePosX,
					static_cast<int32>(Expected[Index]->GetNumberField(TEXT("x"))))
				|| !TestEqual(
					*FString::Printf(TEXT("operation %d position y"), Index),
					Node->NodePosY,
					static_cast<int32>(Expected[Index]->GetNumberField(TEXT("y")))))
			{
				return Fail();
			}
			// Field-for-field equivalence with the single-writer node: class,
			// position, function and the whole visible pin inventory.
			const FString WriterContract = WriterLiveContracts[Index];
			if (!TestEqual(
					*FString::Printf(
						TEXT("live node contract %d matches the single writer"),
						Index),
					CanonicalNodeContract(Node),
					WriterContract))
			{
				AddError(CanonicalNodeContract(Node) + TEXT(" vs ") + WriterContract);
				return Fail();
			}
		}
	}

	// ---- One patch = one Undo/Redo unit, proven on the node fingerprint. ----
	{
		const FString NodesFingerprintAfter =
			UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicNodeStateFingerprint(Graph);
		const bool bUndoCalled = GEditor != nullptr && GEditor->UndoTransaction();
		if (!TestTrue(TEXT("the createNode patch can be undone once"), bUndoCalled)
			|| !TestEqual(
				TEXT("Undo removes exactly the four created nodes"),
				Graph->Nodes.Num(),
				NodeCountBefore)
			|| !TestEqual(
				TEXT("the node fingerprint returns to before after Undo"),
				UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicNodeStateFingerprint(Graph),
				NodesFingerprintBefore))
		{
			return Fail();
		}
		const bool bRedoCalled = GEditor != nullptr && GEditor->RedoTransaction();
		if (!TestTrue(TEXT("the createNode patch can be redone"), bRedoCalled)
			|| !TestEqual(
				TEXT("Redo restores exactly the four created nodes"),
				Graph->Nodes.Num(),
				NodeCountBefore + 4)
			|| !TestEqual(
				TEXT("the node fingerprint returns to after after Redo"),
				UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicNodeStateFingerprint(Graph),
				NodesFingerprintAfter))
		{
			return Fail();
		}
	}

	// ---- A dirty package refuses the next patch; Save is the only disk write. ----
	{
		TSharedPtr<FJsonObject> DirtyResult;
		FString DirtyCode;
		FString DirtyMessage;
		if (!TestFalse(
				TEXT("a second createNode patch is refused on a dirty package"),
				UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
					BuildPatchParams(
						Blueprint,
						Graph,
						{ MakeNodeOperation(TEXT("n5"), TEXT("bool_or"), 680, 0) }),
					DirtyResult,
					DirtyCode,
					DirtyMessage))
			|| !TestEqual(
				TEXT("dirty package code"),
				DirtyCode,
				FString(TEXT("graph-patch-package-dirty"))))
		{
			return Fail();
		}

		TSharedPtr<FJsonObject> SaveParams = MakeShared<FJsonObject>();
		SaveParams->SetStringField(TEXT("assetPath"), ApplyResult->GetStringField(TEXT("assetPath")));
		SaveParams->SetStringField(TEXT("graphGuid"), ApplyResult->GetStringField(TEXT("graphGuid")));
		SaveParams->SetStringField(TEXT("planId"), ApplyResult->GetStringField(TEXT("planId")));
		SaveParams->SetStringField(
			TEXT("expectedGraphFingerprint"),
			ApplyResult->GetStringField(TEXT("graphFingerprintAfter")));
		SaveParams->SetStringField(
			TEXT("expectedStateFingerprint"),
			ApplyResult->GetStringField(TEXT("stateFingerprintAfter")));
		SaveParams->SetNumberField(
			TEXT("expectedNodeCount"),
			ApplyResult->GetNumberField(TEXT("nodeCountAfter")));
		SaveParams->SetNumberField(
			TEXT("expectedLinkCount"),
			ApplyResult->GetNumberField(TEXT("linkCountAfter")));
		SaveParams->SetStringField(
			TEXT("confirmation"),
			ApplyResult->GetStringField(TEXT("requiredSaveConfirmation")));
		TSharedPtr<FJsonObject> SaveResult;
		FString SaveCode;
		FString SaveMessage;
		if (!TestTrue(
				TEXT("the explicit createNode patch save succeeds"),
				UEAgentKitBlueprintPatchExperimental::TrySaveExperimental(
					SaveParams,
					SaveResult,
					SaveCode,
					SaveMessage))
			|| !TestTrue(TEXT("the save result exists"), SaveResult.IsValid()))
		{
			AddError(SaveCode + TEXT(" ") + SaveMessage);
			return Fail();
		}
		const FString AssetShaAfterSave = ComputeAssetSha(Blueprint);
		if (!TestTrue(TEXT("save reports saved"), SaveResult->GetBoolField(TEXT("saved")))
			|| !TestFalse(TEXT("save cleared the dirty flag"), Package->IsDirty())
			|| !TestEqual(
				TEXT("save reported the node fingerprint of the applied state"),
				SaveResult->GetStringField(TEXT("nodesFingerprint")),
				ApplyResult->GetStringField(TEXT("nodesFingerprintAfter")))
			|| !TestEqual(
				TEXT("save reported the exact asset hash on disk"),
				SaveResult->GetStringField(TEXT("assetSha256")),
				AssetShaAfterSave)
			|| !TestFalse(
				TEXT("the only disk write changed the asset"),
				AssetShaAfterSave.Equals(AssetShaBefore, ESearchCase::CaseSensitive)))
		{
			return Fail();
		}

		// After the save the package is clean, so a fresh createNode preview must
		// see the saved state: same node fingerprint, same node count.
		TSharedPtr<FJsonObject> AfterSavePreview;
		FString AfterSaveCode;
		FString AfterSaveMessage;
		if (!TestTrue(
				TEXT("a createNode patch can be previewed again after the save"),
				UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
					BuildPatchParams(
						Blueprint,
						Graph,
						{
							MakeNodeOperation(TEXT("n5"), TEXT("bool_or"), 680, 0),
							MakeNodeOperation(TEXT("n6"), TEXT("string_concat"), 680, 200),
						}),
					AfterSavePreview,
					AfterSaveCode,
					AfterSaveMessage))
			|| !TestTrue(TEXT("the post-save preview exists"), AfterSavePreview.IsValid()))
		{
			AddError(AfterSaveCode + TEXT(" ") + AfterSaveMessage);
			return Fail();
		}
		if (!TestEqual(
				TEXT("the post-save preview sees the created node set"),
				AfterSavePreview->GetStringField(TEXT("nodesFingerprint")),
				ApplyResult->GetStringField(TEXT("nodesFingerprintAfter")))
			|| !TestEqual(
				TEXT("the post-save preview counts the created nodes"),
				AfterSavePreview->GetNumberField(TEXT("nodeCount")),
				static_cast<double>(NodeCountBefore + 4)))
		{
			return Fail();
		}
	}

	// ---- Mixed kinds: setPinDefault + createVariable + createNode. ----
	{
		UEdGraphPin* BoolPin = BoolNode != nullptr
			? BoolNode->FindPin(FName(TEXT("A")), EGPD_Input)
			: nullptr;
		if (!TestNotNull(TEXT("the fixture bool input pin exists"), BoolPin))
		{
			return Fail();
		}
		TSharedRef<FJsonObject> PinOperation = MakeShared<FJsonObject>();
		PinOperation->SetStringField(TEXT("opId"), TEXT("p1"));
		PinOperation->SetStringField(TEXT("kind"), TEXT("setPinDefault"));
		PinOperation->SetStringField(TEXT("nodeGuid"), GuidLower(BoolNode->NodeGuid));
		PinOperation->SetStringField(TEXT("pinName"), TEXT("A"));
		PinOperation->SetStringField(TEXT("pinGuid"), GuidLower(BoolPin->PinId));
		PinOperation->SetStringField(TEXT("type"), TEXT("bool"));
		PinOperation->SetStringField(TEXT("value"), TEXT("true"));

		TSharedRef<FJsonObject> VariableOperation = MakeShared<FJsonObject>();
		VariableOperation->SetStringField(TEXT("opId"), TEXT("v1"));
		VariableOperation->SetStringField(TEXT("kind"), TEXT("createVariable"));
		VariableOperation->SetStringField(TEXT("name"), TEXT("NodePatchFlag"));
		VariableOperation->SetStringField(TEXT("type"), TEXT("bool"));
		VariableOperation->SetStringField(TEXT("defaultValue"), TEXT("true"));

		const TArray<TSharedPtr<FJsonObject>> MixedOperations = {
			PinOperation,
			VariableOperation,
			MakeNodeOperation(TEXT("n1"), TEXT("branch"), 700, 0),
		};
		const int32 VariableCountBefore = Blueprint->NewVariables.Num();
		TSharedPtr<FJsonObject> MixedPreview;
		FString MixedCode;
		FString MixedMessage;
		if (!TestTrue(
				TEXT("the mixed-kind patch previews"),
				UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
					BuildPatchParams(Blueprint, Graph, MixedOperations),
					MixedPreview,
					MixedCode,
					MixedMessage))
			|| !TestTrue(TEXT("the mixed preview exists"), MixedPreview.IsValid()))
		{
			AddError(MixedCode + TEXT(" ") + MixedMessage);
			return Fail();
		}
		TSharedPtr<FJsonObject> MixedApply;
		if (!TestTrue(
				TEXT("the mixed-kind patch applies"),
				UEAgentKitBlueprintPatchExperimental::TryApplyExperimental(
					BuildPatchApplyParams(MixedPreview, MixedOperations),
					MixedApply,
					MixedCode,
					MixedMessage))
			|| !TestTrue(TEXT("the mixed apply result exists"), MixedApply.IsValid()))
		{
			AddError(MixedCode + TEXT(" ") + MixedMessage);
			return Fail();
		}
		const TArray<TSharedPtr<FJsonValue>>& MixedResults =
			MixedApply->GetArrayField(TEXT("results"));
		bool bAllApplied = MixedResults.Num() == 3;
		for (const TSharedPtr<FJsonValue>& Value : MixedResults)
		{
			bAllApplied = bAllApplied
				&& Value->AsObject()->GetStringField(TEXT("status")) == TEXT("applied");
		}
		bool bPinIsTrue = false;
		for (const UEdGraphPin* Pin : BoolNode->Pins)
		{
			if (Pin != nullptr
				&& Pin->Direction == EGPD_Input
				&& Pin->PinName == FName(TEXT("A")))
			{
				bPinIsTrue = Pin->DefaultValue == TEXT("true");
			}
		}
		if (!TestTrue(TEXT("all three mixed operations applied"), bAllApplied)
			|| !TestTrue(TEXT("the pin default landed"), bPinIsTrue)
			|| !TestEqual(
				TEXT("the variable was created"),
				Blueprint->NewVariables.Num(),
				VariableCountBefore + 1)
			|| !TestEqual(
				TEXT("the mixed patch created one node"),
				MixedApply->GetNumberField(TEXT("nodeCountAfter")),
				static_cast<double>(NodeCountBefore + 5))
			|| !TestTrue(
				TEXT("the mixed patch is one verified Undo unit"),
				MixedApply->GetBoolField(TEXT("undoVerified"))
					&& MixedApply->GetBoolField(TEXT("redoVerified")))
			|| !TestEqual(
				TEXT("the mixed patch created one nodeGuid"),
				MixedApply->GetArrayField(TEXT("results"))[2]->AsObject()->GetStringField(TEXT("nodeGuid")).Len(),
				36))
		{
			return Fail();
		}
		// Undo the mixed transaction so the saved asset stays the authoritative
		// on-disk state for this fixture.
		if (!TestTrue(
				TEXT("the mixed patch undoes as one unit"),
				GEditor != nullptr && GEditor->UndoTransaction())
			|| !TestEqual(
				TEXT("undoing the mixed patch removes its node"),
				Graph->Nodes.Num(),
				NodeCountBefore + 4)
			|| !TestEqual(
				TEXT("undoing the mixed patch removes its variable"),
				Blueprint->NewVariables.Num(),
				VariableCountBefore))
		{
			return Fail();
		}
	}

	UEAgentKitBlueprintPatchExperimental::ResetExperimentalWriteForTest();
	UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
	return true;
}

#endif // WITH_DEV_AUTOMATION_TESTS
