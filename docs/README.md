# UE Agent Kit 文档

本目录同时包含两类内容：

1. **当前开发接管/规划文档**：给新 Chat、Agent 和维护者使用；
2. **产品/架构/使用文档**：面向 UEAgentKit 的功能、构建、协议和发布版本。

历史开发计划和旧交接已经归档，不再全部堆在默认导航页。

## 新 Chat / Agent：先读这里

按顺序读取：

1. [`Handoffs/UEAGENTKIT_CURRENT_DEVELOPMENT_HANDOFF_20260830.md`](Handoffs/UEAGENTKIT_CURRENT_DEVELOPMENT_HANDOFF_20260830.md) — **当前项目级完整交接**；仓库、分支、Track、Git 异常、R20、测试规范和下一步边界。
2. [`DEVELOPMENT_WORKFLOW.md`](DEVELOPMENT_WORKFLOW.md) — **强制开发执行规范**；G0-G3 测试分级、U0-U3 UE 验收、UE lease、性能采样和文档粒度。
3. [`Plans/README.md`](Plans/README.md) — 当前计划唯一导航入口。
4. [`Plans/Archive/UEAGENTKIT_C3_CHANGELIST_RESOLVE_AUDIT_RESULT_20260904.md`](Plans/Archive/UEAGENTKIT_C3_CHANGELIST_RESOLVE_AUDIT_RESULT_20260904.md) — C3 owner-reviewed 收口、P4 只读 smoke、G1/G2 与 A27 状态。
5. [`Plans/UEAGENTKIT_P4_AGENT_OPERATION_BOUNDARY_DECISION_20260903.md`](Plans/UEAGENTKIT_P4_AGENT_OPERATION_BOUNDARY_DECISION_20260903.md) — 永久 P4 Agent 权限边界。
6. [`Plans/UEAGENTKIT_W_V_INTEGRATION_RESULT_20260830.md`](Plans/UEAGENTKIT_W_V_INTEGRATION_RESULT_20260830.md) — Writer + Knowledge Web 合并后的 G3 证据。
7. [`Plans/UEAGENTKIT_MASTER_DEVELOPMENT_PLAN_20260827.md`](Plans/UEAGENTKIT_MASTER_DEVELOPMENT_PLAN_20260827.md) / [`Plans/UEAGENTKIT_MIDTERM_EXECUTION_SPEC_20260827.md`](Plans/UEAGENTKIT_MIDTERM_EXECUTION_SPEC_20260827.md) — 历史项目方向和跨 Track 依赖；其中阶段状态与旧 Track C 权限描述由当前 handoff / Plans README / P4 boundary 覆盖。

### 当前开发状态摘要

```text
正式发布版本                    0.7.0 / UE5.6（unchanged）
Track W / Writer               COMPLETE
Track V / Knowledge Web        COMPLETE
W + V integration             G3 PASS
Track M                        COMPLETE through M5
  M6                           optional / data-driven
Track C / P4                   COMPLETE through C3 / owner-reviewed
  C1                           COMPLETE
  C2                           COMPLETE
  C3                           COMPLETE @ 5b705a7
  A27 real C3 mutation         OWNER-FIXTURE BLOCKED
  C4                           optional / deferred
当前 portable full            1062 / 1062 PASS / 17 skipped
下一主线                       real-project write-enabled dogfood
R20                            deferred fixture-lifecycle debt
```

R20 是 DirectHost Fixture 在 resident Editor 生命周期中的语义漂移，不再阻塞 Writer；产品 Revision/Freshness gate 正确 fail-closed。C1–C3 关闭后不自动启动 C4/M6，下一阶段优先在 owner 指定的真实商业项目里做 write-enabled dogfood。

## 历史开发文档

完成/被取代的开发文档仍完整保留：

- [`Plans/Archive/`](Plans/Archive/) — Agent Reliability、Writer W0-W5、D1、V1/V2、Memory M1-M5、Source Control C1-C3 等已完成 Plan/Result；
- [`Handoffs/Archive/`](Handoffs/Archive/) — 旧 milestone / feature / release / chat handoff。

历史文件是证据，不是当前入口。需要调查某个旧能力时再按需打开。

## 当前产品状态 / 架构

- [`PROJECT_STATUS.md`](PROJECT_STATUS.md)：产品能力与已知边界快照。
- [`PROJECT_STATUS_EN.md`](PROJECT_STATUS_EN.md)：English project status snapshot。
- [`AI_NATIVE_UE_EDITOR.md`](AI_NATIVE_UE_EDITOR.md)：AI 可用 UE5 编辑器的知识、实时 CRUD、性能与安全架构。
- [`AI_NATIVE_UE_EDITOR_EN.md`](AI_NATIVE_UE_EDITOR_EN.md)：English AI-usable UE5 Editor architecture。
- [`MEMORY_ARCHITECTURE.md`](MEMORY_ARCHITECTURE.md)：Project Memory / Knowledge Tree / Active Work 架构。
- [`MEMORY_ARCHITECTURE_EN.md`](MEMORY_ARCHITECTURE_EN.md)：English memory architecture。
- [`PERFORMANCE_TEST_PLAN.md`](PERFORMANCE_TEST_PLAN.md)：大型项目、PerfProject、SSD/HDD 模拟与性能门禁设计。
- [`REFERENCE_POLICY.md`](REFERENCE_POLICY.md)：第三方参考、独立实现与依赖分发规则。

