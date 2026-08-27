# UEAgentKit 项目整体开发计划

> 日期：2026-08-27
>
> 文档性质：项目级主计划（Master Plan），统辖多条并行分支线
>
> 当前活跃 worktree：`E:\WorkSpace\UEAgentKit-LiveWriter`（分支 `feature/live-writer-expansion`）
>
> 主仓 worktree：`E:\WorkSpace\UEAgentKit`（分支 `feature/agent-reliability`）
>
> 最新正式发布：`0.7.0`（UE 5.6）
>
> 优先级决策（用户确认）：**W4 写入能力优先，Memory 增强排其后**，随后 P4 协作与知识库 Web 只读浏览
>
> 知识库写入约束（用户确认）：**知识库不允许人工直接修改，只能由 Agent 写入**

## 0. 本计划要解决的问题

0.8 capability scope 已收口，`105 Tool / 18 Operation / 0 Must-fix`。W1–W3 已在真实
UE5.6 上完成常驻 Writer 与 Checkpoint Strong Verify。项目已经"能读、能安全写、
能低延迟连续写、能强验证"。

但三件事仍然缺失，并且它们决定了工具能否从"单点可用"变成"日常可依赖"：

```text
1. 多操作 / 多资产任务仍需 Agent 手工逐个 Plan/Apply/Save/Verify   → W4
2. 项目记忆只在 Agent 主动调用时写入，不会在使用中自动积累        → Memory
3. 多人协作没有 P4 感知，知识库没有任何可视化入口                → P4 / Web
```

本计划把这三件事拆成四条可并行推进的 Track，并明确各自的分支、门禁与验收边界。

## 1. 事实基线（已在磁盘核实，非推测）

### 1.1 Writer 线状态

```text
W0 baseline / latency instrumentation      complete   142ca1e
W1 Blueprint narrow resident Live Apply    complete   8bede6f
W2 Fast Resident Verify                    complete   31f0faa
W3 Checkpoint Strong Verify                complete   C0-C6 全 PASS
W4 Multi-operation / Bounded Batch         计划已写，未实现
```

W1 曾被真实 UE5.6 的 Blueprint Undo crash 阻塞，该问题已修复；W3 已完成并于 2026-08-27 形成独立 Git checkpoint：

```text
3280102 fix: close W3 live-write continuation and snapshot refresh
ab731f1 test: cover W3 continuation and full snapshot refresh
45e6ea2 docs: close W3 checkpoint strong verify
```

W3 产品代码、测试与结果文档已经从 working tree 清出，T0 收口条件满足。当前未提交的 README / Master / Midterm / W4 等文档属于后续规划整理，不属于 W3 实现边界。

### 1.2 Memory 线状态（关键缺口所在）

已实现（`memory_schema.py` v3，12 张表）：

```text
memory_records / memory_scopes / memory_revisions / memory_artifacts
memory_relations / memory_status_events / memory_records_fts (FTS5)
knowledge_nodes / active_work_items / active_work_node_links
active_work_asset_links / active_work_todos
```

已有 14 个 `ue_memory_*` Tool：

```text
add_rule  expand_node  export  get  get_context  get_evidence
mark_superseded  record_finding  record_task  search  status
update_knowledge  update_work  validate
```

**实测缺口**：

```text
向量检索         零实现（grep embedding/faiss/sqlite_vec 无命中）
                 pyproject dependencies = [] ，无任何第三方依赖
自动记忆捕获     仅 memory_service.record_task_outcome 一个入口
                 其余全部依赖 Agent 主动调用 ue_memory_record_*
符号化短期记忆   无
分层蒸馏         知识树是人为 Path 层级，没有 L0→L3 自动提升管线
团队共享         无
```

结论：**当前记忆系统是"Agent 自觉写、FTS5 关键词查"的被动库**。你要的"边用边积累"
在当前实现里不存在，这是 Memory Track 的核心工作量。

### 1.3 P4 与 Web UI 状态

```text
P4 / SourceControl    零实现（仅 Automation handler 内有无关字符串命中）
Web UI / HTTP Server  零实现（无 fastapi / uvicorn / starlette / http.server）
```

### 1.4 代码规模

```text
Python           35,885 行 / src/ue_agent_kit
  最大模块        agent_workflow.py 5,454 行  ← 已是维护热点
                  patches.py 2,253 / task_context.py 1,731
                  mcp_server.py 1,549 / project_memory.py 1,174
C++ Plugin       ~14,000 行（EditorBridge 17 handler / LiveWrite / AssetReaders）
测试             85 个文件；当前 live-writer discovered suite 712（0.8 closeout 历史 full suite 为 739）
文档             146 个 Markdown
```

## 2. 参考架构：TencentDB Agent Memory 的可借鉴部分

该项目的设计与 UEAgentKit 的差距点高度对应，采纳其中四项，拒绝两项。

### 2.1 采纳：L0→L3 四层渐进金字塔

```text
L0 Conversation   原始交互 / 工具调用日志
L1 Atom           原子记忆：单条可复用事实
L2 Scenario       场景：把多条 Atom 聚合成"某类任务怎么做"
L3 Persona        长期偏好 / 项目风格
```

映射到 UEAgentKit（**关键：不新建平行体系，复用已有资产**）：

