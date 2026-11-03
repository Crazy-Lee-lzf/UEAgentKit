#pragma once

#include "CoreMinimal.h"

#if WITH_DEV_AUTOMATION_TESTS

#include "EdGraph/EdGraph.h"
#include "Engine/Blueprint.h"
#include "GraphEditor.h"

/**
 * Test-only scoped input gate for one exact Blueprint graph widget.
 *
 * CURRENT STATE: DISABLED / FAIL-CLOSED.  Arm() always refuses with
 * `enabled-attribute-not-restorable`, so this build never disables any widget.
 *
 * Why: UE 5.6.1 exposes no public way to read the current EnabledState
 * attribute.  `SWidget::SetEnabled(TAttribute<bool>)` and `SWidget::IsEnabled()`
 * are public, but the only accessor that returns the attribute itself,
 * `SWidget::GetEnabledStateAttribute()`, is declared in the `protected:` block
 * that starts at SWidget.h:1678 (verified against the installed engine and by
 * N1's native compile: error C2248).  Without that accessor the original
 * TAttribute<bool> -- including a bound getter -- cannot be snapshotted, so any
 * SetEnabled(false) would be irreversible: restoring `true` would permanently
 * flatten a dynamic binding.
 *
 * The previous revision of this file claimed the accessor was public and that
 * "exact restore" was proven.  That claim was wrong and is corrected here; see
 * docs/R7_ENABLED_BINDING_SAFETY_REVIEW_20261104.md.
 *
 * What remains is the inert state machine (target registration, status query,
 * release bookkeeping) and the host-only console commands, kept so a future
 * public-API-safe mechanism can be reviewed against the same surface.  No
 * production path arms it, no request path references it, and
 * `exclusiveUI` / `humanEditingBlocked` stay false.
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
		int32 Generation = 0;
	};

	class FBlueprintEditSessionUIGate
	{
	public:
		static FBlueprintEditSessionUIGate& Get();

		/** Records the session target with weak object pointers only; no Slate access. */
		void SetSessionTarget(UBlueprint* InBlueprint, UEdGraph* InGraph);

		/** Releases bookkeeping and forgets the target. */
		void ClearSessionTarget();

		/**
		 * Always fails closed in this revision: `enabled-attribute-not-restorable`.
		 * It must not call SWidget::SetEnabled until a reversible capture exists.
		 */
		bool Arm(FString& OutError);

		/** Clears any armed bookkeeping; never mutates a widget in this revision. */
		void Release();

		/** Revalidates liveness; may move Armed -> Lost. */
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
		int32 Generation = 0;
	};
}

#endif // WITH_DEV_AUTOMATION_TESTS
