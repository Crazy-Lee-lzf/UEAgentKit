#!/usr/bin/env python3
"""M3.9 Z5 read-only canary eligibility check on disposable fixtures.

Builds disposable opt-in projects under Build/m39-readiness/z5, exercises the
eligible and fail-closed cases, and asserts that no token or absolute project
path appears in the emitted JSON.  Never writes a real project or descriptor.
"""

from __future__ import annotations

import json
import secrets
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT / "src"),):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from ue_agent_kit.layout_broker import protocol  # noqa: E402
from ue_agent_kit.layout_broker.descriptor import (  # noqa: E402
    build_descriptor,
    write_descriptor_atomic,
)
from ue_agent_kit.layout_broker.security import restrict_file_permissions  # noqa: E402
from ue_agent_kit.pre_m4.canary_preflight import (  # noqa: E402
    CANARY_ALLOWLIST_SCHEMA,
    CANARY_MARKER_SCHEMA,
    run_canary_preflight,
)

OUT = ROOT / "Build" / "m39-readiness" / "z5"
_TOKENS: list[str] = []


def _project(root: Path, name: str) -> tuple[Path, Path, str]:
    project = root / name
    (project / "Saved" / "UEAgentKit").mkdir(parents=True)
    (project / f"{name}.uproject").write_text("{}", encoding="utf-8")
    (project / "Saved" / "UEAgentKit" / "CanaryOptIn.json").write_text(
        json.dumps(
            {"schemaVersion": CANARY_MARKER_SCHEMA, "enabled": True, "projectName": name}
        ),
        encoding="utf-8",
    )
    project_hash = protocol.build_project_path_hash(str(project.resolve()))
    allowlist = root / f"{name}-allowlist.json"
    allowlist.write_text(
        json.dumps(
            {
                "schemaVersion": CANARY_ALLOWLIST_SCHEMA,
                "projectPathHashes": [project_hash],
            }
        ),
        encoding="utf-8",
    )
    return project, allowlist, project_hash


def _descriptor(project: Path, project_hash: str, name: str, *, stale: bool = False) -> Path:
    path = project / "Saved" / "UEAgentKit" / "LayoutBroker.json"
    started = datetime.now(timezone.utc) - (timedelta(hours=13) if stale else timedelta(minutes=1))
    token = secrets.token_hex(32)
    _TOKENS.append(token)
    payload = build_descriptor(
        port=50239,
        token=token,
        project_name=name,
        project_path_hash=project_hash,
        started_at=started,
    )
    write_descriptor_atomic(path, payload)
    return path


def _case(root: Path, name: str, mutate) -> dict:
    project, allowlist, project_hash = _project(root, name)
    descriptor = _descriptor(project, project_hash, name)
    mutate(project, allowlist, descriptor)
    result = run_canary_preflight(project, allowlist)
    payload = result.as_dict()
    return {
        "case": name,
        "decision": payload["decision"],
        "eligible": payload["eligible"],
        "failedChecks": [
            check["code"] for check in payload["checks"] if not check.get("ok")
        ],
        "factsKeys": sorted(payload.get("facts", {})),
        "payload": payload,
    }


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True, exist_ok=True)
    cases: list[dict] = []

    cases.append(_case(OUT, "M39Eligible", lambda project, allowlist, descriptor: None))

    def remove_marker(project: Path, allowlist: Path, descriptor: Path) -> None:
        (project / "Saved" / "UEAgentKit" / "CanaryOptIn.json").unlink()

    cases.append(_case(OUT, "M39NoMarker", remove_marker))

    def deny_allowlist(project: Path, allowlist: Path, descriptor: Path) -> None:
        allowlist.write_text(
            json.dumps({"schemaVersion": CANARY_ALLOWLIST_SCHEMA, "projectPathHashes": []}),
            encoding="utf-8",
        )

    cases.append(_case(OUT, "M39Denied", deny_allowlist))

    def unsafe_acl(project: Path, allowlist: Path, descriptor: Path) -> None:
        descriptor.unlink()
        descriptor.write_text(
            json.dumps(
                build_descriptor(
                    port=50240,
                    token=secrets.token_hex(32),
                    project_name="M39UnsafeAcl",
                    project_path_hash=protocol.build_project_path_hash(str(project)),
                )
            ),
            encoding="utf-8",
        )

    cases.append(_case(OUT, "M39UnsafeAcl", unsafe_acl))

    def stale(project: Path, allowlist: Path, descriptor: Path) -> None:
        descriptor.unlink()
        token = secrets.token_hex(32)
        _TOKENS.append(token)
        payload = build_descriptor(
            port=50241,
            token=token,
            project_name="M39Stale",
            project_path_hash=protocol.build_project_path_hash(str(project)),
            started_at=datetime.now(timezone.utc) - timedelta(hours=13),
        )
        descriptor.write_text(json.dumps(payload), encoding="utf-8")
        restrict_file_permissions(descriptor)

    cases.append(_case(OUT, "M39Stale", stale))

    expected = {
        "M39Eligible": True,
        "M39NoMarker": False,
        "M39Denied": False,
        "M39UnsafeAcl": False,
        "M39Stale": False,
    }
    ok = True
    for case in cases:
        if case["eligible"] != expected[case["case"]]:
            ok = False
    eligible = next(c for c in cases if c["case"] == "M39Eligible")
    blocked = [c for c in cases if c["case"] != "M39Eligible"]

    # Redaction: no generated token value, token-named key, or absolute path.
    text = json.dumps(cases, sort_keys=True)
    absolute_path_leak = str(OUT.resolve()) in text
    token_leak = any(token in text for token in _TOKENS)
    token_key_leak = '"token' in text.lower()
    redaction_ok = not (absolute_path_leak or token_leak or token_key_leak)

    summary = {
        "schemaVersion": "ueak.m39.z5/1.0",
        "readOnly": True,
        "cases": [
            {key: case[key] for key in ("case", "decision", "eligible", "failedChecks", "factsKeys")}
            for case in cases
        ],
        "eligibleCase": eligible["decision"],
        "blockedCodes": {c["case"]: c["failedChecks"] for c in blocked},
        "redaction": {
            "tokenEmitted": token_leak,
            "absolutePathEmitted": absolute_path_leak,
            "ok": redaction_ok,
        },
        "expected": expected,
        "ok": bool(ok and redaction_ok),
    }
    (OUT / "z5-preflight.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"ok": summary["ok"], "eligible": summary["eligibleCase"], "redaction": summary["redaction"]}))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
