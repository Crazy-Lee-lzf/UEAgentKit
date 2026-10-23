# R7 Memory Mount / Knowledge Pack — 只读 PoC 报告

- 日期：2026-10-23（Asia/Hong_Kong）
- 模型：DSH 默认 `cline-pass/deepseek-v4.1-flash-1017`（非 Fast）；开工前已核对，未切换模型
- Worktree：`E:\WorkSpace\UEAgentKit-WT\memory-mount-r7`
- Branch：`feature/memory-mount-r7`
- 基线：`main` `c5ce3419c6962da49e874586f4e5dbd1457a4d0d`（开工时工作树 clean）
- 结论：**R7 第一段只读合同与隔离 PoC 完成**。49/49 新增测试通过，90/90 既有 Memory 回归通过；
  未接入生产服务、未写真实 Memory、未改 schema v6、未 push。R7 写入/隐私边界仍待用户决定。
- 2026-10-24 追加：只读 integration readiness / trust hardening（身份注入、manifest receipt、
  adapter、负例）见 [R7_READONLY_INTEGRATION_READINESS.md](R7_READONLY_INTEGRATION_READINESS.md)；
  追加 27 tests，合计 76 个 R7 测试 + 90 回归。

> 本报告不声称生产可用。PoC 仅用于验证合同、权限、只读索引和中文检索边界；
> 自动维护、联网同步、跨人共享写入、生产 Memory 写入均未实现。

## 1. 执行边界核验

| 项 | 实测 |
| --- | --- |
| 模型 | `cline-pass/deepseek-v4.1-flash-1017`（DSH 默认，非 Fast） |
| Python | 3.12.10 |
| SQLite | 3.49.1（FTS5 trigram 可用） |
| 基线 HEAD | `c5ce3419c6962da49e874586f4e5dbd1457a4d0d` |
| 开工 `git status` | clean |
| UE Editor / BuildPlugin / UE 编译 | 未启动 |
| Computer Use / 键鼠注入 / 截图驱动 | 未使用 |
| A 线 worktree / 主仓 | 未修改 |
| push / merge / reset / clean / 删除工程 | 未执行 |
| 生产 Memory DB | 未打开、未写；测试仅用临时目录和自建 fixture |
| 协调库备份 / Session 白名单 | 未修改、未解除 |

## 2. 交付物

### 2.1 合同与 Schema

| 文件 | Bytes | SHA-256 |
| --- | ---: | --- |
| `spec/memory-mount/R7_MOUNT_CONTRACT.md` | 28,777 | `2A22B53E5EE0454691E18572E0E882CAF2ACE962541A4E82F8467C17F764802F` |
| `spec/memory-mount/r7-pack-manifest.schema.json` | 4,083 | `0DE51ADB07A835FE9759CFCDF46844672E28C84BFA65A8CB5CDD1E63E293983C` |
| `spec/memory-mount/r7-pack-record.schema.json` | 2,168 | `23AB6447D04BBAA2969043492DE472D3F8241032BD819278A98158438CF7CEF9` |

### 2.2 隔离 PoC 模块（未被生产导入）

| 文件 | Bytes | SHA-256 |
| --- | ---: | --- |
| `src/ue_agent_kit/memory_mount/__init__.py` | 635 | `71C77310186C1B956CD87D0F9F23903BC6DA79FE5BC7C0DCF90C2ABAF8E2638B` |
| `src/ue_agent_kit/memory_mount/__main__.py` | 85 | `9573278A10FD58F03B8D2480311533FB9D8CEEC8E0EC13CE414B811D27C78C04` |
| `src/ue_agent_kit/memory_mount/cli.py` | 4,827 | `C661EBA2B622A381798FEFCCECF2CF45AC5DA37C5F5B959152B5D97E20D1CF2E` |
| `src/ue_agent_kit/memory_mount/errors.py` | 3,045 | `61B907E53F8C147E2273D203FB689CA2FEF6AB788A250B7378FFF46EBC8FC7C5` |
| `src/ue_agent_kit/memory_mount/index.py` | 18,373 | `17CA6D8355A4922BE4997D20242FBC2C3E32AFFEB9937BDC80173C9EEC9A3E41` |
| `src/ue_agent_kit/memory_mount/jsonutil.py` | 1,355 | `98BFE82C92368DEB2D8630CC5E9422EF7160C64D71805DEBD62F96CDF8731A81` |
| `src/ue_agent_kit/memory_mount/limits.py` | 2,832 | `4531C90497CBF0F7EF57123ECF29BC24630D7903ACF17CFCF4FA8215309336A2` |
| `src/ue_agent_kit/memory_mount/manifest.py` | 14,790 | `0A22F2DDA34DCD48BBF568B0DC33A49904E17C94679A424A22813EF01AB8156E` |
| `src/ue_agent_kit/memory_mount/records.py` | 17,552 | `50350FF273237CEF870405385D67B21F1A39929675F0521B83CE67D9EC94D9B4` |
| `src/ue_agent_kit/memory_mount/service.py` | 21,297 | `D2E394754DD39846EA7F474200F19125DA70E52942FACA6242C210D0FE3104F3` |

