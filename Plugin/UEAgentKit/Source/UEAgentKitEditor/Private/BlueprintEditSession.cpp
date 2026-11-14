#include "BlueprintEditSession.h"

#include "BlueprintEditBatch.h"
#include "BlueprintGraphLayoutApply.h"

#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "Engine/Blueprint.h"
#include "HAL/PlatformProcess.h"
#include "Misc/DateTime.h"
#include "UObject/Package.h"
#if WITH_DEV_AUTOMATION_TESTS
#include "HAL/IConsoleManager.h"
#include "BlueprintEditSessionUIGate.h"
#endif

namespace UEAgentKitBlueprintEdit
{
	namespace
	{
#if WITH_DEV_AUTOMATION_TESTS
		bool bNextCommitBatchFinalizeFault = false;

		bool ConsumeCommitBatchFinalizeFaultForTesting()
		{
			const bool bArmed = bNextCommitBatchFinalizeFault;
			bNextCommitBatchFinalizeFault = false;
			return bArmed;
		}
#endif

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
		FString BeginFingerprint;
		TMap<FGuid, FIntPoint> AgentBaselinePositions;
		TMap<FGuid, FIntPoint> AgentExpectedPositions;
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
#if WITH_DEV_AUTOMATION_TESTS
		// Restores the widget before the session state disappears.
		FBlueprintEditSessionUIGate::Get().ClearSessionTarget();
#endif
		Impl->bActive = false;
		Impl->Token.Reset();
		Impl->AssetPath.Reset();
		Impl->GraphGuid = FGuid();
		Impl->BridgeSessionId.Reset();
		Impl->ProcessId = 0;
		Impl->CreatedAtUtc.Reset();
		Impl->Blueprint.Reset();
		Impl->Graph.Reset();
		Impl->BeginFingerprint.Reset();
		Impl->AgentBaselinePositions.Reset();
		Impl->AgentExpectedPositions.Reset();
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
		// A Dirty package is explicitly allowed: the user may have unsaved edits
		// in this same graph.  Ownership is proven per node at write time instead
		// of by a global clean-package assumption.

		TMap<FGuid, FIntPoint> Positions;
		if (!ReadNodePositions(Graph, Positions, OutErrorCode, OutErrorMessage))
		{
			return false;
		}
		// Informational only.  The fingerprint no longer gates any batch, so an
		// unavailable fingerprint must not block the session.
		const FString Fingerprint =
			UEAgentKitBlueprintGraphLayout::ComputeGraphContentFingerprint(Graph);

		Impl->bActive = true;
		// The token protocol is strict 64-character lowercase hex: every
		// consumer (IsCanonicalEditSessionToken, the case-sensitive End()
		// comparison and the Python wrapper) accepts only ASCII lowercase.
		// EGuidFormats::Digits formats with %08X (uppercase) and therefore
		// produced a token this plugin's own validator rejects, so the
		// generator must request the lowercase format explicitly.
		Impl->Token = FGuid::NewGuid().ToString(EGuidFormats::DigitsLower)
			+ FGuid::NewGuid().ToString(EGuidFormats::DigitsLower);
		checkf(
			IsCanonicalEditSessionToken(Impl->Token),
			TEXT("A generated Blueprint edit session token must be exactly %d lowercase hex characters."),
			EditSessionTokenLength);
		Impl->AssetPath = AssetPath;
		Impl->GraphGuid = Graph->GraphGuid;
		Impl->BridgeSessionId = BridgeSessionId;
		Impl->ProcessId = FPlatformProcess::GetCurrentProcessId();
		Impl->CreatedAtUtc = FDateTime::UtcNow().ToIso8601();
		Impl->Blueprint = Blueprint;
		Impl->Graph = Graph;
		Impl->BeginFingerprint = Fingerprint;
		Impl->AgentBaselinePositions.Reset();
		Impl->AgentExpectedPositions.Reset();
		Impl->BatchCount = 0;
		Impl->MovedBatchCount = 0;

		OutInfo = FEditSessionBeginInfo();
		OutInfo.Token = Impl->Token;
		OutInfo.AssetPath = Impl->AssetPath;
		OutInfo.GraphGuid = Impl->GraphGuid;
		OutInfo.ExpectedFingerprint = Impl->BeginFingerprint;
		OutInfo.BridgeSessionId = Impl->BridgeSessionId;
		OutInfo.ProcessId = Impl->ProcessId;
		OutInfo.NodeCount = Positions.Num();
		OutInfo.bPackageDirtyBefore = Package->IsDirty();
		OutInfo.bPackageDirtyAfter = Package->IsDirty();
		OutInfo.CreatedAtUtc = Impl->CreatedAtUtc;
#if WITH_DEV_AUTOMATION_TESTS
		// Records the target only.  The test-only UI gate stays disarmed until
		// an explicit console command arms it; production never acquires it.
		FBlueprintEditSessionUIGate::Get().SetSessionTarget(Blueprint, Graph);
#endif
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
#if WITH_DEV_AUTOMATION_TESTS
		// Observe only: a lost/rebuild widget is recorded instead of silently
		// claiming the graph is still gated.  This never gates the write itself.
		FBlueprintEditSessionUIGate::Get().Query();
#endif
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
		TMap<FGuid, FIntPoint> LivePositions;
		if (!ReadNodePositions(Graph, LivePositions, OutErrorCode, OutErrorMessage))
		{
			return false;
		}

