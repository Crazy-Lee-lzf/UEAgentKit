# R1.3 Blueprint 上下文过滤 + 真实资产只读门禁 —— 验收报告

- 状态：**IMPLEMENTED + UE 5.6.1 RUNTIME-VERIFIED（真实 Editor 进程）**；仅 R1.3 切片，未进入 R2 / 通用 Apply。
- 日期：2026-10-24
- 工作树：`E:\WorkSpace\UEAgentKit-WT\r1-node-registry`
- 分支：`feature/r1-node-registry`，起点 `6a0c162`（R1.0–R1.2 已提交）；本切片新增本地提交，未 push / merge / rebase / reset / clean / 删除。
- 执行模型（已核验）：provider `cline-pass`，model `cline-pass/deepseek-v4.1-flash-1017`，`reasoningEffort=max`，非 Fast；同一 DSH session，未切换模型/Provider。
- 上一轮 DSH 以 “model stopped:error” 结束：**不把上一轮记为成功**。本轮先核验 HEAD、干净工作树与既有提交，再重新构建与重跑全部 UE/Python 测试；以下数据均来自本轮真实进程。

## 0. 结论先行

1. R1.3 已实现：对**已加载**的 Blueprint 图/节点/引脚做只读上下文过滤，返回与 `FBlueprintActionFilter` 一致的上下文动作集；目录（catalog）与上下文（context）是两条明确区分的通道，共享同一 `actionId`/`descriptorDigest`。
2. 上下文过滤只评估会话目录中**已描述的 official spawner（512 条）**，不会触碰 project/third-party/unknown spawner 的模板；这是与 `MakeContextMenu` 全量枚举的关键安全差异，已显式记录。
3. T3/T4/T5/T10 已落地：T3 正/负例（palette/node/exec pin/data pin、未知图/未知 pin/未加载资产/非 K2）；T4 两个独立 `UnrealEditor-Cmd` 进程逐 actionId/digest/指纹对拍；T5 使用真实 `/Game` fixture，dirty/磁盘 revision/图指纹/事务/AssetRegistry 全部前后不变；T10 不可信 spawner 在模板访问前拒绝且不产生模板缓存。
4. `graphKinds` 语义已纠正：descriptor 的 `graphKinds` 只是**模板图提示**，不是“可用性”；上下文结果用 `semantics` 明确标注，并由 event 图中的真实正例证明“function 提示 ≠ 只能在 function 图使用”。
5. 覆盖仍然不完整：38325 spawner 中仅 512 条被描述、762 条可描述；上下文只覆盖这 512 条（`notDescribedActions=37813`），未提高 768 模板预算，未伪称全覆盖。
6. R1.3 重建后的 DLL 哈希为 `sha256:56d01e7d0103e9ec27eb81cf339f5eeead73888aa6c70f86a99a146cd49200ff`；已同步到本报告、RunState 与 `evidence-summary-r13.json`。先前 R1.2 实际 DLL 哈希为 `sha256:15480cb8b2bf5edc1363e9d1ad208ead0241d94160f01e07e038b6f9d6fcfa05`（更早报告里的 `74ded…` 是再之前一次构建，已不再作为现行值）。

## 1. 交付

| 交付 | 位置 | 说明 |
|---|---|---|
| 上下文 API | `Public/Native/NativeNodeRegistry.h` | `FNativeContextRequest/Result`、`FilterContext()`、automation seams |
| 上下文实现 | `Private/Native/NativeNodeRegistry.cpp` | 资产/图/节点/pin 解析、K2 校验、`FBlueprintActionFilter` 评估、指纹、上限/截断、目录缓存 |
| 测试 | `Private/Native/NativeNodeRegistryTests.cpp` | 新增 `R1ContextFilter`、`R1UntrustedSpawner`；`R1ReadOnly` 改为真实 fixture；`R1Lifecycle` 3 工具 |
| 影子工具 | `Resources/NativeTools/native-tools.v1.json` | 新增 `graph.nodeRegistry.filterContext`（readOnly、bridge `native.invoke`） |
| 清单加载 | `Private/Native/NativeToolManifest.cpp` | handler 白名单加入 filterContext |
| 双进程脚本 | `scripts/RunR1TwoProcessParity.ps1` | 串行跑 A/B 两个独立 Editor，调用 Python 对拍 |
| 运行脚本 | `scripts/RunR1NativeTests.ps1` | 新增 `-Label`、context export 收集 |
| 离线测试 | `tests/native_tools/test_r1_context.py` | context export 结构/语义/负例 |
| 双进程测试 | `tests/native_tools/test_r1_cross_process.py` | actionId/digest/指纹/pluginSetHash/直方图/context 指纹精确比较 |
| 证据 | `Build/r1-native/Evidence/`（ignored） | 两次自动化日志/报告、`catalog-export.json`、`context-export.json`、`t4/run-a|run-b`、`evidence-summary-r13.json` |

