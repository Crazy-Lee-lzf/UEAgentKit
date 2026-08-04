# UE Agent Kit 大型项目性能测试方案

更新时间：2026-08-04

## 1. 目标

本方案面向一个典型的大型 Unreal Engine 5 商业游戏开发环境：开发仓库约 500 GB、Cooked 发布内容约 30 GB、资产数量达到数十万并使用 World Partition/External Actors。物理测试工程实际存放在 E 盘高速 SSD 上；性能验收同时运行原生 SSD 档位和机械硬盘兼容模拟档位，后者按 50 MB/s 顺序读写上限及小文件寻道延迟建模。

性能目标分为两类：

1. 首次建立资产索引、Revision 和 Project Memory 可以耗时较长，只要过程可恢复、可取消、可观察，不影响后续日常使用。
2. 日常交互必须接近修改代码的体验。查询资产、修改变量、修改少量 Blueprint 节点、编译、Undo 和保存单个普通资产，不得隐式触发全项目扫描、完整 SHA、全量引用重建或独立 Editor 重载。

本方案中的时间分为三种：

- **SSD 原生实测值**：测试工程在 E 盘 SSD 上直接运行，由测试工具记录真实时间。
- **HDD 模拟实测值**：在测试专用 I/O Governor 下运行，限制有效吞吐并注入可重复的小文件寻道延迟。
- **性能目标**：UE Agent Kit 应达到的 p95 交互上限。
- **容量推算**：按 50 MB/s 顺序读写上限及小文件寻道模型估算，不作为真实机械硬盘实测结果。

## 2. 核心原则

### 2.1 交互与批处理分离

交互操作包括：

- 搜索资产、Symbol、Reference 和 Memory。
- 获取当前 Editor Context。
- 修改一个变量或属性。
- 修改 1–5 个 Blueprint 节点或 Pin 连接。
- 编译一个普通 Blueprint。
- Undo、Discard、保存单个资产。

这些操作必须直接返回结果，或在 300 ms 内返回明确的 Task/Change Set 句柄。它们不能等待项目级扫描完成。

批处理包括：

- 首次全量资产目录与 Revision 建立。
- 大范围引用重建。
- 数百或数千资产批量修改。
- 大型 World Actor/Component 扫描。
- 完整 SHA、独立 Reload Verify、CI 审计。

批处理允许耗时数分钟到数小时，但必须具有进度、取消、断点续跑、阶段耗时和部分结果。

### 2.2 分开记录 UE Agent Kit 开销与 Unreal 原生开销

每个操作至少拆分：

```text
MCP 解析与路由
Policy / Revision / Change Set
SQLite / Memory 查询
Editor Bridge 往返
资产加载
实际修改
Blueprint 编译
保存
独立验证
总耗时
```

例如大型 Blueprint 编译 8 秒不一定是 UE Agent Kit 性能问题；但在编译前额外等待 3 秒搜索数据库就是工具开销，必须单独暴露。

### 2.3 Warm、Unloaded 和 Cold 分开测试

- **Warm Loaded**：Editor 已运行，目标资产已经加载。
- **Warm Unloaded**：Editor 已运行，Asset Registry 和 SQLite 已热，但目标资产未加载。
- **Cold**：Editor 或 MCP 刚启动，操作系统文件缓存不可依赖。

日常体验主要以 Warm Loaded 和 Warm Unloaded 为准；Cold 用于评估启动和首次访问成本。

## 3. 目标项目模型

长期容量模型按以下商业项目估算：

```text
开发仓库总量             约 500 GB
Cooked 发布内容           约 30 GB
UE Package                200,000–500,000
普通项目资产             100,000–250,000
External Actor/Object     50,000–300,000
Blueprint                 5,000–30,000
引用关系                  2,000,000–10,000,000
```

500 GB 中可能包含源美术、历史文件、Editor Only 资产、未使用内容、插件、DDC、Intermediate、Saved 和本地 Cook。性能测试必须区分开发仓库总大小与真正参与 UE Agent Kit 深度分析的 UE Package。

## 4. 分层测试环境

### 4.1 日常功能基线：我的项目与 ModelPreview

使用现有专用工程，不接触 Reforge：

```text
E:\WorkSpace\我的项目       受控写入、Undo、Save、Verify 和 Fixture 回归
E:\WorkSpace\ModelPreview  真实角色、材质、动画、Blueprint 和 Editor Context
```

用于验证日常工作体验：

- 普通资产搜索和引用查询。
- 当前 Editor Context。
- Live Apply、Undo、Discard、Save。
- Data Asset、DataTable、Material Instance 和后续 Blueprint Graph 写入。

