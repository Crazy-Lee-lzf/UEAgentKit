# UE Agent Kit Backup Manifest 与 Rollback 1.0

UE Agent Kit 0.4.1 为成功的单资产 Patch Commit 增加可审计 Backup Manifest，并提供默认只读的独立 rollback 命令。

## 1. 安全目标

恢复流程不接受任意源文件和任意目标路径。一次 rollback 必须同时满足：

- Manifest 位于显式指定的 Backup Root 内。
- Manifest 对应一个已成功保存的单资产 Commit；可记录 1–32 个兼容 Operation。
- 目标资产是规范化的 `/Game/...` 单文件 `.uasset`，解析后仍位于目标工程 `Content` 目录内。
- 当前目标文件 SHA-256 等于 Commit 后 Revision。
- Backup SHA-256 等于 Commit 前 Revision。
- 当前 Policy 文件 SHA-256 与 Manifest 记录完全一致，且仍授权相同项目、资产根、资产类、Operation 和精确目标。
- 不存在 `.uexp`、`.ubulk`、`.uptnl`、`.m.ubulk` 或 `.upayload` 等独立 Package Sidecar。
- 显式 Commit 前目标 Unreal 工程必须关闭。

任何一项不满足都会拒绝写盘。

## 2. Backup Manifest

`RunPatch.ps1 -Mode Commit` 在 Commandlet 成功保存并生成原始 `.bak` 后，自动调用：

```bat
scripts\ue-agent.cmd patch manifest ^
  --patch "<PATCH_JSON>" ^
  --policy "<POLICY_JSON>" ^
  --report "<COMMIT_REPORT_JSON>" ^
  --backup-root "<BACKUP_ROOT>"
```

默认 Manifest 与原始备份并排保存：

```text
<backup>.uasset.bak
<backup>.uasset.bak.manifest.json
```

Manifest Schema 位于 [`backup-manifest.schema.json`](backup-manifest.schema.json)。核心字段包括：

```text
manifestId
patchId
projectName
assetPath
assetClass
operation
target
authorizationKey
operationCount
operations[].operationId / operation / target / authorizationKeys
beforeRevision
afterRevision
backup.relativePath
backup.revision
source.patchSha256
source.policySha256
source.commitReportSha256
```

Manifest 只记录 Backup Root 内的相对备份路径，不记录可重定向到任意位置的绝对恢复源路径。

单 Operation Manifest 保留原有顶层 `operation`、`target` 和 `authorizationKey`。多 Operation Manifest 使用 `operation=transaction`，并在 `operations[]` 中按 Patch 顺序记录每个 Operation、目标、变更前后值和全部精确授权键。Rollback 会逐条重新校验当前 Policy 授权，但文件恢复仍是一次完整 Package 原子替换，不会反向执行单个 UObject Operation。

## 3. Rollback Dry Run

默认模式只验证，不修改文件：

```bat
scripts\RunRollback.cmd ^
  -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject" ^
  -Manifest "<BACKUP_MANIFEST_JSON>" ^
  -Policy "<POLICY_JSON>" ^
  -BackupRoot "<BACKUP_ROOT>" ^
  -Mode DryRun ^
  -Report "Output\Rollback\dryrun-report.json"
```

Dry Run 报告会输出目标路径、当前 Revision、期望当前 Revision、Backup Revision、Sidecar 列表和结构化错误码。`willWriteDisk` 必须为 `false`。

## 4. 显式 Rollback Commit

```bat
scripts\RunRollback.cmd ^
  -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject" ^
  -Manifest "<BACKUP_MANIFEST_JSON>" ^
  -Policy "<POLICY_JSON>" ^
  -BackupRoot "<BACKUP_ROOT>" ^
  -Mode Commit ^
  -Report "Output\Rollback\rollback-report.json"
```

执行顺序固定为：

```text
再次验证 Manifest、Policy、当前 Revision 和 Backup Revision
→ 检查目标工程没有运行中的 UnrealEditor/UnrealEditor-Cmd
→ 在 Backup Root/rollback-safety 创建 Commit 后包的安全副本
→ 将原始 Backup 复制到目标目录的临时文件
→ 校验临时文件 Revision
→ 使用原子文件替换恢复目标包
→ 校验恢复后 Revision
→ 写入 Backup Root/rollback-receipts 审计 Receipt
→ 启动独立 UE 进程重新导出恢复后的资产
→ 验证 Project、Asset Path、Asset Class、Revision 和 Dirty 状态
```

成功报告包含：

```text
wroteDisk=true
restored=true
beforeRollbackRevision=<Commit 后 Revision>
afterRollbackRevision=<Commit 前 Revision>
preRollbackBackupPath=<回滚前安全副本>
preRollbackBackupRevision=<Commit 后 Revision>
receiptPath=<审计 Receipt>
```

## 5. 异常恢复

目标文件被替换后，如果 Revision 校验或审计输出失败，rollback 会使用回滚前安全副本自动恢复原目标包，并删除未完成的 Receipt。只有安全恢复本身也失败时，命令才会要求人工使用 `preRollbackBackupPath` 处理。

独立 UE 重载发生在文件恢复和 Receipt 写入之后。若独立验证失败，已恢复的包不会自动改回 Commit 后版本；报告会保留回滚前安全副本路径，供显式审查和人工决定。

## 6. 防重复与并发修改

同一个 Manifest 成功恢复一次后，目标文件已经等于 `beforeRevision`，不再等于 Manifest 的 `afterRevision`。再次执行会返回：

```text
current-revision-conflict
```

这可以防止旧 Manifest 覆盖恢复后的资产或覆盖后续新修改。若确实需要再次切换状态，应基于当前状态执行新的显式 Patch Commit，生成新的 Backup Manifest。

## 7. 当前限制

- 仅支持 `/Game` 下的单文件 `.uasset` Package。
- 不支持 Engine、Plugin Content、外部文件、Package Sidecar 或批量资产恢复。
- 不会绕过现有 Write Policy，也不会从 Manifest 推导新的授权。
- 不提供任意文件复制、任意目标路径或 Shell 接口。
- Rollback 是文件级完整包恢复，不尝试反向执行单个 UObject Operation。
