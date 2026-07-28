# UE Agent Kit 文档

本目录保留当前公开版本的用户文档。项目概览和快速示例见仓库根目录 [`README.md`](../README.md)。

## 使用文档

1. [`RELEASE_0.5.5.md`](RELEASE_0.5.5.md)：0.5.x 日常开发能力、原子事务、验证证据与正式发布收口。
2. [`RELEASE_0.5.5_EN.md`](RELEASE_0.5.5_EN.md)：0.5.5 English release notes。
3. [`RELEASE_0.5.1.md`](RELEASE_0.5.1.md)：0.5.1 中文发布说明、协议补全、高层安全写入与兼容矩阵。
4. [`RELEASE_0.5.1_EN.md`](RELEASE_0.5.1_EN.md)：0.5.1 English release notes。
5. [`RELEASE_0.5.0.md`](RELEASE_0.5.0.md)：0.5.0 固定项目 MCP 工作流发布说明。
6. [`RELEASE_0.5.0_EN.md`](RELEASE_0.5.0_EN.md)：0.5.0 English release notes。
7. [`RELEASE_0.4.4.md`](RELEASE_0.4.4.md)：0.4.4 中文发布说明、正式范围、验证结果和升级步骤。
8. [`RELEASE_0.4.4_EN.md`](RELEASE_0.4.4_EN.md)：0.4.4 English release notes。
9. [`../CHANGELOG.md`](../CHANGELOG.md)：版本变更摘要。
10. [`BUILD_AND_RUN.md`](BUILD_AND_RUN.md)：环境要求、插件构建、资产目录、Blueprint 导出和 SQLite 查询。
11. [`AI_USAGE.md`](AI_USAGE.md)：AI 如何使用资产目录、Blueprint 语义和结构化引用。
12. [`ROADMAP.md`](ROADMAP.md)：版本目标、完成状态和后续边界。
13. [`PARALLEL_AGENT_DEVELOPMENT.md`](PARALLEL_AGENT_DEVELOPMENT.md)：并行 Agent、Worktree 和分层测试规则。
14. [`REFERENCE_POLICY.md`](REFERENCE_POLICY.md)：第三方参考、独立实现和依赖分发规则。
15. [`../spec/BPCTX_FORMAT.md`](../spec/BPCTX_FORMAT.md)：BPCTX/1 文本格式规范。
16. [`../spec/PATCH_SCHEMA.md`](../spec/PATCH_SCHEMA.md)：Patch、Policy、Revision、Dry Run 和 Commit 安全边界。
17. [`../spec/BACKUP_AND_ROLLBACK.md`](../spec/BACKUP_AND_ROLLBACK.md)：Backup Manifest、rollback 与恢复验证。
18. [`../spec/WRITE_FIXTURE_PLAN.md`](../spec/WRITE_FIXTURE_PLAN.md)：声明式 Fixture 生成、重置和独立验证。
19. [`../spec/SCALAR_PATCH_REGRESSION.md`](../spec/SCALAR_PATCH_REGRESSION.md)：标量写入与失败路径真实 UE 回归。
20. [`../spec/MCP_SERVER.md`](../spec/MCP_SERVER.md)：MCP Tool、固定配置、Receipt 和 stdio 契约。
21. [`../spec/INDEX_FRESHNESS.md`](../spec/INDEX_FRESHNESS.md)：SQLite、Revision Export 与磁盘 Package 新鲜度。
22. [`../spec/LIVE_EDITOR_BRIDGE.md`](../spec/LIVE_EDITOR_BRIDGE.md)：localhost IPC、固定工程认证与 Daily Actions。

## 当前版本与开发分支

当前已发布版本为 UE Agent Kit 0.5.5，支持 Unreal Engine 5.6。0.5.x 已完成，`main` 进入 0.6.0 Revision-aware Project Memory 的后续开发阶段。

0.5.5 发布能力包括：

- 通用资产/Blueprint 导出、SQLite/FTS、Revision、引用和四源资产状态。
- Live Editor 状态、日志、编译诊断、导航、验证、Automation 和授权单资产保存。
- Blueprint、标量 Asset、Material Instance、DataTable 和 Data Asset 的受控写入。
- DataTable 多字段、Row 新增/删除/重命名和 Searchable Name 引用影响门禁。
- Data Asset Object/Class、Soft Object/Class、Struct、Array、Set 和 Map 稳定值模型。
- Backup Manifest、独立验证和 Revision-aware rollback。
- Offline 5、Live 23、Workflow 25、Combined 43 Tool；其中 12 个为高层安全写入入口。

0.5.x 剩余重点为 Material 参数报告统一、验证证据绑定、单资产多 Operation 原子事务和正式发布收口。

只读分析路径不修改资产；Blueprint Patch、Asset Patch 与 MCP Commit 仅在明确授权后执行。

测试资产生成方式见 [`../tests/fixtures/README.md`](../tests/fixtures/README.md)。