### 2.3 测试

| 文件 | Bytes | SHA-256 |
| --- | ---: | --- |
| `tests/python/test_memory_mount.py` | 34,198 | `4F880B2E012E276D4C919D63348856A284409FF58FB0F8B12B55FBDB892419F8` |

> 注：以上为 2026-10-23 第一段提交时的哈希。2026-10-24 readiness 修改了
> `service.py`、`records.py`、`cli.py`、`errors.py`、`__init__.py`、合同和
> `test_memory_mount.py`，最新文件
> 清单与新增文件见 [R7_READONLY_INTEGRATION_READINESS.md](R7_READONLY_INTEGRATION_READINESS.md)
> §9；以 Git commit 内容为准。

隔离证据（`Build/` 被 `.gitignore` 忽略，不进入提交）：

```text
Build/r7-isolated/Evidence/
├── build_demo.py
├── unittest-memory-mount.log
├── unittest-memory-regression.log
├── demo-packs/r7.demo.project/{pack.json,records.jsonl}
├── demo-packs/r7.demo.shared/{pack.json,records.jsonl}
├── demo-output.json
├── demo-summary.txt
├── verify-project.json / .err
├── query-shared.json / .err
├── query-project.json / .err
└── cache/, cli-cache/
```

## 3. 合同摘要

### 3.1 Manifest

`pack.json` 必填：`schemaVersion=1`、`packId`、`contentHash`、`revision`、`provenance`、
`priority`、`scope`、`compatibility`、`recordCount`。未知字段拒绝，不做“尽量兼容”。

- `contentHash` 是 `records.jsonl` **精确字节**的 SHA-256（`sha256:<64 lowercase hex>`）。
- `recordCount=0` 时 `records.jsonl` 必须为零字节；否则必须单 LF 结尾。
- `revision` 是生产者标签，不用于跨包排序。
- `provenance` 自述，不是信任证明；`signature` 在 v1 必须为空，非空直接
  `r7-signature-unsupported`。
- `scope.kind` 为 `personal` / `project` / `shared`；`visibility` 必须与 `kind` 一致。
- `compatibility` 固定 reader v1、trigram tokenizer、可选 `memorySchema=6`；未知
  `requiredFeatures` 在建立索引前失败。

**已明确写进合同的限制**：`contentHash` 只保护 records 字节，不保护 manifest 的
`priority/scope/provenance`；未来签名必须覆盖 canonical manifest + contentHash。

### 3.2 Mount / unmount / list / query

- 一个 registry 绑定一个 `principal` 和一个 `projectKey`；mount/query 不接受覆盖参数。
- 授权在 manifest 解析后、records 读取前执行：未授权者不能触发个人/项目包内容解析。
- 同 `packId` + 同 hash + 同路径：幂等返回 `alreadyMounted=true`，不建第二份索引。
- 同 `packId` + 同 hash + 不同路径：`r7-pack-already-mounted`。
- 同 `packId` + 不同 hash：`r7-pack-revision-conflict`；v1 不支持多 revision 并存。
- `unmount` 只关闭本会话索引并移除挂载，不删除源包；索引缓存可留可删，永远不是权威。
- `listMounts` 只返回元数据，不返回本地路径和正文；挂载状态不持久化、不跨进程。
- 查询无挂载时返回空结果 + `r7-no-mounted-packs` 警告，不是错误。

### 3.3 来源与优先级

| Tier | Scope | Rank | 含义 |
| --- | --- | ---: | --- |
| 0 | shared | 0 | 通用共享知识，最低 |
| 1 | personal | 1 | 个人知识，不能静默覆盖项目规则 |
| 2 | project | 2 | 项目事实/规则，默认最高 |

