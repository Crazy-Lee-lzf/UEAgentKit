# RC1 — Clean-Checkout Test Reproducibility — 2026-10-25

- Worktree: `E:\WorkSpace\UEAgentKit` (local `main`)
- Base HEAD: `2bc3a5554d302180607ecd1f552a9e5eecb13d80` (clean before RC1)
- RC1 code/test commit: `ad7a8a5b1c52d7f0f60b4786001ba57a633bf210`
- `origin/main` unchanged: `c5ce3419c6962da49e874586f4e5dbd1457a4d0d` (no push/fetch/tag)
- Mode: test/dev-tool only; no UE Editor/UBT/UBA, no broker service, no product code,
  no Memory DB, no C++/plugin changes. Defaults remain off.

## 1. Result

| Check | Result |
|---|---|
| `tests/native_tools` clean checkout | **44 tests, 5 skipped, 0 failures** (4.957 s) |
| Same suite with full R1 UE evidence | **57 tests, 0 skipped** (candidate M3.10 run) |
| `test_m39_z6_datasets` targeted | **4 tests OK** |
| Z6 tool on clean main (no evidence) | **exit 2**, `EVIDENCE_NOT_PRESENT: three-graphs.json` (no traceback) |
| Python full (clean main, after fix) | **1526 tests, 18 skipped, 0 failures**, 145.374 s |
| Z5 check-only | PASS (previous clean-main run; unchanged, no evidence dependency) |
| Git after RC1 | clean; origin/main ref unchanged; no push |

The full suite is 1526 = previous 1522 + 4 new Z6 tests. The 18 skips are the same 17 as the
clean-main baseline plus the absent-M3.3-evidence guard added in `2bc3a55`.

## 2. `tests/native_tools`: 44/5 vs 57/0

The suite has two layers. A class-level `setUpClass` skip is reported as **one** skipped entry and
hides every test in that class, which is exactly the 13-test difference:

| Skip entry | Hidden tests | Required artifact | Producer |
|---|---:|---|---|
| `ContextExportTests` (class) | 7 | `Build/r1-native/Evidence/context-export.json` (identity check also reads `catalog-export.json`) | `scripts/RunR1NativeTests.ps1` / `UEAgentKit.Native.R1` |
| `TwoProcessParityTests` (class) | 6 | `Build/r1-native/Evidence/t4/run-a/catalog-export.json` and `.../run-b/catalog-export.json` | `scripts/RunR1TwoProcessParity.ps1` |
| `RealEvidencePagingTests.test_512_exported_ids_page_without_gap` | 1 | `Build/r1-native/Evidence/catalog-export.json` (512 descriptors) | `scripts/RunR1NativeTests.ps1` (`R1Catalog` export) |
| `CatalogExportTests.test_export_contains_at_least_two_hundred_descriptors` | 1 | same catalog export | same |
| `CatalogExportTests.test_export_if_present` | 1 | same catalog export | same |

**13 = 7 + 6** hidden by the two class skips; the three individual skips are already part of the
clean 44. With all artifacts present the same command runs 57/0 (the one optional `jsonschema`
schema check can skip if the package is absent; it is installed here).

The layering and exact paths are documented for CI in `tests/native_tools/README.md`. Skips are
**not** treated as passes; no dummy catalog/context data was added.

## 3. Z6 tool: explicit evidence, fail fast

`tools/m39_z6_datasets.py` previously read `Build/m39-readiness/three-graphs.json` and
`Build/m38-integration/...` unconditionally, so a clean checkout raised `FileNotFoundError`.
Minimal changes:

- Explicit `--three-graphs`, `--combined-evidence`, `--native-evidence-dir`, `--out` options
  (defaults remain the previous Build paths for the evidence host).
- `EvidenceNotPresent` → stderr `EVIDENCE_NOT_PRESENT: <basename>` and **exit 2**; no traceback,
  no absolute paths/tokens in output.
- Empty datasets fail closed instead of vacuously passing.
- Aggregates-only output preserved; strict criteria unchanged when evidence is present.
- New `tests/python/test_m39_z6_datasets.py` (4 tests): synthetic positive/negative datasets with
  0 writes/0 tokens, missing evidence, empty datasets, CLI exit 2. It uses synthetic receipts only
  and never claims a real UE pass.

Clean-main behavior after the change:

```text
$ py -3.12 tools/m39_z6_datasets.py
EVIDENCE_NOT_PRESENT: three-graphs.json
(exit 2)
```

## 4. Full-suite flake found and fixed

The first RC1 full run failed once in
`test_layout_broker_server.HappyPathAndParityTests.test_bad_client_does_not_stop_the_broker`
(`completed 0 != 1`). The test passed in isolation and failed under load because the server
publishes the completed counter just after the response. RC1 replaced the instantaneous assertion
with a bounded 2 s poll; five consecutive targeted runs passed, and the next full run was green.
This is a test-only timing fix, not a product change.

## 5. What is still blocked / not claimed

- The **13 evidence-gated native tests** require real UE 5.6.1 artifacts; they remain SKIP on a clean
  checkout and are **not** claimed as passed here. No dummy fixtures.
- No UE build/runtime was performed in RC1; R1/M3 UE evidence remains the pre-existing M3.10 record.
- Z6 production telemetry and R7/M4 activation are still blocked (host auth, signed packs, PII,
  approved thresholds); defaults stay `NativeToolRegistry=off`, `NativeLayoutPreview=off`,
  `MemoryMount` disabled.
- No push, tag, release, or origin change; `origin/main` remains `c5ce341`.

## 6. Commands

```powershell
# clean-checkout native layers
py -3.12 -B -m unittest discover -s tests/native_tools -p "test_*.py"

# Z6 tool behavior
py -3.12 tools/m39_z6_datasets.py
py -3.12 -m unittest test_m39_z6_datasets -v

# full Python suite
py -3.12 scripts/RunPythonTests.py full
```

Logs are under the Git-ignored `Build/` (`rc1-native-tools-clean.log`,
`rc1-python-full-2.log`) and are not committed.
