# GraphPatch batching and session-level authorization — design (2026-11-05)

Status: **not implemented; design only.** This is stage 0 item 0-3.4. No writer
was converted in this round; the existing five experimental writers keep their
current per-operation Preview → Apply → Save contracts.

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