# Agent Reliability R1 Impact Analysis 设计与复用审计

> 日期：2026-08-18
> 分支：`feature/agent-reliability`
> 状态：R1 已完成并本地提交（未 Push）
> 关联：执行规范 [`../../Handoffs/Archive/AGENT_RELIABILITY_R1_FULL_HANDOFF_20260818.md`](../../Handoffs/Archive/AGENT_RELIABILITY_R1_FULL_HANDOFF_20260818.md)

本文档记录 R1（Impact Analysis）的 R1.0 复用审计结论、最终设计决策、硬边界、支持矩阵、性能观察与建议移交 `feature/performance-benchmarks` 的正式性能指标。R1 的核心原则：

> **存在引用 ≠ 已证明会发生语义影响。** 任何输出都不得把静态引用关系表述成“这个消费者一定会坏 / 一定受影响”。

---

## 1. R1.0 复用审计结论（3 个 Flash 只读审计并行）

### 1.1 Reference Graph 查询层（Flash A）

- `references_table` 行归 **consumer（源资产）** 所有（`asset_id → assets`），`target_asset_path` 恒定是被引用目标；`incoming` 方向即 `r.target_asset_path = ?`，索引 `references_target_asset_idx` 直接覆盖。
- 现有 `find_references` 是**单一 asset_path 锚点**、`depth>1` 时禁止端点过滤、`limit/offset` 是“收集后统一分页”（无遍历上限保护）——三者都无法直接表达 R1 的“多目标多跳 + 按 source 聚合 + 独立遍历上限”。
- 结论：**不改造 `find_references`**；在 `queries` 层新建读取连接对象的纯函数语义（R1 实现在 `impact_analysis.py`），复用相同的方向语义、`references_target_asset_idx` 与稳定排序，并在 `IndexQueryService` 上新增薄包装（复用 `_open` / `_base_response` / snapshot 校验），不引入第二套引用数据库。

### 1.2 Domain Evidence 审计（Flash B，含真实 Reforge 索引 SQL 取证）

真实索引（48 资产 / 4430 引用）取证结论：

| 结论 | 证据 |
|---|---|
| 14 种 reference kind 全部 100% 填充 `target_symbol_id` 与 `target_name` | GROUP BY 取证 |
| `target_asset_path` 对 symbol 级 kind（reads/writes/casts/calls-到BP 等）可靠指向 `/Game` 路径；`calls` 有 67.7% 指向原生 `/Script`（空 target_asset_path），0% 非空非 `/Game` | 采样 |
| `inherits` 到项目父类时 `target_asset_path` = 父 BP 路径（BP_VehicleBase 有 6 条继承入边，全部是 6 个 Frame 子类）；原生父类为空 | 采样 |
| `depends-hard-package`：424 行 target_kind=asset（有 /Game 路径）+ 77 行 target_kind=package（空 target_asset_path，指向 /Script//Engine） | 采样 |
| DataTable Row / Material Instance Parent-Parameter / Data Asset Object-Soft Reference / runtime-vs-editor 消费分类：**索引中无任何可机械证明的证据** | 全表检索 |
| symbols 表 `stable_id` UNIQUE；但 `(asset + name + kind)` 不唯一（局部变量重名），因此 symbol 级 subject 只能用**精确 stable_id** | 采样 |

### 1.3 测试/协议审计（Flash C）

产出完整同步清单（Tool Registry 顺序与 FastMCP 注册顺序一致、strict-args 机制、`test_tool_registry.py` / `test_mcp_server.py` / `test_agent_workflow.py` 中全部硬编码工具计数与位置切片、`nextExpansions` 既有断言、测试基建 helper），已全部落实并在全量测试中验证。

---

## 2. 公共 Tool：`ue_analyze_change_impact`

- 只读 query 组，Offline / Live / Workflow 全模式可用；核心事实全部来自 immutable Index。
- 严格参数（`extra=forbid`，`STRICT_IMPACT_ARGUMENT_TOOL_NAMES`）。

### 2.1 请求

```text
target_asset_paths  必填，1..8 个精确 /Game Object Path，不可重复
subject_kind        固定枚举 8 种，默认 asset-level
subject             结构化 subject 精确 stable_id；asset-level 必须为空
max_depth           1..3，默认 2
max_consumers       1..100，默认 100
max_edges           1..1000，默认 500
max_paths           1..100，默认 50
max_output_tokens   256..32768，默认 4096
```

