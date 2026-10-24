# R1.5 / T8 真实 loopback socket 只读影子链路验收报告

- 状态：**PASS（R1.5 read-only shadow vertical slice）**
  - T8 socket 子集 **PASS**：shadow 43/43、off 3/3、manifest missing/corrupt/version 各 3/3。
  - T8 未覆盖项明确为 **PARTIAL**：expired/stale 纯 socket、unsupported spawner over socket、graceful shutdown、>1 MiB 响应主动触发（见 §8）。
  - T9 仅 **观测性 PARTIAL**；T7 未实现；无 BLOCKED。
- 日期：2026-10-24
- 工作树：`E:\WorkSpace\UEAgentKit-WT\r1-node-registry`
- 分支 / 起点：`feature/r1-node-registry` @ `ee3abbd`；本地提交未 push。
- 模型（已核验）：`cline-pass/deepseek-v4.1-flash-1017`，`reasoningEffort=max`，非 Fast；同一 DSH session。
- 宿主：UE 5.6.1（CL 44394996），`Build/r1-native/R1AutomationHost`；manifest 隔离副本 `Build/r15-manifest-host`。
- 最终 DLL：`sha256:1e0049f02de1fdf1f38f6da3d0faf2720a49a6991335726d7ba258492f821a18`。

## 0. 结论

1. 真实 `UnrealEditor-Cmd` + EditorBridge loopback TCP + **外部 Python harness** 完成 hello 认证、catalog/filterContext 分页、负例、断连、只读和性能采样；没有 in-process mock，没有 Editor 内 Python/CMD，没有 GUI/合成输入。
2. `native.invoke` 在 **off / shadow / 无效 manifest** 三种情况下的 capability 与 fail-closed 行为符合预期；默认仍为 OFF，宿主配置已恢复 OFF。
3. catalog 512 条 described official descriptor 以 8 页无重无漏返回；actionIds、semantic descriptor、catalog/context fingerprints 与 R1.4 一致；`total=512`、`catalogTruncated=true`，不冒充 38k。
4. 完整 R1 套件 **9/9 Success，0 warnings/errors**；Python `native_tools` **52/52**，`tests/python` **1241/1241（17 skipped）**。
5. 无效 manifest 在**独立 disposable copy** 中测试；原始 `native-tools.v1.json` 未被修改，Editor 启动不 fatal，legacy bridge 正常。

## 1. 编译与进程

| 项 | 值 |
|---|---|
| 目标 | `R1AutomationHostEditor Win64 Development` |
| 命令 | `Build.bat ... -WaitMutex -NoHotReload` |
| 结果 | **Succeeded**，exit 0（原宿主 + 独立 manifest copy） |
| 最终 DLL | `sha256:1e0049f02de1fdf1f38f6da3d0faf2720a49a6991335726d7ba258492f821a18` |
| 构建日志 | `Build/r1-native/Evidence/R15/build-warmup.log`、`build-manifest-host.log` |
| Editor | `-WindowStyle Minimized` + 仅本 PID 的 `ShowWindow(SW_MINIMIZE)`；无 GUI/合成输入 |
| 并发 | 启动前确认无 M3/UBT/UBA；所有 Editor 串行；结束后 0 UE 进程 |

## 2. 外部 harness

`tests/native_tools/r15_socket_harness.py`：
- 读取 `Saved/UEAgentKit/EditorBridge.json`，按 processId 匹配本次 Editor；
- 每次请求使用新 TCP 连接；非 hello 请求先在同一连接完成 hello（EditorBridge 在每次响应后关闭连接）；
- hello 的 `authToken/serverVersion/projectPathHash` 位于 **request root**（不是 params）；
- 证据中 token 固定写为 `<redacted>`；不复制 descriptor；
- shadow 模式的 fixture 由 test-only `UEAgentKit.Native.SocketWarmup` 用 C++ `LoadObject` 加载并 `AddToRoot`，**不打开 Blueprint asset editor**（避免 minimized 编辑器 Slate restore 崩溃）。

`scripts/RunR15SocketAcceptance.ps1`：
- 切换宿主 `NativeToolRegistry=off|shadow`（gitignored host config），启动/停止自己的 Editor PID，收集日志/进程内存；
- manifest 变体在 `Build/r15-manifest-host` 的独立 plugin copy 上执行，绝不改原 worktree 的 manifest。

## 3. Shadow（真实 socket，43/43）

