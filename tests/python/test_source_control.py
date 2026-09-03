from __future__ import annotations

import marshal
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.cli import build_parser, run as run_cli  # noqa: E402
from ue_agent_kit.source_control import (  # noqa: E402
    MAX_FILES_PER_REQUEST,
    MAX_PATH_CHARS,
    P4CommandRunner,
    P4SourceControlService,
    SourceControlCommandError,
    SourceControlProhibitedOperationError,
    SourceControlValidationError,
    _P4CommandResult,
    _decode_marshal_records,
    _is_error_record,
    resolve_input_paths,
)


def _stat_record(**fields: Any) -> dict[str, Any]:
    record: dict[str, Any] = {"code": "stat"}
    record.update(fields)
    return record


def _error_record(text: str) -> dict[str, Any]:
    return {"code": "error", "data": text, "severity": 2, "generic": 17}


class FakeP4Runner:
    """Deterministic in-process P4 command fixture.

    It is not a generic shell: only the C1/C2 allowlisted commands exist, every
    invocation is validated with the production argv rules, and every received
    argv is appended to ``calls`` so tests can assert exact command lines.
    """

    executable = "fake-p4"

    def __init__(self, world: dict[str, Any]) -> None:
        self.world = world
        self.calls: list[list[str]] = []
        self.real_validator = P4CommandRunner(p4_executable="p4", timeout_seconds=1.0)
        self._files = {
            key.replace("\\", "/").lower(): value for key, value in world["files"].items()
        }
        self._user = world.get("userName", "alice")
        self._client = world.get("clientName", "alice_ws")

    def _down_on(self) -> tuple[str, ...]:
        return tuple(self.world.get("downOn", ()))

    def _sleep_on(self) -> str:
        return str(self.world.get("sleepOn", ""))

    def _entry(self, local_path: str) -> dict[str, Any] | None:
        return self._files.get(local_path.replace("\\", "/").lower())

    def _find_entries(self, paths: list[str]) -> list[dict[str, Any] | None]:
        return [self._entry(path) for path in paths]

    def run(self, argv: list[str]) -> _P4CommandResult:
        tokens = [str(token) for token in argv]
        # Production argv validation runs first: prohibited ops and out-of-schema
        # option tokens raise exactly as the real runner would.
        self.real_validator._validate_argv(tokens)
        self.calls.append(tokens)
        command = tokens[0]
        if command in self._down_on():
            return _P4CommandResult(exit_code=1, records=(), stderr_text="connect failed", duration_ms=1.0)
        if command == self._sleep_on():
            import time

            time.sleep(2.5)
            return _P4CommandResult(exit_code=0, records=(), stderr_text="", duration_ms=2500.0)

        if command == "info":
            return _P4CommandResult(
                exit_code=0,
                records=(
                    _stat_record(
                        userName=self._user,
                        clientName=self._client,
                        serverAddress="fake:1666",
                        serverVersion="P4D/FAKE/1.0",
                    ),
                ),
                stderr_text="",
                duration_ms=1.0,
            )

        if command == "fstat":
            records: list[dict[str, Any]] = []
            for path, entry in zip(tokens[1:], self._find_entries(tokens[1:])):
                if entry is None:
                    records.append(_error_record(f"{path} - no such file(s).\n"))
                    continue
                record = _stat_record(
                    depotFile=entry["depotFile"],
                    clientFile=path,
                    headAction=entry.get("headAction", "add"),
                    headType=entry.get("type", "text"),
                    headRev=entry.get("headRev", "1"),
                    headChange=entry.get("headChange", "22"),
                    headModTime="0",
                )
                if entry.get("haveRev") is not None:
                    record["haveRev"] = entry["haveRev"]
                opened_by = entry.get("openedBy")
                opened_client = entry.get("client", self._client)
                if opened_by == self._user and opened_client == self._client:
                    record["action"] = entry.get("action", "edit")
                    record["change"] = entry.get("change", "default")
                    record["actionOwner"] = self._user
                    record["type"] = entry.get("type", "text")
                    record["workRev"] = entry.get("haveRev", "1")
                records.append(record)
            return _P4CommandResult(exit_code=0, records=tuple(records), stderr_text="", duration_ms=1.0)

        if command == "opened":
            records = []
            paths = tokens[2:] if len(tokens) >= 2 and tokens[1] == "-a" else tokens[1:]
            for entry in self._find_entries(paths):
                if entry is None:
                    continue
                opened_by = entry.get("openedBy")
                if not opened_by:
                    continue
                locked_marker = "yes" if entry.get("lockedBy") == opened_by else ""
                records.append(
                    _stat_record(
                        depotFile=entry["depotFile"],
                        clientFile=entry["depotFile"],
                        rev=entry.get("haveRev", "1"),
                        haveRev=entry.get("haveRev", "1"),
                        action=entry.get("action", "edit"),
                        change=entry.get("change", "default"),
                        type=entry.get("type", "text"),
                        user=opened_by,
                        client=entry.get("client", self._client),
                        locked=locked_marker,
                    )
                )
            return _P4CommandResult(exit_code=0, records=tuple(records), stderr_text="", duration_ms=1.0)

        if command == "diff":
            changed: list[dict[str, Any]] = []
            for path, entry in zip(tokens[2:], self._find_entries(tokens[2:])):
                if entry is None:
                    changed.append(_error_record(f"{path} - file(s) not on client.\n"))
                    continue
                if entry.get("localChanged"):
                    changed.append(_stat_record(depotFile=entry["depotFile"]))
            return _P4CommandResult(exit_code=0, records=tuple(changed), stderr_text="", duration_ms=1.0)

        if command == "edit":
            records = []
            for path, entry in zip(tokens[1:], self._find_entries(tokens[1:])):
                if entry is None:
                    records.append(_error_record(f"{path} - file(s) not on client.\n"))
                    continue
                if entry.get("editBlocked"):
                    records.append(_error_record(f"{path} - edit blocked by fake fixture.\n"))
                    continue
                locked_by = entry.get("lockedBy")
                if locked_by and ("+l" in entry.get("type", "") or entry.get("exclusive")):
                    records.append(
                        _error_record(
                            f"{path} - file(s) locked by {locked_by}; cannot open for edit.\n"
                        )
                    )
                    continue
                entry["openedBy"] = self._user
                entry["action"] = "edit"
                entry["change"] = "default"
                entry["client"] = self._client
                real_path = Path(path)
                if real_path.exists():
                    os.chmod(real_path, stat.S_IWUSR | stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
                records.append(
                    _stat_record(
                        depotFile=entry["depotFile"],
                        clientFile=path,
                        action="edit",
                        change="default",
                        type=entry.get("type", "text"),
                        user=self._user,
                        client=self._client,
                    )
                )
            return _P4CommandResult(exit_code=0, records=tuple(records), stderr_text="", duration_ms=1.0)

        if command == "sync":
            records = []
            for path, entry in zip(tokens[1:], self._find_entries(tokens[1:])):
                if entry is None:
                    records.append(_error_record(f"{path} - file(s) not on client.\n"))
                    continue
                if entry.get("lockedBy") or entry.get("localChanged") or entry.get("syncBlocked"):
                    records.append(
                        _error_record(f"{path} - sync blocked by fake fixture precondition.\n")
                    )
                    continue
                entry["haveRev"] = entry["headRev"]
                real_path = Path(path)
                if real_path.exists():
                    os.chmod(real_path, stat.S_IREAD)
                records.append(
                    _stat_record(
                        depotFile=entry["depotFile"],
                        clientFile=path,
                        action="updated",
                        rev=entry["headRev"],
                        haveRev=entry["headRev"],
                    )
                )
            return _P4CommandResult(exit_code=0, records=tuple(records), stderr_text="", duration_ms=1.0)

        raise SourceControlProhibitedOperationError(f"Fake P4 has no generic behavior for {command}")


def _readonly(path: Path) -> None:
    os.chmod(path, stat.S_IREAD)


def _writable_mode(path: Path) -> bool:
    return bool(path.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


class SourceControlServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="ueak_sc_"))
        self.addCleanup(_remove_tree, self.temp)
        self.clean_file = self.temp / "managed.py"
        self.clean_file.write_text("print(1)\n", encoding="utf-8")
        _readonly(self.clean_file)
        self.other_file = self.temp / "other_open.py"
        self.other_file.write_text("print(2)\n", encoding="utf-8")
        _readonly(self.other_file)
        self.locked_file = self.temp / "locked.bin"
        self.locked_file.write_bytes(b"\x00\x01")
        _readonly(self.locked_file)
        self.behind_file = self.temp / "behind.py"
        self.behind_file.write_text("print(3)\n", encoding="utf-8")
        _readonly(self.behind_file)
        self.unmapped_file = self.temp / "unmapped.txt"
        self.unmapped_file.write_text("local only\n", encoding="utf-8")

        f = self.temp.as_posix()
        self.world = {
            "userName": "alice",
            "clientName": "alice_ws",
            "files": {
                f"{f}/managed.py": {
                    "depotFile": "//depot/Content/managed.py",
                    "headRev": "1",
                    "haveRev": "1",
                    "type": "text",
                    "headAction": "add",
                },
                f"{f}/other_open.py": {
                    "depotFile": "//depot/Content/other_open.py",
                    "headRev": "1",
                    "haveRev": "1",
                    "type": "text",
                    "headAction": "add",
                    "openedBy": "bob",
                    "action": "edit",
                    "client": "bob_ws",
                },
                f"{f}/locked.bin": {
                    "depotFile": "//depot/Content/locked.bin",
                    "headRev": "1",
                    "haveRev": "1",
                    "type": "binary+l",
                    "headAction": "add",
                    "openedBy": "bob",
                    "action": "edit",
                    "client": "bob_ws",
                    "lockedBy": "bob",
                    "exclusive": True,
                },
                f"{f}/behind.py": {
                    "depotFile": "//depot/Content/behind.py",
                    "headRev": "3",
                    "haveRev": "1",
                    "type": "text",
                    "headAction": "edit",
                },
            },
        }

    def _service(self, **kwargs: Any) -> P4SourceControlService:
        service = P4SourceControlService(**kwargs)
        service._runner = FakeP4Runner(self.world)
        return service

    # -- C1 normalization -----------------------------------------------------
    def test_clean_mapped_file_status(self) -> None:
        service = self._service()
        result = service.status([str(self.clean_file)])
        payload = result.to_payload()
        self.assertTrue(payload["provider"]["available"])
        self.assertEqual(payload["summary"]["mapped"], 1)
        state = payload["files"][0]
        self.assertTrue(state["mapped"])
        self.assertEqual(state["depotPath"], "//depot/Content/managed.py")
        self.assertEqual(state["haveRev"], "1")
        self.assertEqual(state["headRev"], "1")
        self.assertFalse(state["openedForEdit"])
        self.assertFalse(state["behindHead"])
        self.assertFalse(state["localModified"])
        self.assertFalse(state["writable"])
        self.assertTrue(state["sourceControlReady"])
        self.assertFalse(state["submitReady"])
        codes = [warning["code"] for warning in state["warnings"]]
        self.assertIn("not-opened-for-edit", codes)
        runner = service._runner
        self.assertIsInstance(runner, FakeP4Runner)
        self.assertEqual(runner.calls[0], ["info"])
        self.assertEqual(runner.calls[1], ["fstat", str(self.clean_file)])
        self.assertEqual(runner.calls[2], ["opened", "-a", str(self.clean_file)])
        self.assertTrue(any(call[0:2] == ["diff", "-se"] for call in runner.calls))
        self.assertTrue(any(call[0:2] == ["diff", "-sd"] for call in runner.calls))

    def test_unmapped_file_status_is_truthful(self) -> None:
        service = self._service()
        state = service.status([str(self.unmapped_file)]).to_payload()["files"][0]
        self.assertFalse(state["mapped"])
        self.assertTrue(state["providerAvailable"])
        self.assertEqual(state["depotPath"], "")
        self.assertTrue(state["writable"])
        self.assertTrue(state["localTestReady"])
        codes = [warning["code"] for warning in state["warnings"]]
        self.assertEqual(codes, ["not-mapped"])

    def test_other_user_ordinary_open_is_warning_only(self) -> None:
        service = self._service()
        state = service.status([str(self.other_file)]).to_payload()["files"][0]
        self.assertTrue(state["mapped"])
        self.assertFalse(state["openedByCurrentClient"])
        self.assertEqual(state["otherOpenUsers"], ["bob"])
        self.assertFalse(state["lockedByOther"])
        codes = [warning["code"] for warning in state["warnings"]]
        self.assertIn("other-user-open", codes)
        self.assertNotIn("exclusive-lock-other-user", codes)

    def test_same_user_different_client_is_not_current_checkout(self) -> None:
        same_user_other_client = self.temp / "same_user_other_client.py"
        same_user_other_client.write_text("print(6)\n", encoding="utf-8")
        _readonly(same_user_other_client)
        self.world["files"][same_user_other_client.as_posix()] = {
            "depotFile": "//depot/Content/same_user_other_client.py",
            "headRev": "1",
            "haveRev": "1",
            "type": "text",
            "headAction": "add",
            "openedBy": "alice",
            "client": "alice_other_ws",
            "action": "edit",
        }
        service = self._service()
        state = service.status([str(same_user_other_client)]).to_payload()["files"][0]
        self.assertFalse(state["openedByCurrentClient"])
        self.assertFalse(state["openedForEdit"])
        self.assertFalse(state["submitReady"])
        self.assertEqual(state["otherOpenUsers"], ["alice@alice_other_ws"])

        result = service.prepare_write([str(same_user_other_client)]).to_payload()
        self.assertTrue(any(r["action"] == "edit" and r["ok"] for r in result["receipts"]))
        post = service.status([str(same_user_other_client)]).to_payload()["files"][0]
        self.assertTrue(post["openedByCurrentClient"])
        self.assertTrue(post["submitReady"])

    def test_same_user_different_client_exclusive_lock_is_other_lock(self) -> None:
        same_user_locked = self.temp / "same_user_locked.bin"
        same_user_locked.write_bytes(b"\x00\x02")
        _readonly(same_user_locked)
        self.world["files"][same_user_locked.as_posix()] = {
            "depotFile": "//depot/Content/same_user_locked.bin",
            "headRev": "1",
            "haveRev": "1",
            "type": "binary+l",
            "headAction": "add",
            "openedBy": "alice",
            "client": "alice_other_ws",
            "lockedBy": "alice",
            "exclusive": True,
        }
        service = self._service()
        state = service.status([str(same_user_locked)]).to_payload()["files"][0]
        self.assertFalse(state["openedByCurrentClient"])
        self.assertTrue(state["lockedByOther"])
        self.assertEqual(state["otherLockUsers"], ["alice@alice_other_ws"])
        self.assertFalse(state["submitReady"])

    def test_exclusive_lock_is_strong_warning(self) -> None:
        service = self._service()
        state = service.status([str(self.locked_file)]).to_payload()["files"][0]
        self.assertEqual(state["exclusiveLockType"], "binary+l")
        self.assertTrue(state["lockedByOther"])
        self.assertEqual(state["otherLockUsers"], ["bob"])
        severities = [warning["severity"] for warning in state["warnings"]]
        self.assertIn("strong-warning", severities)
        codes = [warning["code"] for warning in state["warnings"]]
        self.assertIn("exclusive-lock-other-user", codes)

    def test_behind_head_status(self) -> None:
        service = self._service()
        state = service.status([str(self.behind_file)]).to_payload()["files"][0]
        self.assertTrue(state["behindHead"])
        self.assertEqual(state["haveRev"], "1")
        self.assertEqual(state["headRev"], "3")
        codes = [warning["code"] for warning in state["warnings"]]
        self.assertIn("behind-head", codes)

    def test_local_modified_detection(self) -> None:
        modified = self.temp / "modified.py"
        modified.write_text("changed\n", encoding="utf-8")
        _readonly(modified)
        self.world["files"][modified.as_posix()] = {
            "depotFile": "//depot/Content/modified.py",
            "headRev": "1",
            "haveRev": "1",
            "type": "text",
            "headAction": "add",
            "localChanged": True,
        }
        service = self._service()
        state = service.status([str(modified)]).to_payload()["files"][0]
        self.assertIs(state["localModified"], True)
        codes = [warning["code"] for warning in state["warnings"]]
        self.assertIn("local-differs-from-have", codes)

    def test_provider_unavailable_degrades_to_advisory(self) -> None:
        self.world["downOn"] = ["info"]
        service = self._service()
        payload = service.status([str(self.clean_file)]).to_payload()
        self.assertFalse(payload["provider"]["available"])
        state = payload["files"][0]
        self.assertFalse(state["providerAvailable"])
        self.assertTrue(state["localTestReady"])
        self.assertFalse(state["sourceControlReady"])
        codes = [warning["code"] for warning in state["warnings"]]
        self.assertIn("source-control-unavailable", codes)

    def test_provider_timeout_degrades_to_advisory(self) -> None:
        self.world["sleepOn"] = "info"
        service = P4SourceControlService(timeout_seconds=0.2)
        service._runner = FakeP4Runner(self.world)
        payload = service.status([str(self.clean_file)]).to_payload()
        self.assertFalse(payload["provider"]["available"])

    def test_status_respects_fixed_bounds(self) -> None:
        service = self._service()
        with self.assertRaises(SourceControlValidationError):
            service.status([])
        many = [str(self.clean_file)] * (MAX_FILES_PER_REQUEST + 1)
        with self.assertRaises(SourceControlValidationError):
            service.status(many)
        with self.assertRaises(SourceControlValidationError):
            service.status([str(self.clean_file) + "x" * (MAX_PATH_CHARS + 1)])
        with self.assertRaises(SourceControlValidationError):
            service.status(["//depot/Content/anything.py"])
        with self.assertRaises(SourceControlValidationError):
            service.status([str(self.clean_file) + "*.py"])
        with self.assertRaises(SourceControlValidationError):
            service.status(["-f"])

    # -- C2 assistance --------------------------------------------------------
    def test_prepare_write_checkout_success(self) -> None:
        service = self._service()
        result = service.prepare_write([str(self.clean_file)])
        receipts = result.to_payload()["receipts"]
        self.assertTrue(any(receipt["action"] == "edit" and receipt["ok"] for receipt in receipts))
        self.assertTrue(_writable_mode(self.clean_file))
        runner = service._runner
        self.assertIsInstance(runner, FakeP4Runner)
        edit_calls = [call for call in runner.calls if call[0] == "edit"]
        self.assertEqual(edit_calls, [["edit", str(self.clean_file)]])
        post = service.status([str(self.clean_file)]).to_payload()["files"][0]
        self.assertTrue(post["openedForEdit"])
        self.assertTrue(post["openedByCurrentClient"])
        self.assertTrue(post["writable"])

    def test_prepare_write_checkout_blocked_by_exclusive_lock(self) -> None:
        service = self._service()
        result = service.prepare_write([str(self.locked_file)])
        receipts = result.to_payload()["receipts"]
        edit = next((r for r in receipts if r["action"] == "edit"), None)
        self.assertIsNotNone(edit)
        self.assertFalse(edit["ok"])
        self.assertFalse(_writable_mode(self.locked_file))
        # No automatic override without the explicit flag.
        runner = service._runner
        self.assertIsInstance(runner, FakeP4Runner)
        self.assertFalse(any(call[0] == "sync" for call in runner.calls))

    def test_local_writable_override_requires_explicit_flag_and_audits(self) -> None:
        service = self._service()
        result = service.prepare_write(
            [str(self.locked_file)],
            allow_local_writable_override=True,
        )
        payload = result.to_payload()
        override = next((r for r in payload["receipts"] if r["action"] == "override"), None)
        self.assertIsNotNone(override)
        self.assertTrue(override["ok"])
        self.assertIn("beforeMode", override)
        self.assertIn("afterMode", override)
        state = next(f for f in payload["files"] if f["inputPath"] == str(self.locked_file))
        self.assertTrue(state["localWritableOverride"])
        self.assertFalse(state["submitReady"])
        self.assertFalse(state["openedForEdit"])
        self.assertTrue(state["writable"])
        codes = [warning["code"] for warning in state["warnings"]]
        self.assertIn("local-writable-override", codes)
        self.assertTrue(_writable_mode(self.locked_file))

    def test_override_default_disabled_keeps_file_readonly(self) -> None:
        service = self._service()
        result = service.prepare_write([str(self.locked_file)])
        self.assertFalse(_writable_mode(self.locked_file))
        self.assertFalse(any(r["action"] == "override" for r in result.to_payload()["receipts"]))

    def test_no_sync_after_override(self) -> None:
        service = self._service()
        service.prepare_write(
            [str(self.locked_file), str(self.behind_file)],
            allow_local_writable_override=True,
            request_safe_sync=True,
        )
        runner = service._runner
        self.assertIsInstance(runner, FakeP4Runner)
        sync_calls = [call for call in runner.calls if call[0] == "sync"]
        # The locked file was overridden (never synced); the behind file is not
        # clean (it is writable? no: it is readonly and not opened) -- but the
        # behind file is clean in the fake, so only the locked file must not be
        # synced. Assert no sync targets the locked file.
        self.assertFalse(any(str(self.locked_file) in call for call in sync_calls))

    def test_safe_sync_runs_only_when_clean_and_requested(self) -> None:
        service = self._service()
        # Clean, readonly, behind head, not opened: sync is permitted when requested.
        result = service.prepare_write([str(self.behind_file)], request_safe_sync=True)
        receipts = result.to_payload()["receipts"]
        sync = next((r for r in receipts if r["action"] == "sync"), None)
        self.assertIsNotNone(sync)
        self.assertTrue(sync["ok"])
        self.assertEqual(sync["message"], "synced-exact-clean-file")
        runner = service._runner
        self.assertIsInstance(runner, FakeP4Runner)
        sync_calls = [call for call in runner.calls if call[0] == "sync"]
        self.assertEqual(sync_calls, [["sync", str(self.behind_file)]])

    def test_safe_sync_skipped_without_request(self) -> None:
        service = self._service()
        service.prepare_write([str(self.behind_file)])
        runner = service._runner
        self.assertIsInstance(runner, FakeP4Runner)
        self.assertFalse(any(call[0] == "sync" for call in runner.calls))
        self.assertFalse(any(call[0] == "edit" for call in runner.calls))

    def test_safe_sync_skipped_when_local_modified(self) -> None:
        modified = self.temp / "behind_modified.py"
        modified.write_text("print(4)\n", encoding="utf-8")
        _readonly(modified)
        self.world["files"][modified.as_posix()] = {
            "depotFile": "//depot/Content/behind_modified.py",
            "headRev": "5",
            "haveRev": "1",
            "type": "text",
            "headAction": "edit",
            "localChanged": True,
        }
        service = self._service()
        result = service.prepare_write([str(modified)], request_safe_sync=True)
        receipts = result.to_payload()["receipts"]
        sync = next((r for r in receipts if r["action"] == "sync"), None)
        self.assertIsNone(sync)
        self.assertTrue(
            any(r["action"] == "none" and "not provably clean" in r["message"] for r in receipts)
        )

    def test_safe_sync_skipped_when_writable(self) -> None:
        writable_behind = self.temp / "behind_writable.py"
        writable_behind.write_text("print(5)\n", encoding="utf-8")
        os.chmod(writable_behind, stat.S_IWUSR | stat.S_IRUSR)
        self.world["files"][writable_behind.as_posix()] = {
            "depotFile": "//depot/Content/behind_writable.py",
            "headRev": "5",
            "haveRev": "1",
            "type": "text",
            "headAction": "edit",
        }
        service = self._service()
        service.prepare_write([str(writable_behind)], request_safe_sync=True)
        runner = service._runner
        self.assertIsInstance(runner, FakeP4Runner)
        self.assertFalse(any(call[0] == "sync" for call in runner.calls))

    def test_safe_sync_skipped_when_not_behind(self) -> None:
        service = self._service()
        service.prepare_write([str(self.clean_file)], request_safe_sync=True)
        runner = service._runner
        self.assertIsInstance(runner, FakeP4Runner)
        self.assertFalse(any(call[0] == "sync" for call in runner.calls))

    def test_safe_sync_failure_does_not_continue_to_edit(self) -> None:
        self.world["files"][self.behind_file.as_posix()]["syncBlocked"] = True
        service = self._service()
        result = service.prepare_write([str(self.behind_file)], request_safe_sync=True).to_payload()
        self.assertTrue(any(r["action"] == "sync" and not r["ok"] for r in result["receipts"]))
        runner = service._runner
        self.assertIsInstance(runner, FakeP4Runner)
        self.assertFalse(any(call[0] == "edit" for call in runner.calls))
        post = service.status([str(self.behind_file)]).to_payload()["files"][0]
        self.assertTrue(post["behindHead"])
        self.assertFalse(post["openedByCurrentClient"])
        self.assertFalse(post["submitReady"])

    def test_sync_success_edit_failure_allows_explicit_override(self) -> None:
        self.world["files"][self.behind_file.as_posix()]["editBlocked"] = True
        service = self._service()
        payload = service.prepare_write(
            [str(self.behind_file)],
            request_safe_sync=True,
            allow_local_writable_override=True,
        ).to_payload()
        self.assertTrue(any(r["action"] == "sync" and r["ok"] for r in payload["receipts"]))
        self.assertTrue(any(r["action"] == "edit" and not r["ok"] for r in payload["receipts"]))
        self.assertTrue(any(r["action"] == "override" and r["ok"] for r in payload["receipts"]))
        state = payload["files"][0]
        self.assertTrue(state["localWritableOverride"])
        self.assertFalse(state["submitReady"])

    def test_submit_ready_false_when_current_checkout_is_behind_head(self) -> None:
        entry = self.world["files"][self.behind_file.as_posix()]
        entry["openedBy"] = "alice"
        entry["client"] = "alice_ws"
        entry["action"] = "edit"
        service = self._service()
        state = service.status([str(self.behind_file)]).to_payload()["files"][0]
        self.assertTrue(state["openedByCurrentClient"])
        self.assertTrue(state["behindHead"])
        self.assertFalse(state["submitReady"])

    # -- /Game mapping ---------------------------------------------------------
    def test_game_path_mapping_requires_project_root(self) -> None:
        service = self._service()
        state = service.status(["/Game/Foo/Bar"]).to_payload()["files"][0]
        self.assertEqual(state["pathError"], "game-path-mapping-requires-project-root")

    def test_game_path_mapping_single_existing_candidate(self) -> None:
        project = self.temp / "Project"
        content = project / "Content" / "Foo"
        content.mkdir(parents=True)
        asset = content / "Bar.uasset"
        asset.write_bytes(b"\x00\x01")
        service = self._service(project_root=project)
        state = service.status(["/Game/Foo/Bar"]).to_payload()["files"][0]
        self.assertEqual(state["localPath"].replace("\\", "/"), asset.as_posix())

    def test_game_path_mapping_ambiguous_or_missing(self) -> None:
        project = self.temp / "Project2"
        content = project / "Content"
        content.mkdir(parents=True)
        both = content / "Both"
        both.mkdir()
        (both / "Thing.uasset").write_bytes(b"\x00")
        (both / "Thing.umap").write_bytes(b"\x00")
        service = self._service(project_root=project)
        state = service.status(["/Game/Both/Thing"]).to_payload()["files"][0]
        self.assertEqual(state["pathError"], "game-path-ambiguous-or-missing")

    def test_game_path_mapping_rejects_escape(self) -> None:
        project = self.temp / "Project3"
        (project / "Content").mkdir(parents=True)
        service = self._service(project_root=project)
        state = service.status(["/Game/../secret"]).to_payload()["files"][0]
        self.assertEqual(state["pathError"], "game-path-outside-content")

    def test_paths_with_spaces_are_single_argv_tokens(self) -> None:
        spaced = self.temp / "A Folder With Spaces"
        spaced.mkdir()
        target = spaced / "x file.py"
        target.write_text("print(1)\n", encoding="utf-8")
        _readonly(target)
        self.world["files"][target.as_posix()] = {
            "depotFile": "//depot/Content/x file.py",
            "headRev": "1",
            "haveRev": "1",
            "type": "text",
            "headAction": "add",
        }
        service = self._service()
        state = service.status([str(target)]).to_payload()["files"][0]
        self.assertTrue(state["mapped"])
        runner = service._runner
        self.assertIsInstance(runner, FakeP4Runner)
        fstat = next(call for call in runner.calls if call[0] == "fstat")
        self.assertEqual(fstat, ["fstat", str(target)])

    def test_resolve_input_paths_rejects_wildcards(self) -> None:
        with self.assertRaises(SourceControlValidationError):
            resolve_input_paths([str(self.temp) + "/*.py"])

    # -- Prohibited operations / no generic shell ------------------------------
    def test_prohibited_operations_are_rejected_by_runner(self) -> None:
        runner = P4CommandRunner(p4_executable="p4", timeout_seconds=1.0)
        for argv in (
            ["submit", "-d", "x", "//depot/a"],
            ["revert", "//depot/a"],
            ["revert", "-a"],
            ["delete", "//depot/a"],
            ["obliterate", "//depot/a"],
            ["resolve", "-am", "//depot/a"],
            ["shelve", "//depot/a"],
            ["integrate", "//depot/a", "//depot/b"],
            ["admin", "x"],
            ["print", "//depot/a"],
            ["tag", "-l", "x", "//depot/a"],
        ):
            with self.subTest(argv=argv):
                with self.assertRaises(SourceControlProhibitedOperationError):
                    runner.run(argv)
        # Option tokens outside the per-command schema are rejected too.
        with self.assertRaises(SourceControlProhibitedOperationError):
            runner.run(["fstat", "-f", "file"])
        with self.assertRaises(SourceControlProhibitedOperationError):
            runner.run(["edit", "-c", "123", "file"])
        with self.assertRaises(SourceControlProhibitedOperationError):
            runner.run(["shell", "submit"])

    def test_runner_never_uses_shell(self) -> None:
        module_text = (SRC_ROOT / "ue_agent_kit" / "source_control.py").read_text(encoding="utf-8")
        self.assertNotIn("shell=True", module_text)
        self.assertNotIn("os.system", module_text)
        self.assertNotIn("subprocess.call", module_text)
        self.assertNotIn("subprocess.Popen", module_text)

    def test_fake_provider_rejects_generic_commands(self) -> None:
        runner = FakeP4Runner(self.world)
        with self.assertRaises(SourceControlProhibitedOperationError):
            runner.run(["whatever", "args"])
        with self.assertRaises(SourceControlProhibitedOperationError):
            runner.run(["edit", "-c", "123", "x"])

    def test_no_mutation_of_local_files_for_read_only_status(self) -> None:
        before = self.clean_file.read_bytes()
        service = self._service()
        service.status([str(self.clean_file)])
        self.assertEqual(before, self.clean_file.read_bytes())
        self.assertFalse(_writable_mode(self.clean_file))

    # -- Marshal decode ---------------------------------------------------------
    def test_marshal_records_are_bytes_normalized(self) -> None:
        raw = marshal.dumps({"code": "stat", b"depotFile": b"//depot/Content/x.py"})
        raw += marshal.dumps({"code": "error", b"data": b"no such file(s).\n"})
        records = _decode_marshal_records(raw)
        self.assertEqual(records[0]["code"], "stat")
        self.assertEqual(records[0]["depotFile"], "//depot/Content/x.py")
        self.assertEqual(records[1]["code"], "error")
        self.assertTrue(_is_error_record(records[1]))

    def test_marshal_decode_fails_closed_on_truncated_record(self) -> None:
        raw = marshal.dumps({"code": "stat", "depotFile": "//depot/Content/x.py"})
        raw += marshal.dumps({"code": "stat", "depotFile": "//depot/Content/y.py"})[:-3]
        with self.assertRaises(SourceControlCommandError):
            _decode_marshal_records(raw)

    # -- CLI contract ------------------------------------------------------------
    def test_cli_source_control_status_contract(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["source-control", "status", str(self.clean_file)])
        service = self._service()
        service._runner = FakeP4Runner(self.world)
        # run() builds its own service; instead verify through the shared service
        # contract plus a namespace sanity check on parser shape.
        payload = service.status([str(self.clean_file)]).to_payload()
        self.assertTrue(payload["readOnly"])
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["tool"], "ue_source_control_status")
        self.assertEqual(args.command, "source-control")
        self.assertEqual(args.source_control_command, "status")

    def test_cli_run_dispatch_status(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["source-control", "status", str(self.clean_file)])
        # Inject a fake-backed service by patching module attribute used by run().
        import ue_agent_kit.cli as cli_module

        original = cli_module.P4SourceControlService
        try:
            captured: list[str] = []

            class _StubService:
                def __init__(self, *, project_root: Any = None) -> None:
                    captured.append(str(project_root))

                def status(self, paths: list[str]) -> Any:
                    return _FakeStatusResult()

            cli_module.P4SourceControlService = _StubService  # type: ignore[assignment]
            payload, code = run_cli(args)
            self.assertEqual(code, 0)
            self.assertEqual(payload["tool"], "ue_source_control_status")
        finally:
            cli_module.P4SourceControlService = original


class _FakeStatusResult:
    def to_payload(self) -> dict[str, Any]:
        return {
            "schemaVersion": "1.0",
            "tool": "ue_source_control_status",
            "ok": True,
            "readOnly": True,
            "provider": {"available": False},
            "files": [],
            "warnings": [],
        }


def _remove_tree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
