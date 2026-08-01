# AI 可用的 UE5 编辑器架构

更新时间：2026-08-01

## 1. 目标

UE Agent Kit 的长期目标不是只提供一组 Unreal MCP Tool，而是把 UE5 变成一个 Agent 能持续理解、分析、增删查改、运行验证和维护知识的开发环境。

```text
用户开发任务
→ 项目知识与资产索引
→ 当前 Editor / PIE 上下文
→ 任务级分析与 Change Set
→ 实时 UE 增删查改
→ 编译、验证、撤销或保存
→ 更新 Knowledge Tree
```

运行中的 UE Editor 是日常开发主路径。离线导出、SQLite 索引和 Commandlet 负责未加载资产、全项目查询、批处理、独立验证、回滚与 CI，不替代实时 Editor 交互。

## 2. 三类事实源

### 2.1 Live Editor

回答“当前正在发生什么”：当前选择、打开资产、Blueprint Graph、Actor/Component、Dirty Package、编译状态、PIE 对象、日志、Trace、Collision 和运行时值。

### 2.2 Project Model

由资产导出、Asset Registry、Revision Export 和 SQLite 构成，回答“整个项目中有什么”：未加载资产、Symbol、引用、继承、Blueprint 结构、DataTable/Data Asset 关系和稳定 Revision。

### 2.3 Knowledge Tree

回答“项目为什么这样设计、以前已经确认过什么”：架构、规则、决策、已知问题、验证方法、任务进度和证据。知识节点绑定 Revision，源资产变化后自动 stale。

三类数据不能互相伪装：Editor Memory、磁盘 Package、索引快照和长期知识必须标明来源与新鲜度。

## 3. 核心能力：增删查改

### 查（Read）

优先级最高。包括实时上下文、Blueprint 语义、运行时诊断、全项目搜索、引用分析、批量地图审计、DataTable 检查和值来源追踪。批量查询必须在 Plugin 内完成，不能让 Agent 对每个对象进行一次 MCP 往返。

### 增（Create）

创建资产、DataTable Row、Blueprint 变量/函数/节点/Component、Montage Section/Notify、Anim State、Actor 和项目配置。模板化开发属于重点能力，例如从角色 A 提取结构并为角色 B 创建对应技能树。

### 改（Update）

包括属性、引用、容器、Material 参数、DataTable、Blueprint Default/Pin/Graph、Actor/Component、Animation 和批量配置。实时修改默认只改变 Editor Memory，进入一个任务级 Transaction，不自动保存。

### 删（Delete）

删除 Row、Node、连接、Component、Actor 或资产。删除前必须根据风险执行连接检查、引用影响分析和 Source Control 检查。资产删除、重命名、迁移不进入普通低风险入口。

## 4. Agent 使用模型

Agent 不直接面对几百个 MCP Tool，而使用三层结构：

```text
任务级 Workflow
    diagnoseWeaponHitFailure
    auditCurrentMap
    normalizeDataTable
    cloneSkillTree

领域 Operation
    Blueprint / Actor / Animation / Data / Material / PIE

基础 CRUD
    create / read / update / delete / compile / validate
```

MCP 层保持少量稳定入口，Operation Registry 根据任务、当前资产类型和 Editor 状态动态返回相关 Operation，降低 Schema、Token 和误调用成本。

## 5. 性能原则

### Hot Path

当前选择、已加载对象、Graph、Dirty、PIE 和最近日志直接从 Editor 内存读取，目标是毫秒到数百毫秒，不做备份或独立重载。

### Warm Path

Asset Registry、SQLite、增量导出和缓存负责未加载资产、引用与项目搜索。仅在 Revision 变化后刷新对应数据。

### Cold Path

完整语义导出、全项目重建、独立 UE 重载验证和大规模影响分析仅按需执行。

批量任务必须支持分帧、进度、取消、增量结果和摘要优先。实时 Apply 不启动独立 UE；重型验证延迟到用户决定保存之后，并允许多资产批量验证。

## 6. 风险自适应安全

| 等级 | 操作 | 默认保护 |
|---|---|---|
| R0 | 读取、搜索、诊断、批量检查 | 范围、预算、超时、取消 |
| R1 | 可逆值修改 | 精确目标、局部 Snapshot、Transaction、Read-back、不保存 |
| R2 | 结构修改、批量修改 | Change Set Preview、原子 Transaction、编译/验证 |
| R3 | 保存、删除、重命名、迁移 | Policy、Revision、Source Control、Backup、独立 Verify、Rollback |

标准 Agent 模式不开放任意 Python、Console、UObject Method、文件系统路径、Save All 或任意属性写入。Tool/Operation 禁用必须同时从发现结果移除，并在执行边界再次拒绝旧缓存调用。

## 7. 可逆性

一次任务的相关修改应组成一个 Change Set 和尽可能少的 UE Transaction。成功结果必须返回精确的 Session、Transaction、Target、Before/After、Dirty 和 Save 状态。

撤销分为：

- 用户在 Editor 中直接 Ctrl+Z；
- Agent 按 Transaction 精确 Undo；
- Agent Discard 并丢弃事务；
- 保存后根据 Backup Manifest 执行 Revision-aware Rollback。

Undo 前必须确认事务仍匹配、目标没有被用户后续修改、Package 尚未保存。失败时恢复 Snapshot 和原 Dirty 状态，不能留下半修改结果。

## 8. 首批验收场景

1. **Weapon Hit Diagnostic**：结合 Blueprint/C++、Collision、Trace、PIE 和日志定位枪械无法命中的原因，并支持临时可逆试验。
2. **Map Asset Audit**：在 Editor 内批量扫描当前地图，按规则聚合资产和 Component 问题。
3. **DataTable Audit/Normalize**：整表检查、表格级 Diff、选择性接受、单事务批量修改。
4. **Clone Skill Tree**：提取角色 A 的通用结构，为角色 B 创建新结构、替换身份引用并验证残留引用。
5. **Feature Design/Impact Analysis**：结合知识树、项目索引和实时 Editor，为新增功能给出方案、影响范围和分阶段 Change Set。

## 9. 分支职责

- `feature/live-editor-realtime-io`：Live Editor Context、实时 CRUD、批量任务、运行时诊断、Change Set 与 Transaction。
- `feature/memory-context`：Knowledge Tree、Active Work、Context Pack、Evidence 和长期项目认知。
- `main`：已验证公共协议和功能的稳定集成基线。

两条功能线通过 Task Context、Change Set、Evidence、Asset Identity 和 Revision 等公共协议连接。公共协议应先合入 `main`，功能分支再同步，不允许长期各自定义不兼容格式。