同 tier 内：更高 `priority` → 更强 status（valid > unverified > stale）→ 更新的
`updatedAtUtc` → 字典序 `packId` → 字典序 `recordId`。结果返回 `rank` 输入，便于审查。

**这是本轮只读默认策略，不是永久产品决策**：项目规则默认压过个人偏好；若用户希望
个人 pack 覆盖非安全类项目知识，需要 §8 的明确授权。

### 3.4 去重与冲突

- 完全重复：同 `(recordType, subjectKey)` + 同 record content hash → 只返回一个 winner，
  不产生 conflict 标记。
- 逻辑冲突：同 `(recordType, subjectKey)` + 不同 content hash → `state=unresolved`，
  winner 带 conflict 元数据，绝不自动改写、删除、promote 或标记 superseded。
- `includeConflicts=true` 返回所有不同内容代表，`selected=true/false`；仍受 `limit` 和
  输出上限约束。
- `alternativePackIds` 只列提供非 winning 内容的包，不含 winner。

### 3.5 隐私与权限

- personal：仅 `owner == principal` 可挂载/查询。
- project：仅 `projectKey` 精确匹配可挂载/查询。
- shared：任何非空 principal 可读；不得含个人数据（生产者义务，非 DLP 强制）。
- 记录自身的 `scopes` 只是知识绑定，不授予可见性；`priority` 和 provenance 也不能提权。
- 不使用 Session 白名单、ACL 文件或全局配置；空 principal 在构造时即拒绝。

### 3.6 中文检索

- Pack 索引使用 FTS5 `trigram`；查询按**字面短语**处理，不暴露 FTS5 的 `AND/OR/NEAR/*`。
- ≥3 个 Unicode code point 走 trigram；1–2 个字符走有界 `instr()` 扫描；≤16 字符且
  FTS 零命中时可做同样的有界补充。`matchReason` 明确标注。
- 2 字符查询在包记录数超过 `max_short_query_scan_records` 时返回 `r7-query-too-broad`，
  不做无界扫描。
- 这是**字面子串检索**，不是分词、embedding 或语义检索；合同和报告都不这样声称。
- 未修改 schema v6 的 `unicode61` FTS 或 P1.1 的中文 substring fallback。

### 3.7 Index version

| 项 | 值 |
| --- | --- |
| index format | `r7-pack-index-v1` |
| pack index `user_version` | `1`，独立于 Memory schema v6 |
| tokenizer | FTS5 `trigram` |
| 表 | `pack_index_meta`, `pack_records`, `pack_scopes`, `pack_records_fts` |
| FTS 绑定 | external content over `pack_records` |
| cache key | `<cacheDir>/<packId>-<contentHash[7:19]>.sqlite3` |
| 打开方式 | `mode=ro&immutable=1` + `PRAGMA query_only=ON`；不用 WAL |
| 复用条件 | meta 匹配 + 每条记录 content hash/identity/status/confidence/time 重新校验 |
| 不兼容/损坏 | 从已验证 pack 重建；重建失败则 mount fail-closed |
| 缓存目录 | 禁止位于 pack root 内 |

缓存目录**不是密码学信任边界**：能写缓存和 FTS shadow tables 的本地攻击者仍可能污染
匹配数据；生产接入必须使用用户私有目录、每会话重建或签名缓存摘要。合同已写明这一限制。

### 3.8 资源上限与错误码

主要默认值：manifest 256 KiB、records 16 MiB、单包 5,000 条、单会话 8 个 pack、
总挂载 20,000 条、body 32,768 字符、title 512、subject 256、scopes 32、details 16 KiB、
query 128 字符、limit 50、candidate cap 200、timeout 250 ms（max 2,000）、
输出 65,536 字符、body preview 600（max 4,000）、短查询扫描 20,000 条。

错误码按类别稳定返回：contract/usage（exit 2）、not-found（3）、permission（4）、
conflict（5）、resource/timeout（6）、integrity（7）、internal/io（1）。完整表在合同 §10。
超时和完整性失败均 fail-closed，不返回“部分成功”。

## 4. PoC 实现要点

