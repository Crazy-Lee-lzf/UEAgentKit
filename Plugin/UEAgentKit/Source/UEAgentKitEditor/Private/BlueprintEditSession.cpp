#include "BlueprintEditSession.h"

#include "BlueprintEditBatch.h"
#include "BlueprintGraphLayoutApply.h"

#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "Engine/Blueprint.h"
#include "HAL/PlatformProcess.h"
#include "Misc/DateTime.h"
#include "UObject/Package.h"

namespace UEAgentKitBlueprintEdit
{
	namespace
	{
		bool Fail(
			FString& OutErrorCode,
			FString& OutErrorMessage,
			const TCHAR* Code,
			const FString& Message)
		{
			OutErrorCode = Code;
			OutErrorMessage = Message;
			return false;
		}

		bool IsGraphOwnedByBlueprint(UBlueprint* Blueprint, UEdGraph* Graph)
		{
			if (Blueprint == nullptr || Graph == nullptr)
			{
				return false;
			}
			if (Graph->GetOuter() == Blueprint)
			{
				return true;
			}
			// Element type differs across UE versions (raw UEdGraph* vs
			// TObjectPtr<UEdGraph>), so search each array through a generic
			// lambda instead of taking the address of the member arrays.
			const auto ContainsGraph = [Graph](const auto& Graphs)
			{
				for (UEdGraph* Candidate : Graphs)
				{
					if (Candidate == Graph)
					{
						return true;
					}
				}
				return false;
			};
			return ContainsGraph(Blueprint->UbergraphPages)
				|| ContainsGraph(Blueprint->FunctionGraphs)
				|| ContainsGraph(Blueprint->MacroGraphs)
				|| ContainsGraph(Blueprint->DelegateSignatureGraphs);
		}

