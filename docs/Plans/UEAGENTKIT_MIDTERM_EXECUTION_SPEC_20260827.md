# UEAgentKit 中期执行规格（Mid-term Execution Spec）

> 日期：2026-08-27
>
> 文档性质：**面向执行 Agent 的任务规格书**。本文档假定读者是接手实现的 AI Agent，
> 不假定其读过此前的对话。
>
> 覆盖范围：短期收口（T0）→ W4/W5 → Memory 自动积累 → P4 协作 → 知识库 Web →
> 中期新能力（Widget BP / Blueprint Graph / Level Actor / C++ 理解 / 性能分析）
>
> 配套文档：
> - 项目级主计划：[`UEAGENTKIT_MASTER_DEVELOPMENT_PLAN_20260827.md`](UEAGENTKIT_MASTER_DEVELOPMENT_PLAN_20260827.md)
> - W4 详细计划：[`UEAGENTKIT_W4_MULTI_OPERATION_BOUNDED_BATCH_DETAILED_PLAN_20260826.md`](Archive/UEAGENTKIT_W4_MULTI_OPERATION_BOUNDED_BATCH_DETAILED_PLAN_20260826.md)
> - Post-0.8 计划：[`UEAGENTKIT_POST_0_8_DEVELOPMENT_PLAN_20260823.md`](Archive/UEAGENTKIT_POST_0_8_DEVELOPMENT_PLAN_20260823.md)
>
> 主 worktree：`E:\WorkSpace\UEAgentKit`（`feature/agent-reliability`）
> 活跃 worktree：`E:\WorkSpace\UEAgentKit-LiveWriter`（`feature/live-writer-expansion`）

---

## 第 0 部分：执行规约（所有 Agent 必读）

### 0.1 本文档的任务卡格式

每个任务用统一格式描述，执行 Agent 必须逐项满足：

```text
任务 ID      唯一标识，用于提交信息与文档交叉引用
前置条件     必须已完成的任务 ID；未满足不得开工
分支         必须在此分支实现
预估          工期参考值，非承诺
改动面        允许触碰的文件/目录白名单
禁止项        明确不得做的事
交付物        必须产出的文件
验收契约      逐条可判定的检查项，全部通过才算完成
证据要求      必须留下的确定性证据形式
```

### 0.2 不可协商的工程纪律

以下规则来自项目既有实践（见 `CLAUDE.md`、Post-0.8 计划第 2 节），违反即视为任务失败：

```text
1. 一个任务一个逻辑提交组，不把架构改动、功能实现、测试、文档压进单个提交
2. 不 Rebase 已共享或并行开发的分支；跨分支同步只用明确 checkpoint merge
3. 不修改 published version、不建 Tag、不建 Release artifact、不 Push
   （发布是独立授权流程，任何技术任务都不得触碰）
4. 不提交 Output/、Backups/、测试工程资产、日志、缓存、本地配置
5. 编码 UTF-8 无 BOM、换行 CRLF、无尾随空白
6. 每个阶段结束时仓库必须处于可运行、可恢复状态
```

### 0.3 全局门禁（每个任务完成时执行）

```bat
python scripts\ValidateRelease.py --require-release-docs
```

该命令覆盖版本源一致性、双语发布文档、Ruff、完整 Python 测试、JSON Schema、
Patch 示例与示例 Policy。此外：

```text
触碰 C++ 时        必须执行 scripts\BuildPluginDirect.cmd（UE5.6 Direct Build）
触碰写入能力时      必须执行真实 UE Dry Run / Commit / reload / rollback 回归
触碰 Tool 注册表时  tests/python/test_tool_registry.py 必须反映新的工具面
新增依赖时          禁止（见 0.4）
```

### 0.4 零依赖底线

`pyproject.toml` 的 `dependencies` 当前为 `[]`，**必须保持**。任何第三方库只能进入
`optional-dependencies`，且缺失时功能必须静默降级，不得抛异常、不得让门禁失败。

```text
现有 optional-dependencies
  mcp   = ["mcp>=1.27,<2"]
  dev   = ["build", "jsonschema", "ruff"]
本规格新增允许
  vector = 见任务 M4
```

### 0.5 安全模型不可退让

现有安全边界是产品核心资产，任何任务不得削弱：

```text
不开放任意 SQL / Shell / Python / UObject Method
不开放自动保存（保存必须显式授权）
写入必须保留 Policy / Revision / Plan / Snapshot / Transaction / Save /
  Verify / Semantic Diff / Trust / Recovery 十个环节
Editor-resident fast path 不得替代最终 independent verification
新增 Operation 必须注册明确 Target、Policy、Snapshot、Undo、失败恢复
  与真实 UE 回归；注册本身不授予写权限
```

### 0.6 证据要求

本项目以"确定性证据"而非"声称完成"为验收标准。每个任务必须留下：

```text
真实 UE 相关任务    真实 UE5.6 执行日志 + 前后 SHA-256 Revision + Trust Verdict
Python 相关任务     测试用例 + 门禁命令输出
性能相关任务        确定性 JSON 报告，含环境、样本量、分位数
文档任务            结果文档（*_RESULT_*.md），明确 PASS/FAIL 逐项状态
```

禁止在结果文档中写"应该可以""预计通过"这类未验证表述。未验证项标记为
`blocked` 或 `deferred`，并写明原因。

---

## 第 1 部分：事实基线（已在磁盘核实）

执行 Agent 不得基于推测开工。以下状态于 2026-08-27 核实。

### 1.1 已完成能力

```text
版本                      0.7.0 已发布（UE 5.6）
0.8 capability scope      已本地收口（105 Tool / 18 Operation / 0 Must-fix）
R0 Task Context           complete
R1 Impact Analysis        complete
R2 Semantic Diff          complete
R3 Verification / Trust   complete
R4 / R4.1 Benchmark       complete
R5                        deferred by benchmark evidence

W0 latency baseline       complete   142ca1e
W1 Blueprint 常驻写入      complete   8bede6f（Undo crash 已修复）
W2 Fast Resident Verify   complete   31f0faa
W3 Checkpoint Strong Verify complete C0-C6 全 PASS
W4                        计划已写（767 行），未实现
```

### 1.2 代码规模

```text
Python      35,885 行 / src/ue_agent_kit
            agent_workflow.py 5,454  ← 维护热点，见任务 D1
            patches.py 2,253 / task_context.py 1,731
            mcp_server.py 1,549 / project_memory.py 1,174
C++         ~14,000 行（EditorBridge 17 handler / LiveWrite / AssetReaders）
测试        85 文件；当前 live-writer discovered suite 712（0.8 closeout 历史 full suite 为 739）
文档        146 Markdown
```

### 1.3 已核实的零实现缺口

以下能力**当前完全不存在**，是本规格的主要工作量：

```text
向量检索              grep embedding/faiss/sqlite_vec → 无命中
自动记忆捕获          仅 memory_service.record_task_outcome 一处
符号化上下文压缩      无
L0→L3 分层蒸馏        知识树是人为 Path，无自动提升管线
团队共享              无
P4 / SourceControl    无（仅 Automation handler 有无关字符串）
Web UI / HTTP Server  无
Widget Blueprint Reader   AssetReaders/ 下仅有 Animation/Material/Mesh/Niagara/World
Anim Blueprint 深度     无 State Machine / Transition 导出
Control Rig             无
Blueprint Graph CRUD    grep addNode/createNode/connectPin → 无命中
Level Actor 写入        grep spawnActor/setActorProperty → 无命中
C++ 头文件解析          grep libclang/UCLASS → 无命中
性能分析                grep drawcall/memoryReport → 无命中
```

### 1.4 W3 收口 checkpoint（已完成）

`feature/live-writer-expansion` 的 W3 产品改动、测试与结果文档已于 2026-08-27 收口：

```text
3280102 fix: close W3 live-write continuation and snapshot refresh
ab731f1 test: cover W3 continuation and full snapshot refresh
45e6ea2 docs: close W3 checkpoint strong verify
```

当前 W4 实现基线固定为 `45e6ea2`。仍在 working tree 中的 README 与 Master/Midterm/W4 等规划文档不属于 W3 实现边界。

---

## 第 2 部分：分支与并行策略

### 2.1 分支分配

```text
Track W  Writer 能力         feature/live-writer-expansion   已存在，活跃
Track M  Memory 自动积累     feature/memory-context          远程已存在，复用
Track C  P4 协作感知         feature/source-control-p4       需新建
Track V  知识库 Web 只读      feature/knowledge-web-view      需新建
Track X  中期新能力           feature/asset-coverage-expansion 需新建
```

