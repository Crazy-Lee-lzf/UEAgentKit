# UE Agent Kit 文档

本目录保留当前公开版本与 `main` 开发快照的用户文档。项目概览和快速示例见仓库根目录 [`README.md`](../README.md)。

## 当前状态与对比

- [`PROJECT_STATUS.md`](PROJECT_STATUS.md)：当前已实现能力、明确未实现能力、P0–P3 优先级和后续方向。
- [`PROJECT_STATUS_EN.md`](PROJECT_STATUS_EN.md)：English project status.
- [`MEMORY_ARCHITECTURE.md`](MEMORY_ARCHITECTURE.md)：分层知识树、Active Work、渐进式披露、MCP/Skill 分工和多人共享知识服务设计。
- [`MEMORY_ARCHITECTURE_EN.md`](MEMORY_ARCHITECTURE_EN.md)：English layered memory and collaboration architecture.

- [`COMPARISON_UE_LLM_TOOLKIT.md`](COMPARISON_UE_LLM_TOOLKIT.md)：与 ue-llm-toolkit 的读取、写入、Live Editor 和安全闭环对比。
- [`COMPARISON_UE_LLM_TOOLKIT_EN.md`](COMPARISON_UE_LLM_TOOLKIT_EN.md)：English comparison with ue-llm-toolkit.

## 使用文档

1. [`RELEASE_0.6.0.md`](RELEASE_0.6.0.md)：Revision-aware Project Memory、证据绑定 Task、审计导出和真实 UE5.6 闭环。
2. [`RELEASE_0.6.0_EN.md`](RELEASE_0.6.0_EN.md)：0.6.0 English release notes。
3. [`RELEASE_0.5.5.md`](RELEASE_0.5.5.md)：0.5.x 日常开发能力、原子事务、验证证据与正式发布收口。
4. [`RELEASE_0.5.5_EN.md`](RELEASE_0.5.5_EN.md)：0.5.5 English release notes。
5. [`RELEASE_0.5.1.md`](RELEASE_0.5.1.md)：0.5.1 中文发布说明、协议补全、高层安全写入与兼容矩阵。
6. [`RELEASE_0.5.1_EN.md`](RELEASE_0.5.1_EN.md)：0.5.1 English release notes。
7. [`RELEASE_0.5.0.md`](RELEASE_0.5.0.md)：0.5.0 固定项目 MCP 工作流发布说明。
8. [`RELEASE_0.5.0_EN.md`](RELEASE_0.5.0_EN.md)：0.5.0 English release notes。
9. [`RELEASE_0.4.4.md`](RELEASE_0.4.4.md)：0.4.4 中文发布说明、正式范围、验证结果和升级步骤。
10. [`RELEASE_0.4.4_EN.md`](RELEASE_0.4.4_EN.md)：0.4.4 English release notes。
11. [`../CHANGELOG.md`](../CHANGELOG.md)：版本变更摘要。
12. [`BUILD_AND_RUN.md`](BUILD_AND_RUN.md)：环境要求、插件构建、资产目录、Blueprint 导出和 SQLite 查询。
13. [`AI_USAGE.md`](AI_USAGE.md)：AI 如何使用资产目录、Blueprint 语义和结构化引用。
14. [`ROADMAP.md`](ROADMAP.md)：版本目标、完成状态和后续边界。
15. [`PARALLEL_AGENT_DEVELOPMENT.md`](PARALLEL_AGENT_DEVELOPMENT.md)：并行 Agent、Worktree 和分层测试规则。
16. [`REFERENCE_POLICY.md`](REFERENCE_POLICY.md)：第三方参考、独立实现和依赖分发规则。
17. [`../spec/BPCTX_FORMAT.md`](../spec/BPCTX_FORMAT.md)：BPCTX/1 文本格式规范。
18. [`../spec/PATCH_SCHEMA.md`](../spec/PATCH_SCHEMA.md)：Patch、Policy、Revision、Dry Run 和 Commit 安全边界。
19. [`../spec/BACKUP_AND_ROLLBACK.md`](../spec/BACKUP_AND_ROLLBACK.md)：Backup Manifest、rollback 与恢复验证。
20. [`../spec/WRITE_FIXTURE_PLAN.md`](../spec/WRITE_FIXTURE_PLAN.md)：声明式 Fixture 生成、重置和独立验证。
21. [`../spec/SCALAR_PATCH_REGRESSION.md`](../spec/SCALAR_PATCH_REGRESSION.md)：标量写入与失败路径真实 UE 回归。
22. [`../spec/MCP_SERVER.md`](../spec/MCP_SERVER.md)：MCP Tool、固定配置、Receipt 和 stdio 契约。
23. [`../spec/INDEX_FRESHNESS.md`](../spec/INDEX_FRESHNESS.md)：SQLite、Revision Export 与磁盘 Package 新鲜度。
24. [`../spec/LIVE_EDITOR_BRIDGE.md`](../spec/LIVE_EDITOR_BRIDGE.md)：localhost IPC、固定工程认证与 Daily Actions。
25. [`../spec/PROJECT_MEMORY.md`](../spec/PROJECT_MEMORY.md)：Revision-aware Project Memory 的独立存储、来源、状态、Scope、Revision、冲突与失效契约。

## 当前版本与开发分支

当前已发布版本为 UE Agent Kit 0.6.0，支持 Unreal Engine 5.6。Revision-aware Project Memory 已完成；`main` 已增加首个 Live Editor Write，并同时推进 Live Write 基础层和 0.7.0 Context/Analysis。

0.6.0 发布能力包括：

- 独立 Project Memory SQLite/FTS5、六类记录、来源、Scope、Confidence、Revision Set 和状态机。
- 固定工程 MCP/CLI、可审计 JSON 导出、证据摘要和读取时篡改检测。
- Workflow/rollback 证据原样持久化为 Task Record，并在 Revision 变化后自动 stale。

- 通用资产/Blueprint 导出、SQLite/FTS、Revision、引用和四源资产状态。
- Live Editor 状态、日志、编译诊断、导航、验证、Automation 和授权单资产保存。
- Blueprint、标量 Asset、Material Instance、DataTable 和 Data Asset 的受控写入。
- DataTable 多字段、Row 新增/删除/重命名和 Searchable Name 引用影响门禁。
- Data Asset Object/Class、Soft Object/Class、Struct、Array、Set 和 Map 稳定值模型。
- Backup Manifest、独立验证和 Revision-aware rollback。
- Offline 5、Live 23、Workflow 26、Combined 44 Tool；启用 Memory 后为 12、30、33、51；其中 12 个为高层安全写入入口。
- `main` 新增 `ue_apply_asset_property_live`：对已打开且 Clean 的非 Blueprint 资产执行顶层标量内存修改，进入 Undo 栈并标记 Dirty，但不自动保存。

0.5.x 与 0.6.0 已完成并进入维护。当前 P0 是 Live Transaction/Undo/Discard/Authorized Save/Evidence 基础层；P1A 是 Memory Knowledge Tree、Active Work 和渐进式披露；P1B 是 0.7.0 Context Pack、值来源、执行链、影响分析和语义 Diff。

只读分析路径不修改资产；Blueprint Patch、Asset Patch 与 MCP Commit 仅在明确授权后执行。

测试资产生成方式见 [`../tests/fixtures/README.md`](../tests/fixtures/README.md)。
