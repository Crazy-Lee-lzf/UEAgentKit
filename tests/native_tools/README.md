# tests/native_tools — clean-checkout test layers

This directory has two distinct layers. A **skip is not a pass**: the evidence-gated tests
validate artifacts produced by a real UE 5.6.1 run and are skipped when those Git-ignored
artifacts are absent.

## Layer 1 — pure Python unit checks (always runnable)

```powershell
py -3.12 -B -m unittest discover -s tests/native_tools -p "test_*.py"
```

Clean checkout result: **44 tests, 5 skipped, 0 failures** (about 6 s). These cover the cursor
codec/pagination, golden descriptor fixtures, source write/spawn guards, and the R1.5 harness
helpers. No UE, broker, or Build evidence is required.

## Layer 2 — evidence-gated integration checks (13 tests)

They are skipped by `setUpClass`/`skipTest` when the artifacts below are missing. A class-level
skip is reported as a **single** skipped entry and hides all tests in that class, which is why the
clean count is 44 rather than 57:

| Skip entry | Hidden/affected tests | Required artifact(s) | Producer |
|---|---:|---|---|
| `ContextExportTests` (class skip) | 7 | `Build/r1-native/Evidence/context-export.json` (one identity check also compares `catalog-export.json`) | `scripts/RunR1NativeTests.ps1` / `UEAgentKit.Native.R1` (`R1ContextFilter` export copied from the host) |
| `TwoProcessParityTests` (class skip) | 6 | `Build/r1-native/Evidence/t4/run-a/catalog-export.json` and `.../run-b/catalog-export.json` | `scripts/RunR1TwoProcessParity.ps1` |
| `RealEvidencePagingTests.test_512_exported_ids_page_without_gap` | 1 | `Build/r1-native/Evidence/catalog-export.json` with 512 descriptors | `scripts/RunR1NativeTests.ps1` (`R1Catalog` export) |
| `CatalogExportTests.test_export_contains_at_least_two_hundred_descriptors` | 1 | same catalog export | same |
| `CatalogExportTests.test_export_if_present` | 1 | same catalog export | same |

With all artifacts present and `jsonschema` installed, the same command runs **57 tests, 0 skipped**
(if `jsonschema` is absent, that one optional schema-validation check skips instead).

The extra 13 are exactly the 7 `ContextExportTests` + 6 `TwoProcessParityTests` hidden by the two
class skips. The three individual skips above are already counted in the clean 44.

## Rules

- `Build/**` is Git-ignored; never commit generated evidence.
- Do not synthesize catalog/context files to turn skips into passes. A synthetic unit test may
  exercise parsing logic, but it must not claim a real UE run.
- CI without UE should run Layer 1 and report the evidence layer as `SKIP (evidence not supplied)`,
  not as success. CI with the artifacts can run the full 57.
