# UE Agent Kit 项目现状



更新时间：2026-08-27



本文描述已发布的 `main` 基线及当前开发状态，支持 Unreal Engine 5.6。最新正式发布仍为 **0.7.0**。0.8.x Context / Analysis / Agent Reliability capability scope 已完成本地 closeout，R5 继续 `deferred by benchmark evidence`。当前 `feature/live-writer-expansion` 已完成 W0–W3：Blueprint 窄范围常驻写入、Fast Resident Verify 与 Checkpoint Strong Verify 均已通过真实 UE5.6 验收；下一主线是 W4 Multi-operation / Bounded Batch。当前计划入口统一见 [`Plans/README.md`](Plans/Archive/README.md)，项目级方向见 [`Plans/UEAGENTKIT_MASTER_DEVELOPMENT_PLAN_20260827.md`](Plans/UEAGENTKIT_MASTER_DEVELOPMENT_PLAN_20260827.md)。



## 1. 当前定位



UE Agent Kit 不是“让 AI 任意遥控 Unreal Editor”的通用自动化层，而是面向 AI Agent 的 Unreal Engine **项目智能与受控修改层**：



1. 把二进制资产、Blueprint 语义、引用关系和编辑器运行状态转成稳定、可搜索的数据。

2. 让 Agent 在修改前获得可追溯的项目上下文，而不是只依赖临时截图、日志或猜测。

3. 对写入执行 Policy、Revision、Plan、Dry Run、显式确认、备份、验证和 rollback 门禁。

4. 用 Revision-aware Project Memory 保存规则、发现、决策、任务结论与证据，并在资产变化后自动失效旧结论。



因此，项目当前更接近“安全的 UE 项目知识层 + 修改工作流”，而不是覆盖所有编辑器操作的远程控制台。



## 2. 当前规模



```text

模式                 不启用 Memory    启用 Memory

Offline                   10              22

Live                      43              55

Workflow-only             60              72

Live + Workflow           93             105

```



Tool 数量只表示 MCP 接口数量，不等同于 Unreal Operation 数量。当前 Workflow 包含 12 个高层安全写入入口、底层 Patch 工作流、Live Editor Write、授权保存、验证、索引刷新和 rollback。



0.8 capability closeout 历史门禁基线（2026-08-23）：

```text
Portable unittest            696 passed
Python full suite             739 passed
JSON Schemas / Patch examples 3 / 16
Ruff / compileall             passed
PowerShell parser             61 / 61
R4.1 raw summary --check      passed
Tool / Operation audit        105 / 18
UTF-8 no BOM / CRLF           passed
C++ changed                   0
Direct Build                  not triggered
```

当前 `feature/live-writer-expansion` 的 W3 收口基线（2026-08-27）：

```text
Python full suite             712 / 712 passed
ValidateRelease               0.7.0 passed
Ruff / compileall             passed
UE5.6 Direct Build            passed
git diff --check              passed
W3 real acceptance            C0-C6 PASS
```

测试数量是阶段证据，不作为未来分支永久固定值。

## 3. 已实现的读取能力



### 3.1 离线项目读取



- 资产目录：Static Mesh、Skeletal Mesh、Material、Texture、Animation、DataTable、Niagara、World 等 Asset Registry 可见资产。

- Blueprint 语义：Graph、Node、Pin、连接、变量读写、函数、宏、接口调用、Dynamic Cast、Event Dispatcher 等。

- Canonical JSON 与 BPCTX/1：为稳定比较、索引和 AI 上下文提供两种输出层。

- 资产 Revision：以 Package SHA-256 为基础，和导出快照、SQLite 记录配对。

- 项目级搜索：Asset、Symbol、Reference、全文搜索、路径过滤和稳定分页。

- 引用查询：Hard/Soft Package 依赖、反向引用、限定深度的双向引用查询。

