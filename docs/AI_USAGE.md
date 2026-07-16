# AI 使用指南

## 1. 目标

本工具为 AI 提供可验证、可定位、按需加载的 UE5 项目上下文。当前公开版本仅支持只读分析。

AI 不应把 Blueprint 当作普通文本文件处理，也不应直接修改 `.uasset` 二进制文件。

## 2. 查询工作流

### 查阅单个资产

推荐顺序：

```text
1. 获取资产摘要
2. 获取结构信息
3. 定位相关变量、函数或 Graph
4. 只展开相关节点和 Pin
5. 查询外部引用和依赖
6. 给出带资产路径、Graph 名和 Node GUID 的结论
```

避免一次读取整个大型 Blueprint。

### 全项目检索

优先使用结构化查询：

- 精确资产路径。
- 类名、变量名和函数名。
- 变量 Read/Write 引用。
- 函数 Caller/Callee。
- 接口实现。
- 继承关系。
- 资产依赖。
- 默认值和组件属性覆盖。

语义检索只作为补充，不能替代结构化引用关系。

## 3. 输出 Profile

### index

只用于快速资产发现和清单查询。

### structure

用于类、变量、组件、函数签名和接口分析。

### logic

用于 Graph、Node、Pin 和连接关系分析。

### defaults

用于 Blueprint CDO 和组件模板默认值分析。

### full

用于归档、回归测试和完整 Diff，不应默认塞入 AI 上下文。

### ai

用于面向模型的紧凑上下文。仍应按资产、函数或 Graph 分片加载。

## 4. 事实和推断

AI 输出应区分：

```text
事实：直接来自 Blueprint 导出、索引或编译结果。
推断：根据调用关系、变量命名和控制流做出的解释。
未知：当前适配器没有导出的专用信息。
```

例如 Anim Blueprint 能导出通用 Graph，并不代表已经完整理解 State Machine 和 Transition 语义。

## 5. 修改工作流

未来写入功能必须使用声明式 Patch：

```text
读取当前 Revision
→ 查询引用和影响范围
→ 生成 Patch
→ Dry Run
→ 编译验证
→ 结构化 Diff
→ 用户确认
→ Commit
→ 重新加载验证
```

AI 不得：

- 直接写 `.uasset` 文件。
- 跳过 Revision 检查。
- 跳过备份。
- 在编译失败时请求保存。
- 将 Dry Run 结果描述为已保存。
- 在未允许写入的项目中尝试 Commit。

## 6. 修改前的影响分析

删除或重命名变量、函数、接口、Graph 和组件前，必须先查询：

- 当前资产内引用。
- 其他 Blueprint 引用。
- 子类和实现类。
- 默认值覆盖。
- 资产依赖。

存在引用时，应优先生成迁移计划，而不是直接删除。

## 7. 结果可追溯性

查询和修改结果应尽量返回：

- Asset Path。
- Package Name。
- Revision。
- Graph Name 和 Graph GUID。
- Node GUID。
- Pin ID 或稳定 Pin Key。
- Symbol 名和引用类型。
- 编译结果。
- Diff 摘要。

这样用户和后续 AI 可以重新定位同一对象。

## 8. 当前版本注意事项

当前版本支持读取、Revision、Symbol/Reference 导出，但尚未提供 SQLite 查询服务和 Blueprint 写入能力。AI 可以：

- 查阅 Blueprint 结构。
- 分析变量、组件、函数、节点和连线。
- 根据 Canonical JSON 或 BPCTX 查询当前导出范围内的继承、接口、变量读写、函数调用和宏调用。
- 生成修改建议或未来 Patch 草案。

AI 不能声称已经：

- 修改变量。
- 新增节点。
- 保存 Blueprint。
- 回滚资产。

当前公开能力以仓库根目录 `README.md` 为准。
