# AI 使用指南

## 1. 使用目标

UE Agent Kit 为 AI 提供可验证、可定位、按需加载的 UE5 项目上下文，以及受 Policy、Revision、Plan、确认、备份和验证保护的有限写入能力。当前 `main` 既支持离线索引和 Live Editor 读取，也包含持久化 Patch 工作流与首个不自动保存的 Live Editor Write；具体公开边界以仓库根目录 `README.md` 和 [`PROJECT_STATUS.md`](PROJECT_STATUS.md) 为准。

当前项目数据包括：

```text
通用资产目录：路径、类型、Tags、Revision 和依赖。
Blueprint 深度语义：变量、函数、Graph、Node、Pin 和结构化调用关系。
Project Memory：来源、状态、Revision 和 Evidence 绑定的长期记录。
Live Editor：当前选择、已打开资产、Dirty、日志、编译与编辑器内存状态。
```

后续 Memory 将采用 Knowledge Tree + Active Work + 渐进式披露；设计见 [`MEMORY_ARCHITECTURE.md`](MEMORY_ARCHITECTURE.md)。

## 2. 推荐索引方式

先将通用资产目录和 Blueprint 深度结果导入同一个 SQLite 数据库：

```bat
scripts\ue-agent.cmd index build Output\AssetCatalog
scripts\ue-agent.cmd index build Output\Blueprints
```

通用记录使用 `asset-index` Profile；同一路径存在 Blueprint 深度记录时，深度记录具有更高优先级。

## 3. 资产查询

### 查找某类资产

```bat
scripts\ue-agent.cmd search assets --class StaticMesh
scripts\ue-agent.cmd search assets --class SkeletalMesh
scripts\ue-agent.cmd search assets Manny --class Texture2D
```

AI 应优先使用 Asset Class 筛选，避免仅根据 `SM_`、`T_` 等命名前缀猜测类型。

### 查看单个资产

```bat
scripts\ue-agent.cmd asset /Game/Environment/SM_Wall.SM_Wall --details
```

通用资产详情中可以包含：

- Asset Class 和 Package。
- Asset Registry Tags。
- SHA-256 Revision。
- 直接依赖和被引用关系。

Tags 来自 Asset Registry，不等于完整 UObject 内部状态。未通过专用 Reader 导出的字段应标记为未知，而不是自行推断。

### 查询反向引用

```bat
scripts\ue-agent.cmd references --target-asset /Game/Environment/SM_Wall.SM_Wall
```

回答“谁使用了这个 Mesh、Material 或 Texture”时，应返回引用者 Asset Path 和 Reference Kind。

## 4. Blueprint 查询

推荐顺序：

```text
1. 查找 Blueprint 资产。
2. 获取资产摘要和 Revision。
3. 查找相关 Symbol。
4. 查询结构化 Reference。
5. 只展开相关 Graph、Node 和 Pin。
6. 输出 Asset Path、Graph、Node GUID 和引用类型。
```

示例：

```bat
scripts\ue-agent.cmd search symbols MaxWalkSpeed
scripts\ue-agent.cmd references --kind writes --target-name MaxWalkSpeed
```

不要一次把大型 Blueprint 的完整 Canonical JSON 全部塞入模型上下文。

## 5. Blueprint Profile

```text
index       快速资产和符号发现
structure   类、变量、组件、函数签名和接口
logic       Graph、Node、Pin 和连接
defaults    CDO 与组件模板默认值
full        完整归档与回归验证
ai          面向模型的紧凑上下文
```

`asset-index` 是通用资产目录 Profile，不包含 Blueprint Graph 语义。

## 6. 事实、推断和未知

AI 输出应明确区分：

```text
事实：直接来自 Canonical JSON、BPCTX 或 SQLite 结构化记录。
推断：根据依赖、命名、调用关系和控制流得出的解释。
未知：当前通用记录或专用适配器没有导出的字段。
```

例如，Static Mesh 的 Registry Tags 可以证明资产类型和部分构建元数据，但不能自动证明 Socket、Collision 或每级 LOD 的完整内部结构已经被解析。

## 7. 可追溯性

结论尽量附带：

- Asset Path。
- Asset Class。
- Package Name。
- Revision。
- Reference Kind。
- Blueprint Graph Name / GUID。
- Node GUID 和 Pin ID。

这样用户可以在编辑器中重新定位同一对象，并核对资产是否在查询后发生变化。

## 8. 安全边界

