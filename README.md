# BlueprintContextTool

BlueprintContextTool 是面向 Unreal Engine 5.6 的 AI 开发辅助工具。

当前版本已经能够完整读取普通 Blueprint 的类、变量、默认值、组件、函数、Graph、Node、Pin 和连接关系，并输出 Canonical JSON 与紧凑的 BPCTX 文本。

项目的最终目标是形成统一工作流：

```text
查阅
→ 全项目检索
→ AI 分析
→ 声明式修改
→ Dry Run
→ 编译验证
→ Diff
→ 保存与回滚
```

## 当前状态

已完成：

- UE5.6 Editor-only C++ 插件。
- Blueprint 单资产和目录批量导出 Commandlet。
- 变量、默认值、组件、函数和接口读取。
- Graph、Node、Pin 和全部连接关系读取。
- Canonical JSON、BPCTX/1 和 Manifest 输出。
- Profile 无关的 Asset Revision 和磁盘 SHA-256。
- Symbol/Reference 模型：继承、接口实现、变量读写、函数调用和宏调用。
- 多种 Blueprint 派生类型的通用结构导出验证。

尚未完成：

- SQLite/FTS 项目索引。
- SQLite/FTS 全项目变量读写和函数调用检索。
- 声明式 Patch。
- Blueprint 修改、保存和回滚。
- MCP 接口。
- Widget、Anim 和 Control Rig 的完整专用语义适配。

详细状态见 [`docs/CURRENT_STATUS.md`](docs/CURRENT_STATUS.md)。

## 文档

正式文档入口：

- [`docs/README.md`](docs/README.md)：文档导航。
- [`docs/BUILD_AND_RUN.md`](docs/BUILD_AND_RUN.md)：构建、安装和运行。
- [`docs/AI_USAGE.md`](docs/AI_USAGE.md)：AI 使用规则。
- [`docs/PRODUCT_VISION.md`](docs/PRODUCT_VISION.md)：产品目标。
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)：系统架构。
- [`docs/SAFE_WRITE_MODEL.md`](docs/SAFE_WRITE_MODEL.md)：安全写入模型。
- [`docs/PORTABILITY.md`](docs/PORTABILITY.md)：Python、路径、配置和分发的可移植性约束。
- [`docs/ROADMAP.md`](docs/ROADMAP.md)：开发路线。
- [`spec/BPCTX_FORMAT.md`](spec/BPCTX_FORMAT.md)：BPCTX/1 格式规范。

## 构建

推荐使用：

```powershell
powershell.exe -ExecutionPolicy Bypass -File scripts\BuildPluginDirect.ps1 \
  -EngineRoot "<UE_ROOT>" \
  -MsvcToolsRoot "<MSVC_TOOLS_ROOT>"
```

本机路径与脚本默认值一致时，可以运行：

```bat
scripts\BuildPluginDirect.cmd
```

编译产物位于：

```text
Build\Compiled\BlueprintContextTool
```

## 安装

推荐通过项目级 Junction 挂载编译后的插件：

```text
<ProjectRoot>\Plugins\BlueprintContextTool
→ <ToolRoot>\Build\Compiled\BlueprintContextTool
```

然后在项目的 `.uproject` 中启用：

```json
{
  "Name": "BlueprintContextTool",
  "Enabled": true,
  "TargetAllowList": [
    "Editor"
  ]
}
```

修改 `.uproject` 前应先备份。Junction 不应进入 Git 或 P4。

## 导出示例

```bat
scripts\RunExport.cmd \
  -Asset "/Game/Folder/BP_Name" \
  -Profile logic \
  -Format both
```

批量导出：

```bat
scripts\RunExport.cmd \
  -Root "/Game/Folder" \
  -Profile ai \
  -Format both \
  -CompactJson
```

## 安全原则

- 当前公开版本只读。
- 不直接修改 `.uasset` 二进制文件。
- 未来写入功能默认 Dry Run。
- 编译失败、版本冲突或备份失败时不得保存。
- 正式项目默认只读，写入功能只在明确允许的沙箱中运行。

## 版本控制

仓库只包含源码、脚本、规范、正式文档和必要测试数据。

以下内容不进入版本控制：

```text
Build/
Output/
Backups/
AutoSDK/
dev_docs/
插件 Binaries/Intermediate/Saved/DerivedDataCache
.venv/
.local/
.data/
```
