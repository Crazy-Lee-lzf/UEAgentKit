#include "EditorBridge.h"
#include "EditorBridgeHandlerUtils.h"
#include "BlueprintEditSession.h"
#include "BlueprintGraphLayoutApply.h"

#include "BlueprintEditor.h"
#include "Dom/JsonObject.h"
#include "EdGraph/EdGraph.h"
#include "EdGraphSchema_K2.h"
#include "Editor.h"
#include "Editor/Transactor.h"
#include "Engine/Blueprint.h"
#include "HAL/PlatformProcess.h"
#include "Subsystems/AssetEditorSubsystem.h"
#include "UObject/Package.h"

using namespace UEAgentKitEditorBridgePrivate;

namespace
{
	bool FailEditSession(
		FString& OutErrorCode,
		FString& OutErrorMessage,
		const TCHAR* Code,
		const FString& Message)
	{
		OutErrorCode = Code;
		OutErrorMessage = Message;
		return false;
	}

	bool IsCanonicalEditSessionGuid(const FString& Text, FGuid& OutGuid)
	{
		if (!FGuid::Parse(Text, OutGuid) || !OutGuid.IsValid())
		{
			return false;
		}
		return Text.Equals(
			OutGuid.ToString(EGuidFormats::DigitsWithHyphensLower),
			ESearchCase::CaseSensitive);
	}

	FString MapEditSessionLayoutErrorCode(const FString& Code)
	{
		const FString Prefix(TEXT("graph-layout-"));
		if (Code.StartsWith(Prefix, ESearchCase::CaseSensitive))
		{
			return FString(TEXT("blueprint-edit-session-")) + Code.Mid(Prefix.Len());
		}
		return Code.IsEmpty()
			? FString(TEXT("blueprint-edit-session-target-unavailable"))
			: Code;
	}

	/**
	 * The exact loaded Blueprint must already be open in an ordinary Blueprint
	 * Editor that owns it and currently focuses the exact graph, and that graph
	 * must be editable K2.  This is deliberately the same evidence the node move
	 * batch requires, duplicated here instead of refactoring the existing safe
	 * node-move path.
	 */
	bool ValidateOpenBlueprintEditorTarget(
		UBlueprint* Blueprint,
		UEdGraph* Graph,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		UAssetEditorSubsystem* AssetEditorSubsystem =
			GEditor != nullptr
				? GEditor->GetEditorSubsystem<UAssetEditorSubsystem>()
				: nullptr;
		if (AssetEditorSubsystem == nullptr)
		{
			return FailEditSession(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-edit-session-editor-unavailable"),
				TEXT("The Asset Editor Subsystem is unavailable."));
		}
		IAssetEditorInstance* EditorInstance =
			AssetEditorSubsystem->FindEditorForAsset(Blueprint, false);
		if (EditorInstance == nullptr
			|| EditorInstance->GetEditorName() != FName(TEXT("BlueprintEditor")))
		{
			return FailEditSession(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-edit-session-blueprint-not-open"),
				TEXT("The exact loaded Blueprint must already be open in an ordinary Blueprint Editor."));
		}
		FBlueprintEditor* BlueprintEditor = static_cast<FBlueprintEditor*>(EditorInstance);
		if (BlueprintEditor->GetBlueprintObj() != Blueprint)
		{
			return FailEditSession(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-edit-session-editor-mismatch"),
				TEXT("The Blueprint Editor does not own the exact requested Blueprint."));
		}
		if (BlueprintEditor->GetFocusedGraph() != Graph)
		{
			return FailEditSession(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-edit-session-graph-not-focused"),
				TEXT("Open and focus the exact target GraphGuid before beginning an edit session."));
		}
		if (!BlueprintEditor->IsEditable(Graph) || !Graph->bEditable)
		{
			return FailEditSession(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-edit-session-graph-not-editable"),
				TEXT("The focused graph is not editable in the current Editor state."));
		}
		if (Cast<UEdGraphSchema_K2>(Graph->GetSchema()) == nullptr)
		{
			return FailEditSession(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-edit-session-graph-not-k2"),
				TEXT("Blueprint edit sessions currently accept only an ordinary K2 Blueprint graph."));
		}
		return true;
	}
}

