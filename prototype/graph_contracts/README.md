# contract_candidate — G0.3 approved-decision offline contract prototype

**Status: CANDIDATE — not frozen, not UE-integrated.** This directory contains the
machine-readable half of `G0_3_APPROVED_CONTRACT_CANDIDATE.md`: JSON Schemas, fixtures,
deterministic hash/layout vectors, a stdlib-only validator/planner prototype, and
offline tests. It never starts Unreal, never writes `.uasset/.umap`, and never
imports a third-party runtime package.

## Requirements

- Windows + Python **3.12** (`py -3.12`). The default `python` on this machine is
  3.9 and cannot parse the prototype's type syntax.
- No network, no Unreal Editor.

## Run

```powershell
cd E:\WorkSpace\UEAgentKit-Development\contract_candidate
py -3.12 run_tests.py
```

Expected tail:

```text
Ran 102 tests in ~0.5s
OK
SUMMARY tests=102 failures=0 errors=0 skipped=0
```

Other checks:

```powershell
py -3.12 tools\check_codes.py          # every error-code literal is registered
py -3.12 tools\build_fixtures.py       # deterministic; rewrites fixtures/ + vectors/
py -3.12 tools\build_schemas.py        # rewrites schemas/*.schema.json
py -3.12 tools\build_registries.py     # rewrites error/condition code tables
py -3.12 -m compileall -q ueak_contract tests
```

`build_fixtures.py` is deterministic (fixed GUID counter, no clock, no randomness).
Re-running it must leave `fixtures/`, `schemas/` and `vectors/` byte-identical.

## Layout

```text
ueak_contract/
  canonical.py      canonical JSON + domain-separated sha256
  ids.py            Graph/Node/Pin/Edge/Temp identity grammar
  schema.py         frozen 13-keyword JSON-Schema subset + version negotiation
  typed_graph.py    validation, duplicate-pin builder, semantic/node/layout/size hash
  descriptors.py    NodeDescriptor / OperationDescriptor
  graph_patch.py    IR validation, planning, preview, offline apply/rollback/recovery
  layout.py         deterministic WCC/SCC layout, packing, metrics
  recovery.py       persistent Recovery Marker + Save/Apply gate
  errors.py         code registry client
  fixtures.py       manifest/schema/vector loaders

schemas/            6 *.schema.json + error-codes.json + condition-codes.json
fixtures/           manifest.json + typedgraph/ graphpatch/ layout/ descriptors/
vectors/            hash_vectors.json (canonical, semantic, layout, layoutPlan golden)
tests/              102 unittest cases
tools/              deterministic generators + static code-table check
run_tests.py        offline runner (exit code 0 = pass)
```

## Important non-claims

- `simulate_apply` is an **in-memory structural simulation**, marked
  `simulation: true`. It creates synthetic `uuid5` node/pin ids only to test
  ordering, zero-write preview, idempotency, reverse-op rollback and the
  persistent Recovery Marker. It is **not** a UE GraphPatch executor.
- There is no `FScopedTransaction`, `TryCreateConnection`, Blueprint Compile,
  Save or independent reload here.
- D-01…D-13 are **APPROVED (design) 13/13**, but the contract is not frozen and
  implementation is pending. Do not present the candidate defaults as frozen behaviour.
- `semanticHash` follows the G0 whitelist and therefore does **not** include
  `NodeComment` text; Verify must compare comments separately (see review result
  §5.3).

## Provenance

- Baseline: `E:\WorkSpace\UEAgentKit`, `main`, HEAD `23bdbd6`.
- This directory lives outside the Git repository; the main worktree was not
  modified.
- Full traceability: `../G0_3_APPROVED_CONTRACT_IMPLEMENTATION_20261022.md`.
