# UEAgentKit Patch Schema 1.0

UEAgentKit Patch 是面向 Blueprint 的声明式变更格式。0.3.1 同时提供纯 JSON 预校验和 UE Editor 内执行器。

## 两层执行模型

### 预校验层

`ue-agent patch validate` 只读取 Patch、Policy 和 Blueprint 导出快照：

```text
validationOnly=true
willLoadOrModifyUObjects=false
willWriteDisk=false
commitSupported=true
```

### UE 执行层

`scripts\RunPatch.cmd` 在预校验成功后调用 `BlueprintPatch` Commandlet：

```text
DryRun  = 内存修改 → 编译 → 读取结果 → 回滚 → 再编译，不保存
Commit  = 创建外部备份 → 修改 → 编译 → 保存
```

当前每次执行严格限制为一个 Blueprint 和一个 Operation，避免部分保存。

## CLI

列出操作：

```bat
scripts\ue-agent.cmd patch operations
```

预校验：

```bat
scripts\ue-agent.cmd patch validate ^
  --patch <PATCH_JSON> ^
  --policy <POLICY_JSON> ^
  --export <BLUEPRINT_EXPORT> ^
  --report <VALIDATION_REPORT>
```

执行：

```bat
scripts\RunPatch.cmd ^
  -ProjectPath "<PROJECT>.uproject" ^
  -Patch "<PATCH_JSON>" ^
  -Policy "<POLICY_JSON>" ^
  -RevisionExport "<BLUEPRINT_EXPORT>" ^
  -Mode DryRun|Commit
```

## Patch 根对象

```json
{
  "schemaVersion": "1.0",
  "patchId": "example-patch",
  "projectName": "我的项目",
  "description": "Optional description.",
  "assets": []
}
```

字段：

- `schemaVersion`：固定为 `1.0`。
- `patchId`：本次变更的稳定标识。
- `projectName`：必须同时匹配当前工程、Policy 和导出快照。
- `description`：可选说明。
- `assets`：预校验格式允许数组；当前 UE 执行器要求恰好一项。

## Asset 对象

```json
{
  "assetPath": "/Game/UEAgentKitWriteTests/BP_Target.BP_Target",
  "expectedRevision": "sha256:<64 lowercase hex>",
  "expectedAssetClass": "/Script/Engine.Blueprint",
  "operations": []
}
```

`expectedRevision` 必须来自最新 Blueprint 导出。执行器加载资产后会重新计算磁盘 SHA-256，不信任仅来自预校验快照的结果。

## Policy

```json
{
  "schemaVersion": "1.0",
  "validationEnabled": true,
  "commitEnabled": false,
  "allowedProjectNames": ["我的项目"],
  "allowedAssetRoots": ["/Game/UEAgentKitWriteTests"],
  "allowedOperations": [
    "setVariableDefault",
    "setComponentProperty",
    "setPinDefault"
  ],
  "allowedAssetClasses": ["/Script/Engine.Blueprint"],
  "requireRevision": true,
  "rejectDirtyPackages": true,
  "maxAssetsPerPatch": 10,
  "maxOperationsPerAsset": 32,
  "maxValueBytes": 65536
}
```

安全规则：

- 禁止授权整个 `/Game`；必须使用其下具体目录。
- `Commit` 同时要求命令行 `-Mode Commit` 和 `commitEnabled=true`。
- 当前执行器始终要求 Revision，并建议保持 `rejectDirtyPackages=true`。
- Policy 的数组限制属于格式上限；执行器当前仍只接受单资产、单操作。

## 支持的 Operation

### setVariableDefault

修改目标 Blueprint 自己声明的成员变量默认值：

```json
{
  "operationId": "set-health",
  "operation": "setVariableDefault",
  "target": {"variableName": "Health"},
  "value": 100.0
}
```

当前支持 Bool、整数、浮点、String、Name、Text。继承变量、容器、Struct 和对象引用暂不支持。

### setComponentProperty

修改目标 Blueprint SCS 组件模板属性：

```json
{
  "operationId": "show-box",
  "operation": "setComponentProperty",
  "target": {
    "componentName": "Box",
    "propertyPath": "bHiddenInGame"
  },
  "value": false
}
```

`propertyPath` 支持用点号进入嵌套 Struct；当前最终属性仍限定为标量。

### setBlueprintDescription

修改 Blueprint 资产自身的描述文本，适用于没有成员变量或普通 Pin 的 Blueprint 子类型：

```json
{
  "operationId": "set-description",
  "operation": "setBlueprintDescription",
  "target": {},
  "value": "UEAgentKit verified Blueprint write."
}
```

该操作已在 Function Library、Macro Library、Blueprint Interface 和 Control Rig Blueprint 上完成 Dry Run、Commit、备份和独立重载验证。

### setPinDefault

修改指定 Graph/Node 的未连接输入 Pin 默认值：

```json
{
  "operationId": "enable-sweep",
  "operation": "setPinDefault",
  "target": {
    "graphGuid": "1258f95b-446d-6bb5-a27f-328abaf24c2b",
    "nodeGuid": "058dfab5-4a5e-91e1-69c5-c78b37ea7b11",
    "pinName": "bSweep"
  },
  "value": true
}
```

执行器拒绝输出 Pin、已连接 Pin、只读 Pin和忽略默认值的 Pin，并由该 Graph Schema 校验新值。

## Dry Run 报告

报告至少包含：

```text
mode
patchId
assetPath
assetClass
operation
target
beforeValue
afterValue
restoredValue
beforeRevision
afterRevision
compiled
saved
rolledBack
rollbackValueMatch
diskUnchanged
backupPath
```

Dry Run 成功条件包括 `compiled=true`、`rolledBack=true`、`rollbackValueMatch=true`、`diskUnchanged=true`。

## Commit 与备份

保存前在 `BackupDir` 创建带 `patchId` 和 UTC ticks 的唯一 `.bak` 文件。编译失败、Revision 冲突、Dirty Package、Policy 越界、备份失败或目标解析失败都会在保存前退出。

当前失败保存路径会把备份复制回原资产文件。每次 Commit 后应使用独立 UE 进程重新导出并核对预期值和新 Revision。

JSON Schema 文件：[`patch.schema.json`](patch.schema.json)。运行时校验器与 UE 执行器是最终安全判断来源。
