# Live Asset Read 实施计划交接

更新时间：2026-08-04
目标分支：`feature/live-editor-realtime-io`
当前存放分支：`feature/performance-benchmarks`

> 本文只作为跨分支需求与接口交接。当前未修改 `feature/live-editor-realtime-io` 的 Worktree、代码或文档；由正在维护实时分支的 Agent 在合适时机吸收。

## 1. 目标

Live Asset Read 负责从正在运行的 Unreal Editor 中读取用户当前已经打开或已经加载的资产；目标未加载时，可以在调用方明确允许的情况下由 Agent 显式打开资产，再从 Editor 内存读取类型专用详情。

目标链路：

```text
获取当前 Editor Context
→ 判断目标是否已打开/已加载
→ 已加载：直接读取
→ 未加载且允许：显式打开
→ 调用类型专用 Reader
→ 返回 Editor 内存事实与加载证据
→ 为 Live Write 提供 Before / After Readback
```

该能力不是全项目索引替代品。它服务于当前工作资产、实时分析和实时修改闭环。

## 2. 当前已有基础

0.7.0 已存在：

```text
ue_get_editor_context
ue_get_open_assets
ue_inspect_asset_live
ue_open_asset
ue_focus_asset
ue_get_dirty_assets
ue_get_blueprint_graph_selection
```

当前语义：

- `ue_get_open_assets`：通过 `UAssetEditorSubsystem` 返回已打开资产。
- `ue_inspect_asset_live`：使用 `StaticFindObject` 检查目标是否已加载，不隐式加载，并返回 `loadedByBridge=false`。
- `ue_open_asset`：显式加载并打开资产编辑器，不保存 Package。
- `ue_get_editor_context`：聚合 Selection、Open Assets、Dirty Packages、当前 World、Blueprint Graph Selection、Compile Errors 和日志游标。

当前缺口：已经加载的 Static Mesh、Material Instance、DataTable、Data Asset 等资产尚无统一的类型专用深度读取 Tool。

## 3. 核心使用规则

### 3.1 已打开资产

当目标出现在 `ue_get_open_assets` 中：

```text
直接读取当前 UObject
不重新打开
不重新加载
不保存
返回 openedBefore=true
```

这一路径应优先用于用户正在查看或编辑的资产。

### 3.2 已加载但未打开资产

当 `ue_inspect_asset_live` 返回：

```text
loaded=true
openInEditor=false
```

应直接从当前 UObject 读取，不打开资产窗口，不干扰用户界面。

### 3.3 未加载资产

默认不加载：

```text
openIfNeeded=false
→ 返回 live-editor-asset-not-loaded
```

只有调用方明确允许时：

```text
openIfNeeded=true
→ 调用受控打开逻辑
→ 等待 UObject 和资产编辑器可用
→ 执行类型专用读取
```

打开资产可能触发 DDC、Nanite、距离场或其他 UE 原生派生数据构建，因此不得隐藏在普通搜索、Editor Context 或 `ue_inspect_asset_live` 中。

## 4. 建议 Tool

首选统一高层入口：

```text
ue_read_asset_live
```

建议输入：

```json
{
  "asset_path": "/Game/MS/3D/SM_Rock.SM_Rock",
  "open_if_needed": false,
  "detail_level": "summary"
}
```

建议限制：

```text
asset_path       精确 /Game/...Asset.Asset
open_if_needed   默认 false
detail_level     summary / standard / detailed
```

第一版可只开放 `summary`，避免协议先于实测扩张。

不建议第一版增加：

- 自动关闭资产窗口。
- 批量打开资产。
- 并行读取多个大型 Mesh。
- 任意 UObject 属性遍历。
- 任意反射 Method 调用。

## 5. 建议返回 Envelope

公共字段：

```json
{
  "source": "live-editor-memory",
  "assetPath": "/Game/...Asset.Asset",
  "assetClass": "/Script/Engine.StaticMesh",
  "readerId": "static-mesh-v1",
  "readerVersion": 1,
  "loadedBefore": false,
  "loadedByBridge": true,
  "openBefore": false,
  "openedByBridge": true,
  "packageDirty": false,
  "saved": false,
  "durationMs": 123.4,
  "assetDetails": {}
}
```

必须区分：

