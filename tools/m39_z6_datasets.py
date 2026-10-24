#!/usr/bin/env python3
"""M3.9 Z6 dataset separation: positive-only vs negative-heavy telemetry.

Consumes the combined-branch receipts produced by M3.8/M3.9, writes two explicit
z2-shaped evidence roots, and runs the read-only evaluator separately.  The
positive-only stream must have zero breaches; the negative-heavy stream is
expected to breach security/failure thresholds and must never be used as a
production rate baseline.  Emits aggregates only, never tokens or paths.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT / "src"),):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from ue_agent_kit.pre_m4.telemetry_eval import evaluate_evidence  # noqa: E402

OUT = ROOT / "Build" / "m39-readiness" / "z6"
HOST = ROOT / "Build" / "m37-integration" / "P11AutomationHost"
M38 = ROOT / "Build" / "m38-integration" / "Evidence" / "M3"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    three = _load(ROOT / "Build" / "m39-readiness" / "three-graphs.json")

    positive_results = []
    for graph in three.get("graphs", []):
        for run in graph.get("runs", []):
            graph_id = f"m39-{graph['graph']}-r{run['attempt']}"
            evidence_path = HOST / "Evidence" / f"m32-menu-{graph_id}.json"
            receipt = (_load(evidence_path).get("receipt") or {}) if evidence_path.is_file() else {}
            positive_results.append(
                {"graph": graph["graph"], "scenario": "native-happy", "receipt": receipt}
            )
    positive_root = OUT / "positive"
    positive_root.mkdir(parents=True, exist_ok=True)
    (positive_root / "z2-expanded-matrix.json").write_text(
        json.dumps({"results": positive_results}, sort_keys=True) + "\n", encoding="utf-8"
    )

    negative_root = OUT / "negative"
    negative_root.mkdir(parents=True, exist_ok=True)
    combined = _load(M38 / "combined-m3.json")
    negative_results = []
    for item in combined.get("results", []):
        case = str(item.get("case", ""))
        negative_results.append(
            {
                "graph": case,
                "scenario": case,
                "receipt": item.get("receipt") or {},
            }
        )
    (negative_root / "z2-expanded-matrix.json").write_text(
        json.dumps({"results": negative_results}, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    positive = evaluate_evidence(positive_root).as_dict()
    negative = evaluate_evidence(negative_root).as_dict()

    positive_ok = (
        positive["attempts"] == len(positive_results)
        and positive["nativeSuccess"] == len(positive_results)
        and positive["fallback"] == 0
        and positive["nativeFailure"] == 0
        and positive["securityFailures"] == 0
        and positive["unexpectedWrites"] == 0
        and positive["tokenFields"] == 0
        and positive["thresholdsBreached"] == []
    )
    negative_ok = (
        negative["attempts"] >= 8
        and negative["securityFailures"] > 0
        and negative["unexpectedWrites"] == 0
        and negative["tokenFields"] == 0
        and "any-security-failure" in negative["thresholdsBreached"]
        and negative["fallback"] > 0
        and negative["nativeFailure"] > 0
    )

    summary = {
        "schemaVersion": "ueak.m39.z6-datasets/1.0",
        "readOnly": True,
        "positiveOnly": {
            "source": "M3.9 three-graph native happy runs",
            "summary": positive,
            "ok": bool(positive_ok),
        },
        "negativeHeavy": {
            "source": "M3.8 combined matrix (expected negatives)",
            "summary": negative,
            "ok": bool(negative_ok),
            "note": "security/fallback/failure breaches are expected; not a production rate baseline",
        },
        "falsePositiveCriteria": {
            "positiveOnlyRequires": "0 breaches, 0 security, 0 writes, 0 token fields",
            "negativeHeavyRequires": "expected security/fallback/failure flags, still 0 writes/0 tokens",
            "productionUse": "only positive-only streams may be compared to the proposed thresholds",
        },
        "ok": bool(positive_ok and negative_ok),
    }
    (OUT / "z6-datasets.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "ok": summary["ok"],
                "positive": {
                    "attempts": positive["attempts"],
                    "breaches": positive["thresholdsBreached"],
                },
                "negative": {
                    "attempts": negative["attempts"],
                    "breaches": negative["thresholdsBreached"],
                    "writes": negative["unexpectedWrites"],
                    "tokens": negative["tokenFields"],
                },
            }
        )
    )
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
