# UE Agent Kit 项目现状



更新时间：2026-08-02



本文描述 **0.7.0-dev** 开发线及 `feature/memory-context` 的单人版 Memory/Context MVP。最新正式发布版本仍为 **0.6.0**，支持 Unreal Engine 5.6；Schema v3 Knowledge Tree、Active Work、渐进式 Context，以及 0.6.0 之后的 Live Editor Write 能力均尚未正式发布。



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

Offline                    5              17

Live                      23              35

Workflow                  29              41

Combined                  47              59

```



Tool 数量只表示 MCP 接口数量，不等同于 Unreal Operation 数量。当前 Workflow 包含 12 个高层安全写入入口、底层 Patch 工作流、Live Editor Write、授权保存、验证、索引刷新和 rollback。



当前自动化门禁基线：



```text

Python tests                 299/299

JSON Schemas                 3/3

Patch examples               16/16

UE5.6 Direct Build           passed

真实 Live Editor Write       passed

真实 Live Editor Reference Write  passed

真实 Live Editor Structured Write  passed

UTF-8 no BOM / CRLF          passed

```



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

当前 `0.7.0-dev` 注册表开放 12 个受控 Operation：Data Asset 标量/引用/Struct/Array/Set/Map，Material Instance Scalar/Vector/Texture/Static Switch，以及 DataTable Cell/RowFields/Add/Remove/Rename。它仍只接受已加载、已打开、初始 Clean 的 `/Game` 非 Blueprint、非地图单文件资产，并继续拒绝任意 UObject Method、嵌套属性、PIE/SIE、自动保存和未授权写入。

为后续数百种 Operation 扩展，中央 Bridge 已改为 `operation + assetPath + target + value` 的通用请求和 `LiveWriteOperationRegistry` 分派；Property、Material、DataTable 分属独立域模块，公共 Transaction/Evidence 层统一处理 Snapshot、No-op、失败恢复、Dirty 与 Undo。Python `OperationSpec` 同时驱动 Target 校验、valueKind 和保存后独立验证，不再重复维护硬编码白名单。

Live Apply Workflow 使用固定 Work Root Journal 保存待处理 Receipt；MCP 重启后可恢复经过严格校验的记录，Verify 可指定精确 `liveApplyReceipt`，成功 Undo/Discard/Verify 会关闭记录。Journal I/O 失败不会把已经成功的 Editor 修改伪报成失败。

真实回归分为 Fast（Scalar、Undo/Discard、Closed Loop）和 Full（全部 7 组），正式版本号仍为 0.6.0，状态中明确报告 `developmentLine=0.7.0-dev`。

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



## 5. 当前明确未实现的能力



以下能力不能因为存在“读到相关信息”就视为已支持写入：



- 通用 Blueprint Graph 节点创建、删除、连线和自动布局。

- Anim Blueprint State Machine、Montage、Blend Space、Anim Sequence 写入。

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



### P0A：Realtime Editor CRUD、批量任务与诊断

Live Editor Write 基础层、Material/DataTable、Undo/Discard、Save→Verify→Memory 闭环和注册式扩展架构已经完成。Realtime I/O 作为日常开发主路径，下一阶段优先建设当前 Editor Context、批量 Query/Task、PIE 诊断、Change Set 和高价值资产域 CRUD；不再重写中央分派。每个新增 Operation 仍需补齐：

1. Python `OperationSpec`、Policy 授权和 Plan Schema。
2. 对应 C++ 域执行器与 Operation Descriptor。
3. Snapshot、No-op、失败恢复、Dirty、Undo 和独立 Verify 语义。
4. 真实 UE5.6 成功、拒绝、恢复和闭环回归。

### P0B：Memory 可用性与知识树前置

0.6.0 的 Revision-aware 平面记录库已经完成，但不继续把复杂维护责任直接交给 Agent。Context Pack 之前先完成：

- 任意深度 Knowledge Tree：Project Profile → System → Feature/Entity → Implementation。
- 现有 Rule/Finding/Decision/Known Issue/Task/Evidence 绑定 Knowledge Node，记录类型不再作为主要导航。
- 独立 Active Work：当前目标、进行中、TODO、阻塞、待确认决策和下一步。
- 五级渐进式披露：索引、节点摘要、实现概览、详细记录、原始证据。
- MCP Server 强制 Token Budget、默认状态过滤、重复检测和结构化 `nextActions`。
- 日常采用一个薄 `project-memory` Skill，不把读、写、维护和 TODO 拆成长 Skill。

完整设计见 [`MEMORY_ARCHITECTURE.md`](MEMORY_ARCHITECTURE.md)。

### P1：0.7.0 Context/Analysis 主线



- 自动 Context Pack：按任务只收集必要资产、Symbol、Reference、Memory 和 Live 状态。

- 值来源追踪：一个属性、参数或默认值由哪里定义、覆盖和消费。

- 执行链追踪：Blueprint Exec、函数、接口、Dispatcher 和跨资产调用路径。

- 影响分析：修改前列出直接/间接受影响资产、引用和验证范围。

- 语义 Diff：比较 Blueprint、Data Asset、Material Instance、DataTable 的可读变化，而不是只比较二进制哈希。

- Evidence-backed Hypothesis：区分已证明结论与模型推断。

- 自动 Change Plan 与 Verification Plan。



### P2：高价值专用写入



在 Context/Analysis 和 Live Transaction 基础稳定后，再按真实需求逐项扩展：



- Blueprint Default、Component、Pin 的 Live Apply 与编译验证。

- Enhanced Input / Input Mapping Context。

- 常用 Animation 资产的窄范围编辑。

- Level Actor 的受限 Transform/Property 操作。



完整 Graph 结构写入必须先具备稳定 Node/Pin Identity、结构化 Diff、编译验证和失败恢复；不会为了追求 Tool 数量直接开放任意 Graph 操作。



### P3：0.8.0 Collaboration

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
