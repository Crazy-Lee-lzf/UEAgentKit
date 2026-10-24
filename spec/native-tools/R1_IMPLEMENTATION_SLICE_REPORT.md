# R1 原生节点注册表 —— 首个实现切片报告（R1.0 / R1.1 / R1.2，R1.5 部分）

- 状态：**IMPLEMENTED + UE 5.6.1 RUNTIME-VERIFIED（有实际 UE 进程证据）**；R1.3–R1.8 未实现。
- 日期：2026-10-24
- 工作树：`E:\WorkSpace\UEAgentKit-WT\r1-node-registry`
- 分支：`feature/r1-node-registry`，基线 `b423b87`（N0 合同提交）；本切片提交在其之上，未 merge / 未 rebase / 未 reset / 未 push。
- 执行模型（已核验）：provider `cline-pass`，model `cline-pass/deepseek-v4.1-flash-1017`，`reasoningEffort=max`，非 Fast。
- 上游：`N0_ARCHITECTURE_CONTRACT.md`、`N0_REVIEW_DECISIONS_AND_PREVIEW_MIGRATION.md`、`N0_R1_READINESS_AND_GATE.md`；实现严格复用既有 `ueak.node-descriptor/1.0`、`canonical.py` 域分离哈希、`node-descriptor.schema.json`。
- 红线遵守：未改 `main`、未动其他 worktree、未触碰 Reforge / Lyra 原始 / UETest；未 push / merge / reset / `git clean` / 删除既有资产；未使用 Computer Use / SendInput / 鼠标坐标 / 前台窗口；未开放通用 Apply；未实现 R2；未改 P11 Preview、未移除 `IPythonScriptPlugin` / P8 / P9。
- 特性默认关闭：`[UEAgentKit] NativeToolRegistry=off`（GConfig，仅启动读取）；R1 只读工具默认不可见，MCP 工具计数未变。

## 1. 交付内容

| 交付 | 位置 | 说明 |
|---|---|---|
| 公开只读接口 | `Plugin/UEAgentKit/Source/UEAgentKitEditor/Public/Native/NativeNodeRegistry.h` | `INativeNodeRegistry` + `FNativeNodeRegistry`，模式/状态/清单/目录/影子工具 |
| 注册表实现 | `Private/Native/NativeNodeRegistry.cpp` | 生命周期、目录枚举、trust 分类、模板/引脚策略、descriptor 派生、稳定 ID、汇总与证据 |
| 规范 JSON / 哈希 | `Private/Native/NativeCanonicalJson.h/.cpp` | 与 `canonical.py` 逐字节一致的 UTF-8 排序、无空白、`ensure_ascii=False` 转义；浮点显式拒绝 |
| 清单加载 | `Private/Native/NativeToolManifest.h/.cpp` | `ueak.native-registry/1.0` 子集校验，失败仅禁用 native（非致命） |
| 清单资源 | `Resources/NativeTools/native-tools.v1.json` | 2 个只读工具：`graph.nodeRegistry.catalog` / `.fingerprint` |
| 配置样例 | `Config/DefaultUEAgentKit.ini` | 默认 `off`，注释说明 shadow/active |
| 自动化测试 | `Private/Native/NativeNodeRegistryTests.cpp` | `UEAgentKit.Native.R1Catalog / R1DescriptorParity / R1Lifecycle / R1ReadOnly / R1ShadowTools` |
| 离线契约测试 | `tests/native_tools/test_r1_native_contract.py` | 金值、schema、负例、导出校验、只读源码门禁 |
| 金值生成器 | `tests/native_tools/generate_native_golden.py` | 200 样本；支持 `--from-export` 使用真实 UE 导出 |
| 金值固件 | `Resources/NativeTools/Golden/node-descriptor-golden.v1.json` | 160 条 UE 实际导出 + 40 条合成边界；sha256 见 §6 |
| 运行脚本 | `scripts/RunR1NativeTests.ps1` | 构建/运行/收集证据（本地 ignored） |
| 证据 | `Build/r1-native/Evidence/`（gitignored） | 构建日志、自动化日志、报告、catalog 导出、环境与哈希、`evidence-summary.json` |

