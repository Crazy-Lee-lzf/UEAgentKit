# UEAgentKit Retarget Output Post-process 交接

更新时间：2026-08-13  
工作区：`E:\WorkSpace\UEAgentKit-RealtimeIO`  
分支：`feature/live-editor-realtime-io`  
当前最新提交：`05f9cf2 feat: add retarget output postprocess suggestions`  
远程状态：本地分支领先远程 40 个提交；本轮未 push  
测试工程：`E:\WorkSpace\我的项目\我的项目.uproject`  
UE：`E:\EPICGAME\UE_5.6` / UE 5.6.1

---

## 1. 接手时的当前阶段

动画工具主线状态：

```text
P0 单资产 Animation Scale Fix                 完成
P1 批量只读 Animation Scale Audit              完成
P2 Batch Plan / Apply / Save / Verify / Rollback 完成
P3 Retarget Output Post-process 第一阶段         完成
P3 第二阶段                                     下一步
P4 Additive + Base Pose                         未开始
```

本次 P3 第一阶段已经完成：

```text
Retarget Batch Complete
→ 使用该 Task 的精确 outputs[]
→ 分类输出资产
→ 只把 AnimSequence 送入现有 Animation Scale Audit
→ 生成 Scale Fix Candidate / Manual Review / Reference Follow-up
→ 生成 WorkRoot 内不可执行的 Suggested Post-process Plan
```

**当前不要直接进入 P4。下一步应继续闭合 P3 第二阶段。**

---

## 2. P3 第一阶段已经实现什么

新增 MCP Tool：

```text
ue_start_animation_retarget_postprocess
ue_get_animation_retarget_postprocess
ue_plan_animation_retarget_postprocess
```

Retarget Batch 的每个 output 现在额外返回：

```text
assetClass
assetType
skeletonPath
```

目前支持的输出分类：

```text
AnimSequence
BlendSpace
AimOffset
AnimMontage
Unknown
```

处理规则：

- `AnimSequence`：进入现有 bounded Animation Scale Audit；
- `BlendSpace` / `AimOffset` / `AnimMontage`：只记录为 `referenceFollowups`；
- Additive / Root Motion Review / 其他不能安全自动修复的情况：进入 `manualReview`；
- `normal`：记录为无需修复；
- `root-lock-candidate` / `root-track-candidate`：进入 Scale Fix 建议候选。

P3 **没有复制第二套动画比例诊断逻辑**，而是继续复用 P1：

```text
AnimationScaleAuditService
→ ue_diagnose_animation_scale
→ bounded batches
→ classification
→ Audit Report
```

---

## 3. Suggested Plan 的安全语义

Suggested Plan 写入：

```text
WorkRoot/retarget-postprocess/<postprocessId>/plan.json
```

它不是 P2 executable Batch Plan。

当前固定语义：

```text
modifiesAssets = false
autoApplyAllowed = false
requiresUserReview = true
referenceAssetMutationImplemented = false
```

如果存在：

```text
root-lock-candidate
root-track-candidate
```

Plan 会额外标记：

```text
requiresRetargetOutputIndexRefreshBeforeP2Plan = true
p2Workflow = animation-scale-fix-batch
```

原因非常重要：

> Retarget Batch 刚生成的资产可能只存在于 Editor / Package / Disk 状态中，还没有进入当前 immutable SQLite + Revision Export generation。P2 executable Plan 强制依赖 fresh Index Revision，因此 P3 不能直接把刚生成的动画塞进 P2，也不能绕过 Revision/Freshness 校验。

Plan 文件有 SHA-256 digest；同一 MCP session 中重新读取时会检查文件是否被篡改。

---

## 4. Retarget Task → P3 的读取边界

新增了内部只读 context：

```text
get_animation_retarget_postprocess_context(task_id)
```

语义：

- 只接受 `completed` / `saved` Retarget Batch Task；
- queued/running 状态拒绝；
- **不会推进 Retarget Batch**；
- 不调用 Editor mutation；
- 不保存资产；
- 不修改 Task 状态。

这一点已有单测锁定，防止以后把它错误实现成 `ue_get_animation_retarget_batch` 那种会推进 queued task 的接口。

---

## 5. 真实 UE5.6 Smoke 结果

新增测试：

