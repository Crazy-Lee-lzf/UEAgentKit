# UE Agent Kit 文档

这里收录面向 **使用者、集成者和 AI Agent** 的公开文档。内部开发计划、验收流水账和交接记录不在发布树中维护。

## 从这里开始

- [README](../README.md) / [English](../README_EN.md)
- [AI Agent 快速接入](AGENT_QUICKSTART.md) / [English](AGENT_QUICKSTART_EN.md)
- [当前能力状态](PROJECT_STATUS.md) / [English](PROJECT_STATUS_EN.md)
- [构建与运行](BUILD_AND_RUN.md)
- [专用 Tool 索引](TOOL_CATALOG.md)

## Release Notes

- **0.9.0 Developer Preview 2（候选，未发布）**：[中文](RELEASE_0.9.0_DP2.md) / [English](RELEASE_0.9.0_DP2_EN.md)
- **0.9.0 Developer Preview 1（已发布，2026-10-30）**：[中文](RELEASE_0.9.0_PREVIEW.md) / [English](RELEASE_0.9.0_PREVIEW_EN.md)
- [0.8.0](RELEASE_0.8.0.md) / [English](RELEASE_0.8.0_EN.md)
- [0.7.0](RELEASE_0.7.0.md) / [English](RELEASE_0.7.0_EN.md)
- [0.6.0](RELEASE_0.6.0.md) / [English](RELEASE_0.6.0_EN.md)
- [0.5.5](RELEASE_0.5.5.md) / [English](RELEASE_0.5.5_EN.md)
- [0.5.1](RELEASE_0.5.1.md) / [English](RELEASE_0.5.1_EN.md)
- [0.5.0](RELEASE_0.5.0.md) / [English](RELEASE_0.5.0_EN.md)
- [0.4.4](RELEASE_0.4.4.md) / [English](RELEASE_0.4.4_EN.md)

## 使用与配置

- [AI 使用指南](AI_USAGE.md)
- [程序开发工作流](DEVELOPER_WORKFLOWS.md) / [English](DEVELOPER_WORKFLOWS_EN.md)
- [项目级配置与 Write Policy](PROJECT_LEVEL_CONFIG.md)
- [公开 Roadmap](ROADMAP.md) / [English](ROADMAP_EN.md)

## Blueprint 实验能力

默认关闭、需要显式双门禁，不属于稳定发布面，也不代表生产授权。

- [Blueprint 实验用法](BLUEPRINT_AGENT_E2E_USAGE.md) / [English](BLUEPRINT_AGENT_E2E_USAGE_EN.md)

## 架构与安全

- [Project Memory 架构](MEMORY_ARCHITECTURE.md) / [English](MEMORY_ARCHITECTURE_EN.md)
- [Asset Index Schema v2 与 CJK 迁移](INDEX_SCHEMA_V2_MIGRATION.md)
- [查询模式：Offline / Live / Auto 差距评估](QUERY_MODES_OFFLINE_LIVE_AUTO.md)
- [AI-native UE Editor](AI_NATIVE_UE_EDITOR.md) / [English](AI_NATIVE_UE_EDITOR_EN.md)
- [Reference Policy](REFERENCE_POLICY.md)
- [C++ Code Knowledge](CODE_KNOWLEDGE.md)

核心协议与机器契约位于 [`spec/`](../spec/)：

- [`MCP_SERVER.md`](../spec/MCP_SERVER.md)
- [`LIVE_EDITOR_BRIDGE.md`](../spec/LIVE_EDITOR_BRIDGE.md)
- [`INDEX_FRESHNESS.md`](../spec/INDEX_FRESHNESS.md)
- [`PATCH_SCHEMA.md`](../spec/PATCH_SCHEMA.md)
- [`BACKUP_AND_ROLLBACK.md`](../spec/BACKUP_AND_ROLLBACK.md)

专用动画诊断、修复与重定向工具见 [Tool 索引](TOOL_CATALOG.md)。
