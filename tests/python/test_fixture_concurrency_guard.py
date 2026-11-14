"""DEF-07 contract tests for the write-fixture concurrency guard.

Two layers:

* static contract — the wrapper must call the guard before it launches any
  Unreal commandlet or clears verification output, must never terminate a
  process, and must never read or rewrite another tool's descriptor;
* functional — the PowerShell guard is driven offline with injected process
  snapshots (no Unreal process is started or inspected).

The static assertions fail against the pre-fix wrapper because it contained no
guard at all; no worktree reset is involved.
"""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
WRAPPER = SCRIPTS / "RunWriteFixturePlan.ps1"
GUARD = SCRIPTS / "FixtureConcurrencyGuard.ps1"
PS_HARNESS = ROOT / "tests" / "powershell" / "FixtureConcurrencyGuard.Tests.ps1"

ENTRY_GATE = 'Assert-UeakFixtureProjectIdle -ProjectPath $ProjectPath -Stage "entry"'
WRITE_GATE = '-Stage "write"'
EXPORT_GATE = '-Stage "export"'
LAUNCH = "& $EditorCmd @Arguments"
CLEAR_OUTPUT = "Remove-Item -LiteralPath $VerificationOutput"


def _powershell() -> str | None:
    for name in ("pwsh", "powershell"):
        found = shutil.which(name)
        if found:
            return found
    return None


class FixtureGuardStaticContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.wrapper = WRAPPER.read_text(encoding="utf-8")
        self.guard = GUARD.read_text(encoding="utf-8")

    def test_guard_module_exists_and_is_dot_sourced(self) -> None:
        self.assertTrue(GUARD.is_file())
        self.assertIn('Join-Path $PSScriptRoot "FixtureConcurrencyGuard.ps1"', self.wrapper)

    def test_gate_precedes_launch_and_output_cleanup(self) -> None:
        entry = self.wrapper.index(ENTRY_GATE)
        write = self.wrapper.index(WRITE_GATE)
        launch = self.wrapper.index(LAUNCH)
        export = self.wrapper.index(EXPORT_GATE)
        cleanup = self.wrapper.index(CLEAR_OUTPUT)
        # Entry gate runs before any filesystem work; the write gate is the last
        # check before the commandlet process starts; the export gate runs
        # before the previous verification output is recursively deleted.
        self.assertLess(entry, self.wrapper.index("New-Item -ItemType Directory"))
        self.assertLess(entry, write)
        self.assertLess(write, launch)
        self.assertLess(export, cleanup)

    def test_guard_is_checked_again_immediately_before_each_launch(self) -> None:
        write = self.wrapper.index(WRITE_GATE)
        launch = self.wrapper.index(LAUNCH)
        between = self.wrapper[write:launch]
        # Only the audit-report plumbing and the launch itself may sit between
        # the last gate and the process start.
        self.assertNotIn("Remove-Item", between)
        self.assertNotIn("New-Item", between)
        self.assertIn("concurrency-guard-write.json", self.wrapper)
        self.assertIn("concurrency-guard-export.json", self.wrapper)

    def test_no_process_termination_api(self) -> None:
        for source in (self.wrapper, self.guard):
            for forbidden in (
                "Stop-Process",
                "taskkill",
                "CloseMainWindow",
                "TerminateProcess",
                ".Kill(",
                "Restart-Computer",
                "shutdown.exe",
            ):
                if forbidden in source:
                    self.fail(
                        f"{forbidden!r} must not appear in the fixture guard path"
                    )

    def test_never_reads_or_clears_another_descriptor(self) -> None:
        for source in (self.wrapper, self.guard):
            self.assertNotIn("EditorBridge.json", source)
        self.assertNotIn("Descriptor", self.wrapper)

    def test_fail_closed_codes_and_read_only_detection(self) -> None:
        self.assertIn("fixture-project-in-use", self.guard)
        self.assertIn("fixture-project-lock-unverifiable", self.guard)
        self.assertIn("fixture-project-lock-clear", self.guard)
        self.assertIn("Get-CimInstance", self.guard)
        self.assertIn("Win32_Process", self.guard)
        self.assertIn("CommandLine", self.guard)
        # Matching must go through tokenised project arguments, never a raw
        # substring test that would confuse MyProject with MyProject2.
        self.assertIn("Get-UeakProjectArgumentsFromCommandLine", self.guard)
        self.assertIn("IsPathRooted", self.guard)
        self.assertIn("OrdinalIgnoreCase", self.guard)
        self.assertIn("ReparsePoint", self.guard)
        # A UNC path and a drive-letter path cannot be proven different without
        # resolving per-user drive mappings, so the pair must stay unverifiable.
        self.assertIn("CandidateIsUnc -ne $TargetIsUnc", self.guard)

    def test_no_runtime_bypass_switch(self) -> None:
        # The injection seam is a function parameter used only by the offline
        # harness; the production wrapper must never supply a snapshot and must
        # expose no skip/force switch.
        self.assertIn("ProcessSnapshot", self.guard)
        self.assertNotIn("ProcessSnapshot", self.wrapper.replace("FixtureConcurrencyGuard.ps1", ""))
        for source in (self.wrapper, self.guard):
            self.assertNotIn("SkipProcessGuard", source)
            self.assertNotIn("-Skip", source)
            self.assertNotIn("ForceGuard", source)

    def test_default_mode_is_non_destructive(self) -> None:
        self.assertIn('[string]$Mode = "Create"', self.wrapper)
        self.assertIn('[ValidateSet("Create", "Reset")]', self.wrapper)
        # Reset stays available, but only as an explicit request.
        self.assertNotIn('[string]$Mode = "Reset"', self.wrapper)


class FixtureGuardFunctionalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.powershell = _powershell()
        if self.powershell is None:
            self.skipTest("PowerShell is not available in this environment")

    def _run(self, script: Path, extra: list[str] | None = None) -> subprocess.CompletedProcess:
        command = [
            self.powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ]
        if extra:
            command.extend(extra)
        return subprocess.run(  # noqa: S603 - fixed arguments, local script
            command,
            capture_output=True,
            text=True,
            timeout=180,
        )

    def test_offline_guard_harness_passes(self) -> None:
        self.assertTrue(PS_HARNESS.is_file())
        result = self._run(PS_HARNESS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("0 failed", result.stdout)
        # The harness must actually exercise the blocking decisions, not only
        # the allow path.
        self.assertGreaterEqual(result.stdout.count("blocks"), 3)

    def test_wrapper_parses(self) -> None:
        query = (
            "$tokens=$null;$errors=$null;"
            f"[void][System.Management.Automation.Language.Parser]::ParseFile('{WRAPPER}',"
            "[ref]$tokens,[ref]$errors);"
            "if ($errors.Count -gt 0) { $errors | ForEach-Object { Write-Error $_.Message }; exit 1 }"
        )
        result = subprocess.run(  # noqa: S603 - fixed arguments
            [self.powershell, "-NoProfile", "-Command", query],
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_guard_module_parses(self) -> None:
        query = (
            "$tokens=$null;$errors=$null;"
            f"[void][System.Management.Automation.Language.Parser]::ParseFile('{GUARD}',"
            "[ref]$tokens,[ref]$errors);"
            "if ($errors.Count -gt 0) { $errors | ForEach-Object { Write-Error $_.Message }; exit 1 }"
        )
        result = subprocess.run(  # noqa: S603 - fixed arguments
            [self.powershell, "-NoProfile", "-Command", query],
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
