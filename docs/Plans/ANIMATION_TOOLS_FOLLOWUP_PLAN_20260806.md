# UEAgentKit 动画工具后续开发计划

更新时间：2026-08-12
目标分支：`feature/live-editor-realtime-io`
当前已完成基线：`5310387 feat: complete animation scale fix batch workflow`
测试工程：`E:\\WorkSpace\\我的项目\\我的项目.uproject`
目标接入工程：`E:\\WorkSpace\\ModelPreview\\ModelPreview.uproject`

---

## 1. 当前状态

当前分支已经完成从“只能看动画轨道”到“可以安全修改并验证最终姿势”的第一条纵向闭环。

已实现：

```text
ue_diagnose_animation_scale
ue_plan_animation_scale_fix
setAnimationScaleFix
ue_apply_asset_property_live
ue_undo_asset_property_live
ue_discard_asset_property_live
ue_save_authorized_asset
ue_verify_live_write
ue_refresh_asset_index
```

当前 `setAnimationScaleFix` 支持：

- `Force Root Lock`；
- `Enable Root Motion`；
- `Use Normalized Root Motion Scale`；
- `Root Motion Root Lock`；
- Root Scale Track 保持不变；
- Root Scale Track 使用目标 Skeleton Reference Local Scale；
- Root Scale Track 使用明确 Uniform Scale；
- 修改后最终 Editor World Component Space Scale 验证；
- 失败自动恢复；
- Undo / Discard；
- 授权保存、独立重载验证和索引刷新。

真实样本已验证：

```text
MM_Idle_XinYueHu

Root Lock 路径：
Final Root Scale 1 → 100
Root Track Scale 保持 1
Undo 后恢复为 1

Root Track 路径：
Root Track Scale 1 → 100
Final Root Scale 1 → 100
Undo 后全部恢复为 1
```

已正式保存一个样本：

```text
/Game/Characters/XinYueHu/Animations/Retargeted/
MM_Idle_XinYueHu.MM_Idle_XinYueHu

Force Root Lock       = true
Root Motion Root Lock = RefPose
Root Track Scale      = 1
保存后独立验证        = verified
```

注意：该保存发生在测试工程“我的项目”，不是 ModelPreview。

---

## 2. P0 收口状态

2026-08-08 已完成本纵向切片的代码审阅、响应模型修正和真实 UE5.6 门禁。本地提交与本次文档更新一并完成，不推送远程。

完成结果：Python `382 passed`、Ruff passed、UE5.6 Direct Build passed、Root Lock/Root Track Live Apply+Undo passed、Authorized Save/Independent Verify passed、Index Refresh passed、`git diff --check` passed。

### P0.1 代码审阅

必须检查：

- `LiveWriteAnimationOperations.cpp` 是否只处理 `UAnimSequence`；
- Additive 默认拒绝是否不可绕过；
- Root Bone 精确名称和 Reference Scale 读取是否稳定；
- 修改失败是否整笔 Transaction 恢复；
- No-op 是否不会制造 Dirty；
- 保存后 Undo 是否正确拒绝；
- Runtime-only 字段与持久化字段是否分开返回。

### P0.2 验证响应模型修正

当前保存验证已经能正确判定 `verified`，但响应中：

```text
expectedValue
```

仍可能包含：

```text
finalRootScale
finalEvaluationStatus
referenceLocalScale
```

这些属于运行时求值或派生数据，不是 Package 中直接持久化的字段。后续应明确拆成：

```text
appliedValue
persistedExpectedValue
exportedPersistedValue
runtimeVerification
```

避免调用方错误地对完整对象做相等比较。

### P0.3 门禁

提交前必须通过：

```text
Python 全量测试
Ruff
UE5.6 Direct Plugin Build
Root Lock Live Apply / Undo
Root Track Live Apply / Undo
Authorized Save / Independent Verify
Index Refresh
git diff --check
```

### P0.4 本地提交

建议提交：

```text
feat: add controlled animation scale repair workflow
```