- 四源资产状态：Editor Memory、磁盘 Package、Revision Export、SQLite 分开报告，不把 Dirty 内存伪装成磁盘 Revision。



### 3.2 Live Editor 读取



- Editor、PIE/SIE、当前关卡和当前选择状态。

- 已打开资产和 Dirty Package。

- Output Log 增量读取与 Blueprint 编译错误。

- 不触发加载的实时资产检查。

- 普通 Blueprint Editor 当前 Graph 和选中 Node 定位。



### 3.3 Project Memory 读取



- Rule、Finding、Decision、Known Issue、Task Record 和 Runtime Evidence。

- 来源区分：`user-confirmed`、`tool-observed`、`model-inferred`。

- 状态区分：`valid`、`stale`、`conflicted`、`superseded`、`unverified`。

- Scope、Revision Set、Artifact、Confidence、时间与证据摘要。

- Revision 变化后的自动 stale，以及冲突结论并存。

- Schema v3 Knowledge Tree：规范化 `/project/...` Path、同项目 Parent、无环和安全删除约束。

- 独立 Active Work：`planned/in_progress/blocked/done/cancelled`、TODO、下一步和正规化 Node/Asset 关联。

- 0–4 级渐进式 Context、字符预算、默认过滤 `stale/superseded`、截断 `nextActions` 和按需 Evidence。

- 五个新高层 MCP Tool；原有七个 Memory Tool 保持兼容。



## 4. 已实现的写入与操作能力



### 4.1 非持久化 Live Action



以下操作会改变编辑器界面、内存编译状态或验证状态，但不直接保存 Package：



- 打开或聚焦资产。

- Content Browser 同步。

- 按 ActorGuid 聚焦 Actor。

- Blueprint 内存编译。

- 单资产或文件夹 Data Validation。

- 精确名称 Automation Test。



### 4.2 Live Editor Write 基础层

当前闭环：

```text
Policy / Revision Plan
→ 精确 LIVE APPLY 确认
→ 注册式 Operation 执行器
→ FScopedTransaction / Snapshot / Dirty
→ 显式 Undo / Discard，或 Authorized Save
→ 独立 UE 重载 Verify
→ Memory Evidence
```

当前 0.7.0 注册表开放 12 个受控 Operation：Data Asset 标量/引用/Struct/Array/Set/Map，Material Instance Scalar/Vector/Texture/Static Switch，以及 DataTable Cell/RowFields/Add/Remove/Rename。它仍只接受已加载、已打开、初始 Clean 的 `/Game` 非 Blueprint、非地图单文件资产，并继续拒绝任意 UObject Method、嵌套属性、PIE/SIE、自动保存和未授权写入。

为后续数百种 Operation 扩展，中央 Bridge 已改为 `operation + assetPath + target + value` 的通用请求和 `LiveWriteOperationRegistry` 分派；Property、Material、DataTable 分属独立域模块，公共 Transaction/Evidence 层统一处理 Snapshot、No-op、失败恢复、Dirty 与 Undo。Python `OperationSpec` 同时驱动 Target 校验、valueKind 和保存后独立验证，不再重复维护硬编码白名单。

Live Apply Workflow 使用固定 Work Root Journal 保存待处理 Receipt；MCP 重启后可恢复经过严格校验的记录，Verify 可指定精确 `liveApplyReceipt`，成功 Undo/Discard/Verify 会关闭记录。Journal I/O 失败不会把已经成功的 Editor 修改伪报成失败。

真实回归分为 Fast（Scalar、Undo/Discard、Closed Loop）和 Full（全部 7 组）；发布状态统一报告 `publishedVersion=0.7.0` 与 `developmentLine=0.7.0`。

开发线 `feature/live-writer-expansion` 已在此基础上完成 W1-W3：Blueprint `setVariableDefault` / `setComponentProperty` / `setPinDefault` 可在常驻 Editor 中安全连续写入；W2 提供 Fast Resident Verify；W3 提供零子进程 checkpoint save 与随后独立 Strong Verify，并保留 exact transaction continuation、supersession、Semantic Diff / Trust 与恢复边界。

