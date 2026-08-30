# UEAgentKit Post-0.8 Development Plan

> 日期：2026-08-23
>
> 当前 capability baseline：`feature/agent-reliability@2aadb66`
>
> 最新正式发布：`0.7.0`
>
> 0.8 capability closeout：完成；正式 0.8 package release：未执行
>
> R5：继续 `deferred by benchmark evidence`

## 1. 当前事实基线

0.8 capability closeout 已完成，并形成以下稳定事实：

```text
R0 Task Context                         complete
R1 Impact Analysis                      complete
R2 Semantic Diff                        complete
R3 Verification Plan / Trust Verdict    complete
R4 / R4.1 Real Agent Benchmark          complete
C0-C6 capability closeout               complete
Public Tools                            105
Patch Operations                         18
Must-fix new tools before 0.8             0
Published version                       0.7.0
R5                                      deferred
```

最终门禁：696 项 portable tests、739 项完整 Python suite、61 个 PowerShell 脚本、compileall、R4.1 raw summary、105/105 Tool 与 18/18 Operation 分类、编码/换行和 Git 检查均通过。

R4.1 仍保留两个产品层 limitation：

- High-fanout Full 3/3 能找到正确 direct consumers，但越过用户要求的 direct-only bound，形成 False Success；
- Data Asset scalar 的世界状态与 Trust Evidence 3/3 正确，但 2/3 final claim 将 numeric `beforeValue` stringify。

这两项属于 Agent bound / typed result contract 问题，不是缺少 UE Read/Write capability。

## 2. 后续总体原则

后续不再以“增加 Tool 数量”作为主目标，优先解决大型真实 UE 工程中的使用体验：

```text
能读
→ 能安全写
→ Editor 常驻下低延迟连续写
→ 用 checkpoint 做强独立验证
→ 大项目可接受的索引/查询/验证性能
→ 再考虑更宽的 Writer 与协作能力
```

固定原则：

1. Reliability closeout baseline 不因后续功能开发回退；
2. Editor-resident fast path 不能替代最终 independent verification；
3. Writer 扩展必须继续具备 Policy / Revision / Plan / Snapshot / Transaction / Save / Verify / Semantic Diff / Trust / Recovery；
4. 大型工程交互延迟优先于新增低频 convenience Tool；
5. R5 只有真实 benchmark / Reforge blocker 反复出现时才解冻；
6. 不直接在旧 `feature/live-editor-realtime-io` 上继续新 Writer 开发；新 Writer 应从当前 0.8 capability baseline 起新分支；
7. 不 Rebase 已共享/并行开发分支；需要同步时使用明确 checkpoint merge。

## 3. Track A — 正式 0.8 Package Release（独立授权轨道）

### 目标

把已经完成的 capability scope 转成正式发布版本，但不阻塞后续技术开发。

### 当前状态

```text
Capability Release Review     pass
feature RC baseline           2aadb66
published version             0.7.0
merged to main                no
0.8 tag/release artifact      no
push                          no
```

### 只有用户明确授权后执行

1. 审核 `feature/agent-reliability` 相对 `main` 的最终 merge diff；
2. 合入 `main`；
3. 决定正式版本号（预期 0.8.x）；
4. 同步 `pyproject.toml`、`UEAgentKit.uplugin`、Release Notes / Changelog；
5. 若涉及 C++ 或发布构建要求，执行 UE5.6 Direct Build；
6. 执行 portable release validation、完整 Python/Schema/PowerShell gates；
7. 生成发布 artifact 与 hash；
8. Tag / GitHub Release / Push 仍需单独授权。

正式发布与下面的 Writer / Performance 开发互不依赖。

## 4. Track B — Editor-Resident Writer / Low-Latency Write Path（最高技术优先级）

W0/W1 可直接执行的详细计划见 [UEAGENTKIT_EDITOR_RESIDENT_WRITER_W0_W1_DETAILED_PLAN_20260823.md](UEAGENTKIT_EDITOR_RESIDENT_WRITER_W0_W1_DETAILED_PLAN_20260823.md)。

### 4.1 为什么优先

当前产品已经能安全完成多类写入，但大型 UE 项目中仍存在明显的 cold-start 成本：

```text
Live-capable non-Blueprint:
resident Editor mutation
→ resident Editor save
→ commandlet independent export / verify

Blueprint narrow writes:
Plan / Dry Run / Commit
→ RunPatch.ps1
→ UnrealEditor-Cmd.exe
→ save / verify
```

这意味着“安全闭环已经存在”，但连续修改体验仍不像改代码。目标不是取消独立验证，而是把它从“每一步都冷启动”调整为“任务 checkpoint 强验证”。

### 4.2 分支策略

推荐新建：

```text
feature/live-writer-expansion
```

起点：当前 capability baseline `2aadb66` 或其后明确的 release checkpoint。