- API：`MemoryMountRegistry.mount / unmount / list_mounts / query / close`；
  `load_pack` 验证 manifest、hash、canonical records；`PackIndex` 负责派生索引；
  `python -m ue_agent_kit.memory_mount verify|query` 提供离线 CLI。
- 生产隔离：测试用 AST 检查 `memory_mount/*.py` 不导入任何 `ue_agent_kit.*` 绝对模块，
  并检查 `memory_service.py`、`mcp_server.py`、`cli.py`、`memory_context.py` 不含
  `memory_mount`。模块未被默认服务接线。
- 只读：源包只读；索引以 `mode=ro&immutable=1` 打开并设置 `query_only`；查询路径没有
  pack 数据写语句。测试直接尝试 `DELETE FROM pack_records`，SQLite 拒绝。
- 索引防篡改：复用前校验 meta + 每条记录 content hash + pack/record identity、status、
  confidence、timestamp；测试分别篡改 meta 和 body，均触发重建并恢复原始内容。
- 权限先于内容解析：授权回调在 manifest 解析后、records 读取前执行；测试用“未授权 +
  被篡改 records”验证返回 `r7-permission-denied` 而不是 hash 错误。
- 默认测试数据库：`tempfile.TemporaryDirectory()`；证据库：`Build/r7-isolated/Evidence`。
  任何测试/演示都不引用 `coordination_memory.sqlite3`。

## 5. 测试结果

### 5.1 新增 R7 测试

命令：

```powershell
py -3.12 -m unittest discover -s tests/python -p "test_memory_mount.py" -v
```

实测：**Ran 49 tests — OK（1.321 s）**，0 failure / 0 error / 0 skip。

| 测试类 | 数量 | 覆盖 |
| --- | ---: | --- |
| `ManifestValidationTests` | 8 | JSON Schema、未知字段、schemaVersion、签名、tokenizer、feature、scope 必填 |
| `PackIntegrityTests` | 10 | hash、canonical、重复 ID、recordCount、空包、路径/符号链接/普通文件 |
| `MountPermissionTests` | 10 | shared/personal/project、空 principal、幂等、revision conflict、unmount、mount limit、cache 位置、授权先于解析 |
| `QueryTests` | 14 | 中文 trigram、短中文 fallback、ASCII、过滤、精确去重、冲突优先级、priority tie-break、preview、limit/output cap、timeout、too-broad、output too large、非法参数 |
| `IndexIsolationTests` | 6 | 只读、index v1 vs schema v6、复用/篡改重建、生产未接线、无生产 DB 引用 |
| `CliTests` | 1 | `verify` + `query` 真实 CLI |

### 5.2 既有 Memory 回归

未修改既有 production 文件；仍运行了 8 个相关测试文件，**90/90 PASS**：

| 文件 | 结果 |
| --- | --- |
| `test_project_memory.py` | 17 PASS |
| `test_memory_service.py` | 8 PASS |
| `test_memory_context.py` | 13 PASS |
| `test_memory_cli.py` | 12 PASS |
| `test_memory_injection.py` | 17 PASS |
| `test_memory_overhead.py` | 4 PASS |
| `test_memory_l0.py` | 14 PASS |
| `test_memory_tree.py` | 5 PASS |

合计：**139 项真实测试全部通过**（49 新增 + 90 回归），无 skip。

## 6. 证据运行

### 6.1 隔离 demo

```powershell
py -3.12 Build\r7-isolated\Evidence\build_demo.py
```

实测摘要（`demo-summary.txt`）：

```text
cjk_items=1
cjk_mode=fts5-trigram
cjk_title=共享契约审核与知识包
conflict_items=2
conflict_winner=r7.demo.project
conflict_group=sha256:b23e85ca3ef4dcb5
selected=[True, False]
pack_files_unchanged=True
index_reused=[True, True]
```

- 中文 `契约审核` 命中共享包的 `共享契约审核与知识包`，`matchReason=fts5-trigram`。
- 项目包与共享包对同一 `(projectRule, project.rule.r7.read-only)` 给出不同正文；
  默认 winner 为 project tier，共享包作为 alternative；`includeConflicts=true` 返回两条，
  项目 winner `selected=true`，共享 `selected=false`。
- 运行前后 pack 文件 SHA-256 完全一致（`packFilesUnchanged=true`）；第二次 mount 复用索引
  （`indexReused=[true,true]`）。
- `demo-output.json` 只含元数据、标题、短 preview，不含 `details` 和本地绝对路径。