新建分支基线：需要包含 Writer 基础时，从 `feature/live-writer-expansion@45e6ea2` 或其后明确 checkpoint 切出，**不从缺少 W1-W3 的旧 `main` 基线直接切**。

### 2.2 冲突矩阵

| Track | C++ Plugin | agent_workflow.py | memory_*.py | 与 W 冲突风险 |
|---|---|---|---|---|
| W | 是（EditorBridge 写路径） | 重度 | 只读 | — |
| M | 否 | 仅加 hook 调用点 | 重度 | 低 |
| C | 是（新 SourceControl handler） | 否 | 否 | 中 |
| V | 否 | 否 | 只读 | 极低 |
| X | 是（AssetReaders + 新 Writer） | 中度 | 否 | 高 |

### 2.3 并行许可

```text
可立即与 W4 并行        Track V（零冲突）
                       Track M 的设计阶段（纯文档）
必须等 W4 完成          Track M 实现（L0 蒸馏源需先冻结）
                       Track X（改动面与 W 重叠严重）
必须等 W4 的 C++ 落地    Track C（避免 EditorBridge 双线冲突）
必须等 D1 完成          Track M / X 的 agent_workflow 相关改动
```

---

## 第 3 部分：任务卡 —— Track W（Writer，最高优先）

### 任务 T0：W3 收口 checkpoint（complete）

```text
任务 ID    T0
状态       complete
分支       feature/live-writer-expansion
完成点     45e6ea2
```

实际提交：

```text
3280102 fix: close W3 live-write continuation and snapshot refresh
ab731f1 test: cover W3 continuation and full snapshot refresh
45e6ea2 docs: close W3 checkpoint strong verify
```

验收证据：712/712 Python suite、0.7.0 release validation、UE5.6 Direct Build、`git diff --check` 全 PASS；无 Push / Rebase / Tag / Release。T0 不再是待执行任务。

---

### 任务 W4：多操作 / 有界批量

```text
任务 ID    W4（含 W4-0 … W4-7 子阶段）
前置条件   T0
分支       feature/live-writer-expansion
预估       24 天
```

**实现依据**：完全遵循
[`UEAGENTKIT_W4_MULTI_OPERATION_BOUNDED_BATCH_DETAILED_PLAN_20260826.md`](Archive/UEAGENTKIT_W4_MULTI_OPERATION_BOUNDED_BATCH_DETAILED_PLAN_20260826.md)。
该计划已含完整的目标、非目标、阶段划分、真实 UE 验收案例 C1–C12 与 Definition of Done。
**本规格不重复其内容，也不得与其冲突**；若执行中发现两者矛盾，以 W4 详细计划为准，
并在结果文档中记录矛盾点。

**阶段定义**：以 W4 详细计划第 10、15 节为唯一权威来源。本规格不维护第二套阶段编号，避免后续漂移。当前权威阶段为：

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

**本规格追加的强制要求**（W4 计划未涵盖，但下游 Track 依赖）：

W4-7 收口时必须额外产出 **Change Set 结构冻结声明**。理由：任务 M2 的 L0 捕获直接读取
Change Set 与 batch receipt；结构不冻结会导致 Track M 在 W5 期间被反复破坏。

```text
[ ] Change Set schema 版本号显式递增，写入 CHANGELOG.md
[ ] batch receipt 字段集冻结，逐字段标注是否属于 Memory 蒸馏契约
[ ] partial-applied / partial-saved 持久化格式冻结
[ ] 冻结声明写入 docs/Plans/UEAGENTKIT_W4_..._RESULT_*.md 的独立章节
```

**禁止项**（重申 W4 计划第 3 节非目标）：

```text
Generic Blueprint Graph CRUD
任意 node 创建/删除/连线
任意 UObject 属性反射批量写入
无界资产列表
向 Agent 暴露 100 操作的批量请求
跨 package 原子性声明
无授权自动保存
把常驻 Editor 自验证当作独立 Trust
```

**验收契约**：以 W4 计划第 16 节的 Definition of Done 全 16 项为准，外加上述冻结声明 4 项。

---

### 任务D1：拆分 agent_workflow.py

```text
任务 ID    D1
前置条件   W4
分支       feature/live-writer-expansion
预估       3 天
```

**背景**：`agent_workflow.py` 已 5,454 行，W4 会再加批量编排逻辑。必须在 W4 之后、
Track M/X 之前拆分——提前拆会与 W4 冲突，推后拆会牵动更多调用点。

**目标拆分**：

```text
agent_workflow.py  →  workflow_plan.py       Plan / DryRun
                      workflow_live.py       Live Apply / Undo / Discard
                      workflow_verify.py     Save / Verify / Checkpoint
                      workflow_batch.py      W4 批量编排
                      workflow_common.py     共用类型、路径、序列化
                      agent_workflow.py      仅保留门面 re-export
```

**禁止项**：

```text
不改任何行为、不改任何签名、不改任何返回结构
不趁机重命名
不趁机优化
```

**验收契约**：

```text
[ ] 拆分为纯移动 + import 调整，git diff 中无逻辑行变更
[ ] tests/python/test_tool_registry.py 输出的工具面逐字节不变
[ ] 以 D1 开工前实际 discovered Python suite 为行为保持基线，全部通过且无需修改既有用例（当前 W3 收口值为 712）
[ ] 每个新模块 < 1,500 行
[ ] agent_workflow.py 保留 re-export，外部 import 路径不破坏
[ ] 门禁全绿
```

**证据要求**：拆分前后 `test_tool_registry` 输出的 diff（必须为空）。

---

### 任务 W5：真实项目验收 + 规模基准

```text
任务 ID    W5
前置条件   W4、D1
分支       feature/live-writer-expansion
预估       10 天
```

在 Reforge 真实工程上验收 W4 批量写入，并采集规模基准。

**测量项**：

```text
单操作 / 5 操作 / 20 操作 端到端延迟分解（按 stage）
常驻 Apply vs Cold Commandlet 实测倍率
160-180 GB 工程的 checkpoint save + strong verify 耗时
50 MB/s HDD 档位退化曲线
```

**交付物**：

```text
docs/Plans/UEAGENTKIT_W5_REAL_PROJECT_ACCEPTANCE_RESULT_<date>.md
benchmarks 下的确定性 JSON 报告（含环境、样本量、p50/p95）
```

**验收契约**：

```text
[ ] 真实工程完成至少 3 个多操作任务，全部 Trust verified
[ ] 延迟分解报告含各 stage 占比
[ ] 失败案例被记录（失败数据是 M3 蒸馏的高价值素材）
[ ] 报告可复跑，同环境下结果稳定
```

---

## 第 4 部分：任务卡 —— Track M（Memory 自动积累）

### 4.0 Track M 的否决性约束（执行 Agent 必读）

项目所有者已明确反馈过往同类记忆库的实际问题：

> 每次任务开始和结束 AI 都会花很长时间来处理，导致效率低下、Token 开销也大。

**这是本 Track 的否决性约束**。任何让任务开始变慢、结束变慢的设计一律不采纳，
即使会牺牲记忆完整度。四条硬规则：

**(1) 只注入 L2/L3，L0/L1 一律工具化**

```text
L3 项目约定    注入   ≤ 400 Token，极稳定
L2 任务配方    注入   ≤ 400 Token，仅当前任务域命中，最多 2 条
L1 原子事实    不注入，作为 Tool 按需查
L0 原始证据    不注入，作为 Tool 按需查
```

理由：注入内容每轮变动会破坏上游 KV-cache，这是"每次都很慢"的主因之一。
注入内容必须在无新蒸馏时逐字节稳定。

**(2) 写回必须异步，绝不阻塞任务结束**

```text
任务结束时     只做 append-only 单条写，O(1)，零抽取
L1 蒸馏        显式命令 / 空闲触发 / 启动后台，绝不在任务链路上
L2/L3 蒸馏     显式命令或定期触发，绝不隐式发生
```

**(3) 三重预算硬上限，Server 侧强制**

```text
启动注入      ≤ 800 Token，超出即截断，不得协商
单次召回      ≤ 5 条 / ≤ 2000 字符 / ≤ 300 ms
超时行为      返回已得结果 + truncated 标记，绝不等待
```

**(4) 冷启动零成本**

无记忆或无命中时，注入内容必须为空字符串。不得输出"暂无记忆"占位文本，
不得触发任何检索或建库动作。