不要继续在旧 `feature/live-editor-realtime-io@56afc91` 上直接开发；该分支的主要成果已经进入后续主线，而它本身缺少当前 R0-R4 / C0-C2 Trust contract。

### 4.3 W0 — Baseline / Contract Freeze

- 继承 0.8 Change Set、Semantic Diff、Verification Plan、Trust Verdict 与 Result Contract；
- 明确 Fast Verify 与 Strong Independent Verify 的职责；
- 不修改 R4.1 raw measurement；
- 建立 Writer 延迟基线：Plan、Live Apply、Compile、Save、Independent Verify 各阶段单独计时。

完成标准：能证明后续优化没有绕过 Trust/Recovery 边界。

### 4.4 W1 — Blueprint Narrow Live Apply

第一批只迁移已有、已验证的窄 Blueprint Operation：

```text
setVariableDefault
setComponentProperty
setPinDefault
```

目标路径：

```text
Policy / Revision / Plan
→ 当前 UnrealEditor.exe Editor Bridge
→ FScopedTransaction
→ in-memory mutation
→ Dirty
→ Compile / read-back
→ Undo / Discard 或 Authorized Save
```

要求：

- 不新增 Generic Blueprint Graph CRUD；
- 继续使用稳定 variable/component/node/pin identity；
- compile failure 可恢复；
- transaction 与 Change Set 精确绑定；
- 保存前不自动持久化 Package；
- stale / dirty / wrong session / PIE/SIE 继续 fail closed。

### 4.5 W2 — Fast Resident Verification

增加“迭代阶段快速证明”，但不将其宣传成独立 Trust：

```text
Live Apply
→ exact live read-back
→ Blueprint Compile / Data Validation（适用时）
→ memory dirty/session/revision applicability
→ Fast Verify result
```

Fast Verify 只能证明当前 Editor session 的即时状态，不能关闭需要 independent canonical/reload 的 Required Assertion。

### 4.6 W3 — Checkpoint Strong Verify

把昂贵验证从“每个小修改”提升到任务 checkpoint：

```text
Editor 常驻
edit 1
edit 2
edit 3
compile/read-back
edit 4
...
authorized save
→ one checkpoint independent verify
→ semantic diff
→ verification plan
→ trust verdict
```

目标：同一任务内多个受控修改只在需要形成可信交付点时启动独立 UE commandlet/reload。

需要重新设计的重点不是 Trust 标准，而是 Save/Verify workflow 的触发策略；不能让同一个 Editor UObject 自证为 independent verification。

### 4.7 W4 — Multi-operation / Batch UX

在 W1-W3 稳定后，再做：

- 同一 Blueprint 多个兼容窄 Operation 的连续 transaction；
- Change Set 内多资产 checkpoint；
- 失败时明确 partial-applied / rollback boundary；
- bounded batch，不开放 arbitrary generic batch mutation。

### 4.8 W5 — Real-project acceptance

至少用小型 DirectHost + 一个大型真实工程做：

- Editor 已启动后的首个 read/write latency；
- 连续 5–20 次小修改；
- Blueprint default/component/pin；
- Save checkpoint；
- Strong Independent Verify；
- exact recovery；
- Agent 端 Tool call / token / elapsed 变化。

成功标准应以“减少 UE cold-start 次数和总等待时间”为主，而不是新增多少 Tool。

## 5. Track C — Large-project Performance / True Incremental（与 Writer 可并行）

已有 `feature/performance-benchmarks@a7c5ae9` 保留了有价值的实验，但不要整分支盲合。

已证明的问题包括：

- 默认 deep readers 会触发昂贵资产加载、Nanite/DDC 工作；
- Registry-only 明显更适合作为大索引入口；
- 当前 unchanged incremental 仍先打开/解析 canonical 并计算 SHA，再决定 skip；
- 2-hop Reference traversal 在较大数据下已经接近/超过日常交互预算；
- 500k Asset / 10m Reference 目标仍未正式验证。

推荐按小切片吸收：

### P1 — Registry-only default/index path

先让不需要深语义的全项目 discovery/index 构建尽量不加载资产。

### P2 — Fast Revision

避免为了判断“是否变化”先做完整 canonical/deep export；优先利用 package metadata / manifest / stable revision fingerprint。

### P3 — True incremental manifest

目标顺序：

```text
cheap package fingerprint
→ unchanged skip
→ only changed package deep export
→ changed canonical ingest
```

而不是：

```text
open canonical
→ parse
→ SHA
→ query existing
→ discover unchanged
```

### P4 — Index ingestion optimization

减少大 canonical JSON 的重复打开、解析与 hashing；测量 SQLite transaction、FTS、Reference bulk insert 和 memory peak。

### P5 — Scale validation

分层运行：

1. Reforge / DirectHost functional benchmark；
2. DarkRuins physical benchmark；
3. synthetic large-index；
4. 500k Asset / 10m Reference target。

