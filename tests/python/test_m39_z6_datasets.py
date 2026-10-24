from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for entry in (str(ROOT / "src"), str(ROOT / "tools")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import m39_z6_datasets as z6  # noqa: E402


def _receipt(*, ok: bool = True, fallback: bool = False, code: str = "", dirty: bool = False) -> dict:
    receipt = {
        "ok": ok,
        "previewPath": "legacy-fallback" if fallback else "native-broker",
        "fallback": fallback,
        "saved": False,
        "compiled": False,
        "packageDirtyAfter": dirty,
        "invokedAtUtc": "2026-10-25T00:00:00Z",
        "completedAtUtc": "2026-10-25T00:00:00.050Z",
    }
    if code:
        receipt["errorCode"] = code
    return receipt


class Z6DatasetTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Path, Path, Path, Path]:
        native = root / "native"
        native.mkdir()
        three = root / "three-graphs.json"
        three.write_text(
            json.dumps(
                {
                    "graphs": [
                        {
                            "graph": "g1",
                            "runs": [{"attempt": 1}, {"attempt": 2}],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        for attempt in (1, 2):
            (native / f"m32-menu-m39-g1-r{attempt}.json").write_text(
                json.dumps({"receipt": _receipt()}), encoding="utf-8"
            )

        results = [
            {"case": "native", "receipt": _receipt()},
            {"case": "fallback", "receipt": _receipt(fallback=True)},
            {"case": "timeout", "receipt": _receipt(fallback=True)},
            {"case": "token", "receipt": _receipt(ok=False, code="native-preview-token-rejected")},
            {
                "case": "dacl",
                "receipt": _receipt(ok=False, code="native-preview-broker-descriptor-invalid"),
            },
            {
                "case": "fingerprint",
                "receipt": _receipt(ok=False, code="native-preview-fingerprint-conflict"),
            },
            {
                "case": "revision",
                "receipt": _receipt(ok=False, code="native-preview-revision-conflict"),
            },
            {"case": "dirty", "receipt": _receipt(ok=False, code="native-preview-package-dirty", dirty=True)},
        ]
        combined = root / "combined-m3.json"
        combined.write_text(json.dumps({"results": results}), encoding="utf-8")
        return three, combined, native, root / "out"

    def test_synthetic_positive_and_negative_datasets(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            three, combined, native, out = self._fixture(Path(temp))
            summary = z6.build_datasets(three, combined, native, out)
            self.assertTrue(summary["ok"], summary)
            self.assertEqual(summary["positiveOnly"]["summary"]["attempts"], 2)
            self.assertEqual(summary["positiveOnly"]["summary"]["nativeSuccess"], 2)
            self.assertEqual(summary["positiveOnly"]["summary"]["thresholdsBreached"], [])
            self.assertEqual(summary["negativeHeavy"]["summary"]["attempts"], 8)
            self.assertEqual(summary["negativeHeavy"]["summary"]["unexpectedWrites"], 0)
            self.assertEqual(summary["negativeHeavy"]["summary"]["tokenFields"], 0)
            self.assertIn(
                "any-security-failure",
                summary["negativeHeavy"]["summary"]["thresholdsBreached"],
            )
            self.assertTrue((out / "z6-datasets.json").is_file())

    def test_missing_evidence_fails_fast(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaises(z6.EvidenceNotPresent):
                z6.build_datasets(
                    root / "missing-three.json",
                    root / "missing-combined.json",
                    root / "missing-native",
                    root / "out",
                )

    def test_empty_datasets_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            three = root / "three-graphs.json"
            three.write_text(json.dumps({"graphs": []}), encoding="utf-8")
            combined = root / "combined-m3.json"
            combined.write_text(json.dumps({"results": []}), encoding="utf-8")
            native = root / "native"
            native.mkdir()
            with self.assertRaises(z6.EvidenceNotPresent):
                z6.build_datasets(three, combined, native, root / "out")

    def test_cli_reports_evidence_not_present(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = z6.main(
                    [
                        "--three-graphs",
                        str(root / "missing-three.json"),
                        "--combined-evidence",
                        str(root / "missing-combined.json"),
                        "--native-evidence-dir",
                        str(root / "missing-native"),
                        "--out",
                        str(root / "out"),
                    ]
                )
            self.assertEqual(code, 2)
            self.assertTrue(stderr.getvalue().startswith("EVIDENCE_NOT_PRESENT:"))


if __name__ == "__main__":
    unittest.main()
