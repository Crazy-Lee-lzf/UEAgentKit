// Real-Editor acceptance for the 0-3.4 cross-writer atomic Blueprint patch.
//
// The fixture is a disposable K2 event graph with two unlinked input pins (one
// bool, one int) plus one deliberately connected bool input.  The test proves:
//
//  - the product gate stays off unless the project opts in;
//  - Preview is read-only and resolves one entry per operation;
//  - a single-operation patch derives exactly the before values, revision,
//    graph fingerprint and state fingerprint the single-writer pin-default path
//    derives, so the migrated path is field-for-field equivalent at plan time;
//  - an unknown kind and a duplicate target fail closed before any write;
//  - stale planIds and wrong phrases are refused;
//  - a patch that fails on its SECOND operation restores the FIRST operation's
//    effect, leaves the package clean, cancels the transaction and still reports
//    one status per operation (writeOccurred=false);
//  - the same patch then applies in one transaction, Undo/Redo verifies it, and
//    the explicit Save writes the exact asset.
//
// The failure on operation 2 uses the WITH_DEV_AUTOMATION_TESTS-only fault seam:
// plan-time validation rejects every externally constructible failure before the
// transaction opens, so this is the only deterministic way to reach the
// in-transaction rollback path.

#include "BlueprintEditor.h"
#include "BlueprintContextSha256.h"
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
#include "Editor/Transactor.h"
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

namespace UEAgentKitPatchExpansion
{
	const TCHAR* const AssetPath =
		TEXT("/Game/UEAgentKitPatchExpansion/BP_PatchExpansion.BP_PatchExpansion");
	const TCHAR* const PackageName = TEXT("/Game/UEAgentKitPatchExpansion/BP_PatchExpansion");
	const TCHAR* const AssetName = TEXT("BP_PatchExpansion");

	FString GuidLower(const FGuid& Guid)
	{
		return Guid.ToString(EGuidFormats::DigitsWithHyphensLower);
	}

	bool RemoveStaleFixtureFiles(FString& OutError)
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

	UK2Node_CallFunction* AddCallFunction(
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
			OutError = TEXT("Patch expansion fixture already exists; use a fresh project.");
			return nullptr;
		}
		if (!RemoveStaleFixtureFiles(OutError))
		{
			return nullptr;
		}
		UPackage* Package = CreatePackage(PackageName);
		UBlueprint* Blueprint = FKismetEditorUtilities::CreateBlueprint(
			AActor::StaticClass(),
			Package,
			FName(AssetName),
			BPTYPE_Normal,
			FName(TEXT("UEAgentKitPatchExpansion")));
		if (Blueprint == nullptr || Blueprint->UbergraphPages.IsEmpty())
		{
			OutError = TEXT("Could not create the patch expansion Blueprint.");
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
			OutError = TEXT("Could not create the branch node.");
			return nullptr;
		}
		Graph->AddNode(Branch, true, false);
		Branch->AllocateDefaultPins();
		if (!Branch->NodeGuid.IsValid())
		{
			Branch->CreateNewGuid();
		}
		Branch->NodePosX = 0;
		Branch->NodePosY = 0;

		UK2Node_CallFunction* NotBool = AddCallFunction(
			Graph,
			UKismetMathLibrary::StaticClass(),
			TEXT("Not_PreBool"),
			0,
			260);
		UK2Node_CallFunction* IntAdd = AddCallFunction(
			Graph,
			UKismetMathLibrary::StaticClass(),
			TEXT("Add_IntInt"),
			340,
			0);
		if (NotBool == nullptr || IntAdd == nullptr)
		{
			OutError = TEXT("Could not create the fixture function nodes.");
			return nullptr;
		}

		// Deliberately connect one bool input so the patch must refuse it while
		// the two unlinked inputs stay editable.
		const UEdGraphSchema_K2* Schema = Cast<UEdGraphSchema_K2>(Graph->GetSchema());
		UEdGraphPin* NotBoolOutput = NotBool->GetReturnValuePin();
		UEdGraphPin* BranchCondition = Branch->FindPin(UEdGraphSchema_K2::PN_Condition, EGPD_Input);
		if (Schema == nullptr || NotBoolOutput == nullptr || BranchCondition == nullptr
			|| !Schema->TryCreateConnection(NotBoolOutput, BranchCondition))
		{
			OutError = TEXT("Could not create the deliberately connected bool input.");
			return nullptr;
		}

		FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
		FKismetEditorUtilities::CompileBlueprint(Blueprint, EBlueprintCompileOptions::SkipGarbageCollection);
		if (Blueprint->Status == BS_Error)
		{
			OutError = TEXT("Patch expansion fixture failed to compile.");
			return nullptr;
		}
		FAssetRegistryModule::AssetCreated(Blueprint);
		Package->MarkPackageDirty();
		if (!SaveBlueprint(Blueprint))
		{
			OutError = TEXT("Could not save the patch expansion fixture.");
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

	TSharedPtr<FJsonObject> MakeOperation(
		const TCHAR* OpId,
		UEdGraphNode* Node,
		const TCHAR* PinName,
		const TCHAR* Type,
		const TCHAR* Value)
	{
		UEdGraphPin* Pin = Node != nullptr
			? Node->FindPin(FName(PinName), EGPD_Input)
			: nullptr;
		if (Pin == nullptr || !Pin->PinId.IsValid())
		{
			return nullptr;
		}
		TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
		Item->SetStringField(TEXT("opId"), OpId);
		Item->SetStringField(TEXT("kind"), TEXT("setPinDefault"));
		Item->SetStringField(TEXT("nodeGuid"), GuidLower(Node->NodeGuid));
		Item->SetStringField(TEXT("pinName"), PinName);
		Item->SetStringField(TEXT("pinGuid"), GuidLower(Pin->PinId));
		Item->SetStringField(TEXT("type"), Type);
		Item->SetStringField(TEXT("value"), Value);
		return Item;
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
		const TArray<TSharedPtr<FJsonObject>>& Operations,
		const FString& Confirmation)
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
		Params->SetStringField(TEXT("confirmation"), Confirmation);
		return Params;
	}

	UEdGraphPin* FindInputPin(UEdGraphNode* Node, const TCHAR* PinName)
	{
		return Node != nullptr ? Node->FindPin(FName(PinName), EGPD_Input) : nullptr;
	}
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitPatchExpansionTest,
	"UEAgentKit.AtomicPatch.Expansion",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitPatchExpansionTest::RunTest(const FString& Parameters)
{
	using namespace UEAgentKitPatchExpansion;

	UEAgentKitBlueprintPatchExperimental::ResetExperimentalWriteForTest();
	if (!TestFalse(
			TEXT("experimental atomic patch gate is off unless the project opts in"),
			UEAgentKitBlueprintPatchExperimental::IsExperimentalWriteAvailable()))
	{
		return false;
	}

	FString Error;
	UBlueprint* Blueprint = CreateFixture(Error);
	if (!TestNotNull(TEXT("patch expansion fixture is created"), Blueprint))
	{
		AddError(Error);
		return false;
	}
	UEdGraph* Graph = Blueprint->UbergraphPages[0];
	if (!TestNotNull(TEXT("patch expansion graph is focused"), OpenAndFocus(Blueprint, Graph, Error)))
	{
		AddError(Error);
		return false;
	}
	UPackage* Package = Blueprint->GetOutermost();

	UEdGraphNode* NotBool = nullptr;
	UEdGraphNode* IntAdd = nullptr;
	UEdGraphNode* Branch = nullptr;
	for (UEdGraphNode* Node : Graph->Nodes)
	{
		if (Node == nullptr)
		{
			continue;
		}
		if (Node->GetClass()->GetName() == TEXT("K2Node_CallFunction"))
		{
			UK2Node_CallFunction* Call = Cast<UK2Node_CallFunction>(Node);
			const FString FunctionName = Call != nullptr && Call->GetTargetFunction() != nullptr
				? Call->GetTargetFunction()->GetName()
				: FString();
			if (FunctionName == TEXT("Not_PreBool"))
			{
				NotBool = Node;
			}
			else if (FunctionName == TEXT("Add_IntInt"))
			{
				IntAdd = Node;
			}
		}
		else if (Node->GetClass()->GetName() == TEXT("K2Node_IfThenElse"))
		{
			Branch = Node;
		}
	}
	UEdGraphPin* BoolPin = FindInputPin(NotBool, TEXT("A"));
	UEdGraphPin* IntPin = FindInputPin(IntAdd, TEXT("A"));
	UEdGraphPin* ConnectedBoolPin = Branch != nullptr
		? Branch->FindPin(UEdGraphSchema_K2::PN_Condition, EGPD_Input)
		: nullptr;
	if (!TestNotNull(TEXT("bool fixture pin exists"), BoolPin)
		|| !TestNotNull(TEXT("int fixture pin exists"), IntPin)
		|| !TestNotNull(TEXT("connected bool fixture pin exists"), ConnectedBoolPin))
	{
		return false;
	}
	const FString BoolBefore = BoolPin->DefaultValue;
	const FString IntBefore = IntPin->DefaultValue;

	// The gate must reject before anything else happens.
	TSharedPtr<FJsonObject> GateOp = MakeOperation(TEXT("p1"), NotBool, TEXT("A"), TEXT("bool"), TEXT("true"));
	if (!TestTrue(TEXT("gate operation is built"), GateOp.IsValid()))
	{
		return false;
	}
	{
		TSharedPtr<FJsonObject> GateResult;
		FString GateCode;
		FString GateMessage;
		if (!TestFalse(
				TEXT("gate-off preview is rejected"),
				UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
					BuildPatchParams(Blueprint, Graph, { GateOp }),
					GateResult,
					GateCode,
					GateMessage))
			|| !TestEqual(
				TEXT("gate-off preview error"),
				GateCode,
				FString(TEXT("graph-patch-experimental-disabled"))))
		{
			return false;
		}
	}
	UEAgentKitBlueprintPatchExperimental::SetExperimentalWriteEnabledForTest(true);
	// The equivalence block below deliberately re-previews the same operation
	// through the unpatched single writer, which needs its own gate.
	UEAgentKitBlueprintPinDefaultExperimental::SetExperimentalWriteEnabledForTest(true);
	auto Fail = []()
	{
		UEAgentKitBlueprintPatchExperimental::ResetExperimentalWriteForTest();
		UEAgentKitBlueprintPinDefaultExperimental::ResetExperimentalWriteForTest();
		return false;
	};