结构化 subject 支持矩阵（只支持能被现有证据**机械证明**的 kind，其余显式拒绝，绝不猜测）：

| subject_kind | 状态 | 依据 |
|---|---|---|
| `asset-level` | 支持 | `target_asset_path` 精确匹配 |
| `blueprint-symbol` | 支持 | `subject` = 精确 symbol `stable_id`（symbols 表 UNIQUE）；要求恰好 1 个目标且符号属于该资产；引用行按 `target_symbol_id` 精确匹配，间接层仍走 asset-level 展开 |
| `data-table-row` / `searchable-name` / `data-asset-object` / `material-instance-parent` / `material-instance-parameter` / `blueprint-member` | 显式 `unsupported-impact-subject` | 现有索引无行级/参数级/运行时证据（§1.2） |

### 2.2 响应

```text
request / direction(=consumer-to-target) / method(=reverse-reference-bfs-exact-key)
summary / targets[] / directConsumers[] / indirectConsumers[]
runtimeSensitiveConsumers / analysisGaps[] / validationTargets[]
risks[] / riskSummary / nextActions[] / outputBudget
```

- `targets[]`：`assetPath / found / reason / identity{asset_name, asset_class, parent_class, generated_class} / directConsumerCount / indirectConsumerCount / subject(仅 symbol)`；未索引目标 `found=false, reason=target-not-indexed`。
- consumer 记录：`assetPath / assetClass / depth(shortestDepth) / impactedTargets[] / referenceKinds[{rawReferenceKind, normalizedReferenceKind, source, edgeCount}] / whyIncluded / evidence[{stableId, kind, targetKind, targetName, targetPath, nodeClass, nodeTitle, graphName}] / paths[{targetAssetPath, depth, hops[]}]`。同 consumer 多边合并；`hops` 为中间 consumer 链（不含两端），depth = len(hops)+1。
- `summary`：`targetCount / foundTargetCount / notIndexedTargetCount / visitedAssetCount / visitedEdgeCount / directConsumerCount / indirectConsumerCount / maxDepthRequested / maxDepthReached / consumerLimit / edgeLimit / pathLimit / truncated / truncationReasons[] / frontierOmittedCount / omittedEdgeCount / omittedPathCount / pathCount / unknownReferenceKindCount / runtimeSensitiveConsumerCount / subjectKind`。

---

## 3. 图遍历算法与硬边界

- **BFS（incoming）**：第 1 层查 `target_asset_path IN (found_targets)`（symbol subject 时查 `target_symbol_id = subject`）；第 d 层查 `target_asset_path IN (frontier)`，frontier = 上一层**新发现的非目标 consumer**。SQL 按 500 一批分块，命中 `references_target_asset_idx`，无 N×全表扫描。
- **防环 / shortestDepth**：全局 `visited` 保证每个资产只入队一次；BFS 层序保证首次发现即最短；路径沿 `node_paths` 逆向传播（parent 为请求目标时 hops=[]，否则 hops=parent.hops+[parent]），层内多 sweep 收敛（仅目标间互引会链式延长，sweep 上限 = MAX_TARGETS+1）。
- **去重**：同 consumer 对同 target 的多条边合并进一条记录；多目标共享 consumer 单条记录 + `impactedTargets[]`。
- **自引用**：`consumer == target` 的行不产生 consumer（防止目标以自身消费者出现，也防止 layer-1 自环）。
- **硬上限**：`MAX_IMPACT_TARGETS=8`、`MAX_IMPACT_DEPTH=3`、`MAX_IMPACT_CONSUMERS=100`、`MAX_IMPACT_EDGES=1000`（默认 500）、`MAX_IMPACT_PATHS=100`（默认 50）。任何超限：`truncated=true` + 对应 `truncationReasons`（`consumer-limit / edge-limit / path-limit`）+ 诚实计数（`frontierOmittedCount / omittedEdgeCount / omittedPathCount`），不静默消失。
- **确定性**：所有层内行按 `(asset_path, kind, target_name, stable_id)` 排序；输出列表按 `(depth, casefold(assetPath))` 或 `casefold(assetPath)` 固定排序；相同输入逐字节相同（真实索引上已验证）。

---

## 4. Reference Kind 确定性归一化

基于 §1.2 取证，映射只依赖 exporter 写入的 kind 事实：

