# In-process incremental index hot update — design (2026-11-05)

Status: **not implemented; design + gap analysis only.** This is stage 0 item
0-3.3. It is the remaining piece of "live-first": the write gate no longer needs
a fresh offline index (0-3.1), but reading a *saved* asset's canonical data still
goes through the offline export.

## Problem

`ue_refresh_asset_index` runs `RunExport.ps1` / `RunAssetCatalog.ps1` in a child
`UnrealEditor-Cmd`, then sets `snapshot-refresh-restart-required`
(`workflow_common.py:1251`). That is one cold process and one MCP restart per
refresh, which the user explicitly rejected for the normal loop.

The two constraints that make a naive "just write the SQLite index" wrong:

- the index is opened **immutable/read-only** and must be sidecar-free
  (`agent_api.py:196-205`, `database.py:559-560`); opening it for writes from a
  serving MCP session breaks the quiescent-snapshot contract;
- the bridge and the MCP are separate processes; the bridge cannot write the
  MCP's database safely, and the MCP must not trust a file the Editor wrote
  without proof.

## Proposed design: session-scoped canonical overlay

1. **Bridge emits the record.** After a save that the bridge performed (or a
   user save it observed), the bridge builds the same canonical asset payload the
   offline exporter writes — `FBlueprintContextExporter::BuildContextObject` for
   Blueprints, the equivalent material/DataTable canonical object for other
   asset classes — plus the post-save disk SHA-256 and package dirty=false.
2. **MCP stores it in a session overlay**, never in the immutable index:
   `live_asset_overlay[asset_path] = {revision, canonical, capturedAtUtc,
   editorSessionId, source}`. It is pruned like other session records and
   dropped on snapshot generation change.
3. **Read paths consult the overlay first** for that exact asset: graph context,
   `get_asset`, symbol/reference lookups scoped to one asset. Misses fall back to
   the frozen index exactly as today.
4. **Explicit tool** `ue_refresh_asset_index_live(asset_path)` asks the bridge for
   one asset's canonical record and installs it. It must return
   `indexFresh=false`, `overlayUpdated=true` and must **not** set
   `snapshot-refresh-restart-required`.
5. **Proof requirements** before installing an overlay entry:
   - the bridge reports the asset loaded/saved in the current Editor session;
   - the disk SHA the bridge reports equals the SHA the MCP reads from disk;
   - the asset class matches the index's recorded class (identity drift refuses);
   - otherwise a precise `live-index-refresh-*` code and no overlay write.

## Why an overlay, not a DB rewrite

- Keeps the immutable-snapshot invariants and `index-not-quiescent` semantics.
- Bounded blast radius: a bad record can only affect the one asset it names, and
  it is dropped on restart.
- No schema migration, no cross-process write coordination.

## Known gaps / why this is not implemented yet

- The bridge currently has **no** "canonical record for an arbitrary asset
  class" method; only Blueprint context (`editor.getBlueprintGraphContext`,
  0-3.2) exists. Material/DataTable canonical records would need new bridge
  readers and a defined schema shared with the exporter.
- Installing an overlay means two sources of truth for one asset; every read path
  must be audited to prefer the overlay consistently, or a caller can see index
  data for an asset the overlay owns.
- Revision proof needs the MCP to read the on-disk SHA for the asset's package;
  that mapping (object path → file) must be exact for single-file and
  multi-file (World/ExternalActors) packages.
- The 10-round acceptance needs the overlay to survive **user** saves that the
  bridge did not perform; detecting those requires a save hook or a poll on
  `editor.getDirtyAssets` plus disk SHA, which is a separate risk.

## Minimal next slice

Ship only the Blueprint half: `ue_refresh_asset_index_live(asset_path)` backed by
`editor.getBlueprintGraphContext` + disk SHA, overlay used by the Blueprint
graph-reading tools. Mark `limited` until Material/DataTable canonical readers
exist.

---

# 实施进度（2026-11-12 更新）

状态从「仅设计」改为 **切片 1 已落地，读路径尚未接线**。

## 切片 1 — 已交付：`1956fe3`

`src/ue_agent_kit/live_asset_overlay.py` + `tests/python/test_live_asset_overlay.py`（17 个单元测试）。

