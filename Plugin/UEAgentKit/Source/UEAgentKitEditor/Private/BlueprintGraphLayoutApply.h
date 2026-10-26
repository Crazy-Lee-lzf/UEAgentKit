#pragma once

#include "CoreMinimal.h"

class FJsonObject;
class UBlueprint;
class UEdGraph;
class UEdGraphNode;

/**
 * Internal, fail-closed position-only Blueprint graph layout apply.
 *
 * The write path (ApplyToGraph/TryApply) remains deliberately NOT registered
 * on FUEAgentKitEditorBridge's socket dispatch and has no MCP/TS write surface.
 * P6 registers only the read-only BuildFocusedEditorPreflight adapter through
 * editor.getBlueprintGraphLayoutPreflight.
 *
 * The only mutation is UEdGraphNode::NodePosX / NodePosY inside one
 * FScopedTransaction.  It never adds/removes nodes, edits pins, compiles or
 * saves a package.
 */
namespace UEAgentKitBlueprintGraphLayout
{
	struct FNodeSnapshot
	{
		FGuid Guid;
		UEdGraphNode* Node = nullptr;
		int32 X = 0;
		int32 Y = 0;
		int32 Width = 0;
		int32 Height = 0;
		FString ClassPath;
		bool bComment = false;
		bool bReroute = false;
		int32 InputPinCount = 0;
		int32 OutputPinCount = 0;
		int32 InputLinkCount = 0;
		int32 OutputLinkCount = 0;
	};

	/** Read-only snapshot of the exact live graph used to build a LayoutPlan. */
	bool BuildPreflight(
		UBlueprint* Blueprint,
		UEdGraph* Graph,
		TSharedPtr<FJsonObject>& OutPreflight,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/**
	 * Build one bounded page of the layout-only projection of an exact graph.
	 *
	 * This is deliberately separate from the unbounded canonical context: the
	 * projection carries only the identity, node geometry, pin/link topology and
	 * comment bounds required by the layout planner.  It never carries reflected
	 * defaults, titles, pin blobs or unrelated Blueprint defaults.  Callers page
	 * by offset/limit; the response carries canonical node/pin/edge set hashes
	 * computed over the whole graph so a client can prove that the concatenated
	 * pages are complete and unchanged.
	 */
	bool BuildLayoutProjection(
		UBlueprint* Blueprint,
		UEdGraph* Graph,
		int32 Offset,
		int32 Limit,
		const FString& ExpectedPackageRevision,
		const FString& ExpectedGraphFingerprint,
		TSharedPtr<FJsonObject>& OutProjection,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/**
	 * Shape-only validation shared by the Editor apply and the offline test.
	 * It does not resolve a graph, so missing/unknown nodes remain a graph-level
	 * check performed by TryApply...().
	 */
	bool ValidatePlanShape(
		const TSharedPtr<FJsonObject>& Plan,
		TArray<FString>& OutErrorCodes);

	/**
	 * Recompute the canonical layout hash from a plan's positions array.
	 * Shared with the Editor automation contract so the test proves the same
	 * canonicalisation the apply gate uses instead of duplicating hashing code.
	 */
	bool ComputeLayoutHash(
		const TSharedPtr<FJsonObject>& Plan,
		FString& OutHash);

	/**
	 * Read-only preflight of the Blueprint Editor the user is actually looking
	 * at: the most recently activated ordinary Blueprint Editor and its focused
	 * graph.  This is the internal read surface behind P6's registered
	 * editor.getBlueprintGraphLayoutPreflight method; it never opens, focuses,
	 * loads, saves or mutates anything.
	 */
	bool BuildFocusedEditorPreflight(
		TSharedPtr<FJsonObject>& OutPreflight,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/** Resolve exact loaded Blueprint + exact graph by GUID; no mutation. */
	bool ResolveExactTarget(
		const FString& AssetPath,
		const FString& GraphGuidText,
		UBlueprint*& OutBlueprint,
		UEdGraph*& OutGraph,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/**
	 * Record the graph an explicit focus request selected for one exact
	 * Blueprint.  Read-only preflight/context normally use the toolkit's live
	 * Slate focus; when a background or automation Editor loses keyboard focus
	 * between RPCs this explicit record is the fail-closed fallback, and it is
	 * only accepted for the Blueprint that owns the graph.
	 */
	void SetExplicitFocusedGraph(UEdGraph* Graph);

	/**
	 * Apply a LayoutPlan request to an already-resolved graph.  The caller must
	 * still pass the exact request identity; this function re-checks revision and
	 * live graph fingerprint immediately before the transaction.
	 */
	bool ApplyToGraph(
		UBlueprint* Blueprint,
		UEdGraph* Graph,
		const TSharedPtr<FJsonObject>& Request,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/**
	 * Full internal handler: resolve asset + graph from the request, verify the
	 * exact Blueprint editor is showing that graph and is editable, then apply.
	 */
	bool TryApply(
		const TSharedPtr<FJsonObject>& Request,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage);
}
