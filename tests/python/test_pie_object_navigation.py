"""PIE Inspector P3: object-reference navigation.

Covers the Python half of P3: the ``objectPath`` target form (any UObject inside
one exact PIE world, mutually exclusive with the Actor and Component identities),
the parameter validation that guards it, the pass-through of both PIE property
tools against a fake bridge, and the capability block that documents the new
navigation contract.

No new Tool is introduced by P3: the P2 tools already carry the target, so the
registry surface must stay exactly what it was.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ue_agent_kit.editor_bridge import LiveEditorBridgeService, LiveEditorError  # noqa: E402
from ue_agent_kit.mcp_live_tools import register_live_read_tools  # noqa: E402
from ue_agent_kit.mcp_pure_live_tools import (  # noqa: E402
    PURE_LIVE_PIE_TOOL_NAMES,
    PURE_LIVE_READ_TOOL_NAMES,
    build_pure_live_capabilities,
)
from ue_agent_kit.tool_registry import (  # noqa: E402
    LIVE_EDITOR_METHODS,
    TOOL_DEFINITIONS_BY_NAME,
    tool_names_for_mode,
)

LIST_TOOL = "ue_list_pie_object_properties"
READ_TOOL = "ue_read_pie_object_properties"
CONTEXT_ID = "pie-0-listenServer"
ACTOR_PATH = "/Game/X.X:PersistentLevel.PieP3Actor"
COMPONENT_PATH = "/Game/X.X:PersistentLevel.PieP3Actor.NavComponent"
PAYLOAD_PATH = "/Game/X.X:PersistentLevel.PieP3Actor.NavComponent.PieP3Payload"
INVALID = "live-editor-invalid-parameters"


class _Annotations:
    def __init__(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)


class _Server:
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, *, annotations: Any = None):
        def decorator(function):
            self.tools[function.__name__] = function
            return function

        return decorator


def _error_response(tool: str, exc: BaseException, *, read_only: bool) -> dict[str, Any]:
    return {
        "ok": False,
        "tool": tool,
        "readOnly": read_only,
        "error": {"code": getattr(exc, "code", "error"), "message": str(exc)},
    }


class _FakeService:
    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.payload = payload or {
            "readOnly": True,
            "worldKey": CONTEXT_ID,
            "pieInstanceId": 0,
            "targetKind": "object",
            "objectPath": PAYLOAD_PATH,
            "outerPath": COMPONENT_PATH,
            "properties": [],
        }
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call_tool(self, tool_name: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((tool_name, dict(params or {})))
        return dict(self.payload)


class PieObjectNavigationSurfaceTests(unittest.TestCase):
    def test_p3_adds_no_new_tool(self) -> None:
        for tool, method in (
            (LIST_TOOL, "editor.listPieObjectProperties"),
            (READ_TOOL, "editor.readPieObjectProperties"),
        ):
            with self.subTest(tool=tool):
                definition = TOOL_DEFINITIONS_BY_NAME[tool]
                self.assertEqual(definition.group, "live-read")
                self.assertTrue(definition.read_only)
                self.assertEqual(LIVE_EDITOR_METHODS[tool], method)
                self.assertIn(tool, tool_names_for_mode(live_editor_enabled=True))
                # P1: the per-verb tool stays the implementation but is no longer
                # a registered name; ue_pie(action=...) is the exposed surface.
                self.assertNotIn(tool, PURE_LIVE_READ_TOOL_NAMES)
                self.assertEqual(PURE_LIVE_PIE_TOOL_NAMES, ("ue_pie",))

    def test_capabilities_document_object_navigation(self) -> None:
        capabilities = build_pure_live_capabilities(
            server_name="ue-agent-kit",
            server_version="0.0.0-test",
            live_status={"state": "available"},
        )
        block = capabilities["liveEditor"]["pieObjectPropertyRead"]
        target = block["objectPathTarget"]
        self.assertTrue(target["accepted"])
        self.assertEqual(target["scope"], "any-UObject-inside-the-exact-pie-world")
        self.assertIn("objectPath", target["identityFields"])
        self.assertIn("outerPath", target["identityFields"])
        self.assertEqual(target["notFoundCode"], "pie-object-not-found")
        self.assertEqual(target["crossWorldCode"], "pie-world-stale")
        self.assertIn("actorName", target["mutuallyExclusiveWith"])
        self.assertIn("componentName", target["mutuallyExclusiveWith"])
        self.assertIn("object", block["targetKinds"])

        navigation = block["objectReferenceNavigation"]
        self.assertTrue(navigation["available"])
        self.assertFalse(navigation["autoRecursion"])
        self.assertFalse(navigation["loadsPackages"])
        self.assertEqual(navigation["crossWorldPolicy"], "flagged-refused-when-followed")
        for field in ("path", "class", "worldKey", "pieInstanceId", "outerPath", "crossWorld"):
            self.assertIn(field, navigation["fields"])
        self.assertIn("propertiesReadable", navigation["fields"])
        self.assertIn("pie-object-not-found", block["errorCodes"])


class PieObjectNavigationParameterTests(unittest.TestCase):
    @staticmethod
    def _normalize(tool: str, params: dict[str, Any]) -> dict[str, Any]:
        return LiveEditorBridgeService._normalize_tool_params(tool, params)

    def test_object_path_is_a_complete_target(self) -> None:
        normalized = self._normalize(
            READ_TOOL,
            {
                "pieInstanceId": 0,
                "objectPath": PAYLOAD_PATH,
                "propertyNames": ["Label"],
            },
        )
        self.assertEqual(normalized["objectPath"], PAYLOAD_PATH)
        self.assertEqual(normalized["pieInstanceId"], 0)
        for field in ("actorPath", "actorGuid", "actorName", "actorLabel", "componentPath"):
            self.assertNotIn(field, normalized)

    def test_object_path_keeps_the_expected_world(self) -> None:
        normalized = self._normalize(
            LIST_TOOL,
            {
                "pieInstanceId": 1,
                "objectPath": PAYLOAD_PATH,
                "worldPath": "/Game/X.X:PersistentLevel",
            },
        )
        self.assertEqual(normalized["objectPath"], PAYLOAD_PATH)
        self.assertEqual(normalized["worldPath"], "/Game/X.X:PersistentLevel")

    def test_object_path_excludes_actor_identities(self) -> None:
        for field, value in (
            ("actorPath", ACTOR_PATH),
            ("actorName", "PieP3Actor"),
            ("actorGuid", "0123456789abcdef0123456789abcdef"),
            ("actorLabel", "PieP3Actor"),
        ):
            with self.subTest(field=field):
                with self.assertRaises(LiveEditorError) as raised:
                    self._normalize(
                        READ_TOOL,
                        {
                            "pieInstanceId": 0,
                            "objectPath": PAYLOAD_PATH,
                            "propertyNames": ["Label"],
                            field: value,
                        },
                    )
                self.assertEqual(raised.exception.code, INVALID)

    def test_object_path_excludes_component_identities(self) -> None:
        for field, value in (
            ("componentPath", COMPONENT_PATH),
            ("componentName", "NavComponent"),
            ("componentClassPath", "/Script/Engine.ActorComponent"),
        ):
            with self.subTest(field=field):
                with self.assertRaises(LiveEditorError) as raised:
                    self._normalize(
                        LIST_TOOL,
                        {"pieInstanceId": 0, "objectPath": PAYLOAD_PATH, field: value},
                    )
                self.assertEqual(raised.exception.code, INVALID)

    def test_malformed_object_paths_are_rejected(self) -> None:
        for value in ("Game/X.X", "/Game/X", "", "/Game/X.X..Y", "/Game/X.X."):
            with self.subTest(value=value):
                with self.assertRaises(LiveEditorError) as raised:
                    self._normalize(
                        LIST_TOOL,
                        {"pieInstanceId": 0, "objectPath": value},
                    )
                self.assertEqual(raised.exception.code, INVALID)

    def test_object_path_still_requires_an_exact_instance(self) -> None:
        with self.assertRaises(LiveEditorError) as raised:
            self._normalize(LIST_TOOL, {"objectPath": PAYLOAD_PATH})
        self.assertEqual(raised.exception.code, INVALID)


class PieObjectNavigationToolTests(unittest.TestCase):
    def _tool(self, service: _FakeService, tool: str):
        server = _Server()
        register_live_read_tools(
            server=server,
            live_editor_service=service,
            index_service=None,
            audit_report_root=None,
            read_annotations=_Annotations(readOnlyHint=True),
            tool_annotations_type=_Annotations,
            error_response=_error_response,
        )
        return server.tools[tool]

    def test_read_tool_forwards_object_path(self) -> None:
        service = _FakeService()
        out = self._tool(service, READ_TOOL)(
            pie_instance_id=0,
            property_names=["Label", "Count"],
            object_path=PAYLOAD_PATH,
        )
        self.assertEqual(out["objectPath"], PAYLOAD_PATH)
        tool_name, params = service.calls[0]
        self.assertEqual(tool_name, READ_TOOL)
        self.assertEqual(params["objectPath"], PAYLOAD_PATH)
        self.assertNotIn("actorPath", params)
        self.assertNotIn("componentPath", params)

    def test_list_tool_forwards_object_path(self) -> None:
        service = _FakeService()
        self._tool(service, LIST_TOOL)(pie_instance_id=2, object_path=COMPONENT_PATH)
        _tool_name, params = service.calls[0]
        self.assertEqual(params["objectPath"], COMPONENT_PATH)
        self.assertEqual(params["pieInstanceId"], 2)
        self.assertNotIn("actorPath", params)

    def test_bridge_refusals_pass_through(self) -> None:
        class _Failing(_FakeService):
            def call_tool(self, tool_name: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
                raise LiveEditorError("pie-object-not-found", "No object has that path.")

        out = self._tool(_Failing(), READ_TOOL)(
            pie_instance_id=0,
            property_names=["Label"],
            object_path=PAYLOAD_PATH,
        )
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "pie-object-not-found")
        self.assertTrue(out["readOnly"])


if __name__ == "__main__":
    unittest.main()
