# UEAgentKit Agent Reliability R0 Slice 3 Handoff

> 日期：2026-08-16
> 分支：`feature/agent-reliability`
> 当前基线：`d88c61a feat: add deterministic relevantAssets discovery after real Reforge smoke (R0-S/R0.2)`
> 状态：R0.3（只读 Cross-source Correlation）已完成；R0 里程碑标记完成，R1 等待明确指令。
> 推送约束：只允许本地 Commit，不 Push。

---

## 1. 本片目标

R0.2 之后 `ue_get_task_context` 已经能返回确定性相关资产候选，但 Active Work、显式 Change Set、Live Editor Session 与 Memory Evidence 四个来源仍只是并排摆放，没有显式交叉关联。

本片只做一件事：

```text
R0.3  Cross-source Correlation（只读、非持久化、零模型推断）
```

把四个来源用**精确键**联接起来，让 Agent 一次请求就能看到「这个 Change Set 属于哪个 Editor Session、它的资产在 Editor 里是否 dirty、有没有 Memory 证据、哪个 Active Work 在管这些资产」等确定性事实。

## 2. 硬约束（本片全部遵守）

```text
只读：不写 Memory、不写 Change Set journal、不产生任何持久化状态
零模型推断：只有 session id 相等性、资产路径集合交集、changeSetId 字面量匹配
不新增 Memory / ChangeSet Schema（只调用公开 getter 与 scoped search_records）
不扫描 workflow 私有 _change_sets（只调用 PatchWorkflowService.get_change_set(change_set_id)）
不自动发现 Change Set（仅显式 change_set_id 且 found 时参与）
不进入 R1 Reference / Impact Analysis（无引用遍历、无“一定受影响”判断）
```

## 3. 最终设计（schemaVersion 1.1 → 1.2）

`correlation` section：

```text
correlation
├─ available            true/false；无任何可关联来源对时 false + reason=insufficient-correlatable-sources
├─ method               deterministic-key-matching
├─ summary              linkCount / countsByKind / workItemsConsidered / workItemsTotal /
│                       changeSetAffectedAssetsSampled / changeSetAffectedAssetsTotal /
│                       changeSetAffectedAssetsTruncated / evidenceLookups /
│                       evidenceLookupsTruncated / linksTruncated
└─ links[]              固定排序、上限 16 条
```

7 种 link kind（固定排序）：

```text
change-set-editor-session        cs.editorSessionId ↔ live.sessionId（matches + 不等时产生风险）
change-set-asset-in-editor       cs.affectedAssets ∩ Editor dirty/open（observedVia）
change-set-asset-memory-evidence cs.affectedAssets → 资产 scope Evidence（recordId/status/recordType/title）
work-change-set-asset-overlap    work.assetPaths ∩ cs.affectedAssets（仅显式 Change Set）
work-references-change-set       changeSetId 字面量出现在 work 文本字段（matchedIn）
work-asset-in-editor             work.assetPaths ∩ Editor dirty/open
work-asset-memory-evidence       work.assetPaths → 资产 scope Evidence
```

边界：工作项最多考虑 5（requested 优先，去重）、每工作项资产 4、Change Set affectedAssets 采样 8、每资产证据 3、每请求证据检索 12 次；超界在 summary 如实计数。

新确定性风险：

```text
change-set-editor-session-mismatch (medium)
    source=cross-source-correlation；Change Set 绑定的 editorSessionId 与当前 Live Editor sessionId 不一致
```

预算阶梯插入（位于 relevant-assets-count 之后、target metadata 之前）：

```text
correlation-links   逐条移除
correlation-summary 折叠为 {available, method, omittedDueToBudget}
```

关联永不优先于 target identity / high risk / revision summary。

## 4. 来源降级与不伪造原则

- `include_memory=false` / `include_live_context=false` / Change Set 未找到 → 对应联接整体缺席，不伪造。
- Evidence 关联复用 scoped `search_records`（资产名 token FTS + asset scope、全部状态）；未命中只代表「本次确定性检索未命中」，不代表「不存在记录」（unicode61 边界沿用）。
- correlation 任何子查询异常都只跳过该联接，绝不失败整个请求。

## 5. 测试与门禁

测试（`tests/python/test_task_context.py` 新增 R3.1–R3.17 + MCP 契约断言）：

