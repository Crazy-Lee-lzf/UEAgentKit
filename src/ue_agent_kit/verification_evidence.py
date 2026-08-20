from __future__ import annotations

import copy
import hashlib
import json
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


CAPTURED_VERIFICATION_TOOLS = {
    "ue_compile_blueprint": "compile",
    "ue_validate_asset": "data-validation",
    "ue_validate_folder": "data-validation",
    "ue_run_automation_test": "automation",
}
MAX_VERIFICATION_EVIDENCE_RECORDS = 256
MAX_EVIDENCE_DIAGNOSTICS = 32
MAX_EVIDENCE_REVISION_SET = 8


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _stable_id(prefix: str, value: Any) -> str:
    digest = hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest}"


def _package_file(project_path: Path, asset_path: str) -> Path | None:
    if not asset_path.startswith("/Game/") or "." not in asset_path:
        return None
    package, object_name = asset_path.rsplit(".", 1)
    if not object_name or package.rsplit("/", 1)[-1] != object_name:
        return None
    content_root = (project_path.parent / "Content").resolve()
    candidate = (content_root / (package.removeprefix("/Game/") + ".uasset")).resolve()
    try:
        candidate.relative_to(content_root)
    except ValueError:
        return None
    return candidate


def _file_revision(project_path: Path, asset_path: str) -> str:
    try:
        package_file = _package_file(project_path, asset_path)
        if package_file is None or not package_file.is_file():
            return ""
        digest = hashlib.sha256(package_file.read_bytes()).hexdigest()
    except OSError:
        return ""
    return f"sha256:{digest}"


@dataclass(frozen=True)
class EvidenceCaptureToken:
    tool_name: str
    kind: str
    params: dict[str, Any]
    subject: str
    started_at_utc: str
    before_revision: str


