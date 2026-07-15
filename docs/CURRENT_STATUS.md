# 当前状态

## 项目身份

```text
产品名：UE Agent Kit
仓库名：UEAgentKit
UE 插件：UEAgentKit
编辑器模块：UEAgentKitEditor
Python 包：ue_agent_kit
CLI：ue-agent
Blueprint 子系统：BlueprintContext
紧凑格式：BPCTX/1
```

## 支持环境

```text
Unreal Engine：5.6
插件类型：Editor-only C++
Python：CPython 3.11–3.12
当前模式：只读导出、索引和引用分析
```

## 已实现

### UE 插件

- Blueprint 资产、父类、生成类、Skeleton Class、接口和状态读取。
- Blueprint 变量、类型、Flags 和默认值读取。
- SCS 组件树和组件模板属性覆盖读取。
- 函数签名、Graph、Node、Pin 和完整连接关系读取。
- 节点通用反射属性导出。
- 单资产和目录批量导出 Commandlet。
- `index`、`structure`、`logic`、`defaults`、`full` 和 `ai` Profile。
- Canonical JSON、BPCTX/1 和 Manifest 输出。
- Profile 无关 Asset Revision：Package GUID、文件信息、Dirty 状态和流式 SHA-256。
- Asset、Variable、Component、Function 和 Graph Symbol。
- `inherits`、`implements`、`reads`、`writes`、`calls` 和 `macro-calls` Reference。
- Manifest、Canonical 和 BPCTX 项目身份字段。

### Python 与 SQLite

- SQLite Schema Migration。
- Assets、Symbols、Graphs、Nodes 和 References 数据表。
- FTS5 全文检索。
- Canonical、Manifest 和 BPCTX 导入。
- 基于 Revision、Schema、Exporter、Profile 和文件哈希的增量跳过。
- Profile 等级保护，避免高信息量数据被低 Profile 覆盖。
- 项目身份绑定和跨项目导入拒绝。
- 可选前缀清理，导入失败时拒绝清理。
- CLI：索引构建、统计、资产搜索、符号搜索、引用查询和资产详情。
- 中文路径、Unicode 内容和迁移后导出目录支持。

## 已验证

- UE 5.6 插件编译成功。
- 普通 Blueprint、Actor Component、Interface、Macro Library、Widget Blueprint、Anim Blueprint、Editor Utility Widget 和 Control Rig 的通用结构导出。
- 中文项目路径与中文项目名。
- 单资产和 45 个 Blueprint 的目录批量导出。
- Canonical、BPCTX、Revision 和项目身份一致性。
- Python 单元测试与 SQLite/FTS 查询。
- 被读取的测试 `.uasset` 内容哈希未变化。

## 当前限制

- 尚未实现声明式 Patch、Dry Run、保存和回滚。
- 局部变量、事件定义/调用、接口消息、Dynamic Cast、Delegate、Soft Reference 和 Asset Registry 依赖仍需补充。
- Widget Tree、Slot、Binding 和 Widget Animation 尚未完整建模。
- Anim State Machine、State、Transition 和 Pose Link 尚未完整建模。
- Control Rig Hierarchy 和 RigVM 尚未完整建模。
- 当前仅正式验证 UE 5.6；其他 UE 版本需要单独编译和兼容适配。

## 下一阶段

```text
重命名和文档基线
→ 0.2.2 全项目导出与全新数据库闭环
→ 补充 Blueprint 语义引用
→ 建立专用写入测试资产
→ 声明式 Patch Schema
→ Dry Run
→ 低风险 Blueprint 写入
→ 编译、Diff、保存和回滚
→ MCP 接口
```

## 安全边界

- 生产或正式项目默认只读。
- 写入测试仅在明确授权的测试工程或隔离资产中执行。
- 不直接修改 `.uasset` 二进制文件。
- 未来写入默认 Dry Run。
- 编译失败、Revision 冲突或备份失败时禁止保存。
