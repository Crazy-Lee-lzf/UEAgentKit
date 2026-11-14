#pragma once

#include "CoreMinimal.h"

class FJsonObject;
class UEdGraph;

/**
 * Atomic seam for editing existing Blueprint input-pin defaults.
 *
 * Only existing, unlinked, editable input pins of the exact focused K2 graph,
 * and only the strict bool/int/double/string encodings are accepted.  It never
 * creates nodes, changes links, creates variables, deletes, disconnects,
 * compiles or saves.
 *
 * Slice 16 removed the standalone preview/apply/save pipeline (and its
 * one-click MCP tool) once the cross-writer atomic patch covered this kind; the
 * module is now the seam the patch calls plus the state fingerprint.
 */
namespace UEAgentKitBlueprintPinDefaultExperimental
{
	/**
	 * One pin-default operation resolved against the live graph.
	 *
	 * 0-3.4 seam: the cross-writer atomic patch (BlueprintPatchExperimental)
	 * applies pin defaults through these functions.  Slice 16 removed the
	 * single-writer preview/apply/save pipeline and its one-click MCP tool, so
	 * this module now exists only as that seam plus its state fingerprint.
	 */
	struct FAtomicOperation
	{
		FString NodeGuid;
		FString PinName;
		FString PinGuid;
		/** bool / int / double / string; canonicalized by TryResolveAtomicOperation. */
		FString Type;
		/** Requested value; canonicalized by TryResolveAtomicOperation. */
		FString Value;
		/** Filled by TryResolveAtomicOperation from the live pin. Never requested. */
		FString BeforeValue;
	};

	/**
	 * Validate one operation against the live graph.  Read-only.
	 *
	 * Re-canonicalizes Type/Value (same rules as Preview), re-proves the exact
	 * node/pin identity, and records the live BeforeValue.  No mutation, no
	 * transaction.
	 */
	bool TryResolveAtomicOperation(
		UEdGraph* Graph,
		FAtomicOperation& InOutOperation,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/**
	 * Write resolved operations inside the CALLER's transaction.
	 *
	 * Marks the exact nodes RF_Transactional and calls Modify() so the change is
	 * a real undoable transaction record.  No transaction is opened here, and the
	 * package Dirty bit is not touched.  If a later operation fails, every pin
	 * already written by this call is restored to its BeforeValue before the
	 * failure is returned, and OutAppliedCount reports how many were applied
	 * (0 when this call restored everything itself).
	 */
	bool TryApplyAtomicOperations(
		UEdGraph* Graph,
		const TArray<FAtomicOperation>& Operations,
		int32& OutAppliedCount,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/** Read every operation's pin back; bExpectAfter selects Value or BeforeValue. */
	bool TryCheckAtomicOperations(
		UEdGraph* Graph,
		const TArray<FAtomicOperation>& Operations,
		bool bExpectAfter,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/**
	 * Restore the first AppliedCount operations to their BeforeValue, in reverse
	 * order.  Used by the atomic patch rollback.  Returns false (with the exact
	 * pin) when a pin can no longer be resolved, which means the caller cannot
	 * claim the graph was restored.
	 */
	bool TryRestoreAtomicOperations(
		UEdGraph* Graph,
		const TArray<FAtomicOperation>& Operations,
		int32 AppliedCount,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/**
	 * Whole-graph state fingerprint: pin identity, type, defaults and link counts.
	 *
	 * The atomic patch reports it as `stateFingerprint` and uses it as the
	 * before/after proof of a patch, so it is part of the public seam.
	 */
	FString ComputeAtomicStateFingerprint(UEdGraph* Graph);
}
