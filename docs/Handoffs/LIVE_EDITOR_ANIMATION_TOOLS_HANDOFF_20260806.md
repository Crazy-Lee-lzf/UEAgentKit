# `feature/live-editor-realtime-io` 动画工具交接

更新时间：2026-08-08
工作区：`E:/WorkSpace/UEAgentKit-RealtimeIO`
分支：`feature/live-editor-realtime-io`
P0 工作基线：`afb25ba feat: add live animation scale diagnosis`
远程状态：本地分支领先远程，未推送

---

## 1. 本轮完成内容

本轮在既有 `ue_diagnose_animation_scale` 基础上，新增了受控动画比例修复纵向切片。

核心能力：

```text
ue_plan_animation_scale_fix
→ setAnimationScaleFix
→ ue_apply_asset_property_live
→ Final Component Pose Verify
→ Undo / Discard
→ Authorized Save
→ Independent Verify
→ Index Refresh
```

支持修改：

- Force Root Lock；
- Enable Root Motion；
- Use Normalized Root Motion Scale；
- Root Motion Root Lock；
- Root Scale Track：Keep / ReferenceLocal / Uniform。

安全语义：

- 只处理 `UAnimSequence`；
- 必须精确 Object Path；
- 必须已打开、已加载、Package Clean；
- Policy 必须允许 `setAnimationScaleFix`；
- Additive 修改无条件拒绝，不提供绕过字段；
- `rootBone` 必须是目标 Skeleton 的真实 Root Bone；
- 修改后立即求值最终 Root Component Scale；
- 不达标自动恢复；
- 不自动保存；
- 保存必须走 Preview / Commit / Backup / Verify。

---

## 2. 真实 UE5.6 结果

测试资产：

```text
/Game/Characters/XinYueHu/Animations/Retargeted/
MM_Idle_XinYueHu.MM_Idle_XinYueHu
```

Root Lock 路径：

```text
Before Final Root Scale = 1
Force Root Lock          false → true
Root Motion Root Lock    RefPose
Root Track Scale         保持 1
After Final Root Scale   100
Undo                     成功
```

Root Track 路径：

```text
Before Root Track Scale = 1
After Root Track Scale  = 100
After Final Root Scale  = 100
Undo Root Track Scale   = 1
Undo Final Root Scale   = 1
```

已完成一次正式保存：

```text
Force Root Lock       = true
Root Motion Root Lock = RefPose
Root Track Scale      = 1
Saved Revision        = sha256:a3cb62ec5d0f804e5612e4cc383a9b9dbdf9f9e9dd9d01625479c9440b5d7f5d
Independent Verify    = verified
```

保存后已生成新的 paired snapshot generation：

```text
gen_20260806T140634Z_f932adf2013c
```

2026-08-08 P0 收口时重新做了真实回归。由于样本已经保存为 `Force Root Lock=true`，Smoke 改为以当前持久化状态为基线：

```text
Root Lock Live Apply     Final Root Scale 100 → 1
Root Lock Undo           Final Root Scale 1 → 100
Root Track Live Apply    Root Track Scale 1 → 100
Root Track Undo          Root Track Scale 100 → 1
Disk Package SHA         unchanged
SQLite Index SHA         unchanged
```

保存门禁额外执行了 Root Track `1 → 100 → 1` 的持久化往返。最终资产恢复为原正式保存状态：

```text
Force Root Lock        = true
Root Motion Root Lock  = RefPose
Root Track Scale       = 1
Final Revision         = sha256:a3cb62ec5d0f804e5612e4cc383a9b9dbdf9f9e9dd9d01625479c9440b5d7f5d
Independent Verify     = verified
Final Index Generation = gen_20260808T091840Z_56f7279aced2
```

该结果属于测试工程“我的项目”，不属于 ModelPreview。

---

## 3. 当前工作树状态

P0 单资产修复纵向切片已本地提交：

```text
7180fa9 feat: add controlled animation scale repair workflow
```

当前未提交工作树属于 P1 第一条只读批量 Audit 纵向切片，主要包含：

