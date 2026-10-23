# N0 — Native Tool Architecture Contract (freeze candidate)

- 状态：**CANDIDATE — 未实现、未编码、未在 UE 中运行**。本文只冻结“合同”，不是 R1/R2 实现授权，也不开放通用 Apply。
- 日期：2026-10-23
- 工作树：`E:\WorkSpace\UEAgentKit-WT\native-tools-r1`，分支 `feature/native-tools-contract-r1`
- 基线：已发布 `main` `c5ce3419c6962da49e874586f4e5dbd1457a4d0d`（P12）。P0–P12 已完成，本轮不重跑、不改其运行代码。
- 上游依据：`handoffs/UEAGENTKIT_NEXT_CHAT_HANDOFF_20261023.md`、`handoffs/UEAGENTKIT_NATIVE_PARALLEL_REVIEW_20261023.md`、`goals/UEAGENTKIT_MAIN.md`（2026-10-23 节）、`spec/graph-contracts/D01_D13_USER_DECISIONS.md`。
- 相关但不修改：`spec/LIVE_EDITOR_BRIDGE.md`、`spec/MCP_SERVER.md`、`spec/graph-contracts/*`。

## 0. 本文档回答什么、不回答什么

回答：

1. 一个**单一、版本化**的 Native ToolRegistry 用什么描述、由谁拥有、如何被三处消费（ToolMenus 菜单、EditorBridge/MCP 服务、CLI）。
2. 稳定的 DTO（请求/结果/错误/上下文/修订/证据）与 JSON 示例。
3. 授权、readonly、test-only、Apply 的 fail-closed 判定顺序与拒绝码。
4. game-thread、Editor 生命周期、asset revision、transaction/save/undo 的语义边界。
5. 错误码、审计、兼容性与弃用规则。
6. Python worker / CLI 的保留与退场边界及其可度量退出条件。
7. UE Public API 可行性（含本机 5.6.1 头文件核验等级）与风险。

不回答（明确排除）：

- 不定义 R1 Node Registry 的算法实现细节（见 `N0_R1_READINESS_AND_GATE.md`）。
- 不定义 GraphPatch 写入 schema（沿用 `ueak.graphpatch/1.0` 候选与 D-06/D-07/D-08/D-09）。
- 不允许通用 production Apply；不改变 P7/P8/P9/P11 现有 test-only 宿主安全性。
- 不宣称任何 UE 运行时行为已实测；本轮没有启动 UE、没有编译、没有 Computer Use。

## 1. 验证等级（全文标注纪律）

| 等级 | 含义 | 本轮是否获得 |
|---|---|---|
| `SOURCE-READ` | 读了本仓库实际源码/文档并引用行号 | 是 |
| `HEADER-VERIFIED` | 读了本机 UE 5.6.1 安装的公开头文件（路径见未入库证据 `Build/n0-contract/Evidence/02-ue56-public-api-verification.txt`） | 是 |
| `RUNTIME-VERIFIED` | 真实 UE Editor / UnrealEditor-Cmd 运行结果 | **否**（本轮禁止也不必要） |
| `UNVERIFIED` | 仅设计推断，必须在 R1 自动化中证明 | 标注于风险表 |

任何“引擎会/不会”的行为断言，若未达 `RUNTIME-VERIFIED`，本文一律降级为“需验证”，不得在后续报告中升级措辞。

## 2. 基线审计（事实，不含推测）

### 2.1 现状事实

1. **协议版本与能力表是硬编码字符串**。`EditorBridge.cpp:43` `ProtocolSchemaVersion = "1.0"`，`:44` `PluginVersion = "0.8.0"`，`:49-83` 33 条 capability 字符串；请求解析在 `:687-710`，`hello` 握手在 `:712-739`。
2. **方法分派是单一 if/else 链**。`EditorBridge.cpp:779-1674`，共 53 处 `Method == TEXT(...)` 比较、去重后 48 个方法名字面量（其中 47 个 `editor.*` / `retarget.*`，另 1 个为 `hello`）；未注册方法返回 `live-editor-capability-unavailable`（`:1674`）。
3. **Python 侧另有一张平表**。`src/ue_agent_kit/tool_registry.py:31-203` 共 122 条 `ToolDefinition(name, group, annotation, live_method, high_level_change)`；模式投影 `tool_definitions_for_mode`（`:224-240`）。实测计数：只读 10、Live 45、Workflow 67、Memory 24、Live+Workflow 102、Live+Workflow+Memory 116（`Build/n0-contract/Evidence/05-contract-counts.txt`）。
4. **菜单侧是三个互不相同的类**，各自重复实现门禁与 JSON 收发：
   - `BlueprintGraphLayoutP8Menu.cpp:199` / `P9Menu.cpp:199` 用 `FPlatformProcess::CreateProc("py.exe", ...)` 启动外部 worker；两者仅差 `P8→P9` 记号（已实测哈希比对），且仍注册在旧扩展点 `GraphEditorGraphContextMenu`（`P8Menu.cpp:119`）。`P11Menu.h:11-22` 记录该扩展点在 UE5.6 已不存在、P11 因此改用 ToolMenus；本轮未在 5.6 实机复验 P8/P9 菜单是否出现（`UNVERIFIED`），只把它当作待退役路径。
   - `BlueprintGraphLayoutP11Menu.cpp:285-330` 先找 `tools/p9_layout_preview_worker.py`，再走 `IPythonScriptPlugin::ExecPythonCommand` 在编辑器进程内执行 `runpy.run_path`；`:302-321`。`Build.cs:34` 因此长期依赖 `PythonScriptPlugin`。
   - 三个类各自写 `SaveJsonObject/ReadJsonObject/ShowMessage`（P11 `:35-93`，P8/P9 `:21-67`）。
5. **test-only 门禁今天由三重常量构成**：精确工程绝对路径 + 工程名 + `.uproject` SHA-256（`P9TestOnly.cpp:301-340`），外加同目录 build marker 中的 DLL SHA-256（`:246-296`），外加资产/确认短语白名单（`LIVE_EDITOR_BRIDGE.md:288-302`）。这是当前唯一被 UE 实机验收过的写入门禁形态（P7/P8/P9/P10/P11），N0 必须原样继承而不是重新发明。
6. **revision 与 fingerprint 语义已存在但分散**：`BuildAssetRevision` 产出 `package-sha256-v1`（`BlueprintContextAnalysis.cpp:445-513`，无文件时退化为 `guid:`）；`ComputeGraphFingerprint` 产出 `sha1-length-prefixed-graph-state-v1`（`BlueprintGraphLayoutApply.cpp:390-479`）；写入门禁要求二者精确匹配（`:1502-1549`），并且 dirty 即拒绝（`:1529`）。
7. **事务语义已存在且经过实机验证**：`FScopedTransaction`（`:1884-1895`）→ `Modify()`（`:1897-1905`）→ 同事务内读回（`:1907-1936`）→ `MarkBlueprintAsModified` + Dirty 确认（`:1938-1959`）→ 失败即恢复原位并 `Transaction.Cancel()`。Save 是独立入口（`editor.saveBlueprintGraphLayoutP9TestOnly`），Apply 从不 Save。
8. **契约资产已存在**（候选，未在 C++ 实现）：`ueak.node-descriptor/1.0`、`ueak.operation/1.0`、`ueak.graphpatch/1.0`、`ueak.typedgraph`、canonical 域分离哈希（`graph_contracts/canonical.py:24-38`）、`schemas/*.schema.json`、error-codes 表。
9. **UI 事实**：`UToolMenus::ExtendMenu("GraphEditor.GraphContextMenu.Common")` + `AddDynamicSection` 已在 P11 实机通过（P11 报告与 `P11Menu.cpp:132-195`）；`UGraphNodeContextMenuContext` 是引擎公开类（`Runtime/Engine/Classes/EdGraph/EdGraphNode.h:214`）。
10. **外部进程调用并非都该删除**：`EditorBridgeAutomationHandlers.cpp:160` 启动 `UnrealEditor-Cmd.exe` 跑隔离 Automation，属进程编排，不是架构缺陷。

