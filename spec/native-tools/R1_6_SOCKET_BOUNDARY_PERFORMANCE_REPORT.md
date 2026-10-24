# R1.6 真实 socket 负向门禁补完 / 原生命中只读性能证据报告

- 状态：**PASS（R1.6 closeout；T8 缺口已闭合，T9 观测性 PASS，T10 保留 PARTIAL）**
- 日期：2026-10-25（本地）；R1.6 初始运行 2026-10-24
- 工作树：`E:\WorkSpace\UEAgentKit-WT\r1-node-registry`，分支 `feature/r1-node-registry`，起点 `defb2f3`
- 模型（已核验）：`cline-pass/deepseek-v4.1-flash-1017`，`reasoningEffort=max`，非 Fast；同一 DSH session
- UE：5.6.1（CL 44394996）；宿主 `Build/r1-native/R1AutomationHost`（gitignored）；最终 DLL：
  `sha256:709aff246075ddd7ffe429ab631026432fc5912d5302cfaf61f0a82e7a21ea4e`
- 默认状态：`NativeToolRegistry=off`；宿主 config 已恢复 OFF；无默认开启、无 T7 菜单、无 R2/Apply。

## 0. 结论

1. 补齐 R1.5 的四个真实缺口：**跨 Editor epoch stale、2 秒 TTL 真过期、Editor shutdown 时 held socket 关闭、page 256 响应边界**。
2. 3 次独立真实 Editor 的 shadow socket 各 **46/46**；capture **4/4**、stale **2/2**、expiry **4/4**、shutdown **2/2**、off **3/3**。
3. 同一 seam DLL 上 R1 Automation **9/9 Success，0 warnings/errors**；Python native_tools **57/57**；`tests/python` 1241/1241（见 §8）。
4. `page 256` 实测响应 **618,137 bytes < 1,048,576**，`pageSize=257` 被拒绝；因此 **>1 MiB 响应路径标记 `UNREACHABLE_BY_VALID_REQUEST`**，没有削弱 256 上限。
5. 证据目录内 **0 个 bridge token、0 个原始 authToken 字段、0 个 64 位大写 token**；测试 key 不再硬编码，改为每次运行随机生成并以进程环境传递。

## 1. 本轮实际运行（只跑必要的最小集）

| 项 | 值 |
|---|---|
| 编译 | 已有 seam DLL 的成功构建；最终 DLL hash 见上（未重新跑 3 次编译） |
| 真实 Editor 次数 | 8 次（perf×3、capture、stale、expiry、shutdown、off） |
| 每次 Editor | `-unattended -nop4 -nosplash -NullRHI -NoSound`，`-WindowStyle Minimized` + 仅本 PID `ShowWindow(SW_MINIMIZE)` |
| 并发 | 无 UBT/UBA/其他 Editor；结束后 0 UE 进程 |
| 超时 | socket 读 15 s；descriptor 60 s；Editor 启动 180 s；shutdown harness 30 s |
| R1 Automation | 9/9 Success，0 warnings/errors（`R16/r1-regression/automation-index.json`） |

## 2. T8 缺口闭合

### 2.1 跨 Editor epoch stale

- 两个独立 Editor 使用同一 **test-only** cursor key（随机生成，仅环境变量传递）：
  - Editor A `capture` 取得 `nextCursor`；
  - Editor B 复用同一 cursor；
  - 结果：`native-gate-revision-conflict`（epoch 含 sessionId，跨 session 拒绝）。
- 证据：`R16/stale/socket.json`（2/2）。
- 这不改变生产 key 生命周期：正常 Editor 仍是每 session 随机 128-bit key。

### 2.2 时间过期

- 测试 Editor 以 `-UEAgentKitSocketTest` + `UEAK_TEST_CURSOR_TTL=2` 启动；cursor `ttlSeconds=2`；
- 等待 3 s 后复用：`native-schema-invalid-arguments` / **`cursor is expired`**；
- 生产 TTL 仍为 **900 s**，未修改默认值。
- 证据：`R16/expiry/socket.json`（4/4）。

### 2.3 shutdown / 断连

- 独立客户端完成 hello 后保持连接；Editor 停止时 held socket 在 **0.031 s** 内关闭（`ConnectionResetError`）；
- 既有 partial-line、disconnect/reconnect 断言仍在 46 项中通过；
- 证据：`R16/shutdown/socket.json`（2/2）、`R16/perf-*/socket.json`。

### 2.4 page 256 / >1 MiB 边界

| 项 | 值 |
|---|---|
| 公共 pageSize 上限 | 256 |
| page 256 实测响应 | **618,137 bytes** |
| Bridge 响应 guard | 1,048,576 bytes |
| pageSize=257 | 拒绝：`native-schema-invalid-arguments` |
| 结论 | 有效 DTO **不能**触发 >1 MiB 响应；标记 `UNREACHABLE_BY_VALID_REQUEST`，不降低 page cap |

