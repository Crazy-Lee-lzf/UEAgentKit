"""Offline tests for the independent-process reload evidence harness.

Nothing here opens Unreal.  The harness is exercised with the real export/apply/
save document shapes (``layout_reload_fixtures``) plus synthetic ``.uasset``
bytes, so every refusal below is a deterministic, reproducible check.

The point of the suite is the *negative* space: a same-process readback, a stale
file, a tampered position, a reused PID, or a probe that saved must never be
reported as independent-reload evidence.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
TOOLS = ROOT / "tools"
for entry in (str(SRC), str(ROOT / "tests" / "python")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import layout_reload_fixtures as fixtures  # noqa: E402
from ue_agent_kit.graph_contracts.layout import recompute_layout_hash  # noqa: E402

HARNESS = fixtures.HARNESS_PATH


def _verify(**overrides):
    arguments = {
        "apply_result": fixtures.apply_result(),
        "save_result": fixtures.save_result(),
        "probe": fixtures.probe_document(),
        "asset_bytes": fixtures.asset_bytes(),
        "editor_process": fixtures.editor_process(),
        "asset_filename": fixtures.ASSET_FILENAME,
    }
    arguments.update(overrides)
    return fixtures.verify_independent_reload(**arguments)


class IndependentReloadEvidenceTests(unittest.TestCase):
    def test_fresh_process_probe_verifies_with_full_provenance(self) -> None:
        report = _verify()
        self.assertTrue(report["verified"], report["failClosedCodes"])
        self.assertTrue(report["independentReloadVerified"])
        self.assertTrue(report["acceptanceEligible"])
        self.assertEqual(report["evidenceLevel"], "independent-process")
        self.assertEqual(report["schemaVersion"], "ueak.layout.independent-reload-evidence/1.0")
        self.assertEqual(report["failClosedCodes"], [])
        self.assertTrue(all(check["ok"] for check in report["checks"]))

        provenance = report["provenance"]
        self.assertNotEqual(provenance["probePid"], provenance["editorPid"])
        self.assertEqual(provenance["editorPid"], fixtures.EDITOR_PID)
        self.assertEqual(provenance["probePid"], fixtures.PROBE_PID)
        self.assertIn("BlueprintContextExport", provenance["probeProducer"])
        self.assertEqual(provenance["attestation"], "self-reported")
        self.assertIn("UnrealEditor-Cmd.exe", provenance["probeCommand"])

        identity = report["identity"]
        self.assertEqual(identity["layoutHash"], fixtures.layout_hash(fixtures.TARGET_POSITIONS))
        self.assertEqual(identity["recomputedLayoutHash"], identity["layoutHash"])
        self.assertEqual(identity["graphGuid"], fixtures.GRAPH_GUID)
        self.assertEqual(identity["nodeCount"], len(fixtures.TARGET_POSITIONS))
        self.assertEqual(identity["packageRevision"], fixtures.asset_sha256())
        self.assertEqual(
            sorted(identity["nodeIds"]), sorted(fixtures.TARGET_POSITIONS)
        )

        self.assertEqual(report["asset"]["sha256"], fixtures.asset_sha256())
        self.assertEqual(report["asset"]["savedAssetSha256"], fixtures.asset_sha256())
        self.assertEqual(report["asset"]["sizeBytes"], len(fixtures.asset_bytes()))
        # BlueprintContextExport carries no graph fingerprint; the report says so
        # instead of silently skipping that cross-check.
        self.assertEqual(report["evidenceGaps"], ["graphFingerprint"])
        fingerprint_check = [
            check for check in report["checks"] if check["name"] == "probe.graph-fingerprint"
        ][0]
        self.assertTrue(fingerprint_check["ok"])
        self.assertIn("not available", fingerprint_check["detail"])

    def test_independent_hash_matches_the_product_layout_hash(self) -> None:
        positions = [
            {"nodeId": node_id, "x": x, "y": y}
            for node_id, (x, y) in sorted(fixtures.TARGET_POSITIONS.items(), reverse=True)
        ]
        self.assertEqual(
            fixtures.layout_hash_from_positions(positions),
            recompute_layout_hash({"positions": positions}),
        )

    def test_current_product_receipt_alone_is_not_evidence(self) -> None:
        # This is exactly what the shipped experimental Save returns today.
        report = _verify(probe=None)
        self.assertFalse(report["verified"])
        self.assertFalse(report["independentReloadVerified"])
        self.assertFalse(report["acceptanceEligible"])
        self.assertEqual(report["evidenceLevel"], "unverified")
        self.assertIn("independent-reload-probe-missing", report["failClosedCodes"])

    def test_same_process_probe_is_rejected(self) -> None:
        probe = fixtures.probe_document(pid=fixtures.EDITOR_PID)
        report = _verify(probe=probe)
        self.assertFalse(report["verified"])
        self.assertFalse(report["independentReloadVerified"])
        self.assertEqual(report["evidenceLevel"], "same-process")
        self.assertIn("independent-reload-same-process", report["failClosedCodes"])

    def test_reused_pid_with_a_new_start_time_is_rejected(self) -> None:
        probe = fixtures.probe_document(
            pid=fixtures.EDITOR_PID,
            process_start_utc="2026-10-20T02:11:00.000Z",
        )
        report = _verify(probe=probe)
        self.assertFalse(report["verified"])
        self.assertIn("independent-reload-same-process", report["failClosedCodes"])

    def test_probe_started_before_the_editor_terminated_is_rejected(self) -> None:
        probe = fixtures.probe_document(
            started_at_utc="2026-10-20T02:05:00.000Z",
            process_start_utc="2026-10-20T02:05:00.000Z",
        )
        report = _verify(probe=probe)
        self.assertFalse(report["verified"])
        self.assertIn("independent-reload-timeline-invalid", report["failClosedCodes"])

    def test_probe_from_the_future_is_rejected(self) -> None:
        report = _verify(
            probe=fixtures.probe_document(
                started_at_utc="2099-01-01T00:00:00.000Z",
                process_start_utc="2099-01-01T00:00:00.000Z",
            )
        )
        self.assertFalse(report["verified"])
        self.assertIn("independent-reload-timeline-invalid", report["failClosedCodes"])

    def test_missing_editor_process_provenance_is_rejected(self) -> None:
        report = _verify(editor_process=None)
        self.assertFalse(report["verified"])
        self.assertIn("independent-reload-provenance-missing", report["failClosedCodes"])

    def test_probe_that_reports_a_write_is_rejected(self) -> None:
        probe = fixtures.probe_document()
        probe["readOnly"] = False
        probe["saved"] = True
        report = _verify(probe=probe)
        self.assertFalse(report["verified"])
        self.assertIn("independent-reload-probe-wrote", report["failClosedCodes"])

    def test_probe_with_unknown_schema_is_rejected(self) -> None:
        probe = fixtures.probe_document()
        probe["schemaVersion"] = "ueak.layout.independent-reload-probe/2.0"
        report = _verify(probe=probe)
        self.assertFalse(report["verified"])
        self.assertIn("independent-reload-probe-invalid", report["failClosedCodes"])

    def test_probe_missing_a_required_field_is_rejected(self) -> None:
        probe = fixtures.probe_document()
        probe.pop("processStartUtc")
        report = _verify(probe=probe)
        self.assertFalse(report["verified"])
        self.assertIn("independent-reload-probe-invalid", report["failClosedCodes"])

    def test_tampered_position_is_rejected_with_the_exact_node(self) -> None:
        positions = dict(fixtures.TARGET_POSITIONS)
        bad_node = sorted(positions)[1]
        x, y = positions[bad_node]
        positions[bad_node] = (x + 1, y)
        report = _verify(probe=fixtures.probe_document(positions=positions))
        self.assertFalse(report["verified"])
        self.assertIn("independent-reload-position-mismatch", report["failClosedCodes"])
        self.assertEqual(report["positions"]["mismatchCount"], 1)
        self.assertEqual(report["positions"]["mismatches"][0]["nodeId"], bad_node)

    def test_missing_or_extra_node_is_rejected(self) -> None:
        fewer = dict(fixtures.TARGET_POSITIONS)
        dropped = sorted(fewer)[0]
        fewer.pop(dropped)
        report = _verify(probe=fixtures.probe_document(positions=fewer))
        self.assertFalse(report["verified"])
        self.assertIn("independent-reload-node-set-mismatch", report["failClosedCodes"])
        self.assertEqual(report["positions"]["missing"], [dropped])

    def test_layout_hash_mismatch_between_recompute_and_save_is_rejected(self) -> None:
        save = fixtures.save_result()
        save["result"]["layoutHash"] = "sha256:" + "e" * 64
        save["result"]["confirmation"] = "SAVE EXPERIMENTAL LAYOUT " + save["result"]["layoutHash"]
        save["result"]["readback"]["layoutHash"] = save["result"]["layoutHash"]
        report = _verify(save_result=save)
        self.assertFalse(report["verified"])
        self.assertIn("independent-reload-hash-mismatch", report["failClosedCodes"])

    def test_probe_declaring_a_different_layout_hash_is_rejected(self) -> None:
        probe = fixtures.probe_document()
        probe["layoutHash"] = "sha256:" + "f" * 64
        report = _verify(probe=probe)
        self.assertFalse(report["verified"])
        self.assertIn("independent-reload-hash-mismatch", report["failClosedCodes"])

    def test_stale_disk_bytes_are_rejected(self) -> None:
        report = _verify(asset_bytes=b"a different, older package payload")
        self.assertFalse(report["verified"])
        self.assertIn("independent-reload-disk-mismatch", report["failClosedCodes"])
        self.assertIn("independent-reload-revision-mismatch", report["failClosedCodes"])

    def test_probe_that_hashed_other_bytes_is_rejected(self) -> None:
        probe = fixtures.probe_document(asset_sha="sha256:" + "a" * 64)
        report = _verify(probe=probe)
        self.assertFalse(report["verified"])
        self.assertIn("independent-reload-probe-disk-mismatch", report["failClosedCodes"])

    def test_probe_revision_mismatch_is_rejected(self) -> None:
        probe = fixtures.probe_document(revision="sha256:" + "b" * 64)
        report = _verify(probe=probe)
        self.assertFalse(report["verified"])
        self.assertIn("independent-reload-probe-revision-mismatch", report["failClosedCodes"])

    def test_probe_graph_fingerprint_mismatch_is_rejected(self) -> None:
        probe = fixtures.probe_document()
        probe["graphFingerprint"] = "sha1:" + "9" * 40
        report = _verify(probe=probe)
        self.assertFalse(report["verified"])
        self.assertIn("independent-reload-fingerprint-mismatch", report["failClosedCodes"])

    def test_dirty_reloaded_package_is_rejected(self) -> None:
        probe = fixtures.probe_document()
        probe["packageDirty"] = True
        report = _verify(probe=probe)
        self.assertFalse(report["verified"])
        self.assertIn("independent-reload-package-dirty", report["failClosedCodes"])

    def test_identity_mismatch_is_rejected(self) -> None:
        probe = fixtures.probe_document()
        probe["graphGuid"] = "10000000-0000-4000-8000-000000000002"
        report = _verify(probe=probe)
        self.assertFalse(report["verified"])
        self.assertIn("independent-reload-identity-mismatch", report["failClosedCodes"])

    def test_apply_identity_is_cross_checked_when_present(self) -> None:
        # The shipped Apply result carries no identity; when a caller supplies it
        # anyway it must agree with the Save receipt.
        apply = fixtures.apply_result()
        apply["result"]["graphGuid"] = "10000000-0000-4000-8000-000000000002"
        report = _verify(apply_result=apply)
        self.assertFalse(report["verified"])
        self.assertIn("independent-reload-identity-mismatch", report["failClosedCodes"])

    def test_apply_without_undo_redo_proof_is_rejected(self) -> None:
        apply = fixtures.apply_result()
        apply["result"]["redoVerified"] = False
        report = _verify(apply_result=apply)
        self.assertFalse(report["verified"])
        self.assertIn("independent-reload-apply-unverified", report["failClosedCodes"])

    def test_save_without_explicit_confirmation_is_rejected(self) -> None:
        save = fixtures.save_result()
        save["result"]["confirmation"] = ""
        report = _verify(save_result=save)
        self.assertFalse(report["verified"])
        self.assertIn("independent-reload-save-invalid", report["failClosedCodes"])

    def test_save_that_also_compiled_is_rejected(self) -> None:
        save = fixtures.save_result()
        save["result"]["compiled"] = True
        report = _verify(save_result=save)
        self.assertFalse(report["verified"])
        self.assertIn("independent-reload-save-not-explicit", report["failClosedCodes"])

    def test_bare_results_are_accepted_like_wrapped_ones(self) -> None:
        report = _verify(
            apply_result=fixtures.apply_result()["result"],
            save_result=fixtures.save_result()["result"],
        )
        self.assertTrue(report["verified"], report["failClosedCodes"])

    def test_report_never_upgrades_a_same_process_claim_to_acceptance(self) -> None:
        # Regression: the harness must not copy `independentReloadVerified` from
        # any input document, only compute it.
        save = fixtures.save_result()
        save["result"]["independentReloadVerified"] = True
        report = _verify(save_result=save, probe=None)
        self.assertFalse(report["verified"])
        self.assertFalse(report["independentReloadVerified"])


class IndependentReloadCliTests(unittest.TestCase):
    def _run(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(HARNESS), *arguments],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_cli_builds_a_probe_and_verifies_it_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            asset = fixtures.write_asset(directory)
            export_path = directory / "bpctx.json"
            export_path.write_text(json.dumps(fixtures.export_document()), encoding="utf-8")
            probe_path = directory / "probe.json"
            apply_path = directory / "apply.json"
            save_path = directory / "save.json"
            report_path = directory / "evidence.json"
            apply_path.write_text(json.dumps(fixtures.apply_result()), encoding="utf-8")
            save_path.write_text(json.dumps(fixtures.save_result()), encoding="utf-8")

            built = self._run(
                "build-probe",
                "--export", str(export_path),
                "--asset", str(asset),
                "--pid", str(fixtures.PROBE_PID),
                "--started-utc", fixtures.PROBE_STARTED_UTC,
                "--command", "UnrealEditor-Cmd.exe -run=BlueprintContextExport",
                "--out", str(probe_path),
            )
            self.assertEqual(built.returncode, 0, built.stderr)
            probe = json.loads(probe_path.read_text(encoding="utf-8"))
            self.assertEqual(probe["schemaVersion"], "ueak.layout.independent-reload-probe/1.0")
            self.assertEqual(len(probe["nodes"]), len(fixtures.TARGET_POSITIONS))

            verified = self._run(
                "verify",
                "--apply", str(apply_path),
                "--save", str(save_path),
                "--probe", str(probe_path),
                "--asset", str(asset),
                "--editor-pid", str(fixtures.EDITOR_PID),
                "--editor-started-utc", fixtures.EDITOR_STARTED_UTC,
                "--editor-terminated-utc", fixtures.EDITOR_TERMINATED_UTC,
                "--out", str(report_path),
                "--json",
            )
            self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertTrue(report["verified"])
            self.assertEqual(report["evidenceLevel"], "independent-process")

    def test_cli_exits_two_when_the_probe_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            asset = fixtures.write_asset(directory)
            apply_path = directory / "apply.json"
            save_path = directory / "save.json"
            apply_path.write_text(json.dumps(fixtures.apply_result()), encoding="utf-8")
            save_path.write_text(json.dumps(fixtures.save_result()), encoding="utf-8")
            refused = self._run(
                "verify",
                "--apply", str(apply_path),
                "--save", str(save_path),
                "--asset", str(asset),
                "--editor-pid", str(fixtures.EDITOR_PID),
                "--editor-started-utc", fixtures.EDITOR_STARTED_UTC,
                "--editor-terminated-utc", fixtures.EDITOR_TERMINATED_UTC,
            )
            self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
            self.assertIn("NOT VERIFIED", refused.stdout)

    def test_cli_build_probe_refuses_an_export_without_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            asset = fixtures.write_asset(directory)
            document = fixtures.export_document()
            for node in document["graphs"][0]["nodes"]:
                node.pop("layout")
            export_path = directory / "bpctx.json"
            export_path.write_text(json.dumps(document), encoding="utf-8")
            refused = self._run(
                "build-probe",
                "--export", str(export_path),
                "--asset", str(asset),
                "--pid", "7",
                "--started-utc", fixtures.PROBE_STARTED_UTC,
                "--command", "x",
            )
            self.assertEqual(refused.returncode, 1)
            self.assertIn("-IncludeLayout", refused.stderr)


class IndependentReloadWiringTests(unittest.TestCase):
    """The future single UE run must be wired to the tested harness, not re-implement it."""

    def test_runner_script_is_wired_to_the_tested_harness(self) -> None:
        script = (ROOT / "scripts" / "VerifyLayoutIndependentReload.ps1").read_text(encoding="utf-8")
        self.assertIn("layout_independent_reload_harness.py", script)
        self.assertIn("build-probe", script)
        self.assertIn("-IncludeLayout", script)
        # It must capture the real process identity instead of trusting the export.
        self.assertIn("Start-Process", script)
        self.assertIn("PassThru", script)
        self.assertIn("StartTime", script)
        self.assertIn("editor-terminated-utc", script)
        # It must never save, compile, or run automation tests.
        for forbidden in ("-run=ResavePackages", "BuildPlugin", "Automation RunTests", "SavePackage"):
            self.assertNotIn(forbidden, script)

    def test_runner_export_arguments_match_the_existing_read_only_exporter(self) -> None:
        """The probe reuses the shipped exporter's command line; drift must be caught."""

        runner = (ROOT / "scripts" / "RunExport.ps1").read_text(encoding="utf-8")
        script = (ROOT / "scripts" / "VerifyLayoutIndependentReload.ps1").read_text(encoding="utf-8")
        for token in (
            "-run=BlueprintContextExport",
            "-Profile=",
            "-Format=",
            "-Asset=",
            "-IncludeLayout",
            "-unattended",
            "-nop4",
            "-nosplash",
            "-NoSound",
            "-NullRHI",
        ):
            self.assertIn(token, runner, "RunExport.ps1 no longer uses " + token)
            self.assertIn(token, script, "VerifyLayoutIndependentReload.ps1 must mirror " + token)
        # A GUI Editor would need the saving process to stay alive, which defeats
        # the point of the probe.
        self.assertNotIn("UnrealEditor.exe\"", script)

    def test_spec_documents_the_probe_and_the_still_unverified_state(self) -> None:
        spec = (ROOT / "spec" / "graph-contracts" / "EXPERIMENTAL_MCP_APPLY.md").read_text(encoding="utf-8")
        self.assertIn("VerifyLayoutIndependentReload.ps1", spec)
        self.assertIn("ueak.layout.independent-reload-probe/1.0", spec)
        self.assertIn("ueak.layout.independent-reload-evidence/1.0", spec)
        self.assertIn("independentReloadVerified", spec)
        self.assertIn("leaves `independentReloadVerified` false on purpose", spec)
        self.assertIn("Exit code `0` means verified", spec)


if __name__ == "__main__":
    unittest.main()
