# 第三方项目参考与采用规则

## 1. 原则

本项目可以模仿公开项目的架构和已验证思路，但不能未经审查直接复制代码。

采用任何第三方实现前必须确认：

- 仓库当前许可证。
- 许可证是否允许修改、分发和商业使用。
- 是否需要保留版权和许可证文本。
- 代码针对的 UE 版本。
- 依赖和构建环境。
- 是否能在 UE5.6 下独立编译。
- 是否存在只在 README 中声明、实际未完成的能力。

在许可证未确认前，只允许：

- 阅读架构。
- 记录 API 使用方式。
- 独立重新实现概念。
- 编写兼容性测试。

## 2. 优先参考项目

### getnamo/BlueprintOracle-Unreal

参考方向：

- Editor Commandlet。
- Blueprint Ground Truth 导出。
- 节点剪贴板文本和字节码辅助信息。
- 声明式修改。
- Dry Run、Compile、Commit 分离。
- Blueprint 写入后验证。

计划采用：

- 安全写入流程思想。
- Patch 操作分层。
- 编译门禁。

不直接采用：

- 未经许可证检查的源代码。
- 与当前 Canonical JSON 冲突的数据格式。

### Jinphinity/BlueprintSerializer

参考方向：

- Blueprint 图、节点、Pin、变量、组件和接口的 JSON 模型。
- AI Context 输出。
- 回归测试覆盖。
- 大 Blueprint 处理方式。

计划采用：

- 完整度检查清单。
- 测试用例设计。
- 缺失字段对比。

### ElgSoft/ElgKismetEditorWidget

参考方向：

- 变量、函数、宏、事件分发器、节点和 Pin 的编辑 API。
- Blueprint Editor 内的结构访问。
- UMG Widget Tree 访问。

风险：

- 目标 UE 版本可能较旧。
- 需要逐项映射到 UE5.6 API。

### winyunq/UnrealMotionGraphicsMCP

参考方向：

- Widget Tree 和 Slot 的结构化表示。
- UMG Animation。
- UI Blueprint 的专用查询和写入。
- MCP 中的高层 UMG 操作。

风险：

- 目标版本较新。
- 不能假设 UE5.6 API 完全一致。

### mirno-ehf/ue5-mcp

参考方向：

- UE 插件、MCP Server 和客户端之间的通信结构。
- Editor 打开与 Headless 两种运行模式。
- AI 驱动 Blueprint 编辑的交互方式。

计划采用：

- 通信层设计参考。

不计划采用：

- 将安全策略放在 MCP 包装层。

### Italink/UnrealClientProtocol

参考方向：

- TCP/JSON。
- UObject 查找。
- UFunction 调用。
- UProperty 读写。
- UE Reflection 查询。

风险：

- 通用反射读写权限过大。
- 不能直接作为 Blueprint 图编辑安全接口。

计划采用：

- 协议和序列化思路。
- 只读反射查询能力参考。

## 3. 对比测试方式

第三方项目不直接安装到生产或正式项目。优先使用独立的 UE5.6 写入沙箱，并通过项目级插件挂载进行隔离测试。

对比维度：

- UE5.6 编译情况。
- 安装和卸载是否污染工程。
- 普通 Blueprint 导出完整度。
- Widget、Anim、Control Rig 专用信息。
- 批量处理稳定性。
- 输出确定性。
- 大 Blueprint 性能和内存。
- 写入前是否支持 Dry Run。
- 编译失败是否保存。
- 是否有外部备份和回滚。
- MCP 工具数量与上下文成本。

## 4. 代码采用记录

后续若采用第三方代码，应建立记录：

```text
Source Repository:
Commit:
License:
Files/Functions Referenced:
Modification Summary:
UE5.6 Compatibility Changes:
License Notice Location:
Tests:
```

建议放入：

```text
docs/third-party/<project-name>.md
ThirdPartyNotices/
```

## 5. 当前结论

现有项目不再追求成为另一个独立 JSON 导出器。核心差异化目标是：

```text
稳定读取
+ 全项目结构化检索
+ AI 紧凑上下文
+ 声明式安全修改
+ 编译验证
+ Diff 与回滚
+ 统一 MCP 入口
```

第三方项目用于减少 API 探索和重复试错，但统一数据模型、安全策略和 UE5.6 兼容性由本项目负责。
