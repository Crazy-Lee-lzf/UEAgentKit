# 批量动画比例只读审计工具

> 分支：`feature/live-editor-realtime-io`
> 适用引擎：UE 5.6
> 状态：P1 第一条纵向闭环已实现并通过真实 UE5.6 Editor Smoke

---

## 1. 目标

P1 在单资产 `ue_diagnose_animation_scale` 之上增加面向 Agent 的批量只读任务。它不创建 Patch、不修改 AnimSequence、不产生 Dirty Package，也不保存资产。

当前三件套：

```text
ue_start_animation_scale_audit
ue_get_animation_scale_audit
ue_cancel_animation_scale_audit
```

执行模型：

```text
Explicit AnimSequence Object Paths
→ Start Audit
→ Get: 每次推进一个有界 Batch
→ ue_diagnose_animation_scale
→ 分类 + 精简证据
→ 分页返回结果
→ Completed / Cancelled / Failed
```

当前是 MCP Server 侧的有界任务编排，底层复用已经通过真实 Editor 验证的 `ue_diagnose_animation_scale`。没有增加新的 C++ 写入面。

---

## 2. 为什么不一次扫描全部动画

`ue_diagnose_animation_scale` 需要在 Editor World 中为动画建立临时 SkeletalMeshComponent 并求值最终 Component Space Pose。对大型项目一次加载、求值数百个动画会造成明显的 CPU、内存和动画压缩压力。

因此 Audit 使用轮询推进：

- 单任务最多 1000 个显式 AnimSequence Object Path；
- `batchSize` 默认 1，最大 8；
- 每次 `ue_get_animation_scale_audit` 最多推进一个 Batch；
- `detailLimit` 最大 50；
- `loadIfNeeded` 必须显式决定，默认 `false`；
- Editor Session 发生变化时任务失败，不跨 Session 混合证据；
- 同一 MCP Server 同时只保留一个运行中的 Animation Scale Audit。

这相当于把大任务切成 Agent 可控制的小步，而不是让一次 Tool Call 长时间占用 Editor。

---

## 3. Start

```text
ue_start_animation_scale_audit
```

主要参数：

| 参数 | 说明 |
|---|---|
| `animationPaths` | 可选，1–1000 个精确 `/Game/...Asset.Asset` AnimSequence Object Path |
| `pathPrefix` | 可选，从固定 immutable SQLite Index 中按明确 `/Game/...` 前缀枚举 AnimSequence |
| `boneNames` | 可选，默认 `Root`、`pelvis`，最多 16 个 |
| `loadIfNeeded` | 是否允许为诊断显式加载目标动画，默认 `false` |
| `batchSize` | 每次 Get 最多处理多少个动画，1–8，默认 1 |

`animationPaths` 与 `pathPrefix` 必须二选一。使用 `pathPrefix` 时只查询 MCP 启动时固定的只读 SQLite Snapshot，不临时扫描 Content 目录；候选路径和 `indexSnapshotId` 会冻结进任务，然后才按正常 Batch 流程访问 Editor。

如果目录命中超过 1000 个 AnimSequence，Start 会要求缩小前缀，不会静默截断。

Start 只创建 MCP 内存任务，不修改动画内容。

---

## 4. Get

```text
ue_get_animation_scale_audit
```

每次调用：

1. 验证固定 UE Editor Session 仍然是任务开始时的 Session；
2. 如果任务仍在运行，处理下一个有界 Batch；
3. 复用 `ue_diagnose_animation_scale` 获取 Raw/Compressed Track、Skeleton Reference Pose 和最终 Editor World Pose；
4. 对结果分类；
5. 返回当前进度、分类统计和分页 Detail。

主要返回：

```text
state
progress.processedAssets
progress.totalAssets
progress.completedPercent
summary.classificationCounts
details.items
```

每个 Detail 包含：

```text
Asset Path
Skeleton
Additive / Base Pose 元数据
Root Motion / Force Root Lock
Root Track Raw / Compressed / Reference Scale
Preview Evaluation Status
Root / Pelvis Component Scale
Pelvis Component Location
Classification
Suggested Fix
```

---

## 5. 分类

当前第一版分类：

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

分类只用于生成审计建议，不会自动执行修复。

优先原则：

- Additive 始终进入 `additive-requires-base-pose`，不会推荐直接改 Root Scale；
- 已求值 Root Scale 与 Skeleton Reference Component Scale 接近时为 `normal`；
- Root Motion 打开且最终比例异常时优先 `root-motion-review`；
- `Force Root Lock=false`、Root Track≈1、Skeleton Reference Scale 明显不是 1 时优先 `root-lock-candidate`；
- Root Track 与目标 Skeleton Reference Scale 明显不一致时为 `root-track-candidate`；
- 其余根据最终比例差异归为 too-small / too-large。

---

## 6. Cancel

```text
ue_cancel_animation_scale_audit
```

Cancel 只改变 MCP 内存任务状态。未处理的动画不会被加载，也不会触发任何 Editor 写入。

---

## 7. 真实 UE5.6 Smoke

测试资产：

```text
/Game/Characters/XinYueHu/Animations/Retargeted/
MM_Idle_XinYueHu.MM_Idle_XinYueHu
```

真实结果：

```text
Classification          = normal
Candidate Source        = immutable-index pathPrefix
Force Root Lock         = true
Root Track First Scale  = 1
Evaluated Root Scale    = 100
Cancel                  = passed
Disk Package SHA        = unchanged
SQLite SHA              = unchanged
```

这验证的是完整 MCP `start → get → cancel` 路径，并通过真实 UE5.6 Editor Bridge 调用了最终姿势诊断。

---

## 8. 当前边界

当前已支持显式 Object Path 列表，以及从固定 immutable Index 按明确 `pathPrefix` 枚举候选动画。目录模式不会扫描磁盘，也不会把索引外的新资产偷偷纳入任务。

后续 P1 增量可以增加：

- 更丰富的 Root/Pelvis/Foot 统计；
- 按分类过滤和排序；
- Audit Report 持久化导出；
- 大样本性能门禁。

这些仍保持只读，不与 P2 批量修复混在同一个 Tool 中。