**(5) 蒸馏零 LLM**

UEAgentKit 的 L0 是带 SHA-256 与 Trust Verdict 的结构化确定性数据，用规则即可提取事实。
**禁止在蒸馏路径调用任何 LLM**。这既是效率要求，也保证产出记录可标记为
`tool-observed` 而非 `model-inferred`。

---

### 任务 M1：效率基线与预算门禁

```text
任务 ID    M1
前置条件   W4、D1
分支       feature/memory-context
预估       3 天
```

**本阶段不写任何记忆功能**，只建立能证明"没有变慢"的标尺。先有标尺，再谈功能。

**交付物**：

```text
scripts/MeasureMemoryOverhead.py
docs/Plans/UEAGENTKIT_M1_MEMORY_OVERHEAD_BASELINE_RESULT_<date>.md
```

脚本要求：

```text
对比 Memory 关闭 / 开启（当前 v3 实现）两种模式
测量三项：启动注入 Token 数、首个 Tool 调用延迟、任务结束耗时
输出确定性 JSON（含环境、样本量、p50/p95）
可复跑，同环境结果稳定
```

**同时修复现存问题**：`ue_memory_get_context` 当前无强制上限，Agent 可能一次拉回过多内容。
补上 4.0 的三重预算。

**改动面**：

```text
scripts/MeasureMemoryOverhead.py           新增
src/ue_agent_kit/memory_context.py         加预算强制
src/ue_agent_kit/mcp_memory_tools.py       get_context 返回 truncated 标记
tests/python/test_memory_context.py        预算测试
```

**验收契约**：

```text
[ ] 基线报告产出，记录当前 v3 的实际开销数值
[ ] ue_memory_get_context 强制 ≤ 800 Token / ≤ 5 条 / ≤ 300 ms
[ ] 超预算返回 truncated 标记而非报错
[ ] 无记忆时返回空字符串，无占位文本
[ ] 脚本纳入门禁（ValidateRelease 可选调用）
[ ] 零新增依赖
```

---

### 任务 M2：L0 自动捕获

```text
任务 ID    M2
前置条件   M1、W4（Change Set 结构冻结）
分支       feature/memory-context
预估       4 天
```

**核心思路：不新建 L0 存储，把已有的确定性日志认定为 L0。**

UEAgentKit 已在写这些产物，质量高于对话日志：

```text
live-write-journal/<receipt>.json     每次实际修改
checkpoints/<checkpointId>.json       保存 + 强验证结果
Change Set                            任务级修改批次
Trust Verdict                         验证结论
Semantic Diff                         语义变更
Impact Analysis                       影响范围
```

M2 只做一件事：这些产物落盘时**追加一条极小索引记录**（append-only，O(1)），
指向已有文件，**不复制内容**。

**Schema 迁移 v3 → v4**（纯加法，不改 v3 的 12 张表）：

本任务是 v4 的**唯一**结构变更。M4 的向量表另行占用 v5，两者不得共用版本号——
否则「v4 数据库」这句话无法确定 `memory_embeddings` 是否存在。

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

**写入点**（已有代码路径上各加一行，D1 拆分后位于 `workflow_*.py`）：

```text
写 live-write journal 后
写 checkpoint record 后
Change Set 状态变更后
Trust Verdict 产出后
```

**关键要求**：失败 / 拒绝 / superseded 路径**同样必须记录**。失败案例是最有价值的
记忆素材（M3 从中提取 knownIssue 与 projectRule）。

**验收契约**：

```text
[ ] Schema v4 迁移脚本幂等，v3 数据库可原地升级
[ ] 一次 W4 批量写入后，L0 事件完整且 outcome 正确
[ ] 失败 / 拒绝 / superseded 路径均被记录
[ ] 单条写入 < 5 ms（实测）
[ ] 任务结束额外耗时 < 100 ms（MeasureMemoryOverhead 验证）
[ ] Memory 关闭时零写入、零开销
[ ] 关闭后重新开启，历史事件不丢
[ ] artifact_path 指向的文件被删除时，查询降级而非崩溃
```

---

### 任务 M3：L0→L1 规则蒸馏

```text
任务 ID    M3
前置条件   M2
分支       feature/memory-context
预估       5 天
```

**离线执行，绝不在任务链路上。** 三种触发方式，都不隐式：

```text
显式命令      ue-agent memory distill
空闲触发      MCP Server 空闲 > 30 s 且有 pending L0 时，后台单批处理
下次启动      启动时 pending 超阈值则后台异步处理，不阻塞首个请求
```

**蒸馏规则**（零 LLM，全部从结构化字段推导）：

| L0 来源 | 提取规则 | 产出 L1 记录类型 |
|---|---|---|
| 成功 live_write + Trust verified | 资产类 + 操作 + 目标 + 生效值 | `projectFact` |
| 失败 / 拒绝 | 拒绝原因码 + 上下文 | `knownIssue` |
| Policy 拒绝 | Policy 规则 + 触发条件 | `projectRule` |
| Semantic Diff | 变更前后语义差异 | `projectFact` |
| Impact Analysis | 消费者集合 | `projectFact` |
| supersession | 被覆盖的旧值链 | `decisionRecord` |

**所有产出记录必须带**：

```text
source        = tool-observed
revision_set  = 按证据类型绑定（见下表，不得一律绑 Asset SHA-256）
node_id       = 按资产路径自动挂树
```

**按证据类型绑定 revision**（关键正确性要求）：

每条事实必须绑定**它自己的真实来源**的版本标识。统一绑 Asset SHA-256 会导致
来源已变的记录仍被判为 `valid`——例如改了 `write-policy.json` 后，从 Policy 拒绝
蒸馏出的 `projectRule` 不会转 stale，记忆库继续给出过期规则。

| L0 来源 | revision 绑定 | 何时转 stale |
|---|---|---|
| live_write / Semantic Diff | 目标资产 Revision | 资产变更 |
| Policy 拒绝 | Policy digest | Policy 文件变更 |
| Impact Analysis | index generation + 相关资产 Revision 集合 | 索引换代或任一相关资产变更 |
| Change Set / checkpoint | checkpoint 或 Change Set 的 revision 集合 | 该集合内任一 revision 变更 |
| P4 观测 | provider 的 observation / head revision（可得时） | 观测过期或 head 前移 |
| supersession | 被覆盖值链两端的 revision | 链上任一端变更 |

`revision_set` 因此是**多元组集合**，不是单个哈希；只要集合内任一元素失配即转 stale。

**自动挂树规则**（避免人工维护 Path）：

```text
/Game/Characters/Hero/DA_HeroStats
  → /project/content/characters/hero
资产目录层级直接映射知识树 Path；节点不存在则自动创建
```

**验收契约**：

```text
[ ] 蒸馏路径零 LLM 调用（代码层无任何模型调用）
[ ] 100 条 L0 蒸馏 < 5 s
[ ] 可中断、可续跑（distilled 标记幂等）
[ ] 重复蒸馏同一 L0 不产生重复 L1（UNIQUE 约束验证）
[ ] 资产 Revision 变化后相关 L1 自动转 stale
[ ] Policy 文件变更后，由 Policy 拒绝蒸馏的 projectRule 自动转 stale
[ ] 索引换代后，Impact Analysis 派生记录自动转 stale
[ ] 六类绑定各有一个「来源变更 → 转 stale」测试用例
[ ] 蒸馏不在任何任务同步链路上（MeasureMemoryOverhead 证明）
[ ] 自动建节点不产生孤儿节点或环
[ ] 六类提取规则各有测试用例
[ ] 代码层不存在由签出 / 锁定历史推导 owner / maintainer 的路径
```

**禁止项**：

```text
不得把任一证据类型统一绑到 Asset SHA-256
不得把观测到的人员签出 / 锁定历史蒸馏为「负责人 / 维护者」持久断言
不得在蒸馏路径引入任何 LLM 调用
不得在任务同步链路上执行蒸馏
```

---

### 任务 M4：混合召回（FTS5 + 向量 + RRF）

```text
任务 ID    M4
前置条件   M3
分支       feature/memory-context
预估       6 天
```

当前只有 FTS5 关键词匹配，"这个材质参数为什么是这个值"这类语义查询召回差。

**零依赖底线**：向量能力可选，缺失时静默降级为纯 FTS5。

```toml
[project.optional-dependencies]
vector = ["sqlite-vec>=0.1,<1", "model2vec>=0.3,<1"]
```

**嵌入模型选择原则（效率优先，不可放宽）**：