这组结果回答“常用读取和修改每天用起来是否足够快”，并保证所有破坏性测试都发生在可重置的测试资产中。

### 4.2 中型真实样本：DarkRuinsMegascansSample

位置：

```text
F:\UELecture\DarkRuinsMegascansSample
```

已只读确认：

```text
UE 版本                5.6
Content                约 25.46 GB
.uasset                13,717
.umap                   79
External Actor/Object  约 12,018 个小文件
```

该样本无需解压，适合测试大美术资产、大文件顺序读取和大量 External Actor 小文件元数据访问。

首轮 NativeSSD 只读基线已完成，包含 Registry-only、External Actor/Object、Windows 长路径、SQLite 建库、未变化增量和查询延迟。结果与结论见 [`PERFORMANCE_BASELINE_DARKRUINS_20260803.md`](PERFORMANCE_BASELINE_DARKRUINS_20260803.md)。

L0 Registry、L1 Fast Revision、增量 Manifest、分片输出和按需专用 Reader 的具体实施顺序见 [`OFFLINE_INDEX_PERFORMANCE_PLAN.md`](OFFLINE_INDEX_PERFORMANCE_PLAN.md)。

### 4.3 大型物理测试工程：UEAgentKitPerfProject

建议位置：

```text
E:\WorkSpace\UEAgentKitPerfProject
```

目标规模：

```text
项目目录目标              160–180 GB
项目目录硬上限            200 GB
测试总工作集目标          不超过 260 GB
绝对硬上限                280 GB
最低剩余空间              50 GB
```

不能把全部剩余空间用于 Content，因为 DDC、Intermediate、Saved、性能报告、临时导出和单资产备份仍会增长。生成器在工程达到 200 GB、E 盘剩余空间低于 50 GB 或总工作集超过 260 GB 时必须停止。

建议资产规模：

```text
总 UE Package             150,000–250,000
普通资产                  60,000–120,000
External Actor/Object     80,000–150,000
Blueprint                 2,000–5,000
Material/MI               5,000–15,000
Texture/Static Mesh       20,000–50,000
Animation                 5,000–15,000
Data Asset/DataTable      5,000–20,000
引用关系                  2,000,000–5,000,000
```

### 4.4 500 GB 逻辑规模数据库

物理工程不必达到 500 GB。额外生成以下 SQLite/Memory 合成数据：

```text
资产行                    500,000
Symbol                    2,000,000–5,000,000
Reference                 5,000,000–10,000,000
Memory Record             100,000–300,000
Knowledge Node            50,000–100,000
Active Work               10,000–30,000
```

这部分占用远小于 500 GB，用于验证查询、分页、FTS、Context Budget、Reference 和 Memory 扩展性。

### 4.5 存储性能档位

物理工程始终放在 E 盘 SSD，不为了测试而迁移到机械硬盘。每组关键基准至少运行以下两个档位：

```text
NativeSSD
    使用 E 盘真实速度，不注入等待

SimulatedHDD50
    顺序读取上限       50 MB/s
    顺序写入上限       50 MB/s
    每次新文件打开     默认 10 ms，可测试 8/15 ms
    并发队列深度       1
    小文件合并优化     禁用或单独记录
```

HDD 模拟由测试专用 I/O Governor 和阶段延迟注入实现：

- UEAgentKit 自己控制的 Package Hash、JSONL、报告、索引和备份 I/O 按实际字节数节流。
- 对 SQLite、Unreal 资产 Load/Save 等无法完全拦截的原生 I/O，记录 `bytesRead/filesOpened/assetLoadMs/saveMs`，按相同模型计算兼容时间，并可在测试模式下注入等待以验证 Task、Progress、Cancel 和 Timeout 行为。
- 普通功能模式不启用限速；限速参数不得进入正式用户默认配置。
- 模拟结果用于保守兼容性门禁，最终若获得真实机械硬盘，再补一次外部校准测试。

必须分别报告 `NativeSSD` 和 `SimulatedHDD50`，不能把 SSD 实测时间直接标记为机械硬盘成绩，也不能只给一个混合总时间。

## 5. 大型测试工程实现方案

### 5.1 专用生成入口

新增可重复、可恢复的性能 Fixture Commandlet：

```text
UEAgentKitPerformanceFixture
```

建议 Action：

```text
CreateProjectProfile
CopySeedContent
GenerateArtPayload
GenerateSmallAssets
GenerateReferenceGraph
GenerateBlueprintSuite
GenerateWorldPartition
ValidateFixture
CleanupFixture
```

