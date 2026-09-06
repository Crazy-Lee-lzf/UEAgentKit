"""C1/C2 minimum Perforce collaboration layer for UEAgentKit.

This module is intentionally narrow and advisory:

* ``P4CommandRunner`` executes only a fixed allowlist of structured P4
  operations through ``subprocess`` argv arrays (never through a shell and
  never arbitrary command strings).
* ``P4SourceControlService`` exposes two public operations: read-only status
  awareness (C1) and bounded advisory + local-write assistance (C2).
* P4 collaboration state is advisory. It must never independently hard-block
  a local UEAgentKit Writer operation. ``submit``, ``revert`` and ``delete``
  are permanently human-only and are not reachable through this module.

The destructive-operation boundary is defined by
``docs/Plans/UEAGENTKIT_P4_AGENT_OPERATION_BOUNDARY_DECISION_20260903.md``.
"""

from __future__ import annotations

import hashlib
import io
import itertools
import json
import marshal
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

P4_DEFAULT_TIMEOUT_SECONDS = 2.0
P4_MAX_TIMEOUT_SECONDS = 10.0
MAX_FILES_PER_REQUEST = 16
MAX_PATH_CHARS = 1024
MAX_OTHER_USERS = 8
MAX_WARNINGS_PER_FILE = 8
P4_MAX_STDOUT_BYTES = 2 * 1024 * 1024
P4_MAX_STDERR_BYTES = 64 * 1024

# C3 changelist / resolve hard bounds.
MAX_PENDING_CHANGELISTS = 50
MAX_FILES_PER_CHANGELIST = 100
MAX_DESCRIPTION_UTF8_BYTES = 4096
MAX_CHANGELIST_ID_DIGITS = 12
MAX_CHANGE_SPEC_STDIN_BYTES = 64 * 1024
TEXT_RESOLVE_EXTENSIONS = frozenset({".cpp", ".h", ".ini", ".json", ".csv", ".py"})
BINARY_PACKAGE_EXTENSIONS = frozenset({".uasset", ".umap"})
_MANUAL_FINAL_ACTIONS = frozenset({"none", "submit", "revert", "delete"})
_PENDING_STATUSES = frozenset({"pending", "new"})
_CLIENT_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-/@]+$")

# Read-only commands plus the structured mutation operations allowed inside the
# runner. ``sync`` is reachable only through the safe-sync precondition gate;
# ``edit`` only through the explicit checkout assistance path. C3 adds the
# bounded pending-changelist / reopen / resolve family; every one of those
# commands is validated with an exact argv shape, never by command name alone.
_ALLOWED_COMMANDS = frozenset(
    {
        "info",
        "where",
        "fstat",
        "opened",
        "diff",
        "sync",
        "edit",
        "client",
        "changes",
        "change",
        "reopen",
        "resolve",
    }
)
# Explicitly prohibited even inside private runner APIs.
_PROHIBITED_COMMANDS = frozenset(
    {
        "submit",
        "revert",
        "delete",
        "obliterate",
        "unlock",
        "lock",
        "admin",
        "protect",
        "groups",
        "user",
        "passwd",
        "login",
        "logout",
        "ticket",
        "print",
        "tag",
        "labelsync",
        "populate",
        "integrate",
        "merge",
        "shelve",
        "unshelve",
        "changelist",
        "counter",
        "triggers",
        "typemap",
        "branch",
        "label",
        "configure",
        "server",
        "journal",
        "verify",
        "dbstat",
        "repair",
    }
)

# Options the runner is allowed to see in internally built argv, per command.
# The C3 commands below the line are validated by exact argv shape instead.
_ALLOWED_OPTIONS: dict[str, frozenset[str]] = {
    "opened": frozenset({"-a", "-c"}),
    "diff": frozenset({"-se", "-sd"}),
    "client": frozenset({"-o"}),
    "sync": frozenset({"-n"}),
    "info": frozenset(),
    "where": frozenset(),
    "fstat": frozenset(),
    "edit": frozenset(),
}

# Commands whose argv is checked token-by-token with a typed grammar.
_TYPED_ARGV_COMMANDS = frozenset({"changes", "change", "reopen", "resolve", "opened"})
_RESOLVE_ALLOWED_OPTIONS = frozenset({"-n", "-o", "-am", "-c"})
_RESOLVE_FORBIDDEN_OPTIONS = frozenset({"-a", "-A", "-af", "-at", "-ay", "-d", "-f", "-t", "-v", "-N"})

_PATH_WILDCARDS = ("...", "*", "%", "#", "@")
_PATH_START_DISALLOWED = ("-", "//")


class SourceControlError(Exception):
    """Base error for the C1/C2 source-control layer."""


class SourceControlValidationError(SourceControlError, ValueError):
    """A caller-supplied request violated a hard bound (paths, flags, /Game mapping).

    Also a ``ValueError`` so the shared MCP error mapper emits the stable
    ``invalid-arguments`` code without bespoke wiring."""


class SourceControlProhibitedOperationError(SourceControlError):
    """A prohibited P4 operation was requested through an internal runner API."""


class SourceControlCommandError(SourceControlError):
    """A structured P4 invocation failed at the subprocess layer."""


class SourceControlMutationVerificationError(SourceControlCommandError):
    """A P4 mutation may have occurred, but its post-state could not be verified."""

    def __init__(self, message: str, *, operation: str, changelist_id: str = "") -> None:
        super().__init__(message)
        self.operation = operation
        self.changelist_id = changelist_id


@dataclass(frozen=True)
class SourceControlWarning:
    """A bounded advisory message attached to a file or the whole request."""

    severity: str  # "info" | "warning" | "strong-warning"
    code: str
    message: str

    def to_payload(self) -> dict[str, str]:
        return {"severity": self.severity, "code": self.code, "message": self.message}


def _decode_marshal_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, list):
        return [_decode_marshal_value(item) for item in value]
    return value


def _decode_marshal_records(payload: bytes) -> list[dict[str, Any]]:
    """Decode a ``p4 -G`` byte stream into normalized record dicts.

    Perforce ``-G`` marshalled values are raw bytes on modern clients; keys and
    string values are normalized to ``str`` with utf-8 replacement decoding so
    product state never depends on locale-sensitive human text. Records are
    consecutive marshal dicts with no separator byte, so each record is parsed
    precisely with ``marshal.load`` until the stream is exhausted.
    """
    stream = io.BytesIO(payload)
    records: list[dict[str, Any]] = []
    payload_size = len(payload)
    while stream.tell() < payload_size:
        try:
            obj = marshal.load(stream)
        except EOFError as exc:
            raise SourceControlCommandError("Malformed or truncated p4 -G marshal output.") from exc
        except Exception as exc:
            raise SourceControlCommandError("Malformed or truncated p4 -G marshal output.") from exc
        if not isinstance(obj, dict) or not obj:
            raise SourceControlCommandError("Malformed p4 -G output: expected a non-empty record dictionary.")
        records.append(
            {_decode_marshal_value(key): _decode_marshal_value(value) for key, value in obj.items()}
        )
    return records


def _is_error_record(record: dict[str, Any]) -> bool:
    return record.get("code") == "error"


def _is_no_resolve_record(record: dict[str, Any]) -> bool:
    """Recognize P4's benign tagged response for an exact file with no resolve work."""
    if not _is_error_record(record):
        return False
    data = str(record.get("data") or record.get("message") or "").strip().lower()
    try:
        generic = int(record.get("generic", -1))
        severity = int(record.get("severity", -1))
    except (TypeError, ValueError):
        return False
    return generic == 17 and severity == 2 and data.endswith(" - no file(s) to resolve.")


def _error_text(records: Sequence[dict[str, Any]]) -> Optional[str]:
    for record in records:
        if _is_error_record(record):
            data = record.get("data")
            if isinstance(data, str):
                return data.strip()
    return None


def _normalize_local(path: Optional[Path]) -> str:
    text = str(path)
    return text.replace("\\", "/").lower()


def _validate_path_argument(path: str, *, index: int) -> str:
    if not path:
        raise SourceControlValidationError(f"file[{index}] must be a non-empty string.")
    if len(path) > MAX_PATH_CHARS:
        raise SourceControlValidationError(f"file[{index}] exceeds the {MAX_PATH_CHARS} character path limit.")
    if "\x00" in path:
        raise SourceControlValidationError(f"file[{index}] contains a NUL byte.")
    if path.startswith(_PATH_START_DISALLOWED):
        raise SourceControlValidationError(
            f"file[{index}] must be a local filesystem path or /Game package path, not a depot or option token."
        )
    if any(token in path for token in _PATH_WILDCARDS):
        raise SourceControlValidationError(
            f"file[{index}] must name exactly one file; wildcards and revision syntax are not allowed."
        )
    return path


def _bounded_path_list(paths: Sequence[str]) -> list[str]:
    if not paths:
        raise SourceControlValidationError("At least one exact file path is required.")
    if len(paths) > MAX_FILES_PER_REQUEST:
        raise SourceControlValidationError(f"At most {MAX_FILES_PER_REQUEST} files are allowed per request.")
    return [_validate_path_argument(path, index=index) for index, path in enumerate(paths)]


def _validate_description(description: str) -> str:
    """Validate and normalize a pending-changelist description (4096 UTF-8 bytes)."""
    if not isinstance(description, str):
        raise SourceControlValidationError("description must be a string.")
    text = description.strip()
    if not text:
        raise SourceControlValidationError("description must not be empty.")
    if len(text.encode("utf-8")) > MAX_DESCRIPTION_UTF8_BYTES:
        raise SourceControlValidationError(
            f"description exceeds the {MAX_DESCRIPTION_UTF8_BYTES} UTF-8 byte limit."
        )
    return text


# ---------------------------------------------------------------------------
# C3 typed argv validation helpers.
#
# The C3 command family (changes/change/reopen/resolve and the ``opened -c``
# form) is validated with an exact typed grammar, not by command name alone.
# ``change``/``reopen``/``resolve`` remain absent from the legacy per-token
# option scan below so their sensitive flags cannot be reached by accident.
# ---------------------------------------------------------------------------


def _validate_changelist_id_token(token: str) -> None:
    if (
        not token
        or not token.isdigit()
        or len(token) > MAX_CHANGELIST_ID_DIGITS
        or token != str(int(token))
        or int(token) <= 0
    ):
        raise SourceControlValidationError(
            "A changelist id must be a decimal positive integer without leading zeros."
        )


def _validate_client_name_token(token: str) -> None:
    if not token or len(token) > 256 or not _CLIENT_NAME_RE.fullmatch(token):
        raise SourceControlValidationError("Client names may contain only letters, numbers and . _ - / @")


def _validate_many_paths(tokens: Sequence[str]) -> None:
    for token in tokens:
        _validate_path_argument(token, index=0)


def _validate_changes_argv(rest: Sequence[str]) -> None:
    if len(rest) != 4 or rest[0] != "-s" or rest[1] != "pending" or rest[2] != "-c":
        raise SourceControlProhibitedOperationError(
            "P4 changes argv must have the exact shape: changes -s pending -c <currentClient>."
        )
    _validate_client_name_token(rest[3])


def _validate_change_argv(rest: Sequence[str]) -> None:
    if len(rest) == 1 and rest[0] in {"-o", "-i"}:
        return
    if len(rest) == 2 and rest[0] == "-o":
        _validate_changelist_id_token(rest[1])
        return
    raise SourceControlProhibitedOperationError(
        "P4 change argv must be one of: change -o | change -o <id> | change -i."
    )


def _validate_reopen_argv(rest: Sequence[str]) -> None:
    if len(rest) < 3 or rest[0] != "-c":
        raise SourceControlProhibitedOperationError(
            "P4 reopen argv must have the exact shape: reopen -c <pendingChangeId> <exactFiles...>."
        )
    _validate_changelist_id_token(rest[1])
    _validate_many_paths(rest[2:])


def _validate_opened_argv(rest: Sequence[str]) -> None:
    if rest and rest[0] == "-a":
        _validate_many_paths(rest[1:])
        return
    if len(rest) >= 3 and rest[0] == "-c":
        _validate_changelist_id_token(rest[1])
        _validate_many_paths(rest[2:])
        return
    raise SourceControlProhibitedOperationError(
        "P4 opened argv must have the exact shape: opened -a <files...> | opened -c <id> <files...>."
    )