```text
必须 CPU 可跑，无 GPU 依赖
模型体积 < 100 MB
单条嵌入 < 10 ms
优先静态嵌入（model2vec 类）
禁止需要完整 transformer 推理的方案
```

理由：记忆条目是短文本，静态嵌入质量足够，速度快一到两个数量级。宁可牺牲少量
召回质量，也不得让蒸馏或查询变慢。

**Schema 迁移 v4 → v5 新增表**：

```sql
CREATE TABLE memory_embeddings (
    record_id      TEXT PRIMARY KEY REFERENCES memory_records(record_id) ON DELETE CASCADE,
    model_id       TEXT NOT NULL,
    dim            INTEGER NOT NULL,
    embedding      BLOB NOT NULL,
    created_at_utc TEXT NOT NULL
);
```

**迁移与可选依赖解耦**（必须遵守）：

```text
v4 → v5 迁移无条件执行并建表，即使 vector extra 未安装
未安装时该表保持为空，检索直接走纯 FTS5
```

理由：schema 版本号必须是关于数据库**结构**的可靠陈述。若建表与依赖安装状态挂钩，
同为 v5 的两个库结构不同，后续迁移无法安全判断起点。

**召回融合（RRF）**：

```text
FTS5 BM25 top-k    → rank_fts
向量余弦 top-k      → rank_vec
score = Σ 1/(60 + rank)
按 score 排序后施加 4.0 的三重预算
```

**嵌入生成契约**（分两条路径，不可混淆）：

```text
蒸馏 / 索引路径   为记录生成并持久化嵌入（写入 memory_embeddings）
查询路径          只为查询文本生成 1 次嵌入；不得重算任何记录嵌入
```

向量检索必须对查询文本求嵌入才能算余弦相似度，因此「查询路径零嵌入生成」是不可
满足的契约，不得写入验收项。真正要守的是**查询路径不重算语料库嵌入**：单次查询的
嵌入调用次数恒为 1（未启用向量时为 0）。

**回填（backfill）**：向量能力启用前已存在的 L1 记录没有嵌入，必须提供确定性、
可续跑的回填路径：

```text
命令        ue-agent memory backfill-embeddings
执行方式    离线 / 空闲，绝不在任务链路上（同 M3 约束）
幂等        已有当前 model_id 嵌入的记录跳过
可中断      按 record_id 稳定排序分批，中断后从断点续跑
model 变更  model_id 不匹配的旧嵌入标记为待重建，不静默混用
```

**验收契约**：

```text
[ ] 未安装 vector extra 时全部功能正常，退化为纯 FTS5
[ ] v4 → v5 迁移在未安装 vector extra 时同样执行并建表
[ ] pyproject dependencies 仍为 []
[ ] 安装后语义查询召回优于纯 FTS5（20 条基准查询对比报告）
[ ] 单次混合召回 < 300 ms（含预算截断）
[ ] 单次查询的嵌入调用次数恒为 1；记录嵌入零重算（代码层 + 计数断言确认）
[ ] 回填可中断、可续跑，重复执行不产生重复嵌入
[ ] 回填不在任何任务同步链路上（MeasureMemoryOverhead 验证）
[ ] 嵌入模型缺失 / 加载失败时静默降级，不抛异常
[ ] model_id 变更时旧嵌入可识别并重建
```

---

### 任务 M5：L2 任务配方 + L3 项目约定

```text
任务 ID    M5
前置条件   M4
分支       feature/memory-context
预估       5 天
```

L2/L3 是**唯一会被注入 prompt 的层**，必须极小、极稳。

**L2 Scenario（任务配方）**：

```text
触发条件    同 operation + 同资产类 成功 ≥ 3 次
产出        ≤ 200 字配方摘要
内容        典型 Plan 形态 + 常见拒绝原因 + 必需前置条件
```

**L3 Persona（项目约定）**：

```text
命名规范      从实际资产路径统计推导
Policy 偏好   从实际 Policy 配置提取
高频错误      从 knownIssue 聚合 Top 3
总量硬上限    ≤ 400 Token，超出按命中频率淘汰
```

**注入契约**（直接决定效率，不可放宽）：

```text
ue_memory_get_context 返回
  ├─ L3 项目约定       ≤ 400 Token   总是返回（稳定，利于 KV-cache）
  ├─ L2 任务域配方     ≤ 400 Token   仅命中时，最多 2 条
  └─ L1/L0            不返回，仅告知可用 ue_memory_search 查询
```

L2/L3 生成同样离线，与 M3 同批次执行。

**验收契约**：

```text
[ ] L3 ≤ 400 Token，L2 单条 ≤ 200 字，合计 ≤ 800 Token
[ ] 注入内容在无新蒸馏时逐字节稳定（哈希比对验证）
[ ] L2 仅在任务域命中时出现
[ ] 冷启动 / 无记忆时返回空字符串
[ ] 启动注入额外延迟 < 200 ms
[ ] L3 超上限时淘汰策略确定（同输入同输出）
```

---

### 任务 M6：符号化上下文压缩（可选）

```text
任务 ID    M6
前置条件   M5、W5
分支       feature/memory-context
预估       4 天
状态       可选，数据驱动
```

**仅当 W5 真实项目测量显示以下 JSON 确实是上下文瓶颈时才实施**：

```text
Impact Analysis 消费者图    → Mermaid graph
Change Set 操作序列        → Mermaid sequence
知识树局部结构             → Mermaid tree
```

**禁止投机优化**。若 W5 数据未显示瓶颈，标记 `deferred by benchmark evidence` 并跳过。

---

### Track M 汇总

```text
M1 效率基线与预算门禁      3 天   必须最先
M2 L0 自动捕获             4 天
M3 L0→L1 规则蒸馏          5 天
M4 混合召回                6 天
M5 L2/L3 与注入契约        5 天
M6 符号化压缩              4 天   可选
                    合计 23-27 天
```

---

## 第 5 部分：任务卡 —— Track C（P4 协作感知）

### 5.0 Track C 立场

沿用 ROADMAP 0.9 既定原则：**首版只分析、提示或阻止，不自动抢锁或覆盖他人修改**。
P4 是团队共享状态，误操作代价远高于本地写入，因此门禁比现有写入更保守。

---

### 任务 C1：只读状态感知

```text
任务 ID    C1
前置条件   W4（C++ 改动已落地，避免 EditorBridge 双线冲突）
分支       feature/source-control-p4
预估       4 天
```

**实现方式**：C++ 侧走 UE 内置 `ISourceControlModule`，**不直接调 `p4.exe`**。
理由：凭据管理与工作区解析 UE 已经解决过，自行调用会重复引入这些问题。

**C1-0 能力探测（先做，未完成不得冻结 schema）**：

下列字段并非都能从通用 `ISourceControlState` 稳定取得——部分属于 Perforce 特定
实现，部分需先执行带 history 的 `FUpdateStatus` 才有值。用未核实的字段冻结公开
schema，后果是要么破坏性改版，要么字段静默返回 null。

先对 UE5.6 + Perforce 做一次最小探测，逐字段判定：

```text
checkedOutBy      IsCheckedOutOther(FString* Who) —— 预期通用，待确认
locked / lockedBy 预期部分依赖 provider，待确认
depotPath         预期 Perforce 特定，待确认
headRevision      待确认是否需 FUpdateStatus + history
haveRevision      待确认是否需 FUpdateStatus + history
changelist        预期 Perforce 特定，待确认
```

每个字段判定为三档之一：

```text
generic     通用 ISourceControlState 可得 → 进入公开 schema
provider    仅 Perforce 可得              → 进入 schema，但标注 provider 限定
unavailable 探测环境无法确认              → 进入 schema 但声明为显式可空，
                                            并在文档写明未确认原因
```

**有界出口**：若手边没有多人 P4 环境，不得无限期挂起 C1。无法确认的字段按
`unavailable` 处理、显式可空发布即可，后续在真实环境补测再收紧。

**新增 EditorBridge handler**（字段集以 C1-0 探测结果为准，下列为待确认草案）：

```text
getSourceControlStatus
  输入  assetPaths[]（有界，≤ 100）
  输出  provider / enabled / 每资产：
        { depotPath, checkedOut, checkedOutBy, locked, lockedBy,
          headRevision, haveRevision, isUpToDate, isAdded, isDeleted }
  说明  每个字段必须标注 generic / provider / nullable 三档来源
```

**新增 MCP Tool**：

