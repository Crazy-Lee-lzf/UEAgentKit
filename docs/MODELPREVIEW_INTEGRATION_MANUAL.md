# ModelPreview 接入执行手册（P8）

> 分支：`feature/live-editor-realtime-io`
> 目标工程：`E:\WorkSpace\ModelPreview\ModelPreview.uproject`（UE 5.6）
> 依赖：P7 项目级配置（已就绪）+ P5/P6 只读基线（已就绪）
> 性质：**有序执行手册，非一次性代码任务**。每步执行后回报，逐步推进。

---

## 0. 目标与前置状态

**目标**：把已验证的只读诊断 + AnimSequence Root Lock / Root Track 写工作流接入 ModelPreview，先建立只读基线，再在用户确认后扩大 Policy 到正式动画目录并完成「保存 + 独立验证」。

**前置侦察（2026-08-15）**：

| 项 | 状态 |
|---|---|
| `ModelPreview.uproject` | 存在，UE 5.6，已声明 `UEAgentKit` 插件 `Enabled=true` |
| `ModelPreview/Plugins/UEAgentKit` | 存在但为**旧版全量拷贝**（DLL 1.7MB，07-30，早于 P5/P6/P7） |
| 当前编译插件 | `Build/Compiled/UEAgentKit`（DLL 2.4MB，08-15，含 P5/P6 读取器） |
| ModelPreview Content | 含 `Characters/Mannequins` + `Characters/XinYueHu`（`Animations/Retargeted` 已有重定向动画） |
| P7 项目级 Policy | `config/projects/` 已就绪（`model-preview-read.json` 默认只读 + `model-preview-animation-write.json` 动画写） |

**第一轮禁止修改**：Skeleton / Skeletal Mesh / Physics Asset / Animation Blueprint / Capsule / Cloth / 正式关卡。只验证 AnimSequence Root Lock / Root Track 工作流。

> **边界说明（重要）**：本手册为「有序执行手册」交付物。**任何对 ModelPreview 的改动**（含步骤 1 安装插件、步骤 5 复制资产、步骤 8–9 写动画）都触及 standing constraint「不得修改其他工程内容」，**须先获得用户对每一步的显式授权**才可执行；手册撰写与只读侦察不构成授权。本手册当前仅完成撰写，尚未对 ModelPreview 执行任何改动。

---

## 1. 步骤清单

### 步骤 1 — 安装同一编译插件

> **需显式授权**：修改 `ModelPreview/Plugins/UEAgentKit`（其他工程内容）。默认只读/不改动，等用户确认后才执行。

把 ModelPreview 的插件更新到当前编译版本（含 P5/P6 C++ 读取器）。

```powershell
# 旧版全量拷贝改为备份（非删除，可回滚）
Rename-Item E:\WorkSpace\ModelPreview\Plugins\UEAgentKit UEAgentKit.bak-20260731

# 建立 Package 模式 junction 到当前编译产物
.\scripts\ManageProjectPluginLink.ps1 -Action Install -Mode Package -ProjectPath E:\WorkSpace\ModelPreview\ModelPreview.uproject
```

**验证**：`E:\WorkSpace\ModelPreview\Plugins\UEAgentKit` 为 junction，指向 `Build/Compiled/UEAgentKit`；其 `Binaries/Win64/UnrealEditor-UEAgentKitEditor.dll` 大小 ≈2.4MB（08-15）。

### 步骤 2 — 只读 Editor Status

启动 ModelPreview 编辑器（带 live-editor bridge），只读读取能力与状态：

```text
MCP: ue_get_capabilities / ue_get_project_status / ue_get_editor_context
脚本: tests/integration/mcp_live_editor_smoke.py
```

**验证**：`liveEditor.state == available`；`project.projectName == ModelPreview`；零写盘。

### 步骤 3 — 只读资产扫描

导出 ModelPreview 资产目录（Revision Export + SQLite 索引）：

```powershell
.\scripts\RunAssetCatalog.ps1 -EngineRoot ... -ProjectPath E:\WorkSpace\ModelPreview\ModelPreview.uproject -Root /Game/Characters/XinYueHu/Animations -Output <rev>
.\scripts\ue-agent.py index build <rev> --database <db> --force --project-key ModelPreview
```

**验证**：Revision Export `manifest.json` 的 `projectName == ModelPreview`；索引建立成功；零资产修改。

### 步骤 4 — 生成动画比例基线（P1 Audit）

对 ModelPreview 已有动画跑只读比例审计：

```text
MCP: ue_start_animation_scale_audit → ue_get_animation_scale_audit → ue_export_animation_scale_audit_report
脚本: tests/integration/mcp_animation_scale_audit_smoke.py
```

**验证**：得到 ModelPreview 动画的分类基线（`normal` / `scale-too-small` / `scale-too-large` / `root-lock-candidate` / `root-track-candidate` …），只读不写。

### 步骤 5 — 选择复制测试动画

从「我的项目」复制 1 个已重定向动画作为写测试样本（`复制资产进来`，不碰 ModelPreview 正式内容）：

```text
源:    /Game/Characters/XinYueHu/Animations/Retargeted/MM_Idle_XinYueHu
目标:  /Game/Characters/XinYueHu/Animations/Retargeted/Test/MM_Idle_XinYueHu_Test
```

**验证**：复制后的测试资产独立存在，可回滚删除，不覆盖正式动画。

### 步骤 6 — Live Apply + Undo

在复制出的测试动画上跑 Live Apply + Undo（验证 Root Lock / Root Track 写 + 撤销）：

```text
MCP: ue_plan_animation_scale_fix_batch / ue_apply_..._live / ue_undo_animation_scale_fix_batch
脚本: tests/integration/mcp_live_animation_scale_fix_smoke.py + mcp_live_undo_discard_smoke.py
```

**验证**：Live Apply 生效、Undo 还原；不保存到磁盘（无持久化写）。

### 步骤 7 — 用户确认

**暂停并汇报步骤 1–6 结果**，等用户确认后才进入写阶段（步骤 8–9）。

### 步骤 8 — 扩大 Policy 到正式动画目录

确认后切换到动画写 Policy（P7 的 `model-preview-animation-write.json`）：

```powershell
.\scripts\RunMcp.ps1 ... -ProjectPath E:\WorkSpace\ModelPreview\ModelPreview.uproject -PolicyProfile animation-write
```

**验证**：`retargetCapabilities` 含写能力；`allowedAssetRoots` 收敛到 `/Game/Characters/Animations`。

### 步骤 9 — 保存 + 独立验证

对确认的正式动画执行「保存 + 独立验证」闭环：

```text
MCP: ue_save_animation_scale_fix_batch / ue_verify_animation_scale_fix_batch / ue_refresh_animation_scale_fix_batch_index
脚本: tests/integration/mcp_save_animation_scale_fix.py / mcp_animation_scale_fix_batch_persistence_smoke.py
```

**验证**：正式动画「保存 + 独立验证」通过；ModelPreview 只读基线可复现。

---

## 2. 安全门禁

- 步骤 1–6 只读/工具级，不写 ModelPreview 正式资产内容。
- 步骤 7 是写阶段的强制确认点。
- 步骤 8–9 只写 AnimSequence，**禁止** Skeleton / Skeletal Mesh / Physics Asset / AnimBP / Capsule / Cloth / 关卡。
- 全程本地操作，不提交 `Build/`、`Output/`、`Backups/`、`Intermediate/`、`Saved/`、日志与测试生成资产。
- 回滚：步骤 1 的旧插件拷贝保留为 `Plugins/UEAgentKit.bak-*`；步骤 5 的测试动画独立目录可整目录删除。