| 检查 | 实测 |
|---|---|
| hello 认证 / project identity | 通过；session/projectHash/pluginVersion 匹配 |
| capability | `native.invoke` 存在（34 个，legacy 33 + native） |
| catalog 页大小/页数 | 64 / **8 页** |
| catalog total / truncated | **512** / **true** |
| catalogAvailable | 762 |
| catalog 无重无漏 | 512 unique，按 actionId |
| actionIds vs R1.4 | **完全一致** |
| semantic descriptor vs R1.4（去掉 build hash/discoveredAt） | **完全一致** |
| descriptorDigest vs 当前 build baseline | **512/512 一致** |
| catalog fingerprint | `sha256:b09fb1387e188e02`（与 R1.4 一致） |
| palette context | 443 条，fingerprint `sha256:f7a05fa9ba7b345d`（与 R1.4 一致） |
| exec-pin context | 297 条，fingerprint `sha256:13e68f6732b07f76`（与 R1.4 一致） |
| legacy `maxItems` | 未分页路径未改（R1.4 回归通过） |
| 重放 cursor | 同一 cursor 两次同一页（幂等，只读） |
| 篡改 / pageSize / query mismatch | `native-schema-invalid-arguments` |
| 未知 tool / version / args | `native-tool-unknown` / `native-tool-version-unsupported` / `native-schema-invalid-arguments` |
| 空 query / 未加载 asset | `native-schema-invalid-arguments` / `graphpatch-asset-not-loaded` |
| 5000 字符 cursor | `native-schema-invalid-arguments` |
| 70 KiB request | `request-too-large`，连接关闭 |
| 断连/重连、partial-line 超时 | Editor 继续服务，重连成功 |

**digest 说明**：`descriptorDigest` 的 canonical projection 含 `source.pluginBuildHash`。本次为加入 test-only warmup 重建了 DLL，所以 R1.4 的 digest 与当前 DLL 不同；这是 build-scoped 设计，不是内容漂移。对 R1.4 的 actionIds/semantic descriptor/fingerprints 完全一致，对**当前 build baseline** 的 descriptorDigest 512/512 一致。报告不宣称跨 build digest 等值。

## 4. Off（3/3）

- capability 集合 **33** 个，**无** `native.invoke`；与 manifest 无效时的 capability 集合完全一致。
- `editor.status` 正常（`available`），证明 legacy bridge 未受影响。
- 直接调用 `native.invoke` → `native-registry-unavailable`，不存在 capability 之外的旁路成功。
- 无 asset 操作；默认 OFF 未被改变。

## 5. Manifest 隔离（3 × 3/3）

在独立 copy `Build/r15-manifest-host`（复制 plugin source/resources/config，独立 build）测试：

| 变体 | capability | `native.invoke` | legacy `editor.status` | Editor |
|---|---|---|---|---|
| missing（copy 中临时移走） | 无 native | `native-registry-invalid-manifest` | available | 正常启动，无 fatal |
| corrupt（`{ not json`） | 无 native | `native-registry-invalid-manifest` | available | 正常启动，无 fatal |
| version mismatch（schema 9.9） | 无 native | `native-registry-invalid-manifest` | available | 正常启动，无 fatal |

- 原始 `Plugin/UEAgentKit/Resources/NativeTools/native-tools.v1.json` SHA256 `310ccea60bafbce7cadfb2c15dbcb03ecfe74094e6ea4859736dc7569dc98b10`，测试前后 git status 未变。
- 每个 manifest 运行日志 `Fatal error|Critical error` = 0；启动不因 manifest 失效而终止。
- P11/menu 未修改（本 lane 未改 `UEAgentKitEditorModule.cpp`/P11 源）；legacy capability 列表不变。

## 6. T5 只读（socket）

- `dirtyPackageCount` 0 → 0；
- `inspectAssetLive` 的 `memory.packageDirty` false → false，`loaded` 保持 true；
- `.uasset` SHA256 `a524005fe86bac96d0f038185c00a8d6b2bddb9db75095637c75ca322adf54bd` 前后不变；
- fixture `graphFingerprint` `sha256:5f7400a9…`（warmup 写入；socket 不再重算）；
- **未暴露项**：Undo queue/count、AssetRegistry 计数、graphFingerprint 重算在 socket 层不可见；由 R1.4 `R1ReadOnly` 的 in-editor T5 覆盖（dirty 0→0、packages/assets/undo 不变、revision/graph fingerprint 前后相同）。
- 未执行 Save/Compile；warmup 的 fixture 加载/保存发生在测量前（setup，disposable `/Game/R1Native`）。

## 7. T9 观测数据（有限）

| 指标 | 值 |
|---|---|
| catalog cold（首次，含模板创建） | ~3.00 s |
| catalog warm（后续页/调用） | ~312 ms |
| context p50 / p95（8 次） | ~57 ms / ~71 ms |
| 进程 WorkingSet / Peak | ~2.16 GB / ~2.16 GB |
| Private bytes | ~2.00 GB |

