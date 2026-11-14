# GraphPatch batching and session-level authorization — design (2026-11-05)

Status: **partly implemented.** 0-3.4 (cross-writer atomic transaction) now exists
as a separate experimental patch contract, and the first writer (pin defaults) is
migrated into it. The other four writers keep their per-operation
Preview → Apply → Save contracts; see "切片 8" at the end for exactly which kinds
a patch accepts today.

## Goal

One GraphPatch submission creates nodes, connects pins and sets unconnected pin
defaults in a single `FScopedTransaction`, returns one per-operation result list,
rolls the whole patch back if any operation fails, and never silently skips an
operation. The per-operation confirmation phrase is replaced by one authorization
at task/EditSession level.

## Current state (why it is three calls today)

| Writer | Bridge method family | Authorization today |
| --- | --- | --- |
| graph edit (create nodes/edges) | `editor.*BlueprintGraphEditExperimental` | planId + `requiredConfirmation` per Apply and per Save |
| pin defaults | `editor.*BlueprintPinDefaultEditExperimental` | same |
| rewire | `editor.*BlueprintRewireExperimental` | same |
| node remove | `editor.*BlueprintNodeRemoveExperimental` | same |
| variable create | `editor.*BlueprintVariableWriteExperimental` | same |

Only node movement is inside the EditSession token/batch framework
(`BlueprintEditSession` + `ApplyNodeMoveBatch`). The five writers each build
their own transaction and their own confirmation contract.

## Proposed structure

1. **One patch object** (schema `ueak.graphpatch/1.0`), applied atomically:
   ```json
   {"assetPath": "...", "graphGuid": "...",
    "sessionToken": "<64 lowercase hex>",
    "expectedGraphFingerprint": "sha1:...",
    "operations": [
      {"opId": "n1", "kind": "createNode", "nodeType": "branch", "tempId": "tmp:a", "x": 0, "y": 0},
      {"opId": "e1", "kind": "connect", "from": "tmp:a", "fromPin": "then", "toNodeGuid": "...", "toPin": "execute"},
      {"opId": "p1", "kind": "setPinDefault", "nodeGuid": "...", "pinGuid": "...", "type": "bool", "value": "true"}
    ]}
   ```
   `tempId` is resolved to real GUIDs inside the patch; later operations may
   reference earlier `tempId`s.
2. **Single transaction.** The patch is applied inside one
   `FScopedTransaction`; every operation is validated against the live graph
   before the first write, then applied, then read back. Any failure restores the
   pre-patch `UEdGraph` state (nodes removed, pins re-linked, defaults restored),
   cancels the transaction and returns per-operation errors with
   `writeOccurred=false`.
3. **Per-operation results.** `results[]` carries `opId`, `status`
   (`applied`/`failed`), the resolved GUID(s), and the precise error code. A
   failed patch reports every operation's outcome; nothing is skipped.
4. **Session-level authorization.** The confirmation phrase becomes a property of
   the EditSession: `begin` returns a session token bound to asset + graph +
   fingerprint, and the caller authorizes the *task* once
   (`experimental=true` + one explicit grant). Each patch is then authorized by
   the token, not by a per-apply phrase. The grant must be revocable with
   `end()` and must expire on any fingerprint/Dirty/target change that today
   invalidates a session.
5. **Writer migration order** (each behind its own flag, one commit each):
   1. pin defaults — smallest surface, no graph structure change;
   2. variable create — asset-level, no edge rewiring;
   3. graph edit — already the general case; becomes the batch entry point;
   4. rewire — needs exact edge-set proofs;
   5. node remove — most destructive, last.
   Until a writer is migrated it keeps its current confirmation contract; mixing
   is allowed but a patch may not contain both migrated and unmigrated kinds.

## Security invariants that must not be relaxed

- No arbitrary class/`nodeClass` from the request; only the allowlisted node
  types in `experimental_graph_edit.NODE_TYPES` and its siblings.
- Policy (`allowedAssetRoots`, `allowedOperations`, `allowedAssetClasses`) still
  applies to every operation before any transaction opens.
- The session token remains 64 lowercase hex, case-sensitive, bound to the
  Bridge session + process, and never accepted from a non-session path.
- A patch failure must never report success for a partially applied graph.

## Why not implemented in this round

Converting the writers is a large, high-risk change that cannot be compiled or
run here (no UBT/Editor), and the current per-writer confirmation contracts are
part of N1's accepted evidence. Doing half of it would create a mixed
authorization model with no native proof. The design above is the prerequisite
for the next P1 slice, where each writer migration is compiled by N1
incrementally.

---

# 实施进度（2026-11-12 更新）

