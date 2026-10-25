#include "Native/NativeGraphMenuBinding.h"

#include "Native/NativeNodeRegistry.h"

#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "Editor.h"
#include "Framework/Notifications/NotificationManager.h"
#include "ToolMenu.h"
#include "ToolMenuContext.h"
#include "ToolMenuDelegates.h"
#include "ToolMenuEntry.h"
#include "ToolMenuSection.h"
#include "ToolMenus.h"
#include "Widgets/Notifications/SNotificationList.h"

#define LOCTEXT_NAMESPACE "UEAgentKitNativeGraphMenu"

namespace
{
	const TCHAR* NativeMenuSectionName = TEXT("UEAgentKitNativeR1");
	const TCHAR* NativeMenuEntryName = TEXT("UEAgentKitNativeInspect");
	const TCHAR* NativeMenuToolId = TEXT("graph.nodeRegistry.catalog");
	const TCHAR* NativeMenuToolVersion = TEXT("1.0.0");

	bool bSuppressTestDialogs = false;

	bool IsPieActive()
	{
		return GEditor != nullptr && GEditor->PlayWorld != nullptr;
	}

	bool IsEligibleContext(const UGraphNodeContextMenuContext* Context)
	{
		if (Context == nullptr || Context->bIsDebugging || IsPieActive())
		{
			return false;
		}
		if (Context->Graph == nullptr)
		{
			return false;
		}
		// Only Blueprint/K2 graph context menus are eligible; non-Blueprint
		// graph editors must keep their legacy menu untouched.
		if (Context->Blueprint == nullptr)
		{
			return false;
		}
		// A blank canvas (graph only) must never receive the entry.  Both node
		// and pin menus provide at least one of these pointers.
		return Context->Node != nullptr || Context->Pin != nullptr;
	}
}

FUEAgentKitNativeGraphMenu::FUEAgentKitNativeGraphMenu() = default;

FUEAgentKitNativeGraphMenu::~FUEAgentKitNativeGraphMenu()
{
	Shutdown();
}

bool FUEAgentKitNativeGraphMenu::IsModeEligible(const ENativeRegistryMode InMode)
{
	// Shadow is intentionally excluded: it may be queried through the existing
	// native.invoke surface, but it must not alter the legacy menu at all.
	return InMode == ENativeRegistryMode::Active;
}

bool FUEAgentKitNativeGraphMenu::ShouldAddEntry(const UToolMenu* Menu)
{
	if (Menu == nullptr)
	{
		return false;
	}
	const FNativeNodeRegistry& Registry = FNativeNodeRegistry::Get();
	if (!IsModeEligible(Registry.GetMode()) || !Registry.IsAvailable())
	{
		return false;
	}
	return IsEligibleContext(Menu->FindContext<UGraphNodeContextMenuContext>());
}

void FUEAgentKitNativeGraphMenu::SetTestDialogSuppression(const bool bSuppress)
{
	bSuppressTestDialogs = bSuppress;
}

bool FUEAgentKitNativeGraphMenu::Startup()
{
	if (bRegistered)
	{
		return true;
	}

	const FNativeNodeRegistry& Registry = FNativeNodeRegistry::Get();
	if (!IsModeEligible(Registry.GetMode()) || !Registry.IsAvailable())
	{
		return false;
	}

	UToolMenus* ToolMenus = UToolMenus::Get();
	if (ToolMenus == nullptr)
	{
		return false;
	}
	UToolMenu* Menu = ToolMenus->ExtendMenu(TEXT("GraphEditor.GraphContextMenu.Common"));
	if (Menu == nullptr)
	{
		return false;
	}

	Menu->AddDynamicSection(
		NativeMenuSectionName,
		FNewToolMenuDelegate::CreateRaw(this, &FUEAgentKitNativeGraphMenu::AddContextMenuSection));
	bRegistered = true;
	return true;
}

void FUEAgentKitNativeGraphMenu::Shutdown()
{
	if (!bRegistered)
	{
		return;
	}
	if (UToolMenus* ToolMenus = UToolMenus::Get())
	{
		ToolMenus->RemoveSection(TEXT("GraphEditor.GraphContextMenu.Common"), NativeMenuSectionName);
	}
	bRegistered = false;
}

