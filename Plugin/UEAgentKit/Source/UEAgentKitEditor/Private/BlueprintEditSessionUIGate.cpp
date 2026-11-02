#include "BlueprintEditSessionUIGate.h"

#if WITH_DEV_AUTOMATION_TESTS

#include "BlueprintEditor.h"
#include "Editor.h"
#include "HAL/IConsoleManager.h"
#include "Subsystems/AssetEditorSubsystem.h"

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
		// A new target always starts disarmed: a previous widget must never leak
		// a disabled state into the next session.
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
		if (State == EUIGateState::Armed)
		{
			return true;
		}

		UBlueprint* BoundBlueprint = Blueprint.Get();
		UEdGraph* BoundGraph = Graph.Get();
		if (BoundBlueprint == nullptr || BoundGraph == nullptr)
		{
			OutError = TEXT("no-active-session-target");
			State = EUIGateState::Unavailable;
			Reason = OutError;
			LogUIGateRefused(OutError);
			return false;
		}

		UAssetEditorSubsystem* AssetEditorSubsystem =
			GEditor != nullptr ? GEditor->GetEditorSubsystem<UAssetEditorSubsystem>() : nullptr;
		if (AssetEditorSubsystem == nullptr)
		{
			OutError = TEXT("asset-editor-subsystem-unavailable");
			State = EUIGateState::Unavailable;
			Reason = OutError;
			LogUIGateRefused(OutError);
			return false;
		}

		IAssetEditorInstance* Instance = AssetEditorSubsystem->FindEditorForAsset(BoundBlueprint, false);
		if (Instance == nullptr || Instance->GetEditorName() != FName(TEXT("BlueprintEditor")))
		{
			OutError = TEXT("blueprint-editor-not-open");
			State = EUIGateState::Unavailable;
			Reason = OutError;
			LogUIGateRefused(OutError);
			return false;
		}

		FBlueprintEditor* BlueprintEditor = static_cast<FBlueprintEditor*>(Instance);
		if (BlueprintEditor->GetBlueprintObj() != BoundBlueprint)
		{
			OutError = TEXT("blueprint-editor-mismatch");
			State = EUIGateState::Unavailable;
			Reason = OutError;
			LogUIGateRefused(OutError);
			return false;
		}

		// Side-effect guard: only arm the graph that is already focused, so the
		// single public widget lookup cannot create or reorder a tab.
		if (BlueprintEditor->GetFocusedGraph() != BoundGraph)
		{
			OutError = TEXT("graph-not-focused");
			State = EUIGateState::Unavailable;
			Reason = OutError;
			LogUIGateRefused(OutError);
			return false;
		}

		TSharedPtr<SGraphEditor> GraphWidget = BlueprintEditor->OpenGraphAndBringToFront(BoundGraph, false);
		if (!GraphWidget.IsValid() || GraphWidget->GetCurrentGraph() != BoundGraph)
		{
			OutError = TEXT("graph-widget-unavailable");
			State = EUIGateState::Unavailable;
			Reason = OutError;
			LogUIGateRefused(OutError);
			return false;
		}

		if (!GraphWidget->IsEnabled())
		{
			// Already non-interactive for another reason; taking ownership would
			// make release ambiguous, so refuse instead.
			OutError = TEXT("graph-widget-already-disabled");
			State = EUIGateState::Unavailable;
			Reason = OutError;
			LogUIGateRefused(OutError);
			return false;
		}

		const auto EnabledState = GraphWidget->GetEnabledStateAttribute();
		SavedEnabledState = EnabledState.ToAttribute();
		bSavedStateWasBound = EnabledState.IsBound();
		bSavedWasEnabled = GraphWidget->IsEnabled();

		GraphWidget->SetEnabled(false);
		if (GraphWidget->IsEnabled())
		{
			// SetEnabled assigns the cached value synchronously, so a still
			// enabled widget means the disable was not applied.
			GraphWidget->SetEnabled(SavedEnabledState);
			OutError = TEXT("graph-widget-disable-unverified");
			State = EUIGateState::Unavailable;
			Reason = OutError;
			LogUIGateRefused(OutError);
			return false;
		}

		Widget = GraphWidget;
		++Generation;
		State = EUIGateState::Armed;
		Reason.Reset();
		LogUIGate(FString::Printf(
			TEXT("armed generation=%d bound=%d"),
			Generation,
			bSavedStateWasBound ? 1 : 0));
		return true;
	}

	void FBlueprintEditSessionUIGate::Release()
	{
		TSharedPtr<SGraphEditor> Pinned = Widget.Pin();
		if (Pinned.IsValid())
		{
			// Restores the exact attribute captured at arm time; a bound getter
			// is re-bound rather than frozen to a bool.
			Pinned->SetEnabled(SavedEnabledState);
			if (!bSavedStateWasBound && Pinned->IsEnabled() != bSavedWasEnabled)
			{
				LogUIGate(TEXT("release did not restore the previous enabled state"));
			}
			LogUIGate(FString::Printf(TEXT("released generation=%d"), Generation));
		}

		Widget.Reset();
		SavedEnabledState = TAttribute<bool>();
		bSavedStateWasBound = false;
		bSavedWasEnabled = false;
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
		Snapshot.bSavedStateWasBound = bSavedStateWasBound;
		Snapshot.Generation = Generation;

		if (State != EUIGateState::Armed)
		{
			return Snapshot;
		}

		TSharedPtr<SGraphEditor> Pinned = Widget.Pin();
		Snapshot.bWidgetValid = Pinned.IsValid();
		if (!Pinned.IsValid())
		{
			// Compile/reconstruct or tab teardown destroyed the widget; the new
			// widget is enabled by construction, so the gate is gone and must
			// never be reported as still isolated.
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
				TEXT("state=%d reason=%s valid=%d graphMatches=%d enabled=%d bound=%d generation=%d"),
				static_cast<int32>(Snapshot.State),
				*Snapshot.Reason,
				Snapshot.bWidgetValid ? 1 : 0,
				Snapshot.bGraphMatches ? 1 : 0,
				Snapshot.bWidgetEnabled ? 1 : 0,
				Snapshot.bSavedStateWasBound ? 1 : 0,
				Snapshot.Generation));
		}

		// Host-only escape hatch.  Deliberately not reachable from MCP/socket
		// parameters; it exists so a person can always restore input.
		FAutoConsoleCommand GArmUIGate(
			TEXT("UEAgentKit.BlueprintEditSession.ArmUIGate"),
			TEXT("Test-only: disable the exact focused graph widget of the active Blueprint edit session (dev automation builds only)."),
			FConsoleCommandWithArgsDelegate::CreateStatic(&ArmUIGateCommand));

		FAutoConsoleCommand GReleaseUIGate(
			TEXT("UEAgentKit.BlueprintEditSession.ReleaseUIGate"),
			TEXT("Test-only: restore the graph widget enabled state and drop the UI gate (dev automation builds only)."),
			FConsoleCommandWithArgsDelegate::CreateStatic(&ReleaseUIGateCommand));

		FAutoConsoleCommand GUIGateStatus(
			TEXT("UEAgentKit.BlueprintEditSession.UIGateStatus"),
			TEXT("Test-only: print the UI gate state and revalidate widget liveness (dev automation builds only)."),
			FConsoleCommandWithArgsDelegate::CreateStatic(&UIGateStatusCommand));
	}
}

#endif // WITH_DEV_AUTOMATION_TESTS
