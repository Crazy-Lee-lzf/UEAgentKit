from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .indexer import PROFILE_RANK

STATUS_PASS = "PASS"
STATUS_PARTIAL = "PARTIAL"
STATUS_FAIL = "FAIL"

SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_ERROR = "error"

TRUNCATION_MARKER = "<truncated>"
READER_STATUSES = ("success", "failed", "not-handled")


@dataclass(frozen=True)
class IntegrityFinding:
    code: str
    severity: str
    message: str
    asset_path: str = ""
    remediation: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "assetPath": self.asset_path,
            "remediation": self.remediation,
        }
        if self.details:
            payload["details"] = self.details
        return payload


@dataclass
class IntegrityReport:
    export_root: str
    project_key: str = ""
    profile: str = ""
    status: str = STATUS_FAIL
    counts: dict[str, Any] = field(default_factory=dict)
    checks: list[dict[str, Any]] = field(default_factory=list)
    findings: list[IntegrityFinding] = field(default_factory=list)
    omitted_findings: int = 0
    remediation: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return self.status == STATUS_PASS

    def to_dict(self) -> dict[str, Any]:
        return {
            "exportRoot": self.export_root,
            "projectKey": self.project_key,
            "profile": self.profile,
            "status": self.status,
            "complete": self.complete,
            "counts": self.counts,
            "checks": self.checks,
            "findings": [finding.to_dict() for finding in self.findings],
            "omittedFindings": self.omitted_findings,
            "remediation": self.remediation,
        }

    def render_text(self) -> str:
        lines = [
            f"Index integrity: {self.status}",
            f"  export: {self.export_root}",
            f"  project: {self.project_key or '<unknown>'} profile: {self.profile or '<unknown>'}",
        ]
        for key, value in self.counts.items():
            lines.append(f"  {key}: {value}")
        for check in self.checks:
            lines.append(f"  [{check['status']}] {check['code']}: {check['message']}")
        if self.findings:
            lines.append("  findings:")
            for finding in self.findings:
                location = f" ({finding.asset_path})" if finding.asset_path else ""
                lines.append(f"    - {finding.severity} {finding.code}{location}: {finding.message}")
        if self.omitted_findings:
            lines.append(f"  ... {self.omitted_findings} additional finding(s) omitted")
        if self.remediation:
            lines.append("  remediation:")
            lines.extend(f"    - {item}" for item in self.remediation)
        return "\n".join(lines)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _status_from_findings(findings: Iterable[IntegrityFinding]) -> str:
    severities = {finding.severity for finding in findings}
    if SEVERITY_ERROR in severities:
        return STATUS_FAIL
    if SEVERITY_WARNING in severities:
        return STATUS_PARTIAL
    return STATUS_PASS


def _check(status: str, code: str, message: str, **counts: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"status": status, "code": code, "message": message}
    payload.update({key: value for key, value in counts.items() if value is not None})
    return payload


