# 项目级可写配置（Project-Level Policy）

> 分支：`feature/live-editor-realtime-io`
> 状态：已实现

---

## 1. 目标

把「哪个项目可写、写到哪个目录、用什么写能力」固化为**项目级 Policy 配置**，而不是每次启动 MCP 都手写一份 Policy。约定：

```text
我的项目       默认可写（my-project-write.json）
ModelPreview   默认只读（model-preview-read.json）
ModelPreview   动画写单独 Policy（model-preview-animation-write.json，按 profile 显式选择）
```

**明确禁止**用 `allowedAssetRoots = ["/Game"]` 之类的方式实现「取消只读」：只读必须用 `commitEnabled=false` + 只读能力（`retarget.inspect`）表达，写范围必须收敛到具体目录。

---

## 2. 映射方式

`config/projects/manifest.json` 声明「项目名 → Policy 文件」映射：

```json
{
  "schemaVersion": "1.0",
  "projects": {
    "我的项目": {
      "default": "my-project-write.json"
    },
    "ModelPreview": {
      "default": "model-preview-read.json",
      "profiles": {
        "read": "model-preview-read.json",
        "animation-write": "model-preview-animation-write.json"
      }
    }
  }
}
```

- 项目名取自 `.uproject` 文件的**文件名（不含扩展名）**，与 Revision Export `projectName`、Policy `allowedProjectNames` 的约定一致。
- `default` 是省略 profile 时的选择；`profiles` 允许同一项目有多个 Policy（例如 ModelPreview 的只读与动画写）。
- Policy 文件名相对 manifest 所在目录解析，**不能指向 `config/projects/` 之外**。

解析函数是 `ue_agent_kit.config.resolve_project_policy(project_path, profile=None)`：

- 命中映射 → 返回 Policy 文件绝对路径。
- 无 manifest、或项目不在 manifest 中 → 返回 `None`，调用方回退到显式 `--policy`。
- manifest 损坏、profile 未知、或 Policy 文件缺失 → 抛 `ValueError`（配置错误，应修复而非静默回退）。

---

## 3. 三个示例 Policy

| 文件 | 项目 | 语义 |
|---|---|---|
| `my-project-write.json` | 我的项目 | 可写：测试目录 + 心月狐动画目录，全能力 |
| `model-preview-read.json` | ModelPreview | 默认只读：`commitEnabled=false` + 仅 `retarget.inspect` |
| `model-preview-animation-write.json` | ModelPreview | 动画写：只覆盖确认后的动画目录 |

只读与可写的**判定依据**是 `commitEnabled` + `retargetCapabilities`，不是目录范围：

- **只读** = `commitEnabled=false` 且 `retargetCapabilities=["retarget.inspect"]`（无写能力）。
- **可写** = `commitEnabled=true` 且 `retargetCapabilities` 含写能力（`retarget.configure` / `retarget.batch`）。

> `model-preview-read.json` 仍带 `allowedOperations` / `allowedAssetClasses` / `allowedAssetRoots`，
> 这是为了让三个文件都能通过 `_validate_policy`（写 Policy 的 schema 要求非空）。这些字段在
> `commitEnabled=false` + 无写能力时是**惰性的**，不产生任何写权限。

---

## 4. 使用

### RunMcp.ps1

```powershell
# 我的项目默认可写（省略 -Policy，自动解析 my-project-write.json）
.\scripts\RunMcp.ps1 -EnableWriteTools -EnableCommitTools -ProjectPath E:\WorkSpace\我的项目\我的项目.uproject

# ModelPreview 默认只读
.\scripts\RunMcp.ps1 -EnableWriteTools -EnableCommitTools -ProjectPath E:\WorkSpace\ModelPreview\ModelPreview.uproject

# ModelPreview 动画写（显式选 profile）
.\scripts\RunMcp.ps1 -EnableWriteTools -EnableCommitTools -ProjectPath E:\WorkSpace\ModelPreview\ModelPreview.uproject -PolicyProfile animation-write
```

- 省略 `-Policy` 时，`ue-agent-mcp.py` 用 `--project` + `--policy-profile` 自动解析。
- 提供 `-Policy` 时永远优先用显式文件，忽略 `-PolicyProfile`。
- 项目没有映射（返回 `None`）时，服务启动报错，提示仍需要 `--policy`。

### ue-agent-mcp.py（CLI）

```text
--policy-profile <name>   省略 --policy 时，按 --project 解析项目级 Policy
```

`--policy` 显式传参时优先级最高；`--policy-profile` 只在写工具模式下、且 `--policy` 未给出时生效。

---

## 5. `retargetCapabilities` 字段

Policy 现在正式支持可选字段 `retargetCapabilities`（登记进 `POLICY_FIELDS` 并通过 `_validate_policy` 校验）：

```text
retarget.inspect   只读诊断（分析 / 比例审计 / 地面接触 / 次级运动 / 评估）
retarget.plan      重定向计划
retarget.configure 应用重定向设置（写）
retarget.batch     批量重定向 + 保存（写）
retarget.validate  输出验证
```

未知值会被 `_validate_policy` 以 `policy-retarget-capability-unknown` 拒绝。此前该字段不在 `POLICY_FIELDS`，导致含 `retargetCapabilities` 的 Policy 无法通过 `validate_patch`；P7 一并修复，使项目级 Policy 可同时服务于补丁工作流与重定向工作流。

---

## 6. 安全门禁

- Policy 文件必须位于 `config/projects/`（相对 manifest 解析，禁止越界）。
- 写范围用 `allowedAssetRoots` 精确到目录，禁止 `/Game`。
- `allowedReferenceRoots` 限制引用资产（如 `setAdditiveBasePoseFix` 的 `refSequencePath`）范围，从而约束「只能改我的项目，不可改其他工程」。
- 只读通过 `commitEnabled=false` + 能力白名单表达，不通过放宽目录范围表达。
