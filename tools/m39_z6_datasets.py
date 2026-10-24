#!/usr/bin/env python3
"""M3.9 Z6 dataset separation: positive-only vs negative-heavy telemetry.

Consumes the combined-branch receipts produced by M3.8/M3.9 and writes two
explicit z2-shaped evidence roots.  Inputs are Git-ignored runtime evidence, so
this tool never assumes they exist: a clean checkout must pass explicit paths or
receive a clear ``EVIDENCE_NOT_PRESENT`` failure (exit 2), never a traceback.

The positive-only stream must have zero breaches; the negative-heavy stream is
expected to breach security/failure thresholds and must never be used as a
production rate baseline.  Only aggregates are emitted, never tokens or paths.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT / "src"),):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from ue_agent_kit.pre_m4.telemetry_eval import evaluate_evidence  # noqa: E402

DEFAULT_THREE = ROOT / "Build" / "m39-readiness" / "three-graphs.json"
DEFAULT_COMBINED = ROOT / "Build" / "m38-integration" / "Evidence" / "M3" / "combined-m3.json"
DEFAULT_NATIVE = ROOT / "Build" / "m37-integration" / "P11AutomationHost" / "Evidence"
DEFAULT_OUT = ROOT / "Build" / "m39-readiness" / "z6"


class EvidenceNotPresent(RuntimeError):
    """Raised when an explicitly required ignored-evidence input is absent."""


def _load(path: Path) -> dict:
    if not path.is_file():
        raise EvidenceNotPresent(path.name)
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.name


def build_datasets(
    three_graphs: Path,
    combined_evidence: Path,
    native_evidence_dir: Path,
    out_dir: Path,
) -> dict:
    """Build both datasets and return the summary; raises on missing evidence."""

    three = _load(three_graphs)
    combined = _load(combined_evidence)
    out_dir.mkdir(parents=True, exist_ok=True)

    positive_results = []
    for graph in three.get("graphs", []):
        for run in graph.get("runs", []):
            graph_id = f"m39-{graph['graph']}-r{run['attempt']}"
            evidence_path = native_evidence_dir / f"m32-menu-{graph_id}.json"
            if not evidence_path.is_file():
                raise EvidenceNotPresent(_relative(evidence_path))
            receipt = _load(evidence_path).get("receipt") or {}
            positive_results.append(
                {"graph": graph["graph"], "scenario": "native-happy", "receipt": receipt}
            )
    if not positive_results:
        raise EvidenceNotPresent("three-graphs.json has no run receipts")

    positive_root = out_dir / "positive"
    positive_root.mkdir(parents=True, exist_ok=True)
    (positive_root / "z2-expanded-matrix.json").write_text(
        json.dumps({"results": positive_results}, sort_keys=True) + "\n", encoding="utf-8"
    )

    negative_root = out_dir / "negative"
    negative_root.mkdir(parents=True, exist_ok=True)
    negative_results = []
    for item in combined.get("results", []):
        case = str(item.get("case", ""))
        negative_results.append(
            {"graph": case, "scenario": case, "receipt": item.get("receipt") or {}}
        )
    if not negative_results:
        raise EvidenceNotPresent("combined-m3.json has no results")
    (negative_root / "z2-expanded-matrix.json").write_text(
        json.dumps({"results": negative_results}, sort_keys=True) + "\n", encoding="utf-8"
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
        "sources": {
            "threeGraphs": _relative(three_graphs),
            "combinedEvidence": _relative(combined_evidence),
            "nativeEvidenceDir": _relative(native_evidence_dir),
        },
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
    (out_dir / "z6-datasets.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--three-graphs", type=Path, default=DEFAULT_THREE)
    parser.add_argument("--combined-evidence", type=Path, default=DEFAULT_COMBINED)
    parser.add_argument("--native-evidence-dir", type=Path, default=DEFAULT_NATIVE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    try:
        summary = build_datasets(
            args.three_graphs, args.combined_evidence, args.native_evidence_dir, args.out
        )
    except EvidenceNotPresent as exc:
        print(f"EVIDENCE_NOT_PRESENT: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "ok": summary["ok"],
                "positive": {
                    "attempts": summary["positiveOnly"]["summary"]["attempts"],
                    "breaches": summary["positiveOnly"]["summary"]["thresholdsBreached"],
                },
                "negative": {
                    "attempts": summary["negativeHeavy"]["summary"]["attempts"],
                    "breaches": summary["negativeHeavy"]["summary"]["thresholdsBreached"],
                    "writes": summary["negativeHeavy"]["summary"]["unexpectedWrites"],
                    "tokens": summary["negativeHeavy"]["summary"]["tokenFields"],
                },
            }
        )
    )
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