| 交付物 | 内容 |
|---|---|
| `package_file_for_object_path()` | 精确 `/Game` Package.Object → `Content/*.uasset`；拒绝 `..`、空段、分隔符、盘符；World Partition 多文件包**拒绝而非猜测** |
| `build_overlay_record()` | 只有同时满足「Bridge 报 available + readOnly + `packageDirty=false`」「资产类与索引一致」「Bridge revision 等于 MCP 自己算的磁盘 SHA-256」才建记录，否则精确报 `live-index-refresh-*` |
| `LiveAssetOverlay` | 有界 FIFO 会话存储；读写均深拷贝；`drop_session()` 丢弃上一个 Editor 会话的记录 |
| 记录语义 | `indexFresh=false`、`overlayUpdated=true`、**`restartRequired=false`** |

**这一刀刻意不改变任何查询结果**：切片 1 只回答「桥刚报的这份数据够不够格当作比冻结索引更新」。

### 为什么证据校验是核心

没有它，overlay 就成了**第二个真相源**，且可能与磁盘静默不一致——那比索引过期更糟，因为下游无法察觉。
所以「桥的 revision 与磁盘 SHA 不符就拒绝」是这条设计的支点，不是可选项。

## 剩余切片（按依赖顺序）

| 切片 | 内容 | 前置 |
|---|---|---|
| 2 | 接一个读路径：`ue_get_asset` 在命中 overlay 时返回 overlay 数据并标注 `source=overlay` | 需审计该工具所有字段的取数处 |
| 3 | 显式工具 `ue_refresh_asset_index_live(asset_path)`（纯 Blueprint，复用 0-3.2 的 `editor.getBlueprintGraphContext`） | 会改动 MCP 工具计数，需同步 registry 与所有计数断言 |
| 4 | 用户保存检测：`editor.getDirtyAssets` + 磁盘 SHA 轮询，或桥侧保存钩子 | 单独风险项，需真实 Editor 验证 |
| 5 | 非蓝图 canonical reader（Material / DataTable）与共享 schema | 需新的桥方法与导出器约定 |

**验收口径不变**：切片 3 完成后，刷新单个资产必须返回 `indexFresh=false`、`overlayUpdated=true`，
且**不得**再产生 `snapshot-refresh-restart-required`。

## 明确不做

- 不把 overlay 写进 SQLite（破坏 immutable/无 sidecar 契约）。
- 不让 overlay 覆盖 bridge-native 写入决策：写入门禁仍然只看 Editor 内存（0-3.1）。
## 切片 3 — 已交付：`bcef415`（先于切片 2）

顺序说明：原计划切片 2（接读路径）在前，但读路径没有数据可读就没有意义，
所以先交付切片 3 让 overlay 能被填充，再做切片 2。

新增工具 **`ue_refresh_asset_index_live(asset_path)`**（`live-action` / `planning` 注解）。

```text
exact /Game Package.Object
  → Content/*.uasset 存在性
  → editor.getBlueprintGraphContext（只取 canonical，不改资产）
  → 本进程计算该 .uasset 的 SHA-256
  → build_overlay_record 证据校验（切片 1）
  → 安装到会话 overlay
```

返回语义固定：

| 字段 | 值 | 含义 |
|---|---|---|
| `indexFresh` | `false` | immutable 索引**未被改动** |
| `overlayUpdated` | `true` | 仅安装了一条会话内记录 |
| `restartRequired` | `false` | 刷新单个资产**永不**要求重启 |

只有 Blueprint 支持（复用 0-3.2 的 `editor.getBlueprintGraphContext`）；
非蓝图资产会被 `live-index-refresh-identity-drift` 拒绝，而不是降级放行。

overlay 是**进程级单例**（一个 MCP 进程服务一个固定工程），
`reset_session_overlay()` 仅供测试使用。

工具计数影响（已同步到测试）：live 54→55、live+memory 68→69、
live+workflow+memory 129→130、live+workflow 115→116。
公开文档 `docs/PROJECT_STATUS.md` 的计数表属于 **v0.8.0 已发布版本**，
不随之修改；该页已注明以运行时 `ue_get_capabilities` 为准。

## 下一步：切片 2

读路径仍完全忽略 overlay。要接的第一个读路径是 `ue_get_asset`：
命中 overlay 时返回 overlay 的 canonical 并标注 `source=overlay`，
未命中继续走冻结索引。接之前必须先审计该工具每个字段的取数处，
避免出现「同一响应里一半来自 overlay、一半来自索引」的混合态。