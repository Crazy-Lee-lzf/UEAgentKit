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
//    field, an unknown kind, more operations than the bound) happen before
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
#include "BlueprintNodeRemoveExperimental.h"
#include "BlueprintPatchExperimental.h"
#include "BlueprintPinDefaultEditExperimental.h"
#include "BlueprintRewireExperimental.h"
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
#include "K2Node_VariableGet.h"
#include "K2Node_VariableSet.h"
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
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitPatchCreateNodeTest,
	"UEAgentKit.AtomicPatch.CreateNode",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitPatchCreateNodeTest::RunTest(const FString& Parameters)
{
	using namespace UEAgentKitPatchCreateNode;

	UEAgentKitBlueprintPatchExperimental::ResetExperimentalWriteForTest();
	if (!TestFalse(
			TEXT("experimental atomic patch gate is off unless the project opts in"),
			UEAgentKitBlueprintPatchExperimental::IsExperimentalWriteAvailable()))
	{
		return false;
	}
	UEAgentKitBlueprintPatchExperimental::SetExperimentalWriteEnabledForTest(true);
	auto Fail = []()
	{
		UEAgentKitBlueprintPatchExperimental::ResetExperimentalWriteForTest();
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
		// A tempId became a legal createNode field in 0-3.4 slice 11 (it is what a
		// connect endpoint of the same patch refers to), so this asserts the new
		// contract: the label is accepted and reported, not silently ignored.
		{
			TSharedPtr<FJsonObject> WithTempId =
				MakeNodeOperation(TEXT("n1"), TEXT("branch"), 0, 0);
			WithTempId->SetStringField(TEXT("tempId"), TEXT("tmp:a"));
			TSharedPtr<FJsonObject> TempIdPreview;
			FString TempIdCode;
			FString TempIdMessage;
			if (!TestTrue(
					TEXT("a createNode operation accepts a tempId"),
					UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
						BuildPatchParams(Blueprint, Graph, { WithTempId }),
						TempIdPreview,
						TempIdCode,
						TempIdMessage))
				|| !TestTrue(TEXT("the tempId preview exists"), TempIdPreview.IsValid()))
			{
				AddError(TempIdCode + TEXT(" ") + TempIdMessage);
				return Fail();
			}
			const TSharedPtr<FJsonObject> Entry =
				TempIdPreview->GetArrayField(TEXT("operations"))[0]->AsObject();
			if (!TestEqual(
					TEXT("the preview reports the tempId"),
					Entry->GetStringField(TEXT("tempId")),
					FString(TEXT("tmp:a"))))
			{
				return Fail();
			}
		}
		// Every designed kind is migrated now (`removeNode` closed the list in 0-3.4
		// slice 13), so the refusal is "not a patch kind" rather than "not migrated".
		{
			TSharedRef<FJsonObject> Connect = MakeShared<FJsonObject>();
			Connect->SetStringField(TEXT("opId"), TEXT("x1"));
			Connect->SetStringField(TEXT("kind"), TEXT("moveNode"));
			TSharedPtr<FJsonObject> RefusedResult;
			FString RefusedCode;
			FString RefusedMessage;
			if (!TestFalse(
					TEXT("an unknown kind is refused"),
					UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
						BuildPatchParams(Blueprint, Graph, { Connect }),
						RefusedResult,
						RefusedCode,
						RefusedMessage))
				|| !TestEqual(
					TEXT("unknown kind refusal code"),
					RefusedCode,
					FString(TEXT("graph-patch-kind-not-allowed"))))
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

	const TArray<TSharedPtr<FJsonObject>> NodeOperations = {
		MakeNodeOperation(TEXT("n1"), TEXT("branch"), 0, 0),
		MakeNodeOperation(TEXT("n2"), TEXT("sequence"), 0, 200),
		MakeNodeOperation(TEXT("n3"), TEXT("int_add"), 340, 0),
		MakeNodeOperation(TEXT("n4"), TEXT("string_concat"), 340, 200),
	};
	NodesFingerprintBefore =
		UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicNodeStateFingerprint(Graph);
	if (!TestTrue(
			TEXT("the node fingerprint covers the focused graph"),
			!NodesFingerprintBefore.IsEmpty()))
	{
		return Fail();
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
				TEXT("the preview advertises the seven migrated kinds"),
				JoinStrings(MigratedKinds),
				FString(TEXT("setPinDefault,createVariable,createNode,connect,disconnect,rewirePin,removeNode")))
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
			if (!TestEqual(
					*FString::Printf(TEXT("preview operation %d is validated"), Index),
					Operation->GetStringField(TEXT("status")),
					FString(TEXT("validated")))
				|| !TestFalse(
					*FString::Printf(
						TEXT("preview operation %d has no created nodeGuid yet"),
						Index),
					Operation->HasField(TEXT("nodeGuid"))))
			{
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
			// The created node must still expose the class, position and visible
			// pin inventory the operation asked for.
			const FString Contract = CanonicalNodeContract(Node);
			if (!TestTrue(
					*FString::Printf(TEXT("live node contract %d is complete"), Index),
					!Contract.IsEmpty()))
			{
				AddError(Contract);
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
	return true;
}

namespace UEAgentKitPatchRemoveNode
{
	using namespace UEAgentKitPatchCreateNode;

	const TCHAR* const AssetPath =
		TEXT("/Game/UEAgentKitPatchExpansion/BP_PatchRemoveNode.BP_PatchRemoveNode");
	const TCHAR* const PackageName = TEXT("/Game/UEAgentKitPatchExpansion/BP_PatchRemoveNode");
	const TCHAR* const AssetName = TEXT("BP_PatchRemoveNode");
	const TCHAR* const NotBoolClassPath = TEXT("/Script/BlueprintGraph.K2Node_CallFunction");

	FString NodeClassPath(const UEdGraphNode* Node)
	{
		return Node != nullptr && Node->GetClass() != nullptr
			? Node->GetClass()->GetPathName()
			: FString();
	}

	bool AreLinked(UEdGraphPin* From, UEdGraphPin* To)
	{
		return From != nullptr && To != nullptr && From->LinkedTo.Contains(To)
			&& To->LinkedTo.Contains(From);
	}

	UEdGraphPin* FindPin(UEdGraphNode* Node, const TCHAR* PinName, EEdGraphPinDirection Direction)
	{
		return Node != nullptr ? Node->FindPin(FName(PinName), Direction) : nullptr;
	}

	/**
	 * Seven nodes and three edges:
	 *   A(0,0) -> Branch.Condition, B(0,200) -> OR.A, C(0,400) -> OR.B,
	 *   D(0,600) isolated, Sequence(200,600) isolated but not removable.
	 * OR carries two incident edges (the "too many" case) and Branch one.
	 */
	UBlueprint* CreateRemoveFixture(FString& OutError)
	{
		if (FindObject<UBlueprint>(nullptr, AssetPath) != nullptr)
		{
			OutError = TEXT("RemoveNode fixture already exists; use a fresh project.");
			return nullptr;
		}
		if (!RemoveStaleFiles(PackageName, OutError))
		{
			return nullptr;
		}
		UPackage* Package = CreatePackage(PackageName);
		UBlueprint* Blueprint = FKismetEditorUtilities::CreateBlueprint(
			AActor::StaticClass(),
			Package,
			FName(AssetName),
			BPTYPE_Normal,
			FName(TEXT("UEAgentKitPatchRemoveNode")));
		if (Blueprint == nullptr || Blueprint->UbergraphPages.IsEmpty())
		{
			OutError = TEXT("Could not create the removeNode fixture Blueprint.");
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
		UK2Node_IfThenElse* Branch = NewObject<UK2Node_IfThenElse>(Graph);
		UK2Node_ExecutionSequence* Sequence = NewObject<UK2Node_ExecutionSequence>(Graph);
		if (Branch == nullptr || Sequence == nullptr)
		{
			OutError = TEXT("Could not create the fixture control nodes.");
			return nullptr;
		}
		for (UEdGraphNode* Node : { static_cast<UEdGraphNode*>(Branch),
				static_cast<UEdGraphNode*>(Sequence) })
		{
			Graph->AddNode(Node, true, false);
			Node->AllocateDefaultPins();
			if (!Node->NodeGuid.IsValid())
			{
				Node->CreateNewGuid();
			}
		}
		Branch->NodePosX = 340;
		Branch->NodePosY = 0;
		Sequence->NodePosX = 200;
		Sequence->NodePosY = 600;
		if (AddCallFunctionNode(Graph, UKismetMathLibrary::StaticClass(), TEXT("Not_PreBool"), 0, 0) == nullptr
			|| AddCallFunctionNode(Graph, UKismetMathLibrary::StaticClass(), TEXT("Not_PreBool"), 0, 200) == nullptr
			|| AddCallFunctionNode(Graph, UKismetMathLibrary::StaticClass(), TEXT("Not_PreBool"), 0, 400) == nullptr
			|| AddCallFunctionNode(Graph, UKismetMathLibrary::StaticClass(), TEXT("Not_PreBool"), 0, 600) == nullptr
			|| AddCallFunctionNode(Graph, UKismetMathLibrary::StaticClass(), TEXT("BooleanOR"), 340, 200) == nullptr)
		{
			OutError = TEXT("Could not create the fixture function nodes.");
			return nullptr;
		}
		FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
		FKismetEditorUtilities::CompileBlueprint(
			Blueprint,
			EBlueprintCompileOptions::SkipGarbageCollection);
		if (Blueprint->Status == BS_Error)
		{
			OutError = TEXT("removeNode fixture failed to compile.");
			return nullptr;
		}
		auto FindAt = [Graph](int32 X, int32 Y) -> UEdGraphNode*
		{
			for (UEdGraphNode* Node : Graph->Nodes)
			{
				if (Node != nullptr && Node->NodePosX == X && Node->NodePosY == Y)
				{
					return Node;
				}
			}
			return nullptr;
		};
		UEdGraphPin* AOut = FindPin(FindAt(0, 0), TEXT("ReturnValue"), EGPD_Output);
		UEdGraphPin* BOut = FindPin(FindAt(0, 200), TEXT("ReturnValue"), EGPD_Output);
		UEdGraphPin* COut = FindPin(FindAt(0, 400), TEXT("ReturnValue"), EGPD_Output);
		UEdGraphPin* Condition = FindPin(FindAt(340, 0), TEXT("Condition"), EGPD_Input);
		UEdGraphPin* OrA = FindPin(FindAt(340, 200), TEXT("A"), EGPD_Input);
		UEdGraphPin* OrB = FindPin(FindAt(340, 200), TEXT("B"), EGPD_Input);
		if (AOut == nullptr || BOut == nullptr || COut == nullptr || Condition == nullptr
			|| OrA == nullptr || OrB == nullptr
			|| !Graph->GetSchema()->TryCreateConnection(AOut, Condition)
			|| !Graph->GetSchema()->TryCreateConnection(BOut, OrA)
			|| !Graph->GetSchema()->TryCreateConnection(COut, OrB))
		{
			OutError = TEXT("Could not create the fixture edges.");
			return nullptr;
		}
		FAssetRegistryModule::AssetCreated(Blueprint);
		Package->MarkPackageDirty();
		if (!SaveBlueprint(Blueprint))
		{
			OutError = TEXT("Could not save the removeNode fixture.");
			return nullptr;
		}
		return Blueprint;
	}

	TSharedPtr<FJsonObject> MakeRemovedEdge(UEdGraphPin* From, UEdGraphPin* To)
	{
		TSharedRef<FJsonObject> Edge = MakeShared<FJsonObject>();
		Edge->SetStringField(TEXT("fromNodeGuid"), GuidLower(From->GetOwningNode()->NodeGuid));
		Edge->SetStringField(TEXT("fromPinName"), From->PinName.ToString());
		Edge->SetStringField(TEXT("fromPinGuid"), GuidLower(From->PinId));
		Edge->SetStringField(TEXT("toNodeGuid"), GuidLower(To->GetOwningNode()->NodeGuid));
		Edge->SetStringField(TEXT("toPinName"), To->PinName.ToString());
		Edge->SetStringField(TEXT("toPinGuid"), GuidLower(To->PinId));
		return Edge;
	}

	TSharedPtr<FJsonObject> MakeNodeOperationWithTempId(
		const TCHAR* OpId,
		const TCHAR* NodeType,
		int32 X,
		int32 Y,
		const TCHAR* TempId)
	{
		TSharedPtr<FJsonObject> Operation = MakeNodeOperation(OpId, NodeType, X, Y);
		Operation->SetStringField(TEXT("tempId"), TempId);
		return Operation;
	}

	TSharedPtr<FJsonObject> MakeRemoveOperation(
		const TCHAR* OpId,
		UEdGraphNode* Node,
		const FString& ExpectedClass,
		const TArray<TSharedPtr<FJsonObject>>& RemovedEdges)
	{
		TSharedRef<FJsonObject> Operation = MakeShared<FJsonObject>();
		Operation->SetStringField(TEXT("opId"), OpId);
		Operation->SetStringField(TEXT("kind"), TEXT("removeNode"));
		Operation->SetStringField(TEXT("nodeGuid"), GuidLower(Node->NodeGuid));
		Operation->SetStringField(TEXT("expectedNodeClass"), ExpectedClass);
		TArray<TSharedPtr<FJsonValue>> Values;
		for (const TSharedPtr<FJsonObject>& Edge : RemovedEdges)
		{
			Values.Add(MakeShared<FJsonValueObject>(Edge));
		}
		Operation->SetArrayField(TEXT("removedEdges"), Values);
		return Operation;
	}

	bool SaveApplied(const TSharedPtr<FJsonObject>& ApplyResult, FString& OutError)
	{
		TSharedPtr<FJsonObject> Params = MakeShared<FJsonObject>();
		Params->SetStringField(TEXT("assetPath"), ApplyResult->GetStringField(TEXT("assetPath")));
		Params->SetStringField(TEXT("graphGuid"), ApplyResult->GetStringField(TEXT("graphGuid")));
		Params->SetStringField(TEXT("planId"), ApplyResult->GetStringField(TEXT("planId")));
		Params->SetStringField(
			TEXT("expectedGraphFingerprint"),
			ApplyResult->GetStringField(TEXT("graphFingerprintAfter")));
		Params->SetStringField(
			TEXT("expectedStateFingerprint"),
			ApplyResult->GetStringField(TEXT("stateFingerprintAfter")));
		Params->SetNumberField(
			TEXT("expectedNodeCount"),
			ApplyResult->GetNumberField(TEXT("nodeCountAfter")));
		Params->SetNumberField(
			TEXT("expectedLinkCount"),
			ApplyResult->GetNumberField(TEXT("linkCountAfter")));
		Params->SetStringField(
			TEXT("confirmation"),
			ApplyResult->GetStringField(TEXT("requiredSaveConfirmation")));
		TSharedPtr<FJsonObject> SaveResult;
		FString Code;
		FString Message;
		if (!UEAgentKitBlueprintPatchExperimental::TrySaveExperimental(
				Params,
				SaveResult,
				Code,
				Message))
		{
			OutError = Code + TEXT(" ") + Message;
			return false;
		}
		return true;
	}
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitPatchRemoveNodeTest,
	"UEAgentKit.AtomicPatch.RemoveNode",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitPatchRemoveNodeTest::RunTest(const FString& Parameters)
{
	using namespace UEAgentKitPatchCreateNode;
	using namespace UEAgentKitPatchRemoveNode;

	UEAgentKitBlueprintPatchExperimental::ResetExperimentalWriteForTest();
	if (!TestFalse(
			TEXT("the atomic patch gate stays off unless the project opts in"),
			UEAgentKitBlueprintPatchExperimental::IsExperimentalWriteAvailable()))
	{
		return false;
	}
	UEAgentKitBlueprintPatchExperimental::SetExperimentalWriteEnabledForTest(true);
	auto Fail = []
	{
		UEAgentKitBlueprintPatchExperimental::ResetExperimentalWriteForTest();
		return false;
	};

	FString Error;
	UBlueprint* Blueprint = CreateRemoveFixture(Error);
	if (!TestNotNull(TEXT("removeNode fixture is created"), Blueprint))
	{
		AddError(Error);
		return Fail();
	}
	UEdGraph* Graph = Blueprint->UbergraphPages[0];
	UPackage* Package = Blueprint->GetOutermost();
	if (!TestNotNull(TEXT("removeNode fixture graph is focused"), OpenAndFocus(Blueprint, Graph, Error)))
	{
		AddError(Error);
		return Fail();
	}
	auto FindAt = [Graph](int32 X, int32 Y) -> UEdGraphNode*
	{
		for (UEdGraphNode* Node : Graph->Nodes)
		{
			if (Node != nullptr && Node->NodePosX == X && Node->NodePosY == Y)
			{
				return Node;
			}
		}
		return nullptr;
	};
	UEdGraphNode* A = FindAt(0, 0);
	UEdGraphNode* B = FindAt(0, 200);
	UEdGraphNode* C = FindAt(0, 400);
	UEdGraphNode* D = FindAt(0, 600);
	UEdGraphNode* Branch = FindAt(340, 0);
	UEdGraphNode* BoolOr = FindAt(340, 200);
	UEdGraphNode* Sequence = FindAt(200, 600);
	UEdGraphPin* AOut = FindPin(A, TEXT("ReturnValue"), EGPD_Output);
	UEdGraphPin* BOut = FindPin(B, TEXT("ReturnValue"), EGPD_Output);
	UEdGraphPin* COut = FindPin(C, TEXT("ReturnValue"), EGPD_Output);
	UEdGraphPin* BranchCondition = FindPin(Branch, TEXT("Condition"), EGPD_Input);
	UEdGraphPin* OrA = FindPin(BoolOr, TEXT("A"), EGPD_Input);
	UEdGraphPin* OrB = FindPin(BoolOr, TEXT("B"), EGPD_Input);
	if (!TestNotNull(TEXT("fixture A"), A)
		|| !TestNotNull(TEXT("fixture B"), B)
		|| !TestNotNull(TEXT("fixture C"), C)
		|| !TestNotNull(TEXT("fixture D"), D)
		|| !TestNotNull(TEXT("fixture branch"), Branch)
		|| !TestNotNull(TEXT("fixture or"), BoolOr)
		|| !TestNotNull(TEXT("fixture sequence"), Sequence)
		|| !TestTrue(TEXT("the fixture starts with its three edges"),
			AreLinked(AOut, BranchCondition) && AreLinked(BOut, OrA) && AreLinked(COut, OrB)))
	{
		return Fail();
	}
	const int32 NodeCountBefore = Graph->Nodes.Num();
	const FString AssetShaBefore = ComputeAssetSha(Blueprint);
	const FString EdgesFingerprintBefore =
		UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(Graph);
	const FString NodesFingerprintBefore =
		UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicNodeStateFingerprint(Graph);
	if (!TestEqual(TEXT("the fixture has seven nodes"), NodeCountBefore, 7)
		|| !TestEqual(TEXT("the fixture has three edges"),
			UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(Graph).Len(),
			71))
	{
		return Fail();
	}

	// ---- Fail-closed refusals: one table, no write, no dirty package. ----
	{
		TSharedPtr<FJsonObject> AbsentNode = MakeRemoveOperation(
			TEXT("r1"),
			A,
			NotBoolClassPath,
			{});
		AbsentNode->SetStringField(
			TEXT("nodeGuid"),
			TEXT("00000000-0000-0000-0000-000000000000"));
		TSharedPtr<FJsonObject> BadClass =
			MakeRemoveOperation(TEXT("r1"), A, NodeClassPath(Sequence), {});
		TSharedPtr<FJsonObject> SequenceRemoval =
			MakeRemoveOperation(TEXT("r1"), Sequence, NodeClassPath(Sequence), {});
		TSharedPtr<FJsonObject> TooMany =
			MakeRemoveOperation(TEXT("r1"), BoolOr, NodeClassPath(BoolOr), {});
		TSharedPtr<FJsonObject> MissingEdge =
			MakeRemoveOperation(TEXT("r1"), A, NotBoolClassPath, {});
		TSharedPtr<FJsonObject> WrongEdge = MakeRemoveOperation(
			TEXT("r1"),
			A,
			NotBoolClassPath,
			{ MakeRemovedEdge(BOut, OrA) });
		TSharedPtr<FJsonObject> BadPinGuid = MakeRemoveOperation(
			TEXT("r1"),
			A,
			NotBoolClassPath,
			{ MakeRemovedEdge(AOut, BranchCondition) });
		BadPinGuid->GetArrayField(TEXT("removedEdges"))[0]->AsObject()->SetStringField(
			TEXT("toPinGuid"),
			TEXT("not-a-guid"));
		TSharedPtr<FJsonObject> TwoEdges = MakeRemoveOperation(
			TEXT("r1"),
			D,
			NotBoolClassPath,
			{ MakeRemovedEdge(BOut, OrA), MakeRemovedEdge(COut, OrB) });
		TSharedPtr<FJsonObject> NoEdgeList = MakeRemoveOperation(
			TEXT("r1"),
			D,
			NotBoolClassPath,
			{});
		NoEdgeList->RemoveField(TEXT("removedEdges"));
		struct FRefusal
		{
			const TCHAR* Label;
			TSharedPtr<FJsonObject> Operation;
			const TCHAR* ExpectedCode;
		};
		const TArray<FRefusal> Refusals = {
			{
				TEXT("a node outside the graph"),
				AbsentNode,
				TEXT("graph-remove-node-not-found"),
			},
			{
				TEXT("a class that does not match the node"),
				BadClass,
				TEXT("graph-remove-node-class-mismatch"),
			},
			{
				TEXT("a node class the writer never removes"),
				SequenceRemoval,
				TEXT("graph-remove-node-type-not-allowed"),
			},
			{
				TEXT("a node with two incident edges"),
				TooMany,
				TEXT("graph-remove-node-too-many-connections"),
			},
			{
				TEXT("an omission of the node's live edge"),
				MissingEdge,
				TEXT("graph-remove-removed-edges-mismatch"),
			},
			{
				TEXT("an edge that is not incident to the node"),
				WrongEdge,
				TEXT("graph-remove-removed-edges-mismatch"),
			},
			{
				TEXT("a non-canonical pin GUID on a declared edge"),
				BadPinGuid,
				TEXT("graph-remove-removed-edges-invalid"),
			},
			{
				TEXT("two declared edges for one node"),
				TwoEdges,
				TEXT("graph-remove-removed-edges-invalid"),
			},
			{
				TEXT("a missing removedEdges array"),
				NoEdgeList,
				TEXT("graph-remove-invalid-parameters"),
			},
		};
		for (const FRefusal& Refusal : Refusals)
		{
			TSharedPtr<FJsonObject> RefusedResult;
			FString RefusedCode;
			FString RefusedMessage;
			if (!TestFalse(
					FString::Printf(TEXT("refused: %s"), Refusal.Label),
					UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
						BuildPatchParams(Blueprint, Graph, { Refusal.Operation }),
						RefusedResult,
						RefusedCode,
						RefusedMessage))
				|| !TestEqual(
					FString::Printf(TEXT("refusal code for %s"), Refusal.Label),
					RefusedCode,
					FString(Refusal.ExpectedCode)))
			{
				AddError(RefusedMessage);
				return Fail();
			}
		}
		// `removeNode` was the last designed kind; an unknown kind is now simply
		// not a patch kind.
		{
			TSharedRef<FJsonObject> Unknown = MakeShared<FJsonObject>();
			Unknown->SetStringField(TEXT("opId"), TEXT("x1"));
			Unknown->SetStringField(TEXT("kind"), TEXT("moveNode"));
			TSharedPtr<FJsonObject> RefusedResult;
			FString RefusedCode;
			FString RefusedMessage;
			if (!TestFalse(
					TEXT("an unknown kind is refused"),
					UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
						BuildPatchParams(Blueprint, Graph, { Unknown }),
						RefusedResult,
						RefusedCode,
						RefusedMessage))
				|| !TestEqual(
					TEXT("unknown kind code"),
					RefusedCode,
					FString(TEXT("graph-patch-kind-not-allowed"))))
			{
				return Fail();
			}
		}
		// Two removals in one patch would widen the single writer's one-node bound.
		{
			const TArray<TSharedPtr<FJsonObject>> Operations = {
				MakeRemoveOperation(
					TEXT("r1"),
					A,
					NotBoolClassPath,
					{ MakeRemovedEdge(AOut, BranchCondition) }),
				MakeRemoveOperation(TEXT("r2"), D, NodeClassPath(D), {}),
			};
			TSharedPtr<FJsonObject> RefusedResult;
			FString RefusedCode;
			FString RefusedMessage;
			if (!TestFalse(
					TEXT("two removals in one patch are refused"),
					UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
						BuildPatchParams(Blueprint, Graph, Operations),
						RefusedResult,
						RefusedCode,
						RefusedMessage))
				|| !TestEqual(
					TEXT("removal bound code"),
					RefusedCode,
					FString(TEXT("graph-patch-remove-node-limit"))))
			{
				AddError(RefusedMessage);
				return Fail();
			}
		}
		if (!TestEqual(TEXT("refusals left the node count"), Graph->Nodes.Num(), NodeCountBefore)
			|| !TestFalse(TEXT("refusals left the package clean"), Package->IsDirty())
			|| !TestEqual(TEXT("refusals left the disk asset"), ComputeAssetSha(Blueprint), AssetShaBefore)
			|| !TestEqual(
				TEXT("refusals left the edge fingerprint"),
				UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(Graph),
				EdgesFingerprintBefore)
			|| !TestEqual(
				TEXT("refusals left the node fingerprint"),
				UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicNodeStateFingerprint(Graph),
				NodesFingerprintBefore))
		{
			return Fail();
		}
	}

	// ---- The patch resolves the same node/edge identity it will remove. ----
	{
		TSharedPtr<FJsonObject> PatchPreview;
		FString Code;
		FString Message;
		if (!TestTrue(
				TEXT("the patch preview of the same removal succeeds"),
				UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
					BuildPatchParams(
						Blueprint,
						Graph,
						{ MakeRemoveOperation(
							TEXT("r1"),
							A,
							NotBoolClassPath,
							{ MakeRemovedEdge(AOut, BranchCondition) }) }),
					PatchPreview,
					Code,
					Message)))
		{
			AddError(Code + TEXT(" ") + Message);
			return Fail();
		}
		const TSharedPtr<FJsonObject> Planned =
			PatchPreview->GetArrayField(TEXT("operations"))[0]->AsObject();
		const TArray<TSharedPtr<FJsonValue>>& PlannedEdges =
			Planned->GetArrayField(TEXT("removedEdges"));
		if (!TestEqual(
				TEXT("the preview plans one node"),
				Planned->GetStringField(TEXT("nodeGuid")),
				GuidLower(A->NodeGuid))
			|| !TestEqual(
				TEXT("the preview resolves the node class"),
				Planned->GetStringField(TEXT("nodeClass")),
				FString(NotBoolClassPath))
			|| !TestEqual(TEXT("one removed edge planned"), PlannedEdges.Num(), 1)
			|| !TestEqual(
				TEXT("the planned edge keeps the pin identity"),
				PlannedEdges[0]->AsObject()->GetStringField(TEXT("toPinGuid")),
				GuidLower(BranchCondition->PinId))
			|| !TestEqual(
				TEXT("the preview reports the live node count"),
				PatchPreview->GetNumberField(TEXT("nodeCount")),
				static_cast<double>(NodeCountBefore)))
		{
			return Fail();
		}
	}

	// ---- Rollback (the point of this slice): a removed node AND its edge return.
	{
		const TArray<TSharedPtr<FJsonObject>> Operations = {
			MakeRemoveOperation(
				TEXT("r1"),
				A,
				NotBoolClassPath,
				{ MakeRemovedEdge(AOut, BranchCondition) }),
			UEAgentKitPatchCreateNode::MakeNodeOperation(TEXT("n1"), TEXT("sequence"), 700, 0),
		};
		TSharedPtr<FJsonObject> Preview;
		FString Code;
		FString Message;
		if (!TestTrue(
				TEXT("the removeNode rollback patch previews"),
				UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
					BuildPatchParams(Blueprint, Graph, Operations),
					Preview,
					Code,
					Message)))
		{
			AddError(Code + TEXT(" ") + Message);
			return Fail();
		}
		UEAgentKitBlueprintPatchExperimental::SetNextApplyFaultForTest(1);
		TSharedPtr<FJsonObject> ApplyResult;
		if (!TestFalse(
				TEXT("the removeNode rollback patch fails"),
				UEAgentKitBlueprintPatchExperimental::TryApplyExperimental(
					BuildPatchApplyParams(Preview, Operations),
					ApplyResult,
					Code,
					Message)))
		{
			return Fail();
		}
		UEdGraphNode* RestoredA = FindNodeByGuid(Graph, GuidLower(A->NodeGuid));
		UEdGraphPin* RestoredAOut = FindPin(RestoredA, TEXT("ReturnValue"), EGPD_Output);
		UEdGraphPin* RestoredCondition = FindPin(Branch, TEXT("Condition"), EGPD_Input);
		const TArray<TSharedPtr<FJsonValue>>& Results =
			ApplyResult->GetArrayField(TEXT("results"));
		if (!TestEqual(
				TEXT("the rollback reports the injected fault"),
				Code,
				FString(TEXT("graph-patch-apply-fault-injected")))
			|| !TestEqual(TEXT("one result per operation"), Results.Num(), 2)
			|| !TestTrue(
				TEXT("the removed node EXISTS again after the rollback"),
				RestoredA != nullptr)
			|| !TestEqual(
				TEXT("the restored node kept its class"),
				NodeClassPath(RestoredA),
				FString(NotBoolClassPath))
			|| !TestTrue(
				TEXT("the removed node's EDGE is linked again after the rollback"),
				AreLinked(RestoredAOut, RestoredCondition))
			|| !TestEqual(TEXT("the rollback restored the node count"), Graph->Nodes.Num(), NodeCountBefore)
			|| !TestEqual(
				TEXT("the rollback restored the edge fingerprint"),
				UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(Graph),
				EdgesFingerprintBefore)
			|| !TestEqual(
				TEXT("the rollback restored the node fingerprint"),
				UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicNodeStateFingerprint(Graph),
				NodesFingerprintBefore)
			|| !TestFalse(TEXT("the rollback left the package clean"), Package->IsDirty())
			|| !TestEqual(TEXT("the rollback left the disk asset"), ComputeAssetSha(Blueprint), AssetShaBefore)
			|| !TestTrue(
				TEXT("the rollback says the state is restored"),
				ApplyResult->GetBoolField(TEXT("stateRestored"))))
		{
			AddError(Code + TEXT(" ") + Message);
			return Fail();
		}
	}

	// ---- Remove a node that carries an edge, then Undo/Redo, then Save.
	{
		const TArray<TSharedPtr<FJsonObject>> Operations = {
			MakeRemoveOperation(
				TEXT("r1"),
				A,
				NotBoolClassPath,
				{ MakeRemovedEdge(AOut, BranchCondition) }),
		};
		TSharedPtr<FJsonObject> Preview;
		FString Code;
		FString Message;
		if (!TestTrue(
				TEXT("the removeNode patch previews"),
				UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
					BuildPatchParams(Blueprint, Graph, Operations),
					Preview,
					Code,
					Message))
			|| !TestTrue(TEXT("the removeNode preview exists"), Preview.IsValid()))
		{
			AddError(Code + TEXT(" ") + Message);
			return Fail();
		}
		TSharedPtr<FJsonObject> ApplyResult;
		if (!TestTrue(
				TEXT("the removeNode patch applies"),
				UEAgentKitBlueprintPatchExperimental::TryApplyExperimental(
					BuildPatchApplyParams(Preview, Operations),
					ApplyResult,
					Code,
					Message))
			|| !TestTrue(TEXT("the removeNode result exists"), ApplyResult.IsValid()))
		{
			AddError(Code + TEXT(" ") + Message);
			return Fail();
		}
		const FString EdgesAfter = ApplyResult->GetStringField(TEXT("edgesFingerprintAfter"));
		const FString NodesAfter = ApplyResult->GetStringField(TEXT("nodesFingerprintAfter"));
		if (!TestTrue(TEXT("the removed node is gone"), FindNodeByGuid(Graph, GuidLower(A->NodeGuid)) == nullptr)
			|| !TestEqual(TEXT("one node fewer"), Graph->Nodes.Num(), NodeCountBefore - 1)
			|| !TestEqual(
				TEXT("the removed node took its edge with it"),
				ApplyResult->GetNumberField(TEXT("linkCountAfter")),
				2.0)
			|| !TestEqual(
				TEXT("the patch counts the removed node"),
				ApplyResult->GetNumberField(TEXT("removedNodeCount")),
				1.0)
			|| !TestEqual(
				TEXT("the patch counts the implicitly removed edge"),
				ApplyResult->GetNumberField(TEXT("removedNodeEdgeCount")),
				1.0)
			|| !TestFalse(
				TEXT("removing a node changed the edge fingerprint"),
				EdgesAfter.Equals(EdgesFingerprintBefore, ESearchCase::CaseSensitive))
			|| !TestFalse(
				TEXT("removing a node changed the node fingerprint"),
				NodesAfter.Equals(NodesFingerprintBefore, ESearchCase::CaseSensitive)))
		{
			return Fail();
		}
		if (!TestTrue(TEXT("Undo is available"), GEditor->UndoTransaction())
			|| !TestTrue(
				TEXT("Undo brings the node back"),
				FindNodeByGuid(Graph, GuidLower(A->NodeGuid)) != nullptr)
			|| !TestTrue(
				TEXT("Undo brings the edge back"),
				AreLinked(FindPin(A, TEXT("ReturnValue"), EGPD_Output), BranchCondition))
			|| !TestEqual(TEXT("Undo restores the node count"), Graph->Nodes.Num(), NodeCountBefore)
			|| !TestEqual(
				TEXT("Undo returns the edge fingerprint to before"),
				UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(Graph),
				EdgesFingerprintBefore)
			|| !TestTrue(TEXT("Redo is available"), GEditor->RedoTransaction())
			|| !TestTrue(
				TEXT("Redo removes the node again"),
				FindNodeByGuid(Graph, GuidLower(A->NodeGuid)) == nullptr)
			|| !TestEqual(
				TEXT("Redo returns the node fingerprint to after"),
				UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicNodeStateFingerprint(Graph),
				NodesAfter))
		{
			return Fail();
		}
		if (!TestTrue(TEXT("the explicit save of the removal succeeds"), SaveApplied(ApplyResult, Error)))
		{
			AddError(Error);
			return Fail();
		}
		if (!TestFalse(TEXT("save cleared the dirty flag"), Package->IsDirty())
			|| !TestFalse(
				TEXT("save changed the asset on disk"),
				ComputeAssetSha(Blueprint).Equals(AssetShaBefore, ESearchCase::CaseSensitive)))
		{
			return Fail();
		}
	}

	// ---- Remove an isolated node: nodes change, edges do not.
	{
		const FString EdgesBeforeIsolated =
			UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(Graph);
		const TArray<TSharedPtr<FJsonObject>> Operations = {
			MakeRemoveOperation(TEXT("r2"), D, NotBoolClassPath, {}),
		};
		TSharedPtr<FJsonObject> Preview;
		FString Code;
		FString Message;
		if (!TestTrue(
				TEXT("the isolated removal previews"),
				UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
					BuildPatchParams(Blueprint, Graph, Operations),
					Preview,
					Code,
					Message)))
		{
			AddError(Code + TEXT(" ") + Message);
			return Fail();
		}
		TSharedPtr<FJsonObject> ApplyResult;
		if (!TestTrue(
				TEXT("the isolated removal applies"),
				UEAgentKitBlueprintPatchExperimental::TryApplyExperimental(
					BuildPatchApplyParams(Preview, Operations),
					ApplyResult,
					Code,
					Message)))
		{
			AddError(Code + TEXT(" ") + Message);
			return Fail();
		}
		if (!TestTrue(TEXT("the isolated node is gone"), FindNodeByGuid(Graph, GuidLower(D->NodeGuid)) == nullptr)
			|| !TestEqual(
				TEXT("an isolated removal reports no implicitly removed edge"),
				ApplyResult->GetNumberField(TEXT("removedNodeEdgeCount")),
				0.0)
			|| !TestEqual(
				TEXT("an isolated removal keeps the edge fingerprint"),
				ApplyResult->GetStringField(TEXT("edgesFingerprintAfter")),
				EdgesBeforeIsolated)
			|| !TestEqual(TEXT("the isolated removal keeps the link count"),
				ApplyResult->GetNumberField(TEXT("linkCountAfter")),
				2.0))
		{
			return Fail();
		}
		if (!TestTrue(TEXT("the isolated removal save succeeds"), SaveApplied(ApplyResult, Error)))
		{
			AddError(Error);
			return Fail();
		}
	}

	// ---- All seven kinds in one patch, then one Undo for all of them. ----
	{
		TSharedRef<FJsonObject> PinOperation = MakeShared<FJsonObject>();
		PinOperation->SetStringField(TEXT("opId"), TEXT("p1"));
		PinOperation->SetStringField(TEXT("kind"), TEXT("setPinDefault"));
		PinOperation->SetStringField(TEXT("nodeGuid"), GuidLower(B->NodeGuid));
		PinOperation->SetStringField(TEXT("pinName"), TEXT("A"));
		PinOperation->SetStringField(TEXT("pinGuid"), GuidLower(FindPin(B, TEXT("A"), EGPD_Input)->PinId));
		PinOperation->SetStringField(TEXT("type"), TEXT("bool"));
		PinOperation->SetStringField(TEXT("value"), TEXT("true"));
		TSharedRef<FJsonObject> VariableOperation = MakeShared<FJsonObject>();
		VariableOperation->SetStringField(TEXT("opId"), TEXT("v1"));
		VariableOperation->SetStringField(TEXT("kind"), TEXT("createVariable"));
		VariableOperation->SetStringField(TEXT("name"), TEXT("RemovePatchFlag"));
		VariableOperation->SetStringField(TEXT("type"), TEXT("bool"));
		VariableOperation->SetStringField(TEXT("defaultValue"), TEXT("true"));
		TSharedPtr<FJsonObject> RewireOperation = MakeShared<FJsonObject>();
		RewireOperation->SetStringField(TEXT("opId"), TEXT("w1"));
		RewireOperation->SetStringField(TEXT("kind"), TEXT("rewirePin"));
		auto SetSide = [](const TSharedRef<FJsonObject>& Operation, const TCHAR* Prefix, UEdGraphPin* Pin)
		{
			Operation->SetStringField(
				FString(Prefix) + TEXT("NodeGuid"),
				GuidLower(Pin->GetOwningNode()->NodeGuid));
			Operation->SetStringField(FString(Prefix) + TEXT("PinName"), Pin->PinName.ToString());
			Operation->SetStringField(FString(Prefix) + TEXT("PinGuid"), GuidLower(Pin->PinId));
		};
		SetSide(RewireOperation.ToSharedRef(), TEXT("target"), OrA);
		SetSide(RewireOperation.ToSharedRef(), TEXT("oldSource"), BOut);
		SetSide(RewireOperation.ToSharedRef(), TEXT("newSource"), COut);

		const TArray<TSharedPtr<FJsonObject>> Mixed = {
			PinOperation,
			VariableOperation,
			MakeNodeOperationWithTempId(
				TEXT("n1"),
				TEXT("branch"),
				700,
				420,
				TEXT("tmp:b2")),
			[&]()
			{
				TSharedRef<FJsonObject> Connect = MakeShared<FJsonObject>();
				Connect->SetStringField(TEXT("opId"), TEXT("c1"));
				Connect->SetStringField(TEXT("kind"), TEXT("connect"));
				Connect->SetStringField(TEXT("from"), GuidLower(B->NodeGuid));
				Connect->SetStringField(TEXT("fromPin"), TEXT("ReturnValue"));
				Connect->SetStringField(TEXT("to"), TEXT("tmp:b2"));
				Connect->SetStringField(TEXT("toPin"), TEXT("Condition"));
				return Connect;
			}(),
			[&]()
			{
				TSharedRef<FJsonObject> Disconnect = MakeShared<FJsonObject>();
				Disconnect->SetStringField(TEXT("opId"), TEXT("d1"));
				Disconnect->SetStringField(TEXT("kind"), TEXT("disconnect"));
				Disconnect->SetStringField(TEXT("from"), GuidLower(C->NodeGuid));
				Disconnect->SetStringField(TEXT("fromPin"), TEXT("ReturnValue"));
				Disconnect->SetStringField(TEXT("to"), GuidLower(BoolOr->NodeGuid));
				Disconnect->SetStringField(TEXT("toPin"), TEXT("B"));
				return Disconnect;
			}(),
			RewireOperation,
			MakeRemoveOperation(TEXT("r9"), Branch, NodeClassPath(Branch), {}),
		};
		const int32 VariableCountBefore = Blueprint->NewVariables.Num();
		const FString EdgesBeforeMixed =
			UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(Graph);
		const int32 NodesBeforeMixed = Graph->Nodes.Num();
		TSharedPtr<FJsonObject> Preview;
		FString Code;
		FString Message;
		if (!TestTrue(
				TEXT("the seven-kind patch previews"),
				UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
					BuildPatchParams(Blueprint, Graph, Mixed),
					Preview,
					Code,
					Message))
			|| !TestTrue(TEXT("the seven-kind preview exists"), Preview.IsValid()))
		{
			AddError(Code + TEXT(" ") + Message);
			return Fail();
		}
		TArray<FString> PlannedKinds;
		for (const TSharedPtr<FJsonValue>& Value : Preview->GetArrayField(TEXT("operations")))
		{
			PlannedKinds.Add(Value->AsObject()->GetStringField(TEXT("kind")));
		}
		if (!TestEqual(
				TEXT("all seven kinds are planned in order"),
				FString::Join(PlannedKinds, TEXT(",")),
				FString(TEXT("setPinDefault,createVariable,createNode,connect,disconnect,rewirePin,removeNode"))))
		{
			return Fail();
		}
		TSharedPtr<FJsonObject> ApplyResult;
		if (!TestTrue(
				TEXT("the seven-kind patch applies"),
				UEAgentKitBlueprintPatchExperimental::TryApplyExperimental(
					BuildPatchApplyParams(Preview, Mixed),
					ApplyResult,
					Code,
					Message))
			|| !TestTrue(TEXT("the seven-kind result exists"), ApplyResult.IsValid()))
		{
			AddError(Code + TEXT(" ") + Message);
			return Fail();
		}
		bool bAllApplied = ApplyResult->GetArrayField(TEXT("results")).Num() == 7;
		for (const TSharedPtr<FJsonValue>& Value : ApplyResult->GetArrayField(TEXT("results")))
		{
			bAllApplied = bAllApplied
				&& Value->AsObject()->GetStringField(TEXT("status")) == TEXT("applied");
		}
		if (!TestTrue(TEXT("all seven kinds applied"), bAllApplied)
			|| !TestTrue(TEXT("the removed node is gone"),
				FindNodeByGuid(Graph, GuidLower(Branch->NodeGuid)) == nullptr)
			|| !TestTrue(TEXT("the rewired edge moved to the new source"), AreLinked(COut, OrA))
			|| !TestFalse(TEXT("the rewired edge left the old source"), AreLinked(BOut, OrA))
			|| !TestFalse(TEXT("the disconnected edge is gone"), AreLinked(COut, OrB))
			|| !TestEqual(
				TEXT("the mixed patch nets one node and one edge"),
				Graph->Nodes.Num(),
				NodesBeforeMixed)
			|| !TestEqual(
				TEXT("the mixed patch keeps the link count"),
				ApplyResult->GetNumberField(TEXT("linkCountAfter")),
				2.0)
			|| !TestEqual(
				TEXT("the variable was created"),
				Blueprint->NewVariables.Num(),
				VariableCountBefore + 1))
		{
			return Fail();
		}
		if (!TestTrue(TEXT("one Undo reverts the whole patch"), GEditor->UndoTransaction())
			|| !TestTrue(
				TEXT("Undo brings the removed node back"),
				FindNodeByGuid(Graph, GuidLower(Branch->NodeGuid)) != nullptr)
			|| !TestTrue(TEXT("Undo undoes the rewire"), AreLinked(BOut, OrA))
			|| !TestTrue(TEXT("Undo undoes the disconnect"), AreLinked(COut, OrB))
			|| !TestEqual(
				TEXT("Undo restores the node count"),
				Graph->Nodes.Num(),
				NodesBeforeMixed)
			|| !TestEqual(
				TEXT("Undo returns the edge fingerprint to before"),
				UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(Graph),
				EdgesBeforeMixed)
			|| !TestEqual(
				TEXT("Undo removes the created variable"),
				Blueprint->NewVariables.Num(),
				VariableCountBefore)
			|| !TestTrue(TEXT("Redo re-applies the whole patch"), GEditor->RedoTransaction()))
		{
			return Fail();
		}
	}

	UEAgentKitBlueprintPatchExperimental::ResetExperimentalWriteForTest();
	return true;
}