状态从「仅设计」改为 **切片 1–2 已交付（授权摩擦），原子性尚未开始**。

## 已交付

| 切片 | 提交 | 内容 |
|---|---|---|
| 1 | `38715b8` | 五个 writer 的 **Apply** 确认短语可省略，自动由 planId 派生 |
| 2 | `51b99c7` | 五个 writer 的 **Save** 确认短语同上 |

### 为什么这不是放宽安全边界

短语的语义是 `前缀 + planId`，而 `build_apply_parameters` / `validate_save_parameters`
**本来就会自己算出这个值再比对**。也就是说，调用方传的值必须等于函数自己算的值——
它没有引入任何额外校验，只制造了一次「读预览 → 抠出短语 → 带着整个预览再传回来」的往返。

放宽后仍然成立：

- 调用方**必须**传入完整的 preview / apply 结果，盲写仍然不可能；
- 显式传错短语仍按原错误码拒绝（`*-confirmation-required`）；
- planId 过期检查、graph fingerprint、package revision、Policy、事务边界全部未动；
- Save 仍要求上一步 `applied=true` 且 `saved!=true`。

## 尚未开始

| 切片 | 内容 | 说明 |
|---|---|---|
| 3 | **会话级授权**：`begin` 返回的 token 绑定 asset+graph+fingerprint，一次授权覆盖该任务的多个 apply/save，把 3 次调用压成 1 次 | 这是剩余摩擦的主体 |
| 4 | **跨 writer 原子事务**：一次提交内建节点+连线+默认值，任一失败整体回滚，返回逐条结果 | C++ 重构，风险最高，需真实 Editor 反复验证 |

## 切片 3 的一个已确认障碍

`FBlueprintEditSessionManager` 是 **桥实例持有的成员**（`EditorBridge.cpp:515` `MakeUnique`），
不是进程级单例。五个 writer 都是自由函数，拿不到它。

所以切片 3 有两条路：

1. 在**派发层**（`EditorBridge.cpp`）用 manager 校验 token，通过后把派生的 confirmation
   注入 Params 再调用 writer —— 改动局部，但授权逻辑出现在派发层；
2. 把 manager 引用穿到 writer 签名里 —— 更干净，但要改 5 个 writer 的签名与全部调用点。

按设计文档「一个提交一个 writer」的节奏，切片 3 应从 **pin 默认值** 单独开始，
并在真实 Editor 上验证：无 token 时仍需短语、有 token 时短语可省、token 绑定错误时拒绝。
## 切片 3 — 已交付：`91bdc1e`（合并调用，仅单元测试）

新增工具（experimental，不进 `TOOL_REGISTRY`）：

```text
ue_edit_blueprint_pin_defaults(asset_path, graph_guid, defaults, save=False)
  → 内部 preview → apply（save=True 时再 save）
  → 返回 {saved, stages:{preview, apply, save?}}
```

### 设计取舍

| 决定 | 理由 |
|---|---|
| `save` 默认 `False` | Save 永远是调用方的显式请求，绝不隐含；传 `True` 才是"我要存" |
| 每段用上一段的**裸桥载荷**构建 | 派生短语与指纹全部来自服务端自身输出，调用方仍无法拼装桥请求 |
| 失败返回 `ok=false` + `completedStages`/`stages` | 部分执行必须报告走到了哪一步，不能只回一个失败 |
| 每段仍受 60 KiB 边界限制 | 与三个独立工具一致 |

### 尚未验证的部分（必须说明）

**目前只有单元测试，没有真实 Editor 证据。**

运行验证需要三件事，本次未做：

1. 在 Host 工程的 ini 里设 `[UEAgentKit] ExperimentalGraphPatchEdit=True`（当前关闭）；
2. 以 `--enable-experimental-graphpatch-edit` 启动 MCP；
3. 接受 apply 会让包变脏——收尾时必须显式选择不保存，或改用与现值相同的 no-op 默认值。

在拿到这条真实证据之前，不得把该工具写成"已验证可用"。

## 仍待处理（后续"过度防御"清理项，本次只记录了证据）

| 项 | 证据 | 判断 |
|---|---|---|
| apply 要求回传整个预览对象 | `build_apply_parameters` 实际只取 6 个字段；`planId` 已由 C++ 用实时 `beforeValue` 绑定内容 | 可砍，改为直接收 `plan_id + defaults` |
| 校验服务端自己输出的信封 | `experimental/readOnly/wroteBlueprint/savedPackage/compiled` 恒真 | 可砍 |
| 三层重复校验 | Python → C++ `BuildPlan` → C++ 读回 | 部分冗余，需逐项确认 |
| 预览工具包了一层 `{"result":...}`，而 `validate_preview_result` 要裸载荷 | 调用方必须自己解包 | 契约不一致，应统一 |
| 单一 writer 的 `raise` 28 处 / C++ `Fail` 64 处 | `experimental_pin_default_edit.py` / `BlueprintPinDefaultEditExperimental.cpp` | 密度过高，逐条分辨"真不变量 vs 仪式" |
## 切片 3–6 — 已交付（"过度防御"清理）

