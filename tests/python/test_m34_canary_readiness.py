from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ue_agent_kit.layout_broker import protocol
from ue_agent_kit.layout_broker.descriptor import build_descriptor, write_descriptor_atomic
from ue_agent_kit.layout_broker.security import restrict_file_permissions
from ue_agent_kit.pre_m4.canary_preflight import (
    CANARY_ALLOWLIST_SCHEMA,
    CANARY_MARKER_SCHEMA,
    run_canary_preflight,
)
from ue_agent_kit.pre_m4.telemetry_eval import evaluate_evidence

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "Build" / "m2-native" / "M34"


def _project(root: Path, name: str = "Canary") -> tuple[Path, Path, str]:
    project = root / name
    (project / "Saved" / "UEAgentKit").mkdir(parents=True)
    (project / f"{name}.uproject").write_text("{}", encoding="utf-8")
    marker = {
        "schemaVersion": CANARY_MARKER_SCHEMA,
        "enabled": True,
        "projectName": name,
    }
    (project / "Saved" / "UEAgentKit" / "CanaryOptIn.json").write_text(
        json.dumps(marker), encoding="utf-8"
    )
    project_hash = protocol.build_project_path_hash(str(project.resolve()))
    allowlist = root / "allowlist.json"
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


class CanaryPreflightTests(unittest.TestCase):
    def test_eligible_with_protected_descriptor(self) -> None:
        BUILD.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=BUILD) as temp:
            project, allowlist, project_hash = _project(Path(temp))
            path = project / "Saved" / "UEAgentKit" / "LayoutBroker.json"
            payload = build_descriptor(
                port=50123,
                token="a" * 64,
                project_name="Canary",
                project_path_hash=project_hash,
            )
            write_descriptor_atomic(path, payload)
            result = run_canary_preflight(project, allowlist)
            self.assertTrue(result.eligible, result.as_dict())
            self.assertEqual(result.decision, "ELIGIBLE")

    def test_missing_marker_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(dir=BUILD) as temp:
            project, allowlist, project_hash = _project(Path(temp))
            (project / "Saved" / "UEAgentKit" / "CanaryOptIn.json").unlink()
            path = project / "Saved" / "UEAgentKit" / "LayoutBroker.json"
            write_descriptor_atomic(
                path,
                build_descriptor(
                    port=50124,
                    token="b" * 64,
                    project_name="Canary",
                    project_path_hash=project_hash,
                ),
            )
            self.assertFalse(run_canary_preflight(project, allowlist).eligible)

    def test_missing_allowlist_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(dir=BUILD) as temp:
            project, allowlist, project_hash = _project(Path(temp))
            allowlist.unlink()
            path = project / "Saved" / "UEAgentKit" / "LayoutBroker.json"
            write_descriptor_atomic(
                path,
                build_descriptor(
                    port=50125,
                    token="c" * 64,
                    project_name="Canary",
                    project_path_hash=project_hash,
                ),
            )
            self.assertFalse(run_canary_preflight(project, allowlist).eligible)

    def test_inherited_acl_descriptor_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(dir=BUILD) as temp:
            project, allowlist, project_hash = _project(Path(temp))
            path = project / "Saved" / "UEAgentKit" / "LayoutBroker.json"
            path.write_text(
                json.dumps(
                    build_descriptor(
                        port=50126,
                        token="d" * 64,
                        project_name="Canary",
                        project_path_hash=project_hash,
                    )
                ),
                encoding="utf-8",
            )
            self.assertFalse(run_canary_preflight(project, allowlist).eligible)

    def test_stale_descriptor_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(dir=BUILD) as temp:
            project, allowlist, project_hash = _project(Path(temp))
            path = project / "Saved" / "UEAgentKit" / "LayoutBroker.json"
            old = datetime.now(timezone.utc) - timedelta(hours=13)
            path.write_text(
                json.dumps(
                    build_descriptor(
                        port=50127,
                        token="e" * 64,
                        project_name="Canary",
                        project_path_hash=project_hash,
                        started_at=old,
                    )
                ),
                encoding="utf-8",
            )
            restrict_file_permissions(path)
            self.assertFalse(run_canary_preflight(project, allowlist).eligible)


class TelemetryTests(unittest.TestCase):
    def _receipt(
        self,
        *,
        ok: bool = True,
        fallback: bool = False,
        code: str = "",
        saved: bool = False,
        token: bool = False,
    ) -> dict:
        receipt = {
            "ok": ok,
            "previewPath": "legacy-fallback" if fallback else "native-broker",
            "fallback": fallback,
            "saved": saved,
            "compiled": False,
            "packageDirtyAfter": False,
            "invokedAtUtc": "2026-10-24T00:00:00Z",
            "completedAtUtc": "2026-10-24T00:00:00.100Z",
        }
        if code:
            receipt["errorCode"] = code
        if token:
            receipt["token"] = "must-not-appear"
        return receipt

    def test_synthetic_rates_and_redaction(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            results = []
            for _ in range(89):
                results.append({"graph": "event", "scenario": "happy", "receipt": self._receipt()})
            for _ in range(6):
                results.append({"graph": "event", "scenario": "fallback", "receipt": self._receipt(fallback=True)})
            for _ in range(4):
                results.append(
                    {
                        "graph": "event",
                        "scenario": "token",
                        "receipt": self._receipt(ok=False, code="native-preview-token-rejected"),
                    }
                )
            results.append(
                {
                    "graph": "event",
                    "scenario": "write",
                    "receipt": self._receipt(saved=True, token=True),
                }
            )
            (root / "z2-expanded-matrix.json").write_text(
                json.dumps({"results": results}), encoding="utf-8"
            )
            summary = evaluate_evidence(root)
            payload = json.dumps(summary.as_dict())
            self.assertEqual(summary.attempts, 100)
            self.assertEqual(summary.fallback, 6)
            self.assertEqual(summary.native_success, 90)
            self.assertEqual(summary.security_failures, 4)
            self.assertEqual(summary.unexpected_writes, 1)
            self.assertEqual(summary.token_fields, 1)
            self.assertNotIn("must-not-appear", payload)
            self.assertNotIn("/Game/", payload)
            self.assertIn("fallback-rate>5%", summary.thresholds_breached)
            self.assertIn("any-unexpected-write", summary.thresholds_breached)

    def test_token_named_keys_with_prefixes_are_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            receipt = self._receipt()
            receipt["authToken"] = "must-not-appear"
            receipt["brokerToken"] = "must-not-appear"
            (root / "z2-expanded-matrix.json").write_text(
                json.dumps({"results": [{"graph": "event", "scenario": "happy", "receipt": receipt}]}),
                encoding="utf-8",
            )
            summary = evaluate_evidence(root)
            payload = json.dumps(summary.as_dict())
            self.assertEqual(summary.token_fields, 2)
            self.assertIn("token-field-observed", summary.thresholds_breached)
            self.assertNotIn("must-not-appear", payload)

    def test_real_m33_evidence_read_only(self) -> None:
        evidence_root = ROOT / "Build" / "m2-native" / "Evidence" / "M33"
        evidence_files = (
            "z1-multigraph-parity.json",
            "z2-expanded-matrix.json",
            "z3-rollback.json",
        )
        if not any((evidence_root / name).is_file() for name in evidence_files):
            self.skipTest("M3.3 UE runtime evidence is optional and excluded from Git")
        summary = evaluate_evidence(evidence_root)
        self.assertGreater(summary.attempts, 0)
        self.assertEqual(summary.unexpected_writes, 0)
        self.assertEqual(summary.token_fields, 0)


if __name__ == "__main__":
    unittest.main()
