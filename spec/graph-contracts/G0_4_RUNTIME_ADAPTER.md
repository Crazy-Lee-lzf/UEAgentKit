# G0.4 — Python Runtime Contract Adapter (2026-10-22)

Status: **implemented + offline tested on feature branch; NOT contract-frozen; NOT Editor-integrated**.

> C-LAYOUT-P0 update: this document describes the original two-API adapter.
> The current public surface also includes `preview_graph_layout` and
> `typed_graph_from_blueprint_context`; see
> `C_LAYOUT_MVP_PROGRESS_20261022.md` for the layout MVP, strict exporter
> importer and remaining Editor gaps.

## Scope

- Added `src/ue_agent_kit/graph_contracts/` as an isolated stdlib-only distribution package containing the approved G0.3 schema/validation/preview reference. Existing scalar Patch and Live Editor APIs are unchanged.
- Public APIs: `validate_contract_document(kind, document)` and `preview_graph_patch(patch, typed_graph, environment=None, descriptors=None)`. Both explicitly return `editorVerified=false`, `writeAuthorized=false`. Preview exposes `previewOnly=true` and no transaction/Save/asset writes.
- Supported schema kinds: `graphpatch`, `typedgraph`, `layout-plan`, `node-descriptor`, `operation-descriptor`, `change-set-idempotency`. Schema paths are an allowlist; packaged JSON data is included in the wheel.
- G0.3 policy invariants are enforced by the same candidate implementation: 200 default, explicit 300 structure-g2, position-only 1000 layout-only, no V1 `deleteNode`, explicit enabled confirmation, graph identity checks, no Save.
- ChangeSet 2.1: **only validates an optional idempotency-block schema**; `src/ue_agent_kit/change_sets.py` and its persisted ChangeSet 2.0 format were not modified. No real 2.1 journal migration or UE Apply path is wired.
- Kept the frozen `prototype/graph_contracts` snapshot intact; copies of its stdlib runtime modules were promoted under `src/` with adjusted bundled-schema location. **Duplication is temporary technical debt**. During contract-freeze review decide whether to deduplicate and make the runtime package authoritative; new tests compare preview semantic outputs against the candidate, ignoring only generated timestamps.

## Reproduction

From `E:\WorkSpace\UEAgentKit-WT\graph-contracts`:

```powershell
py -3.12 -m unittest discover -s tests/python -p test_graph_contracts_runtime.py
py -3.12 -m unittest discover -s tests/python -p test_change_sets.py
py -3.12 -m unittest discover -s tests/python -p 'test_patches*.py'
py -3.12 -m unittest discover -s tests/python -p 'test_blueprint_patch*.py'
py -3.12 prototype/graph_contracts/run_tests.py
py -3.12 -m compileall -q src/ue_agent_kit/graph_contracts
```

Observed: runtime **8/8 PASS**, legacy ChangeSet **16/16 PASS**, old scalar Patch **92/92 PASS**, old BP Patch **10/10 PASS**, candidate **102 tests: 0 fail, 0 errors, 1 skipped**, compileall exit 0.

Wheel: built offline using `py -3.12 -m pip wheel . --no-deps --no-index --no-build-isolation`; verified all 8 packaged schemas and runtime modules in the wheel, and extracted wheel into isolated temporary folder to validate a GraphPatch fixture successfully. Ruff was not installed in the Python 3.12 environment; no Ruff pass is claimed.

## Hard boundaries / remaining work

- This is an offline Python adapter, NOT a GraphPatch Executor, Node Spawn engine, D-12 independent UE reload, or real ChangeSet 2.1 migration.
- No MCP registration yet: avoid implying this is an authorized live UE mutation surface.
- Before merging: review public schema versions/packaging and parity; reduce copied source duplication; design durable ChangeSet 2.1 journal with process crash evidence; integrate Policy and C++ UE executor under separate Editor authorization.
- No work on `main`, no asset writes, no push, no model or DSH settings changes.