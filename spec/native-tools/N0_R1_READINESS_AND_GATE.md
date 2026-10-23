# N0 → R1 只读准备审查与门槛

- 状态：**READ-ONLY DESIGN / 未实现 / 未在 UE 运行**
- 日期：2026-10-23（初版）/ 2026-10-24（review round 2 复审）
- 工作树：`E:\WorkSpace\UEAgentKit-WT\native-tools-r1`（`feature/native-tools-contract-r1`），基线 `main` `c5ce3419`
- 上游：`N0_ARCHITECTURE_CONTRACT.md`、`N0_REVIEW_DECISIONS_AND_PREVIEW_MIGRATION.md`（Q1 修订/broker/迁移 gates）、`spec/graph-contracts/D01_D13_USER_DECISIONS.md` D-04/D-06/D-12、`spec/LIVE_EDITOR_BRIDGE.md`
- 结论先行：R1 技术路线可行，**用“动作数据库目录 + 引擎上下文过滤”双通道**，只读、不 spawn、不写 UE 资产；但必须先冻结 descriptor 派生规则与 fixture 金值，否则 `actionId` 会随实现漂移。R1 与 Preview broker 迁移**互不阻塞**：R1 不依赖 broker，broker 也不要求 R1 完成。

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
| schema 校验入口 | `descriptors.validate_node_descriptor`（`descriptors.py:139`）、`schema.validate_document`（`schema.py:348`） | T2 直接调用，不另写 C++ 校验器作为“唯一真相” |

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
5. 第三方/项目插件注册的 spawner 是否有异常实现（`IsTemplateNodeFilteredOut` 抛错/卡死），以及 `GetTemplateNode()` 是否会执行第三方 `Invoke` 代码（源码显示模板路径会跳过 `Modify/AddNode`，但**仍会创建 transient 对象**，`BlueprintNodeSpawner.cpp:339-351`）。
6. Commandlet（`-run=`）环境下动作数据库是否可用；R1 计划只用 Editor 上下文 + 自动化，不用 commandlet。
7. 插件加载集合变化（项目插件/引擎插件启停）对 `actionId` 集合与 `actionDatabaseFingerprint` 的影响；需要 `pluginSetHash` 才能解释差异。
8. `FActionRegistry` 的迭代顺序是否稳定；结论是**不能依赖**，必须排序后再哈希。

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
- `nodeClass` 的**主来源**是 `Spawner->NodeClass`（公开 `UPROPERTY`，`BlueprintNodeSpawner.h:146-147`）；模板类只用于交叉校验，不一致即 `unsupported`（原因 `node-class-mismatch`），不能二选一“取好看的那个”。

### 5.2.1 模板创建的可信度规则（新增）

| spawner 来源 | 允许 `GetTemplateNode()` | 依据 |
|---|---|---|
| `official`（引擎模块） | 允许，带计数与耗时上限 | 官方 spawner 是引擎自带的受信代码 |
| `project` / `third-party` | **默认不允许**；只读 `GetCachedTemplateNode()`；为 null 即 `unsupported`（`template-not-cached`） | 避免“读目录”触发第三方 `Invoke` 代码（`BlueprintNodeSpawner.cpp:339-351`） |
| 任何 spawner 抛错/超时 | 单条 `unsupported`（`spawner-threw` / `spawner-timeout`），整体继续 | 一个坏 spawner 不能毁掉目录 |

若用户明确要求“包含第三方动作的完整描述”，需要显式打开 `AllowUntrustedTemplateSpawn=true`（编译期默认关闭，且只能由本地配置开启，不能由 Agent 参数开启），并记录 `untrustedTemplateSpawned=true` 证据。该开关不改变“只读”性质，但改变“是否执行第三方代码”的信任边界。

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

说明：`title/menuName/category/keywords` 是**本地化字段**：它们**不参与** `actionId`，也不得进入 `discoveryKey`；但按现有 Python 实现，它们**仍会进入** `descriptorDigest`（`descriptors.py:107-120` 只排除自身与 `discoveredAtUtc`），因此“改文案 → digest 变、actionId 不变”。这一反直觉行为是 R1 必须测试并记录的兼容事实，见 §5.6。示例中的哈希值是形状示例，不是真实计算结果。

