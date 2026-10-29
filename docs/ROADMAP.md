# UE Agent Kit Roadmap

当前最新公开预发布为 **0.9.0 Developer Preview 1（v0.9.0-dev1）**，上一个稳定版为 **v0.8.0**。以下是后续产品方向，不代表已经随 DP1 交付或有固定交付日期。

## P0：Blueprint 核心读写与 PIE 诊断

- 完善 Blueprint 结构和调用关系读取，以及**默认关闭**的有界节点修改。
- 通过 PIE 读取明确的 Server/Client World、Actor 与 Component，帮助定位程序逻辑问题。
- 加强 C++/Blueprint 之间的来源关联和读取新鲜度标记；写入始终需要明确授权和验证。

## P1：常用资产（DataTable / DataAsset 优先）

- **DataTable 和玩法 DataAsset**：优先保障必要的配置读取与受控写入。
- **Animation Blueprint、Montage、BlendSpace、Curve**：优先读取，写入仅按重复的程序需求扩展。
- **Level/World**：按目标 Actor 或区域查询；不以全图导出或自动关卡编辑为目标。
- **Material、Mesh、Niagara 等**：优先读取关键参数、结构摘要和引用；不默认开发完整图编辑器。

## 长期原则

只做真实项目需要的有界能力；优先复用现有 Reader、索引和安全编辑流程。AI Agent 不应绕过权限、自动保存所有资产或替用户完成最终 P4 Submit。

实际工具列表与权限，以已部署版本的 `ue_get_capabilities` 为准。