		// Per-request conflict detection only.  Changes anywhere else in the
		// graph (user drags, node add/remove, pin or comment edits, another
		// save) must not block this batch; only the exact nodes this batch
		// names have to still be where the caller says they are.
		for (const FNodeMoveRequest& Request : Requests)
		{
			const FIntPoint* Live = LivePositions.Find(Request.NodeGuid);
			if (Live == nullptr)
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("blueprint-edit-session-node-not-found"),
					FString::Printf(
						TEXT("Node %s is not present in the bound graph."),
						*Request.NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower)));
			}
			if (Live->X != Request.ExpectedX || Live->Y != Request.ExpectedY)
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("blueprint-edit-session-node-position-conflict"),
					FString::Printf(
						TEXT("Node %s is at (%d, %d), but this batch expected (%d, %d); a manual edit, Undo/Redo or another writer changed that exact node."),
						*Request.NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower),
						Live->X,
						Live->Y,
						Request.ExpectedX,
						Request.ExpectedY));
			}
		}

		OutErrorCode.Reset();
		OutErrorMessage.Reset();
		return true;
	}

	bool FBlueprintEditSessionManager::CommitBatch(
		UBlueprint* Blueprint,
		UEdGraph* Graph,
		const TArray<FNodeMoveRequest>& Requests,
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
#if WITH_DEV_AUTOMATION_TESTS
		if (ConsumeCommitBatchFinalizeFaultForTesting())
		{
			// Deliberately fail before any manager mutation, exactly where the
			// real post-apply proof lives.  The caller (ApplyNodeMoveBatch) must
			// restore positions/status/Dirty and cancel the transaction.
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-edit-session-finalize-fault-injected"),
				TEXT("Test-only finalizer fault: the batch must be restored and no write committed."));
		}
