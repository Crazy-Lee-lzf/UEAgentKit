# UE Agent Kit 路线图

更新时间：2026-07-24

当前版本为 **0.5.1**，支持 Unreal Engine 5.6。

UE Agent Kit 的长期定位是面向 AI Agent 的 Unreal Engine 项目智能层：既提供独立的项目读取和受控写入能力，也提供带 Revision 的长期项目记忆、证据驱动分析、影响评估和验证闭环。

项目不以工具数量为目标。后续开发顺序以实际游戏开发工作流为准：先补齐日常 MCP 与 Live Editor 工具，再建设项目记忆和分析能力，最后按程序、策划配置、美术、LD 和 QA 的真实痛点扩展专用能力。

## 已完成检查点

```text
0.2.x  项目级只读分析、Canonical/BPCTX、SQLite/FTS
0.3.x  Blueprint 与非 Blueprint 安全写入基础
0.4.0  Material Instance 与 DataTable 常用写入
0.4.x  Backup Manifest、rollback、Fixture 和完整安全回归
0.5.0  本地 MCP 第一版：查询、Patch、Dry Run、Commit、验证和回滚
0.5.1  MCP 协议补全：状态、分页、新鲜度、高层写入、诊断和 Client 兼容
```

0.5.1 已完成离线 MCP 查询、受控写入和协议诊断。0.5.2 已完成 localhost Editor Bridge、日志与编译诊断、实时资产检查，以及配对 Revision Export + SQLite Generation 的安全单资产刷新；下一批收紧 Dirty UObject、磁盘与索引的细粒度状态模型。

## 0.5.x：MCP 与日常开发工具补全

目标：让 UE Agent Kit 在日常 UE5.6 开发中持续可用，不要求用户或 Agent 手工拼接底层命令和完整 Patch JSON。

### 0.5.1：MCP 查询与协议补全

- [x] 增加项目、索引和能力状态查询。
- [x] 统一 Tool 错误码、分页、局部展开、结果截断和重试语义。
- [x] 比较 SQLite、Revision Export 和磁盘 Package Revision，明确 `fresh`、`stale`、`partial`、`unavailable` 与 `unknown`。
- [x] Commit 后标记固定快照 stale，Verify 保持 stale，精确 rollback 后恢复 fresh。
- [x] 优化 `ue_search`、`ue_get_asset` 和 `ue_find_references` 的过滤、分页、section 和 Token Budget。
- [x] 完成单资产分阶段刷新与 immutable Server 新会话安全重载方案。
- [x] 为现有写入 Operation 提供六个高层参数入口，由 Server 自动生成严格 Patch，并支持 Plan/Dry Run。
- [x] 区分 Policy、Revision、Dirty、超时、UE Crash 和报告错误，并返回脱敏 diagnostic/report ID。
- [x] 补充 Claude Code Schema、标准 `structuredContent` 契约、官方 Python Client 和原始 JSON-RPC Client 回归矩阵；托管 ChatGPT UI 不属于本地自动化范围。

### 0.5.2：Live Editor Read

建立受限的 Editor Bridge，只提供明确注册的高层能力，不开放任意 UObject、Console、Python、Shell 或文件操作。

已完成：

- [x] 仅绑定 `127.0.0.1` 的临时 TCP Listener 与随机会话令牌。
- [x] 固定 Project Path 摘要、Plugin/Server 版本和 Capability 握手。
- [x] Editor 不在线时稳定降级，离线 SQLite Tool 继续工作。
- [x] `ue_editor_status`。
- [x] `ue_get_selection`。
- [x] `ue_get_open_assets`。
- [x] `ue_get_dirty_assets`。
- [x] `ue_get_current_level`。
- [x] `ue_get_pie_state`。
- [x] 区分 Editor Memory、磁盘 Package Revision 与 SQLite Snapshot。
- [x] 真实 UE5.6 Editor + MCP `stdio` 联调与端点/令牌脱敏验证。

后续：

- [x] `ue_get_output_log`：4096 条环形缓冲、单条 1024 字符上限、序号游标和 Category/Verbosity/关键词/UTC/PIE 过滤。
- [x] `ue_get_compile_errors`：当前会话编译相关日志与已加载 Blueprint 编译状态，明确标记历史不完整。
- [x] `ue_inspect_asset_live`：Asset Registry + 已加载内存状态，不触发 `LoadObject`。
- [x] `ue_refresh_asset_index`：精确授权资产、Preview/Apply、独立 Package SHA-256 校验、Revision Export 与 SQLite 配对 Generation、完整校验、原子 Pointer 切换和新会话可见性。
- [x] 工作流会话冻结：首代从外部快照独立复制；内部不可变 Generation 可直接固定；Apply 后旧会话保持旧代并拒绝新 Plan/Receipt，重启后新会话读取新代。
- [x] Dirty Live Editor 资产拒绝、磁盘空间预检、失败保持旧 Pair、配置源快照零修改和真实 UE5.6 双会话刷新回归。
- [ ] Dirty UObject 与磁盘/索引差异的更细粒度状态模型。

### 0.5.3：Daily Actions 与验证

首批低风险操作：

```text
ue_open_asset
ue_focus_asset
ue_sync_content_browser
ue_focus_actor
ue_compile_blueprint
ue_validate_asset
ue_validate_folder
ue_run_automation_test
ue_save_authorized_asset
```

所有写入或保存操作继续受 Policy、Revision、Dry Run、显式确认、备份和验证约束。禁止提供无范围限制的 `save_all`。

### 0.5.4：日常数据编辑完善

优先补当前项目真实使用频率高的内容：