	TSharedPtr<FJsonObject> BoolOp = MakeOperation(TEXT("p1"), NotBool, TEXT("A"), TEXT("bool"), TEXT("true"));
	TSharedPtr<FJsonObject> IntOp = MakeOperation(TEXT("p2"), IntAdd, TEXT("A"), TEXT("int"), TEXT("-7"));
	TSharedPtr<FJsonObject> ConnectedOp =
		MakeOperation(TEXT("p3"), Branch, TEXT("Condition"), TEXT("bool"), TEXT("true"));
	if (!TestTrue(TEXT("bool operation is built"), BoolOp.IsValid())
		|| !TestTrue(TEXT("int operation is built"), IntOp.IsValid())
		|| !TestTrue(TEXT("connected-pin operation is built"), ConnectedOp.IsValid()))
	{
		return Fail();
	}

	// ---- Preview resolves one entry per operation and stays read-only. ----
	TSharedPtr<FJsonObject> Preview;
	FString PreviewCode;
	FString PreviewMessage;
	if (!TestTrue(
			TEXT("two-operation patch preview succeeds"),
			UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
				BuildPatchParams(Blueprint, Graph, { BoolOp, IntOp }),
				Preview,
				PreviewCode,
				PreviewMessage))
		|| !TestTrue(TEXT("preview result exists"), Preview.IsValid()))
	{
		AddError(PreviewCode + TEXT(" ") + PreviewMessage);
		return Fail();
	}
	const FString PlanId = Preview->GetStringField(TEXT("planId"));
	const FString StateFingerprintBefore = Preview->GetStringField(TEXT("stateFingerprint"));
	const FString GraphFingerprint = Preview->GetStringField(TEXT("graphFingerprint"));
	const FString PackageRevision = Preview->GetStringField(TEXT("packageRevision"));
	const FString RequiredConfirmation = Preview->GetStringField(TEXT("requiredConfirmation"));
	const TArray<TSharedPtr<FJsonValue>> PreviewOperations = Preview->GetArrayField(TEXT("operations"));
	if (!TestEqual(TEXT("preview keeps the request order"), Preview->GetNumberField(TEXT("operationCount")), 2.0)
		|| !TestEqual(TEXT("preview operation count matches"), PreviewOperations.Num(), 2)
		|| !TestEqual(
			TEXT("preview required confirmation is derived from planId"),
			RequiredConfirmation,
			FString(TEXT("SET EXPERIMENTAL BLUEPRINT PATCH ")) + PlanId)
		|| !TestEqual(
			TEXT("first previewed before value is the live bool default"),
			PreviewOperations[0]->AsObject()->GetStringField(TEXT("beforeValue")),
			BoolBefore)
		|| !TestEqual(
			TEXT("second previewed before value is the live int default"),
			PreviewOperations[1]->AsObject()->GetStringField(TEXT("beforeValue")),
			IntBefore)
		|| !TestEqual(
			TEXT("every previewed operation is validated"),
			PreviewOperations[1]->AsObject()->GetStringField(TEXT("status")),
			FString(TEXT("validated"))))
	{
		return Fail();
	}

	// ---- Field-for-field equivalence with the single-writer preview. ----
	{
		TSharedPtr<FJsonObject> SingleParams = MakeShared<FJsonObject>();
		SingleParams->SetStringField(TEXT("assetPath"), Blueprint->GetPathName());
		SingleParams->SetStringField(TEXT("graphGuid"), GuidLower(Graph->GraphGuid));
		TSharedRef<FJsonObject> SingleDefault = MakeShared<FJsonObject>();
		SingleDefault->SetStringField(TEXT("nodeGuid"), BoolOp->GetStringField(TEXT("nodeGuid")));
		SingleDefault->SetStringField(TEXT("pinName"), BoolOp->GetStringField(TEXT("pinName")));
		SingleDefault->SetStringField(TEXT("pinGuid"), BoolOp->GetStringField(TEXT("pinGuid")));
		SingleDefault->SetStringField(TEXT("type"), TEXT("bool"));
		SingleDefault->SetStringField(TEXT("value"), TEXT("true"));
		TArray<TSharedPtr<FJsonValue>> Defaults;
		Defaults.Add(MakeShared<FJsonValueObject>(SingleDefault));
		SingleParams->SetArrayField(TEXT("defaults"), Defaults);

		TSharedPtr<FJsonObject> SinglePreview;
		FString SingleCode;
		FString SingleMessage;
		if (!TestTrue(
				TEXT("single-writer pin default preview still succeeds"),
				UEAgentKitBlueprintPinDefaultExperimental::TryPreviewExperimental(
					SingleParams,
					SinglePreview,
					SingleCode,
					SingleMessage))
			|| !TestTrue(TEXT("single-writer preview exists"), SinglePreview.IsValid()))
		{
			AddError(SingleCode + TEXT(" ") + SingleMessage);
			return Fail();
		}
		if (!TestEqual(
				TEXT("migrated patch and single writer agree on the graph fingerprint"),
				SinglePreview->GetStringField(TEXT("graphFingerprint")),
				GraphFingerprint)
			|| !TestEqual(
				TEXT("migrated patch and single writer agree on the package revision"),
				SinglePreview->GetStringField(TEXT("packageRevision")),
				PackageRevision)
			|| !TestEqual(
				TEXT("migrated patch state fingerprint is the single writer defaults fingerprint"),
				SinglePreview->GetStringField(TEXT("defaultsFingerprint")),
				StateFingerprintBefore)
			|| !TestEqual(
				TEXT("migrated patch and single writer agree on the bool before value"),
				SinglePreview->GetArrayField(TEXT("defaults"))[0]->AsObject()->GetStringField(
					TEXT("beforeValue")),
				BoolBefore))
		{
			return Fail();
		}
	}

	// ---- Fail-closed previews. ----
	{
		struct FRefusal
		{
			const TCHAR* Label;
			TArray<TSharedPtr<FJsonObject>> Operations;
			const TCHAR* ExpectedCode;
		};
		TSharedPtr<FJsonObject> Unmigrated =
			MakeShared<FJsonObject>(*BoolOp);
		Unmigrated->SetStringField(TEXT("kind"), TEXT("moveNode"));
		TSharedPtr<FJsonObject> Duplicate =
			MakeShared<FJsonObject>(*BoolOp);
		Duplicate->SetStringField(TEXT("opId"), TEXT("p9"));
		Duplicate->SetStringField(TEXT("value"), TEXT("false"));
		const TArray<FRefusal> Refusals = {
			{ TEXT("an unknown kind is refused"), { Unmigrated }, TEXT("graph-patch-kind-not-allowed") },
			{ TEXT("duplicate target is refused"), { BoolOp, Duplicate }, TEXT("graph-patch-duplicate-target") },
			{
				TEXT("connected pin is refused"),
				{ ConnectedOp },
				TEXT("graph-pin-default-pin-connected")
			},
		};
		for (const FRefusal& Refusal : Refusals)
		{
			TSharedPtr<FJsonObject> RefusedResult;
			FString RefusedCode;
			FString RefusedMessage;
			if (!TestFalse(
					Refusal.Label,
					UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
						BuildPatchParams(Blueprint, Graph, Refusal.Operations),
						RefusedResult,
						RefusedCode,
						RefusedMessage))
				|| !TestEqual(
					FString(Refusal.Label) + TEXT(" code"),
					RefusedCode,
					FString(Refusal.ExpectedCode)))
			{
				return Fail();
			}
		}
	}

	// ---- Stale plan id and wrong phrase are refused without a transaction. ----
	{
		TSharedPtr<FJsonObject> StaleParams = BuildApplyParams(
			Preview,
			{ BoolOp, IntOp },
			RequiredConfirmation);
		StaleParams->SetStringField(TEXT("planId"), TEXT("sha256:") + FString::ChrN(64, TEXT('a')));
		TSharedPtr<FJsonObject> StaleResult;
		FString StaleCode;
		FString StaleMessage;
		if (!TestFalse(
				TEXT("stale plan id is refused"),
				UEAgentKitBlueprintPatchExperimental::TryApplyExperimental(
					StaleParams,
					StaleResult,
					StaleCode,
					StaleMessage))
			|| !TestEqual(
				TEXT("stale plan id error"),
				StaleCode,
				FString(TEXT("graph-patch-plan-stale"))))
		{
			return Fail();
		}
		TSharedPtr<FJsonObject> WrongPhraseParams = BuildApplyParams(
			Preview,
			{ BoolOp, IntOp },
			TEXT("SET EXPERIMENTAL BLUEPRINT PATCH wrong"));
		TSharedPtr<FJsonObject> WrongPhraseResult;
		FString WrongPhraseCode;
		FString WrongPhraseMessage;
		if (!TestFalse(
				TEXT("wrong confirmation is refused"),
				UEAgentKitBlueprintPatchExperimental::TryApplyExperimental(
					WrongPhraseParams,
					WrongPhraseResult,
					WrongPhraseCode,
					WrongPhraseMessage))
			|| !TestEqual(
				TEXT("wrong confirmation error"),
				WrongPhraseCode,
				FString(TEXT("graph-patch-confirmation-required"))))
		{
			return Fail();
		}
		if (!TestEqual(TEXT("refusals left the bool pin alone"), BoolPin->DefaultValue, BoolBefore)
			|| !TestEqual(TEXT("refusals left the int pin alone"), IntPin->DefaultValue, IntBefore)
			|| !TestFalse(TEXT("refusals left the package clean"), Package->IsDirty()))
		{
			return Fail();
		}
	}

	// ---- Atomic rollback: operation 2 fails after operation 1 was written. ----
	{
		TSharedPtr<FJsonObject> FaultParams = BuildApplyParams(
			Preview,
			{ BoolOp, IntOp },
			RequiredConfirmation);
		UEAgentKitBlueprintPatchExperimental::SetNextApplyFaultForTest(1);
		TSharedPtr<FJsonObject> FaultResult;
		FString FaultCode;
		FString FaultMessage;
		const bool bFaultApplySucceeded =
			UEAgentKitBlueprintPatchExperimental::TryApplyExperimental(
				FaultParams,
				FaultResult,
				FaultCode,
				FaultMessage);
		if (!TestFalse(TEXT("faulted patch apply fails"), bFaultApplySucceeded)
			|| !TestEqual(
				TEXT("faulted patch reports the injected fault"),
				FaultCode,
				FString(TEXT("graph-patch-apply-fault-injected")))
			|| !TestTrue(TEXT("faulted patch still returns a result payload"), FaultResult.IsValid()))
		{
			AddError(FaultCode + TEXT(" ") + FaultMessage);
			return Fail();
		}
		const TArray<TSharedPtr<FJsonValue>> FaultResults =
			FaultResult->GetArrayField(TEXT("results"));
		if (!TestFalse(
				TEXT("faulted patch reports writeOccurred=false"),
				FaultResult->GetBoolField(TEXT("writeOccurred")))
			|| !TestFalse(TEXT("faulted patch is not applied"), FaultResult->GetBoolField(TEXT("applied")))
			|| !TestTrue(TEXT("faulted patch opened a transaction"), FaultResult->GetBoolField(TEXT("transactionOpened")))
			|| !TestTrue(TEXT("faulted patch reports the rollback"), FaultResult->GetBoolField(TEXT("rolledBack")))
			|| !TestEqual(TEXT("faulted patch still reports every operation"), FaultResults.Num(), 2)
			|| !TestEqual(
				TEXT("operation 1 was applied before the failure"),
				FaultResults[0]->AsObject()->GetStringField(TEXT("status")),
				FString(TEXT("applied")))
			|| !TestTrue(
				TEXT("operation 1 is reported as rolled back"),
				FaultResults[0]->AsObject()->GetBoolField(TEXT("rolledBack")))
			|| !TestEqual(
				TEXT("operation 2 is the failing one"),
				FaultResults[1]->AsObject()->GetStringField(TEXT("status")),
				FString(TEXT("failed")))
			|| !TestEqual(
				TEXT("operation 2 carries the exact error code"),
				FaultResults[1]->AsObject()->GetStringField(TEXT("errorCode")),
				FString(TEXT("graph-patch-apply-fault-injected"))))
		{
			return Fail();
		}
		// The proof that nothing was left behind: both pins keep their before
		// values, the package is clean again, the graph state fingerprint matches
		// the preview, and a fresh preview of the same plan still succeeds.
		if (!TestEqual(TEXT("operation 1 effect was restored on the bool pin"), BoolPin->DefaultValue, BoolBefore)
			|| !TestEqual(TEXT("operation 2 never touched the int pin"), IntPin->DefaultValue, IntBefore)
			|| !TestFalse(TEXT("rolled back patch left the package clean"), Package->IsDirty())
			|| !TestEqual(
				TEXT("rolled back patch left the graph state fingerprint unchanged"),
				UEAgentKitBlueprintPinDefaultExperimental::ComputeAtomicStateFingerprint(Graph),
				StateFingerprintBefore))
		{
			return Fail();
		}
		TSharedPtr<FJsonObject> Repreview;
		FString RepreviewCode;
		FString RepreviewMessage;
		if (!TestTrue(
				TEXT("the same plan can be previewed again after the rollback"),
				UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
					BuildPatchParams(Blueprint, Graph, { BoolOp, IntOp }),
					Repreview,
					RepreviewCode,
					RepreviewMessage))
			|| !TestEqual(
				TEXT("the re-preview derives the same plan id"),
				Repreview->GetStringField(TEXT("planId")),
				PlanId))
		{
			AddError(RepreviewCode + TEXT(" ") + RepreviewMessage);
			return Fail();
		}
	}

	// ---- One transaction: apply, Undo/Redo verification, then explicit Save. ----
	TSharedPtr<FJsonObject> ApplyResult;
	FString ApplyCode;
	FString ApplyMessage;
	if (!TestTrue(
			TEXT("two-operation patch applies"),
			UEAgentKitBlueprintPatchExperimental::TryApplyExperimental(
				BuildApplyParams(Preview, { BoolOp, IntOp }, RequiredConfirmation),
				ApplyResult,
				ApplyCode,
				ApplyMessage))
		|| !TestTrue(TEXT("apply result exists"), ApplyResult.IsValid()))
	{
		AddError(ApplyCode + TEXT(" ") + ApplyMessage);
		return Fail();
	}
	const TArray<TSharedPtr<FJsonValue>> ApplyResults = ApplyResult->GetArrayField(TEXT("results"));
	if (!TestTrue(TEXT("apply reports applied"), ApplyResult->GetBoolField(TEXT("applied")))
		|| !TestTrue(TEXT("apply reports writeOccurred"), ApplyResult->GetBoolField(TEXT("writeOccurred")))
		|| !TestTrue(TEXT("apply is one transaction"), ApplyResult->GetBoolField(TEXT("singleTransaction")))
		|| !TestEqual(TEXT("apply transaction count"), ApplyResult->GetNumberField(TEXT("transactionCount")), 1.0)
		|| !TestTrue(TEXT("apply verified undo"), ApplyResult->GetBoolField(TEXT("undoVerified")))
		|| !TestTrue(TEXT("apply verified redo"), ApplyResult->GetBoolField(TEXT("redoVerified")))
		|| !TestFalse(TEXT("apply never saves"), ApplyResult->GetBoolField(TEXT("saved")))
		|| !TestEqual(TEXT("apply reports every operation"), ApplyResults.Num(), 2)
		|| !TestEqual(
			TEXT("first result is applied"),
			ApplyResults[0]->AsObject()->GetStringField(TEXT("status")),
			FString(TEXT("applied")))
		|| !TestEqual(
			TEXT("second result is applied"),
			ApplyResults[1]->AsObject()->GetStringField(TEXT("status")),
			FString(TEXT("applied")))
		|| !TestTrue(TEXT("apply left the package dirty"), Package->IsDirty()))
	{
		return Fail();
	}
	if (!TestEqual(TEXT("bool pin physically changed"), BoolPin->DefaultValue, FString(TEXT("true")))
		|| !TestEqual(TEXT("int pin physically changed"), IntPin->DefaultValue, FString(TEXT("-7")))
		|| !TestEqual(
			TEXT("node count is unchanged by a pin-only patch"),
			ApplyResult->GetNumberField(TEXT("nodeCountAfter")),
			ApplyResult->GetNumberField(TEXT("nodeCountBefore")))
		|| !TestEqual(
			TEXT("link count is unchanged by a pin-only patch"),
			ApplyResult->GetNumberField(TEXT("linkCountAfter")),
			ApplyResult->GetNumberField(TEXT("linkCountBefore")))
		|| !TestEqual(
			TEXT("counts match the single-writer view of the graph"),
			ApplyResult->GetNumberField(TEXT("nodeCountAfter")),
			static_cast<double>(Graph->Nodes.Num())))
	{
		return Fail();
	}

	// A second Apply of the same plan must now be refused: the patch is one dirty
	// transition and its plan id was bound to the clean state.
	{
		TSharedPtr<FJsonObject> SecondResult;
		FString SecondCode;
		FString SecondMessage;
		if (!TestFalse(
				TEXT("re-applying on a dirty package is refused"),
				UEAgentKitBlueprintPatchExperimental::TryApplyExperimental(
					BuildApplyParams(Preview, { BoolOp, IntOp }, RequiredConfirmation),
					SecondResult,
					SecondCode,
					SecondMessage))
			|| !TestEqual(
				TEXT("dirty package error"),
				SecondCode,
				FString(TEXT("graph-patch-package-dirty"))))
		{
			return Fail();
		}
	}

	// Explicit Save writes exactly the post-patch state.
	{
		TSharedPtr<FJsonObject> SaveParams = MakeShared<FJsonObject>();
		SaveParams->SetStringField(TEXT("assetPath"), ApplyResult->GetStringField(TEXT("assetPath")));
		SaveParams->SetStringField(TEXT("graphGuid"), ApplyResult->GetStringField(TEXT("graphGuid")));
		SaveParams->SetStringField(TEXT("planId"), PlanId);
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
				TEXT("explicit patch save succeeds"),
				UEAgentKitBlueprintPatchExperimental::TrySaveExperimental(
					SaveParams,
					SaveResult,
					SaveCode,
					SaveMessage))
			|| !TestTrue(TEXT("save result exists"), SaveResult.IsValid()))
		{
			AddError(SaveCode + TEXT(" ") + SaveMessage);
			return Fail();
		}
		if (!TestTrue(TEXT("save reports saved"), SaveResult->GetBoolField(TEXT("saved")))
			|| !TestFalse(TEXT("save cleared the dirty flag"), Package->IsDirty())
			|| !TestFalse(
				TEXT("save result reports a clean package"),
				SaveResult->GetBoolField(TEXT("packageDirty")))
			|| !TestTrue(
				TEXT("save result carries the asset hash"),
				SaveResult->GetStringField(TEXT("assetSha256")).StartsWith(TEXT("sha256:"))))
		{
			return Fail();
		}
	}

	// After the save the package is clean again, so a fresh preview must report
	// the applied values as the new before values.
	{
		TSharedPtr<FJsonObject> AfterSavePreview;
		FString AfterSaveCode;
		FString AfterSaveMessage;
		if (!TestTrue(
				TEXT("preview after save succeeds"),
				UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
					BuildPatchParams(Blueprint, Graph, { BoolOp, IntOp }),
					AfterSavePreview,
					AfterSaveCode,
					AfterSaveMessage)))
		{
			AddError(AfterSaveCode + TEXT(" ") + AfterSaveMessage);
			return Fail();
		}
		const TArray<TSharedPtr<FJsonValue>> AfterSaveOperations =
			AfterSavePreview->GetArrayField(TEXT("operations"));
		if (!TestEqual(
				TEXT("saved bool default reads back as the applied value"),
				AfterSaveOperations[0]->AsObject()->GetStringField(TEXT("beforeValue")),
				FString(TEXT("true")))
			|| !TestEqual(
				TEXT("saved int default reads back as the applied value"),
				AfterSaveOperations[1]->AsObject()->GetStringField(TEXT("beforeValue")),
				FString(TEXT("-7"))))
		{
			return Fail();
		}
	}

	UEAgentKitBlueprintPatchExperimental::ResetExperimentalWriteForTest();
	UEAgentKitBlueprintPinDefaultExperimental::ResetExperimentalWriteForTest();
	return true;
}

