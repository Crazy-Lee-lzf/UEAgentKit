// Real-Editor acceptance for 0-3.4 slice 11: `connect` inside the atomic
// cross-writer Blueprint patch, including tempId endpoints.
//
// One disposable fixture is used: a K2 event graph with two bool sources
// (Not_PreBool), one branch (bool Condition input) and one BooleanOR (bool A/B
// inputs), all unconnected and saved on disk.  The test proves:
//
//  - the product gate stays off unless the project opts in;
//  - tempId shape/duplicate/unknown/forward-reference refusals and the
//    pin-compatibility refusals (type mismatch, self connection, duplicate
//    target, unmigrated `removeNode`) all fail closed before any write and leave
//    the package, the graph and the disk SHA untouched;
//  - a single `connect` patch creates exactly the edge the single-writer graph
//    edit existing-pin path creates (same pin identities, same edge-set hash);
//  - the EDGE fingerprint identifies endpoints: apply changes it, Undo returns
//    it to before, Redo returns it to after;
//  - the reverse case that the pin-default fingerprint cannot see -- two edges
//    whose endpoints are swapped while every per-pin link COUNT stays 1 --
//    leaves `stateFingerprint` unchanged but MUST change `edgesFingerprint`
//    (this is the proof anchor of the slice);
//  - one patch can create a node through `createNode` and wire it through
//    `connect` with a tempId, in one transaction;
//  - a failure injected on the SECOND operation removes the FIRST operation's
//    edge, keeps the node count unchanged, leaves the package clean, leaves the
//    disk SHA unchanged and still reports one status per operation;
//  - the four migrated kinds can be mixed in one patch and one Undo removes all
//    of them;
//  - the explicit Save is the only disk write of that block and a post-save
//    preview sees the saved edge set.
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

namespace UEAgentKitPatchConnect
{
	const TCHAR* const AssetPath =
		TEXT("/Game/UEAgentKitPatchExpansion/BP_PatchConnect.BP_PatchConnect");
	const TCHAR* const PackageName = TEXT("/Game/UEAgentKitPatchExpansion/BP_PatchConnect");
	const TCHAR* const AssetName = TEXT("BP_PatchConnect");

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