### 2.2 缺陷清单（N0 要消除的结构性问题）

| 编号 | 缺陷 | 影响 | 对应决定 |
|---|---|---|---|
| N0-F01 | 同一能力在三处（C++ capability 字符串、C++ if/else、Python 平表）人工同步 | 漂移后 Agent 看到不存在的方法，或菜单执行与 MCP 执行走不同代码 | D-01, D-03 |
| N0-F02 | 门禁逻辑在菜单与 bridge 各自重复（P11 `:223`、`EditorBridge.cpp:936`、`P8Menu.cpp:151`） | 任一处漏改即绕过 | D-04, D-06 |
| N0-F03 | 三个菜单类各自实现 JSON I/O 与弹窗 | 行为不一致、审计缺失 | D-03, D-11 |
| N0-F04 | 编辑器进程内执行 Python（P11） | 依赖 `PythonScriptPlugin`、失败模式不可控、无法审计、与“C++ First”冲突 | D-12 |
| N0-F05 | 外部 `py.exe` 菜单路径（P8/P9） | 无控制台环境即失败；已不再出现在真实菜单 | D-12 |
| N0-F06 | readonly 只是“没有写代码”的事实，不是被强制的不变量 | 新增 handler 可能误写 | D-05 |
| N0-F07 | 无统一审计与请求摘要 | 无法回答“谁在何时以什么参数调用了什么、是否被拒” | D-11 |
| N0-F08 | revision/fingerprint 只在 layout 路径使用，非通用门禁 | R2 结构写入会各自实现 | D-07 |
| N0-F09 | 错误码按功能前缀散落（`live-editor-*`、`graph-layout-*`），无类别/可重试/终态语义 | 客户端无法统一处理 | D-10 |
| N0-F10 | 接口无版本协商，`toolVersion` 概念不存在 | 无法做兼容演进 | D-02, D-13 |

## 3. 决定（N0-D01 … N0-D15）

> 每条决定的“后果”是实施时必须遵守的约束，不是建议。

**N0-D01 单一来源：一份运行时加载的 registry 清单。**
权威来源是随插件发布的 `Plugin/UEAgentKit/Resources/NativeTools/native-tools.v1.json`（`ueak.native-registry/1.0`；该目录当前不存在，由 N1 创建，N0 只冻结路径与加载规则）。C++ 在模块启动时加载并**校验**：清单 schema、每个 `name` 必须有已注册 handler、每个 handler 必须出现在清单、DTO schemaId 必须存在、`version` 为 semver。任何一项失败 → registry 不可用（`native-registry-invalid-manifest`），所有 native 工具 fail-closed。
后果：安全关键常量（test-only 工程路径/SHA、build marker 规则、确认短语模板）**不进清单**，编译进 DLL（见 D-06）；清单只引用 `gateProfile` 名字。

**N0-D02 命名与版本。**
`name`（即 toolId）= `^[a-z][a-z0-9]*(\.[a-z][a-zA-Z0-9]*){2,4}$`，如 `graph.layout.preflight`、`graph.nodeRegistry.filterContext`、`asset.inspect`。`version` 为 `MAJOR.MINOR.PATCH` 的**合同版本**，与插件版本无关。registry 级另有 `registryVersion`。
后果：toolId 永不复用；MAJOR 变更=不兼容；MINOR=仅新增可选参数/新增结果字段/新增工具；PATCH=仅描述与文档。

**N0-D03 统一调用入口。**
新增唯一入口 `native.invoke`（bridge 方法名），参数为 `{toolId, toolVersion, arguments, context, expectedRevision?, confirmation?}`；菜单与 MCP 都调用**同一个** `FNativeToolRegistry::Invoke`，不再各自分派。旧方法名保留为 registry 条目的 `exposure.bridge.aliases`，在过渡期由同一 handler 服务，禁止第二份实现。
后果：菜单 handler 不得再做业务判断（只做 context 提取 + 调 Invoke + 呈现结果）。

**N0-D04 授权梯（顺序固定，先廉后贵，任一失败即拒）。**
`registry 可用 → toolId/版本可协商 → 传输 hello → capability 已注册 → maturity 门（含 test-only）→ 环境门（Editor/PIE/资产已加载/World）→ revision 门 → transaction 门 → schema 校验 → 执行`。
后果：门禁代码只存在于 registry invoke 路径；任何 handler 内不得再写门禁；拒绝必须返回该层专属错误码（见 §8、§11）。

**N0-D05 readonly 是被强制的属性，不是惯例。**
条目 `readOnly=true` 时，invoke 期间 `RequireWritable()` 恒为假：禁止 `FScopedTransaction`、`Modify()`、`MarkPackageDirty`、`SavePackage`、任何 UObject 属性赋值。由 ①合同字段 ②运行时断言 ③静态检查（R1 起 CI 检查 read handler 源文件不出现写 API）三重保证。
后果：read 工具若需要“临时对象”（如 spawner 模板）必须在文档标注为 transient-only，且不得落在目标 package 的 outer 链上。

**N0-D06 test-only 门禁形态冻结（沿用 P7–P11 已验收形态）。**
四要素全部满足才放行：①规范化工程绝对路径精确等于 profile 常量；②工程名精确相等；③`.uproject` SHA-256 精确相等；④同目录 build marker 中 `pluginBuildHash` 精确等于当前加载 DLL 的 SHA-256。另加：资产 allowlist、确认短语模板（含 `planId`）、`packageDirty=false`、PIE stopped、focused graph 精确匹配。
后果：这四类常量**只存在于编译单元**（`TestOnlyGateProfiles`），清单仅写 `gateProfile: "p8-disposable-host"` 之类的 id；运行期数据不可放宽授权。缺少 profile 定义 → 启动即失败。

**N0-D07 revision 语义统一。**
读：`assetRevision`（`package-sha256-v1`，值 `sha256:<64>`；文件不存在时可 `guid:`，但 `writable=false`）+ `graphFingerprint`（`sha1:<40>`，`sha1-length-prefixed-graph-state-v1`）+ `packageDirty`。写：必须携带两者的期望值，且在锁内、首个 `Modify()` 之前**重新计算并逐字比较**；不一致 → `native-gate-revision-conflict` / `native-gate-fingerprint-conflict`。`guid:` 修订一律不可写。
后果：写结果必须返回 before/after 的 revision 与 fingerprint；Save 之后 revision 才会变化，任何客户端不得用 fingerprint 变化推断已保存。

