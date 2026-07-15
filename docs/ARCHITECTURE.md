# 系统架构

## 1. 总体结构

```text
AI Client / Claude Code / ChatGPT
              │
          MCP / JSON-RPC
              │
      Protocol and Policy Layer
              │
┌────────────────────────────────────┐
│ UE5.6 Editor Plugin                │
│                                    │
│ Core                               │
│ Read Adapters                      │
│ Search/Index Producer              │
│ Patch Planner and Validator        │
│ Write Operations                   │
│ Compile/Save/Rollback              │
│ Commandlets                        │
└────────────────────────────────────┘
              │
      Canonical JSON / BPCTX
              │
       SQLite + FTS5 + Graph Index
```

## 2. 模块规划

第一版暂时保留单个 Editor Module，先通过目录和命名空间分层。功能稳定后再决定是否拆分多个 UE Module。

```text
BlueprintContextToolEditor/
├─ Public/
│  ├─ Core/
│  ├─ Read/
│  ├─ Index/
│  ├─ Patch/
│  ├─ Write/
│  ├─ Verify/
│  └─ Commandlets/
└─ Private/
   ├─ Core/
   ├─ Read/
   ├─ Index/
   ├─ Patch/
   ├─ Write/
   ├─ Verify/
   └─ Commandlets/
```

当前 `FBlueprintContextExporter` 后续迁移为普通 Blueprint 读取适配器，不立即重命名整个插件。

## 3. 核心领域模型

### 3.1 Asset Identity

每个资产应包含稳定身份信息：

- 规范化 Object Path。
- Package Name。
- Package GUID 或可用的内容指纹。
- Engine Version。
- Exporter Version。
- Schema Version。
- 资产类和 BlueprintType。

Patch 必须携带 `baseRevision`，防止 AI 基于旧上下文覆盖用户的新修改。

### 3.2 Canonical Model

Canonical JSON 是完整事实层，应包含：

```text
Asset
├─ Identity
├─ Class and Inheritance
├─ Interfaces
├─ Variables
├─ Components
├─ Functions
├─ Graphs
│  ├─ Nodes
│  ├─ Pins
│  └─ Links
├─ Defaults and Overrides
├─ Dependencies
└─ Summary
```

后续新增信息时优先扩展 Canonical Model，再从同一模型生成 BPCTX 和索引记录。

### 3.3 Symbol Model

符号至少分为：

- Asset
- Class
- Interface
- Variable
- Component
- Function
- Event
- Macro
- Graph
- Node
- Property

每个符号需要定义位置和引用关系：

```text
Definition
Read Reference
Write Reference
Call Reference
Implement Reference
Override Reference
Dependency Reference
```

### 3.4 Patch Model

Patch 使用声明式操作列表，而不是暴露任意 UObject 调用。

```json
{
  "schemaVersion": "1.0",
  "asset": "/Game/AITest/BP_TestCharacter",
  "baseRevision": "...",
  "mode": "dry-run",
  "operations": [
    {
      "op": "setVariableDefault",
      "variable": "MoveSpeed",
      "value": "750.0"
    }
  ]
}
```

操作结果必须包含：

- 每一步的成功或失败。
- 失败原因。
- 修改前值和修改后值。
- 编译结果。
- 是否保存。
- 备份位置。
- 重新导出的 Revision。

## 4. 读取适配器

### 4.1 普通 Blueprint

第一版主适配器，负责：

- 变量、函数、接口。
- SCS 组件树。
- CDO 和组件模板属性。
- 图、节点、Pin 和连线。
- 常见 K2 节点语义。
- 变量读写、函数调用和事件关系提取。

### 4.2 Widget Blueprint

后续专用字段：

- Widget Tree。
- Panel Slot 和布局属性。
- Binding。
- Widget Animation。
- Designer 层级与 Graph 逻辑关联。

### 4.3 Anim Blueprint

后续专用字段：

- AnimGraph。
- State Machine、State、Transition。
- Transition Rule。
- Pose Link。
- Anim Node 专用引用。

### 4.4 其他适配器

按需求增加：

- Control Rig
- Material
- Niagara
- Behavior Tree
- StateTree
- DataTable
- Enhanced Input

专用适配器不能破坏通用 Blueprint 导出；不支持的专用结构仍应保留通用节点和反射属性。

## 5. 索引架构

### 5.1 SQLite 结构

建议第一版使用 SQLite：

```text
assets
symbols
variables
functions
graphs
nodes
references
dependencies
inheritance
interfaces
property_overrides
exports
operations
```

### 5.2 FTS5

全文字段包括：

- 资产路径和名称。
- 变量和函数名。
- 节点标题。
- 注释和 Tooltip。
- 类名、接口名和属性名。
- AI 摘要，后续阶段加入。

### 5.3 图关系

不强制引入图数据库。第一版通过 SQLite 邻接表保存：

```text
from_symbol_id
to_symbol_id
reference_kind
asset_id
graph_id
node_guid
pin_id
```

这样可以支持调用链、变量读写、继承和依赖查询。

### 5.4 增量更新

缓存键至少包含：

```text
Asset Path
Package GUID / Content Hash
Engine Version
Exporter Version
Schema Version
Profile
```

只有资产版本变化时才重新导出和重建相关索引。

## 6. 查询接口

第一版高层查询接口：

```text
search_assets
search_symbols
find_references
get_asset_summary
get_blueprint_structure
get_graph
get_callers
get_callees
get_variable_reads
get_variable_writes
get_dependencies
get_inheritance
```

查询结果必须返回可继续展开的 ID，而不是一次返回所有节点详情。

## 7. 写入接口

第一版低风险操作：

```text
setVariableDefault
addVariable
removeVariable
renameVariable
setComponentProperty
setPinDefault
```

第二阶段再增加：

```text
addNode
removeNode
connectPins
disconnectPins
addFunction
setFunctionParameters
replaceFunctionCall
```

## 8. 命令行和 MCP

### 8.1 Commandlet

负责：

- 无界面导出。
- 项目索引。
- Patch Dry Run。
- Patch Commit。
- 批量验证和 CI。

### 8.2 MCP

MCP 只暴露少量高层工具：

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

具体修改操作作为 Patch 内部操作，不为每种节点或属性创建单独 MCP Tool，避免工具定义膨胀。

## 9. 数据流

### 9.1 查询

```text
User Query
→ MCP Query
→ SQLite/FTS/Graph Index
→ Load Minimal Canonical/BPCTX Context
→ AI Explanation
```

### 9.2 修改

```text
User Request
→ Read Current Asset and References
→ AI Creates Patch
→ Validate Policy and Revision
→ Backup
→ Apply In Memory
→ Refresh and Compile
→ Re-export and Diff
→ Explicit Commit
→ Save and Reload Verification
```

## 10. 版本策略

- 代码第一版仍使用 UE5.6。
- Schema 独立版本化。
- Patch Schema 独立版本化。
- 不直接依赖第三方项目的私有格式。
- 参考第三方实现时记录来源、许可证和采用的设计，不复制许可证不兼容代码。

## 11. 可移植性约束

- Python 查询和 MCP 层使用项目本地 `.venv`，开发基线为 Python 3.12，兼容 Python 3.11。
- 核心功能不得依赖系统 PATH、全局 Python 包、固定盘符或全局 UE 插件安装。
- 工具根目录由脚本自身位置推导；UE 和项目路径通过参数、配置或环境变量传入。
- 索引主键使用 UE Object Path/Package Name，不使用本地 Content 绝对路径。
- Git、P4 和无版本控制环境都必须可运行。
- 所有路径和协议必须支持 Unicode。

详细规则见 `PORTABILITY.md`。