# UE Agent Kit 0.9.0 Developer Preview 1（已发布）

**当前最新公开预发布：`v0.9.0-dev1`（Python 包版本 `0.9.0.dev1`），已于 2026-10-30 前发布并合并公开源码至 `main`。** 以前的 `v0.8.0` 仍是上一个稳定版；0.9.0 DP1 是开发预览版，不应当作生产稳定版。

- [GitHub Release 与安装包](https://github.com/Crazy-Lee-lzf/UEAgentKit/releases/tag/v0.9.0-dev1)
- [源代码 Tag](https://github.com/Crazy-Lee-lzf/UEAgentKit/tree/v0.9.0-dev1)
- [Python 3.11–3.14 GitHub CI（四版本通过）](https://github.com/Crazy-Lee-lzf/UEAgentKit/actions/runs/40908281817)

## 已包含的能力

- **Blueprint 读取**：Graph、Node、Pin、变量及引用；区分索引快照和实时 Editor。
- **实验性 Blueprint 修改**：有限白名单节点、受控连线和变量操作；Preview、精确确认、Apply、显式 Save 和 Verify 分离。
- **PIE 只读诊断**：识别 Server/Client World，检查 Actor、Transform、NetRole、组件和明确请求的少量标量属性，可提供反射声明类来源。
- **C++ 静态分析**：符号、调用点和来源证据；不宣称真实运行时调用轨迹。
- **延续已有功能**：受限 DataTable/DataAsset/Material Instance 工作流、Project Memory、按需开启的 P4 安全操作。

## 验收结果

- GitHub Actions Python **3.11、3.12、3.13、3.14 四项全部通过**。
- Windows Python 发行校验：**1,885 项测试，19 项跳过，0 失败**，Ruff 通过。
- UE 5.6 Win64 原生 UAT：**120/120 构建动作成功**，不依赖 AutoSDK Junction。
- 从正式发布 ZIP 解压并加载的 DLL：`UEAgentKit.P0Pie` 原生测试 **2/2 通过，0 错误、0 警告**。
- GitHub 上的 ZIP、wheel、SHA256SUMS 和 release-manifest 已重新下载并逐文件比对。

## 安全边界

实验性 GraphEdit **默认关闭**，需要 MCP 开关和工程配置双重授权，且仅限隔离测试工程。它不是任意节点 CRUD，也不会自动编译或 Save All；不通过通用脚本接口绕过安全门禁。

集成候选曾对有界 Kismet CallFunction、Blueprint 自有 Pure Function 整数常量、无索引 Pure Live MCP、PIE 标量来源进行更广的原生验证；**这不等于最终发行 ZIP 对全部 Blueprint MCP 写入场景均已进行独立端到端复测**。对于 bool/double/string 常量、完整 GAS 状态和运行时 Pin 执行轨迹仍不能作普遍支持承诺；不支持通用关卡、AnimGraph、MaterialGraph 或 NiagaraGraph 写入。

## 环境与安装

**Unreal Engine 5.6 / Windows x64；Python 3.11–3.14。**

从 Release 下载 `UEAgentKit-0.9.0.dev1-UE5.6-Win64.zip`，将其中 `UEAgentKit` 目录复制到项目 `Plugins/`；Python 使用对应 `ue_agent_kit-0.9.0.dev1-py3-none-any.whl`。

[安装和构建](BUILD_AND_RUN.md) · [Agent Quickstart](AGENT_QUICKSTART.md) · [开发者工作流](DEVELOPER_WORKFLOWS.md)

实际启用的 Tool 和运行模式，请以当前部署服务器返回的 `ue_get_capabilities` 为准；0.8.0 的历史 Tool 数量不应当套用到 0.9.0 DP1。
