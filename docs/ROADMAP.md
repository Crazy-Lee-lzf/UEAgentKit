# 开发路线图

本路线图描述 UE Agent Kit 的公开开发方向。实际已实现能力以 [`CURRENT_STATUS.md`](CURRENT_STATUS.md) 为准。

## 当前进度

```text
当前版本：0.2.4
当前阶段：只读索引 MVP 已完成，准备进入安全写入 MVP
主要支持：Unreal Engine 5.6、普通 Blueprint 读取与项目级检索
默认策略：只读，不修改或保存 .uasset
```

## 阶段 0：项目基线

状态：已完成。

- 完成产品命名、目录结构、构建脚本和公开文档整理。
- 建立 UE 5.6 Editor-only 插件与 Python CLI 基线。
- 建立只读安全边界、测试沙箱和第三方参考规则。
- 完成 Git 仓库与版本检查点管理。

## 阶段 1：只读索引 MVP

状态：已完成，0.2.4 形成稳定检查点。

目标是把 Blueprint 从“可导出的 JSON”升级为“可精确查询的项目知识层”。

### 已完成能力

- 导出 Blueprint 类、变量、组件、函数、图、节点、Pin 和连接关系。
- 建立 Asset、Symbol、Graph、Node 和 Reference 数据模型。
- 提取变量读写、函数和宏调用、接口消息、Dynamic Cast、Delegate、继承与实现关系。
- 提取 Asset Registry Hard/Soft Package、Manage 和 Searchable Name 依赖。
- 支持 Soft Object / Soft Class 成员变量级引用。
- 输出 Canonical JSON、BPCTX/1 和 Manifest。
- 建立 SQLite/FTS5 项目索引、增量导入和正向/反向引用查询。
- 保证跨独立编辑器进程的 Canonical 与 BPCTX 确定性。
- 提供可重复生成的只读语义测试资产。

### 版本节点

```text
0.2.2  全项目导出、项目身份和 SQLite 闭环
0.2.3  Event、参数、局部变量、接口消息、Cast 与 Delegate 语义
0.2.4  Soft Reference、PrimaryAssetLabel Manage、Searchable Name 与正式 fixture
```

## 阶段 2：安全写入 MVP

状态：下一阶段。

目标是在隔离测试资产中建立可校验、可预览、可回滚的低风险 Blueprint 修改流程。

### 2.1 写入测试基线

- 建立独立的 `/Game/UEAgentKitWriteTests` 测试目录。
- 提供可重复执行的写入 fixture 生成脚本。
- 覆盖正常修改、非法输入、Revision 冲突、编译失败和回滚场景。
- 正式项目继续默认只读，写入只允许在明确授权目录内执行。

### 2.2 Patch 基础设施

- 声明式 Patch JSON Schema。
- Operation Registry 与操作白名单。
- Asset Revision 检查。
- Project Write Policy 与允许目录检查。
- 结构化校验结果、Expected Changes 和风险等级。

### 2.3 第一批低风险操作

按以下顺序开放：

```text
setVariableDefault
→ setComponentProperty
→ setPinDefault
```

`addVariable`、`renameVariable` 和 `removeVariable` 涉及结构重编译与引用迁移，待基础流程稳定后再开放。

### 2.4 Dry Run、编译与回滚

- Dry Run 只在内存中应用修改，不改变磁盘文件。
- Refresh Blueprint 节点并执行编译。
- 重新导出资产并生成结构化 Diff。
- 编译失败、Revision 冲突、备份失败或 Diff 不符时禁止保存。
- 支持内存回滚和外部 `.uasset` 备份恢复。
- 只有显式 Commit 才允许保存，并在重新加载后验证结果。

### 验收标准

- Dry Run 前后 `.uasset` 哈希不变。
- Commit 后重启编辑器仍能观察到预期修改。
- 错误 Patch 不产生部分保存。
- 编译失败不会写入磁盘。
- 备份、Patch、Diff、日志和恢复路径完整。

## 阶段 3：MCP / Agent 接口

目标是让 Claude Code、ChatGPT 等 AI 客户端通过统一接口查询和修改 UE 项目。

计划工具：

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

MCP 不直接暴露任意 UObject 调用。所有写入必须经过同一 Patch Schema、安全策略、编译验证和回滚流程。

## 阶段 4：图结构编辑

- 添加和删除节点。
- 创建变量 Get/Set 与函数调用节点。
- 连接和断开 Pin。
- 创建简单函数图。
- 修改函数参数。
- 替换函数调用。

这一阶段必须依赖 Blueprint Schema 校验，并保证失败时完整回滚。

## 阶段 5：专用资产适配器

计划优先级：

1. Widget Blueprint。
2. Anim Blueprint。
3. Material。
4. Control Rig。
5. Niagara。
6. Behavior Tree / StateTree。

每类资产先完成读取和索引，再单独开放写入。

## 阶段 6：工程化

- 编辑器内状态面板。
- Git / Perforce 适配。
- CI 回归测试。
- 多项目配置与权限策略。
- 审计日志和操作历史。
- 项目级知识摘要与语义检索。

## 第一版产品边界

第一版的核心闭环是：

```text
普通 Blueprint 可查
→ 全项目可检索
→ 低风险属性可 Dry Run 修改
→ 编译与结构 Diff 验证
→ 显式保存
→ 可恢复和回滚
```

MCP 是这一闭环之上的接入层，不应早于安全写入基础设施实现。
