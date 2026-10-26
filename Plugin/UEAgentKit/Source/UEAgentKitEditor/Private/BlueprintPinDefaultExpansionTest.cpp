// Real-Editor acceptance for P1 typed pin-default edits.
//
// The fixture has four already-existing, unlinked input pins (bool/int/double/
// string), one deliberately connected bool input, and no variable-node creation.
// The test proves Preview is read-only, invalid targets fail closed, stale
// previews are rejected, Apply changes the actual pin defaults in one
// transaction, Undo/Redo restore them, and explicit Save writes the exact asset.

#include "BlueprintContextSha256.h"
#include "BlueprintEditor.h"
#include "BlueprintGraphLayoutApply.h"
#include "BlueprintPinDefaultEditExperimental.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "EdGraphSchema_K2.h"
#include "Editor.h"
#include "Engine/Blueprint.h"
#include "GameFramework/Actor.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformProcess.h"
#include "HAL/PlatformTime.h"
#include "K2Node_CallFunction.h"
#include "K2Node_IfThenElse.h"
#include "Kismet/KismetMathLibrary.h"
#include "Kismet/KismetStringLibrary.h"
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
#include "Tests/AutomationEditorCommon.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"

#if WITH_DEV_AUTOMATION_TESTS

namespace UEAgentKitPinDefaultExpansion
{
	const TCHAR* const AssetPath =
		TEXT("/Game/UEAgentKitPinDefaultExpansion/BP_PinDefaultExpansion.BP_PinDefaultExpansion");
	const TCHAR* const PackageName = TEXT("/Game/UEAgentKitPinDefaultExpansion/BP_PinDefaultExpansion");
	const TCHAR* const AssetName = TEXT("BP_PinDefaultExpansion");

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