```text
R3.1  会话匹配链接（matches=true，无 mismatch 风险）
R3.2  会话失配 → change-set-editor-session-mismatch medium 风险 + matches=false
R3.3  cs affectedAssets ∩ dirty/open → change-set-asset-in-editor
R3.4  cs affectedAssets ↔ 资产 scope Evidence
R3.5  work.assetPaths ∩ affectedAssets → work-change-set-asset-overlap
R3.6  work 文本字段含 changeSetId 字面量 → work-references-change-set（matchedIn）
R3.7  work.assetPaths ∩ Editor dirty → work-asset-in-editor
R3.8  work.assetPaths ↔ 资产 scope Evidence
R3.9  无 change_set_id 不产生任何 change-set 链接（不自动发现）
R3.10 降级侧不伪造：cs 未找到 / include_memory=false / include_live_context=false
R3.11 相同输入 correlation 完全确定（dict 相等）
R3.12 边界诚实：links≤16、work 5/10、affected 8/12、evidenceLookups=12、linksTruncated
R3.13 非持久化回归：调用前后 Memory status 完全一致（record/activeWork/countsByStatus）
R3.14 低预算先裁 correlation（truncationReason 含 correlation-），target identity/riskSummary 保留
R3.15 requested 工作项与 activeWork.items 同 id 去重（workItemsTotal/Considered 均为 1）
R3.16 工作项 assetPaths 超过 4 个时只关联前 4 个（有界且确定）
R3.17 Change Set 无 editorSessionId 时不产生 session 链接、不产生 mismatch 风险
```

门禁：

```text
Ruff 通过（src + tests）
Python 全量 557/557（原 540，+17）
git diff --check 通过
无 C++ 变更、无 Live Editor 行为变更 → 不需要 UE Build / 真实 Smoke
文档同步（spec/MCP_SERVER.md、分析计划、审计 Schema 文档、ROADMAP、PROJECT_STATUS、本 Handoff）
```

## 6. 文件范围

```text
src/ue_agent_kit/task_context.py       correlation 实现 + 预算阶梯 + 风险 + schemaVersion 1.2
src/ue_agent_kit/mcp_server.py         capabilities.taskContext.crossSourceCorrelation /
                                       project status 契约 / server instructions
tests/python/test_task_context.py      R3.1–R3.17 + MCP 契约断言
docs/Plans/AGENT_RELIABILITY_R0_AUDIT_AND_SCHEMA_20260815.md   响应 Schema/风险/阶梯/边界
docs/Plans/AGENT_RELIABILITY_CONTEXT_ANALYSIS_PLAN_20260815.md 状态与 R0.3 概览
docs/ROADMAP.md / docs/PROJECT_STATUS.md                        R0 完成标记
spec/MCP_SERVER.md                     correlation 契约与阶梯
docs/Handoffs/AGENT_RELIABILITY_R0_SLICE3_HANDOFF_20260816.md  本交接
```

## 7. 已知限制（如实记录，不修）

```text
1. Evidence 关联基于资产名 token FTS：记录正文不含资产名 token 时不命中（unicode61 边界）；
   不命中只代表「本次检索未命中」。
2. correlation 是纯只读联接：工作项完成 → 证据回流的写路径仍由既有 Memory 写 Tool 显式完成。
3. Change Set 仅显式传入时参与；不提供“当前有哪些 Change Set”的发现能力（这是有意的）。
4. 链接/采样上限（16/8/5/12）内才保证穷尽；超界部分在 summary 如实计数。
```

## 8. 完成汇报格式（已按此汇报）

```text
1. Commit / Branch
2. R0.3 correlation 最终联接规则
3. 硬约束落实情况（不新增 Schema / 不扫 _change_sets / 不自动发现 / 无 R1）
4. 新增/修改 Tool/API（仅 ue_get_task_context 输出扩展 + capability 契约）
5. 测试与门禁
6. 已知限制
7. R0 完成标记与 R1 建议
```

## 9. 当前决策原则

> R0.3 的价值不是“自动猜关联”，而是把四个已经存在的只读事实源用精确键钉在一起：Agent 看到的每一条关联都能被独立复核，且不会改变任何持久化状态。R1 的 Impact Analysis 应继续只回答“修改会影响什么”，并且不要把深度引用遍历塞进默认 Task Context。
