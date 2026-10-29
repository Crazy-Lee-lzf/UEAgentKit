# Asset Index Schema v2：CJK 子串检索与重建迁移

本文描述 **Asset Index（资产/符号/节点 SQLite 索引）** 的 schema v2。它与 Project Memory schema v7 是两套独立的版本计数器，互不迁移、互不共用数据库。

- `CURRENT_SCHEMA_VERSION = 2`
- `INDEX_REBUILD_REQUIRED_FROM = 2`
- Project Memory 仍为 `CURRENT_MEMORY_SCHEMA_VERSION = 7`，本变更不修改 `memory_schema.py`。

## 1. v2 增加了什么

v2 为 `symbols` 和 `nodes` 各增加一个真实的 `search_text TEXT NOT NULL DEFAULT ''` 列，并把 `symbols_fts`、`nodes_fts` 两个 external-content FTS5 表扩展为 7 列：

| 表 | FTS 列 |
| --- | --- |
| `symbols_fts` | `stable_id, name, kind, symbol_asset_path, class_path, details_json, search_text` |
| `nodes_fts` | `guid, title, object_name, node_class, comment, details_json, search_text` |
| `assets_fts` | 保持 v1 原样，不增加列 |

`symbols` / `nodes` 的 INSERT / DELETE / UPDATE 触发器同步重写：UPDATE 的 `delete` 载荷必须带上 `old.search_text`，否则 external-content FTS5 可能报 `database disk image is malformed`。`assets_*` 触发器与 `assets_fts` 完全保留 v1 行为。

### 1.1 `search_text` 的存储契约

`search_text` **不是全文副本**，只保存投影后的 Han / Kana / Hangul 字符，每个字符之间一个 ASCII 空格：

```text
name = "AK47"
details note = "护甲升级链共三级"
search_text = "护 甲 升 级 链 共 三 级"
```

不复制 ASCII 是 v2 的核心成本约束：在一个数 GB 的资产索引上复制全部 ASCII 文本会额外占用数百 MB。原始列（`name`、`title`、`comment`、`details_json` 等）仍保存完整文本并继续参与 FTS，因此 ASCII 查询行为不变。

当同一字段内两段 CJK 之间隔着标点、空白或 JSON 结构时，投影会插入一个不可见的 **Hangul Filler（U+3164）** 作为 run 边界：

```text
"护甲，升级链"  ->  "护 甲 <U+3164> 升 级 链"
```

U+3164 本身属于 Hangul 字符，因此 `search_text` 仍然满足“只含 Han/Kana/Hangul 字符 + 空格”的契约；同时 unicode61 会把它索引成独立 token，FTS5 phrase 不会跨过它。这样 `甲升` 不会错误命中被逗号分开的 `护甲，升级链`，`甲护` 也不会把“字段 A 末尾的甲 + 字段 B 开头的护”拼成一个短语。

### 1.2 查询语义

`queries.py` 中：

- `_query_runs()` 先做 NFKC 归一化并按空白切分查询；
- `_fts_phrase()` 对纯 ASCII 查询保持旧的整句 quoted phrase（`"weapon socket"`），对含 CJK 的查询按 run 生成 `"护 甲"` 形式；
- 混合查询 `AK47护甲` 生成 `"AK47" AND "护 甲"`，ASCII 部分命中原始列，CJK 部分命中 `search_text`；
- 全角 `ＡＫ４７护甲` 先折叠为 `AK47护甲`，行为一致；
- `assets_fts` 不参与该扩展，资产搜索保持 v1 行为（`search_assets` 使用旧的 literal phrase）。

由此得到：

| 查询 | 结果 |
| --- | --- |
| `护甲` | 命中 `护甲升级链` 内部 |
| `甲护` | 不命中 `护甲升级链` |
| `AK47护甲` | 同时要求 `AK47` 与 `护 甲`，可跨原始列/投影列 |
| `强化` | 命中日文 `武器強化計画` |
| `방어구` | 命中韩文 `방어구 강화` |
| `inventory` | ASCII 行为与 v1 相同 |

已知限制：FTS5 的 phrase 位置是 **按列** 计算的，因此一个 phrase 不会跨 `name` 与 `search_text` 两列拼接；这也是混合查询必须使用 `AND` 而不是单一 phrase 的原因。该行为已由测试固定。

## 2. 为什么 v1 必须整库重建

v2 修改两个 external-content FTS5 表的定义。对已有 v1 索引做原地 `ALTER TABLE` / `DROP TABLE ..._fts` / `rebuild`，意味着：

- 在数 GB 数据库上重写全文索引，耗时和 WAL 体积不可控；
- 中途失败会留下 v1 基础表 + v2 FTS（或反向）的半迁移状态，`quick_check` 之外很难证明一致性；
- external-content FTS5 的 delete 载荷与旧索引不匹配时，SQLite 可能直接报告 malformed database。

因此本实现 **没有任何 v1 → v2 的原地迁移 SQL**：

- `MIGRATIONS` 中保留 v1 历史 DDL 仅用于测试构造真实 v1 数据库和文档追溯；
- 新建数据库只应用最后一份完整 schema（v2），不会先建 v1 再改 FTS；
- 已存在的 v1 数据库在打开前就会被只读预检拒绝，不会进入可写连接，因此不会改变 journal mode、不会写 WAL、不会改 `user_version`。

### 2.1 拒绝规则

`database.py` 在 `open_database(..., migrate=True)` 和 `apply_migrations()` 中执行同一套 fail-closed 检查：

