# DarkRuinsMegascansSample 首轮性能基线

更新时间：2026-08-03

## 1. 测试目的

本轮直接使用现有 UE5.6 项目 `F:\UELecture\DarkRuinsMegascansSample`，验证 UE Agent Kit 0.7.0 在真实 Megascans、World Partition 和大量 External Actor/Object 文件上的首次导出、SQLite 建库、未变化增量建库与查询延迟。

本轮只修改项目级 `Plugins\UEAgentKit`、`Intermediate`、`Saved` 和 DDC，不保存或改写 `Content` 资产。全部测试结束后检查到的 Content 修改文件数为 0。

性能开发分支与 Worktree：

```text
feature/performance-benchmarks
E:\WorkSpace\UEAgentKit-Performance
```

本轮关键提交：

```text
f9db495  perf: add registry-only asset catalog mode
96c5955  perf: support long canonical paths
```

## 2. 项目规模

```text
EngineAssociation       5.6
Content 大小             27,342,654,418 bytes
Content 文件             16,917
UE Package               13,796
.umap                     79
External Actor 文件      11,865
External Object 文件        153
生成 Package 合计       12,018
```

普通资产中主要类型：

```text
Texture2D                    689
MaterialInstanceConstant     384
StaticMesh                   352
World                         57
AnimSequence                  38
MaterialFunction              34
Material                      33
PoseAsset                     28
LevelSequence                  9
```

加入生成 Package 后，主要 Actor 类型包括：

```text
StaticMeshActor             8,739
DecalActor                 1,177
SpotLight                    163
ActorFolder                  153
PointLight                    65
NiagaraActor                  60
```

## 3. 测试环境与注意事项

- Unreal Engine：5.6。
- 插件版本：0.7.0。
- 项目位于 F 盘 exFAT；本报告不将其假定为机械硬盘。
- 测试输出和 SQLite 位于 E 盘。
- 第一次启动时 Unreal 清理了约 48 GiB 的旧虚拟纹理 DDC，使 F 盘可用空间从约 92 GiB 增加到约 140 GiB。因此第一次进程总耗时混入了 DDC 维护成本。
- Registry-only 模式仍为每个导出 Package 计算完整 SHA-256，不是最终的 L0/L1 快速 Revision 模式。

## 4. 默认深度读器全量测试

默认 `/Game` 全量导出会调用专用 Asset Reader，并通过 `GetAsset()` 加载 Static Mesh、Material、World 等资产。

该测试在取得足够证据后主动终止，结果判定为性能失败样本：

```text
状态                       aborted_performance_failure
采样时长                   188.629 秒
已输出 Canonical            93
峰值 Working Set          9,942,216,704 bytes
峰值 Private Memory      11,129,921,536 bytes
Content 修改文件              0
```

典型异常代价：

```text
资产  /Game/CustomAssets/Arch/SM_ArchWay_01_N1
三角形数                    14,776,032
Nanite Build                  68.24 秒
Static Mesh 总构建            140.20 秒
Nanite Page Data             约 113.3 MB
```

另一个 Mesh 的构建日志估算内存约 10,819 MB。

结论：**专用读器不能作为全项目默认索引路径。** 深度读器必须按资产类型、目录或当前任务选择性执行，并与 Registry Inventory、Revision 和全量审计分层。

## 5. 新增 Registry-only 模式

本轮新增真正的 `-NoAssetReaders`：

- PowerShell 参数会转发到 Commandlet。
- 不调用 `FAssetReaderRegistry::ReadAssetDetails`。
- Canonical 输出 `assetReader="disabled"`。
- Canonical 输出 `assetReaderStatus="disabled"`。
- `assetDetails` 为空对象。
- Manifest 输出 `includeAssetReaders=false`。
- Registry Tags、依赖引用和 Package SHA-256 保持启用。

StarterContent 烟雾测试：

```text
资产                       10/10
失败                           0
Commandlet                  0.20 秒
完整进程                   15.84 秒
```

## 6. 普通资产 Registry-only 全量基线

命令范围：

```text
Root               /Game
NoAssetReaders     true
IncludeGenerated   false
IncludeBlueprints  false
```

结果：

```text
候选普通资产                  1,648
成功                          1,648
失败                              0
跳过 Blueprint                  98
跳过生成 Package            12,018
完整进程                    233.912 秒
Commandlet                  223.75 秒
Package 读取量               25.371 GiB
有效 Package 吞吐           116.11 MiB/s
Canonical JSON               1,648
JSON 输出                 20,577,029 bytes
总输出                    21,399,079 bytes
峰值 Working Set         1,782,411,264 bytes
峰值 Private Memory      1,646,448,640 bytes
Registry Tags                27,156
引用                          14,432
可用 Revision SHA             1,648
Content 修改文件                  0
```

最长 Canonical 路径为 307 个字符，超过 Windows 传统 `MAX_PATH`。

## 7. 包含 External Actor/Object 的全量基线

命令范围：

```text
Root               /Game
NoAssetReaders     true
IncludeGenerated   true
IncludeBlueprints  false
```

结果：

```text
资产                         13,666
成功                         13,666
失败                              0
跳过 Blueprint                  98
完整进程                    209.877 秒
Commandlet                  184.94 秒
Package 读取量               25.431 GiB
有效 Package 吞吐           124.08 MiB/s
Canonical JSON              13,666
JSON 输出                103,498,170 bytes
总输出                   108,691,617 bytes
采样峰值 Working Set     1,765,490,688 bytes
采样峰值 Private Memory  1,615,310,848 bytes
Registry Tags                63,594
引用                          43,863
可用 Revision SHA            13,666
Content 修改文件                  0
```