证明：pageSize 上限 256 + 实测 256 页 618,137 B；没有合成超大 descriptor 或绕过公共 DTO。证据：3 次 perf 的 check `max page (256) response is below the 1 MiB response guard`。

### 2.5 第三方 spawner 排除

- Socket catalog 512 条全部为 `trustLevel=official`；新增检查 `no untrusted spawner descriptor in catalog` 通过；
- in-editor `R1UntrustedSpawner` 9/9（`templateCached=no`），未创建 untrusted 生产插件、未调用模板；
- Socket 不提供 spawner 注入入口，故“socket 直接注入 untrusted spawner”是 **NOT APPLICABLE**，不是 PASS 的虚假声明。

## 3. T9 观测性能（3 个独立进程）

每个进程均为 fresh Editor + `SocketWarmup` fixture，catalog 全量 512/8 页；context 为 8 次采样。

| run | catalog cold (ms) | warm (ms) | context p50 (ms) | p95 (ms) | WS before → after (bytes) | peak WS (bytes) | private (bytes) |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | 3581.14 | 338.40 | 67.06 | 85.27 | 2,092,281,856 → 2,208,559,104 | 2,208,563,200 | 2,033,451,008 |
| 2 | 3719.88 | 412.64 | 78.66 | 81.49 | 2,091,601,920 → 2,208,391,168 | 2,208,395,264 | 2,047,614,976 |
| 3 | 4013.53 | 365.67 | 79.36 | 102.37 | 2,102,046,720 → 2,218,954,752 | 2,218,958,848 | 2,071,388,160 |
| **median** | **3719.88** | **365.67** | **78.66** | **85.27** | — | **2,208,563,200** | — |

对比 R1.5 单次基线：cold 2,996.21 → **3,719.88 ms（+24.2%）**；warm 312.21 → **365.67 ms（+17.1%）**；p50 56.97 → **78.66 ms（+38.1%）**；p95 71.19 → **85.27 ms（+19.8%）**；peak WS 2,163,724,288 → **2,208,563,200（+2.1%）**。

解释与限制：
- 样本只有 3 个进程；p50/p95 是每次 8 个 socket 调用的分位数，再取中位数，**不是生产 SLA**。
- cold 包含首次 768 模板创建；warm 包含 catalog 已缓存但每次仍需构造响应。
- WS 包含完整 Editor/Engine 基线，不能当作 native tools 的增量开销。
- 本轮未做 3 次重新编译（已有 seam DLL 验证）；性能差异可能包含 Editor/机器负载波动，R1.5/R1.6 不是严格 A/B。

## 4. 只读 T5

| 项 | 实测 |
|---|---|
| `dirtyPackageCount` | 0 → 0（3 次 perf 均 0） |
| `inspectAssetLive.memory.packageDirty` | false |
| `.uasset` SHA256 | `a524005f…` 前后不变（3 次一致） |
| graph fingerprint | `sha256:5f7400a9…`（warmup 记录；socket 不重算） |
| R1ReadOnly（in-editor） | dirty 0→0、packages 1→1、assets 1→1、undo 0→0、revision/graph fingerprint 前后相同 |
| AssetRegistry / Undo over socket | 未暴露；由 R1ReadOnly 覆盖，socket 层不声称 |

没有 Save/Compile/Apply；socket 调用后无 `.uasset` 变化。

## 5. Test-only cursor seam 安全审计

代码：`NativeNodeRegistry.cpp` / `.h`，**默认值不变**：

- 默认 `CursorTtlSeconds=900`；默认 key 仍由两个 GUID 生成 32 字节；
- 只有 **`FApp::IsUnattended()` AND `FParse::Param("-UEAgentKitSocketTest")`** 同时成立才读环境：
  - `UEAK_TEST_CURSOR_TTL`：clamp 1..3600；
  - `UEAK_TEST_CURSOR_KEY`：仅接受恰好 64 个 hex 字符；非法/缺失则**不覆盖**，保留随机 session key；
- key 只经进程环境传递，不出现在 argv、日志或 response；日志只记录 `keyOverride=yes/no`；
- `Shutdown` 重置 TTL 为 900 并清空 key；
- 生产默认 `NativeToolRegistry=off`；未改 EditorBridge 认证、未改 capability 列表、未改 manifest 加载。

边界：
- 该 seam 是本地 test-only 开关；能设置命令行/环境的人已能控制进程，但它不通过 MCP/EditorBridge 暴露，也不改变默认生产行为。
- 未在用户 shell 中安装 env；runner 在 `Start-Process` 前设置、启动后立即恢复。