| rawReferenceKind | normalizedReferenceKind | 依据 |
|---|---|---|
| `inherits` | `parent-reference` | 目标即父类资产 |
| `depends-hard-package` | `asset-reference` | 硬包依赖 |
| `casts` / `implements` | `class-reference` | target_kind=class/interface |
| `calls` / `macro-calls` / `interface-calls` / `reads` / `writes` / `returns` / `delegate-binds` / `delegate-broadcasts` / `delegate-creates` / `delegate-unbinds` | `blueprint-symbol-reference` | 符号级引用 |
| 其余任意 kind | `unknown-reference`（raw 原样保留） | 不猜测 |

`soft-reference` 与 `searchable-name-reference` 类别当前无证据填充，保留为稳定类别枚举。未知 kind 同时进入 `analysisGaps.unknown-reference-kind` 与 info 级风险 `unknown-reference-kind`。

---

## 5. Unknown / Unsupported / Risks 语义

- **没有找到 Consumer**：`analysisGaps.kind=no-consumer-evidence-in-index`（仅当该目标确无任何 incoming 行时出现；S1/S2/S3 均不出现，S4 出现）。
- **当前证据无法证明**：`unknown-reference-kind`（kind 未覆盖）、`runtime-sensitivity-not-proven`（Index 无 runtime 分类，固定返回）、`frontier-truncated`（遍历超限）。
- **不索引目标**：`targets[].found=false` + `impact-target-not-indexed`（medium）。
- **结构化 subject 不可证**：`unsupported-impact-subject` / `impact-subject-not-found` / `impact-subject-asset-mismatch`（stable 错误码，非 retryable）。
- **确定性风险**（只描述分析/修改范围风险，禁止 likely-to-break / confidence / modelScore）：

```text
high-fanout-target           medium   某目标直连消费者 ≥ 15（阈值固定）
impact-analysis-truncated    medium   consumer/edge/path 任一超限
impact-target-not-indexed    medium   请求目标不在索引
unknown-reference-kind       info     存在未归一化的 raw kind
```

`impact-target-stale` 需要写模式 freshness 三源比较才能证明，read-only 核心不产出（属于后续里程碑）。

---

## 6. Validation Targets

完全确定性的建议验证范围（不是“已验证通过”声明；Verification Plan 属 R3）：

```text
Tier 0  修改目标自身       reason=modified-target-self      depth=0
Tier 1  Direct Consumers  reason=direct-consumer           depth=1
Tier 2  Indirect Consumers reason=indirect-consumer-depth-N  depth=shortestDepth
```

排序 = tier → depth → `casefold(assetPath)`，`priorityOrder` 从 0 连续编号；每条含 `assetPath / tier / priorityOrder / depth / reason / impactedTargets / referenceKinds / source`。consumer 的 tier 由它对**任一**目标的最近关系决定（多目标混合深度时以最短为准，per-target 计数仍按 per-target 路径深度如实统计）。

---

## 7. Token Budget 与裁剪阶梯

复用 `query_protocol.estimate_json_tokens`；裁剪阶梯固定（低优先级先裁，每步后重估）：

```text
1. impact-paths            完整 Impact Path 明细
2. consumer-evidence       所有 consumer 的 evidence[] 行
3. indirect-consumers      indirectConsumers 列表（summary 计数保留）
4. consumer-reference-kinds direct consumers 的 referenceKinds
5. validation-targets      validationTargets 列表
6. target-details          targets[].identity 细节
7. analysis-gaps           analysisGaps 列表
```

始终保留：target 身份（assetPath/found）、summary 计数、risks/riskSummary、nextActions、runtimeSensitiveConsumers 状态与 `outputBudget{maxTokens, estimatedTokens, truncated, truncationReasons[]}`。图截断原因在 `summary.truncationReasons`，Token 裁剪原因在 `outputBudget.truncationReasons`，两者语义独立。

---

## 8. R0 Task Context 集成

- 有显式 `asset_paths`：`nextExpansions` 追加 `ue_analyze_change_impact`，reason=`impact-analysis-explicit-targets`，arguments 带前 2 个目标 + `max_depth=2`。
- 仅 `relevantAssets`：追加 reason=`impact-analysis-relevant-asset-hint` 的有界 hint（首个候选 + `max_depth=2`）。
- 默认 `ue_get_task_context` **不**自动做 depth≥2 引用遍历；R0 schema（1.2）不变。

---

## 9. 公共契约变化