所有生成必须使用固定 Seed、Manifest 和 Checkpoint。中断后从最后一个完成批次继续，不重新生成已完成内容。

### 5.2 Seed Content

使用 DarkRuins 的 UE5.6 Content 作为真实美术种子，只复制 Content、Config 和必要 Project/Plugin 描述，不复制 DDC、Intermediate、Saved 和已有输出。

复制后的原始种子约 25 GB。压缩素材仓库不参与第一版测试，也不进行解压。

### 5.3 大美术负载

通过 AssetTools 或专用 Commandlet，在 Editor 内复制一组真实 Texture、Static Mesh、Material、Material Instance 和少量 Animation Package，直到真实 Content 达到约 130–150 GB。

要求：

- 使用合法唯一 Package Path。
- 每 100–500 个资产提交一个 Checkpoint。
- 记录源资产、目标资产、大小和耗时。
- 不修改原始 `F:\UELecture` 内容。
- 不使用无效扩展名或伪造 `.uasset`。

### 5.4 海量小资产与引用图

生成轻量 Data Asset、DataTable、Material Instance 和测试 Blueprint，使普通资产达到 60,000–120,000。

引用分布不能完全平均，应模拟商业项目：

- 大多数资产只有 1–5 个引用。
- 少数核心资产被数百或数千资产引用。
- 存在软引用、硬引用、Primary Asset 和 Searchable Name。
- 存在链、树、共享依赖和有限环。

### 5.5 Blueprint Suite

生成以下 Blueprint 组：

```text
Simple   1,500–3,000 个，每个 10–50 Node
Medium     300–1,000 个，每个 100–500 Node
Large       20–100 个，每个 1,000–5,000 Node
```

Graph 类型包括：

- 变量默认值和组件属性。
- 长 Exec Chain。
- Branch 分叉。
- Function、Macro、Interface、Dispatcher。
- 多 Graph Blueprint。

日常写入基准主要使用 Simple 和 Medium；Large 用于验证退化行为与超时边界。

### 5.6 World Partition 与 External Actors

生成一张或多张 World Partition 地图：

```text
Actor 总数                 80,000–150,000
纯 Actor                   约 50%
单 Mesh Actor              约 30%
多 Component Actor         约 15%
特殊测试 Actor             约 5%
```

Actor 按 Grid、Data Layer 和 Folder 分布，使每个 External Actor Package 合法生成。测试时只加载部分区域，以区分“项目总 Actor 数”和“当前已加载 Actor 数”。

### 5.7 磁盘与输出保护

- 测试工程、DDC 和 UEAgentKit 输出分别统计。
- 不创建项目级全量备份；Fixture 必须可再生成。
- 默认单资产备份，限制保留数量。
- 每个生成批次前检查磁盘剩余空间。
- 性能输出使用分片 JSONL 或直接 SQLite，避免数十万单资产 JSON。
- 测试工程不得提交 Git/P4。

### 5.8 测试工程生成耗时预估

测试工程在 E 盘 SSD 上生成。以下是 SSD 主机上的粗略工程时间，主要受 Unreal 资产复制、Package 保存、Asset Registry 更新和小资产数量影响，不按 50 MB/s 人工限速：

| 阶段 | SSD 主机粗略预估 | 主要变量 |
|---|---:|---|
| 复制约 25 GB DarkRuins Seed | 2–15 分钟 | SSD 实际吞吐、文件数量 |
| 扩展至约 130–150 GB 大美术负载 | 1–6 小时 | AssetTools 复制、Package Save |
| 生成 60k–120k 普通小资产 | 2–10 小时 | UObject 创建与小 Package 保存 |
| 生成 80k–150k External Actor/Object | 1–8 小时 | World Partition 与小文件元数据 |
| Fixture Validate、Asset Registry 和引用检查 | 0.5–4 小时 | 引用数量与校验深度 |
| 完整 160–180 GB 工程首次生成 | 6–24 小时 | 可跨夜、可断点续跑 |

生成过程应设计为无人值守批处理，可跨夜运行。50 GB、100 GB 和 160–180 GB 分别形成稳定 Checkpoint；达到一个规模后先跑完整基准，再决定是否继续扩容。测试工程只需要生成一次，后续通过 Manifest 重置局部 Fixture，而不是反复重建整个工程。

## 6. 测试矩阵

### 6.1 日常查询

- `ue_search`
- `ue_get_asset`
- `ue_find_references`
- `ue_memory_search`
- `ue_memory_get_context`
- `ue_memory_expand_node`
- `ue_get_editor_context`
- Blueprint Compile Error 和 Output Log 查询