主要门禁：warm p50/p95、结果截断、memory peak、index build/incremental time、commandlet startup 占比。

## 6. Track D — Agent UX / Reliability Tail（小步并行，不进入 R5）

R4.1 暴露的两个稳定 False Success 应继续作为小型 product backlog：

### A1 — Requested-bound binding

目标：减少 Agent 明明拿到 `max_depth=1` 的正确事实，却在 final claim 使用 depth 2 数据。

优先考虑：

- task template / SDK 层显式保存 requested bound；
- final result builder 从同一 response binding 生成 claim；
- 不让 Server 根据自然语言自行猜 depth。

### A2 — Operation-discriminated typed result

目标：让 `beforeValue/afterValue` 在不同 Operation 下保持原始 JSON 类型。

优先考虑：

- discriminated result schema；
- typed result builder / SDK；
- 不用把通用 value 字段粗暴限定为 number。

### A3 — Tool-profile ergonomics

105 个公共 Tool 是完整 Combined+Memory 面，不应让普通 Agent 默认感知全部能力。

后续研究：

- task-oriented Tool Profile；
- domain-specific exposure；
- 高层 Tool 优先、低层 compatibility Tool 按需展开；
- benchmark 比较 Tool surface 缩减后的选择错误与 token 成本。

这些工作属于 Agent UX / contract hardening，不解冻 R5。

## 7. Track E — Maintainability / CI

在 Writer/Performance 第一阶段稳定后推进：

### M1 — Split `agent_workflow.py`

当前 workflow service 已承担过多域职责。按稳定边界逐步拆：

- Change Set / journal；
- live write lifecycle；
- persistence / verify；
- rollback / recovery；
- semantic/trust integration；
- animation-specific workflow。

只做行为保持型拆分，不与新 Writer 大功能混成同一个 commit。

### M2 — Capability count single source

README / Status / Registry 计数全部从 Registry contract 或生成脚本导出，避免手工数字再次漂移。

### M3 — UE plugin build CI

当前 GitHub CI 主要验证 Python/portable contract；UE5.6 C++ plugin build 仍依赖本地 release machine。后续建立至少一个可重复的 UE build gate。

### M4 — Benchmark stop-loss / cost budget

为昂贵 real-agent benchmark 固化：

- measurement drift → stop；
- fairness mismatch → stop；
- exact recovery failure → stop；
- 连续 infrastructure timeout/failure 达阈值 → stop and diagnose；
- 明确 token / elapsed budget。

## 8. Track F — 0.9 Collaboration（后置）

在本地单机 Writer / Performance 稳定后再进入：

1. Source Control Provider / Checkout / Lock / Owner / Head Revision **只读**；
2. Local Dirty / disk Revision / depot head conflict analysis；
3. 多人风险提示与阻断；
4. 再评估共享 Knowledge Service；
5. 首版不自动抢锁、不自动覆盖他人修改。

## 9. R5 继续冻结

不因为 0.8 closeout 完成就自动进入 Value Provenance / Execution Trace。

解冻仍要求：

```text
多个真实任务
+ 重复 primary blocker
+ 当前静态 Context/Impact/Semantic/Trust 无法回答
+ 证明收益高于 Writer/Index/Guidance 修复
```

否则保持：

```text
R5 = deferred by benchmark evidence
```

## 10. 推荐执行顺序

不考虑正式发布授权时，技术开发顺序建议：

```text
1. W0 Writer baseline / latency instrumentation
2. W1 Blueprint narrow Editor-resident Live Apply
3. P1 Registry-only + P2 Fast Revision（可并行）
4. W2 Fast Resident Verify
5. W3 Checkpoint Strong Verify
6. P3 True incremental + P4 index ingestion
7. A1 requested-bound + A2 typed result
8. W4 multi-operation / bounded batch
9. W5 large-project Writer acceptance + P5 scale benchmark
10. M1/M2 maintainability cleanup
11. M3 UE build CI
12. 0.9 source-control read awareness
```

正式 0.8 package release 可以在任意稳定 checkpoint 由用户单独授权启动，不要求等待上述技术计划完成。

## 11. 下一步直接入口

如果立即开始开发，推荐下一任务为：

```text
Create feature/live-writer-expansion from the current 0.8 capability baseline.
Implement W0 only:
- re-audit current Blueprint narrow write path;
- measure commandlet cold-start cost by stage;
- define Fast Resident Verify vs Strong Independent Verify contract;
- produce a minimal W1 implementation plan for setVariableDefault / setComponentProperty / setPinDefault;
- do not implement Generic Blueprint Graph CRUD;
- do not alter R4.1 measurement artifacts or R5 scope.
```

W0 完成后再进入 C++/Bridge 具体写入实现，避免再次把架构、功能和 benchmark 改动压进同一个大提交。