bool FUEAgentKitEditorBridge::TryBeginBlueprintEditSessionResult(
	const TSharedPtr<FJsonObject>& Params,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	// Exact three-field contract: assetPath, graphGuid, experimentalContinuous.
	if (!Params.IsValid() || Params->Values.Num() != 3)
	{
		return FailEditSession(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-edit-session-invalid-parameters"),
			TEXT("Required: assetPath, graphGuid, experimentalContinuous."));
	}
	for (const TPair<FString, TSharedPtr<FJsonValue>>& Entry : Params->Values)
	{
		if (Entry.Key != TEXT("assetPath")
			&& Entry.Key != TEXT("graphGuid")
			&& Entry.Key != TEXT("experimentalContinuous"))
		{
			return FailEditSession(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-edit-session-invalid-parameters"),
				TEXT("Unexpected Blueprint edit session parameter."));
		}
	}

	FString AssetPath;
	FString GraphGuidText;
	FGuid GraphGuid;
	bool bExperimentalContinuous = false;
	if (!Params->TryGetStringField(TEXT("assetPath"), AssetPath)
		|| !IsSafeGameAssetPath(AssetPath)
		|| !Params->TryGetStringField(TEXT("graphGuid"), GraphGuidText)
		|| !IsCanonicalEditSessionGuid(GraphGuidText, GraphGuid)
		|| !Params->TryGetBoolField(TEXT("experimentalContinuous"), bExperimentalContinuous))
	{
		return FailEditSession(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-edit-session-invalid-parameters"),
			TEXT("assetPath must be one exact /Game Object Path, graphGuid a canonical lowercase GUID, and experimentalContinuous a boolean."));
	}
	if (!bExperimentalContinuous)
	{
		return FailEditSession(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-edit-session-experimental-opt-in-required"),
			TEXT("Continuous Blueprint editing has no human/UI edit lock and is experimental; begin requires experimentalContinuous=true and the user must not edit the graph until the session is released."));
	}

	if (GEditor == nullptr)
	{
		return FailEditSession(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-edit-session-editor-unavailable"),
			TEXT("The Unreal Editor is unavailable."));
	}
	if (GEditor->IsPlaySessionInProgress() || GEditor->PlayWorld != nullptr)
	{
		return FailEditSession(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-edit-session-pie-active"),
			TEXT("Blueprint edit sessions are unavailable while PIE or SIE is active."));
	}
	if (GEditor->Trans == nullptr || !GEditor->CanTransact() || GEditor->Trans->IsActive())
	{
		return FailEditSession(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-edit-session-transaction-active"),
			TEXT("The Editor transaction history must be idle so each batch can be its own Undo/Redo unit."));
	}

	UBlueprint* Blueprint = nullptr;
	UEdGraph* Graph = nullptr;
	if (!UEAgentKitBlueprintGraphLayout::ResolveExactTarget(
		AssetPath,
		GraphGuidText,
		Blueprint,
		Graph,
		OutErrorCode,
		OutErrorMessage))
	{
		OutErrorCode = MapEditSessionLayoutErrorCode(OutErrorCode);
		return false;
	}
	if (!ValidateOpenBlueprintEditorTarget(
		Blueprint,
		Graph,
		OutErrorCode,
		OutErrorMessage))
	{
		return false;
	}

	UPackage* Package = Blueprint->GetOutermost();
	if (Package == nullptr)
	{
		return FailEditSession(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-edit-session-package-unavailable"),
			TEXT("The exact Blueprint has no package."));
	}
	if (Package->IsDirty())
	{
		// No ownership inference: a dirty package may already hold user edits,
		// another plugin's change or an earlier unsaved Agent batch.
		return FailEditSession(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-edit-session-package-dirty"),
			TEXT("The exact Blueprint package is already dirty; save or revert it before beginning an edit session."));
	}
	if (!BlueprintEditSessionManager.IsValid())
	{
		return FailEditSession(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-edit-session-editor-unavailable"),
			TEXT("The Blueprint edit session manager is unavailable."));
	}

	UEAgentKitBlueprintEdit::FEditSessionBeginInfo Info;
	if (!BlueprintEditSessionManager->Begin(
		Blueprint,
		Graph,
		AssetPath,
		SessionId,
		Info,
		OutErrorCode,
		OutErrorMessage))
	{
		return false;
	}

	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("action"), TEXT("begin-blueprint-edit-session"));
	Result->SetStringField(TEXT("editSessionToken"), Info.Token);
	Result->SetStringField(TEXT("assetPath"), Info.AssetPath);
	Result->SetStringField(
		TEXT("graphGuid"),
		Info.GraphGuid.ToString(EGuidFormats::DigitsWithHyphensLower));
	Result->SetStringField(TEXT("expectedGraphFingerprint"), Info.ExpectedFingerprint);
	Result->SetNumberField(TEXT("nodeCount"), Info.NodeCount);
	Result->SetStringField(TEXT("createdAtUtc"), Info.CreatedAtUtc);
	Result->SetBoolField(TEXT("packageDirtyBefore"), false);
	Result->SetBoolField(TEXT("packageDirtyAfter"), false);
	Result->SetBoolField(TEXT("exclusiveUI"), false);
	Result->SetBoolField(TEXT("humanEditingBlocked"), false);
	Result->SetBoolField(TEXT("requiresUserNotEdit"), true);
	Result->SetBoolField(TEXT("experimental"), true);
	Result->SetBoolField(TEXT("singleTransactionPerBatch"), true);
	Result->SetBoolField(TEXT("rollbackSupported"), true);
	Result->SetStringField(
		TEXT("rollbackStatus"),
		TEXT("supported-position-only-unsaved-same-graph"));
	Result->SetStringField(
		TEXT("rollbackScope"),
		TEXT("positions-captured-at-begin-while-unsaved"));
	// Keep is the only supported non-destructive end mode; it only releases
	// ownership.
	Result->SetBoolField(TEXT("saved"), false);
	Result->SetBoolField(TEXT("compiled"), false);
	Result->SetStringField(TEXT("evidenceLevel"), TEXT("same-process"));
	Result->SetBoolField(TEXT("acceptanceEligible"), false);
	Result->SetStringField(TEXT("requiredAcceptanceEvidence"), TEXT("independent-reload"));
	Result->SetStringField(TEXT("editorSessionId"), SessionId);
	Result->SetNumberField(
		TEXT("editorProcessId"),
		static_cast<double>(FPlatformProcess::GetCurrentProcessId()));
	OutResult = Result;
	return true;
}

