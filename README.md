# UE Agent Kit

[English](README_EN.md)

UE Agent Kit 是一套面向 Unreal Engine 的开源 AI 开发工具。它通过 UE Editor 插件、Python CLI、SQLite 索引和后续 MCP 接口，让 AI 能够按受控流程查询、分析、修改、验证和回滚 UE 项目内容。

当前阶段仍以 Blueprint 只读分析为主，不会修改或保存 `.uasset`。

## 当前功能

- 读取 Blueprint 的类、父类、接口、变量、默认值、组件和函数。
- 导出 Graph、Node、Pin 及完整连接关系。
- 识别继承、接口实现、变量读写、函数调用和宏调用。
- 生成稳定的 Asset Revision 与 SHA-256 内容指纹。
- 输出 Canonical JSON、BPCTX/1 和 Manifest。
- 使用 SQLite/FTS 建立项目级索引。
- 通过 CLI 检索资产、符号和引用关系。
- 支持增量索引、项目身份隔离、中文路径和离线环境。

当前已在 UE 5.6 下完成真实项目的编译、导出、索引和查询验证。

## 最终目标

```text
查询项目
→ 精确定位资产与逻辑
→ AI 生成声明式修改计划
→ Dry Run
→ 创建、修改或删除 UE 内容
→ 编译与依赖验证
→ 结构化 Diff
→ 显式保存
→ 失败自动回滚
```

长期目标不仅包含普通 Blueprint，还包括 Widget、Anim Blueprint、Control Rig、Material、Niagara、DataTable、Behavior Tree、StateTree 等资产类型。

## 项目结构

```text
UE Agent Kit
├─ UEAgentKit                    UE Editor 插件
│  └─ Blueprint Context         Blueprint 读取、分析和后续 Patch
├─ ue_agent_kit                 Python 索引、查询和工具层
├─ Project Index                SQLite/FTS 项目索引
├─ Validation                   编译、Diff 和一致性验证
└─ Agent Bridge                 后续 MCP / Agent 接口
```

`BlueprintContext` 与 `BPCTX/1` 继续作为 Blueprint 子系统和格式名称使用；它们不再代表整个项目。

## 快速开始

构建 UE 插件：

```bat
scripts\BuildPluginDirect.cmd
```

默认编译输出：

```text
Build\Compiled\UEAgentKit
```

运行 Blueprint 导出：

```bat
scripts\RunExport.cmd -Asset "/Game/Folder/BP_Name" -Profile logic -Format both
```

运行索引与查询：

```bat
scripts\ue-agent.cmd index build --export-root Output\Export
scripts\ue-agent.cmd index stats
scripts\ue-agent.cmd search assets Door
scripts\ue-agent.cmd search symbols MaxWalkSpeed
```

详细步骤见 [`docs/BUILD_AND_RUN.md`](docs/BUILD_AND_RUN.md)。

## 当前限制

- 当前公开实现是只读版本。
- 尚未实现 Blueprint Patch、保存和回滚。
- Widget、Anim Blueprint 和 Control Rig 目前只具备通用 Blueprint 结构导出，专用语义仍需补充。
- 当前主要支持并验证 UE 5.6，其他 UE 版本需要单独编译和适配。

## 安全原则

- 不直接修改 `.uasset` 二进制文件。
- 未来所有写入默认执行 Dry Run。
- 写入前校验资产 Revision，拒绝过期 Patch。
- 编译失败、版本冲突或备份失败时禁止保存。
- 正式项目默认只读，写入测试仅在明确授权的沙箱资产中进行。

## 文档

- [`docs/CURRENT_STATUS.md`](docs/CURRENT_STATUS.md)：当前实现状态。
- [`docs/PRODUCT_VISION.md`](docs/PRODUCT_VISION.md)：产品目标和范围。
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)：系统架构。
- [`docs/SAFE_WRITE_MODEL.md`](docs/SAFE_WRITE_MODEL.md)：安全写入模型。
- [`docs/BUILD_AND_RUN.md`](docs/BUILD_AND_RUN.md)：构建、安装和运行。
- [`docs/ROADMAP.md`](docs/ROADMAP.md)：开发路线。
- [`docs/REFERENCE_POLICY.md`](docs/REFERENCE_POLICY.md)：第三方参考和独立实现规则。
- [`docs/RELEASE_DISTRIBUTION.md`](docs/RELEASE_DISTRIBUTION.md)：发行包和离线依赖策略。
- [`spec/BPCTX_FORMAT.md`](spec/BPCTX_FORMAT.md)：BPCTX/1 格式规范。

## 许可证

UE Agent Kit 使用 [MIT License](LICENSE)。

仓库代码以独立实现为原则。第三方项目主要用于架构、工作流和 UE API 使用方式参考，不直接复制其代码；实际分发的第三方依赖会单独记录许可证、版本和哈希。

UE Agent Kit 是独立开源项目，与 Epic Games, Inc. 没有隶属、赞助或背书关系。Unreal 和 Unreal Engine 是 Epic Games, Inc. 在美国及其他地区的商标或注册商标。