```text
ue_get_source_control_status    有界批量查询
ue_get_asset_checkout_state     单资产签出 / 锁定状态（如实返回 P4 当前状态，
                                不做责任归属推断）
```

**改动面**：

```text
Plugin/.../Private/EditorBridgeSourceControlHandlers.cpp   新增
Plugin/.../Private/EditorBridge.{h,cpp}                    注册 handler
src/ue_agent_kit/editor_bridge.py                          客户端方法
src/ue_agent_kit/mcp_live_tools.py                         2 个新 Tool
tests/python/test_source_control.py                        新增
```

**禁止项**：

```text
不做任何 P4 写操作（checkout / submit / revert / lock 一律不实现）
不直接调用 p4.exe
不引入新依赖
```

**验收契约**：

```text
[ ] C1-0 探测完成，每个字段判定为 generic / provider / unavailable 之一
[ ] schema 在探测完成后才冻结，nullable 字段各有文档化理由
[ ] P4 未启用 / 未连接时明确返回 disabled，不报错、不挂起
[ ] 查询 100 资产 < 2 s
[ ] 他人签出 / 锁定状态正确反映（真实多用户 P4 环境验证）
[ ] 代码层不存在任何 P4 写操作路径
[ ] 非 P4 项目行为完全不变
[ ] UE5.6 Direct Build 通过
[ ] test_tool_registry 反映 +2 Tool
```

**证据要求**：C1-0 字段判定表 + 真实 P4 环境下他人签出资产的查询输出 + Direct Build 日志。
若探测环境不具备多人 P4，须在结果文档明确列出未确认字段及原因。

---

### 任务 C2：写入前冲突预检

```text
任务 ID    C2
前置条件   C1
分支       feature/source-control-p4
预估       5 天
```

把 P4 状态接入既有写入门禁链，作为新的 fail-closed 条件。

**插入位置**：

```text
Plan → Policy → Revision → 【新增 P4 Preflight】 → Live Apply
```

**预检规则（全部 fail-closed）**：

```text
他人锁定        → 拒绝  source-control-locked
他人签出        → 拒绝  source-control-checked-out-by-other
本地非最新      → 拒绝  source-control-out-of-date
未签出且只读    → 拒绝  source-control-not-checked-out（不自动签出）
P4 不可用       → 按 Policy 的 allowWhenProviderUnavailable 决定
```

**Policy 新增字段**：

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

`autoCheckout` 保留字段但**首版不实现 `true` 分支**；传 `true` 时必须显式报
`not-implemented` 而非静默忽略。

**默认值要求**：`preflightEnabled` 对既有 Policy 缺省为 `false`，保证现有项目行为不变。

**验收契约**：

```text
[ ] 五类拒绝路径各有真实 P4 环境验证
[ ] 拒绝时零写入、零 Dirty（Revision 与 Dirty 状态独立确认）
[ ] autoCheckout=true 显式报 not-implemented
[ ] 预检额外延迟 < 500 ms
[ ] 既有非 P4 项目行为完全不变（回归现有写入测试全绿）
[ ] Policy JSON Schema 同步更新并纳入门禁
```

---

### 任务 C3：变更关联与审计

```text
任务 ID    C3
前置条件   C2
分支       feature/source-control-p4
预估       3 天
```

把 AI 修改关联到 P4 Changelist，供人工 Review。

```text
ue_get_changelist_context    读取当前 pending changelist 及其文件
Change Set ↔ Changelist      在 Change Set 记录中登记 changelist 号
Backup Manifest 扩展         增加 depotPath / headRevision 字段
```

**禁止项**：**不实现自动 Submit**。Submit 不可逆且影响团队，永远由人执行。
代码层不得存在 submit / revert 路径。

**验收契约**：

```text
[ ] Change Set 可反查对应 P4 changelist
[ ] Backup Manifest 含 depot 信息，旧 Manifest 仍可读（向后兼容）
[ ] 代码层无任何自动 submit / revert
[ ] Manifest schema 版本递增并写入 CHANGELOG
```

---

### 任务 C4：Memory 联动

```text
任务 ID    C4
前置条件   C3、M2
分支       feature/source-control-p4
预估       2 天
```

P4 状态是高价值 L0 事件，接入 Track M：

```text
冲突拒绝        → knownIssue：某资产在并发写入下曾被锁定拒绝（记录资产与拒绝码）
签出频次统计    → projectFact：某目录的变更活跃度（不含人员断言）
```

**人员归属的硬边界**（不可放宽）：

观测到的签出 / 锁定历史**只能作为事实观测存储，不得蒸馏为「某人是某目录的负责人 /
维护者」这类持久断言**。理由有两条，任一成立即足以禁止：

```text
1  分级违规  由历史频次推断责任归属属于 model-inferred 结论，
             存成 tool-observed 会污染整个 source 分级的可信度
2  数据边界  会把记忆库变成个人活动记录，这不是本项目的用途
```

责任归属只允许来自三种来源，且必须显式标注来源：

```text
项目配置文件中的显式声明
团队规则文档
用户确认（source = user-confirmed）
```

**验收契约**：

```text
[ ] P4 拒绝事件进入 memory_l0_events，event_kind 明确
[ ] 蒸馏产出 knownIssue，source = tool-observed
[ ] 代码层不存在由签出/锁定历史推导 owner / maintainer 的路径
[ ] 人员相关字段仅作为观测事实存储，不进入 projectRule 或长期断言
[ ] Memory 关闭时 C1-C3 功能不受影响
```

### Track C 汇总

```text
C1 只读状态感知        4 天
C2 写入前冲突预检      5 天
C3 变更关联与审计      3 天
C4 Memory 联动         2 天
                合计 14 天
```

---

## 第 6 部分：任务卡 —— Track V（知识库 Web 只读）

### 6.0 Track V 的永久架构约束

项目所有者明确要求：

> 知识库界面不能用人工直接修改，只能 Agent 来修改。

因此 Web 界面是**严格只读**的。这不是阶段性妥协，而是永久约束：

```text
后端只开放 GET，不实现任何 POST / PUT / DELETE
SQLite 以只读模式打开（file:...?mode=ro）
需要修改时，界面提示"请让 Agent 执行"并给出建议的 Agent 指令文本
```

该约束反而简化实现：无需鉴权写入、无需并发控制、无需事务冲突处理。

---

### 任务 V1：本地只读浏览器

```text
任务 ID    V1
前置条件   无（可立即与 W4 并行，零冲突）
分支       feature/knowledge-web-view
预估       6 天
```

**技术选型（零新增运行时依赖）**：

```text
后端    Python 标准库 http.server + sqlite3（mode=ro）
        禁止引入 fastapi / uvicorn / starlette
前端    单个静态 HTML + 原生 JS，无构建步骤、无 npm
启动    ue-agent knowledge-view --port 8765
绑定    仅 127.0.0.1
```

选标准库而非 FastAPI 的理由：项目 `dependencies = []`，Web 浏览是辅助功能，
不值得为它引入 ASGI 栈。只读 JSON 接口用 `http.server` 完全够用。

**安全要求**：

```text
仅绑定 127.0.0.1，不监听 0.0.0.0
文档明确标注为本地开发工具，不应暴露到网络
默认不启用鉴权（本地 + 只读 + 无写入面，风险可接受）
路径穿越防护：所有路由白名单匹配，不做文件系统直读
```

**页面（四视图）**：

```text
知识树        左树 + 右详情，展开节点看挂载记录
记录列表      按 type / status / source 筛选，显示 stale 标记
Active Work   当前目标 / TODO / 阻塞 / 下一步
Evidence      单条记录证据链，指向 receipt / checkpoint / diff
```

**只读 API**：

```text
GET /api/tree                     知识树结构
GET /api/node/<node_id>           节点详情 + 挂载记录
GET /api/records?type=&status=    记录列表（分页）
GET /api/record/<record_id>       记录详情 + Evidence
GET /api/work                     Active Work
GET /api/status                   Memory 状态摘要
```

**改动面**：

```text
src/ue_agent_kit/knowledge_view.py         新增（HTTP server + 只读查询）
src/ue_agent_kit/web/index.html            新增（单文件前端）
src/ue_agent_kit/cli.py                    新增 knowledge-view 子命令
tests/python/test_knowledge_view.py        新增
```

**验收契约**：

