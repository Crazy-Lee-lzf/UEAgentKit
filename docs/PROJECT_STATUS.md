# UE Agent Kit 0.8.0 能力状态

当前已发布版本：**0.8.0**
目标环境：**Unreal Engine 5.6 / Windows / Python 3.11–3.12**


## 产品定位

UE Agent Kit 是 Unreal Engine 的**项目知识层 + 受控修改工作流**，重点是：

1. 把 Unreal 资产、Blueprint 语义、引用关系和 Editor 状态转换为稳定、可搜索的数据。
2. 为 AI Agent 提供项目上下文、影响分析和验证证据。
3. 通过 Policy / Revision / Transaction / Save / Verify 等门禁执行窄范围写入。
4. 保留可追踪的 Project Memory 和变更证据。

它不是通用远程桌面，也不是任意 Unreal Python / Shell 执行器。

## 读取能力

### 离线项目读取

- Asset Registry 可见资产目录和 Package 信息。
- Static Mesh、Skeletal Mesh、Skeleton、Physics Asset、Material、Material Instance、Texture、Animation、DataTable、Data Asset、Niagara、World 等专用读取。
- Blueprint Graph / Node / Pin、变量、函数、宏、接口、Cast、Delegate 等语义。
- Canonical JSON、BPCTX/1、SQLite/FTS5 索引。
- Asset / Symbol / Reference 搜索与反向引用查询。
- Package SHA-256 Revision 与索引新鲜度比较。

### Live Editor 读取

- Editor / PIE/SIE / World / Selection。
- Open Assets 和 Dirty Packages。
- Output Log 和 Blueprint 编译诊断。
- 精确资产 Live Inspect，不为只读查询主动加载无关资产。
- 当前 Blueprint Graph 和选中 Node。

### Project Memory / Knowledge

- Rule、Finding、Decision、Known Issue、Task Record、Runtime Evidence。
- Revision-aware stale / superseded / conflicted 状态。
- 确定性 L0 capture 和 L1 distillation。
- FTS5 召回，以及可选的 Vector + RRF hybrid recall。
- 持久化 L2/L3 项目上下文和有界自动注入。
- 只读 Knowledge Web。

## 写入能力

### Blueprint

支持已注册的窄范围操作，例如变量默认值、组件属性、Pin 默认值和描述。写入受 Policy / Revision / Editor 状态约束，并支持 Transaction、Undo/Discard、显式 Save 和 Verify。

### Data Asset

- 标量属性。
- Object / Class / Soft Object / Soft Class 引用。
- Struct / Array / Set / Map 完整值。

### Material Instance

- Scalar
- Vector
- Texture
- Static Switch

### DataTable

- 单元格。
- 多字段 Row 更新。
- Add / Remove / Rename Row。

### Animation

提供有限的 AnimSequence 实时诊断/修复、Additive Base Pose、比例修复、批处理和重定向辅助 Tool。它不是完整的动画资产编辑器替代品。

## Agent 工作流能力

- Task Context。
- Relevant Asset Discovery。
- Impact Analysis。
- Change Set。
- Semantic Diff。
- Verification Plan。
- Trust Verdict。
- Authorized Save / Strong Verify。
- Recovery / rollback evidence。

## P4 / Perforce

Source Control 为 opt-in 功能。

支持：

- mapping / opened / lock / owner / client / have / head 查询；
- exact-file `p4 edit`；
- 受限 safe sync；
- pending changelist 查询/创建/描述更新；
- exact-file `reopen`；
- resolve preview；
- 满足条件的普通文本 `resolve -am`；
- durable audit receipt。

不支持并且不计划通过通用入口绕过：

- Agent-side P4 Submit；
- P4 Revert；
- P4-managed Delete；
- generic P4 command passthrough；
- `.uasset/.umap` 自动 accept yours/theirs 或自动内容 Resolve。

## MCP Tool 规模

Source Control 默认关闭。0.8.0 当前组合模式为：

| 模式 | 基础 | + Memory | + Source Control | + Memory + Source Control |
|---|---:|---:|---:|---:|
| Offline | 10 | 24 | 16 | 30 |
| Live | 43 | 57 | 49 | 63 |
| Workflow-only | 67 | 81 | 73 | 87 |
| Live + Workflow | 100 | 114 | 106 | 120 |

Tool 数量只是接口规模，不代表每个 Tool 都会修改资产；大量 Tool 是只读查询、规划、验证和状态读取。

## 明确不支持的通用能力

- 任意 Blueprint Graph Node CRUD / 自动布线。
- 任意 Level Actor Spawn/Delete/Transform/Property 编辑。
- Material Graph / Niagara / Sequencer / Control Rig 通用写入。
- PIE 输入注入和录制回放系统。
- 任意 Asset Import / Duplicate / Rename / Delete / Migrate。
- 任意 Console / Python / Shell / UObject Method 执行。
- 自动 Save All。

## 安全模型

核心原则：

- 先理解，再修改。
- 默认只读；写入必须显式启用。
- Write Policy 决定可修改范围。
- Revision 防止基于过期状态写入。
- Dirty Package、Session、Target Identity 等条件不满足时拒绝操作。
- Save 和“任务成功”不是同一件事；成功需要独立验证和证据。
- P4 最终 Submit/Revert/Delete 由人执行。