修改的既有文件仅为接线：`UEAgentKitEditorModule.cpp`（初始化/关闭注册表）、`EditorBridge.cpp`（`native.invoke` 分支 + 仅在 ready 时追加 capability）、`BlueprintContextSha256.*`（新增 `HashUtf8Bytes`，字节级哈希，不改变既有行为）。

## 2. 实现要点（与合同逐条对应）

### 2.1 R1.0 生命周期与开关

- 模式：`off`（默认）/ `shadow` / `active`；未知值 fail-closed 为 `off`。
- `off` 不读清单、不访问动作数据库、不注册任何工具；`BuildCatalog` 返回 `native-registry-unavailable`。
- 清单缺失/损坏 → 状态 `Unavailable` + `native-registry-invalid-manifest`，**不影响** Editor 启动、legacy bridge、P7–P11 菜单与写路径（代码路径已验证，见 §7 缺口）。
- 所有查询 `check(IsInGameThread())`；不缓存裸 UObject；`Shutdown()` 幂等并清空会话冻结状态。

### 2.2 R1.1 目录枚举

- 数据源 `FBlueprintActionDatabase::Get().GetAllActions()`（引擎公开 API，已读 5.6.1 头文件）。
- 一次目录调用（空宿主实测）：`spawners=38325`、`owners=3071`、`descriptors=512`（`available=762`，显式 `truncated=true`）、`unsupported=37547`、`templates created=768 / cached=0`、`pinsAllocated=768`、`durationMs≈831–979`。
- `not-a-node-action`（`UBlueprintAssetNodeSpawner`）单独计数，不进入 `unsupported[]`，不参与 `complete`。
- `unsupported[]` 有界 64 条，每条含 `reason / spawnerClass / nodeClass / trustLevel`；**全量原因直方图** `unsupportedByReason` 同时输出（本轮实测：`template-budget-exceeded=25823`、`unknown-spawner=11638`、`untrusted-spawner-excluded=80`、`action-id-collision=6`）。
- `complete = (totalUnsupported == 0)`；本轮为 `false`，未伪称完整。
- 稳定排序：descriptor 按 `actionId` 升序；unsupported 按 `(reason, spawnerClass, nodeClass)` 升序。
- `pluginSetHash`：启用插件 `Name|VersionName` 排序去重后做域哈希（`ueak.node-registry.pluginset/1`，`sha256:16`）。本轮 `sha256:1d034d339ee271ad`。
- 身份：engine `5.6.1` / CL `44394996`、插件版本 `0.8.0`、DLL SHA-256、editor session id、project name、build configuration。

### 2.3 R1.2 descriptor 派生与稳定 ID

- `discoveryKey` 五字段：`nodeClass`（`Spawner->NodeClass`）、`spawnerClass`、`graphSchema`（模板图 schema）、`actionKind`、`payload`；任何一项取不到即 `unsupported`，不填默认值。
- `actionKind` 冻结映射（仅引擎自有 spawner）：`callFunction / variableGet / variableSet / event / delegate / component / cast / macro / special`；`special` 仅接受冻结白名单（Knot / ExecutionSequence / Select / MultiGate），映射未命中 → `unknown-spawner`，**不落 special**。
- 本地化字段（`title/menuName/category/keywords`）来自 `PrimeDefaultUiSpec()`，只进入结果与 `descriptorDigest`，**绝不进入** `discoveryKey`。
- `pins[]`：`name/direction/kind/type`（type 7 字段齐备）；`kind` 由 `PinCategory == PC_Exec` 判定；容器 `none/array/set/map`；`subCategoryObject` 取对象路径。引脚数超 `MaxPinsPerDescriptor` 显式 `pins-exceeded`，不静默截断。
- `constraints` 只填可证明事实：`requiresBlueprintContext=true`；`requiresTarget`/`requiresWorldContext`/`variadic` 来自 `UFunction` 标志与元数据；`pure`/`latent` 来自节点 API；`hiddenByDefault=[]`。
- `graphKinds` 由模板图 `GetGraphType` 映射（`GT_Ubergraph→event`、`GT_Function→function`、`GT_Macro→macro`，其余 `other`），**不使用 `ubergraph`**。
- `risk=R2`（节点创建属结构写入，统一声明）、`capability=core`、`trustLevel=official`，均来自冻结规则，不按类名猜测。
- 哈希（与 `descriptors.py` / `canonical.py` 一致）：
  - `actionId = "action:" + sha256("ueak.node-action/1\n" + canonical_json(projection))[7:39]`
  - `descriptorDigest = "sha256:" + sha256("ueak.node-descriptor/1\n" + canonical_json(descriptor − descriptorDigest − source.discoveredAtUtc))[7:23]`
  - `actionDatabaseFingerprint = "sha256:" + sha256("ueak.node-action/1.database\n" + canonical_json(sorted(actionIds)))[7:23]`