**N0-D08 transaction / save / undo 语义冻结。**
一次写调用 = 最多一个 `FScopedTransaction`，命名 `UEAgentKit Native <toolId> <planId|requestId>`；事务内失败必须恢复快照、恢复 `bDirtyBefore`、`Cancel()`，不得留下 Dirty 或空事务。成功返回 `transactionId`（事务 GUID）。**Save 永远是独立工具**且需独立确认短语；Apply 工具禁止调用 Save/Compile。Undo/Discard 只能作用于精确 `transactionId + editorSessionId + assetPath`；**不提供通用 Redo 工具**（栈顶身份无法跨调用证明）；test-only 的 undo→verify→redo→verify 只允许作为单个探针工具的一次调用内部序列。

**N0-D09 线程与阻塞。**
所有 handler 在 game thread 执行（`check(IsInGameThread())`）；bridge 的 `FTSTicker` 与现有 `GEditor` 访问已在 game thread（`EditorBridge.cpp:565-576` 直接调用 `GEditor`）。默认 `blocking=false`；声明 `blocking=true` 的工具必须有 `limits.timeoutMs ≤ 2000`，且禁止 Slate 模态（`FMessageDialog` 只允许出现在非 registry 的 UI 呈现层，且 test-only 必须可抑制，参照 P11 `SetTestDialogSuppression`）。
后果：N0 不允许新增 off-thread 执行模型；未来异步必须新增 `invokeAsync` 合同版本，不得偷偷在 handler 里起线程。

**N0-D10 错误分类学。**
新增 `native-*` 错误类别（registry/auth/gate/schema/limit/transaction/internal），每条错误含 `code/category/retryable/terminal/details/suggestedAction`；legacy 方法继续返回**原错误码不变**（兼容），但同一 handler 在 `native.invoke` 下返回 native 类别码。映射表见 §11.3。

**N0-D11 审计。**
每次 invoke（含拒绝）产生一条 `ueak.native.audit/1.0` 记录：`auditId/toolId/toolVersion/access/decision/gateFailed/sessionId/editorSessionId/projectName/projectPathHash/requestDigest(session-scoped HMAC)/durationMs/revisionBefore/revisionAfter/transactionId/resultDigest/errorCode`。内存环形缓冲 256 条；只读工具不落盘；写工具与 test-only 调用额外写 `Saved/UEAgentKit/native-audit/<sessionId>.jsonl`（best-effort，写失败只置 `auditPersisted=false`，不得把已成功的写入报成失败）。`native.audit.tail` 只读工具最多返回 100 条。
后果：审计不得包含绝对路径、令牌、端口、SQLite 路径；`requestDigest` 使用会话密钥 HMAC-SHA256，避免把参数原文写盘。

**N0-D12 Python 边界：算法与协议留在 Python，编辑器内不再执行 Python。**
目标态：**UE 进程内没有 Python 执行**。布局算法继续在 Python（`graph_contracts/layout.py`、`_layout_layered.py`），但它只在 **MCP/离线进程**运行；UE 只提供原生只读数据（preflight/context）与（未来）原生布局实现。P11 的“编辑器内跑 worker”不是被替换成“编辑器内跑另一个 Python”，而是被**结构性移除**。
后果：N1 完成后 `IPythonScriptPlugin` 引用数必须为 0、`Build.cs` 中 `PythonScriptPlugin` 依赖必须移除、插件源码中 `py.exe` 必须为 0（grep 门禁，见 §13）。

**N0-D13 兼容性规则。**
①`native.invoke` 的 wire `schemaVersion` 仍是 `"1.0"`（帧格式不变，native 信封版本在参数内）；②registry 清单哈希随 `hello`/`editor.status` 以新增字段 `nativeRegistry{registryVersion,hash,toolCount}` 发布（老客户端忽略未知字段）；③未知参数一律拒绝（沿用 `LiveEditorBridgeService._reject_unknown_params` 的严格风格）；④结果只允许新增字段；⑤alias 至少保留一个 minor 版本，移除需 `removalVersion` + 测试断言 `native-alias-retired`。

**N0-D14 不碰引擎 Private API。**
只用 Public 头文件与已存在的扩展点；不 fork 引擎；不 include `Private/` 路径；跨版本（5.6→5.7）以头文件核验 + 自动化回归为门槛。若某能力只能靠 Private，则该能力**不做**，除非用户另授权并给出成本评估。

