"""Generate the normative error-code and condition-code registries.

Run from the contract_candidate directory:
    py -3.12 tools/build_registries.py

The JSON files are deliverables; this script is kept so the tables can be
reviewed and regenerated deterministically.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "schemas"
SCHEMAS.mkdir(parents=True, exist_ok=True)

# code, domain, severity, retryable, owner, summary, contractRef, legacyAliasOf
CODES = [
    # ---- contract / schema subset -------------------------------------------
    ("contract-schema-type-mismatch", "contract", "error", False, "INT", "JSON value has the wrong type", "EC-SCHEMA-SUBSET", ""),
    ("contract-schema-required", "contract", "error", False, "INT", "A required property is missing", "EC-SCHEMA-SUBSET", ""),
    ("contract-schema-enum-mismatch", "contract", "error", False, "INT", "Value is outside the registered enum", "EC-SCHEMA-SUBSET", ""),
    ("contract-schema-pattern", "contract", "error", False, "INT", "String does not match the required pattern", "EC-SCHEMA-SUBSET", ""),
    ("contract-schema-min-length", "contract", "error", False, "INT", "String is shorter than minLength", "EC-SCHEMA-SUBSET", ""),
    ("contract-schema-max-length", "contract", "error", False, "INT", "String is longer than maxLength", "EC-SCHEMA-SUBSET", ""),
    ("contract-schema-minimum", "contract", "error", False, "INT", "Number is below minimum", "EC-SCHEMA-SUBSET", ""),
    ("contract-schema-maximum", "contract", "error", False, "INT", "Number is above maximum", "EC-SCHEMA-SUBSET", ""),
    ("contract-schema-additional-property", "contract", "error", False, "INT", "Unknown property is not allowed at this schema version", "EC-SCHEMA-SUBSET", ""),
    ("contract-schema-oneof-mismatch", "contract", "error", False, "INT", "Value does not match exactly one oneOf branch", "EC-SCHEMA-SUBSET", ""),
    ("contract-schema-const-mismatch", "contract", "error", False, "INT", "Value does not equal const", "EC-SCHEMA-SUBSET", ""),
    ("contract-schema-invalid", "contract", "error", False, "INT", "Schema or document shape is invalid", "EC-SCHEMA-SUBSET", ""),
    ("contract-schema-keyword-unsupported", "contract", "error", False, "INT", "Schema uses a keyword outside the frozen stdlib subset", "EC-SCHEMA-SUBSET", ""),
    ("contract-schema-minor-newer", "contract", "warning", False, "INT", "Document minor is newer; unknown optional fields were ignored", "EC-VERSION", ""),
    ("contract-error-code-unknown", "contract", "error", False, "INT", "Diagnostic/error code is not in the registry", "EC-TABLE", ""),
    ("contract-condition-code-unknown", "contract", "error", False, "INT", "Pre/postcondition code is not in the registry", "OD-CODES", ""),
    # ---- TypedGraph read model ---------------------------------------------
    ("graph-schema-version-unsupported", "graph", "error", False, "INT", "Unsupported TypedGraph major version", "EC-VERSION", ""),
    ("graph-invalid-schema-version", "graph", "error", False, "INT", "TypedGraph schemaVersion is missing or malformed", "EC-VERSION", ""),
    ("graph-invalid-id-format", "graph", "error", False, "INT", "Graph/node/pin/asset identity is not canonical", "ID-FMT", ""),
    ("graph-invalid-revision", "graph", "error", False, "INT", "asset.revision/revisionPolicy is invalid", "TG-REVISION-POLICY", ""),
    ("graph-invalid-kind", "graph", "error", False, "INT", "Graph or node kind is unknown", "ID-NODE-KIND", ""),
    ("graph-invalid-source", "graph", "error", False, "INT", "TypedGraph source/exporter metadata is invalid", "TG-EXPORTER-VERSION", ""),
    ("graph-id-guid-mismatch", "graph", "error", False, "INT", "graphId does not match graph.guid", "ID-GRAPH", ""),
    ("graph-node-guid-mismatch", "graph", "error", False, "INT", "nodeId does not match nodeGuid", "ID-NODE", ""),
    ("graph-node-kind-mismatch", "graph", "error", False, "INT", "node.kind disagrees with the classPath determination table", "ID-NODE-KIND", ""),
    ("graph-invalid-coordinate", "graph", "error", False, "INT", "Coordinates/sizes are not finite integers", "TG-COORDINATES", ""),
    ("graph-invalid-size-source", "graph", "error", False, "INT", "size.source is not measured/stored/estimated/unknown", "TG-SIZE-SOURCE", ""),
    ("graph-invalid-pin-index", "graph", "error", False, "INT", "pin.index is not a non-negative export-time array index", "TG-PIN-INDEX", ""),
    ("graph-invalid-pin-source", "graph", "error", False, "INT", "pinIdSource/pinGuidTransient is invalid", "TG-DERIVED-PIN", ""),
    ("graph-invalid-pin-direction", "graph", "error", False, "INT", "Pin direction is invalid or an edge is not output->input", "ID-PIN-DIRECTION", ""),
    ("graph-invalid-edge-kind", "graph", "error", False, "INT", "Edge/pin kind is invalid or inconsistent", "TG-EDGE-STATUS", ""),
    ("graph-invalid-edge-id", "graph", "error", False, "INT", "edgeId is not the pure function of its endpoints", "ID-EDGE", ""),
    ("graph-invalid-edge-status", "graph", "error", False, "INT", "edge.status is not resolved/dangling/duplicate/wrong-kind", "TG-EDGE-STATUS", ""),
    ("graph-duplicate-node-id", "graph", "error", False, "INT", "Duplicate nodeId inside one graph", "ID-NODE", ""),
    ("graph-duplicate-pin-id", "graph", "error", False, "INT", "Duplicate native PinId across nodes", "ID-PIN-BUILDER", ""),
    ("graph-duplicate-edge", "graph", "error", False, "INT", "The same pin pair has more than one edge", "ID-EDGE", ""),
    ("graph-dangling-edge", "graph", "error", False, "INT", "Edge/link endpoint does not exist", "TG-EDGE-STATUS", ""),
    ("graph-pin-node-mismatch", "graph", "error", False, "INT", "Pin owner or node.pins declaration is inconsistent", "ID-PIN", ""),
    ("graph-links-mirror-mismatch", "graph", "error", False, "INT", "pin.links does not mirror the edge set", "TG-LINK-MIRROR", ""),
    ("graph-comment-has-edges", "graph", "error", False, "INT", "Comment node appears in the edge set", "TG-COMMENT", ""),
    ("graph-semantic-hash-mismatch", "graph", "error", False, "INT", "semanticHash does not match the whitelisted projection", "TG-HASH-WHITELIST", ""),
    ("graph-layout-hash-mismatch", "graph", "error", False, "INT", "layoutHash does not match nodeId+x+y", "LP-HASH", ""),
    ("graph-node-hash-mismatch", "graph", "error", False, "INT", "node.semanticHash does not match its node projection", "GP-NODE-HASH", ""),
    ("graph-truncated", "graph", "error", False, "INT", "Graph was truncated and must not be applied", "TG-TRUNCATION", ""),
    ("graph-status-not-resolved", "graph", "error", False, "INT", "Apply requires every edge.status=resolved", "TG-EDGE-STATUS", ""),
    ("graph-diagnostics-error", "graph", "error", False, "INT", "Apply requires diagnostics to be empty or all info", "TG-DIAGNOSTICS", ""),
    ("graph-node-identity-unstable", "graph", "error", False, "A", "Apply requires native node/graph identity", "ID-NODE", ""),
    ("graph-pin-guid-duplicate", "graph", "error", False, "A", "Duplicate PinId was disambiguated; re-export before Apply", "ID-PIN-BUILDER", ""),
    ("graph-pin-guid-derived", "graph", "info", False, "A", "PinId is derived from MD5(stable key), not a native PinId", "TG-DERIVED-PIN", ""),
    ("graph-node-not-found", "graph", "error", False, "B", "Node does not exist in the target graph", "ID-NODE", ""),
    ("graph-pin-not-found", "graph", "error", False, "B", "Pin does not exist on the target node", "ID-PIN", ""),
    # ---- GraphPatch / transaction ------------------------------------------
    ("graphpatch-schema-version-unsupported", "graphpatch", "error", False, "B", "Unsupported GraphPatch major version", "EC-VERSION", ""),
    ("graphpatch-schema-version", "graphpatch", "error", False, "B", "Legacy code for unsupported GraphPatch version", "EC-VERSION", "graphpatch-schema-version-unsupported"),
    ("graphpatch-graph-kind-not-allowed", "graphpatch", "error", False, "B", "V1 Apply does not accept this graph kind (ubergraph is read-only)", "GP-TARGET", ""),
    ("graphpatch-profile-invalid", "graphpatch", "error", False, "B", "graphpatchProfile is missing or unknown", "GP-LIMITS", ""),
    ("graphpatch-confirmation-required", "graphpatch", "error", False, "B", "R2 operation requires explicit confirmed=true", "GP-ORDER", ""),
    ("graphpatch-policy-denied", "graphpatch", "error", False, "B", "Policy does not authorize this operation/graph/node", "TX-POLICY", ""),
    ("graphpatch-limit-exceeded", "graphpatch", "error", False, "B", "Operation/node/position limit exceeded", "GP-LIMITS", ""),
    ("graphpatch-patch-empty", "graphpatch", "error", False, "B", "A GraphPatch must contain at least one operation", "GP-OPERATIONS", ""),
    ("graphpatch-target-asset-mismatch", "graphpatch", "error", False, "B", "Target asset path or sidecar set does not match", "GP-TARGET", ""),
    ("graphpatch-target-graph-mismatch", "graphpatch", "error", False, "B", "Graph id/guid/name/kind does not match", "GP-TARGET", ""),
    ("graphpatch-base-revision-stale", "graphpatch", "error", False, "B", "Disk revision changed", "TG-REVISION-POLICY", ""),
    ("graphpatch-base-graph-hash-stale", "graphpatch", "error", False, "B", "In-memory semanticHash changed", "TG-HASH-WHITELIST", ""),
    ("graphpatch-package-dirty", "graphpatch", "error", False, "B", "Target package is dirty outside a continuing transaction", "TX-REVISION", ""),
    ("graphpatch-pie-active", "graphpatch", "error", False, "B", "PIE/SIE is active", "TX-PREFLIGHT", ""),
    ("graphpatch-editor-session-mismatch", "graphpatch", "error", False, "B", "Editor session changed since Preview", "TX-SESSION", ""),
    ("graphpatch-editor-unavailable", "graphpatch", "error", True, "B", "No resident Editor is available", "TX-PREFLIGHT", ""),
    ("graphpatch-graph-schema-unsupported", "graphpatch", "error", False, "B", "Graph schema is not in the V1 allowlist", "GP-SCHEMA", ""),
    ("graphpatch-descriptor-not-found", "graphpatch", "error", False, "B", "ActionId/NodeDescriptor cannot be resolved", "ND-ACTION-ID", ""),
    ("graphpatch-context-mismatch", "graphpatch", "error", False, "B", "Action is not creatable in this Blueprint/Graph context", "ND-CONTEXT", ""),
    ("graphpatch-node-spawn-failed", "graphpatch", "error", False, "B", "Node spawn failed or failed post-configuration", "GP-APPLY", ""),
    ("graphpatch-node-guid-invalid", "graphpatch", "error", False, "B", "Returned NodeGuid is invalid", "GP-APPLY", ""),
    ("graphpatch-node-guid-duplicate", "graphpatch", "error", False, "B", "Returned NodeGuid collides in the graph", "GP-APPLY", ""),
    ("graphpatch-node-not-found", "graphpatch", "error", False, "B", "Target node does not exist", "GP-OPERATIONS", ""),
    ("graphpatch-pin-not-found", "graphpatch", "error", False, "B", "Pin reference cannot be resolved", "GP-DIRECTION", ""),
    ("graphpatch-pin-ambiguous", "graphpatch", "error", False, "B", "Same-name pin has multiple candidates; pinIndex required", "TG-PIN-INDEX", ""),
    ("graphpatch-pin-direction-mismatch", "graphpatch", "error", False, "B", "Pin direction conflicts with the operation role", "GP-DIRECTION", ""),
    ("graphpatch-pin-type-incompatible", "graphpatch", "error", False, "B", "Pin kinds/types are not compatible", "GP-DIRECTION", ""),
    ("graphpatch-pin-identity-unstable", "graphpatch", "error", False, "B", "Derived PinId used as a structural endpoint", "TG-DERIVED-PIN", ""),
    ("graphpatch-pin-connected", "graphpatch", "error", False, "B", "setPinDefault requires an unconnected input", "GP-ORDER", ""),
    ("graphpatch-pin-default-not-allowed", "graphpatch", "error", False, "B", "Pin flags forbid a default value", "GP-ORDER", ""),
    ("graphpatch-connection-rejected", "graphpatch", "error", False, "B", "Graph Schema rejected the connection or read-back differs", "GP-APPLY", ""),
    ("graphpatch-connection-replaced", "graphpatch", "error", False, "B", "A connection would replace an existing link", "GP-REPLACEMENT", ""),
    ("graphpatch-operation-unknown", "graphpatch", "error", False, "B", "Operation type is not in the V1 table", "GP-OPERATIONS", ""),
    ("graphpatch-operation-duplicate-id", "graphpatch", "error", False, "B", "operationId is duplicated or invalid", "GP-OPERATIONS", ""),
    ("graphpatch-operation-duplicate-target", "graphpatch", "error", False, "B", "The same target/pin is written twice", "GP-ORDER", ""),
    ("graphpatch-operation-order-conflict", "graphpatch", "error", False, "B", "setPinDefault is ordered after a connection using that pin", "GP-ORDER", ""),
    ("graphpatch-operation-not-allowed", "graphpatch", "error", False, "B", "Operation is not allowed by V1 semantics", "GP-OPERATIONS", ""),
    ("graphpatch-temp-id-invalid", "graphpatch", "error", False, "B", "tempId is malformed, duplicated, or missing", "GP-TEMP-ID", ""),
    ("graphpatch-temp-id-unknown", "graphpatch", "error", False, "B", "Reference points at an undefined tempId", "GP-TEMP-ID", ""),
    ("graphpatch-dependency-unknown", "graphpatch", "error", False, "B", "dependsOn references an unknown operation", "GP-DEPENDENCY", ""),
    ("graphpatch-dependency-cycle", "graphpatch", "error", False, "B", "Operation dependency graph contains a cycle", "GP-DEPENDENCY", ""),
    ("graphpatch-node-hash-stale", "graphpatch", "error", False, "B", "expectedNodeHash/expectedOldValue is stale", "GP-NODE-HASH", ""),
    ("graphpatch-delete-connected", "graphpatch", "error", False, "B", "V1 deleteNode only allows unconnected nodes", "GP-DELETE", ""),
    ("graphpatch-self-loop", "graphpatch", "error", False, "B", "Self-loop requires allowSelfLoop=true", "GP-SELF-LOOP", ""),
    ("graphpatch-expectation-mismatch", "graphpatch", "error", False, "B", "expect does not match the applied structure", "GP-EXPECT", ""),
    ("graphpatch-idempotency-conflict", "graphpatch", "error", False, "B", "Same idempotencyKey with a different payloadHash", "GP-IDEMPOTENCY", ""),
    ("graphpatch-idempotency-indeterminate", "graphpatch", "error", False, "B", "Reserved/indeterminate prior Apply; fail-closed pending human reconciliation", "GP-IDEMPOTENCY", ""),
    ("graphpatch-idempotency-locked", "graphpatch", "error", True, "B", "Scope lock is held by another process", "GP-IDEMPOTENCY", ""),
    ("graphpatch-idempotency-state-invalid", "graphpatch", "error", False, "B", "Stored idempotency block is invalid or has no replay result", "GP-IDEMPOTENCY", ""),
    ("graphpatch-payload-hash-mismatch", "graphpatch", "error", False, "B", "Payload hash differs from the stored idempotency record", "GP-IDEMPOTENCY", ""),
    ("graphpatch-compile-failed", "graphpatch", "error", False, "B", "Single post-transaction Compile failed (Error/succeeded=false)", "TX-COMPILE", ""),
    ("graphpatch-compile-warning", "graphpatch", "warning", False, "B", "Compiler warning recorded; does not trigger rollback", "TX-COMPILE", ""),
    ("graphpatch-compile-result-missing", "graphpatch", "error", False, "B", "Trusted compileResult is missing or malformed; fail-closed", "TX-COMPILE", ""),
    ("graphpatch-save-failed", "graphpatch", "error", True, "B", "Explicit Save failed", "TX-SAVE", ""),
    ("graphpatch-verify-mismatch", "graphpatch", "error", False, "B", "Independent reload semantic comparison failed", "TX-VERIFY", ""),
    ("graphpatch-rollback-incomplete", "graphpatch", "error", False, "B", "Inverse operations could not prove restoration", "TX-ROLLBACK", ""),
    ("graphpatch-manual-recovery-required", "graphpatch", "error", False, "B", "Human Undo/backup recovery is required", "TX-RECOVERY-MARKER", ""),
    ("graphpatch-recovery-required", "graphpatch", "error", False, "B", "A pending Recovery Marker blocks Apply/Save", "TX-RECOVERY-MARKER", ""),
    ("graphpatch-recovery-clear-denied", "graphpatch", "error", False, "B", "Recovery Marker clear conditions were not met", "TX-RECOVERY-MARKER", ""),
    ("graphpatch-asset-not-loaded", "graphpatch", "error", False, "B", "Target asset is not loaded", "TX-PREFLIGHT", ""),
    ("graphpatch-graph-not-found", "graphpatch", "error", False, "B", "Target graph does not exist", "GP-TARGET", ""),
    ("graphpatch-min-executor-version", "graphpatch", "error", False, "B", "Executor is older than the Patch minimum version", "EC-VERSION", ""),
    ("graphpatch-target-not-loaded", "graphpatch", "error", False, "B", "Legacy alias for asset-not-loaded", "TX-PREFLIGHT", "graphpatch-asset-not-loaded"),
    # ---- Layout ------------------------------------------------------------
    ("layout-invalid-graph", "layout", "error", False, "C", "Layout input is not a valid TypedGraph", "LP-VALIDATION", ""),
    ("layout-shared-pure-node", "layout", "warning", False, "C", "Shared pure node assigned to one island; never duplicated", "LP-PURE-NODES", ""),
    ("layout-cross-island-data-edge", "layout", "warning", False, "C", "Data edge crosses islands without merging Exec islands", "LP-PURE-NODES", ""),
    ("layout-comment-partial-scope", "layout", "warning", False, "C", "Comment partially overlaps scope and was not moved", "LP-COMMENT", ""),
    ("layout-reroute-broken", "layout", "error", False, "C", "Reroute does not have one in/one out link", "LP-REROUTE", ""),
    ("layout-size-mismatch", "layout", "warning", False, "C", "Measured size differs from estimate after one calibration", "LP-SIZE", ""),
    ("layout-no-space", "layout", "error", False, "C", "No space for scope without overlapping fixed nodes", "LP-SCOPE", ""),
    ("layout-overlap-unresolved", "layout", "error", False, "C", "Node rectangles overlap after layout", "LP-OVERLAP", ""),
    ("overlap-unresolved", "layout", "error", False, "C", "Legacy alias for layout-overlap-unresolved", "LP-OVERLAP", "layout-overlap-unresolved"),
    ("layout-scope-violation", "layout", "error", False, "C", "Scope contains an unknown node or leaves fixed nodes moved", "LP-SCOPE", ""),
    ("layout-nondeterministic", "layout", "error", False, "C", "Two runs with identical input produced different output", "LP-SEED", ""),
    ("layout-semantic-changed", "layout", "error", False, "C", "Layout verification detected a semantic change", "LP-INVARIANTS", ""),
    # ---- Recovery (reserved domain) ---------------------------------------
    ("recovery-marker-corrupt", "recovery", "error", False, "B", "Recovery Marker JSON cannot be parsed or lacks required fields", "TX-RECOVERY-MARKER", ""),
    ("recovery-marker-version-unsupported", "recovery", "error", False, "B", "Unsupported Recovery Marker schema version", "TX-RECOVERY-MARKER", ""),
]

CONDITIONS = [
    ("asset-loaded", "precondition", "Target asset is loaded"),
    ("asset-open", "precondition", "Target asset is open in the resident Editor"),
    ("package-clean", "precondition", "Target package is clean"),
    ("pie-stopped", "precondition", "PIE/SIE is stopped"),
    ("base-revision-match", "precondition", "Disk revision equals baseRevision"),
    ("base-graph-hash-match", "precondition", "semanticHash equals baseGraphHash"),
    ("graph-schema-supported", "precondition", "Graph schema is in the V1 allowlist"),
    ("editor-session-match", "precondition", "Editor session matches Preview"),
    ("recovery-marker-absent", "precondition", "No pending Recovery Marker exists"),
    ("policy-allows-operation", "precondition", "Policy authorizes the operation"),
    ("descriptor-resolved", "precondition", "ActionId resolves to a NodeDescriptor"),
    ("pins-resolved", "precondition", "All Pin references resolve unambiguously"),
    ("node-guid-valid", "postcondition", "Spawned node has a valid unique NodeGuid"),
    ("pin-map-complete", "postcondition", "Every created node/pin is present in the result maps"),
    ("graph-dirty", "postcondition", "Graph is marked dirty but not saved"),
    ("transaction-recorded", "postcondition", "A transaction id and operation receipts exist"),
    ("edge-count-delta-match", "postcondition", "Edge count delta matches the applied operations"),
    ("semantic-hash-changed", "postcondition", "semanticHash changed exactly as expected"),
    ("no-dangling-edge", "postcondition", "No dangling edge remains after Apply"),
]


def main() -> None:
    document = {
        "schemaVersion": "ueak.error-codes/1.0",
        "generatedBy": "contract_candidate/tools/build_registries.py",
        "notes": [
            "Single source of truth for Graph/TypedGraph/Layout/contract error codes.",
            "Legacy values remain registered with legacyAliasOf; new code must not add ad-hoc strings.",
            "severity is the default diagnostic severity; an implementation may not downgrade an error.",
        ],
        "codes": [
            {
                "code": code,
                "domain": domain,
                "severity": severity,
                "retryable": retryable,
                "ownerWorkLine": owner,
                "summary": summary,
                "contractRef": ref,
                **({"legacyAliasOf": alias} if alias else {}),
            }
            for code, domain, severity, retryable, owner, summary, ref, alias in CODES
        ],
    }
    (SCHEMAS / "error-codes.json").write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    condition_document = {
        "schemaVersion": "ueak.condition-codes/1.0",
        "generatedBy": "contract_candidate/tools/build_registries.py",
        "codes": [{"code": code, "kind": kind, "summary": summary} for code, kind, summary in CONDITIONS],
    }
    (SCHEMAS / "condition-codes.json").write_text(
        json.dumps(condition_document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"wrote {len(CODES)} error codes and {len(CONDITIONS)} condition codes")


if __name__ == "__main__":
    main()