- 兼容事实已入测：改 `title` → `actionId` 不变、`descriptorDigest` 变；`title` 混入 `discoveryKey` → 被判定违规（C++ shape 校验 + Python `validate_node_descriptor` + schema 负例）。
- 目录指纹在**同一 Editor 会话**内冻结：首次构建后，未能在有界预算内建模板的 spawner 被记入会话级 `DeferredSpawnerReasons`，后续调用保持同一原因，避免引擎异步模板预热导致两次调用指纹漂移。跨进程则由“同一引擎/插件/工程 → 同一 actionId 集合”保证（见 §5 T4 证据）。

### 2.4 模板与信任边界（必须显式记录）

| spawner 来源 | 行为 |
|---|---|
| `official`（引擎模块） | 先读 `GetCachedTemplateNode()`；缺失时在预算内调用 `GetTemplateNode()`（上限 768/会话、20 s/调用）；随后在**瞬态模板节点**上调用一次 `AllocateDefaultPins()` 以取得引脚形状 |
| `project` / `third-party` / 未知 owner | **在触碰任何模板 API 之前**直接 `untrusted-spawner-excluded`；不读 cached、不创建模板、不描述 |
| 模板创建返回 null | 单条 `template-unavailable`，整体继续 |
| 预算/时间耗尽 | 单条 `template-budget-exceeded`，整体继续，`complete=false` |

**偏差声明（需复核）**：为获得 `pins[]`，本轮在 768 个**瞬态模板节点**上执行了 `AllocateDefaultPins()`。这些对象位于引擎的 transient 模板 Blueprint/Graph 中，不属于任何 `/Game` package，不触发 `Modify/MarkPackageDirty/FScopedTransaction/SavePackage`（T5 证据）。这符合 N0-D05 的 “transient-only 临时对象” 例外，但严格字面上确实创建了 UObject；若用户要求“零 UObject 创建”，需将 `bAllowTransientPinAllocation=false`，代价是 `pins[]` 为空、所有 descriptor 都无法通过 schema（即 R1.2 不可交付）。

### 2.5 R1.5（部分）

- 清单：`Resources/NativeTools/native-tools.v1.json`，2 个只读工具；加载校验（名称/版本/handler/DTO 上限/线程/只读一致性/maturity/exposure/deprecated）；规范哈希 `ueak.native.registry/1`。
- 影子调用：`TryInvokeShadowTool` 严格校验 `toolId/toolVersion/arguments`，只返回只读结果；版本 MAJOR 必须相等、MINOR ≤ 清单；未知字段拒绝；响应 >1 MiB 返回 `native-limit-response-too-large`。
- bridge：新增 `native.invoke` 分支（仅当注册表 `Ready` 时 capability 才出现）；`off` 时不宣告该能力，legacy 方法/计数不变。
- 未做：MCP 工具投影、菜单绑定、DTO schema 文件解析（N0 §4.2 明确属 N1）、真实 socket 客户端驱动。

## 3. 运行环境与构建