```text
scripts/TestMcpRetargetPostprocess.ps1
tests/integration/mcp_retarget_postprocess_smoke.py
```

测试没有运行 Retarget Setup，也没有覆盖正式动画。

它使用测试工程里已有：

```text
/Game/Characters/Mannequins/Meshes/
IKRetargeter_SKM_Manny_Simple_to_SK_XinYueHu
```

随机创建：

```text
/Game/UEAgentKitRetargetTests/Postprocess/
P3_<nonce>_MM_Idle.P3_<nonce>_MM_Idle
```

真实结果：

```text
Retarget Batch                     completed
assetType                          AnimSequence
assetClass                         /Script/Engine.AnimSequence
skeletonPath                       present
P3 Scale Audit                     root-lock-candidate
Suggested Plan                     created
modifiesAssets                     false
autoApplyAllowed                   false
requiresRetargetOutputIndexRefresh true
Retarget Save                      passed
Rollback DryRun                    delete 1 / restore 0
Rollback Commit                    passed
最终 P3_*.uasset                    0
测试结束后 UnrealEditor             closed
```

测试使用 D3D11 Editor。

Catalog Export 仍使用既有 commandlet / NullRHI；没有走之前会触发 Slate `GetRestoredDimensions` Fatal 的“NullRHI + 打开动画编辑器”路径。

---

## 6. 当前验证基线

本轮结束时已通过：

```text
UE5.6 Direct Plugin Build          passed
Python tests                       430 passed
Ruff                               passed
MCP Server contract                passed
Tool Registry contract             passed
Retarget Workflow tests            passed
P3 real UE5.6 smoke                passed
P3 zero-residue rollback           passed
git diff --check                   passed
```

已知且仍存在的 Build Warning：

```text
UEAgentKit plugin does not list IKRig as a dependency,
but UEAgentKitEditor depends on IKRig / IKRigEditor.
```

这不是本轮引入的问题。除非专门处理 Plugin dependency，不要顺手修改。

---

## 7. 必须保持不变的正式测试基线

P2 的正式 XinYueHu 测试样本：

```text
/Game/Characters/XinYueHu/Animations/Retargeted/
MM_Idle_XinYueHu.MM_Idle_XinYueHu
```

当前磁盘 Package SHA：

```text
sha256:a3cb62ec5d0f804e5612e4cc383a9b9dbdf9f9e9dd9d01625479c9440b5d7f5d
```

状态：

```text
Force Root Lock       = true
Root Motion Root Lock = RefPose
Root Track Scale      = 1
```

P3 Smoke 已确认没有改变该正式样本。

不要用 P3 第二阶段测试直接改这个资产来验证 Retarget 新输出链路；仍优先使用 `/Game/UEAgentKitRetargetTests/...` 临时输出。

---

## 8. P3 第二阶段：下一步必须解决的问题

### 8.1 核心目标

把当前：

```text
Retarget Batch
→ P3 Suggested Plan
```

安全接到已经完成的 P2：

```text
P3 Suggested Plan
→ User Review
→ Retarget Save
→ Independent Verify
→ paired Revision Export + SQLite Refresh
→ MCP Restart
→ Fresh Index Revision
→ Revalidate / rebuild P3 suggestion
→ Existing P2 Animation Scale Fix Batch Plan
```

最终不要再造一套 P3 Apply/Save/Scale-Fix 实现。

P3 负责：

```text
Retarget output discovery / classification / orchestration
```

P2 继续负责：

```text
Animation Scale Fix Plan / Live Apply / Save / Verify / Rollback
```

---

### 8.2 第一件事：解决 Retarget 输出持久化状态

现有 Retarget Workflow 已有：

```text
ue_save_animation_retarget_batch
ue_verify_animation_retarget_batch
ue_rollback_animation_retarget_batch
```

优先复用这些接口，不要新增第二套普通 Package Save。

需要明确 P3 在进入 Index Refresh 前至少要求：

```text
Retarget Task status = saved
Independent Verify = verified
```

并冻结：

```text
output Object Path
assetClass
assetType
skeletonPath
saved disk revision
retargetTaskId
retargetPlanId / digest
```

如果保存后实际 Package Revision 和 P3 Suggested Plan 创建时状态不一致，应 fail-stop，而不是继续生成 P2 Plan。

---

### 8.3 第二件事：paired Index Refresh

