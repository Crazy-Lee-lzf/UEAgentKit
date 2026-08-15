# UEAgentKit R0.0 现状审计、复用矩阵与最小 Schema

> 日期：2026-08-15
> 分支：`feature/agent-reliability`（从本地 `main@cc1f0c9` 创建）
> 阶段：R0 Task Context / Context Pack MVP 的第一条纵向切片（R0.0 + R0.1）
> 状态：R0.0 审计完成；R0.1 `ue_get_task_context` 已实现并通过门禁（见对应 Commit）

---

## 1. 审计范围与方法

审计由主 Agent（DeepSeek Pro）在读取计划与现有代码后完成，并交叉验证了两个 Flash 只读审计（Index/Search 线、Memory/Live/Workflow 线）的结论。审计范围：

```text
Index / Search / Symbol / Reference API
Revision / Freshness（三源 SHA-256 比较）
ue_get_asset_state（四源资产状态，Workflow 模式）
Token / Result Budget 机制
ue_memory_get_context / Active Work / Evidence
ue_get_editor_context / Dirty / Open Assets
Change Set / Editor Session
Tool Registry / Capability 契约 / 错误 Envelope
```

只读检查；未修改任何文件，未设计第二套 Schema。

## 2. 复用矩阵

| 能力 | 模块 / 类 / 函数 | MCP Tool（如已注册） | R0 复用方式 | 判定 |
|---|---|---|---|---|
| 项目/索引元数据与统计 | `agent_api.IndexQueryService.check()` | `ue_get_project_status` 内部 | `project` section（projectKey、snapshotId、lastIndexedAtUtc、schema、stats） | 直接复用 |
| 单资产身份/摘要/元数据 | `agent_api.IndexQueryService.get_asset(sections=("identity","summary","metadata"))` | `ue_get_asset` | `targetAssets[]` 的事实来源（identity/summary/metadata，跳过符号/引用分页段） | 直接复用 |
| Asset/Symbol/Reference 检索 | `agent_api.IndexQueryService.search/find_references` | `ue_search` / `ue_find_references` | 第一版不自动展开；作为 `nextExpansions` 引导后续展开（自动多跳属 R0.2） | 按需引导 |
| 三源 Revision / Freshness | `freshness.IndexFreshnessTracker.inspect_asset()` | `ue_get_project_status` 内部 | `revisionState` section；`fresh`/`stale`/`unavailable` + reason + comparisons | 直接复用（仅 Workflow 配置下可用，否则降级） |
| 四源资产状态 | `agent_workflow.PatchWorkflowService.get_asset_state()` | `ue_get_asset_state` | R0 第一片不重复：revisionState + liveEditor 已覆盖所需事实；该 Tool 保持独立作为深查入口 | 不重复实现 |
| Memory 有界上下文 | `memory_service.ProjectMemoryService.get_context()` → `memory_context.build_memory_context()` | `ue_memory_get_context` | `memory` section；自带 ContextBudget 截断、truncated/usage/nextActions | 直接复用 |
| Active Work | `memory_service.ProjectMemoryService.get_work()`；context.activeWork | `ue_memory_update_work`(get) | `activeWork` section + `workItemId` 精确查询 | 直接复用 |
| Memory Evidence | `memory_service.ProjectMemoryService.get_evidence()` | `ue_memory_get_evidence` | 不默认注入；`nextExpansions` 引导按需展开 | 按需引导 |
| Live Editor 聚合上下文 | `editor_bridge.LiveEditorBridgeService.call_tool("ue_get_editor_context")` | `ue_get_editor_context` | `liveEditor` section；editorSessionId + dirty/open 交集用于风险 | 直接复用 |
| Change Set 状态机 | `agent_workflow.PatchWorkflowService.get_change_set()` | `ue_get_change_set` | `changeSet` section；terminal/unknown 状态映射为确定性风险 | 直接复用 |
| Token Budget | `query_protocol.estimate_json_tokens / normalize_output_token_budget / DEFAULT_OUTPUT_TOKEN_BUDGET` | 全部查询 Tool | `max_output_tokens` 参数 + `outputBudget` 报告 + 固定优先级裁剪阶梯 | 直接复用 |
| 错误 Envelope | `mcp_server._error_response()` | 全部 Tool | 参数错误 → `invalid-arguments`；可选来源失败只在 section 内降级 | 直接复用 |
| Tool Registry / 模式门控 | `tool_registry.ToolDefinition`（group="query"） | `ue_get_capabilities` | `ue_get_task_context` 注册为 query 组 → 所有模式可用 + 降级 | 直接复用 |