namespace UEAgentKitPatchVariables
{
	const TCHAR* const AssetPath =
		TEXT("/Game/UEAgentKitPatchExpansion/BP_PatchVariables.BP_PatchVariables");
	const TCHAR* const PackageName = TEXT("/Game/UEAgentKitPatchExpansion/BP_PatchVariables");
	const TCHAR* const AssetName = TEXT("BP_PatchVariables");

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

	int32 FindVariableIndexByName(UBlueprint* Blueprint, const TCHAR* Name)
	{
		if (Blueprint == nullptr)
		{
			return INDEX_NONE;
		}
		const FName NameValue(Name);
		for (int32 Index = 0; Index < Blueprint->NewVariables.Num(); ++Index)
		{
			if (Blueprint->NewVariables[Index].VarName == NameValue)
			{
				return Index;
			}
		}
		return INDEX_NONE;
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

	UBlueprint* CreateFixture(FString& OutError)
	{
		if (FindObject<UBlueprint>(nullptr, AssetPath) != nullptr)
		{
			OutError = TEXT("Variable patch fixture already exists; use a fresh project.");
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
			FName(TEXT("UEAgentKitPatchVariables")));
		if (Blueprint == nullptr || Blueprint->UbergraphPages.IsEmpty())
		{
			OutError = TEXT("Could not create the variable patch Blueprint.");
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
		// One unlinked bool input is enough for the mixed-kind case; nothing here
		// is connected, so a pin-default operation on it is legal.
		UK2Node_CallFunction* NotBool = UEAgentKitPatchExpansion::AddCallFunction(
			Graph,
			UKismetMathLibrary::StaticClass(),
			TEXT("Not_PreBool"),
			0,
			0);
		if (NotBool == nullptr)
		{
			OutError = TEXT("Could not create the variable fixture function node.");
			return nullptr;
		}
		FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
		FKismetEditorUtilities::CompileBlueprint(Blueprint, EBlueprintCompileOptions::SkipGarbageCollection);
		if (Blueprint->Status == BS_Error)
		{
			OutError = TEXT("Variable patch fixture failed to compile.");
			return nullptr;
		}
		FAssetRegistryModule::AssetCreated(Blueprint);
		Package->MarkPackageDirty();
		if (!UEAgentKitPatchExpansion::SaveBlueprint(Blueprint))
		{
			OutError = TEXT("Could not save the variable patch fixture.");
			return nullptr;
		}
		return Blueprint;
	}

	TSharedPtr<FJsonObject> MakeVariableOperation(
		const TCHAR* OpId,
		const TCHAR* Name,
		const TCHAR* Type,
		const TCHAR* DefaultValue)
	{
		TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
		Item->SetStringField(TEXT("opId"), OpId);
		Item->SetStringField(TEXT("kind"), TEXT("createVariable"));
		Item->SetStringField(TEXT("name"), Name);
		Item->SetStringField(TEXT("type"), Type);
		Item->SetStringField(TEXT("defaultValue"), DefaultValue);
		return Item;
	}

	TSharedPtr<FJsonObject> MakeSingleVariablePreviewParams(
		UBlueprint* Blueprint,
		const TCHAR* Name,
		const TCHAR* Type,
		const TCHAR* DefaultValue)
	{
		TSharedRef<FJsonObject> Variable = MakeShared<FJsonObject>();
		Variable->SetStringField(TEXT("name"), Name);
		Variable->SetStringField(TEXT("type"), Type);
		Variable->SetStringField(TEXT("defaultValue"), DefaultValue);
		TArray<TSharedPtr<FJsonValue>> Variables;
		Variables.Add(MakeShared<FJsonValueObject>(Variable));
		TSharedRef<FJsonObject> Params = MakeShared<FJsonObject>();
		Params->SetStringField(TEXT("assetPath"), Blueprint->GetPathName());
		Params->SetArrayField(TEXT("variables"), Variables);
		return Params;
	}
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitPatchVariableTest,
	"UEAgentKit.AtomicPatch.CreateVariable",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitPatchVariableTest::RunTest(const FString& Parameters)
{
	using namespace UEAgentKitPatchVariables;

	UEAgentKitBlueprintPatchExperimental::ResetExperimentalWriteForTest();
	UEAgentKitBlueprintVariableExperimental::ResetExperimentalWriteForTest();
	if (!TestFalse(
			TEXT("experimental atomic patch gate is off unless the project opts in"),
			UEAgentKitBlueprintPatchExperimental::IsExperimentalWriteAvailable()))
	{
		return false;
	}

	FString Error;
	UBlueprint* Blueprint = CreateFixture(Error);
	if (!TestNotNull(TEXT("variable patch fixture is created"), Blueprint))
	{
		AddError(Error);
		return false;
	}
	UEdGraph* Graph = Blueprint->UbergraphPages[0];
	if (!TestNotNull(
			TEXT("variable patch graph is focused"),
			UEAgentKitPatchExpansion::OpenAndFocus(Blueprint, Graph, Error)))
	{
		AddError(Error);
		return false;
	}
	UPackage* Package = Blueprint->GetOutermost();

	UEdGraphNode* NotBool = nullptr;
	for (UEdGraphNode* Node : Graph->Nodes)
	{
		if (Node != nullptr
			&& Node->GetClass()->GetName() == TEXT("K2Node_CallFunction"))
		{
			UK2Node_CallFunction* Call = Cast<UK2Node_CallFunction>(Node);
			const FString FunctionName = Call != nullptr && Call->GetTargetFunction() != nullptr
				? Call->GetTargetFunction()->GetName()
				: FString();
			if (FunctionName == TEXT("Not_PreBool"))
			{
				NotBool = Node;
			}
		}
	}
	UEdGraphPin* BoolPin = NotBool != nullptr
		? NotBool->FindPin(FName(TEXT("A")), EGPD_Input)
		: nullptr;
	if (!TestNotNull(TEXT("bool fixture pin exists"), BoolPin))
	{
		return false;
	}
	const FString BoolBefore = BoolPin->DefaultValue;
	const int32 VariableCountBefore = Blueprint->NewVariables.Num();
	const FString AssetShaBefore = ComputeAssetSha(Blueprint);
	if (!TestTrue(TEXT("fixture asset hash is readable"), !AssetShaBefore.IsEmpty()))
	{
		return false;
	}

	// ---- The gate rejects before anything else happens. ----
	{
		TSharedPtr<FJsonObject> GateResult;
		FString GateCode;
		FString GateMessage;
		if (!TestFalse(
				TEXT("gate-off variable preview is rejected"),
				UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
					UEAgentKitPatchExpansion::BuildPatchParams(
						Blueprint,
						Graph,
						{ MakeVariableOperation(TEXT("v1"), TEXT("AgentCount"), TEXT("int"), TEXT("7")) }),
					GateResult,
					GateCode,
					GateMessage))
			|| !TestEqual(
				TEXT("gate-off preview error"),
				GateCode,
				FString(TEXT("graph-patch-experimental-disabled"))))
		{
			return false;
		}
	}
	UEAgentKitBlueprintPatchExperimental::SetExperimentalWriteEnabledForTest(true);
	// The equivalence blocks below call the two single writers directly.
	UEAgentKitBlueprintPinDefaultExperimental::SetExperimentalWriteEnabledForTest(true);
	UEAgentKitBlueprintVariableExperimental::SetExperimentalWriteEnabledForTest(true);
	auto Fail = []()
	{
		UEAgentKitBlueprintPatchExperimental::ResetExperimentalWriteForTest();
		UEAgentKitBlueprintPinDefaultExperimental::ResetExperimentalWriteForTest();
		UEAgentKitBlueprintVariableExperimental::ResetExperimentalWriteForTest();
		return false;
	};

	TSharedPtr<FJsonObject> VariableOp =
		MakeVariableOperation(TEXT("v1"), TEXT("AgentCount"), TEXT("int"), TEXT("7"));
	if (!TestTrue(TEXT("variable operation is built"), VariableOp.IsValid()))
	{
		return Fail();
	}

	// ---- Preview resolves one entry and matches the variable writer. ----
	TSharedPtr<FJsonObject> Preview;
	FString PreviewCode;
	FString PreviewMessage;
	if (!TestTrue(
			TEXT("single variable patch preview succeeds"),
			UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
				UEAgentKitPatchExpansion::BuildPatchParams(Blueprint, Graph, { VariableOp }),
				Preview,
				PreviewCode,
				PreviewMessage))
		|| !TestTrue(TEXT("preview result exists"), Preview.IsValid()))
	{
		AddError(PreviewCode + TEXT(" ") + PreviewMessage);
		return Fail();
	}
	const FString PlanId = Preview->GetStringField(TEXT("planId"));
	const FString VariablesFingerprintBefore =
		Preview->GetStringField(TEXT("variablesFingerprint"));
	const FString RequiredConfirmation = Preview->GetStringField(TEXT("requiredConfirmation"));
	const TArray<TSharedPtr<FJsonValue>> MigratedKinds = Preview->GetArrayField(TEXT("migratedKinds"));
	if (!TestEqual(TEXT("every migrated kind is advertised"), MigratedKinds.Num(), 7)
		|| !TestEqual(
			TEXT("first migrated kind is setPinDefault"),
			MigratedKinds[0]->AsString(),
			FString(TEXT("setPinDefault")))
		|| !TestEqual(
			TEXT("second migrated kind is createVariable"),
			MigratedKinds[1]->AsString(),
			FString(TEXT("createVariable")))
		|| !TestEqual(
			TEXT("third migrated kind is createNode"),
			MigratedKinds[2]->AsString(),
			FString(TEXT("createNode")))
		|| !TestEqual(
			TEXT("fourth migrated kind is connect"),
			MigratedKinds[3]->AsString(),
			FString(TEXT("connect")))
		|| !TestEqual(
			TEXT("fifth migrated kind is disconnect"),
			MigratedKinds[4]->AsString(),
			FString(TEXT("disconnect")))
		|| !TestEqual(
			TEXT("sixth migrated kind is rewirePin"),
			MigratedKinds[5]->AsString(),
			FString(TEXT("rewirePin")))
		|| !TestEqual(
			TEXT("seventh migrated kind is removeNode"),
			MigratedKinds[6]->AsString(),
			FString(TEXT("removeNode"))))
	{
		return Fail();
	}
	{
		const TArray<TSharedPtr<FJsonValue>> Operations = Preview->GetArrayField(TEXT("operations"));
		const TSharedPtr<FJsonObject> Entry = Operations.Num() == 1
			? Operations[0]->AsObject()
			: nullptr;
		if (!TestTrue(TEXT("preview carries one operation"), Entry.IsValid())
			|| !TestEqual(
				TEXT("previewed variable name"),
				Entry->GetStringField(TEXT("name")),
				FString(TEXT("AgentCount")))
			|| !TestEqual(
				TEXT("previewed variable type"),
				Entry->GetStringField(TEXT("type")),
				FString(TEXT("int")))
			|| !TestEqual(
				TEXT("previewed variable default"),
				Entry->GetStringField(TEXT("defaultValue")),
				FString(TEXT("7")))
			|| !TestFalse(
				TEXT("a new variable did not exist before"),
				Entry->GetBoolField(TEXT("existedBefore")))
			|| !TestEqual(
				TEXT("previewed variable is validated"),
				Entry->GetStringField(TEXT("status")),
				FString(TEXT("validated")))
			|| !TestFalse(
				TEXT("preview did not invent a created GUID"),
				Entry->HasField(TEXT("guid")))
			|| !TestEqual(
				TEXT("preview variable count is the live count"),
				Preview->GetNumberField(TEXT("variableCount")),
				static_cast<double>(VariableCountBefore))
			|| !TestTrue(
				TEXT("preview did not write the Blueprint"),
				FindVariableIndexByName(Blueprint, TEXT("AgentCount")) == INDEX_NONE)
			|| !TestFalse(TEXT("preview left the package clean"), Package->IsDirty()))
		{
			return Fail();
		}
	}

	// ---- Field-for-field equivalence with the single variable writer. ----
	{
		TSharedPtr<FJsonObject> SinglePreview;
		FString SingleCode;
		FString SingleMessage;
		if (!TestTrue(
				TEXT("single-writer variable preview still succeeds"),
				UEAgentKitBlueprintVariableExperimental::TryPreviewExperimental(
					MakeSingleVariablePreviewParams(
						Blueprint,
						TEXT("AgentCount"),
						TEXT("int"),
						TEXT("7")),
					SinglePreview,
					SingleCode,
					SingleMessage))
			|| !TestTrue(TEXT("single-writer variable preview exists"), SinglePreview.IsValid()))
		{
			AddError(SingleCode + TEXT(" ") + SingleMessage);
			return Fail();
		}
		if (!TestEqual(
				TEXT("patch variablesFingerprint is the variable writer's variablesFingerprint"),
				VariablesFingerprintBefore,
				SinglePreview->GetStringField(TEXT("variablesFingerprint")))
			|| !TestEqual(
				TEXT("patch and variable writer agree on graphFingerprint"),
				Preview->GetStringField(TEXT("graphFingerprint")),
				SinglePreview->GetStringField(TEXT("graphFingerprint")))
			|| !TestEqual(
				TEXT("patch and variable writer agree on packageRevision"),
				Preview->GetStringField(TEXT("packageRevision")),
				SinglePreview->GetStringField(TEXT("packageRevision")))
			|| !TestEqual(
				TEXT("patch and variable writer agree on variableCountBefore"),
				Preview->GetNumberField(TEXT("variableCount")),
				SinglePreview->GetNumberField(TEXT("variableCountBefore"))))
		{
			return Fail();
		}
	}

	// ---- Fail-closed previews, all before any write. ----
	{
		TSharedPtr<FJsonObject> ForeignField = MakeVariableOperation(
			TEXT("v1"),
			TEXT("AgentCount"),
			TEXT("int"),
			TEXT("7"));
		ForeignField->SetStringField(TEXT("nodeGuid"), TEXT("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"));

		TSharedPtr<FJsonObject> DuplicateA =
			MakeVariableOperation(TEXT("v1"), TEXT("AgentCount"), TEXT("int"), TEXT("1"));
		TSharedPtr<FJsonObject> DuplicateB =
			MakeVariableOperation(TEXT("v2"), TEXT("agentcount"), TEXT("int"), TEXT("2"));

		TArray<TSharedPtr<FJsonObject>> TooMany;
		for (int32 Index = 0; Index < 9; ++Index)
		{
			TooMany.Add(MakeVariableOperation(
				*FString::Printf(TEXT("v%d"), Index),
				*FString::Printf(TEXT("Var%d"), Index),
				TEXT("int"),
				TEXT("1")));
		}

		struct FRefusal
		{
			const TCHAR* Label;
			TArray<TSharedPtr<FJsonObject>> Operations;
			const TCHAR* ExpectedCode;
		};
		TArray<FRefusal> Refusals;
		Refusals.Add({ TEXT("foreign field on a variable operation is refused"), { ForeignField }, TEXT("graph-patch-unknown-parameter") });
		Refusals.Add({ TEXT("duplicate variable name is refused"), { DuplicateA, DuplicateB }, TEXT("graph-patch-duplicate-target") });
		Refusals.Add({
			TEXT("a name that already belongs to the parent class is refused"),
			{ MakeVariableOperation(TEXT("v1"), TEXT("Tags"), TEXT("int"), TEXT("1")) },
			TEXT("blueprint-variable-duplicate-name")
		});
		Refusals.Add({ TEXT("a ninth variable is refused"), TooMany, TEXT("graph-patch-create-variable-limit") });
		// Every designed kind is migrated now (`removeNode` closed the list in slice
		// 13), so the only kind-level refusal left is "not a patch kind".
		for (const TCHAR* Kind : { TEXT("moveNode") })
		{
			TSharedPtr<FJsonObject> Unmigrated = UEAgentKitPatchExpansion::MakeOperation(
				TEXT("p1"),
				NotBool,
				TEXT("A"),
				TEXT("bool"),
				TEXT("true"));
			if (!Unmigrated.IsValid())
			{
				return Fail();
			}
			Unmigrated->SetStringField(TEXT("kind"), Kind);
			Refusals.Add({ Kind, { Unmigrated }, TEXT("graph-patch-kind-not-allowed") });
		}
		for (const FRefusal& Refusal : Refusals)
		{
			TSharedPtr<FJsonObject> RefusedResult;
			FString RefusedCode;
			FString RefusedMessage;
			if (!TestFalse(
					Refusal.Label,
					UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
						UEAgentKitPatchExpansion::BuildPatchParams(
							Blueprint,
							Graph,
							Refusal.Operations),
						RefusedResult,
						RefusedCode,
						RefusedMessage))
				|| !TestEqual(
					FString(Refusal.Label) + TEXT(" code"),
					RefusedCode,
					FString(Refusal.ExpectedCode)))
			{
				AddError(RefusedCode + TEXT(" ") + RefusedMessage);
				return Fail();
			}
		}
		if (!TestEqual(
				TEXT("refusals left the bool pin alone"),
				BoolPin->DefaultValue,
				BoolBefore)
			|| !TestEqual(
				TEXT("refusals left the variable list alone"),
				Blueprint->NewVariables.Num(),
				VariableCountBefore)
			|| !TestFalse(TEXT("refusals left the package clean"), Package->IsDirty())
			|| !TestEqual(
				TEXT("refusals left the asset on disk unchanged"),
				ComputeAssetSha(Blueprint),
				AssetShaBefore))
		{
			return Fail();
		}
	}