### 4.3 持久化安全写入



当前已支持：



- Blueprint：变量默认值、组件属性、Pin 默认值、描述等已注册 Operation。

- Data Asset 标量属性。

- Data Asset Object/Class、Soft Object/Class 引用。

- Data Asset Struct、Array、Set、Map 完整稳定值。

- Material Instance Scalar、Vector、Texture、Static Switch 参数。

- DataTable 单字段、多字段、Row 新增、删除和重命名。

- 单资产 1–32 个兼容 Operation 原子事务。



持久化闭环：



```text

Plan

→ Dry Run

→ 一次性 Receipt

→ 精确 COMMIT 确认

→ 外部备份

→ UE 保存

→ 独立进程重新加载验证

→ Task Evidence

→ 可验证 rollback

```



Live Editor 中已经产生的受控 Dirty 资产，也可以通过 `ue_save_authorized_asset` 单独执行 Policy/Revision/Session 绑定的授权保存。



### 4.4 Memory 写入



- 添加用户确认规则。

- 记录工具观察或模型推断的 Finding。

- 记录带 Patch、Backup Manifest、Validation Evidence 和最终 Revision 的 Task。

- 显式标记旧记录 superseded。

- 校验当前 Revision 并更新 stale 状态。



### 4.5 Realtime Animation Tools 写入



2026-08 新增动画比例修复与批量闭环（`feature/live-editor-realtime-io`，已合并 `main`）：



- 单资产：`ue_plan_animation_scale_fix` + `setAnimationScaleFix`（Force Root Lock / Root Motion / Root Track Scale）+ Undo / Discard / Authorized Save / Independent Verify / Index Refresh。



- Additive：`ue_plan_additive_base_pose_fix` + `setAdditiveBasePoseFix`（RefPoseSeq / RefFrameIndex / AdditiveAnimType / RefPoseType 修正 + 组合姿势验证）。



- 批量：`ue_plan_animation_scale_fix_batch` + Live Apply / Save / Verify / Index Refresh / Rollback（不可变 Batch Plan，分片 8，持久化分片 2）。



- 重定向：批重定向闭环 `ue_analyze/plan/apply/save/verify/rollback_animation_retarget*` + 输出后处理 `ue_*_animation_retarget_postprocess`。



写入仍走 Policy（含 `retargetCapabilities`）/ Revision / Snapshot / Transaction / Undo / Save / Verify / Rollback 门禁；复合资产（Montage / BlendSpace / AimOffset）重建仍在范围外。



## 5. 当前明确未实现的能力



以下能力不能因为存在“读到相关信息”就视为已支持写入：



- 通用 Blueprint Graph 节点创建、删除、连线和自动布局。

- Anim Blueprint State Machine、Montage、Blend Space、AimOffset 写入（AnimSequence 窄范围写入——Root Lock / Root Track Scale / Additive Base Pose——已实现，见 §4.5）。

- Control Rig、IK Retargeter 和 RigVM Graph 写入。

- Material Graph、Niagara、Sequencer、UMG Widget Tree 写入。

- Level Actor 的通用 Spawn、Delete、Transform 和任意属性修改。

- PIE 输入注入、录制、确定性回放和 Viewport 截图闭环。

- Asset Import、Duplicate、Rename、Delete、Migrate 等生命周期操作。

- Console Command、任意 Python、任意 C++/脚本执行。

- Editor/Visual Studio 自动关闭、重启和构建调度。

- Source Control Checkout、Lock、Owner 和 Depot Head 冲突处理。



这些不是遗漏文档，而是当前有意保留的安全与范围边界。



## 6. 待做功能与优先级