def _validate_resolve_argv(rest: Sequence[str]) -> None:
    if not rest:
        raise SourceControlProhibitedOperationError("P4 resolve argv must not be empty.")
    if any(token in _RESOLVE_FORBIDDEN_OPTIONS for token in rest):
        for token in rest:
            if token in _RESOLVE_FORBIDDEN_OPTIONS:
                raise SourceControlProhibitedOperationError(
                    f"P4 resolve option is outside the frozen C3 merge surface: {token}"
                )
    modes: set[str] = set()
    paths: list[str] = []
    index = 0
    while index < len(rest):
        token = rest[index]
        if token.startswith("-"):
            if token not in _RESOLVE_ALLOWED_OPTIONS:
                raise SourceControlProhibitedOperationError(
                    f"Option token is not allowed for P4 resolve: {token}"
                )
            if token == "-c":
                index += 1
                if index >= len(rest):
                    raise SourceControlProhibitedOperationError(
                        "P4 resolve -c requires a pending changelist id argument."
                    )
                _validate_changelist_id_token(rest[index])
                index += 1
                continue
            modes.add(token)
            index += 1
            continue
        paths.append(token)
        index += 1
    if not paths:
        raise SourceControlProhibitedOperationError(
            "P4 resolve requires at least one exact file path; wildcard/bulk resolve is prohibited."
        )
    if not modes:
        raise SourceControlProhibitedOperationError(
            "P4 resolve requires an explicit preview or merge mode (-n or -am); interactive resolve is prohibited."
        )
    if "-am" in modes and (modes & {"-n", "-o"}):
        raise SourceControlProhibitedOperationError(
            "P4 resolve -am must not be combined with preview flags -n/-o."
        )
    if "-am" not in modes and "-n" not in modes:
        raise SourceControlProhibitedOperationError(
            "P4 resolve may only run exact conflict-free automatic merge (-am) or an exact preview (-n)."
        )
    _validate_many_paths(paths)


_TYPED_ARGV_VALIDATORS: dict[str, Any] = {
    "changes": _validate_changes_argv,
    "change": _validate_change_argv,
    "reopen": _validate_reopen_argv,
    "resolve": _validate_resolve_argv,
    "opened": _validate_opened_argv,
}


def _validate_stdin_usage(tokens: Sequence[str], stdin_bytes: Optional[bytes]) -> None:
    """``change -i`` is the only C3 command allowed to carry structured stdin."""
    is_change_input = len(tokens) >= 2 and tokens[0] == "change" and tokens[1] == "-i"
    if stdin_bytes is not None and not is_change_input:
        raise SourceControlProhibitedOperationError(
            f"P4 {tokens[0]} does not accept stdin; only change -i receives a typed spec form."
        )
    if is_change_input and stdin_bytes is None:
        raise SourceControlValidationError("change -i requires a typed changelist spec form on stdin.")


def _indexed_spec_values(spec: dict[str, Any], prefix: str) -> list[str]:
    """Return real ``p4 -G`` indexed form fields such as Files0/Files1 in order."""
    indexed: list[tuple[int, str]] = []
    for key, value in spec.items():
        if not isinstance(key, str) or not key.startswith(prefix):
            continue
        suffix = key[len(prefix):]
        if suffix.isdigit():
            indexed.append((int(suffix), str(value)))
    indexed.sort(key=lambda item: item[0])
    return [value for _, value in indexed]


def _marshal_change_spec(form: dict[str, Any]) -> bytes:
    """Marshal a typed changelist spec into the real indexed ``p4 -G`` form."""
    payload: dict[Any, Any] = {}
    for key, value in form.items():
        if not isinstance(key, str) or not key:
            raise SourceControlValidationError("Change spec keys must be non-empty strings.")
        if isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                payload[f"{key}{index}".encode("utf-8")] = str(item).encode("utf-8")
        else:
            payload[key.encode("utf-8")] = str(value).encode("utf-8")
    return marshal.dumps(payload)


def _validate_manual_final_action(value: str) -> str:
    action = str(value or "none").strip().lower()
    if action not in _MANUAL_FINAL_ACTIONS:
        raise SourceControlValidationError(
            "manual_final_action must be one of: none, submit, revert, delete."
        )
    return action


@dataclass(frozen=True)
class _P4CommandResult:
    exit_code: int
    records: tuple[dict[str, Any], ...]
    stderr_text: str
    duration_ms: float
    timed_out: bool = False