## 2. 实现细节

### 2.1 上下文解析（fail-closed）

请求：`assetPath` + `graphGuid` 必填；`nodeGuid`、`pinName` 可选；`maxItems` 1–512（默认 128）。

| 情况 | 行为 |
|---|---|
| 资产未加载（`FSoftObjectPath::ResolveObject` 为空） | `graphpatch-asset-not-loaded`；**绝不 LoadObject** |
| 图 GUID 不存在 | `graphpatch-graph-not-found` |
| 非 K2 图 | `graphpatch-context-mismatch`（消息说明非 K2） |
| 节点 GUID 不存在/不属于图 | `graph-node-not-found` |
| pin 不存在/不属于节点 | `graph-pin-not-found` |
| `pinName` 无 `nodeGuid` | `native-schema-invalid-arguments` |
| 上下文种类 | 无 node/pin=`palette`；有 node=`node`；有 pin=`pin` |

### 2.2 引擎过滤语义（为什么不用 `MakeContextMenu` 全量枚举）

- 使用公开 `FBlueprintActionFilter`（UE 5.6.1 头文件已核验）对**已描述 official spawner** 集合逐条 `IsFiltered(FBlueprintActionInfo(Owner, Spawner))`。
- palette 配置镜像 `FBlueprintActionMenuUtils::MakePaletteMenu(Context, nullptr, …)`：`BPFILTER_NoFlags`，并拒绝 `UK2Node_VariableGet/Set`。
- node/pin 配置镜像 `MakeContextMenu` 的上下文敏感标志：`RejectGlobalFields | RejectNonImportedFields | RejectIncompatibleThreadSafety`；pin 继承类型类时加入 `TargetClasses`（`FBlueprintActionFilter::Add`）。
- **不调用** `MakeContextMenu/MakePaletteMenu` 本身：menus 会遍历整个动作数据库，过滤测试可能对 project/third-party spawner 调用 `GetTemplateNode()`，与“不可信 spawner 不触发模板”约束冲突。这里牺牲“菜单文本/分组完全一致”，换取可信边界；差异已在 §5 明示。
- 只评估目录中已描述的 512 条 official action；`evaluatedActions=512`，`notDescribedActions=37813` 明确暴露覆盖缺口。

### 2.3 标识与语义

- 上下文动作复用目录 descriptor 的 `actionId`/`descriptorDigest`（同一 spawner 在 catalog 与 context 必须相同），T3 与 Python 测试都断言这一点。
- 上下文集合按 `actionId` 升序；`actionSetFingerprint = sha256("ueak.node-action/1.context\n" + canonical_json(sorted(ids)))[:16]`（R1.3 本地域，报告与证据中明确标注，不改既有合同域）。
- `graphKinds` 语义：descriptor 仍保留 R1.2 值（来自瞬态模板图，实测全为 `["function"]`），但 context 结果 `semantics` 明确写：
  - `catalogGraphKinds = "template-context-hint"`
  - `contextAuthority = "FBlueprintActionFilter"`
  - 并附注“不是可用性承诺”。
- T3 在 fixture 的 **EventGraph（graphKind=event）** 中找到带 `function` hint 的上下文有效动作，证明不能把 hint 当限制。

### 2.4 真实 fixture 与只读门禁

