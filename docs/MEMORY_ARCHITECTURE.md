# Project Memory 分层架构与协作设计

更新时间：2026-08-02

> 当前 `feature/memory-context` 已实现单人、本地、固定项目版 Schema v3 MVP：Knowledge Tree、Active Work、0–4 级渐进式披露、按需 Evidence 和五个高层 MCP Tool。0.6.0 正式版仍使用 Schema v2；Shared Knowledge Service、团队权限与乐观并发仍属于后续范围。

## 1. 设计目标

Project Memory 后续需要同时解决四个问题：

1. 让知识按项目结构组织，而不是只在平面记录中全文搜索。
2. 采用渐进式披露，避免每次任务加载整个知识库。
3. 把长期知识与正在进行的任务、TODO 和阻塞项分开。
4. 让维护规则由 MCP Server 强制执行，而不是依赖 Agent 自觉遵守。

目标状态是：

```text
复杂性保留在服务内部
Agent 只看到少量高层 Tool
普通任务只读取少量摘要
确定性 Task/Revision/Evidence 自动维护
较弱 Agent 也不容易污染知识库
```

## 2. 四个核心概念

### 2.1 Knowledge Tree

知识树回答“这条知识属于项目的哪里”。节点使用稳定 Path、Parent 和任意深度层级，而不是固定 `level1/level2/level3` 字段。

默认结构可以从三层开始：

```text
/project                                  项目概览
/project/character                        角色系统
/project/character/skills                 技能系统
/project/character/skills/character-a     角色 A
/project/character/skills/character-a/skill-1
```

推荐的节点类别包括：

```text
project
system
feature
component
entity
implementation
```

节点类别只用于辅助，不限制树深度。

### 2.2 Knowledge Record

记录类型回答“这是什么性质的知识”。现有六类记录继续保留：

```text
projectFact
projectRule
decisionRecord
knownIssue
taskRecord
runtimeEvidence
```

后续记录通过 `nodeId` 绑定知识树节点。例如：

```text
Path: /project/weapons/damage
Type: decisionRecord
Status: valid
```

知识树是主导航，Record Type 用于审计、状态和来源管理，不再要求普通 Agent 先理解六种内部类型再决定如何检索。

### 2.3 Active Work

当前工作必须与长期知识分离。Active Work 保存：

```text
current objective
in progress
todo
blocked
pending decision
recently completed
```

建议状态：

```text
planned
in_progress
blocked
done
cancelled
```

Work Item 至少包含：

```text
workItemId
projectKey
title
status
priority
description
nextAction
blockedReason
relatedNodeIds
relatedAssets
owner
createdAtUtc
updatedAtUtc
completedAtUtc
```

任务完成后执行成果沉淀：

```text
Active Work 完成
→ 提取稳定结论
→ 更新对应 Knowledge Node
→ 自动保存 Task Evidence
→ 归档 Work Item
```

临时排查过程、猜测和短期 TODO 不直接进入长期节点摘要。

### 2.4 Evidence

Evidence 保存结论的可验证依据，但默认不直接注入模型上下文。它可以引用：

```text
Patch
Backup Manifest
Validation Report
Automation Report
Log
Blueprint Node / Graph
Revision
External Document
```

节点摘要和详细记录只保存必要说明及稳定引用，原始证据在最后一级按需展开。

## 3. 渐进式披露

Memory 查询分五级返回：

### Level 0：索引

只返回 Path、Title、一句话摘要、状态、子节点数量和是否存在 Active Work。

### Level 1：节点摘要

返回系统或功能的核心概念，不展开具体资产、类和证据。

### Level 2：实现概览

返回主要类、资产、数据流、入口、依赖和当前 Known Issue。

### Level 3：详细记录

返回 Rule、Decision、Finding、Known Issue、Revision 和状态。

### Level 4：原始证据

最后才展开 Patch、日志、节点、Validation Report 和完整 Artifact。

默认预算建议：

```text
Project Profile        300–500 tokens
System Summary         300–600 tokens
Implementation         400–800 tokens
Related Records        300–1000 tokens
Active Work            100–300 tokens
```

普通任务的 Memory 上下文目标控制在约 1000–2500 Token。服务端必须强制限制返回条数、展开深度和预算；Skill 中的文字提醒不能替代服务端门禁。

## 4. MCP 与 Skill 的职责

采用 **MCP 为主体、Skill 为薄层引导**。

```text
Agent
├─ 单一薄 Skill：说明什么时候读写以及调用顺序
└─ 本地 MCP Server：执行检索、预算、维护、校验和写入
```

### MCP Server 负责

- Knowledge Tree 和 Active Work 的存储。
- 渐进式披露、Token Budget 和分页。
- Parent/Child、Path 和 Project Scope 校验。
- 重复检测、冲突并存、Supersede 和 Revision stale。
- Task、Patch、Validation、Rollback Evidence 自动绑定。
- 默认过滤 `stale` 和 `superseded`。
- 返回结构化 `nextActions`，帮助较弱 Agent 继续检索。

### Skill 只负责

- 先读 Project Profile，再定位相关系统。
- 只按需展开实现和证据。
- 不记录普通对话或临时推测。
- 当前任务写入 Active Work，稳定结果才沉淀到长期知识。

