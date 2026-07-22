# UEAgentKit Write Fixture Plan 1.0

Write Fixture Plan 用于在明确授权的测试工程中创建或重置隔离写入测试资产。它不是通用资产生成器，也不会扫描并删除整个目录。

## 执行模型

```text
JSON Schema / Python 只读预校验并固定 Plan SHA-256
→ UE WriteFixturePlan Commandlet 复核 Plan SHA-256，并全量预校验源资产和目标
→ Create 或 Reset
→ 输出实际 Asset Class、Package 文件、大小和 SHA-256 Revision
→ 独立 UE 进程重新导出
→ Python 精确验证类、Revision 和 Dirty 状态
```

正式入口：

```bat
scripts\RunWriteFixturePlan.cmd ^
  -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject" ^
  -Plan "<FIXTURE_PLAN_JSON>" ^
  -Mode Reset
```

## Plan 格式

```json
{
  "schemaVersion": "1.0",
  "root": "/Game/UEAgentKitWriteTests",
  "fixtures": [
    {
      "id": "data-table",
      "kind": "duplicateAsset",
      "sourceAsset": "/Game/Test/DT_Source",
      "targetAsset": "/Game/UEAgentKitWriteTests/DT_Target",
      "expectedClass": "/Script/Engine.DataTable"
    },
    {
      "id": "function-library",
      "kind": "blueprint",
      "targetAsset": "/Game/UEAgentKitWriteTests/BFL_Target",
      "expectedClass": "/Script/Engine.Blueprint",
      "parentClass": "/Script/Engine.BlueprintFunctionLibrary",
      "blueprintType": "FunctionLibrary"
    }
  ]
}
```

JSON Schema 位于 [`write-fixture-plan.schema.json`](write-fixture-plan.schema.json)。仓库示例位于 [`../tests/fixtures/write_fixture_plan.example.json`](../tests/fixtures/write_fixture_plan.example.json)。

## 支持的 Kind

### duplicateAsset

读取现有源资产，通过 UE Editor Asset Subsystem 复制到目标包并保存。

要求：

- `sourceAsset` 是有效且可加载的资产包或对象路径。
- 源资产实际类必须精确匹配 `expectedClass`。
- 源资产只读，不能同时作为同一 Plan 中的目标。
- 目标必须是单文件 `.uasset`；不支持 `.uexp`、`.ubulk`、`.uptnl`、`.m.ubulk` 或 `.upayload` Sidecar。

### scalarAsset

创建插件内置的 `/Script/UEAgentKitEditor.UEAgentKitScalarWriteFixtureAsset`，用于稳定覆盖以下可编辑标量：

- Bool
- Byte、Int32、Int64
- Float、Double
- String、Name、Text
- `FEnumProperty` Enum
- 带 Enum 的 Byte/Numeric Property

默认值由插件构造函数固定，适合重复 Reset 后执行精确 Patch 回归。

### blueprint

通过 `FKismetEditorUtilities::CreateBlueprint` 创建并编译 Blueprint。

支持：

- `Normal`
- `FunctionLibrary`
- `MacroLibrary`
- `Interface`

`parentClass` 必须是可加载的原生类路径，`expectedClass` 固定为 `/Script/Engine.Blueprint`。

## Create 与 Reset

### Create

只允许创建不存在的目标。任意目标已存在时，在修改资产前拒绝整个 Plan。

### Reset

仅删除 `fixtures[].targetAsset` 明确列出的目标，然后重新创建。不会递归删除 `root`，也不会删除 Plan 外资产。

执行任何删除前，Commandlet 会先完成：

- Schema Version、Root 和数量上限检查。
- Fixture ID 与目标唯一性检查。
- 所有源资产加载和精确类检查。
- 所有 Blueprint Parent Class 与类型检查。
- Source/Target 重叠检查。
- 既有目标 Sidecar 检查。

若预校验失败，`deletedCount` 和 `createdCount` 均为 0。

`Reset` 面向可丢弃的测试 Fixture，不是多资产事务：若预校验完成后发生底层删除、复制、编译或保存故障，报告会保留实际 `deletedCount`、`createdCount` 和错误，调用者应修复原因后重新执行 `Reset`。

## 安全边界

- `root` 必须是 `/Game` 下的具体子目录，不能是 `/Game` 本身。
- 每个 `targetAsset` 必须严格位于 `root/` 下，且使用 Package Path，不带 `.ObjectName` 后缀。
- 最多 64 个 Fixture。
- `Create` 不覆盖资产。
- `Reset` 不删除整个目录，只删除 Plan 明列目标。
- 所有目标必须是单文件 Package。
- Wrapper 在启动 UE 前运行纯 Python 预校验，并将 Plan SHA-256 传给 Commandlet；内容变化时在任何删除前拒绝。
- `VerificationOutput` 必须是工具 `Output` 目录下的专用子目录，且不能包含 Plan 或任何报告文件。
- `VerificationOutput` 及其现有父目录、子项不能是 Junction 或符号链接，防止递归清理越过工具目录。
- 完成后必须通过独立 UE 进程重载验证。

## 可重复性的含义

Plan 保证相同来源、类型和目标集合可以重复清理并重建，并且每次都产生可独立加载、类正确、Revision 可用、Package 非 Dirty 的资产。

UE 保存过程可能重新生成 Package GUID 或其他序列化元数据，因此连续 Reset 的 SHA-256 不要求相同。Revision 用于描述并验证每次实际创建结果，不是声明二进制构建可复现。