```text
L0  ←  已有 live-write-journal / receipts / checkpoints / Change Set
       （这些已经是结构化、可恢复、带 Revision 的高质量原始日志）
L1  ←  已有 memory_records（projectFact / knownIssue / decisionRecord）
L2  ←  新增：从 Change Set + Trust Verdict 蒸馏"任务配方"（Recipe）
L3  ←  新增：项目级约定（命名规范、Policy 偏好、常犯错误）
```

UEAgentKit 相比通用记忆库有个决定性优势：**L0 层不是自然语言对话，而是带 SHA-256
Revision 和 Trust Verdict 的确定性证据**。蒸馏出的 L1/L2 天生可验证、可自动 stale，
不需要 LLM 猜测事实是否成立。

### 2.2 采纳：本地 FTS5 + 向量混合召回，零外部 API

参考实现用 FTS5 + 本地嵌入 + BM25/向量 RRF 融合，不依赖外部 API。UEAgentKit 已有 FTS5，
只需补向量层。**必须保持 `dependencies = []` 的零依赖底线**：向量能力做成
`optional-dependencies`，缺失时自动降级为纯 FTS5，任何门禁不得因此失败。

### 2.2.1 【最高优先约束】不得增加任务起止开销

用户已明确反馈过往同类记忆库的实际问题：

> 每次任务开始和结束 AI 都会花很长时间来处理，导致效率低下、Token 开销也大。

这是本 Track 的**否决性约束**：任何让任务开始变慢、结束变慢的设计一律不采纳，
即使会牺牲记忆完整度。参考实现里恰好有四个针对性机制，全部采纳：

**(1) 分层注入 vs 工具化 —— 只注入 L2/L3，L0/L1 一律工具化**

```text
L3 项目约定      注入（极小，稳定，几乎不变）
L2 任务配方      注入（仅当前任务域命中的 1-3 条摘要）
L1 原子事实      不注入，作为 Tool 供模型按需查
L0 原始证据      不注入，作为 Tool 供模型按需查
```

参考实现明确写出这样做的理由：避免上游 KV-cache 失效。注入内容必须稳定且极小，
否则每轮都在破坏 prompt 缓存——这正是"每次都很慢"的根因之一。

**(2) 写回必须异步，绝不阻塞任务结束**

参考实现在人类回合结束后**异步**写回 L0，蒸馏在后台进行。UEAgentKit 对应做法：

```text
任务结束时          只做一次追加写（append-only），O(1)，不做任何抽取
L1 蒸馏             后台 / 下次空闲 / 显式命令触发，不在任务链路上
L2/L3 蒸馏          显式命令或定期触发，绝不隐式发生
```

**(3) 硬性预算：条数 + 字符 + 超时三重上限**

参考实现对召回结果同时施加 item count、character budget、timeout 三重限制。
UEAgentKit 必须在 Server 侧强制（沿用现有 Token Budget 机制）：

```text
启动注入        ≤ 800 Token 硬上限，超出即截断，不得协商
单次召回        ≤ 5 条 / ≤ 2000 字符 / ≤ 300 ms 超时
超时行为        返回已得结果 + 显式 truncated 标记，绝不等待
```

**(4) 冷启动零成本：无命中时不做任何事**

新项目、无记忆、或当前任务域无命中时，注入内容必须为空字符串，不得输出
"暂无记忆"之类占位文本，也不得触发任何检索或建库动作。

**验收门禁（W4 之后、Memory 实现期间持续测量）**：

```text
[ ] Memory 关闭 vs 开启，任务首个 Tool 调用的额外延迟 < 200 ms
[ ] Memory 关闭 vs 开启，启动注入的额外 Token < 800
[ ] 任务结束的额外耗时 < 100 ms（仅 append 写）
[ ] 任一指标超标 → 该阶段判定 blocked，不得进入下一阶段
```

这套门禁必须在 M1 阶段就建立测量脚本，而不是等实现完再补。

### 2.3 采纳：符号化上下文压缩

参考实现把冗长工具日志压成 Mermaid 符号以省 Token。UEAgentKit 的对应场景是
Change Set / Impact Analysis / Semantic Diff 的大 JSON——这些正是当前最占上下文的部分。

### 2.4 采纳：Scope 分层（team / user / agent / visibility）

对应 0.9 已规划的 `/project` `/team` `/user` `/session` 分层，可直接沿用其权限模型。

### 2.5 拒绝：LLM 自由抽取写入知识库

参考实现依赖 LLM 从对话里抽取记忆。UEAgentKit 的 `user-confirmed / tool-observed /
model-inferred` 来源分级和 Revision stale 机制是既有资产，**不能为了自动积累而放弃**。
本计划的自动捕获全部走 `tool-observed`（确定性来源），`model-inferred` 仍需显式标记
且不参与默认召回。

这条同时也是效率约束：**UEAgentKit 的 L0→L1 蒸馏不需要调用 LLM**。Change Set、
Trust Verdict、Semantic Diff 已经是结构化确定性数据，用规则即可提取事实。
不调 LLM 意味着蒸馏成本接近零，这是 UEAgentKit 相对通用记忆库的结构性优势。

### 2.6 拒绝：云端 / 外部数据库依赖与 Proxy 架构

参考实现用 MemoryProxy 拦截 LLM 请求做透明注入，配套 MemoryCore/Hub/Panel 四个服务、
Redis/COS 存储与多节点部署。UEAgentKit **不采纳这套架构**：

