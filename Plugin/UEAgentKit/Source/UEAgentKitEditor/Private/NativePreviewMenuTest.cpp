// M3.0 isolated automation for the opt-in native preview menu path.
// NativeLayoutPreview and LegacyLayoutPreviewFallback are supplied by the P11
// host config; this test only invokes the real generated menu entry.

#include "BlueprintGraphLayoutP11Menu.h"
#include "BlueprintEditor.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "EditorBridge.h"
#include "Editor.h"
#include "Engine/Blueprint.h"
#include "HAL/FileManager.h"
#include "K2Node_CallFunction.h"
#include "HAL/PlatformProcess.h"
#include "HAL/PlatformTime.h"
#include "Misc/AutomationTest.h"
#include "Misc/CommandLine.h"
#include "Misc/FileHelper.h"
#include "Misc/Parse.h"
#include "Misc/PackageName.h"
#include "Misc/Paths.h"
#include "NativePreviewMenuSettings.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "Subsystems/AssetEditorSubsystem.h"
#include "Tests/AutomationEditorCommon.h"
#include "ToolMenu.h"
#include "ToolMenuContext.h"
#include "ToolMenuEntry.h"
#include "ToolMenus.h"
#include "UObject/SavePackage.h"

#if WITH_DEV_AUTOMATION_TESTS

namespace UEAgentKitNativeM3Test
{
	const TCHAR* FixturePath = TEXT("/Game/UEAgentKitP11/BP_LayoutP11.BP_LayoutP11");
	const TCHAR* P11SectionName = TEXT("UEAgentKitGraphLayoutP11");
	const TCHAR* P11EntryName = TEXT("UEAgentKitAutoArrangePreviewP11");

	FString ReceiptPath()
	{
		return FPaths::Combine(
			FPaths::ProjectSavedDir(), TEXT("UEAgentKit"), TEXT("P11Preview"), TEXT("menu-invocation.json"));
	}

	FString EvidencePath(const FString& Scenario)
	{
		return FPaths::Combine(
			FPaths::ProjectDir(), TEXT("Evidence"), FString::Printf(TEXT("m3-menu-%s.json"), *Scenario));
	}

