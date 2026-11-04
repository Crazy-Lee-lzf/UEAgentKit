#pragma once

#include "CoreMinimal.h"
#include "BlueprintEditBatch.h"

class UBlueprint;
class UEdGraph;

/**
 * Internal, game-thread-only Blueprint edit session manager.
 *
 * One session binds exactly one loaded Blueprint + one exact graph + this
 * Editor Bridge session + this process.  It exists so an Agent-authorized task
 * can apply several position-only move batches to one graph without a Save,
 * approval or Editor restart between batches.
 *
 * Contract: BOTH SIDES MAY EDIT (user decision, 2026-11-05).
 *
 *  - A session may begin on an already-Dirty package.  Unsaved user edits are
 *    no longer treated as unprovable ownership.
 *  - Conflict detection is per request, not per graph.  Before a batch, every
 *    node it names must still be at the exact old position the request
 *    declares; changes anywhere else in the graph (positions, node set, pins,
 *    comments, another user save) do not block the batch.
 *  - The package Dirty bit is not a condition.  A user save in the middle of a
 *    session does not invalidate it.
 *  - Rollback is selective: it restores only nodes the Agent moved whose live
 *    position still equals the position the Agent last wrote.  Nodes the user
 *    changed afterwards are skipped and reported, never overwritten, and never
 *    cause the whole rollback to be refused.
 *
 * Safety model (fail closed where it still matters):
 *  - The exact Blueprint/graph objects, asset path, graph GUID, Bridge session
 *    id, process id and token are re-proved before every batch.
 *  - Every write is one `FScopedTransaction` (`ApplyNodeMoveBatch`) with a full
 *    read-back; a finalizer failure restores the whole batch.
 *  - The Agent never saves, compiles or opens an asset here.
 *
 * Explicit limits:
 *  - Rollback is per-node position-only and in-process.  It is not a
 *    cross-save-point, crash-resilient or asset-level revert.
 *  - Nodes deleted by the user are skipped; a node re-created with the same GUID
 *    cannot be distinguished from the original.
 *  - There is NO UI/human edit lock and none is claimed: `exclusiveUI=false`,
 *    `humanEditingBlocked=false`, `requiresUserNotEdit=false`.  The user may
 *    edit the same graph while a session is open; the per-node proof above is
 *    what keeps the Agent from overwriting their work.
 *  - The R7 scoped UI gate is retired by user decision and stays inert; it is
 *    never armed and never calls SetEnabled.
 */
namespace UEAgentKitBlueprintEdit
{
	/** Two concatenated 32-character lowercase hex GUIDs. */
	constexpr int32 EditSessionTokenLength = 64;

	enum class EEditSessionEndMode : uint8
	{
		Keep,
		Rollback,
	};

	struct FEditSessionBeginInfo
	{
		FString Token;
		FString AssetPath;
		FGuid GraphGuid;
		/** Informational snapshot only; no longer a batch precondition. */
		FString ExpectedFingerprint;
		FString BridgeSessionId;
		uint32 ProcessId = 0;
		int32 NodeCount = 0;
		bool bPackageDirtyBefore = false;
		bool bPackageDirtyAfter = false;
		FString CreatedAtUtc;
	};

	struct FEditSessionEndInfo
	{
		FString Mode;
		/** True when the rollback pass completed (it may have skipped nodes). */
		bool bRolledBack = false;
		/** Nodes whose position was actually written back by the rollback transaction. */
		int32 RestoredNodeCount = 0;
		/** True when rollback opened the one C++ transaction; false for a no-op rollback. */
		bool bRestoreTransactionOpened = false;
		/** Nodes the user changed after the Agent wrote them; skipped, never overwritten. */
		int32 SkippedNodeCount = 0;
		TArray<FString> SkippedNodeGuids;
		bool bPackageDirtyAfter = false;
		/** Informational only: false is normal when either side edited the graph. */
		bool bFingerprintMatchedExpected = false;
		int32 BatchCount = 0;
		int32 MovedBatchCount = 0;
	};

