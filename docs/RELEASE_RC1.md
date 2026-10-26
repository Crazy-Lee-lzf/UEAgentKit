# UE Agent Kit RC1 — 内部实验型技术预览

**状态：内部 experimental technology preview，不是 0.8.0 稳定发布，也不是已经发布的 GitHub Release。**  
**包版本仍为 0.8.0。** 本次没有版本号提升、没有 tag、没有 GitHub Release；公开渠道是否发布以及采用哪种发布形态仍待决定。

## 1. 这个 RC1 是什么

RC1 是在 0.8.0 稳定只读与受控写入能力之上，加入一组**默认关闭**的 Blueprint 结构编辑实验能力，用于内部真实 UE 5.6.1 环境验证。它适合内部技术预览、研究和受控测试，不适合作为生产默认配置。

它不是：

- 0.8.0 稳定面的替代或扩展版本号；
- 已经完成的 GitHub Release / tag；
- 任意 Blueprint GraphPatch 执行器；
- 全量 ue-llm-toolkit 替代；
- 独立的 OwnerTrust / T10 服务。

## 2. 真实证据范围

以下结果来自 controller 的本机内部验收记录，**不在本仓库保存原始日志，也不由本页重新执行**。它们只证明所列环境与范围，不构成对所有项目或生产环境的授权。

| 证据 | 结果 |
|---|---|
| Full Python suite | **1840 tests / 18 skipped / 0 failed** |
| Native actions | **57 actions，BUILD SUCCEEDED** |
| 性能 / 索引 / World Partition 真实检查 | **132 checks**（controller-local） |
| Blueprint MCP 五类写 | 独立 UE 5.6.1 disposable host 上的真实 MCP E2E：Preview → Apply → Undo/Redo 验证 → 显式 Save → 新进程 Reload |
| 稳定读取 / 索引 / Memory | 0.8.0 已公开的资产/Blueprint 读取、SQLite/FTS5 索引、Project Memory 与 Knowledge Web 能力 |

**P5 五类 A 级证据不能被解读为生产默认写入安全。** 它是默认关闭的实验写能力在特定 UE 5.6.1 测试环境中的工程验收。

## 3. Blueprint 读取与有界写入

稳定能力包括 Blueprint Graph / Node / Pin / 变量 / 函数 / 宏 / 接口 / Cast / Delegate 等语义读取，canonical context、Graph 结构、Pin 默认值和变量信息的只读发现，以及精确资产/图定位。具体工具与限制以 [Blueprint Graph 读写矩阵](BLUEPRINT_GRAPH_READ_WRITE_MATRIX.md) 和运行时 `ue_get_capabilities` 为准。

RC1 新增的五类**实验**写：

- 有界 GraphEdit：8 类白名单节点及受控 exec/data 连线；
- 未连线输入 Pin 默认值：bool / int / double / string；
- 顶层用户变量创建：bool / int / double / string；
- 单条边的精确 rewire；
- 白名单孤立节点受控删除。

所有五类都要求 Preview、精确 `planId` / confirmation、Apply 内 Undo/Redo 验证、独立显式 Save，以及新进程磁盘 Reload 验证。没有任何自动保存或自动编译。

## 4. 默认关闭与授权边界

实验写必须同时满足：

```ini
[UEAgentKit]
ExperimentalGraphPatchEdit=True
```

```powershell
ue-agent-mcp --enable-live-editor --enable-experimental-graphpatch-edit
```

并且每次 Apply / Save 都要回传 Preview/Apply 产生的精确 confirmation。运行前还要求：

- 精确资产和 Graph 身份（`assetPath` + `graphGuid`）；
- 干净 Package、非 PIE/SIE；
- 当前磁盘 revision 与 Graph fingerprint 匹配；
- 适用的 edge-set hash / 节点身份匹配；
- 目标 Graph 为受支持顶层 K2 图。

实验工具**不经过项目 Write Policy allowlist**。Write Policy 仍适用于稳定写入域；不要把它理解为覆盖这五类实验工具。本仓库也没有独立的 OwnerTrust / T10 服务；实际边界是 MCP CLI flag、项目 ini、精确 plan/confirmation 和 loopback EditorBridge descriptor（随机 `authToken` + 项目路径摘要）。

## 5. 明确限制

- **默认 OFF**：未开启双门禁时实验工具不注册或直接拒绝，不能在生产项目默认开启。
- **Save receipt 仅在当前 Editor 进程内有效（process-local）**：Editor 重启后必须重新 Preview/Apply，不能复用旧 receipt。
- **不自动编译 / 不自动保存**：变量创建保存的是 pending 默认值，可能需要后续显式编译才在生成类 CDO 上生效。
- **nested Graph / 非顶层 Graph 写入不支持**：读取发现与写入是两套边界。
- **复杂 Blueprint、多资产并发、并发 Editor 会话未覆盖**：一次验收只证明一个受控序列。
- **live-only MCP 仍需要一个可用 index DB**：这是当前产品启动限制，不是本 RC1 新增的安全保证。
- **无 OwnerTrust、无全量 LLMToolkit 替代、无任意 GraphPatch**：不要据此推断通用 Blueprint 自动化已经生产可用。
- 删除/rewire 等操作只覆盖文档和矩阵列出的白名单与单对象范围；未列出的节点、变量、Pin 类型和批量操作仍不支持。

## 6. 使用方式

完整的 Preview → Apply → Save 调用顺序、参数大小写差异、错误处理和“不要手写 confirmation”的要求见：

- [Blueprint Agent 端到端实验用法](BLUEPRINT_AGENT_E2E_USAGE.md)
- [Blueprint Graph 读写能力矩阵](BLUEPRINT_GRAPH_READ_WRITE_MATRIX.md)

不要把示例中的占位路径、一次性 plan 或测试资产当成生产默认配置。

## 7. 发布状态

本 RC1 目前只是**内部实验型技术预览**：

- 没有修改版本号（仍为 0.8.0）；
- 没有创建或推送 tag；
- 没有创建 GitHub Release；
- 公开发布方式仍待用户/controller 决定。直接公开当前完整 `main` 历史可能包含内部开发材料，当前不是推荐路径。

发布前应以 [RC1 发布检查清单](RC1_RELEASE_CHECKLIST.md) 为准，并等待明确的发布渠道决策。
