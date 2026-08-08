# UE Agent Kit 文档

本目录保留当前公开版本与 `main` 开发快照的用户文档。项目概览和快速示例见仓库根目录 [`README.md`](../README.md)。

## 当前状态与对比

- [`PROJECT_STATUS.md`](PROJECT_STATUS.md)：当前已实现能力、明确未实现能力、P0–P3 优先级和后续方向。
- [`PROJECT_STATUS_EN.md`](PROJECT_STATUS_EN.md)：English project status.
- [`MEMORY_ARCHITECTURE.md`](MEMORY_ARCHITECTURE.md)：分层知识树、Active Work、渐进式披露、MCP/Skill 分工和多人共享知识服务设计。
- [`MEMORY_ARCHITECTURE_EN.md`](MEMORY_ARCHITECTURE_EN.md)：English layered memory and collaboration architecture.
- [`AI_NATIVE_UE_EDITOR.md`](AI_NATIVE_UE_EDITOR.md)：AI 可用 UE5 编辑器的实时 CRUD、项目模型、知识树、性能与风险自适应安全架构。
- [`AI_NATIVE_UE_EDITOR_EN.md`](AI_NATIVE_UE_EDITOR_EN.md)：English AI-usable UE5 Editor architecture.
- [`PERFORMANCE_TEST_PLAN.md`](PERFORMANCE_TEST_PLAN.md)：500 GB 商业项目模型、E 盘 SSD 上的 160–180 GB 物理测试工程、原生 SSD 与 50 MB/s HDD 模拟档位、日常交互目标与性能门禁。
- [`LIVE_EDITOR_REALTIME_IO_PLAN.md`](LIVE_EDITOR_REALTIME_IO_PLAN.md)：实时资产读取、动画重定向比例诊断、Live Write Readback 与后续 Blueprint 编辑实施计划。
- [`ANIMATION_RETARGET_SCALE_DIAGNOSIS_20260806.md`](ANIMATION_RETARGET_SCALE_DIAGNOSIS_20260806.md)：Root Scale、Force Root Lock、Compressed Track、Additive Base Pose 与最终 Editor World Pose 的真实 UE5.6 诊断结果。
- [`ANIMATION_SCALE_FIX_TOOL.md`](ANIMATION_SCALE_FIX_TOOL.md)：AnimSequence Root Lock / Root Scale Track 的受控 Plan、Live Apply、最终姿势验证、Undo、Save 和 Verify 工作流。
- [`ANIMATION_SCALE_AUDIT_TOOL.md`](ANIMATION_SCALE_AUDIT_TOOL.md)：显式 AnimSequence 列表的有界批量只读比例审计、分类、分页和取消工作流。
- [`ANIMATION_SCALE_FIX_BATCH_TOOL.md`](ANIMATION_SCALE_FIX_BATCH_TOOL.md)：从固定 Audit Report 和显式候选生成不可变批量比例修复 Plan，复用单资产 Policy / Revision 校验，不执行 Editor 写入。
- [`Plans/ANIMATION_TOOLS_FOLLOWUP_PLAN_20260806.md`](Plans/ANIMATION_TOOLS_FOLLOWUP_PLAN_20260806.md)：动画批量审计、批量修复、Additive/Base Pose、浮空、次级运动和 ModelPreview 接入计划。
- [`BRANCH_WORKTREES.md`](BRANCH_WORKTREES.md)：Realtime I/O 与 Memory/Context 双分支、Worktree、公共协议和合并规范。
- [`BRANCH_WORKTREES_EN.md`](BRANCH_WORKTREES_EN.md)：English dual-branch and Worktree workflow.
- [`Handoffs/MEMORY_CONTEXT_HANDOFF_20260801.md`](Handoffs/MEMORY_CONTEXT_HANDOFF_20260801.md)：Memory/Context 里程碑实现范围、兼容要求与测试门禁。
- [`Handoffs/LIVE_EDITOR_REALTIME_IO_HANDOFF_20260801.md`](Handoffs/LIVE_EDITOR_REALTIME_IO_HANDOFF_20260801.md)：Realtime I/O 里程碑实现范围、测试门禁与后续扩展边界。
- [`Handoffs/LIVE_EDITOR_ANIMATION_TOOLS_HANDOFF_20260806.md`](Handoffs/LIVE_EDITOR_ANIMATION_TOOLS_HANDOFF_20260806.md)：动画比例诊断、受控修复、真实保存结果、当前工作树和后续接手边界。
- [`Handoffs/UEAGENTKIT_0.7.0_RELEASE_HANDOFF_20260803.md`](Handoffs/UEAGENTKIT_0.7.0_RELEASE_HANDOFF_20260803.md)：0.7.0 本地 Release、验证结果、源码产物、MSVC 阻塞与后续性能测试交接。
- [`../prompts/LIVE_EDITOR_REALTIME_IO_LOCAL_AGENT_PROMPT_20260801.md`](../prompts/LIVE_EDITOR_REALTIME_IO_LOCAL_AGENT_PROMPT_20260801.md)：可直接复制给本地代码 Agent 的执行提示词。