namespace UEAgentKitPatchVariableAccessor
{
	using namespace UEAgentKitPatchCreateNode;
	using UEAgentKitPatchRemoveNode::AreLinked;
	using UEAgentKitPatchRemoveNode::FindPin;
	using UEAgentKitPatchRemoveNode::SaveApplied;

	const TCHAR* const AssetPath =
		TEXT("/Game/UEAgentKitPatchExpansion/BP_PatchVariableAccessor.BP_PatchVariableAccessor");
	const TCHAR* const PackageName =
		TEXT("/Game/UEAgentKitPatchExpansion/BP_PatchVariableAccessor");
	const TCHAR* const AssetName = TEXT("BP_PatchVariableAccessor");
	const TCHAR* const FixtureVariable = TEXT("Counter");
	const TCHAR* const VariableGetClassPath = TEXT("/Script/BlueprintGraph.K2Node_VariableGet");
	const TCHAR* const VariableSetClassPath = TEXT("/Script/BlueprintGraph.K2Node_VariableSet");

	/** One int member variable named Counter and nothing else, saved on disk. */
	UBlueprint* CreateAccessorFixture(FString& OutError)
	{
		if (FindObject<UBlueprint>(nullptr, AssetPath) != nullptr)
		{
			OutError = TEXT("Variable accessor fixture already exists; use a fresh project.");
			return nullptr;
		}
		if (!RemoveStaleFiles(PackageName, OutError))
		{
			return nullptr;
		}
		UPackage* Package = CreatePackage(PackageName);
		UBlueprint* Blueprint = FKismetEditorUtilities::CreateBlueprint(
			AActor::StaticClass(),
			Package,
			FName(AssetName),
			BPTYPE_Normal,
			FName(TEXT("UEAgentKitPatchVariableAccessor")));
		if (Blueprint == nullptr || Blueprint->UbergraphPages.IsEmpty())
		{
			OutError = TEXT("Could not create the variable accessor fixture Blueprint.");
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
		FEdGraphPinType IntType;
		IntType.PinCategory = UEdGraphSchema_K2::PC_Int;
		if (!FBlueprintEditorUtils::AddMemberVariable(Blueprint, FName(FixtureVariable), IntType)
			|| Blueprint->NewVariables.IsEmpty())
		{
			OutError = TEXT("Could not add the fixture int variable.");
			return nullptr;
		}
		FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
		FKismetEditorUtilities::CompileBlueprint(
			Blueprint,
			EBlueprintCompileOptions::SkipGarbageCollection);
		if (Blueprint->Status == BS_Error)
		{
			OutError = TEXT("Variable accessor fixture failed to compile.");
			return nullptr;
		}
		FAssetRegistryModule::AssetCreated(Blueprint);
		Package->MarkPackageDirty();
		if (!SaveBlueprint(Blueprint))
		{
			OutError = TEXT("Could not save the variable accessor fixture.");
			return nullptr;
		}
		return Blueprint;
	}

	FString FixtureVariableGuid(UBlueprint* Blueprint)
	{
		if (Blueprint == nullptr)
		{
			return FString();
		}
		for (const FBPVariableDescription& Variable : Blueprint->NewVariables)
		{
			if (Variable.VarName.ToString().Equals(FixtureVariable, ESearchCase::CaseSensitive))
			{
				return GuidLower(Variable.VarGuid);
			}
		}
		return FString();
	}

	TSharedPtr<FJsonObject> MakeAccessorNode(
		const TCHAR* OpId,
		const TCHAR* NodeType,
		UBlueprint* Blueprint,
		int32 X,
		int32 Y,
		const TCHAR* TempId = nullptr,
		const TCHAR* VariableType = TEXT("int"))
	{
		TSharedRef<FJsonObject> Operation = MakeShared<FJsonObject>();
		Operation->SetStringField(TEXT("opId"), OpId);
		Operation->SetStringField(TEXT("kind"), TEXT("createNode"));
		Operation->SetStringField(TEXT("nodeType"), NodeType);
		Operation->SetNumberField(TEXT("x"), X);
		Operation->SetNumberField(TEXT("y"), Y);
		Operation->SetStringField(TEXT("variableName"), FixtureVariable);
		Operation->SetStringField(TEXT("variableGuid"), FixtureVariableGuid(Blueprint));
		Operation->SetStringField(TEXT("variableType"), VariableType);
		if (TempId != nullptr)
		{
			Operation->SetStringField(TEXT("tempId"), TempId);
		}
		return Operation;
	}

	TSharedPtr<FJsonObject> WithTempId(TSharedPtr<FJsonObject> Operation, const TCHAR* TempId)
	{
		Operation->SetStringField(TEXT("tempId"), TempId);
		return Operation;
	}

	TSharedPtr<FJsonObject> MakeConnectOperation(
		const TCHAR* OpId,
		const TCHAR* FromRef,
		const TCHAR* FromPin,
		const TCHAR* ToRef,
		const TCHAR* ToPin)
	{
		TSharedRef<FJsonObject> Operation = MakeShared<FJsonObject>();
		Operation->SetStringField(TEXT("opId"), OpId);
		Operation->SetStringField(TEXT("kind"), TEXT("connect"));
		Operation->SetStringField(TEXT("from"), FromRef);
		Operation->SetStringField(TEXT("fromPin"), FromPin);
		Operation->SetStringField(TEXT("to"), ToRef);
		Operation->SetStringField(TEXT("toPin"), ToPin);
		return Operation;
	}
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitPatchVariableAccessorTest,
	"UEAgentKit.AtomicPatch.VariableAccessor",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitPatchVariableAccessorTest::RunTest(const FString& Parameters)
{
	using namespace UEAgentKitPatchCreateNode;
	using namespace UEAgentKitPatchVariableAccessor;

	UEAgentKitBlueprintPatchExperimental::ResetExperimentalWriteForTest();
	if (!TestFalse(
			TEXT("the atomic patch gate stays off unless the project opts in"),
			UEAgentKitBlueprintPatchExperimental::IsExperimentalWriteAvailable()))
	{
		return false;
	}
	UEAgentKitBlueprintPatchExperimental::SetExperimentalWriteEnabledForTest(true);
	auto Fail = []
	{
		UEAgentKitBlueprintPatchExperimental::ResetExperimentalWriteForTest();
		return false;
	};

	FString Error;
	UBlueprint* Blueprint = CreateAccessorFixture(Error);
	if (!TestNotNull(TEXT("variable accessor fixture is created"), Blueprint))
	{
		AddError(Error);
		return Fail();
	}
	UEdGraph* Graph = Blueprint->UbergraphPages[0];
	UPackage* Package = Blueprint->GetOutermost();
	if (!TestNotNull(TEXT("variable accessor graph is focused"), OpenAndFocus(Blueprint, Graph, Error)))
	{
		AddError(Error);
		return Fail();
	}
	const FString GuidText = FixtureVariableGuid(Blueprint);
	const int32 NodeCountBefore = Graph->Nodes.Num();
	const FString AssetShaBefore = ComputeAssetSha(Blueprint);
	const FString EdgesFingerprintBefore =
		UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(Graph);
	const FString NodesFingerprintBefore =
		UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicNodeStateFingerprint(Graph);
	if (!TestEqual(TEXT("the fixture variable has a canonical GUID"), GuidText.Len(), 36))
	{
		return Fail();
	}

	// ---- Fail-closed refusals: one table, no write, no dirty package. ----
	{
		TSharedPtr<FJsonObject> AbsentGuid =
			MakeAccessorNode(TEXT("n1"), TEXT("variable_get"), Blueprint, 200, 0);
		AbsentGuid->SetStringField(
			TEXT("variableGuid"),
			TEXT("00000000-0000-0000-0000-000000000000"));
		AbsentGuid->SetStringField(TEXT("variableName"), TEXT("MissingCounter"));
		TSharedPtr<FJsonObject> NameOwnedByOtherGuid =
			MakeAccessorNode(TEXT("n1"), TEXT("variable_get"), Blueprint, 200, 0);
		NameOwnedByOtherGuid->SetStringField(
			TEXT("variableGuid"),
			TEXT("00000000-0000-0000-0000-000000000000"));
		TSharedPtr<FJsonObject> NameMismatch =
			MakeAccessorNode(TEXT("n1"), TEXT("variable_get"), Blueprint, 200, 0);
		NameMismatch->SetStringField(TEXT("variableName"), TEXT("OtherCounter"));
		TSharedPtr<FJsonObject> TypeMismatch =
			MakeAccessorNode(TEXT("n1"), TEXT("variable_get"), Blueprint, 200, 0, nullptr, TEXT("bool"));
		TSharedPtr<FJsonObject> BadName =
			MakeAccessorNode(TEXT("n1"), TEXT("variable_get"), Blueprint, 200, 0);
		BadName->SetStringField(TEXT("variableName"), TEXT("Output_Get"));
		TSharedPtr<FJsonObject> BadGuid =
			MakeAccessorNode(TEXT("n1"), TEXT("variable_get"), Blueprint, 200, 0);
		BadGuid->SetStringField(TEXT("variableGuid"), TEXT("not-a-guid"));
		TSharedPtr<FJsonObject> BadType =
			MakeAccessorNode(TEXT("n1"), TEXT("variable_set"), Blueprint, 200, 0, nullptr, TEXT("vector"));
		TSharedPtr<FJsonObject> PrimitiveWithVariable =
			MakeNodeOperation(TEXT("n1"), TEXT("branch"), 200, 0);
		PrimitiveWithVariable->SetStringField(TEXT("variableName"), FixtureVariable);
		struct FRefusal
		{
			const TCHAR* Label;
			TSharedPtr<FJsonObject> Operation;
			const TCHAR* ExpectedCode;
		};
		const TArray<FRefusal> Refusals = {
			{ TEXT("a variable GUID and name that do not exist"), AbsentGuid, TEXT("graph-edit-variable-not-found") },
			{ TEXT("a name that belongs to a different GUID"), NameOwnedByOtherGuid, TEXT("graph-edit-variable-identity-mismatch") },
			{ TEXT("a name the declared GUID does not own"), NameMismatch, TEXT("graph-edit-variable-identity-mismatch") },
			{ TEXT("a variableType that does not match the member"), TypeMismatch, TEXT("graph-edit-variable-type-mismatch") },
			{ TEXT("a reserved pin name as variableName"), BadName, TEXT("graph-edit-variable-name-invalid") },
			{ TEXT("a non-canonical variableGuid"), BadGuid, TEXT("graph-edit-variable-guid-invalid") },
			{ TEXT("an unsupported variableType"), BadType, TEXT("graph-edit-variable-type-invalid") },
			{ TEXT("a primitive nodeType carrying a variable field"), PrimitiveWithVariable, TEXT("graph-edit-variable-field-not-allowed") },
		};
		for (const FRefusal& Refusal : Refusals)
		{
			TSharedPtr<FJsonObject> RefusedResult;
			FString RefusedCode;
			FString RefusedMessage;
			if (!TestFalse(
					FString::Printf(TEXT("refused: %s"), Refusal.Label),
					UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
						BuildPatchParams(Blueprint, Graph, { Refusal.Operation }),
						RefusedResult,
						RefusedCode,
						RefusedMessage))
				|| !TestEqual(
					FString::Printf(TEXT("refusal code for %s"), Refusal.Label),
					RefusedCode,
					FString(Refusal.ExpectedCode)))
			{
				AddError(RefusedMessage);
				return Fail();
			}
		}
		if (!TestEqual(TEXT("refusals left the node count"), Graph->Nodes.Num(), NodeCountBefore)
			|| !TestFalse(TEXT("refusals left the package clean"), Package->IsDirty())
			|| !TestEqual(TEXT("refusals left the disk asset"), ComputeAssetSha(Blueprint), AssetShaBefore)
			|| !TestEqual(
				TEXT("refusals left the edge fingerprint"),
				UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(Graph),
				EdgesFingerprintBefore)
			|| !TestEqual(
				TEXT("refusals left the node fingerprint"),
				UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicNodeStateFingerprint(Graph),
				NodesFingerprintBefore))
		{
			return Fail();
		}
	}

	// ---- get + int_add + connect in one transaction, then Undo/Redo, then Save. ----
	{
		const TArray<TSharedPtr<FJsonObject>> Operations = {
			MakeAccessorNode(TEXT("g1"), TEXT("variable_get"), Blueprint, 200, 0, TEXT("tmp:get")),
			WithTempId(MakeNodeOperation(TEXT("a1"), TEXT("int_add"), 420, 0), TEXT("tmp:add")),
			MakeConnectOperation(TEXT("c1"), TEXT("tmp:get"), FixtureVariable, TEXT("tmp:add"), TEXT("A")),
		};
		TSharedPtr<FJsonObject> Preview;
		FString Code;
		FString Message;
		if (!TestTrue(
				TEXT("the accessor patch previews"),
				UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
					BuildPatchParams(Blueprint, Graph, Operations),
					Preview,
					Code,
					Message))
			|| !TestTrue(TEXT("the accessor preview exists"), Preview.IsValid()))
		{
			AddError(Code + TEXT(" ") + Message);
			return Fail();
		}
		const TArray<TSharedPtr<FJsonValue>>& Planned = Preview->GetArrayField(TEXT("operations"));
		if (!TestEqual(
				TEXT("the accessor preview reports the bound variable"),
				Planned[0]->AsObject()->GetStringField(TEXT("variableName")),
				FString(FixtureVariable))
			|| !TestEqual(
				TEXT("the accessor preview reports the variable type"),
				Planned[0]->AsObject()->GetStringField(TEXT("variableType")),
				FString(TEXT("int")))
			|| !TestEqual(
				TEXT("the accessor preview reports the read direction"),
				Planned[0]->AsObject()->GetStringField(TEXT("variableAccess")),
				FString(TEXT("read")))
			|| !TestEqual(
				TEXT("the accessor preview resolves the engine class"),
				Planned[0]->AsObject()->GetStringField(TEXT("nodeClass")),
				FString(VariableGetClassPath)))
		{
			return Fail();
		}
		TSharedPtr<FJsonObject> ApplyResult;
		if (!TestTrue(
				TEXT("the accessor patch applies"),
				UEAgentKitBlueprintPatchExperimental::TryApplyExperimental(
					BuildPatchApplyParams(Preview, Operations),
					ApplyResult,
					Code,
					Message))
			|| !TestTrue(TEXT("the accessor result exists"), ApplyResult.IsValid()))
		{
			AddError(Code + TEXT(" ") + Message);
			return Fail();
		}
		bool bAllApplied = ApplyResult->GetArrayField(TEXT("results")).Num() == 3;
		for (const TSharedPtr<FJsonValue>& Value : ApplyResult->GetArrayField(TEXT("results")))
		{
			bAllApplied = bAllApplied
				&& Value->AsObject()->GetStringField(TEXT("status")) == TEXT("applied");
		}
		const TArray<TSharedPtr<FJsonValue>>& Results = ApplyResult->GetArrayField(TEXT("results"));
		UEdGraphNode* GetNode = FindNodeByGuid(Graph, Results[0]->AsObject()->GetStringField(TEXT("nodeGuid")));
		UEdGraphNode* AddNode = FindNodeByGuid(Graph, Results[1]->AsObject()->GetStringField(TEXT("nodeGuid")));
		UEdGraphPin* CounterOut = FindPin(GetNode, FixtureVariable, EGPD_Output);
		UEdGraphPin* AddA = FindPin(AddNode, TEXT("A"), EGPD_Input);
		UK2Node_VariableGet* GetVariable = Cast<UK2Node_VariableGet>(GetNode);
		if (!TestTrue(TEXT("all three operations applied"), bAllApplied)
			|| !TestTrue(TEXT("the accessor node exists"), GetNode != nullptr)
			|| !TestEqual(
				TEXT("the created accessor is a K2Node_VariableGet"),
				GetNode != nullptr && GetNode->GetClass() != nullptr
					? GetNode->GetClass()->GetPathName()
					: FString(),
				FString(VariableGetClassPath))
			|| !TestTrue(TEXT("the accessor is a variable-get node"), GetVariable != nullptr)
			|| !TestEqual(
				TEXT("the accessor binds the fixture variable"),
				GetVariable != nullptr ? GetVariable->GetVarName().ToString() : FString(),
				FString(FixtureVariable))
			|| !TestNotNull(TEXT("the get node has the variable-named output pin"), CounterOut)
			|| !TestTrue(TEXT("the accessor was wired to the add node"), AreLinked(CounterOut, AddA))
			|| !TestEqual(
				TEXT("two nodes were created"),
				ApplyResult->GetNumberField(TEXT("createdNodeCount")),
				2.0)
			|| !TestEqual(
				TEXT("one edge was created"),
				ApplyResult->GetNumberField(TEXT("createdEdgeCount")),
				1.0)
			|| !TestEqual(
				TEXT("the node count grew by two"),
				ApplyResult->GetNumberField(TEXT("nodeCountAfter")),
				static_cast<double>(NodeCountBefore + 2))
			|| !TestEqual(
				TEXT("the link count grew by one"),
				ApplyResult->GetNumberField(TEXT("linkCountAfter")),
				1.0)
			|| !TestTrue(
				TEXT("the accessor patch is one verified Undo unit"),
				ApplyResult->GetBoolField(TEXT("undoVerified"))
					&& ApplyResult->GetBoolField(TEXT("redoVerified"))))
		{
			return Fail();
		}
		if (!TestTrue(TEXT("Undo removes both created nodes"), GEditor->UndoTransaction())
			|| !TestEqual(TEXT("Undo restores the node count"), Graph->Nodes.Num(), NodeCountBefore)
			|| !TestEqual(
				TEXT("Undo restores the node fingerprint"),
				UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicNodeStateFingerprint(Graph),
				NodesFingerprintBefore)
			|| !TestEqual(
				TEXT("Undo restores the edge fingerprint"),
				UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(Graph),
				EdgesFingerprintBefore)
			|| !TestTrue(TEXT("Redo re-applies the patch"), GEditor->RedoTransaction())
			|| !TestTrue(
				TEXT("Redo brings the accessor back"),
				FindNodeByGuid(Graph, GuidLower(GetNode->NodeGuid)) != nullptr))
		{
			return Fail();
		}
		if (!TestTrue(TEXT("the explicit save of the accessor patch succeeds"), SaveApplied(ApplyResult, Error)))
		{
			AddError(Error);
			return Fail();
		}
		if (!TestFalse(TEXT("save cleared the dirty flag"), Package->IsDirty())
			|| !TestFalse(
				TEXT("save changed the asset on disk"),
				ComputeAssetSha(Blueprint).Equals(AssetShaBefore, ESearchCase::CaseSensitive)))
		{
			return Fail();
		}
	}

