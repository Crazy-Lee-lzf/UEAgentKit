# 构建与运行

本文面向从 GitHub 获取 UE Agent Kit 并接入 Unreal Engine 5.6 项目的用户。

当前版本：**0.8.0**。

## 1. 环境要求

```text
Windows 10 / 11
Unreal Engine 5.6
Visual Studio 2022 C++ Toolchain
PowerShell 5.1+
Python 3.11 或 3.12
P4 CLI（仅启用 Source Control 时需要）
```

UE Agent Kit 是 Editor-only 插件，不用于打包后的游戏运行时。

## 2. Clone 与 Python 环境

```bash
git clone git@github.com:Crazy-Lee-lzf/UEAgentKit.git
cd UEAgentKit
```

创建 Python 环境并安装 MCP 依赖：

```bat
scripts\setup_python.cmd -WithMcp
```

项目虚拟环境位于：

```text
<TOOL_ROOT>\.venv
```

不要从其它机器或旧工作目录复制 `.venv`。

## 3. 构建 UE5.6 Plugin

```bat
scripts\BuildPluginDirect.cmd -EngineRoot "<UE_5.6>"
```

默认输出：

```text
<TOOL_ROOT>\Build\Compiled\UEAgentKit
```

成功后至少应存在：

```text
Build\Compiled\UEAgentKit\UEAgentKit.uplugin
Build\Compiled\UEAgentKit\Binaries\Win64\UnrealEditor-UEAgentKitEditor.dll
```

## 4. 安装到项目

### 使用源码 Plugin

适合开发和调试：

```bat
scripts\InstallProjectPlugin.cmd ^
  -Mode Source ^
  -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject"
```

### 使用编译后的 Package

```bat
scripts\InstallProjectPlugin.cmd ^
  -Mode Package ^
  -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject"
```

安装脚本建立本地 Junction。Junction 本身不应提交到 Git 或 P4。

## 5. 导出资产

### 普通资产

```bat
scripts\RunAssetCatalog.cmd ^
  -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject" ^
  -Root "/Game" ^
  -Output "Output\AssetCatalog"
```

导出单个资产：

```bat
scripts\RunAssetCatalog.cmd ^
  -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject" ^
  -Asset "/Game/Environment/SM_Wall" ^
  -Output "Output\SingleAsset"
```

### Blueprint 语义

```bat
scripts\RunExport.cmd ^
  -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject" ^
  -Root "/Game" ^
  -Profile full ^
  -Format both ^
  -Output "Output\Blueprints"
```

单个 Blueprint：

```bat
scripts\RunExport.cmd ^
  -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject" ^
  -Asset "/Game/Characters/BP_Player" ^
  -Profile logic ^
  -Format both
```

## 6. 建立 SQLite 索引

```bat
scripts\ue-agent.cmd index build Output\AssetCatalog
scripts\ue-agent.cmd index build Output\Blueprints
scripts\ue-agent.cmd index stats
```

查询示例：

```bat
scripts\ue-agent.cmd search assets Door
scripts\ue-agent.cmd search assets --class StaticMesh
scripts\ue-agent.cmd search symbols MaxWalkSpeed
scripts\ue-agent.cmd references --target-asset /Game/Characters/BP_Player.BP_Player
```

## 7. 验证导出

```bat
python scripts\ValidateAssetCatalog.py ^
  --output Output\AssetCatalog ^
  --expect-exporter 0.8.0
```

## 8. 启动 MCP

检查固定 SQLite 数据库：

```bat
scripts\RunMcp.cmd ^
  -Database "<TOOL_ROOT>\.data\ue_agent_kit.sqlite3" ^
  -Check
```

启动本地 stdio MCP：

```bat
scripts\RunMcp.cmd -Database "<TOOL_ROOT>\.data\ue_agent_kit.sqlite3"
```

启用 Project Memory：

```bat
scripts\RunMcp.cmd ^
  -Database "<TOOL_ROOT>\.data\ue_agent_kit.sqlite3" ^
  -EnableProjectMemory ^
  -MemoryDatabase "<TOOL_ROOT>\.data\ue_agent_kit_memory.sqlite3"
```

实际可用 Tool 以 `ue_get_capabilities` 返回值为准。不同启动模式会启用不同的 Offline / Live / Workflow / Memory / Source Control Tool 集合。