当前后续工作的文档入口为 [`Plans/README.md`](Plans/Archive/README.md)。项目级方向由 2026-08-27 Master Plan 统一管理，当前 Writer 实现以 W4 Detailed Plan 为准。`UEAGENTKIT_POST_0_8_DEVELOPMENT_PLAN_20260823.md` 继续保留为从 0.8 closeout 进入 Writer 阶段的历史桥接计划。优先级不再按 Tool 数量推进：

```text
P0  Editor-resident Writer / low-latency write path
P0  Large-project performance / true incremental（可并行）
P1  Agent UX reliability tail：requested-bound + typed result
P2  Maintainability / Tool Profile / UE build CI
P3  0.9 source-control / collaboration
```

正式 0.8 package release 是独立授权轨道，不阻塞以上技术开发；R5 继续冻结。



### 已完成基础 / 当前延伸：Realtime Editor CRUD、批量任务与诊断

Live Editor Write 基础层、Material/DataTable、Undo/Discard、Save→Verify→Memory 闭环和注册式扩展架构已经完成。Realtime Foundation 现已补齐当前 Editor Context、首个分帧 Batch Task 和持久化 Change Set：

- `ue_get_editor_context` 在一次只读请求中聚合 Editor、World、Selection、Open Assets、Dirty Packages、Blueprint Graph Selection、Compile Errors 和 Output Log Cursor，并返回阶段耗时与 `nextActions`。
- `scanCurrentWorld` 只扫描当前已加载 World；枚举和 Actor/Component 处理均受每帧约 2 ms 时间预算与数量上限约束。任务绑定 Editor Session/World，支持进度、取消、超时、失效和部分结果。
- Batch Task 默认只返回摘要；详情通过 `include_details/detail_offset/detail_limit` 分页读取，单页最多 5 个 Actor，避免超过 Bridge 1 MiB 单响应上限。
- Change Set 使用 schema v2 持久化 Task、Editor Session、Operation、Asset、Transaction、Save Receipt 和 Validation 生命周期；支持 `planned/applied/partially_applied/undone/discarded/saved/verified/no-op/failed/unknown`，并保留终态历史。
- 活跃 Change Set 不会被容量清理静默删除；Editor 重启后无法重新证明的运行时状态明确降级为 `unknown`。
- expected no-op 绑定独立 `noop_*` Operation 并直接进入 `no-op` 终态：`liveApplyReceipt=""`、`changeSetBound=true`、`journalPersisted=false`，validation 聚合为 `no-op`、saveState 为 `not-required`；不制造 Transaction、LiveApply journal、授权保存或 Independent Verify。只有固定基线 Canonical Revision 与 Plan `expectedRevision` 完全一致时才有 persisted no-op evidence；无 live/verified no-op stage，同资产 no-op 与真实写混合时保守报告 stage unavailable。

Realtime I/O 基础层已经达到可复用状态，后续进入需求驱动维护，不再以持续扩大 CRUD / Writer 广度或追平 `ue-llm-toolkit` Tool 覆盖面作为首要目标。 Post-0.8 的当前延伸重点不是增加新的 Operation family，而是把现有 Blueprint default/component/pin 窄写入迁移到常驻 Editor Bridge，并将昂贵 Independent Verify 收束到任务 checkpoint；详见 post-0.8 总计划。新的 Writer 只有在 Reforge 真实任务或 Agent Benchmark 反复暴露明确缺口时再增加；每个新增 Operation 仍必须补齐：

1. Python `OperationSpec`、Policy 授权和 Plan Schema。
2. 对应 C++ 域执行器与 Operation Descriptor。
3. Snapshot、No-op、失败恢复、Dirty、Undo 和独立 Verify 语义。
4. 真实 UE5.6 成功、拒绝、恢复和闭环回归。

### 已完成基础 / 后续增强：Memory 与任务上下文

Schema v3 Knowledge Tree、Active Work、渐进式披露、按需 Evidence 与现有 Memory Tool 已完成；0.8 R0-R3 又补齐了 Task Context、Change Set、Editor Session、Revision 与 Evidence 的确定性关联。旧的“Schema 暂停扩张”结论只适用于 0.8 capability closeout，不再代表当前中期计划。