当前读取能力包括资产/Blueprint 导出、SQLite 查询、Reference、Project Memory 和受限 Live Editor 状态。当前写入能力包括 Policy/Revision 门控的 Blueprint、Data Asset、Material Instance、DataTable Patch，授权单资产保存、验证、rollback，以及首个只修改 Editor 内存并进入 Undo 栈的 `ue_apply_asset_property_live`。

不能因为能够读取某类数据，就声称已经支持对应写入。当前仍不支持通用 Blueprint Graph 重写、Animation/Control Rig/Material Graph/Niagara/Sequencer/UMG 写入、任意 Actor 生命周期操作、任意 Console/Python/Shell 或无约束 Save All。

所有持久化写入必须使用固定项目、Policy、Revision、Plan/Dry Run、一次性 Receipt、精确确认、Backup、独立验证和可验证 rollback。Live Apply 默认不等于保存成功。

## MCP 工作流

当前 `main` 的 MCP 模式为 Offline 5、Live 23、Workflow 26、Combined 44；启用固定 Project Memory 后分别为 12、30、33、51。Tool 数量不等同于 Unreal Operation 数量。Agent 仍不能在 Tool 参数中切换项目、引擎、Policy、数据库、Editor Endpoint 或任意文件路径。

推荐调用顺序：

```text
ue_search / ue_get_asset / ue_find_references
→ ue_plan_patch
→ ue_dry_run_patch
→ 人工或上层 Agent 检查结构化 Diff 与安全门
→ ue_apply_patch（一次性 Receipt + 精确确认）
→ ue_verify_asset
→ 必要时 ue_rollback_patch DryRun
→ ue_rollback_patch Commit（一次性 Receipt + 精确确认）
```

MCP Receipt 仅在当前 Server 会话中有效；重启后必须重新 Plan 和 Dry Run。

## Project Memory 的后续渐进式使用

0.6.0 当前使用 `ue_memory_search` 和 `ue_memory_get` 查询平面记录。后续不会让 Agent 每轮加载全部 Memory，而是按以下顺序逐层披露：

```text
Project Profile
→ 相关 System 摘要
→ Feature/Entity/Implementation 摘要
→ 详细 Rule/Decision/Finding/Issue
→ 必要时才读取原始 Evidence
```

服务器将强制 Token Budget、默认过滤 `stale`/`superseded`，并返回建议的 `nextActions`。当前目标、TODO 和阻塞进入独立 Active Work，不混入长期知识记录。MCP 是主要执行层；日常只需要一个薄 `project-memory` Skill，不把读取、写入、维护和 TODO 拆分为多个长 Skill。

完整规划见 [`MEMORY_ARCHITECTURE.md`](MEMORY_ARCHITECTURE.md)。

## Project Memory 查询与审计

在不启动 MCP 的批处理或人工审计场景中，使用固定 `--memory-database` 和 `--project-key`：

```bat
scripts\ue-agent.cmd memory search "damage formula" ^
  --memory-database ".data\ue_agent_kit_memory.sqlite3" ^
  --project-key "MyProject"

scripts\ue-agent.cmd memory export ^
  --memory-database ".data\ue_agent_kit_memory.sqlite3" ^
  --project-key "MyProject" ^
  --output "Output\ProjectMemory\memory-audit.json"
```

AI 使用搜索结果时默认忽略 `stale` 和 `superseded`。需要审查历史时显式传入 `--status stale` 或 `--status superseded`。审计导出中的 `contentSha256` 用于语义内容比较，`evidenceSha256` 绑定 Source、Revision Set 与 Artifact，`snapshotSha256` 绑定整份可移植快照。

## 写入完成后的 Memory 记录

启用 Workflow 与 Project Memory 时，完成写入后按固定顺序执行：

```text
ue_apply_patch
ue_verify_asset
ue_memory_record_task(memoryTaskEvidence.arguments)
```

只在 `ue_verify_asset.verified=true` 且返回 `memoryTaskEvidence` 时记录成功 Task。必须原样使用 `memoryTaskEvidence.arguments`；不要从自然语言总结中猜测 `patch_ref`、`backup_manifest_ref`、`validation_evidence_ref` 或 Revision。

完成 rollback Commit 后，若 `ue_rollback_patch.restored=true` 且返回 `memoryTaskEvidence`，同样原样调用 `ue_memory_record_task`；该记录的 outcome 为 `rolledBack`。不要把 rollback Dry Run 记录成终态。

在开始相关任务前先用 `ue_memory_search` 查询 Valid 记录。默认不要把 `stale` 或 `superseded` 记录当作当前事实；需要历史审计时再显式请求这些状态。
