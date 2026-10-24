# R1.4 / T6 游标分页 —— 离线实现报告（UE 编译与运行时验证 PENDING）

- 状态：**CODE + OFFLINE CONTRACT VERIFIED；UE 5.6 编译 / UnrealEditor 运行时 PENDING（未执行）**
- 日期：2026-10-24
- 工作树：`E:\WorkSpace\UEAgentKit-WT\r1-node-registry`
- 分支 / 起点：`feature/r1-node-registry` @ `bcd3d45`
- 同机并行 lane：`layout-broker-m1`（M3.4，独立 worktree，未触碰）；本 lane 未运行 UE 构建 / Editor / UBT / UBA，重型槽位让给 M3.4。
- 模型（沿用）：`cline-pass/deepseek-v4.1-flash-1017`，`reasoningEffort=max`，非 Fast；同一 DSH session。
- 范围：T6 游标分页（catalog 已描述 512 条 + filterContext match set），**不扩大 38,325 spawner 模板预算**，不改变 trusted/untrusted 边界，不进入 R2 / T7 / T8 / T9。

## 0. 结论先行

1. 实现了**可选、不透明、带 HMAC 的游标分页**：`pageSize`（1–256）+ `cursor`；未传时旧 `maxItems` 行为完全不变。
2. 游标绑定：查询身份 `q`、动作集指纹 `s`、registry epoch `e`（session + manifest + set）、offset/total/pageSize、签发/过期时间；任何篡改/过期/查询变化/指纹或 epoch 变化都 fail-closed。
3. 目录分页只覆盖 **512 条已描述 official descriptor**（`available=762`，`discovered=38,325`）；`pagination.exhausted=true` 表示“512 条描述集翻完”，`catalogTruncated=true / catalogAvailable=762` 另行说明目录本身仍不完整，**不冒充 38k 全量分页**。
4. 旧请求/结果兼容：`maxItems` 路径不产生 `pagination` 字段；新增字段仅在 opt-in 分页时出现；descriptor/schema/actionId/descriptorDigest 均未改。
5. 离线验证：**47/47 Python 测试通过**，含 5 个金值 token 向量、512 个真实导出 actionId 的无重无漏分页、空/1/末页、越界、查询变化、stale/epoch、篡改、过期、重放幂等、无 IO/eval 静态守卫。
6. **C++ 未编译、未在 UE 运行**：所有 C++ 改动与新增 `R1CursorVectors` / `R1Pagination` 自动化测试标记为 **PENDING**，不是 PASS。下一步命令见 §7。

## 1. 变更

| 文件 | 变更 |
|---|---|
| `Private/Native/NativeCursorCodec.h/.cpp` | 新增：base64url、canonical payload、HMAC-SHA256 截断、严格解码/过期/边界校验 |
| `Private/Native/NativeNodeRegistry.h` | 新增 `ApplyPagination`、cursor epoch、per-session key、两个 automation seam；无 UI/bridge/模块改动 |
| `Private/Native/NativeNodeRegistry.cpp` | `catalog` / `filterContext` 新增 opt-in `pageSize`/`cursor`、分页 envelope、stale/query/expiry 拒绝、legacy 路径保持 |
| `Private/BlueprintContextSha256.h/.cpp` | 新增 `HmacSha256`（RFC 2104），不改变既有哈希行为 |
| `Private/Native/NativeNodeRegistryTests.cpp` | 新增 `R1CursorVectors`（金值对拍）与 `R1Pagination`（512 catalog / context 全量翻页、篡改/重放/查询/stale/过期/legacy）；**未运行** |
| `Resources/NativeTools/Golden/r1-cursor-vectors.json` | 5 个固定 key 的 token 金值（Python 生成，C++ 待对拍） |
| `tests/native_tools/r1_cursor_reference.py` | 离线参考实现（token/规范化/HMAC/分页/拒绝语义） |
| `tests/native_tools/test_r1_cursor.py` | 21 个离线测试（含真实 512 导出 ID 分页） |

未触碰：`UEAgentKitEditorModule.cpp`、`EditorBridge.cpp`、`Build.cs`、P11/M3 native layout/menu 源、共享 N0 文档、main/其他 worktree。

## 2. 游标契约

### 2.1 Token

```
v1.<base64url(canonical payload)>.<base64url(HMAC-SHA256(key, "v1."+payload_b64)[:16])>
```

- canonical payload（按键排序、无空白、UTF-8）：