12,018 个生成 Package 总计只有约 61 MiB，因此没有显著增加 SHA 读取时间；主要增加的是大量小文件打开、Canonical JSON 写入、后续验证和 SQLite 导入成本。

## 8. Windows 长路径缺陷

首次验证全量结果时，`pathlib.rglob()` 可以枚举超过 260 字符的 Canonical JSON，但 `Path.read_text()` 无法重新打开文件。

受影响位置：

- `scripts/ValidateAssetCatalog.py`
- `src/ue_agent_kit/indexer.py`
- `_resolve_recorded_path()` 的 `Path.is_file()` 判断
- BPCTX 对应路径判断

修复方式：

- Windows 下统一转换为 `\\?\` 扩展路径。
- UNC 路径转换为 `\\?\UNC\...`。
- JSON 读取、SHA 计算和 `is_file` 判断使用扩展路径。
- 新增盘符、UNC 和已扩展路径回归测试。

修复后：

```text
Python unittest     337/337 passed
普通全量验证        1,648/1,648 valid
生成全量验证       13,666/13,666 valid
```

## 9. SQLite 建库性能

### 9.1 普通资产索引

```text
资产                           1,648
引用                          14,432
首次建库                      17.869 秒
数据库                    43,487,232 bytes
未变化增量                    10.638 秒
增量结果                       1,648 skipped
失败                               0
WAL/SHM 残留                       0
```

### 9.2 包含生成 Package 的索引

```text
资产                          13,666
引用                          43,863
首次建库                     195.734 秒
数据库                   181,321,728 bytes
未变化增量                   104.281 秒
增量结果                      13,666 skipped
失败                               0
WAL/SHM 残留                       0
```

未变化增量仍会打开、解析并重新 SHA 每个 Canonical JSON。它只避免了 SQLite 删除和重写，没有避免全量文件遍历，因此不满足日常增量目标。

## 10. SQLite 查询延迟

以下为同一进程、同一只读连接内 200 次迭代的 p95；“打开并搜索”包含每次重新打开只读数据库，共 40 次。

| 查询 | 1,648 资产 p95 | 13,666 资产 p95 |
|---|---:|---:|
| Stats | 4.66 ms | 60.26 ms |
| 资产文本搜索 `Beach` | 3.49 ms | 30.56 ms |
| StaticMesh Class 过滤 | 0.45 ms | 0.69 ms |
| Symbol 搜索 `Beach` | 12.22 ms | 90.62 ms |
| 最长路径单资产读取 | 0.41 ms | 0.73 ms |
| 最大目标入向引用，返回 100 条 | 1.86 ms | 70.60 ms |
| 出向引用，返回 100 条 | 31.04 ms | 34.29 ms |
| 深度 2 项目内引用遍历 | 28.90 ms | 652.56 ms |
| 打开数据库并搜索 `Beach` | 7.78 ms | 36.96 ms |

大索引中：

```text
最大引用目标       /Game/Main.Main
入边数量           8,902
深度 2 遍历 p50    572.30 ms
深度 2 遍历 p95    652.56 ms
深度 2 遍历最大    895.91 ms
```

普通搜索、Class 过滤、单资产读取和一次数据库打开均满足日常交互目标。深度引用遍历属于显式重查询，当前仍在 1 秒内，但需要加入边数预算、分页和截断提示。

## 11. 当前结论

### 已通过

- UE5.6 项目级插件可以直接在 DarkRuins 运行。
- Registry-only 模式不会加载或重建 Static Mesh/Nanite。
- 普通资产和 External Actor/Object 全量导出均 0 失败。
- 全量导出没有改写 Content。
- Windows 超长 Canonical 路径可验证、可建库。
- 13,666 资产、43,863 引用的 SQLite 查询仍能维持毫秒级日常响应。

### 当前性能缺口

1. 默认专用读器全量运行会触发 Nanite、距离场和 DDC 构建，必须改为选择性深读。
2. Registry-only 仍计算 25.4 GiB 完整 Package SHA，首次导出约 3–4 分钟。
3. 一资产一个 JSON 在 13,666 资产下产生约 103 MB、13,666 个文件。
4. 13,666 资产首次建库 195.7 秒，未变化增量仍需 104.3 秒。
5. 深度 2 引用遍历在高入度 World 节点上 p95 652.6 ms。
6. 第一次 UE 启动可能夹带 DDC 清理，报告必须区分启动、扫描、SHA、写出和验证阶段。

## 12. 下一步

按优先级执行：

1. 实现 L0 Registry Inventory：不加载资产、不计算完整 SHA。
2. 实现 L1 Fast Revision：优先使用 Package 大小、修改时间和可用 GUID，仅对变化候选计算 SHA。
3. Manifest 写入 Canonical SHA 或稳定快速指纹，使未变化增量无需打开 13,666 个 JSON。
4. 将一资产一 JSON 改为分片 JSONL、批量 SQLite 或两者组合。
5. External Actor 默认按 World 聚合，按需展开单 Actor。
6. 专用读器改为显式资产类型、目录或任务选择，不再默认覆盖整个 `/Game`。
7. 为引用遍历增加最大访问边数、时间预算、分页和 `truncated` 标志。
8. 单独测试 98 个 Blueprint、Memory 数据规模和 `SimulatedHDD50`。
9. 完成上述架构后再扩展到 50 GB、100 GB 和 160–180 GB 物理 Fixture。

## 13. 原始结果位置

```text
E:\WorkSpace\UEAgentKit-Performance\Output\Performance\DarkRuins\
├─ inventory.json
├─ smoke_startercontent\
├─ full_game_default\
├─ smoke_startercontent_no_asset_readers\
├─ full_game_no_asset_readers\
├─ full_game_generated_no_asset_readers\
├─ index\
└─ index_generated\
```

查询基准脚本：

```text
tests\performance\benchmark_index_queries.py
```