| 现有数据库状态 | 行为 |
| --- | --- |
| 文件不存在 / 0 字节 | 允许，创建全新 v2 |
| `user_version = 1`（无论是否有数据） | 拒绝，要求整库重建 |
| `user_version = 0` 但已有索引表 | 拒绝，视为不可识别的半成品 |
| `user_version = 2` 且结构完整 | 允许打开/查询 |
| `user_version = 2` 但缺列/缺触发器 | 拒绝，不原地修复 |
| `user_version > 2`（例如未来 v3） | 拒绝，“newer than supported” |

即使 v1 文件是空的，也会被拒绝：工具无法可靠区分“刚生成但未写入的 v1 文件”和“生产索引”，因此不做猜测。删除空 v1 文件后重新构建是安全的。

典型错误信息：

```text
Index database schema 1 requires a full rebuild to schema 2
(INDEX_REBUILD_REQUIRED_FROM=2). In-place schema or FTS migration of a pre-v2
index is not supported ... The existing database file was not modified.
```

## 3. 迁移 / 重建操作步骤

### 3.1 前置检查

1. 停止所有 MCP server、CLI 写入进程和索引任务。
2. 确认目标数据库没有活动的 `-wal` / `-journal` 旁文件。重建助手会拒绝带活动 WAL 的目标。
3. 为目标数据库留出至少与旧库相当的空闲磁盘（新旧文件会短暂并存）。
4. 保留旧数据库的只读备份。工具不会自动删除或改写备份。

### 3.2 使用 `--rebuild` 原子重建

```text
ue-agent index build <export-root> --database <index-path> --rebuild
```

流程：

1. 读取目标版本；v1、v2 可重建，v3+ 或不可识别数据库直接拒绝；
2. 在同目录创建 `.<name>.v2.rebuild.tmp` 全新数据库；
3. 应用完整 v2 schema，执行正常 `build_index` 导入；
4. 校验：
   - `PRAGMA quick_check` 必须为 `ok`；
   - `PRAGMA foreign_key_check` 必须为空；
   - 精确 schema 断言（列、触发器、迁移标记）；
   - 三张 FTS 表的 `integrity-check`；
   - 真实搜索探针：从投影中取一个 CJK 字符，要求对应 FTS 表能命中；
5. 通过后 checkpoint 并退出 WAL 模式，用 `os.replace` 在同一文件系统内原子替换目标；
6. 任一步失败：删除暂存文件，目标文件保持逐字节不变。

重建完成后：

```text
ue-agent index stats --database <index-path>
ue-agent search symbols 护甲 --database <index-path>
```

C++ 代码知识是独立剖面，需要在 v2 库上重新建立（不需要 UE Editor）：

```text
ue-agent index code <project-root> --database <index-path>
```

如使用 Unreal reflection 合并，再执行对应的 `index reflection`。这些步骤只读源码/导出文件，不要求 Editor 在线。

### 3.3 新建索引

新路径直接构建即可，自动得到 v2：

```text
ue-agent index build <export-root> --database <new-index-path>
```

### 3.4 为什么不自动迁移

如果工具在打开旧库时“顺手”迁移，一次普通的只读查询就可能触发数 GB 的 FTS 重写；这正是 v2 明确禁止的行为。`--rebuild` 把破坏性操作变成一个显式、可审计、可回滚的命令。

## 4. 与 Project Memory v7 的关系

| | Asset Index | Project Memory |
| --- | --- | --- |
| 版本常量 | `CURRENT_SCHEMA_VERSION = 2` | `CURRENT_MEMORY_SCHEMA_VERSION = 7` |
| 版本落点 | 资产库 `PRAGMA user_version` | Memory 库 `PRAGMA user_version` |
| 迁移入口 | `database.open_database` / `apply_migrations` | `project_memory.open_project_memory_database` |
| 迁移方式 | v1 拒绝，整库重建 | v6 → v7 原地迁移（沿用既有实现） |
| `search_text` 内容 | 仅 CJK 投影（控制体积） | 完整投影（含 ASCII，Memory 既有契约） |

两者不可混用：不要对资产库调用 Memory 迁移，也不要把资产库的 v2 当成 Memory 的版本。资产库打开时不会创建 `memory_*` 表。

## 5. 兼容性、回滚与限制

- **回滚**：保留旧库备份即可。v2 写入的库不能被旧版本二进制打开（旧版本会把它当作 newer schema 拒绝），因此回滚需要恢复备份文件。
- **不支持 v1 原地查询**：CLI 查询入口和 Agent API 会校验 schema 版本与 v2 形状；低层 `open_database(..., readonly=True, migrate=False)` 保留给显式只读检查工具，不作为面向用户的查询入口。
- **排名**：新增 FTS 列会改变 BM25 的语料统计，CJK 结果的绝对分数不保证与旧版本相同；相同相关性下仍按资产路径、kind、name 稳定排序。
- **资产搜索**：`assets_fts` 不增加 CJK 子串展开，`search_assets("护甲")` 仍按 v1 的整 token 语义工作。这是刻意的范围控制。
- **重建耗时**：与导出目录中的资产数量和详情大小成正比；Python 测试只使用小型临时索引，不在数 GB 生产库上跑基准。

## 6. 验证清单

发布前至少验证：

- 新库 `PRAGMA user_version = 2`，`schema_migrations` 只有版本 2；
- `symbols` / `nodes` 存在 `search_text`，`symbols_fts` / `nodes_fts` 为 7 列且触发器包含 `search_text`；
- `assets_fts` 与 `assets_*` 触发器保持旧形状；
- `护甲` 命中 `护甲升级链`，`甲护` 不命中；
- `AK47护甲` 跨原始列与投影列命中；
- 日文、韩文、全角查询；
- 真实 v1 文件在拒绝后 SHA-256 不变，且不产生 `-wal` / `-shm`；
- `--rebuild` 成功后 `quick_check = ok`，失败时目标逐字节不变；
- C++ 源码 `search symbols` 在无 Editor 的纯 Python 环境中工作。
