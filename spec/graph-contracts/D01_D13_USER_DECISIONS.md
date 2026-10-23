# UEAgentKit G0.2 — 用户正式决策记录（D-01～D-13）

- 决策日期：2026-10-22
- 决策来源：当前 ChatGPT 对话，用户明确回复 **「按推荐方案全部批准」**；紧接在助理呈交 G0.2 十三项推荐、指出 D-07 采用 ChangeSet 2.1、D-08 移除 deleteNode、D-11 只因 Error 回滚且 Save 单独授权的完整批准文本之后。
- 决策状态：**APPROVED (13/13)**；仅批准技术设计选项，**不等于已实现、不等于契约冻结、不等于允许 UE Asset 写入**。
- 唯一审议依据：`E:\WorkSpace\UEAgentKit-Development\G0_2_DECISION_RECOMMENDATIONS_20261022.md`（SHA-256 `04a06690df9a705bb5ec8ab3d2b53407d8473c8b6892d4d2cba266c83740071c`）、`G0_2_CONTRACT_CROSSCHECK_20261022.md`。
- 本文件是用户裁决的持久化记录。G0.1/G0.2 原始候选报告作为历史证据不得被覆盖为“原先就已经批准”。

## 批准的决策

| ID | 用户批准的实施准则 |
|---|---|
| D-01 | EventGraph = `event`；`ubergraph` 只读兼容映射并诊断；V1 Apply 不接受 `ubergraph`。 |
| D-02 | `executor-computed` revision 可用，但必须在受控锁内、首个 `Modify()` 前计算并确认 SHA-256，拒绝 sidecar 不一致，receipt 记录 `actualRevision`；不能跳过现有 preflight。 |
| D-03 | `g:name:` 仅用于只读诊断/布局，禁止 Apply；结构写入必须稳定 GraphGuid。 |
| D-04 | A Node Registry 首轮仅 discovery/descriptor/preview；INT 后续提供受控 `graph.spawnProbe`；与 B 使用同一冻结样本集验证，当前阶段不写 UE。 |
| D-05 | Runtime 保留 stdlib JSON Schema 子集；`jsonschema` 可仅用于开发测试对拍，不新增 Runtime 第三方依赖。 |
| D-06 | 显式 `graphpatchProfile`：`structure-default` 总量 200（create<=100, connect<=150, position<=50）；`structure-g2` 总量 300（create<=100, connect<=200, position<=50，G2 验收显式选择）；`layout-only` 总量 1000（仅 position，禁止混合）；全部受总/分项限制并写 Policy、ChangeSet、receipt；保留原默认 200。 |
| D-07 | **采用 ChangeSet 2.1 可选 `idempotency` 块**，不以 sidecar 作为首选。定义 `reserved → applied / failed / indeterminate`、`payloadHash`/作用域锁/transactionId/receipt；跨进程防重；崩溃后不确定态 fail-closed、人工复核；只承诺 at-most-once 与 safely-retry，不承诺 exactly-once。仅在候选协议/离线原型中修订，不准未经审批改 main 当前 ChangeSet 或 Memory schema。 |
| D-08 | **V1 不含 `deleteNode`**，规划、Schema、测试应拒绝；删除能力另立后续里程碑，不能因通过候选旧测试而偷偷保留。 |
| D-09 | V1 允许 `setNodeComment` 风险 R1、`setNodeEnabled` 风险 R2 且需显式确认；Verify 独立比较 comment，enabled 进入相关语义/编译验证。 |
| D-10 | Recovery Marker 只能在用户显式确认、恢复证据 SHA 相符、当前 semanticHash 恢复到 baseGraphHash **三项全满足**时清除；`acceptDiverged=false`，不得自动清除。 |
| D-11 | 编译结果使用 `compileResult{result,succeeded,diagnostics[]}`；只有 Error/`succeeded=false` 触发回滚；Warning 记录并可继续进入后续流程，**Save 仍必须是单独的显式授权动作**，不因编译 Warning 自动 Save。缺少可信编译结果不得认为编译通过。 |
| D-12 | **G3/C 的所有写盘布局验收（full/selection/incremental）均须独立 UE 进程 Reload + 语义比较**；同进程读回仅供 dev smoke，不能作为最终验收。当前只定义契约与离线证据格式，不启动 UE。 |
| D-13 | V1 LayoutPlan 只包含移动坐标等 V1 字段；`additionalProperties=false`；`suggestedReroutes` 等 V3 字段推迟到新版本。 |

## 当前授权执行范围

1. 原 G0.1 DSH Session `session-772dd913-e8ee-4e29-b21c-aa7ac99d15e1` **复用**，不修改当前 Flash Fast 模型。
2. 仅在 `E:\WorkSpace\UEAgentKit-Development` 更新契约候选、Schema、生成器、Fixtures、Python 离线原型、测试、评审及 Goal/Handoff；通过真实测试证明以上 13 条候选实现一致。
3. 正式契约冻结及是否进入仓库需另设验收门槛；按 `APPROVED — IMPLEMENTATION PENDING` 与 `IMPLEMENTED — TESTED` 分开记录，严禁把设计批准等同于实现完成。
4. **未授权**修改 `E:\WorkSpace\UEAgentKit` main、创建功能分支/Worktree、编译/启动 UE Editor、修改任何 UE Asset、合并/Push GitHub、操作 `E:\WorkSpace\Reforge`、原始 Lyra、DSH/Bridge 配置、Memory Schema P2/P3。
5. 15 分钟 Windows 本地巡检已取消；禁止自动恢复、创建定时任务或派发超出本轮的 Agent 任务。
6. 沿用已有受控 Memory 服务，不直接编辑 SQLite，不把普通 Tool 观测误标为 user-confirmed；本用户决策原文以本文件为证据，写入 Memory 时必须有现有授权写入路径及独立验证。

## 离线验收后续

DSH 完成契约候选修订与离线回归，输出逐项状态、与 G0.1 变更差异、测试结果、证据哈希、遗留问题。测试失败不得宣称“契约已冻结”。离线通过后向 ChatGPT/用户汇报下一阶段的功能 Worktree 与 UE Dev 环境授权请求。