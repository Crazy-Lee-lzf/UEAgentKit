"""PIE runtime inspector P2: the reflective property read surface.

Covers the Python half of P2: registry/registration, pure-live exposure,
parameter normalization (exact instance, one Actor identity, at most one owned
Component, bounded container paging, 1..16 distinct property names) and a
pass-through call against a fake bridge service.
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

from ue_agent_kit.editor_bridge import (  # noqa: E402
    _CAPABILITY_GATED_TOOLS,
    LiveEditorBridgeService,
    LiveEditorError,
)
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
ACTOR_PATH = "/Game/X.X:PersistentLevel.PieP2Actor"
COMPONENT_PATH = "/Game/X.X:PersistentLevel.PieP2Actor.Mesh"
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
            "worldKey": "pie-0-listenServer",
            "pieInstanceId": 0,
            "objectPath": ACTOR_PATH,
            "properties": [],
        }
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call_tool(self, tool_name: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((tool_name, dict(params or {})))
        return dict(self.payload)


class PieObjectPropertyRegistryTests(unittest.TestCase):
    def test_registry_entries_are_live_read_only(self) -> None:
        for tool, method in (
            (LIST_TOOL, "editor.listPieObjectProperties"),
            (READ_TOOL, "editor.readPieObjectProperties"),
        ):
            with self.subTest(tool=tool):
                self.assertIn(tool, TOOL_DEFINITIONS_BY_NAME)
                definition = TOOL_DEFINITIONS_BY_NAME[tool]
                self.assertEqual(definition.group, "live-read")
                self.assertTrue(definition.read_only)
                self.assertFalse(definition.destructive)
                self.assertEqual(definition.live_method, method)
                self.assertEqual(LIVE_EDITOR_METHODS[tool], method)
                self.assertEqual(_CAPABILITY_GATED_TOOLS[tool], method)
                self.assertIn(tool, tool_names_for_mode(live_editor_enabled=True))
                self.assertNotIn(tool, tool_names_for_mode())
                self.assertNotIn(tool, tool_names_for_mode(workflow_enabled=True))
                self.assertIn(tool, PURE_LIVE_READ_TOOL_NAMES)
                self.assertIn(tool, PURE_LIVE_PIE_TOOL_NAMES)

    def test_capability_block_documents_the_contract(self) -> None:
        capabilities = build_pure_live_capabilities(
            server_name="ue-agent-kit",
            server_version="0.0.0-test",
            live_status={"state": "available"},
        )
        block = capabilities["liveEditor"]["pieObjectPropertyRead"]
        self.assertTrue(block["available"])
        self.assertTrue(block["readOnly"])
        self.assertTrue(block["reflectionDriven"])
        self.assertEqual(block["visibilityGate"], "none")
        self.assertIn("editor.listPieObjectProperties", block["bridgeMethods"])
        self.assertIn("editor.readPieObjectProperties", block["bridgeMethods"])
        self.assertEqual(block["maxReadProperties"], 16)
        self.assertEqual(block["maxContainerLimit"], 200)
        self.assertEqual(block["emittedStatuses"], ["ok", "unsupported-type", "unknown"])
        self.assertIn("unsupported-type", block["perPropertyStatus"])
        self.assertIn("not-exposed", block["perPropertyStatus"])
        self.assertIn("text", block["refusedTypeKinds"])
        self.assertIn("bitfield", block["refusedTypeKinds"])
        self.assertIn("struct", block["supportedTypeKinds"])


class PieObjectPropertyParameterTests(unittest.TestCase):
    @staticmethod
    def _normalize(tool: str, params: dict[str, Any]) -> dict[str, Any]:
        return LiveEditorBridgeService._normalize_tool_params(tool, params)

    def test_list_defaults_and_optional_fields(self) -> None:
        normalized = self._normalize(LIST_TOOL, {"pieInstanceId": 0, "actorPath": ACTOR_PATH})
        self.assertEqual(normalized["pieInstanceId"], 0)
        self.assertEqual(normalized["actorPath"], ACTOR_PATH)
        self.assertEqual(normalized["maxProperties"], 128)
        self.assertNotIn("nameContains", normalized)
        self.assertNotIn("componentPath", normalized)

    def test_list_forwards_filters_and_component_identity(self) -> None:
        normalized = self._normalize(
            LIST_TOOL,
            {
                "pieInstanceId": 3,
                "actorName": "PieP2Actor",
                "componentPath": COMPONENT_PATH,
                "componentClassPath": "/Script/Engine.StaticMeshComponent",
                "nameContains": "Vector",
                "maxProperties": 512,
                "worldPath": "/Game/X.X:PersistentLevel",
            },
        )
        self.assertEqual(normalized["pieInstanceId"], 3)
        self.assertEqual(normalized["actorName"], "PieP2Actor")
        self.assertEqual(normalized["componentPath"], COMPONENT_PATH)
        self.assertEqual(
            normalized["componentClassPath"], "/Script/Engine.StaticMeshComponent")
        self.assertEqual(normalized["nameContains"], "Vector")
        self.assertEqual(normalized["maxProperties"], 512)

    def test_read_defaults(self) -> None:
        normalized = self._normalize(
            READ_TOOL,
            {"pieInstanceId": 0, "actorPath": ACTOR_PATH, "propertyNames": ["Health"]},
        )
        self.assertEqual(normalized["offset"], 0)
        self.assertEqual(normalized["limit"], 50)
        self.assertEqual(normalized["maxStringChars"], 256)
        self.assertEqual(normalized["propertyNames"], ["Health"])
        self.assertNotIn("componentPath", normalized)

    def test_exactly_one_actor_identity_is_required(self) -> None:
        with self.assertRaises(LiveEditorError) as raised:
            self._normalize(READ_TOOL, {"pieInstanceId": 0, "propertyNames": ["Health"]})
        self.assertEqual(raised.exception.code, INVALID)
        with self.assertRaises(LiveEditorError) as raised:
            self._normalize(
                LIST_TOOL,
                {"pieInstanceId": 0, "actorPath": ACTOR_PATH, "actorName": "Other"},
            )
        self.assertEqual(raised.exception.code, INVALID)

    def test_exact_instance_is_required(self) -> None:
        with self.assertRaises(LiveEditorError) as raised:
            self._normalize(LIST_TOOL, {"actorPath": ACTOR_PATH})
        self.assertEqual(raised.exception.code, INVALID)
        with self.assertRaises(LiveEditorError) as raised:
            self._normalize(LIST_TOOL, {"pieInstanceId": -1, "actorPath": ACTOR_PATH})
        self.assertEqual(raised.exception.code, INVALID)

    def test_at_most_one_component_identity(self) -> None:
        with self.assertRaises(LiveEditorError) as raised:
            self._normalize(
                LIST_TOOL,
                {
                    "pieInstanceId": 0,
                    "actorPath": ACTOR_PATH,
                    "componentPath": COMPONENT_PATH,
                    "componentName": "Mesh",
                },
            )
        self.assertEqual(raised.exception.code, INVALID)

    def test_component_class_path_requires_an_identity(self) -> None:
        with self.assertRaises(LiveEditorError) as raised:
            self._normalize(
                LIST_TOOL,
                {
                    "pieInstanceId": 0,
                    "actorPath": ACTOR_PATH,
                    "componentClassPath": "/Script/Engine.StaticMeshComponent",
                },
            )
        self.assertEqual(raised.exception.code, INVALID)

    def test_unknown_parameter_is_rejected(self) -> None:
        for tool in (LIST_TOOL, READ_TOOL):
            with self.subTest(tool=tool):
                with self.assertRaises(LiveEditorError) as raised:
                    self._normalize(
                        tool,
                        {
                            "pieInstanceId": 0,
                            "actorPath": ACTOR_PATH,
                            "recursive": True,
                        },
                    )
                self.assertEqual(raised.exception.code, INVALID)

    def test_max_properties_is_bounded(self) -> None:
        for value in (0, 513, -1):
            with self.subTest(value=value):
                with self.assertRaises(LiveEditorError) as raised:
                    self._normalize(
                        LIST_TOOL,
                        {
                            "pieInstanceId": 0,
                            "actorPath": ACTOR_PATH,
                            "maxProperties": value,
                        },
                    )
                self.assertEqual(raised.exception.code, INVALID)

    def test_empty_property_names_is_rejected(self) -> None:
        for names in ([], None):
            with self.subTest(names=names):
                with self.assertRaises(LiveEditorError) as raised:
                    self._normalize(
                        READ_TOOL,
                        {
                            "pieInstanceId": 0,
                            "actorPath": ACTOR_PATH,
                            "propertyNames": names,
                        },
                    )
                self.assertEqual(raised.exception.code, INVALID)

    def test_more_than_sixteen_distinct_names_is_rejected(self) -> None:
        with self.assertRaises(LiveEditorError) as raised:
            self._normalize(
                READ_TOOL,
                {
                    "pieInstanceId": 0,
                    "actorPath": ACTOR_PATH,
                    "propertyNames": [f"Prop{index}" for index in range(17)],
                },
            )
        self.assertEqual(raised.exception.code, INVALID)

    def test_sixteen_distinct_names_with_duplicates_are_accepted(self) -> None:
        names = [f"Prop{index}" for index in range(16)] + ["Prop0", "Prop0"]
        normalized = self._normalize(
            READ_TOOL,
            {"pieInstanceId": 0, "actorPath": ACTOR_PATH, "propertyNames": names},
        )
        # Validation collapses duplicates; the original list is passed through so
        # the Bridge can report exactly which names it collapsed.
        self.assertEqual(normalized["propertyNames"], names)

    def test_non_identifier_property_names_are_rejected(self) -> None:
        for name in ("VectorValue.X", "Items[0]", "1Bad", ""):
            with self.subTest(name=name):
                with self.assertRaises(LiveEditorError) as raised:
                    self._normalize(
                        READ_TOOL,
                        {
                            "pieInstanceId": 0,
                            "actorPath": ACTOR_PATH,
                            "propertyNames": [name],
                        },
                    )
                self.assertEqual(raised.exception.code, INVALID)

    def test_over_long_property_name_is_rejected(self) -> None:
        with self.assertRaises(LiveEditorError) as raised:
            self._normalize(
                READ_TOOL,
                {
                    "pieInstanceId": 0,
                    "actorPath": ACTOR_PATH,
                    "propertyNames": ["A" + "b" * 128],
                },
            )
        self.assertEqual(raised.exception.code, INVALID)

    def test_offset_and_limit_are_bounded(self) -> None:
        for params in (
            {"offset": -1},
            {"limit": 0},
            {"limit": 201},
        ):
            with self.subTest(params=params):
                payload = {
                    "pieInstanceId": 0,
                    "actorPath": ACTOR_PATH,
                    "propertyNames": ["Health"],
                }
                payload.update(params)
                with self.assertRaises(LiveEditorError) as raised:
                    self._normalize(READ_TOOL, payload)
                self.assertEqual(raised.exception.code, INVALID)

    def test_max_string_chars_is_bounded(self) -> None:
        for value in (31, 513):
            with self.subTest(value=value):
                with self.assertRaises(LiveEditorError) as raised:
                    self._normalize(
                        READ_TOOL,
                        {
                            "pieInstanceId": 0,
                            "actorPath": ACTOR_PATH,
                            "propertyNames": ["Health"],
                            "maxStringChars": value,
                        },
                    )
                self.assertEqual(raised.exception.code, INVALID)


class PieObjectPropertyToolTests(unittest.TestCase):
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
        self.assertIn(tool, server.tools)
        return server.tools[tool]

    def test_list_tool_forwards_the_exact_request(self) -> None:
        service = _FakeService()
        out = self._tool(service, LIST_TOOL)(
            pie_instance_id=0,
            actor_name="PieP2Actor",
            component_name="Mesh",
            name_contains="Vector",
            max_properties=16,
        )
        self.assertEqual(out["worldKey"], "pie-0-listenServer")
        self.assertEqual(len(service.calls), 1)
        tool_name, params = service.calls[0]
        self.assertEqual(tool_name, LIST_TOOL)
        self.assertEqual(params["pieInstanceId"], 0)
        self.assertEqual(params["actorName"], "PieP2Actor")
        self.assertEqual(params["componentName"], "Mesh")
        self.assertEqual(params["nameContains"], "Vector")
        self.assertEqual(params["maxProperties"], 16)
        self.assertNotIn("actorPath", params)
        self.assertNotIn("componentPath", params)

    def test_read_tool_forwards_the_exact_request(self) -> None:
        service = _FakeService()
        self._tool(service, READ_TOOL)(
            pie_instance_id=2,
            property_names=["Health", "IntArray"],
            actor_path=ACTOR_PATH,
            offset=3,
            limit=2,
            max_string_chars=64,
        )
        tool_name, params = service.calls[0]
        self.assertEqual(tool_name, READ_TOOL)
        self.assertEqual(params["pieInstanceId"], 2)
        self.assertEqual(params["propertyNames"], ["Health", "IntArray"])
        self.assertEqual(params["actorPath"], ACTOR_PATH)
        self.assertEqual(params["offset"], 3)
        self.assertEqual(params["limit"], 2)
        self.assertEqual(params["maxStringChars"], 64)

    def test_tools_return_bridge_errors_unchanged(self) -> None:
        class _Failing(_FakeService):
            def call_tool(self, tool_name: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
                raise LiveEditorError("pie-actor-not-found", "No Actor matches.")

        for tool, kwargs in (
            (LIST_TOOL, {"pie_instance_id": 0, "actor_path": ACTOR_PATH}),
            (
                READ_TOOL,
                {"pie_instance_id": 0, "actor_path": ACTOR_PATH, "property_names": ["Health"]},
            ),
        ):
            with self.subTest(tool=tool):
                out = self._tool(_Failing(), tool)(**kwargs)
                self.assertFalse(out["ok"])
                self.assertEqual(out["error"]["code"], "pie-actor-not-found")
                self.assertTrue(out["readOnly"])
                self.assertEqual(out["tool"], tool)


if __name__ == "__main__":
    unittest.main()