不建议把读取、写入、维护、TODO 分成多个长 Skill。日常只保留一个约 400–800 Token 的 `project-memory` Skill；审计和迁移可以使用按需加载的专用 Skill。

## 5. 计划中的高层 MCP Tool

以下名称是后续设计目标，当前 0.6.0 尚未注册：

```text
memory_get_context
memory_expand_node
memory_get_evidence
memory_update_knowledge
memory_update_work
```

### `memory_get_context`

绝大多数任务的第一入口。根据 Query、固定 Project、当前资产和预算返回：

- 必要的 Project Profile 片段。
- 匹配知识路径。
- 相关系统摘要。
- 相关 Active Work。
- 推荐的下一步展开动作。

### `memory_expand_node`

按一个稳定 Path、指定深度和 Detail Level 展开子节点或实现摘要。

### `memory_get_evidence`

只在需要证明结论时读取指定 Record 或 Artifact 的证据。

### `memory_update_knowledge`

统一处理节点新增、摘要更新、稳定结论写入、重复检测和确认要求。Agent 不直接操作数据库表，也不手工选择 Revision SHA。

### `memory_update_work`

通过 `start/add_todo/block/complete/cancel/set_next_action` 等 Action 维护当前工作。

确定性的 Task Record、Revision Set 和 Workflow Evidence 不提供自由手工写入入口，由现有 UEAgentKit 工作流自动生成。

## 6. 单人部署

单人阶段保持：

```text
Agent
→ 本地 MCP Server
→ 本地 SQLite / Memory DB
→ localhost Editor Bridge
→ 本机 UE Editor
```

本地 MCP 同时组合离线索引、Memory、当前 Editor 状态和写入工作流。Agent 不需要同时连接多个 MCP。

## 7. 多人协作架构

多人协作采用混合架构：**每名开发者一个本地 MCP，整个团队一个共享知识服务**。

```text
Developer A: Agent → Local MCP A → Local Plugin/Editor
                              └→ Shared Knowledge Service
Developer B: Agent → Local MCP B → Local Plugin/Editor
                              └→ Shared Knowledge Service
```

实时 UE 状态必须留在本地：

- 当前打开资产、Graph 和选择。
- Dirty Package、PIE/SIE 和 Output Log。
- 本地 Workspace、Policy、Receipt 和 Editor Session。
- 尚未保存的内存修改。

共享服务保存：

- Project/Team Knowledge Tree。
- 公共规则、决策、Known Issue 和稳定实现结论。
- 团队 Active Work、负责人、阻塞和 Changelist。
- Task、Validation 和 Revision 审计引用。

推荐 Scope：

```text
/project/...     项目共享
/team/...        团队共享
/user/...        个人私有
/session/...     当前本地会话
```

## 8. 共享存储与并发

不允许把一个可写 SQLite 文件放到 NAS 后由所有开发者直接并发访问。

推荐：

```text
Local SQLite      资产索引、缓存、个人与 Session 数据
PostgreSQL/API    项目共享知识、团队任务与审计
```

共享节点更新使用乐观并发：

```text
nodeId
expectedRevision
newContent
```

若 `expectedRevision` 与服务端当前 Revision 不一致，返回 `knowledge-conflict`，禁止 Last Write Wins 静默覆盖。Project Profile 和系统级摘要必须支持人工确认或显式合并。

## 9. Schema v3 实现状态

`feature/memory-context` 已在 Schema v2 上追加以下本地表和绑定：

```text
knowledge_nodes
memory_records.node_id
active_work_items
active_work_node_links
active_work_asset_links
active_work_todos
```

迁移原则：

1. Schema v2 数据库原地升级到 v3，现有 Record ID、内容摘要、Evidence 摘要、Revision Set、Scope、Relation、Artifact 和状态保持不变。
2. 旧记录迁移后保持 `node_id IS NULL`，不自动猜测归属节点。
3. 新建数据库直接创建 Schema v3；重复打开不会重复创建表、列或迁移数据。
4. Knowledge Node 使用规范化绝对 Path、同项目 Parent 和无环约束；根节点固定为 `/project`。
5. Active Work 使用正规化 Node/Asset 关联表，不把可查询关系只存入 JSON。

## 10. 单人版 MVP 状态

已实现：

1. 任意深度 Knowledge Tree 与 Project Profile 根节点。
2. 现有六类 Record 可选绑定 Knowledge Node。
3. 独立 Active Work、TODO、阻塞和下一步状态机。
4. `ue_memory_get_context`、`ue_memory_expand_node` 和按需 Evidence。
5. 0–4 级渐进式披露、确定性字符预算、默认状态过滤和结构化 `nextActions`。
6. 固定项目高层 Knowledge/Work 更新 Tool，以及兼容式审计导出。

仍属于后续范围：自动 Context Pack、共享知识服务、身份权限、团队 Scope 和乐观并发控制。

## 11. 非目标

- 不把每轮对话自动总结成长期知识。
- 不要求弱 Agent 手工维护六种记录类型。
- 不默认加载全部规则、最近任务或原始日志。
- 不让 Skill 承担数据库一致性、重复检测或 Revision 失效。
- 不使用一个中央 MCP 直接控制所有开发者的 UE 编辑器。