完整 MCP 契约见 [`../spec/MCP_SERVER.md`](../spec/MCP_SERVER.md)。

## 9. 固定真实项目

Live Editor、Writer 和 Source Control 模式都应在服务器启动时固定目标项目，不让单个 Tool 调用任意替换 Project / Database / Policy / Work Root。

建议首次真实项目接入顺序：

```text
Read-only audit
→ 导出资产 / Blueprint
→ 建立 SQLite 索引
→ 启动只读 MCP
→ 检查 P4 mapping（如使用 P4）
→ 创建项目专属 Write Policy
→ 选择测试资产
→ Plan / Dry Run
→ 显式 Apply / Save / Verify
```

项目级配置说明见 [`PROJECT_LEVEL_CONFIG.md`](PROJECT_LEVEL_CONFIG.md)。

## 10. Write Policy

仓库提供：

```text
config\write-policy.example.json
```

真实项目应创建自己的 Policy，至少限制：

- `allowedProjectNames`
- `allowedAssetRoots`
- `allowedOperations`
- `allowedAssetClasses`
- 对应属性 / Material 参数 / DataTable 字段 / Reference 白名单

第一次接入不要直接允许整个 `/Game` 写入。

## 11. Patch / Dry Run / Commit

只读预校验：

```bat
scripts\ue-agent.cmd patch validate ^
  --patch <PATCH_JSON> ^
  --policy <POLICY_JSON> ^
  --export <REVISION_EXPORT> ^
  --report <VALIDATION_REPORT>
```

Dry Run：

```bat
scripts\RunPatch.cmd ^
  -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject" ^
  -Patch "<PATCH_JSON>" ^
  -Policy "<POLICY_JSON>" ^
  -RevisionExport "<REVISION_EXPORT>" ^
  -Mode DryRun
```

Commit 需要命令行显式选择并且 Policy 中允许 Commit：

```bat
scripts\RunPatch.cmd ^
  -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject" ^
  -Patch "<PATCH_JSON>" ^
  -Policy "<POLICY_JSON>" ^
  -RevisionExport "<REVISION_EXPORT>" ^
  -Mode Commit ^
  -BackupDir "Backups\Patches"
```

成功 Commit 会生成外部备份与 Backup Manifest。完整格式见 [`../spec/BACKUP_AND_ROLLBACK.md`](../spec/BACKUP_AND_ROLLBACK.md)。

## 12. Rollback

默认先 Dry Run：

```bat
scripts\RunRollback.cmd ^
  -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject" ^
  -Manifest "<BACKUP_MANIFEST>" ^
  -Policy "<POLICY_JSON>" ^
  -BackupRoot "<BACKUP_ROOT>" ^
  -Mode DryRun
```

确认后使用 `-Mode Commit`。Rollback 会重新校验当前 Revision、Backup hash 和项目状态，并在恢复后执行独立验证。

## 13. P4 / Source Control

Source Control 默认关闭。启用后可读取 mapping/opened/lock/have/head，并在严格范围内进行 checkout、safe sync、pending changelist、reopen 和普通文本 resolve。

产品边界：

```text
P4 Submit          人工执行
P4 Revert          人工执行
P4-managed Delete  人工执行
.uasset/.umap      不自动做内容 Resolve
```

## 14. Release Validation

从源码构建或贡献代码时可运行：

```bat
python scripts\ValidateRelease.py --require-release-docs
```

该检查覆盖版本一致性、Ruff、Python 测试、Schema 和示例 Patch。GitHub Actions 在 Python 3.11 / 3.12 上运行同类验证。

正式 Windows Release 打包：

```bat
scripts\BuildRelease.cmd -EngineRoot "<UE_5.6>"
```

输出包含 UE5.6 Win64 Plugin ZIP、Python wheel、`SHA256SUMS.txt` 和 `release-manifest.json`。

## 15. 可安全重建的本地目录

以下目录不是 GitHub 源码的一部分，可以按需重新生成：

```text
.venv\
Build\Compiled\
Build\DirectHost\
Output\
.data\
Backups\
```

其中 `.data` 和 `Backups` 可能包含你自己的项目索引、Memory 和恢复证据，删除前应确认不再需要。
