# UE Agent Kit

[English](README_EN.md)

> 将 Unreal Engine Blueprint 转换为 AI 和开发者可检索、可追踪的项目知识。

UE Agent Kit 是一套面向 Unreal Engine 的开源 Blueprint 分析工具。它通过 UE Editor 插件读取 Blueprint，再使用 Python CLI 和 SQLite 建立项目级索引，帮助开发者或 AI 快速定位资产、变量、函数、调用关系和依赖关系。

当前版本为 **0.2.4**，支持 **Unreal Engine 5.6**。现阶段仅执行只读分析，不修改或保存 `.uasset`。

> **AI Generated**：本项目的代码和文档主要由 AI 生成，并通过人工审查、UE 5.6 编译、自动化测试和真实工程回归验证。

## 可以用来做什么

Blueprint 保存在二进制资产中，普通文本搜索无法回答很多项目级问题。UE Agent Kit 可以帮助查询：

- 某个变量在哪些 Blueprint 中被读取或写入。
- 哪些资产调用了某个函数、接口消息、宏或 Event Dispatcher。
- 一个 Blueprint 继承了什么、实现了哪些接口、依赖了哪些资产。
- Soft Reference、PrimaryAssetLabel Manage 和 DataTable Row 等引用来自哪里。
- 某个大型 Blueprint 的 Graph、Node、Pin 和连接结构是什么。

导出结果可以直接检查，也可以导入 SQLite，供 CLI、脚本或后续 AI 工具按需查询，而不必反复加载完整 Blueprint。

## 主要能力

- 读取 Blueprint 的类、父类、接口、变量、默认值、组件、函数和 Graph。
- 导出 Node、Pin、连接关系及常用节点属性。
- 识别变量读写、函数和宏调用、接口消息、Dynamic Cast、Delegate、继承与接口实现关系。
- 区分成员变量、局部变量、输入、输出、返回和引用参数。
- 分析 Hard/Soft Package、Soft Object/Class、Manage 和 Searchable Name 依赖。
- 输出 Canonical JSON、BPCTX/1 和 Manifest。
- 使用 SQLite/FTS5 建立增量项目索引，并支持正向和反向引用查询。
- 支持中文路径、Unicode 内容和离线环境。

## 快速开始

### 环境要求

```text
Windows 10 / 11
Unreal Engine 5.6
Visual Studio C++ Toolchain
PowerShell 5.1+
Python 3.11 或 3.12
```

### 1. 构建插件

```bat
scripts\BuildPluginDirect.cmd
```

默认输出到：

```text
Build\Compiled\UEAgentKit
```

### 2. 导出 Blueprint

```bat
scripts\RunExport.cmd -Asset "/Game/Folder/BP_Name" -Profile logic -Format both
```

批量导出目录：

```bat
scripts\RunExport.cmd -Root "/Game/Folder" -Profile ai -Format both -CompactJson
```

### 3. 建立索引并查询

```bat
scripts\ue-agent.cmd index build Output
scripts\ue-agent.cmd index stats
scripts\ue-agent.cmd search assets Door
scripts\ue-agent.cmd search symbols MaxWalkSpeed
scripts\ue-agent.cmd references --target-asset /Game/Characters/BP_Player
```

项目路径、UE 安装路径、插件安装和完整参数说明见 [`docs/BUILD_AND_RUN.md`](docs/BUILD_AND_RUN.md)。

## 输出内容

```text
Output\
├─ manifest.json
├─ canonical\
└─ bpctx\
```

- **Canonical JSON**：完整、稳定的 Blueprint 事实模型。
- **BPCTX/1**：适合 AI 按需读取的紧凑文本格式。
- **SQLite Index**：用于项目级资产、Symbol 和 Reference 检索。

## 文档

- [`docs/BUILD_AND_RUN.md`](docs/BUILD_AND_RUN.md)：构建、安装和运行。
- [`docs/AI_USAGE.md`](docs/AI_USAGE.md)：AI 使用导出与索引的方式。
- [`spec/BPCTX_FORMAT.md`](spec/BPCTX_FORMAT.md)：BPCTX/1 格式规范。

完整文档索引见 [`docs/README.md`](docs/README.md)。

## 安全说明

当前公开版本只读，不会保存 Blueprint。后续写入功能也不会直接修改 `.uasset` 二进制，并将通过 Revision 校验、Dry Run、编译验证、备份和回滚流程执行。

## License

UE Agent Kit 使用 [MIT License](LICENSE)。

本项目采用独立实现方式。第三方项目仅用于研究架构、工作流和 Unreal API 使用方式，相关规则见 [`docs/REFERENCE_POLICY.md`](docs/REFERENCE_POLICY.md)。

UE Agent Kit 是独立开源项目，与 Epic Games, Inc. 没有隶属、赞助或背书关系。Unreal 和 Unreal Engine 是 Epic Games, Inc. 在美国及其他地区的商标或注册商标。
