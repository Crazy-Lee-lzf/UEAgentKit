// Real-Editor acceptance for the P2 bounded Blueprint variable-creation slice.
//
// The fixture is one ordinary Blueprint with two pre-existing variables and a
// two-node/one-exec-link graph.  The test proves the default-OFF gate, that
// malformed names/types/defaults, existing and duplicate names, containers,
// unknown parameters, a mismatched focused Blueprint and a dirty package all
// fail closed, then performs Preview -> Apply -> Undo/Redo -> Save.  The four
// created bool/int/double/string variables must be real UBlueprint variables
// with engine-standard flags, must survive Undo/Redo exactly, and must be
// written to disk without compiling anything.

#include "BlueprintContextSha256.h"
#include "BlueprintEditor.h"
#include "BlueprintGraphLayoutApply.h"
#include "BlueprintVariableWriteExperimental.h"

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
#include "K2Node_IfThenElse.h"
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

namespace UEAgentKitBlueprintVariableExpansion
{
	const TCHAR* const AssetPath =
		TEXT("/Game/UEAgentKitBlueprintVariableExpansion/BP_VariableExpansion.BP_VariableExpansion");
	const TCHAR* const PackageName =
		TEXT("/Game/UEAgentKitBlueprintVariableExpansion/BP_VariableExpansion");
	const TCHAR* const AssetName = TEXT("BP_VariableExpansion");
	const TCHAR* const EvidenceSubdirectory = TEXT("BlueprintVariableExpansion");

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

	FString DescribePinType(const FEdGraphPinType& Type)
	{
		if (Type.PinCategory == UEdGraphSchema_K2::PC_Boolean)
		{
			return TEXT("bool");
		}
		if (Type.PinCategory == UEdGraphSchema_K2::PC_Int)
		{
			return TEXT("int");
		}
		if ((Type.PinCategory == UEdGraphSchema_K2::PC_Real
				&& Type.PinSubCategory == UEdGraphSchema_K2::PC_Double)
			|| Type.PinCategory == UEdGraphSchema_K2::PC_Double)
		{
			return TEXT("double");
		}
		if (Type.PinCategory == UEdGraphSchema_K2::PC_String)
		{
			return TEXT("string");
		}
		return FString::Printf(
			TEXT("unsupported:%s:%s"),
			*Type.PinCategory.ToString(),
			*Type.PinSubCategory.ToString());
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
		if (!UPackage::SavePackage(Package, Blueprint, *Filename, SaveArgs))
		{
			UE_LOG(
				LogTemp,
				Error,
				TEXT("Variable fixture SavePackage failed package=%s file=%s dirty=%s fullyLoaded=%s"),
				*Package->GetName(),
				*Filename,
				Package->IsDirty() ? TEXT("true") : TEXT("false"),
				Package->IsFullyLoaded() ? TEXT("true") : TEXT("false"));
			return false;
		}
		return true;
	}

	int32 CountLinks(const UEdGraph* Graph)
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

	bool AddFixtureVariable(
		UBlueprint* Blueprint,
		const TCHAR* Name,
		const FName Category,
		const TCHAR* DefaultValue,
		const FName SubCategory = NAME_None)
	{
		if (Blueprint == nullptr)
		{
			return false;
		}
		FBPVariableDescription Variable;
		Variable.VarName = FName(Name);
		Variable.VarGuid = FGuid::NewGuid();
		Variable.VarType.PinCategory = Category;
		if (!SubCategory.IsNone())
		{
			Variable.VarType.PinSubCategory = SubCategory;
		}
		Variable.FriendlyName = FName::NameToDisplayString(
			FString(Name),
			Category == UEdGraphSchema_K2::PC_Boolean);
		Variable.PropertyFlags = CPF_Edit | CPF_BlueprintVisible | CPF_DisableEditOnInstance;
		Variable.ReplicationCondition = COND_None;
		Variable.Category = UEdGraphSchema_K2::VR_DefaultCategory;
		Variable.DefaultValue = DefaultValue;
		if (Category == UEdGraphSchema_K2::PC_String)
		{
			Variable.SetMetaData(TEXT("MultiLine"), TEXT("true"));
		}
		Blueprint->NewVariables.Add(Variable);
		return true;
	}

	UK2Node_IfThenElse* AddBranch(UEdGraph* Graph, const int32 X, const int32 Y)
	{
		UK2Node_IfThenElse* Node = NewObject<UK2Node_IfThenElse>(Graph);
		if (Node == nullptr)
		{
			return nullptr;
		}
		Graph->AddNode(Node, true, false);
		Node->AllocateDefaultPins();
		Node->CreateNewGuid();
		Node->NodePosX = X;
		Node->NodePosY = Y;
		return Node;
	}

