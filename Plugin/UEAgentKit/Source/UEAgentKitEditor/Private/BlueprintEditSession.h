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
 * can apply several position-only move batches to one Dirty Blueprint without
 * a Save, approval or Editor restart between batches.
 *
 * Safety model (all checks fail closed):
 *  - Only a clean package (no unsaved Dirty state) may be bound.
 *  - The manager records a strong content fingerprint of the graph (sorted
 *    NodeGuid + class + position + size + comment + name + every Pin's
 *    identity/type/links) and every node position at begin.
 *  - Before every batch the live fingerprint, every expected node position,
 *    package Dirty state, graph ownership and object identity must still match.
 *  - Undo/Redo, a manual node drag, a Save, a reload, a graph structural change
 *    or another plugin's mutation changes the fingerprint (or the Dirty state)
 *    and is refused.
 *
 * Explicit limits:
 *  - A mutation that writes the exact same content back (ABA) cannot be
 *    detected: the fingerprint is content-bound, not history-bound.
 *  - No engine modification hook is bound; this slice does not claim to know
 *    who changed the graph, only that its current content changed.
 *  - There is NO UI/human edit lock (see docs/BLUEPRINT_EDIT_SESSION.md); an
 *    active session only authorizes Agent batches, it does not block a person.
 *    The caller must ask the user not to edit while a session is open and must
 *    check `exclusiveUI=false` / `humanEditingBlocked=false`.
 *  - Rollback is implemented for this session only: it restores every node
 *    position captured at begin, while the package is still unsaved, the same
 *    graph/Blueprint objects are alive, and the live content fingerprint still
 *    equals the session's expected state.  It uses one C++
 *    `FScopedTransaction` (`ApplyNodeMoveBatch`) with a full read-back, and it
 *    refuses before writing when any proof fails, so it never overwrites a
 *    user edit.  It is not a cross-save-point, crash-resilient, or asset-level
 *    revert, and it is not Native-verified in this slice.
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
		FString ExpectedFingerprint;
		FString BridgeSessionId;
		uint32 ProcessId = 0;
		int32 NodeCount = 0;
		FString CreatedAtUtc;
	};

	struct FEditSessionEndInfo
	{
		FString Mode;
		/** True only when every node position equals the position captured at begin. */
		bool bRolledBack = false;
		/** Nodes whose position was actually written back by the rollback transaction. */
		int32 RestoredNodeCount = 0;
		/** True when rollback opened the one C++ transaction; false for a no-op rollback. */
		bool bRestoreTransactionOpened = false;
		bool bPackageDirtyAfter = false;
		bool bFingerprintMatchedExpected = false;
		int32 BatchCount = 0;
		int32 MovedBatchCount = 0;
	};

	/** True only for exactly 64 lowercase hexadecimal characters. */
	bool IsCanonicalEditSessionToken(const FString& Token);

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
		 * The package must be clean; every node needs a unique valid NodeGuid.
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
		 * Prove the exact bound target, content fingerprint, node positions,
		 * package Dirty state and token are all still valid, and that every
		 * request uses the expected position this session recorded.
		 * No mutation.
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
		 * Called only after ApplyNodeMoveBatch succeeded.  Re-reads the live
		 * graph and advances the expected fingerprint/positions.  It never
		 * opens a transaction itself.
		 */
		bool CommitBatch(
			UBlueprint* Blueprint,
			UEdGraph* Graph,
			bool bChanged,
			FString& OutErrorCode,
			FString& OutErrorMessage);

		/**
		 * End the session.
		 *
		 * `keep` releases ownership only: it never writes the asset, saves or
		 * compiles.  `rollback` first proves the token, Bridge session/process,
		 * bound objects, asset path/GUID, package Dirty state, node set,
		 * positions and content fingerprint, then restores every node position
		 * captured at begin inside one `FScopedTransaction` (via
		 * `ApplyNodeMoveBatch`, which reads every position back and restores the
		 * pre-write state on any failure).  A validation failure refuses before
		 * any write and leaves the session active; a fully no-op rollback opens
		 * no transaction.  Rollback is limited to position-only edits made by
		 * this session in the same unsaved package.
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
