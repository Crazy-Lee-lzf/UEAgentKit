# N0 → R1 只读准备审查与门槛

- 状态：**READ-ONLY DESIGN / 未实现 / 未在 UE 运行**
- 日期：2026-10-23
- 工作树：`E:\WorkSpace\UEAgentKit-WT\native-tools-r1`（`feature/native-tools-contract-r1`），基线 `main` `c5ce3419`
- 上游：`N0_ARCHITECTURE_CONTRACT.md`（本目录）、`spec/graph-contracts/D01_D13_USER_DECISIONS.md` D-04/D-06/D-12、`spec/LIVE_EDITOR_BRIDGE.md`
- 结论先行：R1 技术路线可行，**用“动作数据库目录 + 引擎上下文过滤”双通道**，只读、不 spawn、不写 UE 资产；但必须先冻结 descriptor 派生规则与 fixture 金值，否则 `actionId` 会随实现漂移。

## 1. 范围与非目标

R1 定义：在原生 C++ 中提供 **只读** Blueprint Node Registry — 能回答“当前引擎/工程下有哪些可创建节点动作、它们的类型/Spawner/Pin 模板/上下文兼容性/稳定标识是什么”。

| 做 | 不做 |
|---|---|
| 枚举 `FBlueprintActionDatabase` 中的 node spawner | 创建/删除/spawn 任何节点（D-04 明确首轮仅 discovery/descriptor/preview） |
| 生成 `ueak.node-descriptor/1.0` 兼容描述符 | 修改 `ueak.node-descriptor/1.0` 或 `ueak.operation/1.0` schema |
| 上下文过滤（graph / node / pin / palette） | 生成 graph patch、连线、改默认值 |
| 稳定 ID 与指纹（actionId / descriptorDigest / database fingerprint） | 写盘、Save、Compile、Undo/Redo |
| 与 Python `graph_contracts` 离线基线对拍 | 让 Python 在 UE 进程内运行 |
| UE 5.6 自动化验证清单（本轮只写清单，不执行） | 启动 UE、编译、Computer Use |

红线：R1 的任何工具都不得导致 `Package->IsDirty()` 从 false 变 true、不得开 `FScopedTransaction`、不得新增资产。此条由 §6 的只读自动化测试强制。

## 2. 可复用的既有契约资产（不要另造）

| 资产 | 位置 | 复用方式 |
|---|---|---|
| NodeDescriptor 1.0 候选 | `src/ue_agent_kit/graph_contracts/descriptors.py:24-27`、`schemas/node-descriptor.schema.json` | R1 输出**必须**通过该 schema |
| actionId 规则 | `descriptors.py:86-104`（`discovery_key_projection` + `make_action_id`） | 原样实现，不得改字段集或排序 |
| 规范 JSON 与域分离哈希 | `graph_contracts/canonical.py:45-111`（`canonical_json`、`domain_hash`、`fingerprint`） | 必须逐字节一致（含 `.17g` 浮点、UTF-8 键排序） |
| 域标签 | `canonical.py:24-38`（`ueak.node-action/1`、`ueak.node-descriptor/1`） | 不得新增/改名 |
| 错误码表 | `schemas/error-codes.json`、`schemas/condition-codes.json` | 新码必须先入表 |
| 图类型 | `EGraphType`（`EdGraphSchema.h:25-33`） | 与 D-01 的 event/ubergraph 区分一致 |
| test-only 门禁形态 | `P9TestOnly.cpp:246-340` | R1 只读**不需要**门禁；若将来加写探针必须复用 |

R1 的 descriptor 是**读出来的**，不是手写的：任何 fixture 的金值应由 UE 导出后与 Python 校验器对拍，而不是人工填 JSON。

## 3. 技术选型

### 3.1 三条候选

