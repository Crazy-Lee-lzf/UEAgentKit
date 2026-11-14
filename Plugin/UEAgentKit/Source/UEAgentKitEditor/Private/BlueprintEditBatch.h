#pragma once

#include "CoreMinimal.h"

class UBlueprint;
class UEdGraph;
class UEdGraphNode;

/**
 * Small, internal edit-batch/snapshot helper for resident Blueprint graph
 * edits.
 *
 * Scope for this slice: position-only node moves (one or many nodes in one
 * exact graph).  It exists so node creation, removal, pin edits and variable
 * writes can later share one atomic transaction and one read-back gate instead
 * of each inventing its own partial-write path.
 *
 * Guarantees:
 *  - All request validation happens before the transaction is opened.
 *  - Exactly one FScopedTransaction owns every changed node.
 *  - Every changed position is read back; any mismatch restores all snapshots,
 *    restores the pre-write package Dirty state, and cancels the transaction.
 *  - A batch where no position changes opens no transaction and does not dirty
 *    the package.
 *  - Duplicate NodeGUIDs are rejected, but duplicate destination coordinates
 *    are not: overlapping node positions are legal in a UE graph.
 *  - It never saves, compiles, opens, focuses or loads an asset, and it never
 *    starts a child process.
 *
 * It is deliberately NOT a multi-asset checkpoint, a session lock, or a
 * background job: the caller still owns editor/graph/focus/Dirty/PIE checks.
 */
namespace UEAgentKitBlueprintEdit
{
	constexpr int32 MaxNodeMovesPerBatch = 128;
	constexpr int32 MaxNodeCoordinate = 1000000;
	/**
	 * Internal-only request limit for a session-wide restore.  A caller-supplied
	 * batch stays capped at MaxNodeMovesPerBatch (transport contract), but one
	 * rollback must be able to restore every node the session bound, otherwise a
	 * graph with more than 128 nodes could never roll back.  Every request is
	 * still fully validated; only the transport cap is lifted.
	 */
	constexpr int32 NodeMoveRequestLimitUnlimited = INDEX_NONE;

	struct FNodeMoveRequest
	{
		FGuid NodeGuid;
		int32 ExpectedX = 0;
		int32 ExpectedY = 0;
		int32 ToX = 0;
		int32 ToY = 0;
	};

	struct FNodeMoveResult
	{
		FGuid NodeGuid;
		int32 BeforeX = 0;
		int32 BeforeY = 0;
		int32 AfterX = 0;
		int32 AfterY = 0;
		bool bChanged = false;
	};

	/** True only for exactly 64 lowercase hexadecimal characters. */
	bool IsNodeCoordinateInRange(const int64 Value);

	/**
	 * Package Dirty precondition for one move batch.
	 *
	 * RequireClean is the only mode reachable from a request without an edit
	 * session token: it refuses an already-dirty package, so only the first
	 * clean -> dirty batch is possible.  AllowSessionOwnedDirty is reserved for
	 * FBlueprintEditSessionManager after it validated a live token, the bound
	 * target and the expected content fingerprint; no socket parameter selects
	 * it directly.
	 */
	enum class EPackageDirtyGate : uint8
	{
		RequireClean,
		AllowSessionOwnedDirty,
	};

	/**
	 * Called after every position was written and read back, still inside the
	 * one open transaction, before the batch is reported as successful.  A false
	 * return must leave OutErrorCode/OutErrorMessage set; the helper then
	 * restores every position, the Blueprint status/cache and the pre-write Dirty
	 * flag, cancels the transaction, and reports failure with no committed write.
	 */
	using FNodeMoveFinalizeFunction = TFunction<bool(
		bool bChanged,
		FString& OutErrorCode,
		FString& OutErrorMessage)>;

	/**
	 * Validate every request against the live graph and then apply all
	 * position changes inside one transaction.
	 *
	 * On success OutResults holds one entry per request in request order and
	 * bOutChanged reports whether the batch actually moved anything.  On
	 * failure no node position is left changed, the package Dirty flag is
	 * restored, and OutErrorCode is one of the stable node-move-batch-* codes
	 * (or the finalizer's own code).
	 *
	 * MaxRequestCount caps caller-supplied transport requests
	 * (MaxNodeMovesPerBatch); pass NodeMoveRequestLimitUnlimited only from the
	 * session restore path, where one atomic undo must cover the whole bound
	 * graph.
	 *
	 * FinalizeBeforeSuccess runs inside the open transaction after the read-back
	 * and Dirty confirmation.  This is what keeps a session's post-apply state
	 * update in the same rollback boundary as the write: a finalizer failure
	 * restores the graph, so the caller never sees "failed" after a visible
	 * edit.  bOutWriteCommitted is set true only when the transaction committed
	 * and all writes survived.
	 */
	bool ApplyNodeMoveBatch(
		UBlueprint* Blueprint,
		UEdGraph* Graph,
		const TArray<FNodeMoveRequest>& Requests,
		TArray<FNodeMoveResult>& OutResults,
		bool& bOutChanged,
		FString& OutErrorCode,
		FString& OutErrorMessage,
		EPackageDirtyGate DirtyGate = EPackageDirtyGate::RequireClean,
		const int32 MaxRequestCount = MaxNodeMovesPerBatch,
		const FNodeMoveFinalizeFunction& FinalizeBeforeSuccess = FNodeMoveFinalizeFunction(),
		bool* bOutWriteCommitted = nullptr);
}