```json
{"e":"sha256:...","exp":1760000900,"iat":1760000000,"k":"catalog",
 "n":512,"o":0,"p":64,"q":"sha256:...","s":"sha256:...","v":1}
```

| 字段 | 含义 |
|---|---|
| `v` | 版本，当前 1 |
| `k` | `catalog` / `context` |
| `q` | 查询身份指纹（catalog 固定；context 绑定 assetPath/graphGuid/nodeGuid/pinName/contextKind） |
| `s` | 有序 actionId 集合指纹（catalog=`actionDatabaseFingerprint`；context=`actionSetFingerprint`） |
| `e` | registry epoch = f(sessionId, manifestHash, kind, set) |
| `o/n/p` | 下一页 offset / 集合总数 / 页大小 |
| `iat/exp` | 签发/过期 Unix 秒（TTL 900 s，首次页签发，后续页沿用） |

- 会话 HMAC key：初始化时由两个随机 GUID 的 64 个十六进制字符取前 32 字节（128-bit 熵），仅存在内存；`Shutdown` 清除。**不写盘、不进日志、不进结果**。
- 长度上限：token 4096，payload 2048；`pageSize` 1–256；`total` ≤ 1,000,000；offset ∈ [0,total]。
- 无文件系统、无 eval、无资产加载；只读分页。

### 2.2 请求 / 结果（opt-in，向后兼容）

```json
// catalog
{"toolId":"graph.nodeRegistry.catalog","toolVersion":"1.0.0",
 "arguments":{"pageSize":64,"cursor":"v1...."}}
// filterContext
{"arguments":{"assetPath":"/Game/...","graphGuid":"...","nodeGuid":"...","pinName":"then","pageSize":32}}
```

- `pageSize` 与 `maxItems` 互斥；分页时 `includeDescriptors=false` 拒绝。
- 结果 `data` 保留原字段，新增：

```json
"pagination": {
  "kind":"catalog","pageSize":64,"offset":0,"returned":64,"total":512,
  "hasMore":true,"exhausted":false,
  "catalogTruncated":true,"catalogAvailable":762,
  "queryHash":"sha256:...","epoch":"sha256:...","setFingerprint":"sha256:...",
  "issuedAtUnix":1760000000,"expiresAtUnix":1760000900,"ttlSeconds":900,
  "nextCursor":"v1...."
}
```

- 最后一页 `hasMore=false, exhausted=true`，无 `nextCursor`。
- 未分页调用：无 `pagination` 字段，`maxItems` 行为与 R1.3 完全一致。

### 2.3 失败语义（fail-closed）

| 情况 | 返回 |
|---|---|
| token 格式/编码/HMAC/字段/边界非法 | `native-schema-invalid-arguments`（message 指明 invalid/malformed/authentication） |
| 过期 | `native-schema-invalid-arguments`（`cursor is expired`） |
| 查询身份（kind/q）变化 | `native-schema-invalid-arguments`（`does not match this query`） |
| 集合指纹 / epoch / total 变化 | `native-gate-revision-conflict`（`stale`） |
| pageSize 与游标不一致 | `native-schema-invalid-arguments` |
| offset 越界 | `native-schema-invalid-arguments` |
| 重放同一游标 | **允许且幂等**（只读；返回同一页），不是安全缺陷；报告不声称“防重放” |
| 分页中 context match 集 >512 | `native-limit-response-too-large`（不静默截断） |

未新增错误码表项（避免改动共享契约）；用已有码 + 稳定 message 区分，报告显式记录。

## 3. 覆盖与截断的区别

- `pagination.total` = 当前可翻页集合大小：catalog = 512（已描述且本次返回的 descriptor）；context = `matchedActions`（≤512）。
- `pagination.total` 不是 38,325，也不是 762。
- `catalogTruncated=true` + `catalogAvailable=762` 表示“目录本身因 512 描述上限未完整”；`exhausted=true` 只表示“512 描述集已翻完”。
- 未提高 768 模板预算；`notDescribedActions=37813`（38,325 − 512）仍是明确缺口。

## 4. 离线验证（本 lane 实际执行）

命令：

```powershell
py -3.12 -m unittest discover -s tests\native_tools -p "test_*.py"
```

结果：**47 tests OK**（R1.2/R1.3 既有 26 + T6 新增 21）。

T6 覆盖：