| 项 | 值 |
|---|---|
| 引擎 | UE `5.6.1`（CL `44394996`），`E:\EPICGAME\UE_5.6` |
| 工具链 | Visual Studio 2022 `14.44.35228`（MSVC `14.44.35207`），Windows SDK `10.0.22621.0` |
| 目标 | `R1AutomationHostEditor Win64 Development` |
| 宿主 | `Build/r1-native/R1AutomationHost/R1AutomationHost.uproject`（disposable，插件以 junction 引用本 worktree） |
| 结果 | **Succeeded**；仅有 “非首选编译器版本” 与既有 `IKRig` 插件依赖警告 |
| DLL | `UnrealEditor-UEAgentKitEditor.dll`，`sha256:15480cb8b2bf5edc1363e9d1ad208ead0241d94160f01e07e038b6f9d6fcfa05` |

构建/运行严格串行（同机同一时间只有一个 UE 重型任务）；未使用 GUI、截图或 Computer Use。

## 4. 自动化结果（5/5 通过）

命令：`UnrealEditor-Cmd.exe <host>.uproject -unattended -nop4 -nosplash -NullRHI -NoSound -ExecCmds="Automation RunTests UEAgentKit.Native.R1" -TestExit="Automation Test Queue Empty" -ReportExportPath=<Evidence>\AutomationReport-...`

| 测试 | 结果 | 关键计数 |
|---|---|---|
| `UEAgentKit.Native.R1Catalog` | **Success** | spawners 38325 / owners 3071 / descriptors 512（available 762，truncated）/ unsupported 37547 / created 768 / pins 768 / 831 ms |
| `UEAgentKit.Native.R1DescriptorParity` | **Success** | 200/200 金值 actionId + canonical bytes + descriptorDigest 全等；目录 200 条形状校验；本地化负例；title→digest 变/actionId 不变 |
| `UEAgentKit.Native.R1Lifecycle` | **Success** | off → `native-registry-unavailable`；automation seam → shadow + 2 tools；幂等 |
| `UEAgentKit.Native.R1ReadOnly` | **Success** | dirty 0→0、`/Game` packages 0→0、AssetRegistry assets 0→0、undo queue 0→0、undo count 0→0 |
| `UEAgentKit.Native.R1ShadowTools` | **Success** | fingerprint/catalog 影子调用；未知 tool / 版本 2.0.0 / 未知参数 / maxItems 513 均稳定拒绝 |

报告：`Build/r1-native/Evidence/AutomationReport-20261024-014308/index.json`（`succeeded=5, failed=0, notRun=0, totalDuration=3.76 s`）。原始日志：`Build/r1-native/Evidence/automation-r1-native-20261024-014308.log`。

只读证据原文：

```
readOnlyEvidence dirtyBefore=0 dirtyAfter=0 gamePackagesBefore=0 gamePackagesAfter=0
assetsBefore=0 assetsAfter=0 undoQueueBefore=0 undoQueueAfter=0 undoCountBefore=0 undoCountAfter=0
```

**诚实限制**：宿主没有 `/Game` 资产，因此 “dirty package 不变” 在“无资产”前提下是平凡成立；本测试实际证明的是“无事务、无 Undo 栈变化、AssetRegistry 无新增”。有资产场景的 dirty 断言需在含 fixture Blueprint 的宿主补测（T5 剩余项）。

## 5. Python 离线金值与对拍

- 命令：`py -3.12 -m unittest discover -s tests/native_tools -p "test_*.py"` → **13/13 OK**。
- 金值：200 条，`160 ue-catalog + 40 synthetic-parity`；仓库 blob（LF）sha256 `97942712580b08a109cda2d716de46d4fa29a237bf3a64bb8e308e5e649f4269`（工作区按 `.gitattributes` 为 CRLF，故文件系统哈希不同，JSON 语义一致）。
- 对 512 条 **UE 实际导出** descriptor 执行 `validate_node_descriptor` + `jsonschema`（仓库 `node-descriptor.schema.json`）全部通过；`actionId`/`descriptorDigest` 独立重算全部相等。
- 导出分布：`callFunction 345 / cast 95 / variableSet 35 / variableGet 29 / delegate 5 / event 3`；11 个节点类、2411 个 pin。
- 跨进程稳定性（T4 部分证据）：5 次独立 `UnrealEditor-Cmd` 进程（013023 / 013317 / 013456 / 013748 / 014308）得到**完全相同**的 `actionDatabaseFingerprint = sha256:b09fb1387e188e02`；且金值来自 013553 进程、在 014308 进程验证通过（actionId/digest 不含时间，跨进程一致）。
- 金值负例：`title` 混入 `discoveryKey` 被 Python 校验器与 JSON Schema 同时拒绝；额外顶层字段被 `additionalProperties:false` 拒绝；未知 `actionKind` 被拒绝。

