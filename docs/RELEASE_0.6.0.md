# UE Agent Kit 0.6.0 发布说明

UE Agent Kit 0.6.0 面向 Unreal Engine 5.6，新增 Revision-aware Project Memory。它在现有只读索引、受控写入、独立验证和 rollback 安全边界之外，提供可追溯、可失效、可冲突并存的项目长期记忆。

## 主要能力

- 独立 `memory.sqlite3`，不修改或替换 immutable 项目索引。
- 六类记录：Project Fact、Project Rule、Decision Record、Known Issue、Task Record、Runtime Evidence。
- 三类来源：`user-confirmed`、`tool-observed`、`model-inferred`。
- 五种状态：`valid`、`stale`、`conflicted`、`superseded`、`unverified`。
- Project/Asset/Symbol/Property 等 Scope，以及稳定 Revision Set 和 Artifact 绑定。
- Revision 变化后自动把不再成立的记录标记为 `stale`，不静默删除历史。
- Schema v2 双摘要：语义内容摘要与证据摘要；读取时检测正文、Revision 或 Artifact 篡改。

## MCP 与 CLI

Project Memory 默认关闭，并在服务器启动时固定数据库与 Project Key；Tool 参数不能切换数据库或工程。

新增 MCP Tool：

- `ue_memory_search`
- `ue_memory_get`
- `ue_memory_add_rule`
- `ue_memory_record_finding`
- `ue_memory_record_task`
- `ue_memory_mark_superseded`
- `ue_memory_validate`

未启用 Memory 时 Offline/Live/Workflow/Combined 继续保持 5/23/25/43 Tool；启用后分别为 12/30/32/50。

CLI 提供 status、search、get、validate 和 export。Audit Export 包含全部 Record、Status Event、双摘要验证和稳定 Snapshot SHA-256，并对数据库路径脱敏。

## Workflow Evidence 闭环

成功的 `ue_verify_asset` 返回 `outcome=succeeded` 的 `memoryTaskEvidence.arguments`；成功的 rollback Commit 返回 `outcome=rolledBack` 的同格式参数。Agent 可以原样传给 `ue_memory_record_task`，无需从日志或本机路径重建证据。

Task Record 强制绑定：

- Canonical Patch digest
- Backup Manifest ID
- 独立 Validation Evidence ID
- 最终或恢复 Revision Set
- 最终结论与 Outcome

## 验证

0.6.0 已通过：

- Ruff 与 245 项 Python 测试。
- 3 个 JSON Schema 与 16 个示例 Patch。
- 真实 MCP stdio Memory 回归和 Windows UTF-8 CLI/Audit 回归。
- 真实 UE5.6 Commit → independent Verify → succeeded Task → rollback → rolledBack Task → Revision invalidation → Audit 闭环。
- Commit Task 在 rollback 后变为 `stale`，rolledBack Task 保持 `valid`。
- 测试 `.uasset` SHA-256 完全恢复，immutable SQLite Index 目录零修改。

## 兼容性与升级

- 默认未启用 Memory 的 MCP Tool 数量、顺序和行为保持兼容。
- 0.5.5 的项目索引不需要迁移；Memory 使用独立数据库。
- Memory Schema v1 数据库首次打开时自动迁移到 v2 并回填证据摘要。
- 继续禁止任意 Python、Console、Shell、UObject 调用、无约束批量写入和静默覆盖冲突事实。

## 发布产物

```text
UEAgentKit-0.6.0-UE5.6-Win64.zip
ue_agent_kit-0.6.0-py3-none-any.whl
release-manifest.json
SHA256SUMS.txt
```
