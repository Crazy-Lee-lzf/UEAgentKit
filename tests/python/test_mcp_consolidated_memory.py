"""P1: ``ue_memory`` / ``ue_memory_write`` must dispatch, not re-implement.

The facades call the historical per-verb callables, so the contract worth
testing is the dispatch table: the right handler receives exactly the arguments
the caller supplied (omitted ones keep the handler's own default), and
incomplete calls never reach a handler.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.mcp_consolidated_tools import (  # noqa: E402
    MEMORY_KNOWLEDGE_ACTIONS,
    MEMORY_READ_ACTIONS,
    MEMORY_WORK_ACTIONS,
    MEMORY_WRITE_ACTIONS,
    register_memory_read_tool,
    register_memory_write_tool,
)

HANDLER_NAMES = (
    [f"ue_memory_{name}" for name in MEMORY_READ_ACTIONS]
    + [handler for handler, _ in MEMORY_WRITE_ACTIONS.values()]
    + ["ue_memory_update_knowledge", "ue_memory_update_work"]
)


class _RecordingServer:
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, *args: Any, **kwargs: Any) -> Any:
        def decorator(func: Any) -> Any:
            self.tools[func.__name__] = func
            return func

        return decorator


def _error_response(tool: str, exc: Exception, read_only: bool = False) -> dict[str, Any]:
    return {"tool": tool, "ok": False, "error": str(exc), "readOnly": read_only}


def _surface() -> tuple[_RecordingServer, list[tuple[str, dict[str, Any]]]]:
    server = _RecordingServer()
    calls: list[tuple[str, dict[str, Any]]] = []
    handlers: dict[str, Any] = {}
    for name in HANDLER_NAMES:
        def handler(_name: str = name, **kwargs: Any) -> dict[str, Any]:
            calls.append((_name, kwargs))
            return {"ok": True, "tool": _name}

        handlers[name] = handler
    register_memory_read_tool(
        server=server,
        handlers=handlers,
        read_annotations=None,
        error_response=_error_response,
    )
    register_memory_write_tool(
        server=server,
        handlers=handlers,
        planning_annotations=None,
        error_response=_error_response,
    )
    return server, calls


class MemoryReadDispatchTests(unittest.TestCase):
    def test_every_read_action_reaches_its_handler(self) -> None:
        server, calls = _surface()
        cases: dict[str, dict[str, Any]] = {
            "search": {"query": "背包", "limit": 5, "record_types": ["projectRule"]},
            "get": {"record_id": "mem_1"},
            "list_l0_events": {"event_kinds": ["live_write"], "limit": 3},
            "get_l0_event": {"event_id": "l0_1"},
            "get_context": {"query": "q", "detail_level": 2, "max_nodes": 4},
            "expand_node": {"path": "/project", "depth": 2},
            "get_evidence": {"record_id": "mem_1"},
        }
        self.assertEqual(set(cases), set(MEMORY_READ_ACTIONS))
        for action, kwargs in cases.items():
            with self.subTest(action=action):
                before = len(calls)
                result = server.tools["ue_memory"](action=action, **kwargs)
                self.assertTrue(result["ok"])
                self.assertEqual(len(calls), before + 1)
                name, forwarded = calls[-1]
                self.assertEqual(name, f"ue_memory_{action}")
                self.assertEqual(forwarded, kwargs)

    def test_omitted_arguments_keep_the_handler_default(self) -> None:
        server, calls = _surface()
        server.tools["ue_memory"](action="search", query="q")
        self.assertEqual(calls[-1], ("ue_memory_search", {"query": "q"}))
        server.tools["ue_memory"](action="list_l0_events", limit=0)
        self.assertEqual(calls[-1], ("ue_memory_list_l0_events", {"limit": 0}))

    def test_required_arguments_are_checked_before_dispatch(self) -> None:
        server, calls = _surface()
        for action, fragment in (
            ("search", "query"),
            ("get", "record_id"),
            ("expand_node", "path"),
            ("get_l0_event", "event_id"),
            ("get_evidence", "record_id"),
        ):
            with self.subTest(action=action):
                result = server.tools["ue_memory"](action=action)
                self.assertFalse(result["ok"])
                self.assertIn(fragment, result["error"])
        self.assertEqual(calls, [])

    def test_unknown_action_reports_without_dispatch(self) -> None:
        server, calls = _surface()
        result = server.tools["ue_memory"](action="forget")
        self.assertFalse(result["ok"])
        self.assertIn("unknown action", result["error"])
        self.assertEqual(calls, [])


class MemoryWriteDispatchTests(unittest.TestCase):
    def test_every_record_action_reaches_its_handler(self) -> None:
        server, calls = _surface()
        cases: dict[str, dict[str, Any]] = {
            "add_rule": {"subject_key": "rule:1", "title": "t", "body": "b"},
            "record_finding": {
                "record_type": "projectFact",
                "subject_key": "fact:1",
                "title": "t",
                "body": "b",
                "confidence": 0.7,
            },
            "record_task": {
                "task_key": "task:1",
                "title": "t",
                "conclusion": "c",
                "outcome": "succeeded",
                "patch_ref": "patch:1",
                "backup_manifest_ref": "backup-manifest:1",
                "validation_evidence_ref": "validation-evidence:1",
                "revision_set": [{"assetPath": "/Game/A", "revision": "sha256:1"}],
            },
            "mark_superseded": {"record_id": "mem_1", "replacement_record_id": "mem_2", "reason": "dup"},
            "validate": {},
        }
        self.assertEqual(set(cases), set(MEMORY_WRITE_ACTIONS))
        for action, kwargs in cases.items():
            with self.subTest(action=action):
                before = len(calls)
                result = server.tools["ue_memory_write"](action=action, **kwargs)
                self.assertTrue(result["ok"])
                self.assertEqual(len(calls), before + 1)
                name, forwarded = calls[-1]
                self.assertEqual(name, MEMORY_WRITE_ACTIONS[action][0])
                self.assertEqual(forwarded, kwargs)

    def test_knowledge_and_work_sub_actions_forward_action_and_payload(self) -> None:
        server, calls = _surface()
        for action in MEMORY_KNOWLEDGE_ACTIONS:
            with self.subTest(action=action):
                payload = {"nodeId": "node_1"}
                server.tools["ue_memory_write"](action=action, payload=payload)
                self.assertEqual(calls[-1], ("ue_memory_update_knowledge", {"action": action, "payload": payload}))
        for action in MEMORY_WORK_ACTIONS:
            with self.subTest(action=action):
                payload = {"workItemId": "work_1"}
                server.tools["ue_memory_write"](action=action, payload=payload)
                self.assertEqual(calls[-1], ("ue_memory_update_work", {"action": action, "payload": payload}))

    def test_missing_required_arguments_are_checked_before_dispatch(self) -> None:
        server, calls = _surface()
        for action, fragment in (
            ("add_rule", "subject_key"),
            ("record_finding", "record_type"),
            ("record_task", "task_key"),
            ("mark_superseded", "record_id"),
        ):
            with self.subTest(action=action):
                result = server.tools["ue_memory_write"](action=action)
                self.assertFalse(result["ok"])
                self.assertIn(fragment, result["error"])
        self.assertEqual(calls, [])

    def test_unknown_action_reports_without_dispatch(self) -> None:
        server, calls = _surface()
        result = server.tools["ue_memory_write"](action="rewrite_history")
        self.assertFalse(result["ok"])
        self.assertIn("unknown action", result["error"])
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