- fixture：`/Game/R1Native/BP_R1ContextFixture`（disposable host 的 `Content`，gitignored），EventGraph 内含一个 `KismetSystemLibrary.PrintString` 调用节点。
- 创建/保存只发生在测试 setup：`CreateBlueprint` → 加节点 → `CompileBlueprint` → `UPackage::SavePackage`；随后确认 `packageDirty=false` 才开始测量。
- T5 测量项：`/Game` dirty 包数、`/Game` 包数、AssetRegistry `/Game` 资产数、`GEditor->Trans` queue/undo count、fixture 磁盘 `.uasset` SHA-256（asset revision）、fixture 图指纹（节点 GUID/类型/位置/标题/pin/链接排序后 SHA-256）。
- 测量期间调用：完整 catalog、palette/node/pin 三种 context、catalog/fingerprint 影子工具；随后比较全部指标。
- 实测（run A/B 相同）：`dirty 0→0`、`gamePackages 1→1`、`assets 1→1`、`undoQueue 0→0`、`undoCount 0→0`、`revision 945c…→945c…`、图指纹 run 内前后相同。**T5 不再是空宿主上的平凡断言。**

### 2.5 T4 双进程与 T10

- `RunR1TwoProcessParity.ps1` 串行启动两次独立 `UnrealEditor-Cmd`（不并发），各自导出 catalog/context，Python 断言：
  - 512 个 actionId 完全相等；
  - `actionDatabaseFingerprint` 相等（`sha256:b09fb1387e188e02`）；
  - `pluginSetHash` 相等（`sha256:1d034d339ee271ad`）；
  - 每个 actionId 的 `descriptorDigest` 相等；pluginBuildHash/engine/pluginVersion 相等；
  - `unsupportedByReason` 直方图相等；
  - palette/node/execPin/dataPin 的 `actionSetFingerprint` 与 actionId 列表相等。
- T10：`DescribeSpawnerForAutomation` 以 ThirdParty 信任描述一个临时 `UBlueprintNodeSpawner` → `untrusted-spawner-excluded`，且 `GetCachedTemplateNode()==nullptr`；以 official 处理但 spawner 类/节点类不在白名单 → `unknown-spawner`，同样无模板。未向动作数据库注入任何对象。

## 3. 实测结果

### 3.1 构建

| 项 | 值 |
|---|---|
| 目标 | `R1AutomationHostEditor Win64 Development` |
| 结果 | **Succeeded**（仅既有的“非首选 MSVC 版本”与 IKRig 依赖警告） |
| 工具链 | VS 2022 14.44.35228 / MSVC 14.44.35207 / Win SDK 10.0.22621.0 |
| 最终 DLL | `sha256:56d01e7d0103e9ec27eb81cf339f5eeead73888aa6c70f86a99a146cd49200ff` |
| 构建日志 | `Build/r1-native/Evidence/build-r13-final.log` |

### 3.2 UE Automation（7/7 通过，两个独立进程各 7/7）

| 测试 | run A | run B | 关键计数 |
|---|---|---|---|
| `R1Catalog` | Success | Success | spawners 38325 / owners 3071 / descriptors 512（available 762）/ unsupported 37547 / created 768 / pins 768 / 926–1057 ms |
| `R1ContextFilter` | Success | Success | described 512 / evaluated 512 / rejected 215 / palette 443→128 / node 431→128 / exec 297→128 / data 12→12 / exec fp `13e68f6732b07f76` |
| `R1DescriptorParity` | Success | Success | 200/200 金值逐字节通过（R1.2 兼容保持） |
| `R1Lifecycle` | Success | Success | off→unavailable；shadow 3 tools（catalog/fingerprint/filterContext） |
| `R1ReadOnly` | Success | Success | 真实 fixture，所有只读指标前后不变（见 §2.4） |
| `R1ShadowTools` | Success | Success | catalog/fingerprint 影子调用 + 负例 |
| `R1UntrustedSpawner` | Success | Success | `untrusted-spawner-excluded` / `unknown-spawner`，`templateCached=no` |