	/**
	 * A previous run of this disposable fixture may have left the .uasset on
	 * disk.  UnrealEditor then treats a same-named fresh package as "partially
	 * loaded" (nonzero file size) and SavePackage refuses to overwrite it, so
	 * remove the stale fixture bytes first.  Only this test's dedicated
	 * disposable package path is touched.
	 */
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
		if (!UPackage::SavePackage(Package, Blueprint, *Filename, SaveArgs))
		{
			UE_LOG(
				LogTemp,
				Error,
				TEXT("PinDefault fixture SavePackage failed package=%s file=%s dirty=%s fullyLoaded=%s"),
				*Package->GetName(),
				*Filename,
				Package->IsDirty() ? TEXT("true") : TEXT("false"),
				Package->IsFullyLoaded() ? TEXT("true") : TEXT("false"));
			return false;
		}
		return true;
	}

	int32 CountLinks(UEdGraph* Graph)
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
			OutError = TEXT("PinDefault expansion fixture already exists; use a fresh project.");
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
			FName(TEXT("UEAgentKitPinDefaultExpansion")));
		if (Blueprint == nullptr || Blueprint->UbergraphPages.IsEmpty())
		{
			OutError = TEXT("Could not create the PinDefault expansion Blueprint.");
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
		UK2Node_CallFunction* DoubleAdd = AddCallFunction(
			Graph,
			UKismetMathLibrary::StaticClass(),
			TEXT("Add_DoubleDouble"),
			340,
			220);
		UK2Node_CallFunction* StringConcat = AddCallFunction(
			Graph,
			UKismetStringLibrary::StaticClass(),
			TEXT("Concat_StrStr"),
			340,
			440);
		if (NotBool == nullptr || IntAdd == nullptr || DoubleAdd == nullptr || StringConcat == nullptr)
		{
			OutError = TEXT("Could not create one of the function nodes.");
			return nullptr;
		}

		const UEdGraphSchema_K2* Schema = Cast<UEdGraphSchema_K2>(Graph->GetSchema());
		if (Schema == nullptr)
		{
			OutError = TEXT("The fixture graph is not a K2 graph.");
			return nullptr;
		}
		UEdGraphPin* NotBoolOutput = NotBool->GetReturnValuePin();
		UEdGraphPin* BranchCondition = Branch->FindPin(UEdGraphSchema_K2::PN_Condition, EGPD_Input);
		if (NotBoolOutput == nullptr || BranchCondition == nullptr
			|| !Schema->TryCreateConnection(NotBoolOutput, BranchCondition))
		{
			OutError = TEXT("Could not create the deliberately connected bool input.");
			return nullptr;
		}

		FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
		FKismetEditorUtilities::CompileBlueprint(Blueprint, EBlueprintCompileOptions::SkipGarbageCollection);
		if (Blueprint->Status == BS_Error)
		{
			OutError = TEXT("PinDefault expansion fixture failed to compile.");
			return nullptr;
		}
		FAssetRegistryModule::AssetCreated(Blueprint);
		Package->MarkPackageDirty();
		if (!SaveBlueprint(Blueprint))
		{
			OutError = TEXT("Could not save the PinDefault expansion fixture.");
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

	TSharedPtr<FJsonObject> MakeDefault(
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
		Item->SetStringField(TEXT("nodeGuid"), GuidLower(Node->NodeGuid));
		Item->SetStringField(TEXT("pinName"), PinName);
		Item->SetStringField(TEXT("pinGuid"), GuidLower(Pin->PinId));
		Item->SetStringField(TEXT("type"), Type);
		Item->SetStringField(TEXT("value"), Value);
		return Item;
	}

	TSharedPtr<FJsonObject> BuildPreviewParams(
		UBlueprint* Blueprint,
		UEdGraph* Graph,
		UEdGraphNode* BoolNode,
		UEdGraphNode* IntAdd,
		UEdGraphNode* DoubleAdd,
		UEdGraphNode* StringConcat,
		const TCHAR* BoolValue = TEXT("true"),
		const TCHAR* IntValue = TEXT("-7"),
		const TCHAR* DoubleValue = TEXT("3.25"),
		const TCHAR* StringValue = TEXT("hello"))
	{
		TArray<TSharedPtr<FJsonValue>> Defaults;
		Defaults.Add(MakeShared<FJsonValueObject>(
			MakeDefault(BoolNode, TEXT("A"), TEXT("bool"), BoolValue)));
		Defaults.Add(MakeShared<FJsonValueObject>(
			MakeDefault(IntAdd, TEXT("A"), TEXT("int"), IntValue)));
		Defaults.Add(MakeShared<FJsonValueObject>(
			MakeDefault(DoubleAdd, TEXT("A"), TEXT("double"), DoubleValue)));
		Defaults.Add(MakeShared<FJsonValueObject>(
			MakeDefault(StringConcat, TEXT("A"), TEXT("string"), StringValue)));

		TSharedRef<FJsonObject> Params = MakeShared<FJsonObject>();
		Params->SetStringField(TEXT("assetPath"), Blueprint->GetPathName());
		Params->SetStringField(TEXT("graphGuid"), GuidLower(Graph->GraphGuid));
		Params->SetArrayField(TEXT("defaults"), Defaults);
		return Params;
	}

	TSharedPtr<FJsonObject> BuildSinglePreviewParams(
		UBlueprint* Blueprint,
		UEdGraph* Graph,
		const TSharedPtr<FJsonObject>& Default)
	{
		TArray<TSharedPtr<FJsonValue>> Defaults;
		Defaults.Add(MakeShared<FJsonValueObject>(Default));
		TSharedRef<FJsonObject> Params = MakeShared<FJsonObject>();
		Params->SetStringField(TEXT("assetPath"), Blueprint->GetPathName());
		Params->SetStringField(TEXT("graphGuid"), GuidLower(Graph->GraphGuid));
		Params->SetArrayField(TEXT("defaults"), Defaults);
		return Params;
	}

	TSharedPtr<FJsonObject> BuildApplyParams(
		const TSharedPtr<FJsonObject>& Preview,
		const FString& Confirmation)
	{
		TSharedRef<FJsonObject> Params = MakeShared<FJsonObject>();
		Params->SetStringField(TEXT("assetPath"), Preview->GetStringField(TEXT("assetPath")));
		Params->SetStringField(TEXT("graphGuid"), Preview->GetStringField(TEXT("graphGuid")));
		// Preview reports beforeValue/afterValue; Apply accepts the strict value
		// envelope, so the requested value is the previewed afterValue.
		TArray<TSharedPtr<FJsonValue>> Defaults;
		for (const TSharedPtr<FJsonValue>& Value : Preview->GetArrayField(TEXT("defaults")))
		{
			const TSharedPtr<FJsonObject> Entry = Value.IsValid() ? Value->AsObject() : nullptr;
			if (!Entry.IsValid())
			{
				continue;
			}
			TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
			Item->SetStringField(TEXT("nodeGuid"), Entry->GetStringField(TEXT("nodeGuid")));
			Item->SetStringField(TEXT("pinName"), Entry->GetStringField(TEXT("pinName")));
			Item->SetStringField(TEXT("pinGuid"), Entry->GetStringField(TEXT("pinGuid")));
			Item->SetStringField(TEXT("type"), Entry->GetStringField(TEXT("type")));
			Item->SetStringField(TEXT("value"), Entry->GetStringField(TEXT("afterValue")));
			Defaults.Add(MakeShared<FJsonValueObject>(Item));
		}
		Params->SetArrayField(TEXT("defaults"), Defaults);
		Params->SetStringField(TEXT("planId"), Preview->GetStringField(TEXT("planId")));
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
		Params->SetStringField(
			TEXT("expectedDefaultsFingerprint"),
			ApplyResult->GetStringField(TEXT("defaultsFingerprintAfter")));
		Params->SetNumberField(
			TEXT("expectedNodeCount"),
			ApplyResult->GetNumberField(TEXT("nodeCountAfter")));
		Params->SetNumberField(
			TEXT("expectedLinkCount"),
			ApplyResult->GetNumberField(TEXT("linkCountAfter")));
		Params->SetStringField(TEXT("confirmation"), Confirmation);
		return Params;
	}

	bool InsertExpectedDefaults(
		TSharedRef<FJsonObject>& Evidence,
		UEdGraphNode* BoolNode,
		UEdGraphNode* IntAdd,
		UEdGraphNode* DoubleAdd,
		UEdGraphNode* StringConcat)
	{
		TArray<TSharedPtr<FJsonValue>> Expected;
		auto Add = [&Expected](UEdGraphNode* Node, const TCHAR* PinName, const TCHAR* Type)
		{
			UEdGraphPin* Pin = Node != nullptr
				? Node->FindPin(FName(PinName), EGPD_Input)
				: nullptr;
			if (Pin == nullptr)
			{
				return false;
			}
			TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
			Item->SetStringField(TEXT("nodeGuid"), GuidLower(Node->NodeGuid));
			Item->SetStringField(TEXT("pinName"), PinName);
			Item->SetStringField(TEXT("pinGuid"), GuidLower(Pin->PinId));
			Item->SetStringField(TEXT("type"), Type);
			Item->SetStringField(TEXT("value"), Pin->DefaultValue);
			Expected.Add(MakeShared<FJsonValueObject>(Item));
			return true;
		};
		if (!Add(BoolNode, TEXT("A"), TEXT("bool"))
			|| !Add(IntAdd, TEXT("A"), TEXT("int"))
			|| !Add(DoubleAdd, TEXT("A"), TEXT("double"))
			|| !Add(StringConcat, TEXT("A"), TEXT("string")))
		{
			return false;
		}
		Evidence->SetArrayField(TEXT("expectedDefaults"), Expected);
		return true;
	}
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitPinDefaultExpansionTest,
	"UEAgentKit.PinDefault.Expansion",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitPinDefaultExpansionTest::RunTest(const FString& Parameters)
{
	using namespace UEAgentKitPinDefaultExpansion;
	// The product gate must stay OFF for a project that never opted in; the
	// automation-only seam is the only thing that turns it on, and only for the
	// duration of this test.
	UEAgentKitBlueprintPinDefaultExperimental::ResetExperimentalWriteForTest();
	if (!TestFalse(
			TEXT("experimental pin default gate is off unless the project opts in"),
			UEAgentKitBlueprintPinDefaultExperimental::IsExperimentalWriteAvailable()))
	{
		return false;
	}
	bool bGateOffRejected = false;
	{
		TSharedPtr<FJsonObject> GateDefault = MakeShared<FJsonObject>();
		GateDefault->SetStringField(TEXT("nodeGuid"), TEXT("00000000-0000-4000-8000-000000000000"));
		GateDefault->SetStringField(TEXT("pinName"), TEXT("A"));
		GateDefault->SetStringField(TEXT("pinGuid"), TEXT("00000000-0000-4000-8000-000000000001"));
		GateDefault->SetStringField(TEXT("type"), TEXT("bool"));
		GateDefault->SetStringField(TEXT("value"), TEXT("true"));
		TArray<TSharedPtr<FJsonValue>> GateDefaults;
		GateDefaults.Add(MakeShared<FJsonValueObject>(GateDefault));
		TSharedPtr<FJsonObject> GateParams = MakeShared<FJsonObject>();
		GateParams->SetStringField(
			TEXT("assetPath"),
			TEXT("/Game/UEAgentKitPinDefaultExpansion/BP_PinDefaultExpansion.BP_PinDefaultExpansion"));
		GateParams->SetStringField(TEXT("graphGuid"), TEXT("00000000-0000-4000-8000-000000000002"));
		GateParams->SetArrayField(TEXT("defaults"), GateDefaults);
		TSharedPtr<FJsonObject> GateResult;
		FString GateCode;
		FString GateMessage;
		if (!TestFalse(
				TEXT("gate-off preview is rejected"),
				UEAgentKitBlueprintPinDefaultExperimental::TryPreviewExperimental(
					GateParams,
					GateResult,
					GateCode,
					GateMessage))
			|| !TestEqual(
				TEXT("gate-off preview error"),
				GateCode,
				FString(TEXT("graph-pin-default-experimental-disabled"))))
		{
			return false;
		}
		bGateOffRejected = true;
	}
	UEAgentKitBlueprintPinDefaultExperimental::SetExperimentalWriteEnabledForTest(true);
	auto Fail = []()
	{
		UEAgentKitBlueprintPinDefaultExperimental::ResetExperimentalWriteForTest();
		return false;
	};

	FString Error;
	UBlueprint* Blueprint = CreateFixture(Error);
	if (!TestNotNull(TEXT("PinDefault expansion fixture is created"), Blueprint))
	{
		AddError(Error);
		return Fail();
	}
	UEdGraph* Graph = Blueprint->UbergraphPages[0];
	FBlueprintEditor* Editor = OpenAndFocus(Blueprint, Graph, Error);
	if (!TestNotNull(TEXT("PinDefault expansion graph is focused"), Editor))
	{
		AddError(Error);
		return Fail();
	}

	auto* Branch = Cast<UK2Node_IfThenElse>(
		[&Graph]() -> UEdGraphNode*
		{
			for (UEdGraphNode* Node : Graph->Nodes)
			{
				if (Node != nullptr && Node->IsA<UK2Node_IfThenElse>())
				{
					return Node;
				}
			}
			return nullptr;
		}());
	if (!TestNotNull(TEXT("branch fixture exists"), Branch))
	{
		return Fail();
	}

	UK2Node_CallFunction* NotBool = nullptr;
	UK2Node_CallFunction* IntAdd = nullptr;
	UK2Node_CallFunction* DoubleAdd = nullptr;
	UK2Node_CallFunction* StringConcat = nullptr;
	for (UEdGraphNode* Node : Graph->Nodes)
	{
		UK2Node_CallFunction* Call = Cast<UK2Node_CallFunction>(Node);
		if (Call == nullptr || Call->GetFunctionName() == NAME_None)
		{
			continue;
		}
		const FName Name = Call->GetFunctionName();
		if (Name == TEXT("Not_PreBool"))
		{
			NotBool = Call;
		}
		else if (Name == TEXT("Add_IntInt"))
		{
			IntAdd = Call;
		}
		else if (Name == TEXT("Add_DoubleDouble"))
		{
			DoubleAdd = Call;
		}
		else if (Name == TEXT("Concat_StrStr"))
		{
			StringConcat = Call;
		}
	}
	if (!TestNotNull(TEXT("bool fixture exists"), NotBool)
		|| !TestNotNull(TEXT("int add fixture exists"), IntAdd)
		|| !TestNotNull(TEXT("double add fixture exists"), DoubleAdd)
		|| !TestNotNull(TEXT("string concat fixture exists"), StringConcat))
	{
		return Fail();
	}

	const int32 RootNodeCount = Graph->Nodes.Num();
	const int32 RootLinkCount = CountLinks(Graph);

	UEdGraphPin* IntB = IntAdd->FindPin(TEXT("B"), EGPD_Input);
	UEdGraphPin* IntA = IntAdd->FindPin(TEXT("A"), EGPD_Input);
	UEdGraphPin* BoolA = NotBool->FindPin(TEXT("A"), EGPD_Input);
	UEdGraphPin* BranchCondition = Branch->FindPin(
		UEdGraphSchema_K2::PN_Condition,
		EGPD_Input);
	if (!TestNotNull(TEXT("int B fixture pin exists"), IntB)
		|| !TestNotNull(TEXT("int A fixture pin exists"), IntA)
		|| !TestNotNull(TEXT("bool fixture pin exists"), BoolA)
		|| !TestNotNull(TEXT("connected bool fixture pin exists"), BranchCondition))
	{
		return Fail();
	}
	TestTrue(TEXT("bool pin has a persistent ID"), BoolA->PinId.IsValid());
	TestTrue(TEXT("int A pin has a persistent ID"), IntA->PinId.IsValid());

	// Connected input must be rejected before mutation.
	TSharedPtr<FJsonObject> Params = BuildPreviewParams(
		Blueprint,
		Graph,
		NotBool,
		IntAdd,
		DoubleAdd,
		StringConcat);

	// A dirty package must fail closed even for an otherwise valid request, and
	// the rejection must not touch any pin default.
	const FString DirtyIntBefore = IntA->DefaultValue;
	Blueprint->GetOutermost()->MarkPackageDirty();
	TSharedPtr<FJsonObject> DirtyResult;
	FString DirtyCode;
	FString DirtyMessage;
	const bool bDirtyRejected =
		!UEAgentKitBlueprintPinDefaultExperimental::TryPreviewExperimental(
			Params,
			DirtyResult,
			DirtyCode,
			DirtyMessage);
	if (!TestTrue(TEXT("dirty package preview is rejected"), bDirtyRejected)
		|| !TestEqual(
			TEXT("dirty package error"),
			DirtyCode,
			FString(TEXT("graph-pin-default-package-dirty")))
		|| !TestEqual(TEXT("dirty rejection does not mutate int A"), IntA->DefaultValue, DirtyIntBefore)
		|| !TestTrue(TEXT("dirty fixture can be saved clean again"), SaveBlueprint(Blueprint))
		|| !TestFalse(
			TEXT("dirty fixture is clean again"),
			Blueprint->GetOutermost()->IsDirty()))
	{
		AddError(DirtyCode + TEXT(": ") + DirtyMessage);
		return Fail();
	}

	TSharedPtr<FJsonObject> Invalid = BuildPreviewParams(
		Blueprint,
		Graph,
		Branch,
		IntAdd,
		DoubleAdd,
		StringConcat);
	TArray<TSharedPtr<FJsonValue>> InvalidDefaults;
	InvalidDefaults.Add(MakeShared<FJsonValueObject>(
		MakeDefault(Branch, TEXT("Condition"), TEXT("bool"), TEXT("false"))));
	Invalid->SetArrayField(TEXT("defaults"), InvalidDefaults);
	TestTrue(TEXT("connected-input request object is valid"), Invalid.IsValid());
	TestTrue(TEXT("connected-input request has fields"), Invalid.IsValid() && Invalid->Values.Num() >= 3);
	TSharedPtr<FJsonObject> InvalidResult;
	FString InvalidCode;
	FString InvalidMessage;
	const bool bConnectedRejected =
		!UEAgentKitBlueprintPinDefaultExperimental::TryPreviewExperimental(
			Invalid,
			InvalidResult,
			InvalidCode,
			InvalidMessage);
	if (!bConnectedRejected || InvalidCode != TEXT("graph-pin-default-pin-connected"))
	{
		AddError(TEXT("connected input rejection detail: ") + InvalidCode + TEXT(": ") + InvalidMessage);
	}
	if (!TestTrue(TEXT("connected input is rejected"), bConnectedRejected)
		|| !TestEqual(
			TEXT("connected input error"),
			InvalidCode,
			FString(TEXT("graph-pin-default-pin-connected")))
		|| !TestEqual(TEXT("connected preview did not mutate nodes"), Graph->Nodes.Num(), RootNodeCount))
	{
		return Fail();
	}

	// Wrong pin GUID, wrong declared type and oversized string all fail closed.
	TSharedPtr<FJsonObject> BadGuidDefault = MakeDefault(IntAdd, TEXT("A"), TEXT("int"), TEXT("-7"));
	BadGuidDefault->SetStringField(
		TEXT("pinGuid"),
		TEXT("00000000-0000-0000-0000-000000000000"));
	TSharedPtr<FJsonObject> BadGuidParams = BuildSinglePreviewParams(
		Blueprint,
		Graph,
		BadGuidDefault);
	if (!TestFalse(
		TEXT("wrong pinGuid is rejected"),
		UEAgentKitBlueprintPinDefaultExperimental::TryPreviewExperimental(
			BadGuidParams,
			InvalidResult,
			InvalidCode,
			InvalidMessage))
		|| !TestEqual(
			TEXT("wrong pinGuid error"),
			InvalidCode,
			FString(TEXT("graph-pin-default-pin-guid-mismatch"))))
	{
		return Fail();
	}

	TSharedPtr<FJsonObject> WrongTypeDefault = MakeDefault(
		DoubleAdd,
		TEXT("A"),
		TEXT("int"),
		TEXT("7"));
	TSharedPtr<FJsonObject> WrongTypeParams = BuildSinglePreviewParams(
		Blueprint,
		Graph,
		WrongTypeDefault);
	if (!TestFalse(
		TEXT("wrong declared type is rejected"),
		UEAgentKitBlueprintPinDefaultExperimental::TryPreviewExperimental(
			WrongTypeParams,
			InvalidResult,
			InvalidCode,
			InvalidMessage))
		|| !TestEqual(
			TEXT("wrong declared type error"),
			InvalidCode,
			FString(TEXT("graph-pin-default-pin-type-mismatch"))))
	{
		return Fail();
	}

	TSharedPtr<FJsonObject> OversizeDefault = MakeDefault(
		StringConcat,
		TEXT("A"),
		TEXT("string"),
		*FString::ChrN(1025, TEXT('x')));
	TSharedPtr<FJsonObject> OversizeParams = BuildSinglePreviewParams(
		Blueprint,
		Graph,
		OversizeDefault);
	if (!TestFalse(
		TEXT("oversized string is rejected"),
		UEAgentKitBlueprintPinDefaultExperimental::TryPreviewExperimental(
			OversizeParams,
			InvalidResult,
			InvalidCode,
			InvalidMessage))
		|| !TestEqual(
			TEXT("oversized string error"),
			InvalidCode,
			FString(TEXT("graph-pin-default-value-invalid"))))
	{
		return Fail();
	}

	// Unknown fields must never reach the write path.
	TSharedPtr<FJsonObject> UnknownParams = BuildSinglePreviewParams(
		Blueprint,
		Graph,
		MakeDefault(IntAdd, TEXT("A"), TEXT("int"), TEXT("-7")));
	UnknownParams->SetStringField(TEXT("unexpectedField"), TEXT("x"));
	if (!TestFalse(
		TEXT("unknown parameter is rejected"),
		UEAgentKitBlueprintPinDefaultExperimental::TryPreviewExperimental(
			UnknownParams,
			InvalidResult,
			InvalidCode,
			InvalidMessage))
		|| !TestEqual(
			TEXT("unknown parameter error"),
			InvalidCode,
			FString(TEXT("graph-pin-default-unknown-parameter"))))
	{
		return Fail();
	}

	// Read-only preview must not mutate any default.
	const FString IntBefore = IntA->DefaultValue;
	const FString IntBBefore = IntB->DefaultValue;
	const FString ConnectedBoolBefore = BranchCondition->DefaultValue;
	TSharedPtr<FJsonObject> Preview;
	FString PreviewCode;
	FString PreviewMessage;
	if (!TestTrue(
		TEXT("typed four-pin preview succeeds"),
		UEAgentKitBlueprintPinDefaultExperimental::TryPreviewExperimental(
			Params,
			Preview,
			PreviewCode,
			PreviewMessage)))
	{
		AddError(PreviewCode + TEXT(": ") + PreviewMessage);
		return Fail();
	}
	if (!TestTrue(TEXT("preview is read-only"), Preview->GetBoolField(TEXT("readOnly")))
		|| !TestEqual(TEXT("preview does not mutate int A"), IntA->DefaultValue, IntBefore)
		|| !TestEqual(TEXT("preview reports four defaults"), static_cast<int32>(
			Preview->GetNumberField(TEXT("defaultCount"))), 4))
	{
		return Fail();
	}

	// A clean graph mutation outside the plan (then saved) must stale the preview.
	const FString StalePlanId = Preview->GetStringField(TEXT("planId"));
	IntB->DefaultValue = TEXT("999");
	Blueprint->GetOutermost()->MarkPackageDirty();
	if (!TestTrue(TEXT("stale fixture mutation saves"), SaveBlueprint(Blueprint)))
	{
		return Fail();
	}
	TSharedPtr<FJsonObject> StaleResult;
	FString StaleCode;
	FString StaleMessage;
	const bool bStaleRejected =
		!UEAgentKitBlueprintPinDefaultExperimental::TryApplyExperimental(
			BuildApplyParams(
				Preview,
				FString(UEAgentKitBlueprintPinDefaultExperimental::ApplyConfirmationPrefix) + StalePlanId),
			StaleResult,
			StaleCode,
			StaleMessage);
	if (!TestTrue(TEXT("stale default preview is rejected"), bStaleRejected)
		|| !TestEqual(
			TEXT("stale default preview error"),
			StaleCode,
			FString(TEXT("graph-pin-default-plan-stale")))
		|| !TestEqual(TEXT("stale apply did not change int A"), IntA->DefaultValue, IntBefore))
	{
		return Fail();
	}
	IntB->DefaultValue = IntBBefore;
	Blueprint->GetOutermost()->MarkPackageDirty();
	if (!TestTrue(TEXT("stale fixture restore saves"), SaveBlueprint(Blueprint)))
	{
		return Fail();
	}

	// Fresh preview after the stale rejection.
	Params = BuildPreviewParams(
		Blueprint,
		Graph,
		NotBool,
		IntAdd,
		DoubleAdd,
		StringConcat);
	if (!TestTrue(
		TEXT("fresh typed preview succeeds"),
		UEAgentKitBlueprintPinDefaultExperimental::TryPreviewExperimental(
			Params,
			Preview,
			PreviewCode,
			PreviewMessage)))
	{
		AddError(PreviewCode + TEXT(": ") + PreviewMessage);
		return Fail();
	}
	const FString PlanId = Preview->GetStringField(TEXT("planId"));
	const FString ApplyConfirmation =
		FString(UEAgentKitBlueprintPinDefaultExperimental::ApplyConfirmationPrefix) + PlanId;

	// Apply without the exact confirmation token must fail closed.
	TSharedPtr<FJsonObject> BadConfirmResult;
	FString BadConfirmCode;
	FString BadConfirmMessage;
	if (!TestFalse(
		TEXT("Apply without the exact confirmation is rejected"),
		UEAgentKitBlueprintPinDefaultExperimental::TryApplyExperimental(
			BuildApplyParams(Preview, TEXT("SET EXPERIMENTAL BLUEPRINT PIN DEFAULTS wrong")),
			BadConfirmResult,
			BadConfirmCode,
			BadConfirmMessage))
		|| !TestEqual(
			TEXT("Apply confirmation error"),
			BadConfirmCode,
			FString(TEXT("graph-pin-default-confirmation-required"))))
	{
		return Fail();
	}

	// Save before any Apply in this Editor session must fail closed.
	TSharedPtr<FJsonObject> PrematureSave = MakeShared<FJsonObject>();
	PrematureSave->SetStringField(TEXT("assetPath"), Preview->GetStringField(TEXT("assetPath")));
	PrematureSave->SetStringField(TEXT("graphGuid"), Preview->GetStringField(TEXT("graphGuid")));
	PrematureSave->SetStringField(TEXT("planId"), PlanId);
	PrematureSave->SetStringField(
		TEXT("expectedGraphFingerprint"),
		Preview->GetStringField(TEXT("graphFingerprint")));
	PrematureSave->SetStringField(
		TEXT("expectedDefaultsFingerprint"),
		Preview->GetStringField(TEXT("defaultsFingerprint")));
	PrematureSave->SetNumberField(TEXT("expectedNodeCount"), RootNodeCount);
	PrematureSave->SetNumberField(TEXT("expectedLinkCount"), RootLinkCount);
	PrematureSave->SetStringField(
		TEXT("confirmation"),
		FString(UEAgentKitBlueprintPinDefaultExperimental::SaveConfirmationPrefix) + PlanId);
	TSharedPtr<FJsonObject> PrematureSaveResult;
	FString PrematureSaveCode;
	FString PrematureSaveMessage;
	if (!TestFalse(
		TEXT("Save before Apply is rejected"),
		UEAgentKitBlueprintPinDefaultExperimental::TrySaveExperimental(
			PrematureSave,
			PrematureSaveResult,
			PrematureSaveCode,
			PrematureSaveMessage))
		|| !TestEqual(
			TEXT("Save before Apply error"),
			PrematureSaveCode,
			FString(TEXT("graph-pin-default-save-requires-apply"))))
	{
		return Fail();
	}

	TSharedPtr<FJsonObject> ApplyResult;
	FString ApplyCode;
	FString ApplyMessage;
	if (!TestTrue(
		TEXT("typed four-pin apply succeeds"),
		UEAgentKitBlueprintPinDefaultExperimental::TryApplyExperimental(
			BuildApplyParams(Preview, ApplyConfirmation),
			ApplyResult,
			ApplyCode,
			ApplyMessage)))
	{
		AddError(ApplyCode + TEXT(": ") + ApplyMessage);
		return Fail();
	}
	if (!TestTrue(TEXT("Apply reports applied"), ApplyResult->GetBoolField(TEXT("applied")))
		|| !TestTrue(TEXT("Apply verifies Undo"), ApplyResult->GetBoolField(TEXT("undoVerified")))
		|| !TestTrue(TEXT("Apply verifies Redo"), ApplyResult->GetBoolField(TEXT("redoVerified")))
		|| !TestFalse(TEXT("Apply does not save"), ApplyResult->GetBoolField(TEXT("saved")))
		|| !TestEqual(
			TEXT("Apply preserves node count"),
			static_cast<int32>(ApplyResult->GetNumberField(TEXT("nodeCountAfter"))),
			RootNodeCount)
		|| !TestEqual(
			TEXT("Apply preserves link count"),
			static_cast<int32>(ApplyResult->GetNumberField(TEXT("linkCountAfter"))),
			RootLinkCount)
		|| !TestEqual(
			TEXT("Apply reports four defaults"),
			static_cast<int32>(ApplyResult->GetNumberField(TEXT("defaultCount"))),
			4))
	{
		return Fail();
	}

	UEdGraphPin* IntAPin = IntAdd->FindPin(TEXT("A"), EGPD_Input);
	UEdGraphPin* DoubleAPin = DoubleAdd->FindPin(TEXT("A"), EGPD_Input);
	UEdGraphPin* StringAPin = StringConcat->FindPin(TEXT("A"), EGPD_Input);
	UEdGraphPin* BoolAPin = NotBool->FindPin(TEXT("A"), EGPD_Input);
	if (!TestNotNull(TEXT("int A pin remains live"), IntAPin)
		|| !TestNotNull(TEXT("double A pin remains live"), DoubleAPin)
		|| !TestNotNull(TEXT("string A pin remains live"), StringAPin)
		|| !TestNotNull(TEXT("bool A pin remains live"), BoolAPin)
		|| !TestEqual(TEXT("bool default physically changed"), BoolAPin->DefaultValue, FString(TEXT("true")))
		|| !TestEqual(TEXT("connected bool default is untouched"), BranchCondition->DefaultValue, ConnectedBoolBefore)
		|| !TestEqual(TEXT("int default physically changed"), IntAPin->DefaultValue, FString(TEXT("-7")))
		|| !TestEqual(TEXT("double default physically changed"), DoubleAPin->DefaultValue, FString(TEXT("3.25")))
		|| !TestEqual(TEXT("string default physically changed"), StringAPin->DefaultValue, FString(TEXT("hello"))))
	{
		return Fail();
	}

	// Wrong Save fingerprints must fail before a package write.
	TSharedPtr<FJsonObject> WrongSave = BuildSaveParams(
		ApplyResult,
		FString(UEAgentKitBlueprintPinDefaultExperimental::SaveConfirmationPrefix) + PlanId);
	WrongSave->SetStringField(
		TEXT("expectedDefaultsFingerprint"),
		TEXT("sha256:") + FString::ChrN(64, TEXT('0')));
	TSharedPtr<FJsonObject> WrongSaveResult;
	FString WrongSaveCode;
	FString WrongSaveMessage;
	const bool bWrongSaveRejected =
		!UEAgentKitBlueprintPinDefaultExperimental::TrySaveExperimental(
			WrongSave,
			WrongSaveResult,
			WrongSaveCode,
			WrongSaveMessage);
	if (!TestTrue(TEXT("wrong Save fingerprint is rejected"), bWrongSaveRejected)
		|| !TestEqual(
			TEXT("wrong Save fingerprint error"),
			WrongSaveCode,
			FString(TEXT("graph-pin-default-save-plan-mismatch"))))
	{
		return Fail();
	}

	TSharedPtr<FJsonObject> SaveResult;
	FString SaveCode;
	FString SaveMessage;
	const FString SaveConfirmation =
		FString(UEAgentKitBlueprintPinDefaultExperimental::SaveConfirmationPrefix) + PlanId;
	const bool bSaved = UEAgentKitBlueprintPinDefaultExperimental::TrySaveExperimental(
		BuildSaveParams(ApplyResult, SaveConfirmation),
		SaveResult,
		SaveCode,
		SaveMessage);
	if (!TestTrue(TEXT("explicit pin default Save succeeds"), bSaved))
	{
		AddError(SaveCode + TEXT(": ") + SaveMessage);
		return Fail();
	}
	if (!TestTrue(TEXT("Save reports saved"), SaveResult->GetBoolField(TEXT("saved")))
		|| !TestTrue(TEXT("Save readback is verified"), SaveResult->GetBoolField(TEXT("readbackVerified")))
		|| !TestFalse(TEXT("Save leaves package clean"), SaveResult->GetBoolField(TEXT("packageDirty"))))
	{
		return Fail();
	}

	const FString AssetFilename = FPackageName::LongPackageNameToFilename(
		Blueprint->GetOutermost()->GetName(),
		FPackageName::GetAssetPackageExtension());
	FString DiskDigest;
	if (!TestTrue(TEXT("saved default package can be hashed"), FBlueprintContextSha256::HashFile(AssetFilename, DiskDigest)))
	{
		return Fail();
	}
	DiskDigest = FString(TEXT("sha256:")) + DiskDigest.ToLower();
	if (!TestEqual(
		TEXT("Save asset hash equals disk"),
		SaveResult->GetStringField(TEXT("assetSha256")),
		DiskDigest))
	{
		return Fail();
	}

	TSharedRef<FJsonObject> Evidence = MakeShared<FJsonObject>();
	Evidence->SetStringField(TEXT("schemaVersion"), TEXT("ueak.pin-default-expansion-evidence/1.0"));
	Evidence->SetNumberField(TEXT("processId"), static_cast<double>(FPlatformProcess::GetCurrentProcessId()));
	Evidence->SetStringField(TEXT("writtenAtUtc"), FDateTime::UtcNow().ToIso8601());
	Evidence->SetStringField(TEXT("assetPath"), Blueprint->GetPathName());
	Evidence->SetStringField(TEXT("assetFilename"), FPaths::ConvertRelativePathToFull(AssetFilename));
	Evidence->SetStringField(TEXT("graphGuid"), GuidLower(Graph->GraphGuid));
	Evidence->SetStringField(TEXT("planId"), PlanId);
	Evidence->SetNumberField(TEXT("rootNodeCount"), RootNodeCount);
	Evidence->SetNumberField(TEXT("rootLinkCount"), RootLinkCount);
	Evidence->SetNumberField(TEXT("nodeCountAfter"), ApplyResult->GetNumberField(TEXT("nodeCountAfter")));
	Evidence->SetNumberField(TEXT("linkCountAfter"), ApplyResult->GetNumberField(TEXT("linkCountAfter")));
	Evidence->SetNumberField(TEXT("defaultCount"), 4);
	Evidence->SetStringField(TEXT("assetSha256"), DiskDigest);
	Evidence->SetStringField(
		TEXT("graphFingerprintAfter"),
		ApplyResult->GetStringField(TEXT("graphFingerprintAfter")));
	Evidence->SetStringField(
		TEXT("defaultsFingerprintAfter"),
		ApplyResult->GetStringField(TEXT("defaultsFingerprintAfter")));
	Evidence->SetBoolField(TEXT("undoVerified"), ApplyResult->GetBoolField(TEXT("undoVerified")));
	Evidence->SetBoolField(TEXT("redoVerified"), ApplyResult->GetBoolField(TEXT("redoVerified")));
	Evidence->SetObjectField(TEXT("preview"), Preview);
	Evidence->SetObjectField(TEXT("apply"), ApplyResult);
	Evidence->SetObjectField(TEXT("save"), SaveResult);
	Evidence->SetArrayField(TEXT("appliedDefaults"), ApplyResult->GetArrayField(TEXT("defaults")));
	TSharedRef<FJsonObject> Checks = MakeShared<FJsonObject>();
	Checks->SetBoolField(TEXT("defaultGateOffRejected"), bGateOffRejected);
	Checks->SetBoolField(TEXT("dirtyPackageRejected"), bDirtyRejected);
	Checks->SetBoolField(TEXT("connectedPinRejected"), bConnectedRejected);
	Checks->SetBoolField(TEXT("stalePlanRejected"), bStaleRejected);
	Checks->SetBoolField(
		TEXT("wrongApplyConfirmationRejected"),
		BadConfirmCode.Equals(
			TEXT("graph-pin-default-confirmation-required"),
			ESearchCase::CaseSensitive));
	Checks->SetBoolField(
		TEXT("saveBeforeApplyRejected"),
		PrematureSaveCode.Equals(
			TEXT("graph-pin-default-save-requires-apply"),
			ESearchCase::CaseSensitive));
	Checks->SetBoolField(TEXT("wrongSaveFingerprintRejected"), bWrongSaveRejected);
	Checks->SetBoolField(TEXT("previewReadOnly"), Preview->GetBoolField(TEXT("readOnly")));
	Checks->SetBoolField(
		TEXT("nodeAndLinkCountPreserved"),
		static_cast<int32>(ApplyResult->GetNumberField(TEXT("nodeCountAfter"))) == RootNodeCount
			&& static_cast<int32>(ApplyResult->GetNumberField(TEXT("linkCountAfter"))) == RootLinkCount);
	Checks->SetBoolField(
		TEXT("undoRestoredBeforeValues"),
		ApplyResult->GetBoolField(TEXT("undoVerified")));
	Checks->SetBoolField(
		TEXT("redoReappliedAfterValues"),
		ApplyResult->GetBoolField(TEXT("redoVerified")));
	Checks->SetBoolField(
		TEXT("packageCleanAfterSave"),
		!SaveResult->GetBoolField(TEXT("packageDirty")));
	Checks->SetBoolField(
		TEXT("independentReloadPendingCommandlet"),
		!SaveResult->GetBoolField(TEXT("independentReloadVerified")));
	Evidence->SetObjectField(TEXT("checks"), Checks);
	if (!InsertExpectedDefaults(Evidence, NotBool, IntAdd, DoubleAdd, StringConcat))
	{
		AddError(TEXT("Could not serialize expected defaults."));
		return Fail();
	}

	TArray<TSharedPtr<FJsonValue>> ExpectedNodeGuids;
	for (UEdGraphNode* Node : Graph->Nodes)
	{
		if (Node != nullptr && Node->NodeGuid.IsValid())
		{
			ExpectedNodeGuids.Add(MakeShared<FJsonValueString>(GuidLower(Node->NodeGuid)));
		}
	}
	Evidence->SetArrayField(TEXT("expectedNodeGuids"), ExpectedNodeGuids);

	TArray<TSharedPtr<FJsonValue>> ExpectedLinks;
	for (UEdGraphNode* Node : Graph->Nodes)
	{
		if (Node == nullptr)
		{
			continue;
		}
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
				TSharedRef<FJsonObject> Link = MakeShared<FJsonObject>();
				Link->SetStringField(TEXT("fromNodeGuid"), GuidLower(Node->NodeGuid));
				Link->SetStringField(TEXT("fromPinName"), Pin->PinName.ToString());
				Link->SetStringField(TEXT("fromPinGuid"), GuidLower(Pin->PinId));
				Link->SetStringField(
					TEXT("toNodeGuid"),
					GuidLower(Linked->GetOwningNode()->NodeGuid));
				Link->SetStringField(TEXT("toPinName"), Linked->PinName.ToString());
				Link->SetStringField(TEXT("toPinGuid"), GuidLower(Linked->PinId));
				ExpectedLinks.Add(MakeShared<FJsonValueObject>(Link));
			}
		}
	}
	Evidence->SetArrayField(TEXT("expectedLinks"), ExpectedLinks);

	const FString EvidencePath = FPaths::Combine(
		FPaths::ProjectSavedDir(),
		TEXT("PinDefaultExpansion"),
		TEXT("evidence.json"));
	TestTrue(TEXT("PinDefault expansion evidence is written"), WriteJson(EvidencePath, Evidence));
	UEAgentKitBlueprintPinDefaultExperimental::ResetExperimentalWriteForTest();
	return true;
}

