"""Read-only Z5 canary eligibility preflight.

The check never writes a marker, descriptor, ACL, service, or project file.  It
requires an explicit canary marker and an allowlist entry, then verifies a
current-user-only descriptor before declaring a disposable/opted-in project
eligible.  A missing or unsafe input is fail-closed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..layout_broker import descriptor as descriptor_module
from ..layout_broker import protocol
from ..layout_broker.security import verify_file_permissions

CANARY_MARKER_SCHEMA = "ueak.canary-optin/1.0"
CANARY_ALLOWLIST_SCHEMA = "ueak.canary-allowlist/1.0"
_MARKER_KEYS = frozenset({"schemaVersion", "enabled", "projectName"})
_ALLOWLIST_KEYS = frozenset({"schemaVersion", "projectPathHashes"})
_MAX_MARKER_BYTES = 4096


@dataclass
class CanaryPreflightResult:
    decision: str
    eligible: bool
    checks: list[dict[str, Any]] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": "ueak.canary-preflight/1.0",
            "decision": self.decision,
            "eligible": self.eligible,
            "readOnly": True,
            "checks": self.checks,
            "facts": self.facts,
        }


def _check(
    checks: list[dict[str, Any]],
    name: str,
    ok: bool,
    code: str,
    detail: str = "",
) -> bool:
    checks.append({"name": name, "ok": bool(ok), "code": "ok" if ok else code, "detail": detail})
    return ok


def _project_file(project_dir: Path) -> Path:
    files = sorted(project_dir.glob("*.uproject"))
    if len(files) != 1:
        raise ValueError("expected exactly one .uproject in the project directory")
    return files[0]


def _load_json_read_only(path: Path, *, max_bytes: int, code: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(code)
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ValueError(code) from exc
    if size <= 0 or size > max_bytes:
        raise ValueError(code)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(code) from exc
    if not isinstance(value, dict):
        raise ValueError(code)
    return value


def run_canary_preflight(
    project_dir: str | Path,
    allowlist_path: str | Path,
    *,
    descriptor_path: str | Path | None = None,
    now: datetime | None = None,
) -> CanaryPreflightResult:
    project = Path(project_dir).resolve()
    allowlist_file = Path(allowlist_path).resolve()
    checks: list[dict[str, Any]] = []
    facts: dict[str, Any] = {"readOnly": True, "projectName": "", "projectPathHash": ""}
    current = now or datetime.now(timezone.utc)

    if not _check(checks, "project-directory", project.is_dir(), "project-missing"):
        return CanaryPreflightResult("BLOCKED", False, checks, facts)
    try:
        uproject = _project_file(project)
    except ValueError:
        _check(checks, "uproject", False, "uproject-ambiguous")
        return CanaryPreflightResult("BLOCKED", False, checks, facts)
    project_name = uproject.stem
    project_hash = protocol.build_project_path_hash(str(project))
    facts["projectName"] = project_name
    facts["projectPathHash"] = project_hash

    marker_path = project / "Saved" / "UEAgentKit" / "CanaryOptIn.json"
    marker_ok = False
    try:
        marker = _load_json_read_only(marker_path, max_bytes=_MAX_MARKER_BYTES, code="marker-invalid")
        marker_ok = (
            set(marker) == _MARKER_KEYS
            and marker.get("schemaVersion") == CANARY_MARKER_SCHEMA
            and marker.get("enabled") is True
            and marker.get("projectName") == project_name
        )
    except ValueError:
        marker_ok = False
    _check(
        checks,
        "canary-marker",
        marker_ok,
        "marker-missing-or-invalid",
        "explicit marker required",
    )

    allowlist_ok = False
    try:
        allowlist = _load_json_read_only(
            allowlist_file, max_bytes=128 * 1024, code="allowlist-invalid"
        )
        allowlist_ok = (
            set(allowlist) == _ALLOWLIST_KEYS
            and allowlist.get("schemaVersion") == CANARY_ALLOWLIST_SCHEMA
            and isinstance(allowlist.get("projectPathHashes"), list)
            and all(isinstance(item, str) for item in allowlist["projectPathHashes"])
            and project_hash in allowlist["projectPathHashes"]
        )
    except ValueError:
        allowlist_ok = False
    _check(checks, "project-allowlist", allowlist_ok, "allowlist-missing-or-denied")

    descriptor_file = (
        Path(descriptor_path).resolve()
        if descriptor_path is not None
        else project / "Saved" / "UEAgentKit" / protocol.DESCRIPTOR_FILENAME
    )
    descriptor_ok = False
    try:
        payload = descriptor_module.read_descriptor_file(
            descriptor_file,
            project_name=project_name,
            project_path_hash=project_hash,
            now=current,
        )
        security = verify_file_permissions(descriptor_file)
        descriptor_ok = security.proven
        facts["descriptorAgeSeconds"] = descriptor_module.descriptor_age_seconds(
            payload, now=current
        )
        facts["brokerVersion"] = payload.get("brokerVersion", "")
        facts["descriptorBasename"] = descriptor_file.name
        facts["descriptorAclState"] = security.state
    except (protocol.BrokerProtocolError, ValueError, OSError):
        descriptor_ok = False
    _check(
        checks,
        "broker-descriptor",
        descriptor_ok,
        "descriptor-missing-unsafe-or-stale",
        "protected current-user descriptor required",
    )

    eligible = marker_ok and allowlist_ok and descriptor_ok
    return CanaryPreflightResult(
        "ELIGIBLE" if eligible else "BLOCKED",
        eligible,
        checks,
        facts,
    )