	UBlueprint* CreateFixture(FString& OutError)
	{
		if (FindObject<UBlueprint>(nullptr, AssetPath) != nullptr)
		{
			OutError = TEXT("Variable expansion fixture already exists; use a fresh project.");
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
			FName(TEXT("UEAgentKitBlueprintVariableExpansion")));
		if (Blueprint == nullptr || Blueprint->UbergraphPages.IsEmpty())
		{
			OutError = TEXT("Could not create the variable expansion Blueprint.");
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

		UK2Node_IfThenElse* First = AddBranch(Graph, 0, 0);
		UK2Node_IfThenElse* Second = AddBranch(Graph, 320, 0);
		if (First == nullptr || Second == nullptr)
		{
			OutError = TEXT("Could not create the fixture branch nodes.");
			return nullptr;
		}
		UEdGraphPin* ThenPin = First->FindPin(TEXT("then"), EGPD_Output);
		UEdGraphPin* ExecutePin = Second->FindPin(TEXT("execute"), EGPD_Input);
		if (ThenPin == nullptr || ExecutePin == nullptr)
		{
			OutError = TEXT("Could not resolve the fixture exec pins.");
			return nullptr;
		}
		ThenPin->MakeLinkTo(ExecutePin);

		if (!AddFixtureVariable(Blueprint, TEXT("ExistingInt"), UEdGraphSchema_K2::PC_Int, TEXT("5"))
			|| !AddFixtureVariable(Blueprint, TEXT("ExistingString"), UEdGraphSchema_K2::PC_String, TEXT("seed")))
		{
			OutError = TEXT("Could not add the pre-existing fixture variables.");
			return nullptr;
		}

		// Fixture-only compile: the fixture must start from a consistent,
		// already-compiled Blueprint.  The product path under test never compiles.
		FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
		FAssetRegistryModule::AssetCreated(Blueprint);

		if (!SaveBlueprint(Blueprint))
		{
			OutError = TEXT("Could not save the fixture Blueprint.");
			return nullptr;
		}
		Package->SetDirtyFlag(false);
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

	TSharedPtr<FJsonObject> MakeVariable(
		const TCHAR* Name,
		const TCHAR* Type,
		const TCHAR* DefaultValue)
	{
		TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
		Item->SetStringField(TEXT("name"), Name);
		Item->SetStringField(TEXT("type"), Type);
		Item->SetStringField(TEXT("defaultValue"), DefaultValue);
		return Item;
	}

	TSharedPtr<FJsonObject> MakePreviewParams(
		const TArray<TSharedPtr<FJsonValue>>& Variables,
		const TCHAR* RequestAssetPath = AssetPath)
	{
		TSharedRef<FJsonObject> Params = MakeShared<FJsonObject>();
		Params->SetStringField(TEXT("assetPath"), RequestAssetPath);
		Params->SetArrayField(TEXT("variables"), Variables);
		return Params;
	}

	TSharedPtr<FJsonObject> BuildApplyParams(
		const TSharedPtr<FJsonObject>& Preview,
		const FString& Confirmation)
	{
		// Deep-copy the previewed variables: SetArrayField shares the JSON value
		// pointers, and the negative stale-plan case deliberately rewrites one.
		TArray<TSharedPtr<FJsonValue>> Variables;
		for (const TSharedPtr<FJsonValue>& Value : Preview->GetArrayField(TEXT("variables")))
		{
			const TSharedPtr<FJsonObject> Source = Value->AsObject();
			TSharedRef<FJsonObject> Copy = MakeShared<FJsonObject>();
			Copy->SetStringField(TEXT("name"), Source->GetStringField(TEXT("name")));
			Copy->SetStringField(TEXT("type"), Source->GetStringField(TEXT("type")));
			Copy->SetStringField(
				TEXT("defaultValue"),
				Source->GetStringField(TEXT("defaultValue")));
			Variables.Add(MakeShared<FJsonValueObject>(Copy));
		}
		TSharedRef<FJsonObject> Params = MakeShared<FJsonObject>();
		Params->SetStringField(TEXT("assetPath"), Preview->GetStringField(TEXT("assetPath")));
		Params->SetStringField(TEXT("planId"), Preview->GetStringField(TEXT("planId")));
		Params->SetArrayField(TEXT("variables"), Variables);
		Params->SetStringField(TEXT("confirmation"), Confirmation);
		return Params;
	}

	TSharedPtr<FJsonObject> BuildSaveParams(
		const TSharedPtr<FJsonObject>& Apply,
		const FString& Confirmation)
	{
		TSharedRef<FJsonObject> Params = MakeShared<FJsonObject>();
		Params->SetStringField(TEXT("assetPath"), Apply->GetStringField(TEXT("assetPath")));
		Params->SetStringField(TEXT("planId"), Apply->GetStringField(TEXT("planId")));
		Params->SetStringField(
			TEXT("expectedVariablesFingerprint"),
			Apply->GetStringField(TEXT("variablesFingerprintAfter")));
		Params->SetStringField(
			TEXT("expectedPackageRevision"),
			Apply->GetStringField(TEXT("packageRevision")));
		Params->SetNumberField(
			TEXT("expectedVariableCount"),
			Apply->GetNumberField(TEXT("variableCountAfter")));
		Params->SetStringField(TEXT("confirmation"), Confirmation);
		return Params;
	}

	const FBPVariableDescription* FindVariable(const UBlueprint* Blueprint, const TCHAR* Name)
	{
		if (Blueprint == nullptr)
		{
			return nullptr;
		}
		const FName NameValue(Name);
		for (const FBPVariableDescription& Variable : Blueprint->NewVariables)
		{
			if (Variable.VarName == NameValue)
			{
				return &Variable;
			}
		}
		return nullptr;
	}

	TSharedPtr<FJsonObject> VariableJson(const FBPVariableDescription& Variable)
	{
		TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
		Item->SetStringField(TEXT("name"), Variable.VarName.ToString());
		Item->SetStringField(TEXT("type"), DescribePinType(Variable.VarType));
		Item->SetStringField(TEXT("defaultValue"), Variable.DefaultValue);
		Item->SetStringField(TEXT("guid"), GuidLower(Variable.VarGuid));
		return Item;
	}
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitBlueprintVariableExpansionTest,
	"UEAgentKit.VariableCreate.Expansion",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitBlueprintVariableExpansionTest::RunTest(const FString& Parameters)
{
	using namespace UEAgentKitBlueprintVariableExpansion;
	UEAgentKitBlueprintVariableExperimental::ResetExperimentalWriteForTest();
	if (!TestFalse(
			TEXT("experimental Blueprint variable gate is off unless the project opts in"),
			UEAgentKitBlueprintVariableExperimental::IsExperimentalWriteAvailable()))
	{
		return false;
	}

	bool bGateOffRejected = false;
	{
		TArray<TSharedPtr<FJsonValue>> Variables;
		Variables.Add(MakeShared<FJsonValueObject>(
			MakeVariable(TEXT("GateBool"), TEXT("bool"), TEXT("true"))));
		TSharedPtr<FJsonObject> GateResult;
		FString GateCode;
		FString GateMessage;
		if (!TestFalse(
				TEXT("gate-off variable preview is rejected"),
				UEAgentKitBlueprintVariableExperimental::TryPreviewExperimental(
					MakePreviewParams(Variables),
					GateResult,
					GateCode,
					GateMessage))
			|| !TestEqual(
				TEXT("gate-off variable preview error"),
				GateCode,
				FString(TEXT("blueprint-variable-experimental-disabled"))))
		{
			return false;
		}
		bGateOffRejected = true;
	}

	UEAgentKitBlueprintVariableExperimental::SetExperimentalWriteEnabledForTest(true);
	auto Fail = []()
	{
		UEAgentKitBlueprintVariableExperimental::ResetExperimentalWriteForTest();
		return false;
	};

	FString Error;
	UBlueprint* Blueprint = CreateFixture(Error);
	if (!TestNotNull(TEXT("variable expansion fixture is created"), Blueprint))
	{
		AddError(Error);
		return Fail();
	}
	UEdGraph* Graph = Blueprint->UbergraphPages[0];
	FBlueprintEditor* Editor = OpenAndFocus(Blueprint, Graph, Error);
	if (!TestNotNull(TEXT("variable expansion Blueprint is focused"), Editor))
	{
		AddError(Error);
		return Fail();
	}

	const int32 RootNodeCount = Graph->Nodes.Num();
	const int32 RootLinkCount = CountLinks(Graph);
	if (!TestEqual(TEXT("fixture starts with two branch nodes"), RootNodeCount, 2)
		|| !TestEqual(TEXT("fixture starts with one exec link"), RootLinkCount, 1)
		|| !TestEqual(TEXT("fixture starts with two variables"), Blueprint->NewVariables.Num(), 2))
	{
		return Fail();
	}

	TArray<TSharedPtr<FJsonValue>> ExistingVariables;
	for (const FBPVariableDescription& Variable : Blueprint->NewVariables)
	{
		ExistingVariables.Add(MakeShared<FJsonValueObject>(VariableJson(Variable)));
	}

	bool bUnknownParameterRejected = false;
	bool bEmptyVariablesRejected = false;
	bool bTooManyVariablesRejected = false;
	bool bInvalidNameRejected = false;
	bool bExistingNameRejected = false;
	bool bDuplicateRequestRejected = false;
	bool bUnsupportedTypeRejected = false;
	bool bContainerRejected = false;
	bool bInvalidValueRejected = false;
	bool bAssetMismatchRejected = false;
	bool bDirtyRejected = false;

	auto ExpectPreviewError = [this](
		const TSharedPtr<FJsonObject>& Params,
		const TCHAR* ExpectedCode,
		const TCHAR* Label)
	{
		TSharedPtr<FJsonObject> Result;
		FString Code;
		FString Message;
		const bool bRejected =
			!UEAgentKitBlueprintVariableExperimental::TryPreviewExperimental(
				Params,
				Result,
				Code,
				Message);
		TestTrue(Label, bRejected);
		TestEqual(FString(Label) + TEXT(" error code"), Code, FString(ExpectedCode));
		return bRejected && Code.Equals(ExpectedCode, ESearchCase::CaseSensitive);
	};

	{
		TArray<TSharedPtr<FJsonValue>> Variables;
		Variables.Add(MakeShared<FJsonValueObject>(
			MakeVariable(TEXT("NewBool"), TEXT("bool"), TEXT("true"))));
		TSharedPtr<FJsonObject> Unknown = MakePreviewParams(Variables);
		Unknown->SetStringField(TEXT("planId"), TEXT("sha256:ignored"));
		bUnknownParameterRejected = ExpectPreviewError(
			Unknown,
			TEXT("blueprint-variable-unknown-parameter"),
			TEXT("unknown preview parameter is rejected"));
	}
	{
		TArray<TSharedPtr<FJsonValue>> Variables;
		bEmptyVariablesRejected = ExpectPreviewError(
			MakePreviewParams(Variables),
			TEXT("blueprint-variable-invalid-variables"),
			TEXT("empty variables are rejected"));
	}
	{
		TArray<TSharedPtr<FJsonValue>> Variables;
		for (int32 Index = 0; Index < 9; ++Index)
		{
			Variables.Add(MakeShared<FJsonValueObject>(MakeVariable(
				*FString::Printf(TEXT("Var%d"), Index),
				TEXT("int"),
				TEXT("0"))));
		}
		bTooManyVariablesRejected = ExpectPreviewError(
			MakePreviewParams(Variables),
			TEXT("blueprint-variable-invalid-variables"),
			TEXT("more than eight variables are rejected"));
	}
	{
		TArray<TSharedPtr<FJsonValue>> Variables;
		Variables.Add(MakeShared<FJsonValueObject>(
			MakeVariable(TEXT("Bad Name"), TEXT("int"), TEXT("0"))));
		bInvalidNameRejected = ExpectPreviewError(
			MakePreviewParams(Variables),
			TEXT("blueprint-variable-name-invalid"),
			TEXT("invalid ASCII variable name is rejected"));
	}
	{
		TArray<TSharedPtr<FJsonValue>> Variables;
		Variables.Add(MakeShared<FJsonValueObject>(
			MakeVariable(TEXT("ExistingInt"), TEXT("int"), TEXT("0"))));
		bExistingNameRejected = ExpectPreviewError(
			MakePreviewParams(Variables),
			TEXT("blueprint-variable-duplicate-name"),
			TEXT("existing variable name is rejected"));
	}
	{
		TArray<TSharedPtr<FJsonValue>> Variables;
		Variables.Add(MakeShared<FJsonValueObject>(
			MakeVariable(TEXT("DupName"), TEXT("int"), TEXT("0"))));
		Variables.Add(MakeShared<FJsonValueObject>(
			MakeVariable(TEXT("dupname"), TEXT("int"), TEXT("1"))));
		bDuplicateRequestRejected = ExpectPreviewError(
			MakePreviewParams(Variables),
			TEXT("blueprint-variable-duplicate-name"),
			TEXT("case-insensitive duplicate request name is rejected"));
	}
	{
		TArray<TSharedPtr<FJsonValue>> Variables;
		Variables.Add(MakeShared<FJsonValueObject>(
			MakeVariable(TEXT("NewVector"), TEXT("vector"), TEXT("0"))));
		bUnsupportedTypeRejected = ExpectPreviewError(
			MakePreviewParams(Variables),
			TEXT("blueprint-variable-type-not-allowed"),
			TEXT("unsupported variable type is rejected"));
	}
	{
		TSharedRef<FJsonObject> Container = MakeShared<FJsonObject>();
		Container->SetStringField(TEXT("name"), TEXT("NewArray"));
		Container->SetStringField(TEXT("type"), TEXT("int"));
		Container->SetStringField(TEXT("defaultValue"), TEXT("0"));
		Container->SetBoolField(TEXT("container"), true);
		TArray<TSharedPtr<FJsonValue>> Variables;
		Variables.Add(MakeShared<FJsonValueObject>(Container));
		bContainerRejected = ExpectPreviewError(
			MakePreviewParams(Variables),
			TEXT("blueprint-variable-unknown-parameter"),
			TEXT("container declaration is rejected"));
	}
	{
		const TArray<TPair<const TCHAR*, const TCHAR*>> InvalidValues = {
			TPair<const TCHAR*, const TCHAR*>(TEXT("bool"), TEXT("TRUE")),
			TPair<const TCHAR*, const TCHAR*>(TEXT("int"), TEXT("01")),
			TPair<const TCHAR*, const TCHAR*>(TEXT("double"), TEXT("1e3")),
			TPair<const TCHAR*, const TCHAR*>(TEXT("string"), TEXT("line\nbreak")),
		};
		bool bAllRejected = true;
		for (const TPair<const TCHAR*, const TCHAR*>& Case : InvalidValues)
		{
			TArray<TSharedPtr<FJsonValue>> Variables;
			Variables.Add(MakeShared<FJsonValueObject>(
				MakeVariable(TEXT("NewValue"), Case.Key, Case.Value)));
			bAllRejected = ExpectPreviewError(
				MakePreviewParams(Variables),
				TEXT("blueprint-variable-value-invalid"),
				TEXT("non-canonical default value is rejected"))
				&& bAllRejected;
		}
		bInvalidValueRejected = bAllRejected;
	}
	{
		TArray<TSharedPtr<FJsonValue>> Variables;
		Variables.Add(MakeShared<FJsonValueObject>(
			MakeVariable(TEXT("NewBool"), TEXT("bool"), TEXT("true"))));
		bAssetMismatchRejected = ExpectPreviewError(
			MakePreviewParams(
				Variables,
				TEXT("/Game/UEAgentKitBlueprintVariableExpansion/BP_Other.BP_Other")),
			TEXT("blueprint-variable-focused-blueprint-mismatch"),
			TEXT("non-focused assetPath is rejected"));
	}
	{
		Blueprint->GetOutermost()->SetDirtyFlag(true);
		TArray<TSharedPtr<FJsonValue>> Variables;
		Variables.Add(MakeShared<FJsonValueObject>(
			MakeVariable(TEXT("NewBool"), TEXT("bool"), TEXT("true"))));
		bDirtyRejected = ExpectPreviewError(
			MakePreviewParams(Variables),
			TEXT("blueprint-variable-package-dirty"),
			TEXT("dirty package is rejected"));
		Blueprint->GetOutermost()->SetDirtyFlag(false);
	}

	TArray<TSharedPtr<FJsonValue>> RequestedVariables;
	RequestedVariables.Add(MakeShared<FJsonValueObject>(
		MakeVariable(TEXT("NewBool"), TEXT("bool"), TEXT("true"))));
	RequestedVariables.Add(MakeShared<FJsonValueObject>(
		MakeVariable(TEXT("NewInt"), TEXT("int"), TEXT("-7"))));
	RequestedVariables.Add(MakeShared<FJsonValueObject>(
		MakeVariable(TEXT("NewDouble"), TEXT("double"), TEXT("3.25"))));
	RequestedVariables.Add(MakeShared<FJsonValueObject>(
		MakeVariable(TEXT("NewString"), TEXT("string"), TEXT("hello"))));

	TSharedPtr<FJsonObject> Preview;
	FString PreviewCode;
	FString PreviewMessage;
	if (!TestTrue(
			TEXT("variable preview succeeds on the exact focused Blueprint"),
			UEAgentKitBlueprintVariableExperimental::TryPreviewExperimental(
				MakePreviewParams(RequestedVariables),
				Preview,
				PreviewCode,
				PreviewMessage)))
	{
		AddError(PreviewCode + TEXT(": ") + PreviewMessage);
		return Fail();
	}
	const FString PlanId = Preview->GetStringField(TEXT("planId"));
	if (!TestTrue(TEXT("preview is read-only"), Preview->GetBoolField(TEXT("readOnly")))
		|| !TestFalse(TEXT("preview does not write"), Preview->GetBoolField(TEXT("wroteBlueprint")))
		|| !TestFalse(TEXT("preview does not save"), Preview->GetBoolField(TEXT("savedPackage")))
		|| !TestFalse(TEXT("preview does not compile"), Preview->GetBoolField(TEXT("compiled")))
		|| !TestFalse(TEXT("preview does not regenerate the skeleton"), Preview->GetBoolField(TEXT("skeletonRegenerated")))
		|| !TestEqual(TEXT("preview createCount"), static_cast<int32>(Preview->GetNumberField(TEXT("createCount"))), 4)
		|| !TestEqual(
			TEXT("preview variableCountBefore"),
			static_cast<int32>(Preview->GetNumberField(TEXT("variableCountBefore"))),
			2)
		|| !TestEqual(
			TEXT("preview nodeCount"),
			static_cast<int32>(Preview->GetNumberField(TEXT("nodeCount"))),
			RootNodeCount)
		|| !TestEqual(
			TEXT("preview linkCount"),
			static_cast<int32>(Preview->GetNumberField(TEXT("linkCount"))),
			RootLinkCount))
	{
		return Fail();
	}

	bool bStalePlanRejected = false;
	{
		TSharedPtr<FJsonObject> Tampered = BuildApplyParams(
			Preview,
			FString(UEAgentKitBlueprintVariableExperimental::ApplyConfirmationPrefix) + PlanId);
		TArray<TSharedPtr<FJsonValue>> TamperedVariables =
			Tampered->GetArrayField(TEXT("variables"));
		TSharedPtr<FJsonObject> First = TamperedVariables[0]->AsObject();
		First->SetStringField(TEXT("defaultValue"), TEXT("false"));
		TSharedPtr<FJsonObject> Result;
		FString Code;
		FString Message;
		const bool bRejected = !UEAgentKitBlueprintVariableExperimental::TryApplyExperimental(
			Tampered,
			Result,
			Code,
			Message);
		bStalePlanRejected = bRejected
			&& Code.Equals(TEXT("blueprint-variable-plan-stale"), ESearchCase::CaseSensitive);
		if (!TestTrue(TEXT("tampered (stale) variable plan is rejected"), bStalePlanRejected))
		{
			AddError(Code + TEXT(": ") + Message);
			return Fail();
		}
	}

	bool bWrongApplyConfirmationRejected = false;
	{
		TSharedPtr<FJsonObject> Params = BuildApplyParams(Preview, TEXT("CREATE"));
		TSharedPtr<FJsonObject> Result;
		FString Code;
		FString Message;
		const bool bRejected = !UEAgentKitBlueprintVariableExperimental::TryApplyExperimental(
			Params,
			Result,
			Code,
			Message);
		bWrongApplyConfirmationRejected = bRejected
			&& Code.Equals(
				TEXT("blueprint-variable-confirmation-required"),
				ESearchCase::CaseSensitive);
		if (!TestTrue(
				TEXT("wrong Apply confirmation is rejected"),
				bWrongApplyConfirmationRejected))
		{
			return Fail();
		}
	}

	bool bBadPlanIdRejected = false;
	{
		TSharedPtr<FJsonObject> Params = BuildApplyParams(Preview, TEXT("CREATE"));
		Params->SetStringField(TEXT("planId"), TEXT("sha256:not-a-plan"));
		Params->SetStringField(TEXT("confirmation"), TEXT("CREATE"));
		TSharedPtr<FJsonObject> Result;
		FString Code;
		FString Message;
		const bool bRejected = !UEAgentKitBlueprintVariableExperimental::TryApplyExperimental(
			Params,
			Result,
			Code,
			Message);
		bBadPlanIdRejected = bRejected
			&& Code.Equals(
				TEXT("blueprint-variable-invalid-identifier"),
				ESearchCase::CaseSensitive);
		if (!TestTrue(TEXT("malformed planId is rejected"), bBadPlanIdRejected))
		{
			return Fail();
		}
	}

	bool bSaveBeforeApplyRejected = false;
	{
		TSharedPtr<FJsonObject> Params = MakeShared<FJsonObject>();
		Params->SetStringField(TEXT("assetPath"), AssetPath);
		Params->SetStringField(TEXT("planId"), PlanId);
		Params->SetStringField(
			TEXT("expectedVariablesFingerprint"),
			Preview->GetStringField(TEXT("variablesFingerprint")));
		Params->SetStringField(
			TEXT("expectedPackageRevision"),
			Preview->GetStringField(TEXT("packageRevision")));
		Params->SetNumberField(TEXT("expectedVariableCount"), 6);
		Params->SetStringField(
			TEXT("confirmation"),
			FString(UEAgentKitBlueprintVariableExperimental::SaveConfirmationPrefix) + PlanId);
		TSharedPtr<FJsonObject> Result;
		FString Code;
		FString Message;
		const bool bRejected = !UEAgentKitBlueprintVariableExperimental::TrySaveExperimental(
			Params,
			Result,
			Code,
			Message);
		bSaveBeforeApplyRejected = bRejected
			&& Code.Equals(
				TEXT("blueprint-variable-save-requires-apply"),
				ESearchCase::CaseSensitive);
		if (!TestTrue(TEXT("Save before Apply is rejected"), bSaveBeforeApplyRejected))
		{
			AddError(Code + TEXT(": ") + Message);
			return Fail();
		}
	}

	TSharedPtr<FJsonObject> ApplyResult;
	FString ApplyCode;
	FString ApplyMessage;
	if (!TestTrue(
			TEXT("variable Apply succeeds"),
			UEAgentKitBlueprintVariableExperimental::TryApplyExperimental(
				BuildApplyParams(
					Preview,
					FString(UEAgentKitBlueprintVariableExperimental::ApplyConfirmationPrefix) + PlanId),
				ApplyResult,
				ApplyCode,
				ApplyMessage)))
	{
		AddError(ApplyCode + TEXT(": ") + ApplyMessage);
		return Fail();
	}
	if (!TestTrue(TEXT("Apply reports applied"), ApplyResult->GetBoolField(TEXT("applied")))
		|| !TestTrue(TEXT("Apply verified Undo"), ApplyResult->GetBoolField(TEXT("undoVerified")))
		|| !TestTrue(TEXT("Apply verified Redo"), ApplyResult->GetBoolField(TEXT("redoVerified")))
		|| !TestFalse(TEXT("Apply did not save"), ApplyResult->GetBoolField(TEXT("saved")))
		|| !TestFalse(TEXT("Apply did not compile"), ApplyResult->GetBoolField(TEXT("compiled")))
		|| !TestFalse(
			TEXT("Apply did not regenerate the skeleton"),
			ApplyResult->GetBoolField(TEXT("skeletonRegenerated")))
		|| !TestTrue(
			TEXT("Apply marks the package dirty"),
			ApplyResult->GetBoolField(TEXT("packageDirty")))
		|| !TestEqual(
			TEXT("Apply variableCountAfter"),
			static_cast<int32>(ApplyResult->GetNumberField(TEXT("variableCountAfter"))),
			6)
		|| !TestEqual(
			TEXT("Apply createCount"),
			static_cast<int32>(ApplyResult->GetNumberField(TEXT("createCount"))),
			4)
		|| !TestEqual(
			TEXT("Apply preserves node count"),
			static_cast<int32>(ApplyResult->GetNumberField(TEXT("nodeCount"))),
			RootNodeCount)
		|| !TestEqual(
			TEXT("Apply preserves link count"),
			static_cast<int32>(ApplyResult->GetNumberField(TEXT("linkCount"))),
			RootLinkCount))
	{
		return Fail();
	}

	const TArray<TSharedPtr<FJsonValue>> CreatedVariables =
		ApplyResult->GetArrayField(TEXT("createdVariables"));
	if (!TestEqual(TEXT("Apply reports four created variables"), CreatedVariables.Num(), 4))
	{
		return Fail();
	}
	const TArray<TPair<const TCHAR*, const TCHAR*>> ExpectedCreated = {
		TPair<const TCHAR*, const TCHAR*>(TEXT("NewBool"), TEXT("bool")),
		TPair<const TCHAR*, const TCHAR*>(TEXT("NewInt"), TEXT("int")),
		TPair<const TCHAR*, const TCHAR*>(TEXT("NewDouble"), TEXT("double")),
		TPair<const TCHAR*, const TCHAR*>(TEXT("NewString"), TEXT("string")),
	};
	const TArray<const TCHAR*> ExpectedDefaults = {
		TEXT("true"),
		TEXT("-7"),
		TEXT("3.25"),
		TEXT("hello"),
	};
	TArray<TSharedPtr<FJsonValue>> CreatedEvidence;
	for (int32 Index = 0; Index < ExpectedCreated.Num(); ++Index)
	{
		const FBPVariableDescription* Live = FindVariable(Blueprint, ExpectedCreated[Index].Key);
		if (!TestNotNull(
				*FString::Printf(TEXT("live Blueprint has %s"), ExpectedCreated[Index].Key),
				Live)
			|| !TestEqual(
				*FString::Printf(TEXT("%s has the declared type"), ExpectedCreated[Index].Key),
				DescribePinType(Live->VarType),
				FString(ExpectedCreated[Index].Value))
			|| !TestEqual(
				*FString::Printf(TEXT("%s has the exact default"), ExpectedCreated[Index].Key),
				Live->DefaultValue,
				FString(ExpectedDefaults[Index]))
			|| !TestTrue(
				*FString::Printf(TEXT("%s is blueprint visible and editable"), ExpectedCreated[Index].Key),
				(Live->PropertyFlags & (CPF_Edit | CPF_BlueprintVisible))
					== (CPF_Edit | CPF_BlueprintVisible)))
		{
			return Fail();
		}
		const TSharedPtr<FJsonObject> Applied = CreatedVariables[Index]->AsObject();
		if (!TestTrue(
				*FString::Printf(TEXT("%s has a valid GUID"), ExpectedCreated[Index].Key),
				Live->VarGuid.IsValid())
			|| !TestEqual(
				*FString::Printf(TEXT("%s GUID matches the Apply result"), ExpectedCreated[Index].Key),
				GuidLower(Live->VarGuid),
				Applied->GetStringField(TEXT("guid"))))
		{
			return Fail();
		}
		CreatedEvidence.Add(MakeShared<FJsonValueObject>(VariableJson(*Live)));
	}
	for (const TSharedPtr<FJsonValue>& Existing : ExistingVariables)
	{
		const TSharedPtr<FJsonObject> Before = Existing->AsObject();
		const FBPVariableDescription* Live =
			FindVariable(Blueprint, *Before->GetStringField(TEXT("name")));
		if (!TestNotNull(TEXT("pre-existing variable survives Apply"), Live)
			|| !TestEqual(
				TEXT("pre-existing variable GUID is preserved"),
				GuidLower(Live->VarGuid),
				Before->GetStringField(TEXT("guid")))
			|| !TestEqual(
				TEXT("pre-existing variable default is preserved"),
				Live->DefaultValue,
				Before->GetStringField(TEXT("defaultValue"))))
		{
			return Fail();
		}
	}

	bool bWrongSaveFingerprintRejected = false;
	bool bWrongSaveRevisionRejected = false;
	bool bWrongSaveConfirmationRejected = false;
	const FString SaveConfirmation =
		FString(UEAgentKitBlueprintVariableExperimental::SaveConfirmationPrefix) + PlanId;
	{
		TSharedPtr<FJsonObject> Params = BuildSaveParams(ApplyResult, SaveConfirmation);
		Params->SetStringField(
			TEXT("expectedVariablesFingerprint"),
			FString(TEXT("sha256:")) + FString::ChrN(64, TEXT('9')));
		TSharedPtr<FJsonObject> Result;
		FString Code;
		FString Message;
		bWrongSaveFingerprintRejected = !UEAgentKitBlueprintVariableExperimental::TrySaveExperimental(
				Params,
				Result,
				Code,
				Message)
			&& Code.Equals(TEXT("blueprint-variable-save-plan-mismatch"), ESearchCase::CaseSensitive);
		if (!TestTrue(
				TEXT("Save with a wrong variable fingerprint is rejected"),
				bWrongSaveFingerprintRejected))
		{
			return Fail();
		}
	}
	{
		TSharedPtr<FJsonObject> Params = BuildSaveParams(ApplyResult, SaveConfirmation);
		Params->SetStringField(
			TEXT("expectedPackageRevision"),
			FString(TEXT("sha256:")) + FString::ChrN(64, TEXT('8')));
		TSharedPtr<FJsonObject> Result;
		FString Code;
		FString Message;
		bWrongSaveRevisionRejected = !UEAgentKitBlueprintVariableExperimental::TrySaveExperimental(
				Params,
				Result,
				Code,
				Message)
			&& Code.Equals(
				TEXT("blueprint-variable-save-revision-mismatch"),
				ESearchCase::CaseSensitive);
		if (!TestTrue(
				TEXT("Save with a wrong package revision is rejected"),
				bWrongSaveRevisionRejected))
		{
			return Fail();
		}
	}
	{
		TSharedPtr<FJsonObject> Params = BuildSaveParams(ApplyResult, TEXT("SAVE"));
		TSharedPtr<FJsonObject> Result;
		FString Code;
		FString Message;
		bWrongSaveConfirmationRejected = !UEAgentKitBlueprintVariableExperimental::TrySaveExperimental(
				Params,
				Result,
				Code,
				Message)
			&& Code.Equals(
				TEXT("blueprint-variable-confirmation-required"),
				ESearchCase::CaseSensitive);
		if (!TestTrue(
				TEXT("wrong Save confirmation is rejected"),
				bWrongSaveConfirmationRejected))
		{
			return Fail();
		}
	}

	TSharedPtr<FJsonObject> SaveResult;
	FString SaveCode;
	FString SaveMessage;
	if (!TestTrue(
			TEXT("explicit variable Save succeeds"),
			UEAgentKitBlueprintVariableExperimental::TrySaveExperimental(
				BuildSaveParams(ApplyResult, SaveConfirmation),
				SaveResult,
				SaveCode,
				SaveMessage)))
	{
		AddError(SaveCode + TEXT(": ") + SaveMessage);
		return Fail();
	}
	if (!TestTrue(TEXT("Save reports saved"), SaveResult->GetBoolField(TEXT("saved")))
		|| !TestTrue(
			TEXT("Save readback is verified"),
			SaveResult->GetBoolField(TEXT("readbackVerified")))
		|| !TestFalse(
			TEXT("Save leaves the package clean"),
			SaveResult->GetBoolField(TEXT("packageDirty")))
		|| !TestFalse(TEXT("Save did not compile"), SaveResult->GetBoolField(TEXT("compiled")))
		|| !TestFalse(
			TEXT("Save did not regenerate the skeleton"),
			SaveResult->GetBoolField(TEXT("skeletonRegenerated")))
		|| !TestFalse(
			TEXT("Save waits for the independent reload"),
			SaveResult->GetBoolField(TEXT("independentReloadVerified")))
		|| !TestEqual(
			TEXT("Save variableCount"),
			static_cast<int32>(SaveResult->GetNumberField(TEXT("variableCount"))),
			6)
		|| !TestEqual(
			TEXT("Save createCount"),
			static_cast<int32>(SaveResult->GetNumberField(TEXT("createCount"))),
			4))
	{
		return Fail();
	}

	const FString AssetFilename = FPackageName::LongPackageNameToFilename(
		Blueprint->GetOutermost()->GetName(),
		FPackageName::GetAssetPackageExtension());
	FString DiskDigest;
	if (!TestTrue(
			TEXT("saved variable package can be hashed"),
			FBlueprintContextSha256::HashFile(AssetFilename, DiskDigest)))
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
	if (!TestEqual(
			TEXT("saved Blueprint still has six variables"),
			Blueprint->NewVariables.Num(),
			6))
	{
		return Fail();
	}

	TArray<TSharedPtr<FJsonValue>> ExpectedNodeGuids;
	for (const UEdGraphNode* Node : Graph->Nodes)
	{
		if (Node != nullptr && Node->NodeGuid.IsValid())
		{
			ExpectedNodeGuids.Add(MakeShared<FJsonValueString>(GuidLower(Node->NodeGuid)));
		}
	}
	TArray<TSharedPtr<FJsonValue>> ExpectedLinks;
	for (const UEdGraphNode* Node : Graph->Nodes)
	{
		if (Node == nullptr)
		{
			continue;
		}
		for (const UEdGraphPin* Pin : Node->Pins)
		{
			if (Pin == nullptr || Pin->Direction != EGPD_Output)
			{
				continue;
			}
			for (const UEdGraphPin* Linked : Pin->LinkedTo)
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

	TSharedRef<FJsonObject> Checks = MakeShared<FJsonObject>();
	Checks->SetBoolField(TEXT("defaultGateOffRejected"), bGateOffRejected);
	Checks->SetBoolField(TEXT("unknownParameterRejected"), bUnknownParameterRejected);
	Checks->SetBoolField(TEXT("emptyVariablesRejected"), bEmptyVariablesRejected);
	Checks->SetBoolField(TEXT("tooManyVariablesRejected"), bTooManyVariablesRejected);
	Checks->SetBoolField(TEXT("invalidNameRejected"), bInvalidNameRejected);
	Checks->SetBoolField(TEXT("existingNameRejected"), bExistingNameRejected);
	Checks->SetBoolField(TEXT("duplicateRequestRejected"), bDuplicateRequestRejected);
	Checks->SetBoolField(TEXT("unsupportedTypeRejected"), bUnsupportedTypeRejected);
	Checks->SetBoolField(TEXT("containerDeclarationRejected"), bContainerRejected);
	Checks->SetBoolField(TEXT("invalidValueEncodingRejected"), bInvalidValueRejected);
	Checks->SetBoolField(TEXT("nonFocusedAssetRejected"), bAssetMismatchRejected);
	Checks->SetBoolField(TEXT("dirtyPackageRejected"), bDirtyRejected);
	Checks->SetBoolField(TEXT("stalePlanRejected"), bStalePlanRejected);
	Checks->SetBoolField(TEXT("wrongApplyConfirmationRejected"), bWrongApplyConfirmationRejected);
	Checks->SetBoolField(TEXT("malformedPlanIdRejected"), bBadPlanIdRejected);
	Checks->SetBoolField(TEXT("saveBeforeApplyRejected"), bSaveBeforeApplyRejected);
	Checks->SetBoolField(TEXT("wrongSaveFingerprintRejected"), bWrongSaveFingerprintRejected);
	Checks->SetBoolField(TEXT("wrongSaveRevisionRejected"), bWrongSaveRevisionRejected);
	Checks->SetBoolField(TEXT("wrongSaveConfirmationRejected"), bWrongSaveConfirmationRejected);
	Checks->SetBoolField(TEXT("previewReadOnly"), Preview->GetBoolField(TEXT("readOnly")));
	Checks->SetBoolField(TEXT("previewCompiledFalse"), !Preview->GetBoolField(TEXT("compiled")));
	Checks->SetBoolField(
		TEXT("applyNoCompileNoSave"),
		!ApplyResult->GetBoolField(TEXT("compiled"))
			&& !ApplyResult->GetBoolField(TEXT("saved"))
			&& !ApplyResult->GetBoolField(TEXT("skeletonRegenerated")));
	Checks->SetBoolField(
		TEXT("undoRestoredBeforeState"),
		ApplyResult->GetBoolField(TEXT("undoVerified")));
	Checks->SetBoolField(
		TEXT("redoReappliedAfterState"),
		ApplyResult->GetBoolField(TEXT("redoVerified")));
	Checks->SetBoolField(
		TEXT("nodeAndLinkCountPreserved"),
		static_cast<int32>(ApplyResult->GetNumberField(TEXT("nodeCount"))) == RootNodeCount
			&& static_cast<int32>(ApplyResult->GetNumberField(TEXT("linkCount"))) == RootLinkCount);
	Checks->SetBoolField(TEXT("packageCleanAfterSave"), !SaveResult->GetBoolField(TEXT("packageDirty")));
	Checks->SetBoolField(
		TEXT("independentReloadPendingCommandlet"),
		!SaveResult->GetBoolField(TEXT("independentReloadVerified")));

	TSharedRef<FJsonObject> Evidence = MakeShared<FJsonObject>();
	Evidence->SetStringField(TEXT("schemaVersion"), TEXT("ueak.blueprint-variable-evidence/1.0"));
	Evidence->SetNumberField(
		TEXT("processId"),
		static_cast<double>(FPlatformProcess::GetCurrentProcessId()));
	Evidence->SetStringField(TEXT("writtenAtUtc"), FDateTime::UtcNow().ToIso8601());
	Evidence->SetStringField(TEXT("assetPath"), Blueprint->GetPathName());
	Evidence->SetStringField(TEXT("assetFilename"), FPaths::ConvertRelativePathToFull(AssetFilename));
	Evidence->SetStringField(TEXT("graphGuid"), GuidLower(Graph->GraphGuid));
	Evidence->SetStringField(TEXT("planId"), PlanId);
	Evidence->SetNumberField(TEXT("expectedVariableCount"), 6);
	Evidence->SetNumberField(TEXT("expectedCreatedCount"), 4);
	Evidence->SetNumberField(TEXT("expectedExistingCount"), ExistingVariables.Num());
	Evidence->SetNumberField(TEXT("rootNodeCount"), RootNodeCount);
	Evidence->SetNumberField(TEXT("rootLinkCount"), RootLinkCount);
	Evidence->SetStringField(TEXT("assetSha256"), DiskDigest);
	Evidence->SetStringField(
		TEXT("variablesFingerprintAfter"),
		ApplyResult->GetStringField(TEXT("variablesFingerprintAfter")));
	Evidence->SetBoolField(
		TEXT("undoVerified"),
		ApplyResult->GetBoolField(TEXT("undoVerified")));
	Evidence->SetBoolField(
		TEXT("redoVerified"),
		ApplyResult->GetBoolField(TEXT("redoVerified")));
	Evidence->SetBoolField(TEXT("compiled"), false);
	Evidence->SetBoolField(TEXT("skeletonRegenerated"), false);
	Evidence->SetObjectField(TEXT("preview"), Preview);
	Evidence->SetObjectField(TEXT("apply"), ApplyResult);
	Evidence->SetObjectField(TEXT("save"), SaveResult);
	Evidence->SetArrayField(TEXT("expectedVariables"), CreatedEvidence);
	Evidence->SetArrayField(TEXT("expectedExistingVariables"), ExistingVariables);
	Evidence->SetArrayField(TEXT("expectedNodeGuids"), ExpectedNodeGuids);
	Evidence->SetArrayField(TEXT("expectedLinks"), ExpectedLinks);
	Evidence->SetObjectField(TEXT("checks"), Checks);

	const FString EvidencePath = FPaths::Combine(
		FPaths::ProjectSavedDir(),
		EvidenceSubdirectory,
		TEXT("evidence.json"));
	if (!TestTrue(
			TEXT("variable expansion evidence is written"),
			WriteJson(EvidencePath, Evidence)))
	{
		return Fail();
	}

	UEAgentKitBlueprintVariableExperimental::ResetExperimentalWriteForTest();
	return true;
}

namespace UEAgentKitBlueprintVariablePieGate
{
	const TCHAR* const EvidenceSubdirectory = TEXT("BlueprintVariableExpansion");

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

	struct FState
	{
		FAutomationTestBase* Test = nullptr;
		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = nullptr;
		int32 VariablesBefore = 0;
		double StartedAt = 0.0;
		bool bCompleted = false;
	};

	bool ResolveFixture(FState& OutState, FString& OutError)
	{
		UBlueprint* Blueprint = LoadObject<UBlueprint>(
			nullptr,
			UEAgentKitBlueprintVariableExpansion::AssetPath);
		if (Blueprint == nullptr || Blueprint->UbergraphPages.IsEmpty())
		{
			OutError = TEXT("The saved variable fixture is missing; run UEAgentKit.VariableCreate.Expansion first.");
			return false;
		}
		UEdGraph* Graph = Blueprint->UbergraphPages[0];
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
		OutState.VariablesBefore = Blueprint->NewVariables.Num();
		return true;
	}

	TSharedPtr<FJsonObject> MakePreviewParams()
	{
		TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
		Item->SetStringField(TEXT("name"), TEXT("PieVar"));
		Item->SetStringField(TEXT("type"), TEXT("int"));
		Item->SetStringField(TEXT("defaultValue"), TEXT("0"));
		TArray<TSharedPtr<FJsonValue>> Variables;
		Variables.Add(MakeShared<FJsonValueObject>(Item));
		TSharedRef<FJsonObject> Params = MakeShared<FJsonObject>();
		Params->SetStringField(
			TEXT("assetPath"),
			UEAgentKitBlueprintVariableExpansion::AssetPath);
		Params->SetArrayField(TEXT("variables"), Variables);
		return Params;
	}

	class FWaitForPieThenReject : public IAutomationLatentCommand
	{
	public:
		explicit FWaitForPieThenReject(const TSharedRef<FState>& InState)
			: State(InState)
		{
		}

		virtual bool Update() override
		{
			FAutomationTestBase* Test = State->Test;
			if (!State->bCompleted && State->StartedAt == 0.0)
			{
				State->StartedAt = FPlatformTime::Seconds();
			}
			const bool bPlaying = GEditor != nullptr
				&& GEditor->IsPlaySessionInProgress()
				&& GEditor->PlayWorld != nullptr;
			if (bPlaying)
			{
				TSharedPtr<FJsonObject> PreviewResult;
				FString PreviewCode;
				FString PreviewMessage;
				const bool bPreviewRejected =
					!UEAgentKitBlueprintVariableExperimental::TryPreviewExperimental(
						MakePreviewParams(),
						PreviewResult,
						PreviewCode,
						PreviewMessage);

				TSharedPtr<FJsonObject> ApplyParams = MakePreviewParams();
				const FString FabricatedPlanId =
					FString(TEXT("sha256:")) + FString::ChrN(64, TEXT('a'));
				ApplyParams->SetStringField(TEXT("planId"), FabricatedPlanId);
				ApplyParams->SetStringField(
					TEXT("confirmation"),
					FString(UEAgentKitBlueprintVariableExperimental::ApplyConfirmationPrefix)
						+ FabricatedPlanId);
				TSharedPtr<FJsonObject> ApplyResult;
				FString ApplyCode;
				FString ApplyMessage;
				const bool bApplyRejected =
					!UEAgentKitBlueprintVariableExperimental::TryApplyExperimental(
						ApplyParams,
						ApplyResult,
						ApplyCode,
						ApplyMessage);

				const bool bVariablesUnchanged =
					State->Blueprint->NewVariables.Num() == State->VariablesBefore
					&& UEAgentKitBlueprintVariableExpansion::FindVariable(
						State->Blueprint,
						TEXT("PieVar")) == nullptr;
				const bool bOk = bPreviewRejected
					&& PreviewCode == TEXT("blueprint-variable-pie-active")
					&& bApplyRejected
					&& ApplyCode == TEXT("blueprint-variable-pie-active")
					&& bVariablesUnchanged;

				TSharedRef<FJsonObject> Evidence = MakeShared<FJsonObject>();
				Evidence->SetStringField(
					TEXT("schemaVersion"),
					TEXT("ueak.blueprint-variable-pie-gate/1.0"));
				Evidence->SetNumberField(
					TEXT("processId"),
					static_cast<double>(FPlatformProcess::GetCurrentProcessId()));
				Evidence->SetStringField(TEXT("writtenAtUtc"), FDateTime::UtcNow().ToIso8601());
				Evidence->SetStringField(
					TEXT("assetPath"),
					State->Blueprint->GetPathName());
				Evidence->SetStringField(
					TEXT("graphGuid"),
					GuidLower(State->Graph->GraphGuid));
				Evidence->SetBoolField(TEXT("pieStarted"), true);
				Evidence->SetBoolField(TEXT("previewRejected"), bPreviewRejected);
				Evidence->SetStringField(TEXT("previewErrorCode"), PreviewCode);
				Evidence->SetStringField(TEXT("previewErrorMessage"), PreviewMessage);
				Evidence->SetBoolField(TEXT("applyRejected"), bApplyRejected);
				Evidence->SetStringField(TEXT("applyErrorCode"), ApplyCode);
				Evidence->SetStringField(TEXT("applyErrorMessage"), ApplyMessage);
				Evidence->SetBoolField(TEXT("variablesUnchanged"), bVariablesUnchanged);
				Evidence->SetNumberField(TEXT("variablesBefore"), State->VariablesBefore);
				Evidence->SetNumberField(
					TEXT("variablesAfter"),
					State->Blueprint->NewVariables.Num());
				Evidence->SetBoolField(TEXT("success"), bOk);
				const FString Path = FPaths::Combine(
					FPaths::ProjectSavedDir(),
					EvidenceSubdirectory,
					TEXT("pie-gate.json"));
				if (!WriteJson(Path, Evidence))
				{
					Test->AddError(TEXT("Could not write the PIE gate evidence."));
				}
				Test->TestTrue(
					TEXT("PIE rejects variable preview and apply with pie-active"),
					bOk);
				if (!bOk)
				{
					Test->AddError(FString::Printf(
						TEXT("PIE gate detail: preview=%s/%s apply=%s/%s unchanged=%s"),
						bPreviewRejected ? TEXT("rejected") : TEXT("accepted"),
						*PreviewCode,
						bApplyRejected ? TEXT("rejected") : TEXT("accepted"),
						*ApplyCode,
						bVariablesUnchanged ? TEXT("true") : TEXT("false")));
				}
				UEAgentKitBlueprintVariableExperimental::ResetExperimentalWriteForTest();
				State->bCompleted = true;
				return true;
			}
			if (FPlatformTime::Seconds() - State->StartedAt > 60.0)
			{
				TSharedRef<FJsonObject> Evidence = MakeShared<FJsonObject>();
				Evidence->SetStringField(
					TEXT("schemaVersion"),
					TEXT("ueak.blueprint-variable-pie-gate/1.0"));
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
						EvidenceSubdirectory,
						TEXT("pie-gate.json")),
					Evidence);
				Test->AddError(
					TEXT("PIE did not start within 60 seconds; variable PIE rejection was not run."));
				UEAgentKitBlueprintVariableExperimental::ResetExperimentalWriteForTest();
				State->bCompleted = true;
				return true;
			}
			return false;
		}

	private:
		TSharedRef<FState> State;
	};

	class FWaitForPieStop : public IAutomationLatentCommand
	{
	public:
		explicit FWaitForPieStop(const TSharedRef<FState>& InState)
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
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitBlueprintVariablePieGateTest,
	"UEAgentKit.VariableCreate.PieGate",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitBlueprintVariablePieGateTest::RunTest(const FString& Parameters)
{
	using namespace UEAgentKitBlueprintVariablePieGate;
	FString Error;
	TSharedRef<FState> State = MakeShared<FState>();
	State->Test = this;
	if (!ResolveFixture(*State, Error))
	{
		AddError(Error);
		return false;
	}
	UEAgentKitBlueprintVariableExperimental::SetExperimentalWriteEnabledForTest(true);
	ADD_LATENT_AUTOMATION_COMMAND(FStartPIECommand(false));
	ADD_LATENT_AUTOMATION_COMMAND(FWaitForPieThenReject(State));
	ADD_LATENT_AUTOMATION_COMMAND(FEndPlayMapCommand());
	ADD_LATENT_AUTOMATION_COMMAND(FWaitForPieStop(State));
	return true;
}

#endif // WITH_DEV_AUTOMATION_TESTS