namespace UEAgentKitPinDefaultPieGate
{
	using namespace UEAgentKitPinDefaultExpansion;

	UK2Node_CallFunction* FindCallFunction(UEdGraph* Graph, const TCHAR* FunctionName)
	{
		for (UEdGraphNode* Node : Graph->Nodes)
		{
			UK2Node_CallFunction* Call = Cast<UK2Node_CallFunction>(Node);
			if (Call != nullptr && Call->GetFunctionName() == FName(FunctionName))
			{
				return Call;
			}
		}
		return nullptr;
	}

	struct FState
	{
		FAutomationTestBase* Test = nullptr;
		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = nullptr;
		UK2Node_CallFunction* NotBool = nullptr;
		UK2Node_CallFunction* IntAdd = nullptr;
		UK2Node_CallFunction* DoubleAdd = nullptr;
		UK2Node_CallFunction* StringConcat = nullptr;
		TArray<FString> DefaultsBefore;
		double StartedAt = 0.0;
		bool bCompleted = false;
	};

	TArray<UEdGraphPin*> FixturePins(const FState& State)
	{
		TArray<UEdGraphPin*> Pins;
		for (UEdGraphNode* Node : {
				static_cast<UEdGraphNode*>(State.NotBool),
				static_cast<UEdGraphNode*>(State.IntAdd),
				static_cast<UEdGraphNode*>(State.DoubleAdd),
				static_cast<UEdGraphNode*>(State.StringConcat) })
		{
			Pins.Add(Node != nullptr ? Node->FindPin(TEXT("A"), EGPD_Input) : nullptr);
		}
		return Pins;
	}

