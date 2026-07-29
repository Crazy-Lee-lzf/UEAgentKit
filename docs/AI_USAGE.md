# AI 使用指南

## 1. 使用目标

UE Agent Kit 为 AI 提供可验证、可定位、按需加载的 UE5 项目上下文。当前版本只读，不允许 AI 直接修改 `.uasset`。

项目知识分为两层：

```text
通用资产目录：所有常用 UE 资产的路径、类型、Tags、Revision 和依赖。
Blueprint 深度语义：变量、函数、Graph、Node、Pin 和结构化调用关系。
```

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

当前版本只能：

- 导出资产目录和 Blueprint 语义。
- 建立 SQLite 索引。
- 搜索资产、Symbol 和 Reference。
- 分析依赖和调用关系。

当前版本不能声称已经：

- 修改属性或默认值。
- 新增或删除节点。
- 保存 Blueprint 或其他资产。
- 回滚资产。

公开能力以仓库根目录 `README.md` 为准。


## MCP 工作流

0.5.5 的默认 MCP 模式提供能力、项目状态和三个查询 Tool。完整固定项目模式共 16 个 Tool，增加六个高层 Plan/Dry Run 入口与底层 Apply、Verify、rollback 工作流，但 Agent 仍不能选择项目、引擎、Policy、数据库或任意文件路径。

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
