#include "BlueprintEditSessionUIGate.h"

#if WITH_DEV_AUTOMATION_TESTS

#include "HAL/IConsoleManager.h"

namespace UEAgentKitBlueprintEdit
{
	namespace
	{
		void LogUIGate(const FString& Message)
		{
			UE_LOG(LogTemp, Display, TEXT("[UEAgentKit][UIGate] %s"), *Message);
		}

		void LogUIGateRefused(const FString& Reason)
		{
			UE_LOG(LogTemp, Display, TEXT("[UEAgentKit][UIGate] arm refused: %s"), *Reason);
		}
	}

	FBlueprintEditSessionUIGate& FBlueprintEditSessionUIGate::Get()
	{
		static FBlueprintEditSessionUIGate Instance;
		return Instance;
	}

	void FBlueprintEditSessionUIGate::SetSessionTarget(UBlueprint* InBlueprint, UEdGraph* InGraph)
	{
		// Records weak pointers only.  No Slate access, no widget mutation.
		Release();
		Blueprint = InBlueprint;
		Graph = InGraph;
		State = EUIGateState::Idle;
		Reason.Reset();
	}

	void FBlueprintEditSessionUIGate::ClearSessionTarget()
	{
		Release();
		Blueprint.Reset();
		Graph.Reset();
		State = EUIGateState::Idle;
		Reason.Reset();
	}

	bool FBlueprintEditSessionUIGate::Arm(FString& OutError)
	{
		OutError.Reset();

		// FAIL CLOSED (R7, 2026-11-04).
		//
		// A reversible arm needs the original TAttribute<bool> so that a bound
		// getter can be restored on release.  UE 5.6.1 keeps that accessor
		// protected: SWidget::GetEnabledStateAttribute() lives in the
		// `protected:` block starting at SWidget.h:1678, which N1's native
		// build reproduced as error C2248.  SWidget::SetEnabled() replaces the
		// attribute, so a release could only guess `true`, permanently
		// flattening any dynamic binding.
		//
		// Therefore this prototype must not disable anything.  The previous
		// acquisition sequence (focus check -> OpenGraphAndBringToFront ->
		// identity check -> SetEnabled(false)) was removed rather than kept
		// behind a flag; it remains in git history at 5c92e0c for review.
		OutError = TEXT("enabled-attribute-not-restorable");
		State = EUIGateState::Unavailable;
		Reason = OutError;
		LogUIGateRefused(OutError);
		return false;
	}

	void FBlueprintEditSessionUIGate::Release()
	{
		// Nothing is disabled in this revision, so there is nothing to restore.
		// The weak handle stays part of the surface so a future reversible
		// implementation has exactly one place to release from.
		Widget.Reset();
		if (State == EUIGateState::Armed)
		{
			State = EUIGateState::Idle;
		}
	}

	FUIGateSnapshot FBlueprintEditSessionUIGate::Query()
	{
		FUIGateSnapshot Snapshot;
		Snapshot.State = State;
		Snapshot.Reason = Reason;
		Snapshot.Generation = Generation;

		if (State != EUIGateState::Armed)
		{
			return Snapshot;
		}

		// Dormant in this revision (Arm never succeeds), kept so a future
		// reversible implementation cannot silently claim a stale handle.
		TSharedPtr<SGraphEditor> Pinned = Widget.Pin();
		Snapshot.bWidgetValid = Pinned.IsValid();
		if (!Pinned.IsValid())
		{
			State = EUIGateState::Lost;
			Reason = TEXT("widget-destroyed");
		}
		else
		{
			Snapshot.bGraphMatches = Pinned->GetCurrentGraph() == Graph.Get();
			Snapshot.bWidgetEnabled = Pinned->IsEnabled();
			if (!Snapshot.bGraphMatches)
			{
				State = EUIGateState::Lost;
				Reason = TEXT("graph-changed");
			}
			else if (Snapshot.bWidgetEnabled)
			{
				State = EUIGateState::Lost;
				Reason = TEXT("widget-reenabled");
			}
		}

		Snapshot.State = State;
		Snapshot.Reason = Reason;
		return Snapshot;
	}

	namespace
	{
		void ArmUIGateCommand(const TArray<FString>&)
		{
			FString Error;
			if (!FBlueprintEditSessionUIGate::Get().Arm(Error))
			{
				LogUIGate(FString::Printf(TEXT("arm refused: %s"), *Error));
				return;
			}
			LogUIGate(TEXT("arm ok"));
		}

		void ReleaseUIGateCommand(const TArray<FString>&)
		{
			FBlueprintEditSessionUIGate::Get().Release();
			LogUIGate(TEXT("release requested"));
		}

		void UIGateStatusCommand(const TArray<FString>&)
		{
			const FUIGateSnapshot Snapshot = FBlueprintEditSessionUIGate::Get().Query();
			LogUIGate(FString::Printf(
				TEXT("state=%d reason=%s valid=%d graphMatches=%d enabled=%d generation=%d"),
				static_cast<int32>(Snapshot.State),
				*Snapshot.Reason,
				Snapshot.bWidgetValid ? 1 : 0,
				Snapshot.bGraphMatches ? 1 : 0,
				Snapshot.bWidgetEnabled ? 1 : 0,
				Snapshot.Generation));
		}

		// Host-only escape hatch.  Deliberately not reachable from MCP/socket
		// parameters; it exists so a person can always restore input.
		FAutoConsoleCommand GArmUIGate(
			TEXT("UEAgentKit.BlueprintEditSession.ArmUIGate"),
			TEXT("Test-only: arm the scoped graph gate. Always fails closed with enabled-attribute-not-restorable in this build (dev automation builds only)."),
			FConsoleCommandWithArgsDelegate::CreateStatic(&ArmUIGateCommand));

		FAutoConsoleCommand GReleaseUIGate(
			TEXT("UEAgentKit.BlueprintEditSession.ReleaseUIGate"),
			TEXT("Test-only: drop the UI gate bookkeeping (dev automation builds only)."),
			FConsoleCommandWithArgsDelegate::CreateStatic(&ReleaseUIGateCommand));

		FAutoConsoleCommand GUIGateStatus(
			TEXT("UEAgentKit.BlueprintEditSession.UIGateStatus"),
			TEXT("Test-only: print the UI gate state (dev automation builds only)."),
			FConsoleCommandWithArgsDelegate::CreateStatic(&UIGateStatusCommand));
	}
}

#endif // WITH_DEV_AUTOMATION_TESTS
