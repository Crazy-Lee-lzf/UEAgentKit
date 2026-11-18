# AI 使用指南

首次接入请先读 [AI Agent 快速接入](AGENT_QUICKSTART.md)。本文只补充查询、证据和 Memory 的使用细节。

## 1. 版本与运行模式

- 稳定版 **0.8.0**；最新预发布 **0.9.0 Developer Preview 1（`v0.9.0-dev1`）**；下一版候选 **0.9.0 Developer Preview 2（计划版本 `0.9.0.dev2`，未发布）**。
- Tool 按启动参数动态注册。连接后先调用 `ue_get_capabilities`；没出现的 Tool 就是当前不可用。
- `workflow` 组需要 `--enable-write-tools` + `--enable-subprocess-workflow` + 项目 `[UEAgentKit] SubprocessWorkflow=True` + Write Policy。缺任一项时不要尝试写入。
- 实验 Blueprint 写入使用独立门禁，不经过 Write Policy；见 [Blueprint 实验用法](BLUEPRINT_AGENT_E2E_USAGE.md)。

## 2. 索引与查询

先把通用资产目录和 Blueprint 深度结果导入同一个 SQLite 数据库：

```bat
scripts\ue-agent.cmd index build Output\AssetCatalog
scripts\ue-agent.cmd index build Output\Blueprints
```

```bat
scripts\ue-agent.cmd search assets --class StaticMesh
scripts\ue-agent.cmd search assets --class SkeletalMesh
scripts\ue-agent.cmd search assets Manny --class Texture2D
scripts\ue-agent.cmd asset /Game/Environment/SM_Wall.SM_Wall --details
scripts\ue-agent.cmd references --target-asset /Game/Environment/SM_Wall.SM_Wall
```

优先用 Asset Class 筛选，不要仅凭 `SM_`、`T_` 等前缀猜类型。通用记录使用 `asset-index` Profile；同一路径有 Blueprint 深度记录时，深度记录优先。

## 3. Blueprint 查询

```text
1. 查找 Blueprint 资产。
2. 获取资产摘要和 Revision。
3. 查找相关 Symbol。
4. 查询结构化 Reference。
5. 只展开相关 Graph、Node 和 Pin。
6. 输出 Asset Path、Graph、Node GUID 和引用类型。
```

```bat
scripts\ue-agent.cmd search symbols MaxWalkSpeed
scripts\ue-agent.cmd references --kind writes --target-name MaxWalkSpeed
```

不要一次把大型 Blueprint 的完整 Canonical JSON 塞进模型上下文。离线索引是保存时的快照；需要当前图、选区或 PIE 状态时，使用 live-read 工具。

Blueprint Profile：

```text
index       快速资产和符号发现
structure   类、变量、组件、函数签名和接口
logic       Graph、Node、Pin 和连接
defaults    CDO 与组件模板默认值
full        完整归档与回归验证
ai          面向模型的紧凑上下文
```

## 4. 事实、推断和未知

```text
事实：直接来自 Canonical JSON、BPCTX 或 SQLite 结构化记录。
推断：根据依赖、命名、调用关系和控制流得出的解释。
未知：当前 Reader 或索引没有导出的字段。
```

Registry Tags 能证明资产类型和部分构建元数据，但不能证明 Socket、Collision 或每级 LOD 的内部结构已经被解析。结论尽量附上 Asset Path、Asset Class、Package、Revision、Graph / Node GUID 和 Pin ID，便于在 Editor 中复核。

## 5. 受控写入

```text
ue_patch(action="plan")
→ ue_patch(action="dry_run")
→ 用户确认 Diff / 目标 / 风险
→ ue_patch(action="apply")（一次性 Receipt + 精确确认）
→ 显式 Save
→ ue_patch(action="verify")
→ 必要时 ue_patch(action="rollback") DryRun → Commit
```

对已打开、干净的非 Blueprint 单资产，`ue_asset_property(action="apply")` 修改 Editor 内存并进入 Undo 栈，但不保存；保存用 `ue_blueprint_edit(action="save")`，放弃用 `ue_asset_property(action="undo")` / `ue_asset_property(action="discard")`。

不要从自然语言猜 Receipt、Revision、确认字符串或 Backup 引用。Dry Run 通过不等于允许 Commit；Live Apply 成功不等于磁盘已保存；保存成功也不等于独立验证通过。失败时停止写入，返回错误码和当前状态；回滚按 [备份与回滚契约](../spec/BACKUP_AND_ROLLBACK.md) 执行。

## 6. Project Memory

按需逐层读取，不要每轮加载全部 Memory：

```text
ue_memory(action="get_context")
→ ue_memory(action="expand_node")
→ ue_memory(action="get_evidence")
→ 需要精确记录时 ue_memory(action="search") / ue_memory(action="get")
```

服务器会强制 Token Budget，默认过滤 `stale` / `superseded`，并返回建议的 `nextActions`。当前目标、TODO 和阻塞属于 Active Work，不混入长期知识记录。

批处理或人工审计可以不启动 MCP：

```bat
scripts\ue-agent.cmd memory search "damage formula" ^
  --memory-database ".data\ue_agent_kit_memory.sqlite3" ^
  --project-key "MyProject"

scripts\ue-agent.cmd memory export ^
  --memory-database ".data\ue_agent_kit_memory.sqlite3" ^
  --project-key "MyProject" ^
  --output "Output\ProjectMemory\memory-audit.json"
```

审计导出中 `contentSha256` 用于语义内容比较，`evidenceSha256` 绑定 Source、Revision Set 与 Artifact，`snapshotSha256` 绑定整份可移植快照。

## 7. 写入后的 Memory 记录

```text
ue_patch(action="apply")
→ ue_patch(action="verify")
→ ue_memory_write(**)   # 原样传入 memoryTaskEvidence.arguments
```

只在 `ue_patch(action="verify").verified=true` 且返回 `memoryTaskEvidence` 时记录成功 Task，并原样使用 `memoryTaskEvidence.arguments`。Rollback Commit 返回 `restored=true` 和 `memoryTaskEvidence` 时同样记录，outcome 为 `rolledBack`；不要把 rollback Dry Run 记录成终态。

能读取某类数据，不代表支持对应写入；完整能力边界与不支持列表见 [能力状态](PROJECT_STATUS.md)。