class VerificationEvidenceStore:
    """Bounded server-session evidence captured only from registered UEAgentKit actions."""

    def __init__(
        self,
        *,
        project_name: str,
        project_path: Path,
        maximum_records: int = MAX_VERIFICATION_EVIDENCE_RECORDS,
    ) -> None:
        if maximum_records < 1 or maximum_records > MAX_VERIFICATION_EVIDENCE_RECORDS:
            raise ValueError("maximum_records is outside the fixed verification evidence bound")
        self.project_name = project_name
        self.project_path = project_path.expanduser().resolve()
        self.maximum_records = maximum_records
        self._lock = threading.RLock()
        self._records: list[dict[str, Any]] = []

    def begin_registered_tool(self, tool_name: str, params: dict[str, Any]) -> EvidenceCaptureToken | None:
        kind = CAPTURED_VERIFICATION_TOOLS.get(tool_name)
        if kind is None:
            return None
        normalized = copy.deepcopy(params)
        subject = str(normalized.get("assetPath") or normalized.get("packagePath") or normalized.get("testName") or "")
        asset_path = str(normalized.get("assetPath", ""))
        return EvidenceCaptureToken(
            tool_name=tool_name,
            kind=kind,
            params=normalized,
            subject=subject,
            started_at_utc=_utc_now(),
            before_revision=_file_revision(self.project_path, asset_path) if asset_path else "",
        )

    def finish_registered_tool(
        self,
        token: EvidenceCaptureToken | None,
        response: dict[str, Any],
    ) -> dict[str, Any] | None:
        if token is None or token.tool_name not in CAPTURED_VERIFICATION_TOOLS:
            return None
        if response.get("ok") is not True or response.get("tool") != token.tool_name:
            return None
        result = response.get("result")
        if not isinstance(result, dict):
            return None
        validation = result.get("validationEvidence")
        validation = validation if isinstance(validation, dict) else {}
        live_editor = response.get("liveEditor")
        live_editor = live_editor if isinstance(live_editor, dict) else {}
        response_project = str(validation.get("projectName") or live_editor.get("projectName") or self.project_name)
        if response_project != self.project_name:
            return None

        completed_at = str(validation.get("completedAtUtc") or validation.get("observedAtUtc") or _utc_now())
        editor_session_id = str(validation.get("editorSessionId") or result.get("editorSessionId") or "")
        asset_path = str(result.get("assetPath") or token.params.get("assetPath") or "")
        after_revision = _file_revision(self.project_path, asset_path) if asset_path else ""
        revision_set = self._revision_set(
            token,
            result,
            validation,
            asset_path,
            after_revision,
            editor_session_id,
        )
        diagnostics = self._diagnostics(result)
        evidence_id = str(validation.get("evidenceId") or "")
        if not evidence_id:
            evidence_id = _stable_id("session_evidence", {
                "tool": token.tool_name,
                "subject": token.subject,
                "session": editor_session_id,
                "started": token.started_at_utc,
                "completed": completed_at,
                "result": result.get("result", result.get("state", "")),
            })
        record = {
            "schemaVersion": "1.0",
            "evidenceId": evidence_id,
            "kind": token.kind,
            "tool": token.tool_name,
            "source": "registered-tool-capture",
            "projectName": self.project_name,
            "editorSessionId": editor_session_id,
            "subject": token.subject,
            "assetPath": asset_path,
            "testName": str(result.get("testName") or token.params.get("testName") or ""),
            "startedAtUtc": str(validation.get("startedAtUtc") or token.started_at_utc),
            "completedAtUtc": completed_at,
            "observedAtUtc": str(validation.get("observedAtUtc") or completed_at),
            "result": str(result.get("result") or result.get("state") or "unknown"),
            "succeeded": self._succeeded(token.kind, result),
            "warnings": self._warnings(token.kind, result),
            "revisionCoverage": str(validation.get("revisionCoverage") or ("complete" if revision_set else "unavailable")),
            "revisionSet": revision_set,
            "beforeRevision": token.before_revision,
            "afterRevision": after_revision,
            "packageDirtyBefore": (
                result.get("packageDirtyBefore")
                if isinstance(result.get("packageDirtyBefore"), bool)
                else None
            ),
            "packageDirtyAfter": (
                result.get("packageDirtyAfter")
                if isinstance(result.get("packageDirtyAfter"), bool)
                else None
            ),
            "diagnostics": diagnostics,
            "diagnosticsTruncated": bool(
                result.get("diagnosticsTruncated")
                or result.get("issuesTruncated")
                or result.get("entriesTruncated")
                or len(diagnostics) >= MAX_EVIDENCE_DIAGNOSTICS
            ),
            "details": self._bounded_details(token.kind, result),
        }
        with self._lock:
            self._records = [item for item in self._records if item["evidenceId"] != evidence_id]
            self._records.append(copy.deepcopy(record))
            if len(self._records) > self.maximum_records:
                self._records = self._records[-self.maximum_records :]
        return copy.deepcopy(record)

    @staticmethod
    def _revision_set(
        token: EvidenceCaptureToken,
        result: dict[str, Any],
        validation: dict[str, Any],
        asset_path: str,
        after_revision: str,
        editor_session_id: str,
    ) -> list[dict[str, Any]]:
        raw = validation.get("revisionSet")
        if isinstance(raw, list):
            return [copy.deepcopy(item) for item in raw[:MAX_EVIDENCE_REVISION_SET] if isinstance(item, dict)]
        dirty_before = result.get("packageDirtyBefore")
        dirty_after = result.get("packageDirtyAfter")
        if (
            token.kind != "compile"
            or not asset_path
            or not after_revision
            or not editor_session_id
            or dirty_before is not False
            or dirty_after is not False
        ):
            return []
        stable = token.before_revision == after_revision
        return [{
            "assetPath": asset_path,
            "revision": after_revision,
            "revisionAfter": after_revision,
            "revisionStable": stable,
            "packageDirtyBefore": dirty_before,
            "packageDirtyAfter": dirty_after,
        }]

    @staticmethod
    def _diagnostics(result: dict[str, Any]) -> list[dict[str, Any]]:
        for key in ("diagnostics", "issues", "entries"):
            values = result.get(key)
            if isinstance(values, list):
                return [copy.deepcopy(item) for item in values[:MAX_EVIDENCE_DIAGNOSTICS] if isinstance(item, dict)]
        return []

    @staticmethod
    def _succeeded(kind: str, result: dict[str, Any]) -> bool:
        if kind == "compile":
            return result.get("compiled") is True and result.get("succeeded") is True
        if kind == "automation":
            return result.get("successful") is True and result.get("state") == "success"
        return result.get("result") in {"valid", "valid-with-warnings"}

    @staticmethod
    def _warnings(kind: str, result: dict[str, Any]) -> bool:
        if kind == "compile":
            return result.get("result") == "success-with-warnings"
        if kind == "automation":
            return int(result.get("warningCount", 0) or 0) > 0
        return result.get("result") == "valid-with-warnings" or int(result.get("numWarnings", 0) or 0) > 0

    @staticmethod
    def _bounded_details(kind: str, result: dict[str, Any]) -> dict[str, Any]:
        keys = {
            "compile": ("compiled", "succeeded", "result", "classPath"),
            "data-validation": (
                "result", "numRequested", "numChecked", "numValid", "numInvalid",
                "numWarnings", "numUnableToValidate", "numSkipped",
            ),
            "automation": (
                "state", "successful", "timedOut", "isolatedProcess", "exitCode",
                "errorCount", "warningCount",
            ),
        }[kind]
        return {key: copy.deepcopy(result[key]) for key in keys if key in result}

    def find(self, *, kind: str, subject: str) -> list[dict[str, Any]]:
        with self._lock:
            return [
                copy.deepcopy(item)
                for item in reversed(self._records)
                if item.get("kind") == kind and item.get("subject") == subject
            ]

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(self._records)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "persistent": False,
                "arbitraryIngest": False,
                "projectBound": True,
                "bounded": True,
                "recordCount": len(self._records),
                "maxRecords": self.maximum_records,
                "capturedTools": sorted(CAPTURED_VERIFICATION_TOOLS),
            }