## 3. 缺口（R0.1 新增的最小实现）

现有代码没有、且 R0.1 必须新增的只有一件事：

```text
跨源编排层（task_context.py TaskContextService）：
  - 按任务把 Index / Revision / Memory / Live / Change Set 聚合进统一 envelope；
  - 每源独立 try/except 降级（Optional 源失败不拖垮整个请求）；
  - 确定性风险派生（只用已观察到的事实，零模型推断）；
  - 全局 max_output_tokens 裁剪阶梯与 outputBudget 报告；
  - MCP Tool 注册（ue_get_task_context，query 组，严格参数）。
```

其余全部复用现有模块。没有新增 Memory Schema、没有第二套搜索引擎、没有新 C++。

## 4. 禁止重复实现项

```text
queries.py / indexer.py 的检索与索引
freshness.py 的 Revision 比较
memory_context.py 的渐进式披露与字符预算
memory_service.py 的存储与校验
editor_bridge.py 的 Bridge 协议
change_sets.py / agent_workflow.py 的 Change Set journal
query_protocol.py 的 Token 估算与 continuation
tool_registry.py 的模式门控
```

## 5. 最小 Request Schema（R0.1 定稿）

```text
ue_get_task_context(
    query                 str     必填，≤2048 字符
    asset_paths           list    可选，≤10 个精确 /Game Object Path，禁止重复
    work_item_id          str     可选，≤128 字符
    change_set_id         str     可选，≤64 字符（格式错误在 section 内降级，不整体失败）
    include_live_context  bool    默认 true
    include_memory        bool    默认 true
    max_output_tokens     int     默认 4096，范围 256–32768（约 4 字符/token）
)
```

## 6. Response Schema（R0.1 定稿）

```text
TaskContext
├─ request           query/assetPaths/workItemId/changeSetId/include*/maxOutputTokens 回显
├─ project           projectKey、projectName、index(snapshotId/lastIndexedAtUtc/schema/immutable/quiescent)、stats、sources
├─ targetAssets[]    每资产：assetPath、found、whyIncluded=explicit-asset-path、source=immutable-sqlite-index、
│                    identity、summary、metadata（未命中 → reason=asset-not-indexed）
├─ relevantAssets    R0.2 起为确定性候选集：query 分词（≤8 term）复用 Asset Search + 少量 Symbol Search，
│                    与显式目标互斥、固定排序（matchCount 降序 → 首个命中 term 位置 → assetPath）、Top N≤8；
│                    每条含 assetPath/assetClass/source/whyIncluded/matchKind，可附 matchedTerms/matchCount/
│                    matchedSymbol；无 score/confidence；搜索子源异常降级 degradedSources，不伪造结果
├─ memory            available/included/reason/source=project-memory、
│                    summary(projectProfile/nodes/records/truncated/usage/nextActions)、staleRecordCount
├─ activeWork        available/included/items[]、truncated、requestedWorkItem（workItemId 精确查询，含 found/reason）
├─ liveEditor        available/included/reason/source=live-editor-memory、editorSessionId、summary（Bridge 聚合上下文）
├─ revisionState     available/source=sqlite-revision-export-disk-sha256、overall、
│                    assets{path: state/reason/indexRevision/revisionExportRevision/diskRevision/comparisons/comparedAtUtc}
├─ changeSet         requested/available/found/changeSetId/summary（journal 状态机原样）
├─ risks[]           仅确定性事实，每项 kind/severity/source/assetPath?/details
├─ riskSummary       count/highCount/mediumCount/infoCount
├─ nextExpansions[]  tool/reason/arguments，引导 ue_get_asset / ue_find_references / ue_memory_get_evidence /
│                    ue_get_editor_context / ue_get_change_set 等渐进展开
├─ degradedSources[] 被降级的 section 与原因
└─ outputBudget      maxTokens/estimatedTokens/truncated/truncationReason
```