- 报告：`Evidence/AutomationReport-20261024-022230/index.json`、`…-022249/index.json`；日志同名。
- context 耗时（目录预热后）：palette 21.4 ms、node 20.4 ms、exec pin 20.9 ms、data pin 19.3 ms；目录首次 926–1057 ms。

### 3.3 Python 离线（26/26 通过）

- `py -3.12 -m unittest discover -s tests/native_tools -p "test_*.py"` → **26 tests OK**。
- 包含：R1.2 金值 200 条、512 条导出 descriptor 的 `validate_node_descriptor` + JSON Schema、R1.3 context export 7 项、T4 双进程 6 项、源码只读守卫。
- 上下文导出结构：palette/node/execPin/dataPin 全部 `ok=true`，`graphKind=event`，`graphSchema=/Script/BlueprintGraph.EdGraphSchema_K2`，`nonK2ErrorCode=graphpatch-context-mismatch`。

### 3.4 T4 双进程精确对拍

| 项 | run A | run B | 结论 |
|---|---|---|---|
| actionId 数 | 512 | 512 | 集合/顺序完全相等 |
| `actionDatabaseFingerprint` | `sha256:b09fb1387e188e02` | 同左 | 相等 |
| `pluginSetHash` | `sha256:1d034d339ee271ad` | 同左 | 相等 |
| 每个 actionId 的 descriptorDigest | — | — | 全部相等 |
| unsupported 直方图 | 6/25823/11638/80 | 同左 | 相等 |
| context actionSetFingerprint | `f7a05… / ded73… / 13e68… / d852f…` | 同左 | 四个上下文全部相等 |

## 4. DLL 哈希一致性（修正上一轮偏差）

| 阶段 | 实际 DLL SHA-256 | 记录位置 |
|---|---|---|
| R1.2 最终实测 | `15480cb8b2bf5edc1363e9d1ad208ead0241d94160f01e07e038b6f9d6fcfa05` | 上一轮报告已按此修正（`74ded…` 为更早构建，作废） |
| R1.3 本轮重建 | `56d01e7d0103e9ec27eb81cf339f5eeead73888aa6c70f86a99a146cd49200ff` | 本报告、RunState、`evidence-summary-r13.json`、两次 `environment-*.json` |

本报告与 RunState 只使用上表“现行值”；任何未同步的旧值均视为历史记录，不作为本轮证据。

## 5. 已知差异与缺口（不隐瞒）

1. **palette 语义是“镜像过滤配置”，不是调用 `MakePaletteMenu`**：为避免菜单构建遍历并触发不可信 spawner 的模板，palette 用同一 `FBlueprintActionFilter` 配置直接评估已描述集合。分组/文本/菜单排序不在本切片范围。
2. **上下文只覆盖 catalog 已描述的 512 条**（`notDescribedActions=37813`，占 38325 的绝大多数）；`complete=false`、`truncated` 由 `maxItems` 控制。未提高 768 模板预算（无不含内存证据的扩容）。
3. **`maxItems` 截断**：palette/node/exec 均命中 128 上限（matched 443/431/297），data pin 12 条未截断；`truncated=true` 显式返回。没有 cursor 分页（T6 未做）。
4. **图指纹跨进程不一致**：run A 与 run B 的 fixture 图指纹不同（`88370…` vs `20b67…`），而磁盘 revision 相同；推测与 fixture 载入/编译状态或本地化标题有关。T5 只断言**同一进程内**前后不变（已通过）；跨进程稳定性证据使用 catalog/context 指纹，不使用 fixture 图指纹。
5. **T4 只比较 catalog/context 导出**，未覆盖“蓝图 recompile 后”的动作集合变化（T4 剩余项）。
6. **T8 的 socket 路径未驱动**：`native.invoke` 新工具只在进程内单测；未用 Python 客户端连真实 Editor。
7. **T6/T7/T9 未做**：无 cursor 分页、无 ToolMenus 绑定、无模板缓存峰值内存测量。目录首次 <1.1 s、context ~20 ms 仅为单机观测，非阈值承诺。
8. **`graphKinds` 仍是模板图提示**（全部 `["function"]`），只是不再被解释为可用性；R1.3 上下文才是权威。
9. **不可信 spawner 的“抛错注入”未做**：T10 用真实临时 spawner 证明 fail-closed + 不建模板，但没有构造会抛异常的第三方规则。代码路径先 trust 后模板，已由 seam 断言。