	bool CurrentDefaultsMatch(const TArray<UEdGraphPin*>& Pins, const TArray<FString>& Expected)
	{
		if (Pins.Num() != Expected.Num())
		{
			return false;
		}
		for (int32 Index = 0; Index < Pins.Num(); ++Index)
		{
			if (Pins[Index] == nullptr
				|| !Pins[Index]->DefaultValue.Equals(Expected[Index], ESearchCase::CaseSensitive))
			{
				return false;
			}
		}
		return true;
	}

	class FWaitForPieThenReject final : public IAutomationLatentCommand
	{
	public:
		explicit FWaitForPieThenReject(TSharedRef<FState> InState)
			: State(InState)
		{
		}

		virtual bool Update() override
		{
			if (State->bCompleted)
			{
				return true;
			}
			if (State->StartedAt <= 0.0)
			{
				State->StartedAt = FPlatformTime::Seconds();
			}
			const bool bPlaying = GEditor != nullptr
				&& GEditor->IsPlaySessionInProgress()
				&& GEditor->PlayWorld != nullptr;
			if (bPlaying)
			{
				TSharedPtr<FJsonObject> PreviewParams = BuildPreviewParams(
					State->Blueprint,
					State->Graph,
					State->NotBool,
					State->IntAdd,
					State->DoubleAdd,
					State->StringConcat);
				TSharedPtr<FJsonObject> PreviewResult;
				FString PreviewCode;
				FString PreviewMessage;
				const bool bPreviewRejected =
					!UEAgentKitBlueprintPinDefaultExperimental::TryPreviewExperimental(
						PreviewParams,
						PreviewResult,
						PreviewCode,
						PreviewMessage);

				const FString FabricatedPlanId = FString(TEXT("sha256:")) + FString::ChrN(64, TEXT('a'));
				TSharedPtr<FJsonObject> ApplyParams = MakeShared<FJsonObject>();
				ApplyParams->SetStringField(
					TEXT("assetPath"),
					PreviewParams->GetStringField(TEXT("assetPath")));
				ApplyParams->SetStringField(
					TEXT("graphGuid"),
					PreviewParams->GetStringField(TEXT("graphGuid")));
				ApplyParams->SetArrayField(
					TEXT("defaults"),
					PreviewParams->GetArrayField(TEXT("defaults")));
				ApplyParams->SetStringField(TEXT("planId"), FabricatedPlanId);
				ApplyParams->SetStringField(
					TEXT("confirmation"),
					FString(UEAgentKitBlueprintPinDefaultExperimental::ApplyConfirmationPrefix)
						+ FabricatedPlanId);
				TSharedPtr<FJsonObject> ApplyResult;
				FString ApplyCode;
				FString ApplyMessage;
				const bool bApplyRejected =
					!UEAgentKitBlueprintPinDefaultExperimental::TryApplyExperimental(
						ApplyParams,
						ApplyResult,
						ApplyCode,
						ApplyMessage);

				const TArray<UEdGraphPin*> Pins = FixturePins(*State);
				const bool bDefaultsUnchanged = CurrentDefaultsMatch(Pins, State->DefaultsBefore);
				const bool bOk = bPreviewRejected
					&& PreviewCode == TEXT("graph-pin-default-pie-active")
					&& bApplyRejected
					&& ApplyCode == TEXT("graph-pin-default-pie-active")
					&& bDefaultsUnchanged;

				TSharedRef<FJsonObject> Evidence = MakeShared<FJsonObject>();
				Evidence->SetStringField(TEXT("schemaVersion"), TEXT("ueak.pin-default-pie-gate/1.0"));
				Evidence->SetNumberField(
					TEXT("processId"),
					static_cast<double>(FPlatformProcess::GetCurrentProcessId()));
				Evidence->SetStringField(TEXT("writtenAtUtc"), FDateTime::UtcNow().ToIso8601());
				Evidence->SetStringField(TEXT("assetPath"), State->Blueprint->GetPathName());
				Evidence->SetStringField(TEXT("graphGuid"), GuidLower(State->Graph->GraphGuid));
				Evidence->SetBoolField(TEXT("pieStarted"), true);
				Evidence->SetBoolField(TEXT("previewRejected"), bPreviewRejected);
				Evidence->SetStringField(TEXT("previewErrorCode"), PreviewCode);
				Evidence->SetStringField(TEXT("previewErrorMessage"), PreviewMessage);
				Evidence->SetBoolField(TEXT("applyRejected"), bApplyRejected);
				Evidence->SetStringField(TEXT("applyErrorCode"), ApplyCode);
				Evidence->SetStringField(TEXT("applyErrorMessage"), ApplyMessage);
				Evidence->SetBoolField(TEXT("defaultsUnchanged"), bDefaultsUnchanged);
				Evidence->SetBoolField(TEXT("success"), bOk);
				const FString Path = FPaths::Combine(
					FPaths::ProjectSavedDir(),
					TEXT("PinDefaultExpansion"),
					TEXT("pie-gate.json"));
				if (!WriteJson(Path, Evidence))
				{
					State->Test->AddError(TEXT("Could not write the PIE gate evidence."));
				}
				State->Test->TestTrue(TEXT("PIE rejects preview and apply with pie-active"), bOk);
				if (!bOk)
				{
					State->Test->AddError(FString::Printf(
						TEXT("PIE gate detail: preview=%s/%s apply=%s/%s unchanged=%s"),
						bPreviewRejected ? TEXT("rejected") : TEXT("accepted"),
						*PreviewCode,
						bApplyRejected ? TEXT("rejected") : TEXT("accepted"),
						*ApplyCode,
						bDefaultsUnchanged ? TEXT("true") : TEXT("false")));
				}
				UEAgentKitBlueprintPinDefaultExperimental::ResetExperimentalWriteForTest();
				State->bCompleted = true;
				return true;
			}
			if (FPlatformTime::Seconds() - State->StartedAt > 60.0)
			{
				TSharedRef<FJsonObject> Evidence = MakeShared<FJsonObject>();
				Evidence->SetStringField(TEXT("schemaVersion"), TEXT("ueak.pin-default-pie-gate/1.0"));
				Evidence->SetNumberField(
					TEXT("processId"),
					static_cast<double>(FPlatformProcess::GetCurrentProcessId()));
				Evidence->SetStringField(TEXT("writtenAtUtc"), FDateTime::UtcNow().ToIso8601());
				Evidence->SetBoolField(TEXT("pieStarted"), false);
				Evidence->SetBoolField(TEXT("success"), false);
				Evidence->SetStringField(
					TEXT("note"),
					TEXT("PIE did not start within 60 seconds; the PIE gate was not exercised."));
				WriteJson(
					FPaths::Combine(
						FPaths::ProjectSavedDir(),
						TEXT("PinDefaultExpansion"),
						TEXT("pie-gate.json")),
					Evidence);
				State->Test->AddError(
					TEXT("PIE did not start within 60 seconds; preview/apply PIE rejection was not run."));
				State->bCompleted = true;
				return true;
			}
			return false;
		}

