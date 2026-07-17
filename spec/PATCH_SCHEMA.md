# UEAgentKit Patch Schema 1.0

UEAgentKit Patch 是面向 Blueprint 的声明式变更请求格式。当前 0.3.0 Baseline 仅执行纯 JSON 与导出快照校验，不加载或修改 UObject，也不会写入 `.uasset`。

## 安全边界

无论 Policy 中 `commitEnabled` 为何，当前实现都固定返回：

```text
willLoadOrModifyUObjects=false
willWriteDisk=false
commitSupported=false
```

当前代码不得调用 `Modify`、`MarkPackageDirty`、`SavePackage`，也不存在 Commit 命令。

## CLI

列出已注册操作：

```bat
scripts\ue-agent.cmd patch operations
```

校验 Patch：

```bat
scripts\ue-agent.cmd patch validate ^
  --patch examples\patches\set-variable-default.json ^
  --policy config\write-policy.example.json ^
  --export Output\Blueprints ^
  --report Output\patch-report.json
```

退出码：

```text
0 = Patch 合法
1 = Patch、Policy 或 Export 校验失败
2 = 输入文件或路径不存在
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

未知字段、重复 JSON Key、空资产列表和不支持的 Schema Version 均会被拒绝。

## Asset 对象

每个 Asset 必须包含：

- `assetPath`：完整对象路径，例如 `/Game/Test/BP_Target.BP_Target`。
- `expectedRevision`：`sha256:<64 lowercase hex>`。
- `operations`：至少一个声明式操作。
- `expectedAssetClass`：可选，但建议提供 `/Script/Module.Class`。

对象名必须与 Package 叶子名称一致。Policy 不允许直接授权整个 `/Game`，只能授权更小的测试目录。

## Policy

Policy 控制项目名、资产目录、Asset Class、Operation 和数量限制。示例见：

```text
config/write-policy.example.json
```

默认建议：

- `validationEnabled=true`
- `commitEnabled=false`
- `requireRevision=true`
- `rejectDirtyPackages=true`
- 仅授权 `/Game/UEAgentKitWriteTests`

即使 `commitEnabled=true`，当前版本仍然只做校验。

## 支持的 Operation

### setVariableDefault

```json
{
  "operationId": "set-health",
  "operation": "setVariableDefault",
  "target": {
    "variableName": "Health"
  },
  "value": 125.0
}
```

### setComponentProperty

```json
{
  "operationId": "set-visible",
  "operation": "setComponentProperty",
  "target": {
    "componentName": "StaticMesh",
    "propertyPath": "Rendering.Visible"
  },
  "value": true
}
```

### setPinDefault

```json
{
  "operationId": "set-pin",
  "operation": "setPinDefault",
  "target": {
    "graphGuid": "11111111-1111-1111-1111-111111111111",
    "nodeGuid": "22222222-2222-2222-2222-222222222222",
    "pinName": "NewValue"
  },
  "value": "42"
}
```

`value` 当前只允许有限 JSON Scalar：字符串、数字、布尔值或 `null`。对象、数组、NaN 和 Infinity 会被拒绝。

## Export 快照要求

校验输入必须是 UEAgentKit Blueprint 导出目录，并至少包含：

```text
manifest.json
canonical\*.json
```

校验器会检查：

- Manifest `projectName` 与 Patch 一致。
- `failureCount` 等于 0。
- Canonical `projectName` 与 Manifest 一致。
- Asset Path、Asset Class 和 Revision 存在。
- Revision 可用且与 `expectedRevision` 完全一致。
- Package 在导出时不是 Dirty。

## Report

Report 使用稳定排序输出错误，并包含 Asset、Operation、Revision 和 Expected Change 信息。它明确记录：

```text
dryRun=true
validationOnly=true
willLoadOrModifyUObjects=false
willWriteDisk=false
commitSupported=false
```

JSON Schema 文件：[`patch.schema.json`](patch.schema.json)。运行时校验器仍是最终安全判断来源，因为 Policy、Revision、导出快照和字节大小限制无法仅靠静态 JSON Schema 完整表达。
