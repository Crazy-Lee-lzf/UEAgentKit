# UE Agent Kit 离线索引性能实施计划

更新时间：2026-08-04
适用分支：`feature/performance-benchmarks`

## 1. 目标

本计划负责 UE Agent Kit 的全项目离线读取、首次索引、增量索引和大型项目性能门禁。目标不是让所有读取都变成实时操作，而是建立一条可分层、可恢复、可取消、可测量的批处理链路：

```text
L0 Registry Inventory
→ L1 Fast Revision
→ 变化候选筛选
→ L2 Selective Semantic Read
→ SQLite / 分片输出
→ 按需 L3 Full Audit
```

核心要求：

1. 普通项目搜索和实时编辑不得隐式触发全项目扫描、完整 SHA 或专用 Asset Reader。
2. 首次全量允许耗时较长，但必须有阶段计时、进度、取消和可恢复状态。
3. 未变化增量必须避免重新打开、解析和哈希全部 Canonical JSON。
4. 专用 Reader 只能按资产、目录、类型或任务显式选择，不再默认覆盖整个 `/Game`。
5. SQLite 继续作为主查询层；优化重点优先放在进入 SQLite 前的磁盘与导出链路。

## 2. 范围与边界

本分支负责：

- Asset Registry 全量清单。
- 快速 Revision 与完整 SHA 分层。
- Canonical Manifest 和增量候选选择。
- SQLite 首次建库和增量更新。
- 分片 JSONL、调试 JSON 和输出目录布局。
- External Actor/Object 聚合。
- 专用 Reader 的批处理调度、预算和性能记录。
- NativeSSD 与 `SimulatedHDD50` 基准。
- 50 GB、100 GB、160–180 GB 物理 Fixture 和 500k Asset/10m Reference 逻辑数据集。

本分支不负责：

- 在交互式 Editor 中打开资产。
- 读取当前用户已经打开的资产窗口。
- Live Write、Undo、Save、Verify。
- 实时资产对象的 Dirty 内存状态。
- 修改 `feature/live-editor-realtime-io` 的代码或文档。

实时读取所需的公共 Reader 接口只在本计划中定义兼容边界，实际 Live Tool 由对应实时分支实现。

## 3. DarkRuins 当前基线

测试项目：

```text
F:\UELecture\DarkRuinsMegascansSample
UE 5.6
Content 约 27.3 GB
UE Package 13,796
External Actor/Object 12,018
```

已完成结果：

| 项目 | 当前结果 |
|---|---:|
| Registry-only + Full SHA，13,666 Package | 209.877 秒 |
| 峰值 Working Set | 约 1.64 GiB |
| SQLite 首次建库 | 195.734 秒 |
| 未变化增量建库 | 104.281 秒 |
| 普通资产搜索 p95 | 30.56 ms |
| 打开数据库并搜索 p95 | 36.96 ms |
| 单资产读取 p95 | 0.73 ms |
| 深度 2 高入度引用遍历 p95 | 652.56 ms |

默认专用 Reader 全量模式的失败样本：

```text
单个约 1478 万三角形 Static Mesh
触发 Nanite / 距离场 / DDC 构建
单资产约 140 秒
进程工作集超过 9 GiB
```

结论：查询数据库不是当前主要瓶颈。主要瓶颈是全量 Package SHA、逐资产 JSON、增量前的全量文件遍历和隐式资产深度加载。

完整实测见 [`PERFORMANCE_BASELINE_DARKRUINS_20260803.md`](PERFORMANCE_BASELINE_DARKRUINS_20260803.md)。

## 4. 分层读取架构

### 4.1 L0 Registry Inventory

L0 只使用 Asset Registry 已有数据，不调用：

```cpp
FAssetData::GetAsset()
LoadObject()
StaticLoadObject()
```

L0 输出至少包含：

- Object Path、Package Name、Asset Name。
- Asset Class。
- Package Flags。
- Registry Tags。
- Hard/Soft Package Dependency 和 Searchable Name。
- 是否位于 External Actors/Objects。
- 所属 World 的可推导聚合信息。

L0 不计算完整 Package SHA，不创建 DDC，不加载 UObject。

初始性能目标，DarkRuins 13,666 Package：

```text
Commandlet 主处理目标        < 20 秒
完整进程目标                < 45 秒
峰值 Working Set            < 2 GiB
Content 改写                    0
```

该目标是设计门禁，不是当前版本承诺；首次实现先记录真实阶段耗时，再决定是否调整。

### 4.2 L1 Fast Revision

L1 用低成本字段识别变化候选：

```text
packagePath
fileSize
lastWriteTimeUtc
packageGuid（可用时）
registryClass
registryTagFingerprint
dependencyFingerprint
```

规则：

- 快速指纹完全一致：直接视为未变化候选，不读取完整 Package。
- 快速指纹变化：进入完整 SHA 或选择性 Reader。
- 文件新增、删除、移动：直接进入增量更新。
- Package GUID 不可用时，不能只凭时间戳宣称内容完全一致；结果必须标记 Revision 强度。

Revision 强度建议：

