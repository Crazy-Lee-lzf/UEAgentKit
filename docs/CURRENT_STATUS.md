# 当前状态

## 项目身份

```text
产品名：UE Agent Kit
当前版本：0.2.3
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
- Asset、Variable、Delegate、Component、Function、Graph、Event 和 Function Entry Symbol。
- Variable Symbol 区分成员变量、函数参数与函数局部变量，并记录所属函数作用域。
- 函数参数区分 `input`、`output`、`return` 和 `inout`，并记录 `value/reference` 传递方式与 Const 属性。
- `FunctionResult` 输出生成参数 Symbol 和 `returns` Reference，关联所属函数、返回节点及上游赋值 Node。
- Event Symbol 区分 Custom Event、Override Event 和普通 Event，并记录签名路径。
- 同 Blueprint 内的 Custom Event 调用可解析到对应 Event Symbol。
- `inherits`、`implements`、`reads`、`writes`、`returns`、`calls`、`interface-calls`、`casts`、`macro-calls` 和 Delegate Reference。
- Blueprint Interface Message 节点单独标记为 `interface-calls`，并记录 Message Dispatch。
- Dynamic Cast 记录声明源类型、目标类型、Pure/Impure 模式以及成功/失败分支目标。
- Event Dispatcher 作为独立 Delegate Symbol，关联签名 Graph、签名函数和 Multicast 属性。
- Delegate 创建、绑定、解绑、广播、Assign 和 Clear 分别使用独立 Reference Kind，并记录目标对象、Handler 和签名。
- Asset Registry 依赖导出为普通 Reference，区分 Hard/Soft Package、Game/EditorOnly、Build、Direct/Indirect 属性和项目/引擎/脚本/Mount 域。
- 依赖目标优先解析为 Asset Symbol；无法解析为资产对象时保留 Package、Primary Asset 或 Searchable Name 标识。
- 对 UE 每次加载会重建 GUID 的 `K2Node_PromotableOperator.ErrorTolerance` 瞬态 Pin 使用确定性派生 ID，其他 Pin 继续保留原生 GUID。
- 反射属性文本中的 `/Engine/Transient.PropertyBag_<hex>` 进程级随机对象名统一归一化为 `/Engine/Transient.PropertyBag_<transient>`，不改写真实资产路径或持久对象名。
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
- 0.2.3 Event / Custom Event / Function Entry 语义导出与 Custom Event 调用关联。
- 0.2.3 成员变量与局部作用域变量区分；函数参数引用可解析到对应参数 Symbol。
- 0.2.3 函数 ReturnValue、命名 Output、Value/Reference 和 Const 参数语义验证。
- `returns` Reference 在 Canonical、BPCTX 和 SQLite 中验证，可定位返回节点及上游值来源。
- 0.2.3 Blueprint Interface Message 与普通函数调用分类验证。
- 0.2.3 Dynamic Cast 源类型、目标类型、模式和分支语义验证。
- 0.2.3 Event Dispatcher Symbol、自有 Dispatcher 广播、原生 Delegate 绑定、外部 Blueprint Dispatcher 绑定/解绑验证。
- Delegate Symbol 检索和跨资产反向引用查询已通过 SQLite 验证。
- 0.2.3 Asset Registry Package 依赖在 45 个 Blueprint 上验证：543 条依赖边，其中 Hard Package 194 条、Soft Package 349 条。
- Asset Registry 依赖的 Canonical、BPCTX、SQLite 正向/反向查询和同目标多属性边保留均已验证。
- 两个独立编辑器进程分别完整导出 45 个 Blueprint，45 个 Canonical 与 45 个 BPCTX 均逐文件 SHA-256 完全一致。
- Python 单元测试与 SQLite/FTS 查询。
- 完整导出前后 434 个测试 `.uasset` 的数量与 SHA-256 均未变化。

## 当前限制

- 尚未实现声明式 Patch、Dry Run、保存和回滚。
- Soft Object/Class 变量和同步/异步加载节点仍需补充专用测试资产与语义建模；当前 45 个 Blueprint 中没有可用样本。
- 函数 Input、Output、Return、Const Reference 和 `FunctionEntry.LocalVariables` 已建模；当前测试工程没有真实 `inout` 参数或多 FunctionResult 节点函数，因此这两类仍缺资产验证。
- 当前 Event 调用关联优先覆盖同 Blueprint 内的 Custom Event；跨 Blueprint Event 仍需继续建模。
- Delegate 已记录创建、绑定、解绑和广播的静态关系，但不会推断一次广播在运行时实际触发的 Handler 集合或执行顺序。
- `Assign Delegate` 和 `Clear Delegate` 代码路径已实现并通过编译，但当前测试工程没有对应节点，尚缺真实资产验证。
- Asset Registry 的 Manage 与 Searchable Name 代码路径已实现；当前 45 个 Blueprint 只产生 Package 类别依赖，因此这两类尚缺真实资产验证。
- 同一目标可能同时存在 Hard Game 与 Soft Editor Build 等多条边；这是 Asset Registry 的不同依赖属性记录，不做合并。
- `UK2Node_Message` 已作为接口消息建模；强类型直接接口调用仍按普通函数调用输出，后续可进一步细分。
- Dynamic Cast 的源类型当前为 Cast 输入 Pin 的声明类型，不追踪上游表达式的运行时具体类型。
- Widget Tree、Slot、Binding 和 Widget Animation 尚未完整建模。
- Anim State Machine、State、Transition 和 Pose Link 尚未完整建模。
- Control Rig Hierarchy 和 RigVM 尚未完整建模。
- 当前仅正式验证 UE 5.6；其他 UE 版本需要单独编译和兼容适配。

## 下一阶段

```text
重命名和文档基线
→ 0.2.2 全项目导出与全新数据库闭环
→ 0.2.3 Event、Custom Event、Function Entry、完整函数参数、局部变量、接口消息、Dynamic Cast、Delegate 与 Asset Registry Package 依赖语义
→ 建立 Soft Reference、Manage Dependency 与 Searchable Name 专用测试资产
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
