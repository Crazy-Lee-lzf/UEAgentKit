# Live Editor Realtime I/O 开发计划

更新时间：2026-08-05
目标分支：`feature/live-editor-realtime-io`
当前基线：`5a46f5b`

## 1. 分支定位

本分支负责正在运行的 Unreal Editor 中的实时读取、受控操作、实时修改、分帧批处理和修改证据闭环。

目标链路：

```text
理解当前 Editor 状态
→ 读取用户当前打开或已经加载的资产
→ 必要时显式打开目标资产
→ 形成修改前事实与计划
→ 受控 Live Apply
→ 修改后实时读回
→ Undo / Discard / Authorized Save
→ 独立 Verify
→ Change Set / Memory Evidence
```

本分支不承担全项目首次索引、全量 Package SHA、海量 Canonical 导出和大型项目 SQLite 重建；这些属于离线索引与性能分支。

## 2. 当前已具备的基础

### 2.1 Live Read

```text
ue_editor_status
ue_get_selection
ue_get_open_assets
ue_get_dirty_assets
ue_get_current_level
ue_get_pie_state
ue_get_output_log
ue_get_compile_errors
ue_inspect_asset_live
ue_get_blueprint_graph_selection
ue_get_editor_context
```

当前语义：

- `ue_get_open_assets` 返回当前资产编辑器中打开的资产。
- `ue_inspect_asset_live` 使用 `StaticFindObject` 检查精确资产是否已加载，不隐式加载。
- `ue_get_editor_context` 聚合 Editor、World、Selection、Open Assets、Dirty Packages、Blueprint Graph Selection、Compile Errors 和 Output Log Cursor。
- Live Read 的事实源固定为 `source=live-editor-memory`，不能伪装成磁盘 Revision、Canonical Export 或 SQLite Snapshot。

### 2.2 Live Action

```text
ue_open_asset
ue_focus_asset
ue_sync_content_browser
ue_focus_actor
ue_compile_blueprint
ue_validate_asset
ue_validate_folder
ue_run_automation_test
```

这些操作可以改变窗口、选择、资产加载状态或 Blueprint 内存编译状态，但不自动保存 Package。

### 2.3 Realtime Foundation

```text
ue_get_editor_context
ue_start_batch_task
ue_get_batch_task
ue_cancel_batch_task
```

已有 `scanCurrentWorld` 分帧任务，具备：

- Editor Session / World Identity 绑定；
- 进度、取消、超时；
- 约 2 ms 单帧预算；
- 详情分页与响应大小限制。

### 2.4 Live Write

0.7.0 已开放 Data Asset、Material Instance 和 DataTable 的注册式写入，并具备：

- Plan / Policy / Revision 门禁；
- `FScopedTransaction` 与 `UObject::Modify()`；
- Before Snapshot；
- No-op 检查；
- 失败恢复；
- Dirty 状态；
- 精确 Undo / Discard；
- Authorized Save；
- 独立 Verify；
- Journal / Change Set / Evidence。

## 3. 动画重定向工作流已经合入

`feature/animation-retarget-workflow` 已于 2026-08-05 Fast-forward 合入本分支，合入范围为：

```text
9fd60fd  retarget API spike
2836190  read-only retarget analysis core
4f2ff5f  live retarget read-only analysis
5708b5a  retarget plan and IK rig setup
cdc362a  retargeter configuration and retarget pose
69efa91  batch animation retarget
1e31463  authorized save for retarget outputs
efa921f  retarget validation
136f588  BlendSpace / Montage validation fix
5a46f5b  save / verify / rollback / evidence closed loop
```

当前工作流提供：

```text
ue_analyze_animation_retarget
ue_plan_animation_retarget
ue_apply_animation_retarget_setup
ue_start/get/cancel_animation_retarget_batch
ue_save_animation_retarget_batch
ue_validate_animation_retarget
ue_verify_animation_retarget_batch
ue_rollback_animation_retarget_batch
```

该工作流将作为本分支验证“读取复杂资产 → 分析 → 修改 → 保存 → 验证 → 回滚”的首个大型专用域。

## 4. 当前优先问题：动画比例异常

真实项目「我的项目」中的心月狐重定向结果存在：

```text
Idle 等部分动画：角色整体缩小约 100 倍
跑步动画：比例正常
另有部分动画：角色整体放大约 100 倍
```

该现象具有明显的动画相关性，而不是固定目标网格缩放错误。优先怀疑：

1. 源动画 Root / Pelvis 骨骼存在不同的 Scale Key；
2. 源 Skeleton 与目标 Skeleton 的 Root Reference Pose Scale 存在 1 / 100 / 0.01 差异；
3. IK Retargeter 的 Retarget Root、Root Translation Mode 或 Global Scale 处理在不同动画数据上分支；
4. 动画压缩或重定向后 Root Scale Track 被保留、移除或错误转换；
5. 某些源动画来自不同导入批次或不同 Skeleton 资产，单位约定不一致；
6. 当前验证器只检查位置、旋转和有限值，未把 Scale 异常作为失败条件。