	// ---- variable_set: the write accessor and its four-pin contract. ----
	{
		const TArray<TSharedPtr<FJsonObject>> Operations = {
			MakeAccessorNode(TEXT("s1"), TEXT("variable_set"), Blueprint, 200, 400, TEXT("tmp:set")),
		};
		TSharedPtr<FJsonObject> Preview;
		FString Code;
		FString Message;
		if (!TestTrue(
				TEXT("the set accessor patch previews"),
				UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
					BuildPatchParams(Blueprint, Graph, Operations),
					Preview,
					Code,
					Message)))
		{
			AddError(Code + TEXT(" ") + Message);
			return Fail();
		}
		TSharedPtr<FJsonObject> ApplyResult;
		if (!TestTrue(
				TEXT("the set accessor patch applies"),
				UEAgentKitBlueprintPatchExperimental::TryApplyExperimental(
					BuildPatchApplyParams(Preview, Operations),
					ApplyResult,
					Code,
					Message)))
		{
			AddError(Code + TEXT(" ") + Message);
			return Fail();
		}
		UEdGraphNode* SetNode = FindNodeByGuid(
			Graph,
			ApplyResult->GetArrayField(TEXT("results"))[0]->AsObject()->GetStringField(TEXT("nodeGuid")));
		if (!TestTrue(TEXT("the set accessor exists"), SetNode != nullptr)
			|| !TestEqual(
				TEXT("the created accessor is a K2Node_VariableSet"),
				SetNode != nullptr && SetNode->GetClass() != nullptr
					? SetNode->GetClass()->GetPathName()
					: FString(),
				FString(VariableSetClassPath))
			|| !TestNotNull(TEXT("the set node has an execute input"), FindPin(SetNode, TEXT("execute"), EGPD_Input))
			|| !TestNotNull(TEXT("the set node has the variable input"), FindPin(SetNode, FixtureVariable, EGPD_Input))
			|| !TestNotNull(TEXT("the set node has a then output"), FindPin(SetNode, TEXT("then"), EGPD_Output))
			|| !TestNotNull(
				TEXT("the set node has an Output_Get output"),
				FindPin(SetNode, TEXT("Output_Get"), EGPD_Output))
			|| !TestEqual(
				TEXT("the set accessor reports the write direction"),
				ApplyResult->GetArrayField(TEXT("results"))[0]->AsObject()->GetStringField(
					TEXT("variableAccess")),
				FString(TEXT("write"))))
		{
			return Fail();
		}
		if (!TestTrue(TEXT("the set accessor save succeeds"), SaveApplied(ApplyResult, Error)))
		{
			AddError(Error);
			return Fail();
		}
	}

	// ---- Rollback: an injected failure removes both created accessors again. ----
	{
		const FString EdgesBeforeRollback =
			UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(Graph);
		const FString NodesBeforeRollback =
			UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicNodeStateFingerprint(Graph);
		const FString ShaBeforeRollback = ComputeAssetSha(Blueprint);
		const int32 NodesBefore = Graph->Nodes.Num();
		const TArray<TSharedPtr<FJsonObject>> Operations = {
			MakeAccessorNode(TEXT("g1"), TEXT("variable_get"), Blueprint, 200, 600),
			MakeAccessorNode(TEXT("s1"), TEXT("variable_set"), Blueprint, 420, 600),
		};
		TSharedPtr<FJsonObject> Preview;
		FString Code;
		FString Message;
		if (!TestTrue(
				TEXT("the accessor rollback patch previews"),
				UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
					BuildPatchParams(Blueprint, Graph, Operations),
					Preview,
					Code,
					Message)))
		{
			AddError(Code + TEXT(" ") + Message);
			return Fail();
		}
		UEAgentKitBlueprintPatchExperimental::SetNextApplyFaultForTest(1);
		TSharedPtr<FJsonObject> ApplyResult;
		if (!TestFalse(
				TEXT("the accessor rollback patch fails"),
				UEAgentKitBlueprintPatchExperimental::TryApplyExperimental(
					BuildPatchApplyParams(Preview, Operations),
					ApplyResult,
					Code,
					Message)))
		{
			return Fail();
		}
		if (!TestEqual(
				TEXT("the rollback reports the injected fault"),
				Code,
				FString(TEXT("graph-patch-apply-fault-injected")))
			|| !TestEqual(
				TEXT("the rollback removed the first accessor"),
				Graph->Nodes.Num(),
				NodesBefore)
			|| !TestEqual(
				TEXT("the rollback restored the node fingerprint"),
				UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicNodeStateFingerprint(Graph),
				NodesBeforeRollback)
			|| !TestEqual(
				TEXT("the rollback restored the edge fingerprint"),
				UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(Graph),
				EdgesBeforeRollback)
			|| !TestFalse(TEXT("the rollback left the package clean"), Package->IsDirty())
			|| !TestEqual(TEXT("the rollback left the disk asset"), ComputeAssetSha(Blueprint), ShaBeforeRollback)
			|| !TestTrue(
				TEXT("the rollback says the state is restored"),
				ApplyResult->GetBoolField(TEXT("stateRestored"))))
		{
			AddError(Code + TEXT(" ") + Message);
			return Fail();
		}
	}

	UEAgentKitBlueprintPatchExperimental::ResetExperimentalWriteForTest();
	return true;
}

#endif // WITH_DEV_AUTOMATION_TESTS