| 切片 | 提交 | 内容 |
|---|---|---|
| 3 | `91bdc1e` | 一键 `ue_edit_blueprint_pin_defaults`：preview→apply（可选 save） |
| 4 | `9304315` | apply 直接收 `plan_id + asset_path + graph_guid + defaults`，不再回传整个预览 |
| 5 | `e63d5c7` | 信封与裸载荷都接受（pin 默认值） |
| 6 | `e518676` | 共享 `unwrap_tool_result`，五个 writer 统一 |

### 为什么这些都不降低安全性

| 改动 | 依据 |
|---|---|
| 不回传整个预览 | `planId` 是 Editor 用**实时 `beforeValue`** 算的 sha256，既证明"预览过"也证明"图没变"；预览对象里其余字段（beforeValue、两个指纹、packageRevision、五个信封标志）校验完就被丢弃 |
| 一键三步 | 每一步的校验、planId 过期检查、事务边界、读回校验全部保留，只是不再分三次往返 |
| 信封容错 | 换取的是调用方不必知道"该传 `preview["result"]` 还是整个返回体"；无法识别的载荷仍按原样失败 |
| 短语可省略 | 短语由 planId 派生，传值必须等于函数自己算的值，本来就没有额外校验 |

### 剩余清理项（尚未做）

| 项 | 说明 |
|---|---|
| 信封自检恒真断言 | `experimental/readOnly/wroteBlueprint/savedPackage/compiled` 是对服务端自身输出的自检 |
| 三层重复校验 | Python → C++ `BuildPlan` → C++ 读回，需逐项分辨真冗余 |
| 一键工具推广 | 目前只有 pin 默认值有；其余四个 writer 仍是三步 |
| 运行时证据 | 一键工具**仍只有单元测试**，未在真实 Editor 上跑过 |

## 审计基线（2026-11-12）

单看 pin 默认值这一条路径：Python `raise` **28 处**、C++ `Fail()` **64 处**、错误码 10 种。
清理后行为未变，但调用方要穿过的"仪式"减少了：3 次往返 → 1 次，2 次短语回传 → 0 次，
1 次多 KB 对象回传 → 0 次。
### 真实 Editor 验收（2026-11-12 21:17）

环境：Host `N1_BlueprintNative_20261031`，插件 `0.9.0.dev1`，DLL `712BD75E…`，
临时开启 `[UEAgentKit] ExperimentalGraphPatchEdit=True`（验收后已还原并删除备份），
MCP 以 `--database <不存在>` + `--enable-experimental-graphpatch-edit` 启动。

| 检查 | 目标 | 结果 |
|---|---|---|
| experimental 工具注册 | — | 15 个 `*_experimental` + `ue_edit_blueprint_pin_defaults` |
| preview | BP_N1EditSession | `ok=true`，返回 `planId`，`beforeValue="0"` |
| **apply 传整个预览响应**（信封容错） | BP_N1EditSession | `ok=true`，`applied=true` |
| **apply 传 `plan_id + 四个字段`**（字段式） | BP_N1EditSession129 | `ok=true`，`applied=true` |
| **一键工具 `save=False`** | BP_N1EditSession | `ok=true`，`saved=false`，`stages=[preview, apply]` |
| 脏包拒绝 | 同上 | `graph-pin-default-package-dirty`，`stages=[]`、无 `completedStages` |
| 磁盘 | 全部 5 个资产 | **SHA 全部未变**（apply 只改内存） |
| 收尾 | — | 0 个 UE 进程；配置已还原；备份已删除 |

### 过程中确认的两条前置条件（写工具调用方必须知道）

1. **包必须是干净的**：脏包时 preview 直接拒绝 `graph-pin-default-package-dirty`。
2. **蓝图编辑器必须已打开**：否则 `graph-layout-editor-not-open`。光 `openAsset` 不够，
   需要编辑器窗口存在。

### 一条操作经验（记录以免重犯）

脏包关闭 Editor 会弹 UE 原生「保存内容」对话框，该对话框 **UIA 读不到**
（`get_window_state` 超时报 `UnrealWindow`），且截图里可能不在可见画面。
此时**不要**盲按 Enter（默认是"保存"）。安全做法是直接终止进程丢弃内存状态，
并在终止前后各取一次资产 SHA 证明磁盘未变——本次即按此流程，五个资产 SHA 全一致。
## 切片 7 — 已交付并运行时验证：`dc12cc2`