### 5.5 与原 schema 的精确兼容（逐条核对）

| schema 规则 | R1 必须做什么 | 不能做什么 |
|---|---|---|
| `additionalProperties:false`（根） | 只输出 schema 列出的字段 | 加 `pluginSetHash`/`unsupported` 等自定义顶层字段（这些放**外层工具结果**，不放 descriptor） |
| 必填 `title/menuName/category/keywords/searchTokens` | 必须填（来自引擎菜单/模板文本） | 因“本地化”而省略必填字段 |
| 必填 `graphKinds` 非空 | 用 `typed_graph` 词汇：`event/function/macro/ubergraph/delegate-signature/other`（`typed_graph.py:380`） | 自造 `eventGraph` 之类字符串 |
| D-01：EventGraph=`event`，`ubergraph` 只读 | `GT_Ubergraph` → `event`；映射表写进 R1.2 | 输出 `ubergraph` 让 V1 Apply 误判可写 |
| `pins[].type` 七个必填字段 | 从 `FEdGraphPinType` 逐字段导出 | 用 `PinCategory` 字符串代替完整类型 |
| `pins[].kind` | `PinCategory == PC_Exec` → `exec`，否则 `data`（`EdGraphSchema_K2.h:366,733`） | 用方向或名称猜 |
| `source.kind` 枚举 | 固定 `FBlueprintActionDatabase` | 加别的来源枚举值（需 schema 升级） |
| `descriptorDigest` 16 hex | 按域哈希取 16 | 用完整 64 hex |
| `risk/capability/trustLevel` | 来自冻结映射表 | 按类名猜测 |

此外：`descriptor` 的顶层字段集合必须与 `schemas/node-descriptor.schema.json` **完全一致**；R1 的 `unsupported[]`、`pluginSetHash`、`complete` 属于**工具结果外层**（与 descriptor 并列），不污染 descriptor。

### 5.6 哈希输入清单（必须冻结）

| ID | 输入字段 | 是否含本地化 | 是否含时间 |
|---|---|---|---|
| `actionId` | `discoveryKey` 的 5 个字段（payload 按键排序、固定键集） | 否 | 否 |
| `descriptorDigest` | 整个 descriptor 去掉 `descriptorDigest` 与 `source.discoveredAtUtc` | **是**（现有行为；见下方澄清） | 仅排除 `discoveredAtUtc` |
| `actionDatabaseFingerprint` | 排序后的 `actionId` 集合 | 否 | 否 |

**关键澄清（本轮新增）**：`descriptors.py:107-120` 的 digest 投影只排除了 `descriptorDigest` 与 `source.discoveredAtUtc`，**并未排除** `title/menuName/category/keywords`。这意味着“改菜单文案会改 descriptorDigest”。R1 有两种选择：

- **兼容优先（推荐）**：完全按现有 Python 行为实现，接受“本地化文本变化 → descriptorDigest 变化”；`actionId` 仍稳定，因此不影响“同一动作”的跨会话识别。
- **稳定优先（需契约升级）**：把本地化字段排除出 digest，但这会改变 Python 侧的既有语义，**不得在 R1 私自做**，必须走 descriptor 1.1。

R1 默认采用“兼容优先”，并在 T2 中显式断言 `descriptorDigest` 会随 title 变化、`actionId` 不变——把这条“看起来反直觉”的行为写成测试，而不是留给后人踩坑。

### 5.7 跨进程与插件集合

- 同一引擎构建 + 同一插件构建 + 同一 fixture：`actionId` 集合必须完全一致；`actionDatabaseFingerprint` 也应一致。
- 项目/第三方插件集合变化时：允许 fingerprint 变化，但必须输出 `pluginSetHash`（排序后的插件名+版本哈希）与 `identityChanged` 标记；**不得静默**。
- 迭代顺序：`FActionRegistry` 是 `TMap`，顺序不可依赖；所有哈希前必须按 `actionId` 排序（`descriptors.py:103-104` 的 `sorted(...)` 等价行为）。
- 蓝图 recompile 后：blueprint 相关 spawner 可能重建，但 `actionId` 由 discoveryKey 决定，预期稳定；T4 必须覆盖。

