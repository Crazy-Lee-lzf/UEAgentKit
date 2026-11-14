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
| 4 | ~~用户保存检测~~ **已交付（0-3.3 切片 4）**：读 overlay 时比较磁盘 SHA 与记录 revision，不一致就用同一刷新核心自动重证；被拒则标 stale + 原因码 | 无桥侧轮询/钩子，不需要真实 Editor 常驻 |
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
### 切片 3 真实 Editor 验收（2026-11-12 19:58）

环境：Host `N1_BlueprintNative_20261031`，插件 `0.9.0.dev1`，DLL `712BD75E…`，
Editor PID 5632，**无索引库、无 `--revision-export`**（正好叠加验证 0-3.6）。

| 检查 | 结果 |
|---|---|
| 工具已注册 | `ue_refresh_asset_index_live` 在列，工具总数 116 |
| 磁盘 SHA（独立计算） | `5252e1103c851d93…` |
| 返回 `revision` | `sha256:5252e1103c851d93…` **与磁盘一致** |
| `indexFresh` | `false` |
| `overlayUpdated` | `true` |
| `restartRequired` | `false` |
| `overlayEntryCount` | `1` |
| 验收后磁盘 SHA | `5252E1103C851D93…` **未变**（刷新是纯只读） |
| 收尾 | Editor 无提示退出，0 个 UE 进程 |

这条验收同时证明了 0-3.6：整条链路在**没有索引、没有 Revision Export** 的机器上成立。
## 切片 2 — 已交付：`4040d87`（真实 Editor 端到端验收）

`ue_get_asset` 现在会报告已证明的 overlay，且**绝不把它混进索引字段**。

### 真实 Editor 验收（2026-11-12 20:05）

环境：Host `N1_BlueprintNative_20261031`，Editor PID 3276，DLL `712BD75E…`，
**无索引库、无 `--revision-export`**。四步都在同一个 MCP 会话内完成：

| 步骤 | 调用 | 结果 |
|---|---|---|
| A | `ue_get_asset`（无 overlay，无索引） | `ok=false`，`database-not-found` —— 行为与改动前一致 |
| B | `ue_refresh_asset_index_live` | `ok=true`，`overlayEntryCount=1`，`restartRequired=false` |
| C | `ue_get_asset(sections=["overlay"])` | `ok=true`、`source=overlay`、`indexFresh=false`、`revision=sha256:5252e1103c851d93…`、含 canonical |
| D | `ue_get_asset`（无 sections，无索引但有 overlay） | `ok=true`、`source=overlay`、`degradedReason=index-unavailable: FileNotFoundError` |

结论：**在完全没有索引的机器上，用一次刷新就能让 `ue_get_asset` 返回可证明的最新数据**，
且来源在响应里写明（`source` / `indexFresh` / `degradedReason`），调用方不会误判。

验收后磁盘 SHA 未变，Editor 无提示退出，0 个 UE 进程。

### 读取契约

```text
无 overlay 记录              → 响应与改动前逐字节一致（不新增键）
有 overlay，sections=None    → source=index+overlay，附 overlay 元数据（不含 canonical）
sections=["overlay"]         → 只读 overlay，不调用索引
sections=["overlay","symbols"] → 索引按 ["symbols"] 查询，附带 canonical
索引不可用 + 有 overlay      → 仅 overlay 应答，带 degradedReason
索引不可用 + 无 overlay      → 原错误不变
```

canonical 只在显式请求 overlay 段时附带，默认响应不会因整份图导出而膨胀，输出预算不受影响。

## 切片 4 — 已交付：用户保存检测（0-3.3）

用户 Ctrl+S 改的是磁盘，overlay 记录不会自己更新。读路径现在每次都做一次轻量核对：

```text
sha256(包文件) == record.revision ?
  是 → 直接用记录（不调用 Bridge，缓存价值保留）
  否 → 复用 prove_and_install()（与显式工具同一份核心）重证
         成功 → 用新记录应答：overlayCause="disk-changed"，overlayRefreshed=true
         被拒 → 保留旧记录但 overlayStale=true，staleCause=<精确错误码>，且不再返回 canonical
```

`staleCause` 除 `live-index-refresh-*` 外还有两个文件层错误：`package-file-missing`、`package-file-unreadable`。
无论哪条路径都不抛异常；过期记录**绝不**被当成当前磁盘状态返回（stale 时不输出 canonical）。

overlay 段新增四个只增不改的字段：`overlayCause`（`installed` / `explicit` / `disk-changed`）、
`overlayRefreshed`、`overlayStale`、`staleCause`。`ue_refresh_asset_index_live` 的响应也带
`overlayCause="explicit"`，其余字段与错误码不变。

---

## 资产基线变更（2026-11-14，必须知道）

本节及上方各验收表里引用的 `5252E1103C85…`（`BP_N1EditSession.uasset`，526,517 字节）**已经不再是磁盘现状**。

**发生了什么**：2026-11-14 补 MCP 端到端时，该资产被多次真实 apply + save；中途用于还原的备份
（`.bak-e2e`）在验证尚未结束时被删除，之后的写入无法回滚。清理阶段还误删了 1 个**原有**节点
（按坐标筛探针时命中 5 个，实际只造了 4 个），随后按等价方式补回。

**现状（实测）**：

| 项 | 值 |
|---|---|
| SHA-256 | `73A67086CBEE94F5…`（525,860 字节） |
| nodeCount | **259**（与原始一致） |
| 类分布 | 256 `K2Node_CallFunction` + 3 `K2Node_Event`（与原始一致） |
| 探针节点残留 | 无 |
| 内容差异 | 1 个 `int_add` 节点是重建的（位置/类/函数相同，**GUID 不同**） |

**怎么用**：

- **不要**再拿 `5252E110…` 作为"资产未变"的判据；
- 需要"未变"证据时，先取当前 SHA 作为本次会话的基线；
- 上方表格是历史记录，反映的是当时的真实状态，**不要改写**。