- 这是 **单机、单次、含 Editor/Engine 基线** 的观测，不是生产内存/延迟承诺；cold 包含 768 模板预算与首次 catalog 构建。
- 无无界重试循环；分页有页数上限和 15 s 客户端读超时。

## 8. T8/T9/T10 覆盖边界

| 项 | 状态 |
|---|---|
| T8 真实 socket auth/gating/catalog/context/分页/负例/断连 | **PASS**（43 checks） |
| T8 expired / stale epoch/set（纯 socket） | **PARTIAL**：R1.4 `R1Pagination` 已覆盖；本次未等待 900 s 或构造多 epoch |
| T8 unsupported third-party spawner（socket） | **PARTIAL**：R1 `R1UntrustedSpawner` 已覆盖；socket 不暴露 spawner 注入 |
| T8 graceful shutdown over socket | **PARTIAL**：本次由测试脚本停止自己的 PID；未验证协议级 graceful shutdown |
| T8 响应 >1 MiB 主动触发 | **PARTIAL**：pageSize 上限 256，本次请求侧 oversize 已验证；未构造 >1 MiB 响应 |
| T9 生产内存/延迟 | **PARTIAL**：仅观测样本，含 Editor 基线 |
| T7 菜单绑定 | **未做** |
| T10 异常注入（socket） | 未做；R1 in-editor untrusted test 已通过 |
| R2/Apply、默认开启、38k 扩面 | **未做，禁止** |

## 9. 失败与修正记录（保留证据）

1. **`openAsset/focusAsset` + minimized Editor 崩溃**：`GetRestoredDimensions is not expected to be called on this platform`，Slate/ApplicationCore fatal；日志在 `R15/manual-editor.log`、`manual-context.log`。改为 test-only `SocketWarmup` 用 C++ `LoadObject` 加载 fixture，不打开 asset editor。
2. **hello auth 失败**：EditorBridge 在 request root 读取 `authToken/serverVersion/projectPathHash`，harness 最初放在 `params`；修正后通过。
3. **orchestrator exit code 误判**：Python stdout 被 PowerShell 当作返回值数组；改为 `| Out-Host` + `$LASTEXITCODE`，并修复 manifest 分支同类问题。
4. **token 泄漏风险**：一次 debug 运行把 descriptor 对象写入 `run-meta.json`；已覆盖为 redacted，并对 R15 全目录按当前 token 检索 **0 次**；最终证据仅含 `<redacted>`。
5. **digest 变化**：`pluginBuildHash` 进入 descriptorDigest；重建 DLL 后 R1.4 digest 必然变化；改为对当前 build baseline 验证 512/512，并对 R1.4 验证 actionIds/semantic/fingerprints。

## 10. 回归

- R1：**9 Success / 0 failed / 0 notRun / 0 warnings**（`R15/r1-regression/automation-index.json`）。
  - `R1Catalog` 512/762，`R1ContextFilter` palette/exec 443/297，`R1CursorVectors` 5/5，`R1Pagination` 8/10 页，`R1DescriptorParity` 200 金值，`R1ReadOnly` T5，`R1ShadowTools`，`R1UntrustedSpawner` 全绿。
- Python `native_tools`：**52/52 OK**（47 + 5 harness 单测）。
- Python `tests/python`：**1241/1241 OK，17 skipped**。
- `tests/integration`：0 tests（无该类测试集）。
- 无 UE 编译警告新增；构建仅有既有 MSVC/IKRig 警告。

## 11. 证据

| 证据 | 路径 |
|---|---|
| Shadow（43 checks） | `Build/r1-native/Evidence/R15/shadow/socket.json` |
| Off（3 checks） | `R15/off/socket.json` |
| Manifest missing/corrupt/version | `R15/manifest-*/socket.json` |
| R1 9-test 回归 | `R15/r1-regression/automation-index.json` |
| 汇总 | `R15/r15-summary.json` |
| 构建 | `R15/build-warmup.log`、`R15/build-manifest-host.log` |
| 进程内存 | `R15/shadow/process-metrics.json` |
| Editor 日志 | `R15/*/editor.log` |
| 失败留证 | `R15/manual-editor.log`、`manual-context*.log` |
| 原始 manifest hash | `sha256:310ccea60bafbce7cadfb2c15dbcb03ecfe74094e6ea4859736dc7569dc98b10` |

## 12. 结论

R1.5 的真实 loopback socket 只读影子链路已通过：off/shadow/manifest 三种 gating、真实 hello 认证、catalog/filterContext 分页、负例/断连/oversize、只读性和 R1 全回归均有一手证据。覆盖仍为 512 described / 762 available / 38,325 discovered；未默认开启、未扩 38k、未做 T7/T10/R2。T8 的 expired/stale/unsupported/graceful-shutdown 与 T9 生产开销明确标为 PARTIAL/未覆盖，不冒充全量。