	bool WriteJson(const FString& Path, const TSharedPtr<FJsonObject>& Object, FString& OutError)
	{
		FString Text;
		const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Text);
		if (!FJsonSerializer::Serialize(Object.ToSharedRef(), Writer)
			|| !FFileHelper::SaveStringToFile(Text, *Path, FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
		{
			OutError = FString::Printf(TEXT("Could not write %s."), *Path);
			return false;
		}
		return true;
	}

	UEdGraph* PrimaryGraph(UBlueprint* Blueprint)
	{
		return Blueprint != nullptr && Blueprint->UbergraphPages.Num() > 0 ? Blueprint->UbergraphPages[0] : nullptr;
	}

	UEdGraph* FindGraph(UBlueprint* Blueprint, const FString& Name)
	{
		if (Blueprint == nullptr)
		{
			return nullptr;
		}
		for (UEdGraph* Graph : Blueprint->UbergraphPages)
		{
			if (Graph != nullptr && Graph->GetName() == Name)
			{
				return Graph;
			}
		}
		for (UEdGraph* Graph : Blueprint->FunctionGraphs)
		{
			if (Graph != nullptr && Graph->GetName() == Name)
			{
				return Graph;
			}
		}
		return nullptr;
	}

	FBlueprintEditor* OpenAndFocus(UBlueprint* Blueprint, UEdGraph* Graph, FString& OutError)
	{
		if (GEditor == nullptr)
		{
			OutError = TEXT("GEditor is unavailable.");
			return nullptr;
		}
		UAssetEditorSubsystem* AssetEditor = GEditor->GetEditorSubsystem<UAssetEditorSubsystem>();
		if (AssetEditor == nullptr || !AssetEditor->OpenEditorForAsset(Blueprint))
		{
			OutError = TEXT("Could not open the P11 fixture editor.");
			return nullptr;
		}
		IAssetEditorInstance* Instance = AssetEditor->FindEditorForAsset(Blueprint, false);
		FBlueprintEditor* Editor = static_cast<FBlueprintEditor*>(Instance);
		if (Editor == nullptr || !Editor->OpenGraphAndBringToFront(Graph, true).IsValid())
		{
			OutError = TEXT("Could not focus the P11 fixture graph.");
			return nullptr;
		}
		return Editor;
	}

	UToolMenu* GenerateMenu(UEdGraph* Graph, UEdGraphNode* Node, UEdGraphPin* Pin, FToolMenuContext& OutContext)
	{
		UGraphNodeContextMenuContext* Context = NewObject<UGraphNodeContextMenuContext>();
		Context->Init(Graph, Node, Pin, false);
		OutContext = FToolMenuContext(TSharedPtr<FUICommandList>(), TSharedPtr<FExtender>(), Context);
		UToolMenus* ToolMenus = UToolMenus::Get();
		if (ToolMenus == nullptr)
		{
			return nullptr;
		}
		ToolMenus->RegisterMenu(TEXT("GraphEditor.GraphContextMenu.Common"));
		return ToolMenus->GenerateMenu(TEXT("GraphEditor.GraphContextMenu.Common"), OutContext);
	}

	FToolMenuEntry* FindEntry(UToolMenu* Menu)
	{
		if (Menu == nullptr)
		{
			return nullptr;
		}
		FToolMenuSection* Section = Menu->FindSection(P11SectionName);
		return Section != nullptr ? Section->FindEntry(P11EntryName) : nullptr;
	}

	bool SaveFixture(UBlueprint* Blueprint, FString& OutError)
	{
		UPackage* Package = Blueprint != nullptr ? Blueprint->GetOutermost() : nullptr;
		if (Package == nullptr)
		{
			OutError = TEXT("The M3 fixture package is unavailable.");
			return false;
		}
		Package->MarkPackageDirty();
		const FString Filename = FPackageName::LongPackageNameToFilename(
			Package->GetName(), FPackageName::GetAssetPackageExtension());
		FSavePackageArgs SaveArgs;
		SaveArgs.TopLevelFlags = RF_Public | RF_Standalone;
		SaveArgs.SaveFlags = SAVE_NoError;
		if (!UPackage::SavePackage(Package, Blueprint, *Filename, SaveArgs))
		{
			OutError = TEXT("Could not save the M3 fixture.");
			return false;
		}
		return true;
	}
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitNativeM3MenuTest,
	"UEAgentKit.Native.M3Menu",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitNativeM3MenuTest::RunTest(const FString& Parameters)
{
	(void)Parameters;
	FString Scenario;
	if (!TestTrue(
			TEXT("M3 scenario is provided"),
			FParse::Value(FCommandLine::Get(), TEXT("UEAgentKitM3Scenario="), Scenario)
			&& !Scenario.IsEmpty()))
	{
		return false;
	}
	if (!TestTrue(TEXT("M3 native flag is on"), FUEAgentKitNativePreviewSettings::Get().bNativeLayoutPreview))
	{
		return false;
	}
	FString AssetOverride;
	FString GraphOverride;
	FString GraphId;
	FString CapturePath;
	const bool bM32Graph = FParse::Value(FCommandLine::Get(), TEXT("UEAgentKitM3Asset="), AssetOverride)
		&& !AssetOverride.IsEmpty();
	UBlueprint* Blueprint = bM32Graph
		? LoadObject<UBlueprint>(nullptr, *AssetOverride)
		: LoadObject<UBlueprint>(nullptr, UEAgentKitNativeM3Test::FixturePath);
	UEdGraph* Graph = nullptr;
	if (bM32Graph)
	{
		FParse::Value(FCommandLine::Get(), TEXT("UEAgentKitM3GraphName="), GraphOverride);
		FParse::Value(FCommandLine::Get(), TEXT("UEAgentKitM32GraphId="), GraphId);
		FParse::Value(FCommandLine::Get(), TEXT("UEAgentKitM32Capture="), CapturePath);
		Graph = UEAgentKitNativeM3Test::FindGraph(Blueprint, GraphOverride);
	}
	else
	{
		Graph = UEAgentKitNativeM3Test::PrimaryGraph(Blueprint);
	}
	if (!TestNotNull(TEXT("M3 graph fixture loads"), Blueprint)
		|| !TestNotNull(TEXT("M3 graph loads"), Graph))
	{
		return false;
	}
	UEdGraphNode* ContextNode = nullptr;
	UEdGraphPin* ContextPin = nullptr;
	for (UEdGraphNode* Node : Graph->Nodes)
	{
		if (Node == nullptr)
		{
			continue;
		}
		for (UEdGraphPin* Pin : Node->Pins)
		{
			if (Pin != nullptr && Pin->Direction == EGPD_Input && Pin->PinName == FName(TEXT("InString")))
			{
				ContextNode = Node;
				ContextPin = Pin;
				break;
			}
		}
		if (ContextNode != nullptr)
		{
			break;
		}
	}
	if (!TestNotNull(TEXT("M3 context node exists"), ContextNode)
		|| !TestNotNull(TEXT("M3 context pin exists"), ContextPin))
	{
		return false;
	}
	if (bM32Graph && !CapturePath.IsEmpty())
	{
		FString CaptureOpenError;
		TestNotNull(
			TEXT("M3 capture editor opens"),
			UEAgentKitNativeM3Test::OpenAndFocus(Blueprint, Graph, CaptureOpenError));
		FUEAgentKitGraphLayoutP11Menu* ActiveMenu = FUEAgentKitGraphLayoutP11Menu::GetActiveForTest();
		FUEAgentKitEditorBridge* CaptureBridge = ActiveMenu != nullptr ? ActiveMenu->GetBridgeForTest() : nullptr;
		TSharedPtr<FJsonObject> CapturedPreflight;
		TSharedPtr<FJsonObject> CapturedEnvelope;
		FString CaptureErrorCode;
		FString CaptureErrorMessage;
		if (TestNotNull(TEXT("M3 capture bridge exists"), CaptureBridge)
			&& TestTrue(
				TEXT("M3 capture preflight builds"),
				CaptureBridge->BuildP9MenuPreviewInputs(
					CapturedPreflight, CapturedEnvelope, CaptureErrorCode, CaptureErrorMessage)))
		{
			TSharedPtr<FJsonObject> Capture = MakeShared<FJsonObject>();
			Capture->SetStringField(TEXT("schemaVersion"), TEXT("ueak.m32.graph-capture/1.0"));
			Capture->SetStringField(TEXT("id"), GraphId);
			Capture->SetStringField(TEXT("assetPath"), AssetOverride);
			Capture->SetStringField(TEXT("graphName"), Graph->GetName());
			Capture->SetStringField(
				TEXT("graphGuid"), Graph->GraphGuid.ToString(EGuidFormats::DigitsWithHyphensLower));
			Capture->SetObjectField(TEXT("preflight"), CapturedPreflight);
			Capture->SetObjectField(TEXT("envelope"), CapturedEnvelope);
			FString CaptureWriteError;
			TestTrue(
				TEXT("M3 graph capture writes"),
				UEAgentKitNativeM3Test::WriteJson(CapturePath, Capture, CaptureWriteError));
		}
		else
		{
			AddError(FString::Printf(TEXT("%s: %s"), *CaptureErrorCode, *CaptureErrorMessage));
		}
	}

	FString Error;
	FBlueprintEditor* Editor = UEAgentKitNativeM3Test::OpenAndFocus(Blueprint, Graph, Error);
	if (!TestNotNull(TEXT("M3 editor opens"), Editor))
	{
		AddError(Error);
		return false;
	}
	TestFalse(TEXT("M3 fixture is clean before the action"), Blueprint->GetOutermost()->IsDirty());

	FToolMenuContext MenuContext;
	UToolMenu* Menu = UEAgentKitNativeM3Test::GenerateMenu(Graph, ContextNode, ContextPin, MenuContext);
	FToolMenuEntry* Entry = UEAgentKitNativeM3Test::FindEntry(Menu);
	if (!TestNotNull(TEXT("M3 menu entry exists"), Entry))
	{
		return false;
	}
	FUEAgentKitGraphLayoutP11Menu::SetTestDialogSuppression(true);
	if (Scenario == TEXT("dirty"))
	{
		Blueprint->GetOutermost()->SetDirtyFlag(true);
	}
	const double InvokedAt = FPlatformTime::Seconds();
	const bool bInvoked = Entry->TryExecuteToolUIAction(MenuContext);
	const double InvokeSeconds = FPlatformTime::Seconds() - InvokedAt;
	TestTrue(TEXT("M3 menu action invoked"), bInvoked);
	TestTrue(TEXT("M3 menu action returned without waiting for the worker"), InvokeSeconds < 0.5);

	FUEAgentKitGraphLayoutP11Menu* Active = FUEAgentKitGraphLayoutP11Menu::GetActiveForTest();
	if (!TestNotNull(TEXT("M3 active menu runtime exists"), Active))
	{
		return false;
	}
	if (Scenario == TEXT("revision") || Scenario == TEXT("fingerprint"))
	{
		if (!TestTrue(
				TEXT("M3 stale scenario waits for broker response before mutation"),
				Active->WaitForNativeWorkerResponseForTest(8.0)))
		{
			return false;
		}
		UK2Node_CallFunction* Print = nullptr;
		for (UEdGraphNode* Node : Graph->Nodes)
		{
			Print = Cast<UK2Node_CallFunction>(Node);
			if (Print != nullptr)
			{
				break;
			}
		}
		if (!TestNotNull(TEXT("M3 stale mutation node exists"), Print))
		{
			return false;
		}
		Print->NodePosX += 40;
		if (Scenario == TEXT("revision"))
		{
			if (!TestTrue(TEXT("M3 revision mutation saves"), UEAgentKitNativeM3Test::SaveFixture(Blueprint, Error)))
			{
				AddError(Error);
				return false;
			}
		}
		else
		{
			Blueprint->GetOutermost()->SetDirtyFlag(false);
		}
	}
	if (Scenario == TEXT("busy"))
	{
		FToolMenuContext SecondContext;
		UToolMenu* SecondMenu = UEAgentKitNativeM3Test::GenerateMenu(Graph, ContextNode, ContextPin, SecondContext);
		FToolMenuEntry* SecondEntry = UEAgentKitNativeM3Test::FindEntry(SecondMenu);
		if (TestNotNull(TEXT("M3 second menu entry exists"), SecondEntry))
		{
			TestTrue(TEXT("M3 second action returns"), SecondEntry->TryExecuteToolUIAction(SecondContext));
		}
		TestTrue(TEXT("M3 second action was rejected as busy"), Active->GetBusyRejectCountForTest() >= 1);
	}
	TestTrue(
		TEXT("M3 native preview reaches terminal"),
		Active->WaitForNativePreviewForTest(15.0));
	TSharedPtr<FJsonObject> Receipt = Active->GetLastReceiptForTest();
	if (!TestTrue(TEXT("M3 receipt was written"), Receipt.IsValid()))
	{
		return false;
	}
	FUEAgentKitGraphLayoutP11Menu::SetTestDialogSuppression(false);

	const bool bOk = Receipt->GetBoolField(TEXT("ok"));
	const bool bFallback = Receipt->GetBoolField(TEXT("fallback"));
	const FString PreviewPath = Receipt->GetStringField(TEXT("previewPath"));
	const FString ErrorCode = Receipt->GetStringField(TEXT("errorCode"));
	if (Scenario == TEXT("happy") || Scenario == TEXT("busy"))
	{
		TestTrue(TEXT("M3 native plan produced"), bOk && Receipt->GetBoolField(TEXT("planProduced")));
		TestFalse(TEXT("M3 happy path did not fall back"), bFallback);
		TestEqual(TEXT("M3 happy preview path"), PreviewPath, FString(TEXT("native-broker")));
		TestTrue(TEXT("M3 planId present"), Receipt->GetStringField(TEXT("planId")).StartsWith(TEXT("layout:")));
		TestTrue(TEXT("M3 layoutHash present"), Receipt->GetStringField(TEXT("layoutHash")).StartsWith(TEXT("sha256:")));
	}
	else if (Scenario == TEXT("fallback"))
	{
		TestTrue(TEXT("M3 fallback produced a legacy plan"), bOk && Receipt->GetBoolField(TEXT("planProduced")));
		TestTrue(TEXT("M3 fallback indicator set"), bFallback);
		TestEqual(TEXT("M3 fallback preview path"), PreviewPath, FString(TEXT("legacy-fallback")));
		TestEqual(
			TEXT("M3 fallback preserves native code"),
			Receipt->GetStringField(TEXT("nativeErrorCode")),
			FString(TEXT("native-preview-broker-descriptor-missing")));
	}
	else if (Scenario == TEXT("strict"))
	{
		TestFalse(TEXT("M3 strict path produces no plan"), bOk || Receipt->GetBoolField(TEXT("planProduced")));
		TestFalse(TEXT("M3 strict path does not fall back"), bFallback);
		TestEqual(
			TEXT("M3 strict code"),
			ErrorCode,
			FString(TEXT("native-preview-broker-descriptor-missing")));
	}
	else if (Scenario == TEXT("token") || Scenario == TEXT("dacl") || Scenario == TEXT("forged")
		|| Scenario == TEXT("dirty") || Scenario == TEXT("revision") || Scenario == TEXT("fingerprint"))
	{
		TestFalse(TEXT("M3 security failure produces no plan"), bOk || Receipt->GetBoolField(TEXT("planProduced")));
		TestFalse(TEXT("M3 security failure does not fall back"), bFallback);
		TestTrue(TEXT("M3 security code present"), !ErrorCode.IsEmpty());
		if (Scenario == TEXT("dirty"))
		{
			TestEqual(TEXT("M3 dirty code"), ErrorCode, FString(TEXT("native-preview-package-dirty")));
		}
		else if (Scenario == TEXT("revision"))
		{
			TestEqual(TEXT("M3 revision code"), ErrorCode, FString(TEXT("native-preview-revision-conflict")));
		}
		else if (Scenario == TEXT("fingerprint"))
		{
			TestEqual(TEXT("M3 fingerprint code"), ErrorCode, FString(TEXT("native-preview-fingerprint-conflict")));
		}
		else if (Scenario == TEXT("forged"))
		{
			TestEqual(TEXT("M3 forged plan code"), ErrorCode, FString(TEXT("native-preview-plan-id-mismatch")));
		}
	}
	else if (Scenario == TEXT("unavailable"))
	{
		TestTrue(TEXT("M3 unavailable fallback produces a legacy plan"), bOk && Receipt->GetBoolField(TEXT("planProduced")));
		TestTrue(TEXT("M3 unavailable fallback indicator"), bFallback);
		TestEqual(
			TEXT("M3 unavailable native code"),
			Receipt->GetStringField(TEXT("nativeErrorCode")),
			FString(TEXT("native-preview-broker-unavailable")));
	}
	else if (Scenario == TEXT("oversize-response"))
	{
		TestFalse(TEXT("M3 oversized response produces no plan"), bOk || Receipt->GetBoolField(TEXT("planProduced")));
		TestFalse(TEXT("M3 oversized response does not fall back"), bFallback);
		TestEqual(
			TEXT("M3 oversized response code"),
			ErrorCode,
			FString(TEXT("native-preview-response-too-large")));
	}
	else if (Scenario == TEXT("timeout"))
	{
		TestTrue(TEXT("M3 timeout fallback produces a legacy plan"), bOk && Receipt->GetBoolField(TEXT("planProduced")));
		TestTrue(TEXT("M3 timeout fallback indicator"), bFallback);
		TestEqual(
			TEXT("M3 timeout native code"),
			Receipt->GetStringField(TEXT("nativeErrorCode")),
			FString(TEXT("native-preview-timeout")));
	}
	else if (Scenario == TEXT("timeout-strict"))
	{
		TestFalse(TEXT("M3 timeout strict produces no plan"), bOk || Receipt->GetBoolField(TEXT("planProduced")));
		TestFalse(TEXT("M3 timeout strict does not fall back"), bFallback);
		TestEqual(TEXT("M3 timeout strict code"), ErrorCode, FString(TEXT("native-preview-timeout")));
	}
	if (Scenario == TEXT("dirty"))
	{
		Blueprint->GetOutermost()->SetDirtyFlag(false);
	}
	TestFalse(TEXT("M3 preview does not dirty the package"), Blueprint->GetOutermost()->IsDirty());

	TSharedPtr<FJsonObject> Evidence = MakeShared<FJsonObject>();
	Evidence->SetStringField(TEXT("schemaVersion"), TEXT("ueak.m3.menu-evidence/1.0"));
	Evidence->SetStringField(TEXT("scenario"), Scenario);
	Evidence->SetNumberField(TEXT("invokeSeconds"), InvokeSeconds);
	Evidence->SetBoolField(TEXT("nonBlockingReturn"), InvokeSeconds < 0.5);
	Evidence->SetNumberField(TEXT("busyRejects"), Active->GetBusyRejectCountForTest());
	Evidence->SetNumberField(TEXT("waitSeconds"), Active->GetLastWaitSecondsForTest());
	Evidence->SetNumberField(TEXT("fallbackSeconds"), Active->GetLastFallbackSecondsForTest());
	Evidence->SetStringField(TEXT("nativeErrorCode"), Receipt->GetStringField(TEXT("nativeErrorCode")));
	Evidence->SetStringField(TEXT("previewPath"), PreviewPath);
	Evidence->SetObjectField(TEXT("receipt"), Receipt);
	Evidence->SetBoolField(TEXT("packageDirtyAfter"), Blueprint->GetOutermost()->IsDirty());
	FString EvidenceName = FString::Printf(TEXT("m3-menu-%s.json"), *Scenario);
	if (bM32Graph && !GraphId.IsEmpty())
	{
		EvidenceName = FString::Printf(TEXT("m32-menu-%s.json"), *GraphId);
	}
	const FString EvidencePath = FPaths::Combine(
		FPaths::ProjectDir(), TEXT("Evidence"), EvidenceName);
	TestTrue(
		TEXT("M3 evidence writes"),
		UEAgentKitNativeM3Test::WriteJson(EvidencePath, Evidence, Error));
	return true;
}


namespace UEAgentKitNativeM3Pie
{
	struct FState
	{
		FAutomationTestBase* Test = nullptr;
		UEdGraph* Graph = nullptr;
		UEdGraphNode* Node = nullptr;
		UEdGraphPin* Pin = nullptr;
		FString GraphId;
		bool bCompleted = false;
	};

	class FInvokeMenuDuringPie final : public IAutomationLatentCommand
	{
	public:
		explicit FInvokeMenuDuringPie(TSharedRef<FState> InState)
			: State(InState)
		{
		}

		virtual bool Update() override
		{
			if (State->bCompleted)
			{
				return true;
			}
			if (GEditor == nullptr || !GEditor->IsPlaySessionInProgress() || GEditor->PlayWorld == nullptr)
			{
				return false;
			}
			FToolMenuContext Context;
			UToolMenu* Menu = UEAgentKitNativeM3Test::GenerateMenu(
				State->Graph, State->Node, State->Pin, Context);
			FToolMenuEntry* Entry = UEAgentKitNativeM3Test::FindEntry(Menu);
			State->Test->TestNotNull(TEXT("M3 PIE menu entry exists"), Entry);
			if (Entry == nullptr)
			{
				State->bCompleted = true;
				return true;
			}
			State->Test->TestTrue(
				TEXT("M3 PIE menu action returns without waiting"),
				Entry->TryExecuteToolUIAction(Context));
			FUEAgentKitGraphLayoutP11Menu* Active = FUEAgentKitGraphLayoutP11Menu::GetActiveForTest();
			State->Test->TestNotNull(TEXT("M3 PIE active runtime exists"), Active);
			if (Active == nullptr)
			{
				State->bCompleted = true;
				return true;
			}
			State->Test->TestTrue(
				TEXT("M3 PIE preview reaches terminal"),
				Active->WaitForNativePreviewForTest(15.0));
			TSharedPtr<FJsonObject> Receipt = Active->GetLastReceiptForTest();
			State->Test->TestTrue(TEXT("M3 PIE receipt exists"), Receipt.IsValid());
			if (Receipt.IsValid())
			{
				const FString Code = Receipt->GetStringField(TEXT("errorCode"));
				State->Test->TestEqual(
					TEXT("M3 PIE fail-closed code"), Code, FString(TEXT("native-preview-pie-active")));
				State->Test->TestFalse(
					TEXT("M3 PIE does not fall back"), Receipt->GetBoolField(TEXT("fallback")));
				State->Test->TestFalse(
					TEXT("M3 PIE produces no plan"), Receipt->GetBoolField(TEXT("planProduced")));
				FString Error;
				TSharedPtr<FJsonObject> Evidence = MakeShared<FJsonObject>();
				Evidence->SetStringField(TEXT("schemaVersion"), TEXT("ueak.m3.pie-evidence/1.0"));
				Evidence->SetStringField(TEXT("scenario"), TEXT("pie"));
				Evidence->SetObjectField(TEXT("receipt"), Receipt);
				Evidence->SetBoolField(TEXT("packageDirtyAfter"), false);
				State->Test->TestTrue(
					TEXT("M3 PIE evidence writes"),
					UEAgentKitNativeM3Test::WriteJson(
						UEAgentKitNativeM3Test::EvidencePath(
						State->GraphId.IsEmpty()
							? TEXT("pie")
							: *FString::Printf(TEXT("pie-%s"), *State->GraphId)),
					Evidence,
					Error));
			}
			State->bCompleted = true;
			return true;
		}

	private:
		TSharedRef<FState> State;
	};

	class FWaitForPieStop final : public IAutomationLatentCommand
	{
	public:
		virtual bool Update() override
		{
			return GEditor == nullptr
				|| (!GEditor->IsPlaySessionInProgress() && GEditor->PlayWorld == nullptr);
		}
	};
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitNativeM3PieTest,
	"UEAgentKit.Native.M3PieMenu",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitNativeM3PieTest::RunTest(const FString& Parameters)
{
	(void)Parameters;
	if (!TestTrue(TEXT("M3 PIE native flag is on"), FUEAgentKitNativePreviewSettings::Get().bNativeLayoutPreview))
	{
		return false;
	}
	FString AssetOverride;
	FString GraphOverride;
	FString GraphId;
	const bool bM32Graph = FParse::Value(FCommandLine::Get(), TEXT("UEAgentKitM3Asset="), AssetOverride)
		&& !AssetOverride.IsEmpty();
	UBlueprint* Blueprint = bM32Graph
		? LoadObject<UBlueprint>(nullptr, *AssetOverride)
		: LoadObject<UBlueprint>(nullptr, UEAgentKitNativeM3Test::FixturePath);
	UEdGraph* Graph = nullptr;
	if (bM32Graph)
	{
		FParse::Value(FCommandLine::Get(), TEXT("UEAgentKitM3GraphName="), GraphOverride);
		FParse::Value(FCommandLine::Get(), TEXT("UEAgentKitM32GraphId="), GraphId);
		Graph = UEAgentKitNativeM3Test::FindGraph(Blueprint, GraphOverride);
	}
	else
	{
		Graph = UEAgentKitNativeM3Test::PrimaryGraph(Blueprint);
	}
	if (!TestNotNull(TEXT("M3 PIE fixture loads"), Blueprint)
		|| !TestNotNull(TEXT("M3 PIE graph loads"), Graph))
	{
		return false;
	}
	UEdGraphNode* ContextNode = nullptr;
	UEdGraphPin* ContextPin = nullptr;
	for (UEdGraphNode* Node : Graph->Nodes)
	{
		if (Node == nullptr)
		{
			continue;
		}
		for (UEdGraphPin* Pin : Node->Pins)
		{
			if (Pin != nullptr && Pin->Direction == EGPD_Input && Pin->PinName == FName(TEXT("InString")))
			{
				ContextNode = Node;
				ContextPin = Pin;
				break;
			}
		}
		if (ContextNode != nullptr)
		{
			break;
		}
	}
	if (!TestNotNull(TEXT("M3 PIE context node exists"), ContextNode)
		|| !TestNotNull(TEXT("M3 PIE context pin exists"), ContextPin))
	{
		return false;
	}
	FString Error;
	if (!TestNotNull(TEXT("M3 PIE editor opens"), UEAgentKitNativeM3Test::OpenAndFocus(Blueprint, Graph, Error)))
	{
		AddError(Error);
		return false;
	}
	FUEAgentKitGraphLayoutP11Menu::SetTestDialogSuppression(true);
	TSharedRef<UEAgentKitNativeM3Pie::FState> State = MakeShared<UEAgentKitNativeM3Pie::FState>();
	State->Test = this;
	State->Graph = Graph;
	State->Node = ContextNode;
	State->Pin = ContextPin;
	State->GraphId = GraphId;
	ADD_LATENT_AUTOMATION_COMMAND(FStartPIECommand(false));
	ADD_LATENT_AUTOMATION_COMMAND(UEAgentKitNativeM3Pie::FInvokeMenuDuringPie(State));
	ADD_LATENT_AUTOMATION_COMMAND(FEndPlayMapCommand());
	ADD_LATENT_AUTOMATION_COMMAND(UEAgentKitNativeM3Pie::FWaitForPieStop());
	return true;
}

#endif // WITH_DEV_AUTOMATION_TESTS