这里优先复用 P2 已经实现的 paired snapshot infrastructure：

```text
Revision Export candidate
+ SQLite candidate
→ atomic paired generation
→ active pointer switch
→ MCP restart required
```

不要实现：

```text
只更新 SQLite
只写一个临时 revision 字段
直接向当前数据库 INSERT 新资产
绕过 Revision Export
```

这些都会破坏现有 Freshness 契约。

需要确认：

1. 新 Retarget output 是否能用现有 Asset Catalog Export 精确导出；
2. 多个 Retarget output 能否一次进入同一个 paired generation；
3. Apply 前 Preview 必须零写入 active index；
4. Apply 成功后当前 MCP session 必须失效/要求重启；
5. 重启后 `ue_get_asset_state` / immutable Index 能读取新的 Revision。

优先抽取/复用 P2 Batch Index Refresh 的实现，不复制一套 snapshot writer。

---

### 8.4 第三件事：Restart 后重新建立可信上下文

不要把 Restart 前的内存 Task 当作唯一可信来源。

Restart 后应至少依靠持久化的 P3 WorkRoot artifact + fresh Index：

```text
Suggested Plan
Audit Report
Retarget output Object Paths
fresh Index Revision
```

然后重新验证：

```text
asset exists
asset class still AnimSequence
revision matches fresh Index
classification still eligible
Skeleton / Reference Scale still compatible
```

如果需要重新执行 Scale Audit，应重新 Audit；不要因为 Restart 前曾经得到 `root-lock-candidate` 就永久信任旧运行时求值。

推荐原则：

> P3 Suggested Plan 是“建议来源”，fresh Index + 当前 Audit 才是生成 P2 executable Plan 时的事实来源。

---

### 8.5 第四件事：接入现有 P2 Plan

只允许当前 P2 已支持的自动分类：

```text
root-lock-candidate
root-track-candidate
```

最终应调用/复用：

```text
ue_plan_animation_scale_fix_batch
```

而不是创建：

```text
ue_apply_animation_retarget_postprocess_scale_fix
```

之类的新写入 API。

目标是让 P3 只负责把经过 fresh Revision 验证的 eligible AnimSequence 变成 P2 的输入：

```text
Completed Audit Report
+ explicit asset_paths
+ fresh Index Revision
→ immutable P2 Batch Plan
```

P2 后续已有：

```text
Live Apply
Undo
Authorized Save
Independent Verify
Index Refresh
Persisted Rollback
```

继续全部复用。

---

## 9. P3 第二阶段建议纵向切片

不要一次做完整自动化。建议顺序：

### Slice A：Retarget Save → Index Preview

只实现：

```text
saved Retarget Task
→ collect exact AnimSequence outputs
→ independent export candidate
→ paired Index Refresh Preview
```

要求 Preview 零写入。

### Slice B：Atomic Index Apply

```text
all candidates ready
→ one exact confirmation
→ paired SQLite + Revision Export generation
→ atomic active switch
→ requires MCP restart
```

先在临时 Retarget output 上验证。

### Slice C：Restart → Fresh Revision Verify

```text
restart MCP
→ reopen P3 artifact
→ fresh immutable Index lookup
→ compare Object Path / Revision / Class
```

不生成 P2 Plan也可以，先把重启边界闭合。

### Slice D：P3 → P2 Plan

最后才做：

```text
fresh P3 audit candidate
→ existing ue_plan_animation_scale_fix_batch
```

这一阶段仍不自动 Apply。

---

## 10. 当前明确不要做

P3 第二阶段暂时不要：

- 在 Retarget Batch 内自动开启 Force Root Lock；
- 在 Retarget Batch 内自动修改 Root Scale Track；
- 绕过 P2 Revision/Freshness 校验；
- 新建第二套 Animation Scale Apply/Save/Rollback；
- 自动修改 BlendSpace / AimOffset / Montage；
- 处理 Additive + Base Pose；
- 修改 Skeleton；
- 修改 Skeletal Mesh；
- 修改 Physics Asset；
- 修改 Animation Blueprint；
- 进入 ModelPreview 正式写入；
- 为了测试修改正式 XinYueHu baseline 动画。

Additive + Base Pose 仍属于 P4。

Composite/reference asset mutation 应在 P3 Scale Fix 衔接闭合之后再做独立纵向切片。