**N0-D15 文档与代码同源。**
registry 清单是契约；`spec/native-tools/` 的示例必须能被机器校验（本轮已用 JSON 解析探针校验全部 ```json 块）。任何示例与清单不一致视为文档缺陷。

## 4. Registry 清单模型

### 4.1 顶层

```json
{
  "schemaVersion": "ueak.native-registry/1.0",
  "registryVersion": "1.0.0",
  "host": {
    "pluginVersion": "0.9.0",
    "minEngineVersion": "5.6.1",
    "engineVersionTested": "5.6.1"
  },
  "tools": []
}
```

`host.pluginVersion` 只用于文档/诊断；真正的版本一致性仍由既有 `hello`（`live-editor-version-mismatch`）负责，不重复实现。

### 4.2 工具条目

字段**复用** `ueak.operation/1.0` 的词汇（`readOnly/risk/requires/transaction/limits/evidence/errorCodes/implementation/minHostVersion/deprecated/replacement/requiresExplicitConfirmation`），新增 `exposure`、`gating`、`threading`、`dto`、`audit`。`domain` 在 operation 的 5 值基础上增加 `editor` 与 `workflow`（这两个值只用于 native 清单，**不修改** `ueak.operation/1.0` schema；R2 若需要再由专门变更引入 operation 1.1）。

```json
{
  "name": "graph.layout.preflight",
  "version": "1.0.0",
  "domain": "graph",
  "titleKey": "ueak.tool.graph.layout.preflight.title",
  "readOnly": true,
  "risk": "R0",
  "implementation": "core",
  "capabilities": ["graph-layout-preflight"],
  "requires": {
    "editor": true,
    "transaction": false,
    "compile": false,
    "save": false,
    "policyKeys": [],
    "revisionScope": "none"
  },
  "exposure": {
    "bridge": {
      "method": "native.invoke",
      "aliases": ["editor.getBlueprintGraphLayoutPreflight"]
    },
    "mcp": {
      "toolName": "ue_get_blueprint_graph_layout_preflight",
      "group": "live-read",
      "since": "0.8.0"
    },
    "toolMenu": null,
    "cli": null
  },
  "gating": {
    "defaultDecision": "deny",
    "maturity": "stable",
    "gateProfile": null,
    "requiresPIEStopped": false,
    "requiresPackageClean": false,
    "requiresAssetLoaded": true,
    "mayLoadAsset": false,
    "requiresConfirmation": false,
    "assetAllowlist": []
  },
  "threading": {
    "execution": "game",
    "blocking": false,
    "maxBlockMs": 0
  },
  "dto": {
    "paramsSchemaId": "ueak.native.graph.layout.preflight.params/1.0",
    "resultSchemaId": "ueak.native.graph.layout.preflight.result/1.0",
    "maxRequestBytes": 4096,
    "maxResponseBytes": 1048576,
    "maxItems": 512,
    "truncation": "explicit-flags"
  },
  "transaction": {
    "kind": "none",
    "snapshot": "io",
    "rollback": "none"
  },
  "limits": {
    "maxOperationsPerInvocation": 1,
    "maxAffectedNodes": 0,
    "timeoutMs": 2000
  },
  "errorCodes": [
    "graph-layout-editor-unavailable",
    "graph-layout-graph-not-focused",
    "graph-layout-graph-not-k2"
  ],
  "evidence": {
    "required": [],
    "optional": ["receipt"]
  },
  "audit": {
    "persist": false,
    "includeResultDigest": true
  },
  "minHostVersion": "0.9.0",
  "deprecated": false
}
```

字段约束（加载时强制）：

| 字段 | 约束 |
|---|---|
| `readOnly` | `true` ⇒ `requires.transaction=false`、`transaction.kind="none"`、`requires.save=false` |
| `readOnly=false` | 必须 `requires.transaction=true` 且 `transaction.rollback ∈ {undo, reverse-ops}` |
| `gating.maturity` | `stable / experimental / test-only / deprecated` |
| `maturity=test-only` | 必须 `gateProfile != null`、`assetAllowlist` 非空、`requiresConfirmation=true` |
| `exposure.*` | 至少一个 surface 非 null；`bridge.aliases` 只能指向已存在 legacy 方法名 |
| `dto.maxResponseBytes` | ≤ 1048576（Python Bridge 单响应上限，`LIVE_EDITOR_BRIDGE.md:92`） |
| `dto.maxRequestBytes` | ≤ 65536（bridge 单请求上限，`EditorBridge.cpp:47`） |
| `threading.execution` | 仅 `"game"`（N0 不支持其它值） |
| `transaction.kind` | `none / value / structure`（与 operation descriptor 同义） |
| `deprecated=true` | 必须给 `replacement` |

**关于 schemaId**：§4.2/§4.3 示例中的 `dto.*SchemaId` 是 N0 冻结的**目标 id 形态**；实现时必须随工具提供对应 JSON Schema 文件。清单加载校验要求 schemaId 可解析（文件存在且可被校验器加载），否则启动失败（fail-closed，`native-registry-invalid-manifest`）。N0 阶段尚不存在这些新 schema 文件，这是 N1 的实现任务，不是 N0 的缺陷。

### 4.3 test-only 写入门禁条目（示例）

```json
{
  "name": "graph.layout.applyTestOnly",
  "version": "1.0.0",
  "domain": "graph",
  "titleKey": "ueak.tool.graph.layout.applyTestOnly.title",
  "readOnly": false,
  "risk": "R2",
  "implementation": "core",
  "capabilities": ["graph-layout-apply-test-only"],
  "requires": {
    "editor": true,
    "transaction": true,
    "compile": false,
    "save": false,
    "policyKeys": [],
    "revisionScope": "asset+graph"
  },
  "exposure": {
    "bridge": {
      "method": "native.invoke",
      "aliases": ["editor.applyBlueprintGraphLayoutP9TestOnly"]
    },
    "mcp": {
      "toolName": "ue_apply_blueprint_graph_layout_p9_test_only",
      "group": "live-action",
      "since": "0.9.0"
    },
    "toolMenu": {
      "section": "UEAgentKit.Native",
      "entryName": "UEAgentKit.Native.GraphLayout.ApplyTestOnly",
      "context": "graph",
      "order": 100
    },
    "cli": null
  },
  "gating": {
    "defaultDecision": "deny",
    "maturity": "test-only",
    "gateProfile": "p9-disposable-host",
    "requiresPIEStopped": true,
    "requiresPackageClean": true,
    "requiresAssetLoaded": true,
    "mayLoadAsset": false,
    "requiresConfirmation": true,
    "confirmationTemplate": "APPLY P9 TEST-ONLY {planId}",
    "assetAllowlist": ["/Game/UEAgentKitP9/BP_LayoutP9.BP_LayoutP9"]
  },
  "threading": {
    "execution": "game",
    "blocking": false,
    "maxBlockMs": 0
  },
  "dto": {
    "paramsSchemaId": "ueak.graphlayoutapply/1.0",
    "resultSchemaId": "ueak.native.graph.layout.apply.result/1.0",
    "maxRequestBytes": 65536,
    "maxResponseBytes": 1048576,
    "maxItems": 512,
    "truncation": "explicit-flags"
  },
  "transaction": {
    "kind": "value",
    "snapshot": "graph",
    "rollback": "undo"
  },
  "limits": {
    "maxOperationsPerInvocation": 512,
    "maxAffectedNodes": 512,
    "timeoutMs": 2000
  },
  "errorCodes": [
    "native-gate-test-only-project-rejected",
    "native-gate-test-only-build-rejected",
    "native-gate-confirmation-required",
    "native-gate-revision-conflict",
    "native-gate-fingerprint-conflict",
    "native-transaction-readback-failed"
  ],
  "evidence": {
    "required": ["plan", "apply-report", "receipt"],
    "optional": ["typedgraph-before", "typedgraph-after"]
  },
  "audit": {
    "persist": true,
    "includeResultDigest": true
  },
  "minHostVersion": "0.9.0",
  "deprecated": false
}
```

注意：`dto.paramsSchemaId` 直接引用已存在的 `ueak.graphlayoutapply/1.0`（`schemas/graph-layout-apply.schema.json`），**不为 native 入口另造一套请求格式**；native 只是把它装进统一信封。`limits.*` 是 registry 的**硬上限**，D-06 的 profile（200/300/1000）只能更严格，不能放宽；两者冲突时取更小值。

## 5. 统一调用入口与 DTO

### 5.1 请求（wire，newline-delimited JSON，沿用既有帧）

```json
{
  "requestId": "6d1f0f0e-6f4f-4a1f-9d6f-1a2b3c4d5e6f",
  "schemaVersion": "1.0",
  "method": "native.invoke",
  "params": {
    "toolId": "graph.layout.preflight",
    "toolVersion": "1.0.0",
    "arguments": {},
    "context": {
      "surface": "toolMenu",
      "assetPath": "/Game/UEAgentKitP9/BP_LayoutP9.BP_LayoutP9",
      "graphGuid": "01234567-89ab-cdef-0123-456789abcdef",
      "nodeGuid": "11111111-2222-3333-4444-555555555555",
      "pinName": "then"
    }
  }
}
```

`hello` 仍是每连接第一步（令牌/版本/工程摘要），`native.invoke` 不重复认证。`context.surface` 取值固定为 `toolMenu / mcp / cli`（与 `exposure` 的键同名，避免两套词汇）。按 surface 严格校验合法子集：`toolMenu` 必须带 `assetPath+graphGuid`（node/pin 可选）；`mcp` 允许空 context（工具自行解析 focused editor）；未知 context 键拒绝。

### 5.2 成功结果

```json
{
  "requestId": "6d1f0f0e-6f4f-4a1f-9d6f-1a2b3c4d5e6f",
  "schemaVersion": "1.0",
  "ok": true,
  "result": {
    "toolId": "graph.layout.preflight",
    "toolVersion": "1.0.0",
    "auditId": "audit:8f2c1d4e",
    "access": "read",
    "editorSessionId": "3f6d2a1c9b8e4d70",
    "registryVersion": "1.0.0",
    "data": {
      "assetPath": "/Game/UEAgentKitP9/BP_LayoutP9.BP_LayoutP9",
      "graphGuid": "01234567-89ab-cdef-0123-456789abcdef",
      "packageRevision": "sha256:2f7c0d9e4b1a8c6d5e3f2a1b0c9d8e7f6a5b4c3d2e1f0a9b8c7d6e5f4a3b2c1d",
      "packageDirty": false,
      "graphFingerprint": "sha1:9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a0b",
      "graphFingerprintAlgorithm": "sha1-length-prefixed-graph-state-v1",
      "nodeCount": 11,
      "nodes": [],
      "nodesTruncated": false,
      "complete": true
    },
    "revision": {
      "scope": "none",
      "assetRevision": null,
      "graphFingerprint": null
    }
  }
}
```

`data` 的形状由该工具的 `dto.resultSchemaId` 决定；`result` 外层字段由 N0 固定。读工具的 `data` 允许截断，但必须给 `nodesTruncated/complete` 明确布尔（沿用 P6 preflight 的 512 上限与 `complete` 语义）。

### 5.3 失败结果

```json
{
  "requestId": "6d1f0f0e-6f4f-4a1f-9d6f-1a2b3c4d5e6f",
  "schemaVersion": "1.0",
  "ok": false,
  "error": {
    "code": "native-gate-fingerprint-conflict",
    "category": "gate",
    "message": "The live graph changed after preflight.",
    "retryable": false,
    "terminal": true,
    "details": {
      "expected": "sha1:9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a0b",
      "actual": "sha1:0000000000000000000000000000000000000000"
    },
    "suggestedAction": "re-run graph.layout.preflight and re-plan"
  }
}
```

### 5.4 上下文对象（读工具通用 `context` 结果）

```json
{
  "schemaVersion": "ueak.native.context/1.0",
  "engine": {
    "version": "5.6.1",
    "changelist": 44394996,
    "buildConfiguration": "Development"
  },
  "world": {
    "type": "Editor",
    "pieState": "stopped",
    "currentLevel": "/Game/UEAgentKitP9/Maps/LayoutP9",
    "worldId": "5b1d0c9a-7e6f-4a3b-8c2d-1e0f9a8b7c6d"
  },
  "editor": {
    "editorName": "BlueprintEditor",
    "sessionId": "3f6d2a1c9b8e4d70",
    "pluginVersion": "0.9.0",
    "pluginBuildHash": "sha256:7eb2db917a355dd53bc8f95c7987507c824f4837929f1bb051dfdc8186cb8e6a",
    "pieState": "stopped"
  },
  "assets": [
    {
      "assetPath": "/Game/UEAgentKitP9/BP_LayoutP9.BP_LayoutP9",
      "assetClass": "/Script/Engine.Blueprint",
      "packageRevision": "sha256:2f7c0d9e4b1a8c6d5e3f2a1b0c9d8e7f6a5b4c3d2e1f0a9b8c7d6e5f4a3b2c1d",
      "packageDirty": false,
      "loaded": true,
      "loadedByBridge": false,
      "graphGuid": "01234567-89ab-cdef-0123-456789abcdef",
      "graphFingerprint": "sha1:9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a0b"
    }
  ],
  "revision": {
    "strategy": "package-sha256-v1",
    "graphFingerprintAlgorithm": "sha1-length-prefixed-graph-state-v1"
  }
}
```

`editor.pluginBuildHash` 与 `assets[].packageRevision` 是既有字段语义的汇总，不新增事实源；`assets[].loadedByBridge=false` 沿用“不偷偷加载资产”的既有承诺。

### 5.5 写调用必须携带的期望值

`arguments` **就是** `ueak.graphlayoutapply/1.x` 的现有文档（`schemas/graph-layout-apply.schema.json`），不重新嵌套、不改字段名；native 只在外层加信封。

```json
{
  "toolId": "graph.layout.applyTestOnly",
  "toolVersion": "1.0.0",
  "arguments": {
    "schemaVersion": "ueak.graphlayoutapply/1.0",
    "assetPath": "/Game/UEAgentKitP9/BP_LayoutP9.BP_LayoutP9",
    "graphGuid": "01234567-89ab-cdef-0123-456789abcdef",
    "expectedPackageRevision": "sha256:2f7c0d9e4b1a8c6d5e3f2a1b0c9d8e7f6a5b4c3d2e1f0a9b8c7d6e5f4a3b2c1d",
    "expectedGraphFingerprint": "sha1:9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a0b",
    "plan": {
      "schemaVersion": "ueak.layoutplan/1.0",
      "planId": "plan:3c2b1a0f",
      "status": "ok",
      "layoutHash": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
      "baseGraphHash": "sha1:9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a0b",
      "positions": [],
      "movedNodeIds": [],
      "scopeNodeIds": [],
      "fixedNodeIds": []
    },
    "requestedMode": "position-only",
    "allowSave": false,
    "allowCompile": false,
    "acceptanceEligible": false,
    "requiredAcceptanceEvidence": "independent-reload"
  },
  "confirmation": "APPLY P9 TEST-ONLY plan:3c2b1a0f"
}
```

`confirmation` 是 `params` 顶层字段，对应 legacy 的 `{request, confirmation}` 形态；`context.surface` 不参与写门禁判定。

## 6. ToolMenus 绑定合同

菜单条目不是独立实现，只是 registry 条目的一个 surface。

| 规则 | 内容 |
|---|---|
| 绑定时机 | `StartupModule` 内、EditorBridge 启动后、registry 加载成功后；仅 `GIsEditor && !IsRunningCommandlet && !bAutomationChild`（沿用 `UEAgentKitEditorModule.cpp:16-36`） |
| 扩展点 | 只允许 `UToolMenus::ExtendMenu` + `AddDynamicSection`（P11 阶段 UE 实机证据，见 `C_LAYOUT_P11_EDITOR_SCOPE_ACCEPTANCE_REPORT_20261023.md`；本轮未复验）；禁止使用旧 `GraphEditorGraphContextMenu` 扩展点（P11 头注释记录其在 5.6 已不存在） |
| 动态可见性 | section 回调里按 `UGraphNodeContextMenuContext` 判定 node/pin/graph 上下文；无上下文则不添加条目 |
| 文本 | 清单只存 `titleKey`；C++ 用 `LOCTEXT` 映射，菜单文本不进入任何哈希（沿用 ND-LOCALIZED-BAN） |
| 执行 | `ExecuteAction` 只做：从 context 提取 `assetPath/graphGuid/nodeGuid/pinName` → `Invoke(toolId, ...)` → 呈现结果；**不得**直接调用业务函数 |
| 模态 | 禁止无条件 `FMessageDialog`；测试可抑制；长时间操作返回 receipt 而非阻塞弹窗 |
| 卸载 | `ShutdownModule` 先 `RemoveSection`，再注销 registry；顺序反了会留悬空委托 |
| 失败呈现 | 显示 `error.code + suggestedAction`，不得把内部路径/令牌显示出来 |

菜单条目的 `exposure.toolMenu.context` 取值：`graph`、`node`、`pin`、`asset`、`always`。`asset` 仅用于 Content Browser 上下文，本轮不实现（无已验证扩展点），故 N0 清单不使用该值。

## 7. MCP 投影与漂移检测

1. Python 侧 `tool_registry.py` 仍是 MCP 的**注册白名单**（不可被 Agent 参数覆盖），新增可选字段 `nativeToolId`，保持现有 `name/group/annotation/live_method` 语义与顺序。
2. **模式计数冻结**：只读 10 / Live 45 / Workflow 67 / Memory 24 / Live+Workflow 102 / 全开 116，由 `tests/python/test_tool_registry.py:147-177` 断言。N0/N1 不得改动这些数字，除非同一变更同时更新测试与本文档。
3. 漂移检测：启用 Live Editor 时，MCP 调用只读工具 `native.registry.describe`（新增，`readOnly=true`）拿到 `{registryVersion, hash, toolCount, tools:[{name,version,readOnly}]}`，与本地路由表比较：toolId 集合差、MAJOR 不等、hash 在会话内变化 → 拒绝该工具并返回 `native-registry-version-mismatch`（映射为 MCP 侧 `live-editor-version-mismatch` 同级错误）。**不做静默降级**。
4. 路由：`ToolDefinition.live_method` 继续作为 legacy 方法；`nativeToolId` 非空时优先 `native.invoke`。两者必须指向同一 handler（由 C++ 单测/代码审查保证）。
5. 离线模式不依赖 registry：无 Live Editor 时不注册 native 工具，`ue_get_capabilities` 不宣称它们存在。

## 8. 授权、readonly、test-only、Apply 的 fail-closed 判定

### 8.1 判定顺序（固定）

| 序 | 检查 | 失败码 |
|---|---|---|
| 1 | registry 已加载且未冻结失败 | `native-registry-unavailable` / `native-registry-invalid-manifest` |
| 2 | `toolId` 存在 | `native-tool-unknown` |
| 3 | `toolVersion` 可协商（MAJOR 相等、MINOR ≤ 清单） | `native-tool-version-unsupported` |
| 4 | 连接已 `hello` | `live-editor-authentication-required`（沿用） |
| 5 | surface 允许（例：MCP 不可调 `toolMenu`-only 工具） | `native-tool-surface-unsupported` |
| 6 | capability 已注册 | `live-editor-capability-unavailable`（沿用） |
| 7 | maturity：`stable` 直接过；`test-only` 走 §8.2；`deprecated` 允许但回 `deprecation` 提示；`experimental` 需 capability 显式开启 | `native-gate-*` |
| 8 | 环境：Editor 可用、PIE 状态、资产已加载、World 有效 | `native-gate-editor-unavailable` / `-pie-active` / `-asset-not-loaded` / `-world-unavailable` |
| 9 | revision：dirty / revision / fingerprint | `native-gate-package-dirty` / `-revision-conflict` / `-fingerprint-conflict` |
| 10 | transaction：无进行中的 native 写 | `native-transaction-busy` |
| 11 | schema：`arguments` 校验（未知字段拒绝） | `native-schema-invalid-arguments` |
| 12 | 执行 | `native-internal-error` |

任何一步拒绝都**不得**产生 UObject 修改、Dirty 变化或事务。

### 8.2 test-only 放行（全部满足）

1. `gateProfile` 在编译期表中存在；
2. 规范化 `.uproject` 绝对路径 == profile 常量；
3. 工程名 == profile 常量；
4. `.uproject` SHA-256 == profile 常量；
5. 工程目录内 marker 文件 JSON 的 `pluginBuildHash` == 当前加载 DLL SHA-256；
6. `assetPath` ∈ `gating.assetAllowlist`（精确 Object Path）；
7. focused graph GUID == 请求 `graphGuid`；
8. `packageDirty == false`、`editable == true`、PIE stopped；
9. `confirmation` 严格等于模板渲染值（含 `planId`）；
10. 请求 schema/plan 通过既有 shape 校验（`ValidatePlanShape`/`ComputeLayoutHash`）。

拒绝码细分：2–4 → `native-gate-test-only-project-rejected`；5 → `native-gate-test-only-build-rejected`；6 → `native-gate-test-only-asset-rejected`；9 → `native-gate-confirmation-required`；其余沿用既有 `graph-layout-*`/`native-gate-*`。

### 8.3 Apply

- N0/N1 **不开放通用 Apply**。任何 `readOnly=false` 且 `maturity=stable` 的 graph 结构写入条目，在 N0 阶段一律不允许出现在清单中；R2 之前只允许 `test-only`。
- 写入必须同时满足：`revisionScope=asset+graph`、期望值匹配、单事务、同事务读回、Dirty 确认、显式确认短语、`maturity=test-only`。
- Apply 永不 Save、永不 Compile、永不自动重试。重试必须由调用方在重新 preflight 后发起（`retryable=false` 的写入错误不给自动重试建议）。

## 9. Game thread 与 Editor 生命周期

### 9.1 不变式

| ID | 不变式 |
|---|---|
| I1 | 所有 handler 在 game thread；跨线程调用是 bug，直接 `check` 失败而不是静默排队 |
| I2 | 不跨调用缓存裸 `UObject*`；只缓存 `FGuid`/`FSoftObjectPath`/`TWeakObjectPtr`，每次调用重新解析并验证 |
| I3 | registry 在首个 invoke 后冻结；运行期注册返回 `native-registry-frozen` |
| I4 | 每个 editor session 同时最多一个写调用（`native-transaction-busy`）；读调用串行执行 |
| I5 | PIE/SIE 期间写工具拒绝；Batch/Automation 等待中的任务在 PIE 开始时进入明确终态 |
| I6 | 卸载顺序：菜单 unbind → registry 注销 → bridge stop；不得遗留未提交事务 |
| I7 | 不允许 handler 打开 Slate 模态（除 test-only 且可抑制的提示） |
| I8 | 所有工具结果必须带 `editorSessionId`；跨 session 使用旧结果必须被拒绝（沿用 undo/discard 的 session 校验风格） |

### 9.2 生命周期序

| 阶段 | 动作 | 失败处理 |
|---|---|---|
| `StartupModule` | 加载清单 → 校验 → 注册 handler → 绑定菜单 → 启动 bridge | 清单失败：registry 不可用但 bridge 仍起（便于诊断），所有 native 工具拒；菜单不绑定 |
| 首帧后 | `FTSTicker` 处理 socket/批任务 | 既有行为不变 |
| PIE 开始/结束 | 置 `pieState`，写工具改判 | 进行中的 batch task 标 `invalidated` |
| `ShutdownModule` | 取消 pending automation/batch → 菜单 unbind → registry 注销 → bridge stop | 未提交事务：记录 `audit` 并 `Cancel()` |

## 10. Asset revision / transaction / save / undo

### 10.1 revision 事实源（保持四源分离，`LIVE_EDITOR_BRIDGE.md:371-388`）

| 源 | 内容 | 是否可用于写门禁 |
|---|---|---|
| Editor Memory | 选择、打开、Dirty、PIE | 只作环境门 |
| Disk Package | `.uasset` SHA-256（`package-sha256-v1`） | 是（唯一） |
| Revision Export | MCP 会话冻结快照 | 离线工作流用 |
| Immutable Index | SQLite 快照 | 查询用 |

### 10.2 写序列（必须按序，任一步失败即停）

1. 解析资产（不加载）→ 2. 校验 editor session 与 focused graph → 3. 重算 `packageRevision` → 4. 重算 `graphFingerprint` → 5. 开 `FScopedTransaction` → 6. `Modify()` 目标对象 → 7. 变更 → 8. 同事务读回逐字比较 → 9. `MarkBlueprintAsModified` + Dirty 确认 → 10. 提交并返回 `transactionId`。
失败：恢复快照 → 恢复 `bDirtyBefore` → `Cancel()` → 返回错误；不得“部分成功”。

### 10.3 Save

- 独立工具（`asset.save.authorized`，`maturity=test-only` 起），确认短语绑定资产与 revision；
- 前置：`packageDirty=true`、PIE stopped、revision 与调用方声明一致；
- 结果：磁盘 SHA-256、`saved=true`、`revisionAfter`；
- 禁止：Apply 内部 Save、批量隐式 Save、Save 失败后回滚内存修改（内存修改仍有效，如实报告）。

### 10.4 Undo / Discard / Redo

| 动作 | 允许 | 约束 |
|---|---|---|
| Undo | 是（工具） | `transactionId + editorSessionId + assetPath` 三者精确；栈顶必须就是该事务；包未被保存过；目标值仍等于 apply 的 `afterValue` |
| Discard | 是（工具） | 同上，`UndoTransaction(false)` |
| Redo | **否（无通用工具）** | 栈身份无法跨调用证明；`test-only` 允许“undo→verify→redo→verify”单调用探针并返回证据 |

## 11. 错误码、审计

### 11.1 类别

| category | 含义 | retryable 默认 |
|---|---|---|
| `registry` | 清单/注册/冻结 | 否 |
| `auth` | 令牌/版本/工程摘要 | 否（可修复后重连） |
| `capability` | 未注册能力 | 否 |
| `gate` | 授权/环境/修订拒绝 | 否（`-editor-unavailable` 可重试） |
| `schema` | 参数/结果不合 schema | 否 |
| `limit` | 超限/截断 | 截断非错误；超限否 |
| `transaction` | 事务/读回/Dirty | 否 |
| `internal` | 未预期 | 视情况 |

### 11.2 新增码（N0 冻结集合）

```text
native-registry-unavailable
native-registry-invalid-manifest
native-registry-frozen
native-registry-version-mismatch
native-tool-unknown
native-tool-version-unsupported
native-tool-surface-unsupported
native-gate-denied
native-gate-editor-unavailable
native-gate-pie-active
native-gate-test-only-project-rejected
native-gate-test-only-build-rejected
native-gate-test-only-asset-rejected
native-gate-confirmation-required
native-gate-asset-not-loaded
native-gate-world-unavailable
native-gate-package-dirty
native-gate-package-invalid
native-gate-revision-conflict
native-gate-fingerprint-conflict
native-schema-invalid-arguments
native-schema-invalid-result
native-limit-request-too-large
native-limit-response-too-large
native-transaction-unavailable
native-transaction-busy
native-transaction-readback-failed
native-transaction-dirty-not-confirmed
native-write-forbidden-for-read-tool
native-alias-retired
native-audit-unavailable
native-internal-error
```

### 11.3 legacy 映射（legacy 方法仍返回原码，native.invoke 下用新码）

| legacy | native |
|---|---|
| `graph-layout-test-only-disabled` | `native-gate-denied`（`details.legacyCode` 保留原码） |
| `graph-layout-test-only-project-rejected` | `native-gate-test-only-project-rejected` |
| `graph-layout-test-only-build-rejected` | `native-gate-test-only-build-rejected` |
| `graph-layout-test-only-asset-rejected` | `native-gate-test-only-asset-rejected` |
| `graph-layout-revision-conflict` | `native-gate-revision-conflict` |
| `graph-layout-fingerprint-conflict` | `native-gate-fingerprint-conflict` |
| `graph-layout-package-dirty` | `native-gate-package-dirty` |
| `graph-layout-invalid-parameters` | `native-schema-invalid-arguments` |
| `graph-layout-transaction-unavailable` | `native-transaction-unavailable` |
| `graph-layout-readback-failed` | `native-transaction-readback-failed` |

### 11.4 审计记录

```json
{
  "schemaVersion": "ueak.native.audit/1.0",
  "auditId": "audit:8f2c1d4e",
  "observedAtUtc": "2026-10-23T15:52:58.578Z",
  "toolId": "graph.layout.applyTestOnly",
  "toolVersion": "1.0.0",
  "access": "write",
  "decision": "denied",
  "gateFailed": "revision",
  "errorCode": "native-gate-revision-conflict",
  "sessionId": "3f6d2a1c9b8e4d70",
  "editorSessionId": "3f6d2a1c9b8e4d70",
  "projectName": "P9AutomationHost",
  "projectPathHash": "sha1:0d1e2f3a4b5c6d7e8f90123456789abcdef01234",
  "requestDigest": "hmac-sha256:5c4b3a2918f7e6d5",
  "surface": "mcp",
  "durationMs": 3,
  "revisionBefore": null,
  "revisionAfter": null,
  "transactionId": null,
  "resultDigest": null
}
```

规则：只读调用 `persist=false`；写与 test-only 调用 `persist=true`；审计写盘失败只置 `auditPersisted=false`；`requestDigest` 用会话密钥 HMAC，不落原文。

## 12. 兼容性

1. **wire 不变**：`schemaVersion="1.0"`、newline JSON、hello 先行、64 KiB 请求 / 1 MiB 响应、单请求单响应。新增 native 信封在 `params` 内层版本化。
2. **新增字段只增不改**：`hello`/`editor.status` 增加可选 `nativeRegistry{...}`；老客户端忽略。
3. **toolId 永不复用**；`version` MAJOR 变更视为新合同（旧版本可在过渡期并存，`exposure` 分别声明）。
4. **alias 生命周期**：canonical 工具标 `stable` 后 alias 至少保留一个 minor；移除时必须 `removalVersion` + 测试断言 `native-alias-retired` + 文档变更记录。
5. **未知参数拒绝、未知结果字段忽略**（严格入、宽松出）。
6. **计数冻结**：MCP 模式计数与 `test_tool_registry.py` 同步；任何计数变化必须同 commit 改测试与文档。
7. **清单哈希**：`registry.hash` = 对清单规范 JSON 做域分离哈希（`ueak.native.registry/1`），会话内必须稳定；变化即 `native-registry-version-mismatch`。

## 13. Python worker / CLI 的保留与退场边界

| 领域 | 现状 | N0 判定 | 退出/保留条件 |
|---|---|---|---|
| 布局算法（`graph_contracts/layout.py`、`_layout_layered.py`） | Python，P0–P12 验收基线 | **保留**；只在 MCP/离线进程运行 | C++ 实现只有在同输入 parity、布局哈希/拓扑一致、安全负例与性能均通过后才可替换（N4） |
| MCP 协议与工具注册（`mcp_server.py`、`mcp_*_tools.py`、`tool_registry.py`） | Python | **保留** | 长期；它是 Agent 侧唯一白名单 |
| 编辑器内 Python（P11 `IPythonScriptPlugin`） | 存在 | **退场**（N1 第一步） | 插件源码 `IPythonScriptPlugin` = 0；`Build.cs` 移除 `PythonScriptPlugin`；预览改由 MCP 侧算法 + 原生只读工具 |
| 菜单外启 `py.exe`（P8/P9） | 存在，实际不可达 | **退场** | 插件源码 `py.exe` = 0；P8/P9 菜单类删除或改为 registry 菜单条目 |
| 外部进程跑隔离 Automation（`UnrealEditor-Cmd`） | 存在 | **保留**（进程编排） | 不得用于“测试当前同一 Editor 实例”；证据目录隔离 |
| 构建/CI/发布脚本（PowerShell/Python） | 存在 | **保留** | 不嵌入 UE C++ |
| 离线语料/基准/索引 | Python | **保留** | 与运行时解耦 |

可度量退出条件（N1 完成时全部满足才算退场）：

```text
grep -R "IPythonScriptPlugin" Plugin/            == 0
grep -R "py.exe" Plugin/                        == 0
grep -R "PythonScriptPlugin" Plugin/**/*.Build.cs == 0
```

并且：`ue_preview_blueprint_graph_layout` 行为不变（同一算法、同一 fail-closed 条件），P11 菜单替代路径有自动化证据。

## 14. UE Public API 可行性及风险

| 能力 | API | 等级 | 风险/备注 |
|---|---|---|---|
| 图/节点/Pin 读取 | `UEdGraph`/`UEdGraphNode`/`UEdGraphPin` | `SOURCE-READ`（现网已用） | 无 |
| 焦点图定位 | `FBlueprintEditor` + toolkit tab | `SOURCE-READ`（P6–P11 已用） | 依赖“最近激活的 BlueprintEditor”策略，非 API 保证 |
| 事务/Undo | `FScopedTransaction`、`GEditor->UndoTransaction` | `SOURCE-READ`（P7–P11 实机） | 栈顶身份校验必须保留 |
| 保存 | `UPackage::SavePackage` | `SOURCE-READ`（P9 test-only 已用） | 只允许显式 Save 工具 |
| 菜单 | `UToolMenus::ExtendMenu`、`AddDynamicSection` | `SOURCE-READ`（P11 阶段实机，非本轮） | 只允许动态 section 形态 |
| 节点上下文 | `UGraphNodeContextMenuContext`（`EdGraphNode.h:214`） | `HEADER-VERIFIED` | 引擎公开类 |
| 动作数据库 | `FBlueprintActionDatabase::Get/GetAllActions/OnEntryUpdated` | `HEADER-VERIFIED`（`BlueprintActionDatabase.h:44,64,92,166`） | 首次访问触发 prime；成本未测 |
| 动作上下文集 | `FBlueprintActionContext{Blueprints,Graphs,Pins,SelectedObjects}` | `HEADER-VERIFIED`（`BlueprintActionFilter.h:47-83`） | 与菜单同源 |
| 动作过滤 | `FBlueprintActionFilter::IsFiltered` | `HEADER-VERIFIED`（`:370`） | 过滤结果即菜单语义 |
| 菜单动作构建 | `BlueprintActionMenuUtils::MakeContextMenu`、`FBlueprintActionMenuBuilder`、`FBlueprintActionMenuItem::GetRawAction` | `HEADER-VERIFIED`（`BlueprintActionMenuUtils.h:61`、`BlueprintActionMenuBuilder.h:32`、`BlueprintActionMenuItem.h:56`） | 需处理 time-slicing；文本为本地化字段，不得进哈希 |
| 条目枚举 | `FGraphActionListBuilderBase::GetNumActions/GetSchemaAction` | `HEADER-VERIFIED`（`EdGraphSchema.h:591,594`） | 5.5 起 `ActionGroup` 已弃用，必须用 `GetSchemaAction` |
| Spawner 身份/模板 | `UBlueprintNodeSpawner::GetSpawnerSignature/GetTemplateNode/GetCachedTemplateNode/IsTemplateNodeFilteredOut` | `HEADER-VERIFIED`（`BlueprintNodeSpawner.h:186,221,233,245`） | 模板节点在 transient outer 创建、跳过 `Modify/AddNode`（`BlueprintNodeSpawner.cpp:339-351`）；仍会分配 UObject，需 GC/性能实测 |
| 图类型 | `EGraphType`（`GT_Function/GT_Ubergraph/GT_Macro/...`） | `HEADER-VERIFIED`（`EdGraphSchema.h:25-33`） | 与 D-01 对齐 |
| 图编辑器扩展 | `FGraphEditorModule::GetAllGraphEditorContextMenuExtender` | `HEADER-VERIFIED`（`GraphEditorModule.h:36`） | **不使用**：UE5.6 上下文菜单已 ToolMenus 化 |

原则：不 include 引擎 `Private/`，不复制引擎内部结构体布局，不依赖 `FNodeTemplateCache` 私有实现（只用其公开访问器）。跨 5.7 的破坏性变更以编译 + 自动化回归为准，不以“现在能编”当作保证。

## 15. 待人工裁决

| 编号 | 问题 | 建议 |
|---|---|---|
| Q1 | 是否接受 N1 的“编辑器内彻底无 Python”（代价：C++ 布局完成前，编辑器菜单没有实时 Plan 预览） | 接受；菜单改为只读“导出布局上下文”，Agent 侧预览能力不变 |
| Q2 | registry 清单是运行时加载 JSON（本文方案），还是编译期生成表 | 运行时 JSON + 编译期安全常量；可审计、可 diff |
| Q3 | 统一 `native.invoke` 是否与既有 48 个 legacy 方法长期并存 | 并存一个 minor，然后按 alias 规则移除 |
| Q4 | 审计是否允许写 `Saved/`（写工具） | 允许，best-effort、不含敏感字段 |
| Q5 | 菜单是否需要 Content Browser（asset）上下文条目 | 本轮不做（无已验证扩展点） |
| Q6 | 是否把 MCP 文档里 “Live + Workflow = 101” 更正为实测 102 | 需单独文档变更；N0 不改外部文档，仅记录差异（证据 `05-contract-counts.txt`） |
| Q7 | 是否将 `domain` 新增 `editor/workflow` 视为 native 清单私有值 | 是；不改 `ueak.operation/1.0` |
| Q8 | read 工具结果 1 MiB 上限是否足够大图 | 保持 1 MiB + 显式截断标志；大图走分页/裁剪 |

## 16. N0 验收清单

- [x] 单一 registry 模型、字段约束、加载校验规则明确（§4）
- [x] 统一入口 `native.invoke` + request/result/error/context/revision 示例（§5）
- [x] ToolMenus 绑定与“同一 handler”约束（§6）
- [x] MCP 投影、计数冻结、漂移检测（§7）
- [x] 授权梯、readonly 强制、test-only 四要素、Apply fail-closed（§8）
- [x] game-thread 与生命周期不变式（§9）
- [x] revision / transaction / save / undo 语义与写序列（§10）
- [x] 错误分类、新增码集合、legacy 映射、审计模型（§11）
- [x] 兼容性与弃用规则（§12）
- [x] Python/CLI 边界与可度量退出条件（§13）
- [x] UE Public API 核验等级与风险（§14）
- [x] 明确未做：未编译、未启动 UE、未改 P0–P12 运行代码、未开放 Apply
- [ ] 人类评审 Q1–Q8 并批准本文件为 N0 frozen（待批准）
- [ ] R1 代码启动（**未获新指令前不做**）