```text
不引入 LLM 请求代理     现有集成点是 MCP Server，注入通过 Tool 返回值完成
不引入 Redis / COS      单人本地场景 SQLite 足够
不引入独立服务集群      MCP Server 进程内完成，避免运维负担
不引入云端存储          固定项目 + 本地 stdio + 无出站是现有安全模型基础
```

### 2.7 采纳：Wiki / CodeGraph 的"工具化而非注入"思路

参考实现把文档组织成可检索 Wiki，把代码库索引成含文件/符号/调用关系的 CodeGraph，
两者都只作为 Tool 按需读取，不整体注入。

UEAgentKit 已经有等价物且更强：SQLite 索引里的 Asset / Symbol / Reference 就是
现成的 CodeGraph，`ue_search_*` / `ue_get_references` 就是现成的按需工具。
**这部分无需新建，只需在 Track V 里给它一个可视化入口。**

### 2.8 参考边界声明

本地参考副本位于 `E:\WorkSpace\TencentDB-Agent-Memory`，**仅用于研究架构与设计思路**，
适用 [`docs/REFERENCE_POLICY.md`](../REFERENCE_POLICY.md) 的既有规则：不复制代码、
不移植实现、不引入其依赖。本计划采纳的是分层策略、注入/工具化边界、异步写回与
预算约束这些**设计决策**，具体实现全部独立编写。