四个 writer 各加一个一键工具，**复用**原有三步工具而不是复制校验：

```text
ue_edit_blueprint_graph(assetPath, graphGuid, nodes, connections, operation, save=False)
ue_remove_blueprint_node(asset_path, graph_guid, node_guid, ..., save=False)
ue_rewire_blueprint_pin(asset_path, graph_guid, operation, ..., save=False)
ue_create_blueprint_variables(asset_path, variables, save=False)
```

### 为什么用"复用"而不是"重写一遍校验"

组合工具依次调用本模块自己的 preview / apply / save 函数，
所以检查逻辑**不可能与已通过测试和原生验收的路径产生漂移**；
新增代码只有编排和错误上报。

### 运行时验证（2026-11-14 11:45）

`ue_create_blueprint_variables` 在 `BP_N1EditSession129` 上真实跑通：

| 检查 | 结果 |
|---|---|
| `ok` | `true` |
| `saved` | `false`（未保存，符合默认） |
| `stages` | `[apply, preview]` |
| `apply.applied` | `true` |
| 磁盘 | 五个资产 SHA **均未变** |
| 收尾 | 0 个 UE 进程；ini 已还原；备份已删除 |

另外三条（graph edit / node remove / rewire）只有单元测试，
未逐条跑真实 Editor——它们共用同一套编排代码，但**这不等于已验证**。

## 切片 8 — 跨 writer 原子事务：pin 默认值已迁移（2026-11-14）

0-3.4 的第一个真正切片。新增一条独立的 patch 契约（不复用、也不改动五个 writer
各自的 Preview/Apply/Save 语义），并把 **pin 默认值** 作为第一个 kind 迁进去。

### 入口

```text
editor.previewBlueprintPatchExperimental
editor.applyBlueprintPatchExperimental
editor.saveBlueprintPatchExperimental
```

MCP（experimental，仍受 `--enable-experimental-graphpatch-edit` 与
`[UEAgentKit] ExperimentalGraphPatchEdit=True` 双门禁）：
`ue_preview_blueprint_patch_experimental` / `ue_apply_blueprint_patch_experimental` /
`ue_save_blueprint_patch_experimental`。

### 契约

| 项 | 内容 |
|---|---|
| 请求 | `assetPath` + `graphGuid` + `operations[]`（1–16 条，`opId` 唯一且为可打印 ASCII） |
| 已迁移 kind | 仅 `setPinDefault`（nodeGuid/pinName/pinGuid/type/value，沿用 pin 默认值的同一套校验与同一套写入函数） |
| 未迁移 kind | `createVariable`/`createNode`/`connect`/`rewirePin`/`removeNode` → `graph-patch-kind-not-migrated`，**在任何写入前拒绝**；未知 kind → `graph-patch-kind-not-allowed`。一个 patch 不允许混用已迁移与未迁移 kind |
| 重复目标 | 同一 nodeGuid+pinName+pinGuid 出现两次 → `graph-patch-duplicate-target`（否则第一条的读回必然失败） |
| planId | `sha256:` over `ueak.blueprint-patch/1.0` + assetPath + graphGuid + packageRevision + graphFingerprint + stateFingerprint + 每条 op 的（opId, kind, 解析后的身份, beforeValue, afterValue），**按请求顺序**（顺序本身有语义） |
| 事务 | 一个 `FScopedTransaction`（`UEAgentKit Apply Blueprint Patch`），`transactionCount=1` |
| 失败 | 逆序回放已应用 op 的 before 值 → 恢复 Blueprint `Status`/`bCachedDependenciesUpToDate` → `SetDirtyFlag(原值)` → `Transaction.Cancel()`，返回 `writeOccurred=false` |
| 逐条结果 | 成功：每条 `status=applied`；失败：已应用的为 `applied`+`rolledBack=true`，失败那条 `failed`+精确 `errorCode`，其后为 `notAttempted`。桥错误信封原来只有 code/message，现在把结果对象放进**附加**的 `error.details`，调用方不会只拿到“第一处错误” |
| 读回 | 每条 op 用所属 writer 的读回函数校验；随后一次 Undo/Redo：Undo 后整图 stateFingerprint 必须等于 preview 的 before，Redo 后必须等于 after（证明“一个 patch = 一个 Undo 单元”） |
| stateFingerprint | 与 pin 默认值 writer 的 `defaultsFingerprint` **同一个函数**，所以单 op patch 可与未迁移路径逐字段比对 |
| Save | 独立动作，需要同一 Editor 会话内该 planId 的 Apply 回执 + 精确指纹与 node/link 计数；Apply 从不保存 |

