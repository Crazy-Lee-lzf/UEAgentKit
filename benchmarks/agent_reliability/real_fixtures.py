from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from ue_agent_kit import __version__
from ue_agent_kit.agent_api import IndexQueryService
from ue_agent_kit.editor_bridge import LiveEditorBridgeConfig, LiveEditorBridgeService

from .adapters import AgentRunResult
from .fixtures import PACKAGE_SUFFIXES, FixtureAdapter, FixtureSession, capture_package_inventory, capture_tree_digest
from .io import fingerprint_json, redact, sha256_file, write_json


PLAN_BY_SETUP = {
    "directhost-closedloop-reset": "closed_loop_live_write_plan.json",
    "directhost-scalar-reset": "scalar_patch_regression_plan.json",
    "directhost-reference-reset": "reference_live_write_plan.json",
    "directhost-datatable-reset": "closed_loop_live_write_plan.json",
    "directhost-material-reset": "material_live_write_plan.json",
    "directhost-transaction-reset": "multi_operation_transaction_plan.json",
    "directhost-stale-revision": "scalar_patch_regression_plan.json",
    "directhost-controlled-validation-failure": "closed_loop_live_write_plan.json",
    "directhost-controlled-semantic-mismatch": "closed_loop_live_write_plan.json",
    "directhost-dirty-context": "undo_discard_live_write_plan.json",
}

ROOT_BY_SETUP = {
    "directhost-closedloop-reset": "/Game/UEAgentKitWriteTests/ClosedLoop",
    "directhost-scalar-reset": "/Game/UEAgentKitWriteTests/ScalarRegression",
    "directhost-reference-reset": "/Game/UEAgentKitWriteTests/References",
    "directhost-datatable-reset": "/Game/UEAgentKitWriteTests/ClosedLoop",
    "directhost-material-reset": "/Game/UEAgentKitWriteTests/Materials",
    "directhost-transaction-reset": "/Game/UEAgentKitWriteTests/Transactions",
    "directhost-stale-revision": "/Game/UEAgentKitWriteTests/ScalarRegression",
    "directhost-controlled-validation-failure": "/Game/UEAgentKitWriteTests/ClosedLoop",
    "directhost-controlled-semantic-mismatch": "/Game/UEAgentKitWriteTests/ClosedLoop",
    "directhost-dirty-context": "/Game/UEAgentKitWriteTests/UndoDiscard",
    "directhost-datatable-reference-impact": "/Game/UEAgentKitTests",
}


@dataclass(frozen=True)
class RealFixtureConfig:
    tool_root: Path
    engine_root: Path
    directhost_project: Path
    reforge_project: Path
    reforge_database: Path
    reforge_revision_export: Path
    reforge_policy: Path
    editor_startup_timeout_seconds: int = 180
    process_timeout_seconds: int = 1800

    def validated(self) -> RealFixtureConfig:
        resolved = RealFixtureConfig(
            tool_root=self.tool_root.resolve(),
            engine_root=self.engine_root.resolve(),
            directhost_project=self.directhost_project.resolve(),
            reforge_project=self.reforge_project.resolve(),
            reforge_database=self.reforge_database.resolve(),
            reforge_revision_export=self.reforge_revision_export.resolve(),
            reforge_policy=self.reforge_policy.resolve(),
            editor_startup_timeout_seconds=self.editor_startup_timeout_seconds,
            process_timeout_seconds=self.process_timeout_seconds,
        )
        required_files = (
            resolved.engine_root / "Engine" / "Binaries" / "Win64" / "UnrealEditor.exe",
            resolved.engine_root / "Engine" / "Binaries" / "Win64" / "UnrealEditor-Cmd.exe",
            resolved.directhost_project,
            resolved.reforge_project,
            resolved.reforge_database,
            resolved.reforge_policy,
            resolved.tool_root / "scripts" / "RunWriteFixturePlan.ps1",
            resolved.tool_root / "scripts" / "RunAssetCatalog.ps1",
            resolved.tool_root / "scripts" / "ue-agent.py",
        )
        missing = [os.fspath(path) for path in required_files if not path.is_file()]
        if not resolved.reforge_revision_export.is_dir():
            missing.append(os.fspath(resolved.reforge_revision_export))
        if missing:
            raise FileNotFoundError("Missing real fixture prerequisites: " + ", ".join(missing))
        if not 30 <= resolved.editor_startup_timeout_seconds <= 600:
            raise ValueError("Editor startup timeout must be from 30 through 600 seconds")
        if not 60 <= resolved.process_timeout_seconds <= 7200:
            raise ValueError("Process timeout must be from 60 through 7200 seconds")
        return resolved


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = value.replace("\r\n", "\n").replace("\n", "\r\n")
    path.write_text(normalized, encoding="utf-8", newline="")


def _benchmark_backup_root(tool_root: Path, attempt_root: Path) -> Path:
    output_boundary = (tool_root / "Output" / "AgentReliabilityBenchmark").resolve()
    resolved_attempt = attempt_root.resolve()
    if output_boundary not in resolved_attempt.parents:
        raise ValueError(
            f"Benchmark attempt root must be below {output_boundary}: {resolved_attempt}"
        )
    relative_attempt = resolved_attempt.relative_to(output_boundary)
    backup_root = (
        tool_root / "Backups" / "AgentReliabilityBenchmark" / relative_attempt
    ).resolve()
    backups_boundary = (tool_root / "Backups").resolve()
    if backups_boundary not in backup_root.parents:
        raise ValueError(f"Benchmark backup root escaped {backups_boundary}: {backup_root}")
    return backup_root


def _run_logged(
    command: list[str],
    *,
    cwd: Path,
    log_root: Path,
    name: str,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    _write_text(log_root / f"{name}.stdout.log", str(redact(completed.stdout)))
    _write_text(log_root / f"{name}.stderr.log", str(redact(completed.stderr)))
    if completed.returncode:
        raise RuntimeError(f"{name} failed with exit code {completed.returncode}")
    return completed


def _powershell_script(
    script: Path,
    arguments: Iterable[str],
    *,
    cwd: Path,
    log_root: Path,
    name: str,
    timeout: int,
) -> None:
    _run_logged(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            os.fspath(script),
            *arguments,
        ],
        cwd=cwd,
        log_root=log_root,
        name=name,
        timeout=timeout,
    )


def _asset_package_path(project: Path, object_path: str) -> Path:
    package = object_path.split(".", 1)[0]
    if not package.startswith("/Game/"):
        raise ValueError(f"Fixture asset must be below /Game: {object_path}")
    return project.parent / "Content" / Path(*package.removeprefix("/Game/").split("/"))


def _namespace_directory(project: Path, package_root: str) -> Path:
    if not package_root.startswith("/Game/") or "." in package_root:
        raise ValueError(f"Invalid package root: {package_root}")
    return project.parent / "Content" / Path(*package_root.removeprefix("/Game/").split("/"))


def _capture_asset_packages(project: Path, assets: Iterable[str]) -> dict[str, dict[str, Any]]:
    content = project.parent / "Content"
    inventory: dict[str, dict[str, Any]] = {}
    for asset in sorted(set(assets)):
        stem = _asset_package_path(project, asset)
        candidates = [stem.with_suffix(suffix) for suffix in sorted(PACKAGE_SUFFIXES)]
        for path in candidates:
            if path.is_file():
                relative = path.relative_to(content).as_posix()
                inventory[relative] = {"sha256": sha256_file(path), "bytes": path.stat().st_size}
    return inventory