```text
registry-only
fast-file-fingerprint
package-guid-backed
sha256-confirmed
```

### 4.3 L2 Selective Semantic Read

L2 对明确候选加载 UObject，并执行类型专用 Reader。

允许的选择方式：

- 精确资产列表。
- 指定非根目录。
- 指定 Asset Class。
- 当前任务直接引用的资产集合。
- 快速 Revision 判定为变化的资产。

禁止默认行为：

```text
扫描 /Game 时自动读取全部 Static Mesh
扫描 /Game 时自动读取全部 Material/World
普通 ue_search 前执行专用 Reader
未变化增量重新深读全部资产
```

专用 Reader 每次必须记录：

```text
assetPath
readerId / readerVersion
loadedByReader
loadMs
readMs
totalMs
workingSetBefore / peak / after
DDC bytes before / after（测试环境可用时）
success / errorCode
```

### 4.4 L3 Full Audit

L3 用于：

- Release。
- CI 或 Nightly Audit。
- 明确指定目录的完整验证。
- Revision 冲突调查。
- 独立保存后验证。

L3 可以执行完整 SHA 和深度 Reader，但必须是显式后台任务，并支持进度、取消、Checkpoint 和失败恢复。

## 5. 增量 Manifest

### 5.1 Manifest 目标

当前未变化增量仍需 104.281 秒，因为 Indexer 会重新打开并哈希 13,666 个 Canonical JSON。新 Manifest 必须在不打开 Canonical 内容的情况下筛选未变化项。

建议每项记录：

```json
{
  "assetPath": "/Game/...Asset.Asset",
  "canonicalRelativePath": "canonical/...json",
  "canonicalSize": 1234,
  "canonicalMtimeUtc": "...",
  "canonicalSha256": "...",
  "packageRelativePath": "Content/...uasset",
  "packageSize": 123456,
  "packageMtimeUtc": "...",
  "packageGuid": "...",
  "fastRevision": "...",
  "confirmedRevision": "...",
  "readerId": "disabled",
  "readerVersion": 0
}
```

### 5.2 增量流程

```text
读取上一代 Manifest
→ 枚举当前 Registry / 文件元数据
→ 比较快速指纹
→ 得到 added / changed / deleted / unchanged
→ 只处理 added / changed / deleted
→ 分批更新 SQLite
→ 原子生成新 Manifest
```

未变化项不得：

- 打开 Canonical JSON。
- 重新解析 JSON。
- 重新计算 Canonical SHA。
- 重新写入 SQLite 明细行。

### 5.3 增量门禁

DarkRuins 13,666 项全部未变化时：

```text
目标总耗时             < 5 秒
目标 Canonical 打开数       0
目标 Package SHA 数          0
SQLite updated               0
SQLite deleted               0
```

100 个资产变化时：

```text
只处理 100 个变化项及受影响引用
不得退化为 13,666 项完整重建
输出 changedCandidateCount 和 processedCount
```

## 6. 输出与存储

### 6.1 SQLite 为主查询层

SQLite 已证明在 13,666 资产、43,863 引用下满足普通交互要求，因此第一阶段不更换数据库。

优先优化：

- 输入候选选择。
- 批量事务。
- Prepared Statement 复用。
- Reference 增量删除和重建范围。
- FTS 增量维护。

### 6.2 单资产 JSON 的定位

单资产 Canonical JSON 保留用于：

- 调试。
- 单资产证据。
- 可读 Diff。
- 独立验证快照。

大型全量默认输出应支持：

```text
shards/assets-000001.jsonl
shards/assets-000002.jsonl
manifest.json
index.sqlite3
```

分片约束：

- 固定资产数量或固定字节上限。
- 每个分片有 SHA、记录数、起止 Asset Path。
- 中断时只重建未完成分片。
- 长路径 Asset Path 不能直接决定超长物理文件名。

## 7. External Actor/Object 策略

默认结果按 World 聚合：

```text
worldPath
externalActorCount
externalObjectCount
classDistribution
dataLayerDistribution
totalPackageBytes
fastRevisionAggregate
```

只有在以下情况展开单项：

- 用户明确请求 Actor/区域详情。
- 当前 World/Cell 与任务有关。
- 某个 External Actor 被判断为变化候选。
- 引用或验证任务需要精确 Package。

这样可以避免普通上下文中返回数千个 StaticMeshActor 记录。

## 8. 专用 Reader 执行规范

### 8.1 默认关闭

全量离线索引默认：

```text
AssetReaders=disabled
```

### 8.2 显式按需

调用方必须提供至少一种限制：

```text
--Asset <exact object path>
--AssetList <file>
--Root </Game/non-root/path>
--Class <asset class>
--ChangedOnly
```

### 8.3 资源保护

第一阶段规则：

- Static Mesh Reader 串行执行，一次最多一个。
- 不并行加载多个大型 Mesh。
- 单资产默认超时 5 分钟。
- 记录峰值内存和执行时间。
- 不保存 Content。
- 明确说明 DDC、Intermediate、Saved 可能变化。
- 超时后终止当前 Commandlet，不将半成品标记成功。

