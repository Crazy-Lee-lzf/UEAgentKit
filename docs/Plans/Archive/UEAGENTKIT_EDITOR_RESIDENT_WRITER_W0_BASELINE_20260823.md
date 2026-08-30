# UEAgentKit Editor-Resident Writer W0 Baseline

> 日期：2026-08-23
>
> 分支：`feature/live-writer-expansion`
>
> 基线提交：`feature/agent-reliability@9917c0a`（含 W0/W1 详细计划文档）
>
> 执行入口：`docs/Plans/UEAGENTKIT_EDITOR_RESIDENT_WRITER_W0_W1_DETAILED_PLAN_20260823.md`

## 1. 目标

W0 只做审计、测量、契约与最小 instrumentation。本文记录：

- 当前写路径机械事实表（Trace Matrix）；
- stage wall-clock 采集点；
- B0-B3 baseline 测量记录；
- Fast Resident Verify 与 Strong Independent Verify 书面契约；
- Save / Verify 双 cold-start 决策记录；
- W0 Exit Gate 核对结果。

W1 不会在 W0 未完成前开始实现。

## 2. 当前路径 Trace Matrix

标记约定：

```text
resident-editor        = 在已打开 UnrealEditor.exe 内完成（Editor Bridge / Live Write Registry）
python-only            = 不启动任何 Unreal 进程，仅在 MCP Server / Python 内完成
commandlet-cold-start  = 启动 UnrealEditor-Cmd.exe（可能是 AssetPatch / BlueprintPatch / AssetCatalogExport）
independent-reload     = 新进程重新加载/导出磁盘资产（属于 commandlet-cold-start 的一种）
not-applicable         = 该路径当前不经过此阶段
```

| Stage | Non-BP Live | BP Variable | BP Component | BP Pin | UE Process | Independent |
|---|---|---|---|---|---|---|
| Plan | python-only | python-only | python-only | python-only | 0 | no |
| DryRun | commandlet-cold-start（若走 high-level DryRun；B0 Live 闭环不经过） | commandlet-cold-start | commandlet-cold-start | commandlet-cold-start | 1 | no |
| Live Apply / Commit | resident-editor（`editor.applyAssetPropertyLive`） | commandlet-cold-start（当前未注册 Live，走 `RunPatch.ps1` Commit） | commandlet-cold-start | commandlet-cold-start | 1（BP 当前） | no |
| Compile | not-applicable（普通属性无编译） | commandlet-cold-start（`BlueprintPatchCommandlet::CompileBlueprint`） | commandlet-cold-start | commandlet-cold-start | 1（BP 当前） | no |
| Save Preview | python-only + resident inspection | python-only + resident inspection | python-only + resident inspection | python-only + resident inspection | 0 | no |
| Save Commit | resident-editor save + commandlet-cold-start independent export | resident-editor save（若已 dirty）+ commandlet-cold-start independent export | resident-editor save（若已 dirty）+ commandlet-cold-start independent export | resident-editor save（若已 dirty）+ commandlet-cold-start independent export | 1 | yes（第一次独立 export） |
| Verify | commandlet-cold-start independent reload/export | commandlet-cold-start independent reload/export | commandlet-cold-start independent reload/export | commandlet-cold-start independent reload/export | 1 | yes（第二次独立 export） |
| Semantic Diff | python-only（消费已有 evidence/canonical） | python-only | python-only | python-only | 0 | no（消费已有 evidence） |
| Trust | python-only | python-only | python-only | python-only | 0 | no（消费已有 evidence） |

### 2.1 关键机械事实

- `plan_patch()` 只读取 SQLite index、`validate_patch()` 读取固定 Revision Export，不启动 UE 进程。
- `dry_run_patch()` / `apply_patch()` 通过 `_run_script("RunPatch.ps1")` 启动 `UnrealEditor-Cmd.exe`；`RunPatch.ps1` 内部根据 operation 集合选择 `AssetPatch` 或 `BlueprintPatch` Commandlet。
- `apply_asset_property_live()` 对 `LIVE_WRITE_OPERATION_REGISTRY` 内 operation 调用 Editor Bridge `editor.applyAssetPropertyLive`，全程 resident-editor。
- 当前 Blueprint 三个 operation（`setVariableDefault` / `setComponentProperty` / `setPinDefault`）没有 `live_write_value_kind`，因此不在 `LIVE_WRITE_OPERATION_REGISTRY`，不能走 resident Live Apply。
- `save_authorized_asset(mode=Commit)` 在 Editor Bridge 内保存后立刻调用 `_export_refresh_candidate()` → `RunAssetCatalog.ps1`，这是第一次独立 Commandlet export。
- `verify_live_write()` 在已保存后再次调用 `RunAssetCatalog.ps1`，这是第二次独立 Commandlet export。
- `semantic_diff_workflow.py` 与 `verification_trust.py` 不启动子进程，只消费已产生的 evidence / canonical / report。