## 6. 证据清单（本地 ignored）

| 证据 | 路径 / 值 |
|---|---|
| 构建日志 | `Build/r1-native/Evidence/build-r1-native*.log`（最终 Succeeded） |
| 自动化日志 | `Build/r1-native/Evidence/automation-r1-native-20261024-014308.log` |
| 自动化报告 | `Build/r1-native/Evidence/AutomationReport-20261024-014308/index.json` |
| catalog 导出 | `Build/r1-native/Evidence/catalog-export.json`（sha256 `b4de27923e8d7dbf51bff787ddbbba2e8369ed944fdd1cb780d50a66ea208d45`） |
| 机器可读汇总 | `Build/r1-native/Evidence/evidence-summary.json` |
| 环境 | `Build/r1-native/Evidence/environment-20261024-014308.json` |
| 插件 DLL 哈希 | `sha256:15480cb8b2bf5edc1363e9d1ad208ead0241d94160f01e07e038b6f9d6fcfa05` |
| `actionDatabaseFingerprint` | `sha256:b09fb1387e188e02` |
| `pluginSetHash` | `sha256:1d034d339ee271ad` |
| 清单哈希 | 由加载器计算并记录在 shadow ready 日志（`hash=sha256:b3234bffa219ad21`，随清单内容） |

## 7. 门槛状态（对照 N0_R1_READINESS_AND_GATE.md §6 / §9）

| 项 | 状态 | 说明 |
|---|---|---|
| T1 目录 | **PASS（子集）** | 非空、两次调用指纹一致、unsupported ≤64 且直方图自洽、complete 互补、pluginSetHash 存在 |
| T2 descriptor/schema | **PASS** | 200 金值逐字节对拍；200 条目录形状；512 条导出离线 schema 校验；负例 |
| T3 上下文过滤 | 未实现 | R1.3 |
| T4 跨进程身份 | **部分 PASS** | 5 进程 fingerprint 相同 + 金值跨进程复验；无专门双进程 harness |
| T5 只读 | **PASS（宿主无资产）** | 无事务/Undo/AssetRegistry 变化；dirty 断言平凡成立，需有资产宿主补测 |
| T6 分页/边界 | 部分 | 只有 `maxItems + truncated + totalAvailable`；无 cursor |
| T7 菜单绑定 | 未实现 | 未接 ToolMenus |
| T8 门禁 | 部分 | 进程内未知 tool/版本/参数/越界已测；socket/flag 阶梯未驱动 |
| T9 性能 | 部分 | 首次目录 831–979 ms / 768 模板；未测内存峰值 |
| T10 不可信 spawner | 未实现注入测试 | 代码路径在模板访问前排除；80 条 `untrusted-spawner-excluded` 实测；无“抛错假 spawner”注入 |
| G1 N0 人工评审 | 仍待人工 | 本轮按指令把 Q2–Q8 建议当作 scoped defaults |
| G2 清单可机器校验 | 部分 | 加载器校验通过；无独立 schema 文件 |
| G3 派生表冻结 | 代码冻结 | 写入源码常量，待合同版本化 |
| G4 fixture/host | 部分 | disposable host 已建；无 `/Game` fixture BP |
| G5 T1–T10 清单评审 | 仍待人工 | — |
| G6 工作树隔离 | 已满足 | 独立 worktree / 分支 |
| G7 单 UE 重型任务 | 已满足 | 构建/运行串行 |
| G8 不开放 Apply/R2 | 已满足 | 无写路径 |
| G9 两 flag 独立 | 已满足 | 未触碰 Preview / broker |
| G10 off 非回归由测试证明 | 部分 | off 状态不可用已测；legacy capability 计数未直接断言 |