W4 完成并冻结 Change Set 结构后，Memory Track 计划按 2026-08-27 Master/Midterm 继续：先建立效率门禁，再以确定性 L0 事件索引扩展 Schema v4，随后在独立迁移中加入 v5 embedding 存储与可选混合召回。自动蒸馏只使用 `tool-observed` 证据，不在任务同步链路调用 LLM。

当前实现细节见 [`MEMORY_ARCHITECTURE.md`](MEMORY_ARCHITECTURE.md)；未来增强契约以 [`Plans/UEAGENTKIT_MASTER_DEVELOPMENT_PLAN_20260827.md`](Plans/UEAGENTKIT_MASTER_DEVELOPMENT_PLAN_20260827.md) 和 Midterm Spec 为准。

### P0C：大型项目性能基准

首次建立知识库允许较慢，但日常搜索、变量修改和少量 Blueprint Graph 编辑必须接近修改代码的体验。性能计划采用 Reforge、现成 UE5.6 DarkRuins 样本、E 盘 SSD 上的 160–180 GB 物理测试工程和 500k Asset/10m Reference 逻辑数据库；关键基准分别运行原生 SSD 与机械硬盘 50 MB/s 模拟档位。重点门禁包括：

- Warm 搜索、Reference 和 Memory Context p95 控制在 500–800 ms。
- 已加载普通属性 Live Apply p95 小于 500 ms。
- 已加载小型 Blueprint 修改少量 Node 小于 1 秒，不含 Unreal 原生 Compile。
- 小型 Blueprint 修改加 Compile 的目标 p95 小于 3 秒。
- Batch 操作在 300 ms 内返回 Task 句柄，并在后台分帧运行。

测试工程项目目录目标 160–180 GB，硬上限 200 GB；总工作集不得超过 260 GB，E 盘剩余空间低于 50 GB 时自动停止生成。完整方案见 [`PERFORMANCE_TEST_PLAN.md`](PERFORMANCE_TEST_PLAN.md)。

### P1：0.8.x Context / Analysis / Agent Reliability（capability scope 已完成）

基础设施阶段已经基本完成，本阶段不再以新增 Tool / Asset Class / Writer 数量作为主进度。推荐在 `feature/agent-reliability` 上按可独立提交、可中断的里程碑推进，详细计划见 [`Plans/AGENT_RELIABILITY_CONTEXT_ANALYSIS_PLAN_20260815.md`](Plans/Archive/AGENT_RELIABILITY_CONTEXT_ANALYSIS_PLAN_20260815.md)。

```text
R0  Task Context / Context Pack MVP
R1  Impact Analysis
R2  Semantic Diff
R3  Verification Plan + Trust Verdict
R4  Real Agent Benchmark v1
R5  Value Provenance / Execution Trace（由 Benchmark 决定）
```

R0–R4 已完成：高层任务上下文、逆向引用影响分析、事实级 Semantic Diff、Evidence-gated Verification/Trust 与第一版真实 Agent Benchmark 已形成可测量链路；仍不新增 Memory Schema，也不在 Server 内做模型推断。

R0.0（现状审计 + 复用矩阵 + 最小 Schema）与 R0.1（`ue_get_task_context` 第一条纵向切片）已完成并本地提交到 `feature/agent-reliability`：query + 显式 assetPaths → targetAssets → revisionState → 可选 Memory 摘要 / Live Editor 摘要 / Change Set → 确定性 risks → 有界输出；所有可选来源支持 section 级降级。复用矩阵与 Schema 见 [`Plans/AGENT_RELIABILITY_R0_AUDIT_AND_SCHEMA_20260815.md`](Plans/Archive/AGENT_RELIABILITY_R0_AUDIT_AND_SCHEMA_20260815.md)。