```text
loadedBefore      调用前 UObject 是否已在内存
loadedByBridge    本次是否触发加载
openBefore        调用前是否已有资产编辑器
openedByBridge    本次是否打开资产窗口
packageDirty      当前 Editor 内存 Package 状态
saved             Live Read 固定为 false
```

若读取的是 Dirty 对象，返回值只代表 Editor 内存，不得声称是磁盘 Revision、SQLite Snapshot 或 Canonical Export。

## 6. 数据源语义

四种事实源必须继续分开：

```text
Editor Memory     当前打开、加载、选择、Dirty 和未保存值
Disk Package      当前已保存 .uasset/.umap
Revision Export   最近一次 Canonical Revision Snapshot
SQLite Index      最近一次不可变索引 Snapshot
```

Live Read 返回：

```text
source=live-editor-memory
```

若需要对比磁盘或 SQLite，应由上层组合调用，不在 Live Reader 内隐式启动离线 Commandlet 或刷新全项目索引。

## 7. 公共 Reader 重构

当前离线 Static Mesh Reader 内部直接调用：

```cpp
UStaticMesh* StaticMesh = Cast<UStaticMesh>(AssetData.GetAsset());
```

应拆分为两层：

```cpp
EAssetReaderStatus BuildStaticMeshDetails(
    const UStaticMesh* StaticMesh,
    const FAssetReaderOptions& Options,
    TSharedRef<FJsonObject>& OutDetails,
    FString& OutError);
```

离线入口：

```text
FAssetData
→ 显式决定是否 GetAsset()
→ BuildStaticMeshDetails()
```

实时入口：

```text
StaticFindObject / 已打开 UObject
→ BuildStaticMeshDetails()
```

要求：

- Static Mesh 字段只维护一套。
- Reader 输出 `readerId/readerVersion` 一致。
- 离线和实时的加载证据字段分开包装。
- 公共函数本身不加载资产、不打开窗口、不保存 Package。

公共重构完成后，优先合入 `main`，再由性能与实时长期分支同步，避免两个分支复制实现。

## 8. Static Mesh 第一阶段读取范围

复用现有字段：

- LOD 数量。
- 每个 LOD 的 Section 数量。
- 材质槽、Imported Slot、Material、Overlay Material。
- Bounds。
- Lightmap Resolution 和 Coordinate Index。
- Nanite 配置。
- Collision Trace Flag 和简单碰撞数量。
- Socket 名称与 Transform。

后续经实测再增加：

- LOD 顶点数和三角形数。
- UV Channel 数量。
- LOD Screen Size 和 Reduction Settings。
- Section 与材质槽对应关系。
- Nanite 实际资源统计。
- Distance Field 状态。
- 估算 Render Memory。

第一版不要为了补齐全部 Mesh 数据而扩大加载风险；先验证已加载对象的稳定读取。

## 9. DDC、内存和磁盘规则

### 9.1 已经加载的资产

通常附加成本较低，但仍需记录读取耗时。不得假设“已加载”就绝对不会发生任何懒构建。

### 9.2 Agent 显式打开的资产

可能触发：

- Nanite DDC。
- 距离场。
- Static Mesh Render Data。
- LOD 派生数据。
- Shader 或其他相关缓存。

这类变化位于 DDC、Intermediate 或 Saved，不等于修改 Content Package。

### 9.3 第一阶段保护

- 一次只打开并深读一个 Static Mesh。
- 不并行读取多个大型 Mesh。
- 单资产操作默认超时 5 分钟。
- 不自动保存。
- 默认不自动关闭用户已经打开的资产。
- Agent 自己打开的资产第一版也不自动关闭，避免关闭错误窗口或干扰后续操作。
- 返回 `loadedByBridge/openedByBridge`，让上层决定后续行为。

普通一两个 Mesh 的 UEAgentKit 输出通常只有 KB 级；主要风险是瞬时内存和 DDC 增量。

## 10. 与 Live Write 的闭环

Live Read 应成为修改前后的标准读回层：

```text
Live Read Before
→ Plan / Policy / Revision
→ Live Apply
→ Live Read After
→ 比较 Before / After
→ Undo / Discard / Authorized Save
→ 独立 Verify
```

用途：

- 修改前生成准确 Before。
- 修改后确认内存值与请求一致。
- 检测 No-op。
- 检测目标被用户或其他工具再次修改。
- 给 Change Set 和 Memory Evidence 提供当前 Editor 证据。