普通一两个 Mesh 的输出 JSON 通常只有 KB 级；风险主要是资产加载时的瞬时内存和 DDC，而不是 UEAgentKit 输出体积。

## 9. 引用查询保护

高入度节点必须返回预算信息：

```text
visitedNodes
visitedEdges
returnedCount
maxDepth
timeBudgetMs
edgeBudget
truncated
truncationReason
nextCursor
```

初始门禁：

- 普通直接引用查询 p95 < 500 ms。
- 深度遍历默认时间预算 750 ms。
- 达到边数或时间预算时返回部分结果，不阻塞到任意深度完成。

## 10. 测试矩阵

### 10.1 现有真实工程

```text
我的项目                   功能和写入 Fixture
ModelPreview               真实角色/材质/蓝图样本
DarkRuinsMegascansSample   27 GB、External Actor、大型 Mesh
M4 副本                    约 3.8 GB 中型美术项目
```

本性能分支不改 Reforge。

### 10.2 规模阶段

```text
DarkRuins     13,666 Package
50 GB         第一物理 Fixture
100 GB        扩展曲线
160–180 GB    最终物理 Fixture
500k Asset    逻辑数据库
10m Reference 逻辑引用图
```

### 10.3 存储档位

- NativeSSD。
- SimulatedHDD50：50 MB/s、单文件打开延迟 10 ms、队列深度 1。

## 11. 实施阶段

### P0：统一测量

- 固定 `environment.json`。
- 记录启动、Registry、SHA、Reader、写出、验证、建库各阶段。
- 将 DarkRuins 当前结果保存为回归基线。

### P1：L0 Registry Inventory

- 新增不加载、不 SHA 的导出模式。
- 输出 Registry、Dependency 和快速统计。
- 在 DarkRuins 验证 13,666/13,666、0 Content 改写。

### P2：L1 Fast Revision 与 Manifest

- 定义 Revision 强度。
- 实现 Manifest 比较。
- 未变化增量不打开 Canonical。
- 增加新增、删除、移动和时间戳异常测试。

### P3：输出分片与 SQLite 增量

- 增加 JSONL Shard。
- 批量 SQLite Transaction。
- 增量更新 Reference/FTS。
- External Actor 按 World 聚合。

### P4：Selective Reader

- Reader 公共序列化函数与加载调度分离。
- 精确资产/目录/类型/ChangedOnly。
- Static Mesh 资源预算和失败恢复。
- 后续扩展 Material、DataTable、Data Asset、Blueprint。

### P5：大型 Fixture 与门禁

- 50 GB、100 GB、160–180 GB。
- NativeSSD 和 SimulatedHDD50。
- 建立 CI 可运行的小规模门禁和人工大型基准。

## 12. 预期代码与文档位置

```text
Plugin/UEAgentKit/.../AssetCatalogExportCommandlet.*
Plugin/UEAgentKit/.../AssetReaders/*
src/ue_agent_kit/indexer.py
src/ue_agent_kit/queries.py
scripts/RunAssetCatalog.ps1
scripts/ValidateAssetCatalog.py
tests/performance/
tests/python/
docs/PERFORMANCE_TEST_PLAN.md
docs/PERFORMANCE_BASELINE_DARKRUINS_20260803.md
```

## 13. 跨分支接口边界

实时分支可能需要读取已加载的 `UStaticMesh`。为避免两套字段实现，公共层最终应形成：

```cpp
EAssetReaderStatus BuildStaticMeshDetails(
    const UStaticMesh* StaticMesh,
    const FAssetReaderOptions& Options,
    TSharedRef<FJsonObject>& OutDetails,
    FString& OutError);
```

离线分支负责：

```text
选择是否加载
加载资产
批处理预算
调用公共序列化函数
输出 Canonical / SQLite
```

实时分支负责：

```text
检查已加载/已打开状态
必要时显式打开
从 Editor 内存取得 UObject
调用同一公共序列化函数
返回 Dirty 内存语义
```

公共接口稳定后应先合入 `main`，再由长期分支同步；不得在两个分支分别复制 Static Mesh 字段实现。

## 14. 完成标准

本计划第一阶段完成必须同时满足：

1. DarkRuins L0 全量成功、0 Content 改写、无 UObject 加载。
2. 全未变化增量不打开 Canonical、不计算 Package SHA，目标小于 5 秒。
3. 100 个变化资产只处理变化集合及受影响引用。
4. 默认全量路径不执行专用 Reader。
5. Static Mesh 精确按需读取支持超时和资源记录。
6. 单资产 JSON、分片 JSONL 和 SQLite 的职责明确。
7. 查询性能不低于当前 DarkRuins 基线。
8. Python 回归、UE5.6 编译和真实 DarkRuins 基准全部通过。

## 15. 下一步执行顺序

1. 为 Asset Catalog 增加不计算 Package SHA 的 L0 模式。
2. 为 Manifest 定义快速 Revision 和 Revision 强度。
3. 先实现 DarkRuins 全未变化增量不打开 Canonical。
4. 再实现 1、100、1000 个变化资产的增量基准。
5. 最后处理 JSONL Shard、External Actor 聚合和 Selective Reader。
