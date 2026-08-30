# UEAgentKit D1 Agent Workflow Split Result

> Date: 2026-08-29
>
> Branch: `feature/live-writer-expansion`
>
> Entry: `1c68f4d` (D1 plan committed)
>
> Scope: pure structural refactor of `src/ue_agent_kit/agent_workflow.py`; no product behavior, public Tool, serialized evidence, Policy, schema, Writer safety, or C++ change.

## 1. Result Summary

D1 is complete. `agent_workflow.py` is now a thin compatibility facade; the orchestration implementation lives in five new modules:

```text
src/ue_agent_kit/workflow_common.py   1,210 lines
src/ue_agent_kit/workflow_plan.py     1,268 lines
src/ue_agent_kit/workflow_live.py     1,292 lines
src/ue_agent_kit/workflow_verify.py   1,320 lines
src/ue_agent_kit/workflow_batch.py      808 lines
src/ue_agent_kit/agent_workflow.py      172 lines (facade + compatibility glue)
```

Every new `workflow_*.py` is below the 1,500-line bound; `agent_workflow.py` is far below the 1,000-line target.

## 2. Refactor Shape

`PatchWorkflowService` is assembled as:

```python
class PatchWorkflowService(
    WorkflowPlanMixin,
    WorkflowLiveMixin,
    WorkflowVerifyMixin,
    WorkflowBatchMixin,
    WorkflowCommonBase,
    RetargetWorkflowMixin,
):
    pass
```

Method bodies were moved unchanged from the original monolith. The W4 state-machine domains remain authoritative in `bounded_batch.py`, `checkpoint_sets.py`, and `batch_recovery.py`; `workflow_batch.py` contains only the batch-facing hooks and snapshot-refresh machinery consumed by those services.

## 3. Pure-move Proof

Generated against `HEAD:src/ue_agent_kit/agent_workflow.py`:

```text
moved definition count        159
  top-level moved count        50
  PatchWorkflowService methods 109
AST mismatches                  0
missing definitions             0
unexpected replacements         0
retained in facade for mock     2 (_is_reparse_point, _write_json_atomic)
```

Evidence: `Output/D1Acceptance/workflow-ast-move-report.json`.

The two retained helpers are kept as actual definitions in `agent_workflow.py` so existing `unittest.mock.patch("ue_agent_kit.agent_workflow.<helper>")` tests continue to work. Split modules call dynamic proxies to those facade definitions; this is documented compatibility glue, not a method-body rewrite.

## 4. Compatibility Gates

### Public imports

All previously consumed symbols remain importable from `ue_agent_kit.agent_workflow`, including `PatchWorkflowService`, `PatchWorkflowConfig`, `WorkflowError`, `MATERIAL_PARAMETER_OPERATIONS`, `live_write_stable_target_key`, `LiveApplyRecord`, `ProcessResult`, `_live_write_expected_exported_value`, `_live_write_runtime_verification`, and `_live_write_blueprint_exported_value`.

Evidence: `Output/D1Acceptance/public-imports-before.json` / `public-imports-after.json`.

### Tool Registry

```text
Tool Registry total                        112  unchanged
workflow-only                               67  unchanged
workflow + memory                           79  unchanged
combined live + workflow                   100  unchanged
combined live + workflow + memory          112  unchanged
Patch operation count                       18  unchanged
Live-write operation count                  17  unchanged
```

Evidence: `Output/D1Acceptance/tool-registry-before.json` / `tool-registry-after.json` (byte-identical content).

### Structural regression test

The existing `tests/python/test_tool_registry.py` still verifies the facade contains the required structural strings; no existing test was modified. No new structural test was added because the existing tests already cover the compatibility contract.

## 5. Real UE5.6 Smokes

### D1-R1 — full happy-path

```text
one Change Set
→ BP 3 ops + DA 1 op bounded batch Plan
→ resident Apply + Fast Verify all
→ checkpoint-set Preview/Commit
→ aggregate Strong Verify / Semantic Diff / Trust
→ Trust = verified
```