class P4CommandRunner:
    """Structured, allowlisted P4 subprocess runner.

    Only internal operation builders may construct argv. The runner re-validates
    the command against the allowlist and validates each command with either the
    legacy per-token option scan (C1/C2 commands) or the exact typed C3 argv
    grammar. Arbitrary command strings and shell execution are not
    representable. ``change -i`` is the only command that may carry stdin, and
    it accepts only a typed marshal spec form built by this module.
    """

    def __init__(
        self,
        *,
        p4_executable: Optional[str] = None,
        timeout_seconds: float = P4_DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._p4_executable = p4_executable or shutil.which("p4") or "p4"
        if not 0.1 <= timeout_seconds <= P4_MAX_TIMEOUT_SECONDS:
            raise SourceControlValidationError("timeout_seconds must be from 0.1 through 10.0.")
        self._timeout_seconds = timeout_seconds

    @property
    def executable(self) -> str:
        return self._p4_executable

    def _validate_argv(self, tokens: Sequence[str]) -> None:
        if not tokens:
            raise SourceControlProhibitedOperationError("Empty P4 argv is prohibited.")
        command = tokens[0]
        if command in _PROHIBITED_COMMANDS:
            raise SourceControlProhibitedOperationError(f"Prohibited P4 operation: {command}")
        if command not in _ALLOWED_COMMANDS:
            raise SourceControlProhibitedOperationError(f"P4 operation is outside the allowlist: {command}")
        if command in _TYPED_ARGV_VALIDATORS:
            _TYPED_ARGV_VALIDATORS[command](tokens[1:])
            return
        allowed_options = _ALLOWED_OPTIONS.get(command, frozenset())
        for token in tokens[1:]:
            if token.startswith("-"):
                if token not in allowed_options:
                    raise SourceControlProhibitedOperationError(
                        f"Option token is not allowed for P4 {command}: {token}"
                    )
                continue
            _validate_path_argument(token, index=0)

    def run(self, argv: Sequence[str], *, stdin_bytes: Optional[bytes] = None) -> _P4CommandResult:
        tokens = [str(token) for token in argv]
        self._validate_argv(tokens)
        _validate_stdin_usage(tokens, stdin_bytes)
        if stdin_bytes is not None and len(stdin_bytes) > MAX_CHANGE_SPEC_STDIN_BYTES:
            raise SourceControlValidationError(
                f"change -i spec exceeds the {MAX_CHANGE_SPEC_STDIN_BYTES} byte stdin limit."
            )
        started = time.perf_counter()
        try:
            with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
                proc = subprocess.run(
                    [self._p4_executable, "-G", *tokens],
                    stdout=stdout_file,
                    stderr=stderr_file,
                    input=stdin_bytes,
                    timeout=self._timeout_seconds,
                )
                stdout_size = stdout_file.tell()
                stderr_size = stderr_file.tell()
                if stdout_size > P4_MAX_STDOUT_BYTES:
                    raise SourceControlCommandError(
                        f"P4 {tokens[0]} output exceeded the {P4_MAX_STDOUT_BYTES} byte stdout limit."
                    )
                if stderr_size > P4_MAX_STDERR_BYTES:
                    raise SourceControlCommandError(
                        f"P4 {tokens[0]} output exceeded the {P4_MAX_STDERR_BYTES} byte stderr limit."
                    )
                stdout_file.seek(0)
                stderr_file.seek(0)
                stdout_payload = stdout_file.read(P4_MAX_STDOUT_BYTES + 1)
                stderr_payload = stderr_file.read(P4_MAX_STDERR_BYTES + 1)
        except subprocess.TimeoutExpired as exc:
            raise SourceControlCommandError(
                f"P4 {tokens[0]} timed out after {self._timeout_seconds:g}s."
            ) from exc
        except OSError as exc:
            raise SourceControlCommandError(
                f"Unable to start the P4 executable '{self._p4_executable}': {exc}"
            ) from exc
        records = tuple(_decode_marshal_records(stdout_payload))
        return _P4CommandResult(
            exit_code=proc.returncode,
            records=records,
            stderr_text=stderr_payload.decode("utf-8", errors="replace")[:2000],
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )


@dataclass(frozen=True)
class ResolvedInputPath:
    input_path: str
    local_path: Optional[Path]
    exists: bool
    error: Optional[str] = None


def resolve_input_paths(inputs: Sequence[str], *, project_root: Optional[Path] = None) -> tuple[ResolvedInputPath, ...]:
    """Resolve exact local /Game inputs to exactly one existing candidate.

    For ``/Game/...`` package paths only the normal project mount is mapped in
    C1/C2 (``<Project>/Content/<Path>.uasset`` or ``.umap``). Both/neither
    existing candidates are ambiguous and are reported, never guessed.
    """
    bounded = _bounded_path_list(inputs)
    resolved: list[ResolvedInputPath] = []
    for raw in bounded:
        if raw.startswith("/Game/"):
            if project_root is None:
                resolved.append(
                    ResolvedInputPath(
                        input_path=raw,
                        local_path=None,
                        exists=False,
                        error="game-path-mapping-requires-project-root",
                    )
                )
                continue
            relative = raw[len("/Game/") :].replace("/", os.sep)
            content_root = (project_root.expanduser().resolve() / "Content").resolve()
            candidates = [content_root / f"{relative}.uasset", content_root / f"{relative}.umap"]
            existing: list[Path] = []
            for candidate in candidates:
                try:
                    candidate.resolve().relative_to(content_root)
                except ValueError:
                    resolved.append(
                        ResolvedInputPath(
                            input_path=raw,
                            local_path=None,
                            exists=False,
                            error="game-path-outside-content",
                        )
                    )
                    break
                if candidate.exists():
                    existing.append(candidate)
            if existing:
                if len(existing) == 1:
                    resolved.append(ResolvedInputPath(input_path=raw, local_path=existing[0], exists=True))
                else:
                    resolved.append(
                        ResolvedInputPath(
                            input_path=raw,
                            local_path=None,
                            exists=False,
                            error="game-path-ambiguous-or-missing",
                        )
                    )
            elif not any(item.input_path == raw and item.error == "game-path-outside-content" for item in resolved):
                resolved.append(
                    ResolvedInputPath(
                        input_path=raw,
                        local_path=None,
                        exists=False,
                        error="game-path-ambiguous-or-missing",
                    )
                )
            continue
        local = Path(raw)
        if not local.is_absolute():
            local = Path.cwd() / local
        local = local.resolve()
        resolved.append(ResolvedInputPath(input_path=raw, local_path=local, exists=local.exists()))
    return tuple(resolved)


def _file_writable(path: Optional[Path]) -> Optional[bool]:
    if path is None or not path.exists():
        return None
    try:
        mode = path.stat().st_mode
    except OSError:
        return None
    return bool(mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def make_local_writable(path: Path) -> tuple[Optional[str], Optional[str]]:
    """Remove local readonly protection only. Returns (before_mode, after_mode)."""
    before = None
    try:
        before = stat.filemode(path.stat().st_mode)
    except OSError:
        before = None
    try:
        os.chmod(path, stat.S_IWUSR | stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    except OSError:
        pass
    after = None
    try:
        after = stat.filemode(path.stat().st_mode)
    except OSError:
        after = None
    return before, after


@dataclass(frozen=True)
class SourceControlFileState:
    """Bounded structured P4 collaboration facts for one exact file."""

    input_path: str
    local_path: Optional[str]
    depot_path: str = ""
    client_path: str = ""
    mapped: bool = False
    provider_available: bool = False
    file_type: str = ""
    exclusive_lock_type: str = ""
    have_rev: Optional[str] = None
    head_rev: Optional[str] = None
    head_action: str = ""
    opened_for_edit: bool = False
    opened_by_current_client: bool = False
    action: str = ""
    change: str = ""
    other_open_users: tuple[str, ...] = ()
    locked_by_other: bool = False
    other_lock_users: tuple[str, ...] = ()
    behind_head: bool = False
    local_modified: Optional[bool] = None
    writable: Optional[bool] = None
    local_writable_override: bool = False
    file_exists: bool = False
    path_error: Optional[str] = None
    source_control_ready: bool = False
    submit_ready: bool = False
    local_test_ready: bool = True
    warnings: tuple[SourceControlWarning, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "inputPath": self.input_path,
            "localPath": self.local_path,
            "depotPath": self.depot_path,
            "clientPath": self.client_path,
            "mapped": self.mapped,
            "providerAvailable": self.provider_available,
            "fileType": self.file_type,
            "exclusiveLockType": self.exclusive_lock_type,
            "haveRev": self.have_rev,
            "headRev": self.head_rev,
            "headAction": self.head_action,
            "openedForEdit": self.opened_for_edit,
            "openedByCurrentClient": self.opened_by_current_client,
            "action": self.action,
            "change": self.change,
            "otherOpenUsers": list(self.other_open_users[:MAX_OTHER_USERS]),
            "lockedByOther": self.locked_by_other,
            "otherLockUsers": list(self.other_lock_users[:MAX_OTHER_USERS]),
            "behindHead": self.behind_head,
            "localModified": self.local_modified,
            "writable": self.writable,
            "localWritableOverride": self.local_writable_override,
            "fileExists": self.file_exists,
            "pathError": self.path_error,
            "sourceControlReady": self.source_control_ready,
            "submitReady": self.submit_ready,
            "localTestReady": self.local_test_ready,
            "warnings": [warning.to_payload() for warning in self.warnings[:MAX_WARNINGS_PER_FILE]],
        }


@dataclass(frozen=True)
class SourceControlStatusResult:
    provider_available: bool
    server_version: str
    client_name: str
    user_name: str
    server_address: str
    files: tuple[SourceControlFileState, ...]

    def to_payload(self) -> dict[str, Any]:
        mapped = sum(1 for file_state in self.files if file_state.mapped)
        opened = sum(1 for file_state in self.files if file_state.opened_for_edit)
        behind = sum(1 for file_state in self.files if file_state.behind_head)
        warnings: list[dict[str, str]] = []
        for file_state in self.files:
            warnings.extend(warning.to_payload() for warning in file_state.warnings[:MAX_WARNINGS_PER_FILE])
        return {
            "schemaVersion": "1.0",
            "tool": "ue_source_control_status",
            "ok": True,
            "readOnly": True,
            "provider": {
                "available": self.provider_available,
                "serverVersion": self.server_version,
                "serverAddress": self.server_address,
                "clientName": self.client_name,
                "userName": self.user_name,
            },
            "fileCount": len(self.files),
            "summary": {"mapped": mapped, "openedForEdit": opened, "behindHead": behind},
            "files": [file_state.to_payload() for file_state in self.files],
            "warnings": warnings,
        }


@dataclass(frozen=True)
class SourceControlPrepareResult:
    """C2 assistance receipts: checkout / override / safe-sync attempts and outcomes."""

    provider_available: bool
    server_version: str
    client_name: str
    user_name: str
    server_address: str
    files: tuple[SourceControlFileState, ...]
    receipts: tuple[dict[str, Any], ...]
    actions: tuple[str, ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "schemaVersion": "1.0",
            "tool": "ue_source_control_prepare_write",
            "ok": True,
            "readOnly": False,
            "provider": {
                "available": self.provider_available,
                "serverVersion": self.server_version,
                "serverAddress": self.server_address,
                "clientName": self.client_name,
                "userName": self.user_name,
            },
            "actions": list(self.actions),
            "receipts": list(self.receipts),
            "files": [file_state.to_payload() for file_state in self.files],
        }


@dataclass(frozen=True)
class _ProviderInfo:
    available: bool
    version: str = ""
    address: str = ""
    client: str = ""
    user: str = ""


def _int_or_none(value: Any) -> Optional[int]:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _record_matches_path(record: dict[str, Any], local_text: str) -> bool:
    client_file = str(record.get("clientFile", "")).replace("\\", "/").lower()
    return client_file == local_text.replace("\\", "/").lower()


class P4SourceControlService:
    """Advisory C1/C2 service over a structured ``P4CommandRunner``.

    Provider unavailability or a timeout degrades to advisory metadata
    (``provider.available=false`` plus warnings). It never makes the caller's
    local operation fail and never fabricates Writer-owned safety flags.
    """

    def __init__(
        self,
        *,
        p4_executable: Optional[str] = None,
        timeout_seconds: float = P4_DEFAULT_TIMEOUT_SECONDS,
        project_root: Optional[Path] = None,
        audit_report_root: Optional[Path] = None,
    ) -> None:
        self._runner = P4CommandRunner(p4_executable=p4_executable, timeout_seconds=timeout_seconds)
        self._project_root = Path(project_root).resolve() if project_root is not None else None
        self._audit_report_root = Path(audit_report_root).expanduser().resolve() if audit_report_root is not None else None
        self._provider_cache: Optional[_ProviderInfo] = None

    @property
    def project_root(self) -> Optional[Path]:
        return self._project_root

    @property
    def audit_report_root(self) -> Optional[Path]:
        return self._audit_report_root

    def clear_provider_cache(self) -> None:
        self._provider_cache = None

    def _probe_provider(self) -> _ProviderInfo:
        if self._provider_cache is not None:
            return self._provider_cache
        info = _ProviderInfo(available=False)
        try:
            result = self._runner.run(["info"])
        except SourceControlCommandError:
            self._provider_cache = info
            return info
        record = next((item for item in result.records if not _is_error_record(item)), None)
        if record is None:
            self._provider_cache = info
            return info
        info = _ProviderInfo(
            available=True,
            version=str(record.get("serverVersion", "")),
            address=str(record.get("serverAddress", "")),
            client=str(record.get("clientName", "")),
            user=str(record.get("userName", "")),
        )
        self._provider_cache = info
        return info

    def provider_capabilities(self) -> dict[str, Any]:
        info = self._probe_provider()
        return {
            "available": info.available,
            "serverVersion": info.version,
            "serverAddress": info.address,
            "clientName": info.client,
            "userName": info.user,
            "executable": self._runner.executable,
        }

    def _fstat_by_local(self, local_paths: Sequence[str]) -> dict[str, dict[str, Any]]:
        result = self._runner.run(["fstat", *local_paths])
        by_local: dict[str, dict[str, Any]] = {}
        for record in result.records:
            if _is_error_record(record):
                continue
            client_file = str(record.get("clientFile", ""))
            if not client_file:
                continue
            key = client_file.replace("\\", "/").lower()
            by_local.setdefault(key, record)
        return by_local

    def _opened_records(self, local_paths: Sequence[str]) -> list[dict[str, Any]]:
        result = self._runner.run(["opened", "-a", *local_paths])
        records: list[dict[str, Any]] = []
        for record in result.records:
            if _is_error_record(record):
                continue
            if record.get("depotFile") and record.get("user"):
                records.append(record)
        return records

    def _diff_probe(self, local_paths: Sequence[str]) -> tuple[set[str], bool]:
        """Return (depot files with local difference, group_verified_clean).

        ``diff -se`` / ``diff -sd`` emit zero records for a clean exact path.
        Benign tagged errors ("no such file(s)", "up-to-date") are expected when
        a batch contains unmapped files or an opened clean file and do not make
        the group dirty. ``group_verified_clean`` is True only when no content
        difference records and no unexpected errors were produced.
        """
        changed: set[str] = set()
        verified = False
        try:
            se = self._runner.run(["diff", "-se", *local_paths])
            sd = self._runner.run(["diff", "-sd", *local_paths])
        except SourceControlCommandError:
            return changed, verified
        unexpected = False

        def examine(result: _P4CommandResult) -> None:
            nonlocal unexpected
            if result.exit_code != 0:
                unexpected = True
                return
            for record in result.records:
                if _is_error_record(record):
                    data = str(record.get("data", ""))
                    if any(token in data for token in ("no such file", "not on client", "not in client", "up-to-date")):
                        continue
                    unexpected = True
                    continue
                depot_file = record.get("depotFile")
                if isinstance(depot_file, str) and depot_file:
                    changed.add(depot_file)

        examine(se)
        examine(sd)
        verified = not unexpected
        return changed, verified

    def _query_phase(
        self, local_paths: Sequence[str]
    ) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], set[str], bool, bool]:
        fstat_by_local: dict[str, dict[str, Any]] = {}
        opened_records: list[dict[str, Any]] = []
        changed: set[str] = set()
        verified = False
        query_ok = True
        try:
            if local_paths:
                fstat_by_local = self._fstat_by_local(local_paths)
                opened_records = self._opened_records(local_paths)
                changed, verified = self._diff_probe(local_paths)
        except SourceControlCommandError:
            query_ok = False
        return fstat_by_local, opened_records, changed, verified, query_ok

    # ------------------------------------------------------------------ C1 --
    def status(self, paths: Sequence[str]) -> SourceControlStatusResult:
        resolved = resolve_input_paths(paths, project_root=self._project_root)
        provider = self._probe_provider()
        local_paths = [str(item.local_path) for item in resolved if item.local_path is not None]
        fstat_by_local, opened_records, changed, verified, query_ok = self._query_phase(local_paths)
        if not query_ok and provider.available:
            provider = _ProviderInfo(available=False)
            self._provider_cache = provider
            fstat_by_local, opened_records, changed, verified = {}, [], set(), False

        files = tuple(
            self._build_file_state(
                item,
                provider=provider,
                fstat_by_local=fstat_by_local,
                opened_records=opened_records,
                changed=changed,
                diff_verified=verified,
            )
            for item in resolved
        )
        return SourceControlStatusResult(
            provider_available=provider.available,
            server_version=provider.version,
            client_name=provider.client,
            user_name=provider.user,
            server_address=provider.address,
            files=files,
        )

    def _build_file_state(
        self,
        resolved: ResolvedInputPath,
        *,
        provider: _ProviderInfo,
        fstat_by_local: dict[str, dict[str, Any]],
        opened_records: list[dict[str, Any]],
        changed: set[str],
        diff_verified: bool,
    ) -> SourceControlFileState:
        local = resolved.local_path
        local_text = str(local) if local is not None else None
        file_exists = resolved.exists
        warnings: list[SourceControlWarning] = []
        writable = _file_writable(local)

        if not provider.available:
            warnings.append(
                SourceControlWarning(
                    severity="warning",
                    code="source-control-unavailable",
                    message="P4 provider is unavailable; collaboration state is unknown.",
                )
            )
            return SourceControlFileState(
                input_path=resolved.input_path,
                local_path=local_text,
                provider_available=False,
                writable=writable,
                file_exists=file_exists,
                path_error=resolved.error,
                source_control_ready=False,
                submit_ready=False,
                local_test_ready=True,
                warnings=tuple(warnings),
            )

        if resolved.error is not None:
            message = (
                "Path could not be resolved to exactly one file."
                if resolved.error == "game-path-ambiguous-or-missing"
                else "Mapping /Game package paths requires a configured project root."
            )
            warnings.append(SourceControlWarning(severity="warning", code=resolved.error, message=message))
            return SourceControlFileState(
                input_path=resolved.input_path,
                local_path=local_text,
                provider_available=True,
                writable=None,
                file_exists=False,
                path_error=resolved.error,
                source_control_ready=False,
                submit_ready=False,
                local_test_ready=True,
                warnings=tuple(warnings),
            )

        record: Optional[dict[str, Any]] = None
        if local_text is not None:
            record = fstat_by_local.get(local_text.replace("\\", "/").lower())

        if record is None:
            warnings.append(
                SourceControlWarning(
                    severity="info",
                    code="not-mapped",
                    message="File is not tracked by the P4 depot through this client.",
                )
            )
            return SourceControlFileState(
                input_path=resolved.input_path,
                local_path=local_text,
                mapped=False,
                provider_available=True,
                writable=writable,
                file_exists=file_exists,
                source_control_ready=True,
                submit_ready=False,
                local_test_ready=True,
                warnings=tuple(warnings),
            )

        depot_file = str(record.get("depotFile", ""))
        client_file = str(record.get("clientFile", ""))
        head_rev = str(record.get("headRev", "")) or None
        have_rev = str(record.get("haveRev", "")) or None
        head_type = str(record.get("headType", ""))
        opened_type = str(record.get("type", ""))
        file_type = opened_type or head_type
        head_action = str(record.get("headAction", ""))
        exclusive_lock_type = file_type if "+l" in file_type else ""
        head_num = _int_or_none(head_rev)
        have_num = _int_or_none(have_rev)

        # Current-client open facts: fstat is client-scoped and already reports
        # the current client's action/change when this client has the file open.
        opened_for_edit = bool(record.get("action"))
        own_action = str(record.get("action", ""))
        own_change = str(record.get("change", ""))
        if opened_for_edit and record.get("actionOwner"):
            opened_by_current = str(record.get("actionOwner", "")) == provider.user
        else:
            opened_by_current = opened_for_edit

        other_users: list[str] = []
        other_locks: list[str] = []
        for row in opened_records:
            if str(row.get("depotFile", "")) != depot_file:
                continue
            user = str(row.get("user", ""))
            client = str(row.get("client", ""))
            is_current_client_open = bool(
                user == provider.user and client and client == provider.client
            )
            if is_current_client_open:
                if not opened_by_current:
                    opened_by_current = True
                    opened_for_edit = True
                    own_action = str(row.get("action", own_action))
                    own_change = str(row.get("change", own_change))
                continue
            display_owner = user
            if user == provider.user and client:
                display_owner = f"{user}@{client}"
            if display_owner and display_owner not in other_users:
                other_users.append(display_owner)
            marker = str(row.get("locked", ""))
            is_exclusive = "+l" in file_type or "+l" in head_type
            if is_exclusive or marker.lower() in {"yes", "true"}:
                if display_owner and display_owner not in other_locks:
                    other_locks.append(display_owner)
        other_users = other_users[:MAX_OTHER_USERS]
        other_locks = other_locks[:MAX_OTHER_USERS]
        locked_by_other = bool(other_locks)

        behind_head = bool(have_num is not None and head_num is not None and have_num < head_num)

        local_modified: Optional[bool] = None
        if have_num is not None and local is not None and local.exists():
            if depot_file in changed:
                local_modified = True
            elif diff_verified and writable is False:
                local_modified = False

        if other_users and not locked_by_other:
            warnings.append(
                SourceControlWarning(
                    severity="warning",
                    code="other-user-open",
                    message=f"Opened by other user(s): {', '.join(other_users)}.",
                )
            )
        if locked_by_other:
            warnings.append(
                SourceControlWarning(
                    severity="strong-warning",
                    code="exclusive-lock-other-user",
                    message=f"Exclusive lock held by other user(s): {', '.join(other_locks)}.",
                )
            )
        if behind_head:
            warnings.append(
                SourceControlWarning(
                    severity="warning",
                    code="behind-head",
                    message=f"Workspace is behind head ({have_rev}/{head_rev}).",
                )
            )
        if local_modified is True:
            warnings.append(
                SourceControlWarning(
                    severity="strong-warning",
                    code="local-differs-from-have",
                    message="Workspace copy differs from the have revision.",
                )
            )
        if not opened_for_edit:
            warnings.append(
                SourceControlWarning(
                    severity="info",
                    code="not-opened-for-edit",
                    message="File is not opened for edit; checkout is available as assistance.",
                )
            )

        submit_ready = bool(
            opened_for_edit
            and opened_by_current
            and have_num is not None
            and not behind_head
            and not locked_by_other
        )
        return SourceControlFileState(
            input_path=resolved.input_path,
            local_path=local_text,
            depot_path=depot_file,
            client_path=client_file,
            mapped=True,
            provider_available=True,
            file_type=file_type,
            exclusive_lock_type=exclusive_lock_type,
            have_rev=have_rev,
            head_rev=head_rev,
            head_action=head_action,
            opened_for_edit=opened_for_edit,
            opened_by_current_client=opened_by_current,
            action=own_action,
            change=own_change,
            other_open_users=tuple(other_users),
            locked_by_other=locked_by_other,
            other_lock_users=tuple(other_locks),
            behind_head=behind_head,
            local_modified=local_modified,
            writable=writable,
            file_exists=file_exists,
            source_control_ready=True,
            submit_ready=submit_ready,
            local_test_ready=True,
            warnings=tuple(warnings),
        )

    # ------------------------------------------------------------------ C2 --
    def _safe_sync_permitted(self, file_state: SourceControlFileState, resolved: ResolvedInputPath) -> bool:
        """Deterministic clean precondition for an exact-file sync.

        A sync proceeds only when every precondition is provably clean: mapped,
        exists, have copy present, behind head, not opened, no local override,
        no local modification (diff-verified), no other lock, and the local
        copy is still readonly (P4-managed). This is intentionally conservative.
        """
        if not file_state.mapped or not file_state.provider_available:
            return False
        if resolved.local_path is None or not resolved.local_path.exists():
            return False
        if file_state.have_rev is None or file_state.head_rev is None:
            return False
        if not file_state.behind_head:
            return False
        if file_state.opened_for_edit or file_state.opened_by_current_client:
            return False
        if file_state.local_writable_override:
            return False
        if file_state.local_modified is not False:
            return False
        if file_state.writable is not False:
            return False
        if file_state.locked_by_other:
            return False
        return True

    def prepare_write(
        self,
        paths: Sequence[str],
        *,
        allow_local_writable_override: bool = False,
        request_safe_sync: bool = False,
    ) -> SourceControlPrepareResult:
        resolved = resolve_input_paths(paths, project_root=self._project_root)
        provider = self._probe_provider()
        if not provider.available:
            files = tuple(
                self._build_file_state(
                    item,
                    provider=provider,
                    fstat_by_local={},
                    opened_records=[],
                    changed=set(),
                    diff_verified=False,
                )
                for item in resolved
            )
            return SourceControlPrepareResult(
                provider_available=False,
                server_version="",
                client_name="",
                user_name="",
                server_address="",
                files=files,
                receipts=(),
                actions=(),
            )

        pre = self.status(paths)
        pre_by_input = {file_state.input_path: file_state for file_state in pre.files}

        receipts: list[dict[str, Any]] = []
        sync_targets: list[tuple[ResolvedInputPath, SourceControlFileState]] = []
        edit_targets: list[tuple[ResolvedInputPath, SourceControlFileState]] = []
        skipped: list[tuple[str, str, str]] = []  # (input_path, code, message)

        for item in resolved:
            file_state = pre_by_input.get(item.input_path)
            if file_state is None or not file_state.mapped or file_state.local_path is None:
                continue
            if file_state.opened_for_edit and file_state.opened_by_current_client:
                skipped.append((item.input_path, "already-open-in-current-client", "already open in this client"))
                continue
            if file_state.behind_head:
                if request_safe_sync and self._safe_sync_permitted(file_state, item):
                    # Do not enqueue edit yet. A behind-head file reaches checkout
                    # only after the requested exact clean sync succeeds.
                    sync_targets.append((item, file_state))
                else:
                    skipped.append(
                        (
                            item.input_path,
                            "behind-head-sync-not-clean-or-not-requested",
                            "behind head; exact clean sync not requested or not provably clean",
                        )
                    )
                continue
            # A checkout is attempted even when another user holds an exclusive
            # lock: the P4 failure is surfaced as a receipt instead of being
            # converted into a Writer rejection. Override remains optional.
            edit_targets.append((item, file_state))

        # 1) Safe sync of exact clean files (only when explicitly requested and proven clean).
        for item, _file_state in sync_targets:
            local_text = str(item.local_path)
            try:
                result = self._runner.run(["sync", local_text])
            except SourceControlCommandError as exc:
                receipts.append({"file": item.input_path, "action": "sync", "ok": False, "message": str(exc)})
                continue
            error = _error_text(result.records)
            if error and "up-to-date" not in error:
                receipts.append({"file": item.input_path, "action": "sync", "ok": False, "message": error})
            else:
                receipts.append(
                    {"file": item.input_path, "action": "sync", "ok": True, "message": "synced-exact-clean-file"}
                )
                # The explicit safe-sync succeeded, so this exact file may now
                # proceed to the normal checkout attempt.
                edit_targets.append((item, _file_state))

        # 2) p4 edit / checkout assistance.
        for item, _file_state in edit_targets:
            local_text = str(item.local_path)
            try:
                result = self._runner.run(["edit", local_text])
            except SourceControlCommandError as exc:
                receipts.append({"file": item.input_path, "action": "edit", "ok": False, "message": str(exc)})
                continue
            error = _error_text(result.records)
            if error is None:
                receipts.append({"file": item.input_path, "action": "edit", "ok": True, "message": "opened-for-edit"})
            else:
                receipts.append({"file": item.input_path, "action": "edit", "ok": False, "message": error})

        # 3) Optional local writable override for files that could not be checked out.
        overridden: set[str] = set()
        if allow_local_writable_override:
            for item in resolved:
                file_state = pre_by_input.get(item.input_path)
                if file_state is None or item.local_path is None or not item.local_path.exists():
                    continue
                if file_state.opened_for_edit and file_state.opened_by_current_client:
                    continue
                if not (file_state.mapped and file_state.writable is False):
                    continue
                # Never override a file that a successful checkout already made
                # legitimately writable and opened in this client. A successful
                # sync alone must not suppress override after a later edit failure.
                if any(
                    receipt.get("file") == item.input_path
                    and receipt.get("action") == "edit"
                    and receipt.get("ok") is True
                    for receipt in receipts
                ):
                    continue
                before, after = make_local_writable(item.local_path)
                writable_now = _file_writable(item.local_path)
                if writable_now is True:
                    receipts.append(
                        {
                            "file": item.input_path,
                            "action": "override",
                            "beforeMode": before,
                            "afterMode": after,
                            "ok": True,
                            "message": "local-readonly-override-applied",
                        }
                    )
                    overridden.add(item.input_path)
                else:
                    receipts.append(
                        {
                            "file": item.input_path,
                            "action": "override",
                            "ok": False,
                            "message": "override-failed-to-make-file-writable",
                        }
                    )

        for input_path, code, message in skipped:
            receipts.append({"file": input_path, "action": "none", "ok": False, "message": message, "code": code})

        # 4) Post-state capture; re-apply the override marker so the response is
        # truthful (the file is not legitimately opened in P4).
        post = self.status(paths)
        post_by_input = {file_state.input_path: file_state for file_state in post.files}
        final_files: list[SourceControlFileState] = []
        for item in resolved:
            state = post_by_input.get(item.input_path)
            if state is None:
                state = pre_by_input[item.input_path]
            if state is not None and item.input_path in overridden:
                warnings = list(state.warnings)
                warnings.append(
                    SourceControlWarning(
                        severity="strong-warning",
                        code="local-writable-override",
                        message=(
                            "Local readonly protection was removed without a P4 checkout; "
                            "the file is not opened in P4 and is not submit-ready."
                        ),
                    )
                )
                state = replace(
                    state,
                    writable=True,
                    local_writable_override=True,
                    submit_ready=False,
                    opened_for_edit=False,
                    opened_by_current_client=False,
                    warnings=tuple(warnings),
                )
            if state is not None:
                final_files.append(state)

        actions = tuple(receipt for receipt in receipts if receipt["action"] != "none")
        return SourceControlPrepareResult(
            provider_available=provider.available,
            server_version=provider.version,
            client_name=provider.client,
            user_name=provider.user,
            server_address=provider.address,
            files=tuple(final_files),
            receipts=tuple(receipts),
            actions=tuple(receipt["action"] for receipt in actions),
        )

    # ------------------------------------------------------------------ C3 --
    # Pending changelist read / prepare, resolve preview and bounded text
    # resolve. Everything stays on the current user/current client boundary and
    # submit/revert/delete remain unreachable.

    def _read_changelist_spec(self, changelist_id: str) -> Optional[dict[str, Any]]:
        """Return ``change -o <id>`` or fail closed on provider/query errors."""
        result = self._runner.run(["change", "-o", changelist_id])
        if result.exit_code != 0:
            raise SourceControlCommandError(
                result.stderr_text or f"P4 change -o {changelist_id} failed with exit code {result.exit_code}."
            )
        for record in result.records:
            if not _is_error_record(record):
                continue
            error = str(record.get("data") or record.get("message") or "P4 change query failed.")
            lowered = error.lower()
            if "change" in lowered and ("unknown" in lowered or "does not exist" in lowered or "doesn't exist" in lowered):
                return None
            raise SourceControlCommandError(f"P4 change query failed: {error.strip()}")
        for record in result.records:
            if record.get("Change"):
                return record
        raise SourceControlCommandError(
            f"P4 change -o {changelist_id} returned no structured changelist record."
        )

    def _pending_change_records(self, provider: _ProviderInfo) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        result = self._runner.run(["changes", "-s", "pending", "-c", provider.client])
        if result.exit_code != 0:
            raise SourceControlCommandError(
                result.stderr_text or f"P4 pending changelist query failed with exit code {result.exit_code}."
            )
        for record in result.records:
            if _is_error_record(record):
                error = str(record.get("data") or record.get("message") or "P4 pending changelist query failed.")
                raise SourceControlCommandError(f"P4 pending changelist query failed: {error.strip()}")
            if record.get("Change"):
                records.append(record)
        return records

    def _changelist_state_from_spec(
        self, spec: dict[str, Any], provider: _ProviderInfo
    ) -> SourceControlChangelistState:
        changelist_id = str(spec.get("Change", ""))
        user = str(spec.get("User", ""))
        client = str(spec.get("Client", ""))
        status = str(spec.get("Status", "")).lower()
        files = tuple(_indexed_spec_values(spec, "Files"))
        file_count = len(files)
        pending = status in _PENDING_STATUSES
        current_user_owned = user == provider.user
        current_client_owned = client == provider.client
        warnings: list[SourceControlWarning] = []
        if not pending:
            warnings.append(
                SourceControlWarning(
                    severity="warning",
                    code="changelist-not-pending",
                    message=f"Changelist {changelist_id} status is '{status}'; only pending changes are editable.",
                )
            )
        if not current_user_owned or not current_client_owned:
            warnings.append(
                SourceControlWarning(
                    severity="strong-warning",
                    code="changelist-not-current-owner",
                    message="The pending changelist is not owned by the current user/client; no mutation is permitted.",
                )
            )
        if len(files) > MAX_FILES_PER_CHANGELIST:
            warnings.append(
                SourceControlWarning(
                    severity="info",
                    code="changelist-files-truncated",
                    message=f"Changelist contains more than {MAX_FILES_PER_CHANGELIST} files; only the first files are listed.",
                )
            )
        if files:
            warnings.append(
                SourceControlWarning(
                    severity="info",
                    code="submit-ready-not-verified",
                    message="Submit readiness requires per-file status; it is not asserted for a changelist listing.",
                )
            )
        return SourceControlChangelistState(
            changelist_id=changelist_id,
            status=status,
            user=user,
            client=client,
            description=str(spec.get("Description", "")),
            files=files[:MAX_FILES_PER_CHANGELIST],
            file_count=file_count,
            current_user_owned=current_user_owned,
            current_client_owned=current_client_owned,
            pending=pending,
            warnings=tuple(warnings),
        )

    def changelists(self, changelist_id: str = "") -> SourceControlChangelistQueryResult:
        """Read-only bounded pending changelist list / single spec view."""
        provider = self._probe_provider()
        warnings: list[SourceControlWarning] = []
        if not provider.available:
            warnings.append(
                SourceControlWarning(
                    severity="warning",
                    code="source-control-unavailable",
                    message="P4 provider is unavailable; pending changelist state is unknown.",
                )
            )
            return SourceControlChangelistQueryResult(
                provider_available=False,
                server_version="",
                client_name="",
                user_name="",
                server_address="",
                changelists=(),
                requested_id=changelist_id,
                not_found=False,
                warnings=tuple(warnings),
            )

        if changelist_id:
            _validate_changelist_id_token(changelist_id)
            spec = self._read_changelist_spec(changelist_id)
            if spec is None:
                warnings.append(
                    SourceControlWarning(
                        severity="warning",
                        code="changelist-not-found",
                        message=f"Pending changelist {changelist_id} was not found on this server.",
                    )
                )
                return SourceControlChangelistQueryResult(
                    provider_available=True,
                    server_version=provider.version,
                    client_name=provider.client,
                    user_name=provider.user,
                    server_address=provider.address,
                    changelists=(),
                    requested_id=changelist_id,
                    not_found=True,
                    warnings=tuple(warnings),
                )
            return SourceControlChangelistQueryResult(
                provider_available=True,
                server_version=provider.version,
                client_name=provider.client,
                user_name=provider.user,
                server_address=provider.address,
                changelists=(self._changelist_state_from_spec(spec, provider),),
                requested_id=changelist_id,
                not_found=False,
                warnings=tuple(warnings),
            )

        records = self._pending_change_records(provider)
        states: list[SourceControlChangelistState] = []
        for record in records[:MAX_PENDING_CHANGELISTS]:
            spec = self._read_changelist_spec(str(record.get("Change", "")))
            if spec is None:
                continue
            state = self._changelist_state_from_spec(spec, provider)
            if state.pending:
                states.append(state)
        if len(records) > MAX_PENDING_CHANGELISTS:
            warnings.append(
                SourceControlWarning(
                    severity="info",
                    code="pending-changelist-list-truncated",
                    message=f"Pending changelist list truncated to {MAX_PENDING_CHANGELISTS}.",
                )
            )
        return SourceControlChangelistQueryResult(
            provider_available=True,
            server_version=provider.version,
            client_name=provider.client,
            user_name=provider.user,
            server_address=provider.address,
            changelists=tuple(states),
            requested_id="",
            not_found=False,
            warnings=tuple(warnings),
        )

    def _extract_created_change_id(self, records: Sequence[dict[str, Any]]) -> Optional[str]:
        for record in records:
            if _is_error_record(record):
                continue
            text = str(record.get("data", "")) or str(record.get("message", ""))
            match = re.search(r"Change\s+(\d+)\s+created", text)
            if match:
                return match.group(1)
        return None

    def _find_created_pending_by_description(
        self, description: str, provider: _ProviderInfo, pre_ids: list[str]
    ) -> Optional[str]:
        """Fallback confirmation for a created changelist via pending-list diff."""
        try:
            post_ids = [
                str(record.get("Change", ""))
                for record in self._pending_change_records(provider)
            ]
        except SourceControlCommandError:
            return None
        candidates = [change_id for change_id in post_ids if change_id not in pre_ids]
        matching: list[str] = []
        for change_id in candidates:
            spec = self._read_changelist_spec(change_id)
            if spec is None:
                continue
            if (
                str(spec.get("Status", "")).lower() in _PENDING_STATUSES
                and str(spec.get("User", "")) == provider.user
                and str(spec.get("Client", "")) == provider.client
                and str(spec.get("Description", "")) == description
            ):
                matching.append(change_id)
        if len(matching) == 1:
            return matching[0]
        return None

    def _create_pending_changelist(self, description: str, provider: _ProviderInfo) -> str:
        pre_ids = [
            str(record.get("Change", "")) for record in self._pending_change_records(provider)
        ]
        form = {
            "Change": "new",
            "Client": provider.client,
            "User": provider.user,
            "Status": "new",
            "Description": description,
        }
        payload = _marshal_change_spec(form)
        try:
            result = self._runner.run(["change", "-i"], stdin_bytes=payload)
        except SourceControlCommandError as exc:
            raise SourceControlCommandError(f"Pending changelist creation failed: {exc}") from exc
        error = _error_text(result.records)
        if error is not None:
            raise SourceControlCommandError(f"Pending changelist creation was rejected: {error}")
        change_id = self._extract_created_change_id(result.records)
        if change_id is None:
            try:
                change_id = self._find_created_pending_by_description(description, provider, pre_ids)
            except SourceControlCommandError as exc:
                raise SourceControlMutationVerificationError(
                    f"Pending changelist creation may have succeeded, but discovery of the created changelist failed: {exc}",
                    operation="create-changelist",
                ) from exc
        if change_id is None:
            raise SourceControlMutationVerificationError(
                "Pending changelist creation may have succeeded, but its changelist number could not be confirmed.",
                operation="create-changelist",
            )
        try:
            spec = self._read_changelist_spec(change_id)
        except SourceControlCommandError as exc:
            raise SourceControlMutationVerificationError(
                f"Pending changelist {change_id} may have been created, but post-state verification failed: {exc}",
                operation="create-changelist",
                changelist_id=change_id,
            ) from exc
        if spec is None:
            raise SourceControlMutationVerificationError(
                f"Pending changelist {change_id} may have been created, but it could not be re-read.",
                operation="create-changelist",
                changelist_id=change_id,
            )
        if (
            str(spec.get("Status", "")).lower() not in _PENDING_STATUSES
            or str(spec.get("User", "")) != provider.user
            or str(spec.get("Client", "")) != provider.client
        ):
            raise SourceControlMutationVerificationError(
                f"Created changelist {change_id} failed the current user/client pending ownership check.",
                operation="create-changelist",
                changelist_id=change_id,
            )
        return change_id

    def _update_changelist_description(
        self, changelist_id: str, description: str, provider: _ProviderInfo
    ) -> None:
        spec = self._read_changelist_spec(changelist_id)
        if spec is None:
            raise SourceControlValidationError(f"Pending changelist {changelist_id} was not found.")
        status = str(spec.get("Status", "")).lower()
        if status not in _PENDING_STATUSES:
            raise SourceControlValidationError(
                f"Changelist {changelist_id} is not pending ('{status}'); its description cannot be edited."
            )
        if (
            str(spec.get("User", "")) != provider.user
            or str(spec.get("Client", "")) != provider.client
        ):
            raise SourceControlValidationError(
                f"Pending changelist {changelist_id} is not owned by the current user/client; no mutation is permitted."
            )
        raw_files = _indexed_spec_values(spec, "Files")
        # The canonical P4 form round-trip is change -o -> change -i. Preserve
        # every structured form field exactly (including indexed FilesN/JobsN)
        # except the transport-only code marker and the caller-authorized Description.
        form: dict[str, Any] = {
            key: value for key, value in spec.items() if key not in {"code", "Description"}
        }
        form["Description"] = description
        payload = _marshal_change_spec(form)
        try:
            result = self._runner.run(["change", "-i"], stdin_bytes=payload)
        except SourceControlCommandError as exc:
            raise SourceControlCommandError(f"Changelist description update failed: {exc}") from exc
        error = _error_text(result.records)
        if error is not None:
            raise SourceControlCommandError(f"Changelist description update was rejected: {error}")
        try:
            updated = self._read_changelist_spec(changelist_id)
        except SourceControlCommandError as exc:
            raise SourceControlMutationVerificationError(
                f"Changelist {changelist_id} may have been updated, but post-state verification failed: {exc}",
                operation="update-description",
                changelist_id=changelist_id,
            ) from exc
        updated_files = _indexed_spec_values(updated, "Files") if updated is not None else []
        if (
            updated is None
            or str(updated.get("Description", "")) != description
            or updated_files != raw_files
            or str(updated.get("User", "")) != provider.user
            or str(updated.get("Client", "")) != provider.client
            or str(updated.get("Status", "")).lower() not in _PENDING_STATUSES
        ):
            raise SourceControlMutationVerificationError(
                f"Changelist {changelist_id} description update may have occurred, but exact post-state verification failed.",
                operation="update-description",
                changelist_id=changelist_id,
            )

    def _reopen_exact_file(
        self, local_text: str, changelist_id: str
    ) -> tuple[bool, Optional[str]]:
        """Reopen one exact already-opened path. Returns (ok, error_text_or_none)."""
        try:
            result = self._runner.run(["reopen", "-c", changelist_id, local_text])
        except SourceControlCommandError as exc:
            return False, str(exc)
        return True, _error_text(result.records)

    def _mutation_verification_failure_result(
        self,
        *,
        paths: Sequence[str],
        resolved: Sequence[ResolvedInputPath],
        pre_by_input: dict[str, SourceControlFileState],
        provider: _ProviderInfo,
        description: str,
        change_set_id: str,
        manual_final_action: str,
        error: SourceControlMutationVerificationError,
    ) -> SourceControlPrepareChangelistResult:
        """Return a truthful audited partial result after a mutation cannot be verified."""
        exact_inputs = [item.input_path for item in resolved]
        try:
            post = self.status(paths)
            post_by_input = {state.input_path: state for state in post.files}
        except SourceControlError:
            post_by_input = dict(pre_by_input)
        final_files = tuple(
            post_by_input.get(item.input_path, pre_by_input[item.input_path]) for item in resolved
        )
        action_receipts = (
            {
                "action": error.operation,
                "ok": False,
                "code": "mutation-post-verify-failed",
                "changelistId": error.changelist_id or None,
                "mutationMayHaveOccurred": True,
                "message": str(error),
            },
        )
        warnings = self._result_warnings(
            final_files, provider, False, action_receipts, manual_final_action
        )
        audit_receipt = self._build_audit_receipt(
            operation=error.operation,
            provider=provider,
            changelist_id=error.changelist_id,
            change_set_id=change_set_id,
            exact_inputs=exact_inputs,
            pre_by_input=pre_by_input,
            post_by_input=post_by_input,
            resolved=resolved,
            action_receipts=action_receipts,
            manual_final_action=manual_final_action,
        )
        return SourceControlPrepareChangelistResult(
            provider_available=True,
            server_version=provider.version,
            client_name=provider.client,
            user_name=provider.user,
            server_address=provider.address,
            ok=False,
            changelist_id=error.changelist_id,
            changelist_created=error.operation == "create-changelist",
            description=description,
            change_set_id=change_set_id,
            files=final_files,
            receipts=action_receipts,
            audit_receipt=audit_receipt,
            submit_ready=False,
            manual_final_action=manual_final_action,
            warnings=warnings,
        )


    def prepare_changelist(
        self,
        paths: Sequence[str],
        description: str,
        *,
        changelist_id: Optional[str] = None,
        change_set_id: str = "",
        manual_final_action: str = "none",
    ) -> SourceControlPrepareChangelistResult:
        """Create/update one pending CL and move exact already-opened files into it."""
        resolved = resolve_input_paths(paths, project_root=self._project_root)
        description = _validate_description(description)
        manual_final_action = _validate_manual_final_action(manual_final_action)
        provider = self._probe_provider()
        if not provider.available:
            warnings = (
                SourceControlWarning(
                    severity="warning",
                    code="source-control-unavailable",
                    message="P4 provider is unavailable; no changelist mutation was attempted.",
                ),
            )
            return SourceControlPrepareChangelistResult(
                provider_available=False,
                server_version="",
                client_name="",
                user_name="",
                server_address="",
                ok=False,
                changelist_id="",
                changelist_created=False,
                description=description,
                change_set_id="",
                files=(),
                receipts=(),
                audit_receipt={},
                submit_ready=False,
                manual_final_action=manual_final_action,
                warnings=warnings,
            )

        validated_change_set_id = ""
        if change_set_id:
            from .change_sets import ChangeSetError, validate_change_set_id

            try:
                validated_change_set_id = validate_change_set_id(change_set_id)
            except ChangeSetError as exc:
                raise SourceControlValidationError(str(exc)) from exc

        pre = self.status(paths)
        pre_by_input = {file_state.input_path: file_state for file_state in pre.files}
        exact_inputs = [item.input_path for item in resolved]

        # 1) Resolve the destination pending changelist (create or reuse).
        target_id = ""
        changelist_created = False
        description_updated = False
        action_receipts: list[dict[str, Any]] = []
        if changelist_id:
            _validate_changelist_id_token(changelist_id)
            spec = self._read_changelist_spec(changelist_id)
            if spec is None:
                raise SourceControlValidationError(f"Pending changelist {changelist_id} was not found.")
            state = self._changelist_state_from_spec(spec, provider)
            if not state.pending or not state.current_user_owned or not state.current_client_owned:
                raise SourceControlValidationError(
                    f"Pending changelist {changelist_id} is not owned by the current user/client "
                    "or is not pending; no mutation is permitted."
                )
            target_id = changelist_id
            if state.description != description:
                try:
                    self._update_changelist_description(changelist_id, description, provider)
                except SourceControlMutationVerificationError as exc:
                    return self._mutation_verification_failure_result(
                        paths=paths,
                        resolved=resolved,
                        pre_by_input=pre_by_input,
                        provider=provider,
                        description=description,
                        change_set_id=validated_change_set_id,
                        manual_final_action=manual_final_action,
                        error=exc,
                    )
                description_updated = True
                action_receipts.append(
                    {
                        "action": "update-description",
                        "ok": True,
                        "changelistId": changelist_id,
                        "message": "description-updated",
                    }
                )
        else:
            try:
                target_id = self._create_pending_changelist(description, provider)
            except SourceControlMutationVerificationError as exc:
                return self._mutation_verification_failure_result(
                    paths=paths,
                    resolved=resolved,
                    pre_by_input=pre_by_input,
                    provider=provider,
                    description=description,
                    change_set_id=validated_change_set_id,
                    manual_final_action=manual_final_action,
                    error=exc,
                )
            changelist_created = True
            action_receipts.append(
                {
                    "action": "create-changelist",
                    "ok": True,
                    "changelistId": target_id,
                    "message": "pending-changelist-created",
                }
            )

        # 2) Reopen the exact already-opened current-client files.
        for item in resolved:
            file_state = pre_by_input.get(item.input_path)
            if file_state is None or not file_state.mapped or file_state.local_path is None:
                action_receipts.append(
                    {
                        "file": item.input_path,
                        "action": "reopen",
                        "ok": False,
                        "code": "not-mapped",
                        "message": "File is not mapped through the current client.",
                    }
                )
                continue
            if file_state.local_writable_override or _override_like_writable(file_state):
                action_receipts.append(
                    {
                        "file": item.input_path,
                        "action": "reopen",
                        "ok": False,
                        "code": "override-only",
                        "message": "File is writable only through a local override and is not legitimately opened in P4.",
                    }
                )
                continue
            if not (file_state.opened_for_edit and file_state.opened_by_current_client):
                action_receipts.append(
                    {
                        "file": item.input_path,
                        "action": "reopen",
                        "ok": False,
                        "code": "not-open-current-client",
                        "message": "File is not opened for edit by the current user/client; open it first.",
                    }
                )
                continue
            if file_state.change == target_id:
                action_receipts.append(
                    {
                        "file": item.input_path,
                        "action": "reopen",
                        "ok": True,
                        "code": "already-in-changelist",
                        "changelistId": target_id,
                        "message": "File is already in the target pending changelist.",
                    }
                )
                continue
            ok, error = self._reopen_exact_file(str(file_state.local_path), target_id)
            action_receipts.append(
                {
                    "file": item.input_path,
                    "action": "reopen",
                    "ok": ok,
                    "code": "reopened" if ok and error is None else "reopen-failed",
                    "changelistId": target_id,
                    "message": "file-reopened" if ok and error is None else (error or "reopen-failed"),
                }
            )

        # 3) Post-state verification for every reopen attempt against the target CL.
        attempted = [
            receipt
            for receipt in action_receipts
            if receipt.get("action") == "reopen"
            and receipt.get("code") not in {"not-mapped", "override-only", "not-open-current-client"}
        ]
        attempted_inputs = [str(receipt["file"]) for receipt in attempted]
        if attempted_inputs:
            verify_records: dict[str, dict[str, Any]] = {}
            try:
                result = self._runner.run(["opened", "-c", target_id, *attempted_inputs])
                for record in result.records:
                    if _is_error_record(record):
                        continue
                    client_file = str(record.get("clientFile", ""))
                    if client_file:
                        verify_records[client_file.replace("\\", "/").lower()] = record
            except SourceControlCommandError as exc:
                for receipt in attempted:
                    receipt["ok"] = False
                    receipt["code"] = "post-verify-failed"
                    receipt["message"] = f"post-state verification failed: {exc}"
            else:
                for receipt in attempted:
                    local_text = str(pre_by_input[receipt["file"]].local_path)
                    record = verify_records.get(local_text.replace("\\", "/").lower())
                    if (
                        record is None
                        or str(record.get("change", "")) != target_id
                        or str(record.get("user", "")) != provider.user
                        or str(record.get("client", "")) != provider.client
                    ):
                        receipt["ok"] = False
                        receipt["code"] = "post-verify-failed"
                        receipt["message"] = "reopen could not be confirmed in the target pending changelist."

        # 4) Post file states.
        post = self.status(paths)
        post_by_input = {file_state.input_path: file_state for file_state in post.files}
        final_files = tuple(
            post_by_input.get(item.input_path, pre_by_input[item.input_path]) for item in resolved
        )

        reopen_failures = [
            receipt for receipt in action_receipts
            if receipt.get("action") == "reopen" and receipt.get("ok") is False
        ]
        ok = bool(target_id) and not reopen_failures
        operation = (
            "create-changelist"
            if changelist_created
            else ("update-description" if description_updated else "reopen")
        )
        warnings = self._result_warnings(final_files, provider, ok, reopen_failures, manual_final_action)
        audit_receipt = self._build_audit_receipt(
            operation=operation,
            provider=provider,
            changelist_id=target_id,
            change_set_id=validated_change_set_id,
            exact_inputs=exact_inputs,
            pre_by_input=pre_by_input,
            post_by_input=post_by_input,
            resolved=resolved,
            action_receipts=action_receipts,
            manual_final_action=manual_final_action,
        )
        submit_ready = _aggregate_submit_ready(final_files, ok)
        return SourceControlPrepareChangelistResult(
            provider_available=True,
            server_version=provider.version,
            client_name=provider.client,
            user_name=provider.user,
            server_address=provider.address,
            ok=ok,
            changelist_id=target_id,
            changelist_created=changelist_created,
            description=description,
            change_set_id=validated_change_set_id,
            files=final_files,
            receipts=tuple(action_receipts),
            audit_receipt=audit_receipt,
            submit_ready=submit_ready,
            manual_final_action=manual_final_action,
            warnings=warnings,
        )

    def resolve_status(self, paths: Sequence[str]) -> SourceControlResolveStatusResult:
        """Read-only exact-file resolve preview (``resolve -n`` only, output-bounded)."""
        resolved = resolve_input_paths(paths, project_root=self._project_root)
        provider = self._probe_provider()
        warnings: list[SourceControlWarning] = []
        if not provider.available:
            warnings.append(
                SourceControlWarning(
                    severity="warning",
                    code="source-control-unavailable",
                    message="P4 provider is unavailable; resolve state is unknown.",
                )
            )
            file_states = tuple(
                SourceControlResolveFileState(
                    input_path=item.input_path,
                    local_path=str(item.local_path) if item.local_path is not None else None,
                    provider_available=False,
                    resolve_state_unknown=True,
                    warnings=(
                        SourceControlWarning(
                            severity="warning",
                            code="source-control-unavailable",
                            message="P4 provider is unavailable; resolve state is unknown.",
                        ),
                    ),
                )
                for item in resolved
            )
            return SourceControlResolveStatusResult(
                provider_available=False,
                server_version="",
                client_name="",
                user_name="",
                server_address="",
                files=file_states,
                warnings=tuple(warnings),
            )

        status_result = self.status(paths)
        pre_by_input = {file_state.input_path: file_state for file_state in status_result.files}
        probe_locals = [
            str(file_state.local_path)
            for file_state in pre_by_input.values()
            if file_state.local_path is not None and file_state.mapped and file_state.provider_available
        ]
        preview_by_local: dict[str, dict[str, Any]] = {}
        query_error: Optional[str] = None
        if probe_locals:
            try:
                result = self._runner.run(["resolve", "-n", *probe_locals])
            except SourceControlCommandError as exc:
                query_error = str(exc)
            else:
                non_benign_errors = [
                    record
                    for record in result.records
                    if _is_error_record(record) and not _is_no_resolve_record(record)
                ]
                error_text = _error_text(non_benign_errors)
                if result.exit_code != 0 or error_text is not None:
                    query_error = error_text or result.stderr_text or "resolve preview failed"
                if query_error is None:
                    for record in result.records:
                        if _is_error_record(record):
                            continue
                        client_file = str(record.get("clientFile", ""))
                        if client_file:
                            preview_by_local[client_file.replace("\\", "/").lower()] = record

        file_states: list[SourceControlResolveFileState] = []
        for item in resolved:
            pre = pre_by_input.get(item.input_path)
            local_text = str(item.local_path) if item.local_path is not None else None
            if pre is None or not pre.provider_available:
                file_states.append(
                    SourceControlResolveFileState(
                        input_path=item.input_path,
                        local_path=local_text,
                        provider_available=bool(pre is not None and pre.provider_available),
                        mapped=bool(pre is not None and pre.mapped),
                        resolve_state_unknown=True,
                        warnings=(SourceControlWarning("warning", "source-control-unavailable", "P4 provider is unavailable; resolve state is unknown."),),
                    )
                )
                continue
            if not pre.mapped or local_text is None:
                file_states.append(
                    SourceControlResolveFileState(
                        input_path=item.input_path,
                        local_path=local_text,
                        provider_available=True,
                        mapped=False,
                        warnings=(SourceControlWarning("info", "not-mapped", "File is not tracked by the P4 depot through this client."),),
                    )
                )
                continue
            if query_error is not None:
                file_states.append(
                    SourceControlResolveFileState(
                        input_path=item.input_path,
                        local_path=local_text,
                        depot_path=pre.depot_path,
                        provider_available=True,
                        mapped=True,
                        changelist_id=pre.change if pre.opened_by_current_client else "",
                        resolve_state_unknown=True,
                        submit_ready=False,
                        warnings=(SourceControlWarning("strong-warning", "resolve-state-unknown", f"Resolve preview failed; state is unknown: {query_error}"),),
                    )
                )
                continue
            record = preview_by_local.get(local_text.replace("\\", "/").lower())
            needs_resolve = record is not None
            file_type = str(record.get("type", "")) if record is not None else pre.file_type
            file_states.append(
                self._build_resolve_file_state(item, pre, record, needs_resolve, file_type)
            )
        summary = _resolve_summary(file_states)
        return SourceControlResolveStatusResult(
            provider_available=True,
            server_version=provider.version,
            client_name=provider.client,
            user_name=provider.user,
            server_address=provider.address,
            files=tuple(file_states),
            summary=summary,
            warnings=tuple(warnings),
        )

    def _build_resolve_file_state(
        self,
        item: ResolvedInputPath,
        pre: SourceControlFileState,
        record: Optional[dict[str, Any]],
        needs_resolve: bool,
        file_type: str,
    ) -> SourceControlResolveFileState:
        local_path = pre.local_path
        suffix = Path(local_path).suffix.lower() if local_path else ""
        binary_package = suffix in BINARY_PACKAGE_EXTENSIONS
        text_like = _is_text_like_type(file_type)
        generic_binary = bool(file_type) and not binary_package and not text_like
        mergeable_text = needs_resolve and not binary_package and not generic_binary and suffix in TEXT_RESOLVE_EXTENSIONS
        resolve_kind = "content"
        base_revision = ""
        if record is not None:
            resolve_kind = _infer_resolve_kind(record)
            base_revision = str(record.get("startFromRev") or record.get("baseRev") or "")
        warnings: list[SourceControlWarning] = []
        if needs_resolve and binary_package:
            warnings.append(
                SourceControlWarning(
                    severity="strong-warning",
                    code="binary-package-resolve-required",
                    message="Unreal binary package needs reconciliation; automatic text resolve is not allowed.",
                )
            )
        elif needs_resolve and generic_binary:
            warnings.append(
                SourceControlWarning(
                    severity="warning",
                    code="binary-file-resolve-not-supported",
                    message="Generic binary file needs resolve; C3 automatic text resolve does not apply.",
                )
            )
        elif needs_resolve and not mergeable_text:
            warnings.append(
                SourceControlWarning(
                    severity="warning",
                    code="text-resolve-not-eligible",
                    message="File needs resolve but is not an eligible mergeable text file for C3 automatic resolve.",
                )
            )
        submit_ready = pre.submit_ready and not needs_resolve
        return SourceControlResolveFileState(
            input_path=item.input_path,
            local_path=local_path,
            depot_path=pre.depot_path,
            provider_available=True,
            mapped=True,
            needs_resolve=needs_resolve,
            resolve_kind=resolve_kind,
            base_revision=base_revision,
            file_type=file_type,
            changelist_id=pre.change if pre.opened_by_current_client else "",
            mergeable_text=mergeable_text,
            binary_package=binary_package,
            generic_binary=generic_binary,
            submit_ready=submit_ready,
            warnings=tuple(warnings),
        )

    def resolve_text(
        self,
        paths: Sequence[str],
        *,
        changelist_id: Optional[str] = None,
    ) -> SourceControlResolveTextResult:
        """Exact conflict-free text resolve (``resolve -am``) on eligible files only."""
        resolved = resolve_input_paths(paths, project_root=self._project_root)
        provider = self._probe_provider()
        if not provider.available:
            warnings = (
                SourceControlWarning(
                    severity="warning",
                    code="source-control-unavailable",
                    message="P4 provider is unavailable; no resolve mutation was attempted.",
                ),
            )
            return SourceControlResolveTextResult(
                provider_available=False,
                server_version="",
                client_name="",
                user_name="",
                server_address="",
                ok=False,
                all_resolved=False,
                binary_reconciliation_required=False,
                files=(),
                receipts=(),
                audit_receipt={},
                submit_ready=False,
                warnings=warnings,
            )
        if changelist_id:
            _validate_changelist_id_token(changelist_id)
            spec = self._read_changelist_spec(changelist_id)
            state = (
                self._changelist_state_from_spec(spec, provider) if spec is not None else None
            )
            if state is None or not state.pending or not state.current_user_owned or not state.current_client_owned:
                raise SourceControlValidationError(
                    f"Pending changelist {changelist_id} is not owned by the current user/client "
                    "or is not pending; no mutation is permitted."
                )

        preview = self.resolve_status(paths)
        pre = self.status(paths)
        preview_by_input = {file_state.input_path: file_state for file_state in preview.files}
        pre_by_input = {file_state.input_path: file_state for file_state in pre.files}

        receipts: list[dict[str, Any]] = []
        exact_inputs: list[str] = []
        binary_required = False
        unresolved_remainder = False
        for item in resolved:
            exact_inputs.append(item.input_path)
            file_state = pre_by_input.get(item.input_path)
            resolve_state = preview_by_input.get(item.input_path)
            if file_state is None or resolve_state is None:
                receipts.append(
                    {
                        "file": item.input_path,
                        "action": "resolve-text",
                        "ok": False,
                        "code": "resolve-state-unknown",
                        "message": "File state could not be determined; no resolve was attempted.",
                    }
                )
                unresolved_remainder = True
                continue
            if not file_state.mapped or not resolve_state.mapped:
                receipts.append(
                    {
                        "file": item.input_path,
                        "action": "resolve-text",
                        "ok": False,
                        "code": "not-mapped",
                        "message": "File is not tracked by the P4 depot through this client.",
                    }
                )
                continue
            if not (file_state.opened_for_edit and file_state.opened_by_current_client):
                receipts.append(
                    {
                        "file": item.input_path,
                        "action": "resolve-text",
                        "ok": False,
                        "code": "not-open-current-client",
                        "message": "File is not opened for edit by the current user/client; no resolve was attempted.",
                    }
                )
                continue
            if file_state.local_writable_override or _override_like_writable(file_state):
                receipts.append(
                    {
                        "file": item.input_path,
                        "action": "resolve-text",
                        "ok": False,
                        "code": "override-only",
                        "message": "File is writable only through a local override and is not legitimately opened in P4.",
                    }
                )
                continue
            if changelist_id and file_state.change != changelist_id:
                unresolved_remainder = True
                receipts.append(
                    {
                        "file": item.input_path,
                        "action": "resolve-text",
                        "ok": False,
                        "code": "changelist-scope-mismatch",
                        "changelistId": changelist_id,
                        "actualChangelistId": file_state.change,
                        "message": "File is not opened in the explicitly requested changelist; no resolve was attempted.",
                    }
                )
                continue
            if not resolve_state.needs_resolve:
                receipts.append(
                    {
                        "file": item.input_path,
                        "action": "resolve-text",
                        "ok": True,
                        "code": "already-resolved",
                        "message": "File does not need resolve.",
                    }
                )
                continue
            if resolve_state.binary_package:
                binary_required = True
                unresolved_remainder = True
                receipts.append(
                    {
                        "file": item.input_path,
                        "action": "resolve-text",
                        "ok": False,
                        "code": "binary-package-resolve-required",
                        "message": "Unreal binary package needs UE-level reconciliation; automatic text resolve is not allowed.",
                    }
                )
                continue
            if resolve_state.generic_binary:
                unresolved_remainder = True
                receipts.append(
                    {
                        "file": item.input_path,
                        "action": "resolve-text",
                        "ok": False,
                        "code": "binary-file-resolve-not-supported",
                        "message": "Generic binary file is outside C3 automatic text resolve; left unresolved.",
                    }
                )
                continue
            if not resolve_state.mergeable_text:
                unresolved_remainder = True
                receipts.append(
                    {
                        "file": item.input_path,
                        "action": "resolve-text",
                        "ok": False,
                        "code": "not-eligible-text-resolve",
                        "message": "File needs resolve but is not an eligible mergeable text file; left unresolved.",
                    }
                )
                continue
            before_sha = _sha256_file(file_state.local_path)
            merge_argv = ["resolve", "-am"]
            if changelist_id:
                merge_argv += ["-c", changelist_id]
            merge_argv.append(str(file_state.local_path))
            try:
                merge_result = self._runner.run(merge_argv)
            except SourceControlCommandError as exc:
                unresolved_remainder = True
                receipts.append(
                    {
                        "file": item.input_path,
                        "action": "resolve-text",
                        "ok": False,
                        "code": "resolve-command-error",
                        "message": str(exc),
                    }
                )
                continue
            error = _error_text(merge_result.records)
            if error is not None:
                unresolved_remainder = True
                receipts.append(
                    {
                        "file": item.input_path,
                        "action": "resolve-text",
                        "ok": False,
                        "code": "resolve-command-error",
                        "message": error,
                    }
                )
                continue
            # Post-verify: re-query the exact preview; absence means resolved.
            still_unresolved = self._exact_file_needs_resolve(file_state, str(file_state.local_path))
            after_sha = _sha256_file(file_state.local_path)
            if still_unresolved:
                unresolved_remainder = True
                receipts.append(
                    {
                        "file": item.input_path,
                        "action": "resolve-text",
                        "ok": False,
                        "code": "resolve-conflict-remains",
                        "beforeSha256": before_sha,
                        "afterSha256": after_sha,
                        "message": "Automatic merge skipped a content conflict; the file is left unresolved for the human.",
                    }
                )
                continue
            receipts.append(
                {
                    "file": item.input_path,
                    "action": "resolve-text",
                    "ok": True,
                    "code": "resolve-text-ok",
                    "beforeSha256": before_sha,
                    "afterSha256": after_sha,
                    "message": "Conflict-free text merge resolved the file.",
                }
            )

        post = self.status(paths)
        post_by_input = {file_state.input_path: file_state for file_state in post.files}
        final_files = tuple(
            post_by_input.get(item.input_path, pre_by_input[item.input_path]) for item in resolved
        )
        failures = [receipt for receipt in receipts if receipt.get("ok") is False]
        ok = not failures and not unresolved_remainder and not binary_required
        manual_final_action = "none"
        warnings = self._result_warnings(final_files, provider, ok, failures, manual_final_action)
        audit_receipt = self._build_audit_receipt(
            operation="resolve-text",
            provider=provider,
            changelist_id=changelist_id or "",
            change_set_id="",
            exact_inputs=exact_inputs,
            pre_by_input=pre_by_input,
            post_by_input=post_by_input,
            resolved=resolved,
            action_receipts=receipts,
            manual_final_action=manual_final_action,
        )
        submit_ready = _aggregate_submit_ready(final_files, ok and not unresolved_remainder)
        return SourceControlResolveTextResult(
            provider_available=True,
            server_version=provider.version,
            client_name=provider.client,
            user_name=provider.user,
            server_address=provider.address,
            ok=ok,
            all_resolved=not unresolved_remainder and not binary_required,
            binary_reconciliation_required=binary_required,
            files=final_files,
            receipts=tuple(receipts),
            audit_receipt=audit_receipt,
            submit_ready=submit_ready,
            warnings=warnings,
        )

    def _exact_file_needs_resolve(
        self, file_state: SourceControlFileState, local_text: str
    ) -> bool:
        try:
            result = self._runner.run(["resolve", "-n", local_text])
        except SourceControlCommandError:
            return True
        if result.exit_code != 0:
            return True
        for record in result.records:
            if _is_error_record(record):
                if _is_no_resolve_record(record):
                    continue
                return True
            client_file = str(record.get("clientFile", ""))
            if client_file and client_file.replace("\\", "/").lower() == local_text.replace("\\", "/").lower():
                return True
        return False

    def _result_warnings(
        self,
        final_files: Sequence[SourceControlFileState],
        provider: _ProviderInfo,
        ok: bool,
        failures: Sequence[dict[str, Any]],
        manual_final_action: str,
    ) -> tuple[SourceControlWarning, ...]:
        warnings: list[SourceControlWarning] = []
        if not ok:
            codes = sorted({str(item.get("code", "unknown")) for item in failures})
            warnings.append(
                SourceControlWarning(
                    severity="strong-warning",
                    code="partial-or-blocked-operation",
                    message=f"Request did not fully succeed; affected codes: {', '.join(codes)}.",
                )
            )
        for file_state in final_files:
            for warning in file_state.warnings[:MAX_WARNINGS_PER_FILE]:
                warnings.append(warning)
        if manual_final_action != "none":
            warnings.append(
                SourceControlWarning(
                    severity="info",
                    code="manual-final-action",
                    message="Final action remains manual and is not executable by the Agent.",
                )
            )
        return tuple(warnings)

    def _build_audit_receipt(
        self,
        *,
        operation: str,
        provider: _ProviderInfo,
        changelist_id: str,
        change_set_id: str,
        exact_inputs: Sequence[str],
        pre_by_input: dict[str, SourceControlFileState],
        post_by_input: dict[str, SourceControlFileState],
        resolved: Sequence[ResolvedInputPath],
        action_receipts: Sequence[dict[str, Any]],
        manual_final_action: str,
    ) -> dict[str, Any]:
        pre_state = []
        post_state = []
        for item in resolved:
            before = pre_by_input.get(item.input_path)
            after = post_by_input.get(item.input_path)
            if before is not None:
                pre_state.append(_file_state_evidence(before))
            if after is not None:
                post_state.append(_file_state_evidence(after))
        receipt = {
            "schemaVersion": "1.0",
            "receiptId": _new_receipt_id(),
            "operation": operation,
            "occurredAtUtc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "providerUser": provider.user,
            "providerClient": provider.client,
            "changelistId": changelist_id or None,
            "changeSetId": change_set_id or None,
            "exactFiles": list(exact_inputs),
            "preState": pre_state,
            "postState": post_state,
            "actionReceipts": [dict(item) for item in action_receipts],
            "mutationMayHaveOccurred": any(bool(item.get("mutationMayHaveOccurred")) for item in action_receipts),
            "manualFinalAction": manual_final_action,
            "submitCapability": False,
            "revertCapability": False,
            "deleteCapability": False,
        }
        persisted, path = SourceControlAuditStore(self.audit_report_root).write(receipt)
        receipt["receiptPath"] = path
        receipt["persisted"] = persisted
        return receipt


# ---------------------------------------------------------------------------
# C3 dataclasses, resolve helpers and the bounded durable audit store.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceControlChangelistState:
    """Bounded structured state for one pending changelist."""

    changelist_id: str
    status: str
    user: str
    client: str
    description: str
    files: tuple[str, ...]
    file_count: int
    current_user_owned: bool
    current_client_owned: bool
    pending: bool
    submit_ready: bool = False
    warnings: tuple[SourceControlWarning, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "changelistId": self.changelist_id,
            "status": self.status,
            "user": self.user,
            "client": self.client,
            "description": self.description,
            "files": list(self.files[:MAX_FILES_PER_CHANGELIST]),
            "fileCount": self.file_count,
            "currentUserOwned": self.current_user_owned,
            "currentClientOwned": self.current_client_owned,
            "pending": self.pending,
            "submitReady": self.submit_ready,
            "warnings": [warning.to_payload() for warning in self.warnings[:MAX_WARNINGS_PER_FILE]],
        }


@dataclass(frozen=True)
class SourceControlChangelistQueryResult:
    provider_available: bool
    server_version: str
    client_name: str
    user_name: str
    server_address: str
    changelists: tuple[SourceControlChangelistState, ...]
    requested_id: str = ""
    not_found: bool = False
    warnings: tuple[SourceControlWarning, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "schemaVersion": "1.0",
            "tool": "ue_source_control_changelists",
            "ok": True,
            "readOnly": True,
            "provider": {
                "available": self.provider_available,
                "serverVersion": self.server_version,
                "serverAddress": self.server_address,
                "clientName": self.client_name,
                "userName": self.user_name,
            },
            "requestedChangelistId": self.requested_id,
            "notFound": self.not_found,
            "pendingCount": len(self.changelists),
            "changelists": [changelist.to_payload() for changelist in self.changelists],
            "warnings": [warning.to_payload() for warning in self.warnings],
        }


@dataclass(frozen=True)
class SourceControlResolveFileState:
    """Bounded structured resolve preview state for one exact file."""

    input_path: str
    local_path: Optional[str]
    depot_path: str = ""
    provider_available: bool = True
    mapped: bool = False
    needs_resolve: bool = False
    resolve_kind: str = ""
    base_revision: str = ""
    file_type: str = ""
    changelist_id: str = ""
    mergeable_text: bool = False
    binary_package: bool = False
    generic_binary: bool = False
    resolve_state_unknown: bool = False
    submit_ready: bool = False
    warnings: tuple[SourceControlWarning, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "inputPath": self.input_path,
            "localPath": self.local_path,
            "depotPath": self.depot_path,
            "providerAvailable": self.provider_available,
            "mapped": self.mapped,
            "needsResolve": self.needs_resolve,
            "resolveKind": self.resolve_kind,
            "baseRevision": self.base_revision,
            "fileType": self.file_type,
            "changelistId": self.changelist_id,
            "mergeableText": self.mergeable_text,
            "binaryPackage": self.binary_package,
            "genericBinary": self.generic_binary,
            "resolveStateUnknown": self.resolve_state_unknown,
            "submitReady": self.submit_ready,
            "warnings": [warning.to_payload() for warning in self.warnings[:MAX_WARNINGS_PER_FILE]],
        }


@dataclass(frozen=True)
class SourceControlResolveStatusResult:
    provider_available: bool
    server_version: str
    client_name: str
    user_name: str
    server_address: str
    files: tuple[SourceControlResolveFileState, ...]
    summary: dict[str, int] = field(default_factory=dict)
    warnings: tuple[SourceControlWarning, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        warnings: list[dict[str, str]] = []
        for file_state in self.files:
            warnings.extend(warning.to_payload() for warning in file_state.warnings[:MAX_WARNINGS_PER_FILE])
        warnings.extend(warning.to_payload() for warning in self.warnings)
        return {
            "schemaVersion": "1.0",
            "tool": "ue_source_control_resolve_status",
            "ok": True,
            "readOnly": True,
            "provider": {
                "available": self.provider_available,
                "serverVersion": self.server_version,
                "serverAddress": self.server_address,
                "clientName": self.client_name,
                "userName": self.user_name,
            },
            "summary": dict(self.summary),
            "files": [file_state.to_payload() for file_state in self.files],
            "warnings": warnings,
        }


@dataclass(frozen=True)
class SourceControlPrepareChangelistResult:
    provider_available: bool
    server_version: str
    client_name: str
    user_name: str
    server_address: str
    ok: bool
    changelist_id: str
    changelist_created: bool
    description: str
    change_set_id: str
    files: tuple[SourceControlFileState, ...]
    receipts: tuple[dict[str, Any], ...]
    audit_receipt: dict[str, Any]
    submit_ready: bool
    manual_final_action: str = "none"
    warnings: tuple[SourceControlWarning, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "schemaVersion": "1.0",
            "tool": "ue_source_control_prepare_changelist",
            "ok": self.ok,
            "readOnly": False,
            "provider": {
                "available": self.provider_available,
                "serverVersion": self.server_version,
                "serverAddress": self.server_address,
                "clientName": self.client_name,
                "userName": self.user_name,
            },
            "changelistId": self.changelist_id,
            "changelistCreated": self.changelist_created,
            "description": self.description,
            "changeSetId": self.change_set_id,
            "submitReady": self.submit_ready,
            "files": [file_state.to_payload() for file_state in self.files],
            "receipts": [dict(item) for item in self.receipts],
            "auditReceipt": dict(self.audit_receipt),
            "manualFinalAction": self.manual_final_action,
            "warnings": [warning.to_payload() for warning in self.warnings],
        }


@dataclass(frozen=True)
class SourceControlResolveTextResult:
    provider_available: bool
    server_version: str
    client_name: str
    user_name: str
    server_address: str
    ok: bool
    all_resolved: bool
    binary_reconciliation_required: bool
    files: tuple[SourceControlFileState, ...]
    receipts: tuple[dict[str, Any], ...]
    audit_receipt: dict[str, Any]
    submit_ready: bool
    warnings: tuple[SourceControlWarning, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "schemaVersion": "1.0",
            "tool": "ue_source_control_resolve_text",
            "ok": self.ok,
            "readOnly": False,
            "provider": {
                "available": self.provider_available,
                "serverVersion": self.server_version,
                "serverAddress": self.server_address,
                "clientName": self.client_name,
                "userName": self.user_name,
            },
            "allResolved": self.all_resolved,
            "binaryReconciliationRequired": self.binary_reconciliation_required,
            "submitReady": self.submit_ready,
            "files": [file_state.to_payload() for file_state in self.files],
            "receipts": [dict(item) for item in self.receipts],
            "auditReceipt": dict(self.audit_receipt),
            "manualFinalAction": self.audit_receipt.get("manualFinalAction", "none"),
            "warnings": [warning.to_payload() for warning in self.warnings],
        }


_receipt_counter = itertools.count(1)


def _new_receipt_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    return f"sc_{stamp}_{next(_receipt_counter):04d}"


def _sha256_file(path: Optional[Path]) -> Optional[str]:
    """Streamed SHA-256 of one local file; bounded memory usage."""
    if path is None:
        return None
    if isinstance(path, str):
        path = Path(path)
    if not path.exists():
        return None
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _override_like_writable(state: SourceControlFileState) -> bool:
    """A mapped file that is writable on disk without a current-client open is
    an override-like local write and must never be moved or auto-resolved."""
    if not (state.mapped and state.provider_available):
        return False
    if state.writable is not True:
        return False
    if state.opened_for_edit and state.opened_by_current_client:
        return False
    return True


def _file_state_evidence(state: SourceControlFileState) -> dict[str, Any]:
    """Bounded before/after evidence snapshot used inside audit receipts."""
    return {
        "inputPath": state.input_path,
        "depotPath": state.depot_path,
        "clientPath": state.client_path,
        "action": state.action,
        "change": state.change,
        "mapped": state.mapped,
        "openedForEdit": state.opened_for_edit,
        "openedByCurrentClient": state.opened_by_current_client,
        "localWritableOverride": state.local_writable_override,
        "localModified": state.local_modified,
        "haveRev": state.have_rev,
        "headRev": state.head_rev,
        "submitReady": state.submit_ready,
        "warningCodes": [warning.code for warning in state.warnings[:MAX_WARNINGS_PER_FILE]],
    }


def _is_text_like_type(file_type: str) -> bool:
    return file_type.lower().startswith(("text", "unicode", "utf16"))


def _infer_resolve_kind(record: dict[str, Any]) -> str:
    """Map a ``resolve -n`` record onto a bounded resolve-kind label."""
    explicit = str(record.get("resolveKind", "")).lower()
    if explicit in {"content", "filename", "filetype", "branch", "delete"}:
        return explicit
    if explicit:
        return "unknown"
    their_action = str(record.get("theirAction", "")).lower()
    base_action = str(record.get("baseAction", "")).lower()
    if their_action == "delete" or base_action == "delete":
        return "delete"
    from_file = str(record.get("fromFile", ""))
    depot_file = str(record.get("depotFile", ""))
    if from_file and depot_file and from_file != depot_file:
        return "branch"
    if record.get("startFromRev") is not None or record.get("endFromRev") is not None:
        return "content"
    return "unknown"


def _aggregate_submit_ready(
    final_files: Sequence[SourceControlFileState], operation_ok: bool
) -> bool:
    """Truthful aggregate: only opened current-client files contribute, and a
    blocked operation (conflict / binary / partial reopen) never reports ready."""
    if not operation_ok:
        return False
    opened = [state for state in final_files if state.opened_by_current_client]
    if not opened:
        return False
    return all(state.submit_ready for state in opened)


def _resolve_summary(file_states: Sequence[SourceControlResolveFileState]) -> dict[str, int]:
    return {
        "needsResolve": sum(1 for state in file_states if state.needs_resolve),
        "binaryPackages": sum(1 for state in file_states if state.binary_package),
        "genericBinary": sum(1 for state in file_states if state.generic_binary),
        "mergeableText": sum(1 for state in file_states if state.mergeable_text),
        "resolveStateUnknown": sum(1 for state in file_states if state.resolve_state_unknown),
        "mapped": sum(1 for state in file_states if state.mapped),
    }


class SourceControlAuditStore:
    """Bounded durable source-control audit receipt writer.

    Receipts are written under ``<audit_report_root>/source-control/sc_*.json``
    when an audit root is configured; otherwise receipts are returned in-memory
    with ``persisted=false``. Files are written through a temporary file and an
    atomic ``os.replace`` so a partially written receipt is never readable.
    """

    def __init__(self, audit_report_root: Optional[Path]) -> None:
        self._root = (
            Path(audit_report_root).expanduser().resolve()
            if audit_report_root is not None
            else None
        )

    @property
    def enabled(self) -> bool:
        return self._root is not None

    def write(self, receipt: dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Return ``(persisted, receipt_path_or_none)``; never raises on disk errors."""
        if self._root is None:
            return False, None
        receipt_id = str(receipt.get("receiptId", ""))
        if not receipt_id.startswith("sc_") or not re.fullmatch(r"sc_[A-Za-z0-9_]+", receipt_id):
            return False, None
        directory = self._root / "source-control"
        target = directory / f"{receipt_id}.json"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{receipt_id}.tmp")
            temporary.write_text(
                json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(temporary, target)
        except OSError:
            return False, None
        return True, str(target)