每项运行：

```text
Warmup       5 次
Measured    30 次
报告         p50 / p95 / p99 / min / max
```

### 6.2 日常写入

- 普通 UObject/Data Asset 标量修改。
- Blueprint 变量默认值修改。
- 添加 1 个 Node。
- 添加并连接 5 个 Node。
- 修改 Pin Default。
- 添加或修改 Function/Variable。
- Compile。
- Undo/Discard。
- 保存一个普通资产。
- 保存后按需 Independent Verify。

每项分别测试 Loaded、Unloaded、Small Blueprint、Medium Blueprint。

### 6.3 批处理

- 10,000 / 50,000 个已加载 Actor 扫描。
- 100 / 1,000 / 10,000 个资产批量读取。
- 100 / 1,000 个资产批量标量修改。
- 增量索引 100 / 1,000 / 10,000 个变化资产。
- 初次 Registry、完整 SHA、专用 Reader 和 SQLite Build。

批处理重点记录首次摘要返回时间、总耗时、取消延迟、Checkpoint、每帧预算和恢复能力。

### 6.4 稳定性

- 常用查询重复 1,000 次。
- Editor Context 重复 500 次。
- Batch 状态轮询 5,000 次。
- Live Apply → Undo 重复 500 次。

检查内存、Handle、SQLite Connection、Journal 和临时文件是否持续增长。

## 7. 日常操作时间目标

以下是设计目标，不是当前版本实测承诺。每项需要分别报告 E 盘原生 SSD 和 `SimulatedHDD50`。Warm Loaded 操作通常不受磁盘影响；未加载资产、保存、独立验证和首次查询更容易暴露慢盘差异。

| 操作 | Warm Loaded SSD p95 | Warm Unloaded SSD p95 | SimulatedHDD50 p95 | 可接受上限 |
|---|---:|---:|---:|---:|
| MCP Tool 基础往返 | 50–150 ms | 50–200 ms | 50–250 ms | 300 ms |
| 资产/符号搜索 | 50–250 ms | 100–500 ms | 150–800 ms | 1.5 s |
| Reference 查询 | 100–500 ms | 150–800 ms | 200 ms–1.2 s | 2 s |
| Memory Context | 150–500 ms | 200–800 ms | 300 ms–1.5 s | 2 s |
| Editor Context | 100–400 ms | 100–500 ms | 150–800 ms | 1.5 s |
| 修改普通标量 | 100–300 ms | 0.3–1.5 s | 0.8–3 s | 4 s |
| 修改 Blueprint 变量默认值 | 150–500 ms | 0.5–2 s | 1–4 s | 5 s |
| 添加 1–5 个 Blueprint Node/连接 | 200–800 ms | 0.8–2.5 s | 1.5–5 s | 6 s |
| 小型 Blueprint Compile | 0.5–2 s | 0.5–3 s | 1–5 s | 6 s |
| 中型 Blueprint Compile | 1–5 s | 1–6 s | 2–10 s | 12 s |
| 大型 Blueprint Compile | 5–20 s | 5–30 s | 8–45 s | 60 s |
| Undo/Discard | 100–400 ms | 100–500 ms | 150–800 ms | 1.5 s |
| 保存普通资产 | 0.3–1.5 s | 0.3–2 s | 0.8–5 s | 6 s |
| 保存大型 Mesh/Texture | 2–10 s | 2–10 s | 5–30 s | 45 s |
| 打开普通未加载资产 | — | 0.2–1.5 s | 0.8–5 s | 6 s |
| Independent Verify | 5–30 s | 5–30 s | 10–90 s | 后台任务 |

一个已经加载的小型 Blueprint，完成“修改变量或添加少量节点 → Compile”的 SSD 目标总耗时为 **1–3 秒**；SSD 未加载时为 **2–6 秒**；`SimulatedHDD50` 下普通小型资产目标为 **3–9 秒**。UI/Agent 必须显示修改、编译、保存是独立阶段，不能把完整独立验证隐藏在每次小修改后面。

## 8. 批处理与首次初始化预估

### 8.1 顺序读写理论下限

按 50 MB/s：

```text
180 GB 纯顺序读取约 61 分钟
200 GB 纯顺序读取约 68 分钟
500 GB 纯顺序读取约 2 小时 47 分钟
```

实际 UE 工程包含大量小文件、寻道、Hash、UObject 加载、JSON/SQLite 写入，因此实际时间显著更长。

### 8.2 预估范围

