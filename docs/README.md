# UE Agent Kit 文档

本目录保留当前公开版本的用户文档。项目概览和快速示例见仓库根目录 [`README.md`](../README.md)。

## 使用文档

1. [`RELEASE_0.5.1.md`](RELEASE_0.5.1.md)：0.5.1 中文发布说明、协议补全、高层安全写入与兼容矩阵。
2. [`RELEASE_0.5.1_EN.md`](RELEASE_0.5.1_EN.md)：0.5.1 English release notes.
3. [`RELEASE_0.5.0.md`](RELEASE_0.5.0.md)：0.5.0 固定项目 MCP 工作流发布说明。
4. [`RELEASE_0.5.0_EN.md`](RELEASE_0.5.0_EN.md)：0.5.0 English release notes.
5. [`RELEASE_0.4.4.md`](RELEASE_0.4.4.md)：0.4.4 中文发布说明、正式范围、验证结果和升级步骤。
6. [`RELEASE_0.4.4_EN.md`](RELEASE_0.4.4_EN.md)：0.4.4 English release notes.
7. [`../CHANGELOG.md`](../CHANGELOG.md)：版本变更摘要。
8. [`BUILD_AND_RUN.md`](BUILD_AND_RUN.md)：环境要求、插件构建、通用资产目录、Blueprint 导出和 SQLite 查询。
9. [`AI_USAGE.md`](AI_USAGE.md)：AI 如何使用资产目录、Blueprint 语义和结构化引用。
10. [`ROADMAP.md`](ROADMAP.md)：版本目标、完成状态和后续边界。
11. [`PARALLEL_AGENT_DEVELOPMENT.md`](PARALLEL_AGENT_DEVELOPMENT.md)：Sol/Luna 分工、Worktree、文件所有权和分层测试规则。
12. [`REFERENCE_POLICY.md`](REFERENCE_POLICY.md)：第三方参考、独立实现和依赖分发规则。
13. [`../spec/BPCTX_FORMAT.md`](../spec/BPCTX_FORMAT.md)：BPCTX/1 文本格式规范。
14. [`../spec/PATCH_SCHEMA.md`](../spec/PATCH_SCHEMA.md)：Patch Schema、Policy、Revision 和校验、Dry Run、备份和显式 Commit 安全边界。
15. [`../spec/BACKUP_AND_ROLLBACK.md`](../spec/BACKUP_AND_ROLLBACK.md)：Backup Manifest、独立 rollback、审计回执和恢复后验证。
16. [`../spec/WRITE_FIXTURE_PLAN.md`](../spec/WRITE_FIXTURE_PLAN.md)：声明式写入测试资产生成、重置和独立验证。
17. [`../spec/SCALAR_PATCH_REGRESSION.md`](../spec/SCALAR_PATCH_REGRESSION.md)：完整标量写入、独立重载和失败路径回归。
18. [`../spec/MCP_SERVER.md`](../spec/MCP_SERVER.md)：MCP Tool、固定配置、Receipt、stdio 和完整安全边界。
19. [`../spec/INDEX_FRESHNESS.md`](../spec/INDEX_FRESHNESS.md)：SQLite、Revision Export、磁盘 Package 三源新鲜度与安全重载。
20. [`../spec/LIVE_EDITOR_BRIDGE.md`](../spec/LIVE_EDITOR_BRIDGE.md)：localhost IPC、固定工程认证握手、实时读取与 Daily Actions。

## 当前版本与开发分支

当前已发布版本为 UE Agent Kit 0.5.1，支持 Unreal Engine 5.6。`main` 分支已经包含尚未发布的 0.5.2–0.5.4 能力。

当前开发分支能力包括：

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
