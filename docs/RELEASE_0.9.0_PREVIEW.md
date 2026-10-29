# UE Agent Kit 0.9.0 Developer Preview 1（本地候选 0.9.0.dev1）

**尚未发布。** 当前公开版本仍为 **0.8.0**；本页不是升级说明或生产授权。

## 拟纳入的能力

- **Blueprint 读取**：Graph、Node、Pin、变量及引用，区分索引快照和实时 Editor。
- **实验性 Blueprint 修改**：有限白名单节点、精确连线/断线和受控变量操作；Preview、确认、显式 Save、Verify 分离。
- **PIE 只读诊断**：识别 Server/Client World，精确查看 Actor、Transform、NetRole、组件及指定的少量标量属性；已解析属性可报告反射声明类。
- **C++ 分析**：搜索符号、静态调用点及来源；不保证运行时分派。
- **现有工具**：继续提供受控 DataTable/DataAsset/Material Instance 配置操作、Project Memory、可选 P4 安全功能。

## 安全与限制

实验性 GraphEdit **默认关闭，不受稳定 Write Policy 白名单约束**；必须 MCP 开关和工程配置双门禁，且仅供隔离测试工程。它不是任意节点 CRUD，也不会自动编译或 Save All。

本地集成候选已通过 UE 5.6.1 原生验收：有界 Kismet CallFunction、同一 Blueprint 自有 Pure Function 调用及其未连线输入常量（原生实测 int）、无索引 Pure Live MCP 启动，以及 ue_get_pie_scalars 标量读取和反射声明类来源。**候选代码通过验收不代表已公开发布。** bool/double/string 输入常量、Blueprint 自声明属性来源和 PIE Client 标量值尚未得到同等原生覆盖。PIE 不提供运行时 Pin 执行轨迹或完整 GAS 状态；不支持通用关卡、AnimGraph、MaterialGraph、NiagaraGraph 写入。

## 环境与接入

目标：**UE 5.6 / Win64、Python 3.11–3.14**（0.8.0 已发布版为 3.11–3.12）。[安装](BUILD_AND_RUN.md) · [Agent Quickstart](AGENT_QUICKSTART.md) · [开发者工作流](DEVELOPER_WORKFLOWS.md)。

最终能力以冻结构建的 `ue_get_capabilities` 为准。正式发布仍须通过真实 UE/MCP 验收、隐私与版权审计，并获得用户授权；本候选没有 GitHub Tag 或 Release。
