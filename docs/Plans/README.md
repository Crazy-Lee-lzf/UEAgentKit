# UEAgentKit Plans 文档索引

> 更新时间：2026-08-27
>
> 本目录保留当前计划、阶段结果与历史执行记录。历史文件不删除、不移动；后续工作应先从本索引进入，避免误用已经完成或被新计划取代的旧文档。

## 当前权威入口

| 层级 | 文档 | 用途 | 状态 |
|---|---|---|---|
| 项目方向 | [`UEAGENTKIT_MASTER_DEVELOPMENT_PLAN_20260827.md`](UEAGENTKIT_MASTER_DEVELOPMENT_PLAN_20260827.md) | 项目级优先级、Track 边界、架构取舍 | 当前主计划 |
| 中期拆解 | [`UEAGENTKIT_MIDTERM_EXECUTION_SPEC_20260827.md`](UEAGENTKIT_MIDTERM_EXECUTION_SPEC_20260827.md) | 任务依赖、验收契约与跨 Track 关系 | 当前执行规格 |
| 当前实现 | [`UEAGENTKIT_W4_MULTI_OPERATION_BOUNDED_BATCH_DETAILED_PLAN_20260826.md`](UEAGENTKIT_W4_MULTI_OPERATION_BOUNDED_BATCH_DETAILED_PLAN_20260826.md) | W4-0～W4-7 的具体实现与真实 UE 验收 | 下一主线 |
| 当前子阶段 | [`UEAGENTKIT_W4_1_BOUNDED_BATCH_PLAN_DETAILED_PLAN_20260828.md`](UEAGENTKIT_W4_1_BOUNDED_BATCH_PLAN_DETAILED_PLAN_20260828.md) | W4-1 只读 Bounded Batch Plan 的实现与验收契约 | complete |
| 当前子阶段结果 | [`UEAGENTKIT_W4_1_BOUNDED_BATCH_PLAN_RESULT_20260828.md`](UEAGENTKIT_W4_1_BOUNDED_BATCH_PLAN_RESULT_20260828.md) | W4-1 真实 fixed-project S1/S2 只读规划证据 | complete |
| 当前步骤 | [`UEAGENTKIT_W4_0_CONTRACT_FREEZE_AND_BASELINE_PLAN_20260827.md`](UEAGENTKIT_W4_0_CONTRACT_FREEZE_AND_BASELINE_PLAN_20260827.md) | W4-0 契约冻结与 W3 手工编排基线采集 | complete |
| 当前步骤结果 | [`UEAGENTKIT_W4_0_CONTRACT_FREEZE_AND_BASELINE_RESULT_20260827.md`](UEAGENTKIT_W4_0_CONTRACT_FREEZE_AND_BASELINE_RESULT_20260827.md) | W4-0 B0/B1 真实 UE 手工编排基线与冻结契约 | complete |
| 最近完成 | [`UEAGENTKIT_W3_CHECKPOINT_STRONG_VERIFY_RESULT_20260825.md`](UEAGENTKIT_W3_CHECKPOINT_STRONG_VERIFY_RESULT_20260825.md) | W3 C0-C6 最终证据与 W4 入口基线 | complete |
| 计划审计 | [`UEAGENTKIT_MASTER_PLAN_CORRECTION_NOTES_20260827.md`](UEAGENTKIT_MASTER_PLAN_CORRECTION_NOTES_20260827.md) | 2026-08-27 主计划校正记录 | 已处理的审计记录，不是新规格 |

### 冲突时的解释顺序

```text
项目优先级 / Track 取舍
→ MASTER_DEVELOPMENT_PLAN

跨 Track 依赖 / 任务验收
→ MIDTERM_EXECUTION_SPEC

W4 内部阶段、状态机、失败恢复与 C1-C12
→ W4 Detailed Plan

已经完成的事实与真实 UE 证据
→ 对应 RESULT 文档
```

若这些文档之间出现新冲突，应修正文档本身，不依赖对话记忆覆盖磁盘事实。

## 当前阶段