```text
[ ] 数据库以 mode=ro 打开；代码层不存在任何写入 SQL
[ ] 仅监听 127.0.0.1（绑定地址有测试断言）
[ ] pyproject dependencies 保持 []
[ ] 无 npm / 无构建步骤
[ ] Memory 数据库不存在时给出清晰提示而非崩溃
[ ] stale / conflicted / superseded 有明确视觉区分
[ ] 界面任何位置都不提供编辑入口
[ ] 未知路由返回 404，不泄露文件系统信息
[ ] 中文路径与 Unicode 内容正确显示
```

---

### 任务 V2：可视化分析面板

```text
任务 ID    V2
前置条件   V1
分支       feature/knowledge-web-view
预估       8 天
```

在只读前提下增加分析视图：

```text
资产引用图      Asset → Asset 依赖，力导向图，可下钻
影响范围图      选中资产，高亮消费者（复用 Impact Analysis 数据）
知识覆盖热图    哪些资产目录有记忆、哪些是盲区
变更时间线      Change Set 时序 + Trust 结果
stale 分布      哪些知识因资产变更失效，按目录聚合
```

**图形库要求**：单文件可 vendored 的轻量库（如 d3 单文件构建），不引入 npm。
vendored 文件需在 `docs/REFERENCE_POLICY.md` 或 NOTICE 中登记来源与许可。

**验收契约**：

```text
[ ] 5000 节点引用图可交互，不卡死（实测帧率报告）
[ ] 图数据全部来自现有 SQLite，不新增导出步骤
[ ] 仍然严格只读
[ ] vendored 库来源与许可已登记
[ ] 大项目下分页/裁剪生效，不一次性拉全图
```

### Track V 汇总

```text
V1 本地只读浏览器      6 天
V2 可视化分析面板      8 天
                合计 14 天
```

---

## 第 7 部分：任务卡 —— Track X（中期新能力）

### 7.0 Track X 的启动前提与既有立场冲突说明

**执行 Agent 必须先读这一节。**

现行 ROADMAP 与 Post-0.8 计划把 Blueprint Graph CRUD、Level Actor 通用 CRUD 明确定为
**由真实需求或 Benchmark 失败数据驱动**，而非按计划推进：

> Blueprint Graph、Level Actor 等新 Writer 改为由 Reforge 真实需求或
> Agent Benchmark 失败数据驱动。

本规格**不推翻该立场**。Track X 的定位是：

```text
读取能力扩展（X1 / X2 / X4 / X5）  可按计划推进，风险低，无写入面
写入能力扩展（X3）                 必须先有需求证据，不得直接开工
```

因此 X3 设了显式的**需求门禁**：无证据不开工。这不是流程形式，而是避免把
数月工作量投在没人用的能力上。

### 7.1 Track X 共同约束

```text
分支     feature/asset-coverage-expansion
基线     从 D1 完成点切出（拆分后的 workflow_*.py）
前置     W4、D1 全部完成（改动面与 Track W 重叠严重）
```

---

### 任务 X1：Widget Blueprint 深度读取

```text
任务 ID    X1
前置条件   W4、D1
分支       feature/asset-coverage-expansion
预估       6 天
```

**现状**：`AssetReaders/` 下仅有 Animation / Material / Mesh / Niagara / World，
Widget Blueprint 只能拿到通用 Asset Registry 记录，无法回答 UI 结构问题。

**新增 Reader 输出**：

```text
Widget Tree        层级结构、每节点 Widget Class、Slot 类型
Slot 属性          Anchors / Alignment / Offsets / ZOrder / Padding
Binding            属性绑定到的函数或变量
Named Slot         命名插槽及其填充
Animation          Widget Animation 名称、时长、轨道目标摘要
Event              OnClicked 等事件绑定的函数名
```

**实现要求**：

```text
新增 Plugin/.../AssetReaders/WidgetBlueprintAssetReader.cpp
在 AssetReaderRegistry 注册 WidgetBlueprint / UserWidget
沿用现有 Reader 的稳定排序与 canonical JSON 约定
不读取运行时状态，不实例化 Widget
Blueprint Graph 部分复用既有 Blueprint 导出，不重复实现
```

**验收契约**：

```text
[ ] 真实 Widget Blueprint 导出结构完整，含嵌套容器
[ ] 输出稳定排序，同资产两次导出逐字节一致
[ ] 未知 Widget Class 安全回退，不崩溃
[ ] 不实例化 Widget、不触发 Construct
[ ] canonical schema 版本递增并更新 JSON Schema
[ ] SQLite 索引可检索 Widget 节点
[ ] UE5.6 Direct Build 通过
```

---

### 任务 X2：Anim Blueprint 状态机读取

```text
任务 ID    X2
前置条件   X1
分支       feature/asset-coverage-expansion
预估       6 天
```

**现状**：Anim Sequence / Montage / BlendSpace 已有 Reader，但 Anim Blueprint 的
State Machine 完全没有导出，无法回答"这个动画状态怎么切换"。

**新增输出**：

```text
State Machine     状态列表、Entry State、每状态的 Animation 资产引用
Transition        源/目标状态、条件表达式摘要、Blend 时长、优先级
Anim Graph        节点类型摘要（复用 Blueprint Graph 导出）
Blend Node        Layered Blend / Blend by Bool / Blend by Int 参数
Notify            State 上的 Notify 绑定
Variable          Anim Blueprint 暴露的变量（驱动状态切换的输入）
```

**验收契约**：

```text
[ ] 真实 Anim Blueprint 的 State Machine 与 Transition 完整导出
[ ] Transition 条件以稳定文本摘要表示，不求完整表达式求值
[ ] 多层 State Machine（嵌套）正确导出
[ ] 输出稳定排序
[ ] 不编译、不实例化 AnimInstance
[ ] canonical schema 版本递增
```

---

### 任务 X3：Blueprint Graph 结构写入（需求门禁）

```text
任务 ID    X3
前置条件   W4、D1、X1、X2、且【需求证据门禁通过】
分支       feature/asset-coverage-expansion
预估       15 天（高风险，估值不确定性大）
```

#### 需求证据门禁（不通过则不开工）

开工前必须在计划文档中登记以下证据之一：

```text
证据 A   Reforge 真实工程中出现 ≥ 3 个明确需要 Graph 结构修改的任务，
         且已记录任务描述与当前的人工替代成本
证据 B   Agent Benchmark 中出现 ≥ 3 次因缺少 Graph 写入而失败的案例，
         附 R4 风格的确定性失败记录
```

**无证据时的正确动作**：在 ROADMAP 标记
`X3 deferred pending real-project demand evidence`，转而执行 X4 / X5。

#### 若门禁通过，实施范围（严格有界）

**首版只做三个最小操作，不做通用 CRUD**：

```text
addNode        仅限白名单节点类型（CallFunction / VariableGet / VariableSet）
connectPins    仅限类型兼容的既有 Pin
deleteNode     仅限本次会话内由 addNode 创建的节点
```

**明确排除**：

```text
任意节点类型创建
宏 / 折叠图 / 事件图结构变更
Construction Script 结构变更
删除既有（非本次创建的）节点
跨 Graph 移动节点
Anim Graph / Material Graph 结构写入
```

**必须复用的既有安全设施**（不得新建平行机制）：

```text
Policy 白名单        新增 graphOperations 白名单，默认空
Revision 校验        沿用现有 SHA-256 门禁
Snapshot / Undo      沿用 W1 的 Blueprint 常驻写入 + post-compile 目标重解析
                     注意：W1 曾因 compile 后持有失效指针导致 Editor crash，
                     Graph 写入的对象生命周期问题更严重，必须逐操作重解析
Compile 门禁         编译失败必须恢复到 exact pre-write state
Fast / Strong Verify 沿用 W2 / W3
Semantic Diff        新增 Graph 结构 diff（节点/连线增删）
Trust Verdict        沿用 R3
```

**高风险提示（执行 Agent 必须重视）**：

```text
Blueprint compile 会重建 GeneratedClass / CDO / FProperty / UEdGraphNode / UEdGraphPin。
W1 的真实 crash 正源于此。Graph 写入持有的对象更多、生命周期更复杂。
任何跨 compile 边界的裸指针持有都是 crash 源。
必须使用稳定标识（GraphGuid / NodeGuid / PinName）+ 每次访问前重解析。
```

**验收契约**：

```text
[ ] 需求证据门禁已登记并通过
[ ] 三个操作各有真实 UE5.6 Dry Run / Commit / reload / rollback 全链验证
[ ] compile 失败可恢复到 exact pre-write state（含 Undo 后 Editor 不崩溃）
[ ] 连续 10 次 addNode + connectPins + Undo 循环，Editor 无崩溃
[ ] 类型不兼容连线被拒绝，零写入
[ ] Policy graphOperations 默认空，未授权即拒绝
[ ] Graph Semantic Diff 正确反映结构变更
[ ] 白名单外节点类型被拒绝
[ ] Trust Verdict 在全链通过时为 verified
```