不推送远程。

---

## 3. P1：批量动画诊断

2026-08-08 已完成第一条批量只读纵向闭环：

```text
ue_start_animation_scale_audit
ue_get_animation_scale_audit
ue_cancel_animation_scale_audit
```

当前版本接受显式 AnimSequence Object Path 列表，单任务最多 1000 个资产，`batchSize` 最大 8；每次 Get 只推进一个 Batch，并提供分类统计、分页 Detail、Cancel 和 Editor Session 失效检测。真实 UE5.6 Smoke 已验证 `MM_Idle_XinYueHu` 分类为 `normal`，Root Track Scale=1、最终 Root Scale=100，磁盘 Package 和 SQLite 均未变化。

P1 已完成：显式 Object Path 列表、固定 immutable Index `pathPrefix` 候选生成、Index Snapshot ID 冻结、按分类过滤与稳定排序、1000 候选 / Batch 8 / Page 50 有界工作量门禁，以及固定 WorkRoot 的确定性 JSON Audit Report 均已通过测试；下一步进入 P2 批量 Plan / Apply / Save / Rollback。

当前工具一次可以读取多个动画，但还没有形成面向 Agent 的批量分类任务。

新增建议：

```text
ue_start_animation_scale_audit
ue_get_animation_scale_audit
ue_cancel_animation_scale_audit
```

使用现有 Realtime Batch Task 框架，逐帧处理资产，避免一次加载和求值大量动画。

每个 AnimSequence 输出：

```text
Asset Path
Asset Type
Skeleton
Additive Type
Base Pose Type / Path
Root Motion 设置
Force Root Lock
Root Track Raw / Compressed Scale
Final Root / Pelvis Scale
Final Pelvis Location
诊断分类
建议修复路径
```

建议分类：

```text
normal
scale-too-small
scale-too-large
root-lock-candidate
root-track-candidate
root-motion-review
additive-requires-base-pose
unsupported-composite
load-failed
```

要求：

- 默认只读；
- 显式 `loadIfNeeded`；
- 分帧；
- 可取消；
- 结果分页；
- 不修改、不保存。

---

## 4. P2：批量修复计划与 Change Set

P2 第一条规划纵向切片已实现：`ue_plan_animation_scale_fix_batch` 从固定 WorkRoot 的 Completed Audit Report + 显式 `asset_paths` 生成不可变 Batch Plan，`ue_get_animation_scale_fix_batch` 可重新读取并校验该 Plan。每个候选仍复用现有单资产 `ue_plan_animation_scale_fix`，因此 Policy、Revision、Asset Class 和 Operation 校验没有新增旁路。

当前自动策略只接受 `root-lock-candidate` / `root-track-candidate`；其他被用户选中的分类直接拒绝，不静默跳过。默认 `expectedFinalScale` 从每个 Audit Item 的 Skeleton Root Reference Component Scale 推导，不写死 `100`。Root Track 可显式 override 并自动切换为 Uniform；Root Lock override 必须与 Reference Scale 一致。

规划切片若创建第 N 个子 Plan 失败，会清理本次已经创建的未消费子 Plan 和 WorkRoot 目录，不留下半成功 Batch Plan。最大 100 个资产，与现有 Change Set 100 Operation 上限对齐。

P2 已完整实现并通过当前门禁。Live 阶段：`ue_apply_animation_scale_fix_batch_live` 通过一次精确 Batch confirmation 创建现有 Change Set，每次最多处理 8 个子 Plan；`ue_get_animation_scale_fix_batch` 始终只读；`ue_undo_animation_scale_fix_batch` 按相反顺序每次最多 Undo 8 个未持久化事务。子项失败立即 fail-stop，后续项不静默跳过。

持久化阶段：`ue_save_animation_scale_fix_batch` 与 `ue_verify_animation_scale_fix_batch` 每次最多处理 2 个资产，继续复用单资产 Authorized Save / Backup Manifest / Independent Reload Verify。保存成功后每个资产都具备标准 Rollback Manifest；若 Package 已保存但 Manifest 生成失败，重试只补 Manifest，不会再次保存同一资产。