| 方案 | 做法 | 优点 | 缺点 | 判定 |
|---|---|---|---|---|
| A. 直接枚举动作数据库 | `FBlueprintActionDatabase::Get().GetAllActions()` 遍历 `FActionRegistry`，自行对每个 spawner 取模板/签名 | 不依赖菜单构建、可控、可全量 | 过滤逻辑要自己重写，可能与真实菜单不一致；全量遍历成本未知 | **采用（目录通道）** |
| B. 复用引擎菜单构建 | `FBlueprintActionContext{Blueprints,Graphs,Pins}` + `BlueprintActionMenuUtils::MakeContextMenu` + `FBlueprintActionMenuBuilder`，枚举 `GetSchemaAction` | “所见即菜单”：过滤/目标类/上下文语义与编辑器一致，无需重写规则 | 依赖 Kismet/GraphEditor；文本/template 生成较重；time-slicing 需处理 | **采用（上下文通道）** |
| C. `UEdGraphSchema::GetContextMenuActions` / Slate 菜单 | 通过 schema 的 UI 回调或 `SGraphEditorActionMenu` | 最贴近 UI | UI 耦合、不可在无 Slate 的自动化中稳定调用、返回的是菜单项而非数据 | 拒绝 |

**决定 R1-T01：A 出“目录（catalog）”，B 出“上下文可创建集（context）”，两者共享同一 descriptor 投影与同一 actionId 规则。** 目录用于 Agent 搜索/文档/差异检测；上下文用于“在这个图上、从这个 pin 能建什么”。二者结果不要求相等，但同一 spawner 的 descriptor 必须逐字段相同（由 R1.3 测试断言）。

### 3.2 已核验的 API 面（`HEADER-VERIFIED`，非运行实测）

- `FBlueprintActionDatabase`：`Get()`（首次访问会 populate，头注释 `BlueprintActionDatabase.h:58-64`）、`GetAllActions()`（`:92`）、`OnEntryUpdated()`（`:166`）。`FActionRegistry = TMap<FObjectKey, TArray<TObjectPtr<UBlueprintNodeSpawner>>>`（`:49-52`）。
- `FBlueprintActionContext`：`Blueprints/Graphs/Pins/SelectedObjects`（`BlueprintActionFilter.h:47-83`）；`FBlueprintActionFilter::IsFiltered`（`:370`）。
- `BlueprintActionMenuUtils::MakeContextMenu(Context, bIsContextSensitive, ClassTargetMask, MenuOut)`（`BlueprintActionMenuUtils.h:61`）；`MakePaletteMenu`（`:49`）。
- `FBlueprintActionMenuBuilder`（`BlueprintActionMenuBuilder.h:32`）、`FBlueprintActionMenuItem::GetRawAction()`（`BlueprintActionMenuItem.h:56`）。
- 枚举：`FGraphActionListBuilderBase::GetNumActions()/GetSchemaAction(int32)`（`EdGraphSchema.h:591,594`）；5.5 起 `ActionGroup` 已弃用（`:489`）。
- `UBlueprintNodeSpawner`：`GetSpawnerSignature()`（`BlueprintNodeSpawner.h:186`）、`GetCachedTemplateNode()`（`:221`）、`GetTemplateNode()`（`:233`）、`IsTemplateNodeFilteredOut()`（`:245`）。
- 模板节点创建路径：模板节点位于 **transient 兼容 Blueprint** 的图里，`SpawnEdGraphNode` 对 template outer 跳过 `Modify()`/`AddNode()`（`BlueprintNodeSpawner.cpp:339-351`）；缓存有内存上限（`BlueprintNodeTemplateCache.cpp:78-96,120`）。
- 图节点上下文类：`UGraphNodeContextMenuContext`（`EdGraphNode.h:214`）。

证据：`Build/n0-contract/Evidence/02-ue56-public-api-verification.txt`（含每个头文件 SHA-256）。

### 3.3 必须承认的未知

以下都是 `UNVERIFIED`，必须由 §6 自动化或人工确认，不得在报告中写成已证明：

