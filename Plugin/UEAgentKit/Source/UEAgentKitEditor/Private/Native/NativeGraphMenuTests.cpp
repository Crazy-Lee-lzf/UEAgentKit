#include "Native/NativeGraphMenuBinding.h"
#include "Native/NativeNodeRegistry.h"

#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "Editor.h"
#include "Editor/Transactor.h"
#include "Engine/Blueprint.h"
#include "Misc/AutomationTest.h"
#include "Misc/ScopeExit.h"
#include "ToolMenu.h"
#include "ToolMenuContext.h"
#include "ToolMenuEntry.h"
#include "ToolMenuSection.h"
#include "ToolMenus.h"
#include "UObject/Package.h"

#if WITH_DEV_AUTOMATION_TESTS

namespace UEAgentKitNativeGraphMenuTest
{
	namespace
	{
		const TCHAR* SectionName = TEXT("UEAgentKitNativeR1");
		const TCHAR* EntryName = TEXT("UEAgentKitNativeInspect");
		const TCHAR* CatalogTool = TEXT("graph.nodeRegistry.catalog");

		UGraphNodeContextMenuContext* MakeContext(
			UEdGraph* Graph,
			UEdGraphNode* Node,
			UEdGraphPin* Pin)
		{
			UGraphNodeContextMenuContext* Context = NewObject<UGraphNodeContextMenuContext>();
			Context->Init(Graph, Node, Pin, false);
			// Non-K2 contexts are rejected by the production registry; the menu
			// test only needs a stable non-null Blueprint identity.
			Context->Blueprint = NewObject<UBlueprint>(GetTransientPackage());
			return Context;
		}

		UToolMenu* GenerateMenu(
			UEdGraph* Graph,
			UEdGraphNode* Node,
			UEdGraphPin* Pin,
			FToolMenuContext& OutContext)
		{
			UGraphNodeContextMenuContext* Context = MakeContext(Graph, Node, Pin);
			OutContext = FToolMenuContext(TSharedPtr<FUICommandList>(), TSharedPtr<FExtender>(), Context);
			UToolMenus* ToolMenus = UToolMenus::Get();
			if (ToolMenus == nullptr)
			{
				return nullptr;
			}
			// The real SGraphEditor registers the common root before generating a
			// context menu.  Register it only when absent; repeated RegisterMenu
			// calls emit LogToolMenus warnings and make a clean pass impossible.
			if (!ToolMenus->IsMenuRegistered(TEXT("GraphEditor.GraphContextMenu.Common")))
			{
				ToolMenus->RegisterMenu(TEXT("GraphEditor.GraphContextMenu.Common"));
			}
			return ToolMenus->GenerateMenu(TEXT("GraphEditor.GraphContextMenu.Common"), OutContext);
		}

		FToolMenuEntry* FindNativeEntry(UToolMenu* Menu)
		{
			if (Menu == nullptr)
			{
				return nullptr;
			}
			FToolMenuSection* Section = Menu->FindSection(SectionName);
			return Section != nullptr ? Section->FindEntry(EntryName) : nullptr;
		}

		TArray<FString> SnapshotMenu(UToolMenu* Menu)
		{
			TArray<FString> Snapshot;
			if (Menu == nullptr)
			{
				return Snapshot;
			}
			for (const FToolMenuSection& Section : Menu->Sections)
			{
				Snapshot.Add(FString::Printf(TEXT("section:%s"), *Section.Name.ToString()));
				for (const FToolMenuEntry& Entry : Section.Blocks)
				{
					Snapshot.Add(FString::Printf(
						TEXT("entry:%s/%s"),
						*Section.Name.ToString(),
						*Entry.Name.ToString()));
				}
			}
			return Snapshot;
		}
	}
}