验证后的“保留修改”分支由 `ue_refresh_animation_scale_fix_batch_index` 完成：Preview 每次最多准备 2 个短路径独立 Export candidate；全部 Ready 后以 `REFRESH BATCH <batchPlanId>` 一次性构建一个同时包含整批目标的 paired SQLite + Revision Export generation，并原子切换 active pointer。Apply 成功后当前 MCP session 必须重启。正式 XinYueHu 样本只实测到 Index Preview，确认 Package / SQLite SHA 零写入；多资产 Apply 的原子 generation 行为由真实临时 SQLite + Revision Export Fixture 验证。

验证后的“撤销修改”分支由 `ue_rollback_animation_scale_fix_batch` 完成：DryRun 每次最多 2 个且零写入；全部 Ready 后必须关闭目标 Unreal Editor，再用 `ROLLBACK BATCH <batchPlanId>` 按 Save 反序每次最多 Commit 2 个资产，并做独立重载验证。部分 Save 失败时，先 Live Undo 仍为 `unsaved` 的内存项，再 persisted rollback 已保存项。

真实 UE5.6 持久化 Smoke 已验证：Root Track `1→100`、Authorized Save 到临时 Revision `8ee5391d...`、Independent Verify 通过、Index Refresh Preview 不改变 SQLite、Rollback DryRun 零写入、关闭 Editor 后 Commit 成功，Package SHA 精确恢复正式基线 `a3cb62ec...`，SQLite SHA 全程不变。Rollback 独立验证路径已缩短到 `WorkRoot/rollback-verify/<short-id>`，修复了 Windows 下约 300 字符 Canonical Export 路径导致的写文件失败。

当前 P2 工具：

```text
ue_plan_animation_scale_fix_batch
ue_get_animation_scale_fix_batch
ue_apply_animation_scale_fix_batch_live
ue_save_animation_scale_fix_batch
ue_verify_animation_scale_fix_batch
ue_refresh_animation_scale_fix_batch_index
ue_rollback_animation_scale_fix_batch
ue_undo_animation_scale_fix_batch
```

执行语义：

```text
Audit Report
→ Candidate Selection
→ Immutable Batch Plan
→ Bounded Live Apply + Runtime Verify
→ [Unsaved branch] Reverse Live Undo
→ [Persist branch] Bounded Authorized Save
→ Independent Verify
→ [Keep branch] Bounded Index Preview → Atomic Paired Snapshot Apply → MCP Restart
→ [Revert branch] Rollback DryRun → Editor Closed → Reverse Persisted Rollback Commit
```

批量修改必须：

- 每个资产独立 Before Snapshot；
- 每个资产独立结果和错误；
- 支持部分失败但禁止静默跳过；
- 保存前显示会修改的 Package 数量；
- 保存前生成 Backup Manifest；
- 支持整组 rollback；
- 不允许把固定值 `100` 写死在通用逻辑中。

`expectedFinalScale` 应优先自动来自：

```text
Target Skeleton Root Reference Component Scale
```

用户可以覆盖，但必须显式。

---

## 5. P3：重定向输出后处理

P3 第一条只读纵向切片已实现并通过真实 UE5.6 零残留 Smoke。Retarget Batch 完成后不再重新扫描整个输出目录，而是直接使用该 Task 已记录的精确 `outputs[]`。每个输出现在额外带 `assetClass`、`assetType`、`skeletonPath`，因此后处理不按文件名猜类型。

新增工具：

```text
ue_start_animation_retarget_postprocess
ue_get_animation_retarget_postprocess
ue_plan_animation_retarget_postprocess
```

当前链路：

```text
Completed / Saved Retarget Batch
→ Exact Output Classification
→ AnimSequence only: existing bounded Animation Scale Audit
→ Build Suggested Fix / Manual Review / Reference Follow-up
→ Immutable Suggested Post-process Plan under WorkRoot
```

