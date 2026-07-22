# UE Agent Kit 文档

本目录保留当前公开版本的用户文档。项目概览和快速示例见仓库根目录 [`README.md`](../README.md)。

## 使用文档

1. [`BUILD_AND_RUN.md`](BUILD_AND_RUN.md)：环境要求、插件构建、通用资产目录、Blueprint 导出和 SQLite 查询。
2. [`AI_USAGE.md`](AI_USAGE.md)：AI 如何使用资产目录、Blueprint 语义和结构化引用。
3. [`ROADMAP.md`](ROADMAP.md)：0.4.0、0.4.x 和 0.5.0 的公开版本目标与边界。
4. [`REFERENCE_POLICY.md`](REFERENCE_POLICY.md)：第三方参考、独立实现和依赖分发规则。
5. [`../spec/BPCTX_FORMAT.md`](../spec/BPCTX_FORMAT.md)：BPCTX/1 文本格式规范。
6. [`../spec/PATCH_SCHEMA.md`](../spec/PATCH_SCHEMA.md)：Patch Schema、Policy、Revision 和校验、Dry Run、备份和显式 Commit 安全边界。
7. [`../spec/BACKUP_AND_ROLLBACK.md`](../spec/BACKUP_AND_ROLLBACK.md)：Backup Manifest、独立 rollback、审计回执和恢复后验证。
8. [`../spec/WRITE_FIXTURE_PLAN.md`](../spec/WRITE_FIXTURE_PLAN.md)：声明式写入测试资产生成、重置和独立验证。
9. [`../spec/SCALAR_PATCH_REGRESSION.md`](../spec/SCALAR_PATCH_REGRESSION.md)：完整标量写入、独立重载和失败路径回归。

## 当前版本

UE Agent Kit 0.4.4 支持 Unreal Engine 5.6，当前公开能力包括：

- 通用 UE 资产目录、Asset Registry Tags、Revision 和依赖导出。
- Blueprint 只读语义分析。
- SQLite/FTS5 项目索引、Asset Class 筛选和正反向引用查询。
- Blueprint Patch 覆盖八类 Blueprint；Asset Patch 已验证通用标量属性、InputAction Enum、Material Instance Global Scalar/Vector/Texture/Static Switch，以及 DataTable 单 Row、单顶层标量字段的精确白名单写入。
- 0.4.1 增加 Commit Backup Manifest、默认 Dry Run 的独立 rollback、回滚前安全副本、唯一回执和独立 UE 恢复验证。
- 0.4.2 增加 Write Fixture Plan、Create/Reset、安全目标边界和独立 UE 重载验证。
- 0.4.3 增加原生 Scalar Fixture、11 类标量完整 Dry Run/Commit/重载矩阵，以及 6 类失败路径零写入回归。
- 0.4.4 增加 Dirty Package、真实 Sidecar 和 SaveFailure 备份/磁盘保护回归，将失败矩阵扩展到 9 类。

只读分析路径不修改资产；Blueprint Patch 与 Asset Patch 仅在明确授权后执行 Dry Run 或显式 Commit。

测试资产生成方式见 [`../tests/fixtures/README.md`](../tests/fixtures/README.md)。