### 6.2 真实 CLI

```powershell
$env:PYTHONPATH="src"
py -3.12 -m ue_agent_kit.memory_mount verify Build\r7-isolated\Evidence\demo-packs\r7.demo.project
py -3.12 -m ue_agent_kit.memory_mount query Build\r7-isolated\Evidence\demo-packs\r7.demo.shared `
  --query 契约审核 --principal alice --project-key UEAgentKit `
  --cache-dir Build\r7-isolated\Evidence\cli-cache
```

实测退出码均为 0；`verify-project.json` 报 `verified=true`、`recordCount=2`，并包含
`integrity.receiptVerified=true`；`query-shared.json` 命中 1 条中文记录，
`retrievalMode=fts5-trigram`。stderr 现在**显式**包含
`warning: r7-untrusted-demo-identity`，说明 CLI 的 `--principal` 不是鉴权。

### 6.3 生产 Memory 库证据

| 项 | 值 |
| --- | --- |
| 路径 | `E:\WorkSpace\UEAgentKit-Development\state\coordination_memory.sqlite3` |
| 开工 SHA-256 | `50A44D5959128C4C1C4DE5257CEC23B3AFCF24C170C0897D006B6F9928333789` |
| 结束 SHA-256 | `50A44D5959128C4C1C4DE5257CEC23B3AFCF24C170C0897D006B6F9928333789` |
| 大小 / mtime | 1,007,616 bytes / `2026-10-23T15:36:24Z` |
| `-wal` / `-shm` / `-journal` | 0 / 0 / 0 |
| 本轮写入 / migration | 0 / 0 |

未对真实库执行任何 SQLite 打开；仅用文件 SHA-256 做只读证据。

### 6.4 Schema v6

- `CURRENT_MEMORY_SCHEMA_VERSION` 仍为 `6`，migrations 仍为 1..6。
- 新测试显式断言 pack index `user_version=1` 且不等于 6。
- 未修改 `src/ue_agent_kit/memory_schema.py`、`project_memory.py`、`memory_service.py`、
  `memory_context.py` 或任何 production 文件。

## 7. Diff、文件与 commit

`git status` 仅显示 3 个新目录/文件，无既有 tracked 文件修改：

```text
?? spec/memory-mount/
?? src/ue_agent_kit/memory_mount/
?? tests/python/test_memory_mount.py
```

- 本地 scoped commit：`0d0ebc9ebe8587595bec7338f566df345206e63f`
- 未 push、未 merge、未 reset、未 clean。
- `Build/r7-isolated/Evidence` 被 `.gitignore` 的 `Build/` 忽略，不进入提交。
- 未创建/修改 A 线 worktree、主仓、coordination DB 或其备份。

## 8. R7 后续需要用户决定的写入/隐私边界

以下均**未实现**，合同 §14 有同样列表：

1. **信任模型**：pack 是否签名？哪些 key/author 可信？还是只做人工 review 的本地目录？
2. **Principal 身份**：`scope.owner` 用什么稳定身份（OS user / DSH session / agent id）？
   如何证明而不是自述？
3. **优先级策略**：是否永久保持 `project > personal > shared`？个人 pack 是否可覆盖
   非安全类项目知识？谁批准例外？
4. **共享分发**：只允许本地目录，还是允许 reviewed repo/registry？谁可发布、如何撤销？
5. **写入/promotion**：mounted record 是否可 promote 到 Project Memory？幂等键、状态迁移、
   用户确认流程是什么？
6. **PII/redaction**：shared pack 允许含什么？发布前是否强制扫描/脱敏？
7. **保留与审计**：index cache 和 query log 保留多久？是否记录 query 文本/正文？
   （当前 PoC 两者都不记录。）
8. **生产集成**：何时、是否通过 MCP 暴露？工具名、权限、速率限制是什么？
9. **Schema 演进**：pack index 长期独立，还是迁移到未来 memory schema v7？
10. **多用户并发**：R7 v1 是 session-local；跨进程 catalog、多写者协调是否进入下一阶段？

## 9. 已知限制与不冒充