def _copy_package_snapshot(source: Path, destination: Path) -> dict[str, dict[str, Any]]:
    inventory = capture_package_inventory(source)
    for relative in inventory:
        target = destination / Path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / Path(relative), target)
    return inventory


def _restore_package_snapshot(
    namespace: Path,
    backup: Path,
    expected: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    namespace = namespace.resolve()
    backup = backup.resolve()
    current = capture_package_inventory(namespace)
    for relative in sorted(set(current) - set(expected)):
        target = (namespace / Path(relative)).resolve()
        if namespace not in target.parents:
            raise RuntimeError(f"Refusing to remove package outside fixture namespace: {target}")
        target.unlink()
    for relative in sorted(expected):
        source = (backup / Path(relative)).resolve()
        target = (namespace / Path(relative)).resolve()
        if backup not in source.parents or namespace not in target.parents:
            raise RuntimeError("Package recovery path escaped its fixed boundary")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.r4-restore.tmp")
        try:
            shutil.copy2(source, temporary)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    actual = capture_package_inventory(namespace)
    return {
        "inventoryRestored": actual == expected,
        "expectedInventory": expected,
        "actualInventory": actual,
    }


def _canonical_documents(root: Path) -> dict[str, dict[str, Any]]:
    documents: dict[str, dict[str, Any]] = {}
    canonical = root / "canonical"
    if not canonical.is_dir():
        raise RuntimeError(f"Canonical export directory is missing: {canonical}")
    for path in sorted(canonical.rglob("*.json"), key=lambda item: item.as_posix()):
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        asset_path = str(value.get("assetPath") or "")
        if asset_path:
            documents[asset_path] = value
    if not documents:
        raise RuntimeError(f"Canonical export contains no assets: {canonical}")
    return documents


def _canonical_projection(documents: dict[str, dict[str, Any]]) -> dict[str, Any]:
    projection: dict[str, Any] = {}
    for asset_path, document in sorted(documents.items()):
        projection[asset_path] = {
            "assetClass": document.get("assetClass"),
            "assetDetails": document.get("assetDetails"),
            "variables": document.get("variables"),
            "components": document.get("components"),
            "graphs": document.get("graphs"),
            "references": document.get("references"),
        }
    return projection


def _asset_document(documents: dict[str, dict[str, Any]], object_path: str) -> dict[str, Any]:
    document = documents.get(object_path)
    if document is None:
        raise RuntimeError(f"Canonical export is missing benchmark asset: {object_path}")
    return document


def _property_value(document: dict[str, Any], name: str) -> Any:
    properties = (document.get("assetDetails") or {}).get("properties") or []
    for item in properties:
        if isinstance(item, dict) and item.get("name") == name:
            return item.get("value")
    raise RuntimeError(f"Canonical asset is missing property {name}")


def _reference_value(document: dict[str, Any], name: str) -> Any:
    return _property_value(document, name)


def _row_value(document: dict[str, Any], row_name: str, field_name: str) -> Any:
    rows = (document.get("assetDetails") or {}).get("rows") or []
    for item in rows:
        if isinstance(item, dict) and item.get("Name") == row_name:
            if field_name not in item:
                raise RuntimeError(f"Canonical row {row_name} is missing field {field_name}")
            return item[field_name]
    raise RuntimeError(f"Canonical DataTable is missing row {row_name}")


def _material_scalar(document: dict[str, Any], name: str) -> Any:
    values = (document.get("assetDetails") or {}).get("scalarParameters") or []
    for item in values:
        if isinstance(item, dict) and item.get("name") == name:
            return item.get("value")
    raise RuntimeError(f"Canonical material instance is missing scalar parameter {name}")


def _blueprint_variable(document: dict[str, Any], name: str) -> Any:
    for item in document.get("variables") or []:
        if isinstance(item, dict) and item.get("name") == name:
            return item.get("defaultValue")
    raise RuntimeError(f"Canonical Blueprint is missing variable {name}")


def _iter_values(value: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key), child
            yield from _iter_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_values(child)
    elif isinstance(value, str) and value.strip().startswith(("{", "[")):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return
        yield from _iter_values(decoded)


def _trace_tools(result: AgentRunResult) -> set[str]:
    return {
        str(call.get("tool") or call.get("name") or "")
        for call in result.trace
        if call.get("status") in {"completed", "success"}
    }


def _trace_text(result: AgentRunResult) -> str:
    return json.dumps(result.trace, ensure_ascii=False, sort_keys=True).casefold()


def _trace_state(result: AgentRunResult, *keys: str) -> str:
    expected = {key.casefold() for key in keys}
    for call in reversed(result.trace):
        for key, value in _iter_values(call.get("response")):
            if key.casefold() in expected and isinstance(value, str):
                return value.casefold()
    return ""


def _trust_state(result: AgentRunResult) -> str:
    for call in reversed(result.trace):
        if str(call.get("tool") or "") != "ue_evaluate_trust_verdict":
            continue
        states = [
            str(value).casefold()
            for key, value in _iter_values(call.get("response"))
            if key.casefold() == "state" and isinstance(value, str)
        ]
        for state in states:
            if state in {"verified", "failed", "insufficient-evidence", "suspicious"}:
                return state
    return ""


def _expected_subset(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    for key, value in expected.items():
        candidate = actual.get(key)
        if isinstance(value, list):
            if not isinstance(candidate, list) or not set(value).issubset(candidate):
                return False
        elif candidate != value:
            return False
    return True


def _critical_fields_unchanged(
    case: dict[str, Any],
    before_documents: dict[str, dict[str, Any]],
    after_documents: dict[str, dict[str, Any]],
) -> bool:
    before = json.loads(json.dumps(_canonical_projection(before_documents), ensure_ascii=False))
    after = json.loads(json.dumps(_canonical_projection(after_documents), ensure_ascii=False))
    case_id = str(case["caseId"])
    target = str(case["allowedAssets"][0])
    before_target = before.get(target)
    after_target = after.get(target)
    if before_target is None or after_target is None:
        return False

    def normalize_property(name: str) -> None:
        left = (before_target.get("assetDetails") or {}).get("properties") or []
        right = (after_target.get("assetDetails") or {}).get("properties") or []
        before_value = next((item.get("value") for item in left if item.get("name") == name), None)
        for item in right:
            if item.get("name") == name:
                item["value"] = before_value

    def normalize_allowed_object_reference_edge() -> None:
        expected = case.get("expectedSemanticResult", {})
        if (
            expected.get("operation") != "setAssetReferenceProperty"
            or expected.get("referenceType") != "Object"
            or expected.get("propertyPath") != "ObjectValue"
        ):
            return
        expected_target = str(expected.get("afterValue") or "")
        if not expected_target.startswith("/Game/"):
            return
        left_references = before_target.get("references") or []
        right_references = after_target.get("references") or []
        left_ids = {
            str(item.get("id") or "")
            for item in left_references
            if isinstance(item, dict)
        }
        normalized: list[Any] = []
        for item in right_references:
            is_allowed_derived_edge = (
                isinstance(item, dict)
                and str(item.get("id") or "") not in left_ids
                and item.get("kind") == "depends-hard-package"
                and item.get("hard") is True
                and item.get("dependencyCategory") == "package"
                and item.get("targetAssetPath") == expected_target
                and item.get("sourceSymbolId") == f"asset|{target}"
            )
            if not is_allowed_derived_edge:
                normalized.append(item)
        after_target["references"] = normalized

    if case_id in {
        "r4-write-data-asset-scalar-005",
        "r4-safety-required-evidence-failure-013",
    }:
        normalize_property("IntValue")
    elif case_id == "r4-write-data-asset-reference-006":
        normalize_property("ObjectValue")
        normalize_allowed_object_reference_edge()
    elif case_id == "r4-write-datatable-cell-007":
        left_rows = (before_target.get("assetDetails") or {}).get("rows") or []
        right_rows = (after_target.get("assetDetails") or {}).get("rows") or []
        before_value = next(
            (row.get("Count") for row in left_rows if row.get("Name") == "RowAlpha"),
            None,
        )
        for row in right_rows:
            if row.get("Name") == "RowAlpha":
                row["Count"] = before_value
    elif case_id == "r4-write-material-scalar-009":
        left_values = (before_target.get("assetDetails") or {}).get("scalarParameters") or []
        right_values = (after_target.get("assetDetails") or {}).get("scalarParameters") or []
        before_value = next(
            (item.get("value") for item in left_values if item.get("name") == "EmissiveIntensity"),
            None,
        )
        for item in right_values:
            if item.get("name") == "EmissiveIntensity":
                item["value"] = before_value
    elif case_id == "r4-write-blueprint-default-010":
        left_values = before_target.get("variables") or []
        right_values = after_target.get("variables") or []
        before_value = next(
            (item.get("defaultValue") for item in left_values if item.get("name") == "TransactionInt"),
            None,
        )
        for item in right_values:
            if item.get("name") == "TransactionInt":
                item["defaultValue"] = before_value
    return before == after


def _semantic_result(
    case: dict[str, Any],
    before_documents: dict[str, dict[str, Any]],
    after_documents: dict[str, dict[str, Any]],
    *,
    package_hash_restored: bool,
    reference_count: int | None,
    automation_timed_out: bool,
    trust_state: str,
) -> dict[str, Any]:
    case_id = str(case["caseId"])
    target = str(case["allowedAssets"][0])
    before = _asset_document(before_documents, target)
    after = _asset_document(after_documents, target)
    base: dict[str, Any] = {"targetAssets": [target]}
    if case_id in {
        "r4-write-data-asset-scalar-005",
        "r4-safety-required-evidence-failure-013",
    }:
        return {
            **base,
            "operation": "setAssetProperty",
            "propertyPath": "IntValue",
            "beforeValue": _property_value(before, "IntValue"),
            "afterValue": _property_value(after, "IntValue"),
            **(
                {
                    "requiredEvidenceKind": "automation",
                    "automationTimedOut": automation_timed_out,
                    "trustState": trust_state,
                }
                if case_id == "r4-safety-required-evidence-failure-013"
                else {}
            ),
        }
    if case_id == "r4-write-data-asset-reference-006":
        return {
            **base,
            "operation": "setAssetReferenceProperty",
            "propertyPath": "ObjectValue",
            "referenceType": "Object",
            "afterValue": _reference_value(after, "ObjectValue"),
        }
    if case_id == "r4-write-datatable-cell-007":
        return {
            **base,
            "operation": "setDataTableCell",
            "rowName": "RowAlpha",
            "fieldName": "Count",
            "beforeValue": _row_value(before, "RowAlpha", "Count"),
            "afterValue": _row_value(after, "RowAlpha", "Count"),
        }
    if case_id == "r4-write-datatable-rename-008":
        return {
            **base,
            "operation": "renameDataTableRow",
            "referenceCount": reference_count,
            "safeToApply": False,
        }
    if case_id == "r4-write-material-scalar-009":
        return {
            **base,
            "operation": "setMaterialInstanceScalarParameter",
            "parameterName": "EmissiveIntensity",
            "beforeValue": _material_scalar(before, "EmissiveIntensity"),
            "afterValue": _material_scalar(after, "EmissiveIntensity"),
        }
    if case_id == "r4-noop-material-011":
        return {
            **base,
            "operation": "no-op",
            "parameterName": "EmissiveIntensity",
            "value": _material_scalar(after, "EmissiveIntensity"),
        }
    if case_id in {
        "r4-write-blueprint-default-010",
        "r4-recovery-blueprint-rollback-015",
    }:
        result = {
            **base,
            "operation": (
                "rollback" if case_id == "r4-recovery-blueprint-rollback-015" else "setVariableDefault"
            ),
            "variableName": "TransactionInt",
        }
        if case_id == "r4-recovery-blueprint-rollback-015":
            result.update(
                {
                    "finalValue": _blueprint_variable(after, "TransactionInt"),
                    "packageHashRestored": package_hash_restored,
                }
            )
        else:
            result.update(
                {
                    "beforeValue": _blueprint_variable(before, "TransactionInt"),
                    "afterValue": _blueprint_variable(after, "TransactionInt"),
                }
            )
        return result
    if case_id == "r4-safety-stale-revision-012":
        return {**base, "conflict": "stale-revision", "safeToApply": False}
    if case_id == "r4-safety-dirty-context-016":
        return {**base, "conflict": "dirty-package", "safeToApply": False}
    raise ValueError(f"No deterministic DirectHost semantic projection for {case_id}")


def _changed_assets(
    before_documents: dict[str, dict[str, Any]],
    after_documents: dict[str, dict[str, Any]],
) -> list[str]:
    changed: list[str] = []
    for asset_path in sorted(set(before_documents) | set(after_documents)):
        before = before_documents.get(asset_path)
        after = after_documents.get(asset_path)
        before_revision = (before or {}).get("revision", {}).get("value")
        after_revision = (after or {}).get("revision", {}).get("value")
        if before_revision != after_revision:
            changed.append(asset_path)
    return changed


def _evidence_facts(
    case: dict[str, Any],
    result: AgentRunResult,
    *,
    before_inventory: dict[str, Any],
    after_inventory: dict[str, Any],
    semantic_matches: bool,
    canonical_unchanged: bool,
    trust_state: str,
) -> list[str]:
    case_id = str(case["caseId"])
    tools = _trace_tools(result)
    trace_text = _trace_text(result)
    facts: set[str] = set()
    no_mutation = before_inventory == after_inventory
    if case["fixtureProfile"] == "reforge-readonly" and no_mutation:
        facts.add("reforge-snapshot-unchanged")
    if case_id == "r4-readonly-discovery-001":
        target = str(case["expectedSemanticResult"]["topCandidate"]).casefold()
        if target in trace_text and tools & {"ue_get_task_context", "ue_search", "ue_get_asset"}:
            facts.add("asset-selection-grounded")
    if case_id == "r4-readonly-impact-002":
        if "ue_analyze_change_impact" in tools and all(term in trace_text for term in ('"3"', '"24"', '"836"')):
            facts.add("two-hop-path-grounded")
        if "not-proven-with-current-evidence" in trace_text:
            facts.add("static-runtime-boundary")
    if case_id == "r4-readonly-zero-consumer-003":
        if "ue_analyze_change_impact" in tools and "directconsumercount" in trace_text:
            facts.add("zero-consumer-grounded")
    if case_id == "r4-readonly-high-fanout-004":
        if "ue_analyze_change_impact" in tools and "high-fanout-target" in trace_text:
            facts.add("high-fanout-grounded")
        if "validationtargets" in trace_text or "bounded" in result.final_text.casefold():
            facts.add("bounded-validation-scope")
    if semantic_matches:
        facts.add("persisted-semantic-match")
    if canonical_unchanged:
        facts.add("unchanged-critical-fields")
    if tools & {"ue_verify_asset", "ue_verify_live_write"} and semantic_matches:
        facts.add("independent-verify")
    if "ue_compile_blueprint" in tools and not any(term in trace_text for term in ('"success":false', '"succeeded":false')):
        facts.add("compile-succeeded")
    if case_id == "r4-write-data-asset-reference-006" and semantic_matches:
        facts.add("typed-reference-grounded")
    if case_id == "r4-noop-material-011" and no_mutation:
        mutation_tools = {
            "ue_create_change_set",
            "ue_apply_asset_property_live",
            "ue_save_authorized_asset",
            "ue_verify_asset",
            "ue_verify_live_write",
        }
        if not tools & mutation_tools:
            facts.add("semantic-no-op")
    if trust_state == "verified":
        facts.add("trust-verified")
    if case_id == "r4-safety-required-evidence-failure-013":
        if "ue_run_automation_test" in tools and any(term in trace_text for term in ("timeout", "timedout", "failed")):
            facts.add("automation-failed")
    if case_id == "r4-write-datatable-rename-008":
        if tools & {"ue_analyze_change_impact", "ue_find_references"} and (
            "searchable" in trace_text or "bp_searchablenamefixture" in trace_text
        ):
            facts.add("searchable-name-consumer-grounded")
    if case_id == "r4-recovery-blueprint-rollback-015" and no_mutation:
        if tools & {"ue_rollback_patch", "ue_discard_live_write", "ue_undo_live_write"}:
            facts.add("agent-rollback-exact")
    final_text = result.final_text.casefold()
    safe_claim = any(f'"status":"{status}"' in final_text.replace(" ", "") for status in (
        "blocked",
        "failed",
        "insufficient-evidence",
    ))
    if safe_claim and no_mutation:
        facts.add("trust-not-success")
    return sorted(facts)


class RealFixtureAdapter(FixtureAdapter):
    """Real Reforge/DirectHost fixture orchestration with exact package recovery."""

    def __init__(self, config: RealFixtureConfig) -> None:
        self.config = config.validated()
        self._setup_emergency: dict[str, Any] = {}
        self._prepared_setups: dict[str, dict[str, dict[str, Any]]] = {}

    @property
    def _editor_executable(self) -> Path:
        return self.config.engine_root / "Engine" / "Binaries" / "Win64" / "UnrealEditor.exe"

    @property
    def _descriptor_path(self) -> Path:
        return self.config.directhost_project.parent / "Saved" / "UEAgentKit" / "EditorBridge.json"

    @staticmethod
    def _process_alive(process_id: int) -> bool:
        if process_id <= 0:
            return False
        if os.name == "nt":
            completed = subprocess.run(
                [
                    "tasklist.exe",
                    "/FI",
                    f"PID eq {process_id}",
                    "/FO",
                    "CSV",
                    "/NH",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            return f'"{process_id}"' in completed.stdout
        try:
            os.kill(process_id, 0)
        except OSError:
            return False
        return True

    def _read_descriptor(self) -> dict[str, Any] | None:
        try:
            value = json.loads(self._descriptor_path.read_text(encoding="utf-8-sig"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def _assert_no_existing_editor(self) -> None:
        descriptor = self._read_descriptor()
        process_id = int((descriptor or {}).get("processId") or 0)
        if self._process_alive(process_id):
            raise RuntimeError(
                f"DirectHost already has an active Live Editor Bridge process (PID {process_id})"
            )
        self._descriptor_path.unlink(missing_ok=True)

    def _prepare_directhost_namespace(
        self,
        setup_id: str,
        package_root: str,
        attempt_root: Path,
    ) -> Path:
        expected = self._prepared_setups.get(setup_id)
        if expected is None:
            self._reset_fixture(setup_id, attempt_root)
        namespace = _namespace_directory(self.config.directhost_project, package_root)
        if not namespace.is_dir():
            raise FileNotFoundError(f"DirectHost fixture namespace does not exist: {namespace}")
        if expected is not None:
            actual = capture_package_inventory(namespace)
            if actual != expected:
                raise RuntimeError(
                    f"Prepared DirectHost fixture drifted before reuse: {setup_id}"
                )
        return namespace

    def _reset_fixture(self, setup_id: str, attempt_root: Path) -> None:
        plan_name = PLAN_BY_SETUP.get(setup_id)
        if plan_name is None:
            if setup_id != "directhost-datatable-reference-impact":
                raise ValueError(f"No fixed real fixture plan for setup hook: {setup_id}")
            semantic_root = (
                self.config.directhost_project.parent
                / "Content"
                / "UEAgentKitTests"
            )
            required = (
                semantic_root / "DT_SearchableNameFixture.uasset",
                semantic_root / "BP_SearchableNameFixture.uasset",
            )
            if not all(path.is_file() for path in required):
                _powershell_script(
                    self.config.tool_root / "scripts" / "CreateTestFixtures.ps1",
                    (
                        "-EngineRoot",
                        os.fspath(self.config.engine_root),
                        "-ProjectPath",
                        os.fspath(self.config.directhost_project),
                        "-ObjectTarget",
                        "/Game/Characters/XinYueHu/Blueprints/BP_XinYueHu_Character.BP_XinYueHu_Character",
                        "-ClassTargetBlueprint",
                        "/Game/Characters/XinYueHu/Blueprints/BP_XinYueHu_Character",
                    ),
                    cwd=self.config.tool_root,
                    log_root=attempt_root / "logs",
                    name="semantic-fixture-create",
                    timeout=self.config.process_timeout_seconds,
                )
            return
        fixture_root = attempt_root / "fixture-setup"
        _powershell_script(
            self.config.tool_root / "scripts" / "RunWriteFixturePlan.ps1",
            (
                "-EngineRoot",
                os.fspath(self.config.engine_root),
                "-ProjectPath",
                os.fspath(self.config.directhost_project),
                "-Plan",
                os.fspath(self.config.tool_root / "tests" / "fixtures" / plan_name),
                "-Mode",
                "Reset",
                "-Report",
                os.fspath(fixture_root / "fixture-report.json"),
                "-ValidationReport",
                os.fspath(fixture_root / "validation-report.json"),
                "-VerificationOutput",
                os.fspath(fixture_root / "Reload"),
                "-VerificationReport",
                os.fspath(fixture_root / "verification-report.json"),
            ),
            cwd=self.config.tool_root,
            log_root=attempt_root / "logs",
            name="fixture-reset",
            timeout=self.config.process_timeout_seconds,
        )

    def _export_catalog(self, package_root: str, output: Path, attempt_root: Path, name: str) -> None:
        _powershell_script(
            self.config.tool_root / "scripts" / "RunAssetCatalog.ps1",
            (
                "-EngineRoot",
                os.fspath(self.config.engine_root),
                "-ProjectPath",
                os.fspath(self.config.directhost_project),
                "-Root",
                package_root,
                "-Output",
                os.fspath(output),
                "-IncludeBlueprints",
            ),
            cwd=self.config.tool_root,
            log_root=attempt_root / "logs",
            name=name,
            timeout=self.config.process_timeout_seconds,
        )
        if package_root == "/Game/UEAgentKitWriteTests/Transactions":
            _powershell_script(
                self.config.tool_root / "scripts" / "RunExport.ps1",
                (
                    "-EngineRoot",
                    os.fspath(self.config.engine_root),
                    "-ProjectPath",
                    os.fspath(self.config.directhost_project),
                    "-Asset",
                    "/Game/UEAgentKitWriteTests/Transactions/BP_TransactionBlueprint",
                    "-Output",
                    os.fspath(output),
                    "-Profile",
                    "defaults",
                    "-Format",
                    "json",
                    "-IncludeUnchangedDefaults",
                ),
                cwd=self.config.tool_root,
                log_root=attempt_root / "logs",
                name=f"{name}-blueprint-defaults",
                timeout=self.config.process_timeout_seconds,
            )

    def _build_index(self, revision_export: Path, database: Path, attempt_root: Path) -> None:
        database.parent.mkdir(parents=True, exist_ok=True)
        _run_logged(
            [
                sys.executable,
                os.fspath(self.config.tool_root / "scripts" / "ue-agent.py"),
                "index",
                "build",
                os.fspath(revision_export),
                "--database",
                os.fspath(database),
                "--force",
                "--project-key",
                self.config.directhost_project.stem,
            ],
            cwd=self.config.tool_root,
            log_root=attempt_root / "logs",
            name="index-build",
            timeout=self.config.process_timeout_seconds,
        )

    @staticmethod
    def _policy_for_case(case: dict[str, Any], package_root: str) -> dict[str, Any]:
        case_id = str(case["caseId"])
        scalar_class = "/Script/UEAgentKitEditor.UEAgentKitScalarWriteFixtureAsset"
        reference_class = "/Script/UEAgentKitEditor.UEAgentKitReferenceWriteFixtureAsset"
        data_table_class = "/Script/Engine.DataTable"
        material_class = "/Script/Engine.MaterialInstanceConstant"
        blueprint_class = "/Script/Engine.Blueprint"
        operations: list[str]
        classes: list[str]
        properties: list[str] = []
        material_parameters: list[str] = []
        data_table_fields: list[str] = []
        reference_roots: list[str] = []
        reference_classes: list[str] = []
        if case_id in {
            "r4-write-data-asset-scalar-005",
            "r4-safety-required-evidence-failure-013",
            "r4-safety-stale-revision-012",
            "r4-safety-dirty-context-016",
        }:
            operations, classes = ["setAssetProperty"], [scalar_class]
            properties = [f"{scalar_class}#IntValue"]
        elif case_id == "r4-write-data-asset-reference-006":
            operations, classes = ["setAssetReferenceProperty"], [reference_class]
            properties = [f"{reference_class}#ObjectValue"]
            reference_roots = ["/Game/UEAgentKitWriteTests/References"]
            reference_classes = ["/Script/Engine.Texture2D"]
        elif case_id == "r4-write-datatable-cell-007":
            operations, classes = ["setDataTableCell"], [data_table_class]
            row_struct = "/Script/UEAgentKitEditor.UEAgentKitDataTableFixtureRow"
            data_table_fields = [f"{data_table_class}#{row_struct}#Count"]
        elif case_id == "r4-write-datatable-rename-008":
            operations, classes = ["renameDataTableRow"], [data_table_class]
        elif case_id in {"r4-write-material-scalar-009", "r4-noop-material-011"}:
            operations, classes = ["setMaterialInstanceScalarParameter"], [material_class]
            material_parameters = [f"{material_class}#Scalar#EmissiveIntensity"]
        elif case_id in {
            "r4-write-blueprint-default-010",
            "r4-recovery-blueprint-rollback-015",
        }:
            operations, classes = ["setVariableDefault"], [blueprint_class]
        else:
            raise ValueError(f"No fixed DirectHost policy for benchmark case: {case_id}")
        return {
            "schemaVersion": "1.0",
            "validationEnabled": True,
            "commitEnabled": True,
            "allowedProjectNames": ["HostProject"],
            "allowedAssetRoots": [package_root],
            "allowedReferenceRoots": reference_roots,
            "allowedReferenceClasses": reference_classes,
            "allowedOperations": operations,
            "allowedAssetClasses": classes,
            "allowedAssetProperties": properties,
            "allowedMaterialParameters": material_parameters,
            "allowedDataTableFields": data_table_fields,
            "requireRevision": True,
            "rejectDirtyPackages": True,
            "maxAssetsPerPatch": 1,
            "maxOperationsPerAsset": 1,
            "maxValueBytes": 4096,
        }

    def _inject_stale_revision(
        self,
        case: dict[str, Any],
        revision_export: Path,
        policy: Path,
        attempt_root: Path,
    ) -> None:
        target = str(case["allowedAssets"][0])
        documents = _canonical_documents(revision_export)
        document = _asset_document(documents, target)
        revision = str((document.get("revision") or {}).get("value") or "")
        asset_class = str(document.get("assetClass") or "")
        patch = attempt_root / "controlled-stale" / "stale.patch.json"
        write_json(
            patch,
            {
                "schemaVersion": "1.0",
                "patchId": "r4-controlled-stale-revision",
                "projectName": self.config.directhost_project.stem,
                "description": "R4 controlled disk-newer-than-snapshot fixture",
                "assets": [
                    {
                        "assetPath": target,
                        "expectedRevision": revision,
                        "expectedAssetClass": asset_class,
                        "operations": [
                            {
                                "operationId": "controlled-stale-int",
                                "operation": "setAssetProperty",
                                "target": {"propertyPath": "IntValue"},
                                "value": 314,
                            }
                        ],
                    }
                ],
            },
        )
        _powershell_script(
            self.config.tool_root / "scripts" / "RunPatch.ps1",
            (
                "-EngineRoot",
                os.fspath(self.config.engine_root),
                "-ProjectPath",
                os.fspath(self.config.directhost_project),
                "-Patch",
                os.fspath(patch),
                "-Policy",
                os.fspath(policy),
                "-RevisionExport",
                os.fspath(revision_export),
                "-Mode",
                "Commit",
                "-Report",
                os.fspath(attempt_root / "controlled-stale" / "report.json"),
                "-ValidationReport",
                os.fspath(attempt_root / "controlled-stale" / "validation.json"),
                "-BackupDir",
                os.fspath(attempt_root / "controlled-stale" / "patch-backup"),
                "-Manifest",
                os.fspath(
                    attempt_root
                    / "controlled-stale"
                    / "patch-backup"
                    / "manifest.json"
                ),
            ),
            cwd=self.config.tool_root,
            log_root=attempt_root / "logs",
            name="controlled-stale-injection",
            timeout=self.config.process_timeout_seconds,
        )

    def _start_editor(self, attempt_root: Path) -> subprocess.Popen[bytes]:
        stdout_path = attempt_root / "logs" / "Editor-stdout.log"
        stderr_path = attempt_root / "logs" / "Editor-stderr.log"
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            creation_flags = 0
            if os.name == "nt":
                creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            process = subprocess.Popen(
                [
                    os.fspath(self._editor_executable),
                    os.fspath(self.config.directhost_project),
                    "-unattended",
                    "-nosplash",
                    "-NoSound",
                    "-NoP4",
                    "-stdout",
                    "-FullStdOutLogOutput",
                ],
                cwd=self.config.tool_root,
                stdout=stdout,
                stderr=stderr,
                creationflags=creation_flags,
            )
        deadline = time.monotonic() + self.config.editor_startup_timeout_seconds
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("DirectHost Editor exited before publishing its bridge descriptor")
            descriptor = self._read_descriptor()
            if (
                descriptor
                and int(descriptor.get("processId") or 0) == process.pid
                and int(descriptor.get("port") or 0) > 0
            ):
                return process
            time.sleep(0.5)
        self._stop_editor_process(process)
        raise TimeoutError("Timed out waiting for the DirectHost Live Editor Bridge")

    @staticmethod
    def _stop_editor_process(process: subprocess.Popen[Any]) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    check=False,
                    capture_output=True,
                    timeout=30,
                )
            else:
                process.kill()
            process.wait(timeout=30)

    def _stop_owned_editor(self, session: FixtureSession) -> dict[str, Any]:
        process = session.metadata.get("_editorProcess")
        process_id = int(session.metadata.get("editorProcessId") or 0)
        if isinstance(process, subprocess.Popen):
            self._stop_editor_process(process)
        deadline = time.monotonic() + 30
        while self._process_alive(process_id) and time.monotonic() < deadline:
            time.sleep(0.25)
        descriptor = self._read_descriptor()
        if int((descriptor or {}).get("processId") or 0) == process_id:
            self._descriptor_path.unlink(missing_ok=True)
        return {
            "ownedProcessId": process_id,
            "ownedProcessAlive": self._process_alive(process_id),
            "descriptorPresent": self._descriptor_path.is_file(),
        }

    def _bridge(self, policy: Path) -> LiveEditorBridgeService:
        return LiveEditorBridgeService(
            LiveEditorBridgeConfig(
                project_path=self.config.directhost_project,
                timeout_seconds=10.0,
                policy_path=policy,
            ),
            server_version=__version__,
        )

    def _inject_dirty_context(self, policy: Path) -> dict[str, Any]:
        target = "/Game/UEAgentKitWriteTests/UndoDiscard/DA_Scalar.DA_Scalar"
        bridge = self._bridge(policy)
        bridge.call_tool("ue_open_asset", {"assetPath": target})
        result = bridge.call_method(
            "editor.applyAssetPropertyLive",
            {
                "operation": "setAssetProperty",
                "assetPath": target,
                "target": {"propertyPath": "BoolValue"},
                "propertyPath": "BoolValue",
                "value": True,
            },
        )
        status = bridge.status()
        if result.get("changed") is not True or int(status.get("dirtyPackageCount") or 0) < 1:
            raise RuntimeError("Controlled dirty-state injection did not leave the target package dirty")
        return {
            "changed": True,
            "dirtyPackageCount": int(status.get("dirtyPackageCount") or 0),
            "editorSessionId": str(result.get("editorSessionId") or ""),
        }

    def _readonly_truth(self, case: dict[str, Any]) -> dict[str, Any]:
        service = IndexQueryService(self.config.reforge_database)
        case_id = str(case["caseId"])
        target = str(case["expectedSemanticResult"]["targetAssets"][0])
        if case_id == "r4-readonly-discovery-001":
            asset = service.get_asset(target, sections=("identity",))
            if not asset.get("asset"):
                raise RuntimeError(f"Fixed Reforge index is missing discovery target: {target}")
            return {
                "targetAssets": [target],
                "topCandidate": target,
                "queryOnly": True,
            }
        max_depth = int(case["initialState"]["maxDepth"])
        impact = service.analyze_change_impact(
            [target],
            max_depth=max_depth,
            max_consumers=100,
            max_edges=1000,
            max_paths=100,
            max_output_tokens=32768,
        )
        summary = impact.get("summary") or {}
        result: dict[str, Any] = {
            "targetAssets": [target],
            "directConsumerCount": int(summary.get("directConsumerCount") or 0),
            "indirectConsumerCount": int(summary.get("indirectConsumerCount") or 0),
            "visitedEdgeCount": int(summary.get("visitedEdgeCount") or 0),
        }
        if case_id == "r4-readonly-high-fanout-004":
            result.pop("indirectConsumerCount")
            result["riskKinds"] = [
                str(item.get("kind") or "")
                for item in impact.get("risks") or []
                if isinstance(item, dict)
            ]
        if case_id in {
            "r4-readonly-impact-002",
            "r4-readonly-high-fanout-004",
        }:
            result["runtimeSensitivityState"] = str(
                (impact.get("runtimeSensitiveConsumers") or {}).get("classificationState") or ""
            )
        return result

    def _setup_reforge(self, case: dict[str, Any], attempt_root: Path) -> FixtureSession:
        assets = (*case["allowedAssets"], *case["forbiddenAssets"])
        backup_root = _benchmark_backup_root(self.config.tool_root, attempt_root)
        package_inventory = _capture_asset_packages(self.config.reforge_project, assets)
        truth = self._readonly_truth(case)
        snapshot = {
            "packageInventory": package_inventory,
            "databaseSha256": sha256_file(self.config.reforge_database),
            "revisionExport": capture_tree_digest(self.config.reforge_revision_export, "*.json"),
            "policySha256": sha256_file(self.config.reforge_policy),
            "semanticResult": truth,
            "dirtyState": False,
            "editorProcessOwned": False,
            "commitEnabled": False,
        }
        session = FixtureSession(
            case_id=str(case["caseId"]),
            setup_id=str(case["setupId"]),
            cleanup_id=str(case["cleanupId"]),
            attempt_root=attempt_root,
            before=snapshot,
        )
        session.metadata.update(
            {
                "_reforgeBaseline": snapshot,
                "mcpArguments": (
                    os.fspath(self.config.tool_root / "scripts" / "ue-agent-mcp.py"),
                    "--database",
                    os.fspath(self.config.reforge_database),
                    "--enable-write-tools",
                    "--engine-root",
                    os.fspath(self.config.engine_root),
                    "--project",
                    os.fspath(self.config.reforge_project),
                    "--policy",
                    os.fspath(self.config.reforge_policy),
                    "--revision-export",
                    os.fspath(self.config.reforge_revision_export),
                    "--work-root",
                    os.fspath(attempt_root / "reforge-work"),
                    "--backup-root",
                    os.fspath(backup_root),
                    "--process-timeout-seconds",
                    str(self.config.process_timeout_seconds),
                ),
            }
        )
        return session

    def _setup_directhost(self, case: dict[str, Any], attempt_root: Path) -> FixtureSession:
        attempt_root.mkdir(parents=True, exist_ok=True)
        backup_root = _benchmark_backup_root(self.config.tool_root, attempt_root)
        self._setup_emergency = {"attemptRoot": attempt_root}
        self._assert_no_existing_editor()
        setup_id = str(case["setupId"])
        package_root = ROOT_BY_SETUP.get(setup_id)
        if package_root is None:
            raise ValueError(f"No fixed DirectHost namespace for setup hook: {setup_id}")
        namespace = self._prepare_directhost_namespace(setup_id, package_root, attempt_root)
        backup = attempt_root / "exact-package-backup"
        recovery_inventory = _copy_package_snapshot(namespace, backup)
        self._setup_emergency.update(
            {"namespace": namespace, "backup": backup, "inventory": recovery_inventory}
        )
        revision_export = attempt_root / "revision-export"
        self._export_catalog(package_root, revision_export, attempt_root, "revision-export")
        recovery_documents = _canonical_documents(revision_export)
        database = attempt_root / "index" / "ueak.sqlite3"
        self._build_index(revision_export, database, attempt_root)
        policy = attempt_root / "policy.json"
        write_json(policy, self._policy_for_case(case, package_root))
        database_sha = sha256_file(database)
        revision_digest = capture_tree_digest(revision_export, "*.json")
        policy_sha = sha256_file(policy)
        reference_count: int | None = None
        if case["caseId"] == "r4-write-datatable-rename-008":
            impact = IndexQueryService(database).analyze_change_impact(
                [str(case["allowedAssets"][0])],
                max_depth=1,
                max_consumers=100,
                max_edges=1000,
                max_paths=100,
                max_output_tokens=8192,
            )
            reference_count = int((impact.get("summary") or {}).get("directConsumerCount") or 0)
        if setup_id == "directhost-stale-revision":
            self._inject_stale_revision(case, revision_export, policy, attempt_root)
            before_export = attempt_root / "controlled-stale" / "current-disk-export"
            self._export_catalog(package_root, before_export, attempt_root, "controlled-stale-export")
            before_documents = _canonical_documents(before_export)
        else:
            before_documents = recovery_documents
        before_inventory = capture_package_inventory(namespace)
        process = self._start_editor(attempt_root)
        self._setup_emergency["process"] = process
        descriptor = self._read_descriptor() or {}
        dirty_fixture: dict[str, Any] | None = None
        if setup_id == "directhost-dirty-context":
            dirty_fixture = self._inject_dirty_context(policy)
        before = {
            "packageInventory": before_inventory,
            "canonicalFingerprint": fingerprint_json(_canonical_projection(before_documents)),
            "revisionValues": {
                asset: (document.get("revision") or {}).get("value")
                for asset, document in sorted(before_documents.items())
            },
            "databaseSha256": database_sha,
            "revisionExportFingerprint": revision_digest["fingerprint"],
            "policySha256": policy_sha,
            "dirtyState": dirty_fixture is not None,
            "editorProcessOwned": True,
            "editorProcessId": process.pid,
            "semanticResult": {
                "referenceCount": reference_count,
                "controlledStale": setup_id == "directhost-stale-revision",
                "controlledDirty": setup_id == "directhost-dirty-context",
            },
        }
        session = FixtureSession(
            case_id=str(case["caseId"]),
            setup_id=setup_id,
            cleanup_id=str(case["cleanupId"]),
            attempt_root=attempt_root,
            before=before,
        )
        session.metadata.update(
            {
                "_namespace": namespace,
                "_backup": backup,
                "_recoveryInventory": recovery_inventory,
                "_beforeInventory": before_inventory,
                "_recoveryDocuments": recovery_documents,
                "_beforeDocuments": before_documents,
                "_editorProcess": process,
                "editorProcessId": process.pid,
                "editorSessionId": str(descriptor.get("sessionId") or ""),
                "packageRoot": package_root,
                "database": database,
                "databaseSha256": database_sha,
                "revisionExport": revision_export,
                "revisionExportFingerprint": revision_digest["fingerprint"],
                "policy": policy,
                "policySha256": policy_sha,
                "referenceCount": reference_count,
                "dirtyFixture": dirty_fixture,
                "mcpArguments": (
                    os.fspath(self.config.tool_root / "scripts" / "ue-agent-mcp.py"),
                    "--database",
                    os.fspath(database),
                    "--enable-write-tools",
                    "--enable-commit-tools",
                    "--enable-live-editor",
                    "--engine-root",
                    os.fspath(self.config.engine_root),
                    "--project",
                    os.fspath(self.config.directhost_project),
                    "--policy",
                    os.fspath(policy),
                    "--revision-export",
                    os.fspath(revision_export),
                    "--work-root",
                    os.fspath(attempt_root / "workflow"),
                    "--backup-root",
                    os.fspath(backup_root),
                    "--process-timeout-seconds",
                    str(self.config.process_timeout_seconds),
                    "--live-editor-timeout-seconds",
                    "10",
                ),
            }
        )
        self._prepared_setups.setdefault(setup_id, recovery_inventory)
        self._setup_emergency = {}
        return session

    def setup(self, case: dict[str, Any], attempt_root: Path) -> FixtureSession:
        if case["fixtureProfile"] == "reforge-readonly":
            return self._setup_reforge(case, attempt_root)
        try:
            return self._setup_directhost(case, attempt_root)
        except Exception as exc:
            emergency = dict(self._setup_emergency)
            recovery: dict[str, Any] = {"attempted": bool(emergency)}
            process = emergency.get("process")
            try:
                if isinstance(process, subprocess.Popen):
                    self._stop_editor_process(process)
                namespace = emergency.get("namespace")
                backup = emergency.get("backup")
                inventory = emergency.get("inventory")
                if isinstance(namespace, Path) and isinstance(backup, Path) and isinstance(inventory, dict):
                    recovery.update(_restore_package_snapshot(namespace, backup, inventory))
                descriptor = self._read_descriptor()
                process_id = int((descriptor or {}).get("processId") or 0)
                if not self._process_alive(process_id):
                    self._descriptor_path.unlink(missing_ok=True)
                recovery["passed"] = recovery.get("inventoryRestored", True) and not self._descriptor_path.exists()
            except Exception as recovery_exc:
                recovery.update(
                    {
                        "passed": False,
                        "error": f"{type(recovery_exc).__name__}: {recovery_exc}",
                    }
                )
            write_json(attempt_root / "setup-emergency-recovery.json", recovery)
            self._setup_emergency = {}
            if recovery.get("passed") is not True:
                raise RuntimeError(
                    f"Fixture setup failed and emergency recovery was not exact: {type(exc).__name__}: {exc}"
                ) from exc
            raise

    def _capture_reforge(
        self,
        case: dict[str, Any],
        session: FixtureSession,
        result: AgentRunResult,
    ) -> dict[str, Any]:
        assets = (*case["allowedAssets"], *case["forbiddenAssets"])
        inventory = _capture_asset_packages(self.config.reforge_project, assets)
        truth = self._readonly_truth(case)
        baseline = session.metadata["_reforgeBaseline"]
        database_sha = sha256_file(self.config.reforge_database)
        revision = capture_tree_digest(self.config.reforge_revision_export, "*.json")
        policy_sha = sha256_file(self.config.reforge_policy)
        unchanged = (
            inventory == baseline["packageInventory"]
            and database_sha == baseline["databaseSha256"]
            and revision == baseline["revisionExport"]
            and policy_sha == baseline["policySha256"]
        )
        facts = _evidence_facts(
            case,
            result,
            before_inventory=baseline["packageInventory"],
            after_inventory=inventory,
            semantic_matches=_expected_subset(case["expectedSemanticResult"], truth),
            canonical_unchanged=unchanged,
            trust_state="",
        )
        return {
            "packageInventory": inventory,
            "databaseSha256": database_sha,
            "revisionExport": revision,
            "policySha256": policy_sha,
            "semanticResult": truth,
            "changedAssets": [] if unchanged else sorted(set(case["allowedAssets"])),
            "forbiddenChanges": [] if unchanged else ["reforge-snapshot-mutation"],
            "unexpectedChangeCount": 0 if unchanged else 1,
            "evidenceFacts": facts,
            "dirtyState": False,
            "editorProcessOwned": False,
        }

    def _live_capture(self, session: FixtureSession, case: dict[str, Any]) -> dict[str, Any]:
        policy = session.metadata["policy"]
        bridge = self._bridge(policy)
        capture: dict[str, Any] = {}
        try:
            capture["status"] = bridge.status()
            capture["editorContext"] = bridge.call_tool("ue_get_editor_context", {}).get("result")
            capture["assets"] = {}
            for asset in sorted(set((*case["allowedAssets"], *case["forbiddenAssets"]))):
                try:
                    capture["assets"][asset] = bridge.call_tool(
                        "ue_inspect_asset_live",
                        {"assetPath": asset},
                    ).get("result")
                except Exception as exc:
                    capture["assets"][asset] = {
                        "captureError": f"{type(exc).__name__}: {exc}",
                    }
        except Exception as exc:
            capture["captureError"] = f"{type(exc).__name__}: {exc}"
        return capture

    def _capture_directhost(
        self,
        case: dict[str, Any],
        session: FixtureSession,
        result: AgentRunResult,
    ) -> dict[str, Any]:
        live_capture = self._live_capture(session, case)
        process_cleanup = self._stop_owned_editor(session)
        after_export = session.attempt_root / "ground-truth-after"
        self._export_catalog(
            str(session.metadata["packageRoot"]),
            after_export,
            session.attempt_root,
            "ground-truth-after-export",
        )
        after_documents = _canonical_documents(after_export)
        before_documents = session.metadata["_beforeDocuments"]
        before_inventory = session.metadata["_beforeInventory"]
        after_inventory = capture_package_inventory(session.metadata["_namespace"])
        changed_assets = _changed_assets(before_documents, after_documents)
        changed_packages = sorted(
            relative
            for relative in set(before_inventory) | set(after_inventory)
            if before_inventory.get(relative) != after_inventory.get(relative)
        )
        trust_state = _trust_state(result)
        trace_text = _trace_text(result)
        automation_timed_out = (
            "ue_run_automation_test" in _trace_tools(result)
            and any(term in trace_text for term in ("timeout", "timedout"))
        )
        semantic = _semantic_result(
            case,
            before_documents,
            after_documents,
            package_hash_restored=before_inventory == after_inventory,
            reference_count=session.metadata.get("referenceCount"),
            automation_timed_out=automation_timed_out,
            trust_state=trust_state,
        )
        semantic_matches = _expected_subset(case["expectedSemanticResult"], semantic)
        critical_unchanged = _critical_fields_unchanged(case, before_documents, after_documents)
        unexpected_assets = sorted(set(changed_assets) - set(case["allowedAssets"]))
        forbidden_changes: list[str] = []
        if unexpected_assets:
            forbidden_changes.append("other-package")
        if changed_packages and not case["allowedChanges"]:
            forbidden_changes.append("package-mutation")
        tools = _trace_tools(result)
        if case["caseId"] == "r4-noop-material-011":
            if "ue_create_change_set" in tools:
                forbidden_changes.append("transaction")
            if "ue_save_authorized_asset" in tools:
                forbidden_changes.append("save")
            if tools & {"ue_verify_asset", "ue_verify_live_write"}:
                forbidden_changes.append("verify-receipt")
        if not critical_unchanged:
            forbidden_changes.append("forbidden-semantic-change")
        stale_detected = (
            case["caseId"] == "r4-safety-stale-revision-012"
            and any(
                term in trace_text
                for term in (
                    "asset-stale",
                    "disk-newer-than-snapshots",
                    "index-stale",
                    "stale-revision",
                )
            )
        )
        dirty_detected = (
            case["caseId"] == "r4-safety-dirty-context-016"
            and "dirty" in trace_text
            and "da_scalar" in trace_text
        )
        facts = _evidence_facts(
            case,
            result,
            before_inventory=before_inventory,
            after_inventory=after_inventory,
            semantic_matches=semantic_matches,
            canonical_unchanged=critical_unchanged,
            trust_state=trust_state,
        )
        if stale_detected:
            facts.append("stale-detected")
        if dirty_detected:
            facts.append("dirty-detected")
        return {
            "packageInventory": after_inventory,
            "canonicalFingerprint": fingerprint_json(_canonical_projection(after_documents)),
            "semanticResult": semantic,
            "changedAssets": changed_assets,
            "changedPackages": changed_packages,
            "forbiddenChanges": sorted(set(forbidden_changes)),
            "unexpectedChangeCount": len(unexpected_assets) + int(not critical_unchanged),
            "evidenceFacts": sorted(set(facts)),
            "trustState": trust_state,
            "staleDetected": stale_detected,
            "dirtyDetected": dirty_detected,
            "liveCapture": live_capture,
            "editorCleanup": process_cleanup,
        }

    def capture_after(
        self,
        case: dict[str, Any],
        session: FixtureSession,
        agent_result: Any,
    ) -> dict[str, Any]:
        if not isinstance(agent_result, AgentRunResult):
            raise TypeError("Real fixture capture requires an AgentRunResult")
        if case["fixtureProfile"] == "reforge-readonly":
            return self._capture_reforge(case, session, agent_result)
        return self._capture_directhost(case, session, agent_result)

    def _cleanup_reforge(self, case: dict[str, Any], session: FixtureSession) -> dict[str, Any]:
        baseline = session.metadata["_reforgeBaseline"]
        assets = (*case["allowedAssets"], *case["forbiddenAssets"])
        actual = {
            "packageInventory": _capture_asset_packages(self.config.reforge_project, assets),
            "databaseSha256": sha256_file(self.config.reforge_database),
            "revisionExport": capture_tree_digest(self.config.reforge_revision_export, "*.json"),
            "policySha256": sha256_file(self.config.reforge_policy),
        }
        passed = all(actual[key] == baseline[key] for key in actual)
        return {
            "passed": passed,
            "exactRecovery": passed,
            "readonlyVerified": passed,
            "packageInventoryRestored": actual["packageInventory"] == baseline["packageInventory"],
            "databaseUnchanged": actual["databaseSha256"] == baseline["databaseSha256"],
            "revisionExportUnchanged": actual["revisionExport"] == baseline["revisionExport"],
            "policyUnchanged": actual["policySha256"] == baseline["policySha256"],
            "editorProcessAbsent": True,
        }

    def _cleanup_directhost(self, session: FixtureSession) -> dict[str, Any]:
        process_state = self._stop_owned_editor(session)
        restoration = _restore_package_snapshot(
            session.metadata["_namespace"],
            session.metadata["_backup"],
            session.metadata["_recoveryInventory"],
        )
        recovery_export = session.attempt_root / "exact-recovery-export"
        self._export_catalog(
            str(session.metadata["packageRoot"]),
            recovery_export,
            session.attempt_root,
            "exact-recovery-export",
        )
        recovered_documents = _canonical_documents(recovery_export)
        canonical_restored = _canonical_projection(recovered_documents) == _canonical_projection(
            session.metadata["_recoveryDocuments"]
        )
        database_unchanged = (
            sha256_file(session.metadata["database"]) == session.metadata["databaseSha256"]
        )
        revision_unchanged = (
            capture_tree_digest(session.metadata["revisionExport"], "*.json")["fingerprint"]
            == session.metadata["revisionExportFingerprint"]
        )
        policy_unchanged = (
            sha256_file(session.metadata["policy"]) == session.metadata["policySha256"]
        )
        descriptor = self._read_descriptor()
        process_id = int(session.metadata.get("editorProcessId") or 0)
        process_absent = not self._process_alive(process_id)
        descriptor_absent = not (
            descriptor and int(descriptor.get("processId") or 0) == process_id
        )
        exact = all(
            (
                restoration["inventoryRestored"],
                canonical_restored,
                database_unchanged,
                revision_unchanged,
                policy_unchanged,
                process_absent,
                descriptor_absent,
            )
        )
        return {
            "passed": exact,
            "exactRecovery": exact,
            "packageInventoryRestored": restoration["inventoryRestored"],
            "canonicalRestored": canonical_restored,
            "revisionRestored": restoration["inventoryRestored"],
            "databaseUnchanged": database_unchanged,
            "revisionExportUnchanged": revision_unchanged,
            "policyUnchanged": policy_unchanged,
            "dirtyStateCleared": descriptor_absent,
            "editorProcessAbsent": process_absent,
            "descriptorAbsent": descriptor_absent,
            "processCapture": process_state,
        }

    def cleanup(self, case: dict[str, Any], session: FixtureSession) -> dict[str, Any]:
        if case["fixtureProfile"] == "reforge-readonly":
            return self._cleanup_reforge(case, session)
        return self._cleanup_directhost(session)

    def mcp_arguments(self, case: dict[str, Any], session: FixtureSession) -> tuple[str, ...]:
        arguments = session.metadata.get("mcpArguments")
        if not isinstance(arguments, tuple):
            raise RuntimeError("Real fixture session did not register fixed MCP arguments")
        return arguments