## 8. 已知缺口与风险

1. **`graphKinds` 过窄**：模板图是引擎模板缓存创建的 function graph，本轮 512 条全部为 `["function"]`。这不是“该动作只能用于 function 图”的兼容性承诺；R1.3 上下文通道才是权威。合同若要求 `graphKinds` 表达真实可用图类型，需要新增冻结映射或真实上下文探测（不得猜测）。
2. **模板预算导致目录覆盖有限**：`totalDescriptorsAvailable=762`（512 条被 `maxItems` 截断），相对 38325 个 spawner 只是很小子集；`complete=false` 已如实声明。扩大覆盖需提高预算并评估内存/GC。
3. **跨进程 actionId 稳定性只做了观测，没有独立双进程断言测试**；T4 正式化仍缺。
4. **T5 无资产**：dirty 不变在空宿主上平凡成立。
5. **T10 未注入假 spawner**：仅靠代码顺序（trust 检查先于模板访问）+ 80 条实测排除证据，未做故障注入。
6. **R1.5 socket 路径未驱动**：`native.invoke` 分支只做了进程内单测；未用 Python 客户端连真实 Editor 验证。
7. **清单损坏非致命性未运行时演示**：为避免改动已交付资源，未在运行中破坏清单；该路径为代码审查级验证。
8. **`descriptorDigest` 包含本地化文本**：按兼容优先实现并测试（改文案 → digest 变、actionId 不变）；若未来要稳定 digest，必须走 descriptor 1.1，本轮未私改。
9. **图 schema 仅来自模板**：无模板即 `graph-schema-unavailable` 排除；未用上下文图 schema 回退（属 R1.3）。
10. **透明性**：768 个瞬态模板节点 + `AllocateDefaultPins()` 是本实现取得 pins 的必要步骤；未写资产、未开事务、未 Save。

## 9. 复现

```powershell
# 构建（单实例）
E:\EPICGAME\UE_5.6\Engine\Build\BatchFiles\Build.bat R1AutomationHostEditor Win64 Development `
  -Project="E:\WorkSpace\UEAgentKit-WT\r1-node-registry\Build\r1-native\R1AutomationHost\R1AutomationHost.uproject" `
  -WaitMutex -NoHotReload

# UE 自动化（默认 off 启动；测试内部以 automation seam 切到 shadow）
powershell -File scripts\RunR1NativeTests.ps1 -SkipBuild -Filter "UEAgentKit.Native.R1"

# 离线契约/金值（会校验 Evidence/catalog-export.json 若存在）
py -3.12 -m unittest discover -s tests\native_tools -p "test_*.py"

# 用真实导出重建金值（如需）
py -3.12 tests\native_tools\generate_native_golden.py --from-export Build\r1-native\Evidence\catalog-export.json
```

## 10. 结论与下一步

R1.0–R1.2 的**最小真实纵向切片**已实现并在 UE 5.6.1 disposable 宿主中通过 5/5 自动化与 13/13 离线测试；descriptor 与既有 Python 合同逐字节对拍通过，512 条真实导出通过仓库 schema/校验器；只读边界（无事务、无 Undo、无 AssetRegistry 新增）有实测证据；精确缺口已列于 §7–§8。

建议下一步（需新指令，不在本轮范围）：

1. 增加 `/Game` fixture Blueprint 宿主，补 T5 的真实 dirty/revision 断言。
2. 实现 R1.3 上下文过滤（引擎 `MakeContextMenu` / `FBlueprintActionFilter`），以上下文图 schema 修正 `graphKinds` 语义。
3. 把 T4 做成“双进程 + 导出对拍”脚本，冻结插件集/引擎集差异诊断。
4. 若接受 R1.5 方向，补 socket 客户端探针与清单损坏负例；否则保持 shadow 内部形态并删除 bridge 分支。
5. 在人工评审确认前，不进入 R2 / Apply。