- [`COMPARISON_UE_LLM_TOOLKIT.md`](COMPARISON_UE_LLM_TOOLKIT.md)：与 ue-llm-toolkit 的读取、写入、Live Editor 和安全闭环对比。
- [`COMPARISON_UE_LLM_TOOLKIT_EN.md`](COMPARISON_UE_LLM_TOOLKIT_EN.md)：English comparison with ue-llm-toolkit.

## 使用文档

1. [`RELEASE_0.7.0.md`](RELEASE_0.7.0.md)：Realtime Foundation、注册式 Live Write、Schema v3 Memory、Batch/Change Set 与发布验证。
2. [`RELEASE_0.7.0_EN.md`](RELEASE_0.7.0_EN.md)：0.7.0 English release notes。
3. [`RELEASE_0.6.0.md`](RELEASE_0.6.0.md)：Revision-aware Project Memory、证据绑定 Task、审计导出和真实 UE5.6 闭环。
4. [`RELEASE_0.6.0_EN.md`](RELEASE_0.6.0_EN.md)：0.6.0 English release notes。
5. [`RELEASE_0.5.5.md`](RELEASE_0.5.5.md)：0.5.x 日常开发能力、原子事务、验证证据与正式发布收口。
6. [`RELEASE_0.5.5_EN.md`](RELEASE_0.5.5_EN.md)：0.5.5 English release notes。
7. [`RELEASE_0.5.1.md`](RELEASE_0.5.1.md)：0.5.1 中文发布说明、协议补全、高层安全写入与兼容矩阵。
8. [`RELEASE_0.5.1_EN.md`](RELEASE_0.5.1_EN.md)：0.5.1 English release notes。
9. [`RELEASE_0.5.0.md`](RELEASE_0.5.0.md)：0.5.0 固定项目 MCP 工作流发布说明。
10. [`RELEASE_0.5.0_EN.md`](RELEASE_0.5.0_EN.md)：0.5.0 English release notes。
11. [`RELEASE_0.4.4.md`](RELEASE_0.4.4.md)：0.4.4 中文发布说明、正式范围、验证结果和升级步骤。
12. [`RELEASE_0.4.4_EN.md`](RELEASE_0.4.4_EN.md)：0.4.4 English release notes。
13. [`../CHANGELOG.md`](../CHANGELOG.md)：版本变更摘要。
14. [`BUILD_AND_RUN.md`](BUILD_AND_RUN.md)：环境要求、插件构建、资产目录、Blueprint 导出和 SQLite 查询。
15. [`AI_USAGE.md`](AI_USAGE.md)：AI 如何使用资产目录、Blueprint 语义和结构化引用。
16. [`ROADMAP.md`](ROADMAP.md)：版本目标、完成状态和后续边界。
17. [`PARALLEL_AGENT_DEVELOPMENT.md`](PARALLEL_AGENT_DEVELOPMENT.md)：并行 Agent、Worktree 和分层测试规则。
18. [`REFERENCE_POLICY.md`](REFERENCE_POLICY.md)：第三方参考、独立实现和依赖分发规则。
19. [`../spec/BPCTX_FORMAT.md`](../spec/BPCTX_FORMAT.md)：BPCTX/1 文本格式规范。
20. [`../spec/PATCH_SCHEMA.md`](../spec/PATCH_SCHEMA.md)：Patch、Policy、Revision、Dry Run 和 Commit 安全边界。
21. [`../spec/BACKUP_AND_ROLLBACK.md`](../spec/BACKUP_AND_ROLLBACK.md)：Backup Manifest、rollback 与恢复验证。
22. [`../spec/WRITE_FIXTURE_PLAN.md`](../spec/WRITE_FIXTURE_PLAN.md)：声明式 Fixture 生成、重置和独立验证。
23. [`../spec/SCALAR_PATCH_REGRESSION.md`](../spec/SCALAR_PATCH_REGRESSION.md)：标量写入与失败路径真实 UE 回归。
24. [`../spec/MCP_SERVER.md`](../spec/MCP_SERVER.md)：MCP Tool、固定配置、Receipt 和 stdio 契约。
25. [`../spec/INDEX_FRESHNESS.md`](../spec/INDEX_FRESHNESS.md)：SQLite、Revision Export 与磁盘 Package 新鲜度。
26. [`../spec/LIVE_EDITOR_BRIDGE.md`](../spec/LIVE_EDITOR_BRIDGE.md)：localhost IPC、固定工程认证与 Daily Actions。
27. [`../spec/PROJECT_MEMORY.md`](../spec/PROJECT_MEMORY.md)：Revision-aware Project Memory 的独立存储、来源、状态、Scope、Revision、冲突与失效契约。

