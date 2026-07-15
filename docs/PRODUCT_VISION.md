# UE5 AI 开发辅助工具：产品目标

## 1. 产品定位

本项目从单纯的 Blueprint 上下文导出器，升级为集成以下能力的一体化 UE5 AI 开发工具：

- 查阅（Inspect）：读取 Blueprint、组件、变量、函数、图、节点、Pin、默认值和依赖关系。
- 检索（Search）：在整个 UE 项目中检索资产、符号、变量读写、函数调用、接口实现、继承和属性覆盖。
- 分析（Analyze）：为 AI 提供按需、紧凑、可追溯的项目上下文。
- 修改（Modify）：通过声明式 Patch 修改 Blueprint，而不是让 AI 直接任意操作 UObject。
- 验证（Verify）：修改后执行 Refresh、Compile、重新导出和结构化 Diff。
- 保存与回滚（Commit/Rollback）：只有显式提交且验证通过才保存资产；失败时恢复。

项目顶层名称统一为 `UE Agent Kit`，插件与主模块分别使用 `UEAgentKit` 和 `UEAgentKitEditor`。`BlueprintContext`、`BlueprintContextAnalysis` 与 `BPCTX/1` 继续作为 Blueprint 专用子系统和格式名称，不代表整个项目。

## 2. 核心用户场景

### 2.1 查阅单个 Blueprint

用户询问：

> 分析 BP_PlayerCharacter 的冲刺逻辑。

工具应自动完成：

1. 定位资产。
2. 读取类、变量、组件和相关图。
3. 仅加载与冲刺相关的函数、事件、变量读写和调用关系。
4. 返回可定位到资产、图、节点 GUID 和 Pin 的解释。

### 2.2 全项目检索

用户询问：

> 找出所有修改 MaxWalkSpeed 的地方。

工具应检索：

- Blueprint 变量读写节点。
- 组件属性 Set 节点。
- CDO 和组件模板默认值覆盖。
- 父类与子类中的继承覆盖。
- 相关 C++ 符号引用，后续阶段接入。

### 2.3 受控修改

用户请求：

> 将测试角色的默认移动速度改为 750，并添加 SprintMultiplier 变量。

工具应执行：

1. 生成声明式 Patch。
2. 校验资产版本指纹。
3. 创建外部备份。
4. 默认执行 Dry Run。
5. 在内存中应用修改。
6. Refresh All Nodes（刷新所有节点）。
7. Compile Blueprint（编译蓝图）。
8. 重新导出并生成修改前后 Diff。
9. 只有显式 Commit 且无错误才保存。
10. 记录操作日志和可恢复信息。

## 3. 第一版目标

第一版重点完成普通 Blueprint 的完整闭环，不追求覆盖所有 UE 资产类型。

### 3.1 必须完成

- 普通 Blueprint 完整只读导出。
- 项目资产索引和增量更新。
- 资产、变量、函数、图和节点精确检索。
- 变量定义、读引用和写引用检索。
- 函数/事件调用关系检索。
- 父子类、接口和资产依赖检索。
- 声明式 Patch 数据模型。
- 修改变量默认值。
- 新增和删除 Blueprint 变量。
- 修改组件模板的低风险属性。
- 修改 Pin 默认值。
- Dry Run、备份、编译门禁、Diff、显式保存和回滚。
- 命令行接口和最小 MCP 查询/修改接口。

### 3.2 第一版不强求

- 任意复杂节点自动生成。
- 任意控制流自动重连。
- 完整 Widget Animation 写入。
- 完整 Anim State Machine 写入。
- Control Rig、Niagara、Material、Behavior Tree、StateTree 全量支持。
- AI 自动保存且不经用户确认。

## 4. 设计原则

### 4.1 UE 内部对象是事实源

完整读取和修改由 UE5.6 Editor C++ 插件执行，直接访问：

- `UBlueprint`
- `UEdGraph`
- `UEdGraphNode`
- `UEdGraphPin`
- `USimpleConstructionScript`
- Generated Class 和 CDO
- `FBlueprintEditorUtils`
- `FKismetEditorUtilities`

Python 只用于测试编排、索引后处理、文本转换和辅助脚本，不作为 Blueprint 内部结构的唯一事实源。

### 4.2 MCP 只是协议层

MCP 不直接实现 Blueprint 修改逻辑。所有安全策略、资产事务、编译验证和保存逻辑必须位于 UE 插件内部。

### 4.3 默认只读和 Dry Run

任何写入 API 默认不得保存资产。只有以下条件全部满足时才能 Commit：

- 操作类型在白名单中。
- 目标资产版本与 Patch 基线一致。
- 外部备份成功。
- 内存修改成功。
- Blueprint 编译无错误。
- 重新导出结果满足预期。
- 用户或调用方显式传入 Commit。

### 4.4 输出必须适合 AI 按需读取

Canonical JSON 作为完整事实层；BPCTX 作为紧凑上下文层；SQLite/FTS 作为结构化检索层。AI 不应一次加载整个项目导出结果。

### 4.5 不把试验性修改放进正式项目

项目应按写入策略分级：

- 生产或正式项目：默认只读，只用于兼容性、性能和检索验证。
- 写入沙箱：允许创建、修改、删除和导入专用测试 Blueprint。
- 专用资产工程：需要测试角色、动画或复杂导入资产时，先复制到隔离测试目录再修改。

具体的本机项目路径和测试资产名单属于内部开发配置，不写入正式文档。
## 5. 成功标准

第一版完成时，应能稳定执行以下流程：

```text
用户提出问题或修改请求
    → 精确检索相关资产和符号
    → 加载最小必要 Blueprint 上下文
    → AI 生成解释或声明式 Patch
    → UE 插件 Dry Run
    → 编译验证和结构化 Diff
    → 显式 Commit
    → 保存并重新加载验证
    → 可查询操作记录并回滚
```

测试资产损坏、版本冲突、编译失败或不支持的操作必须明确失败，不得静默保存部分结果。