R0-S（真实 Reforge Context Smoke）与 R0.2（Deterministic Relevant Asset Discovery）已完成并本地提交：真实 Reforge 索引（48 资产，logic profile）上 S1/S2/S3 三个 Case 记录见 [`Plans/AGENT_RELIABILITY_R0_REAL_CONTEXT_SMOKE_20260816.md`](Plans/Archive/AGENT_RELIABILITY_R0_REAL_CONTEXT_SMOKE_20260816.md)；`relevantAssets` 现为确定性候选集（query 分词 + Asset/Symbol Search 复用、与显式目标互斥、固定排序、Top N=8、可解释 whyIncluded/matchKind、无 score/confidence），预算不足时先裁候选 metadata 再减候选数量，绝不优先于 target identity / high risk / revision summary。

R0.3（只读 Cross-source Correlation）已完成并本地提交，**R0 里程碑标记完成**：`ue_get_task_context` 新增 `correlation` section（schemaVersion 1.2），用精确键把 Active Work、显式 Change Set、Live Editor Session 与 Memory Evidence 关联起来（session id 相等性、资产路径集合交集、changeSetId 字面量、资产 scope Evidence），只读、非持久化、零模型推断；不新增 Memory/ChangeSet Schema、不扫描 workflow 私有 `_change_sets`、不自动发现 Change Set、不做 R1 引用遍历。链接固定排序上限 16 条，边界计数如实报告；新增确定性风险 `change-set-editor-session-mismatch`（medium）；预算阶梯先裁 correlation links/summary，绝不优先于 target identity / high risk / revision summary。交接见 [`Handoffs/AGENT_RELIABILITY_R0_SLICE3_HANDOFF_20260816.md`](Handoffs/Archive/AGENT_RELIABILITY_R0_SLICE3_HANDOFF_20260816.md)。

R1/R2 已分别解决「修改会影响什么」和「实际发生了什么变化」。R3 实现见 [`Plans/AGENT_RELIABILITY_R3_VERIFICATION_TRUST_DESIGN_20260820.md`](Plans/Archive/AGENT_RELIABILITY_R3_VERIFICATION_TRUST_DESIGN_20260820.md)，执行规范见 [`Handoffs/AGENT_RELIABILITY_R3_FULL_HANDOFF_20260820.md`](Handoffs/Archive/AGENT_RELIABILITY_R3_FULL_HANDOFF_20260820.md)：两个只读 Tool 固定区分 required/recommended/informational、pass/fail/unknown/not-applicable，以及 verified/suspicious/failed/insufficient-evidence。Compile/Validation/Automation 仅由有界、无任意注入、session-local Store 捕获；Trust Tool 不自动执行动作。保存、独立重载或 verified Semantic Diff 都不自动等同于整个任务成功。

R4 已用跨 Data Asset / DataTable / Material Instance / Blueprint / Context / stale / rollback 的真实 Agent Case 统计 Trusted Completion、False Success、Wrong Asset、Unintended Change 和 Recovery。15 个 Full + 9 个 matched Legacy attempt 共 24/24 保留，0 infrastructure failure、0 fairness mismatch、全部 fixture 精确恢复。Paired Full 相对 Legacy 的 Task Completion `+44.44 pp`、Trusted Completion `+22.22 pp`、False Success `-11.11 pp`、Wrong Asset `-22.22 pp`、Tool Calls `-4.11`；但 Full 绝对 Trusted Completion 仅 `26.67%`，False Success / all cases 仍为 `33.33%`，stale detection 还出现退化。完整结果见 [`Plans/AGENT_RELIABILITY_R4_BENCHMARK_RESULT_20260820.md`](Plans/Archive/AGENT_RELIABILITY_R4_BENCHMARK_RESULT_20260820.md)。动画没有进入 v1 Case，不再作为主开发方向。



