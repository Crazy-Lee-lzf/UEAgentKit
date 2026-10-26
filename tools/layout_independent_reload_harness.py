"""Independent-process reload evidence for the experimental graph-layout Save.

Why this exists
---------------
``ue_save_blueprint_graph_layout_experimental`` proves only a *same-process*
readback: after ``SavePackage`` the Editor hashes the file from disk and the
package still holds the positions in memory.  It therefore reports
``independentReloadVerified: false`` (see ``BlueprintGraphLayoutExperimental.cpp``).
A same-process readback cannot distinguish "the bytes on disk describe the layout"
from "the Editor still remembers the layout", so it is not acceptance evidence.

This module turns the missing evidence into something checkable without opening
Unreal: it defines the probe a *fresh* process must produce, and verifies one
apply/save/probe triple against the bytes on disk.  It never mutates a file and
never contacts the Editor.

Evidence standard (all of it is required)
-----------------------------------------
1. The probe came from a different OS process than the one that saved:
   different PID *and* different process start time, and it started only after
   the saving Editor had terminated.
2. The probe was read-only: it did not save, compile, or touch the package.
3. The bytes on disk are the bytes the Save returned: the file SHA256 equals
   ``save.assetSha256``, ``save.readback.assetSha256`` and the probe's
   ``assetSha256``; the derived ``package-sha256-v1`` revision equals
   ``save.readback.packageRevision`` and the probe's ``packageRevision``.
4. Same asset, same graph, same node GUID set, and *exactly* equal int32
   positions for every node.
5. The layout hash recomputed here (independent implementation of
   ``ueak.typedgraph.layout/1``) equals the probe's, the Save's and the Apply's
   ``layoutHash``; the probe's ``graphFingerprint`` equals the post-save one.

Anything else fails closed and keeps ``independentReloadVerified`` false.  The
probe's provenance is recorded as ``attestation: self-reported`` because a JSON
file cannot prove it was written by the process it names; the operator command
that produced it is recorded verbatim so the claim is auditable.

Probe producer (no C++ change needed)
-------------------------------------
``scripts/RunExport.ps1 -Asset <path> -Profile logic -Format json -IncludeLayout``
already dumps every node's ``layout{x,y}``, the graph ``guid`` and the
``package-sha256-v1`` revision from a *fresh* ``UnrealEditor-Cmd.exe`` process
reading the ``.uasset`` from disk.  ``build-probe`` converts that export plus the
launcher-captured PID/start time into the probe document below.

CLI::

    py -3.12 tools/layout_independent_reload_harness.py build-probe \
        --export bpctx.json --asset BP_Layout.uasset \
        --pid 4242 --started-utc 2026-10-26T03:00:00Z \
        --command "UnrealEditor-Cmd.exe ... -run=BlueprintContextExport" \
        --out probe.json

    py -3.12 tools/layout_independent_reload_harness.py verify \
        --apply apply.json --save save.json --probe probe.json \
        --asset BP_Layout.uasset \
        --editor-pid 1111 --editor-started-utc ... --editor-terminated-utc ... \
        --out evidence.json

``verify`` exits 0 only when the evidence is complete, 2 when it fails closed,
and 1 on a usage error, so a caller can never mistake a refusal for success.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

PROBE_SCHEMA = "ueak.layout.independent-reload-probe/1.0"
EVIDENCE_SCHEMA = "ueak.layout.independent-reload-evidence/1.0"
EXPORT_SCHEMA_PREFIX = "1."
LAYOUT_DOMAIN = "ueak.typedgraph.layout/1"
REVISION_PREFIX = "sha256:"
MAX_REPORTED_MISMATCHES = 20

# Probe fields this harness requires before it will look at the numbers.
REQUIRED_PROBE_FIELDS = (
    "schemaVersion",
    "readOnly",
    "assetPath",
    "graphGuid",
    "assetSha256",
    "processId",
    "processStartUtc",
    "startedAtUtc",
    "nodes",
)


def _fail(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _is_int32(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and -2147483648 <= value <= 2147483647


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and value.startswith(REVISION_PREFIX)
        and len(value) == len(REVISION_PREFIX) + 64
        and all(character in "0123456789abcdef" for character in value[len(REVISION_PREFIX):])
    )


def _sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return REVISION_PREFIX + digest.hexdigest()


def _sha256_of_bytes(payload: bytes) -> str:
    return REVISION_PREFIX + hashlib.sha256(payload).hexdigest()


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def layout_hash_from_positions(positions: Iterable[Mapping[str, Any]]) -> str:
    """Independent implementation of the ``ueak.typedgraph.layout/1`` hash.

    Matches ``FUEAgentKitBlueprintGraphLayout::ComputeLayoutHash``: positions are
    sorted by ``nodeId`` (case-sensitive ordinal) and hashed as
    ``"<domain>\\n" + canonical_json`` with UTF-8 encoding.
    """

    normalized = [
        {"nodeId": str(item["nodeId"]), "x": int(item["x"]), "y": int(item["y"])}
        for item in positions
    ]
    normalized.sort(key=lambda item: item["nodeId"])
    payload = json.dumps(
        normalized, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return REVISION_PREFIX + hashlib.sha256(
        LAYOUT_DOMAIN.encode("utf-8") + b"\n" + payload
    ).hexdigest()


def unwrap_result(document: Mapping[str, Any]) -> Mapping[str, Any]:
    """Accept either an MCP envelope (``{"result": {...}}``) or a bare result."""

    if not isinstance(document, Mapping):
        raise ValueError("document must be a JSON object")
    inner = document.get("result")
    if isinstance(inner, Mapping) and "result" not in inner:
        return inner
    return document


def _node_positions(items: Any, *, label: str) -> dict[str, tuple[int, int]]:
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)) or not items:
        raise ValueError(f"{label} must be a non-empty array of {{nodeId,x,y}} objects")
    positions: dict[str, tuple[int, int]] = {}
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            raise ValueError(f"{label}[{index}] must be an object")
        node_id = item.get("nodeId")
        if not isinstance(node_id, str) or not node_id.startswith("n:"):
            raise ValueError(f"{label}[{index}].nodeId must be a n:<guid> string")
        if not _is_int32(item.get("x")) or not _is_int32(item.get("y")):
            raise ValueError(f"{label}[{index}] x/y must be int32 values")
        key = node_id.strip().lower()
        if key in positions:
            raise ValueError(f"{label} contains duplicate {key}")
        positions[key] = (int(item["x"]), int(item["y"]))
    if not positions:
        raise ValueError(f"{label} produced no positions")
    return positions


# --------------------------------------------------------------------------- #
# build-probe: BlueprintContextExport output -> independent-reload probe
# --------------------------------------------------------------------------- #


def build_probe(
    *,
    export_document: Mapping[str, Any],
    asset_sha256: str,
    process_id: int,
    process_start_utc: str,
    started_at_utc: str,
    command: str,
    graph_guid: str = "",
    executable: str = "",
    host_name: str = "",
    reloaded_at_utc: str = "",
) -> dict[str, Any]:
    """Build the probe document from one include-layout BlueprintContextExport."""

    if not isinstance(export_document, Mapping):
        raise ValueError("export document must be a JSON object")
    schema = str(export_document.get("schemaVersion") or "")
    if not schema.startswith(EXPORT_SCHEMA_PREFIX):
        raise ValueError("export document is not a BlueprintContextExport 1.x document")
    graphs = export_document.get("graphs")
    if not isinstance(graphs, list) or not graphs:
        raise ValueError("export document has no graphs; use -Profile logic -IncludeLayout")
    candidates = [
        graph
        for graph in graphs
        if isinstance(graph, Mapping) and (not graph_guid or str(graph.get("guid")) == graph_guid)
    ]
    if len(candidates) != 1:
        raise ValueError(
            "select exactly one graph with --graph; the export contains "
            + ", ".join(str(graph.get("guid")) for graph in graphs if isinstance(graph, Mapping))
        )
    graph = candidates[0]
    nodes = graph.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("the selected graph has no nodes in the export")
    positions: list[dict[str, Any]] = []
    for index, node in enumerate(nodes):
        if not isinstance(node, Mapping):
            raise ValueError(f"graphs[].nodes[{index}] must be an object")
        layout = node.get("layout")
        guid = node.get("guid")
        if not isinstance(layout, Mapping) or not isinstance(guid, str) or not guid:
            raise ValueError(
                "export nodes need layout{x,y}; add -IncludeLayout to the export command"
            )
        positions.append(
            {
                "nodeId": "n:" + guid.strip().lower(),
                "x": int(layout.get("x", 0)),
                "y": int(layout.get("y", 0)),
            }
        )
    revision = export_document.get("revision") if isinstance(export_document.get("revision"), Mapping) else {}
    return {
        "schemaVersion": PROBE_SCHEMA,
        "readOnly": True,
        "saved": False,
        "compiled": False,
        "writeEffects": {
            "memoryModified": False,
            "diskModified": False,
            "packageSaved": False,
            "packageCompiled": False,
        },
        "assetPath": str(export_document.get("assetPath") or ""),
        "graphGuid": str(graph.get("guid") or ""),
        "graphName": str(graph.get("name") or ""),
        "packageDirty": bool(revision.get("packageDirty", True)),
        "packageRevision": str(revision.get("value") or ""),
        "assetSha256": asset_sha256,
        "engineVersion": str(export_document.get("engineVersion") or ""),
        "exporterVersion": str(export_document.get("exporterVersion") or ""),
        "processId": int(process_id),
        "processStartUtc": str(process_start_utc),
        "startedAtUtc": str(started_at_utc),
        "reloadedAtUtc": str(reloaded_at_utc or started_at_utc),
        "executable": str(executable),
        "hostName": str(host_name),
        "command": str(command),
        "producer": "BlueprintContextExport (-run=BlueprintContextExport -IncludeLayout)",
        "loadSource": "disk",
        "layoutHash": layout_hash_from_positions(positions),
        "nodes": positions,
    }


# --------------------------------------------------------------------------- #
# verify: apply + save + probe + on-disk bytes -> evidence report
# --------------------------------------------------------------------------- #


def verify_independent_reload(
    *,
    apply_result: Mapping[str, Any],
    save_result: Mapping[str, Any],
    probe: Mapping[str, Any] | None,
    asset_bytes: bytes,
    editor_process: Mapping[str, Any] | None,
    asset_filename: str = "",
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Decide whether one apply/save/probe triple is independent-reload evidence."""

    checks: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> bool:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})
        return bool(ok)

    def fail(code: str, message: str) -> None:
        failures.append(_fail(code, message))

    now = now_utc or datetime.now(timezone.utc)
    disk_sha256 = _sha256_of_bytes(asset_bytes)
    derived_revision = disk_sha256

    apply_document = dict(unwrap_result(apply_result))
    save_document = dict(unwrap_result(save_result))

    # ---- Apply claims -----------------------------------------------------
    apply_layout_hash = str(apply_document.get("layoutHash") or "")
    apply_fingerprint = str(apply_document.get("graphFingerprintAfterApply") or "")
    apply_applied = apply_document.get("applied") is True
    apply_undo_redo = (
        apply_document.get("undoVerified") is True and apply_document.get("redoVerified") is True
    )
    record("apply.applied", apply_applied, "the Apply result must report applied=true")
    record("apply.undo-redo", apply_undo_redo, "undoVerified and redoVerified must both be true")
    if not apply_applied:
        fail("independent-reload-apply-missing", "the Apply result does not report an applied change")
    if not apply_undo_redo:
        fail("independent-reload-apply-unverified", "the Apply result did not prove Undo and Redo")
    if not _is_sha256(apply_layout_hash):
        fail("independent-reload-apply-invalid", "apply.layoutHash is not a canonical sha256")
    try:
        apply_positions = _node_positions(
            apply_document.get("positionsAfter"), label="apply.positionsAfter"
        )
    except ValueError as error:
        apply_positions = {}
        fail("independent-reload-apply-invalid", str(error))

    # ---- Save claims ------------------------------------------------------
    save_layout_hash = str(save_document.get("layoutHash") or "")
    save_asset_sha256 = str(save_document.get("assetSha256") or "")
    save_saved = save_document.get("saved") is True
    save_confirmation = str(save_document.get("confirmation") or "")
    save_compiled = save_document.get("compiled") is True or save_document.get("autoSave") is True
    readback = save_document.get("readback") if isinstance(save_document.get("readback"), Mapping) else {}
    readback_revision = str(readback.get("packageRevision") or "")
    readback_fingerprint = str(readback.get("graphFingerprint") or "")
    readback_sha256 = str(readback.get("assetSha256") or "")

    record("save.explicit", save_saved and not save_compiled, "Save must be explicit, without compile/auto-save")
    record(
        "save.confirmation",
        save_confirmation == "SAVE EXPERIMENTAL LAYOUT " + save_layout_hash and bool(save_layout_hash),
        "the Save confirmation must be bound to save.layoutHash",
    )
    if not save_saved:
        fail("independent-reload-save-missing", "the Save result does not report saved=true")
    if save_compiled:
        fail("independent-reload-save-not-explicit", "the Save result reports compile or auto-save")
    if not save_confirmation.startswith("SAVE EXPERIMENTAL LAYOUT "):
        fail("independent-reload-save-invalid", "the Save result carries no layout-bound confirmation")

    # ---- Disk reconciliation ---------------------------------------------
    disk_matches = (
        _is_sha256(save_asset_sha256)
        and save_asset_sha256 == disk_sha256
        and (not readback_sha256 or readback_sha256 == disk_sha256)
    )
    record(
        "disk.asset-sha256",
        disk_matches,
        f"disk={disk_sha256} save={save_asset_sha256} readback={readback_sha256}",
    )
    if not disk_matches:
        fail(
            "independent-reload-disk-mismatch",
            "the bytes on disk are not the bytes the Save returned (assetSha256 differs)",
        )
    revision_matches = _is_sha256(readback_revision) and readback_revision == derived_revision
    record(
        "disk.package-revision",
        revision_matches,
        f"derived={derived_revision} save.readback={readback_revision}",
    )
    if not revision_matches:
        fail(
            "independent-reload-revision-mismatch",
            "the package-sha256-v1 revision of the file on disk differs from the saved readback",
        )

    # ---- Probe presence and provenance -----------------------------------
    provenance: dict[str, Any] = {
        "attestation": "self-reported",
        "editorPid": None,
        "editorStartedUtc": "",
        "editorTerminatedUtc": "",
        "probePid": None,
        "probeProcessStartUtc": "",
        "probeStartedAtUtc": "",
        "probeReloadedAtUtc": "",
        "probeExecutable": "",
        "probeCommand": "",
        "probeProducer": "",
        "probeLoadSource": "",
        "engineVersion": "",
        "hostName": "",
    }
    probe_document: Mapping[str, Any] = probe if isinstance(probe, Mapping) else {}
    probe_positions: dict[str, tuple[int, int]] = {}
    mismatches: list[dict[str, Any]] = []
    evidence_gaps: list[str] = []
    recomputed = ""
    evidence_level = "unverified"

    if not probe_document:
        fail(
            "independent-reload-probe-missing",
            "no independent-process probe was supplied; same-process readback is not evidence",
        )
        record("probe.present", False, "no probe document")
    else:
        record("probe.present", True, "")
        missing = [field for field in REQUIRED_PROBE_FIELDS if field not in probe_document]
        if missing:
            fail("independent-reload-probe-invalid", "probe is missing " + ", ".join(missing))
            record("probe.schema", False, "missing " + ", ".join(missing))
        else:
            schema_ok = probe_document.get("schemaVersion") == PROBE_SCHEMA
            record("probe.schema", schema_ok, f"probe.schemaVersion={probe_document.get('schemaVersion')!r}")
            if not schema_ok:
                fail("independent-reload-probe-invalid", f"probe schema must be {PROBE_SCHEMA}")
        try:
            probe_positions = _node_positions(
                probe_document.get("nodes"), label="probe.nodes"
            )
        except ValueError as error:
            fail("independent-reload-probe-invalid", str(error))

        write_effects = probe_document.get("writeEffects")
        read_only = (
            probe_document.get("readOnly") is True
            and probe_document.get("saved") is not True
            and probe_document.get("compiled") is not True
            and (not isinstance(write_effects, Mapping) or not any(bool(value) for value in write_effects.values()))
        )
        record("probe.read-only", read_only, "the probe must not save, compile, or write the package")
        if not read_only:
            fail("independent-reload-probe-wrote", "the probe reports a write; it is not read-only evidence")

        probe_pid = probe_document.get("processId")
        editor_pid = editor_process.get("pid") if isinstance(editor_process, Mapping) else None
        probe_start = _parse_utc(probe_document.get("processStartUtc"))
        editor_start = _parse_utc(editor_process.get("startedAtUtc")) if isinstance(editor_process, Mapping) else None
        editor_terminated = _parse_utc(editor_process.get("terminatedAtUtc")) if isinstance(editor_process, Mapping) else None
        probe_started = _parse_utc(probe_document.get("startedAtUtc"))
        probe_reloaded = _parse_utc(probe_document.get("reloadedAtUtc")) or probe_started

        provenance.update(
            {
                "editorPid": editor_pid,
                "editorStartedUtc": str((editor_process or {}).get("startedAtUtc") or ""),
                "editorTerminatedUtc": str((editor_process or {}).get("terminatedAtUtc") or ""),
                "probePid": probe_pid,
                "probeProcessStartUtc": str(probe_document.get("processStartUtc") or ""),
                "probeStartedAtUtc": str(probe_document.get("startedAtUtc") or ""),
                "probeReloadedAtUtc": str(probe_document.get("reloadedAtUtc") or ""),
                "probeExecutable": str(probe_document.get("executable") or ""),
                "probeCommand": str(probe_document.get("command") or ""),
                "probeProducer": str(probe_document.get("producer") or ""),
                "probeLoadSource": str(probe_document.get("loadSource") or ""),
                "engineVersion": str(probe_document.get("engineVersion") or ""),
                "hostName": str(probe_document.get("hostName") or ""),
            }
        )

        provenance_complete = (
            isinstance(probe_pid, int)
            and not isinstance(probe_pid, bool)
            and probe_pid > 0
            and probe_start is not None
            and probe_started is not None
            and isinstance(editor_pid, int)
            and not isinstance(editor_pid, bool)
            and editor_pid > 0
            and editor_start is not None
            and editor_terminated is not None
        )
        record(
            "provenance.complete",
            provenance_complete,
            "editor pid/start/termination and probe pid/start timestamps are all required",
        )
        if not provenance_complete:
            fail(
                "independent-reload-provenance-missing",
                "the saving Editor process (pid, start, termination) and the probe process "
                "(pid, start) must both be recorded",
            )

        if provenance_complete:
            different_process = probe_pid != editor_pid and probe_start != editor_start
            record(
                "provenance.different-process",
                different_process,
                f"probePid={probe_pid} editorPid={editor_pid}",
            )
            if not different_process:
                evidence_level = "same-process"
                fail(
                    "independent-reload-same-process",
                    "the probe ran in the saving Editor process; that is a same-process readback",
                )
            timeline_ok = (
                editor_terminated <= probe_started  # type: ignore[operator]
                and probe_started <= probe_reloaded  # type: ignore[operator]
                and probe_started <= now
            )
            record(
                "provenance.timeline",
                timeline_ok,
                f"editorTerminated={provenance['editorTerminatedUtc']} probeStarted={provenance['probeStartedAtUtc']}",
            )
            if not timeline_ok:
                fail(
                    "independent-reload-timeline-invalid",
                    "the probe must start after the saving Editor terminated and before now",
                )

        # Probe-side reconciliation with the saved receipt.
        probe_sha256 = str(probe_document.get("assetSha256") or "")
        probe_disk_ok = _is_sha256(probe_sha256) and probe_sha256 == disk_sha256
        record("probe.asset-sha256", probe_disk_ok, f"probe={probe_sha256} disk={disk_sha256}")
        if not probe_disk_ok:
            fail(
                "independent-reload-probe-disk-mismatch",
                "the probe hashed different bytes than the ones on disk after Save",
            )
        probe_revision = str(probe_document.get("packageRevision") or "")
        if probe_revision:
            probe_revision_ok = probe_revision == derived_revision
            record("probe.package-revision", probe_revision_ok, f"probe={probe_revision}")
            if not probe_revision_ok:
                fail(
                    "independent-reload-probe-revision-mismatch",
                    "the fresh process saw a different package revision than the file on disk",
                )
        probe_fingerprint = str(probe_document.get("graphFingerprint") or "")
        if probe_fingerprint:
            fingerprint_ok = bool(readback_fingerprint) and probe_fingerprint == readback_fingerprint
            record("probe.graph-fingerprint", fingerprint_ok, "graph state hash must match the post-save value")
            if not fingerprint_ok:
                fail(
                    "independent-reload-fingerprint-mismatch",
                    "the reloaded graph state differs from the post-save graph fingerprint",
                )
        else:
            # BlueprintContextExport does not emit the graph fingerprint; say so
            # instead of silently dropping a cross-check.
            evidence_gaps.append("graphFingerprint")
            record(
                "probe.graph-fingerprint",
                True,
                "not available: BlueprintContextExport does not emit graphFingerprint "
                "(positions and layoutHash are still verified)",
            )
        probe_dirty = probe_document.get("packageDirty")
        dirty_ok = probe_dirty is not True and readback.get("packageDirty") is not True
        record("probe.package-dirty", dirty_ok, "a fresh load of a saved package must be clean")
        if not dirty_ok:
            fail("independent-reload-package-dirty", "the reloaded package still reports dirty")

        # ---- Identity -----------------------------------------------------
        # The shipped Save receipt and the probe both carry assetPath/graphGuid.
        # The Apply result does not (it is bound to the graph by layoutHash), so
        # when it does carry identity it must agree rather than be required.
        save_asset = str(save_document.get("assetPath") or "")
        save_graph = str(save_document.get("graphGuid") or "")
        apply_asset = str(apply_document.get("assetPath") or "")
        apply_graph = str(apply_document.get("graphGuid") or "")
        identity_values = {
            "save.assetPath": save_asset,
            "probe.assetPath": str(probe_document.get("assetPath") or ""),
            "save.graphGuid": save_graph,
            "probe.graphGuid": str(probe_document.get("graphGuid") or ""),
            "apply.assetPath": apply_asset,
            "apply.graphGuid": apply_graph,
        }
        identity_ok = (
            bool(save_asset)
            and bool(save_graph)
            and save_asset == identity_values["probe.assetPath"]
            and save_graph == identity_values["probe.graphGuid"]
            and (not apply_asset or apply_asset == save_asset)
            and (not apply_graph or apply_graph == save_graph)
        )
        record("identity.asset-and-graph", identity_ok, json.dumps(identity_values, sort_keys=True))
        if not identity_ok:
            fail(
                "independent-reload-identity-mismatch",
                "save/probe (and Apply, when it carries identity) disagree on assetPath or graphGuid",
            )

        # ---- Node set and positions --------------------------------------
        expected_ids = set(apply_positions)
        actual_ids = set(probe_positions)
        node_set_ok = bool(expected_ids) and expected_ids == actual_ids
        record(
            "positions.node-set",
            node_set_ok,
            f"missing={sorted(expected_ids - actual_ids)} extra={sorted(actual_ids - expected_ids)}",
        )
        if not node_set_ok:
            fail(
                "independent-reload-node-set-mismatch",
                "the reloaded graph does not contain exactly the applied node GUIDs",
            )

        mismatches = [
            {
                "nodeId": node_id,
                "expected": {"x": expected_positions[0], "y": expected_positions[1]},
                "actual": {"x": probe_positions[node_id][0], "y": probe_positions[node_id][1]},
            }
            for node_id, expected_positions in sorted(apply_positions.items())
            if node_id in probe_positions and probe_positions[node_id] != expected_positions
        ]
        positions_ok = bool(apply_positions) and not mismatches
        record(
            "positions.exact",
            positions_ok,
            f"compared={len(set(apply_positions) & set(probe_positions))} mismatched={len(mismatches)}",
        )
        if not positions_ok:
            fail(
                "independent-reload-position-mismatch",
                "at least one node position reloaded from disk differs from the applied position",
            )

        # ---- Layout hash --------------------------------------------------
        recomputed = layout_hash_from_positions(
            [{"nodeId": node_id, "x": x, "y": y} for node_id, (x, y) in probe_positions.items()]
        )
        probe_layout_hash = str(probe_document.get("layoutHash") or "")
        hash_ok = (
            recomputed == probe_layout_hash == save_layout_hash == apply_layout_hash
            and bool(save_layout_hash)
            and bool(apply_layout_hash)
        )
        record(
            "layout.hash",
            hash_ok,
            f"recomputed={recomputed} probe={probe_layout_hash} save={save_layout_hash} apply={apply_layout_hash}",
        )
        if not hash_ok:
            fail(
                "independent-reload-hash-mismatch",
                "the layout hash recomputed from the reloaded positions does not equal the saved layoutHash",
            )

    verified = not failures
    if verified:
        evidence_level = "independent-process"

    report = {
        "schemaVersion": EVIDENCE_SCHEMA,
        "verified": verified,
        "independentReloadVerified": verified,
        "evidenceLevel": evidence_level,
        "acceptanceEligible": verified,
        "requiredAcceptanceEvidence": "independent-reload",
        "sameProcessReadbackIsNotEvidence": True,
        "failClosedCodes": [failure["code"] for failure in failures],
        "evidenceGaps": evidence_gaps,
        "failures": failures,
        "checks": checks,
        "identity": {
            "assetPath": str(save_document.get("assetPath") or ""),
            "assetFilename": asset_filename,
            "graphGuid": str(save_document.get("graphGuid") or ""),
            "layoutHash": str(save_document.get("layoutHash") or ""),
            "recomputedLayoutHash": recomputed,
            "packageRevision": derived_revision,
            "postSaveFingerprint": str(readback_fingerprint or ""),
            "nodeCount": len(probe_positions),
            "nodeIds": sorted(probe_positions),
        },
        "asset": {
            "filename": asset_filename,
            "sizeBytes": len(asset_bytes),
            "sha256": disk_sha256,
            "savedAssetSha256": save_asset_sha256,
        },
        "provenance": provenance,
        "positions": {
            "expectedCount": len(apply_positions),
            "reloadedCount": len(probe_positions),
            "mismatchCount": len(mismatches),
            "mismatches": mismatches[:MAX_REPORTED_MISMATCHES],
            "missing": sorted(set(apply_positions) - set(probe_positions)),
            "extra": sorted(set(probe_positions) - set(apply_positions)),
        },
        "generatedAtUtc": _iso(now),
        "harnessVersion": "1.0",
    }
    return report


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _load_json(path: str) -> dict[str, Any]:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return document