## 6. 必要 UE 5.6 自动化验证（本轮未执行）

所有测试在 **disposable host**（建议新建 `Build/R1AutomationHost/R1AutomationHost.uproject` + 新建空 Blueprint fixture，绝不使用 Lyra/Reforge/用户工程）中通过 `UnrealEditor-Cmd -ExecCmds="Automation RunTests <name>;Quit" -unattended -nop4 -nosplash -NullRHI -ReportExportPath=...` 执行。禁止 Computer Use、SendInput、鼠标坐标、前台窗口、截图作为依赖。

| ID | 测试名（建议） | 断言 | 负例/边界 |
|---|---|---|---|
| T1 | `UEAgentKit.Native.R1Catalog` | 枚举非空；两次枚举 `actionDatabaseFingerprint` 相同；`unsupported[]` ≤64 且有原因码分布；`complete` 与 `unsupported` 严格互补；`pluginSetHash` 存在 | 空工程也必须有确定结果（`complete=false` 而非崩溃） |
| T2 | `UEAgentKit.Native.R1DescriptorSchema` | 抽样 200 条 descriptor 通过**仓库自带** `validate_node_descriptor`；`actionId`/digest 形态合规；`title` 变化 → `actionId` 不变、`descriptorDigest` 变（§5.6 的反直觉行为显式入测） | 本地化字段混入 `discoveryKey` 必须失败；额外顶层字段必须被 `additionalProperties:false` 拒绝 |
| T3 | `UEAgentKit.Native.R1ContextFilter` | node/pin/palette 三种上下文集合不同；已知动作在、已知不兼容动作不在；pin 改变确实改变集合 | pin 为空/节点为 null/非 K2 图 → 稳定错误码 |
| T4 | `UEAgentKit.Native.R1IdentityStability` | 同进程两次、**两个独立 UnrealEditor-Cmd 进程**的 `actionId` 集合完全一致；蓝图 recompile 后 blueprint 相关 action 一致 | `pluginSetHash` 不同的两个进程必须显式标记，不得假装一致 |
| T5 | `UEAgentKit.Native.R1ReadOnly` | 调用全部 R1 工具后：`dirtyPackageCount` 不变、`graphFingerprint` 不变、`packageRevision` 不变、`GEditor->Trans` 事务数不变、无新增 transient 资产/AssetRegistry 条目 | 任何一项变化即失败；包括“读目录后 package 变 dirty” |
| T6 | `UEAgentKit.Native.R1Bounds` | `maxItems`/`nextCursor`/`truncated`/`totalAvailable` 自洽；两次分页结果稳定；结果 <1 MiB | 超限返回 `native-limit-response-too-large`，不得静默截断丢字段 |
| T7 | `UEAgentKit.Native.R1MenuBinding` | `RegistryMode=shadow` 时 legacy 菜单**零变化**；`active` 时才新增/替换；执行 action 调到同一 invoke；shutdown 后 section 移除 | 无 node/pin 上下文时不出现条目 |
| T8 | `UEAgentKit.Native.R1Gates` | 未知 toolId/版本不匹配/surface 不允许/PIE 中/flag 关闭 →确错误码且零副作用 | 错误不得泄露本机路径 |
| T9（可选） | `UEAgentKit.Native.R1Performance` | 目录枚举耗时、模板缓存峰值内存、`MakeContextMenu` 耗时 | 阈值由用户确认；本轮不给数字 |
| T10（新） | `UEAgentKit.Native.R1UntrustedSpawner` | 第三方/项目 spawner 默认**不触发**模板创建（只读 cached）；构造会抛错的假 spawner，目录整体仍成功且该条计入 `unsupported` | `AllowUntrustedTemplateSpawn` 关闭时不得出现 `untrustedTemplateSpawned=true` |