## 构建和使用

- [`BUILD_AND_RUN.md`](BUILD_AND_RUN.md)：环境、插件构建、导出、SQLite 查询。
- [`AI_USAGE.md`](AI_USAGE.md)：AI 如何使用资产目录、Blueprint 语义和结构化引用。
- [`MODELPREVIEW_INTEGRATION_MANUAL.md`](MODELPREVIEW_INTEGRATION_MANUAL.md)：ModelPreview 集成说明。
- [`BRANCH_WORKTREES.md`](BRANCH_WORKTREES.md)：Worktree/分支工作方式（当前具体 ref 状态以 2026-08-30 handoff 为准）。
- [`BRANCH_WORKTREES_EN.md`](BRANCH_WORKTREES_EN.md)：English worktree workflow。
- [`PARALLEL_AGENT_DEVELOPMENT.md`](PARALLEL_AGENT_DEVELOPMENT.md)：并行 Agent、文件所有权和共享 UE 资源规则。

## Live Editor / 动画工具文档

- [`LIVE_EDITOR_REALTIME_IO_PLAN.md`](LIVE_EDITOR_REALTIME_IO_PLAN.md)
- [`ANIMATION_RETARGET_SCALE_DIAGNOSIS_20260806.md`](ANIMATION_RETARGET_SCALE_DIAGNOSIS_20260806.md)
- [`ANIMATION_SCALE_FIX_TOOL.md`](ANIMATION_SCALE_FIX_TOOL.md)
- [`ANIMATION_SCALE_AUDIT_TOOL.md`](ANIMATION_SCALE_AUDIT_TOOL.md)
- [`ANIMATION_SCALE_FIX_BATCH_TOOL.md`](ANIMATION_SCALE_FIX_BATCH_TOOL.md)
- [`ADDITIVE_ANIMATION_DIAGNOSIS_TOOL.md`](ADDITIVE_ANIMATION_DIAGNOSIS_TOOL.md)
- [`ADDITIVE_ANIMATION_EVALUATION_TOOL.md`](ADDITIVE_ANIMATION_EVALUATION_TOOL.md)
- [`ADDITIVE_BASE_POSE_FIX_PLAN_TOOL.md`](ADDITIVE_BASE_POSE_FIX_PLAN_TOOL.md)
- [`ADDITIVE_BASE_POSE_FIX_WRITE_TOOL.md`](ADDITIVE_BASE_POSE_FIX_WRITE_TOOL.md)
- [`CHARACTER_GROUND_CONTACT_TOOL.md`](CHARACTER_GROUND_CONTACT_TOOL.md)
- [`SKELETAL_SECONDARY_MOTION_TOOL.md`](SKELETAL_SECONDARY_MOTION_TOOL.md)
- [`RETARGET_POSTPROCESS_TOOL.md`](RETARGET_POSTPROCESS_TOOL.md)

## 协议 / 安全规范

核心规范位于仓库 [`spec/`](../spec/)：

- [`BPCTX_FORMAT.md`](../spec/BPCTX_FORMAT.md)
- [`PATCH_SCHEMA.md`](../spec/PATCH_SCHEMA.md)
- [`BACKUP_AND_ROLLBACK.md`](../spec/BACKUP_AND_ROLLBACK.md)
- [`WRITE_FIXTURE_PLAN.md`](../spec/WRITE_FIXTURE_PLAN.md)
- [`SCALAR_PATCH_REGRESSION.md`](../spec/SCALAR_PATCH_REGRESSION.md)
- [`MCP_SERVER.md`](../spec/MCP_SERVER.md)
- [`INDEX_FRESHNESS.md`](../spec/INDEX_FRESHNESS.md)
- [`LIVE_EDITOR_BRIDGE.md`](../spec/LIVE_EDITOR_BRIDGE.md)
- [`PROJECT_MEMORY.md`](../spec/PROJECT_MEMORY.md)

## 发布文档

最新正式发布仍为 **0.7.0 / UE5.6**：

- [`RELEASE_0.7.0.md`](RELEASE_0.7.0.md) / [`RELEASE_0.7.0_EN.md`](RELEASE_0.7.0_EN.md)
- [`RELEASE_0.6.0.md`](RELEASE_0.6.0.md) / [`RELEASE_0.6.0_EN.md`](RELEASE_0.6.0_EN.md)
- [`RELEASE_0.5.5.md`](RELEASE_0.5.5.md) / [`RELEASE_0.5.5_EN.md`](RELEASE_0.5.5_EN.md)
- [`RELEASE_0.5.1.md`](RELEASE_0.5.1.md) / [`RELEASE_0.5.1_EN.md`](RELEASE_0.5.1_EN.md)
- [`RELEASE_0.5.0.md`](RELEASE_0.5.0.md) / [`RELEASE_0.5.0_EN.md`](RELEASE_0.5.0_EN.md)
- [`RELEASE_0.4.4.md`](RELEASE_0.4.4.md) / [`RELEASE_0.4.4_EN.md`](RELEASE_0.4.4_EN.md)
- [`../CHANGELOG.md`](../CHANGELOG.md)

本地开发能力进入 `main` 不等于正式 package release。Push / Tag / Release / version change 仍需要独立授权。
