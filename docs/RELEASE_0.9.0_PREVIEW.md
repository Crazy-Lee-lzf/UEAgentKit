# UE Agent Kit 0.9.0 Developer Preview 1（已发布）

**状态：预发布（Prerelease），2026-10-30 发布。** 这是历史发布说明，不是下一版升级说明，也不是生产授权。稳定版仍为 **0.8.0**。

- [GitHub Release 与安装包](https://github.com/Crazy-Lee-lzf/UEAgentKit/releases/tag/v0.9.0-dev1)
- [源代码 Tag `v0.9.0-dev1`](https://github.com/Crazy-Lee-lzf/UEAgentKit/tree/v0.9.0-dev1)
- 版本：`0.9.0.dev1`；环境：Unreal Engine 5.6 / Windows x64 / Python 3.11–3.14

## DP1 包含什么

- **Blueprint 读取**：Graph、Node、Pin、变量及引用，区分索引快照与实时 Editor。
- **实验性 Blueprint 修改**（默认关闭）：八种 primitive 节点与变量 accessor 的创建/连线、未连线输入的 Pin 默认值、顶层成员变量创建、单边 rewire、孤立节点移除；Preview、精确确认、Apply、显式 Save、Verify 分离。
- **PIE 只读诊断**：Server/Client World、Actor、Transform、NetRole、组件与指定标量属性；已解析属性可报告反射声明类。
- **C++ 静态分析**：符号、调用点与来源证据；不宣称运行时分派。
- **已有能力**：受限 DataTable / Data Asset / Material Instance 工作流、Project Memory、按需开启的 P4 操作。
- 旧版单 writer 工具（pin-default / variable / rewire / node-remove / graph-edit 外壳）是 DP1 的写入口；它们已在后续开发中被统一的原子 patch 取代。

## 验证范围

- GitHub Actions：Python 3.11、3.12、3.13、3.14 四项通过。
- Windows 发行校验通过：完整 Python 测试、Ruff 与打包检查无失败。
- UE 5.6 Win64 原生 UAT 通过，不依赖 AutoSDK Junction。
- 从发布 ZIP 解压加载的 DLL 通过 `UEAgentKit.P0Pie` 原生测试，0 错误、0 警告。
- Release ZIP、wheel、SHA256SUMS 与 release-manifest 已重新下载并逐文件比对。

这些结果只对应 DP1 的冻结构建，不外推到其他 UE 版本、项目或后续代码。

## 安装

从 Release 下载 `UEAgentKit-0.9.0.dev1-UE5.6-Win64.zip`，把其中的 `UEAgentKit` 目录复制到项目 `Plugins/`。Python 使用对应 `ue_agent_kit-0.9.0.dev1-py3-none-any.whl`。

## 限制

实验性 Blueprint 写入默认关闭，需要 MCP 开关与工程 ini 双重授权，且仅限隔离测试工程。DP1 不支持通用关卡、AnimGraph、MaterialGraph、NiagaraGraph 写入，也不提供运行时 Pin 执行轨迹或完整 GAS 状态。bool/double/string 输入常量、Blueprint 自声明属性来源和 PIE Client 标量值不在 DP1 的原生覆盖范围内。

下一版候选见 [0.9.0 Developer Preview 2](RELEASE_0.9.0_DP2.md)；稳定版见 [0.8.0 Release Notes](RELEASE_0.8.0.md)。