	bool RemoveStaleFiles(FString& OutError)
	{
		const FString Filename = FPackageName::LongPackageNameToFilename(
			PackageName,
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

	UBlueprint* CreateFixture(FString& OutError)
	{
		if (FindObject<UBlueprint>(nullptr, AssetPath) != nullptr)
		{
			OutError = TEXT("Connect fixture already exists; use a fresh project.");
			return nullptr;
		}
		if (!RemoveStaleFiles(OutError))
		{
			return nullptr;
		}
		UPackage* Package = CreatePackage(PackageName);
		UBlueprint* Blueprint = FKismetEditorUtilities::CreateBlueprint(
			AActor::StaticClass(),
			Package,
			FName(AssetName),
			BPTYPE_Normal,
			FName(TEXT("UEAgentKitPatchConnect")));
		if (Blueprint == nullptr || Blueprint->UbergraphPages.IsEmpty())
		{
			OutError = TEXT("Could not create the connect fixture Blueprint.");
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
		if (Branch == nullptr)
		{
			OutError = TEXT("Could not create the fixture branch node.");
			return nullptr;
		}
		Graph->AddNode(Branch, true, false);
		Branch->AllocateDefaultPins();
		if (!Branch->NodeGuid.IsValid())
		{
			Branch->CreateNewGuid();
		}
		Branch->NodePosX = 340;
		Branch->NodePosY = 0;
		if (AddCallFunctionNode(
				Graph,
				UKismetMathLibrary::StaticClass(),
				TEXT("Not_PreBool"),
				0,
				0) == nullptr
			|| AddCallFunctionNode(
				Graph,
				UKismetMathLibrary::StaticClass(),
				TEXT("Not_PreBool"),
				0,
				200) == nullptr
			|| AddCallFunctionNode(
				Graph,
				UKismetMathLibrary::StaticClass(),
				TEXT("BooleanOR"),
				340,
				200) == nullptr)
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
			OutError = TEXT("Connect fixture failed to compile.");
			return nullptr;
		}
		FAssetRegistryModule::AssetCreated(Blueprint);
		Package->MarkPackageDirty();
		if (!SaveBlueprint(Blueprint))
		{
			OutError = TEXT("Could not save the connect fixture.");
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

	UEdGraphNode* FindNodeAt(UEdGraph* Graph, int32 X, int32 Y)
	{
		for (UEdGraphNode* Node : Graph->Nodes)
		{
			if (Node != nullptr && Node->NodePosX == X && Node->NodePosY == Y)
			{
				return Node;
			}
		}
		return nullptr;
	}

	UEdGraphPin* FindPin(UEdGraphNode* Node, const TCHAR* PinName, EEdGraphPinDirection Direction)
	{
		return Node != nullptr ? Node->FindPin(FName(PinName), Direction) : nullptr;
	}

	bool AreLinked(UEdGraphPin* From, UEdGraphPin* To)
	{
		return From != nullptr && To != nullptr
			&& From->LinkedTo.Contains(To) && To->LinkedTo.Contains(From);
	}

	TSharedPtr<FJsonObject> MakeNodeOperation(
		const TCHAR* OpId,
		const TCHAR* NodeType,
		int32 X,
		int32 Y,
		const TCHAR* TempId = nullptr)
	{
		TSharedRef<FJsonObject> Operation = MakeShared<FJsonObject>();
		Operation->SetStringField(TEXT("opId"), OpId);
		Operation->SetStringField(TEXT("kind"), TEXT("createNode"));
		Operation->SetStringField(TEXT("nodeType"), NodeType);
		Operation->SetNumberField(TEXT("x"), X);
		Operation->SetNumberField(TEXT("y"), Y);
		if (TempId != nullptr)
		{
			Operation->SetStringField(TEXT("tempId"), TempId);
		}
		return Operation;
	}

	TSharedPtr<FJsonObject> MakeConnectOperation(
		const TCHAR* OpId,
		const FString& FromRef,
		const TCHAR* FromPin,
		const FString& ToRef,
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

	/** The single writer's pure existing-pin connection shape. */
	TSharedPtr<FJsonObject> MakeExistingPin(
		UEdGraphNode* Node,
		UEdGraphPin* Pin,
		const TCHAR* Direction,
		const TCHAR* Category)
	{
		TSharedRef<FJsonObject> Spec = MakeShared<FJsonObject>();
		Spec->SetStringField(TEXT("nodeGuid"), GuidLower(Node->NodeGuid));
		Spec->SetStringField(TEXT("pinGuid"), GuidLower(Pin->PinId));
		Spec->SetStringField(TEXT("pinName"), Pin->PinName.ToString());
		Spec->SetStringField(TEXT("direction"), Direction);
		Spec->SetStringField(TEXT("category"), Category);
		Spec->SetStringField(TEXT("subCategory"), TEXT("None"));
		return Spec;
	}

	TSharedPtr<FJsonObject> BuildGraphEditParams(
		UBlueprint* Blueprint,
		UEdGraph* Graph,
		const TSharedPtr<FJsonObject>& FromExisting,
		const TSharedPtr<FJsonObject>& ToExisting)
	{
		TSharedRef<FJsonObject> Connection = MakeShared<FJsonObject>();
		Connection->SetObjectField(TEXT("fromExisting"), FromExisting);
		Connection->SetObjectField(TEXT("toExisting"), ToExisting);
		TArray<TSharedPtr<FJsonValue>> Connections;
		Connections.Add(MakeShared<FJsonValueObject>(Connection));
		TSharedRef<FJsonObject> Params = MakeShared<FJsonObject>();
		Params->SetStringField(TEXT("assetPath"), Blueprint->GetPathName());
		Params->SetStringField(TEXT("graphGuid"), GuidLower(Graph->GraphGuid));
		Params->SetArrayField(TEXT("nodes"), {});
		Params->SetArrayField(TEXT("connections"), Connections);
		return Params;
	}

	TSharedPtr<FJsonObject> BuildGraphEditApplyParams(
		const TSharedPtr<FJsonObject>& Preview,
		const TSharedPtr<FJsonObject>& FromExisting,
		const TSharedPtr<FJsonObject>& ToExisting)
	{
		TSharedRef<FJsonObject> Connection = MakeShared<FJsonObject>();
		Connection->SetObjectField(TEXT("fromExisting"), FromExisting);
		Connection->SetObjectField(TEXT("toExisting"), ToExisting);
		TArray<TSharedPtr<FJsonValue>> Connections;
		Connections.Add(MakeShared<FJsonValueObject>(Connection));
		TSharedRef<FJsonObject> Params = MakeShared<FJsonObject>();
		Params->SetStringField(TEXT("assetPath"), Preview->GetStringField(TEXT("assetPath")));
		Params->SetStringField(TEXT("graphGuid"), Preview->GetStringField(TEXT("graphGuid")));
		Params->SetArrayField(TEXT("nodes"), {});
		Params->SetArrayField(TEXT("connections"), Connections);
		Params->SetStringField(TEXT("planId"), Preview->GetStringField(TEXT("planId")));
		Params->SetStringField(
			TEXT("confirmation"),
			Preview->GetStringField(TEXT("requiredConfirmation")));
		return Params;
	}

	int32 CountLiveLinks(UEdGraph* Graph)
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
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitPatchConnectTest,
	"UEAgentKit.AtomicPatch.Connect",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitPatchConnectTest::RunTest(const FString& Parameters)
{
	using namespace UEAgentKitPatchConnect;

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
	UBlueprint* Blueprint = CreateFixture(Error);
	if (!TestNotNull(TEXT("connect fixture is created"), Blueprint))
	{
		AddError(Error);
		return false;
	}
	UEdGraph* Graph = Blueprint->UbergraphPages[0];
	UPackage* Package = Blueprint->GetOutermost();
	if (!TestNotNull(TEXT("connect fixture graph is focused"), OpenAndFocus(Blueprint, Graph, Error)))
	{
		AddError(Error);
		return false;
	}
	UEdGraphNode* NotBool1 = FindNodeAt(Graph, 0, 0);
	UEdGraphNode* NotBool2 = FindNodeAt(Graph, 0, 200);
	UEdGraphNode* Branch = FindNodeAt(Graph, 340, 0);
	UEdGraphNode* BoolOr = FindNodeAt(Graph, 340, 200);
	if (!TestNotNull(TEXT("fixture bool source 1"), NotBool1)
		|| !TestNotNull(TEXT("fixture bool source 2"), NotBool2)
		|| !TestNotNull(TEXT("fixture branch"), Branch)
		|| !TestNotNull(TEXT("fixture boolean-or"), BoolOr))
	{
		return Fail();
	}
	const int32 NodeCountBefore = Graph->Nodes.Num();
	if (!TestEqual(TEXT("the fixture has exactly four nodes"), NodeCountBefore, 4)
		|| !TestEqual(TEXT("the fixture starts with no edges"), CountLiveLinks(Graph), 0))
	{
		return Fail();
	}
	FString AssetShaBefore = ComputeAssetSha(Blueprint);
	FString EdgesFingerprintBefore =
		UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(Graph);
	const FString StateFingerprintBefore =
		UEAgentKitBlueprintPinDefaultExperimental::ComputeAtomicStateFingerprint(Graph);
	if (!TestTrue(TEXT("the fixture asset exists on disk"), !AssetShaBefore.IsEmpty())
		|| !TestTrue(
			TEXT("the edge fingerprint covers the focused graph"),
			!EdgesFingerprintBefore.IsEmpty()))
	{
		return Fail();
	}

	// ---- Fail-closed refusals: no write, no dirty package, no disk change. ----
	{
		UEdGraphPin* SourceOut = FindPin(NotBool1, TEXT("ReturnValue"), EGPD_Output);
		UEdGraphPin* SourceIn = FindPin(NotBool1, TEXT("A"), EGPD_Input);
		UEdGraphPin* BranchCondition = FindPin(Branch, TEXT("Condition"), EGPD_Input);
		UEdGraphPin* BranchExec = FindPin(Branch, TEXT("execute"), EGPD_Input);
		if (!TestNotNull(TEXT("fixture source output"), SourceOut)
			|| !TestNotNull(TEXT("fixture source input"), SourceIn)
			|| !TestNotNull(TEXT("fixture branch condition"), BranchCondition)
			|| !TestNotNull(TEXT("fixture branch exec"), BranchExec))
		{
			return Fail();
		}
		const FString NotBool1Guid = GuidLower(NotBool1->NodeGuid);

		struct FRefusal
		{
			const TCHAR* Label;
			TArray<TSharedPtr<FJsonObject>> Operations;
			const TCHAR* ExpectedCode;
		};
		const TArray<FRefusal> Refusals = {
			{
				TEXT("an empty tempId is refused"),
				{ MakeNodeOperation(TEXT("n1"), TEXT("branch"), 700, 0, TEXT("tmp:")) },
				TEXT("graph-edit-temp-id-invalid"),
			},
			{
				TEXT("an over-long tempId is refused"),
				{
					MakeNodeOperation(
						TEXT("n1"),
						TEXT("branch"),
						700,
						0,
						*FString::Printf(TEXT("tmp:%s"), *FString::ChrN(65, TEXT('a')))),
				},
				TEXT("graph-edit-temp-id-invalid"),
			},
			{
				TEXT("a tempId with a space is refused"),
				{ MakeNodeOperation(TEXT("n1"), TEXT("branch"), 700, 0, TEXT("tmp:a b")) },
				TEXT("graph-edit-temp-id-invalid"),
			},
			{
				TEXT("a duplicate tempId is refused"),
				{
					MakeNodeOperation(TEXT("n1"), TEXT("branch"), 700, 0, TEXT("tmp:dup")),
					MakeNodeOperation(TEXT("n2"), TEXT("sequence"), 700, 200, TEXT("tmp:dup")),
				},
				TEXT("graph-patch-duplicate-temp-id"),
			},
			{
				TEXT("an undeclared tempId is refused"),
				{
					MakeConnectOperation(
						TEXT("c1"),
						TEXT("tmp:missing"),
						TEXT("then"),
						NotBool1Guid,
						TEXT("A")),
				},
				TEXT("graph-patch-unknown-temp-id"),
			},
			{
				TEXT("a forward tempId reference is refused"),
				{
					MakeConnectOperation(
						TEXT("c1"),
						TEXT("tmp:later"),
						TEXT("then"),
						NotBool1Guid,
						TEXT("A")),
					MakeNodeOperation(TEXT("n1"), TEXT("branch"), 700, 0, TEXT("tmp:later")),
				},
				TEXT("graph-patch-forward-temp-id"),
			},
			{
				TEXT("a non-GUID, non-tempId endpoint is refused"),
				{
					MakeConnectOperation(
						TEXT("c1"),
						TEXT("not-a-guid"),
						TEXT("ReturnValue"),
						NotBool1Guid,
						TEXT("A")),
				},
				TEXT("graph-patch-invalid-identifier"),
			},
			{
				TEXT("a bool-to-exec edge is refused"),
				{
					MakeConnectOperation(
						TEXT("c1"),
						NotBool1Guid,
						TEXT("ReturnValue"),
						GuidLower(Branch->NodeGuid),
						TEXT("execute")),
				},
				TEXT("graph-edit-pin-type-mismatch"),
			},
			{
				TEXT("a self connection is refused"),
				{
					MakeConnectOperation(
						TEXT("c1"),
						NotBool1Guid,
						TEXT("ReturnValue"),
						NotBool1Guid,
						TEXT("A")),
				},
				TEXT("graph-edit-self-connection"),
			},
			{
				TEXT("two edges into one input are refused"),
				{
					MakeConnectOperation(
						TEXT("c1"),
						GuidLower(NotBool1->NodeGuid),
						TEXT("ReturnValue"),
						GuidLower(Branch->NodeGuid),
						TEXT("Condition")),
					MakeConnectOperation(
						TEXT("c2"),
						GuidLower(NotBool2->NodeGuid),
						TEXT("ReturnValue"),
						GuidLower(Branch->NodeGuid),
						TEXT("Condition")),
				},
				TEXT("graph-edit-connection-duplicate"),
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
						BuildPatchParams(Blueprint, Graph, Refusal.Operations),
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
		// `removeNode` is designed but still unmigrated.
		{
			TSharedRef<FJsonObject> Disconnect = MakeShared<FJsonObject>();
			Disconnect->SetStringField(TEXT("opId"), TEXT("r1"));
			Disconnect->SetStringField(TEXT("kind"), TEXT("removeNode"));
			TSharedPtr<FJsonObject> RefusedResult;
			FString RefusedCode;
			FString RefusedMessage;
			if (!TestFalse(
					TEXT("removeNode is still refused as unmigrated"),
					UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
						BuildPatchParams(Blueprint, Graph, { Disconnect }),
						RefusedResult,
						RefusedCode,
						RefusedMessage))
				|| !TestEqual(
					TEXT("removeNode refusal code"),
					RefusedCode,
					FString(TEXT("graph-patch-kind-not-migrated"))))
			{
				return Fail();
			}
		}
		if (!TestEqual(TEXT("refusals left the node count unchanged"), Graph->Nodes.Num(), NodeCountBefore)
			|| !TestEqual(TEXT("refusals left the edge set unchanged"), CountLiveLinks(Graph), 0)
			|| !TestFalse(TEXT("refusals left the package clean"), Package->IsDirty())
			|| !TestEqual(
				TEXT("refusals left the disk asset unchanged"),
				ComputeAssetSha(Blueprint),
				AssetShaBefore)
			|| !TestEqual(
				TEXT("refusals left the edge fingerprint unchanged"),
				UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(Graph),
				EdgesFingerprintBefore))
		{
			return Fail();
		}
	}

	// ---- Single-op equivalence with the single-writer existing-pin path. ----
	{
		UEdGraphPin* SourceOut = FindPin(NotBool1, TEXT("ReturnValue"), EGPD_Output);
		UEdGraphPin* BranchCondition = FindPin(Branch, TEXT("Condition"), EGPD_Input);
		const TSharedPtr<FJsonObject> FromExisting =
			MakeExistingPin(NotBool1, SourceOut, TEXT("output"), TEXT("bool"));
		const TSharedPtr<FJsonObject> ToExisting =
			MakeExistingPin(Branch, BranchCondition, TEXT("input"), TEXT("bool"));

		TSharedPtr<FJsonObject> WriterPreview;
		FString WriterCode;
		FString WriterMessage;
		if (!TestTrue(
				TEXT("the single-writer existing-pin preview succeeds"),
				UEAgentKitBlueprintGraphEditExperimental::TryPreviewExperimental(
					BuildGraphEditParams(Blueprint, Graph, FromExisting, ToExisting),
					WriterPreview,
					WriterCode,
					WriterMessage)))
		{
			AddError(WriterCode + TEXT(" ") + WriterMessage);
			return Fail();
		}
		TSharedPtr<FJsonObject> WriterApply;
		if (!TestTrue(
				TEXT("the single-writer existing-pin apply succeeds"),
				UEAgentKitBlueprintGraphEditExperimental::TryApplyExperimental(
					BuildGraphEditApplyParams(WriterPreview, FromExisting, ToExisting),
					WriterApply,
					WriterCode,
					WriterMessage)))
		{
			AddError(WriterCode + TEXT(" ") + WriterMessage);
			return Fail();
		}
		const FString WriterEdgeHash =
			UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(Graph);
		if (!TestTrue(TEXT("the writer created the edge"), AreLinked(SourceOut, BranchCondition)))
		{
			return Fail();
		}
		// The writer's preview entry is the reference contract (pin names/types).
		const TSharedPtr<FJsonObject> WriterConnection =
			WriterPreview->GetArrayField(TEXT("connections"))[0]->AsObject();
		if (!TestEqual(
				TEXT("the writer plan connects bool to bool"),
				WriterConnection->GetStringField(TEXT("fromType")),
				FString(TEXT("bool"))))
		{
			return Fail();
		}

		// Test setup: put the fixture back to its saved baseline (no edges).
		SourceOut->BreakLinkTo(BranchCondition);
		SourceOut->GetOwningNode()->PinConnectionListChanged(SourceOut);
		BranchCondition->GetOwningNode()->PinConnectionListChanged(BranchCondition);
		if (!TestEqual(TEXT("the probe edge was removed"), CountLiveLinks(Graph), 0))
		{
			return Fail();
		}
		Package->MarkPackageDirty();
		if (!TestTrue(TEXT("the cleaned fixture saves again"), SaveBlueprint(Blueprint))
			|| !TestFalse(TEXT("the cleaned fixture is clean"), Package->IsDirty()))
		{
			return Fail();
		}
		AssetShaBefore = ComputeAssetSha(Blueprint);
		EdgesFingerprintBefore =
			UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(Graph);

		const TArray<TSharedPtr<FJsonObject>> ConnectOps = {
			MakeConnectOperation(
				TEXT("c1"),
				GuidLower(NotBool1->NodeGuid),
				TEXT("ReturnValue"),
				GuidLower(Branch->NodeGuid),
				TEXT("Condition")),
		};
		TSharedPtr<FJsonObject> Preview;
		FString PreviewCode;
		FString PreviewMessage;
		if (!TestTrue(
				TEXT("the single connect patch previews"),
				UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
					BuildPatchParams(Blueprint, Graph, ConnectOps),
					Preview,
					PreviewCode,
					PreviewMessage))
			|| !TestTrue(TEXT("the connect preview exists"), Preview.IsValid()))
		{
			AddError(PreviewCode + TEXT(" ") + PreviewMessage);
			return Fail();
		}
		const TSharedPtr<FJsonObject> Planned =
			Preview->GetArrayField(TEXT("operations"))[0]->AsObject();
		if (!TestEqual(
				TEXT("the patch binds the live edge fingerprint"),
				Preview->GetStringField(TEXT("edgesFingerprint")),
				EdgesFingerprintBefore)
			|| !TestEqual(
				TEXT("the patch preview resolves the source node"),
				Planned->GetStringField(TEXT("fromNodeGuid")),
				GuidLower(NotBool1->NodeGuid))
			|| !TestEqual(
				TEXT("the patch preview resolves the target pin"),
				Planned->GetStringField(TEXT("toPin")),
				FString(TEXT("Condition")))
			|| !TestEqual(
				TEXT("the patch preview derives the same source type"),
				Planned->GetStringField(TEXT("fromType")),
				WriterConnection->GetStringField(TEXT("fromType")))
			|| !TestEqual(
				TEXT("the patch preview derives the same target type"),
				Planned->GetStringField(TEXT("toType")),
				WriterConnection->GetStringField(TEXT("toType")))
			|| !TestFalse(
				TEXT("the patch preview has no created edge GUID yet"),
				Planned->HasField(TEXT("guid"))))
		{
			return Fail();
		}

		TSharedPtr<FJsonObject> ApplyResult;
		FString ApplyCode;
		FString ApplyMessage;
		if (!TestTrue(
				TEXT("the single connect patch applies"),
				UEAgentKitBlueprintPatchExperimental::TryApplyExperimental(
					BuildPatchApplyParams(Preview, ConnectOps),
					ApplyResult,
					ApplyCode,
					ApplyMessage))
			|| !TestTrue(TEXT("the connect apply result exists"), ApplyResult.IsValid()))
		{
			AddError(ApplyCode + TEXT(" ") + ApplyMessage);
			return Fail();
		}
		const TSharedPtr<FJsonObject> Applied =
			ApplyResult->GetArrayField(TEXT("results"))[0]->AsObject();
		const FString EdgesFingerprintAfter =
			UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(Graph);
		if (!TestTrue(TEXT("the patch connected the exact pins"), AreLinked(SourceOut, BranchCondition))
			|| !TestEqual(TEXT("one edge exists"), CountLiveLinks(Graph), 1)
			|| !TestEqual(
				TEXT("the node count is unchanged by connect"),
				ApplyResult->GetNumberField(TEXT("nodeCountAfter")),
				static_cast<double>(NodeCountBefore))
			|| !TestEqual(
				TEXT("the link count delta is one"),
				ApplyResult->GetNumberField(TEXT("linkCountAfter")),
				static_cast<double>(1))
			|| !TestEqual(
				TEXT("createdEdgeCount is one"),
				ApplyResult->GetNumberField(TEXT("createdEdgeCount")),
				1.0)
			|| !TestEqual(
				TEXT("the patch's after edge fingerprint is the writer's edge hash"),
				ApplyResult->GetStringField(TEXT("edgesFingerprintAfter")),
				WriterEdgeHash)
			|| !TestEqual(
				TEXT("the reported after fingerprint is the live one"),
				ApplyResult->GetStringField(TEXT("edgesFingerprintAfter")),
				EdgesFingerprintAfter)
			|| !TestFalse(
				TEXT("the edge fingerprint changed"),
				EdgesFingerprintAfter.Equals(
					EdgesFingerprintBefore,
					ESearchCase::CaseSensitive))
			|| !TestEqual(
				TEXT("the applied result resolves the source node"),
				Applied->GetStringField(TEXT("fromNodeGuid")),
				GuidLower(NotBool1->NodeGuid))
			|| !TestEqual(
				TEXT("the applied result resolves the source pin GUID"),
				Applied->GetStringField(TEXT("fromPinGuid")),
				GuidLower(SourceOut->PinId))
			|| !TestEqual(
				TEXT("the applied result resolves the target node"),
				Applied->GetStringField(TEXT("toNodeGuid")),
				GuidLower(Branch->NodeGuid))
			|| !TestEqual(
				TEXT("the applied result resolves the target pin GUID"),
				Applied->GetStringField(TEXT("toPinGuid")),
				GuidLower(BranchCondition->PinId))
			|| !TestFalse(TEXT("the apply never saved"), ApplyResult->GetBoolField(TEXT("saved")))
			|| !TestEqual(
				TEXT("apply did not touch the disk"),
				ComputeAssetSha(Blueprint),
				AssetShaBefore))
		{
			return Fail();
		}

		// One patch = one Undo/Redo unit, proven on the edge fingerprint.
		if (!TestTrue(TEXT("the connect patch undoes"), GEditor != nullptr && GEditor->UndoTransaction())
			|| !TestEqual(TEXT("Undo removes the edge"), CountLiveLinks(Graph), 0)
			|| !TestEqual(
				TEXT("Undo returns the edge fingerprint to before"),
				UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(Graph),
				EdgesFingerprintBefore)
			|| !TestTrue(TEXT("the connect patch redoes"), GEditor != nullptr && GEditor->RedoTransaction())
			|| !TestEqual(TEXT("Redo restores the edge"), CountLiveLinks(Graph), 1)
			|| !TestEqual(
				TEXT("Redo returns the edge fingerprint to after"),
				UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(Graph),
				EdgesFingerprintAfter))
		{
			return Fail();
		}
		// Back to the no-edge baseline for the next block, clean on disk.
		if (!TestTrue(TEXT("undo back to the baseline"), GEditor != nullptr && GEditor->UndoTransaction()))
		{
			return Fail();
		}
		Package->MarkPackageDirty();
		if (!TestTrue(TEXT("the baseline saves"), SaveBlueprint(Blueprint))
			|| !TestFalse(TEXT("the baseline is clean"), Package->IsDirty()))
		{
			return Fail();
		}
		AssetShaBefore = ComputeAssetSha(Blueprint);
		EdgesFingerprintBefore =
			UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(Graph);
	}

	// ---- Atomic rollback: fail on operation 2, the first edge must disappear. ----
	const TArray<TSharedPtr<FJsonObject>> TwoEdgeOps = {
		MakeConnectOperation(
			TEXT("c1"),
			GuidLower(NotBool1->NodeGuid),
			TEXT("ReturnValue"),
			GuidLower(Branch->NodeGuid),
			TEXT("Condition")),
		MakeConnectOperation(
			TEXT("c2"),
			GuidLower(NotBool2->NodeGuid),
			TEXT("ReturnValue"),
			GuidLower(BoolOr->NodeGuid),
			TEXT("A")),
	};
	TSharedPtr<FJsonObject> TwoEdgePreview;
	{
		FString Code;
		FString Message;
		if (!TestTrue(
				TEXT("the two-edge patch previews"),
				UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
					BuildPatchParams(Blueprint, Graph, TwoEdgeOps),
					TwoEdgePreview,
					Code,
					Message))
			|| !TestTrue(TEXT("the two-edge preview exists"), TwoEdgePreview.IsValid()))
		{
			AddError(Code + TEXT(" ") + Message);
			return Fail();
		}
	}
	{
		UEAgentKitBlueprintPatchExperimental::SetNextApplyFaultForTest(1);
		TSharedPtr<FJsonObject> FailedResult;
		FString FailedCode;
		FString FailedMessage;
		if (!TestFalse(
				TEXT("the fault-injected connect patch fails"),
				UEAgentKitBlueprintPatchExperimental::TryApplyExperimental(
					BuildPatchApplyParams(TwoEdgePreview, TwoEdgeOps),
					FailedResult,
					FailedCode,
					FailedMessage))
			|| !TestTrue(TEXT("the failed apply result exists"), FailedResult.IsValid()))
		{
			return Fail();
		}
		const FString InjectedCode = FailedCode;
		const TArray<TSharedPtr<FJsonValue>>& Results =
			FailedResult->GetArrayField(TEXT("results"));
		if (!TestEqual(
				TEXT("the injected fault code is reported"),
				InjectedCode,
				FString(TEXT("graph-patch-apply-fault-injected")))
			|| !TestFalse(
				TEXT("a rolled-back patch reports writeOccurred=false"),
				FailedResult->GetBoolField(TEXT("writeOccurred")))
			|| !TestTrue(
				TEXT("a rolled-back patch reports transactionOpened=true"),
				FailedResult->GetBoolField(TEXT("transactionOpened")))
			|| !TestTrue(
				TEXT("the rollback reports stateRestored=true"),
				FailedResult->GetBoolField(TEXT("stateRestored")))
			|| !TestEqual(TEXT("one result per operation"), Results.Num(), 2)
			|| !TestEqual(
				TEXT("the first operation was applied before the fault"),
				Results[0]->AsObject()->GetStringField(TEXT("status")),
				FString(TEXT("applied")))
			|| !TestTrue(
				TEXT("the first operation is marked rolledBack"),
				Results[0]->AsObject()->GetBoolField(TEXT("rolledBack")))
			|| !TestEqual(
				TEXT("the second operation is the failed one"),
				Results[1]->AsObject()->GetStringField(TEXT("status")),
				FString(TEXT("failed"))))
		{
			return Fail();
		}
		// The first edge must be gone, the node count untouched.
		if (!TestEqual(TEXT("the rollback left no edge"), CountLiveLinks(Graph), 0)
			|| !TestEqual(TEXT("the rollback left the node count"), Graph->Nodes.Num(), NodeCountBefore)
			|| !TestEqual(
				TEXT("the rollback returned the edge fingerprint to before"),
				UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(Graph),
				EdgesFingerprintBefore)
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
		FString ReCode;
		FString ReMessage;
		if (!TestTrue(
				TEXT("the same connect plan can be previewed again"),
				UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
					BuildPatchParams(Blueprint, Graph, TwoEdgeOps),
					RePreview,
					ReCode,
					ReMessage))
			|| !TestEqual(
				TEXT("the planId is stable after the rollback"),
				RePreview->GetStringField(TEXT("planId")),
				TwoEdgePreview->GetStringField(TEXT("planId"))))
		{
			AddError(ReCode + TEXT(" ") + ReMessage);
			return Fail();
		}
	}

	// ---- The same two-edge patch now applies as one transaction. ----
	{
		TSharedPtr<FJsonObject> ApplyResult;
		FString Code;
		FString Message;
		if (!TestTrue(
				TEXT("the two-edge connect patch applies"),
				UEAgentKitBlueprintPatchExperimental::TryApplyExperimental(
					BuildPatchApplyParams(TwoEdgePreview, TwoEdgeOps),
					ApplyResult,
					Code,
					Message))
			|| !TestTrue(TEXT("the two-edge apply result exists"), ApplyResult.IsValid()))
		{
			AddError(Code + TEXT(" ") + Message);
			return Fail();
		}
		const FString EdgesFingerprintAfter =
			UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(Graph);
		if (!TestTrue(TEXT("two edges exist"), CountLiveLinks(Graph) == 2)
			|| !TestTrue(
				TEXT("the first edge is the planned one"),
				AreLinked(
					FindPin(NotBool1, TEXT("ReturnValue"), EGPD_Output),
					FindPin(Branch, TEXT("Condition"), EGPD_Input)))
			|| !TestTrue(
				TEXT("the second edge is the planned one"),
				AreLinked(
					FindPin(NotBool2, TEXT("ReturnValue"), EGPD_Output),
					FindPin(BoolOr, TEXT("A"), EGPD_Input)))
			|| !TestTrue(
				TEXT("undo/redo verified in the apply"),
				ApplyResult->GetBoolField(TEXT("undoVerified"))
					&& ApplyResult->GetBoolField(TEXT("redoVerified")))
			|| !TestEqual(
				TEXT("the edge fingerprint changed"),
				EdgesFingerprintAfter.Equals(
					EdgesFingerprintBefore,
					ESearchCase::CaseSensitive)
					? 0
					: 1,
				1)
			|| !TestEqual(
				TEXT("the two results resolve different targets"),
				ApplyResult->GetArrayField(TEXT("results"))[0]->AsObject()->GetStringField(TEXT("toNodeGuid")) ==
						ApplyResult->GetArrayField(TEXT("results"))[1]->AsObject()->GetStringField(TEXT("toNodeGuid"))
					? 0
					: 1,
				1))
		{
			return Fail();
		}
		if (!TestTrue(TEXT("the two-edge patch undoes"), GEditor != nullptr && GEditor->UndoTransaction())
			|| !TestEqual(TEXT("Undo removes both edges"), CountLiveLinks(Graph), 0)
			|| !TestEqual(
				TEXT("Undo returns the edge fingerprint to before"),
				UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(Graph),
				EdgesFingerprintBefore))
		{
			return Fail();
		}
		Package->MarkPackageDirty();
		if (!TestTrue(TEXT("the baseline saves again"), SaveBlueprint(Blueprint))
			|| !TestFalse(TEXT("the baseline is clean again"), Package->IsDirty()))
		{
			return Fail();
		}
		AssetShaBefore = ComputeAssetSha(Blueprint);
		EdgesFingerprintBefore =
			UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(Graph);
	}

	// ---- Build and wire in one patch (createNode + connect with tempIds). ----
	{
		const TArray<TSharedPtr<FJsonObject>> BuildAndWire = {
			MakeNodeOperation(TEXT("n1"), TEXT("not_bool"), 0, 420, TEXT("tmp:src")),
			MakeNodeOperation(TEXT("n2"), TEXT("branch"), 340, 420, TEXT("tmp:dst")),
			MakeConnectOperation(
				TEXT("c1"),
				TEXT("tmp:src"),
				TEXT("ReturnValue"),
				TEXT("tmp:dst"),
				TEXT("Condition")),
		};
		TSharedPtr<FJsonObject> Preview;
		FString Code;
		FString Message;
		if (!TestTrue(
				TEXT("the build-and-wire patch previews"),
				UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
					BuildPatchParams(Blueprint, Graph, BuildAndWire),
					Preview,
					Code,
					Message))
			|| !TestTrue(TEXT("the build-and-wire preview exists"), Preview.IsValid()))
		{
			AddError(Code + TEXT(" ") + Message);
			return Fail();
		}
		// The preview can only name the endpoint by its tempId: the node and its
		// pin GUIDs do not exist yet.
		const TSharedPtr<FJsonObject> PlannedConnect =
			Preview->GetArrayField(TEXT("operations"))[2]->AsObject();
		if (!TestEqual(
				TEXT("the preview keeps the tempId endpoint"),
				PlannedConnect->GetStringField(TEXT("fromTempId")),
				FString(TEXT("tmp:src")))
			|| !TestFalse(
				TEXT("the preview has no source node GUID yet"),
				PlannedConnect->HasField(TEXT("fromNodeGuid")))
			|| !TestTrue(
				TEXT("the preview reports the connect as validated"),
				PlannedConnect->GetStringField(TEXT("status")) == TEXT("validated")))
		{
			return Fail();
		}
		TSharedPtr<FJsonObject> ApplyResult;
		if (!TestTrue(
				TEXT("the build-and-wire patch applies"),
				UEAgentKitBlueprintPatchExperimental::TryApplyExperimental(
					BuildPatchApplyParams(Preview, BuildAndWire),
					ApplyResult,
					Code,
					Message))
			|| !TestTrue(TEXT("the build-and-wire result exists"), ApplyResult.IsValid()))
		{
			AddError(Code + TEXT(" ") + Message);
			return Fail();
		}
		const TArray<TSharedPtr<FJsonValue>>& Results =
			ApplyResult->GetArrayField(TEXT("results"));
		const FString SourceGuid =
			Results[0]->AsObject()->GetStringField(TEXT("nodeGuid"));
		const FString TargetGuid =
			Results[1]->AsObject()->GetStringField(TEXT("nodeGuid"));
		UEdGraphNode* CreatedSource = FindNodeByGuid(Graph, SourceGuid);
		UEdGraphNode* CreatedTarget = FindNodeByGuid(Graph, TargetGuid);
		if (!TestNotNull(TEXT("the source node exists"), CreatedSource)
			|| !TestNotNull(TEXT("the target node exists"), CreatedTarget)
			|| !TestEqual(
				TEXT("the connect resolved its tempId source to the created node"),
				Results[2]->AsObject()->GetStringField(TEXT("fromNodeGuid")),
				SourceGuid)
			|| !TestEqual(
				TEXT("the connect resolved its tempId target to the created node"),
				Results[2]->AsObject()->GetStringField(TEXT("toNodeGuid")),
				TargetGuid)
			|| !TestTrue(
				TEXT("the created nodes are wired together"),
				AreLinked(
					FindPin(CreatedSource, TEXT("ReturnValue"), EGPD_Output),
					FindPin(CreatedTarget, TEXT("Condition"), EGPD_Input)))
			|| !TestEqual(
				TEXT("the patch added two nodes and one edge"),
				ApplyResult->GetNumberField(TEXT("nodeCountAfter")),
				static_cast<double>(NodeCountBefore + 2))
			|| !TestEqual(
				TEXT("the patch added exactly one edge"),
				ApplyResult->GetNumberField(TEXT("linkCountAfter")),
				1.0)
			|| !TestEqual(
				TEXT("the applied count covers all three operations"),
				ApplyResult->GetNumberField(TEXT("appliedOperationCount")),
				3.0))
		{
			return Fail();
		}
		// One Undo removes the edge and both nodes: one transaction, one unit.
		if (!TestTrue(TEXT("the build-and-wire patch undoes"), GEditor != nullptr && GEditor->UndoTransaction())
			|| !TestEqual(TEXT("Undo removes both created nodes"), Graph->Nodes.Num(), NodeCountBefore)
			|| !TestEqual(TEXT("Undo removes the edge"), CountLiveLinks(Graph), 0)
			|| !TestEqual(
				TEXT("Undo returns the edge fingerprint to before"),
				UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(Graph),
				EdgesFingerprintBefore))
		{
			return Fail();
		}
		Package->MarkPackageDirty();
		if (!TestTrue(TEXT("the baseline saves after build-and-wire"), SaveBlueprint(Blueprint))
			|| !TestFalse(TEXT("the baseline is clean again"), Package->IsDirty()))
		{
			return Fail();
		}
		AssetShaBefore = ComputeAssetSha(Blueprint);
		EdgesFingerprintBefore =
			UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(Graph);
	}

	// ---- Four migrated kinds in one patch, then the explicit Save. ----
	{
		UEdGraphPin* SourceIn = FindPin(NotBool1, TEXT("A"), EGPD_Input);
		if (!TestNotNull(TEXT("the fixture bool input exists"), SourceIn))
		{
			return Fail();
		}
		TSharedRef<FJsonObject> PinOperation = MakeShared<FJsonObject>();
		PinOperation->SetStringField(TEXT("opId"), TEXT("p1"));
		PinOperation->SetStringField(TEXT("kind"), TEXT("setPinDefault"));
		PinOperation->SetStringField(TEXT("nodeGuid"), GuidLower(NotBool1->NodeGuid));
		PinOperation->SetStringField(TEXT("pinName"), TEXT("A"));
		PinOperation->SetStringField(TEXT("pinGuid"), GuidLower(SourceIn->PinId));
		PinOperation->SetStringField(TEXT("type"), TEXT("bool"));
		PinOperation->SetStringField(TEXT("value"), TEXT("true"));

		TSharedRef<FJsonObject> VariableOperation = MakeShared<FJsonObject>();
		VariableOperation->SetStringField(TEXT("opId"), TEXT("v1"));
		VariableOperation->SetStringField(TEXT("kind"), TEXT("createVariable"));
		VariableOperation->SetStringField(TEXT("name"), TEXT("ConnectPatchFlag"));
		VariableOperation->SetStringField(TEXT("type"), TEXT("bool"));
		VariableOperation->SetStringField(TEXT("defaultValue"), TEXT("true"));

		const TArray<TSharedPtr<FJsonObject>> Mixed = {
			PinOperation,
			VariableOperation,
			MakeNodeOperation(TEXT("n1"), TEXT("branch"), 700, 420, TEXT("tmp:newbranch")),
			MakeConnectOperation(
				TEXT("c1"),
				GuidLower(NotBool1->NodeGuid),
				TEXT("ReturnValue"),
				TEXT("tmp:newbranch"),
				TEXT("Condition")),
		};
		const int32 VariableCountBefore = Blueprint->NewVariables.Num();
		TSharedPtr<FJsonObject> Preview;
		FString Code;
		FString Message;
		if (!TestTrue(
				TEXT("the four-kind patch previews"),
				UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
					BuildPatchParams(Blueprint, Graph, Mixed),
					Preview,
					Code,
					Message))
			|| !TestTrue(TEXT("the four-kind preview exists"), Preview.IsValid()))
		{
			AddError(Code + TEXT(" ") + Message);
			return Fail();
		}
		const TArray<TSharedPtr<FJsonValue>>& PlannedOps =
			Preview->GetArrayField(TEXT("operations"));
		if (!TestEqual(TEXT("four operations planned"), PlannedOps.Num(), 4)
			|| !TestEqual(
				TEXT("the four kinds keep their order"),
				FString::Printf(
					TEXT("%s,%s,%s,%s"),
					*PlannedOps[0]->AsObject()->GetStringField(TEXT("kind")),
					*PlannedOps[1]->AsObject()->GetStringField(TEXT("kind")),
					*PlannedOps[2]->AsObject()->GetStringField(TEXT("kind")),
					*PlannedOps[3]->AsObject()->GetStringField(TEXT("kind"))),
				FString(TEXT("setPinDefault,createVariable,createNode,connect"))))
		{
			return Fail();
		}
		TSharedPtr<FJsonObject> ApplyResult;
		if (!TestTrue(
				TEXT("the four-kind patch applies"),
				UEAgentKitBlueprintPatchExperimental::TryApplyExperimental(
					BuildPatchApplyParams(Preview, Mixed),
					ApplyResult,
					Code,
					Message))
			|| !TestTrue(TEXT("the four-kind result exists"), ApplyResult.IsValid()))
		{
			AddError(Code + TEXT(" ") + Message);
			return Fail();
		}
		bool bAllApplied = ApplyResult->GetArrayField(TEXT("results")).Num() == 4;
		for (const TSharedPtr<FJsonValue>& Value : ApplyResult->GetArrayField(TEXT("results")))
		{
			bAllApplied = bAllApplied
				&& Value->AsObject()->GetStringField(TEXT("status")) == TEXT("applied");
		}
		bool bPinIsTrue = false;
		for (const UEdGraphPin* Pin : NotBool1->Pins)
		{
			if (Pin != nullptr && Pin->Direction == EGPD_Input && Pin->PinName == FName(TEXT("A")))
			{
				bPinIsTrue = Pin->DefaultValue == TEXT("true");
			}
		}
		if (!TestTrue(TEXT("all four kinds applied"), bAllApplied)
			|| !TestTrue(TEXT("the pin default landed"), bPinIsTrue)
			|| !TestEqual(
				TEXT("the variable was created"),
				Blueprint->NewVariables.Num(),
				VariableCountBefore + 1)
			|| !TestEqual(
				TEXT("one node and one edge were created"),
				ApplyResult->GetNumberField(TEXT("nodeCountAfter")),
				static_cast<double>(NodeCountBefore + 1))
			|| !TestEqual(
				TEXT("the mixed patch added exactly one edge"),
				ApplyResult->GetNumberField(TEXT("linkCountAfter")),
				1.0)
			|| !TestTrue(
				TEXT("the mixed patch is one verified Undo unit"),
				ApplyResult->GetBoolField(TEXT("undoVerified"))
					&& ApplyResult->GetBoolField(TEXT("redoVerified"))))
		{
			return Fail();
		}

		// A dirty package refuses the next patch; Save is the only disk write.
		TSharedPtr<FJsonObject> DirtyResult;
		FString DirtyCode;
		FString DirtyMessage;
		if (!TestFalse(
				TEXT("another patch is refused on a dirty package"),
				UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
					BuildPatchParams(
						Blueprint,
						Graph,
						{ MakeNodeOperation(TEXT("n9"), TEXT("sequence"), 1000, 0) }),
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
				TEXT("the explicit four-kind save succeeds"),
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
				TEXT("save reported the edge fingerprint of the applied state"),
				SaveResult->GetStringField(TEXT("edgesFingerprint")),
				ApplyResult->GetStringField(TEXT("edgesFingerprintAfter")))
			|| !TestEqual(
				TEXT("save reported the exact asset hash on disk"),
				SaveResult->GetStringField(TEXT("assetSha256")),
				AssetShaAfterSave)
			|| !TestFalse(
				TEXT("the explicit save changed the asset"),
				AssetShaAfterSave.Equals(AssetShaBefore, ESearchCase::CaseSensitive)))
		{
			return Fail();
		}
		// The saved edge set is the new baseline a fresh preview must see.
		TSharedPtr<FJsonObject> AfterSavePreview;
		FString AfterSaveCode;
		FString AfterSaveMessage;
		if (!TestTrue(
				TEXT("a connect patch can be previewed on the saved state"),
				UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
					BuildPatchParams(
						Blueprint,
						Graph,
						{
							MakeConnectOperation(
								TEXT("c9"),
								GuidLower(NotBool2->NodeGuid),
								TEXT("ReturnValue"),
								GuidLower(BoolOr->NodeGuid),
								TEXT("A")),
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
				TEXT("the post-save preview sees the saved edge set"),
				AfterSavePreview->GetStringField(TEXT("edgesFingerprint")),
				ApplyResult->GetStringField(TEXT("edgesFingerprintAfter"))))
		{
			return Fail();
		}
		if (!TestEqual(
				TEXT("the save readback reports the saved link count"),
				SaveResult->GetObjectField(TEXT("readback"))->GetNumberField(TEXT("linkCount")),
				1.0)
			|| !TestEqual(
				TEXT("the save readback reports the saved node count"),
				SaveResult->GetObjectField(TEXT("readback"))->GetNumberField(TEXT("nodeCount")),
				static_cast<double>(NodeCountBefore + 1)))
		{
			return Fail();
		}
	}

	// ---- Reverse case: swap endpoints without changing any per-pin count. ----
	{
		const TArray<TSharedPtr<FJsonObject>> BuildAndTwoEdges = {
			MakeNodeOperation(TEXT("n1"), TEXT("not_bool"), 0, 640, TEXT("tmp:src1")),
			MakeNodeOperation(TEXT("n2"), TEXT("not_bool"), 0, 860, TEXT("tmp:src2")),
			MakeConnectOperation(
				TEXT("c1"),
				TEXT("tmp:src1"),
				TEXT("ReturnValue"),
				GuidLower(Branch->NodeGuid),
				TEXT("Condition")),
			MakeConnectOperation(
				TEXT("c2"),
				TEXT("tmp:src2"),
				TEXT("ReturnValue"),
				GuidLower(BoolOr->NodeGuid),
				TEXT("A")),
		};
		TSharedPtr<FJsonObject> Preview;
		FString Code;
		FString Message;
		if (!TestTrue(
				TEXT("the reverse-probe patch previews"),
				UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
					BuildPatchParams(Blueprint, Graph, BuildAndTwoEdges),
					Preview,
					Code,
					Message))
			|| !TestTrue(TEXT("the reverse-probe preview exists"), Preview.IsValid()))
		{
			AddError(Code + TEXT(" ") + Message);
			return Fail();
		}
		const FString EdgesBeforeProbe =
			UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(Graph);
		TSharedPtr<FJsonObject> ApplyResult;
		if (!TestTrue(
				TEXT("the reverse-probe patch applies"),
				UEAgentKitBlueprintPatchExperimental::TryApplyExperimental(
					BuildPatchApplyParams(Preview, BuildAndTwoEdges),
					ApplyResult,
					Code,
					Message))
			|| !TestTrue(TEXT("the reverse-probe result exists"), ApplyResult.IsValid()))
		{
			AddError(Code + TEXT(" ") + Message);
			return Fail();
		}
		const TArray<TSharedPtr<FJsonValue>>& Results =
			ApplyResult->GetArrayField(TEXT("results"));
		UEdGraphNode* Source1 = FindNodeByGuid(Graph, Results[0]->AsObject()->GetStringField(TEXT("nodeGuid")));
		UEdGraphNode* Source2 = FindNodeByGuid(Graph, Results[1]->AsObject()->GetStringField(TEXT("nodeGuid")));
		UEdGraphPin* Out1 = FindPin(Source1, TEXT("ReturnValue"), EGPD_Output);
		UEdGraphPin* Out2 = FindPin(Source2, TEXT("ReturnValue"), EGPD_Output);
		UEdGraphPin* BranchCondition = FindPin(Branch, TEXT("Condition"), EGPD_Input);
		UEdGraphPin* BoolOrA = FindPin(BoolOr, TEXT("A"), EGPD_Input);
		if (!TestTrue(
				TEXT("the probe edges exist"),
				AreLinked(Out1, BranchCondition) && AreLinked(Out2, BoolOrA)))
		{
			return Fail();
		}
		const FString EdgesAfterProbe =
			UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(Graph);
		const FString StateAfterProbe =
			UEAgentKitBlueprintPinDefaultExperimental::ComputeAtomicStateFingerprint(Graph);
		// The saved baseline already carries the mixed patch's single edge, so the
		// pre-swap count is that one plus the two probe edges.
		const int32 LinksAfterProbe = CountLiveLinks(Graph);
		if (!TestFalse(
				TEXT("creating two edges changed the edge fingerprint"),
				EdgesAfterProbe.Equals(EdgesBeforeProbe, ESearchCase::CaseSensitive)))
		{
			return Fail();
		}

		// Swap the two targets: every pin keeps exactly one link, only the
		// endpoints change.  This is the case the pin-default hash cannot see.
		Out1->BreakLinkTo(BranchCondition);
		Out2->BreakLinkTo(BoolOrA);
		if (!TestTrue(
				TEXT("the swap recreates both edges"),
				Graph->GetSchema()->TryCreateConnection(Out1, BoolOrA)
					&& Graph->GetSchema()->TryCreateConnection(Out2, BranchCondition)))
		{
			return Fail();
		}
		if (!TestTrue(
				TEXT("the swapped edges exist"),
				AreLinked(Out1, BoolOrA) && AreLinked(Out2, BranchCondition))
			|| !TestEqual(TEXT("the link count is unchanged by the swap"), CountLiveLinks(Graph), LinksAfterProbe)
			|| !TestEqual(
				TEXT("the edge fingerprint changes when only the endpoints change"),
				UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(Graph)
						.Equals(EdgesAfterProbe, ESearchCase::CaseSensitive)
					? 0
					: 1,
				1)
			|| !TestTrue(
				TEXT("the pin-default fingerprint cannot see the endpoint swap (documented blind spot)"),
				UEAgentKitBlueprintPinDefaultExperimental::ComputeAtomicStateFingerprint(Graph)
					.Equals(StateAfterProbe, ESearchCase::CaseSensitive)))
		{
			return Fail();
		}
	}

	UEAgentKitBlueprintPatchExperimental::ResetExperimentalWriteForTest();
	UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
	return true;
}

namespace UEAgentKitPatchEdgeOps
{
	using namespace UEAgentKitPatchConnect;

	const TCHAR* const AssetPath =
		TEXT("/Game/UEAgentKitPatchExpansion/BP_PatchEdgeOps.BP_PatchEdgeOps");
	const TCHAR* const PackageName = TEXT("/Game/UEAgentKitPatchExpansion/BP_PatchEdgeOps");
	const TCHAR* const AssetName = TEXT("BP_PatchEdgeOps");

	void RemoveStaleFiles()
	{
		const FString Filename = FPackageName::LongPackageNameToFilename(
			PackageName,
			FPackageName::GetAssetPackageExtension());
		for (const FString& Candidate :
			{ Filename,
				FPaths::ChangeExtension(Filename, TEXT("uexp")),
				FPaths::ChangeExtension(Filename, TEXT("ubulk")) })
		{
			if (IFileManager::Get().FileExists(*Candidate))
			{
				IFileManager::Get().Delete(*Candidate, false, true, true);
			}
		}
	}

	/**
	 * Four nodes with exactly two saved edges:
	 * Not_PreBool(0,0) -> Branch.Condition and Not_PreBool(0,200) -> BooleanOR.A.
	 * BooleanOR.B, Branch.execute and every Not_PreBool input start unlinked, so a
	 * test can name an edge that does not exist without creating one.
	 */
	UBlueprint* CreateEdgeFixture(FString& OutError)
	{
		if (FindObject<UBlueprint>(nullptr, AssetPath) != nullptr)
		{
			OutError = TEXT("Edge-op fixture already exists; use a fresh project.");
			return nullptr;
		}
		RemoveStaleFiles();
		UPackage* Package = CreatePackage(PackageName);
		UBlueprint* Blueprint = FKismetEditorUtilities::CreateBlueprint(
			AActor::StaticClass(),
			Package,
			FName(AssetName),
			BPTYPE_Normal,
			FName(TEXT("UEAgentKitPatchEdgeOps")));
		if (Blueprint == nullptr || Blueprint->UbergraphPages.IsEmpty())
		{
			OutError = TEXT("Could not create the edge-op fixture Blueprint.");
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
		if (Branch == nullptr)
		{
			OutError = TEXT("Could not create the fixture branch node.");
			return nullptr;
		}
		Graph->AddNode(Branch, true, false);
		Branch->AllocateDefaultPins();
		if (!Branch->NodeGuid.IsValid())
		{
			Branch->CreateNewGuid();
		}
		Branch->NodePosX = 340;
		Branch->NodePosY = 0;
		if (AddCallFunctionNode(Graph, UKismetMathLibrary::StaticClass(), TEXT("Not_PreBool"), 0, 0) == nullptr
			|| AddCallFunctionNode(Graph, UKismetMathLibrary::StaticClass(), TEXT("Not_PreBool"), 0, 200) == nullptr
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
			OutError = TEXT("Edge-op fixture failed to compile.");
			return nullptr;
		}
		UEdGraphPin* Not1Out = FindPin(FindNodeAt(Graph, 0, 0), TEXT("ReturnValue"), EGPD_Output);
		UEdGraphPin* Not2Out = FindPin(FindNodeAt(Graph, 0, 200), TEXT("ReturnValue"), EGPD_Output);
		UEdGraphPin* Condition = FindPin(FindNodeAt(Graph, 340, 0), TEXT("Condition"), EGPD_Input);
		UEdGraphPin* OrA = FindPin(FindNodeAt(Graph, 340, 200), TEXT("A"), EGPD_Input);
		if (Not1Out == nullptr || Not2Out == nullptr || Condition == nullptr || OrA == nullptr
			|| !Graph->GetSchema()->TryCreateConnection(Not1Out, Condition)
			|| !Graph->GetSchema()->TryCreateConnection(Not2Out, OrA))
		{
			OutError = TEXT("Could not create the fixture edges.");
			return nullptr;
		}
		FAssetRegistryModule::AssetCreated(Blueprint);
		Package->MarkPackageDirty();
		if (!SaveBlueprint(Blueprint))
		{
			OutError = TEXT("Could not save the edge-op fixture.");
			return nullptr;
		}
		return Blueprint;
	}

	void SetRewireSide(const TSharedRef<FJsonObject>& Operation, const TCHAR* Prefix, UEdGraphPin* Pin)
	{
		Operation->SetStringField(
			FString(Prefix) + TEXT("NodeGuid"),
			GuidLower(Pin->GetOwningNode()->NodeGuid));
		Operation->SetStringField(
			FString(Prefix) + TEXT("PinName"),
			Pin->PinName.ToString());
		Operation->SetStringField(FString(Prefix) + TEXT("PinGuid"), GuidLower(Pin->PinId));
	}

	TSharedPtr<FJsonObject> MakeRewireOperation(
		const TCHAR* OpId,
		UEdGraphPin* Target,
		UEdGraphPin* OldSource,
		UEdGraphPin* NewSource)
	{
		TSharedRef<FJsonObject> Operation = MakeShared<FJsonObject>();
		Operation->SetStringField(TEXT("opId"), OpId);
		Operation->SetStringField(TEXT("kind"), TEXT("rewirePin"));
		SetRewireSide(Operation, TEXT("target"), Target);
		SetRewireSide(Operation, TEXT("oldSource"), OldSource);
		SetRewireSide(Operation, TEXT("newSource"), NewSource);
		return Operation;
	}

	TSharedPtr<FJsonObject> MakeDisconnectOperation(
		const TCHAR* OpId,
		const FString& FromRef,
		const TCHAR* FromPin,
		const FString& ToRef,
		const TCHAR* ToPin)
	{
		TSharedPtr<FJsonObject> Operation =
			MakeConnectOperation(OpId, FromRef, FromPin, ToRef, ToPin);
		Operation->SetStringField(TEXT("kind"), TEXT("disconnect"));
		return Operation;
	}

	TSharedPtr<FJsonObject> MakeDisconnectOperation(
		const TCHAR* OpId,
		UEdGraphPin* From,
		UEdGraphPin* To)
	{
		return MakeDisconnectOperation(
			OpId,
			GuidLower(From->GetOwningNode()->NodeGuid),
			*From->PinName.ToString(),
			GuidLower(To->GetOwningNode()->NodeGuid),
			*To->PinName.ToString());
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
	FUEAgentKitPatchEdgeOpsTest,
	"UEAgentKit.AtomicPatch.EdgeOps",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitPatchEdgeOpsTest::RunTest(const FString& Parameters)
{
	using namespace UEAgentKitPatchConnect;
	using namespace UEAgentKitPatchEdgeOps;

	UEAgentKitBlueprintPatchExperimental::ResetExperimentalWriteForTest();
	UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
	UEAgentKitBlueprintRewireExperimental::ResetExperimentalWriteForTest();
	if (!TestFalse(
			TEXT("the atomic patch gate stays off unless the project opts in"),
			UEAgentKitBlueprintPatchExperimental::IsExperimentalWriteAvailable()))
	{
		return false;
	}
	UEAgentKitBlueprintPatchExperimental::SetExperimentalWriteEnabledForTest(true);
	UEAgentKitBlueprintGraphEditExperimental::SetExperimentalWriteEnabledForTest(true);
	UEAgentKitBlueprintRewireExperimental::SetExperimentalWriteEnabledForTest(true);
	auto Fail = []
	{
		UEAgentKitBlueprintPatchExperimental::ResetExperimentalWriteForTest();
		UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
		UEAgentKitBlueprintRewireExperimental::ResetExperimentalWriteForTest();
		return false;
	};

	FString Error;
	UBlueprint* Blueprint = CreateEdgeFixture(Error);
	if (!TestNotNull(TEXT("edge-op fixture is created"), Blueprint))
	{
		AddError(Error);
		return Fail();
	}
	UEdGraph* Graph = Blueprint->UbergraphPages[0];
	UPackage* Package = Blueprint->GetOutermost();
	if (!TestNotNull(TEXT("edge-op fixture graph is focused"), OpenAndFocus(Blueprint, Graph, Error)))
	{
		AddError(Error);
		return Fail();
	}
	UEdGraphNode* Not1 = FindNodeAt(Graph, 0, 0);
	UEdGraphNode* Not2 = FindNodeAt(Graph, 0, 200);
	UEdGraphNode* Branch = FindNodeAt(Graph, 340, 0);
	UEdGraphNode* BoolOr = FindNodeAt(Graph, 340, 200);
	UEdGraphPin* Not1Out = FindPin(Not1, TEXT("ReturnValue"), EGPD_Output);
	UEdGraphPin* Not2Out = FindPin(Not2, TEXT("ReturnValue"), EGPD_Output);
	UEdGraphPin* Not1In = FindPin(Not1, TEXT("A"), EGPD_Input);
	UEdGraphPin* BranchCondition = FindPin(Branch, TEXT("Condition"), EGPD_Input);
	UEdGraphPin* BranchExec = FindPin(Branch, TEXT("execute"), EGPD_Input);
	UEdGraphPin* BranchThen = FindPin(Branch, TEXT("then"), EGPD_Output);
	UEdGraphPin* OrA = FindPin(BoolOr, TEXT("A"), EGPD_Input);
	UEdGraphPin* OrB = FindPin(BoolOr, TEXT("B"), EGPD_Input);
	if (!TestTrue(
			TEXT("the fixture starts with exactly the two planned edges"),
			AreLinked(Not1Out, BranchCondition) && AreLinked(Not2Out, OrA))
		|| !TestNotNull(TEXT("fixture output pin"), Not1Out)
		|| !TestNotNull(TEXT("fixture second output pin"), Not2Out)
		|| !TestNotNull(TEXT("fixture bool input pin"), Not1In)
		|| !TestNotNull(TEXT("fixture branch condition"), BranchCondition)
		|| !TestNotNull(TEXT("fixture branch exec input"), BranchExec)
		|| !TestNotNull(TEXT("fixture branch exec output"), BranchThen)
		|| !TestNotNull(TEXT("fixture or A"), OrA)
		|| !TestNotNull(TEXT("fixture or B"), OrB))
	{
		return Fail();
	}
	const int32 NodeCountBefore = Graph->Nodes.Num();
	const FString AssetShaBefore = ComputeAssetSha(Blueprint);
	const FString EdgesFingerprintBefore =
		UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(Graph);
	if (!TestEqual(TEXT("the fixture has exactly four nodes"), NodeCountBefore, 4)
		|| !TestEqual(TEXT("the fixture starts with two edges"), CountLiveLinks(Graph), 2))
	{
		return Fail();
	}

	// ---- Fail-closed refusals: one table, no write, no dirty package. ----
	{
		TSharedPtr<FJsonObject> AbsentNodeRewire =
			MakeRewireOperation(TEXT("r1"), BranchCondition, Not2Out, Not1Out);
		AbsentNodeRewire->SetStringField(
			TEXT("newSourceNodeGuid"),
			TEXT("00000000-0000-0000-0000-000000000000"));
		struct FRefusal
		{
			const TCHAR* Label;
			TArray<TSharedPtr<FJsonObject>> Operations;
			const TCHAR* ExpectedCode;
		};
		const TArray<FRefusal> Refusals = {
			{
				TEXT("disconnect of an edge that is not there"),
				{ MakeDisconnectOperation(TEXT("d1"), Not1Out, OrB) },
				TEXT("graph-edit-disconnect-edge-not-found"),
			},
			{
				TEXT("disconnect of an edge to a node this patch creates"),
				{
					MakeNodeOperation(TEXT("n1"), TEXT("branch"), 700, 0, TEXT("tmp:new")),
					MakeDisconnectOperation(
						TEXT("d1"),
						GuidLower(Not1->NodeGuid),
						TEXT("ReturnValue"),
						TEXT("tmp:new"),
						TEXT("Condition")),
				},
				TEXT("graph-edit-disconnect-edge-not-found"),
			},
			{
				TEXT("disconnect whose source is an input pin"),
				{ MakeDisconnectOperation(TEXT("d1"), BranchCondition, Not1In) },
				TEXT("graph-edit-pin-not-found"),
			},
			{
				TEXT("disconnect across incompatible pin kinds"),
				{ MakeDisconnectOperation(TEXT("d1"), Not1Out, BranchExec) },
				TEXT("graph-edit-pin-type-mismatch"),
			},
			{
				TEXT("disconnect of a self connection"),
				{ MakeDisconnectOperation(TEXT("d1"), Not1Out, Not1In) },
				TEXT("graph-edit-self-connection"),
			},
			{
				TEXT("rewire whose declared old source is not the current one"),
				{ MakeRewireOperation(TEXT("r1"), BranchCondition, Not2Out, Not1Out) },
				TEXT("graph-rewire-old-edge-mismatch"),
			},
			{
				TEXT("rewire of an unlinked target"),
				{ MakeRewireOperation(TEXT("r1"), OrB, Not2Out, Not1Out) },
				TEXT("graph-rewire-target-unlinked"),
			},
			{
				TEXT("rewire onto the current source"),
				{ MakeRewireOperation(TEXT("r1"), BranchCondition, Not1Out, Not1Out) },
				TEXT("graph-rewire-no-op"),
			},
			{
				TEXT("rewire across incompatible pin kinds"),
				{ MakeRewireOperation(TEXT("r1"), OrA, Not2Out, BranchThen) },
				TEXT("graph-rewire-pin-kind-mismatch"),
			},
			{
				TEXT("rewire onto a node outside the graph"),
				{ AbsentNodeRewire },
				TEXT("graph-rewire-node-not-found"),
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
						BuildPatchParams(Blueprint, Graph, Refusal.Operations),
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
			|| !TestEqual(TEXT("refusals left the edge count"), CountLiveLinks(Graph), 2)
			|| !TestFalse(TEXT("refusals left the package clean"), Package->IsDirty())
			|| !TestEqual(TEXT("refusals left the disk asset"), ComputeAssetSha(Blueprint), AssetShaBefore)
			|| !TestEqual(
				TEXT("refusals left the edge fingerprint"),
				UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(Graph),
				EdgesFingerprintBefore))
		{
			return Fail();
		}
	}

	// ---- The plan hash binds the live edge set, not just the operation text. ----
	const TArray<TSharedPtr<FJsonObject>> DisconnectOrA = {
		MakeDisconnectOperation(TEXT("d1"), Not2Out, OrA),
	};
	const FString PlanIdWithTwoEdges = [&]()
	{
		TSharedPtr<FJsonObject> Preview;
		FString Code;
		FString Message;
		if (!UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
				BuildPatchParams(Blueprint, Graph, DisconnectOrA),
				Preview,
				Code,
				Message))
		{
			AddError(Code + TEXT(" ") + Message);
			return FString();
		}
		TSharedPtr<FJsonObject> Again;
		FString AgainCode;
		FString AgainMessage;
		if (!UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
				BuildPatchParams(Blueprint, Graph, DisconnectOrA),
				Again,
				AgainCode,
				AgainMessage)
			|| !TestEqual(
				TEXT("the same request on an unchanged graph keeps its planId"),
				Again->GetStringField(TEXT("planId")),
				Preview->GetStringField(TEXT("planId"))))
		{
			return FString();
		}
		return Preview->GetStringField(TEXT("planId"));
	}();
	if (!TestEqual(
			TEXT("a disconnect preview produced a planId"),
			PlanIdWithTwoEdges.Len(),
			71))
	{
		return Fail();
	}

	// ---- Disconnect: the edge goes away, Undo brings it back, Save is explicit.
	{
		const TArray<TSharedPtr<FJsonObject>> Operations = {
			MakeDisconnectOperation(TEXT("d1"), Not1Out, BranchCondition),
		};
		TSharedPtr<FJsonObject> Preview;
		FString Code;
		FString Message;
		if (!TestTrue(
				TEXT("the disconnect patch previews"),
				UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
					BuildPatchParams(Blueprint, Graph, Operations),
					Preview,
					Code,
					Message))
			|| !TestTrue(TEXT("the disconnect preview exists"), Preview.IsValid()))
		{
			AddError(Code + TEXT(" ") + Message);
			return Fail();
		}
		TSharedPtr<FJsonObject> ApplyResult;
		if (!TestTrue(
				TEXT("the disconnect patch applies"),
				UEAgentKitBlueprintPatchExperimental::TryApplyExperimental(
					BuildPatchApplyParams(Preview, Operations),
					ApplyResult,
					Code,
					Message))
			|| !TestTrue(TEXT("the disconnect result exists"), ApplyResult.IsValid()))
		{
			AddError(Code + TEXT(" ") + Message);
			return Fail();
		}
		const TArray<TSharedPtr<FJsonValue>>& Results = ApplyResult->GetArrayField(TEXT("results"));
		const FString EdgesAfter = ApplyResult->GetStringField(TEXT("edgesFingerprintAfter"));
		if (!TestFalse(TEXT("the disconnected edge is gone"), AreLinked(Not1Out, BranchCondition))
			|| !TestEqual(TEXT("one edge is left"), CountLiveLinks(Graph), 1)
			|| !TestEqual(
				TEXT("the disconnect counts as a removed edge"),
				ApplyResult->GetNumberField(TEXT("removedEdgeCount")),
				1.0)
			|| !TestEqual(
				TEXT("the disconnect created no edge"),
				ApplyResult->GetNumberField(TEXT("createdEdgeCount")),
				0.0)
			|| !TestFalse(
				TEXT("the disconnect changed the edge fingerprint"),
				EdgesAfter.Equals(EdgesFingerprintBefore, ESearchCase::CaseSensitive))
			|| !TestEqual(TEXT("one result per operation"), Results.Num(), 1)
			|| !TestEqual(
				TEXT("the disconnect reports the resolved source pin"),
				Results[0]->AsObject()->GetStringField(TEXT("fromPinGuid")),
				GuidLower(Not1Out->PinId)))
		{
			return Fail();
		}
		if (!TestTrue(TEXT("Undo is available"), GEditor->UndoTransaction())
			|| !TestTrue(TEXT("Undo restores the disconnected edge"), AreLinked(Not1Out, BranchCondition))
			|| !TestEqual(TEXT("Undo restores the edge count"), CountLiveLinks(Graph), 2)
			|| !TestEqual(
				TEXT("Undo returns the edge fingerprint to before"),
				UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(Graph),
				EdgesFingerprintBefore)
			|| !TestTrue(TEXT("Redo is available"), GEditor->RedoTransaction())
			|| !TestFalse(TEXT("Redo removes the edge again"), AreLinked(Not1Out, BranchCondition))
			|| !TestEqual(TEXT("Redo restores the edge count"), CountLiveLinks(Graph), 1)
			|| !TestEqual(
				TEXT("Redo returns the edge fingerprint to after"),
				UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(Graph),
				EdgesAfter))
		{
			return Fail();
		}
		if (!TestTrue(TEXT("the explicit save of the disconnect succeeds"), SaveApplied(ApplyResult, Error)))
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

	// ---- The same request on the new edge set is a different plan. ----
	{
		TSharedPtr<FJsonObject> Preview;
		FString Code;
		FString Message;
		if (!TestTrue(
				TEXT("the disconnect request still previews after the save"),
				UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
					BuildPatchParams(Blueprint, Graph, DisconnectOrA),
					Preview,
					Code,
					Message)))
		{
			AddError(Code + TEXT(" ") + Message);
			return Fail();
		}
		if (!TestFalse(
				TEXT("removing one unrelated edge changes the plan id (the edge set is bound)"),
				Preview->GetStringField(TEXT("planId"))
					.Equals(PlanIdWithTwoEdges, ESearchCase::CaseSensitive)))
		{
			return Fail();
		}
	}

	// ---- RewirePin: move one input to another source, one edge in one edge out.
	{
		const TArray<TSharedPtr<FJsonObject>> Operations = {
			MakeRewireOperation(TEXT("r1"), OrA, Not2Out, Not1Out),
		};
		TSharedPtr<FJsonObject> Preview;
		FString Code;
		FString Message;
		if (!TestTrue(
				TEXT("the rewire patch previews"),
				UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
					BuildPatchParams(Blueprint, Graph, Operations),
					Preview,
					Code,
					Message))
			|| !TestTrue(TEXT("the rewire preview exists"), Preview.IsValid()))
		{
			AddError(Code + TEXT(" ") + Message);
			return Fail();
		}
		const TArray<TSharedPtr<FJsonValue>>& Planned = Preview->GetArrayField(TEXT("operations"));
		if (!TestEqual(
				TEXT("the rewire preview resolves the pin kind"),
				Planned[0]->AsObject()->GetStringField(TEXT("pinKind")),
				FString(TEXT("bool")))
			|| !TestEqual(
				TEXT("the rewire preview resolves the target pin"),
				Planned[0]->AsObject()->GetStringField(TEXT("targetPinGuid")),
				GuidLower(OrA->PinId)))
		{
			return Fail();
		}
		const FString EdgesBeforeRewire =
			UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(Graph);
		TSharedPtr<FJsonObject> ApplyResult;
		if (!TestTrue(
				TEXT("the rewire patch applies"),
				UEAgentKitBlueprintPatchExperimental::TryApplyExperimental(
					BuildPatchApplyParams(Preview, Operations),
					ApplyResult,
					Code,
					Message))
			|| !TestTrue(TEXT("the rewire result exists"), ApplyResult.IsValid()))
		{
			AddError(Code + TEXT(" ") + Message);
			return Fail();
		}
		const FString EdgesAfterRewire = ApplyResult->GetStringField(TEXT("edgesFingerprintAfter"));
		FString WriterHash;
		if (!TestTrue(TEXT("the fixture source is wired to the new source"), AreLinked(Not1Out, OrA))
			|| !TestFalse(TEXT("the old edge is gone"), AreLinked(Not2Out, OrA))
			|| !TestEqual(TEXT("a rewire keeps the edge count"), CountLiveLinks(Graph), 1)
			|| !TestEqual(
				TEXT("a rewire reports no created edge"),
				ApplyResult->GetNumberField(TEXT("createdEdgeCount")),
				0.0)
			|| !TestEqual(
				TEXT("a rewire reports no removed edge"),
				ApplyResult->GetNumberField(TEXT("removedEdgeCount")),
				0.0)
			|| !TestTrue(
				TEXT("the live edge-set hash is available"),
				UEAgentKitBlueprintRewireExperimental::TryComputeLiveEdgeSetHash(Graph, WriterHash))
			|| !TestEqual(
				TEXT("the patch edge fingerprint is the rewire writer's own edge-set hash"),
				EdgesAfterRewire,
				WriterHash)
			|| !TestFalse(
				TEXT("the rewire changed the edge fingerprint"),
				EdgesAfterRewire.Equals(EdgesBeforeRewire, ESearchCase::CaseSensitive)))
		{
			return Fail();
		}
		if (!TestTrue(TEXT("Undo is available"), GEditor->UndoTransaction())
			|| !TestTrue(TEXT("Undo puts the old source back"), AreLinked(Not2Out, OrA))
			|| !TestFalse(TEXT("Undo removes the new edge"), AreLinked(Not1Out, OrA))
			|| !TestEqual(
				TEXT("Undo returns the edge fingerprint to before"),
				UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(Graph),
				EdgesBeforeRewire)
			|| !TestTrue(TEXT("Redo is available"), GEditor->RedoTransaction())
			|| !TestTrue(TEXT("Redo re-applies the new source"), AreLinked(Not1Out, OrA))
			|| !TestEqual(
				TEXT("Redo returns the edge fingerprint to after"),
				UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(Graph),
				EdgesAfterRewire))
		{
			return Fail();
		}
		if (!TestTrue(TEXT("the explicit save of the rewire succeeds"), SaveApplied(ApplyResult, Error)))
		{
			AddError(Error);
			return Fail();
		}
	}

	// ---- Rollback: a failure on operation 2 must undo operation 1. ----
	{
		const FString EdgesBeforeRollback =
			UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(Graph);
		const FString ShaBeforeRollback = ComputeAssetSha(Blueprint);
		struct FRollback
		{
			const TCHAR* Label;
			TArray<TSharedPtr<FJsonObject>> Operations;
			/** Checked after the rollback: the edge that must be back. */
			UEdGraphPin* KeptFrom;
			UEdGraphPin* KeptTo;
			/** Checked after the rollback: the edge that must not exist. */
			UEdGraphPin* GoneFrom;
			UEdGraphPin* GoneTo;
		};
		const TArray<FRollback> Rollbacks = {
			{
				TEXT("a failed connect after a disconnect puts the edge back"),
				{
					MakeDisconnectOperation(TEXT("d1"), Not1Out, OrA),
					MakeConnectOperation(
						TEXT("c1"),
						GuidLower(Not2->NodeGuid),
						TEXT("ReturnValue"),
						GuidLower(BoolOr->NodeGuid),
						TEXT("B")),
				},
				Not1Out,
				OrA,
				Not2Out,
				OrB,
			},
			{
				TEXT("a failed connect after a rewire puts the old edge back"),
				{
					MakeRewireOperation(TEXT("r1"), OrA, Not1Out, Not2Out),
					MakeConnectOperation(
						TEXT("c1"),
						GuidLower(Not1->NodeGuid),
						TEXT("ReturnValue"),
						GuidLower(Branch->NodeGuid),
						TEXT("Condition")),
				},
				Not1Out,
				OrA,
				Not2Out,
				OrA,
			},
		};
		for (const FRollback& Case : Rollbacks)
		{
			TSharedPtr<FJsonObject> Preview;
			FString Code;
			FString Message;
			if (!TestTrue(
					FString::Printf(TEXT("rollback case previews: %s"), Case.Label),
					UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
						BuildPatchParams(Blueprint, Graph, Case.Operations),
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
					FString::Printf(TEXT("rollback case fails: %s"), Case.Label),
					UEAgentKitBlueprintPatchExperimental::TryApplyExperimental(
						BuildPatchApplyParams(Preview, Case.Operations),
						ApplyResult,
						Code,
						Message)))
			{
				return Fail();
			}
			const TArray<TSharedPtr<FJsonValue>>& Results =
				ApplyResult->GetArrayField(TEXT("results"));
			if (!TestEqual(
					FString::Printf(TEXT("rollback case code: %s"), Case.Label),
					Code,
					FString(TEXT("graph-patch-apply-fault-injected")))
				|| !TestTrue(
					FString::Printf(TEXT("rollback case reports two results: %s"), Case.Label),
					Results.Num() == 2)
				|| !TestTrue(
					FString::Printf(TEXT("rollback case restored the first edge: %s"), Case.Label),
					AreLinked(Case.KeptFrom, Case.KeptTo))
				|| !TestFalse(
					FString::Printf(TEXT("rollback case left no second edge: %s"), Case.Label),
					AreLinked(Case.GoneFrom, Case.GoneTo))
				|| !TestEqual(
					TEXT("the rollback restored the edge count"),
					CountLiveLinks(Graph),
					1)
				|| !TestEqual(
					FString::Printf(TEXT("rollback case restored the edge fingerprint: %s"), Case.Label),
					UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(Graph),
					EdgesBeforeRollback)
				|| !TestFalse(TEXT("the rollback left the package clean"), Package->IsDirty())
				|| !TestEqual(TEXT("the rollback left the disk asset"), ComputeAssetSha(Blueprint), ShaBeforeRollback)
				|| !TestTrue(
					FString::Printf(TEXT("rollback case says the state is restored: %s"), Case.Label),
					ApplyResult->GetBoolField(TEXT("stateRestored"))))
			{
				AddError(Code + TEXT(" ") + Message);
				return Fail();
			}
		}
	}

	// ---- Six kinds in one patch, then one Undo for all of them. ----
	{
		const TArray<TSharedPtr<FJsonObject>> AddSecondEdge = {
			MakeConnectOperation(
				TEXT("c0"),
				GuidLower(Not2->NodeGuid),
				TEXT("ReturnValue"),
				GuidLower(BoolOr->NodeGuid),
				TEXT("B")),
		};
		TSharedPtr<FJsonObject> SeedPreview;
		FString Code;
		FString Message;
		if (!TestTrue(
				TEXT("the seeding connect previews"),
				UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
					BuildPatchParams(Blueprint, Graph, AddSecondEdge),
					SeedPreview,
					Code,
					Message)))
		{
			AddError(Code + TEXT(" ") + Message);
			return Fail();
		}
		TSharedPtr<FJsonObject> SeedApply;
		if (!TestTrue(
				TEXT("the seeding connect applies"),
				UEAgentKitBlueprintPatchExperimental::TryApplyExperimental(
					BuildPatchApplyParams(SeedPreview, AddSecondEdge),
					SeedApply,
					Code,
					Message))
			|| !TestTrue(TEXT("the seeding save succeeds"), SaveApplied(SeedApply, Error)))
		{
			AddError(Code + TEXT(" ") + Message + TEXT(" ") + Error);
			return Fail();
		}
		if (!TestEqual(TEXT("the seeded graph has two edges"), CountLiveLinks(Graph), 2))
		{
			return Fail();
		}

		TSharedRef<FJsonObject> PinOperation = MakeShared<FJsonObject>();
		PinOperation->SetStringField(TEXT("opId"), TEXT("p1"));
		PinOperation->SetStringField(TEXT("kind"), TEXT("setPinDefault"));
		PinOperation->SetStringField(TEXT("nodeGuid"), GuidLower(Not1->NodeGuid));
		PinOperation->SetStringField(TEXT("pinName"), TEXT("A"));
		PinOperation->SetStringField(TEXT("pinGuid"), GuidLower(Not1In->PinId));
		PinOperation->SetStringField(TEXT("type"), TEXT("bool"));
		PinOperation->SetStringField(TEXT("value"), TEXT("true"));
		TSharedRef<FJsonObject> VariableOperation = MakeShared<FJsonObject>();
		VariableOperation->SetStringField(TEXT("opId"), TEXT("v1"));
		VariableOperation->SetStringField(TEXT("kind"), TEXT("createVariable"));
		VariableOperation->SetStringField(TEXT("name"), TEXT("EdgeOpsFlag"));
		VariableOperation->SetStringField(TEXT("type"), TEXT("bool"));
		VariableOperation->SetStringField(TEXT("defaultValue"), TEXT("true"));

		const TArray<TSharedPtr<FJsonObject>> Mixed = {
			PinOperation,
			VariableOperation,
			MakeNodeOperation(TEXT("n1"), TEXT("branch"), 700, 420, TEXT("tmp:branch2")),
			MakeConnectOperation(
				TEXT("c1"),
				GuidLower(Not2->NodeGuid),
				TEXT("ReturnValue"),
				TEXT("tmp:branch2"),
				TEXT("Condition")),
			MakeDisconnectOperation(TEXT("d1"), Not2Out, OrB),
			MakeRewireOperation(TEXT("r1"), OrA, Not1Out, Not2Out),
		};
		const int32 VariableCountBefore = Blueprint->NewVariables.Num();
		const FString EdgesBeforeMixed =
			UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(Graph);
		TSharedPtr<FJsonObject> Preview;
		if (!TestTrue(
				TEXT("the six-kind patch previews"),
				UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
					BuildPatchParams(Blueprint, Graph, Mixed),
					Preview,
					Code,
					Message))
			|| !TestTrue(TEXT("the six-kind preview exists"), Preview.IsValid()))
		{
			AddError(Code + TEXT(" ") + Message);
			return Fail();
		}
		const TArray<TSharedPtr<FJsonValue>>& Planned = Preview->GetArrayField(TEXT("operations"));
		TArray<FString> PlannedKinds;
		for (const TSharedPtr<FJsonValue>& Value : Planned)
		{
			PlannedKinds.Add(Value->AsObject()->GetStringField(TEXT("kind")));
		}
		if (!TestEqual(
				TEXT("all six kinds are planned in order"),
				FString::Join(PlannedKinds, TEXT(",")),
				FString(TEXT("setPinDefault,createVariable,createNode,connect,disconnect,rewirePin"))))
		{
			return Fail();
		}
		TSharedPtr<FJsonObject> ApplyResult;
		if (!TestTrue(
				TEXT("the six-kind patch applies"),
				UEAgentKitBlueprintPatchExperimental::TryApplyExperimental(
					BuildPatchApplyParams(Preview, Mixed),
					ApplyResult,
					Code,
					Message))
			|| !TestTrue(TEXT("the six-kind result exists"), ApplyResult.IsValid()))
		{
			AddError(Code + TEXT(" ") + Message);
			return Fail();
		}
		bool bAllApplied = ApplyResult->GetArrayField(TEXT("results")).Num() == 6;
		for (const TSharedPtr<FJsonValue>& Value : ApplyResult->GetArrayField(TEXT("results")))
		{
			bAllApplied = bAllApplied
				&& Value->AsObject()->GetStringField(TEXT("status")) == TEXT("applied");
		}
		bool bPinIsTrue = false;
		for (const UEdGraphPin* Pin : Not1->Pins)
		{
			if (Pin != nullptr && Pin->Direction == EGPD_Input && Pin->PinName == FName(TEXT("A")))
			{
				bPinIsTrue = Pin->DefaultValue == TEXT("true");
			}
		}
		if (!TestTrue(TEXT("all six kinds applied"), bAllApplied)
			|| !TestTrue(TEXT("the pin default landed"), bPinIsTrue)
			|| !TestEqual(
				TEXT("the variable was created"),
				Blueprint->NewVariables.Num(),
				VariableCountBefore + 1)
			|| !TestEqual(
				TEXT("one node was created"),
				ApplyResult->GetNumberField(TEXT("nodeCountAfter")),
				static_cast<double>(NodeCountBefore + 1))
			|| !TestEqual(
				TEXT("the mixed patch keeps the edge count (one in, one out)"),
				ApplyResult->GetNumberField(TEXT("linkCountAfter")),
				2.0)
			|| !TestTrue(TEXT("the rewired edge exists"), AreLinked(Not2Out, OrA))
			|| !TestFalse(TEXT("the disconnected edge is gone"), AreLinked(Not2Out, OrB))
			|| !TestTrue(
				TEXT("the created node was wired through its tempId"),
				AreLinked(Not2Out, FindPin(FindNodeAt(Graph, 700, 420), TEXT("Condition"), EGPD_Input)))
			|| !TestFalse(
				TEXT("the mixed patch changed the edge fingerprint"),
				UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(Graph)
					.Equals(EdgesBeforeMixed, ESearchCase::CaseSensitive)))
		{
			return Fail();
		}
		if (!TestTrue(TEXT("one Undo reverts the whole patch"), GEditor->UndoTransaction())
			|| !TestEqual(TEXT("Undo restores the node count"), Graph->Nodes.Num(), NodeCountBefore)
			|| !TestEqual(TEXT("Undo restores the edge count"), CountLiveLinks(Graph), 2)
			|| !TestTrue(TEXT("Undo restores the rewired edge"), AreLinked(Not1Out, OrA))
			|| !TestTrue(TEXT("Undo restores the disconnected edge"), AreLinked(Not2Out, OrB))
			|| !TestEqual(
				TEXT("Undo returns the edge fingerprint to before"),
				UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(Graph),
				EdgesBeforeMixed)
			|| !TestEqual(
				TEXT("Undo removes the created variable"),
				Blueprint->NewVariables.Num(),
				VariableCountBefore)
			|| !TestTrue(TEXT("Redo re-applies the whole patch"), GEditor->RedoTransaction())
			|| !TestTrue(TEXT("Redo restores the rewired edge"), AreLinked(Not2Out, OrA)))
		{
			return Fail();
		}
	}

	UEAgentKitBlueprintPatchExperimental::ResetExperimentalWriteForTest();
	UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest();
	UEAgentKitBlueprintRewireExperimental::ResetExperimentalWriteForTest();
	return true;
}

#endif // WITH_DEV_AUTOMATION_TESTS
