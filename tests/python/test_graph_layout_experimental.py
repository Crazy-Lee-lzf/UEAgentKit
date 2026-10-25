from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ue_agent_kit.editor_bridge import LiveEditorError  # noqa: E402
from ue_agent_kit.experimental_graph_layout import (  # noqa: E402
    APPLY_METHOD,
    APPLY_TOOL_NAME,
    SAVE_METHOD,
    SAVE_TOOL_NAME,
    build_apply_request,
    confirmation_for_plan,
    save_confirmation_for_layout,
)
from ue_agent_kit.graph_layout_preview import build_preview  # noqa: E402
from ue_agent_kit.mcp_graph_layout_experimental import (  # noqa: E402
    register_graph_layout_experimental_tools,
)
from ue_agent_kit.tool_registry import TOOL_DEFINITIONS_BY_NAME  # noqa: E402

CONTEXT = ROOT / "tests" / "fixtures" / "graph_contracts" / "bpctx_export_linear.json"
FINGERPRINT = "sha1:" + "c" * 40


class _FakeServer:
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, *, annotations: Any = None):
        def decorate(function: Any) -> Any:
            self.tools[function.__name__] = function
            return function

        return decorate


class _FakeLiveEditor:
    def __init__(self, capabilities: list[str] | None = None) -> None:
        self.capabilities = capabilities if capabilities is not None else [APPLY_METHOD, SAVE_METHOD]
        self.calls: list[tuple[str, dict[str, Any], float | None]] = []
        self.result: dict[str, Any] = {"changed": True, "singleTransaction": True}

    def status(self) -> dict[str, Any]:
        return {"state": "available", "capabilities": self.capabilities}

    def call_method(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        self.calls.append((method, dict(params or {}), timeout_seconds))
        return dict(self.result)


def _error_response(tool: str, exc: BaseException, *, read_only: bool) -> dict[str, Any]:
    return {
        "tool": tool,
        "ok": False,
        "readOnly": read_only,
        "error": {
            "code": getattr(exc, "code", "error"),
            "message": str(exc),
        },
    }


def _document() -> dict[str, Any]:
    return json.loads(CONTEXT.read_text(encoding="utf-8"))


def _preflight(document: dict[str, Any]) -> dict[str, Any]:
    graph = document["graphs"][0]
    return {
        "schemaVersion": "ueak.graphlayoutpreflight/1.0",
        "available": True,
        "readOnly": True,
        "source": "focused-blueprint-editor",
        "selectionPolicy": "most-recently-active-blueprint-editor",
        "focusScope": "toolkit-graph-tab",
        "assetPath": document["assetPath"],
        "graphGuid": graph["guid"],
        "graphName": graph["name"],
        "packageRevision": document["revision"]["value"],
        "packageDirty": False,
        "graphFingerprint": FINGERPRINT,
        "nodeCount": len(graph["nodes"]),
        "complete": True,
        "nodesTruncated": False,
        "pieState": "stopped",
        "editable": True,
        "sessionId": "11111111-2222-4333-8444-555555555555",
        "pluginBuildHash": "sha256:" + "d" * 64,
    }


def _envelope(document: dict[str, Any]) -> dict[str, Any]:
    return {
        "schemaVersion": "ueak.graphlayoutcontext/1.0",
        "available": True,
        "readOnly": True,
        "source": "focused-blueprint-editor",
        "assetPath": document["assetPath"],
        "graphGuid": document["graphs"][0]["guid"],
        "packageRevision": document["revision"]["value"],
        "packageDirty": False,
        "graphFingerprint": FINGERPRINT,
        "exportedAtUtc": "2026-10-22T12:00:00Z",
        "context": copy.deepcopy(document),
    }


def _preview(mode: str = "full", scope: list[str] | None = None) -> dict[str, Any]:
    document = _document()
    return build_preview(
        _preflight(document),
        _envelope(document),
        mode=mode,
        scope_node_ids=scope,
    )


class ExperimentalGraphLayoutRequestTests(unittest.TestCase):
    def test_preview_result_builds_one_canonical_apply_request(self) -> None:
        preview = _preview()
        before = copy.deepcopy(preview)
        request = build_apply_request(preview)
        self.assertEqual(preview, before)
        self.assertEqual(request["schemaVersion"], "ueak.graphlayoutapply/1.0")
        self.assertEqual(request["assetPath"], preview["assetPath"])
        self.assertEqual(request["graphGuid"], preview["graphGuid"])
        self.assertEqual(request["expectedPackageRevision"], preview["packageRevision"])
        self.assertEqual(request["expectedGraphFingerprint"], preview["graphFingerprint"])
        self.assertEqual(request["requestedMode"], "position-only")
        self.assertFalse(request["allowSave"])
        self.assertFalse(request["allowCompile"])
        self.assertFalse(request["acceptanceEligible"])
        self.assertEqual(request["requiredAcceptanceEvidence"], "independent-reload")
        self.assertEqual(request["plan"], preview["plan"])
        self.assertEqual(
            confirmation_for_plan(request["plan"]["planId"]),
            "APPLY EXPERIMENTAL LAYOUT " + request["plan"]["planId"],
        )

    def test_selection_preview_keeps_the_same_exact_request_shape(self) -> None:
        document = _document()
        scope = ["n:" + document["graphs"][0]["nodes"][-1]["guid"]]
        preview = _preview("selection", scope)
        request = build_apply_request(preview)
        self.assertEqual(request["plan"]["mode"], "selection")
        self.assertEqual(request["plan"]["scopeNodeIds"], scope)

    def test_incremental_preview_keeps_the_same_exact_request_shape(self) -> None:
        document = _document()
        scope = ["n:" + node["guid"] for node in document["graphs"][0]["nodes"][-2:]]
        preview = _preview("incremental", scope)
        request = build_apply_request(preview)
        self.assertEqual(request["plan"]["mode"], "incremental")
        self.assertEqual(request["plan"]["scopeNodeIds"], scope)
        self.assertEqual(request["expectedGraphFingerprint"], preview["graphFingerprint"])

    def test_position_tampering_and_hash_tampering_fail_closed(self) -> None:
        preview = _preview()
        tampered = copy.deepcopy(preview)
        tampered["plan"]["positions"][0]["x"] += 1
        with self.assertRaises(LiveEditorError) as raised:
            build_apply_request(tampered)
        self.assertEqual(raised.exception.code, "graph-layout-experimental-preview-mismatch")

        mirrored = copy.deepcopy(preview)
        mirrored["plan"]["positions"][0]["x"] += 1
        mirrored["positions"][0]["afterX"] += 1
        with self.assertRaises(LiveEditorError) as raised:
            build_apply_request(mirrored)
        self.assertEqual(raised.exception.code, "graph-layout-experimental-preview-hash-mismatch")

        mismatched = copy.deepcopy(preview)
        mismatched["positions"][0]["afterX"] += 1
        with self.assertRaises(LiveEditorError) as raised:
            build_apply_request(mismatched)
        self.assertEqual(raised.exception.code, "graph-layout-experimental-preview-mismatch")

    def test_non_read_only_or_dirty_preview_is_rejected(self) -> None:
        preview = _preview()
        forged = copy.deepcopy(preview)
        forged["readOnly"] = False
        with self.assertRaises(LiveEditorError):
            build_apply_request(forged)

        dirty = copy.deepcopy(preview)
        dirty["packageDirty"] = True
        with self.assertRaises(LiveEditorError):
            build_apply_request(dirty)

        not_editable = copy.deepcopy(preview)
        not_editable["applyEligible"] = False
        with self.assertRaises(LiveEditorError):
            build_apply_request(not_editable)

    def test_unknown_moved_node_is_rejected(self) -> None:
        preview = _preview()
        forged = copy.deepcopy(preview)
        forged["plan"]["movedNodeIds"] = ["n:00000000-0000-4000-8000-00000000ffff"]
        with self.assertRaises(LiveEditorError) as raised:
            build_apply_request(forged)
        self.assertEqual(raised.exception.code, "graph-layout-experimental-preview-mismatch")

    def test_experimental_tools_are_not_in_the_public_registry(self) -> None:
        self.assertNotIn(APPLY_TOOL_NAME, TOOL_DEFINITIONS_BY_NAME)
        self.assertNotIn(SAVE_TOOL_NAME, TOOL_DEFINITIONS_BY_NAME)

    def test_capabilities_require_the_running_editor_to_advertise_opt_in(self) -> None:
        from ue_agent_kit.mcp_server import _capabilities_response

        response = _capabilities_response(
            None,
            object(),
            None,
            experimental_graph_layout_apply=True,
            live_editor_status={
                "state": "available",
                "capabilities": [APPLY_METHOD, SAVE_METHOD],
            },
        )
        blocked = response["liveEditor"]["experimentalGraphLayoutApply"]
        self.assertTrue(blocked["registered"])
        self.assertTrue(blocked["available"])
        self.assertTrue(blocked["pluginCapabilityObserved"])
        self.assertFalse(blocked["productionDefault"])

        unobserved = _capabilities_response(
            None,
            object(),
            None,
            experimental_graph_layout_apply=True,
            live_editor_status={"state": "available", "capabilities": []},
        )
        blocked = unobserved["liveEditor"]["experimentalGraphLayoutApply"]
        self.assertTrue(blocked["registered"])
        self.assertFalse(blocked["available"])
        self.assertFalse(blocked["pluginCapabilityObserved"])


class ExperimentalGraphLayoutToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = _FakeServer()
        self.live = _FakeLiveEditor()
        register_graph_layout_experimental_tools(
            server=self.server,
            live_editor_service=self.live,
            tool_annotations_type=lambda **kwargs: kwargs,
            error_response=_error_response,
        )
        self.preview = _preview()
        self.confirmation = confirmation_for_plan(self.preview["planId"])

    def test_apply_passes_the_canonical_request_and_exact_confirmation(self) -> None:
        result = self.server.tools[APPLY_TOOL_NAME](self.preview, self.confirmation)
        self.assertTrue(result["ok"])
        self.assertTrue(result["experimental"])
        self.assertFalse(result["productionDefault"])
        self.assertEqual(len(self.live.calls), 1)
        method, params, timeout = self.live.calls[0]
        self.assertEqual(method, APPLY_METHOD)
        self.assertEqual(params["confirmation"], self.confirmation)
        self.assertEqual(params["request"]["assetPath"], self.preview["assetPath"])
        self.assertEqual(params["request"]["plan"], self.preview["plan"])
        self.assertEqual(timeout, 60.0)

    def test_apply_requires_the_plan_bound_confirmation_and_sends_nothing_otherwise(self) -> None:
        result = self.server.tools[APPLY_TOOL_NAME](self.preview, "APPLY EXPERIMENTAL LAYOUT layout:wrong")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "graph-layout-experimental-confirmation-required")
        self.assertEqual(self.live.calls, [])

    def test_apply_fails_closed_when_the_editor_project_has_not_opted_in(self) -> None:
        self.live.capabilities = []
        result = self.server.tools[APPLY_TOOL_NAME](self.preview, self.confirmation)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "graph-layout-experimental-disabled")
        self.assertEqual(self.live.calls, [])

    def test_save_requires_the_layout_bound_confirmation(self) -> None:
        layout_hash = self.preview["layoutHash"]
        fingerprint = "sha1:" + "f" * 40
        confirmation = save_confirmation_for_layout(layout_hash)
        result = self.server.tools[SAVE_TOOL_NAME](
            self.preview["assetPath"],
            self.preview["graphGuid"],
            layout_hash,
            fingerprint,
            confirmation,
        )
        self.assertTrue(result["ok"])
        method, params, timeout = self.live.calls[0]
        self.assertEqual(method, SAVE_METHOD)
        self.assertEqual(params["layoutHash"], layout_hash)
        self.assertEqual(params["expectedGraphFingerprint"], fingerprint)
        self.assertEqual(params["confirmation"], confirmation)
        self.assertEqual(timeout, 60.0)

        bad = self.server.tools[SAVE_TOOL_NAME](
            self.preview["assetPath"],
            self.preview["graphGuid"],
            layout_hash,
            fingerprint,
            "SAVE EXPERIMENTAL LAYOUT " + "sha256:" + "0" * 64,
        )
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["error"]["code"], "graph-layout-experimental-confirmation-required")
        self.assertEqual(len(self.live.calls), 1)

    def test_bad_save_identity_is_rejected_before_the_bridge(self) -> None:
        result = self.server.tools[SAVE_TOOL_NAME](
            self.preview["assetPath"],
            self.preview["graphGuid"],
            "not-a-hash",
            "sha1:" + "f" * 40,
            "SAVE EXPERIMENTAL LAYOUT not-a-hash",
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "graph-layout-experimental-preview-invalid")
        self.assertEqual(self.live.calls, [])

    def test_server_source_registers_only_under_the_explicit_flag(self) -> None:
        source = (ROOT / "src/ue_agent_kit/mcp_server.py").read_text(encoding="utf-8")
        self.assertIn("--enable-experimental-graph-layout-apply", source)
        self.assertIn("register_graph_layout_experimental_tools(", source)
        self.assertIn("if experimental_graph_layout_apply:", source)
        self.assertIn('"experimentalGraphLayoutApply"', source)
        self.assertIn('"productionDefault": False', source)


if __name__ == "__main__":
    unittest.main()