```text
0.7.0 published                         = unchanged
0.8 capability scope                    = locally closed
W0 resident writer baseline             = complete
W1 Blueprint narrow resident write      = complete
W2 Fast Resident Verify                 = complete
W3 Checkpoint Strong Verify             = complete
W4 bounded multi-operation / multi-asset = W4-0/W4-1 complete; W4-2 next
R5 Value Provenance / Execution Trace   = deferred by benchmark evidence
```

当前 `feature/live-writer-expansion` 的 W3 收口 checkpoint：

```text
3280102 fix: close W3 live-write continuation and snapshot refresh
ab731f1 test: cover W3 continuation and full snapshot refresh
45e6ea2 docs: close W3 checkpoint strong verify
```

当前验证基线：712/712 Python tests、0.7.0 release validation、UE5.6 Direct Build 均通过。

## Writer 历史链

按时间顺序保留：

```text
UEAGENTKIT_EDITOR_RESIDENT_WRITER_W0_BASELINE_20260823.md
UEAGENTKIT_EDITOR_RESIDENT_WRITER_W0_W1_DETAILED_PLAN_20260823.md
UEAGENTKIT_EDITOR_RESIDENT_WRITER_W1_ACCEPTANCE_PLAN_20260824.md
UEAGENTKIT_EDITOR_RESIDENT_WRITER_W1_RECOVERY_CLOSURE_PLAN_20260824.md
UEAGENTKIT_EDITOR_RESIDENT_WRITER_W1_ACCEPTANCE_RESULT_20260824.md
UEAGENTKIT_W2_FAST_RESIDENT_VERIFY_DETAILED_PLAN_20260824.md
UEAGENTKIT_W2_FAST_RESIDENT_VERIFY_RESULT_20260824.md
UEAGENTKIT_W3_CHECKPOINT_STRONG_VERIFY_DETAILED_PLAN_20260825.md
UEAGENTKIT_W3_BP_SNAPSHOT_REFRESH_BLOCKER_CLOSURE_PLAN_20260825.md
UEAGENTKIT_W3_CHECKPOINT_STRONG_VERIFY_RESULT_20260825.md
UEAGENTKIT_W4_MULTI_OPERATION_BOUNDED_BATCH_DETAILED_PLAN_20260826.md
UEAGENTKIT_W4_0_CONTRACT_FREEZE_AND_BASELINE_PLAN_20260827.md
UEAGENTKIT_W4_0_CONTRACT_FREEZE_AND_BASELINE_RESULT_20260827.md
UEAGENTKIT_W4_1_BOUNDED_BATCH_PLAN_DETAILED_PLAN_20260828.md
UEAGENTKIT_W4_1_BOUNDED_BATCH_PLAN_RESULT_20260828.md
```

其中 PLAN 表示当时的执行设计，RESULT 表示最终事实。阶段完成后，RESULT 优先于同阶段 PLAN 中的中间状态或 blocker 描述。

## 0.8 Closeout 历史

以下文档记录 2026-08-23 的 0.8 capability closeout，继续作为历史证据保留：

```text
UEAGENTKIT_0_8_CAPABILITY_GAP_AUDIT_20260823.md
UEAGENTKIT_0_8_RELEASE_REVIEW_20260823.md
UEAGENTKIT_POST_0_8_DEVELOPMENT_PLAN_20260823.md
```

`UEAGENTKIT_POST_0_8_DEVELOPMENT_PLAN_20260823.md` 是从 0.8 closeout 进入 Writer 工作的历史桥接计划；当前项目规划入口已经由 2026-08-27 Master Plan / Midterm Spec 接替。其历史基线（例如 739 项 full Python suite）不应被解释为当前 live-writer 分支的固定测试数量。

## 维护规则

- 不删除历史 PLAN/RESULT/Handoff，只标注当前入口与状态。
- 不把测试数量永久硬编码为未来门禁；使用当前分支实际 discovered suite，并在结果文档记录当次值。
- 新的大阶段优先新增一个 Detailed Plan + 一个最终 Result，不在多个总计划中复制阶段内部状态机。
- `MASTER_DEVELOPMENT_PLAN` 负责方向；具体阶段的 Detailed Plan 负责实现细节，避免双重维护。
- 发布版本、Tag、Push、Release artifact 始终属于独立授权流程，不由计划文档自动授权。