void FUEAgentKitNativeGraphMenu::AddContextMenuSection(UToolMenu* Menu)
{
	if (!ShouldAddEntry(Menu))
	{
		return;
	}

	FToolMenuSection& Section = Menu->FindOrAddSection(
		NativeMenuSectionName,
		LOCTEXT("Section", "UEAgentKit Native Registry (read-only)"));
	Section.AddMenuEntry(
		NativeMenuEntryName,
		LOCTEXT("InspectCatalog", "Inspect Native Node Registry (read-only)"),
		LOCTEXT(
			"InspectCatalogTip",
			"Read the native Blueprint node catalog. Read-only: no node creation, transaction, save, or package dirty."),
		FSlateIcon(),
		FToolUIActionChoice(FToolUIAction(FToolMenuExecuteAction::CreateRaw(
			this,
			&FUEAgentKitNativeGraphMenu::ExecuteFromContext))));
}

void FUEAgentKitNativeGraphMenu::ExecuteFromContext(const FToolMenuContext& Context)
{
	const UGraphNodeContextMenuContext* MenuContext = Context.FindContext<UGraphNodeContextMenuContext>();
	if (!IsEligibleContext(MenuContext))
	{
		Notify(TEXT("The native registry inspector requires a live graph node or pin context."));
		return;
	}

	TSharedPtr<FJsonObject> Result;
	FString ErrorCode;
	FString ErrorMessage;
	if (ExecuteReadOnlyTool(NativeMenuToolId, Result, ErrorCode, ErrorMessage))
	{
		int32 DescriptorCount = 0;
		const TSharedPtr<FJsonObject>* Data = nullptr;
		if (Result.IsValid() && Result->TryGetObjectField(TEXT("data"), Data) && Data != nullptr && Data->IsValid())
		{
			double Count = 0.0;
			if ((*Data)->TryGetNumberField(TEXT("descriptorCount"), Count))
			{
				DescriptorCount = static_cast<int32>(Count);
			}
		}
		Notify(FString::Printf(
			TEXT("Native node registry inspected (read-only): %d descriptors. No asset was changed."),
			DescriptorCount));
		return;
	}

	const FString Detail = ErrorMessage.IsEmpty()
		? FString()
		: FString::Printf(TEXT(" (%s)"), *ErrorMessage);
	Notify(FString::Printf(
		TEXT("Native node registry inspection failed: %s%s"),
		*ErrorCode,
		*Detail));
}

bool FUEAgentKitNativeGraphMenu::ExecuteMenuActionForTest(
	const FToolMenuContext& Context,
	const FString& ToolId,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	OutResult.Reset();
	OutErrorCode.Reset();
	OutErrorMessage.Reset();

	const UGraphNodeContextMenuContext* MenuContext = Context.FindContext<UGraphNodeContextMenuContext>();
	if (!IsEligibleContext(MenuContext))
	{
		OutErrorCode = IsPieActive() ? TEXT("live-editor-pie-active") : TEXT("native-schema-invalid-arguments");
		OutErrorMessage = IsPieActive()
			? TEXT("The native registry menu is unavailable while PIE or SIE is active.")
			: TEXT("A live graph node or pin context is required.");
		return false;
	}

	return ExecuteReadOnlyTool(ToolId, OutResult, OutErrorCode, OutErrorMessage);
}

bool FUEAgentKitNativeGraphMenu::ExecuteReadOnlyTool(
	const FString& ToolId,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage) const
{
	// This is the same process-wide dispatch used by native.invoke.  The
	// registry owns manifest/tool/version/argument validation and all gates.
	TSharedPtr<FJsonObject> Arguments = MakeShared<FJsonObject>();
	Arguments->SetNumberField(TEXT("maxItems"), 8);
	return FNativeNodeRegistry::Get().TryInvokeShadowTool(
		ToolId,
		NativeMenuToolVersion,
		Arguments,
		OutResult,
		OutErrorCode,
		OutErrorMessage);
}

void FUEAgentKitNativeGraphMenu::Notify(const FString& Text) const
{
	if (bSuppressTestDialogs)
	{
		UE_LOG(LogTemp, Display, TEXT("UEAgentKit native menu notification (suppressed): %s"), *Text);
		return;
	}

	FNotificationInfo Info(FText::FromString(Text));
	Info.ExpireDuration = 6.0f;
	Info.bUseSuccessFailIcons = false;
	FSlateNotificationManager::Get().AddNotification(Info);
}

#undef LOCTEXT_NAMESPACE