风险 kind 全集（R0.2）：

```text
target-dirty-in-editor (high)        Editor Dirty Package 命中目标
target-open-in-editor (info)         Editor 打开资产命中目标
asset-stale (high)                   三源 Revision 不一致
asset-revision-dirty (high)          Revision 源带 dirty 标记
asset-revision-unavailable (medium)  三源比较无法完成
target-not-indexed (high)            SQLite 索引无此资产
memory-stale-records (medium)        资产 scope 下存在 stale Memory 记录
memory-conflicted-records (medium)   上下文命中 conflicted 记录
change-set-not-found (medium)        changeSetId 不存在（section 降级 + 风险）
change-set-terminal (medium)         Change Set 处于 undone/discarded/verified/failed
change-set-unknown (info)            Change Set 状态为 unknown
work-item-not-found (medium)         workItemId 不存在
live-editor-unavailable (info)       Bridge 不可达（其余 section 正常返回）
memory-context-failed (info)         Memory 上下文构建失败（其余 section 正常返回）
revision-state-unavailable (info)    未配置 Revision Export（Offline/Live 模式常态）
relevant-assets-search-failed (info) 候选搜索子源全部失败（relevantAssets=[]，无伪造候选）
```

## 7. 预算裁剪阶梯（固定优先级，晚者先裁）

```text
1. changeSet.summary.operations → 清空 + ue_get_change_set 展开提示
2. liveEditor.summary → {omittedDueToBudget: true} + ue_get_editor_context 展开提示
3. memory.summary.records → 逐条移除
4. memory.summary.nodes → 逐条移除
5. activeWork.items → 逐条移除
6. relevantAssets[].matchedTerms/matchCount/matchedSymbol → 移除（先裁候选 metadata）
7. relevantAssets → 逐条移除（再减候选数量）
8. targetAssets[].metadata → 移除
9. targetAssets[].summary → 移除
10. revisionState.assets[].comparisons → 移除
11. project.stats → 移除
12. nextExpansions → 逐条移除
13. risks[].details → 清空
14. revisionState.assets[] 修订字段 → 只保留 state/reason
15. targetAssets[].identity → 只保留 asset_path/asset_class
16. degradedSources → 清空
17. request 冗余字段 → 移除
18. project.sources / project.index.snapshotId / memory 摘要冗余字段 → 移除
```

裁剪到最小封套仍超预算时，诚实报告 `truncationReason=minimal-envelope-exceeds-token-budget` 并保留核心身份事实，不伪造“已满足预算”。

## 8. 已知边界（R0.2 记录，不本轮修复）

1. Memory FTS 为 `unicode61`，纯中文短语查询命中不可靠（实测英文/ASCII token 正常）。stale 检测因此在无 ASCII token 时可能漏报；检测不到只代表“本次检索未命中”，不代表“不存在 stale 记录”。修复方向属于 Memory 层，按禁止范围不在 R0 展开。
2. `revisionState` 依赖 Workflow 模式的 Revision Export 配置；Offline/Live 模式降级为 unavailable 并如实报告。
3. `workItemId` 与 Change Set 的深度绑定（自动完成→证据回流）属 R0.3，不在 R0.2。
4. R0.2 `relevantAssets` 只做分词检索召回，不做引用图遍历（多跳属 R1）；候选来自当前索引子树；符号补充在真实 Smoke 上未带来新资产（只提供符号级证据）；不产出 score/confidence。