**若中途发现风险不可控**：允许缩小到只支持 `setPinDefault` 已有能力 +
`connectPins`，并在结果文档记录 `addNode deferred`。**不允许带着 crash 风险交付**。

---

### 任务 X4：Level Actor 只读增强

```text
任务 ID    X4
前置条件   W4、D1
分支       feature/asset-coverage-expansion
预估       7 天
```

**现状**：World Reader 已输出 Persistent Level、World Settings、Streaming/World Partition、
Actor/Component 类别计数与有界明细，且在 Actor Descriptor 元数据可用时只读输出外部 Actor 摘要。
**缺的是按需查询单个 Actor 的详情**。

**新增能力（纯只读，不写入）**：

```text
ue_get_level_actors        有界分页列出 Actor（按 Class / 名称 / 标签筛选）
ue_get_actor_detail        单 Actor：Transform、Component 树、暴露属性、引用资产
ue_find_actors_by_asset    反查：哪些 Actor 引用了指定资产
```

**约束**：

```text
不主动加载未加载的外部 Actor（World Partition 场景）
不触发 BeginPlay
不修改任何 Actor
有界返回（默认 ≤ 100，硬上限 500）
未加载 Actor 明确标记 not-loaded，不静默省略
```

**验收契约**：

```text
[ ] World Partition 工程中不触发外部 Actor 加载（实测内存与加载计数）
[ ] 未加载 Actor 明确标记
[ ] 分页稳定（同一查询两次结果顺序一致）
[ ] 反查功能正确（与 SQLite 引用索引交叉验证）
[ ] 大关卡（≥ 5000 Actor）查询 < 3 s
[ ] 无任何写入路径
```

**关于 Actor 写入**：与 X3 同理，需求驱动。首版**不实现** spawn / destroy /
setActorProperty。Actor 写入牵涉关卡保存、World Partition 外部 Actor 文件、
Actor 间引用完整性，风险高于资产属性写入，应在 X4 只读能力被真实使用后再评估。

---

### 任务 X5：C++ 符号只读索引

```text
任务 ID    X5
前置条件   W4、D1
分支       feature/asset-coverage-expansion
预估       10 天
```

**现状**：完全无 C++ 理解能力。Agent 无法回答"这个 Blueprint 的父类 C++ 里有什么函数"。

**范围界定（关键：不做完整 C++ 解析）**：

```text
做      项目 Source/ 下的 UCLASS / USTRUCT / UENUM / UFUNCTION / UPROPERTY
        反射宏声明的符号 + 其所在文件行号
        C++ 类 → Blueprint 子类的关联
不做    完整 C++ 语法树
不做    模板推导 / 宏展开
不做    引擎源码索引（体量过大，且用户通常不改引擎）
不做    函数体分析 / 调用图
```

理由：Agent 的实际需求是"这个类暴露了什么给 Blueprint"，反射宏声明已覆盖 95% 场景。
完整 C++ 解析需要 libclang（重依赖）且收益不成比例。

**实现方式（零依赖）**：

```text
正则 + 括号配对的轻量扫描器，不引入 libclang
仅解析 UE 反射宏的规范写法
遇到无法解析的构造：跳过并记录 unparsed，不猜测、不报错
输出进 SQLite，与现有 Symbol 表同构
```

**新增 Tool**：

```text
ue_search_cpp_symbols       搜索 C++ 类 / 函数 / 属性
ue_get_cpp_class_detail     单类详情：基类、反射函数、反射属性、文件位置
ue_get_blueprint_native_parent  Blueprint → 其 C++ 父类的反射面
```

**验收契约**：

```text
[ ] 零新增依赖（无 libclang）
[ ] Reforge 项目 Source/ 全量扫描 < 30 s
[ ] UCLASS / USTRUCT / UENUM / UFUNCTION / UPROPERTY 正确识别
[ ] 无法解析的构造记入 unparsed 列表，不静默丢弃、不报错
[ ] Blueprint → C++ 父类关联正确
[ ] 文件行号准确（可点击定位）
[ ] 不索引引擎源码（除显式配置）
[ ] 增量扫描：仅重扫变更文件
```

---

### 任务 X6：资产性能分析（只读）

```text
任务 ID    X6
前置条件   X4
分支       feature/asset-coverage-expansion
预估       8 天
```

**现状**：有依赖追踪但无性能视角。Agent 无法回答"哪些资产占内存最多"。

**新增能力（全部基于已有元数据，不做运行时 profiling）**：

```text
ue_analyze_texture_memory     Texture 尺寸/格式/Mip → 估算显存占用，按目录聚合
ue_analyze_mesh_complexity    三角数 / LOD 数 / 材质槽数 / Nanite 状态
ue_analyze_material_cost      Expression 数量 / 采样器数 / Shading Model 复杂度
ue_find_asset_outliers        异常检测：超大贴图、无 LOD 的高模、过多材质槽
```

**明确不做**：

```text
不做运行时 Draw Call 统计（需要 PIE 与渲染线程介入，超出只读边界）
不做 GPU profiling
不做 Shader 编译耗时分析
不给出"应该改成什么"的建议（那是 Agent 基于数据的判断，不是工具的职责）
```

**验收契约**：

```text
[ ] 估算值与 UE Size Map 同量级（抽样 20 资产对比，误差 < 20%）
[ ] 估算方法在文档中写明公式与假设，不伪装为精确值
[ ] 大项目（50k 资产）分析 < 60 s
[ ] 全部基于已有索引，不重新加载资产
[ ] outlier 阈值可配置，默认值有依据说明
```

### Track X 汇总

```text
X1 Widget Blueprint 读取       6 天
X2 Anim Blueprint 状态机       6 天
X3 Blueprint Graph 写入       15 天  ← 需求门禁，可能 deferred
X4 Level Actor 只读增强        7 天
X5 C++ 符号只读索引           10 天
X6 资产性能分析                8 天
                        合计 37-52 天
```

---

## 第 8 部分：横向维护任务

### 任务 D2：Tool 计数单一来源

```text
任务 ID    D2
前置条件   W4
分支       随任意 Track 附带
预估       1 天
```

当前 `105 / 93 / 60 / 43 / 22 / 10` 等计数散落在 README、README_EN、ROADMAP 与测试中，
每次增删 Tool 都要手改多处，已出现不一致风险。

```text
改为从注册表运行时导出为 JSON
文档引用生成结果，或由 ValidateRelease 校验文档数字与实际一致
```

**验收契约**：

```text
[ ] 计数有单一权威来源
[ ] ValidateRelease 校验文档数字与运行时一致，不一致则失败
[ ] 现有文档数字全部核对更新
```

### 任务 D3：UE Build CI

```text
任务 ID    D3
前置条件   无（建议在 Track C / X 之前）
分支       独立
预估       3 天
```

当前 UE5.6 编译只在本地发布机执行，C++ 改动累积后才发现问题。Track C 与 X 都会大量
改动 C++，D3 应先行。

**验收契约**：

```text
[ ] 有引擎环境的机器上可定时或按需触发 Direct Build
[ ] 失败时产出可读的编译错误摘要
[ ] 不要求 GitHub Actions（引擎许可限制），本地/自建 runner 即可
```

### 任务 D4：API 参考文档

```text
任务 ID    D4
前置条件   D2
分支       随 Track V 附带
预估       2 天
```

从 MCP 注册表自动生成工具参考，按场景分组（查询 / 修改 / 验证 / 记忆 / 协作）。
可与 V1 合并交付：Web 界面顺带提供工具浏览页。

**验收契约**：

```text
[ ] 工具参考自动生成，不手写维护
[ ] 按场景分组，每组有一句话使用指引
[ ] 新用户可在 30 分钟内完成首次查询 + 首次 Dry Run
```

---

## 第 9 部分：总排期与依赖

### 9.1 依赖图

依赖图以各任务卡的「前置条件」字段为唯一权威来源。图与卡冲突时以卡为准，
并须回来修正本图。

**主线与 Track M / C**：