### 为什么这不是放宽安全边界

- 仍是“先预览再执行”：planId 由 Editor 用**实时** before 值算，Apply 重算后不一致即 `graph-patch-plan-stale`；
- 仍然只接受 exact focused graph、clean package、可编辑 K2 图、PIE 停止；`CanTransact` 且无活动事务；
- 未放大任何上限（16 op / 60 KiB / 单图单包），未引入通用 Graph CRUD，Python 只发送自己校验过的字段；
- 迁移后单 op 的 **plan 期字段**与未迁移路径逐字段一致（见下方证据），Apply 路径共用一个写入函数而不是两份。

### 为什么需要测试专用故障注入（必须说明）

patch 在事务打开**之前**就把每条 op 对着实时图校验完了，因此外部可构造的失败
全部发生在事务之前——“第 2 条失败、第 1 条必须整体回滚”这条要求无法从 MCP 自然
触发。为此新增 `SetNextApplyFaultForTest`：仅 `WITH_DEV_AUTOMATION_TESTS` 编译，
不可从 socket 参数、环境变量或项目配置到达，且只消费一次。回滚证据来自真实 Editor
里运行的自动化测试，而不是 MCP 调用。

### 真实 Editor 证据（2026-11-14）

环境：Host `N1_BlueprintNative_20261031`，插件 `0.9.0.dev1`，UE 5.6.1，
临时开启 `[UEAgentKit] ExperimentalGraphPatchEdit=True`（验收后已还原并删除备份）。

**A. 自动化测试 `UEAgentKit.AtomicPatch.Expansion`（隔离 `UnrealEditor-Cmd`，
`-unattended -NullRHI`，报告 `index.json`：succeeded=1 / failed=0 / errors=0，exit 0）**

| 检查 | 结果 |
|---|---|
| 项目未 opt-in 时 gate-off 拒绝 | `graph-patch-experimental-disabled` |
| 2 条 op 的 preview | 每条一条 `validated`、before 值取自实时 pin |
| 与单 writer preview 逐字段一致 | graphFingerprint / packageRevision / beforeValue 相同，patch `stateFingerprint` == 单 writer `defaultsFingerprint` |
| 未迁移 kind / 重复目标 / 已连接 pin | 各自被拒（`graph-patch-kind-not-migrated` / `graph-patch-duplicate-target` / `graph-pin-default-pin-connected`） |
| 过期 planId / 错短语 | `graph-patch-plan-stale` / `graph-patch-confirmation-required`，且 package 仍干净 |
| **第 2 条注入失败 → 整体回滚** | `writeOccurred=false`、`transactionOpened=true`、`rolledBack=true`；`results` 两条齐全（第 1 条 `applied`+`rolledBack=true`、第 2 条 `failed`+`graph-patch-apply-fault-injected`） |
| 回滚后没有留下东西 | 第 1 条改过的 pin 回到 before 值、package `IsDirty()=false`、整图 stateFingerprint == preview before、**同一 plan 可再次 preview 且 planId 不变** |
| 同一 patch 正常执行 | `applied=true`、`singleTransaction=true`、`transactionCount=1`、`undoVerified=true`、`redoVerified=true`、`saved=false`、node/link 计数不变、两条 `applied` |
| 脏包再次 Apply | `graph-patch-package-dirty` |
| 显式 Save | `saved=true`、`packageDirty=false`、写出 `assetSha256` |
| Save 后再 preview | 两条 beforeValue 变成刚写入的值 |

**B. MCP 侧真实调用（同一 Host，3 条 op：int A=42 / int B=7 / bool A=false）**

| 检查 | 结果 |
|---|---|
| 三个 patch capability | 运行时 `status` 全部出现 |
| preview | `operationCount=3`，before = `-7` / `1` / `true`，planId 为 sha256 |
| 与单 writer preview 一致 | `stateFingerprint` == `defaultsFingerprint`；graphFingerprint / packageRevision / beforeValue 相同 |
| 计划期失败（第 2 条指向已连接 pin） | `graph-pin-default-pin-connected`，`writeOccurred=false`，`error.details.results` 两条齐全（`validated` / `failed`） |
| 过期 planId | `graph-patch-plan-stale`，资产 SHA 未变 |
| apply | `applied=true`、`transactionCount=1`、`undoVerified/redoVerified=true`、`saved=false`；**资产 SHA 未变** |
| 脏包 | 再次 preview → `graph-patch-package-dirty` |
| 显式 save | `saved=true`、`packageDirty=false`，资产 SHA 由 `4718f7dc…` 变为 `53b86b30…`（fileSize 22143→22144） |
| 独立 reload | 关闭并重启 Editor 后 packageRevision == 保存文件的 SHA，preview 的 beforeValue 仍是 `42`/`7`/`false` |