using namespace UEAgentKitNativeGraphMenuTest;

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitNativeGraphMenuTest,
	"UEAgentKit.Native.R1MenuBinding",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitNativeGraphMenuTest::RunTest(const FString& Parameters)
{
	FNativeNodeRegistry& Registry = FNativeNodeRegistry::Get();
	const bool bWasInitialized = Registry.IsInitialized();
	const ENativeRegistryMode OriginalMode = bWasInitialized ? Registry.GetMode() : ENativeRegistryMode::Off;
	ON_SCOPE_EXIT
	{
		// Restore the exact pre-test lifecycle.  A previously uninitialized
		// registry must stay uninitialized, not be left in Off.
		Registry.Shutdown();
		if (bWasInitialized)
		{
			Registry.Initialize(OriginalMode);
		}
		FUEAgentKitNativeGraphMenu::SetTestDialogSuppression(false);
	};

	UEdGraph* Graph = NewObject<UEdGraph>(GetTransientPackage());
	Graph->GraphGuid = FGuid::NewGuid();
	UEdGraphNode* Node = NewObject<UEdGraphNode>(Graph);
	Node->NodeGuid = FGuid::NewGuid();
	Graph->Nodes.Add(Node);
	UEdGraphPin* Pin = UEdGraphPin::CreatePin(Node);
	Pin->PinName = TEXT("TestPin");
	Pin->Direction = EGPD_Output;

	// OFF: no registration and no section.  The menu signature must be
	// byte-for-byte (section/entry order and names) unchanged.
	Registry.Shutdown();
	Registry.Initialize(ENativeRegistryMode::Off);
	TestEqual(TEXT("off mode state is Off"), static_cast<int32>(Registry.GetState()), static_cast<int32>(ENativeRegistryState::Off));
	TestEqual(TEXT("off mode is selected"), static_cast<int32>(Registry.GetMode()), static_cast<int32>(ENativeRegistryMode::Off));
	FUEAgentKitNativeGraphMenu::SetTestDialogSuppression(true);

	TSharedPtr<FUEAgentKitNativeGraphMenu> Menu = MakeShared<FUEAgentKitNativeGraphMenu>();
	ON_SCOPE_EXIT
	{
		// Covers every early return after registration; the explicit shutdown
		// below still verifies the production cleanup path itself.
		if (Menu.IsValid())
		{
			Menu->Shutdown();
		}
		Menu.Reset();
	};
	FToolMenuContext OffContextBefore;
	UToolMenu* OffMenuBefore = GenerateMenu(Graph, Node, nullptr, OffContextBefore);
	const TArray<FString> OffSnapshotBefore = SnapshotMenu(OffMenuBefore);
	TestFalse(TEXT("off mode does not register the native menu"), Menu->Startup());
	TestFalse(TEXT("off mode menu is not registered"), Menu->IsRegistered());
	FToolMenuContext OffContextAfter;
	UToolMenu* OffMenuAfter = GenerateMenu(Graph, Node, nullptr, OffContextAfter);
	const TArray<FString> OffSnapshotAfter = SnapshotMenu(OffMenuAfter);
	TestTrue(TEXT("off mode leaves the legacy menu unchanged"), OffSnapshotBefore == OffSnapshotAfter);
	TestNull(TEXT("off mode has no native section"), OffMenuAfter != nullptr ? OffMenuAfter->FindSection(SectionName) : nullptr);

	// SHADOW: registry is readable but the menu remains exactly as OFF.
	// Initialize is intentionally state-gated: every transition must be
	// Shutdown -> Initialize, otherwise this would silently stay Off.
	Registry.Shutdown();
	Registry.Initialize(ENativeRegistryMode::Shadow);
	if (!TestTrue(TEXT("shadow registry is ready"), Registry.IsAvailable()))
	{
		return false;
	}
	TestEqual(TEXT("shadow mode state is Ready"), static_cast<int32>(Registry.GetState()), static_cast<int32>(ENativeRegistryState::Ready));
	TestEqual(TEXT("shadow mode is selected"), static_cast<int32>(Registry.GetMode()), static_cast<int32>(ENativeRegistryMode::Shadow));
	FToolMenuContext ShadowContextBefore;
	UToolMenu* ShadowMenuBefore = GenerateMenu(Graph, Node, Pin, ShadowContextBefore);
	const TArray<FString> ShadowSnapshotBefore = SnapshotMenu(ShadowMenuBefore);
	TestFalse(TEXT("shadow mode does not register the native menu"), Menu->Startup());
	TestFalse(TEXT("shadow mode menu is not registered"), Menu->IsRegistered());
	FToolMenuContext ShadowContextAfter;
	UToolMenu* ShadowMenuAfter = GenerateMenu(Graph, Node, Pin, ShadowContextAfter);
	const TArray<FString> ShadowSnapshotAfter = SnapshotMenu(ShadowMenuAfter);
	TestTrue(TEXT("shadow mode leaves the legacy menu unchanged"), ShadowSnapshotBefore == ShadowSnapshotAfter);
	TestNull(TEXT("shadow mode has no native section"), ShadowMenuAfter != nullptr ? ShadowMenuAfter->FindSection(SectionName) : nullptr);

	// ACTIVE: the only mode that registers.  The section is context-gated.
	Registry.Shutdown();
	Registry.Initialize(ENativeRegistryMode::Active);
	if (!TestTrue(TEXT("active registry is ready"), Registry.IsAvailable()))
	{
		return false;
	}
	TestEqual(TEXT("active mode state is Ready"), static_cast<int32>(Registry.GetState()), static_cast<int32>(ENativeRegistryState::Ready));
	TestEqual(TEXT("active mode is selected"), static_cast<int32>(Registry.GetMode()), static_cast<int32>(ENativeRegistryMode::Active));
	TestTrue(TEXT("active mode registers the native menu"), Menu->Startup());
	TestTrue(TEXT("active mode reports registered"), Menu->IsRegistered());
	TestTrue(TEXT("repeated startup is idempotent"), Menu->Startup());
	TestTrue(TEXT("one active menu registration"), Menu->IsRegistered());

	FToolMenuContext NodeContext;
	UToolMenu* NodeMenu = GenerateMenu(Graph, Node, nullptr, NodeContext);
	TestNotNull(TEXT("node context finds the native entry"), FindNativeEntry(NodeMenu));
	FToolMenuSection* NativeSection = NodeMenu != nullptr ? NodeMenu->FindSection(SectionName) : nullptr;
	TestNotNull(TEXT("node context has the native section"), NativeSection);
	TestEqual(TEXT("native section has exactly one entry"), NativeSection != nullptr ? NativeSection->Blocks.Num() : 0, 1);

	FToolMenuContext PinContext;
	UToolMenu* PinMenu = GenerateMenu(Graph, Node, Pin, PinContext);
	TestNotNull(TEXT("pin context finds the native entry"), FindNativeEntry(PinMenu));

	FToolMenuContext EmptyContext;
	UToolMenu* EmptyMenu = GenerateMenu(Graph, nullptr, nullptr, EmptyContext);
	TestNull(TEXT("blank canvas has no native section"), EmptyMenu != nullptr ? EmptyMenu->FindSection(SectionName) : nullptr);

	// The menu action is the same read-only native.invoke dispatch.  A failed
	// unknown tool must not leave a transaction, dirty package, or asset.
	UPackage* GraphPackage = Graph->GetOutermost();
	const bool bDirtyBefore = GraphPackage != nullptr && GraphPackage->IsDirty();
	const int32 UndoCountBefore = (GEditor != nullptr && GEditor->Trans != nullptr) ? GEditor->Trans->GetUndoCount() : -1;
	TSharedPtr<FJsonObject> UnknownResult;
	FString UnknownCode;
	FString UnknownMessage;
	TestFalse(
		TEXT("unknown native menu action fails closed"),
		Menu->ExecuteMenuActionForTest(NodeContext, TEXT("graph.unknown.tool"), UnknownResult, UnknownCode, UnknownMessage));
	TestEqual(TEXT("unknown native menu action error code"), UnknownCode, FString(TEXT("native-tool-unknown")));
	TestFalse(TEXT("unknown native menu action leaves the package clean"), GraphPackage != nullptr && GraphPackage->IsDirty());
	const int32 UndoCountAfter = (GEditor != nullptr && GEditor->Trans != nullptr) ? GEditor->Trans->GetUndoCount() : -1;
	TestEqual(TEXT("unknown native menu action leaves undo count unchanged"), UndoCountAfter, UndoCountBefore);
	TestTrue(TEXT("unknown native menu action leaves dirty state unchanged"), bDirtyBefore == (GraphPackage != nullptr && GraphPackage->IsDirty()));

	TSharedPtr<FJsonObject> CatalogResult;
	FString CatalogCode;
	FString CatalogMessage;
	TestTrue(
		TEXT("catalog native menu action succeeds"),
		Menu->ExecuteMenuActionForTest(NodeContext, CatalogTool, CatalogResult, CatalogCode, CatalogMessage));
	TestTrue(TEXT("catalog result is read-only"), CatalogResult.IsValid() && CatalogResult->HasField(TEXT("readOnly")));
	bool bReadOnly = false;
	if (CatalogResult.IsValid())
	{
		CatalogResult->TryGetBoolField(TEXT("readOnly"), bReadOnly);
	}
	TestTrue(TEXT("catalog marks access read-only"), bReadOnly);
	FString SessionId;
	if (CatalogResult.IsValid())
	{
		CatalogResult->TryGetStringField(TEXT("editorSessionId"), SessionId);
	}
	TestFalse(TEXT("catalog result carries an editor session identity"), SessionId.IsEmpty());
	FString ResultToolId;
	if (CatalogResult.IsValid())
	{
		CatalogResult->TryGetStringField(TEXT("toolId"), ResultToolId);
	}
	TestEqual(TEXT("catalog action uses the same native tool id"), ResultToolId, FString(CatalogTool));
	TestFalse(TEXT("catalog action leaves the package clean"), GraphPackage != nullptr && GraphPackage->IsDirty());

	// Shutdown removes the dynamic section; a second shutdown is a no-op.
	Menu->Shutdown();
	TestFalse(TEXT("shutdown clears registration"), Menu->IsRegistered());
	FToolMenuContext AfterShutdownContext;
	UToolMenu* AfterShutdownMenu = GenerateMenu(Graph, Node, nullptr, AfterShutdownContext);
	TestNull(TEXT("shutdown removes the native section"), AfterShutdownMenu != nullptr ? AfterShutdownMenu->FindSection(SectionName) : nullptr);
	Menu->Shutdown();
	TestFalse(TEXT("repeated shutdown is idempotent"), Menu->IsRegistered());

	Menu.Reset();
	FUEAgentKitNativeGraphMenu::SetTestDialogSuppression(false);
	return true;
}

#endif // WITH_DEV_AUTOMATION_TESTS