| 操作 | 180 GB 物理测试工程 | 500 GB 商业项目模型 |
|---|---:|---:|
| Registry-only 基线 | 15–60 分钟 | 30–120 分钟 |
| 全 Package SHA | 2–5 小时 | 6–18 小时 |
| 当前式全量专用 Reader + 单资产 JSON | 6–15 小时 | 12–30 小时以上 |
| 优化后选择性语义读取 | 2–8 小时 | 6–20 小时 |
| SQLite/FTS Build | 10–60 分钟 | 30–120 分钟 |
| 增量处理 100 个变化资产 | 1–5 分钟 | 1–5 分钟 |
| 增量处理 1,000 个变化资产 | 5–30 分钟 | 5–30 分钟 |
| 扫描 10,000 已加载 Actor | 3–15 秒 | 同规模相近 |
| 扫描 50,000 已加载 Actor | 15–90 秒 | 同规模相近 |

这些范围必须通过真实测试修正。首次初始化允许较慢，但必须支持 Checkpoint、Resume、Cancel 和阶段进度。

## 9. 强制性能边界

任何日常交互 Tool 都不得隐式执行：

- 全项目目录扫描。
- 全项目 Package SHA。
- 全量 Reference 重建。
- 全量 Memory Validate。
- 全项目 Canonical JSON 重写。
- 独立 Unreal Editor 重载。
- Save All。

性能验收目标：

```text
Warm 搜索/引用/Memory p95       < 500–800 ms
UEAgentKit 额外写入开销 p95     < 300 ms
Loaded 普通属性 Live Apply p95  < 500 ms
Loaded 小型 BP 少量节点修改      < 1 s（不含 Compile）
小型 BP 修改 + Compile p95       < 3 s
Batch Task 句柄返回               < 300 ms
后台任务单帧处理预算              约 2 ms，p99 不持续超过 4 ms
```

若 Unreal 原生 Compile/Save 超过目标，结果必须明确标记瓶颈阶段，而不是只返回一个总耗时。

## 10. 测量与报告格式

每次调用至少记录：

```text
operation
projectScale
assetPath / assetClass
assetLoaded
cacheState
mcpMs
policyMs
indexMs
memoryMs
bridgeMs
assetLoadMs
mutationMs
compileMs
saveMs
verifyMs
totalMs
bytesRead / bytesWritten
filesOpened
resultBytes
truncated
success / errorCode
```

输出：

```text
performance-report.json
performance-summary.md
environment.json
```

`environment.json` 必须记录 CPU、内存、磁盘、文件系统、UE 版本、Plugin Commit、Python/SQLite 版本、资产数量、引用数量、Memory 数量和 Loaded Actor 数量。

## 11. 实现目录

建议新增：

```text
tests/performance/
    benchmark_index_queries.py
    benchmark_offline.py
    benchmark_memory.py
    benchmark_editor_context.py
    benchmark_live_write.py
    benchmark_blueprint_graph.py
    benchmark_batch_world.py
    datasets/
    baselines/

scripts/
    TestPerformance.ps1
    TestPerformance.cmd

Plugin/UEAgentKit/.../
    PerformanceFixtureCommandlet
```

建议使用独立分支：

```text
feature/performance-benchmarks
```

该分支只建设测试框架、Fixture 和报告，不与 Realtime Reader/Writer 或 Memory 功能争夺协议定义。公共 Timing Envelope 先合入 `main`，再同步到长期功能分支。

## 12. 实施顺序

1. 增加统一阶段计时与 JSON 报告，不先优化。
2. 在“我的项目”和 ModelPreview 建立日常查询、Editor Context 与 Live Write 基线。
3. 在 DarkRuins 上进行只读 Registry、文件分布和 External Actor 基线。
4. 建立 500k 资产、10m Reference 的逻辑数据库。
5. 创建 `UEAgentKitPerfProject`，先达到 50 GB，再验证生成器和磁盘保护。
6. 扩展到 100 GB，验证增量、恢复和清理。
7. 最终扩展到 160–180 GB，不超过 200 GB 项目硬上限。
8. 测试普通变量和 Blueprint 少量节点的交互性能。
9. 测试已知慢批处理和首次初始化。
10. 根据 p95 最慢项优化，并建立回归门禁。

第一轮最优先验证：

```text
ue_search / ue_find_references
ue_memory_get_context
ue_get_editor_context
普通变量 Live Apply → Undo
Blueprint 变量或 1–5 Node 修改 → Compile → Undo
```

这些操作直接决定 UE Agent Kit 能否达到“修改正常资产像修改代码一样简单”的目标。