磁盘：验收用的 5 个既有资产 SHA **全部未变**；唯一的写入属于显式 save，且只落在
本次新建的一次性 fixture 包上（`/Game/UEAgentKitPatchExpansion/BP_PatchExpansion`），
收尾时已删除。验收后 0 个 UE 进程，`DefaultEngine.ini` 已还原。

### 仍是单 writer 路径的

| writer | 状态 |
|---|---|
| pin 默认值 | 已迁移（单 writer 与 patch 共用写入函数） |
| 变量创建 | 已在切片 9 迁移（见下节） |
| graph edit（建节点/连线） | 未迁移 |
| rewire | 未迁移 |
| node remove | 未迁移 |

### 已知限制 / 后续

- 一次 patch 只能落在一个图、一个包上；跨资产原子性不在本切片内。
- 迁移下一个 writer 时，patch 的 plan 构建必须**沿用同一模式**：事务外对着实时图
  解析，事务内只用已解析结果写入（各 writer 自己的 `BuildPlan` 都带 clean-package
  前置条件，不能在共享事务里第二次调用）。
- 后续 kind 一旦会改节点/连线，Undo/Redo 与读回必须按 kind 分派；当前读回与
  Undo/Redo 校验只实现了 pin 默认值的分支。

## 切片 9 — 跨 writer 原子事务：变量创建已迁移（2026-11-14）

0-3.4 的第二个切片。把 `createVariable` 作为第二个 kind 迁进**已有的** patch 契约，
与 `setPinDefault` 共用同一个 `FScopedTransaction`、同一套逐条结果与整体回滚；没有新建
第二套 patch 契约，也没有改动五个 writer 各自的 Preview/Apply/Save 语义。

### 先定的契约问题：变量是资产级的，patch 却要求 graphGuid

**决定：请求形状不变**（`assetPath` + `graphGuid` + `operations[]`）。变量 op 的解析与
写入**只对 Blueprint 生效**，`graphGuid` 在这条路径上只是“这个包/这个 Blueprint”的身份与
授权锚点，不是作用域约束：

- `ResolveExactTarget(assetPath, graphGuid)` 本来就同时解析出 Blueprint 与 Graph，并证明
  该图属于该 Blueprint；变量路径只用其中的 Blueprint，不用图。
- 仍然要求 focused editor 显示的正是请求里的 assetPath/graphGuid（同一套 preflight），
  所以“变量 op 与图无关”不等于可以脱离编辑器上下文；PIE/clean-package/可编辑等前置
  条件一条都没有放宽。
- 因此一个纯 `createVariable` patch 仍然只能落在一个包上，`graphGuid` 是**身份**
  （provenance/authorization）而不是作用域。

为什么不改成“资产级 patch（去掉 graphGuid）”：那会引入第二套请求契约与第二套授权
（现有授权检查建立在“用户在看的正是这张图”之上），与本切片“不新建第二套契约”的要求
直接冲突。代价是一个纯变量 patch 也必须先聚焦该 Blueprint 的某个图——这个代价在切片 8
的 preflight 里已经付过了。

### 契约增量

| 项 | 内容 |
|---|---|
| 已迁移 kind | `setPinDefault` + `createVariable` |
| createVariable 字段 | `opId`/`kind` 之外只接受 `name`/`type`/`defaultValue`；出现别的字段 → `graph-patch-unknown-parameter`（沿用 setPinDefault 的做法） |
| 校验 | 与变量 writer **同一套**：标识符规则、bool/int/double/string 白名单、默认值规范化 |
| 数量上限 | 一个 patch 最多 8 条 `createVariable`（变量 writer 的同一上限），超出 → `graph-patch-create-variable-limit`；总 op 上限仍是 16 |
| patch 内重名 | 按 FName 语义（忽略大小写）判重 → `graph-patch-duplicate-target` |
| 与已有变量重名 | 含父类变量 → `blueprint-variable-duplicate-name`（沿用 writer 的码） |
| 解析 | **事务外**只对 Blueprint 解析：名字合法性 + 类型白名单 + 默认值 + 重名，并记录 `existedBefore=false` |
| 写入 | **事务内**只写已解析结果，走 `TryApplyAtomicVariableOperations`；字段与变量 writer 逐字段一致（VarGuid/FriendlyName/PropertyFlags/Category/MultiLine/DefaultValue） |
| 回滚 | 逆序删除已创建的变量 → 恢复 `Status`/`bCachedDependenciesUpToDate`/package Dirty → `Transaction.Cancel()`；随后比较 blueprint 级指纹是否回到 before |
| 指纹 | `stateFingerprint` 仍是**图级** pin 默认值指纹（含义不变，切片 8 的等价关系因此仍然成立）；新增 blueprint 级 `variablesFingerprint`（before/after），与变量 writer 的 `variablesFingerprint` 是**同一个函数** |
| 读回 | 每条 op 用所属 writer 的读回函数；Undo/Redo 后同时校验图级与 blueprint 级指纹 |
| 包前置条件 | 含 `createVariable` 的 patch 额外要求 packageRevision 是 sha256（与未迁移路径一致：资产必须先落盘） |

