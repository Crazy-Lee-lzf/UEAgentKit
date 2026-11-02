#pragma once

#include "CoreMinimal.h"

#if WITH_DEV_AUTOMATION_TESTS

#include "EdGraph/EdGraph.h"
#include "Engine/Blueprint.h"
#include "GraphEditor.h"

/**
 * Test-only scoped input gate for one exact Blueprint graph widget.
 *
 * This is deliberately NOT wired into any production request path.  Production
 * responses keep `exclusiveUI=false` and `humanEditingBlocked=false`; nothing
 * here may change that until a Native UI acceptance run proves every release
 * path.
 *
 * Safety properties this prototype is built on (all public UE 5.6.1 API):
 *  - it disables exactly one SGraphEditor widget, never the Editor window,
 *    never another tab, never the console and never a global input hook;
 *  - it only arms when the bound graph is already the focused graph, so the
 *    only public widget lookup (OpenGraphAndBringToFront) cannot create or
 *    reorder a tab;
 *  - it snapshots the exact previous enabled attribute via
 *    SWidget::GetEnabledStateAttribute().ToAttribute(), which preserves a bound
 *    getter, and restores that attribute on release;
 *  - it holds the widget by TWeakPtr and reports Lost instead of claiming
 *    isolation when the widget is destroyed, re-enabled or re-pointed.
 */
namespace UEAgentKitBlueprintEdit
{
	enum class EUIGateState : uint8
	{
		Idle,
		Armed,
		Lost,
		Unavailable,
	};

	struct FUIGateSnapshot
	{
		EUIGateState State = EUIGateState::Idle;
		FString Reason;
		bool bWidgetValid = false;
		bool bGraphMatches = false;
		bool bWidgetEnabled = true;
		bool bSavedStateWasBound = false;
		int32 Generation = 0;
	};

	class FBlueprintEditSessionUIGate
	{
	public:
		static FBlueprintEditSessionUIGate& Get();

		/** Records the session target with weak object pointers only; no Slate access. */
		void SetSessionTarget(UBlueprint* InBlueprint, UEdGraph* InGraph);

		/** Releases an armed widget and forgets the target. */
		void ClearSessionTarget();

		/** Explicit arm; returns false and fills OutError when safety cannot be proven. */
		bool Arm(FString& OutError);

		/** Restores the exact previous enabled attribute when the widget is alive. */
		void Release();

		/** Revalidates liveness and may move Armed -> Lost. */
		FUIGateSnapshot Query();

		bool IsArmed() const
		{
			return State == EUIGateState::Armed;
		}

	private:
		EUIGateState State = EUIGateState::Idle;
		FString Reason;
		TWeakObjectPtr<UBlueprint> Blueprint;
		TWeakObjectPtr<UEdGraph> Graph;
		TWeakPtr<SGraphEditor> Widget;
		TAttribute<bool> SavedEnabledState;
		bool bSavedStateWasBound = false;
		bool bSavedWasEnabled = false;
		int32 Generation = 0;
	};
}

#endif // WITH_DEV_AUTOMATION_TESTS