1. 动作数据库首次 prime 的耗时与内存（大工程/大量插件时）。
2. 模板缓存达到上限后的行为（丢弃/降级/重建）与对 descriptor 稳定性的影响。
3. `MakeContextMenu` 在 `bUseTimeSlicing` 默认关闭时是否仍会返回不完整集合（`GetNumPendingActions()` 是否恒 0）。
4. 同一 spawner 在不同 graph schema 下的 `GetTemplateNode()` 是否产生不同 descriptor（多 schema 工程）。
5. 第三方/项目插件注册的 spawner 是否有异常实现（`IsTemplateNodeFilteredOut` 抛错/卡死）。
6. Commandlet（`-run=`）环境下动作数据库是否可用；R1 计划只用 Editor 上下文 + 自动化，不用 commandlet。

## 4. 功能拆分（R1.1 – R1.8）

| 编号 | 交付 | 依赖 | 验收要点 |
|---|---|---|---|
| R1.1 | 目录枚举：遍历 `GetAllActions()`，统计 spawner 数、owner 数、未知类计数 | 无 | 同会话两次枚举 fingerprint 一致；`unsupported[]` 有界（≤64 项） |
| R1.2 | descriptor 派生：`discoveryKey`（nodeClass/spawnerClass/graphSchema/actionKind/payload）、`pins[]`、`constraints`、`risk`/`capability`/`trustLevel` | R1.1 | 派生表冻结；未知 spawner → 排除并计入 unsupported，**不猜测** |
| R1.3 | 上下文过滤：`contextFilter(graph, node?, pin?, selected?)` → 可创建 action 集（引擎过滤为准） | R1.2 | 目录与上下文对同一 spawner 的 descriptor 完全一致 |
| R1.4 | 稳定 ID：`actionId`、`descriptorDigest`、`actionDatabaseFingerprint`、`session`/`build` 身份 | R1.2 | 跨两个 Editor 进程一致；recompile 后 blueprint 相关 action 仍稳定 |
| R1.5 | 契约工具：`graph.nodeRegistry.catalog`、`.describe`、`.filterContext`、`.fingerprint`（全部 `readOnly=true`） | R1.4 | 结果 < 1 MiB、显式截断、`readOnly` 断言 |
| R1.6 | Python parity harness：用同一 fixture 的 UE 导出与离线校验器对拍 | R1.2/R1.4 | `actionId` 集合、`descriptorDigest`、canonical JSON 字节一致 |
| R1.7 | UE 5.6 自动化（见 §6） | R1.5 | 只在 disposable host 运行；证据 JSON + 日志 |
| R1.8 | 文档/registry 条目/错误码入表 | 全部 | 清单加载校验通过；MCP 计数冻结不破 |

拆分原则：R1.1–R1.4 是可离线/可单测的纯数据层；R1.5 才接触传输与菜单；R1.6 保证“原生不等于另立标准”。

## 5. 稳定标识与派生规则

### 5.1 三个 ID 的分工

| ID | 算法 | 稳定性来源 | 用途 |
|---|---|---|---|
| `actionId` | `sha256("ueak.node-action/1\n" + canonical_json(projection))[7:39]`，前缀 `action:` | 仅依赖 `discoveryKey` 的稳定字段 | 跨会话引用同一“动作” |
| `descriptorDigest` | 对 descriptor 去掉自身字段与 `discoveredAtUtc` 后做域哈希，取 16 hex | 依赖 descriptor 内容 | 检测描述变化 |
| `actionDatabaseFingerprint` | `sha256("ueak.node-action/1.database\n" + canonical_json(sorted(actionIds)))[:16]` | 依赖整个目录 | 判断两次导出是否同一集合 |

三个都必须带 `sha256:` 前缀（`fingerprint()` 语义），`actionId` 例外（`action:` + 32 hex）。schema 已强制这些形态（`node-descriptor.schema.json:9-12,297-300`）。

### 5.2 `discoveryKey` 字段来源（派生表，R1.2 冻结）

