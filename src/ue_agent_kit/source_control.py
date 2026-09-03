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

import io
import marshal
import os
import shutil
import stat
import subprocess
import tempfile
import time
from dataclasses import dataclass, replace
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

# Read-only commands plus the two structured mutation operations allowed inside
# the runner. ``sync`` is reachable only through the safe-sync precondition
# gate; ``edit`` only through the explicit checkout assistance path.
_ALLOWED_COMMANDS = frozenset({"info", "where", "fstat", "opened", "diff", "sync", "edit", "client"})
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
        "resolve",
        "merge",
        "shelve",
        "unshelve",
        "change",
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
_ALLOWED_OPTIONS: dict[str, frozenset[str]] = {
    "opened": frozenset({"-a"}),
    "diff": frozenset({"-se", "-sd"}),
    "client": frozenset({"-o"}),
    "sync": frozenset({"-n"}),
    "info": frozenset(),
    "where": frozenset(),
    "fstat": frozenset(),
    "edit": frozenset(),
}

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
    the command against the allowlist and the option token against the
    per-command set; path tokens are validated again with the exact-file rules.
    Arbitrary command strings and shell execution are not representable.
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
            raise SourceControlProhibitedOperationError(f"P4 operation is outside the C1/C2 allowlist: {command}")
        allowed_options = _ALLOWED_OPTIONS.get(command, frozenset())
        for token in tokens[1:]:
            if token.startswith("-"):
                if token not in allowed_options:
                    raise SourceControlProhibitedOperationError(
                        f"Option token is not allowed for P4 {command}: {token}"
                    )
                continue
            _validate_path_argument(token, index=0)

    def run(self, argv: Sequence[str]) -> _P4CommandResult:
        tokens = [str(token) for token in argv]
        self._validate_argv(tokens)
        started = time.perf_counter()
        try:
            with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
                proc = subprocess.run(
                    [self._p4_executable, "-G", *tokens],
                    stdout=stdout_file,
                    stderr=stderr_file,
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
    ) -> None:
        self._runner = P4CommandRunner(p4_executable=p4_executable, timeout_seconds=timeout_seconds)
        self._project_root = Path(project_root).resolve() if project_root is not None else None
        self._provider_cache: Optional[_ProviderInfo] = None

    @property
    def project_root(self) -> Optional[Path]:
        return self._project_root

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
