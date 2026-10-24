// M3.0 isolated automation for the opt-in native preview menu path.
// NativeLayoutPreview and LegacyLayoutPreviewFallback are supplied by the P11
// host config; this test only invokes the real generated menu entry.

#include "BlueprintGraphLayoutP11Menu.h"
#include "BlueprintEditor.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "Editor.h"
#include "Engine/Blueprint.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformProcess.h"
#include "HAL/PlatformTime.h"
#include "Misc/AutomationTest.h"
#include "Misc/CommandLine.h"
#include "Misc/FileHelper.h"
#include "Misc/Parse.h"
#include "Misc/Paths.h"
#include "NativePreviewMenuSettings.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "Subsystems/AssetEditorSubsystem.h"
#include "ToolMenu.h"
#include "ToolMenuContext.h"
#include "ToolMenuEntry.h"
#include "ToolMenus.h"

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
	UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, UEAgentKitNativeM3Test::FixturePath);
	UEdGraph* Graph = UEAgentKitNativeM3Test::PrimaryGraph(Blueprint);
	if (!TestNotNull(TEXT("M3 P11 fixture loads"), Blueprint)
		|| !TestNotNull(TEXT("M3 P11 graph loads"), Graph))
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
	else if (Scenario == TEXT("token") || Scenario == TEXT("dacl"))
	{
		TestFalse(TEXT("M3 security failure produces no plan"), bOk || Receipt->GetBoolField(TEXT("planProduced")));
		TestFalse(TEXT("M3 security failure does not fall back"), bFallback);
		TestTrue(TEXT("M3 security code present"), !ErrorCode.IsEmpty());
	}
	TestFalse(TEXT("M3 preview does not dirty the package"), Blueprint->GetOutermost()->IsDirty());

	TSharedPtr<FJsonObject> Evidence = MakeShared<FJsonObject>();
	Evidence->SetStringField(TEXT("schemaVersion"), TEXT("ueak.m3.menu-evidence/1.0"));
	Evidence->SetStringField(TEXT("scenario"), Scenario);
	Evidence->SetNumberField(TEXT("invokeSeconds"), InvokeSeconds);
	Evidence->SetBoolField(TEXT("nonBlockingReturn"), InvokeSeconds < 0.5);
	Evidence->SetNumberField(TEXT("busyRejects"), Active->GetBusyRejectCountForTest());
	Evidence->SetObjectField(TEXT("receipt"), Receipt);
	Evidence->SetBoolField(TEXT("packageDirtyAfter"), Blueprint->GetOutermost()->IsDirty());
	TestTrue(
		TEXT("M3 evidence writes"),
		UEAgentKitNativeM3Test::WriteJson(UEAgentKitNativeM3Test::EvidencePath(Scenario), Evidence, Error));
	return true;
}

#endif // WITH_DEV_AUTOMATION_TESTS
