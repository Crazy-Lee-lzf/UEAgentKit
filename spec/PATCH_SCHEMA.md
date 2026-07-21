# UEAgentKit Patch Schema 1.0

UEAgentKit Patch 是面向 Unreal Engine 资产的声明式变更格式。0.3.6 同时提供纯 JSON 预校验，以及 UE Editor 内的 Blueprint、通用属性和 Material Instance 参数执行器。

## 两层执行模型

### 预校验层

`ue-agent patch validate` 只读取 Patch、Policy 和最新导出快照：

```text
validationOnly=true
willLoadOrModifyUObjects=false
willWriteDisk=false
commitSupported=true
```

### UE 执行层

`scripts\RunPatch.cmd` 在预校验成功后按 Operation 分发：

```text
Blueprint Operation                       → BlueprintPatch Commandlet
setAssetProperty                          → AssetPatch Commandlet
setMaterialInstanceScalarParameter        → AssetPatch Commandlet
setMaterialInstanceVectorParameter        → AssetPatch Commandlet
setMaterialInstanceTextureParameter       → AssetPatch Commandlet
```

执行语义：

```text
Blueprint DryRun = 内存修改 → 编译 → 读取结果 → 回滚 → 再编译，不保存
Asset DryRun     = 内存修改 → PostEditChange → 读取结果 → 回滚，不保存
Commit           = 创建外部备份 → 修改 → 校验/编译 → 保存
```

当前每次执行严格限制为一个资产和一个 Operation，避免部分保存。

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
  --export <REVISION_EXPORT> ^
  --report <VALIDATION_REPORT>
```

执行：

```bat
scripts\RunPatch.cmd ^
  -ProjectPath "<PROJECT>.uproject" ^
  -Patch "<PATCH_JSON>" ^
  -Policy "<POLICY_JSON>" ^
  -RevisionExport "<REVISION_EXPORT>" ^
  -Mode DryRun|Commit