| 字段 | 原生来源（建议） | 备注 |
|---|---|---|
| `nodeClass` | `Spawner->NodeClass->GetPathName()`；模板可用时用 `Template->GetClass()->GetPathName()` | 两者不一致说明 spawner 自定义创建，记录诊断并**排除** |
| `spawnerClass` | `Spawner->GetClass()->GetPathName()` | 直接 |
| `graphSchema` | 模板 `GetGraph()->GetSchema()->GetClass()->GetPathName()`；无模板时用上下文图 schema | 无 schema 的 spawner 排除 |
| `actionKind` | 由 spawner/节点类映射表得出（九值枚举） | 映射表见 5.3 |
| `payload` | 按 kind 取固定键（`ACTION_PAYLOAD_KEYS`） | 只允许稳定字符串（类路径/函数路径/变量名/macro 图名），禁止本地化文本 |

派生纪律：
- 任何取值失败 → 该 spawner 记入 `unsupported[]`（含类名与原因码），**不得**填默认值；
- 本地化字段（title/menuName/category/keywords/tooltip/friendlyName）**绝不进入** `discoveryKey`（ND-LOCALIZED-BAN，`descriptors.py:169-174`）；
- 目录完整性用显式布尔：`complete = (unsupported.length == 0)`；调用方不得把 `complete=false` 的目录当全量。

### 5.3 actionKind 映射（九值，需 fixture 固化）

| kind | 典型来源 |
|---|---|
| `callFunction` | `BlueprintFunctionNodeSpawner` / `UK2Node_CallFunction` 系 |
| `event` | `BlueprintEventNodeSpawner` / `BlueprintBoundEventNodeSpawner` / `UK2Node_Event` 系 |
| `variableGet` | `BlueprintVariableNodeSpawner`（getter 形态） |
| `variableSet` | `BlueprintVariableNodeSpawner`（setter 形态） |
| `macro` | `UK2Node_MacroInstance` 系 |
| `cast` | `UK2Node_DynamicCast` / `K2Node_ClassDynamicCast` |
| `delegate` | `BlueprintDelegateNodeSpawner` / delegate 相关 K2 节点 |
| `component` | `BlueprintComponentNodeSpawner` |
| `special` | 明确列入白名单的其它 K2 节点（knot、sequence、select 等） |

规则：映射未命中**不落 `special`**（否则引擎升级会静默改变哈希），而是 `unsupported`。`special` 只接受冻结白名单里的类。白名单与映射表作为 R1.2 的冻结产物，变更需 MAJOR 版本。

### 5.4 descriptor 示例（单条，符合 `node-descriptor.schema.json`）

```json
{
  "schemaVersion": "ueak.node-descriptor/1.0",
  "actionId": "action:0123456789abcdef0123456789abcdef",
  "discoveryKey": {
    "nodeClass": "/Script/BlueprintGraph.K2Node_CallFunction",
    "spawnerClass": "/Script/BlueprintGraph.BlueprintFunctionNodeSpawner",
    "graphSchema": "/Script/BlueprintGraph.EdGraphSchema_K2",
    "actionKind": "callFunction",
    "payload": {
      "functionPath": "/Script/Engine.KismetSystemLibrary:PrintString"
    }
  },
  "title": "Print String",
  "menuName": "Print String",
  "category": "Utilities|String",
  "keywords": ["print", "string", "log"],
  "searchTokens": ["print string", "kismetsystemlibrary"],
  "graphKinds": ["event", "function"],
  "graphSchemas": ["/Script/BlueprintGraph.EdGraphSchema_K2"],
  "pins": [
    {
      "name": "execute",
      "direction": "input",
      "kind": "exec",
      "type": {
        "category": "exec",
        "subCategory": "",
        "subCategoryObject": "",
        "container": "none",
        "isReference": false,
        "isConst": false,
        "isWeakPointer": false
      },
      "optional": false,
      "dynamic": false,
      "variadic": false
    },
    {
      "name": "then",
      "direction": "output",
      "kind": "exec",
      "type": {
        "category": "exec",
        "subCategory": "",
        "subCategoryObject": "",
        "container": "none",
        "isReference": false,
        "isConst": false,
        "isWeakPointer": false
      },
      "optional": false,
      "dynamic": false,
      "variadic": false
    }
  ],
  "constraints": {
    "requiresBlueprintContext": true,
    "requiresTarget": false,
    "requiresWorldContext": false,
    "pure": false,
    "latent": false,
    "variadic": false,
    "hiddenByDefault": []
  },
  "source": {
    "kind": "FBlueprintActionDatabase",
    "engineVersion": "5.6.1",
    "actionDatabaseFingerprint": "sha256:0123456789abcdef",
    "pluginBuildHash": "sha256:7eb2db917a355dd53bc8f95c7987507c824f4837929f1bb051dfdc8186cb8e6a",
    "discoveredAtUtc": "2026-10-23T15:52:58Z"
  },
  "risk": "R2",
  "capability": "core",
  "trustLevel": "official",
  "descriptorDigest": "sha256:fedcba9876543210"
}
```