Live Read 本身不获得写权限，也不绕过现有 Policy、Revision、Transaction 和 Save 门禁。

## 11. 测试矩阵

### 11.1 状态路径

1. 资产已经打开：直接读取，`openedByBridge=false`。
2. 资产已加载但未打开：直接读取，不改变 UI。
3. 资产未加载且 `openIfNeeded=false`：稳定拒绝。
4. 资产未加载且 `openIfNeeded=true`：显式打开后读取。
5. 目标不存在、Class 不支持或 Object Path 不精确：稳定拒绝。
6. PIE/SIE：按现有 Live Action 规则拒绝显式打开；对已加载对象是否允许只读需要单独确定并测试。

### 11.2 Dirty 语义

1. Clean 已打开资产。
2. 用户手工修改后 Dirty、未保存。
3. Live Apply 后 Dirty。
4. Undo/Discard 后读回。
5. Authorized Save 后重新读取。
6. Editor 重启后磁盘值恢复为事实源。

### 11.3 性能与稳定性

- 普通 Static Mesh 读取 100 次。
- 同一个已加载 Mesh 连续读取 1000 次，检查内存和 Handle。
- 依次读取 10–100 个已加载 Mesh。
- 显式打开普通 Mesh。
- 显式打开大型 Nanite Mesh。
- DDC 已存在与缺失分别测试。
- 记录 Working Set、Private Memory、DDC 增量和操作耗时。
- 单响应不得超过 Bridge 1 MiB；详细数组必须分页或截断。

测试项目优先：

```text
我的项目       受控功能 Fixture
ModelPreview   真实角色/材质/蓝图
DarkRuins      大型 Static Mesh、Nanite、External Actor
```

不使用 Reforge。

## 12. 建议阶段

### P0：只读公共函数

- 从离线 Static Mesh Reader 中拆出纯 UObject 序列化函数。
- 保持原有 Canonical 输出兼容。
- 不新增 Live Tool。

### P1：已加载资产读取

- 新增 `ue_read_asset_live` 或等价 Capability。
- 第一版 `open_if_needed=false`。
- 支持 Static Mesh Summary。
- 验证 Dirty 内存语义。

### P2：显式打开后读取

- 增加 `open_if_needed=true`。
- 返回完整加载/打开证据。
- 增加超时和大型 Mesh 测试。

### P3：Live Write Readback

- 修改前自动调用 Reader。
- Apply 后读回。
- Change Set 保存 Before/After 摘要。
- 不改变现有显式确认和保存规则。

### P4：扩展资产类型

建议顺序：

1. Material Instance。
2. DataTable。
3. Data Asset。
4. Blueprint Summary。
5. Skeletal Mesh、Skeleton 和 Animation。

每类都复用已有离线序列化逻辑，不允许复制第二套 Schema。

## 13. 完成标准

第一阶段完成需要：

1. 能列出当前已打开资产。
2. 能区分 Open、Loaded 和 Unloaded。
3. 已加载 Static Mesh 可深读且不重新加载。
4. 未加载资产默认拒绝，不发生隐式 DDC 构建。
5. 明确允许后可打开并读取，返回 `loadedByBridge/openedByBridge`。
6. 读取 Dirty 内存值时明确标记 `source=live-editor-memory`。
7. 不保存 Content，不修改 SQLite/Revision Export。
8. 与离线 Reader 使用同一字段实现。
9. 普通资产响应满足 Live Bridge 2 秒默认超时；大型加载转为可观察的受控 Action 或明确提高单次超时。
10. UE5.6 编译、Python 回归和真实 Editor 测试通过。

## 14. 与性能分支的依赖

性能分支负责：

- L0/L1/L2/L3 调度。
- 离线批处理资源记录。
- DarkRuins 大型 Mesh 测试数据。
- 公共 Reader 纯序列化接口的兼容要求。

实时分支负责：

- 当前 Editor 状态。
- 已打开/已加载判断。
- 显式打开动作。
- Editor 内存 Dirty 语义。
- Live Tool Envelope。
- 与 Live Write 的 Before/After Readback。

任何共享代码先通过 `main` 汇合，不在两个长期分支各自维护同名 Reader。
