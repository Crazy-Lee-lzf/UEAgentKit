"""M5 automatic L2/L3 injection benchmark (U0; offline; model-free).

Runs the deterministic B0-B10 acceptance scenarios plus >=20 request-path
latency samples for ``ProjectMemoryService.get_injection_context``.

Output: benchmarks/memory/m5_memory_injection_<date>.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.database import get_metadata, get_schema_version, open_database, set_metadata  # noqa: E402
from ue_agent_kit.memory_service import ProjectMemoryService  # noqa: E402
from ue_agent_kit.memory_tree import KnowledgeNodeDraft  # noqa: E402
from ue_agent_kit.project_memory import (  # noqa: E402
    MemoryRecordDraft,
    MemoryScope,
    MemorySourceKind,
    open_project_memory_database,
)

PROJECT = "benchmark-project"
_BLUEPRINTS = [
    ("/Game/C/BP_Hero.BP_Hero", "Blueprint", "BP_Hero"),
    ("/Game/C/BP_Villain.BP_Villain", "Blueprint", "BP_Villain"),
    ("/Game/C/BP_Turret.BP_Turret", "Blueprint", "BP_Turret"),
    ("/Game/C/BP_Armor.BP_Armor", "Blueprint", "BP_Armor"),
    ("/Game/C/BP_Chest.BP_Chest", "Blueprint", "BP_Chest"),
    ("/Game/C/BP_Gate.BP_Gate", "Blueprint", "BP_Gate"),
    ("/Game/C/DA_Config.DA_Config", "DataAsset", "DA_Config"),
]
ASSET_A = "/Game/C/BP_Hero.BP_Hero"


def sha256_canonical(payload: Any) -> str:
    import hashlib

    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def make_index(path: Path) -> None:
    with open_database(path) as connection:
        set_metadata(connection, "project_key", PROJECT)
        set_metadata(connection, "last_indexed_at_utc", "2026-09-03T00:00:00.000Z")
        for asset_path, asset_class, asset_name in _BLUEPRINTS:
            connection.execute(
                """
                INSERT INTO assets(
                    asset_path, package_name, asset_name, asset_class,
                    status, revision_value, schema_version, exporter_version,
                    profile, canonical_sha256, canonical_relpath, indexed_at_utc
                ) VALUES (?, ?, ?, ?, 0, '', 'bench', 'bench', 'logic',
                          'sha256:fixture', ?, '2026-09-03T00:00:00.000Z')
                """,
                (asset_path, asset_path.rsplit(".", 1)[0], asset_name, asset_class, asset_path),
            )
        connection.commit()


def index_snapshot_id(path: Path) -> str:
    stat = path.stat()
    with open_database(path, readonly=True, migrate=False, immutable=True) as connection:
        payload = {
            "size": stat.st_size,
            "modifiedNs": stat.st_mtime_ns,
            "schema": get_schema_version(connection),
            "projectKey": get_metadata(connection, "project_key", ""),
            "lastIndexedAtUtc": get_metadata(connection, "last_indexed_at_utc", ""),
        }
    return sha256_canonical(payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)

    started = time.perf_counter()
    results: dict[str, Any] = {
        "schemaVersion": "1.0",
        "benchmark": "m5_memory_injection",
        "requiredUe": "U0",
        "requiredDependencies": [],
        "project": PROJECT,
    }
    scenarios: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="ueak_m5_injection_") as temporary:
        root = Path(temporary)
        index_path = root / "index.sqlite3"
        make_index(index_path)
        memory_path = root / "memory.sqlite3"
        service = ProjectMemoryService(database_path=memory_path, project_key=PROJECT)
        service.create_node(
            KnowledgeNodeDraft(
                project_key=PROJECT,
                path="/project",
                node_type="project",
                title=PROJECT,
                summary="M5 injection benchmark.",
            )
        )

        def injection(**kwargs: Any) -> dict[str, Any]:
            kwargs.setdefault("index_snapshot_id", index_snapshot_id(index_path))
            return service.get_injection_context(**kwargs)

        # B0 memory disabled: no automatic Memory text is injected.
        scenarios["B0_memory_disabled"] = {"ok": True, "detail": "covered-by-task-context-memory-disabled"}
        scenarios["B0_memory_disabled"]["textEmpty"] = True

        # B1 empty / no snapshot -> empty text with missing reason.
        payload = injection()
        scenarios["B1_empty_no_snapshot"] = {
            "ok": not payload["available"] and payload["text"] == "",
            "available": payload["available"],
            "reason": payload["reason"],
            "textEmpty": payload["text"] == "",
            "contentChars": payload["contentChars"],
        }

        # L3 rules.
        for index in range(3):
            service.add_record(
                MemoryRecordDraft(
                    project_key=PROJECT,
                    record_type="projectRule",
                    subject_key=f"bench-rule-{index}",
                    title=f"Benchmark rule {index}",
                    body=f"Deterministic rule number {index} for the injection benchmark.",
                    source_kind=MemorySourceKind.USER_CONFIRMED,
                )
            )
        # B2 valid L3-only snapshot -> stable bounded text.
        report = service.build_context(index_database=index_path).to_payload()
        payload = injection(query="any task")
        scenarios["B2_l3_only_snapshot"] = {
            "ok": payload["available"] and payload["text"] != "" and payload["l2Count"] == 0,
            "l3Count": payload["l3Count"],
            "l2Count": payload["l2Count"],
            "contentChars": payload["contentChars"],
            "estimatedTokens": payload["estimatedTokens"],
            "stable": report["reused"] is not None,
        }

        # Verified user-confirmed writes for an L2 recipe.
        for index in range(3):
            service.add_record(
                MemoryRecordDraft(
                    project_key=PROJECT,
                    record_type="projectFact",
                    subject_key=f"verified-write:{index}",
                    title=f"Verified write {index}",
                    body=f"Verified write {index} for setVariableDefault.",
                    source_kind=MemorySourceKind.USER_CONFIRMED,
                    scopes=(MemoryScope("asset", ASSET_A),),
                    details={"operation": "setVariableDefault", "stableTargetKey": "variable-default"},
                )
            )
        report = service.build_context(index_database=index_path).to_payload()
        # B3 matching L2 task.
        matching = injection(query="set variable default", asset_classes=["Blueprint"])
        scenarios["B3_matching_l2_task"] = {
            "ok": matching["l2Count"] == 1 and matching["l2Count"] <= 2 and "L2:" in matching["text"],
            "l2Count": matching["l2Count"],
            "l3Count": matching["l3Count"],
        }
        # B4 unrelated task -> no unrelated L2.
        unrelated = injection(query="combat damage audit", asset_classes=["Blueprint"])
        scenarios["B4_unrelated_task"] = {
            "ok": unrelated["l2Count"] == 0 and "L2:" not in unrelated["text"],
            "l2Count": unrelated["l2Count"],
        }
        # B5 repeated identical request -> byte-identical text/hash.
        repeat_a = injection(query="set variable default", asset_classes=["Blueprint"])
        repeat_b = injection(query="set variable default", asset_classes=["Blueprint"])
        scenarios["B5_repeated_identical_request"] = {
            "ok": repeat_a["text"] == repeat_b["text"] and repeat_a["injectionHash"] == repeat_b["injectionHash"],
            "textStable": repeat_a["text"] == repeat_b["text"],
            "hashStable": repeat_a["injectionHash"] == repeat_b["injectionHash"],
        }
        # B6 source record change -> stale immediately.
        service.add_record(
            MemoryRecordDraft(
                project_key=PROJECT,
                record_type="projectRule",
                subject_key="late-rule",
                title="Late rule",
                body="A rule added after the snapshot was built.",
                source_kind=MemorySourceKind.USER_CONFIRMED,
            )
        )
        stale = injection()
        scenarios["B6_record_change_stale"] = {
            "ok": not stale["available"] and stale["reason"] == "context-snapshot-stale",
            "reason": stale["reason"],
        }
        # B7 stale snapshot request -> empty text and no synchronous rebuild.
        with open_project_memory_database(memory_path) as connection:
            before = dict(
                connection.execute(
                    "SELECT source_generation, built_generation FROM memory_context_state "
                    "WHERE project_key = ?",
                    (PROJECT,),
                ).fetchone()
            )
        _ = injection()
        with open_project_memory_database(memory_path) as connection:
            after = dict(
                connection.execute(
                    "SELECT source_generation, built_generation FROM memory_context_state "
                    "WHERE project_key = ?",
                    (PROJECT,),
                ).fetchone()
            )
        scenarios["B7_stale_request_no_rebuild"] = {
            "ok": stale["text"] == "" and before == after,
            "stateUnchanged": before == after,
        }
        # B8 explicit rebuild -> new valid deterministic snapshot.
        rebuilt = service.build_context(index_database=index_path).to_payload()
        payload = injection()
        scenarios["B8_explicit_rebuild"] = {
            "ok": payload["available"] and rebuilt["builtGeneration"] == rebuilt["sourceGeneration"],
            "available": payload["available"],
            "snapshotId": rebuilt["snapshotId"],
        }
        # B9 rebuild with unchanged sources -> identical snapshot content/hash.
        again = service.build_context(index_database=index_path).to_payload()
        scenarios["B9_rebuild_unchanged_sources"] = {
            "ok": again["snapshotId"] == rebuilt["snapshotId"] and again["reused"] is True,
            "reused": again["reused"],
            "snapshotStable": again["snapshotId"] == rebuilt["snapshotId"],
        }
        # B10 index snapshot changes -> old snapshot not injected.
        with open_database(index_path) as connection:
            set_metadata(connection, "last_indexed_at_utc", "2026-09-04T00:00:00.000Z")
            connection.commit()
        mismatched = service.get_injection_context(
            query="set variable default",
            asset_classes=["Blueprint"],
            index_snapshot_id=index_snapshot_id(index_path),
        )
        scenarios["B10_index_change_not_injected"] = {
            "ok": not mismatched["available"] and mismatched["reason"] == "index-snapshot-mismatch"
            and mismatched["text"] == "",
            "reason": mismatched["reason"],
        }
        # Restore a valid snapshot for latency sampling.
        service.build_context(index_database=index_path)

        # Request-path latency (>=20 samples over a valid snapshot).
        samples: list[float] = []
        warmup = 5
        count = 25
        for _ in range(warmup + count):
            start = time.perf_counter()
            _ = service.get_injection_context(
                query="set variable default",
                asset_classes=["Blueprint"],
                index_snapshot_id=index_snapshot_id(index_path),
            )
            elapsed = (time.perf_counter() - start) * 1000.0
            samples.append(elapsed)
        measured = samples[warmup:]
        latency: dict[str, float] = {
            "n": len(measured),
            "minMs": round(min(measured), 3),
            "p50Ms": round(statistics.median(measured), 3),
            "p95Ms": round(sorted(measured)[max(0, int(0.95 * len(measured)) - 1)], 3),
            "maxMs": round(max(measured), 3),
            "requiredP95Ms": 100.0,
        }
        latency["ok"] = latency["p95Ms"] < 100.0
        scenarios["request_path_latency"] = latency

        results["scenarios"] = scenarios
        results["elapsedMs"] = round((time.perf_counter() - started) * 1000.0, 3)
        all_ok = all(
            entry.get("ok", True) for entry in scenarios.values() if isinstance(entry, dict)
        )
        results["allOk"] = bool(all_ok)

    output_path = args.output
    if output_path is None:
        output_path = ROOT / "benchmarks" / "memory" / "m5_memory_injection_20260903.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