- DataTable 单 Row 多字段与受控 Row 操作。
- Data Asset 的对象引用、软引用、Struct 和容器值模型。
- Material Instance 参数工作流统一。
- Blueprint 默认值、组件属性和 Pin 默认值的高层 MCP 封装。
- 单资产多 Operation 原子 Dry Run、Commit 和 rollback。

暂不优先实现完整 Blueprint Graph、Anim State Machine、Control Rig、Sequencer 和任意脚本执行。

## 0.6.0：带 Revision 的项目长期记忆

项目记忆是 0.5.x 之后的最高优先级能力。

目标：让 AI 不再在每次会话中从零开始，同时避免过期知识长期污染分析结果。

### 记忆类型

```text
ProjectFact       从资产、代码、索引或运行证据提取的项目事实
ProjectRule       用户或团队确认的开发规则和安全约束
DecisionRecord    已采用或放弃的方案及原因
KnownIssue        已知问题、触发条件、规避方式和当前状态
TaskRecord        一次任务的目标、证据、结论、修改和验证结果
RuntimeEvidence   日志、测试、性能数据、崩溃和 PIE 结果
```

### 每条记忆必须记录

- `projectKey`、Engine Version 和适用范围。
- 来源类型和可追溯 Source ID。
- 相关 Asset、Symbol、Graph、Node、DataTable Row 或日志范围。
- 创建时间、最后验证时间和置信度。
- 关联 Package Revision 或 Revision Set。
- `valid`、`stale`、`conflicted`、`superseded` 等状态。
- 用户确认、工具观测和模型推断必须明确区分。

### 失效与冲突规则

- 关联资产 Revision 变化后，事实记忆不能继续视为已验证事实。
- 用户确认的项目规则不会因资产变化自动删除，但可以被新规则替代。
- 相互矛盾的记忆必须并存并标记冲突，不允许静默覆盖。
- 模型推断不能直接升级为项目事实，必须经过证据或用户确认。

### MCP 能力

```text
ue_memory_search
ue_memory_get
ue_memory_add_rule
ue_memory_record_finding
ue_memory_mark_superseded
ue_memory_validate
```

0.6.0 首版以可追溯、可失效和可检索为重点，不追求自动生成大规模知识总结。

## 0.7.0：上下文与分析能力

建立在索引、Live Editor 和项目记忆之上：

```text
ue_analyze_task
ue_build_context
ue_trace_value
ue_trace_execution
ue_analyze_impact
ue_diff_asset
ue_create_hypotheses
ue_create_change_plan
ue_create_verification_plan
```

首批验收任务：

1. 一个变量或配置值最终从哪里产生。
2. 一个函数、Blueprint 或资产被哪些内容使用。
3. 修改或删除某个资产可能影响什么。
4. 两个资产版本发生了哪些语义变化。
5. 根据日志、静态结构和历史结论维护可证伪的根因假设。

分析结论必须附带证据来源；无法证明的内容必须标记为推断。

## 0.8.0：协作、冲突与岗位能力

### 多人协作与资产冲突

重点考虑 Perforce/Git 工作区中的资产冲突、锁定和责任边界：

- 当前 Source Control Provider 状态。
- 资产是否被 Checkout、Lock，以及持有人。
- 本地 Dirty、磁盘 Revision、Depot/Remote Head 的差异。
- 多人同时修改同一 Package 或强关联资产的冲突风险。
- 修改计划涉及的资产是否越过预设责任范围。
- Commit 前冲突预检和风险报告。
- 资产锁定、所有权和责任范围仅做分析与提示，不自动抢锁或覆盖他人修改。

Perforce/Git Changelist 的语义审查暂缓。当前团队 Changelist 缺少稳定规范和元数据，在规则建立前不把 CL 描述作为可靠分析输入。

### 岗位能力包

按真实项目需求逐步建立：

```text
Programmer Pack     C++/Blueprint/Config/日志/网络/性能关联
Designer Data Pack  DataTable、Data Asset、公式、范围和引用验证
Art & TA Pack       导入设置、LOD、材质、纹理、骨骼和资产预算
Level Design Pack   World Partition、Data Layer、HLOD、导航和空间审计
QA & Review Pack    复现、自动测试、Revision 绑定和变更风险
```

每个能力包由 Reader、分析规则、安全写入、验证工具和报告组成，不以单独增加 Tool 数量作为完成标准。

## 0.9.0 及以后

根据前述阶段暴露的真实缺口补充：

- Blueprint Graph 节点、Pin 和函数图编辑。
- Anim State Machine、Montage、Control Rig、Material Graph 和 Niagara。
- PIE 输入记录、回放和自动验收。
- 性能数据到 Actor、资产、材质和逻辑的归因。
- UE 5.4/5.5/5.7+ 兼容矩阵。

## 暂不作为当前主线

- 为追赶同类项目而一次性补齐所有 Editor Operation。
- 无安全边界的任意 UObject、Python、Console、Shell 或文件系统接口。
- 无约束批量修改、批量删除或 `save_all`。
- 在缺少团队规范时依赖 Changelist 描述进行自动语义审查。

## 版本原则

- UE Agent Kit 保持独立实现和独立安装，不依赖其他 UE Agent 插件才能工作。
- 先满足当前真实开发需求，再补暂时不用的 Reader 或 Writer。
- 每个写入 Operation 独立完成 Schema、Policy、Dry Run、Commit、备份、重载和负面测试。
- 每条项目记忆必须可追溯，并能在 Revision 变化后失效或重新验证。
- 正式项目默认只读，不直接编辑 `.uasset` 二进制文件。