```

Blueprint 应使用深度导出结果；非 Blueprint 应使用通用资产目录结果。两者都必须提供最新的 Package SHA-256 Revision。

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
- `assets`：格式允许数组；当前 UE 执行器要求恰好一项。

## Asset 对象

```json
{
  "assetPath": "/Game/UEAgentKitWriteTests/T_Target.T_Target",
  "expectedRevision": "sha256:<64 lowercase hex>",
  "expectedAssetClass": "/Script/Engine.Texture2D",
  "operations": []
}
```

执行器加载资产后会重新计算磁盘 SHA-256，不信任仅来自预校验快照的结果。

## Policy

```json
{
  "schemaVersion": "1.0",
  "validationEnabled": true,
  "commitEnabled": false,
  "allowedProjectNames": ["我的项目"],
  "allowedAssetRoots": ["/Game/UEAgentKitWriteTests"],
  "allowedReferenceRoots": ["/Game/Characters/Mannequins/Textures"],
  "allowedReferenceClasses": ["/Script/Engine.Texture2D"],
  "allowedOperations": [
    "setVariableDefault",
    "setComponentProperty",
    "setPinDefault",
    "setBlueprintDescription",
    "setAssetProperty",
    "setMaterialInstanceScalarParameter",
    "setMaterialInstanceVectorParameter",
    "setMaterialInstanceTextureParameter"
  ],
  "allowedAssetClasses": [
    "/Script/Engine.Blueprint",
    "/Script/Engine.Texture2D",
    "/Script/Engine.MaterialInstanceConstant"
  ],
  "allowedAssetProperties": [
    "/Script/Engine.Texture2D#SRGB"
  ],
  "allowedMaterialParameters": [
    "/Script/Engine.MaterialInstanceConstant#Scalar#Roughness",
    "/Script/Engine.MaterialInstanceConstant#Vector#Base Color",
    "/Script/Engine.MaterialInstanceConstant#Texture#Base Texture"
  ],
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
- `setAssetProperty` 必须由 `allowedAssetProperties` 精确授权。
- 属性授权格式固定为 `AssetClass#Property.Path`，例如 `/Script/Engine.Texture2D#SRGB`。
- Material 参数授权格式固定为 `AssetClass#Type#ParameterName`；当前 `Type` 支持 `Scalar`、`Vector` 和 `Texture`。
- Texture 参数引用的目标资产必须同时落在 `allowedReferenceRoots` 内，且实际类必须精确命中 `allowedReferenceClasses`。
- Blueprint Operation 不能用于非 Blueprint；`setAssetProperty` 不能用于 Blueprint；Material 参数操作只接受 MaterialInstanceConstant。

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

`propertyPath` 支持用点号进入嵌套 Struct；最终属性仍限定为标量。

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

执行器拒绝输出 Pin、已连接 Pin、只读 Pin 和忽略默认值的 Pin，并由该 Graph Schema 校验新值。

### setBlueprintDescription

修改 Blueprint 资产自身的描述文本：

```json
{
  "operationId": "set-description",
  "operation": "setBlueprintDescription",
  "target": {},
  "value": "UEAgentKit verified Blueprint write."
}
```

该操作已在 Function Library、Macro Library、Blueprint Interface 和 Control Rig Blueprint 上完成 Dry Run、Commit、备份和独立重载验证。

### setAssetProperty

修改非 Blueprint 资产上由 Policy 精确授权的反射属性：

```json
{
  "operationId": "disable-srgb",
  "operation": "setAssetProperty",
  "target": {"propertyPath": "SRGB"},
  "value": false
}
```

限制：

- 最终属性必须具有 `CPF_Edit`，且不能是 Transient、DuplicateTransient 或 NonPIEDuplicateTransient。
- 支持 Bool、整数、浮点、String、Name、Text 和 Enum。
- 支持用点号进入嵌套 Struct，例如 `Rules.Priority`。
- 不支持 Array、Set、Map、对象引用、软对象引用和任意 UObject 替换。
- 当前 Revision 与备份以主 `.uasset` 为边界，因此存在 `.uexp/.ubulk/.uptnl/.m.ubulk/.upayload` 等侧文件时直接拒绝。
- 已完成 PrimaryAssetLabel/Data Asset、Texture2D 和 Static Mesh 的 Dry Run、Commit、备份与独立重载验证。

### setMaterialInstanceScalarParameter

修改 `MaterialInstanceConstant` 上由 Policy 精确授权的 Global Scalar 参数：

```json
{
  "operationId": "set-roughness",
  "operation": "setMaterialInstanceScalarParameter",
  "target": {"parameterName": "Roughness"},
  "value": 0.42
}
```

限制与语义：

- 当前只支持 `MaterialInstanceConstant`、Global association 和 Scalar 参数。
- 参数名必须在继承链中恰好匹配一次，并由 `allowedMaterialParameters` 精确授权。
- 值必须是可表示为有限 `float` 的 JSON number。
- 修改通过 UE5.6 `MaterialEditingLibrary` 执行；Setter 返回值不作为成功依据，执行器以重新读取结果为准。
- Dry Run 会保存并恢复完整 `ScalarParameterValues` 数组，因此继承参数临时新增的 Override 也会被移除。
- 报告额外包含 `rollbackStructureMatch`；成功 Dry Run 要求其为 `true`。
- 已完成继承参数和已有 Override 两条路径的 Dry Run、Commit、外部备份和独立 UE 重载验证。

### setMaterialInstanceVectorParameter

修改 `MaterialInstanceConstant` 上由 Policy 精确授权的 Global Vector 参数：

```json
{
  "operationId": "set-base-color",
  "operation": "setMaterialInstanceVectorParameter",
  "target": {"parameterName": "Base Color"},
  "value": {"r": 0.2, "g": 0.4, "b": 0.8, "a": 1.0}
}
```

限制与语义：

- 当前只支持 `MaterialInstanceConstant`、Global association 和 Vector 参数。
- 值必须是仅包含 `r`、`g`、`b`、`a` 四个有限数字的 JSON 对象。
- 参数名必须在继承链中恰好匹配一次，并由 `AssetClass#Vector#ParameterName` 精确授权。
- 修改通过 UE5.6 `MaterialEditingLibrary` 执行，并以重新读取的 `FLinearColor` 结果作为成功依据。
- Dry Run 保存并恢复完整 `VectorParameterValues` 数组，同时比较每个 Override 的 ParameterInfo、值和编辑器兼容字段。
- 成功 Dry Run 要求 `rollbackValueMatch=true`、`rollbackStructureMatch=true` 和 `diskUnchanged=true`。
- 已在真实 `Base Color` 参数上完成 Dry Run、Commit、唯一备份、独立 UE 进程重载和过期 Revision 拒绝验证。

### setMaterialInstanceTextureParameter

修改 `MaterialInstanceConstant` 上由 Policy 精确授权的 Global Texture 参数：

```json
{
  "operationId": "set-base-texture",
  "operation": "setMaterialInstanceTextureParameter",
  "target": {"parameterName": "Base Texture"},
  "value": "/Game/Characters/Mannequins/Textures/Manny/T_Manny_02_D.T_Manny_02_D"
}
```

限制与语义：

- 当前只支持 `MaterialInstanceConstant`、Global association 和 Texture 参数。
- 值必须是现有 `/Game/.../Asset.Asset` 对象路径，不接受文件系统路径。
- 参数必须由 `AssetClass#Texture#ParameterName` 精确授权。
- 引用资产必须位于 `allowedReferenceRoots`，并在加载后由实际 UObject Class 精确匹配 `allowedReferenceClasses`。
- Dry Run 保存并恢复完整 `TextureParameterValues` 数组，并逐项比较 ParameterInfo、Texture 指针和编辑器兼容字段。
- UE 5.6 的 `SetMaterialInstanceTextureParameterValue` 会应用修改但始终返回 `false`；执行器不信任该返回值，而以精确参数读回作为成功依据。
- 成功 Dry Run 要求 `rollbackValueMatch=true`、`rollbackStructureMatch=true` 和 `diskUnchanged=true`。
- 已完成真实 Texture2D 的 Dry Run、Commit、唯一备份、独立 UE 进程重载和过期 Revision 拒绝验证。

## Dry Run 报告

报告包含：

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
rollbackStructureMatch (Material Instance operation)
diskUnchanged
backupPath
```

Blueprint Dry Run 成功时要求 `compiled=true`。非 Blueprint 不执行 Blueprint 编译，因此 `compiled=false`。所有 Dry Run 都必须满足：

```text
rolledBack=true
rollbackValueMatch=true
rollbackStructureMatch=true (when reported)
diskUnchanged=true
```

## Commit 与备份

保存前在 `BackupDir` 创建带 `patchId` 和 UTC ticks 的唯一 `.bak` 文件。Revision 冲突、Dirty Package、Policy 越界、属性未授权、备份失败、目标解析失败、类型不支持或 Blueprint 编译失败都会在保存前退出。

保存失败或保存后报告写入失败时，执行器会把外部备份复制回原资产文件。每次 Commit 后应使用独立 UE 进程重新导出，并核对预期值、新 Revision 和备份哈希。

JSON Schema 文件：[`patch.schema.json`](patch.schema.json)。运行时校验器与 UE 执行器是最终安全判断来源。
