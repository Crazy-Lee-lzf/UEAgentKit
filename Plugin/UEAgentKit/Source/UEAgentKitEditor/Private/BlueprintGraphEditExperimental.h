#pragma once

#include "CoreMinimal.h"

class FJsonObject;
class UBlueprint;
class UEdGraph;

/**
 * Explicitly opted-in, bounded Blueprint graph structure edit.
 *
 * This is a create/connect slice with deterministic node/schema contracts.  It
 * is not enabled by default, is not a general GraphPatch executor, and has no
 * Python, console or arbitrary command fallback.  The exact Editor project must
 * opt in with:
 *
 *     [UEAgentKit]
 *     ExperimentalGraphPatchEdit=True
 *
 * Supported nodes are Branch, Sequence, Boolean NOT/OR, integer add/equal,
 * double add, string concat, and self-context member-variable Get/Set accessors
 * for existing plain bool/int/double/string variables.  Supported connections
 * are exact exec or exact bool/int/double/string data links; one output may fan
 * out to distinct inputs.  One endpoint may be an exact existing K2 pin
 * (nodeGuid + pinGuid + name + direction + category); an existing input must be
 * unlinked and the schema must return MAKE.  Every call still requires the exact
 * focused Blueprint graph, clean package, stopped PIE/SIE, current revision and
 * graph fingerprint, one FScopedTransaction, exact readback, and Undo/Redo
 * verification.  Save is a separate explicit gate bound to the Apply plan,
 * post-Apply fingerprint and exact node/link counts.
 *
 * 0-3.4 adds an atomic seam for the eight primitive nodeTypes (see
 * FAtomicNodeOperation): the cross-writer patch creates nodes through the same
 * functions the single writer uses, inside the caller's transaction, and the
 * writer exposes its own node-structure fingerprint.  The seam never creates an
 * edge, an owned literal or a variable accessor.
 */
namespace UEAgentKitBlueprintGraphEditExperimental
{
	const TCHAR* const PreviewMethod = TEXT("editor.previewBlueprintGraphEditExperimental");
	const TCHAR* const ApplyMethod = TEXT("editor.applyBlueprintGraphEditExperimental");
	const TCHAR* const SaveMethod = TEXT("editor.saveBlueprintGraphEditExperimental");
	const TCHAR* const ApplyConfirmationPrefix = TEXT("CREATE EXPERIMENTAL GRAPH NODES ");
	const TCHAR* const SaveConfirmationPrefix = TEXT("SAVE EXPERIMENTAL GRAPH NODES ");

	/** True only when the current project set the experimental opt-in flag. */
	bool IsExperimentalWriteAvailable();