说明：`title/menuName/category/keywords` 是**本地化字段**，允许出现在 descriptor 结果里供人读，但**不参与**任何哈希；`actionId` 只由 `discoveryKey` 决定。示例中的哈希值是形状示例，不是真实计算结果。

## 6. 必要 UE 5.6 自动化验证（本轮未执行）

所有测试在 **disposable host**（建议新建 `Build/R1AutomationHost/R1AutomationHost.uproject` + 新建空 Blueprint fixture，绝不使用 Lyra/Reforge/用户工程）中通过 `UnrealEditor-Cmd -ExecCmds="Automation RunTests <name>;Quit" -unattended -nop4 -nosplash -NullRHI -ReportExportPath=...` 执行。禁止 Computer Use、SendInput、鼠标坐标、前台窗口、截图作为依赖。

| ID | 测试名（建议） | 断言 | 负例/边界 |
|---|---|---|---|
| T1 | `UEAgentKit.Native.R1Catalog` | 枚举非空；两次枚举 `actionDatabaseFingerprint` 相同；`unsupported[]` ≤64 且有原因 | 空工程也必须有确定结果（`complete=false` 而非崩溃） |
| T2 | `UEAgentKit.Native.R1DescriptorSchema` | 抽样 200 条 descriptor 全部通过 `node-descriptor` schema；`actionId` 形态合规 | 故意破坏字段时必须被 schema 拒绝 |
| T3 | `UEAgentKit.Native.R1ContextFilter` | node/pin/palette 三种上下文返回集合不同且符合预期；已知动作在、已知不兼容动作不在 | pin 为空/节点为 null/非 K2 图 → 稳定错误码 |
| T4 | `UEAgentKit.Native.R1IdentityStability` | 同进程两次、跨进程两次（两次 UnrealEditor-Cmd）`actionId` 集合完全一致 | 蓝图 recompile 后 blueprint 相关 action 仍一致 |
| T5 | `UEAgentKit.Native.R1ReadOnly` | 调用全部 R1 工具后：`dirtyPackageCount` 不变、`graphFingerprint` 不变、`packageRevision` 不变、undo 栈未增长（`GEditor->Trans` 事务数不变） | 任何一项变化即失败 |
| T6 | `UEAgentKit.Native.R1Bounds` | 大图/大目录下 `maxItems` 生效、`truncated/complete` 正确、结果 <1 MiB | 超限返回 `native-limit-response-too-large` 而非截断丢字段 |
| T7 | `UEAgentKit.Native.R1MenuBinding` | registry 条目生成 ToolMenus 动态 section；执行 action 调到同一 invoke；shutdown 后 section 移除 | 无 node/pin 上下文时不出现条目 |
| T8 | `UEAgentKit.Native.R1Gates` | 未知 toolId/版本不匹配/surface 不允许/PIE 中 → 精确错误码且零副作用 | 错误不得泄露本机路径 |
| T9（可选） | `UEAgentKit.Native.R1Performance` | 目录枚举耗时、模板缓存峰值内存、`MakeContextMenu` 耗时 | 阈值由用户确认；本轮不给数字 |

证据要求：每项产出 `Evidence/*.json`（测试名、开始/结束 UTC、通过/失败、关键计数、`pluginBuildHash`、`engineVersion`、`sessionId`）+ 原始 log；报告只引用可复核的哈希，不把“测试名存在”当作通过。

