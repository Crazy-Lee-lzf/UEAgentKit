"""Static guards for the R1.6 test-only cursor seam and its orchestration."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_CPP = ROOT / "Plugin/UEAgentKit/Source/UEAgentKitEditor/Private/Native/NativeNodeRegistry.cpp"
REGISTRY_H = ROOT / "Plugin/UEAgentKit/Source/UEAgentKitEditor/Public/Native/NativeNodeRegistry.h"
HARNESS = ROOT / "tests/native_tools/r15_socket_harness.py"
RUNNER = ROOT / "scripts/RunR16Boundary.ps1"


class R16SeamGuardTests(unittest.TestCase):
    def test_default_ttl_is_900_and_effective_ttl_is_used(self) -> None:
        header = REGISTRY_H.read_text(encoding="utf-8")
        source = REGISTRY_CPP.read_text(encoding="utf-8")
        self.assertIn("int32 CursorTtlSeconds = 900", header)
        self.assertIn("CursorTtlSeconds = UEAgentKitNativeCursor::CursorTtlSeconds", source)
        self.assertIn("NowUnix + CursorTtlSeconds", source)
        self.assertIn('SetNumberField(TEXT("ttlSeconds"), CursorTtlSeconds)', source)

    def test_seam_requires_unattended_and_explicit_flag(self) -> None:
        source = REGISTRY_CPP.read_text(encoding="utf-8")
        self.assertIn(
            'FApp::IsUnattended() && FParse::Param(FCommandLine::Get(), TEXT("UEAgentKitSocketTest"))',
            source,
        )
        self.assertIn('UEAK_TEST_CURSOR_TTL', source)
        self.assertIn('UEAK_TEST_CURSOR_KEY', source)
        self.assertNotIn("*KeyHex", source)
        self.assertIn('keyOverride=%s', source)

    def test_runner_does_not_commit_a_shared_cursor_key(self) -> None:
        runner = RUNNER.read_text(encoding="utf-8")
        self.assertRegex(runner, r"Get-Random -Maximum 256")
        self.assertIsNone(re.search(r"\$TestKey\s*=\s*\"[0-9a-fA-F]{64}\"", runner))
        self.assertIn("SetEnvironmentVariable($key, $saved[$key]", runner)

    def test_harness_has_bounded_modes_and_redaction(self) -> None:
        harness = HARNESS.read_text(encoding="utf-8")
        for mode in ("capture", "stale", "expiry", "shutdown"):
            self.assertIn(f'"{mode}"', harness)
        self.assertIn("pageSize\": 256", harness)
        self.assertIn('"<redacted-cursor>"', harness)
        self.assertIn("data.pop(\"descriptors\"", harness)


if __name__ == "__main__":
    unittest.main()