---

## 11. 关键源码

P3：

```text
src/ue_agent_kit/retarget_postprocess.py
src/ue_agent_kit/mcp_retarget_tools.py
src/ue_agent_kit/retarget_workflow.py
src/ue_agent_kit/mcp_server.py
src/ue_agent_kit/tool_registry.py
```

UE Retarget output metadata：

```text
Plugin/UEAgentKit/Source/UEAgentKitEditor/
Private/Retarget/RetargetBatchTask.cpp

Plugin/UEAgentKit/Source/UEAgentKitEditor/
Public/Retarget/RetargetTypes.h
```

P1/P2 复用入口重点查看：

```text
src/ue_agent_kit/animation_scale_audit.py
src/ue_agent_kit/animation_scale_fix_batch.py
src/ue_agent_kit/agent_workflow.py
```

具体类名/文件若后续重构，以搜索现有 Tool 实现为准，不要按交接文档盲猜。

---

## 12. 测试文件

P3 单测：

```text
tests/python/test_retarget_postprocess.py
tests/python/test_retarget_workflow.py
tests/python/test_mcp_server.py
tests/python/test_tool_registry.py
```

P3 真实 UE5.6：

```text
tests/integration/mcp_retarget_postprocess_smoke.py
scripts/TestMcpRetargetPostprocess.ps1
```

P2 相关测试不要删除或弱化。P3 接入 P2 时尤其要继续保住：

```text
Fresh Index Revision
paired snapshot atomicity
Get does not advance writes
Save / Verify boundedness
Persisted Rollback
```

---

## 13. 文档

先读：

```text
docs/RETARGET_POSTPROCESS_TOOL.md
docs/Plans/ANIMATION_TOOLS_FOLLOWUP_PLAN_20260806.md
```

历史总交接：

```text
docs/Handoffs/LIVE_EDITOR_ANIMATION_TOOLS_HANDOFF_20260806.md
```

本文件是当前 P3 接手入口，优先级高于旧 Handoff 中对“当前工作树”的描述。

---

## 14. 测试环境与固定路径

```text
Engine:
E:\EPICGAME\UE_5.6

Test project:
E:\WorkSpace\我的项目\我的项目.uproject

Worktree:
E:\WorkSpace\UEAgentKit-RealtimeIO

Compiled plugin:
E:\WorkSpace\UEAgentKit-RealtimeIO\Build\Compiled\UEAgentKit

Test project plugin junction:
E:\WorkSpace\我的项目\Plugins\UEAgentKit
→ E:\WorkSpace\UEAgentKit-RealtimeIO\Build\Compiled\UEAgentKit
```

P3 Smoke 输出：

```text
E:\WorkSpace\UEAgentKit-RealtimeIO\Output\McpRetargetPostprocessSmoke
```

P3 临时资产目录：

```text
/Game/UEAgentKitRetargetTests/Postprocess
```

测试结束后必须确认：

```text
P3_*.uasset count = 0
UnrealEditor process = none
```

---

## 15. 接手后的第一轮检查

先不要改代码，依次确认：

```text
1. git status --short --branch
2. git log -5 --oneline
3. git diff --check
4. 确认 HEAD 包含 05f9cf2
5. 阅读 docs/RETARGET_POSTPROCESS_TOOL.md
6. 阅读本 Handoff 的 P3 第二阶段章节
7. 确认测试工程没有残留 P3_*.uasset
8. 确认没有测试 UnrealEditor 在后台运行
```

然后从 **Slice A：Retarget Save → paired Index Refresh Preview** 开始。

不要重新设计 P0/P1/P2，也不要先做 Composite 引用修改。

---

## 16. 提交与交付约束

当前已完成提交：

```text
05f9cf2 feat: add retarget output postprocess suggestions
```

本轮未 push。

下一阶段完成一条完整 P3 第二阶段纵向切片后再独立提交，建议不要和 Additive / Composite Reference Update 混在同一个 commit 中。

提交前至少执行：

```text
Python full tests
Ruff
git diff --check
UE5.6 Direct Plugin Build（如果改 C++）
P3 real UE5.6 smoke / 对应新 smoke
磁盘零残留检查
```

如果真实测试涉及 temporary Retarget output，必须验证 Rollback 后磁盘删除成功。