	// ---- Atomic rollback: operation 2 fails after operation 1 was created. ----
	{
		TSharedPtr<FJsonObject> Op1 =
			MakeVariableOperation(TEXT("v1"), TEXT("RollbackVar"), TEXT("int"), TEXT("1"));
		TSharedPtr<FJsonObject> Op2 =
			MakeVariableOperation(TEXT("v2"), TEXT("SecondVar"), TEXT("int"), TEXT("2"));
		TSharedPtr<FJsonObject> Op3 =
			MakeVariableOperation(TEXT("v3"), TEXT("ThirdVar"), TEXT("int"), TEXT("3"));
		TSharedPtr<FJsonObject> RollbackPreview;
		FString RollbackPreviewCode;
		FString RollbackPreviewMessage;
		if (!TestTrue(
				TEXT("three-operation variable patch previews"),
				UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
					UEAgentKitPatchExpansion::BuildPatchParams(Blueprint, Graph, { Op1, Op2, Op3 }),
					RollbackPreview,
					RollbackPreviewCode,
					RollbackPreviewMessage)))
		{
			AddError(RollbackPreviewCode + TEXT(" ") + RollbackPreviewMessage);
			return Fail();
		}
		const FString RollbackPlanId = RollbackPreview->GetStringField(TEXT("planId"));
		const FString RollbackFingerprint =
			RollbackPreview->GetStringField(TEXT("variablesFingerprint"));