- 不是生产功能：没有默认服务接线、没有 MCP 工具、没有持久化 catalog、没有自动维护。
- 没有签名/信任链；`provenance` 和 `sourceKind` 都是自述，读取器只能保留标签，不能证明。
- 缓存只做 meta + 记录行校验，不是密码学真实性；FTS shadow table 仍可能被本地写者污染。
- 中文能力是 FTS5 trigram + 有界字面子串，不是分词/语义/向量检索。
- `lower()` 的大小写折叠只对 ASCII 可靠；非 ASCII 拉丁大小写不保证。
- 索引复用校验是 O(records)；未做大包性能 benchmark，也未做真实多人并发压测。
- 查询不返回 `details`；正文只返回 preview，可能不足以支持需要全文的消费方。
- 只支持本地目录 pack；没有网络同步、远程 registry、跨设备分发。
- 未验证 Windows 之外的路径/符号链接语义；当前证据只来自本机 Python 3.12 / SQLite 3.49.1。

## 10. 复现命令

```powershell
# 1) 新增测试（49）
py -3.12 -m unittest discover -s tests/python -p "test_memory_mount.py" -v

# 2) 既有 Memory 回归（90）
$files = @('test_project_memory.py','test_memory_service.py','test_memory_context.py',
           'test_memory_cli.py','test_memory_injection.py','test_memory_overhead.py',
           'test_memory_l0.py','test_memory_tree.py')
foreach ($f in $files) {
  py -3.12 -m unittest discover -s tests/python -p $f
}

# 3) 隔离 demo（只写 Build/r7-isolated/Evidence）
py -3.12 Build\r7-isolated\Evidence\build_demo.py

# 4) CLI 只读验证/查询
$env:PYTHONPATH = "src"
py -3.12 -m ue_agent_kit.memory_mount verify Build\r7-isolated\Evidence\demo-packs\r7.demo.project
py -3.12 -m ue_agent_kit.memory_mount query Build\r7-isolated\Evidence\demo-packs\r7.demo.shared `
  --query 契约审核 --principal alice --project-key UEAgentKit
```

测试日志：`Build/r7-isolated/Evidence/unittest-memory-mount.log`、
`Build/r7-isolated/Evidence/unittest-memory-regression.log`。

## 11. 2026-10-24 readiness follow-up

本轮只做独立、只读、可测试的准备，**没有**接入 `mcp_server.py` / `memory_service.py`，
也没有连接 DSH token 或修改 whitelist：

- 新增 `claims.py`：`VerifiedCallerContext` 只能由 `ClaimVerifier` 注入；
  `DemoCallerContext` 显式 `untrusted-demo`；`HmacClaimVerifier` / `TestClaimIssuer`
  是本地 test-only 注入示例，不是生产身份。
- 新增 `integrity.py`：可选 `pack.integrity.json` sidecar，覆盖 manifest canonical digest /
  scope / provenance / revision / priority / recordCount；无 receipt 明确标为 legacy
  unverified；有 receipt 仅算 digest-verified，不声称可信作者；非空 signature 仍拒绝。
- 新增 `adapter.py`：`ReadOnlyMountAdapter` 只接受 `VerifiedCallerContext`，包名按不可信输入
  做根目录 confinement，并可要求 receipt。
- 新增 27 条 readiness tests：identity 负例、receipt 篡改、adapter 权限/路径/cache 污染/
  短中文/多 pack 冲突。实测 **27/27 PASS**；原有 49/49 仍 PASS；合计 **76/76**。
- 90 条既有 Memory 回归仍 **90/90 PASS**；本轮真实测试总计 **166 项通过**
  （76 + 90），无 skip。
- 隔离 microbench（5000 records）：`loadVerifyMs≈181`、`mountFirstMs≈349`、
  `mountReuseMs≈246`、trigram median≈8.4ms、短查询 fallback median≈6.1ms。
  仅记录观测值，不设通过阈值。
- 本轮 code+docs commit：`__R7_READINESS_COMMIT__`（最终交付消息同时给出后续 docs commit）。

详细威胁模型、接口和待人工裁决项见
[R7_READONLY_INTEGRATION_READINESS.md](R7_READONLY_INTEGRATION_READINESS.md)。

## 附：测试类计数核对

```text
CliTests                  1
IndexIsolationTests       6
ManifestValidationTests   8
MountPermissionTests     10
PackIntegrityTests       10
QueryTests               14
---------------------------
total                    49
```

本报告中的“49 + 90 = 139”均为真实运行结果，不是估算；没有跳过用例，也没有把
“编译通过”当作测试通过。
