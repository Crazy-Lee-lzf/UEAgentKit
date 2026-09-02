"""Measure deterministic M3 L0->L1 distillation performance.

Standard-library only, U0. Creates a deterministic 100-event fixture with a
mix of verified, rejection, policy, semantic-diff, supersession, and
no-output live-write events, then distills them and enforces the M3 hard gate:

  100 L0 deterministic distillation < 5000 ms

Usage:
  python scripts/MeasureMemoryDistillation.py [--out ...] [--gate]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

TOOL_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = TOOL_ROOT / "src"
for root in (SRC_ROOT,):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from ue_agent_kit.memory_l0 import MemoryL0CaptureService  # noqa: E402
from ue_agent_kit.memory_service import ProjectMemoryService  # noqa: E402
from ue_agent_kit.memory_tree import KnowledgeNodeDraft  # noqa: E402
from ue_agent_kit.memory_distill import MemoryDistillationService  # noqa: E402


REPORT_SCHEMA = "ueagentkit-memory-distillation/1.0"
PROJECT_KEY = "benchmark-distill-project"
ASSET = "/Game/Characters/Hero/DA_HeroStats.DA_HeroStats"
POLICY = {
    "schemaVersion": "1.0",
    "allowedProjectNames": [PROJECT_KEY],
    "allowedAssetRoots": ["/Game/"],
    "allowedAssetClasses": [],
    "allowedOperations": [],
    "commitEnabled": True,
}
DISTILL_HARD_MS = 5000.0


def _canonical_policy(policy: dict[str, Any]) -> str:
    return json.dumps(policy, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _policy_digest() -> str:
    return hashlib.sha256(_canonical_policy(POLICY).encode("utf-8")).hexdigest()


def _write_artifact(root: Path, relative: str, payload: dict[str, Any]) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return path


def _build_fixture(root: Path) -> Path:
    memory_path = root / "memory.sqlite3"
    artifact_root = root / "workflow"
    artifact_root.mkdir()
    policy_path = root / "policy.json"
    policy_path.write_text(_canonical_policy(POLICY), encoding="utf-8")

    service = ProjectMemoryService(database_path=memory_path, project_key=PROJECT_KEY)
    service.create_node(
        KnowledgeNodeDraft(
            project_key=PROJECT_KEY,
            path="/project",
            node_type="project",
            title=PROJECT_KEY,
            summary="Deterministic M3 distillation benchmark root.",
        )
    )
    l0 = MemoryL0CaptureService(
        database_path=memory_path,
        project_key=PROJECT_KEY,
        artifact_root=artifact_root,
    )

    digest = _policy_digest()
    # Deterministic 100-event schedule: 80 single events covering every
    # deterministic rule family plus 10 supersession pairs. Each R5 pair is
    # two events (durable live-write journal + Change Set) but exactly one
    # decisionRecord, which is the provenance shape R5 requires.
    single_kinds = (
        "verified",
        "rejection",
        "policy",
        "semantic",
        "recovery-verified",
        "recovery-partial",
        "live-write",
        "no-op",
    )
    for index in range(80):
        kind = single_kinds[index % len(single_kinds)]
        if kind == "verified":
            payload = {
                "schemaVersion": "1.0",
                "assetPath": ASSET,
                "assetRevisions": [
                    {
                        "assetPath": ASSET,
                        "revision": f"sha256:{index:064x}",
                        "revisionStable": True,
                    }
                ],
            }
            path = _write_artifact(artifact_root, f"verified/{index}.json", payload)
            l0.append_event(
                l0.artifact_draft(
                    artifact_path=path,
                    event_kind="checkpoint_set",
                    lifecycle_state="verified",
                    outcome="success",
                    asset_paths=(ASSET,),
                    change_set_id=f"cs_bench_{index}",
                    details={"checkpointSetId": f"cps_bench_{index}"},
                )
            )
        elif kind == "rejection":
            l0.capture_rejection(
                operation="apply_asset_property_live",
                error_code="revision-mismatch",
                asset_paths=(ASSET,),
                change_set_id=f"cs_bench_{index}",
                target_identity="asset-property:IntValue",
            )
        elif kind == "policy":
            l0.capture_rejection(
                operation="apply_asset_property_live",
                error_code="policy-rejected",
                asset_paths=(ASSET,),
                change_set_id=f"cs_bench_{index}",
                target_identity="asset-property:IntValue",
                policy_digest=digest,
            )
        elif kind == "semantic":
            payload = {
                "schemaVersion": "1.0",
                "assetPath": ASSET,
                "assetRevisions": [
                    {
                        "assetPath": ASSET,
                        "revision": f"sha256:{index:064x}",
                        "revisionStable": True,
                    }
                ],
                "summary": {"missingExpectedCount": 0, "unexpectedCount": 0, "analysisGapCount": 0},
            }
            path = _write_artifact(artifact_root, f"semantic/{index}.json", payload)
            l0.append_event(
                l0.artifact_draft(
                    artifact_path=path,
                    event_kind="semantic_diff",
                    lifecycle_state="verified",
                    outcome="success",
                    asset_paths=(ASSET,),
                    change_set_id=f"cs_bench_{index}",
                    details={"changeSetId": f"cs_bench_{index}"},
                )
            )
        elif kind == "recovery-verified":
            payload = {
                "schemaVersion": "1.0",
                "assetPath": ASSET,
                "assetRevisions": [
                    {
                        "assetPath": ASSET,
                        "revision": f"sha256:{index:064x}",
                        "revisionStable": True,
                    }
                ],
            }
            path = _write_artifact(artifact_root, f"recovery/verified/{index}.json", payload)
            l0.append_event(
                l0.artifact_draft(
                    artifact_path=path,
                    event_kind="recovery",
                    lifecycle_state="recovered",
                    outcome="recovered",
                    asset_paths=(ASSET,),
                    change_set_id=f"cs_bench_{index}",
                    details={"recoveryId": f"rec_bench_{index}"},
                )
            )
        elif kind == "recovery-partial":
            payload = {
                "schemaVersion": "1.0",
                "assetPath": ASSET,
                "recoveredCount": 1,
                "failedCount": 1,
            }
            path = _write_artifact(artifact_root, f"recovery/partial/{index}.json", payload)
            l0.append_event(
                l0.artifact_draft(
                    artifact_path=path,
                    event_kind="recovery",
                    lifecycle_state="partial",
                    outcome="partial",
                    asset_paths=(ASSET,),
                    change_set_id=f"cs_bench_{index}",
                    details={"recoveryId": f"rec_partial_{index}"},
                )
            )
        elif kind == "live-write":
            payload = {
                "schemaVersion": "1.0",
                "assetPath": ASSET,
                "operation": "setVariableDefault",
                "target": {"variableName": "Health"},
                "beforeValue": index,
                "afterValue": index + 1,
                "stableTargetKey": "blueprint-variable:Health",
            }
            path = _write_artifact(artifact_root, f"live/{index}.json", payload)
            l0.append_event(
                l0.artifact_draft(
                    artifact_path=path,
                    event_kind="live_write",
                    lifecycle_state="applied",
                    outcome="success",
                    asset_paths=(ASSET,),
                    change_set_id=f"cs_bench_{index}",
                    details={"operation": "setVariableDefault"},
                )
            )
        else:
            payload = {"schemaVersion": "1.0", "assetPath": ASSET}
            path = _write_artifact(artifact_root, f"noop/{index}.json", payload)
            l0.append_event(
                l0.artifact_draft(
                    artifact_path=path,
                    event_kind="change_set",
                    lifecycle_state="no-op",
                    outcome="no-op",
                    asset_paths=(ASSET,),
                    change_set_id=f"cs_bench_{index}",
                    details={"operationCount": 0},
                )
            )
    for pair in range(10):
        change_set_id = f"cs_bench_super_{pair}"
        live_payload = {
            "schemaVersion": "1.0",
            "projectName": PROJECT_KEY,
            "assetPath": ASSET,
            "operation": "setVariableDefault",
            "valueKind": "int",
            "beforeValue": pair,
            "afterValue": pair + 1,
            "target": {"variableName": "Health"},
            "stableTargetKey": "blueprint-variable:Health",
        }
        live_path = _write_artifact(artifact_root, f"live-write-journal/{change_set_id}.json", live_payload)
        l0.append_event(
            l0.artifact_draft(
                artifact_path=live_path,
                event_kind="live_write",
                lifecycle_state="superseded",
                outcome="superseded",
                asset_paths=(ASSET,),
                change_set_id=change_set_id,
                details={"operation": "setVariableDefault"},
            )
        )
        change_set_payload = {
            "schemaVersion": "2.0",
            "projectName": PROJECT_KEY,
            "changeSetId": change_set_id,
            "status": "no-op",
            "operations": [
                {
                    "receipt": f"live_{change_set_id}",
                    "planId": f"plan_{change_set_id}",
                    "assetPath": ASSET,
                    "operation": "setVariableDefault",
                    "status": "superseded",
                    "stableTargetKey": "blueprint-variable:Health",
                    "target": {"variableName": "Health", "propertyPath": ""},
                    "oldValue": pair,
                    "expectedValue": pair,
                    "afterValue": pair + 1,
                    "newValue": pair + 1,
                }
            ],
        }
        change_set_path = _write_artifact(artifact_root, f"change-sets/{change_set_id}.json", change_set_payload)
        l0.append_event(
            l0.artifact_draft(
                artifact_path=change_set_path,
                event_kind="change_set",
                lifecycle_state="superseded",
                outcome="superseded",
                asset_paths=(ASSET,),
                change_set_id=change_set_id,
                details={"operationCount": 1},
            )
        )
    return memory_path


def _environment() -> dict[str, str]:
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "implementation": platform.python_implementation(),
        "hostnameHash": "",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default="benchmarks/memory/m3_memory_distillation_after_20260831.json",
        help="output JSON report path",
    )
    parser.add_argument("--gate", action="store_true", help="fail when the hard M3 gate is not met")
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="ueak_m3_distill_bench_") as temporary:
        root = Path(temporary)
        print("building deterministic 100-event distillation fixture ...", flush=True)
        memory_path = _build_fixture(root)
        service = MemoryDistillationService(
            memory_database=memory_path,
            project_key=PROJECT_KEY,
            artifact_root=root / "workflow",
            index_database=root / "index.sqlite3",
            policy_path=root / "policy.json",
        )
        print("distilling ...", flush=True)
        started = time.perf_counter()
        result = service.distill(max_events=100)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if result.selected_count != 100 or result.failed_count != 0:
            raise RuntimeError(f"benchmark distillation did not complete cleanly: {result}")

    report = {
        "schema": REPORT_SCHEMA,
        "mode": "gate" if args.gate else "baseline",
        "generatedAtUtc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "environment": _environment(),
        "fixture": {
            "projectKey": PROJECT_KEY,
            "events": 100,
            "mix": [
                "verified-checkpoint-set (R1 projectFact)",
                "workflow-rejection (R2 knownIssue)",
                "policy-rejection-with-digest (R3 projectRule)",
                "verified-semantic-diff (R4 projectFact)",
                "verified-recovery (R1 projectFact)",
                "partial-recovery (R2 knownIssue)",
                "supersession-pair: live-write journal + change set (R5 decisionRecord)",
                "resident-live-write-no-output",
                "no-op-change-set-no-output",
            ],
            "note": "No absolute user paths are recorded.",
        },
        "measurements": {
            "distillElapsedMs": round(elapsed_ms, 3),
            "selectedCount": result.selected_count,
            "evaluatedCount": result.evaluated_count,
            "distilledCount": result.distilled_count,
            "producedRecordCount": result.produced_record_count,
            "reusedRecordCount": result.reused_record_count,
            "deferredCount": result.deferred_count,
            "failedCount": result.failed_count,
            "pendingAfter": result.pending_after,
        },
        "gates": {
            "100_l0_deterministic_distillation_lt_5s": {
                "limitMs": DISTILL_HARD_MS,
                "actualMs": round(elapsed_ms, 3),
                "pass": elapsed_ms < DISTILL_HARD_MS,
            }
        },
    }
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = TOOL_ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"report written: {out_path}")
    if args.gate and not report["gates"]["100_l0_deterministic_distillation_lt_5s"]["pass"]:
        print("GATE FAILED: 100 L0 deterministic distillation exceeded 5 seconds", file=sys.stderr)
        return 1
    if args.gate:
        print("GATE PASS: 100 L0 deterministic distillation < 5 seconds")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
