"""Static T10 guards: trust-before-template and bounded template budget.

These checks intentionally stay offline.  They do not claim the UE runtime
passed; the real T10 evidence is the automation index/log produced by the
disposable R1 host.  UE builds with exceptions disabled on some targets, so a
literal C++ `throw` fixture is not a portable or honest test.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CPP = ROOT / "Plugin/UEAgentKit/Source/UEAgentKitEditor/Private/Native/NativeNodeRegistry.cpp"
HEADER = ROOT / "Plugin/UEAgentKit/Source/UEAgentKitEditor/Public/Native/NativeNodeRegistry.h"
TEST = ROOT / "Plugin/UEAgentKit/Source/UEAgentKitEditor/Private/Native/NativeNodeRegistryTests.cpp"


class T10BudgetSourceGuards(unittest.TestCase):
    def test_untrusted_is_rejected_before_any_template_api(self) -> None:
        source = CPP.read_text(encoding="utf-8")
        start = source.index("bool BuildDescriptorForSpawner(")
        # The trust gate and the first template access are both near the top of
        # this function; a short slice keeps the guard from depending on the
        # exact end of a very large descriptor-building function.
        body = source[start : start + 30000]
        trust = body.index("if (Trust != ENativeTrust::Official)")
        cached = body.index("GetCachedTemplateNode")
        create = body.index("GetTemplateNode")
        self.assertLess(trust, cached)
        self.assertLess(trust, create)
        self.assertIn('return Fail(OutResult, TEXT("untrusted-spawner-excluded"));', body)

    def test_session_budget_constants_are_the_frozen_r1_limits(self) -> None:
        source = CPP.read_text(encoding="utf-8")
        header = HEADER.read_text(encoding="utf-8")
        self.assertIn("constexpr int32 MaxCatalogItems = 512;", source)
        self.assertIn("constexpr int32 MaxSessionTemplateCreations = 768;", source)
        self.assertIn("int32 MaxTemplateSpawns = 512;", header)
        self.assertIn("MaxTemplateSpawns = FMath::Clamp(Effective.MaxTemplateSpawns, 0, 4096);", source)
        self.assertIn(
            "FMath::Max(0, MaxSessionTemplateCreations - SessionTemplateCreations)",
            source,
        )
        self.assertIn("SessionTemplateCreations += Context.TemplateNodesCreated;", source)

    def test_catalog_does_not_expand_to_the_38k_discovered_set(self) -> None:
        source = CPP.read_text(encoding="utf-8")
        self.assertNotIn("38325", source)
        self.assertNotIn("38,325", source)
        self.assertIn("Result.Descriptors.SetNum(Effective.MaxItems);", source)
        self.assertIn("Result.bTruncated = true;", source)

    def test_real_t10_negatives_exist_in_automation(self) -> None:
        test = TEST.read_text(encoding="utf-8")
        for token in (
            "untrusted-spawner-excluded",
            "template-budget-exceeded",
            "cached untrusted template pointer is unchanged",
            "cached untrusted spawner does not dirty packages",
            "budget-exhausted official spawner is rejected",
            "budget-exhausted spawner did not create a template",
            "budget-exhausted spawner does not dirty packages",
        ):
            self.assertIn(token, test)
        self.assertIn("UBlueprintFunctionNodeSpawner::Create", test)
        self.assertIn("GetCachedTemplateNode()", test)

    def test_no_untrusted_template_spawn_escape_hatch_is_implemented(self) -> None:
        source = CPP.read_text(encoding="utf-8")
        header = HEADER.read_text(encoding="utf-8")
        # N0 reserves this as a future, default-off local setting.  R1 currently
        # has no such switch; silence here means "not implemented", not "tested".
        self.assertNotIn("AllowUntrustedTemplateSpawn", source + header)
        self.assertNotIn("untrustedTemplateSpawned", source + header)
        self.assertIn("bAllowOfficialTemplateSpawn", header)


if __name__ == "__main__":
    unittest.main()
