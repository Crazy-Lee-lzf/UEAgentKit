# G0.3 已批准契约候选（Delta 规范）

- 状态：**DESIGN APPROVED 13/13；IMPLEMENTED — CANDIDATE TESTED；NOT FROZEN；NOT UE-VERIFIED**
- 依据：`goals/G0_2_USER_APPROVAL_D01_D13_20261022.md`（SHA-256 `c18b70836011ed374d9948967d7aadd1e081687794240f11c0b2115a455e97ff`）
- 基线：`E:\WorkSpace\UEAgentKit` `main` HEAD `23bdbd6`，本轮未改 main、未启动 UE、未写 Asset。
- 本文件只记录相对 G0.1 的**已批准变更**。未列出的条目继续沿用 `G0_1_CONTRACT_CANDIDATE.md`；G0.1 原文作为历史证据保留，不覆盖其“当时未批准”的状态。
- 实现与证据：`G0_3_APPROVED_CONTRACT_IMPLEMENTATION_20261022.md`；`contract_candidate/`；`G0_3_checks/verification_result.json`。
- 决策状态：**APPROVED ≠ FROZEN ≠ IMPLEMENTED IN UE**。正式契约冻结与 Editor 写图仍需独立门槛。

## 1. 版本与兼容

- TypedGraph / GraphPatch / Descriptor / LayoutPlan 的 `schemaVersion` 仍为 `1.0`：G0.1 从未冻结，本轮是**冻结前**修订。
- 新增 `schemas/change-set-idempotency.schema.json`，表示候选的 ChangeSet **2.1 可选 `idempotency` 块**；主仓库现有 `change_sets.py` 未修改。
- 旧的 `deleteNode` 不再属于 V1；没有可执行兼容路径。
- G0.1 旧 fixture/证据哈希记录在 `G0_3_checks/g0_1_baseline_hashes.json`，用于差异审计。

## 2. D-06：GraphPatch 显式 profile

`target.graphpatchProfile`（可选，缺省 `structure-default`；建议写入 ChangeSet/receipt）：

| profile | total | createNode | connectPins | setNodePosition | mixed create/connect |
|---|---:|---:|---:|---:|---|
| `structure-default` | 200 | 100 | 150 | 50 | 允许 |
| `structure-g2` | 300 | 100 | 200 | 50 | 允许；**G2 验收显式选择** |
| `layout-only` | 1000 | 0 | 0 | 1000 | **禁止** |

- 默认 200 不变；100+150=250 默认 `graphpatch-limit-exceeded`，`structure-g2` 可规划。
- 1000 position 只允许 `layout-only`；1001 拒绝；layout-only 混入 create/connect 拒绝。
- receipt 必含 `graphpatchProfile` 与 `operationCounts{create,connect,position,other,total}`；Policy/ChangeSet 必须记录同一 profile。
- 源码：`graph_patch.py` `GRAPH_PATCH_PROFILES` / `validate_profile_limits`；测试：`test_g03_decisions.D06ProfileTests`。

## 3. D-07：ChangeSet 2.1 可选 idempotency 块

候选块字段（`schemas/change-set-idempotency.schema.json`）：

```text
schemaVersion = ueak.change-set-idempotency/1.0
changeSetSchemaVersion = 2.1
scopeKey, projectName, assetPath, graphId, idempotencyKey, patchId,
payloadHash, state ∈ {reserved, applied, failed, indeterminate},
reservedAtUtc, updatedAtUtc, transactionId?, actualGraphHash?,
applyReceipt?, failureCode?, result?（离线 replay 缓存，非 ChangeSet 必需）
```

- 状态机：`reserved → applied | failed | indeterminate`。
- 作用域：`(projectName, assetPath, graphId, idempotencyKey)`；同一 scope 使用 CreateNew 锁文件，跨进程锁冲突 → `graphpatch-idempotency-locked`。
- Apply 前 `reserved`；成功 `applied`；可回滚失败 `failed`；无法证明恢复/崩溃遗留 `reserved` → 后续 fail-closed `graphpatch-idempotency-indeterminate`。
- 同键同 payload 且 `applied` → replay；同键不同 payload → `graphpatch-idempotency-conflict`。
- **只承诺 at-most-once / safely-retry，不承诺 exactly-once。** 主仓库 ChangeSet/Memory Schema 未修改；候选 `ueak_contract/idempotency.py` 是隔离原型。