## 6. T1–T10 / G1–G10 状态

| 项 | 状态 | 说明 |
|---|---|---|
| T1 目录 | PASS | 非空、两次指纹一致、unsupported 有界 + 直方图、complete 互补、pluginSetHash |
| T2 descriptor/schema | PASS | 200 金值 + 512 导出 schema/校验器；负例 |
| T3 上下文过滤 | **PASS（核心）** | palette/node/exec/data 正例；未知图/pin/未加载资产/非 K2 负例；确定性；ID 与目录一致 |
| T4 跨进程 | **PASS（导出级）** | 两独立进程 actionId/digest/指纹/插件集/直方图/context 指纹全等；recompile 场景未覆盖 |
| T5 只读 | **PASS（真实资产）** | dirty/revision/graph fingerprint/undo/AssetRegistry 前后不变 |
| T6 边界/分页 | 部分 | `maxItems/truncated/totalAvailable`；无 cursor |
| T7 菜单绑定 | 未做 | 不接 ToolMenus |
| T8 门禁 | 部分 | 进程内工具/参数/上下文负例；socket 未驱动 |
| T9 性能 | 部分 | 目录 0.93–1.06 s、context ~20 ms；未测内存峰值 |
| T10 不可信 spawner | **PASS（负例）** | 模板访问前拒绝、无 cached template；无异常注入 |
| G1 人工评审 N0 | 仍待人工 | 本轮按指令执行 scoped defaults |
| G2 清单机器校验 | 通过 | 3 工具清单加载通过 |
| G3 派生表冻结 | 代码冻结 | R1.2 不变 |
| G4 fixture/host | **已满足** | `/Game/R1Native/BP_R1ContextFixture` |
| G5 T1–T10 评审 | 仍待人工 | — |
| G6 工作树隔离 | 满足 | 独立 worktree/分支 |
| G7 单 UE 重型任务 | 满足 | 构建/运行 A/B 全程串行 |
| G8 不开放 Apply/R2 | 满足 | 无写路径 |
| G9 两 flag 独立 | 满足 | 未触碰 Preview/broker |
| G10 off 非回归 | 部分 | off 不可用已测；legacy capability 计数未直接断言 |

## 7. 复现

```powershell
# 构建（单实例）
E:\EPICGAME\UE_5.6\Engine\Build\BatchFiles\Build.bat R1AutomationHostEditor Win64 Development `
  -Project="E:\WorkSpace\UEAgentKit-WT\r1-node-registry\Build\r1-native\R1AutomationHost\R1AutomationHost.uproject" `
  -WaitMutex -NoHotReload

# 单进程 7 个测试
powershell -File scripts\RunR1NativeTests.ps1 -SkipBuild -Filter "UEAgentKit.Native.R1" -Label run-a

# 双进程 T4 + Python 对拍（串行两个 Editor）
powershell -File scripts\RunR1TwoProcessParity.ps1 -SkipBuild

# 全量离线（26 tests）
py -3.12 -m unittest discover -s tests\native_tools -p "test_*.py"
```

## 8. 结论

R1.3 的上下文过滤与真实资产只读门禁已经实现并通过真实 UE 5.6.1 双进程验收：上下文集合由引擎 `FBlueprintActionFilter` 决定，与目录共享稳定 ID；T5 在真实 `/Game` fixture 上证明零资产写入；T4 两进程逐 actionId/digest/指纹相等；T10 证明不可信 spawner 不触发模板。覆盖仍只限 512/38325，已明确声明，不冒充全量。

下一步（需新指令）：T6 cursor 分页、T7 菜单绑定、T8 socket 驱动、T9 内存测量、T4 recompile 场景；以及是否接受“palette 镜像过滤而非 MakePaletteMenu”的语义差异。**不进入 R2/Apply。**
