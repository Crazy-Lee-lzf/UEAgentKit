"""0-3.4: one call runs preview -> apply (-> save) for pin defaults."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
TESTS = Path(__file__).resolve().parent
for entry in (str(SRC), str(TESTS)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from ue_agent_kit.experimental_pin_default_edit import (  # noqa: E402
    APPLY_CAPABILITY,
    APPLY_METHOD,
    PREVIEW_CAPABILITY,
    PREVIEW_METHOD,
    SAVE_CAPABILITY,
    SAVE_METHOD,
    APPLY_TOOL_NAME,
    PREVIEW_TOOL_NAME,
    PinDefaultError,
    build_apply_parameters,
    build_save_parameters,
    build_apply_parameters_from_fields,
    confirmation_for_plan,
)
from ue_agent_kit.mcp_pin_default_edit_experimental import (  # noqa: E402
    register_pin_default_edit_experimental_tools,
)
from test_pin_default_edit_experimental import (  # noqa: E402
    AFTER_FINGERPRINT,
    ASSET,
    GRAPH,
    GRAPH_FINGERPRINT,
    PLAN_ID,
    _FakeServer,
    _annotations,
    _default,
    _error_response,
    _preview,
)

TOOL = "ue_edit_blueprint_pin_defaults"


def _apply_payload() -> dict[str, Any]:
    return {
        "applied": True,
        "saved": False,
        "assetPath": ASSET,
        "graphGuid": GRAPH,
        "planId": PLAN_ID,
        "graphFingerprintAfter": GRAPH_FINGERPRINT,
        "defaultsFingerprintAfter": AFTER_FINGERPRINT,
        "nodeCountAfter": 4,
        "linkCountAfter": 3,
    }


def _save_payload() -> dict[str, Any]:
    return {"saved": True, "assetPath": ASSET, "planId": PLAN_ID}


class _StagedLive:
    """Returns a different payload per bridge method, and can fail one stage."""

    def __init__(self, fail_at: str = "") -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail_at = fail_at
        self.payloads = {
            PREVIEW_METHOD: _preview(),
            APPLY_METHOD: _apply_payload(),
            SAVE_METHOD: _save_payload(),
        }

    def status(self) -> dict[str, Any]:
        return {
            "state": "available",
            "capabilities": [PREVIEW_CAPABILITY, APPLY_CAPABILITY, SAVE_CAPABILITY],
        }

    def call_method(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        self.calls.append((method, copy.deepcopy(dict(params or {}))))
        if self.fail_at and method == self.fail_at:
            raise PinDefaultError("stage-failed", f"{method} failed on purpose.")
        return copy.deepcopy(self.payloads[method])

    def methods(self) -> list[str]:
        return [method for method, _params in self.calls]


class CombinedEditToolTests(unittest.TestCase):
    def _tool(self, service: _StagedLive):
        server = _FakeServer()
        register_pin_default_edit_tools = register_pin_default_edit_experimental_tools
        register_pin_default_edit_tools(
            server=server,
            live_editor_service=service,
            tool_annotations_type=_annotations,
            error_response=_error_response,
        )
        return server.tools[TOOL]

    def test_default_path_applies_without_saving(self) -> None:
        service = _StagedLive()
        out = self._tool(service)(
            asset_path=ASSET, graph_guid=GRAPH, defaults=[_default()]
        )
        self.assertTrue(out["ok"])
        self.assertFalse(out["saved"])
        self.assertEqual(service.methods(), [PREVIEW_METHOD, APPLY_METHOD])
        self.assertEqual(sorted(out["stages"]), ["apply", "preview"])

    def test_save_true_runs_all_three_stages_in_order(self) -> None:
        service = _StagedLive()
        out = self._tool(service)(
            asset_path=ASSET, graph_guid=GRAPH, defaults=[_default()], save=True
        )
        self.assertTrue(out["ok"])
        self.assertTrue(out["saved"])
        self.assertEqual(
            service.methods(), [PREVIEW_METHOD, APPLY_METHOD, SAVE_METHOD]
        )
        self.assertEqual(sorted(out["stages"]), ["apply", "preview", "save"])

    def test_apply_request_is_built_from_the_preview(self) -> None:
        service = _StagedLive()
        self._tool(service)(
            asset_path=ASSET, graph_guid=GRAPH, defaults=[_default()]
        )
        _method, apply_params = service.calls[1]
        self.assertEqual(apply_params["planId"], PLAN_ID)
        # The phrase is derived, never supplied by the caller.
        self.assertEqual(apply_params["confirmation"], confirmation_for_plan(PLAN_ID))

    def test_preview_failure_stops_before_apply(self) -> None:
        service = _StagedLive(fail_at=PREVIEW_METHOD)
        out = self._tool(service)(
            asset_path=ASSET, graph_guid=GRAPH, defaults=[_default()]
        )
        self.assertFalse(out["ok"])
        self.assertEqual(service.methods(), [PREVIEW_METHOD])
        self.assertNotIn("completedStages", out)

    def test_apply_failure_reports_how_far_it_got(self) -> None:
        service = _StagedLive(fail_at=APPLY_METHOD)
        out = self._tool(service)(
            asset_path=ASSET, graph_guid=GRAPH, defaults=[_default()]
        )
        self.assertFalse(out["ok"])
        self.assertEqual(out["completedStages"], ["preview"])
        self.assertIn("preview", out["stages"])
        self.assertNotIn("apply", out["stages"])

    def test_save_failure_reports_both_earlier_stages(self) -> None:
        service = _StagedLive(fail_at=SAVE_METHOD)
        out = self._tool(service)(
            asset_path=ASSET, graph_guid=GRAPH, defaults=[_default()], save=True
        )
        self.assertFalse(out["ok"])
        self.assertEqual(out["completedStages"], ["preview", "apply"])

    def test_tool_is_registered(self) -> None:
        server = _FakeServer()
        register_pin_default_edit_experimental_tools(
            server=server,
            live_editor_service=_StagedLive(),
            tool_annotations_type=_annotations,
            error_response=_error_response,
        )
        self.assertIn(TOOL, server.tools)



class ApplyFromFieldsTests(unittest.TestCase):
    """0-3.4: the plan fields alone must build the identical request."""

    def test_matches_the_preview_path_exactly(self) -> None:
        from_preview = build_apply_parameters(_preview())
        from_fields = build_apply_parameters_from_fields(
            plan_id=PLAN_ID,
            asset_path=ASSET,
            graph_guid=GRAPH,
            defaults=[_default()],
        )
        self.assertEqual(from_fields, from_preview)

    def test_rejects_a_malformed_plan_id(self) -> None:
        for bad in ("", "sha256:short", "abc", "sha256:" + "A" * 64):
            with self.subTest(plan_id=bad):
                with self.assertRaises(PinDefaultError) as raised:
                    build_apply_parameters_from_fields(
                        plan_id=bad,
                        asset_path=ASSET,
                        graph_guid=GRAPH,
                        defaults=[_default()],
                    )
                self.assertEqual(
                    raised.exception.code, "graph-pin-default-invalid-parameters"
                )

    def test_explicit_wrong_confirmation_is_still_refused(self) -> None:
        with self.assertRaises(PinDefaultError) as raised:
            build_apply_parameters_from_fields(
                plan_id=PLAN_ID,
                asset_path=ASSET,
                graph_guid=GRAPH,
                defaults=[_default()],
                confirmation="SET EXPERIMENTAL BLUEPRINT PIN DEFAULTS wrong",
            )
        self.assertEqual(
            raised.exception.code, "graph-pin-default-confirmation-required"
        )

    def test_tool_accepts_the_plan_fields(self) -> None:
        service = _StagedLive()
        server = _FakeServer()
        register_pin_default_edit_experimental_tools(
            server=server,
            live_editor_service=service,
            tool_annotations_type=_annotations,
            error_response=_error_response,
        )
        out = server.tools["ue_apply_blueprint_pin_defaults_experimental"](
            plan_id=PLAN_ID,
            asset_path=ASSET,
            graph_guid=GRAPH,
            defaults=[_default()],
        )
        self.assertTrue(out["ok"])
        self.assertEqual(service.methods(), [APPLY_METHOD])
        self.assertEqual(service.calls[0][1]["planId"], PLAN_ID)

    def test_tool_refuses_when_neither_form_is_complete(self) -> None:
        service = _StagedLive()
        server = _FakeServer()
        register_pin_default_edit_experimental_tools(
            server=server,
            live_editor_service=service,
            tool_annotations_type=_annotations,
            error_response=_error_response,
        )
        out = server.tools["ue_apply_blueprint_pin_defaults_experimental"](
            plan_id=PLAN_ID,
        )
        self.assertFalse(out["ok"])
        self.assertEqual(
            out["error"]["code"], "graph-pin-default-invalid-parameters"
        )
        self.assertEqual(service.methods(), [])


def _register_stub(*_args: Any, **_kwargs: Any) -> None:
    return None



class EnvelopeToleranceTests(unittest.TestCase):
    """0-3.4: passing the tool response itself must work, not just the payload."""

    def test_apply_accepts_the_tool_envelope(self) -> None:
        wrapped = {
            "schemaVersion": "1.0",
            "tool": PREVIEW_TOOL_NAME,
            "ok": True,
            "result": _preview(),
        }
        self.assertEqual(
            build_apply_parameters(wrapped), build_apply_parameters(_preview())
        )

    def test_save_accepts_the_tool_envelope(self) -> None:
        wrapped = {
            "schemaVersion": "1.0",
            "tool": APPLY_TOOL_NAME,
            "ok": True,
            "result": _apply_payload(),
        }
        self.assertEqual(
            build_save_parameters(wrapped), build_save_parameters(_apply_payload())
        )

    def test_a_wrapper_without_the_marker_is_still_refused(self) -> None:
        with self.assertRaises(PinDefaultError):
            build_apply_parameters(
                {"schemaVersion": "1.0", "result": {"nonsense": True}}
            )


if __name__ == "__main__":
    unittest.main()