### 为什么这不是放宽安全边界

- 变量写入走变量 writer 自己抽出的写入函数（`TryApplyAtomicVariableOperations`），单 writer
  与 patch 共用同一份字段构造与同一份读回，不存在两份实现；
- 变量数量上限沿用 writer 的 8，没有因为“一个 patch 能装 16 条 op”而被放大；
- 仍然只接受 exact focused graph、clean package、可编辑 K2 图、PIE 停止、`CanTransact`
  且无活动事务；仍然不保存、不编译；
- 未新增任何通用 Graph CRUD，也没有让 patch 接受 `createVariable` 之外的变量操作
  （改名/删除/改类型仍然没有入口）。

### 仍是单 writer 路径的

| writer | 状态 |
|---|---|
| pin 默认值 | 已迁移（切片 8） |
| 变量创建 | 已迁移（本切片） |
| graph edit（建节点/连线） | 未迁移 |
| rewire | 未迁移 |
| node remove | 未迁移 |

### 已知限制（含一条必须知道的指纹性质）

- 一次 patch 只能落在一个包上；变量 op 仍然要求 focused editor 正看着该 Blueprint 的某张图。
- 一个 patch 最多 8 条 `createVariable`（变量 writer 的同一上限），总 op 上限仍是 16。
- **`variablesFingerprint` 不是“磁盘资产的身份”**：它把 `FBPVariableDescription.DefaultValue`
  也哈希进去，而引擎在存盘往返时会把 double 默认值规范化（请求 `2.5`，重新加载后读回
  `2.500000`）。所以它同一次会话内稳定（patch 与单 writer 逐字段相等，见证据），但
  **跨 save/reload 会变**。这不影响任何契约：planId 与确认短语都在一次会话内，Save 还额外
  绑定 `packageRevision`（= 磁盘 SHA），reload 之后必须重新 preview。该性质来自 writer 既有的
  同一个指纹函数，不是本切片引入的。
- 变量创建仍然不编译：`requiresExplicitCompile` 由变量 writer 自身报告，patch 结果不与
  “已编译”混为一谈（patch 的 `compiled=false`）。

### 真实 Editor 证据（2026-11-14）

环境：一次性 Host `N1_BlueprintNative_20261031`，插件 `0.9.0.dev1`，UE 5.6.1；临时开启
`[UEAgentKit] ExperimentalGraphPatchEdit=True`（验收后已还原成 515 字节原文件并删除备份）。

**A. 自动化测试（隔离 `UnrealEditor-Cmd`，`-unattended -NullRHI`，`index.json`：succeeded=2 /
failed=0，exit 0）**

| 检查 | 结果 |
|---|---|
| `UEAgentKit.AtomicPatch.Expansion`（切片 8 的 pin 路径） | Success（迁移没有回归） |
| `UEAgentKit.AtomicPatch.CreateVariable` | Success |
| 未 opt-in 时 gate-off | `graph-patch-experimental-disabled` |
| preview 与变量 writer 逐字段一致 | `variablesFingerprint` / `graphFingerprint` / `packageRevision` / `variableCount` 全等；op 报 name/type/defaultValue 与 `existedBefore=false`，且不预造 guid |
| 拒绝项（任何写入前） | 外来字段 `graph-patch-unknown-parameter`；patch 内重名 `graph-patch-duplicate-target`；父类已有属性 `blueprint-variable-duplicate-name`；第 9 个变量 `graph-patch-create-variable-limit`；`createNode`/`connect`/`rewirePin`/`removeNode` 全部 `graph-patch-kind-not-migrated` |
| 拒绝后没有留下东西 | 变量数、bool pin、package Dirty、磁盘资产 SHA 全部未变 |
| **注入第 2 条失败 → 整体回滚** | `writeOccurred=false`、`transactionOpened=true`、`rolledBack=true`；`results` 三条齐全（`applied`+`rolledBack=true` / `failed`+`graph-patch-apply-fault-injected` / `notAttempted`） |
| 回滚后没有留下东西 | 第 1 条创建的变量**已删除**、另两条从未创建、变量数回到原值、package `IsDirty()=false`、`variablesFingerprint` 回到 preview 前值、**磁盘资产 SHA 未变**、同一 plan 再次 preview 得到同一 planId |
| 混合 kind | `[setPinDefault, createVariable]` 两条都 `applied`；bool pin 变成 `true`、double 变量默认 `1.5`；node/link 计数不变；`undoVerified=true` / `redoVerified=true`；`saved=false` |
| Save 后的跨模块校验 | 保存后单变量 writer 的 preview 报出的 `variablesFingerprint` == patch 的 `variablesFingerprintAfter`，其 `variableCountBefore` == 2 |
| 显式 Save | `saved=true`、`packageDirty=false`、`assetSha256` == 磁盘 SHA；这是本次唯一一次磁盘写入 |