## 6. Secret / 日志扫描

扫描 `R16` 的 62 个文件（.json/.log/.txt 等）：

| 类别 | 命中 |
|---|---|
| 当前 bridge authToken 原始值 | **0** |
| `"authToken"` 非 `<redacted>` 字段 | **0** |
| 64 位大写 token 模式 | **0** |
| 共享 test-only key 字面量 | **0**（runner 已改为每次 `Get-Random` 生成，且不写日志） |
| 原始 `v1.` cursor | 仅出现在脱敏扫描脚本自身（工具源码中的字符串），不在响应样本；request sample 中 cursor 为 `<redacted-cursor>` |

未打印任何 secret；未读取生产密钥；未修改 `EditorBridge.json`。

## 7. T8 / T9 / T10 / T7 真实状态

| 项 | 状态 | 证据/说明 |
|---|---|---|
| T8 auth/gating/catalog/context/paging/负例 | **PASS** | shadow 46/46 ×3、off 3/3 |
| T8 cross-editor epoch stale | **PASS** | stale 2/2，`native-gate-revision-conflict` |
| T8 TTL expiry | **PASS** | expiry 4/4，TTL=2 s，`cursor is expired` |
| T8 shutdown / held socket | **PASS** | shutdown 2/2，0.031 s reset |
| T8 >1 MiB response | **PASS as invariant / UNREACHABLE_BY_VALID_REQUEST** | page 256 = 618,137 B < 1,048,576；257 拒绝 |
| T8 third-party spawner exclusion | **PASS (exclusion)**；socket 注入 **N/A** | catalog 无 untrusted + R1UntrustedSpawner 9/9 |
| T8 manifest invalid | **PASS (R1.5 retained)** | R1.5 三变体 3/3；本 R1.6 未改 manifest loader/DLL 逻辑 |
| T9 观测性能 | **PASS（观测性，非生产承诺）** | 3 runs 表 |
| T10 UObject/template budget | **PARTIAL（沿用 R1.4/R1.5）** | 本轮未新增 in-editor T10；仍 768 模板、512 described |
| T7 UI menu | **NOT DONE** | 明确不做；避免与 M3 集成冲突 |
| R2/Apply、默认开启、38k 扩面 | **NOT DONE / prohibited** | — |

## 8. 回归

| 套件 | 结果 |
|---|---|
| R1 Automation（seam DLL） | **9/9 Success，0 warnings，0 failed，0 notRun** |
| Python `native_tools` | **57/57 OK**（52 + 5 R1.6 seam/harness guards） |
| Python `tests/python` | **1241/1241 OK（17 skipped）**（R1.6 tree；native_tools 改动不影响） |
| `tests/integration` | 0 tests |
| `git diff --check` | PASS |
| UE processes after | 0 |

## 9. 证据与哈希

| 项 | 值/路径 |
|---|---|
| DLL | `sha256:709aff246075ddd7ffe429ab631026432fc5912d5302cfaf61f0a82e7a21ea4e` |
| `NativeNodeRegistry.cpp` | `265ff9b824fcb4d43f5316661f26ab87bc8c995a745e8e895e71cff11a97968b` |
| `NativeNodeRegistry.h` | `33574e2fc64da1fce8f3b800420e160639b021f5bf531b07282fdeeae502ef09` |
| `r15_socket_harness.py` | `da0fe65587f93ba0481e1c69b8c985315d3fff4ee5c7ebe90e97df0bd13df7f6` |
| `RunR16Boundary.ps1` | `2f7aa4832004f2e296a83bcd6cbf53f5faef2e55635fed5d928a5b065c4fa255` |
| 原始 manifest | `sha256:310ccea60bafbce7cadfb2c15dbcb03ecfe74094e6ea4859736dc7569dc98b10`（未改） |
| 汇总 | `R16/r16-summary.json`、`R16/r16-runs-summary.json` |
| perf | `R16/perf-1|2|3/{socket.json,metrics-before/after.json,editor.log}` |
| 边界 | `R16/capture|stale|expiry|shutdown|off/socket.json` |
| R1 | `R16/r1-regression/automation-index.json` |
| 安全 | `R16/seam-check.log`（只显示 ttl/keyOverride，不含 key/token） |

## 10. 结论与限制

R1.6 已在不重写 registry、不默认开启、不扩 38k 的前提下，闭合 R1.5 的 socket negative-gate 与 page-size/performance 缺口：真实 UE 5.6.1 上 3 次 46/46、stale/expiry/shutdown/off 全部通过，R1 9/9，Python 57/57。T8 的 >1 MiB 响应经测量证明对有效 DTO **不可达**；T9 只是 3 个观测样本，不承诺生产内存/SLA；T10 与 T7 未扩展。**停止于 R1.6。**