该问题用来推动以下只读能力：

- Animation Sequence 实时摘要；
- Skeleton Reference Pose 与骨骼层级读取；
- Skeletal Mesh Import / Bounds / Skeleton 关联读取；
- IK Rig / IK Retargeter 配置读取；
- Root / Pelvis Transform Track 分段采样；
- 重定向前后动画的 Transform 对照；
- Scale 异常诊断与验证门禁。

## 5. Live Asset Read

### 5.1 统一入口

建议新增：

```text
ue_read_asset_live
```

建议输入：

```json
{
  "asset_path": "/Game/Characters/XinYueHu/Animations/Retargeted/RTG_MM_Idle.RTG_MM_Idle",
  "open_if_needed": false,
  "detail_level": "summary"
}
```

### 5.2 加载规则

```text
资产已经打开
    直接读取当前 UObject
    不重新加载或重新打开

资产已加载但未打开
    直接读取
    不改变用户界面

资产未加载且 open_if_needed=false
    返回 live-editor-asset-not-loaded

资产未加载且 open_if_needed=true
    显式调用受控打开逻辑
    等待 UObject 可用
    再读取
```

普通搜索、`ue_get_editor_context` 和 `ue_inspect_asset_live` 不得隐式加载资产。

### 5.3 返回证据

```text
source=live-editor-memory
assetPath
assetClass
readerId
readerVersion
loadedBefore
loadedByBridge
openBefore
openedByBridge
packageDirty
saved=false
durationMs
assetDetails
```

Dirty 对象的返回值只代表 Editor 内存当前状态。

## 6. Reader 架构

类型专用字段只维护一套纯 UObject 序列化实现：

```text
离线入口
    FAssetData / Commandlet
    → 显式决定是否加载
    → 公共 Build...Details()

实时入口
    StaticFindObject / 已打开 UObject
    → 公共 Build...Details()
```

公共 Reader 函数必须：

- 不自行加载资产；
- 不打开窗口；
- 不保存 Package；
- 不修改 UObject；
- 不访问任意反射 Method；
- 返回稳定 Reader ID / Version；
- 对大型数组分页或截断。

## 7. 第一批 Reader 顺序

### P0：动画比例诊断 Reader

按本次真实问题优先实现：

1. `AnimSequence`；
2. `Skeleton`；
3. `SkeletalMesh`；
4. `IKRigDefinition`；
5. `IKRetargeter`。

首版动画读取字段：

```text
Skeleton Path
Play Length
Frame / Sample Rate
Additive 设置
Root Motion 设置
Retarget Source / Pose 相关元数据
Track 数量与 Bone 名称
Root / Pelvis 在 0/25/50/75/100% 的 Local 与 Component Transform
每个采样点的 Translation / Rotation / Scale
Scale 的 min/max/non-uniform/negative/near-zero 状态
```

首版 Skeleton 读取字段：

```text
Bone Count
Root Bone
Parent Chain
Reference Pose Local Transform
Root / Pelvis Reference Scale
Compatible Skeleton
Translation Retargeting Mode（可用时）
```

首版 Retargeter 读取字段：

```text
Source / Target IK Rig
Retarget Root
Chain Mapping
Root Settings
Global Settings
Target Retarget Pose
Scale / Translation 相关设置
```

### P1：Static Mesh / Skeletal Mesh 通用摘要

- Bounds；
- 材质槽；
- LOD / Section；
- Collision；
- Socket；
- Nanite / Lightmap 等适用字段。

### P2：Material Instance / DataTable / Data Asset

复用现有写入域的 Schema 和读回逻辑，统一 Before / After。

### P3：Blueprint Summary / Graph Read

- Blueprint 变量；
- Components；
- Graph Summary；
- 当前打开 Graph 的节点与 Pin；
- 后续支持安全 Graph 修改。

## 8. 动画比例问题排查阶段

### A. 建立对照样本

至少选：

```text
缩小样本：Idle
正常样本：Jog / Run
放大样本：用户确认的异常动画
```

每个样本同时记录：

- 源 Animation Sequence；
- 重定向后 Animation Sequence；
- 源/目标 Skeleton；
- Source/Target Skeletal Mesh；
- IK Rig；
- IK Retargeter；
- 动画资产 Revision。

### B. 只读采样

对 Root、Retarget Root、Pelvis、Head、Hands、Feet 采样：

```text
Reference Pose
0%
25%
50%
75%
100%
```

比较：

```text
源 Local Scale
源 Component Scale
目标 Local Scale
目标 Component Scale
目标网格 Component Scale
最终预览 Bounds 比例
```

### C. 判断层级