第一次运行该自动化测试时有 1 条失败，原因是测试自己**在 Save 之后又 preview 了同一个变量名**，
被 `blueprint-variable-duplicate-name` 正确地拒绝——这反而说明该断言有效；改成 preview 一个新
变量名后第二次运行全绿（本表引用的就是第二次）。

**B. MCP 侧真实调用（同一个 Host，真实 GUI Editor + `--enable-experimental-graphpatch-edit` 双门禁）**

| 检查 | 结果 |
|---|---|
| `ue_focus_blueprint_graph` + preflight | `graphGuid=01f99ee3…`、`packageRevision=sha256:81f5763a…`、`packageDirty=false`、`editable=true`、PIE stopped |
| preview（1 条 `createVariable` int 7） | `planId=sha256:62588480…`、`variablesFingerprint=sha256:e58a09c3…`、`variableCount=1`、`migratedKinds=[setPinDefault, createVariable]`，op 为 `validated` |
| 与已有变量重名 | `blueprint-variable-duplicate-name`，`error.details.results` 里一条 `failed` |
| 未迁移 kind | `graph-patch-kind-not-migrated`（客户端拒绝，编辑器没有收到请求） |
| 9 条 `createVariable` | `graph-patch-create-variable-limit` |
| apply | `applied=true`、`transactionCount=1`、`undoVerified/redoVerified=true`、`saved=false`；`variableCount` 1→2；新变量 `guid=8624c137…`；`variablesFingerprintAfter=sha256:1425ea22…`；**磁盘 SHA 未变**（Save 前的 `fileSizeBefore=18974`，preflight revision 仍是 `81f5763a…`） |
| apply 之后再 preview | `graph-patch-package-dirty` |
| 显式 save | `saved=true`、`packageDirty=false`、`assetSha256=sha256:d8cbeaf9…` == 磁盘 SHA，`fileSize` 18974→20007，`variablesFingerprint` 仍是 `1425ea22…` |
| 关闭 Editor（终止进程，不触碰任何保存弹窗） | 前后磁盘 SHA 都是 `d8cbeaf9…`；0 个 UE 进程 |
| 独立 reload（重启 Editor 后的新会话） | `packageRevision=d8cbeaf9…` == 磁盘 SHA；`variableCount=2`；`LiveCounter` 读回 `guid=8624c137…`、`type=int`、`default=7`、`flags=0x10005`；图级 `stateFingerprint` 仍是 `ccd556b4…`（变量不影响图级指纹） |
| double 探针（印证上面那条指纹性质） | 请求 `2.5` → 存盘往返后 context 读回 `2.500000`，`variablesFingerprint` 由 `02c4904e…` 变成 `d0735ab5…` |

**C. 磁盘与收尾**

- 两个一次性 fixture 包（`BP_PatchExpansion` / `BP_PatchVariables`）在验收后删除；除显式 Save 外
  没有资产 SHA 变化（每次 kill 前后都取了 SHA）。
- 收尾：0 个 UE 进程；`DefaultEngine.ini` 已还原（515 字节，无 `[UEAgentKit]` 段），备份已删除。

### 只被单测覆盖的

- Python 契约层：`createVariable` 的字段校验、与变量 writer 共享的规范化、patch 内重名、8 条上限、
  preview/apply 结果里 variable 条目与 `variablesFingerprint` 的校验。
- `graph-patch-create-variable-limit` 与“patch 内变量重名”的 native 分支由上面的自动化测试覆盖；
  MCP 侧只经真实调用观察，没有单独单测。
- `mcp_server._error_response` 的 `PatchError` 分支有单测，同时也在上面的真实调用里出现过
  （`graph-patch-kind-not-migrated`、`graph-patch-create-variable-limit`）。