证据要求：每项产出 `Evidence/*.json`（测试名、开始/结束 UTC、通过/失败、关键计数、`pluginBuildHash`、`engineVersion`、`sessionId`）+ 原始 log；报告只引用可复核的哈希，不把“测试名存在”当作通过。

明确禁止：用同一个 Editor 实例既做被测又被 kill；用 `-NullRHI` 之外的 GUI 交互；把 P7–P11 的旧证据冒充 R1 证据。

## 7. 预览路径替代顺序（已由 review round 2 修订）

> **本节的初版（S0–S5，把 P11 菜单改成“只导出 Context”）已作废**。它把已验收的实时 Preview 降级为数据导出，违反零降级。正式方案与完整时序/协议/安全/回滚见 `N0_REVIEW_DECISIONS_AND_PREVIEW_MIGRATION.md` §3–§9。

替代后的顺序（摘要，细节以迁移文件为准）：

| 阶段 | 动作 | 旧路径 | 回滚 |
|---|---|---|---|
| M0 | 影子骨架：registry `off`、broker descriptor 校验、客户端（不接菜单） | 原样 | 关闭 flag |
| M1 | broker daemon（复用 `build_preview`）+ 协议/安全测试 | 原样 | 停 daemon |
| M2 | 原生只读工具 `graph.preflight.get` / `graph.context.get`，与 legacy 方法并行对拍 | 原样 | 删除条目 |
| M3 | 菜单 opt-in：成功走 broker，不可用回退 legacy，receipt 标 `previewPath` | 可用 | flag off |
| M4 | 默认开启（保留回退） | 可用（回退） | flag off |
| M5 | 关闭回退（明确失败，不伪造成功） | 仅显式开启 | flag 打开回退 |
| M6 | 退役 legacy（删 `IPythonScriptPlugin` / `py.exe`） | 删除 | 依赖 git 回退 + 用户批准 |

与 R1 的关系：以上阶段**不依赖 R1**，R1 也不依赖 broker；两条线可以并行**设计**，但都受“同机一次一个重型 UE 任务”约束。R1 的 `RegistryMode` 与 Preview 的 `NativeLayoutPreview` 是两个独立 flag，不得捆绑发布。

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
| G1 | `N0_ARCHITECTURE_CONTRACT.md`（v2）与 `N0_REVIEW_DECISIONS_AND_PREVIEW_MIGRATION.md` 评审通过（Q1 修订 + Q2–Q8 + Q9–Q12 有结论） | **待人工** |
| G2 | registry 清单 schema 可机器校验，示例 JSON 全部可解析 | 已自检（`Build/n0-contract/Evidence/06-json-block-validation.txt`） |
| G3 | R1 派生表 + actionKind 白名单 + 哈希输入清单草案存在 | 本文 §5 为草案，**待冻结** |
| G4 | fixture 方案与 disposable host 命名确定 | 本文 §6 建议，**待确认** |
| G5 | 自动化测试清单评审通过（T1–T10） | 本文 §6，**待确认** |
| G6 | 工作树/分支隔离：R1 用独立 worktree（见 §9.1） | **待执行** |
| G7 | 确认无同时 UE 重型构建；构建预算与时间窗 | **待确认** |
| G8 | 明确不开放通用 Apply、不动 P7–P11 证据 | 已具备（本文与 N0-D05） |
| G9 | 明确 R1 与 Preview 迁移互不阻塞，且两个 feature flag 独立发布 | 已写入 §7；**待确认** |
| G10 | `RegistryMode=off` 时 legacy 非回归由测试证明（不是口头承诺） | **待实现**（M0/R1.0 的一部分） |

### 9.1 工作树与合并策略（本轮澄清）