## 4. D-08：V1 不含 deleteNode

- `OPERATION_TYPES`、`graphpatch.schema.json` 的 9→8 分支均无 `deleteNode`。
- 规划遇到 `type=deleteNode` 明确 `graphpatch-operation-not-allowed`；不存在无连接/有连接的执行捷径。
- 旧的“delete 导致 manual-recovery”证据保留在 `G0_2_checks/verification_result.json`，G0.3 报告只引用，不覆盖。
- 删除能力另立后续里程碑；不得因旧测试通过而恢复。

## 5. D-09：comment 独立验证、enabled 显式确认

- `setNodeComment` R1；`setNodeEnabled` R2，Operation 必须带 `confirmed=true`，否则 `graphpatch-confirmation-required`。
- OperationDescriptor 新增可选 `requiresExplicitConfirmation`；`od_set_node_enabled.json` 为 R2 + true。
- `verify_result` 增加独立 `commentHash` 比较（node comment + comments[].text）；`semanticHash` 仍按 G0.1 白名单不含 comment。篡改 comment → `graphpatch-verify-mismatch`。
- `enabled` 已进入 semanticHash 的 `flags.enabled`；启用状态变化会改变语义哈希并参与编译验证。

## 6. D-11：结构化 compileResult

```json
{"result":"success|success-with-warnings|error",
 "succeeded":true,
 "diagnostics":[{"severity":"warning|error","message":"...","code":"..."}]}
```

- 只有 `succeeded=false` 或 `result=error` 触发 `graphpatch-compile-failed` 与回滚。
- `success-with-warnings` 保持 `status=applied`、`compileStatus=success-with-warnings`，warning 写入结果/receipt；不自动 Save。
- 缺失/畸形 compileResult → `graphpatch-compile-result-missing`，fail-closed 回滚。
- 与 UE `EditorBridgeValidationHandlers.cpp:376-380` 的 `succeeded = (Status != BS_Error)` 对齐；Save 仍是独立显式授权动作。

## 7. D-12：布局验收证据

`LayoutPlan` 新增（Schema 必填）：

```text
evidenceLevel = "same-process" | "independent-reload"
acceptanceEligible = false   # 离线候选恒为 false
requiredAcceptanceEvidence = "independent-reload"
```

- 离线原型只产生 `same-process`、`acceptanceEligible=false`；**不得**据此声称通过 G3/C。
- G3/C 的所有写盘布局（full/selection/incremental）最终验收均要求独立 UE 进程 Reload + 语义比较；同进程读回只作 dev smoke。

## 8. D-01/02/03/04/05/10/13

- **D-01**：EventGraph 导出 `event`；`ubergraph` 只读兼容；Apply 对 `target.graphKind=ubergraph` 返回 `graphpatch-graph-kind-not-allowed`。
- **D-02**：保留 `executor-computed`，但必须在锁内、首个 `Modify()` 前计算并确认 revision；sidecar 拒绝；`actualRevision` 入 receipt。
- **D-03**：`g:name:` 只读诊断/布局；Apply 必须 native `g:<guid>`（已有负例）。
- **D-04**：A 只读 discovery/descriptor/preview；INT 后续提供受控 `graph.spawnProbe`；本轮无 UE。
- **D-05**：runtime 继续只用 stdlib 子集；`jsonschema` 仅 dev 对拍；候选无第三方 import。
- **D-10**：Marker 清除三重条件（用户确认 + 证据 SHA + semanticHash 恢复）；`acceptDiverged=false`。
- **D-13**：LayoutPlan 只含 V1 字段，`additionalProperties=false`；`suggestedReroutes` 等 V3 字段拒绝。

## 9. 未变化的安全门禁

Preview 零写入、Apply 不自动 Save、单次 Compile、独立 Verify、Recovery Marker 持久阻断、Policy/Revision/ChangeSet 复用、Schema 子集/版本拒绝、128 条码表约束均继续有效。G0.1 的 53 条 CR 映射仍适用，除本文件明确变更的 D-06/07/08/09/11/12 条款。

## 10. 非声明

- 没有 UE5.6 Editor 证据；没有 C++ GraphPatch Executor；没有真实 ChangeSet/Memory 修改；没有 exactly-once；没有正式契约冻结。
- D-01～D-13 是设计批准，不等于实现完成，也不授权后续 Editor 写图。