- MCP `ue_start_animation_scale_audit` / `ue_get_animation_scale_audit` / `ue_cancel_animation_scale_audit`；
- 有界 Batch、分页、Cancel 和 Editor Session 失效检测；
- `normal` / Root Lock / Root Track / Root Motion / Additive 等分类；
- 真实 UE5.6 只读 Audit Smoke；
- 测试 Editor 默认最小化启动；
- P1 文档和 Tool Registry / Capability 契约更新。

接手后第一件事必须运行：

```text
git status --short --branch
git diff --check
```

不要丢弃当前工作树。

---

## 4. 已通过门禁

```text
UE5.6 C++ Direct Build       passed
Python tests                 387 passed
Ruff                         passed
Root Lock Live Apply/Undo    passed
Root Track Live Apply/Undo   passed
Authorized Save              passed
Independent Reload Verify    verified
Index Refresh                passed
git diff --check             passed
```

Verify 响应已经明确拆分为：

```text
appliedValue
persistedExpectedValue
exportedPersistedValue
runtimeVerification
```

旧 `expectedValue` / `exportedValue` 兼容字段仍保留，但不再要求调用方把运行时求值字段与磁盘 Export 直接比较。

测试环境备注：D3D12 Editor 曾出现 `DXGI_ERROR_DEVICE_REMOVED`；`-NullRHI` 在打开动画资产后又触发 Slate `GetRestoredDimensions` Fatal。最终保存恢复与独立验证使用 D3D11 Editor 完成。这两个崩溃发生在测试 Editor/RHI 层，不改变上述资产写入和验证结论。

`scripts/LaunchEditorKeepOpen.ps1` 现在默认使用 `WindowStyle=Minimized`，并在 Bridge 就绪后短暂守护 UE 最终主窗口。真实 Windows `IsIconic` 检查确认脚本结束后和 2 秒后窗口均保持最小化；测试仍使用正常 RHI/Slate，不使用 `-NullRHI`。

---

## 5. 当前已知设计问题

### 5.1 Additive 尚未解决

当前只读工具会返回 Base Pose 元数据，但修改工具无条件拒绝 Additive，并且 MCP/Patch Schema 不提供 `allowAdditive` 绕过。必须实现 Base Pose 组合求值后才能开放。

### 5.2 当前只有单资产修改能力

批量只读 Audit 第一版已经实现；仍没有批量修复 Plan、Change Set 批量 Apply、批量 Save 和 Rollback。

### 5.3 项目级写入配置尚未正式产品化

当前测试通过命令行固定 Policy 启用。用户提到“取消我的项目只读”，正确后续是建立项目级 Write Profile，而不是全局开放 `/Game`。

---

## 6. 下一步

完整计划见：

```text
docs/Plans/ANIMATION_TOOLS_FOLLOWUP_PLAN_20260806.md
```

优先级：

```text
P0 收口、测试、提交当前单资产工具（收口与门禁已完成，本地提交与本文档一并完成，不推送远程）
P1 批量只读动画 Audit（显式列表 start/get/cancel 已完成；目录候选生成、性能门禁和 Report 导出待继续）
P2 批量修复 Plan / Apply / Save / Rollback
P3 重定向输出后处理
P4 Additive + Base Pose
P5 浮空诊断 Reader
P6 尾巴/衣服/Physics/Cloth Reader
P7 项目级可写配置
P8 ModelPreview 小范围接入
P9 0.8.0-dev 合并准备
```

---

## 7. 与 ModelPreview 的边界

ModelPreview 已记录本轮结果和后续角色侧计划，但尚未安装并验证当前动画修改工具。

ModelPreview 接入时：

1. 先使用只读 Policy；
2. 扫描现有动画比例；
3. 复制一个错误动画到测试目录；
4. 运行 Live Apply + Undo；
5. 用户确认后才扩大正式动画目录 Policy；
6. 第一阶段不修改 Skeleton、Mesh、Physics Asset、ABP、Capsule 或 Cloth。

角色浮空、尾巴和衣服问题应由后续专用 Reader 提供证据，不能直接用通用属性写入猜测修复。
