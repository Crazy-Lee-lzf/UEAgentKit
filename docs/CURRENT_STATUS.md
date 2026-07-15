# 当前状态

## 支持环境

```text
Unreal Engine: 5.6
Plugin Type: Editor-only C++
Current Mode: Read-only export and reference analysis
```

## 已实现

- Blueprint 资产、父类、生成类、Skeleton Class、接口和状态读取。
- Blueprint 变量、类型、Flags 和默认值读取。
- SCS 组件树和组件模板属性覆盖读取。
- 函数签名读取。
- Graph、Node、Pin 和全部连接关系读取。
- 节点通用反射属性导出。
- Canonical JSON、BPCTX/1 和 Manifest 输出。
- 单资产与目录批量 Commandlet。
- `index`、`structure`、`logic`、`defaults`、`full` 和 `ai` Profile。
- Profile 无关的 Asset Revision：Package GUID、文件信息、Dirty 状态和流式 SHA-256。
- Asset、Variable、Component、Function 和 Graph Symbol。
- 继承、接口实现、变量读写、函数调用和宏调用 Reference。
- BPCTX/1 的 Revision、Symbol 和 Reference 记录。
- 通用 Canonical/BPCTX/Revision 验证脚本。

## 已验证的 Blueprint 类型

以下类型已在 UE5.6 真实项目中完成只读导出验证：

- 普通 Blueprint。
- Actor Component Blueprint。
- Blueprint Interface。
- Blueprint Macro Library。
- Widget Blueprint。
- Anim Blueprint。
- Editor Utility Widget Blueprint。
- Control Rig Blueprint。

这些类型都能导出通用 Blueprint 结构。专用资产的专用语义完整度见下一节。

## 当前限制

### Widget Blueprint

尚未完整导出：

- Widget Tree。
- Panel Slot 和布局层级。
- Binding。
- Widget Animation。

### Anim Blueprint

尚未完整导出：

- State Machine。
- State。
- Transition。
- Transition Rule。
- Pose Link 专用语义。

### Control Rig

尚未完整导出：

- Rig Hierarchy。
- Bone、Control 和 Null 层级。
- RigVM 专用模型。

### 写入能力

当前版本没有 Blueprint 修改和保存能力。尚未实现：

- 声明式 Patch。
- Dry Run。
- 变量或组件属性修改。
- 节点创建和连接。
- 编译门禁。
- 保存和回滚。

## 当前开发重点

下一阶段不是继续扩展普通 JSON 导出，而是建立：

```text
SQLite 项目索引
→ 基于现有 Symbol/Reference 的变量读写和函数调用检索
→ 资产依赖和继承查询
→ 声明式 Patch
→ 安全写入
→ 编译验证
→ Diff 和回滚
→ MCP
```

## 安全边界

- 复杂正式项目默认只读。
- 写入测试只在专用测试工程或复制出的测试资产中进行。
- 未来所有写入默认 Dry Run。
- 编译失败或版本冲突时不得保存。

详细设计见：

- `ARCHITECTURE.md`
- `SAFE_WRITE_MODEL.md`
- `ROADMAP.md`