| 问题 | 结论 |
|---|---|
| 本 worktree 的定位 | **只承载 N0 合同与证据**；不在这里写 R1 代码，也不在这里做 UE 构建 |
| N0 如何进入 main | 评审通过后，由协调者把 `feature/native-tools-contract-r1`（docs-only）合并到 `main`；建议 fast-forward 或单个 merge commit，保留 P12 `c5ce3419` 为基线 |
| R1 从哪来 | 从**合并后的 main** 新建 `feature/r1-node-registry` + 独立 worktree（含自己的 `Build/R1AutomationHost`） |
| N0 合并被推迟时 | R1 可从 N0 tip 分支以保留合同 lineage；N0 合并后再把 `main` merge 进 R1（**不 rebase、不 reset**），并在 R1 报告写清合同版本 |
| 证据隔离 | N0 证据留在本 worktree 的 `Build/n0-contract/Evidence`（gitignored）；R1 证据写 R1 worktree 的 `Build/R1AutomationHost/Evidence`；不得互相引用为“同一轮证据” |
| 并发 | 同机一次最多一个重型 UE 构建/Editor；N1（broker）与 R1 若都要 UE 回归，必须串行排期 |
| 禁止 | 不使用 `git clean -fdx`、不删除他人 worktree、不动 Reforge/Lyra Golden/UETest、不 push（除协调者明确授权） |

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
| 读目录意外执行第三方 spawner 代码 | 中 | 默认只读 `GetCachedTemplateNode`；`AllowUntrustedTemplateSpawn` 编译期关闭；T10 覆盖 |
| `descriptorDigest` 含本地化文本导致“文案改动=digest 变” | 中 | 明确记录（§5.6）；T2 断言 `actionId` 不变、digest 变；如要稳定需 descriptor 1.1，不在 R1 私改 |
| `FActionRegistry` 迭代顺序不稳定导致 fingerprint 抖动 | 中 | 哈希前强制按 `actionId` 排序；T1/T4 覆盖 |
| R1 与 Preview 迁移抢同一台 UE/同一 worktree | 中 | §9.1 隔离与串行排期；两个 flag 独立 |

## 11. 待人工裁决（R1 相关）

| 编号 | 问题 | 建议 |
|---|---|---|
| R1-Q1 | fixture 蓝图内容（空图 / 含 10–20 个典型节点 / 含 macro+cast+delegate） | 建议三者都建，分别验证目录、上下文、边界 |
| R1-Q2 | 是否包含项目/第三方插件注册的 spawner | 默认包含但标 `trustLevel=project/third-party` 并允许过滤 |
| R1-Q3 | `BlueprintAssetNodeSpawner` 等“资产动作”是否算 node action | 建议排除并计入 `not-a-node-action`，不污染完整性判定 |
| R1-Q4 | 是否要求 5.5 兼容 | 建议只承诺 5.6.1，5.7 另行回归 |
| R1-Q5 | 性能阈值（目录枚举上限） | 建议先测后定，不在合同里编造数字 |
| R1-Q6 | 是否把 R1 只读工具暴露到 MCP（`ue_get_node_registry_*`） | 建议暴露 2 个（catalog 摘要 + filterContext），计数变更需同 commit 改测试与文档 |
| R1-Q7 | 是否接受“本地化文本变化导致 `descriptorDigest` 变化”（现有 Python 行为） | 建议接受（兼容优先），并写进测试；如不接受，必须走 descriptor 1.1 而不是 R1 私改 |
| R1-Q8 | 是否允许 `AllowUntrustedTemplateSpawn` 开关存在 | 建议保留但默认关闭、只可由本地配置开启，且必须有 `untrustedTemplateSpawned` 证据 |
| R1-Q9 | R1 是否必须等 N0 合并进 main 才能开分支 | 建议是（见 §9.1）；例外需协调者书面同意 |

## 12. 本轮明确未做

- 未写任何 R1/R2/R7 C++ 代码；未改 `BlueprintGraphLayoutP*`、`EditorBridge*`、`Build.cs`。
- 未启动 UE、未编译、未运行 Automation、未产生任何 `RUNTIME-VERIFIED` 结论。- 未开放通用 Apply，未改 Python 布局算法，未改 MCP 工具计数。
- 未 push、未 merge、未 reset、未清理任何工作树或证据。