```text
源动画本身已有 0.01 / 100 Scale
    内容导入或源动画问题

源动画正常，重定向结果出现异常
    Retargeter / RunRetarget / Root Settings 问题

动画资产采样正常，预览角色异常
    Skeletal Mesh / Preview Mesh / Animation Blueprint / Component Transform 问题

只在 ABP 中异常，Anim Sequence Editor 正常
    ABP 节点、Slot、Montage、Modify Bone 或 Component 配置问题
```

### D. 修复方式门禁

未定位前禁止批量“乘 100”或“乘 0.01”。

修复必须优先作用于根因：

1. 修正源动画 / Skeleton 单位；
2. 修正 Retarget Root / Root Settings；
3. 重定向时过滤或规范化 Root Scale Track；
4. 最后才考虑对明确资产做受控 Scale 修复 Operation。

## 9. 验证器补强

当前 `ue_validate_animation_retarget` 已检查：

- Skeleton 匹配；
- Play Length；
- Root/Pelvis/Head/Hands/Feet 的位置和旋转采样；
- NaN / Inf；
- 位移与旋转异常。

需要增加：

```text
Scale 有限值
Near-zero Scale
Negative Scale
Non-uniform Scale
与 Reference Pose Scale 的倍率
跨帧 Scale 波动
Root / Pelvis Component Scale
最终 Bounds 与目标 Reference Bounds 比率
```

建议第一版门禁：

```text
任一主骨骼 Scale 分量 <= 0
    error

任一主骨骼 Scale 分量 < 0.1 或 > 10
    error

相对 Reference Pose 比率 < 0.1 或 > 10
    error

三个分量差异超过 10%
    warning / error，按骨骼类型区分

Bounds 尺寸与目标参考差异超过 10 倍
    error
```

门限需要通过真实样本校准，不能只依赖固定常数。

## 10. 与 Live Write 的闭环

```text
Live Read Before
→ Plan / Policy / Revision
→ Live Apply
→ Live Read After
→ 自动比较
→ Undo / Discard / Authorized Save
→ Independent Verify
```

对动画重定向工作流：

```text
读取源动画 / Skeleton / Retargeter
→ 生成 Retarget Plan
→ Apply Setup
→ Batch Retarget
→ 读取输出动画
→ Scale / Motion Validate
→ Save
→ 独立重载再 Validate
→ Rollback / Evidence
```

验证失败的输出不得被自动视为成功完成。

## 11. 性能和安全规则

- 已打开或已加载资产优先直接读取。
- 未加载资产默认不打开。
- `open_if_needed=true` 必须返回加载和打开证据。
- 一次只显式打开一个大型 Mesh 或动画相关复杂资产。
- 不自动关闭用户已打开的窗口。
- 不自动保存。
- 不在普通读取中刷新全项目索引。
- Live Bridge 默认 2 秒；需要加载或长采样时转为可观察任务，或显式使用更高超时。
- 单响应不超过 1 MiB；Track/Bone/Frame 详情分页或截断。
- PIE/SIE 下显式打开和写入继续拒绝；纯读取是否允许按 Capability 单独测试。

## 12. 测试项目

```text
我的项目
    动画重定向、受控写入、Undo/Save/Verify Fixture

ModelPreview
    心月狐真实角色、Skeleton、Animation、Material、Blueprint Editor Context

DarkRuins
    大型 Mesh、Nanite、DDC 与实时读取性能
```

不修改 Reforge。

## 13. 实施顺序

1. 完成本计划与动画工作流合入记录。
2. 对 Idle / Run / 放大样本做只读 Root Scale 对照。
3. 增强 Retarget Validation 的 Scale 与 Bounds 诊断。
4. 新增 Animation / Skeleton / Skeletal Mesh / Retargeter Live Reader。
5. 定位并修复比例异常根因。
6. 用异常样本建立真实 UE5.6 回归。
7. 将 Live Reader 接入 Retarget Before / After Readback。
8. 扩展 Static Mesh、Material、DataTable、Data Asset Reader。
9. 再推进 Blueprint Graph 的安全读取与修改。

## 14. 第一里程碑完成标准

- 动画工作流完整存在于 `feature/live-editor-realtime-io`。
- 能列出并识别用户当前打开的动画相关资产。
- 能读取已加载 AnimSequence、Skeleton、Skeletal Mesh 和 IK Retargeter 摘要。
- 未加载资产默认不隐式打开。
- 明确允许后可打开并返回 `loadedByBridge/openedByBridge`。
- 能自动检测 Idle 缩小 100 倍和放大 100 倍样本。
- 能说明异常发生在源动画、重定向结果、Skeleton、Retargeter还是 ABP/Component 层。
- Scale 异常进入 Validation Error，不再出现结构验证 `passed` 但肉眼比例严重错误。
- 修复后 Idle、Run 和放大样本在 Anim Sequence Editor 与角色预览中比例一致。
- 全流程不自动保存，保留 Undo / Authorized Save / Verify / Rollback。
- UE5.6 编译、Python 回归和真实 Editor 回归通过。