参考来源：
[TencentDB-Agent-Memory](https://github.com/TencentCloud/TencentDB-Agent-Memory) ·
[MemoryCore README](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/feat/server_team/MemoryCore/README.md) ·
[四层管线说明](https://www.marktechpost.com/2026/05/23/tencent-open-sources-tencentdb-agent-memory-a-4-tier-local-memory-pipeline-for-ai-agents/) ·
[Mermaid 上下文卸载](https://www.tencentcloud.com/techpedia/144098) ·
[腾讯云资产管理四类资产](https://cloud.tencent.com/document/product/1813/134591) ·
[FTS5+向量混合召回实现参考](https://github.com/baodq97/tencentdb-agent-memory)

## 3. Track 划分与分支策略

四条 Track，三个并行分支。分支隔离原则沿用 Post-0.8 计划第 2 节：不 Rebase 已共享
分支，同步只用明确 checkpoint merge。

```text
Track W  Writer 能力          feature/live-writer-expansion   ← 当前活跃，最高优先
Track M  Memory 自动积累      feature/memory-context          ← 已存在远程分支，复用
Track C  P4 协作感知          feature/source-control-p4       ← 新建
Track V  知识库 Web 只读浏览   feature/knowledge-web-view      ← 新建
```

### 3.1 并行安全性分析

| Track | 触碰 C++ Plugin | 触碰 agent_workflow.py | 触碰 memory_* | 冲突风险 |
|---|---|---|---|---|
| W | 是（EditorBridge 写路径） | 是（重度） | 只读调用 | — |
| M | 否 | 仅新增 hook 调用点 | 是（重度） | 与 W 低 |
| C | 是（新 SourceControl handler） | 否 | 否 | 与 W 中 |
| V | 否 | 否 | 只读 | 极低 |

**排期约束**：
- Track V 可立即与 W 并行，无冲突。
- Track M 需在 W4 的 Change Set 结构冻结后再进入实现（否则 L0 蒸馏源会变），
  但**设计与 Schema 可提前进行**。
- Track C 触碰 C++，建议等 W4 的 C++ 改动落地后启动，避免 EditorBridge 双线冲突。

推荐启动顺序：

```text
T0 W3 收口 checkpoint     → complete (`45e6ea2`)
W4-0 … W4-7              → 立即，主线
Track V                  → 与 W4 并行（零冲突）
Track M 设计阶段         → 与 W4 并行（纯文档 + Schema 设计）
Track M 实现阶段         → W4 Change Set 冻结后
Track C                  → W4 C++ 落地后
```

## 4. Track W — Writer 能力（最高优先）

### T0：W3 收口 checkpoint（complete）

T0 已于 2026-08-27 完成。实际提交边界以磁盘 Git 历史为准：

```text
3280102 fix: close W3 live-write continuation and snapshot refresh
ab731f1 test: cover W3 continuation and full snapshot refresh
45e6ea2 docs: close W3 checkpoint strong verify
```

收口时 712/712 Python suite、`ValidateRelease.py` 0.7.0、UE5.6 Direct Build 与 `git diff --check` 全部通过。没有 Push、Rebase、Tag 或 Release。W4 的实现基线固定为 `45e6ea2`；后续规划文档不属于 W3 checkpoint。

### W4：多操作 / 有界批量（主线，详细计划已冻结）

W4 详细计划见
[`UEAGENTKIT_W4_MULTI_OPERATION_BOUNDED_BATCH_DETAILED_PLAN_20260826.md`](UEAGENTKIT_W4_MULTI_OPERATION_BOUNDED_BATCH_DETAILED_PLAN_20260826.md)，
本主计划不重复其内容，只固定它在项目层的位置与排期。

阶段定义以 W4 详细计划第 10、15 节为唯一权威来源；本主计划不维护第二套阶段编号。当前权威阶段为：

```text
W4-0  Contract Freeze and Baseline
W4-1  Bounded Batch Plan
W4-2  Single-Asset Multi-operation Apply
W4-3  Multi-Asset Resident Apply
W4-4  Multi-Asset Checkpoint Save
W4-5  Aggregate Strong Verify / Semantic Diff / Trust
W4-6  Recovery and Restart Hardening
W4-7  Full Acceptance / Documentation
```

**项目层补充约束**（W4 计划未涵盖，但对后续 Track 至关重要）：

W4 结束时必须冻结并文档化 Change Set 的最终结构，因为 Track M 的 L0 蒸馏直接读取它：

```text
[ ] Change Set schema 版本号显式递增并写入 CHANGELOG
[ ] batch receipt 的字段集冻结，标注哪些字段是 Memory 蒸馏契约
[ ] partial-applied / partial-saved 的持久化格式冻结
```

缺这一步，Track M 会在 W5 期间被反复破坏。

### W5：真实项目验收 + 规模基准（W4 后，≈10 天）

在 Reforge 真实工程上跑 W4 批量写入，采集：

```text
单操作 / 5 操作 / 20 操作 的 端到端延迟分解
常驻 Apply vs Cold Commandlet 的实测倍率
160-180 GB 工程下的 checkpoint save + strong verify 耗时
50 MB/s HDD 档位的退化曲线
```

W5 的输出是 Memory Track 的 L2 蒸馏素材来源之一（真实失败案例）。

## 5. Track M — Memory 自动积累（W4 之后）

分支：`feature/memory-context`（远程已存在，复用）

### 5.0 设计立场

当前记忆系统是"Agent 自觉写、FTS5 关键词查"的被动库。Track M 要把它变成
"用就会积累、查得准、且不拖慢任务"的主动库。

三个不可妥协的边界：

```text
1. 效率优先于完整度   任何拖慢任务起止的机制一律砍掉（见 2.2.1）
2. 确定性优先于覆盖度  自动写入只用 tool-observed，不用 LLM 抽取
3. 兼容优先于重构     Schema v3 的 12 张表不动，只做加法
```

### M1：效率基线与预算门禁（3 天，必须最先做）

**先建测量，再谈功能。** 这一阶段不写任何记忆功能，只建立能证明"没有变慢"的标尺。

交付：

```text
scripts/MeasureMemoryOverhead.py
  → 对比 Memory 关闭 / 开启（当前 v3 实现）两种模式
  → 测量：启动注入 Token 数、首个 Tool 调用延迟、任务结束耗时
  → 输出确定性 JSON 报告，纳入常规门禁
```

同时修正现状问题：当前 `ue_memory_get_context` 没有强制上限，Agent 可能一次拉回
过多内容。M1 要给它补上 2.2.1 的三重预算。

验收：

```text
[ ] 基线报告产出，记录当前 v3 的实际开销
[ ] ue_memory_get_context 强制 ≤ 800 Token / ≤ 5 条 / ≤ 300 ms
[ ] 超预算返回 truncated 标记而非报错
[ ] 无记忆时返回空，不产生占位文本
```

### M2：L0 自动捕获（4 天）

**核心思路：不新建 L0 存储，把已有的确定性日志认定为 L0。**

UEAgentKit 已经在写这些东西，它们比对话日志质量高得多：

```text
live-write-journal/<receipt>.json     每次实际修改
checkpoints/<checkpointId>.json       保存 + 强验证结果
Change Set                            任务级修改批次
Trust Verdict                         验证结论
Semantic Diff                         语义变更
Impact Analysis                       影响范围
```

M2 只做一件事：在这些产物落盘时，**追加一条极小的索引记录**（append-only，O(1)），
指向已有文件，不复制内容。

新增表（Schema 迁移 v3 → v4，纯加法。M4 的向量表另占 v5，不共用版本号）：

```sql
CREATE TABLE memory_l0_events (
    event_id        TEXT PRIMARY KEY,
    project_key     TEXT NOT NULL,
    event_kind      TEXT NOT NULL,     -- live_write | checkpoint | change_set
                                       -- | trust | semantic_diff | impact
    occurred_at_utc TEXT NOT NULL,
    artifact_path   TEXT NOT NULL,     -- 指向已有 JSON，不复制内容
    asset_paths     TEXT NOT NULL,     -- JSON 数组
    change_set_id   TEXT,
    outcome         TEXT NOT NULL,     -- success | failed | rejected | superseded
    distilled       INTEGER NOT NULL DEFAULT 0,
    UNIQUE(project_key, event_kind, artifact_path)
);
CREATE INDEX memory_l0_pending_idx
    ON memory_l0_events(project_key, distilled, occurred_at_utc);
```

写入点（全部是已有代码路径上加一行）：

```text
agent_workflow.py  写 live-write journal 后
                   写 checkpoint record 后
                   Change Set 状态变更后
                   Trust Verdict 产出后
```

**效率保证**：单条 INSERT，无索引重建，无 LLM，无网络。实测应 < 5 ms。

验收：

```text
[ ] 一次 W4 批量写入后，L0 事件完整记录且 outcome 正确
[ ] 失败 / 拒绝 / superseded 路径同样被记录（失败案例是最有价值的记忆）
[ ] 任务结束额外耗时 < 100 ms
[ ] Memory 关闭时零写入、零开销
[ ] 关闭 Memory 后再开启，历史事件不丢
```

### M3：L0→L1 规则蒸馏（5 天）

**离线执行，不在任务链路上。** 触发方式三种，都不是隐式的：

```text
显式命令      ue-agent memory distill
空闲触发      MCP Server 空闲 > 30 s 且有 pending L0 时，后台单批处理
下次启动      启动时若 pending > 阈值，后台异步处理（不阻塞首个请求）
```

规则蒸馏（**零 LLM**，全部从结构化字段推导）：

| L0 来源 | 提取规则 | 产出 L1 记录 |
|---|---|---|
| 成功 live_write + Trust verified | 资产类 + 操作 + 目标 + 生效值 | `projectFact`：该资产此属性当前值与来源 |
| 失败 / 拒绝 | 拒绝原因码 + 上下文 | `knownIssue`：什么条件下会被拒 |
| Policy 拒绝 | Policy 规则 + 触发条件 | `projectRule`：项目实际生效的约束 |
| Semantic Diff | 变更前后语义差异 | `projectFact`：变更历史 |
| Impact Analysis | 消费者集合 | `projectFact`：该资产的实际影响面 |
| supersession | 被覆盖的旧值链 | `decisionRecord`：为何改成现值 |

所有产出记录：

```text
source            = tool-observed（确定性，非推测）
revision_set      = 按证据类型绑定其真实来源的版本标识（见下）
node_id           = 按资产路径自动挂到知识树对应节点
```

**revision 绑定必须按证据类型区分**，不得一律绑 Asset SHA-256：

```text
live_write / Semantic Diff   目标资产 Revision
Policy 拒绝                  Policy digest
Impact Analysis              index generation + 相关资产 Revision 集合
Change Set / checkpoint      该 checkpoint / Change Set 的 revision 集合
P4 观测                      provider 的 observation / head revision（可得时）
supersession                 被覆盖值链两端的 revision
```

理由：统一绑资产哈希会让来源已变的记录仍被判 `valid`。例如改了
`write-policy.json`，从 Policy 拒绝蒸馏出的 `projectRule` 不会转 stale，
记忆库会继续给出过期规则——这正是三源新鲜度机制要防的失效模式。
`revision_set` 因此是多元组集合，集合内任一元素失配即转 stale。

自动挂树规则（避免人工维护 Path）：

```text
/Game/Characters/Hero/DA_HeroStats
  → /project/content/characters/hero
资产目录层级直接映射知识树 Path，节点不存在则自动创建
```

验收：

```text
[ ] 蒸馏完全不调用 LLM
[ ] 100 条 L0 蒸馏耗时 < 5 s
[ ] 蒸馏过程可中断、可续跑（distilled 标记幂等）
[ ] 资产 Revision 变化后，相关 L1 自动转 stale
[ ] 蒸馏不在任何任务的同步链路上（用测量脚本证明）
[ ] 重复蒸馏同一 L0 不产生重复 L1
```

### M4：混合召回（FTS5 + 向量 + RRF）（6 天）

当前只有 FTS5 关键词匹配，"这个材质参数为什么是这个值"这类语义查询召回差。

**零依赖底线**：向量能力做成可选，缺失时静默降级为纯 FTS5。

```text
pyproject.toml
[project.optional-dependencies]
vector = ["sqlite-vec>=0.1,<1", "model2vec>=0.3,<1"]
```

嵌入模型选择原则（**效率优先**）：

```text
必须 CPU 可跑，无 GPU 依赖
模型体积 < 100 MB
单条嵌入 < 10 ms
优先静态嵌入（model2vec 类），拒绝需要完整 transformer 推理的方案
```

理由：记忆条目是短文本，静态嵌入质量足够，速度快一到两个数量级。宁可牺牲少量
召回质量，也不能让蒸馏或查询变慢。

新增表（Schema 迁移 v4 → v5。迁移无条件执行并建表，即使 vector extra 未安装，
以保证版本号是关于结构的可靠陈述）：

```sql
CREATE TABLE memory_embeddings (
    record_id    TEXT PRIMARY KEY REFERENCES memory_records(record_id) ON DELETE CASCADE,
    model_id     TEXT NOT NULL,
    dim          INTEGER NOT NULL,
    embedding    BLOB NOT NULL,
    created_at_utc TEXT NOT NULL
);
```

召回融合（RRF，参考实现同思路）：

```text
FTS5 BM25 top-k        →  rank_fts
向量余弦 top-k          →  rank_vec
RRF: score = Σ 1/(60 + rank)
最终按 score 排序，施加 2.2.1 的三重预算
```

嵌入生成契约：**记录嵌入只在 M3 蒸馏时顺带生成**；查询路径只为查询文本生成 1 次
嵌入，不重算任何记录嵌入。向量检索必须对查询求嵌入才能算相似度，所以"查询时零嵌入
生成"是不可满足的说法，不得作为验收项。另需提供可续跑的回填命令，处理向量能力启用
前已存在的 L1 记录。

验收：

```text
[ ] 未安装 vector extra 时，全部功能正常，退化为 FTS5
[ ] 安装后，语义查询召回优于纯 FTS5（准备 20 条基准查询对比）
[ ] 单次混合召回 < 300 ms（含预算截断）
[ ] 查询路径只生成 1 个 query embedding，不重算 corpus / record embeddings
[ ] 嵌入模型缺失 / 加载失败时静默降级，不抛异常
```

### M5：L2 任务配方 + L3 项目约定（5 天）

L2/L3 是**唯一会被注入 prompt 的层**，因此必须极小、极稳。

**L2 Scenario（任务配方）**：把重复出现的成功模式聚合成"这类任务这样做"。

```text
触发条件    同类操作（同 operation + 同资产类）成功 ≥ 3 次
产出        一条 ≤ 200 字的配方摘要
内容        典型 Plan 形态 + 常见拒绝原因 + 必需前置条件
```

**L3 Persona（项目约定）**：项目级稳定事实。

```text
命名规范        从实际资产路径统计推导
Policy 偏好     从实际 Policy 配置提取
高频错误        从 knownIssue 聚合出 Top 3
```

L3 总量硬上限：**≤ 400 Token**。超出时按命中频率淘汰。

注入契约（关键，直接决定效率）：

```text
ue_memory_get_context 返回
  ├─ L3 项目约定        ≤ 400 Token   总是返回（极稳定，利于 KV-cache）
  ├─ L2 当前任务域配方   ≤ 400 Token   仅命中时返回，最多 2 条
  └─ L1/L0             不返回，仅告知"可用 ue_memory_search 查询"
```

L2/L3 生成同样离线，与 M3 同批次执行。

验收：

```text
[ ] L3 ≤ 400 Token，L2 单条 ≤ 200 字，合计 ≤ 800 Token
[ ] 注入内容在无新蒸馏时逐字节稳定（保护 KV-cache）
[ ] L2 仅在任务域命中时出现
[ ] 冷启动 / 无记忆时返回空字符串
[ ] 启动注入额外延迟 < 200 ms
```

### M6：符号化上下文压缩（4 天，可选/延后）

参考实现用 Mermaid 压缩工具日志省 Token。UEAgentKit 的对应场景：

```text
Impact Analysis 的消费者图    → Mermaid graph
Change Set 的操作序列        → Mermaid sequence
知识树局部结构               → Mermaid tree
```

**判定为可选**：仅当 W5 真实项目测量显示这些 JSON 确实是上下文瓶颈时才实施。
不做投机优化。

### Track M 总计

```text
M1 效率基线与预算门禁      3 天   ← 必须最先
M2 L0 自动捕获             4 天
M3 L0→L1 规则蒸馏          5 天
M4 混合召回                6 天
M5 L2/L3 与注入契约        5 天
M6 符号化压缩              4 天   ← 可选，数据驱动
                    合计 ≈ 23-27 天
```

## 6. Track C — P4 协作感知

分支：`feature/source-control-p4`（新建，W4 的 C++ 改动落地后启动）

### 6.0 立场

沿用 ROADMAP 0.9 的既定原则：**首版只分析、提示或阻止，不自动抢锁或覆盖他人修改**。
P4 是团队共享状态，误操作代价远高于本地写入，因此比现有写入门禁更保守。

### C1：只读状态感知（4 天）

C++ 侧走 UE 内置 `ISourceControlModule`，不直接调 `p4.exe`（避免凭据管理与
工作区解析问题，UE 已经解决过）。

新增 EditorBridge handler：

```text
getSourceControlStatus
  输入  assetPaths[]（有界，≤ 100）
  输出  provider / enabled / 每资产:
        { depotPath, checkedOut, checkedOutBy, locked, lockedBy,
          headRevision, haveRevision, isUpToDate, isAdded, isDeleted }
```

对应 MCP Tool：

```text
ue_get_source_control_status      有界批量查询
ue_get_asset_checkout_state       单资产签出/锁定状态（不做责任归属推断）
```

不引入新依赖，不做任何 P4 写操作。

验收：

```text
[ ] P4 未启用 / 未连接时明确返回 disabled，不报错、不挂起
[ ] 查询 100 资产 < 2 s
[ ] 他人签出 / 锁定状态正确反映
[ ] 只读，绝不触发 checkout
```

### C2：写入前冲突预检（5 天）

把 P4 状态接入既有写入门禁链，作为新的 fail-closed 条件。

插入位置（在现有 Policy / Revision 校验之后，Apply 之前）：

```text
Plan → Policy → Revision → 【新增 P4 Preflight】 → Live Apply
```

预检规则（全部 fail-closed）：

```text
他人锁定           → 拒绝，报 source-control-locked
他人签出           → 拒绝，报 source-control-checked-out-by-other
本地非最新         → 拒绝，报 source-control-out-of-date
未签出且只读       → 拒绝，报 source-control-not-checked-out
                     （不自动签出，需用户显式操作）
P4 不可用          → 按 Policy 配置决定 skip 或 fail
```

Policy 新增字段：

```json
{
  "sourceControl": {
    "preflightEnabled": true,
    "requireCheckedOut": true,
    "requireUpToDate": true,
    "allowWhenProviderUnavailable": false,
    "autoCheckout": false
  }
}
```

`autoCheckout` 默认 `false` 且**首版不实现 true 分支**，保留字段以便将来扩展。

验收：

```text
[ ] 六类拒绝路径各有真实 P4 环境验证
[ ] 拒绝时零写入、零 Dirty
[ ] P4 不可用且 allowWhenProviderUnavailable=false 时拒绝
[ ] 预检额外延迟 < 500 ms
[ ] 既有非 P4 项目行为完全不变（默认关闭）
```

### C3：变更关联与审计（3 天）

把 AI 修改关联到 P4 Changelist，供人工 Review。

```text
ue_get_changelist_context     读取当前 pending changelist 及其文件
Change Set ↔ Changelist       在 Change Set 记录中登记 changelist 号
Backup Manifest 扩展          增加 depotPath / headRevision 字段
```

**不实现自动 Submit**。Submit 是不可逆的团队级操作，永远由人执行。

验收：

```text
[ ] Change Set 可反查对应 P4 changelist
[ ] Backup Manifest 含 depot 信息
[ ] 无任何自动 submit / revert 代码路径
```

### C4：Memory 联动（2 天）

P4 状态是高价值 L0 事件，接入 Track M：

```text
冲突拒绝           → knownIssue：某资产在并发写入下曾被锁定拒绝
签出频次统计       → projectFact：某目录的变更活跃度（不含人员断言）
```

**人员归属边界**：观测到的签出 / 锁定历史只能作为事实观测存储，不得蒸馏为
「某人是某目录负责人」这类持久断言——那属于 model-inferred 结论，存成
tool-observed 会污染 source 分级，且会把记忆库变成个人活动记录。责任归属
只允许来自项目配置的显式声明、团队规则或用户确认。

依赖 Track M 的 M2 完成。

### Track C 总计

```text
C1 只读状态感知        4 天
C2 写入前冲突预检      5 天
C3 变更关联与审计      3 天
C4 Memory 联动         2 天
                合计 ≈ 14 天
```

## 7. Track V — 知识库 Web 只读浏览

分支：`feature/knowledge-web-view`（新建，可立即与 W4 并行，零冲突）

### 7.0 立场（用户明确约束）

```text
人工不得直接修改知识库，写入只能由 Agent 完成
```

因此 Web 界面是**严格只读**的。这不是阶段性妥协，而是永久架构约束：

```text
后端只开放 GET，不实现任何 POST / PUT / DELETE
数据库连接以只读模式打开（SQLite mode=ro）
需要修改时，界面提示"请让 Agent 执行"，并给出建议的 Agent 指令文本
```

这个约束反而简化了实现：无需鉴权写入、无需并发控制、无需事务冲突处理。

### V1：本地只读浏览器（6 天）

技术选型（**零新增运行时依赖**）：

```text
后端    Python 标准库 http.server + sqlite3（mode=ro）
        不引入 fastapi / uvicorn / starlette
前端    单个静态 HTML + 原生 JS，无构建步骤、无 npm
启动    ue-agent knowledge-view --port 8765
绑定    仅 127.0.0.1，不监听外部接口
```

选标准库而非 FastAPI 的理由：项目当前 `dependencies = []`，Web 浏览是辅助功能，
不值得为它引入 ASGI 栈与运行时依赖。只读 JSON 接口用 `http.server` 完全够用。

**安全说明**：绑定 127.0.0.1 且只读，但仍需在文档中明确这是本地开发工具，
不应暴露到网络。默认不启用任何鉴权（本地只读、无写入面）。

页面（四个视图）：

```text
知识树      左树 + 右详情，展开节点看挂载的记录
记录列表    按 type / status / source 筛选，显示 stale 标记
Active Work 当前目标 / TODO / 阻塞 / 下一步
Evidence    单条记录的证据链，指向 receipt / checkpoint / diff
```

只读 API：

```text
GET /api/tree                    知识树结构
GET /api/node/<node_id>          节点详情 + 挂载记录
GET /api/records?type=&status=   记录列表（分页）
GET /api/record/<record_id>      记录详情 + Evidence
GET /api/work                    Active Work
GET /api/status                  Memory 状态摘要
```

验收：

```text
[ ] 数据库以 mode=ro 打开，写入尝试在代码层不存在
[ ] 仅监听 127.0.0.1
[ ] 零新增运行时依赖（pyproject dependencies 保持 []）
[ ] 无 npm / 构建步骤
[ ] Memory 数据库不存在时给出清晰提示而非崩溃
[ ] stale / conflicted / superseded 状态有明确视觉区分
[ ] 界面任何位置都不提供编辑入口
```

### V2：可视化分析面板（8 天，V1 之后）

在只读前提下增加分析视图：

```text
资产引用图      Asset → Asset 依赖，力导向图，可下钻
影响范围图      选中资产，高亮其消费者（复用 Impact Analysis 数据）
知识覆盖热图    哪些资产目录有记忆、哪些是盲区
变更时间线      Change Set 时序 + Trust 结果
stale 分布      哪些知识因资产变更失效，按目录聚合
```

图形库选择：单文件可 vendored 的轻量库（如 d3 单文件构建），不引入 npm。

验收：

```text
[ ] 5000 节点引用图可交互（不卡死）
[ ] 图数据全部来自现有 SQLite，不新增导出步骤
[ ] 仍然严格只读
```

### Track V 总计

```text
V1 本地只读浏览器        6 天
V2 可视化分析面板        8 天
                  合计 ≈ 14 天
```

## 8. 横向：维护性与技术债

这些不单独占 Track，插入到各 Track 的自然间隙。

### D1：拆分 agent_workflow.py（3 天，W4 之后立即）

5,454 行已是维护热点，W4 会再往里加编排逻辑。W4 完成后必须拆分：

```text
agent_workflow.py  →  workflow_plan.py       Plan / DryRun
                      workflow_live.py       Live Apply / Undo / Discard
                      workflow_verify.py     Save / Verify / Checkpoint
                      workflow_batch.py      W4 批量编排
                      workflow_common.py     共用类型与路径
```

约束：纯移动 + import 调整，不改行为；`test_tool_registry.py` 保证工具面不变。

**时机很关键**：必须在 W4 之后、Track M 实现之前。W4 之前拆会与 W4 冲突，
Track M 之后拆会牵动更多调用点。

### D2：Tool 计数单一来源（1 天）

当前 105 / 93 / 60 等计数散落在 README、ROADMAP、测试中，容易不一致。改为从
注册表运行时导出，文档引用生成结果。

### D3：UE Build CI（3 天）

当前 UE5.6 编译只在本地发布机执行。目标是在有引擎环境的机器上做定时编译门禁，
避免 C++ 改动累积后才发现问题。Track C 会加 C++ 代码，D3 最好在其之前。

### D4：API 参考文档（2 天）

补 0.1 节分析出的文档缺口：从 MCP 注册表自动生成工具参考，按场景分组。
可与 Track V 合并交付（Web 界面顺带提供工具浏览页）。

## 9. 总排期与依赖图

```text
T0  W3 收口 checkpoint                 complete   45e6ea2
     │
     ├─────────────────────────────────────────────────┐
     ▼                                                 ▼
W4  多操作 / 有界批量        24 天          V1 只读浏览器      6 天
     │                                                 │
     │  （Change Set 结构冻结）                          ▼
     │                                      V2 可视化面板     8 天
     ▼
D1  拆分 agent_workflow      3 天
     │
     ├──────────────────┬──────────────────┐
     ▼                  ▼                  ▼
W5  真实项目验收 10 天   M1 效率基线 3 天    C1 P4 只读 4 天
                        │                  │
                        ▼                  ▼
                   M2 L0 捕获 4 天      C2 冲突预检 5 天
                        │                  │
                        ▼                  ▼
                   M3 L1 蒸馏 5 天      C3 变更审计 3 天
                        │                  │
                        ▼                  │
                   M4 混合召回 6 天         │
                        │                  │
                        ▼                  │
                   M5 L2/L3 注入 5 天 ◄─────┘
                        │              C4 Memory 联动 2 天
                        ▼
                   M6 符号化压缩 4 天（可选）
```

### 关键依赖

```text
W4 → D1        W4 加完编排再拆分，避免冲突
W4 → M2        L0 蒸馏源必须先冻结
D1 → M/C       在拆分后的模块上开发，避免大文件冲突
M2 → C4        P4 事件需要 L0 通道
V1/V2 独立     全程可并行
```

### 里程碑

```text
里程碑 1（≈4 周）   W4 完成 + V1 上线
                    → Agent 可做多操作任务，知识库可视
里程碑 2（≈7 周）   D1 + M1-M3 + C1 完成
                    → 记忆开始自动积累，P4 状态可见
里程碑 3（≈11 周）  M4-M5 + C2-C3 + V2 完成
                    → 混合召回可用，协作安全，分析面板可用
里程碑 4            W5 规模验收 + 0.9 发布评审
```

按单人 AI 辅助开发估算，总量约 11–13 周。四条 Track 并行不意味着人力并行，而是
**遇到阻塞时可切换到另一条线，不空等**——这也是用多 worktree 的实际收益。

## 10. 全局验收门禁

沿用现有门禁，新增三项：

```text
既有
[ ] python scripts\ValidateRelease.py --require-release-docs
[ ] Ruff / 完整 Python suite / compileall
[ ] UE5.6 Direct Build（触碰 C++ 时）
[ ] UTF-8 无 BOM / CRLF / whitespace / 完整 diff

新增
[ ] pyproject dependencies 保持 []（向量能力仅在 optional 内）
[ ] Memory 开销门禁：scripts\MeasureMemoryOverhead.py 全部达标
[ ] Web 视图只读断言：无任何写入 SQL 路径
```

## 11. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| Memory 拖慢任务（用户已遇到过） | 工具变得不可用 | M1 先建门禁；任一指标超标即 blocked；蒸馏零 LLM、全异步 |
| W4 批量放大 recovery 复杂度 | 部分应用状态难恢复 | 沿用 W4 计划的显式 partial 边界；不声明跨包原子性 |
| 向量依赖破坏零依赖底线 | 安装复杂化 | 放入 optional-dependencies，缺失静默降级 |
| P4 误操作影响团队 | 代价高于本地写入 | 首版纯只读 + fail-closed 预检；无自动 submit/checkout |
| agent_workflow.py 继续膨胀 | 维护成本失控 | D1 在 W4 后强制执行，不可延后 |
| 四 Track 并行冲突 | 合并困难 | 按第 3.1 节排期；C 等 W4 的 C++ 落地；V 全程独立 |
| 自动记忆写入噪声 | 知识库污染 | 只用 tool-observed；去重靠 UNIQUE 约束；stale 自动失效 |

## 12. 立即可执行的下一步

```text
1. 按 W4 详细计划启动 W4-0（Contract Freeze and Baseline）
2. W4 内部阶段、失败语义与 C1-C12 均以 W4 Detailed Plan 为唯一权威
3. 其他 Track 仅在需要切换主线或满足其前置条件时启动；不影响当前 W4 主线
```

Track M 的实现不要在 W4 完成前开始——L0 蒸馏源会变。但设计文档可以现在写，
这样 W4 一结束就能直接进入 M1。

