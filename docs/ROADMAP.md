# 开发路线图

## 阶段 0：基线整理

状态：已完成。

目标：把现有只读导出器整理为后续可扩展基础。

任务：

- 更新产品目标、架构、安全写入和测试沙箱文档。
- 更新 README 和实现状态。
- 保留现有普通 Blueprint 导出能力。
- 记录 UE5.6 编译成功和真实项目兼容性测试结果。
- 初始化本地 Git，但第一版完成前不创建正式版本提交。
- 清理不应进入版本控制的 Build、Output、Backups、AutoSDK 和临时 HostProject。

验收：

- 文档无明显冲突。
- 现有插件仍能编译。
- 生产级复杂项目的只读导出回归仍通过。

## 阶段 1：只读索引 MVP

状态：进行中。Revision、Symbol 和 Reference 导出模型已完成；SQLite/FTS 与查询接口尚未完成。

目标：从“导出文件”升级为“可精确查询的项目知识层”。

### 1.1 读取模型补强

- 提取常见 K2 节点语义。
- 标记变量 Get/Set。
- 标记函数、事件、接口和宏调用。
- 提取当前 Blueprint 定义与引用关系。
- 记录依赖、继承、接口实现和默认值覆盖。

### 1.2 SQLite 索引

- 建立数据库 Schema。
- 从 Canonical JSON 构建索引。
- 增量更新。
- FTS5 全文检索。
- 调用关系和变量读写查询。

### 1.3 查询命令

- `search_assets`
- `search_symbols`
- `find_references`
- `get_asset_summary`
- `get_graph`
- `get_callers/get_callees`
- `get_variable_reads/get_variable_writes`

验收：

- 能回答“变量在哪里被写入”。
- 能回答“谁调用了某函数”。
- 能回答“哪些子蓝图覆盖了属性”。
- 查询结果可继续按资产、图和节点展开。

## 阶段 2：安全写入 MVP

测试项目：配置为允许写入的独立 UE5.6 沙箱工程。

### 2.1 Patch 基础设施

- Patch JSON Schema。
- Operation Registry。
- Asset Revision 检查。
- Project Write Policy。
- Dry Run 和 Commit 模式。
- 外部备份、操作日志和 Diff。

### 2.2 第一批操作

- `setVariableDefault`
- `addVariable`
- `renameVariable`
- `removeVariable`，只允许无引用变量。
- `setComponentProperty`
- `setPinDefault`

### 2.3 编译和保存门禁

- Refresh 节点。
- Compile Blueprint。
- 收集编译错误和警告。
- 重新导出验证。
- 显式保存。
- 重新加载验证。
- 回滚。

验收：

- Dry Run 不改变 `.uasset`。
- Commit 后重启编辑器仍生效。
- 编译失败不保存。
- 错误 Patch 不造成部分写入。
- 备份可恢复。

## 阶段 3：MCP 第一版

目标：让 Claude Code 等 AI 客户端使用统一查询和修改接口。

工具：

```text
ue_search
ue_get_asset
ue_find_references
ue_plan_patch
ue_dry_run_patch
ue_apply_patch
ue_verify_asset
ue_rollback_patch
```

要求：

- MCP 不直接接触 UE 内部写入实现。
- 所有写入经过同一 Patch 和安全策略。
- 返回值支持分页和按需展开。
- 超大 Blueprint 不一次返回完整 JSON。

## 阶段 4：图结构编辑

- 添加和删除节点。
- 变量 Get/Set 节点。
- Call Function 节点。
- 连接和断开 Pin。
- 创建简单函数图。
- 修改函数参数。
- 替换函数调用。

验收重点：

- 使用 Schema 校验连接。
- 节点创建后 GUID、Pin 和图状态一致。
- 编译失败完整回滚。

## 阶段 5：专用资产适配器

优先级：

1. Widget Blueprint。
2. Anim Blueprint。
3. Material。
4. Control Rig。
5. Niagara。
6. Behavior Tree / StateTree。

每种适配器先完成读取和索引，再开放写入。

## 阶段 6：工程化

- 编辑器内状态面板。
- Git/P4 适配。
- CI 回归测试。
- 多项目配置。
- 权限、审计和操作历史。
- 项目级知识摘要和语义检索。

## 第一版定义

“第一版”指阶段 0、1、2 的核心能力完成，并具备最小 MCP 或命令行入口：

```text
普通 Blueprint 可查
→ 全项目可检索
→ 低风险属性可 Dry Run 修改
→ 编译验证
→ 显式保存
→ 可回滚
```

第一版完成后再进行首次正式 Git Commit 和版本标记。
