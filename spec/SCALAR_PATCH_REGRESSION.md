# UEAgentKit Scalar Patch Regression 1.0

Scalar Patch Regression 用于对 `setAssetProperty` 当前支持的全部标量类型执行真实 Unreal Engine 写入回归，而不是只做 Python 或反射静态测试。

## 正式入口

```bat
scripts\RunScalarPatchRegression.cmd ^
  -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject"
```

可选参数：

- `-EngineRoot`：UE5.6 根目录。
- `-Output`：回归输出目录，必须是工具 `Output` 下的专用子目录。
- `-FixturePlan`：标量 Fixture Plan，默认使用 `tests/fixtures/scalar_patch_regression_plan.json`。

## 原生测试资产

插件提供仅用于编辑器测试的：

```text
/Script/UEAgentKitEditor.UEAgentKitScalarWriteFixtureAsset
```

它继承 `UDataAsset`，具有固定默认值：

| Property | UE Property | 默认值 | 回归值 |
|---|---|---:|---:|
| `BoolValue` | BoolProperty | `false` | `true` |
| `ByteValue` | ByteProperty | `7` | `201` |
| `IntValue` | IntProperty | `-17` | `2048` |
| `Int64Value` | Int64Property | `1234567890123` | `-4000000000000` |
| `FloatValue` | FloatProperty | `1.25` | `3.75` |
| `DoubleValue` | DoubleProperty | `-2.5` | `123.125` |
| `StringValue` | StrProperty | `Initial String` | `Updated String 0.4.3` |
| `NameValue` | NameProperty | `InitialName` | `UpdatedName043` |
| `TextValue` | TextProperty | `Initial Text` | `Updated Text 0.4.3` |
| `EnumValue` | EnumProperty | `Alpha` | `Beta` |
| `LegacyEnumValue` | enum-backed ByteProperty | `UEAK_LegacyAlpha` | `UEAK_LegacyBeta` |

Fixture 通过 Write Fixture Plan 的 `scalarAsset` Kind 创建和重置，不依赖测试工程中的任意业务资产。

## 正向矩阵

脚本固定执行：

1. Reset 原生 Scalar Fixture，并通过独立 UE 进程验证默认值。
2. 对 11 个属性分别执行一次 Dry Run。
3. 每次 Dry Run 验证：
   - 目标 Property 类型正确。
   - 内存值发生预期变化。
   - 原值精确恢复。
   - `rollbackValueMatch=true`。
   - `diskUnchanged=true`。
   - Package 文件 SHA-256 不变。
4. 所有 Dry Run 后重新启动 UE，验证 11 个属性仍为默认值，Revision 未变化。
5. 对 11 个属性顺序 Commit。
6. 每次 Commit 验证：
   - 保存成功且 Revision 变化。
   - 外部 `.bak` 与 Backup Manifest 存在。
   - 独立 UE 进程重新导出。
   - 当前属性和此前所有已提交属性均持久化。
   - Canonical Revision 与 Commit Report 完全一致。
7. 最后 Reset Fixture，并独立验证所有默认值恢复。

## 失败矩阵

最终 Reset 后固定执行 6 个预期失败：

| Case | 拒绝层 | 预期结果 |
|---|---|---|
| 未授权属性 | Python Policy 校验 | `asset-property-not-allowed` |
| 过期 Revision | Python Revision 校验 | `revision-conflict` |
| 数值属性使用 JSON String | UE AssetPatch | 类型拒绝 |
| Byte 值 `300` | UE AssetPatch | 范围拒绝 |
| 非法 Enum 名称 | UE AssetPatch | Enum 拒绝 |
| 不存在的 Property | UE AssetPatch | 目标拒绝 |

每个失败用例都必须满足：

- `RunPatch` 返回失败。
- `summary.json` 记录拒绝阶段；预校验失败还记录明确错误码。
- `.uasset` SHA-256 前后完全一致。
- 全部失败结束后，独立 UE 进程重新导出。
- 11 个默认值和 Package Revision 均保持不变。

## 输出摘要

`summary.json` 至少包含：

```json
{
  "dryRunCount": 11,
  "commitCount": 11,
  "failureCount": 6,
  "finalResetVerified": true,
  "failureMatrixDiskUnchanged": true
}
```

摘要同时记录每次 Dry Run、Commit、备份、Manifest、独立重载和失败路径报告位置。

## 安全边界

- 输出只能位于工具 `Output` 的专用子目录。
- 输出路径及其内容不能包含 Junction 或符号链接。
- Fixture Plan 不能位于即将清理的输出目录内。
- 所有测试资产严格位于 `/Game/UEAgentKitWriteTests/ScalarRegression/`。
- 脚本正常完成时将 Fixture Reset 回默认值；异常中断时只允许残留在隔离测试目录。
- 测试资产类位于 Editor Module，仅用于编辑器和 Commandlet 回归，不应作为游戏运行时业务类型使用。