bool FUEAgentKitEditorBridge::TryEndBlueprintEditSessionResult(
	const TSharedPtr<FJsonObject>& Params,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	// Exact one- or two-field contract: editSessionToken[, mode].
	if (!Params.IsValid() || Params->Values.Num() < 1 || Params->Values.Num() > 2)
	{
		return FailEditSession(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-edit-session-invalid-parameters"),
			TEXT("Required: editSessionToken with optional mode (keep|rollback)."));
	}
	for (const TPair<FString, TSharedPtr<FJsonValue>>& Entry : Params->Values)
	{
		if (Entry.Key != TEXT("editSessionToken") && Entry.Key != TEXT("mode"))
		{
			return FailEditSession(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-edit-session-invalid-parameters"),
				TEXT("Unexpected Blueprint edit session end parameter."));
		}
	}

	FString Token;
	if (!Params->TryGetStringField(TEXT("editSessionToken"), Token)
		|| !UEAgentKitBlueprintEdit::IsCanonicalEditSessionToken(Token))
	{
		return FailEditSession(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-edit-session-token-invalid"),
			TEXT("editSessionToken must be the exact 64-character lowercase hex token returned by begin."));
	}

	FString ModeText(TEXT("keep"));
	Params->TryGetStringField(TEXT("mode"), ModeText);
	UEAgentKitBlueprintEdit::EEditSessionEndMode Mode =
		UEAgentKitBlueprintEdit::EEditSessionEndMode::Keep;
	if (ModeText.Equals(TEXT("keep"), ESearchCase::CaseSensitive))
	{
		Mode = UEAgentKitBlueprintEdit::EEditSessionEndMode::Keep;
	}
	else if (ModeText.Equals(TEXT("rollback"), ESearchCase::CaseSensitive))
	{
		Mode = UEAgentKitBlueprintEdit::EEditSessionEndMode::Rollback;
	}
	else
	{
		return FailEditSession(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-edit-session-invalid-parameters"),
			TEXT("mode must be keep or rollback."));
	}

	if (!BlueprintEditSessionManager.IsValid())
	{
		return FailEditSession(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-edit-session-editor-unavailable"),
			TEXT("The Blueprint edit session manager is unavailable."));
	}

	UEAgentKitBlueprintEdit::FEditSessionEndInfo Info;
	if (!BlueprintEditSessionManager->End(
		Token,
		SessionId,
		Mode,
		Info,
		OutErrorCode,
		OutErrorMessage))
	{
		return false;
	}

	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("action"), TEXT("end-blueprint-edit-session"));
	Result->SetStringField(TEXT("mode"), Info.Mode);
	Result->SetBoolField(TEXT("ended"), true);
	Result->SetBoolField(TEXT("rolledBack"), Info.bRolledBack);
	Result->SetNumberField(TEXT("restoredNodeCount"), Info.RestoredNodeCount);
	Result->SetBoolField(
		TEXT("restoreTransactionOpened"),
		Info.bRestoreTransactionOpened);
	Result->SetNumberField(TEXT("batchCount"), Info.BatchCount);
	Result->SetNumberField(TEXT("movedBatchCount"), Info.MovedBatchCount);
	Result->SetBoolField(
		TEXT("fingerprintMatchedExpected"),
		Info.bFingerprintMatchedExpected);
	Result->SetBoolField(TEXT("packageDirtyAfter"), Info.bPackageDirtyAfter);
	// Ending a session releases ownership; it never saves, compiles or locks UI.
	Result->SetBoolField(TEXT("saved"), false);
	Result->SetBoolField(TEXT("compiled"), false);
	Result->SetBoolField(TEXT("exclusiveUI"), false);
	Result->SetBoolField(TEXT("humanEditingBlocked"), false);
	Result->SetBoolField(TEXT("rollbackSupported"), true);
	Result->SetStringField(
		TEXT("rollbackStatus"),
		TEXT("supported-position-only-unsaved-same-graph"));
	Result->SetStringField(TEXT("evidenceLevel"), TEXT("same-process"));
	Result->SetBoolField(TEXT("acceptanceEligible"), false);
	Result->SetStringField(TEXT("editorSessionId"), SessionId);
	Result->SetNumberField(
		TEXT("editorProcessId"),
		static_cast<double>(FPlatformProcess::GetCurrentProcessId()));
	OutResult = Result;
	return true;
}