**当前执行状态（2026-08-23）**：0.8.x Closeout C0–C6 已完成。R4.1 以冻结 fingerprint 运行 4 anchors × 2 profiles × 3 repeats，24/24 retained、12/12 paired fairness matched、0 drift、0 infrastructure failure、24/24 exact recovery。Full stale 与 Blueprint default 均 3/3 Trusted；high-fanout 3/3 越过 direct-only bound，scalar 2/3 将 numeric beforeValue stringify，均作为真实 False Success 保留。结果见 [`Plans/AGENT_RELIABILITY_R4_1_REPEAT_RESULT_20260823.md`](Plans/Archive/AGENT_RELIABILITY_R4_1_REPEAT_RESULT_20260823.md)。

Read/Write Audit 已分类 105 个公共 Tool 与 18 个 Patch Operation，结论为 `0 Must-fix new tools`；Generic Graph/Actor/Material Graph/Niagara/Sequencer/Control Rig 与 arbitrary script 继续明确延期。Scope Freeze 见 [`Plans/UEAGENTKIT_0_8_CAPABILITY_GAP_AUDIT_20260823.md`](Plans/Archive/UEAGENTKIT_0_8_CAPABILITY_GAP_AUDIT_20260823.md)。R5 保持 `deferred by benchmark evidence`：只有后续真实 Case 反复出现 Value Provenance / Execution Trace primary blocker 才解冻。最新正式发布版本仍为 0.7.0，本次 capability closeout 不修改 published version、Tag 或远端。


### P2：高价值专用写入

本项改为**需求驱动候选池**，不属于当前固定排期。只有 Reforge 真实任务或 R4 Agent Benchmark 反复暴露明确缺口时，再按收益排序解冻：



- 现有 Blueprint Default、Component、Pin 的 Editor-resident Live Apply 已提升为 Post-0.8 W1，不再作为“新增 Writer family”候选；本池只保留新的 Operation family。

- Enhanced Input / Input Mapping Context。

- Animation Writer 扩展暂缓；现有 Realtime Animation Tools 保留为已完成能力和验证样本。

- Level Actor 的受限 Transform/Property 操作。



完整 Graph 结构写入必须先具备稳定 Node/Pin Identity、结构化 Diff、编译验证和失败恢复；不会为了追求 Tool 数量直接开放任意 Graph 操作。



### P3：0.9.0 Collaboration（延后）

多人部署采用混合架构：每名开发者运行本地 MCP 并连接本机 UEAgentKit Plugin/Editor；团队共享的是独立 Knowledge Service，而不是一个能够直接控制所有开发者编辑器的中央 MCP。共享层计划使用 PostgreSQL/API，本地 SQLite 保留资产索引、缓存、个人和 Session 数据。



- Source Control Provider、Checkout、Lock、Owner、Head Revision 读取。

- Local Dirty、磁盘 Revision、Depot/Remote Head 分歧分析。

- 多人修改风险、责任边界和阻断策略。

- 首版只分析、警告或阻止，不自动抢锁或覆盖他人修改。



## 7. 后续方向原则



1. **先理解，再修改**：优先提高上下文、引用和影响分析质量。

2. **先窄后宽**：每个写入域先做一个真实纵向闭环，再扩展 Operation 数量。

3. **Live 不等于无门禁**：编辑器内存写入仍必须经过固定项目、Policy、Revision、Plan 和显式确认。

4. **不把保存等同于成功**：成功必须包含独立验证和可追溯证据。

5. **不追求任意脚本能力**：Console/Python/Shell 虽然扩展快，但会绕过 UE Agent Kit 的核心安全模型。

6. **以真实项目需求排序**：优先实现 Reforge 实际开发中反复出现、能明显减少人工操作的能力。

7. **渐进式披露**：默认只加载 Project Profile 和直接相关节点摘要，详细实现与原始证据必须显式展开。

8. **本地执行、共享知识**：UE 编辑器状态和写入会话留在本机，长期项目知识与团队任务由共享服务管理。