明确禁止：用同一个 Editor 实例既做被测又被 kill；用 `-NullRHI` 之外的 GUI 交互；把 P7–P11 的旧证据冒充 R1 证据。

## 7. 预览路径替代顺序（N1 / P8 / P9 / P11）

目标：消除“编辑器内执行 Python”和“菜单外启 py.exe”，但**不改变 Agent 侧预览能力**（Python 算法仍在 MCP/离线侧运行）。

| 步 | 动作 | 影响面 | 回滚 | 完成判据 |
|---|---|---|---|---|
| S0 | 加 registry + `native.invoke` + `native.registry.describe`；legacy 路径不动 | 无行为变化 | 关掉清单即可 | 清单校验通过；legacy 回归全绿 |
| S1 | 新增原生只读工具 `graph.preflight.get` / `graph.context.get`，与现有 `editor.getBlueprintGraphLayoutPreflight` / `editor.getFocusedBlueprintGraphContext` 并行 | 新增 | 删除条目 | 两者数据逐字段一致（除新增信封字段） |
| S2 | P11 菜单从“跑 Python worker”改为“导出只读上下文”（写同一 `menu-invocation.json`，保留 `readOnly=true/saved=false/compiled=false`），移除 `planId/layoutHash` 依赖 | P11 disposable host 菜单行为变化 | 恢复 P11 类（保留提交 945dd59） | 菜单 action 由原生 invoke 执行；无 Python |
| S3 | 删除 P8/P9 菜单类（`CreateProc("py.exe")` 与旧扩展点注册） | 仅 lab-only | 同上 | 插件源码 `py.exe` = 0 |
| S4 | 移除 `IPythonScriptPlugin` 引用与 `Build.cs` 的 `PythonScriptPlugin` | 编译依赖 | 恢复 Build.cs | grep 门禁（§9）全 0 |
| S5 | MCP `ue_preview_blueprint_graph_layout` 改为读原生方法（同一 Python 算法，同一参数/上限/fail-closed） | Agent 侧不变 | 回退到 legacy 方法 | 预览回归 + `test_tool_registry` 计数不变 |

顺序理由：S1 先建立“同一数据两条路”的可对拍状态；S2 在最小爆炸半径（disposable 菜单）里切执行路径；S3/S4 才删依赖；S5 最后搬 Agent 入口，避免同时改数据源与消费方。

注意：S2 会改变 P11 菜单的可见行为（不再产出 Plan）。这是**有意降级**，必须写进 N1 报告并由用户确认；不允许静默把 `planProduced` 改成 `true`。

## 8. R2 依赖门槛

R2（GraphPatch 结构写入）只有在以下全部满足后才能启动：

1. R1 合同冻结：descriptor 派生表、actionKind 白名单、`actionId` 规则、fixture 金值；T1–T8 通过并有可复核证据。
2. 身份稳定：同一 5.6.1 构建下跨进程 `actionId` 集合一致；升级引擎导致的差异有显式诊断（不得静默改 ID）。
3. 写门禁复用：R2 直接复用 N0-D06/D07/D08（test-only 四要素、revision/fingerprint 双匹配、单事务 + 同事务读回 + Dirty 确认），不得新造第二套。
4. 幂等：落实 D-07（ChangeSet 2.1 `idempotency`，`reserved → applied/failed/indeterminate`，at-most-once），崩溃后不确定态 fail-closed。
5. 结构限制：D-06 profile（`structure-default` 200/`structure-g2` 300/`layout-only` 1000）在 Policy 与 receipt 中可见；D-08 明确无 `deleteNode`。
6. 证据等级：D-12 要求写盘验收 = 独立 UE 进程 Reload + 语义比较；同进程读回不算验收。
7. 资源：同机一次最多一个重型 UE 构建/Editor；R2 不与 R1 并行写同一目录。
8. 人工授权：R2 启动需要用户新指令；本文不构成授权。

## 9. 进入 R1 的门槛（N0 验收）

