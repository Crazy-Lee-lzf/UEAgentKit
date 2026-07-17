# UE Agent Kit 文档

本目录保留当前公开版本的用户文档。项目概览和快速示例见仓库根目录 [`README.md`](../README.md)。

## 使用文档

1. [`BUILD_AND_RUN.md`](BUILD_AND_RUN.md)：环境要求、插件构建、通用资产目录、Blueprint 导出和 SQLite 查询。
2. [`AI_USAGE.md`](AI_USAGE.md)：AI 如何使用资产目录、Blueprint 语义和结构化引用。
3. [`REFERENCE_POLICY.md`](REFERENCE_POLICY.md)：第三方参考、独立实现和依赖分发规则。
4. [`../spec/BPCTX_FORMAT.md`](../spec/BPCTX_FORMAT.md)：BPCTX/1 文本格式规范。
5. [`../spec/PATCH_SCHEMA.md`](../spec/PATCH_SCHEMA.md)：Patch Schema、Policy、Revision 和纯校验安全边界。

## 当前版本

UE Agent Kit 0.3.0 支持 Unreal Engine 5.6，当前公开能力包括：

- 通用 UE 资产目录、Asset Registry Tags、Revision 和依赖导出。
- Blueprint 只读语义分析。
- SQLite/FTS5 项目索引、Asset Class 筛选和正反向引用查询。
- 声明式 Blueprint Patch 的 Policy、Revision 和导出快照纯校验。

工具不修改或保存 `.uasset`；Patch Baseline 也不加载或修改 UObject。

测试资产生成方式见 [`../tests/fixtures/README.md`](../tests/fixtures/README.md)。