- `TOOL_REGISTRY`：query 组 +1（`ue_analyze_change_impact`，位于 `ue_find_references` 之后、`ue_get_task_context` 之前，与 FastMCP 注册顺序一致）。全模式工具计数 7/19/40/52/57/69/90/102。
- `capabilities.impactAnalysis`：`available/readOnly/deterministic/modelInference=false/direction/method/source/maxTargets=8/maxDepth=3/defaultDepth=2/supportsIndirect/supportsValidationTargets/supportsRuntimeSensitivityClassification=false/runtimeSensitivityState/maxConsumers=100/maxEdges=1000/maxPaths=100/subjectKinds/unsupportedSubjectKinds/highFanoutThreshold=15`；`limits` 增加 `impactTargets/impactDepth/impactConsumers/impactEdges/impactPaths`；`ue_get_project_status` 增加 `impactAnalysis` 块。
- server instructions 增加一句说明。
- 新错误码：`unsupported-impact-subject` / `impact-subject-not-found` / `impact-subject-asset-mismatch`。

---

## 10. 性能观察与建议正式指标（移交 feature/performance-benchmarks）

> 注：`feature/performance-benchmarks` 是独立分支，本轮**未切换分支**、未修改其文档；以下指标为 R1 在真实 Reforge 索引上的实测与建议基线，供该分支合并时直接采用。

| Case | 目标 | 耗时 | visited edges |
|---|---|---|---|
| S1 fan-out depth1 | BP_VehicleBase | 14.4 ms | 282 |
| S2 indirect depth2 | BP_SphereTraceWheel_V2 | 27.2 ms | 836 |
| S3 multi-target depth2 | 2 目标 | 32.5 ms | 1636 |
| S4 无消费者 | BP_GM_main | 5.9 ms | 0 |

- 实现上无 N×全表扫描：每层按 frontier 分块（500/批）走 `references_target_asset_idx`；同节点不重复入队。
- 建议正式指标（脱离本机硬阈值，作为 benchmark 分支的量化目标）：
  - `impact_depth1_p95 < 50 ms`、`impact_depth2_p95 < 150 ms`（48 资产级；大工程另行取样）；
  - 每层查询次数 ≤ ceil(visited_assets / 500)，无单节点重复查询；
  - 输出 `estimatedTokens ≤ max_output_tokens` 或显式 `truncated=true`（最低保障 body 除外）。

---

## 11. 测试与 Smoke

- 单元/契约：`tests/python/test_impact_analysis.py` 33 用例（T1–T20 + 域测试：symbol direct/indirect/not-found/mismatch、自引用、目标间互引、kind 归一化表、edge/path-limit 截断、多目标混合深度计数、strict args、capability 契约、模式一致性、R0 集成）；`test_task_context.py` 新增 r4_1/r4_2。
- 全量门禁：Ruff 通过；Python **592/592**（原 557，+35）；`git diff --check` 通过；无 C++ 变更（无需 UE Build）。
- 真实 Reforge Smoke：`scripts/reforge_impact_smoke.py`（read-only 模式 + 现有 immutable 索引，不启动 UE Editor、不修改任何 Reforge 资产），物证在 `Output/ReforgeContextSmoke/R1-impact/`（S1–S4 impact JSON、evidence、`smoke-results.json`、`smoke-summary.json`）。S2 证明真实 2 跳路径存在（`Wheel ← VehicleBase ← BP_CargoBase`），未出现需要扩大导出范围的情况；索引文件 SHA-256 在 Smoke 前后一致。

## 12. 已知限制与明确延后

- 静态引用不证明运行时语义影响；`runtimeSensitiveConsumers` 固定 `not-proven-with-current-evidence`，执行链/值来源归 **R5**。
- `validationTargets` 只是建议验证范围，不构成已验证声明（**R3**）。
- DataTable Row / Material Instance Parameter / Data Asset 对象级 / Blueprint Member 等结构化 subject 无索引证据，显式 unsupported；如需支持必须先在 Index/Exporter 增加可机械证明的事实（默认不改 Index）。
- 输出超过所有裁剪档后允许最低保障 body 超过预算（与 task_context 的 minimal-envelope 行为一致），并显式 `truncated=true`。
- `impact-target-stale` 风险需要写模式 freshness 三源证据，read-only 核心不产出。
- `feature/performance-benchmarks` 文档同步未执行（不同分支，避免中途切换分支），指标见 §10。
- **R2（Semantic Diff）未开始。**