只有 `AnimSequence` 会进入现有 P1 Scale Audit。`BlendSpace` / `AimOffset` / `AnimMontage` 当前只进入 `referenceFollowups`，不修改引用资产。Additive / Root Motion Review 等不能安全自动修复的分类进入 `manualReview`，不新增旁路。

Suggested Plan 明确保持只读：

```text
modifiesAssets = false
autoApplyAllowed = false
requiresUserReview = true
referenceAssetMutationImplemented = false
```

这里不能直接生成可执行 P2 Batch Plan。新 Retarget 输出在 Batch 完成后可能尚未进入 immutable SQLite，而 P2 Plan 强制依赖 fresh Index Revision。存在 `root-lock-candidate` / `root-track-candidate` 时，P3 Suggested Plan 会显式返回：

```text
requiresRetargetOutputIndexRefreshBeforeP2Plan = true
p2Workflow = animation-scale-fix-batch
```

真实 UE5.6 Smoke 使用已有 IK Retargeter，只创建随机命名的一个新 AnimSequence，不调用 Retarget Setup、不覆盖现有动画。输出被 C++ 分类为 `AnimSequence`，Scale Audit 得到 `root-lock-candidate`，Suggested Plan 写入固定 WorkRoot；随后 Retarget Save + Rollback DryRun/Commit 删除临时输出。最终 `P3_*.uasset` 数量为 0，UnrealEditor 已关闭。

完整第一阶段契约见：

```text
docs/RETARGET_POSTPROCESS_TOOL.md
```

P3 下一条纵向切片解决 Retarget 输出的持久化 / paired Index Refresh / MCP Restart 边界，然后才把 eligible AnimSequence suggestion 转换为现有 P2 Batch Plan：

```text
P3 Suggested Plan
→ User Review
→ Authorized Retarget Save / Independent Verify
→ Paired Revision Export + SQLite Refresh
→ MCP Restart
→ Revalidate against fresh Index Revision
→ Existing P2 Animation Scale Fix Batch Plan
```

之后再单独实现 BlendSpace / AimOffset / Montage 引用更新。仍不得在 `Retarget Batch` 内静默修改 Root Lock 或 Root Track；Additive + Base Pose 继续属于 P4。

---

## 6. P4：Additive / Base Pose 求值

> 状态（2026-08-14）：第一片（只读诊断 `ue_diagnose_additive_animation`）、第二片（只读组合求值 `ue_evaluate_animation_with_base_pose`）、第三片（只读修复计划 `ue_plan_additive_base_pose_fix`）与第四片（**执行写入** `setAdditiveBasePoseFix`）均已完成。第二片用引擎内置路径（`GetAdditiveBasePose` → `GetBonePose_Additive` → `AccumulateAdditivePose` → `FCSPose`）实际合成 Base Pose + Additive Delta；第四片顺带修正了第二片的 base 采样顺序 bug（`BaseComponentPose` 原在 `AccumulateAdditivePose` 之后采样，而 `FCSPose::InitPose` 存引用、accumulate 就地改写 BasePose，导致 base 实际等于 combined）。第四片新增 live-write 操作 `setAdditiveBasePoseFix` 与 plan 工具 `ue_plan_additive_base_pose_fix_apply`，替换 `RefPoseSeq` / 写回 `RefFrameIndex` / 可选修正 `AdditiveAnimType`·`RefPoseType`，并在 ReadAfter 中重新合成组合姿势验证最终 Root Scale；快照/Undo/Discard/Save/Verify 复用现有 live-write 闭环。范围限定「我的项目」（`ProjectContent` + `/Game` 路径 + policy `allowedReferenceRoots`），禁止触碰其他工程。**第四片之后（2026-08-15，实测结论）**：修正后的组合求值证实——源 Manny additive 为 base=1/delta=0/combined=1（健康），重定向心月狐 additive 为 base≈100/delta≈-0.01/combined≈99。这里 `combined = raw` 恒成立，≈99 是 raw 里的重定向残留，而 **delta ≈ 0**（运行时几乎不改变 scale）。所以 Additive **不需要 scale-fix**——`≈99` 是求值假象，不是游戏里的最终比例；真实 minor 问题是自引用 Base Pose 造成的 delta ≈ -0.01（该是 0），由第四片 `setAdditiveBasePoseFix` 修正。曾尝试扩展 `setAnimationScaleFix` 支持 Additive（root track scale → 1），真实 Editor Smoke 证实 root track 改动**不会改变 Additive 的 combined scale**（仍 ≈99，验证回滚），故撤销该扩展、保持 `setAnimationScaleFix` 对 Additive 拒绝。**剩余**：复合资产（AimOffset/BlendSpace/Montage）重建仍在范围外。详见 `docs/ADDITIVE_ANIMATION_DIAGNOSIS_TOOL.md`、`docs/ADDITIVE_ANIMATION_EVALUATION_TOOL.md`、`docs/ADDITIVE_BASE_POSE_FIX_PLAN_TOOL.md`、`docs/ADDITIVE_BASE_POSE_FIX_WRITE_TOOL.md`、`docs/ANIMATION_SCALE_FIX_TOOL.md` §5 与 `dev_docs/ANIMATION_ADDITIVE_EVALUATION_RESULT.md`。

