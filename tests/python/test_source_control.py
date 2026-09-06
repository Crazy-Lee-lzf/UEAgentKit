from __future__ import annotations

import json
import marshal
import os
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock
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
    _marshal_change_spec,
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

    It is not a generic shell: only the allowlisted commands exist, every
    invocation is validated with the production argv rules, and every received
    argv is appended to ``calls`` so tests can assert exact command lines. The
    C3 changelist/reopen/resolve family is modeled deterministically from the
    ``world`` state so mutation matrices never depend on a real P4 server.
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
        self._pending = world.setdefault("pendingChanges", {})
        self._next_change = int(world.setdefault("nextChangeId", 5001))

    def _down_on(self) -> tuple[str, ...]:
        return tuple(self.world.get("downOn", ()))

    def _sleep_on(self) -> str:
        return str(self.world.get("sleepOn", ""))

    def _entry(self, local_path: str) -> dict[str, Any] | None:
        return self._files.get(local_path.replace("\\", "/").lower())

    def _find_entries(self, paths: list[str]) -> list[dict[str, Any] | None]:
        return [self._entry(path) for path in paths]

    def _decode_spec(self, payload: bytes) -> dict[str, Any]:
        raw = marshal.loads(payload)
        if not isinstance(raw, dict):
            raise ValueError("change -i spec must be a marshaled dict")
        decoded: dict[str, Any] = {}
        for key, value in raw.items():
            text_key = key.decode("utf-8") if isinstance(key, bytes) else str(key)
            if isinstance(value, list):
                decoded[text_key] = [
                    item.decode("utf-8") if isinstance(item, bytes) else str(item) for item in value
                ]
            elif isinstance(value, bytes):
                decoded[text_key] = value.decode("utf-8")
            else:
                decoded[text_key] = str(value)
        return decoded

    def run(self, argv: list[str], *, stdin_bytes: bytes | None = None) -> _P4CommandResult:
        tokens = [str(token) for token in argv]
        # Production argv validation runs first: prohibited ops and out-of-schema
        # option tokens raise exactly as the real runner would.
        self.real_validator._validate_argv(tokens)
        is_change_input = tokens[:2] == ["change", "-i"]
        if stdin_bytes is not None and not is_change_input:
            raise SourceControlProhibitedOperationError(
                f"Fake P4 {tokens[0]} does not accept stdin."
            )
        if is_change_input and stdin_bytes is None:
            raise SourceControlValidationError("change -i requires a typed changelist spec form on stdin.")
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
            if tokens[1] == "-c":
                change_id = tokens[2]
                paths = tokens[3:]
                for path, entry in zip(paths, self._find_entries(paths)):
                    if entry is None or not entry.get("openedBy"):
                        continue
                    if str(entry.get("change", "default")) != change_id:
                        continue
                    records.append(
                        _stat_record(
                            depotFile=entry["depotFile"],
                            clientFile=path,
                            rev=entry.get("haveRev", "1"),
                            haveRev=entry.get("haveRev", "1"),
                            action=entry.get("action", "edit"),
                            change=change_id,
                            type=entry.get("type", "text"),
                            user=entry.get("openedBy"),
                            client=entry.get("client", self._client),
                            locked="yes" if entry.get("lockedBy") == entry.get("openedBy") else "",
                        )
                    )
                return _P4CommandResult(exit_code=0, records=tuple(records), stderr_text="", duration_ms=1.0)
            paths = tokens[2:] if tokens[1] == "-a" else tokens[1:]
            for path, entry in zip(paths, self._find_entries(paths)):
                if entry is None:
                    continue
                opened_by = entry.get("openedBy")
                if not opened_by:
                    continue
                locked_marker = "yes" if entry.get("lockedBy") == opened_by else ""
                records.append(
                    _stat_record(
                        depotFile=entry["depotFile"],
                        clientFile=path,
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

        # ------------------------------------------------------------ C3 ----
        if command == "changes":
            if self.world.get("changesQueryError"):
                return _P4CommandResult(
                    exit_code=0,
                    records=(_error_record(str(self.world["changesQueryError"])),),
                    stderr_text="",
                    duration_ms=1.0,
                )
            records = []
            client_name = tokens[4]  # changes -s pending -c <client>
            for change_id in sorted(self._pending, key=lambda item: int(item)):
                spec = self._pending[change_id]
                if spec.get("client") != client_name:
                    continue
                records.append(
                    _stat_record(
                        Change=change_id,
                        User=spec.get("user", ""),
                        Client=spec.get("client", ""),
                        Status=spec.get("status", "pending"),
                        Description=spec.get("description", ""),
                    )
                )
            return _P4CommandResult(exit_code=0, records=tuple(records), stderr_text="", duration_ms=1.0)

        if command == "change":
            if tokens[1] == "-i":
                spec = self._decode_spec(stdin_bytes or b"")
                change_value = str(spec.get("Change", "new"))
                if change_value == "new":
                    change_id = str(self._next_change)
                    self._next_change += 1
                    self._pending[change_id] = {
                        "status": "pending",
                        "user": spec.get("User", self._user),
                        "client": spec.get("Client", self._client),
                        "description": spec.get("Description", ""),
                        "files": [],
                    }
                    return _P4CommandResult(
                        exit_code=0,
                        records=(_stat_record(code="info", data=f"Change {change_id} created."),),
                        stderr_text="",
                        duration_ms=1.0,
                    )
                existing = self._pending.get(change_value)
                if existing is None:
                    return _P4CommandResult(
                        exit_code=0,
                        records=(_error_record(f"Change {change_value} unknown.\n"),),
                        stderr_text="",
                        duration_ms=1.0,
                    )
                incoming_files = [
                    value for key, value in sorted(
                        ((key, value) for key, value in spec.items() if key.startswith("Files") and key[5:].isdigit()),
                        key=lambda item: int(item[0][5:]),
                    )
                ]
                if incoming_files:
                    existing["files"] = incoming_files
                existing["description"] = spec.get("Description", existing.get("description", ""))
                return _P4CommandResult(
                    exit_code=0,
                    records=(_stat_record(code="info", data=f"Change {change_value} updated."),),
                    stderr_text="",
                    duration_ms=1.0,
                )
            if len(tokens) == 2:  # change -o  (new template)
                return _P4CommandResult(
                    exit_code=0,
                    records=(
                        _stat_record(
                            code="stat",
                            Change="new",
                            User=self._user,
                            Client=self._client,
                            Status="new",
                            Description="",
                        ),
                    ),
                    stderr_text="",
                    duration_ms=1.0,
                )
            change_id = tokens[2]
            if change_id in set(self.world.get("changeReadErrorIds", ())):
                return _P4CommandResult(
                    exit_code=0,
                    records=(_error_record("Perforce password (P4PASSWD) invalid or unset.\n"),),
                    stderr_text="",
                    duration_ms=1.0,
                )
            spec = self._pending.get(change_id)
            if spec is None:
                return _P4CommandResult(
                    exit_code=0,
                    records=(_error_record(f"Change {change_id} unknown.\n"),),
                    stderr_text="",
                    duration_ms=1.0,
                )
            record: dict[str, Any] = _stat_record(
                code="stat",
                Change=change_id,
                User=spec.get("user", ""),
                Client=spec.get("client", ""),
                Status=spec.get("status", "pending"),
                Description=spec.get("description", ""),
            )
            files = spec.get("files") or []
            for index, depot_file in enumerate(files):
                record[f"Files{index}"] = depot_file
            return _P4CommandResult(exit_code=0, records=(record,), stderr_text="", duration_ms=1.0)

        if command == "reopen":
            records = []
            change_id = tokens[2]
            for path, entry in zip(tokens[3:], self._find_entries(tokens[3:])):
                if entry is None:
                    records.append(_error_record(f"{path} - file(s) not on client.\n"))
                    continue
                if entry.get("openedBy") != self._user or entry.get("client") != self._client:
                    records.append(_error_record(f"{path} - file(s) not opened on this client.\n"))
                    continue
                old_change = str(entry.get("change", "default"))
                if old_change != change_id:
                    if old_change in self._pending and entry["depotFile"] in self._pending[old_change].get("files", []):
                        self._pending[old_change]["files"].remove(entry["depotFile"])
                    entry["change"] = change_id
                    self._pending[change_id].setdefault("files", [])
                    if entry["depotFile"] not in self._pending[change_id]["files"]:
                        self._pending[change_id]["files"].append(entry["depotFile"])
                records.append(
                    _stat_record(
                        depotFile=entry["depotFile"],
                        clientFile=path,
                        action=entry.get("action", "edit"),
                        change=change_id,
                        type=entry.get("type", "text"),
                        user=self._user,
                        client=self._client,
                    )
                )
            return _P4CommandResult(exit_code=0, records=tuple(records), stderr_text="", duration_ms=1.0)

        if command == "resolve":
            flags = tokens[1:]
            mode = None
            change_id = None
            paths: list[str] = []
            index = 0
            while index < len(flags):
                flag = flags[index]
                if flag == "-c":
                    change_id = flags[index + 1]
                    index += 2
                    continue
                if flag in {"-n", "-am"}:
                    mode = flag
                elif flag == "-o":
                    pass  # preview output content is not modeled by the fixture
                else:
                    paths.append(flag)
                index += 1
            needs = []
            for path, entry in zip(paths, self._find_entries(paths)):
                if entry is None:
                    continue
                if change_id is not None and str(entry.get("change", "default")) != change_id:
                    continue
                if mode == "-n":
                    if entry.get("needsResolve"):
                        record: dict[str, Any] = _stat_record(
                            depotFile=entry["depotFile"],
                            clientFile=path,
                            type=entry.get("type", "text"),
                            theirRev=entry.get("headRev", "1"),
                        )
                        if entry.get("resolveKind", "content") == "content":
                            record["startFromRev"] = entry.get("baseRev", "1")
                            record["endFromRev"] = entry.get("theirRev", "1")
                        else:
                            record["resolveKind"] = entry.get("resolveKind")
                        needs.append(record)
                    elif self.world.get("resolveNoWorkAsError"):
                        needs.append(_error_record(f"{path} - no file(s) to resolve.\n"))
                    continue
                if mode == "-am":
                    if entry.get("needsResolve") is not True:
                        continue
                    kind = entry.get("resolveKind", "content")
                    if kind != "content" or entry.get("contentConflict") or not _text_like(entry.get("type", "")):
                        needs.append(
                            _stat_record(
                                code="info",
                                data=f"{path} - resolve skipped by automatic merge.\n",
                            )
                        )
                        continue
                    if entry.get("resolveStillNeedsResolve"):
                        # Simulate a server that reports an automatic merge but
                        # leaves the file unresolved; the product must never
                        # claim success without a clean post-query.
                        needs.append(
                            _stat_record(
                                code="info",
                                data=f"{path} - merging {entry['depotFile']}#{entry.get('theirRev','1')}.\n",
                            )
                        )
                        continue
                    entry["needsResolve"] = False
                    entry["resolved"] = True
                    needs.append(
                        _stat_record(
                            code="info",
                            data=f"{path} - merging {entry['depotFile']}#{entry.get('theirRev','1')}.\n",
                        )
                    )
            return _P4CommandResult(exit_code=0, records=tuple(needs), stderr_text="", duration_ms=1.0)

        raise SourceControlProhibitedOperationError(f"Fake P4 has no generic behavior for {command}")


def _text_like(type_name: str) -> bool:
    lowered = type_name.lower()
    return lowered.startswith(("text", "unicode", "utf16"))


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
            ["shelve", "//depot/a"],
            ["integrate", "//depot/a", "//depot/b"],
            ["admin", "x"],
            ["print", "//depot/a"],
            ["tag", "-l", "x", "//depot/a"],
            ["merge", "//depot/a", "//depot/b"],
            ["unshelve", "123"],
            ["lock", "//depot/a"],
            ["unlock", "//depot/a"],
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

    def test_c3_runner_rejects_forbidden_resolve_change_reopen_flags(self) -> None:
        runner = P4CommandRunner(p4_executable="p4", timeout_seconds=1.0)
        path = "C:/fake/managed.py"
        for argv in (
            ["resolve", "-af", path],
            ["resolve", "-at", path],
            ["resolve", "-ay", path],
            ["resolve", "-f", path],
            ["resolve", "-t", path],
            ["resolve", "-a", path],
            ["resolve", "-A", path],
            ["resolve", "-N", path],
            ["resolve", "-d", path],
            ["change", "-d", "123"],
            ["change", "-f", "-o"],
            ["change", "-u", "-o"],
            ["change", "-U", "-o"],
            ["reopen", "-t", "text", path],
            ["reopen", "-f", path],
            ["reopen", path],
        ):
            with self.subTest(argv=argv):
                with self.assertRaises(SourceControlProhibitedOperationError):
                    runner.run(argv)

    def test_c3_runner_accepts_only_typed_shapes(self) -> None:
        runner = P4CommandRunner(p4_executable="p4", timeout_seconds=1.0)
        path = "C:/fake/managed.py"
        # Valid frozen shapes (validation only; no real P4 subprocess is spawned).
        for argv in (
            ["changes", "-s", "pending", "-c", "alice_ws"],
            ["change", "-o"],
            ["change", "-o", "123"],
            ["reopen", "-c", "123", path],
            ["resolve", "-n", path],
            ["resolve", "-n", "-o", path],
            ["resolve", "-n", "-c", "123", path],
            ["resolve", "-am", path],
            ["resolve", "-am", "-c", "123", path],
            ["opened", "-c", "123", path],
        ):
            with self.subTest(argv=argv):
                runner._validate_argv(argv)
        # Invalid shapes.
        for argv in (
            ["changes", "-s", "submitted", "-c", "alice_ws"],
            ["changes", "-c", "alice_ws"],
            ["resolve", path],  # interactive resolve is prohibited
            ["resolve", "-am", "-n", path],
            ["change", "-i", path],
            ["change", "-d"],
        ):
            with self.subTest(argv=argv):
                with self.assertRaises(SourceControlProhibitedOperationError):
                    runner.run(argv)
        # Non-numeric / zero / wildcard changelist ids are rejected as validation.
        for argv in (
            ["resolve", "-c", "abc", path],
            ["resolve", "-c", "0", path],
            ["reopen", "-c", "abc", path],
            ["reopen", "-c", "0", path],
            ["reopen", "-c", "default", path],
            ["opened", "-c", "abc", path],
            ["change", "-o", "abc"],
        ):
            with self.subTest(argv=argv):
                with self.assertRaises(SourceControlValidationError):
                    runner.run(argv)
        # Depot / wildcard / revision tokens remain invalid path arguments.
        with self.assertRaises(SourceControlValidationError):
            runner.run(["resolve", "-am", "//depot/a"])
        with self.assertRaises(SourceControlValidationError):
            runner.run(["resolve", "-am", "C:/fake/*.py"])
        with self.assertRaises(SourceControlValidationError):
            runner.run(["change", "-o", "1#2"])

    def test_change_i_requires_typed_stdin_only(self) -> None:
        runner = P4CommandRunner(p4_executable="p4", timeout_seconds=1.0)
        with self.assertRaises(SourceControlValidationError):
            runner.run(["change", "-i"])
        with self.assertRaises(SourceControlProhibitedOperationError):
            runner.run(["change", "-o"], stdin_bytes=b"not allowed")
        payload = _marshal_change_spec({"Change": "new", "Client": "c", "User": "u", "Description": "d"})
        self.assertIsInstance(payload, bytes)

    def test_change_i_real_runner_uses_input_without_explicit_stdin_pipe(self) -> None:
        runner = P4CommandRunner(p4_executable="p4", timeout_seconds=1.0)
        payload = _marshal_change_spec(
            {"Change": "new", "Client": "alice_ws", "User": "alice", "Description": "review"}
        )

        def fake_run(argv: list[str], **kwargs: Any) -> Any:
            self.assertEqual(argv[:3], ["p4", "-G", "change"])
            self.assertEqual(kwargs.get("input"), payload)
            self.assertNotIn("stdin", kwargs)
            marshal.dump({b"code": b"info", b"data": b"Change 123 created."}, kwargs["stdout"])
            class Completed:
                returncode = 0
            return Completed()

        with mock.patch("ue_agent_kit.source_control.subprocess.run", side_effect=fake_run):
            result = runner.run(["change", "-i"], stdin_bytes=payload)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Change 123 created", str(result.records[0].get("data", "")))

    def test_marshal_change_spec_expands_real_indexed_files_fields(self) -> None:
        payload = _marshal_change_spec(
            {"Change": "123", "Description": "d", "Files": ["//depot/a", "//depot/b"]}
        )
        decoded = FakeP4Runner({"files": {}})._decode_spec(payload)
        self.assertNotIn("Files", decoded)
        self.assertEqual(decoded["Files0"], "//depot/a")
        self.assertEqual(decoded["Files1"], "//depot/b")

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


class C3ChangelistResolveAuditTests(unittest.TestCase):
    """Deterministic C3 mutation matrix over the fake P4 world."""

    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="ueak_c3_"))
        self.addCleanup(_remove_tree, self.temp)
        f = self.temp.as_posix()

        self.open_py = self.temp / "work.py"
        self.open_py.write_text("value = 1\n", encoding="utf-8")
        self.merge_py = self.temp / "merge.py"
        self.merge_py.write_text("left = 1\nright = 2\n", encoding="utf-8")
        self.conflict_py = self.temp / "conflict.py"
        self.conflict_py.write_text("conflict = True\n", encoding="utf-8")
        self.binary_uasset = self.temp / "Asset.uasset"
        self.binary_uasset.write_bytes(b"\x00\x01\x02")
        self.generic_bin = self.temp / "payload.bin"
        self.generic_bin.write_bytes(b"\x03\x04\x05")
        self.note_txt = self.temp / "note.txt"
        self.note_txt.write_text("notes\n", encoding="utf-8")
        self.closed_py = self.temp / "closed.py"
        self.closed_py.write_text("closed = True\n", encoding="utf-8")
        _readonly(self.closed_py)

        def entry(name: str, depot: str, **extra: Any) -> dict[str, Any]:
            return {
                "depotFile": f"//depot/Content/{depot}",
                "headRev": "1",
                "haveRev": "1",
                "type": extra.pop("type", "text"),
                "headAction": "add",
                **extra,
            }

        self.world: dict[str, Any] = {
            "userName": "alice",
            "clientName": "alice_ws",
            "nextChangeId": 5100,
            "pendingChanges": {
                "2001": {
                    "status": "pending",
                    "user": "alice",
                    "client": "alice_ws",
                    "description": "alice existing review",
                    "files": [],
                },
                "7001": {
                    "status": "pending",
                    "user": "bob",
                    "client": "bob_ws",
                    "description": "bob review",
                    "files": [],
                },
            },
            "files": {
                f"{f}/work.py": entry("work.py", "work.py", openedBy="alice", client="alice_ws", action="edit"),
                f"{f}/merge.py": entry(
                    "merge.py", "merge.py", openedBy="alice", client="alice_ws", action="edit",
                    needsResolve=True, baseRev="1", theirRev="2",
                ),
                f"{f}/conflict.py": entry(
                    "conflict.py", "conflict.py", openedBy="alice", client="alice_ws", action="edit",
                    needsResolve=True, contentConflict=True, baseRev="1", theirRev="2",
                ),
                f"{f}/Asset.uasset": entry(
                    "Asset.uasset", "Asset.uasset", type="binary", openedBy="alice", client="alice_ws",
                    action="edit", needsResolve=True, baseRev="1", theirRev="2",
                ),
                f"{f}/payload.bin": entry(
                    "payload.bin", "payload.bin", type="binary", openedBy="alice", client="alice_ws",
                    action="edit", needsResolve=True, baseRev="1", theirRev="2",
                ),
                f"{f}/note.txt": entry(
                    "note.txt", "note.txt", openedBy="alice", client="alice_ws", action="edit",
                    needsResolve=True, baseRev="1", theirRev="2",
                ),
                f"{f}/closed.py": entry("closed.py", "closed.py", needsResolve=True),
            },
        }

    def _service(self, **kwargs: Any) -> P4SourceControlService:
        service = P4SourceControlService(**kwargs)
        service._runner = FakeP4Runner(self.world)
        return service

    # -- C3 changelists read ---------------------------------------------------
    def test_changelists_lists_only_current_client_pending(self) -> None:
        service = self._service()
        payload = service.changelists().to_payload()
        self.assertTrue(payload["readOnly"])
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["pendingCount"], 1)
        changelist = payload["changelists"][0]
        self.assertEqual(changelist["changelistId"], "2001")
        self.assertEqual(changelist["status"], "pending")
        self.assertEqual(changelist["description"], "alice existing review")
        self.assertTrue(changelist["currentUserOwned"])
        self.assertTrue(changelist["currentClientOwned"])
        self.assertEqual(changelist["fileCount"], 0)
        runner = service._runner
        self.assertIsInstance(runner, FakeP4Runner)
        self.assertEqual(runner.calls[1], ["changes", "-s", "pending", "-c", "alice_ws"])

    def test_changelists_single_read_shows_ownership(self) -> None:
        service = self._service()
        owned = service.changelists("2001").to_payload()
        self.assertFalse(owned["notFound"])
        state = owned["changelists"][0]
        self.assertTrue(state["currentUserOwned"])
        self.assertTrue(state["currentClientOwned"])
        other = service.changelists("7001").to_payload()
        state = other["changelists"][0]
        self.assertTrue(state["pending"])
        self.assertFalse(state["currentUserOwned"])
        self.assertFalse(state["currentClientOwned"])
        codes = [warning["code"] for warning in state["warnings"]]
        self.assertIn("changelist-not-current-owner", codes)

    def test_changelists_single_read_missing(self) -> None:
        service = self._service()
        payload = service.changelists("9999").to_payload()
        self.assertTrue(payload["notFound"])
        self.assertEqual(payload["pendingCount"], 0)
        codes = [warning["code"] for warning in payload["warnings"]]
        self.assertIn("changelist-not-found", codes)

    def test_changelists_provider_error_is_not_reported_as_empty_or_not_found(self) -> None:
        self.world["changesQueryError"] = "Perforce password invalid.\n"
        service = self._service()
        with self.assertRaises(SourceControlCommandError):
            service.changelists()
        self.world.pop("changesQueryError")
        self.world["changeReadErrorIds"] = ["2001"]
        with self.assertRaises(SourceControlCommandError):
            service.changelists("2001")

    def test_changelist_files_use_indexed_schema_and_truthful_total_count(self) -> None:
        files = [f"//depot/Content/f{i}.txt" for i in range(105)]
        self.world["pendingChanges"]["2001"]["files"] = files
        service = self._service()
        payload = service.changelists("2001").to_payload()["changelists"][0]
        self.assertEqual(payload["fileCount"], 105)
        self.assertEqual(len(payload["files"]), 100)
        self.assertEqual(payload["files"][0], files[0])
        self.assertEqual(payload["files"][-1], files[99])
        self.assertIn("changelist-files-truncated", [w["code"] for w in payload["warnings"]])

    def test_changelists_invalid_id_rejected(self) -> None:
        service = self._service()
        with self.assertRaises(SourceControlValidationError):
            service.changelists("abc")
        with self.assertRaises(SourceControlValidationError):
            service.changelists("0")

    # -- C3 prepare_changelist -------------------------------------------------
    def test_prepare_changelist_creates_cl_and_reopens_exact_file(self) -> None:
        service = self._service()
        result = service.prepare_changelist([str(self.open_py)], "move work into review")
        payload = result.to_payload()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["changelistId"], "5100")
        self.assertTrue(payload["changelistCreated"])
        self.assertEqual(payload["description"], "move work into review")
        runner = service._runner
        self.assertIsInstance(runner, FakeP4Runner)
        self.assertEqual(self.world["pendingChanges"]["5100"]["description"], "move work into review")
        self.assertEqual(self.world["pendingChanges"]["5100"]["files"], ["//depot/Content/work.py"])
        action_codes = [(r["action"], r.get("code")) for r in payload["receipts"]]
        self.assertIn(("create-changelist", None), action_codes)
        self.assertIn(("reopen", "reopened"), action_codes)
        state = payload["files"][0]
        self.assertTrue(state["openedByCurrentClient"])
        self.assertEqual(state["change"], "5100")
        self.assertTrue(state["submitReady"])
        receipt = payload["auditReceipt"]
        self.assertEqual(receipt["operation"], "create-changelist")
        self.assertEqual(receipt["manualFinalAction"], "none")
        self.assertFalse(receipt["submitCapability"])
        self.assertFalse(receipt["revertCapability"])
        self.assertFalse(receipt["deleteCapability"])
        self.assertEqual(receipt["exactFiles"], [str(self.open_py)])
        # structured change -i received a typed spec on stdin
        change_input_calls = [call for call in runner.calls if call[:2] == ["change", "-i"]]
        self.assertEqual(len(change_input_calls), 1)

    def test_prepare_changelist_updates_owned_pending_description_only(self) -> None:
        service = self._service()
        result = service.prepare_changelist(
            [str(self.open_py)], "updated review description", changelist_id="2001"
        )
        payload = result.to_payload()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["changelistId"], "2001")
        self.assertFalse(payload["changelistCreated"])
        spec = self.world["pendingChanges"]["2001"]
        self.assertEqual(spec["description"], "updated review description")
        self.assertEqual(spec["user"], "alice")
        self.assertEqual(spec["client"], "alice_ws")
        self.assertEqual(spec["status"], "pending")
        state = payload["files"][0]
        self.assertEqual(state["change"], "2001")
        action_codes = [(r["action"], r.get("code")) for r in payload["receipts"]]
        self.assertIn(("update-description", None), action_codes)
        self.assertIn(("reopen", "reopened"), action_codes)
        self.assertEqual(payload["auditReceipt"]["operation"], "update-description")

    def test_prepare_changelist_rejects_unowned_or_nonpending_cl(self) -> None:
        service = self._service()
        with self.assertRaises(SourceControlValidationError):
            service.prepare_changelist([str(self.open_py)], "x", changelist_id="7001")
        self.world["pendingChanges"]["2001"]["status"] = "submitted"
        with self.assertRaises(SourceControlValidationError):
            service.prepare_changelist([str(self.open_py)], "x", changelist_id="2001")

    def test_prepare_changelist_partial_move_is_truthful(self) -> None:
        service = self._service()
        result = service.prepare_changelist(
            [str(self.open_py), str(self.closed_py)], "partial review"
        )
        payload = result.to_payload()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["changelistId"], "5100")
        reopen = [r for r in payload["receipts"] if r["action"] == "reopen"]
        by_file = {r["file"]: r for r in reopen}
        self.assertTrue(by_file[str(self.open_py)]["ok"])
        self.assertFalse(by_file[str(self.closed_py)]["ok"])
        self.assertEqual(by_file[str(self.closed_py)]["code"], "not-open-current-client")
        state = next(f for f in payload["files"] if f["inputPath"] == str(self.open_py))
        self.assertEqual(state["change"], "5100")
        closed = next(f for f in payload["files"] if f["inputPath"] == str(self.closed_py))
        self.assertFalse(closed["openedByCurrentClient"])
        self.assertFalse(payload["submitReady"])
        codes = [warning["code"] for warning in payload["warnings"]]
        self.assertIn("partial-or-blocked-operation", codes)

    def test_prepare_changelist_does_not_move_override_only_files(self) -> None:
        locked = self.temp / "locked.bin"
        locked.write_bytes(b"\x00\x01")
        _readonly(locked)
        self.world["files"][locked.as_posix()] = {
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
        }
        service = self._service()
        service.prepare_write([str(locked)], allow_local_writable_override=True)
        result = service.prepare_changelist([str(locked)], "must not move override")
        payload = result.to_payload()
        self.assertFalse(payload["ok"])
        reopen = [r for r in payload["receipts"] if r["action"] == "reopen"]
        self.assertTrue(reopen)
        self.assertEqual(reopen[0]["code"], "override-only")

    def test_prepare_changelist_validates_change_set_link(self) -> None:
        service = self._service()
        payload = service.prepare_changelist(
            [str(self.open_py)], "with change set", change_set_id="cs_abc123"
        ).to_payload()
        self.assertEqual(payload["changeSetId"], "cs_abc123")
        self.assertEqual(payload["auditReceipt"]["changeSetId"], "cs_abc123")
        with self.assertRaises(SourceControlValidationError):
            service.prepare_changelist([str(self.open_py)], "bad cs", change_set_id="not-a-cs")

    def test_prepare_changelist_post_mutation_verification_failure_is_audited(self) -> None:
        audit_root = self.temp / "audit-uncertain"
        service = self._service(audit_report_root=audit_root)
        self.world["changeReadErrorIds"] = ["5100"]
        payload = service.prepare_changelist([str(self.open_py)], "uncertain create").to_payload()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["receipts"][0]["code"], "mutation-post-verify-failed")
        self.assertTrue(payload["receipts"][0]["mutationMayHaveOccurred"])
        audit = payload["auditReceipt"]
        self.assertTrue(audit["mutationMayHaveOccurred"])
        self.assertTrue(audit["persisted"])
        self.assertTrue(Path(audit["receiptPath"]).exists())

    def test_prepare_changelist_manual_final_action_is_metadata_only(self) -> None:
        service = self._service()
        payload = service.prepare_changelist(
            [str(self.open_py)], "prepare submit", manual_final_action="submit"
        ).to_payload()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["manualFinalAction"], "submit")
        self.assertEqual(payload["auditReceipt"]["manualFinalAction"], "submit")
        self.assertFalse(payload["auditReceipt"]["submitCapability"])
        codes = [warning["code"] for warning in payload["warnings"]]
        self.assertIn("manual-final-action", codes)
        with self.assertRaises(SourceControlValidationError):
            service.prepare_changelist([str(self.open_py)], "bad", manual_final_action="execute")

    def test_prepare_changelist_description_bounds(self) -> None:
        service = self._service()
        with self.assertRaises(SourceControlValidationError):
            service.prepare_changelist([str(self.open_py)], "   ")
        oversized = "x" * 5000
        with self.assertRaises(SourceControlValidationError):
            service.prepare_changelist([str(self.open_py)], oversized)

    # -- C3 resolve preview ----------------------------------------------------
    def test_resolve_status_text_preview(self) -> None:
        service = self._service()
        result = service.resolve_status([str(self.merge_py)])
        payload = result.to_payload()
        self.assertTrue(payload["readOnly"])
        state = payload["files"][0]
        self.assertTrue(state["needsResolve"])
        self.assertEqual(state["resolveKind"], "content")
        self.assertTrue(state["mergeableText"])
        self.assertFalse(state["binaryPackage"])
        self.assertEqual(payload["summary"]["needsResolve"], 1)
        runner = service._runner
        self.assertIsInstance(runner, FakeP4Runner)
        self.assertIn(["resolve", "-n", str(self.merge_py)], runner.calls)

    def test_resolve_status_resolved_and_closed(self) -> None:
        service = self._service()
        payload = service.resolve_status([str(self.open_py), str(self.closed_py)]).to_payload()
        by_input = {state["inputPath"]: state for state in payload["files"]}
        self.assertFalse(by_input[str(self.open_py)]["needsResolve"])
        # closed.py needs resolve in the fixture but is not opened by the client.
        closed = by_input[str(self.closed_py)]
        self.assertTrue(closed["mapped"])
        self.assertTrue(closed["needsResolve"])
        self.assertFalse(closed["submitReady"])

    def test_resolve_status_binary_package_warns(self) -> None:
        service = self._service()
        state = service.resolve_status([str(self.binary_uasset)]).to_payload()["files"][0]
        self.assertTrue(state["needsResolve"])
        self.assertTrue(state["binaryPackage"])
        self.assertFalse(state["mergeableText"])
        codes = [warning["code"] for warning in state["warnings"]]
        self.assertIn("binary-package-resolve-required", codes)

    def test_resolve_status_generic_binary_is_not_unreal_package(self) -> None:
        service = self._service()
        state = service.resolve_status([str(self.generic_bin)]).to_payload()["files"][0]
        self.assertTrue(state["needsResolve"])
        self.assertFalse(state["binaryPackage"])
        self.assertTrue(state["genericBinary"])
        self.assertFalse(state["mergeableText"])
        codes = [warning["code"] for warning in state["warnings"]]
        self.assertIn("binary-file-resolve-not-supported", codes)
        self.assertNotIn("binary-package-resolve-required", codes)

    def test_resolve_status_non_whitelisted_extension(self) -> None:
        service = self._service()
        state = service.resolve_status([str(self.note_txt)]).to_payload()["files"][0]
        self.assertTrue(state["needsResolve"])
        self.assertFalse(state["mergeableText"])
        codes = [warning["code"] for warning in state["warnings"]]
        self.assertIn("text-resolve-not-eligible", codes)

    def test_resolve_status_benign_no_resolve_tagged_error_is_clean(self) -> None:
        self.world["resolveNoWorkAsError"] = True
        service = self._service()
        payload = service.resolve_status([str(self.open_py)]).to_payload()
        state = payload["files"][0]
        self.assertFalse(state["needsResolve"])
        self.assertFalse(state["resolveStateUnknown"])
        self.assertEqual(payload["summary"]["resolveStateUnknown"], 0)

    def test_resolve_status_fails_closed_on_preview_error(self) -> None:
        self.world["downOn"] = ["resolve"]
        service = self._service()
        payload = service.resolve_status([str(self.merge_py)]).to_payload()
        state = payload["files"][0]
        self.assertTrue(state["resolveStateUnknown"])
        self.assertFalse(state["needsResolve"])
        self.assertEqual(payload["summary"]["resolveStateUnknown"], 1)

    def test_resolve_status_provider_unavailable(self) -> None:
        self.world["downOn"] = ["info"]
        service = self._service()
        payload = service.resolve_status([str(self.merge_py)]).to_payload()
        self.assertFalse(payload["provider"]["available"])
        state = payload["files"][0]
        self.assertTrue(state["resolveStateUnknown"])
        self.assertTrue(state["localPath"])

    # -- C3 bounded resolve -am ------------------------------------------------
    def test_resolve_text_clean_merge_succeeds(self) -> None:
        service = self._service()
        result = service.resolve_text([str(self.merge_py)])
        payload = result.to_payload()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["allResolved"])
        self.assertFalse(payload["binaryReconciliationRequired"])
        receipt = payload["receipts"][0]
        self.assertEqual(receipt["action"], "resolve-text")
        self.assertEqual(receipt["code"], "resolve-text-ok")
        self.assertIn("beforeSha256", receipt)
        self.assertIn("afterSha256", receipt)
        runner = service._runner
        self.assertIsInstance(runner, FakeP4Runner)
        self.assertTrue(any(call[:2] == ["resolve", "-am"] for call in runner.calls))
        self.assertFalse(self.world["files"][self.merge_py.as_posix()].get("needsResolve"))
        state = payload["files"][0]
        self.assertFalse(state["warnings"] and any(
            w["code"] == "partial-or-blocked-operation" for w in state["warnings"]
        ))

    def test_resolve_text_conflict_remains_unresolved(self) -> None:
        service = self._service()
        result = service.resolve_text([str(self.conflict_py)])
        payload = result.to_payload()
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["allResolved"])
        self.assertFalse(payload["submitReady"])
        receipt = payload["receipts"][0]
        self.assertEqual(receipt["code"], "resolve-conflict-remains")
        self.assertIsNotNone(receipt.get("beforeSha256"))
        # The file remains unresolved and no forced acceptance flag was used.
        self.assertTrue(self.world["files"][self.conflict_py.as_posix()].get("needsResolve"))
        codes = [warning["code"] for warning in payload["warnings"]]
        self.assertIn("partial-or-blocked-operation", codes)

    def test_resolve_text_post_verify_accepts_benign_no_resolve_tagged_error(self) -> None:
        self.world["resolveNoWorkAsError"] = True
        service = self._service()
        payload = service.resolve_text([str(self.merge_py)]).to_payload()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["allResolved"])
        self.assertEqual(payload["receipts"][0]["code"], "resolve-text-ok")

    def test_resolve_text_never_claims_success_without_clean_post_query(self) -> None:
        # The fixture simulates a server that reports an automatic merge but
        # leaves the file unresolved; the post-query must fail closed.
        entry = self.world["files"][self.merge_py.as_posix()]
        entry["resolveStillNeedsResolve"] = True
        service = self._service()
        payload = service.resolve_text([str(self.merge_py)]).to_payload()
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["allResolved"])
        self.assertEqual(payload["receipts"][0]["code"], "resolve-conflict-remains")
        # The merge primitive was the only resolve command executed.
        runner = service._runner
        self.assertIsInstance(runner, FakeP4Runner)
        merge_calls = [call for call in runner.calls if call[0] == "resolve" and "-am" in call]
        self.assertEqual(len(merge_calls), 1)
        self.assertTrue(self.world["files"][self.merge_py.as_posix()].get("needsResolve"))

    def test_resolve_text_binary_package_is_never_resolved(self) -> None:
        service = self._service()
        result = service.resolve_text([str(self.binary_uasset)])
        payload = result.to_payload()
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["binaryReconciliationRequired"])
        self.assertFalse(payload["submitReady"])
        receipt = payload["receipts"][0]
        self.assertEqual(receipt["code"], "binary-package-resolve-required")
        runner = service._runner
        self.assertIsInstance(runner, FakeP4Runner)
        self.assertFalse(any(call[0] == "resolve" and "-am" in call for call in runner.calls))

    def test_resolve_text_non_whitelisted_extension_left_for_human(self) -> None:
        service = self._service()
        payload = service.resolve_text([str(self.note_txt)]).to_payload()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["receipts"][0]["code"], "not-eligible-text-resolve")
        runner = service._runner
        self.assertIsInstance(runner, FakeP4Runner)
        self.assertFalse(any(call[0] == "resolve" and "-am" in call for call in runner.calls))

    def test_resolve_text_changelist_scope_mismatch_never_runs_merge(self) -> None:
        self.world["files"][self.merge_py.as_posix()]["change"] = "2001"
        service = self._service()
        # A second owned CL is valid, but the file is not in it.
        self.world["pendingChanges"]["2002"] = {
            "status": "pending", "user": "alice", "client": "alice_ws",
            "description": "other", "files": [],
        }
        payload = service.resolve_text([str(self.merge_py)], changelist_id="2002").to_payload()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["receipts"][0]["code"], "changelist-scope-mismatch")
        self.assertEqual(payload["auditReceipt"]["changelistId"], "2002")
        runner = service._runner
        self.assertIsInstance(runner, FakeP4Runner)
        self.assertFalse(any(call[0] == "resolve" and "-am" in call for call in runner.calls))

    def test_resolve_text_scoped_merge_always_uses_c_flag(self) -> None:
        self.world["files"][self.merge_py.as_posix()]["change"] = "2001"
        self.world["pendingChanges"]["2001"]["files"] = ["//depot/Content/merge.py"]
        service = self._service()
        payload = service.resolve_text([str(self.merge_py)], changelist_id="2001").to_payload()
        self.assertTrue(payload["ok"])
        runner = service._runner
        self.assertIsInstance(runner, FakeP4Runner)
        merge_calls = [call for call in runner.calls if call[0] == "resolve" and "-am" in call]
        self.assertEqual(merge_calls, [["resolve", "-am", "-c", "2001", str(self.merge_py)]])

    def test_resolve_text_noop_when_already_resolved(self) -> None:
        service = self._service()
        payload = service.resolve_text([str(self.open_py)]).to_payload()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["allResolved"])
        self.assertEqual(payload["receipts"][0]["code"], "already-resolved")
        runner = service._runner
        self.assertIsInstance(runner, FakeP4Runner)
        self.assertFalse(any(call[0] == "resolve" and "-am" in call for call in runner.calls))

    def test_resolve_text_requires_current_client_open(self) -> None:
        service = self._service()
        payload = service.resolve_text([str(self.closed_py)]).to_payload()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["receipts"][0]["code"], "not-open-current-client")

    def test_resolve_text_receipt_is_durable_and_restart_readable(self) -> None:
        audit_root = self.temp / "audit-root"
        service = self._service(audit_report_root=audit_root)
        payload = service.resolve_text([str(self.merge_py)]).to_payload()
        receipt = payload["auditReceipt"]
        self.assertTrue(receipt["persisted"])
        self.assertIsNotNone(receipt["receiptPath"])
        path = Path(receipt["receiptPath"])
        self.assertTrue(path.exists())
        self.assertIn("source-control", path.parts)
        self.assertTrue(path.name.startswith("sc_"))
        stored = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(stored["operation"], "resolve-text")
        self.assertEqual(stored["manualFinalAction"], "none")
        self.assertFalse(stored["submitCapability"])
        self.assertFalse(stored["revertCapability"])
        self.assertFalse(stored["deleteCapability"])
        self.assertEqual(stored["exactFiles"], [str(self.merge_py)])
        self.assertTrue(stored["preState"])
        self.assertTrue(stored["postState"])

    def test_no_submit_revert_delete_capability_surface(self) -> None:
        service = self._service()
        prepare = service.prepare_changelist([str(self.open_py)], "capability proof").to_payload()
        self.assertEqual(prepare["manualFinalAction"], "none")
        resolve = service.resolve_text([str(self.merge_py)]).to_payload()
        self.assertEqual(resolve["manualFinalAction"], "none")
        for receipt in (prepare["auditReceipt"], resolve["auditReceipt"]):
            self.assertFalse(receipt["submitCapability"])
            self.assertFalse(receipt["revertCapability"])
            self.assertFalse(receipt["deleteCapability"])


def _remove_tree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