		/**
		 * Read every node position and require one unique valid NodeGuid per
		 * node.  A duplicate or invalid identity makes any exactness claim
		 * unreliable, so the whole read fails.
		 */
		bool ReadNodePositions(
			UEdGraph* Graph,
			TMap<FGuid, FIntPoint>& OutPositions,
			FString& OutErrorCode,
			FString& OutErrorMessage)
		{
			OutPositions.Reset();
			if (Graph == nullptr)
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("blueprint-edit-session-graph-unavailable"),
					TEXT("The exact graph is unavailable."));
			}
			for (UEdGraphNode* Node : Graph->Nodes)
			{
				if (Node == nullptr)
				{
					continue;
				}
				if (!Node->NodeGuid.IsValid())
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("blueprint-edit-session-invalid-live-node"),
						TEXT("The live graph contains a node without a valid NodeGuid."));
				}
				if (OutPositions.Contains(Node->NodeGuid))
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("blueprint-edit-session-duplicate-live-node"),
						TEXT("The live graph contains duplicate NodeGuid values, so no exact target can be proven."));
				}
				OutPositions.Add(Node->NodeGuid, FIntPoint(Node->NodePosX, Node->NodePosY));
			}
			return true;
		}

		bool PackageDirtyMatches(
			UBlueprint* Blueprint,
			const bool bExpectedDirty,
			FString& OutErrorCode,
			FString& OutErrorMessage)
		{
			UPackage* Package = Blueprint != nullptr ? Blueprint->GetOutermost() : nullptr;
			if (Package == nullptr)
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("blueprint-edit-session-package-unavailable"),
					TEXT("The exact Blueprint has no package."));
			}
			if (Package->IsDirty() != bExpectedDirty)
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("blueprint-edit-session-package-dirty-changed"),
					bExpectedDirty
						? TEXT("The Blueprint package is no longer Dirty; it was saved or reverted outside this edit session.")
						: TEXT("The Blueprint package became Dirty outside this edit session; the session cannot prove ownership."));
			}
			return true;
		}
	}

	struct FBlueprintEditSessionManager::FImpl
	{
		bool bActive = false;
		FString Token;
		FString AssetPath;
		FGuid GraphGuid;
		FString BridgeSessionId;
		uint32 ProcessId = 0;
		FString CreatedAtUtc;
		TWeakObjectPtr<UBlueprint> Blueprint;
		TWeakObjectPtr<UEdGraph> Graph;
		FString InitialFingerprint;
		FString ExpectedFingerprint;
		TMap<FGuid, FIntPoint> InitialPositions;
		TMap<FGuid, FIntPoint> ExpectedPositions;
		bool bExpectedPackageDirty = false;
		int32 BatchCount = 0;
		int32 MovedBatchCount = 0;
	};

	FBlueprintEditSessionManager::FBlueprintEditSessionManager()
		: Impl(MakeUnique<FImpl>())
	{
	}

	FBlueprintEditSessionManager::~FBlueprintEditSessionManager() = default;

	void FBlueprintEditSessionManager::Reset()
	{
		Impl->bActive = false;
		Impl->Token.Reset();
		Impl->AssetPath.Reset();
		Impl->GraphGuid = FGuid();
		Impl->BridgeSessionId.Reset();
		Impl->ProcessId = 0;
		Impl->CreatedAtUtc.Reset();
		Impl->Blueprint.Reset();
		Impl->Graph.Reset();
		Impl->InitialFingerprint.Reset();
		Impl->ExpectedFingerprint.Reset();
		Impl->InitialPositions.Reset();
		Impl->ExpectedPositions.Reset();
		Impl->bExpectedPackageDirty = false;
		Impl->BatchCount = 0;
		Impl->MovedBatchCount = 0;
	}

	bool FBlueprintEditSessionManager::HasActiveSession() const
	{
		return Impl->bActive;
	}

	bool FBlueprintEditSessionManager::HasActiveSessionFor(
		const FString& AssetPath,
		const FGuid& GraphGuid) const
	{
		return Impl->bActive
			&& Impl->AssetPath.Equals(AssetPath, ESearchCase::CaseSensitive)
			&& Impl->GraphGuid == GraphGuid;
	}

	bool IsCanonicalEditSessionToken(const FString& Token)
	{
		if (Token.Len() != EditSessionTokenLength)
		{
			return false;
		}
		for (const TCHAR Character : Token)
		{
			const bool bHex = (Character >= TEXT('0') && Character <= TEXT('9'))
				|| (Character >= TEXT('a') && Character <= TEXT('f'));
			if (!bHex)
			{
				return false;
			}
		}
		return true;
	}

	bool FBlueprintEditSessionManager::Begin(
		UBlueprint* Blueprint,
		UEdGraph* Graph,
		const FString& AssetPath,
		const FString& BridgeSessionId,
		FEditSessionBeginInfo& OutInfo,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		if (Blueprint == nullptr || Graph == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-edit-session-graph-unavailable"),
				TEXT("The exact Blueprint and graph must both be loaded."));
		}
		if (Impl->bActive)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-edit-session-already-active"),
				TEXT("Another Blueprint edit session is already active; end it before beginning a new one."));
		}
		if (!IsGraphOwnedByBlueprint(Blueprint, Graph))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-edit-session-graph-mismatch"),
				TEXT("The graph is not owned by the exact requested Blueprint."));
		}
		UPackage* Package = Blueprint->GetOutermost();
		if (Package == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-edit-session-package-unavailable"),
				TEXT("The exact Blueprint has no package."));
		}
		if (Package->IsDirty())
		{
			// A dirty package may already contain user edits, another plugin's
			// changes or an earlier unsaved batch.  Ownership cannot be proven.
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-edit-session-package-dirty"),
				TEXT("The exact Blueprint package is already dirty; save or revert it before beginning an Agent edit session."));
		}

		TMap<FGuid, FIntPoint> Positions;
		if (!ReadNodePositions(Graph, Positions, OutErrorCode, OutErrorMessage))
		{
			return false;
		}
		const FString Fingerprint =
			UEAgentKitBlueprintGraphLayout::ComputeGraphContentFingerprint(Graph);
		if (Fingerprint.IsEmpty())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-edit-session-fingerprint-unavailable"),
				TEXT("The exact graph content fingerprint could not be computed."));
		}

		Impl->bActive = true;
		Impl->Token = FGuid::NewGuid().ToString(EGuidFormats::Digits)
			+ FGuid::NewGuid().ToString(EGuidFormats::Digits);
		Impl->AssetPath = AssetPath;
		Impl->GraphGuid = Graph->GraphGuid;
		Impl->BridgeSessionId = BridgeSessionId;
		Impl->ProcessId = FPlatformProcess::GetCurrentProcessId();
		Impl->CreatedAtUtc = FDateTime::UtcNow().ToIso8601();
		Impl->Blueprint = Blueprint;
		Impl->Graph = Graph;
		Impl->InitialFingerprint = Fingerprint;
		Impl->ExpectedFingerprint = Fingerprint;
		Impl->InitialPositions = Positions;
		Impl->ExpectedPositions = Positions;
		Impl->bExpectedPackageDirty = false;
		Impl->BatchCount = 0;
		Impl->MovedBatchCount = 0;

		OutInfo = FEditSessionBeginInfo();
		OutInfo.Token = Impl->Token;
		OutInfo.AssetPath = Impl->AssetPath;
		OutInfo.GraphGuid = Impl->GraphGuid;
		OutInfo.ExpectedFingerprint = Impl->ExpectedFingerprint;
		OutInfo.BridgeSessionId = Impl->BridgeSessionId;
		OutInfo.ProcessId = Impl->ProcessId;
		OutInfo.NodeCount = Positions.Num();
		OutInfo.CreatedAtUtc = Impl->CreatedAtUtc;
		OutErrorCode.Reset();
		OutErrorMessage.Reset();
		return true;
	}

	bool FBlueprintEditSessionManager::ValidateBatch(
		UBlueprint* Blueprint,
		UEdGraph* Graph,
		const FString& AssetPath,
		const FGuid& GraphGuid,
		const FString& BridgeSessionId,
		const FString& Token,
		const TArray<FNodeMoveRequest>& Requests,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		if (!Impl->bActive || !IsCanonicalEditSessionToken(Token))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-edit-session-token-invalid"),
				TEXT("The edit session token is unknown or the session already ended."));
		}
		if (!Token.Equals(Impl->Token, ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-edit-session-token-invalid"),
				TEXT("The edit session token does not match the active session."));
		}
		if (!BridgeSessionId.Equals(Impl->BridgeSessionId, ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-edit-session-editor-session-mismatch"),
				TEXT("The Editor Bridge session no longer matches the one that began this edit session."));
		}
		if (Impl->ProcessId != FPlatformProcess::GetCurrentProcessId())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-edit-session-editor-session-mismatch"),
				TEXT("The Editor process no longer matches the one that began this edit session."));
		}
		if (Blueprint == nullptr || Graph == nullptr
			|| Impl->Blueprint.Get() != Blueprint
			|| Impl->Graph.Get() != Graph)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-edit-session-target-changed"),
				TEXT("The bound Blueprint or graph object was replaced; the edit session is no longer valid."));
		}
		if (!Impl->AssetPath.Equals(AssetPath, ESearchCase::CaseSensitive)
			|| Impl->GraphGuid != GraphGuid
			|| Graph->GraphGuid != GraphGuid)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-edit-session-asset-mismatch"),
				TEXT("The request assetPath/graphGuid does not match the bound edit session target."));
		}
		if (!IsGraphOwnedByBlueprint(Blueprint, Graph))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-edit-session-graph-mismatch"),
				TEXT("The bound graph is no longer owned by the bound Blueprint."));
		}
		if (!PackageDirtyMatches(Blueprint, Impl->bExpectedPackageDirty, OutErrorCode, OutErrorMessage))
		{
			return false;
		}

		TMap<FGuid, FIntPoint> LivePositions;
		if (!ReadNodePositions(Graph, LivePositions, OutErrorCode, OutErrorMessage))
		{
			return false;
		}
		if (LivePositions.Num() != Impl->ExpectedPositions.Num())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-edit-session-node-set-changed"),
				TEXT("The live graph node set changed outside this edit session."));
		}
		for (const TPair<FGuid, FIntPoint>& Expected : Impl->ExpectedPositions)
		{
			const FIntPoint* Live = LivePositions.Find(Expected.Key);
			if (Live == nullptr || *Live != Expected.Value)
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("blueprint-edit-session-node-position-conflict"),
					FString::Printf(
						TEXT("Node %s is not at the position this edit session recorded; a manual edit, Undo/Redo or another writer changed the graph."),
						*Expected.Key.ToString(EGuidFormats::DigitsWithHyphensLower)));
			}
		}
		const FString LiveFingerprint =
			UEAgentKitBlueprintGraphLayout::ComputeGraphContentFingerprint(Graph);
		if (LiveFingerprint.IsEmpty()
			|| !LiveFingerprint.Equals(Impl->ExpectedFingerprint, ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-edit-session-fingerprint-conflict"),
				TEXT("The live graph content fingerprint no longer matches this edit session's expected state."));
		}

		for (const FNodeMoveRequest& Request : Requests)
		{
			const FIntPoint* Expected = Impl->ExpectedPositions.Find(Request.NodeGuid);
			if (Expected == nullptr)
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("blueprint-edit-session-node-not-found"),
					FString::Printf(
						TEXT("Node %s is not part of this edit session's bound graph state."),
						*Request.NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower)));
			}
			if (Expected->X != Request.ExpectedX || Expected->Y != Request.ExpectedY)
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("blueprint-edit-session-node-position-conflict"),
					FString::Printf(
						TEXT("Node %s expected (%d, %d), but this edit session recorded (%d, %d)."),
						*Request.NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower),
						Request.ExpectedX,
						Request.ExpectedY,
						Expected->X,
						Expected->Y));
			}
		}

		OutErrorCode.Reset();
		OutErrorMessage.Reset();
		return true;
	}

	bool FBlueprintEditSessionManager::CommitBatch(
		UBlueprint* Blueprint,
		UEdGraph* Graph,
		const bool bChanged,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		if (!Impl->bActive || Blueprint == nullptr || Graph == nullptr
			|| Impl->Blueprint.Get() != Blueprint
			|| Impl->Graph.Get() != Graph)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-edit-session-target-changed"),
				TEXT("The bound Blueprint or graph object is no longer valid."));
		}

		TMap<FGuid, FIntPoint> LivePositions;
		if (!ReadNodePositions(Graph, LivePositions, OutErrorCode, OutErrorMessage))
		{
			return false;
		}
		const FString LiveFingerprint =
			UEAgentKitBlueprintGraphLayout::ComputeGraphContentFingerprint(Graph);
		if (LiveFingerprint.IsEmpty())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-edit-session-fingerprint-unavailable"),
				TEXT("The exact graph content fingerprint could not be recomputed after the batch."));
		}

		UPackage* Package = Blueprint->GetOutermost();
		if (Package == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-edit-session-package-unavailable"),
				TEXT("The exact Blueprint has no package."));
		}
		Impl->ExpectedPositions = LivePositions;
		Impl->ExpectedFingerprint = LiveFingerprint;
		Impl->bExpectedPackageDirty = Package->IsDirty();
		++Impl->BatchCount;
		if (bChanged)
		{
			++Impl->MovedBatchCount;
		}
		OutErrorCode.Reset();
		OutErrorMessage.Reset();
		return true;
	}

	bool FBlueprintEditSessionManager::End(
		const FString& Token,
		const FString& BridgeSessionId,
		const EEditSessionEndMode Mode,
		FEditSessionEndInfo& OutInfo,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		OutInfo = FEditSessionEndInfo();
		OutInfo.Mode = Mode == EEditSessionEndMode::Rollback ? TEXT("rollback") : TEXT("keep");

		if (!Impl->bActive)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-edit-session-token-invalid"),
				TEXT("No Blueprint edit session is active."));
		}
		if (!IsCanonicalEditSessionToken(Token)
			|| !Token.Equals(Impl->Token, ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-edit-session-token-invalid"),
				TEXT("The edit session token does not match the active session."));
		}
		if (!BridgeSessionId.Equals(Impl->BridgeSessionId, ESearchCase::CaseSensitive)
			|| Impl->ProcessId != FPlatformProcess::GetCurrentProcessId())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-edit-session-editor-session-mismatch"),
				TEXT("The Editor Bridge session or process no longer matches the one that began this edit session."));
		}

		OutInfo.BatchCount = Impl->BatchCount;
		OutInfo.MovedBatchCount = Impl->MovedBatchCount;

		UBlueprint* BoundBlueprint = Impl->Blueprint.Get();
		UEdGraph* BoundGraph = Impl->Graph.Get();
		const bool bTargetStillBound =
			BoundBlueprint != nullptr
			&& BoundGraph != nullptr
			&& IsGraphOwnedByBlueprint(BoundBlueprint, BoundGraph)
			&& BoundGraph->GraphGuid == Impl->GraphGuid;

		if (Mode == EEditSessionEndMode::Rollback)
		{
			// Every proof below must hold before the first write; a failure keeps
			// the session active and leaves every node untouched.  The restore
			// itself reuses ApplyNodeMoveBatch, which opens exactly one
			// FScopedTransaction, reads every position back and restores the
			// pre-write state (positions, Blueprint status, Dirty bit) on any
			// mismatch instead of leaving a half-applied rollback.
			if (!bTargetStillBound)
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("blueprint-edit-session-rollback-target-changed"),
					TEXT("The bound Blueprint or graph was replaced, closed or reloaded; rollback refuses to guess a target."));
			}
			if (!PackageDirtyMatches(
				BoundBlueprint,
				Impl->bExpectedPackageDirty,
				OutErrorCode,
				OutErrorMessage))
			{
				OutErrorCode = TEXT("blueprint-edit-session-rollback-") + OutErrorCode.Mid(
					FString(TEXT("blueprint-edit-session-")).Len());
				return false;
			}

			TMap<FGuid, FIntPoint> LivePositions;
			if (!ReadNodePositions(BoundGraph, LivePositions, OutErrorCode, OutErrorMessage))
			{
				return false;
			}
			if (LivePositions.Num() != Impl->ExpectedPositions.Num())
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("blueprint-edit-session-rollback-node-set-changed"),
					TEXT("The live graph node set changed outside this edit session; rollback refuses to restore a different graph."));
			}
			for (const TPair<FGuid, FIntPoint>& Expected : Impl->ExpectedPositions)
			{
				const FIntPoint* Live = LivePositions.Find(Expected.Key);
				if (Live == nullptr || *Live != Expected.Value)
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("blueprint-edit-session-rollback-node-position-conflict"),
						FString::Printf(
							TEXT("Node %s is not at the position this edit session last recorded; a manual edit, Undo/Redo or another writer changed the graph."),
							*Expected.Key.ToString(EGuidFormats::DigitsWithHyphensLower)));
				}
			}
			const FString LiveFingerprint =
				UEAgentKitBlueprintGraphLayout::ComputeGraphContentFingerprint(BoundGraph);
			if (LiveFingerprint.IsEmpty()
				|| !LiveFingerprint.Equals(Impl->ExpectedFingerprint, ESearchCase::CaseSensitive))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("blueprint-edit-session-rollback-fingerprint-conflict"),
					TEXT("The live graph content no longer matches this edit session's expected state; rollback refuses to overwrite it."));
			}
			OutInfo.bFingerprintMatchedExpected = true;

			TArray<FNodeMoveRequest> RestoreRequests;
			RestoreRequests.Reserve(Impl->ExpectedPositions.Num());
			for (const TPair<FGuid, FIntPoint>& Expected : Impl->ExpectedPositions)
			{
				const FIntPoint* Initial = Impl->InitialPositions.Find(Expected.Key);
				if (Initial == nullptr)
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("blueprint-edit-session-rollback-state-incomplete"),
						TEXT("The edit session lost the initial position record; rollback cannot restore exactly."));
				}
				FNodeMoveRequest Request;
				Request.NodeGuid = Expected.Key;
				Request.ExpectedX = Expected.Value.X;
				Request.ExpectedY = Expected.Value.Y;
				Request.ToX = Initial->X;
				Request.ToY = Initial->Y;
				RestoreRequests.Add(Request);
			}

			TArray<FNodeMoveResult> RestoreResults;
			bool bRestoreChanged = false;
			if (!ApplyNodeMoveBatch(
				BoundBlueprint,
				BoundGraph,
				RestoreRequests,
				RestoreResults,
				bRestoreChanged,
				OutErrorCode,
				OutErrorMessage,
				EPackageDirtyGate::AllowSessionOwnedDirty))
			{
				const FString Prefix(TEXT("node-move-batch-"));
				if (OutErrorCode.StartsWith(Prefix, ESearchCase::CaseSensitive))
				{
					OutErrorCode =
						FString(TEXT("blueprint-edit-session-rollback-"))
						+ OutErrorCode.Mid(Prefix.Len());
				}
				else if (OutErrorCode.IsEmpty())
				{
					OutErrorCode = TEXT("blueprint-edit-session-rollback-failed");
				}
				return false;
			}

			int32 ChangedCount = 0;
			for (const FNodeMoveResult& Result : RestoreResults)
			{
				if (Result.bChanged)
				{
					++ChangedCount;
				}
			}
			// A no-op restore (net-zero session) opens no transaction and reports
			// zero restored nodes; the positions already equal the initial ones.
			OutInfo.bRolledBack = true;
			OutInfo.RestoredNodeCount = ChangedCount;
			OutInfo.bRestoreTransactionOpened = bRestoreChanged;
		}

		// Keep only releases ownership.  It never writes, saves or compiles.
		if (bTargetStillBound)
		{
			const FString LiveFingerprint =
				UEAgentKitBlueprintGraphLayout::ComputeGraphContentFingerprint(BoundGraph);
			if (Mode == EEditSessionEndMode::Keep)
			{
				OutInfo.bFingerprintMatchedExpected =
					!LiveFingerprint.IsEmpty()
					&& LiveFingerprint.Equals(Impl->ExpectedFingerprint, ESearchCase::CaseSensitive);
			}
		}
		UPackage* Package = Impl->Blueprint.IsValid()
			? Impl->Blueprint->GetOutermost()
			: nullptr;
		OutInfo.bPackageDirtyAfter = Package != nullptr && Package->IsDirty();

		Impl->bActive = false;
		Impl->Token.Reset();
		Impl->AssetPath.Reset();
		Impl->GraphGuid = FGuid();
		Impl->BridgeSessionId.Reset();
		Impl->ProcessId = 0;
		Impl->CreatedAtUtc.Reset();
		Impl->Blueprint.Reset();
		Impl->Graph.Reset();
		Impl->InitialPositions.Reset();
		Impl->ExpectedPositions.Reset();
		Impl->InitialFingerprint.Reset();
		Impl->ExpectedFingerprint.Reset();
		Impl->bExpectedPackageDirty = false;
		Impl->BatchCount = 0;
		Impl->MovedBatchCount = 0;
		OutErrorCode.Reset();
		OutErrorMessage.Reset();
		return true;
	}
}