def build_probe_command(arguments: argparse.Namespace) -> int:
    export_document = _load_json(arguments.export)
    asset_path = Path(arguments.asset)
    if not asset_path.is_file():
        print(f"error: asset not found: {asset_path}", file=sys.stderr)
        return 1
    try:
        probe = build_probe(
            export_document=export_document,
            asset_sha256=_sha256_of_file(asset_path),
            process_id=arguments.pid,
            process_start_utc=arguments.started_utc,
            started_at_utc=arguments.started_utc,
            command=arguments.command,
            graph_guid=arguments.graph,
            executable=arguments.executable,
            host_name=arguments.host,
            reloaded_at_utc=arguments.reloaded_utc,
        )
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    payload = json.dumps(probe, ensure_ascii=False, indent=2) + "\n"
    if arguments.out:
        Path(arguments.out).write_text(payload, encoding="utf-8", newline="\n")
    else:
        sys.stdout.write(payload)
    return 0


def verify_command(arguments: argparse.Namespace) -> int:
    asset_path = Path(arguments.asset)
    if not asset_path.is_file():
        print(f"error: asset not found: {asset_path}", file=sys.stderr)
        return 1
    editor_process = {
        "pid": arguments.editor_pid,
        "startedAtUtc": arguments.editor_started_utc,
        "terminatedAtUtc": arguments.editor_terminated_utc,
    }
    report = verify_independent_reload(
        apply_result=_load_json(arguments.apply),
        save_result=_load_json(arguments.save),
        probe=_load_json(arguments.probe) if arguments.probe else None,
        asset_bytes=asset_path.read_bytes(),
        editor_process=editor_process,
        asset_filename=str(asset_path),
    )
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if arguments.out:
        Path(arguments.out).write_text(payload, encoding="utf-8", newline="\n")
    if arguments.json:
        sys.stdout.write(payload)
    else:
        verdict = "VERIFIED" if report["verified"] else "NOT VERIFIED (fail closed)"
        print(f"independentReloadVerified={report['independentReloadVerified']} evidenceLevel={report['evidenceLevel']}")
        print(f"verdict: {verdict}")
        for failure in report["failures"]:
            print(f"  - {failure['code']}: {failure['message']}")
        if report["verified"]:
            print(f"  layoutHash={report['identity']['layoutHash']}")
            print(f"  assetSha256={report['asset']['sha256']}")
            print(f"  probePid={report['provenance']['probePid']} editorPid={report['provenance']['editorPid']}")
    return 0 if report["verified"] else 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subcommands = parser.add_subparsers(dest="command", required=True)

    build = subcommands.add_parser("build-probe", help="convert a BlueprintContextExport into a probe")
    build.add_argument("--export", required=True, help="json output of -run=BlueprintContextExport")
    build.add_argument("--asset", required=True, help="the .uasset file on disk")
    build.add_argument("--pid", type=int, required=True, help="PID of the fresh export process")
    build.add_argument("--started-utc", required=True, help="ISO-8601 UTC start time of that process")
    build.add_argument("--command", required=True, help="exact command line that produced the export")
    build.add_argument("--graph", default="", help="graph guid; required when the export has several")
    build.add_argument("--executable", default="", help="UnrealEditor-Cmd.exe path")
    build.add_argument("--host", default="", help="machine name")
    build.add_argument("--reloaded-utc", default="", help="ISO-8601 UTC reload completion time")
    build.add_argument("--out", default="", help="write the probe here instead of stdout")
    build.set_defaults(handler=build_probe_command)

    verify = subcommands.add_parser("verify", help="verify one apply/save/probe triple")
    verify.add_argument("--apply", required=True)
    verify.add_argument("--save", required=True)
    verify.add_argument("--probe", default="")
    verify.add_argument("--asset", required=True)
    verify.add_argument("--editor-pid", type=int, default=0)
    verify.add_argument("--editor-started-utc", default="")
    verify.add_argument("--editor-terminated-utc", default="")
    verify.add_argument("--out", default="")
    verify.add_argument("--json", action="store_true", help="print the report as JSON")
    verify.set_defaults(handler=verify_command)

    arguments = parser.parse_args(argv)
    return int(arguments.handler(arguments))


if __name__ == "__main__":
    raise SystemExit(main())