	/** True only for exactly 64 lowercase hexadecimal characters. */
	bool IsCanonicalEditSessionToken(const FString& Token);

#if WITH_DEV_AUTOMATION_TESTS
	/**
	 * Test-only fault seam (N1-R4.1).  The next CommitBatch call fails right
	 * before it mutates manager state, so the in-transaction finalizer path can
	 * be proven to restore the batch.  Compiled only into builds that enable dev
	 * automation tests; it is not reachable from MCP/socket parameters, the
	 * environment, or Shipping builds.  The armed flag is consumed once.
	 */
	void SetNextCommitBatchFinalizeFaultForTesting(bool bEnabled);
#endif

	class FBlueprintEditSessionManager final
	{
	public:
		FBlueprintEditSessionManager();
		~FBlueprintEditSessionManager();

		FBlueprintEditSessionManager(const FBlueprintEditSessionManager&) = delete;
		FBlueprintEditSessionManager& operator=(const FBlueprintEditSessionManager&) = delete;

		/** Drop all state.  Called from Bridge Start/Stop; never writes an asset. */
		void Reset();

		bool HasActiveSession() const;
		bool HasActiveSessionFor(const FString& AssetPath, const FGuid& GraphGuid) const;

		/**
		 * Bind the exact loaded target.  The caller has already validated the
		 * Editor, open Blueprint Editor, focus, editability and K2 schema.
		 * A Dirty package is allowed; the graph may contain unsaved user edits.
		 * Every node needs a unique valid NodeGuid.
		 */
		bool Begin(
			UBlueprint* Blueprint,
			UEdGraph* Graph,
			const FString& AssetPath,
			const FString& BridgeSessionId,
			FEditSessionBeginInfo& OutInfo,
			FString& OutErrorCode,
			FString& OutErrorMessage);

		/**
		 * Prove the bound target and token, then check every request against the
		 * live position it declares.  Only the nodes named by this batch are
		 * checked; the rest of the graph may have changed.  No mutation.
		 */
		bool ValidateBatch(
			UBlueprint* Blueprint,
			UEdGraph* Graph,
			const FString& AssetPath,
			const FGuid& GraphGuid,
			const FString& BridgeSessionId,
			const FString& Token,
			const TArray<FNodeMoveRequest>& Requests,
			FString& OutErrorCode,
			FString& OutErrorMessage);

		/**
		 * Called inside the batch transaction after the read-back.  Records, per
		 * written node, the pre-write position (rollback baseline) and the
		 * position the Agent just wrote (later user-edit detection).  It never
		 * opens a transaction itself.
		 */
		bool CommitBatch(
			UBlueprint* Blueprint,
			UEdGraph* Graph,
			const TArray<FNodeMoveRequest>& Requests,
			bool bChanged,
			FString& OutErrorCode,
			FString& OutErrorMessage);

		/**
		 * End the session.
		 *
		 * `keep` releases ownership only: it never writes the asset, saves or
		 * compiles.  `rollback` restores only nodes the Agent moved whose live
		 * position still equals the Agent's last write; nodes the user changed
		 * afterwards are skipped and reported (`skippedNodeCount` /
		 * `skippedNodeGuids`) instead of being overwritten.  The restore uses one
		 * `FScopedTransaction` through `ApplyNodeMoveBatch`, which reads every
		 * position back and restores the pre-write state on any failure.  A fully
		 * no-op rollback opens no transaction.  The package Dirty bit and graph
		 * content fingerprint are not preconditions.
		 */
		bool End(
			const FString& Token,
			const FString& BridgeSessionId,
			EEditSessionEndMode Mode,
			FEditSessionEndInfo& OutInfo,
			FString& OutErrorCode,
			FString& OutErrorMessage);

	private:
		struct FImpl;
		TUniquePtr<FImpl> Impl;
	};
}
