# UE Agent Kit 文档

这里收录面向 **使用者、集成者和贡献者** 的产品文档。内部开发计划、Agent 交接记录、阶段验收流水账不属于公开产品文档，不在发布树中维护。

## 快速开始

- [AI Agent 快速接入（推荐）](AGENT_QUICKSTART.md) / [English](AGENT_QUICKSTART_EN.md)
- [常用程序开发工作流](DEVELOPER_WORKFLOWS.md) / [English](DEVELOPER_WORKFLOWS_EN.md)

- [README](../README.md) / [English README](../README_EN.md)
- [构建与运行](BUILD_AND_RUN.md)
- [AI 使用说明](AI_USAGE.md)
- [项目级配置](PROJECT_LEVEL_CONFIG.md)
- [当前能力状态](PROJECT_STATUS.md) / [English](PROJECT_STATUS_EN.md)
- [公开 Roadmap](ROADMAP.md) / [English](ROADMAP_EN.md)

## Release Notes

- **0.9.0 Developer Preview 1（已发布的预览版）**：[中文](RELEASE_0.9.0_PREVIEW.md) / [English](RELEASE_0.9.0_PREVIEW_EN.md)

- [0.8.0](RELEASE_0.8.0.md) / [English](RELEASE_0.8.0_EN.md)
- [0.7.0](RELEASE_0.7.0.md) / [English](RELEASE_0.7.0_EN.md)
- [0.6.0](RELEASE_0.6.0.md) / [English](RELEASE_0.6.0_EN.md)
- [0.5.5](RELEASE_0.5.5.md) / [English](RELEASE_0.5.5_EN.md)
- [0.5.1](RELEASE_0.5.1.md) / [English](RELEASE_0.5.1_EN.md)
- [0.5.0](RELEASE_0.5.0.md) / [English](RELEASE_0.5.0_EN.md)
- [0.4.4](RELEASE_0.4.4.md) / [English](RELEASE_0.4.4_EN.md)

## 架构与安全

- [Memory Architecture](MEMORY_ARCHITECTURE.md) / [English](MEMORY_ARCHITECTURE_EN.md)
- [Asset Index Schema v2 与 CJK 迁移](INDEX_SCHEMA_V2_MIGRATION.md)
- [AI-native UE Editor](AI_NATIVE_UE_EDITOR.md) / [English](AI_NATIVE_UE_EDITOR_EN.md)
- [Reference Policy](REFERENCE_POLICY.md)
- [C++ Code Knowledge](CODE_KNOWLEDGE.md)

核心协议与机器契约位于 [`spec/`](../spec/)：

- [`MCP_SERVER.md`](../spec/MCP_SERVER.md)
- [`LIVE_EDITOR_BRIDGE.md`](../spec/LIVE_EDITOR_BRIDGE.md)
- [`INDEX_FRESHNESS.md`](../spec/INDEX_FRESHNESS.md)
- [`PATCH_SCHEMA.md`](../spec/PATCH_SCHEMA.md)
- [`BACKUP_AND_ROLLBACK.md`](../spec/BACKUP_AND_ROLLBACK.md)

## Blueprint 实验功能

- [隔离工程 GraphEdit 用法](BLUEPRINT_AGENT_E2E_USAGE.md)（默认关闭、非生产功能）

## 专用工具文档

- [AI Agent 专用 Tool 索引与安全调用规则](TOOL_CATALOG.md)

本目录中 `*_TOOL.md` 文档描述可公开使用的动画诊断、修复、重定向和二次运动等专用 Tool。具体可用性以当前版本的 MCP capabilities 和对应 Tool 文档为准。