这是动画工具下一阶段最重要的技术问题。

当前限制：

- `AnimSingleNodeInstance` 不能脱离 Base Pose 正确求值 Additive；
- Additive 压缩 Scale 是 Delta，不是绝对 Scale；
- Base Pose 可能仍引用 Manny 动画；
- 单独预览 Additive 得到的最终比例不可信。

后续 Reader 需要：

```text
Additive Sequence
+ Ref Pose Type
+ Ref Pose Sequence
+ Ref Frame Index
+ Skeleton Compatibility
+ Base Pose Final Component Pose
+ Additive Delta
→ Combined Final Pose
```

建议新增：

```text
ue_diagnose_additive_animation
ue_evaluate_animation_with_base_pose
ue_plan_additive_base_pose_fix
```

第一版只支持明确 Base Pose Sequence 的 Additive；Animation Blueprint 任意图求值放到后续阶段。

修复内容可能包括：

- 将 Base Pose 引用替换为对应心月狐重定向动画；
- 修正 Base Pose Frame；
- 修正 Additive 类型；
- 重建依赖 AimOffset / BlendSpace / Montage；
- 在组合上下文中验证最终 Root/Pelvis Scale。

---

## 7. P5：浮空诊断 Reader

> **状态（2026-08-15）**：已完成。新增只读工具 `ue_diagnose_character_ground_contact`（C++ `editor.diagnoseCharacterGroundContact` + Python 分类 `character_ground_contact.py` + capability 复用 `retarget.inspect`）。详细子项与实现见 `docs/Plans/ANIMATION_TOOLS_P5_P9_DETAILED_PLAN_20260815.md` §1 与 `docs/CHARACTER_GROUND_CONTACT_TOOL.md`。

ModelPreview 后续需要判断浮空到底来自碰撞、Mesh Offset 还是动画。UEAgentKit 应提供只读诊断，而不是让 Agent直接猜。

建议工具：

```text
ue_diagnose_character_ground_contact
```

读取：

```text
Character Capsule Radius / Half Height
SkeletalMeshComponent Relative Transform
Mesh Bounds
Skeleton Reference Root / Pelvis / Foot Transforms
Animation Root / Pelvis / Foot Final Component Pose
Foot Socket / Bone 到 Capsule Bottom 的距离
左右脚最低点
Root Motion Z
Retarget Root Z
```

输出分类：

```text
mesh-offset-candidate
capsule-size-candidate
animation-root-z-candidate
pelvis-offset-candidate
foot-ik-needed
insufficient-context
```

工具第一版只读，不修改 Capsule 或 Mesh Relative Location。