		TSharedPtr<FJsonObject> FaultParams = UEAgentKitPatchExpansion::BuildApplyParams(
			RollbackPreview,
			{ Op1, Op2, Op3 },
			RollbackPreview->GetStringField(TEXT("requiredConfirmation")));
		UEAgentKitBlueprintPatchExperimental::SetNextApplyFaultForTest(1);
		TSharedPtr<FJsonObject> FaultResult;
		FString FaultCode;
		FString FaultMessage;
		const bool bFaultApplySucceeded =
			UEAgentKitBlueprintPatchExperimental::TryApplyExperimental(
				FaultParams,
				FaultResult,
				FaultCode,
				FaultMessage);
		if (!TestFalse(TEXT("faulted variable patch apply fails"), bFaultApplySucceeded)
			|| !TestEqual(
				TEXT("faulted variable patch reports the injected fault"),
				FaultCode,
				FString(TEXT("graph-patch-apply-fault-injected")))
			|| !TestTrue(TEXT("faulted variable patch still returns results"), FaultResult.IsValid()))
		{
			AddError(FaultCode + TEXT(" ") + FaultMessage);
			return Fail();
		}
		const TArray<TSharedPtr<FJsonValue>> FaultResults =
			FaultResult->GetArrayField(TEXT("results"));
		if (!TestFalse(
				TEXT("faulted variable patch reports writeOccurred=false"),
				FaultResult->GetBoolField(TEXT("writeOccurred")))
			|| !TestTrue(
				TEXT("faulted variable patch opened a transaction"),
				FaultResult->GetBoolField(TEXT("transactionOpened")))
			|| !TestTrue(
				TEXT("faulted variable patch reports the rollback"),
				FaultResult->GetBoolField(TEXT("rolledBack")))
			|| !TestEqual(TEXT("faulted variable patch reports every operation"), FaultResults.Num(), 3)
			|| !TestEqual(
				TEXT("operation 1 was applied before the failure"),
				FaultResults[0]->AsObject()->GetStringField(TEXT("status")),
				FString(TEXT("applied")))
			|| !TestTrue(
				TEXT("operation 1 is reported as rolled back"),
				FaultResults[0]->AsObject()->GetBoolField(TEXT("rolledBack")))
			|| !TestEqual(
				TEXT("operation 2 is the failing one"),
				FaultResults[1]->AsObject()->GetStringField(TEXT("status")),
				FString(TEXT("failed")))
			|| !TestEqual(
				TEXT("operation 2 carries the exact error code"),
				FaultResults[1]->AsObject()->GetStringField(TEXT("errorCode")),
				FString(TEXT("graph-patch-apply-fault-injected")))
			|| !TestEqual(
				TEXT("operation 3 was never attempted"),
				FaultResults[2]->AsObject()->GetStringField(TEXT("status")),
				FString(TEXT("notAttempted"))))
		{
			return Fail();
		}
		// The proof that the created variable did not survive the rollback.
		if (!TestTrue(
				TEXT("the variable created by operation 1 was deleted again"),
				FindVariableIndexByName(Blueprint, TEXT("RollbackVar")) == INDEX_NONE)
			|| !TestTrue(
				TEXT("operation 2 never created its variable"),
				FindVariableIndexByName(Blueprint, TEXT("SecondVar")) == INDEX_NONE)
			|| !TestTrue(
				TEXT("operation 3 never created its variable"),
				FindVariableIndexByName(Blueprint, TEXT("ThirdVar")) == INDEX_NONE)
			|| !TestEqual(
				TEXT("rollback left the variable count unchanged"),
				Blueprint->NewVariables.Num(),
				VariableCountBefore)
			|| !TestFalse(TEXT("rolled back variable patch left the package clean"), Package->IsDirty())
			|| !TestEqual(
				TEXT("rolled back variable patch left the variable fingerprint unchanged"),
				UEAgentKitBlueprintVariableExperimental::ComputeAtomicVariableStateFingerprint(
					Blueprint),
				RollbackFingerprint)
			|| !TestEqual(
				TEXT("rolled back variable patch left the asset on disk unchanged"),
				ComputeAssetSha(Blueprint),
				AssetShaBefore)
			|| !TestEqual(TEXT("rollback left the bool pin alone"), BoolPin->DefaultValue, BoolBefore))
		{
			return Fail();
		}
		TSharedPtr<FJsonObject> Repreview;
		FString RepreviewCode;
		FString RepreviewMessage;
		if (!TestTrue(
				TEXT("the same variable plan can be previewed again after the rollback"),
				UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
					UEAgentKitPatchExpansion::BuildPatchParams(Blueprint, Graph, { Op1, Op2, Op3 }),
					Repreview,
					RepreviewCode,
					RepreviewMessage))
			|| !TestEqual(
				TEXT("the re-preview derives the same plan id"),
				Repreview->GetStringField(TEXT("planId")),
				RollbackPlanId))
		{
			AddError(RepreviewCode + TEXT(" ") + RepreviewMessage);
			return Fail();
		}
	}

	// ---- Mixed kind: one patch creates a variable and sets a pin default. ----
	TSharedPtr<FJsonObject> ApplyResult;
	FString ApplyCode;
	FString ApplyMessage;
	const FString MixedVariableDefault = TEXT("1.5");
	{
		TSharedPtr<FJsonObject> PinOp = UEAgentKitPatchExpansion::MakeOperation(
			TEXT("p1"),
			NotBool,
			TEXT("A"),
			TEXT("bool"),
			TEXT("true"));
		TSharedPtr<FJsonObject> MixedVariableOp =
			MakeVariableOperation(TEXT("v1"), TEXT("AgentCount"), TEXT("double"), *MixedVariableDefault);
		if (!TestTrue(TEXT("mixed pin operation is built"), PinOp.IsValid()))
		{
			return Fail();
		}
		TSharedPtr<FJsonObject> MixedPreview;
		FString MixedCode;
		FString MixedMessage;
		if (!TestTrue(
				TEXT("mixed pin/variable patch previews"),
				UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
					UEAgentKitPatchExpansion::BuildPatchParams(
						Blueprint,
						Graph,
						{ PinOp, MixedVariableOp }),
					MixedPreview,
					MixedCode,
					MixedMessage))
			|| !TestTrue(TEXT("mixed preview result exists"), MixedPreview.IsValid()))
		{
			AddError(MixedCode + TEXT(" ") + MixedMessage);
			return Fail();
		}
		const TArray<TSharedPtr<FJsonValue>> MixedOperationsCheck =
			MixedPreview->GetArrayField(TEXT("operations"));
		if (!TestEqual(TEXT("mixed preview keeps both operations"), MixedOperationsCheck.Num(), 2)
			|| !TestEqual(
				TEXT("mixed preview keeps the request order (pin first)"),
				MixedOperationsCheck[0]->AsObject()->GetStringField(TEXT("kind")),
				FString(TEXT("setPinDefault")))
			|| !TestEqual(
				TEXT("mixed preview keeps the request order (variable second)"),
				MixedOperationsCheck[1]->AsObject()->GetStringField(TEXT("kind")),
				FString(TEXT("createVariable"))))
		{
			return Fail();
		}
		const FString MixedConfirmation =
			MixedPreview->GetStringField(TEXT("requiredConfirmation"));
		if (!TestTrue(
				TEXT("mixed pin/variable patch applies"),
				UEAgentKitBlueprintPatchExperimental::TryApplyExperimental(
					UEAgentKitPatchExpansion::BuildApplyParams(
						MixedPreview,
						{ PinOp, MixedVariableOp },
						MixedConfirmation),
					ApplyResult,
					ApplyCode,
					ApplyMessage))
			|| !TestTrue(TEXT("mixed apply result exists"), ApplyResult.IsValid()))
		{
			AddError(ApplyCode + TEXT(" ") + ApplyMessage);
			return Fail();
		}
	}

	const TArray<TSharedPtr<FJsonValue>> ApplyResults = ApplyResult->GetArrayField(TEXT("results"));
	if (!TestTrue(TEXT("mixed apply reports applied"), ApplyResult->GetBoolField(TEXT("applied")))
		|| !TestTrue(TEXT("mixed apply reports writeOccurred"), ApplyResult->GetBoolField(TEXT("writeOccurred")))
		|| !TestTrue(TEXT("mixed apply is one transaction"), ApplyResult->GetBoolField(TEXT("singleTransaction")))
		|| !TestEqual(TEXT("mixed apply transaction count"), ApplyResult->GetNumberField(TEXT("transactionCount")), 1.0)
		|| !TestTrue(TEXT("mixed apply verified undo"), ApplyResult->GetBoolField(TEXT("undoVerified")))
		|| !TestTrue(TEXT("mixed apply verified redo"), ApplyResult->GetBoolField(TEXT("redoVerified")))
		|| !TestFalse(TEXT("mixed apply never saves"), ApplyResult->GetBoolField(TEXT("saved")))
		|| !TestEqual(TEXT("mixed apply reports every operation"), ApplyResults.Num(), 2)
		|| !TestEqual(
			TEXT("first mixed result is applied"),
			ApplyResults[0]->AsObject()->GetStringField(TEXT("status")),
			FString(TEXT("applied")))
		|| !TestEqual(
			TEXT("second mixed result is applied"),
			ApplyResults[1]->AsObject()->GetStringField(TEXT("status")),
			FString(TEXT("applied")))
		|| !TestTrue(TEXT("mixed apply left the package dirty"), Package->IsDirty()))
	{
		return Fail();
	}
	const int32 CreatedVariableIndex = FindVariableIndexByName(Blueprint, TEXT("AgentCount"));
	const FString VariablesFingerprintAfter =
		ApplyResult->GetStringField(TEXT("variablesFingerprintAfter"));
	if (!TestEqual(TEXT("bool pin physically changed"), BoolPin->DefaultValue, FString(TEXT("true")))
		|| !TestTrue(TEXT("the variable was created"), CreatedVariableIndex != INDEX_NONE)
		|| !TestEqual(
			TEXT("the variable reads back with the requested default"),
			CreatedVariableIndex != INDEX_NONE
				? Blueprint->NewVariables[CreatedVariableIndex].DefaultValue
				: FString(),
			FString(MixedVariableDefault))
		|| !TestEqual(
			TEXT("the variable reads back with the requested type"),
			CreatedVariableIndex != INDEX_NONE
				? Blueprint->NewVariables[CreatedVariableIndex].VarType.PinCategory.ToString()
				: FString(),
			FString(TEXT("real")))
		|| !TestEqual(
			TEXT("the created GUID is reported"),
			CreatedVariableIndex != INDEX_NONE
				? ApplyResults[1]->AsObject()->GetStringField(TEXT("guid"))
				: FString(),
			CreatedVariableIndex != INDEX_NONE
				? Blueprint->NewVariables[CreatedVariableIndex]
					  .VarGuid.ToString(EGuidFormats::DigitsWithHyphensLower)
				: FString())
		|| !TestEqual(
			TEXT("variable count grew by exactly the created variable"),
			ApplyResult->GetNumberField(TEXT("variableCountAfter")),
			static_cast<double>(VariableCountBefore + 1))
		|| !TestEqual(
			TEXT("variable count before matches the fixture"),
			ApplyResult->GetNumberField(TEXT("variableCountBefore")),
			static_cast<double>(VariableCountBefore))
		|| !TestEqual(
			TEXT("node count is unchanged by a pin/variable patch"),
			ApplyResult->GetNumberField(TEXT("nodeCountAfter")),
			ApplyResult->GetNumberField(TEXT("nodeCountBefore")))
		|| !TestEqual(
			TEXT("link count is unchanged by a pin/variable patch"),
			ApplyResult->GetNumberField(TEXT("linkCountAfter")),
			ApplyResult->GetNumberField(TEXT("linkCountBefore")))
		|| !TestFalse(
			TEXT("the variable fingerprint changed"),
			VariablesFingerprintAfter.Equals(
				ApplyResult->GetStringField(TEXT("variablesFingerprintBefore")),
				ESearchCase::CaseSensitive)))
	{
		return Fail();
	}

	// The receipt must be what the package now contains, so a second Apply of the
	// same plan is refused on the dirty package.
	{
		TSharedPtr<FJsonObject> SecondResult;
		FString SecondCode;
		FString SecondMessage;
		if (!TestFalse(
				TEXT("re-applying a variable patch on a dirty package is refused"),
				UEAgentKitBlueprintPatchExperimental::TryApplyExperimental(
					UEAgentKitPatchExpansion::BuildApplyParams(
						Preview,
						{ VariableOp },
						RequiredConfirmation),
					SecondResult,
					SecondCode,
					SecondMessage))
			|| !TestEqual(
				TEXT("dirty package error"),
				SecondCode,
				FString(TEXT("graph-patch-package-dirty"))))
		{
			return Fail();
		}
	}

	// ---- Explicit Save, then a cross-module check on the saved state. ----
	{
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
				TEXT("explicit variable patch save succeeds"),
				UEAgentKitBlueprintPatchExperimental::TrySaveExperimental(
					SaveParams,
					SaveResult,
					SaveCode,
					SaveMessage))
			|| !TestTrue(TEXT("save result exists"), SaveResult.IsValid()))
		{
			AddError(SaveCode + TEXT(" ") + SaveMessage);
			return Fail();
		}
		const FString AssetShaAfterSave = ComputeAssetSha(Blueprint);
		if (!TestTrue(TEXT("save reports saved"), SaveResult->GetBoolField(TEXT("saved")))
			|| !TestFalse(TEXT("save cleared the dirty flag"), Package->IsDirty())
			|| !TestEqual(
				TEXT("save reported the variable fingerprint of the applied state"),
				SaveResult->GetStringField(TEXT("variablesFingerprint")),
				VariablesFingerprintAfter)
			|| !TestEqual(
				TEXT("save reported the live variable count"),
				SaveResult->GetNumberField(TEXT("variableCount")),
				static_cast<double>(VariableCountBefore + 1))
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
	}

	// After the save the package is clean again, so the single variable writer's
	// own preview must report exactly the fingerprint the patch reported as its
	// after state.  That is the cross-module proof the two paths compute the same
	// thing on the post-patch Blueprint.
	{
		TSharedPtr<FJsonObject> SingleAfterSave;
		FString SingleAfterSaveCode;
		FString SingleAfterSaveMessage;
		if (!TestTrue(
				TEXT("single-writer variable preview succeeds on the saved state"),
				UEAgentKitBlueprintVariableExperimental::TryPreviewExperimental(
					MakeSingleVariablePreviewParams(
						Blueprint,
						TEXT("NextVar"),
						TEXT("bool"),
						TEXT("true")),
					SingleAfterSave,
					SingleAfterSaveCode,
					SingleAfterSaveMessage))
			|| !TestTrue(TEXT("single-writer post-save preview exists"), SingleAfterSave.IsValid()))
		{
			AddError(SingleAfterSaveCode + TEXT(" ") + SingleAfterSaveMessage);
			return Fail();
		}
		if (!TestEqual(
				TEXT("the patch's after fingerprint is the variable writer's view of the saved state"),
				SingleAfterSave->GetStringField(TEXT("variablesFingerprint")),
				VariablesFingerprintAfter)
			|| !TestEqual(
				TEXT("the variable writer counts the created variable"),
				SingleAfterSave->GetNumberField(TEXT("variableCountBefore")),
				static_cast<double>(VariableCountBefore + 1)))
		{
			return Fail();
		}
		TSharedPtr<FJsonObject> AfterSavePreview;
		FString AfterSaveCode;
		FString AfterSaveMessage;
		TSharedPtr<FJsonObject> NextVariableOp =
			MakeVariableOperation(TEXT("v2"), TEXT("AnotherVar"), TEXT("bool"), TEXT("true"));
		if (!TestTrue(
				TEXT("variable patch preview after save succeeds"),
				UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
					UEAgentKitPatchExpansion::BuildPatchParams(
						Blueprint,
						Graph,
						{ NextVariableOp }),
					AfterSavePreview,
					AfterSaveCode,
					AfterSaveMessage)))
		{
			AddError(AfterSaveCode + TEXT(" ") + AfterSaveMessage);
			return Fail();
		}
		// The variable created by the patch is part of the new before state, and
		// creating the same name again must now be refused as a duplicate.
		{
			TSharedPtr<FJsonObject> DuplicateAfterSave;
			FString DuplicateAfterSaveCode;
			FString DuplicateAfterSaveMessage;
			if (!TestFalse(
					TEXT("re-creating the saved variable is refused"),
					UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
						UEAgentKitPatchExpansion::BuildPatchParams(
							Blueprint,
							Graph,
							{ VariableOp }),
						DuplicateAfterSave,
						DuplicateAfterSaveCode,
						DuplicateAfterSaveMessage))
				|| !TestEqual(
					TEXT("re-creating the saved variable reports the duplicate"),
					DuplicateAfterSaveCode,
					FString(TEXT("blueprint-variable-duplicate-name"))))
			{
				return Fail();
			}
		}
		if (!TestEqual(
				TEXT("the saved variable is part of the new before state"),
				AfterSavePreview->GetStringField(TEXT("variablesFingerprint")),
				VariablesFingerprintAfter)
			|| !TestEqual(
				TEXT("the saved state counts the created variable"),
				AfterSavePreview->GetNumberField(TEXT("variableCount")),
				static_cast<double>(VariableCountBefore + 1)))
		{
			return Fail();
		}
	}

	UEAgentKitBlueprintPatchExperimental::ResetExperimentalWriteForTest();
	UEAgentKitBlueprintPinDefaultExperimental::ResetExperimentalWriteForTest();
	UEAgentKitBlueprintVariableExperimental::ResetExperimentalWriteForTest();
	return true;
}

#endif // WITH_DEV_AUTOMATION_TESTS