```text
V1 只读浏览器  6d   ← 无前置，可立即开工
 │
 ▼
V2 可视化面板  8d

T0  W3 收口 checkpoint  complete (`45e6ea2`)
 │
 ▼
W4  多操作/有界批量  24d
 │  （含 Change Set 结构冻结）
 │
 ├─────────────┬─────────────────────────────┐
 ▼             ▼                             ▼
D1 拆分        C1 P4 只读  4d            D2 文档  ─▶ D4
 agent_workflow   │                       （见下）
 3d               ▼
 │             C2 冲突预检  5d
 │                │
 ├──────┐         ▼
 ▼      ▼      C3 变更审计  3d
W5     M1         │
真实   效率       │
验收   基线       │
10d    3d         │
 │      │         │
 │      ▼         │
 │   M2 L0 捕获 ◄─┘（C4 需 M2）
 │      4d
 │      │
 │      ▼
 │   M3 L1 蒸馏  5d
 │      │
 │      ▼
 │   M4 混合召回  6d
 │      │
 │      ▼
 │   M5 L2/L3  5d  ◄── C4 P4 联动  2d（前置 C3 + M2）
 │      │
 └──────┴──▶ M6 符号化  4d（可选，前置 M5 + W5）
```

**Track X**（三条独立分支，均只依赖 W4 + D1，不得串行化）：

```text
W4 + D1
 │
 ├─▶ X1 Widget BP  6d ──▶ X2 Anim BP  6d ──▶ X3 Graph 写入  15d
 │                                            （需求证据门禁，可能 deferred）
 │
 ├─▶ X4 Actor 只读  7d ──▶ X6 性能分析  8d
 │
 └─▶ X5 C++ 索引  10d
```

X1、X4、X5 之间**无技术依赖**，可同时开工或任意顺序推进。若发现新的技术依赖，
先更新任务卡的前置条件，再更新本图。

**D 线**：

```text
D3 UE Build CI  ← 无前置，建议先于 Track C / X
D2 文档收口     ← 前置 W4
D4 工具参考     ← 前置 D2
```

### 9.2 关键依赖说明

```text
W4 → D1        W4 加完编排再拆分，避免冲突
W4 → M2        L0 蒸馏源必须先冻结，否则 Track M 被反复破坏
D1 → M/C/X     在拆分后模块上开发，避免大文件冲突
M2 → C4        P4 事件需要 L0 通道
W5 → M6        M6 需要 W5 的真实验收数据作为符号化输入
D3 → C/X       C++ 改动量大，CI 应先行
V1/V2 独立     全程可并行，V1 无任何前置
X1/X4/X5 并列  三者互不依赖，串行化会白等约 12 天
X3 有门禁      无需求证据不开工
```

### 9.3 里程碑

```text
里程碑 1  T0 + W4 + V1                     ≈ 4 周
          Agent 可做多操作任务，知识库可视化

里程碑 2  D1 + M1-M3 + C1 + X1             ≈ 7 周
          记忆开始自动积累，P4 状态可见，Widget BP 可读

里程碑 3  M4-M5 + C2-C3 + V2 + X2/X4       ≈ 11 周
          混合召回可用，协作安全，分析面板可用

里程碑 4  W5 + X5/X6 + D2-D4               ≈ 15 周
          规模验收完成，C++ 与性能分析可用，文档收口

里程碑 5  0.9 发布评审（独立授权）
```

工期按单人 AI 辅助开发估算。**四条 Track 并行不意味人力并行**，而是遇阻塞时可切换
另一条线不空等——这是多 worktree 的实际收益。

### 9.4 建议的 Agent 分派

若同时投入多个执行 Agent：

```text
Agent 1   Track W（W4 → D1 → W5）           主线；T0 已完成
Agent 2   Track V（V1 → V2 → D4）           完全独立，可立即开工
Agent 3   Track M（W4 后：M1 → M6）          需等 W4
Agent 4   Track C（W4 后：D3 → C1 → C4）     需等 W4 的 C++ 落地
Agent 5   Track X 分支 A（D1 后：X1 → X2 → X3）
Agent 6   Track X 分支 B（D1 后：X4 → X6）    与分支 A 无依赖
Agent 7   Track X 分支 C（D1 后：X5）         与 A / B 均无依赖
```

**跨 Agent 协调要求**：

```text
每个 Agent 只在自己分支提交，不跨分支改动
需要对方产出时，等其 checkpoint 提交后 merge，不 Rebase
共享文件（CHANGELOG / ROADMAP / README）的改动集中到里程碑收口时统一处理，
  避免多分支同时改同一文档造成频繁冲突
```

---

## 第 10 部分：全局验收与交接

### 10.1 每个任务的收尾清单

```text
[ ] 验收契约逐项标注 PASS / FAIL / blocked / deferred，无空项
[ ] 结果文档 docs/Plans/UEAGENTKIT_<任务ID>_..._RESULT_<date>.md 已产出
[ ] 门禁命令输出已附在结果文档
[ ] 触碰 C++ 则附 Direct Build 日志尾部
[ ] 触碰写入则附真实 UE 前后 Revision 与 Trust Verdict
[ ] 提交边界符合 0.2 纪律
[ ] published version 未变，无 Tag，无 Push
```

### 10.2 禁止的收尾方式

```text
禁止  在结果文档写"应该可以""预计通过""理论上正确"
禁止  把未验证项标为 PASS
禁止  为了通过验收而放宽验收契约本身
禁止  把 blocked 问题隐藏在"已知限制"里而不显式标注
```

若某项无法通过，正确做法是标注 `blocked` + 写明具体阻塞原因 + 给出下一步诊断方向，
参考 W1 曾经的 `record W1 acceptance blocked status` 提交做法。

### 10.3 交回复核时需提供

项目所有者会在完成后复核。请提供：

```text
1. 各任务结果文档路径清单
2. 完整 git log --oneline（含分支）
3. 门禁命令的最终输出
4. 真实 UE 验收的证据摘要（Revision / Trust / 崩溃与否）
5. 未完成项清单，含 blocked / deferred 原因
6. 与本规格的偏差清单（若有），含偏差理由
```

### 10.4 需要项目所有者决策的事项

以下事项执行 Agent **不得自行决定**，须停下询问：

```text
X3 需求证据门禁是否放行
是否启动正式 0.8 / 0.9 package release
是否放宽任何安全边界（0.5 节）
是否引入 optional 之外的新依赖
是否修改 published version / 建 Tag / Push
Memory 效率门禁（4.0）任一指标超标时是否接受
发现本规格与既有 ROADMAP 立场冲突时的取舍
```

---

## 第 11 部分：风险登记

| 风险 | 影响 | 对策 | 负责任务 |
|---|---|---|---|
| Memory 拖慢任务（所有者已实际遇到） | 工具不可用 | M1 先建门禁；超标即 blocked；蒸馏零 LLM 全异步 | M1 |
| W4 批量放大 recovery 复杂度 | 部分应用状态难恢复 | 沿用 W4 计划显式 partial 边界；不声明跨包原子性 | W4 |
| Blueprint Graph 写入 crash | Editor 崩溃，数据风险 | 需求门禁 + 稳定标识重解析 + 允许缩小范围 | X3 |
| 向量依赖破坏零依赖底线 | 安装复杂化 | optional-dependencies + 静默降级 | M4 |
| P4 误操作影响团队 | 代价高于本地写入 | 首版纯只读 + fail-closed；无自动 submit/checkout | C1-C3 |
| agent_workflow.py 继续膨胀 | 维护失控 | D1 在 W4 后强制执行，不可延后 | D1 |
| 五 Track 并行冲突 | 合并困难 | 按 2.3 排期；共享文档集中收口 | 全部 |
| 自动记忆写入噪声 | 知识库污染 | 只用 tool-observed；UNIQUE 去重；stale 自动失效 | M2-M3 |
| C++ 扫描器误判 | 索引错误 | 明确不做完整解析；标注置信度；解析失败跳过不猜 | X5 |
| 性能估算被误当精确值 | 错误优化决策 | 文档写明公式与假设；命名含 estimate | X6 |
| 中期能力与既有 ROADMAP 立场冲突 | 方向摇摆 | 7.0 已声明：读取按计划，写入需证据 | X |

---

## 第 12 部分：立即可执行的入口

```text
1. 启动 W4-0：Contract Freeze and Baseline
2. W4 的阶段、失败边界与真实 UE C1-C12 以 W4 Detailed Plan 为唯一实现权威
3. 其他 Track 仅在需要切换主线或满足前置条件时启动；T0 不再需要执行
```

Track M 的实现不要在 W4 完成前开始——L0 蒸馏源会变。但设计文档可现在写，
W4 一结束即可进入 M1。