	/** Read-only plan for a bounded createNode/connectPins request. */
	bool TryPreviewExperimental(
		const TSharedPtr<FJsonObject>& Params,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/** Apply the exact previewed plan to the exact focused graph. */
	bool TryApplyExperimental(
		const TSharedPtr<FJsonObject>& Params,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/** Explicit Save of the exact package produced by the preceding Apply. */
	bool TrySaveExperimental(
		const TSharedPtr<FJsonObject>& Params,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/**
	 * One primitive-node creation operation resolved against the live graph.
	 *
	 * 0-3.4 seam: the cross-writer atomic patch (BlueprintPatchExperimental)
	 * creates nodes through these functions instead of keeping a second copy of
	 * the node-type whitelist, the coordinate bound, the spawner/CallFunction
	 * path or the pin-contract read-back.  The single writer's own Apply creates
	 * the same eight primitive nodeTypes through the very same creation
	 * function, so the two paths cannot drift.
	 *
	 * Deliberately NOT part of this seam: `call_function`,
	 * `call_owned_pure_function`, `variable_get`/`variable_set` and the owned
	 * input literals.  They need connection-pin resolution (an existing pin, a
	 * function allowlist entry, a member-variable binding) and belong to the
	 * connect slice; `createNode` here is "create one node, touch no edge".
	 */
	struct FAtomicNodeOperation
	{
		/** One of the eight primitive nodeTypes; canonicalized by resolve. */
		FString NodeType;
		/** Resolved class path (e.g. /Script/BlueprintGraph.K2Node_IfThenElse). */
		FString NodeClassPath;
		/** Resolved reflected function for the CallFunction nodeTypes, else empty. */
		FString FunctionName;
		/** Resolved engine pin contract; filled by resolve, read-only afterwards. */
		TArray<FString> InputPins;
		TArray<FString> OutputPins;
		TMap<FString, FString> InputPinTypes;
		TMap<FString, FString> OutputPinTypes;
		/** Node position; the writer's own bound applies. */
		int32 X = 0;
		int32 Y = 0;
		/** Assigned inside the transaction; empty in a plan the caller previewed. */
		FString NodeGuid;
	};

	/**
	 * Maximum nodes one Apply may create.
	 *
	 * The atomic patch enforces the same number for `createNode` operations so one
	 * patch can never create more nodes than the single-writer tool.
	 */
	constexpr int32 MaxNodesPerApply = 16;

	/** The single writer's coordinate bound, shared with the atomic patch. */
	constexpr int32 MaxNodeCoordinate = 100000;

	/**
	 * Validate one operation against the live graph.  Read-only.
	 *
	 * Applies the same nodeType whitelist, coordinate bound and K2-schema
	 * precondition as Preview, resolves the class path and function name, and
	 * records them in the operation.  No mutation, no transaction.
	 */
	bool TryResolveAtomicNodeOperation(
		UEdGraph* Graph,
		FAtomicNodeOperation& InOutOperation,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/**
	 * Create resolved nodes inside the CALLER's transaction.
	 *
	 * Uses exactly the creation function the single-writer Apply uses, verifies
	 * each created node's class, position and pin contract, and marks the package
	 * through the caller's transaction only.  No transaction is opened here.  If
	 * any operation fails, every node this call already created is removed again
	 * and OutCreatedGuids is emptied, so an empty result always means "this call
	 * wrote nothing".
	 */
	bool TryApplyAtomicNodeOperations(
		UBlueprint* Blueprint,
		UEdGraph* Graph,
		const TArray<FAtomicNodeOperation>& Operations,
		TArray<FGuid>& OutCreatedGuids,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/**
	 * Read every operation's node back; bExpectAfter selects "exists with the
	 * resolved class/position/pin contract" or "does not exist any more".
	 */
	bool TryCheckAtomicNodeOperations(
		UEdGraph* Graph,
		const TArray<FAtomicNodeOperation>& Operations,
		bool bExpectAfter,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/**
	 * Remove the first AppliedCount operations' nodes, in reverse order.
	 *
	 * Used by the atomic patch rollback.  A node that is already gone counts as
	 * restored.  Returns false when a node survives its own removal, which means
	 * the caller cannot claim a clean restore.
	 */
	bool TryRestoreAtomicNodeOperations(
		UBlueprint* Blueprint,
		UEdGraph* Graph,
		const TArray<FAtomicNodeOperation>& Operations,
		int32 AppliedCount,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/**
	 * Graph-level node structure fingerprint (node identity, class, position and
	 * pin inventory, in GUID order).
	 *
	 * This is the graph-edit writer's own state hash, reported by the atomic patch
	 * as `nodesFingerprint`.  It is deliberately narrower than
	 * `UEAgentKitBlueprintPinDefaultExperimental::ComputeAtomicStateFingerprint`
	 * (which hashes pin defaults and link counts but neither the node class nor
	 * the node position) and wider in exactly those two respects, so the two
	 * together cover structure and defaults.  Pin defaults are NOT hashed here.
	 */
	FString ComputeAtomicNodeStateFingerprint(UEdGraph* Graph);

	/**
	 * One edge creation operation resolved against the live graph.
	 *
	 * 0-3.4 slice 11 seam: the cross-writer atomic patch connects pins through
	 * these functions.  An endpoint is either one existing node GUID or one
	 * tempId that an earlier `createNode` operation of the same patch declared;
	 * the patch resolves tempIds to GUIDs before Apply, so this seam only ever
	 * writes edges between real nodes inside the caller's transaction.
	 *
	 * The single writer's own connect path is not moved onto this seam: it also
	 * carries owned literals, `fromExisting`/`toExisting` pin identities and
	 * disconnect plans, which are outside this slice.  What is shared is the
	 * judgement of what a legal edge is (`PinMatchesType`,
	 * `ExistingPinTypeToken`, `ValidateSchemaConnection`) and the one
	 * `TryCreateConnection` call.
	 */
	struct FAtomicEdgeOperation
	{
		/** Source endpoint: existing node GUID, or a tempId while still unresolved. */
		FString FromNodeGuid;
		FString FromTempId;
		FString FromPin;
		/** Target endpoint: existing node GUID, or a tempId while still unresolved. */
		FString ToNodeGuid;
		FString ToTempId;
		FString ToPin;
		/** Resolved by resolve (existing endpoints) or apply (tempId endpoints). */
		FString FromPinId;
		FString ToPinId;
		FString FromType;
		FString ToType;
	};

	/**
	 * Lookup for a tempId endpoint's declared node contract.
	 *
	 * Returns the resolved createNode operation that declared the tempId, or null
	 * when the patch did not declare it.  Read-only: it is used at plan time, when
	 * the node does not exist yet.
	 */
	using FTempNodeResolver =
		TFunctionRef<const FAtomicNodeOperation* (const FString& TempId)>;

	/** The single writer's own connection bounds, shared with the atomic patch. */
	constexpr int32 MaxConnectionsPerApply = 24;
	constexpr int32 MaxConnectionsPerNodePerApply = 8;

	/**
	 * Validate one edge against the live graph.  Read-only.
	 *
	 * Resolves both endpoints (live node + exact pin), checks direction, the
	 * supported exec/bool/int/double/string token, exact token equality, the
	 * self-connection rule, an already-linked input, and the K2 schema response.
	 * For a tempId endpoint the pin contract comes from the declaring createNode
	 * operation, because the node does not exist yet.
	 */
	bool TryResolveAtomicEdgeOperation(
		UEdGraph* Graph,
		FAtomicEdgeOperation& InOutOperation,
		FTempNodeResolver ResolveTempNode,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/**
	 * Create resolved edges inside the CALLER's transaction.
	 *
	 * Endpoints must already carry resolved node GUIDs (the patch translates
	 * tempIds first).  Fills the resolved pin GUIDs and type tokens back into the
	 * operations, verifies each new link, and on any failure removes every edge
	 * this call already created before returning false.  No transaction is opened
	 * here and the package Dirty bit is not touched here.
	 */
	bool TryApplyAtomicEdgeOperations(
		UBlueprint* Blueprint,
		UEdGraph* Graph,
		TArray<FAtomicEdgeOperation>& InOutOperations,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/**
	 * Read every operation's edge back; bExpectAfter selects "exactly this edge
	 * exists and it is the only link on the target input" or "the edge is gone".
	 * A missing node/pin counts as "gone" so an Undo that also removed the nodes
	 * a patch created still verifies.
	 */
	bool TryCheckAtomicEdgeOperations(
		UEdGraph* Graph,
		const TArray<FAtomicEdgeOperation>& Operations,
		bool bExpectAfter,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/**
	 * Remove the first AppliedCount operations' edges, in reverse order.
	 *
	 * Used by the atomic patch rollback.  An edge that is already gone counts as
	 * restored.  Returns false when an edge survives its own removal.
	 */
	bool TryRestoreAtomicEdgeOperations(
		UBlueprint* Blueprint,
		UEdGraph* Graph,
		const TArray<FAtomicEdgeOperation>& Operations,
		int32 AppliedCount,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/**
	 * Graph-level edge-set fingerprint: sha256 over the sorted
	 * `fromNodeGuid|fromPinName|fromPinId>toNodeGuid|toPinName|toPinId` list.
	 *
	 * This is the rewire writer's own live edge-set hash (the same function its
	 * Preview/Apply and Save already use), reported by the atomic patch as
	 * `edgesFingerprint`.  It is the only one of the three hashes that identifies
	 * edge ENDPOINTS: the pin-default state hash records only per-pin link
	 * COUNTS, and the node hash records no links at all.
	 */
	FString ComputeAtomicEdgeStateFingerprint(UEdGraph* Graph);

	/** Automation-only seam; never used by the bridge or MCP. */
	void SetExperimentalWriteEnabledForTest(bool bEnabled);
	void ResetExperimentalWriteForTest();
}