#endif
		// Record per-node Agent ownership.  The baseline is the pre-write
		// position the request declared (ValidateBatch proved it equals the live
		// position before the transaction); the expected position is what the
		// Agent just wrote.  If the user later moves that same node, the live
		// position stops matching and rollback skips it instead of overwriting
		// the user's move.
		for (const FNodeMoveRequest& Request : Requests)
		{
			const FIntPoint* Live = LivePositions.Find(Request.NodeGuid);
			if (Live == nullptr)
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("blueprint-edit-session-node-not-found"),
					FString::Printf(
						TEXT("Node %s disappeared while the batch was being finalized."),
						*Request.NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower)));
			}
			if (Live->X != Request.ToX || Live->Y != Request.ToY)
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("blueprint-edit-session-batch-readback-mismatch"),
					FString::Printf(
						TEXT("Node %s was written to (%d, %d) but reads back at (%d, %d)."),
						*Request.NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower),
						Request.ToX,
						Request.ToY,
						Live->X,
						Live->Y));
			}
			if (!Impl->AgentBaselinePositions.Contains(Request.NodeGuid))
			{
				Impl->AgentBaselinePositions.Add(
					Request.NodeGuid,
					FIntPoint(Request.ExpectedX, Request.ExpectedY));
			}
			Impl->AgentExpectedPositions.Add(Request.NodeGuid, *Live);
		}
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
			// Selective rollback: restore only nodes the Agent moved whose live
			// position still equals the position the Agent last wrote.  A node the
			// user moved afterwards is skipped and reported, never overwritten,
			// and the rest of the rollback still proceeds.  The package Dirty bit
			// and the graph content fingerprint are deliberately not preconditions.
			if (!bTargetStillBound)
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("blueprint-edit-session-rollback-target-changed"),
					TEXT("The bound Blueprint or graph was replaced, closed or reloaded; rollback refuses to guess a target."));
			}

			TMap<FGuid, FIntPoint> LivePositions;
			if (!ReadNodePositions(BoundGraph, LivePositions, OutErrorCode, OutErrorMessage))
			{
				return false;
			}

			TArray<FNodeMoveRequest> RestoreRequests;
			RestoreRequests.Reserve(Impl->AgentExpectedPositions.Num());
			for (const TPair<FGuid, FIntPoint>& AgentExpected : Impl->AgentExpectedPositions)
			{
				const FIntPoint* Baseline = Impl->AgentBaselinePositions.Find(AgentExpected.Key);
				const FIntPoint* Live = LivePositions.Find(AgentExpected.Key);

				// Node deleted, or the session lost its pre-write baseline.
				if (Baseline == nullptr || Live == nullptr)
				{
					OutInfo.SkippedNodeGuids.Add(
						AgentExpected.Key.ToString(EGuidFormats::DigitsWithHyphensLower));
					continue;
				}
				// The user moved this node after the Agent wrote it: never
				// overwrite that move.
				if (*Live != AgentExpected.Value)
				{
					OutInfo.SkippedNodeGuids.Add(
						AgentExpected.Key.ToString(EGuidFormats::DigitsWithHyphensLower));
					continue;
				}
				// Already back at its pre-Agent position: nothing to write.
				if (*Live == *Baseline)
				{
					continue;
				}

				FNodeMoveRequest Request;
				Request.NodeGuid = AgentExpected.Key;
				Request.ExpectedX = Live->X;
				Request.ExpectedY = Live->Y;
				Request.ToX = Baseline->X;
				Request.ToY = Baseline->Y;
				RestoreRequests.Add(Request);
			}
			OutInfo.SkippedNodeCount = OutInfo.SkippedNodeGuids.Num();

			TArray<FNodeMoveResult> RestoreResults;
			bool bRestoreChanged = false;
			if (RestoreRequests.Num() > 0)
			{
				if (!ApplyNodeMoveBatch(
					BoundBlueprint,
					BoundGraph,
					RestoreRequests,
					RestoreResults,
					bRestoreChanged,
					OutErrorCode,
					OutErrorMessage,
					EPackageDirtyGate::AllowSessionOwnedDirty,
					// One rollback must restore every Agent-owned node, not only
					// the 128-request transport cap of a single caller batch.
					NodeMoveRequestLimitUnlimited))
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
			}

			int32 ChangedCount = 0;
			for (const FNodeMoveResult& Result : RestoreResults)
			{
				if (Result.bChanged)
				{
					++ChangedCount;
				}
			}
			// `bRolledBack` means the rollback pass completed; the restored and
			// skipped counts describe what it actually did.  No eligible node
			// opens no transaction.
			OutInfo.bRolledBack = true;
			OutInfo.RestoredNodeCount = ChangedCount;
			OutInfo.bRestoreTransactionOpened = bRestoreChanged;
		}

		// Keep only releases ownership.  It never writes, saves or compiles.
		if (bTargetStillBound)
		{
			// Informational only: false is normal once either side edited the
			// graph during the session.
			const FString LiveFingerprint =
				UEAgentKitBlueprintGraphLayout::ComputeGraphContentFingerprint(BoundGraph);
			OutInfo.bFingerprintMatchedExpected =
				!LiveFingerprint.IsEmpty()
				&& !Impl->BeginFingerprint.IsEmpty()
				&& LiveFingerprint.Equals(Impl->BeginFingerprint, ESearchCase::CaseSensitive);
		}
		UPackage* Package = Impl->Blueprint.IsValid()
			? Impl->Blueprint->GetOutermost()
			: nullptr;
		OutInfo.bPackageDirtyAfter = Package != nullptr && Package->IsDirty();

#if WITH_DEV_AUTOMATION_TESTS
		// Release the test-only widget gate on both keep and rollback before
		// the session stops being observable.
		FBlueprintEditSessionUIGate::Get().ClearSessionTarget();
#endif
		Impl->bActive = false;
		Impl->Token.Reset();
		Impl->AssetPath.Reset();
		Impl->GraphGuid = FGuid();
		Impl->BridgeSessionId.Reset();
		Impl->ProcessId = 0;
		Impl->CreatedAtUtc.Reset();
		Impl->Blueprint.Reset();
		Impl->Graph.Reset();
		Impl->BeginFingerprint.Reset();
		Impl->AgentBaselinePositions.Reset();
		Impl->AgentExpectedPositions.Reset();
		Impl->BeginFingerprint.Reset();
		Impl->BatchCount = 0;
		Impl->MovedBatchCount = 0;
		OutErrorCode.Reset();
		OutErrorMessage.Reset();
		return true;
	}
}

#if WITH_DEV_AUTOMATION_TESTS
namespace UEAgentKitBlueprintEdit
{
	namespace
	{
		void ArmCommitBatchFinalizeFaultForTesting()
		{
			bNextCommitBatchFinalizeFault = true;
		}
	}

	void SetNextCommitBatchFinalizeFaultForTesting(const bool bEnabled)
	{
		bNextCommitBatchFinalizeFault = bEnabled;
	}

	// Test-build-only console seam for N1-R4.1.  Shipping and non-automation
	// builds do not compile this command at all.
	FAutoConsoleCommand GFailNextBlueprintEditSessionFinalize(
		TEXT("UEAgentKit.BlueprintEditSession.FailNextFinalize"),
		TEXT("Test-only: fail the next Blueprint edit-session batch finalizer (dev automation builds only)."),
		FConsoleCommandDelegate::CreateStatic(&ArmCommitBatchFinalizeFaultForTesting));
}
#endif