---

## 8. P6：尾巴、衣服和 Cloth 资产诊断

UEAgentKit 不应先实现“自动开启 Cloth”，而应先提供结构读取能力。

建议新增 Skeletal Mesh / Physics Reader：

```text
ue_inspect_skeletal_secondary_motion
```

读取：

```text
附加骨骼链
父子层级
每骨骼顶点影响数量
最大/平均 Skin Weight
动画轨道是否存在
Physics Asset 路径
Physics Body / Constraint
Clothing Asset 数量
Cloth Section / LOD Mapping
Chaos Cloth Config 摘要
Anim Blueprint 中 AnimDynamics / RigidBody / Spring 节点
```

输出应区分：

```text
missing-bones
missing-skin-weights
no-animation-tracks
no-secondary-motion-node
no-physics-bodies
cloth-data-present
cloth-data-missing
cloth-binding-incomplete
```

后续写入能力再分开实现：

1. Anim Dynamics / Rigid Body 节点的受控创建；
2. Physics Asset Body / Constraint 创建；
3. Chaos Cloth Asset 创建与 Section 绑定；
4. Cloth 参数修改；
5. PIE 动态验证和性能证据。

禁止提供通用任意 AnimGraph 节点写入作为替代。

---

## 9. P7：项目级可写配置

“我的项目”是主要写入沙箱，但不能简单使用全局取消只读。

正确方案是项目级固定配置：

```text
Project Path
Policy Path
Allowed Asset Roots
Allowed Operations
Allowed Asset Classes
EnableWriteTools
EnableCommitTools
```

建议建立：

```text
config/projects/my-project-write.json
config/projects/model-preview-read.json
config/projects/model-preview-animation-write.json
```

语义：

- “我的项目”：允许测试目录和明确心月狐动画目录写入；
- ModelPreview 默认只读；
- ModelPreview 动画写入使用单独 Policy，范围只覆盖确认后的动画目录；
- Reforge 继续默认只读。

不要把 `allowedAssetRoots=/Game` 或任意 UObject 写入作为“取消只读”的实现。

---

## 10. P8：ModelPreview 接入

接入 ModelPreview 时按以下顺序：

```text
安装同一编译插件
→ 只读 Editor Status
→ 只读资产扫描
→ 生成当前动画比例基线
→ 选择复制出的测试动画
→ Live Apply + Undo
→ 用户确认
→ 扩大 Policy 到正式动画目录
→ 保存和独立验证
```

ModelPreview 第一轮禁止修改：

- Skeleton；
- Skeletal Mesh；
- Physics Asset；
- Animation Blueprint；
- Capsule；
- Cloth；
- 正式关卡。

只验证 AnimSequence Root Lock / Root Track 工作流。

---

## 11. P9：发布与合并

完成 P0 后，当前动画修复纵向切片可以作为独立提交保留在分支。

达到以下条件后再合入 `main`：

- 单资产诊断和修改 API 稳定；
- 响应字段区分 Runtime 与 Persisted；
- 保存与回滚真实 UE5.6 回归通过；
- 项目级 Policy 示例完成；
- 工具数量和文档更新；
- 与 Memory/Context 分支公共协议无冲突。

建议版本归属：

```text
0.8.0-dev Realtime Animation Tools
```

批量动画和 Additive 可以作为后续独立提交，不阻塞当前单资产修复工具合入。

---

## 12. 推荐执行顺序

```text
第一步：收口并提交当前单资产修复工具
第二步：批量只读 Audit
第三步：批量 Plan / Apply / Save / Rollback
第四步：重定向后处理集成
第五步：Additive + Base Pose
第六步：浮空诊断 Reader
第七步：尾巴/衣服/Physics/Cloth Reader
第八步：ModelPreview 小范围写入接入
第九步：0.8.0-dev 合并准备
```

当前不要同时推进 Additive、Cloth、AnimGraph 写入和批量保存。先完成每条纵向闭环，再进入下一层。