Invariants observed:

```text
4 logical resident applies
4 Fast Verifies
2 package Saves
2 Strong Verify child Unreal processes max
same W4 durable record shapes
no extra cold Unreal process before Strong Verify
```

Evidence: `Output/D1Acceptance/d1-r1-report.json`.

### D1-R2 — resident recovery

```text
unsaved bounded batch
→ product recovery path
→ strict global reverse Undo
→ state = recovered
→ Save = 0
```

Observed:

```text
recovery order: bop_0004 → bop_0003 → bop_0002 → bop_0001
state: recovered
savePerformed: false
```

Evidence: `Output/D1Acceptance/d1-r2-report.json`.

## 6. Regression Gates

```text
Python discovered suite          766 / 766 PASS
Ruff                             PASS
compileall                       PASS
ValidateRelease 0.7.0            PASS
git diff --check                 PASS
AST move report                  0 mismatches
Tool Registry before/after       unchanged
legacy import snapshot           unchanged
D1-R1 real UE smoke              PASS
D1-R2 real UE recovery smoke     PASS
fixture independent verification PASS (2/2)
```

C++ Direct Build was not rerun because D1 touches no C++.

## 7. Final Fixture State

After the smokes, the official `WriteFixturePlan Reset` and independent Reload were run:

```text
DA_TransactionAsset  sha256:a20a1ee3e91ef4d06c2527c7c9d59ed66659bc2d8a2a198cbfad9327e45da2a7
BP_TransactionBlueprint sha256:57328079dccfeddb7ea572c6636705d19a0cfef255234bcd726547416b3af6c2
independent verification: 2/2 PASS
```

Active paired snapshot was refreshed to the final disk Revisions:

```text
generation: gen_20260829T094146Z_f4b5671ea55f
```

Evidence:
- `Output/D1Acceptance/final-fixture-reset-report.json`
- `Output/D1Acceptance/final-fixture-verification-report.json`
- `Output/D1Acceptance/final-fixture-validation-report.json`
- `Output/D1Acceptance/FinalReload/`

No product asset, Output artifact, backup, Tag, Release, or Push is included in the D1 commit.

## 8. Explicit Prohibitions Observed

```text
rename public methods/classes/constants          NO
change public function signatures/defaults       NO
change error codes/messages intentionally        NO
change return dictionaries                       NO
change persisted JSON layout or schema versions  NO
change W4 bounds                                 NO
change confirmation phrases                      NO
change Policy or Revision behavior               NO
change transaction/recovery order                NO
change tool registration                         NO
optimize process calls                           NO
reduce Bridge calls                              NO
cache new state                                  NO
replace locks                                    NO
introduce async behavior                         NO
add dependencies                                 NO
modify C++                                       NO
start W5 measurements                            NO
start Memory implementation                      NO
general code cleanup while moving                NO
```

## 9. Exit Gate

All D1 exit conditions are met:

```text
[x] entry baseline is 1c68f4d and pre-refactor suite captured
[x] every old PatchWorkflowService method has one documented destination
[x] workflow_common/plan/live/verify/batch modules created
[x] every new workflow_*.py is < 1,500 lines
[x] agent_workflow.py is a thin compatibility facade
[x] legacy imports remain valid
[x] public signatures/defaults remain unchanged
[x] moved-definition AST report has 0 mismatches
[x] existing tests pass without changing their behavioral expectations
[x] Tool Registry / Operation surface unchanged
[x] W4 persisted evidence contract unchanged
[x] D1-R1 real UE full happy-path smoke passes
[x] D1-R2 resident recovery smoke passes
[x] official fixture Reset + independent verification passes
[x] Python / Ruff / compileall / ValidateRelease / git diff --check all pass
[x] no C++ / dependency / Policy / schema / published-version change
[x] D1 Result document written
```

The mainline may now proceed to W5, and workflow-touching Memory / Track X work may use the new `workflow_*.py` structure.