| 编号 | 门槛 | 当前状态 |
|---|---|---|
| G1 | `N0_ARCHITECTURE_CONTRACT.md` 评审通过（Q1–Q8 有结论） | **待人工** |
| G2 | registry 清单 schema 可机器校验，示例 JSON 全部可解析 | 已自检（`Build/n0-contract/Evidence/06-json-block-validation.txt`） |
| G3 | R1 派生表 + actionKind 白名单草案存在 | 本文 §5 为草案，**待冻结** |
| G4 | fixture 方案与 disposable host 命名确定 | 本文 §6 建议，**待确认** |
| G5 | 自动化测试清单评审通过（T1–T9） | 本文 §6，**待确认** |
| G6 | 工作树/分支隔离：R1 单独 worktree，不与他人共用可变目录 | **待执行** |
| G7 | 确认无同时 UE 重型构建；构建预算与时间窗 | **待确认** |
| G8 | 明确不开放通用 Apply、不动 P7–P11 证据 | 已具备（本文与 N0-D05） |

## 10. 风险与缓解

| 风险 | 等级 | 缓解 |
|---|---|---|
| 动作数据库 prime 成本高（首次调用卡顿） | 中 | R1 首次调用前预热并记录耗时；把 prime 从 invoke 路径移到模块启动/后台，失败则 `complete=false` |
| 模板缓存内存上限导致 descriptor 不稳定 | 中 | 只读 `GetCachedTemplateNode` 优先；缓存淘汰时重新派生并校验 digest；T6 覆盖 |
| 引擎过滤语义与自写目录语义不一致 | 高 | 双通道设计 + T3 断言；`context` 只信引擎，`catalog` 不做兼容性承诺 |
| 5.5→5.6 API 漂移（`ActionGroup` 弃用） | 中 | 只用 `GetSchemaAction`；编译门槛 + 版本化 include |
| 第三方 spawner 异常/卡死 | 中 | 每 spawner 有界尝试 + 超时/异常计数入 `unsupported[]`；不因单个 spawner 失败整体失败 |
| `actionId` 与 Python 基线不一致 | 高 | R1.6 parity harness 必须逐字节比对 canonical JSON；不一致即阻断 |
| 本地化字段混入哈希 | 中 | schema/校验器拒绝（ND-LOCALIZED-BAN）；T2 负例 |
| 误把“只读”实现成“无写入测试” | 高 | T5 显式断言 dirty/revision/undo 栈不变 |
| 大图结果超 1 MiB | 中 | 分页 + `truncated` 标志；`native-limit-response-too-large` 兜底 |

## 11. 待人工裁决（R1 相关）

| 编号 | 问题 | 建议 |
|---|---|---|
| R1-Q1 | fixture 蓝图内容（空图 / 含 10–20 个典型节点 / 含 macro+cast+delegate） | 建议三者都建，分别验证目录、上下文、边界 |
| R1-Q2 | 是否包含项目/第三方插件注册的 spawner | 默认包含但标 `trustLevel=project/third-party` 并允许过滤 |
| R1-Q3 | `BlueprintAssetNodeSpawner` 等“资产动作”是否算 node action | 建议排除并计入 `not-a-node-action`，不污染完整性判定 |
| R1-Q4 | 是否要求 5.5 兼容 | 建议只承诺 5.6.1，5.7 另行回归 |
| R1-Q5 | 性能阈值（目录枚举上限） | 建议先测后定，不在合同里编造数字 |
| R1-Q6 | 是否把 R1 只读工具暴露到 MCP（`ue_get_node_registry_*`） | 建议暴露 2 个（catalog 摘要 + filterContext），计数变更需同 commit 改测试与文档 |

## 12. 本轮明确未做

- 未写任何 R1/R2/R7 C++ 代码；未改 `BlueprintGraphLayoutP*`、`EditorBridge*`、`Build.cs`。
- 未启动 UE、未编译、未运行 Automation、未产生任何 `RUNTIME-VERIFIED` 结论。
- 未开放通用 Apply，未改 Python 布局算法，未改 MCP 工具计数。
- 未 push、未 merge、未 reset、未清理任何工作树或证据。