## 3. Stage Wall-clock Instrumentation

已对 `agent_workflow.py` 做最小 instrumentation：

- `ProcessResult` 增加 `elapsed_ms: float = 0.0`；
- `_default_process_runner()` 使用 `time.perf_counter()` 记录每个 workflow 子进程的 wall-clock；
- 现有公开 Tool success contract 不变，未把 wall-clock 作为 Trust evidence。

采集字段映射：

```text
plan_ms                       plan_patch() 内 python-only 耗时（可在调用方/测试 harness 计时）
dry_run_ms                    dry_run_patch() 内 _run_script("RunPatch.ps1") 的 ProcessResult.elapsed_ms
live_apply_or_commit_ms       apply_asset_property_live() 或 apply_patch() 的 bridge/script 耗时
compile_ms                    BlueprintPatchCommandlet 报告内 compiled=true 对应的一次 Commandlet 总耗时（stage 级粗粒度）
save_preview_ms               save_authorized_asset(mode=Preview) python-only 耗时
save_commit_ms                save_authorized_asset(mode=Commit) bridge save + 第一次 export 总耗时
save_embedded_verify_ms       第一次 RunAssetCatalog.ps1 的 ProcessResult.elapsed_ms
strong_verify_ms              第二次 RunAssetCatalog.ps1 的 ProcessResult.elapsed_ms
semantic_diff_ms              semantic_diff_workflow 调用耗时（python-only）
verification_plan_ms          verification/trust 调用耗时（python-only）
trust_ms                      verification_trust 调用耗时（python-only）
child_ue_process_count        当前 BP 冷路径：Plan=0, DryRun=1, Commit=1, Save export=1, Verify export=1
child_ue_process_total_ms     各 RunPatch / RunAssetCatalog ProcessResult.elapsed_ms 之和
```

## 4. Baseline Cases

### 4.1 B0 — Existing non-Blueprint Live scalar

路径：

```text
Plan → Live Apply → Save Preview → Save Commit → Verify Live Write
→ Semantic Diff → Verification Plan → Trust → exact recovery
```

- 执行载体：现有 `TestMcpLiveWrite.ps1` / `mcp_live_write_smoke.py`。
- 重复：3 次。
- 记录字段：cold-start count、total elapsed、UE process elapsed share、resident editor elapsed share。

### 4.2 B1 — Blueprint variable default

路径（当前冷路径，W1 完成后改为 Live Apply）：

```text
Plan → DryRun → Commit → Save/Verify → Semantic Diff → Trust → fixture recovery
```

- 执行载体：`scripts/TestMcpBlueprintSemanticDiff.ps1` 或 `TestMultiOperationTransactions.ps1` 的 Blueprint 单操作段。
- 重复：3 次。

### 4.3 B2 — Blueprint component property

当前冷路径：

```text
Plan → DryRun → Commit → Save/Verify → Semantic Diff → Trust → fixture recovery
```

- 至少 1 次完整功能闭环；可使用事务 fixture 的组件属性操作（若现有 fixture 无单操作脚本，则用 `RunPatch.ps1` 手工构造单操作 patch）。

### 4.4 B3 — Blueprint pin default

当前冷路径同上，至少 1 次完整功能闭环。

### 4.5 已记录 Baseline

#### B0 — Existing non-Blueprint Live scalar（直接 Host 项目，`TestMcpLiveWrite.ps1`）

```text
attempt 1 total_elapsed_ms = 34467.5   cold-start count = 2 (fixture reset + revision export; live apply 0; save/verify exports 2)
attempt 2 total_elapsed_ms = 35041.4   cold-start count = 2
attempt 3 total_elapsed_ms = 36553.2   cold-start count = 2
fixture_recovery: ok (3/3)
```

说明：B0 的 `TestMcpLiveWrite.ps1` 在 live apply 前需要 fixture reset + revision export 两个 Commandlet；live apply 本身为 resident-editor；Save Commit 与 Verify 各一次独立 export，共 2 次保存/验证 cold-start。

#### B1 — Blueprint variable default（cold path，`TestMcpBlueprintSemanticDiff.ps1`）

