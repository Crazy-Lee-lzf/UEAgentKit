# UE Agent Kit 0.5.5 发布说明

UE Agent Kit 0.5.5 面向 Unreal Engine 5.6，完成 0.5.x 日常开发能力收口。它把 0.5.2–0.5.4 阶段的 Live Editor、受控 Daily Actions、写入扩展、验证证据和事务能力整合为一个正式版本，同时保持固定项目、Policy、Revision、Dry Run、显式 Commit、Package 备份、独立验证和 rollback 安全边界。

## 主要能力

### Live Editor 与固定项目工作流

- 受认证的 localhost Editor Bridge，绑定固定 Project Path Hash、随机会话 Token、Plugin/Server 版本和 Capability。
- 有界 Output Log、编译诊断、不触发目标加载的资产检查、Blueprint Graph/Node 定位。
- 资产打开/聚焦、Content Browser 同步、ActorGuid 聚焦、Blueprint 内存编译、Data Validation 等不保存 Daily Actions。
- 四源资产状态：Editor Memory、磁盘 Package、Revision Export、SQLite。
- 单资产授权保存、独立验证和配对 Revision Export/SQLite Generation 原子刷新。

当前 Tool 模式：

```text
Offline   5
Live      23
Workflow  25
Combined  43
```

### 受控写入扩展

- 16 个注册 Patch Operation，12 个高层安全写入入口。
- DataTable 单 Row 多字段原子更新、Row 新增/删除/重命名和 Searchable Name 引用影响门禁。
- Data Asset Object/Class、Soft Object/Class 引用写入与 `null` 清除。
- Data Asset 顶层 Struct、Array、Set、Map 稳定 JSON、Canonical 顺序、深恢复和结构化 Diff。
- Material Instance Scalar、Vector、Texture、Static Switch 统一原生 JSON 报告，包含 Override、来源、Expression GUID 和完整 rollback 状态。

### 验证证据

- Data Validation 与 Automation Test 结果绑定 Project、Editor Session、UTC 时间和 Evidence ID。
- Asset/Folder Validation 提供稳定排序的 Revision Set、验证前后 Package SHA-256、Dirty 来源和执行期间稳定性。
- 没有资产输入的 Automation 明确使用 `revisionCoverage=not-applicable`，不会伪造 Revision。

### 单资产多 Operation 原子事务

- 一个资产可包含 1–32 个兼容 Operation。
- 全目标预校验，拒绝重复目标和混合 Executor。
- Commit 只创建一个 Package 备份，Blueprint 只编译一次，Package 只保存一次。
- Dry Run 使用 `process-discard`，不保存磁盘；Commit 使用 `package-backup`。
- 一个 Backup Manifest 按顺序记录全部 Operation、变更前后值和精确授权键，并整体 rollback。
- DataTable Row 新增/删除/重命名保持单 Operation，避免结构与引用顺序歧义。

## 发布工程

- 统一 `scripts/ValidateRelease.py`：版本源、双语发布文档、Ruff、Python 全测、3 份 Schema、16 个示例 Patch 和示例 Policy。
- GitHub Actions 覆盖 Python 3.11/3.12，并构建 Python Distribution。
- `scripts/BuildRelease.cmd` 在干净工作树上构建 UE5.6/Win64 Plugin ZIP、Python wheel、`SHA256SUMS.txt` 和 `release-manifest.json`。

## 验证结果

```text
Ruff                                      passed
Python unittest                           201/201 passed
JSON Schema meta-validation               3/3 passed
Example Patch schema validation           16/16 passed
UE5.6 Direct plugin build                 passed
UE5.6 UAT Win64 plugin package            passed
Material four-parameter regression        passed
DataTable field/row/reference regressions passed
Data Asset reference/structured regressions passed
Single-asset transaction regression       passed
Independent reload and rollback           passed
Exact final Package Revision recovery     passed
```

事务回归会在每次 Fixture Reset 后捕获该次运行的初始 Revision，并要求整体 rollback 后的最终 Revision 与其逐字节一致。具体 SHA-256 保存在该次回归的 `summary.json`；由于 Reset 会重新保存 Package，不把跨运行可变化的 Fixture 哈希固化为发布常量。

## 升级自 0.5.1

1. 关闭 UE Agent Kit MCP Server 和 Unreal Editor。
2. 完整替换旧 `UEAgentKit` Plugin 目录，不要混合旧 `Binaries`。
3. 重新运行 `scripts\setup_python.cmd -WithMcp` 或安装 `ue-agent-kit[mcp]`。
4. 重新导出 Revision、重建 SQLite，并确认 `ue_get_project_status` 为 `fresh`。
5. 使用 `ue_get_capabilities` 重新发现 43 Tool 与当前限制。
6. 多 Operation Patch 必须使用一个资产、兼容 Operation 和精确逐目标 Policy 授权。

## 发布产物

```text
UEAgentKit-0.5.5-UE5.6-Win64.zip
ue_agent_kit-0.5.5-py3-none-any.whl
SHA256SUMS.txt
release-manifest.json
```

具体 SHA-256 由同目录 `SHA256SUMS.txt` 和 `release-manifest.json` 给出。

## 仍不包含

- 多资产事务。
- 任意 SQL、Shell、Console、Python、文件覆盖或 UObject 调用。
- 完整 Blueprint/Material/Animation/Control Rig/Sequencer/Niagara Graph 任意编辑。
- 带独立 `.uexp/.ubulk/.uptnl/.m.ubulk/.upayload` 侧文件的 Package 写入。
- Hosted ChatGPT UI 端到端自动化。