## 当前版本与开发分支

当前已发布版本为 UE Agent Kit 0.7.0，支持 Unreal Engine 5.6。本地 `main` 已正式集成 Revision-aware Project Memory、Schema v3 Knowledge Tree/Active Work、Realtime Context/Batch/Change Set 和受控 Live Editor Write 基础层；两个长期功能分支继续保留并从 `main` 同步后并行开发。

0.7.0 发布能力包括：

- 独立 Project Memory SQLite/FTS5、六类记录、来源、Scope、Confidence、Revision Set 和状态机。
- 固定工程 MCP/CLI、可审计 JSON 导出、证据摘要和读取时篡改检测。
- Workflow/rollback 证据原样持久化为 Task Record，并在 Revision 变化后自动 stale。

- 通用资产/Blueprint 导出、SQLite/FTS、Revision、引用和四源资产状态。
- Live Editor 状态、日志、编译诊断、导航、验证、Automation 和授权单资产保存。
- Blueprint、标量 Asset、Material Instance、DataTable 和 Data Asset 的受控写入。
- DataTable 多字段、Row 新增/删除/重命名和 Searchable Name 引用影响门禁。
- Data Asset Object/Class、Soft Object/Class、Struct、Array、Set 和 Map 稳定值模型。
- Backup Manifest、独立验证和 Revision-aware rollback。
- Offline 5、Live 32、Workflow 41、Combined 68 Tool；启用 Memory 后为 17、44、53、80；其中 12 个为高层安全写入入口，Memory 另提供 12 个低层与渐进式高层入口。
- `main` 的 `ue_apply_asset_property_live` 当前支持 12 个 Data Asset、Material Instance 和 DataTable Operation；统一 Transaction/Evidence、精确 Undo/Discard、授权保存后独立 Verify、可恢复 Journal 和注册式资产域执行器均已完成，但仍不自动保存或开放任意 UObject。

0.5.x、0.6.0 与 0.7.0 已完成并进入维护。下一阶段由 Realtime I/O 与 Memory/Context 两条长期分支继续扩展 0.8.0-dev Context/Analysis 和高价值编辑能力；大型项目性能框架作为共享横向能力建设，离线导出与 Commandlet 继续承担全项目索引、批处理、独立验证、回滚和 CI。

只读分析路径不修改资产；Blueprint Patch、Asset Patch 与 MCP Commit 仅在明确授权后执行。

测试资产生成方式见 [`../tests/fixtures/README.md`](../tests/fixtures/README.md)；大型项目规模、物理测试工程和交互耗时目标见 [`PERFORMANCE_TEST_PLAN.md`](PERFORMANCE_TEST_PLAN.md)。