def _find_truncation_markers(value: Any, path: str = "") -> list[str]:
    hits: list[str] = []
    if isinstance(value, str):
        if TRUNCATION_MARKER in value:
            hits.append(path or "$")
    elif isinstance(value, dict):
        for key, item in value.items():
            hits.extend(_find_truncation_markers(item, f"{path}.{key}" if path else str(key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            hits.extend(_find_truncation_markers(item, f"{path}[{index}]"))
    return hits


def _file_contains_marker(path: Path, needle: bytes = b"<truncated>", chunk_size: int = 1024 * 1024) -> bool:
    try:
        with path.open("rb") as stream:
            while True:
                chunk = stream.read(chunk_size)
                if not chunk:
                    return False
                if needle in chunk:
                    return True
    except OSError:
        return False


def _resolve_canonical_path(export_root: Path, entry: dict[str, Any]) -> Path | None:
    recorded = str(entry.get("jsonPath", ""))
    candidates: list[Path] = []
    if recorded:
        normalized = recorded.replace("\\", "/")
        marker = "/canonical/"
        if marker in normalized:
            candidates.append(export_root / normalized[normalized.index(marker) + 1 :])
        candidates.append(Path(recorded).expanduser())
    asset_path = str(entry.get("assetPath", ""))
    if asset_path:
        package_name, asset_name = asset_path.rsplit(".", 1) if "." in asset_path else (asset_path, "")
        canonical_root = export_root / "canonical"
        if canonical_root.is_dir():
            candidates.extend(canonical_root.rglob(f"{Path(package_name.lstrip('/')).name}_{asset_name}.json"))
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def _asset_name(asset_path: str) -> str:
    return asset_path.rsplit(".", 1)[-1] if "." in asset_path else asset_path


def _index_details_incomplete(
    connection: sqlite3.Connection,
    asset_id: int,
    canonical: dict[str, Any],
) -> tuple[bool, str]:
    symbols = canonical.get("symbols", [])
    if not isinstance(symbols, list) or not symbols:
        return False, ""
    row = connection.execute(
        "SELECT COUNT(*) FROM symbols WHERE asset_id = ?",
        (asset_id,),
    ).fetchone()
    if not row or int(row[0]) == 0:
        return True, "indexed asset has no symbols although Canonical lists symbols"
    if canonical.get("assetReaderStatus") != "success" or not canonical.get("assetDetails"):
        return False, ""
    rows = connection.execute(
        "SELECT details_json FROM symbols WHERE asset_id = ? AND kind = 'asset' LIMIT 1",
        (asset_id,),
    ).fetchall()
    if not rows:
        return True, "indexed asset has no asset symbol row"
    try:
        details = json.loads(rows[0][0] or "{}")
    except json.JSONDecodeError:
        return True, "indexed asset symbol details are not valid JSON"
    symbol = details.get("symbol", {}) if isinstance(details, dict) else {}
    if not isinstance(symbol, dict) or not symbol.get("assetDetails"):
        return True, "indexed asset symbol lost Canonical assetDetails"
    return False, ""


def audit_export_integrity(
    export_root: str | Path,
    *,
    index_db: str | Path | None = None,
    project_key: str = "",
    max_findings: int = 50,
) -> IntegrityReport:
    """Audit one AssetCatalog export (and optionally its SQLite index) for silent data loss.

    The audit is read-only. It does not repair data; it classifies the export as
    PASS/PARTIAL/FAIL and reports per-asset evidence so an Agent can decide
    whether to re-export, re-import, or widen the export scope.
    """
    root = Path(export_root).expanduser().resolve()
    report = IntegrityReport(export_root=str(root))
    findings: list[IntegrityFinding] = []
    checks: list[dict[str, Any]] = []
    counts: dict[str, Any] = {}

    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        finding = IntegrityFinding(
            code="manifest-missing",
            severity=SEVERITY_ERROR,
            message=f"manifest.json not found under {root}",
            remediation="Point --export at the AssetCatalog export root that contains manifest.json.",
        )
        return _finalize(report, [finding], [], {}, max_findings)

    manifest = _load_json(manifest_path)
    entries = manifest.get("assets", [])
    if not isinstance(entries, list):
        entries = []
    report.project_key = str(manifest.get("projectName", "")).strip()
    report.profile = str(manifest.get("profile", "")).strip()
    if project_key and report.project_key and project_key != report.project_key:
        findings.append(
            IntegrityFinding(
                code="project-key-mismatch",
                severity=SEVERITY_ERROR,
                message=f"expected project '{project_key}', manifest has '{report.project_key}'",
                remediation="Audit the manifest from the intended project; do not import across projects.",
            )
        )

    counts["assetCount"] = len(entries)
    counts["successCount"] = sum(1 for entry in entries if entry.get("success"))
    counts["failureCount"] = len(entries) - counts["successCount"]
    counts["readerSuccessCount"] = 0
    counts["readerFailureCount"] = 0
    counts["notHandledCount"] = 0
    counts["unknownReaderStatusCount"] = 0
    counts["skippedBlueprintCount"] = int(manifest.get("skippedBlueprintCount", 0) or 0)
    counts["skippedGeneratedCount"] = int(manifest.get("skippedGeneratedCount", 0) or 0)
    counts["truncatedAssetCount"] = 0
    counts["detailsEmptyCount"] = 0
    counts["successfulWithFailedReader"] = 0
    counts["bpctxTruncatedCount"] = 0

    canonical_paths: dict[str, Path] = {}
    canonical_documents: dict[str, dict[str, Any]] = {}
    seen_asset_paths: set[str] = set()
    seen_json_paths: set[str] = set()
    naming_mismatches = 0
    profile_mismatches = 0
    missing_canonical = 0
    world_truncations = 0

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        asset_path = str(entry.get("assetPath", "")).strip()
        if not asset_path:
            findings.append(
                IntegrityFinding(
                    code="asset-path-missing",
                    severity=SEVERITY_ERROR,
                    message="manifest entry has no assetPath",
                    remediation="Re-export; every manifest entry must carry its asset path.",
                )
            )
            continue
        if asset_path in seen_asset_paths:
            findings.append(
                IntegrityFinding(
                    code="asset-path-duplicate",
                    severity=SEVERITY_ERROR,
                    message="duplicate assetPath in manifest",
                    asset_path=asset_path,
                    remediation="De-duplicate the export root or re-export the catalog.",
                )
            )
        seen_asset_paths.add(asset_path)

        json_path = str(entry.get("jsonPath", ""))
        if json_path:
            normalized_json = json_path.replace("\\", "/")
            if normalized_json in seen_json_paths:
                findings.append(
                    IntegrityFinding(
                        code="json-path-duplicate",
                        severity=SEVERITY_ERROR,
                        message="duplicate jsonPath in manifest",
                        asset_path=asset_path,
                        remediation="Re-export; two entries must not point at the same Canonical file.",
                    )
                )
            seen_json_paths.add(normalized_json)

        if not entry.get("success"):
            findings.append(
                IntegrityFinding(
                    code="export-entry-failed",
                    severity=SEVERITY_ERROR,
                    message=str(entry.get("error", "") or "manifest entry is marked failed"),
                    asset_path=asset_path,
                    remediation="Inspect the per-asset error and re-export; a failed entry never reaches queries.",
                )
            )

        status = str(entry.get("assetReaderStatus", "")).strip().lower()
        if status == "success":
            counts["readerSuccessCount"] += 1
        elif status == "failed":
            counts["readerFailureCount"] += 1
        elif status == "not-handled":
            counts["notHandledCount"] += 1
        elif status:
            counts["unknownReaderStatusCount"] += 1
            findings.append(
                IntegrityFinding(
                    code="reader-status-unknown",
                    severity=SEVERITY_WARNING,
                    message=f"unrecognized assetReaderStatus '{status}'",
                    asset_path=asset_path,
                    remediation="Upgrade the exporter or map the status; unrecognized status cannot be verified.",
                )
            )
        else:
            counts["unknownReaderStatusCount"] += 1
            findings.append(
                IntegrityFinding(
                    code="reader-status-missing",
                    severity=SEVERITY_WARNING,
                    message="manifest entry has no assetReaderStatus (legacy export)",
                    asset_path=asset_path,
                    remediation="Legacy manifests cannot prove specialized details; "
                    "re-export with a current exporter for verified coverage.",
                )
            )

        if entry.get("success") and status == "failed":
            counts["successfulWithFailedReader"] += 1
            findings.append(
                IntegrityFinding(
                    code="successful-entry-with-failed-reader",
                    severity=SEVERITY_ERROR,
                    message="manifest marks the asset successful even though its specialized reader failed",
                    asset_path=asset_path,
                    remediation="Re-export after fixing the reader; the index must not import registry-only data as complete.",
                    details={
                        "assetReader": entry.get("assetReader", ""),
                        "assetReaderError": entry.get("assetReaderError", ""),
                    },
                )
            )

        canonical: dict[str, Any] | None = None
        canonical_path = _resolve_canonical_path(root, entry)
        if canonical_path is None:
            missing_canonical += 1
            findings.append(
                IntegrityFinding(
                    code="canonical-missing",
                    severity=SEVERITY_ERROR,
                    message="manifest entry has no readable Canonical JSON",
                    asset_path=asset_path,
                    remediation="Re-export the asset; the manifest entry cannot be imported or audited.",
                )
            )
        else:
            canonical_paths[asset_path] = canonical_path
            try:
                canonical = _load_json(canonical_path)
            except (OSError, json.JSONDecodeError) as exc:
                findings.append(
                    IntegrityFinding(
                        code="canonical-invalid",
                        severity=SEVERITY_ERROR,
                        message=f"Canonical JSON cannot be parsed: {exc}",
                        asset_path=asset_path,
                        remediation="Re-export the asset; the file is corrupt.",
                    )
                )
                canonical = None
            if canonical is not None:
                canonical_documents[asset_path] = canonical
                canonical_asset_path = str(canonical.get("assetPath", ""))
                if canonical_asset_path != asset_path:
                    findings.append(
                        IntegrityFinding(
                            code="canonical-asset-path-mismatch",
                            severity=SEVERITY_ERROR,
                            message=f"Canonical assetPath '{canonical_asset_path}' does not match manifest",
                            asset_path=asset_path,
                            remediation="Re-export; the manifest entry points at the wrong Canonical file.",
                        )
                    )
                canonical_profile = str(canonical.get("profile", "")).strip()
                if report.profile and canonical_profile and canonical_profile != report.profile:
                    profile_mismatches += 1
                    findings.append(
                        IntegrityFinding(
                            code="profile-mismatch",
                            severity=SEVERITY_WARNING,
                            message=f"Canonical profile '{canonical_profile}' differs from manifest profile '{report.profile}'",
                            asset_path=asset_path,
                            remediation="Re-export with one profile; mixed profiles reduce detail coverage predictability.",
                        )
                    )
                canonical_status = str(canonical.get("assetReaderStatus", "")).strip().lower()
                if status and canonical_status and canonical_status != status:
                    findings.append(
                        IntegrityFinding(
                            code="reader-status-inconsistent",
                            severity=SEVERITY_ERROR,
                            message=f"manifest reader status '{status}' != Canonical '{canonical_status}'",
                            asset_path=asset_path,
                            remediation="Re-export; manifest counts cannot be trusted when statuses disagree.",
                        )
                    )
                if entry.get("success") and canonical_status == "failed" and status != "failed":
                    findings.append(
                        IntegrityFinding(
                            code="reader-failed-but-success",
                            severity=SEVERITY_ERROR,
                            message="Canonical specialized reader failed, but the manifest entry is successful",
                            asset_path=asset_path,
                            remediation="Re-export after fixing the reader; the index must not import registry-only data as complete.",
                        )
                    )
                asset_details = canonical.get("assetDetails")
                details_empty = not isinstance(asset_details, dict) or not asset_details
                if canonical_status == "success" and details_empty:
                    counts["detailsEmptyCount"] += 1
                    findings.append(
                        IntegrityFinding(
                            code="reader-details-empty",
                            severity=SEVERITY_ERROR,
                            message="specialized reader succeeded but assetDetails is empty",
                            asset_path=asset_path,
                            remediation="Re-export the asset; specialized properties are missing from Canonical JSON.",
                        )
                    )
                if isinstance(asset_details, dict) and asset_details:
                    truncated_fields = _find_truncation_markers(asset_details)
                    if truncated_fields:
                        counts["truncatedAssetCount"] += 1
                        findings.append(
                            IntegrityFinding(
                                code="value-truncated",
                                severity=SEVERITY_WARNING,
                                message=f"bounded export truncated {len(truncated_fields)} property value(s)",
                                asset_path=asset_path,
                                remediation="Bounded by design; query the asset directly or raise the reader limit in a dedicated task.",
                                details={"fields": truncated_fields[:8]},
                            )
                        )
                    if canonical_status == "success" and canonical.get("summary", {}).get("specializedDetails") == 0:
                        findings.append(
                            IntegrityFinding(
                                code="specialized-details-count-zero",
                                severity=SEVERITY_WARNING,
                                message="Canonical summary reports specializedDetails=0 for a successful reader",
                                asset_path=asset_path,
                                remediation="Check the exporter version; index coverage of specialized properties is uncertain.",
                            )
                        )
                    if asset_details.get("actorListTruncated") is True:
                        world_truncations += 1
                        findings.append(
                            IntegrityFinding(
                                code="world-actors-truncated",
                                severity=SEVERITY_WARNING,
                                message=(
                                    "World actor list truncated at export time "
                                    f"({asset_details.get('exportedActorCount')}/{asset_details.get('loadedActorCount')})"
                                ),
                                asset_path=asset_path,
                                remediation="Bounded by design (20,000); audit cannot recover omitted actors.",
                                details={
                                    "exportedActorCount": asset_details.get("exportedActorCount"),
                                    "loadedActorCount": asset_details.get("loadedActorCount"),
                                },
                            )
                        )
                    world_partition = asset_details.get("worldPartition")
                    if isinstance(world_partition, dict) and world_partition.get("actorDescListTruncated") is True:
                        world_truncations += 1
                        findings.append(
                            IntegrityFinding(
                                code="world-actor-descs-truncated",
                                severity=SEVERITY_WARNING,
                                message="World partition actor descriptors truncated at export time",
                                asset_path=asset_path,
                                remediation="Bounded by design (20,000); audit cannot recover omitted descriptors.",
                            )
                        )

                name = _asset_name(asset_path)
                if json_path:
                    stem = Path(json_path.replace("\\", "/")).stem
                    if name and not stem.endswith(f"_{name}"):
                        naming_mismatches += 1
                        findings.append(
                            IntegrityFinding(
                                code="entry-naming-mismatch",
                                severity=SEVERITY_WARNING,
                                message=f"jsonPath stem '{stem}' does not end with Canonical asset name '_{name}'",
                                asset_path=asset_path,
                                remediation="Regenerate the manifest; the indexer may relocate the file instead of failing.",
                            )
                        )

        bpctx_recorded = str(entry.get("bpctxPath", "")).strip()
        if bpctx_recorded:
            bpctx_path = Path(bpctx_recorded)
            if not bpctx_path.is_absolute():
                bpctx_path = root / bpctx_path
            if bpctx_path.is_file() and _file_contains_marker(bpctx_path):
                counts["bpctxTruncatedCount"] += 1
                findings.append(
                    IntegrityFinding(
                        code="bpctx-value-truncated",
                        severity=SEVERITY_WARNING,
                        message="Blueprint context contains a value truncated at export time",
                        asset_path=asset_path,
                        remediation="Bounded by design (65,536 chars); read the asset directly for full property text.",
                    )
                )

    counts["worldTruncationCount"] = world_truncations
    counts["missingCanonicalCount"] = missing_canonical
    checks.append(
        _check(
            STATUS_PASS if counts["unknownReaderStatusCount"] == 0 else STATUS_PARTIAL,
            "reader-status",
            "specialized reader statuses are present"
            if counts["unknownReaderStatusCount"] == 0
            else f"{counts['unknownReaderStatusCount']} entry/entries have no readable assetReaderStatus (legacy export)",
            success=counts["readerSuccessCount"],
            failed=counts["readerFailureCount"],
            notHandled=counts["notHandledCount"],
            unknown=counts["unknownReaderStatusCount"],
        )
    )

    manifest_reader_failure = manifest.get("readerFailureCount")
    if manifest_reader_failure is not None:
        try:
            declared = int(manifest_reader_failure)
        except (TypeError, ValueError):
            declared = -1
        if declared != counts["readerFailureCount"]:
            findings.append(
                IntegrityFinding(
                    code="reader-failure-count-mismatch",
                    severity=SEVERITY_ERROR,
                    message=(
                        f"manifest readerFailureCount={declared} does not match "
                        f"{counts['readerFailureCount']} failed entries"
                    ),
                    remediation="Re-export; downstream tooling cannot trust either count.",
                )
            )
    manifest_reader_success = manifest.get("readerSuccessCount")
    if manifest_reader_success is not None:
        try:
            declared_success = int(manifest_reader_success)
        except (TypeError, ValueError):
            declared_success = -1
        if declared_success != counts["readerSuccessCount"]:
            findings.append(
                IntegrityFinding(
                    code="reader-success-count-mismatch",
                    severity=SEVERITY_ERROR,
                    message=(
                        f"manifest readerSuccessCount={declared_success} does not match "
                        f"{counts['readerSuccessCount']} successful readers"
                    ),
                    remediation="Re-export or fix the manifest writer; not-handled entries must not be counted as success.",
                )
            )

    success_declared = manifest.get("successCount")
    if success_declared is not None:
        try:
            declared_success_count = int(success_declared)
        except (TypeError, ValueError):
            declared_success_count = -1
        if declared_success_count != counts["successCount"]:
            findings.append(
                IntegrityFinding(
                    code="success-count-mismatch",
                    severity=SEVERITY_ERROR,
                    message=(
                        f"manifest successCount={declared_success_count} does not match "
                        f"{counts['successCount']} successful entries"
                    ),
                    remediation="Regenerate the manifest before importing.",
                )
            )
        if counts["unknownReaderStatusCount"] == 0:
            derived = (
                counts["readerSuccessCount"]
                + counts["readerFailureCount"]
                + counts["notHandledCount"]
            )
            if derived != counts["successCount"]:
                findings.append(
                    IntegrityFinding(
                        code="reader-status-partition-mismatch",
                        severity=SEVERITY_ERROR,
                        message=(
                            "successful entries are not partitioned across reader success/failure/not-handled "
                            f"({derived} != {counts['successCount']})"
                        ),
                        remediation="Re-export; a successful entry without a reader status hides missing details.",
                    )
                )

    declared_asset_count = manifest.get("assetCount")
    if declared_asset_count is not None:
        try:
            declared_assets = int(declared_asset_count)
        except (TypeError, ValueError):
            declared_assets = -1
        if declared_assets != counts["assetCount"]:
            findings.append(
                IntegrityFinding(
                    code="asset-count-mismatch",
                    severity=SEVERITY_ERROR,
                    message=(
                        f"manifest assetCount={declared_assets} does not match "
                        f"{counts['assetCount']} listed entries"
                    ),
                    remediation="Regenerate the manifest; missing or extra entries hide assets from coverage.",
                )
            )
    declared_failure_count = manifest.get("failureCount")
    if declared_failure_count is not None:
        try:
            declared_failures = int(declared_failure_count)
        except (TypeError, ValueError):
            declared_failures = -1
        if declared_failures != counts["failureCount"]:
            findings.append(
                IntegrityFinding(
                    code="failure-count-mismatch",
                    severity=SEVERITY_ERROR,
                    message=(
                        f"manifest failureCount={declared_failures} does not match "
                        f"{counts['failureCount']} failed entries"
                    ),
                    remediation="Regenerate the manifest; the export failure count cannot be trusted.",
                )
            )

    if counts["skippedBlueprintCount"]:
        include_blueprints = bool(manifest.get("includeBlueprints"))
        findings.append(
            IntegrityFinding(
                code="blueprints-not-exported",
                severity=SEVERITY_WARNING if include_blueprints else SEVERITY_INFO,
                message=f"{counts['skippedBlueprintCount']} Blueprint asset(s) were skipped"
                + (" although includeBlueprints=true" if include_blueprints else " (includeBlueprints=false)"),
                remediation="This is a scope choice unless the caller expected Blueprints; export with -IncludeBlueprints or a dedicated profile.",
            )
        )
    if counts["skippedGeneratedCount"]:
        include_generated = bool(manifest.get("includeGenerated"))
        findings.append(
            IntegrityFinding(
                code="generated-packages-not-exported",
                severity=SEVERITY_WARNING if include_generated else SEVERITY_INFO,
                message=f"{counts['skippedGeneratedCount']} external/generated package(s) were skipped",
                remediation="External actor/object packages are excluded by default; export them explicitly only for World investigation.",
            )
        )
    export_root_scope = str(manifest.get("root", "")).strip()
    if export_root_scope and export_root_scope != "/Game":
        findings.append(
            IntegrityFinding(
                code="export-scope-narrowed",
                severity=SEVERITY_INFO,
                message=f"export root is '{export_root_scope}', not '/Game'",
                remediation="Expected for scoped exports; audit only claims coverage for this root.",
            )
        )
    if report.profile and report.profile not in PROFILE_RANK:
        findings.append(
            IntegrityFinding(
                code="profile-unknown",
                severity=SEVERITY_WARNING,
                message=f"unknown export profile '{report.profile}'",
                remediation="Upgrade the auditor/exporter; profile rank and coverage cannot be verified.",
            )
        )

    checks.append(
        _check(
            STATUS_PASS if missing_canonical == 0 else STATUS_FAIL,
            "canonical-files",
            f"{counts['assetCount'] - missing_canonical} of {counts['assetCount']} manifest entries resolve to Canonical JSON",
            missing=missing_canonical,
            detailsEmpty=counts["detailsEmptyCount"],
            truncated=counts["truncatedAssetCount"],
            namingMismatches=naming_mismatches,
            profileMismatches=profile_mismatches,
        )
    )

    index_path = Path(index_db).expanduser().resolve() if index_db else None
    if index_path is not None:
        _audit_index(
            index_path,
            report.project_key,
            entries,
            canonical_paths,
            canonical_documents,
            findings,
            counts,
            checks,
        )
    else:
        findings.append(
            IntegrityFinding(
                code="index-not-verified",
                severity=SEVERITY_WARNING,
                message="no index database supplied; export coverage is verified but index coverage is not",
                remediation="Re-run with --index-db to prove every successful export asset reached queries.",
            )
        )
        checks.append(
            _check(
                STATUS_PARTIAL,
                "index-coverage",
                "no index database supplied; export-only audit cannot prove assets reached the index",
                checked=False,
            )
        )

    return _finalize(report, findings, checks, counts, max_findings)


def _audit_index(
    index_path: Path,
    manifest_project: str,
    entries: list[dict[str, Any]],
    canonical_paths: dict[str, Path],
    canonical_documents: dict[str, dict[str, Any]],
    findings: list[IntegrityFinding],
    counts: dict[str, Any],
    checks: list[dict[str, Any]],
) -> None:
    if not index_path.is_file():
        findings.append(
            IntegrityFinding(
                code="index-database-missing",
                severity=SEVERITY_ERROR,
                message=f"index database not found: {index_path.name}",
                remediation="Build the index first, or omit --index-db for an export-only audit.",
            )
        )
        return
    try:
        connection = sqlite3.connect(f"file:{index_path.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        findings.append(
            IntegrityFinding(
                code="index-open-failed",
                severity=SEVERITY_ERROR,
                message=f"index database cannot be opened read-only: {exc}",
                remediation="Check the path and file permissions; the audit never writes to the index.",
            )
        )
        return
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        required = {"assets", "symbols", "metadata"}
        missing_tables = sorted(required - tables)
        if missing_tables:
            findings.append(
                IntegrityFinding(
                    code="index-schema-missing",
                    severity=SEVERITY_ERROR,
                    message=f"index database is missing tables: {', '.join(missing_tables)}",
                    remediation="Rebuild the index with the current importer; this database is not an AssetCatalog index.",
                )
            )
            return

        metadata = {
            str(row[0]): str(row[1])
            for row in connection.execute("SELECT key, value FROM metadata").fetchall()
        }
        index_project = metadata.get("project_key", "")
        if manifest_project and index_project and manifest_project != index_project:
            findings.append(
                IntegrityFinding(
                    code="index-project-mismatch",
                    severity=SEVERITY_ERROR,
                    message=f"index project '{index_project}' != manifest project '{manifest_project}'",
                    remediation="Rebuild the index for the manifest project; cross-project rows would leak into queries.",
                )
            )
        indexed_rows = connection.execute(
            "SELECT id, asset_path, profile, canonical_sha256, summary_json FROM assets"
        ).fetchall()
        indexed = {str(row[1]): row for row in indexed_rows}
        counts["indexedAssetCount"] = len(indexed)
        expected_assets = [
            str(entry.get("assetPath", ""))
            for entry in entries
            if entry.get("success") and entry.get("assetPath")
        ]
        counts["expectedIndexedCount"] = len(expected_assets)
        missing = [asset_path for asset_path in expected_assets if asset_path not in indexed]
        counts["missingFromIndexCount"] = len(missing)
        counts["indexedPercent"] = (
            round(100.0 * (len(expected_assets) - len(missing)) / len(expected_assets), 1)
            if expected_assets
            else 0.0
        )
        for asset_path in missing:
            findings.append(
                IntegrityFinding(
                    code="missing-from-index",
                    severity=SEVERITY_ERROR,
                    message="successful export asset is absent from the index",
                    asset_path=asset_path,
                    remediation="Re-run build_index; a missing row means queries can never return this asset.",
                )
            )
        extras = sorted(set(indexed) - set(expected_assets))
        counts["indexOnlyAssetCount"] = len(extras)
        if extras:
            findings.append(
                IntegrityFinding(
                    code="index-only-assets",
                    severity=SEVERITY_INFO,
                    message=f"{len(extras)} indexed asset(s) are not in this export root",
                    remediation="Normal for incremental/root-scoped imports; prune only when the export is authoritative.",
                    details={"sample": extras[:5]},
                )
            )

        stale = 0
        lower_profile = 0
        incomplete_details = 0
        for asset_path, row in indexed.items():
            if asset_path not in canonical_paths:
                continue
            index_id = int(row[0])
            index_profile = str(row[2])
            stored_sha = str(row[3])
            canonical_path = canonical_paths[asset_path]
            try:
                actual_sha = _sha256(canonical_path)
            except OSError:
                actual_sha = ""
            if stored_sha and actual_sha and stored_sha != actual_sha:
                stale += 1
                findings.append(
                    IntegrityFinding(
                        code="index-stale-canonical",
                        severity=SEVERITY_ERROR,
                        message="indexed canonical_sha256 does not match the current Canonical file",
                        asset_path=asset_path,
                        remediation="Re-import this export; queries currently serve stale asset details.",
                    )
                )
            canonical = canonical_documents.get(asset_path, {})
            canonical_profile = str(canonical.get("profile", ""))
            if canonical_profile in PROFILE_RANK and index_profile in PROFILE_RANK:
                if PROFILE_RANK[canonical_profile] > PROFILE_RANK[index_profile]:
                    lower_profile += 1
                    findings.append(
                        IntegrityFinding(
                            code="index-profile-downgrade",
                            severity=SEVERITY_ERROR,
                            message=(
                                f"index profile '{index_profile}' is lower than Canonical '{canonical_profile}'"
                            ),
                            asset_path=asset_path,
                            remediation="Import with the export profile or higher; lower profiles replace detailed rows.",
                        )
                    )
            incomplete, reason = _index_details_incomplete(connection, index_id, canonical)
            if incomplete:
                incomplete_details += 1
                findings.append(
                    IntegrityFinding(
                        code="index-details-incomplete",
                        severity=SEVERITY_ERROR,
                        message=reason,
                        asset_path=asset_path,
                        remediation="Re-import the Canonical file; the index row exists but specialized details are lost.",
                    )
                )
        counts["indexStaleCount"] = stale
        counts["indexProfileDowngradeCount"] = lower_profile
        counts["indexDetailsIncompleteCount"] = incomplete_details

        checks.append(
            _check(
                STATUS_PASS if not missing and not stale and not lower_profile and not incomplete_details else STATUS_FAIL,
                "index-coverage",
                (
                    f"{counts['indexedAssetCount']} indexed asset(s); "
                    f"{counts['indexedPercent']}% of expected export assets are present"
                ),
                missing=len(missing),
                stale=stale,
                lowerProfile=lower_profile,
                incompleteDetails=incomplete_details,
                indexOnly=len(extras),
            )
        )
    finally:
        connection.close()


def _finalize(
    report: IntegrityReport,
    findings: list[IntegrityFinding],
    checks: list[dict[str, Any]],
    counts: dict[str, Any],
    max_findings: int,
) -> IntegrityReport:
    report.status = _status_from_findings(findings)
    if not checks:
        checks = [_check(report.status, "export", "export-level integrity checks completed")]
    report.checks = checks
    report.counts = counts
    report.findings = findings[:max_findings]
    report.omitted_findings = max(0, len(findings) - len(report.findings))
    remediation: list[str] = []
    for finding in findings:
        if finding.remediation and finding.remediation not in remediation:
            remediation.append(finding.remediation)
    report.remediation = remediation[:8]
    return report