```text
attempt 1 total_elapsed_ms = 57185.4
attempt 2 total_elapsed_ms = 57376.4
attempt 3 total_elapsed_ms = 55242.6
cold-start count = 7（按代码路径统计：fixture reset 1 + revision export 1 + DryRun patch 1 + Commit patch 1 + verify export 1 + rollback dry-run 1 + rollback commit/verify 1）
fixture_recovery: ok (3/3)
```

#### B2 — Blueprint component property（cold path，手工 `RunPatch.ps1` Patch，`DefaultSceneRoot.RelativeLocation.X`）

```text
dry_run_ms = 8345.8
commit_ms   = 8309.8
cold-start count = 2（DryRun Commandlet + Commit Commandlet；未做完整 Semantic Diff/Trust 闭环）
fixture_recovery: ok（Commit 后从 raw backup 精确恢复 baseline sha256:423c...）
```

说明：当前 DirectHost `BP_TransactionBlueprint` 自带 `DefaultSceneRoot` 组件，因此 B2 可执行。B2 的完整 Semantic Diff / Trust 闭环可在 W1 测试矩阵中补全。

#### B3 — Blueprint pin default（W1 Acceptance 已补测）

- W1 Acceptance 新增确定性 fixture：EventGraph 内 `Add_IntInt` 调用节点，`graphGuid=12345678-9abc-def0-1234-56789abcdef0`，`nodeGuid=11111111-2222-2222-3333-333344444444`，input pin `A` 默认 `0`、`B` 默认 `1`。
- 当前 cold path 已实测：

```text
dry_run_ms = 8283.3
commit_ms   = 8269.9
cold-start count = 2
fixture_recovery: ok（Commit 后从 raw backup 精确恢复 baseline sha256:ec11...）
```

- 代码路径事实不变：`setPinDefault` 当前经 `RunPatch.ps1` → `BlueprintPatchCommandlet`。

### 4.6 记录模板

```text
case: B0/B1/B2/B3
attempt: 1/2/3
cold-start count:
total elapsed ms:
ue_process_elapsed_ms:
ue_process_share:
resident_elapsed_ms:
resident_share:
fixture_recovery: ok
```

## 5. Fast Resident Verify / Strong Independent Verify Contract Freeze

### 5.1 Fast Resident Verify

只允许证明：

- exact Editor Session；
- exact target asset loaded；
- exact target identity 仍存在；
- current in-memory value == requested value；
- package dirty/clean state；
- Blueprint compile result；
- 可选 Data Validation；
- current Change Set / Transaction applicability。

明确不能证明：

- fresh process reload；
- saved disk canonical correctness；
- independent package Revision；
- runtime behavior；
- whole-task Trust。

### 5.2 Strong Independent Verify

必须继续证明：

- disk Package 已保存；
- independent Unreal load/export；
- exact asset identity；
- actual disk Revision；
- canonical expected semantics；
- Change Set verified stage applicability。

Fast Resident Verify 不能替代 Strong Independent Verify；W2 才建立正式 Fast Verify，W3 才允许 checkpoint 优化。

## 6. Save / Verify Double Cold-start Decision Record

问题：一次 Live Write 保存闭环当前至少包含两次独立 Commandlet export：

```text
Save Commit → embedded independent export（第一次）
Verify      → second independent export（第二次）
```

候选方案：

### Option A — 保持现状（推荐用于 W0/W1）

```text
Save Commit → embedded independent export → Verify → second independent export
```

- 优点：兼容最强，不改变 `ue_save_authorized_asset` 的 `verified=true` 语义。
- 缺点：cold-start 重复，但属于 W3 优化范围。

### Option B — Save 只做 persistence + disk revision，Verify 做唯一 Strong Verify

- 优点：职责清楚、只一次 strong verify。
- 风险：改变现有 `ue_save_authorized_asset` 的 `verified=true` 语义，需要兼容迁移。

### Option C — Save 增加 explicit verification mode，默认保持兼容

- 优点：可渐进迁移。
- 风险：公共 API 复杂度增加。

**W0 推荐：Option A 作为 W0/W1 默认；W3 再评估 Option B/C。W1 不实施 Save/Verify 重构。**

## 7. W0 Exit Gate

```text
[ ] Current path trace matrix complete
[ ] B0-B3 baseline recorded
[ ] Cold-start count mechanically measured
[ ] Save/Verify duplicate export cost measured
[ ] Fast Verify contract written
[ ] Strong Verify contract written
[ ] W3 migration recommendation written
[ ] DirectHost fixtures exact recovery
[ ] No R4.1 raw measurement changes
[ ] No product Writer behavior changes unless instrumentation required
[ ] git diff --check pass
```

## 8. 独立 Commit 建议

```text
perf/docs: baseline editor-resident writer path
```