	private:
		TSharedRef<FState> State;
	};

	class FWaitForPieStop final : public IAutomationLatentCommand
	{
	public:
		explicit FWaitForPieStop(TSharedRef<FState> InState)
			: State(InState)
		{
		}

		virtual bool Update() override
		{
			return GEditor == nullptr
				|| (!GEditor->IsPlaySessionInProgress() && GEditor->PlayWorld == nullptr);
		}

	private:
		TSharedRef<FState> State;
	};

	bool ResolveFixture(FState& OutState, FString& OutError)
	{
		UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, AssetPath);
		if (Blueprint == nullptr || Blueprint->UbergraphPages.IsEmpty())
		{
			OutError = TEXT("The saved PinDefault fixture is missing; run UEAgentKit.PinDefault.Expansion first.");
			return false;
		}
		UEdGraph* Graph = Blueprint->UbergraphPages[0];
		UK2Node_CallFunction* NotBool = FindCallFunction(Graph, TEXT("Not_PreBool"));
		UK2Node_CallFunction* IntAdd = FindCallFunction(Graph, TEXT("Add_IntInt"));
		UK2Node_CallFunction* DoubleAdd = FindCallFunction(Graph, TEXT("Add_DoubleDouble"));
		UK2Node_CallFunction* StringConcat = FindCallFunction(Graph, TEXT("Concat_StrStr"));
		if (NotBool == nullptr || IntAdd == nullptr || DoubleAdd == nullptr || StringConcat == nullptr)
		{
			OutError = TEXT("The saved PinDefault fixture lost one of its four typed nodes.");
			return false;
		}
		UAssetEditorSubsystem* Subsystem = GEditor != nullptr
			? GEditor->GetEditorSubsystem<UAssetEditorSubsystem>()
			: nullptr;
		if (Subsystem == nullptr)
		{
			OutError = TEXT("No AssetEditorSubsystem for the PIE gate fixture.");
			return false;
		}
		Subsystem->OpenEditorForAsset(Blueprint);
		IAssetEditorInstance* Instance = Subsystem->FindEditorForAsset(Blueprint, false);
		if (Instance == nullptr || Instance->GetEditorName() != FName(TEXT("BlueprintEditor")))
		{
			OutError = TEXT("No BlueprintEditor for the PIE gate fixture.");
			return false;
		}
		FBlueprintEditor* Editor = static_cast<FBlueprintEditor*>(Instance);
		if (!Editor->OpenGraphAndBringToFront(Graph, true).IsValid()
			|| Editor->GetFocusedGraph() != Graph)
		{
			OutError = TEXT("Could not focus the PIE gate fixture graph.");
			return false;
		}
		OutState.Blueprint = Blueprint;
		OutState.Graph = Graph;
		OutState.NotBool = NotBool;
		OutState.IntAdd = IntAdd;
		OutState.DoubleAdd = DoubleAdd;
		OutState.StringConcat = StringConcat;
		return true;
	}
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitPinDefaultPieGateTest,
	"UEAgentKit.PinDefault.PieGate",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitPinDefaultPieGateTest::RunTest(const FString& Parameters)
{
	using namespace UEAgentKitPinDefaultPieGate;
	FString Error;
	TSharedRef<FState> State = MakeShared<FState>();
	State->Test = this;
	if (!ResolveFixture(*State, Error))
	{
		AddError(Error);
		return false;
	}
	for (UEdGraphPin* Pin : FixturePins(*State))
	{
		if (Pin == nullptr)
		{
			AddError(TEXT("The PIE gate fixture lost one of its four typed input pins."));
			return false;
		}
		State->DefaultsBefore.Add(Pin->DefaultValue);
	}
	UEAgentKitBlueprintPinDefaultExperimental::SetExperimentalWriteEnabledForTest(true);
	ADD_LATENT_AUTOMATION_COMMAND(FStartPIECommand(false));
	ADD_LATENT_AUTOMATION_COMMAND(FWaitForPieThenReject(State));
	ADD_LATENT_AUTOMATION_COMMAND(FEndPlayMapCommand());
	ADD_LATENT_AUTOMATION_COMMAND(FWaitForPieStop(State));
	return true;
}

#endif // WITH_DEV_AUTOMATION_TESTS