- 5 个金值 token：`catalog-first`、`catalog-last`、`context-page`、`empty`、`one`；encode/decode 逐字相等。
- 512 个**真实 UE 导出** actionId（来自 `Evidence/catalog-export.json`）按 64/页翻完，**无重叠、无缺口、顺序一致**。
- 合成 512 集同样无重无漏；空集、1 条、末页、重放。
- `pageSize` 1/256 通过，257 拒绝；`maxItems` 互斥。
- 查询 kind/字段变化、stale `s`、stale `e`、total 变化、pageSize 不匹配、过期、篡改、malformed、错误 key 全部 fail-closed。
- 静态守卫：cursor 代码不含 `FPaths/IFileManager/CreateProcess/system/eval/LoadObject/FSoftObjectPath`；C++ 常量（version 1 / 256 / 900 / 4096）与参考实现一致。

**未验证**：C++ 实现是否编译、C++ 生成的 token 是否与 Python 金值逐字一致、UE 内分页是否真实无重无漏。这些必须由 §7 的 UE 自动化完成；本轮不得标 PASS。

## 5. 兼容性 / 安全边界

- 默认 `NativeToolRegistry=off` 未变；分页只在 shadow/active 且显式 `pageSize/cursor` 时生效。
- 不改 descriptor schema、`actionId`、`descriptorDigest`、200 金值；不改 manifest 工具数/名称。
- 不触碰模板预算、`DeferredSpawnerReasons`、trust 判定、`GetTemplateNode/AllocateDefaultPins` 策略。
- 不加载资产；context 仍要求资产已加载。
- 游标无秘密泄露：诊断只暴露 queryHash/epoch/setFingerprint（非密钥）；HMAC key 不返回。
- 单请求响应仍受 1 MiB 上限；`pageSize≤256` + 现有 descriptor 大小远低于上限。

## 6. 风险 / 待复核

1. **C++ 编译风险**：新增 `NativeCursorCodec`、`ApplyPagination` 与测试尚未经过 UBT；可能存在 include/类型/`TSharedRef` 兼容问题。首次 UE 验证必须先编译，编译失败即 BLOCKED，不得跳过。
2. **C++/Python token 一致性**：依赖 canonical JSON 键排序、整数格式、HMAC、base64url 完全一致；金值测试是唯一证据，未运行前只能视为设计一致。
3. **palette context 覆盖**仍是 443→分页；未实现 T7 菜单、T8 socket、T9 内存。
4. **replay 语义**：允许幂等重放；若未来要求一次性游标，需要服务端 nonce 存储（有界内存/过期清理），本轮不做。
5. **错误码**：未新增 cursor 专用码，使用已有 `native-schema-invalid-arguments` / `native-gate-revision-conflict`；若评审要求独立码，需先入 error-code 表。
6. **context match >512**：当前实测 443，未触发；超限会显式拒绝而不是分页，需评估是否提高内部上限（不得连带提高模板预算）。

## 7. 待执行的 UE 验证（需重型槽位释放后）

```powershell
# 1) 构建（单实例）
E:\EPICGAME\UE_5.6\Engine\Build\BatchFiles\Build.bat R1AutomationHostEditor Win64 Development `
  -Project="E:\WorkSpace\UEAgentKit-WT\r1-node-registry\Build\r1-native\R1AutomationHost\R1AutomationHost.uproject" `
  -WaitMutex -NoHotReload

# 2) 全量回归（7 + 2 测试；R1CursorVectors / R1Pagination 先于其他用例执行）
powershell -File scripts\RunR1NativeTests.ps1 -SkipBuild -Filter "UEAgentKit.Native.R1"

# 3) 如需单独重跑两个新测试（Automation 过滤器不支持 "|"）
powershell -File scripts\RunR1NativeTests.ps1 -SkipBuild -Filter "UEAgentKit.Native.R1CursorVectors"
powershell -File scripts\RunR1NativeTests.ps1 -SkipBuild -Filter "UEAgentKit.Native.R1Pagination"

# 4) 离线（含真实导出分页）
py -3.12 -m unittest discover -s tests\native_tools -p "test_*.py"
```

验收判据：`R1CursorVectors` 5/5 token 相等；`R1Pagination` catalog 8 页/512 无重无漏、context 全量翻页、篡改/重放/查询/stale/过期/legacy 断言通过；随后更新本报告状态为 RUNTIME-VERIFIED，并记录新的 DLL hash。**在此之前保持 PENDING。**

## 8. 结论

T6 的离线契约、分页算法、边界与失败语义已实现并由 47 个 Python 测试验证；C++ 只是代码完成，未编译未运行，不作为 PASS。覆盖仍为 512 已描述 / 762 可描述 / 38,325 发现，未扩大预算，未冒充全量。停止于 R1.4 离线完成，不进入 R2/T7/T8/T9。
