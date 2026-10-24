# R1.4 / T6 游标分页 —— UE 5.6.1 真实编译与运行时验收报告

- 状态：**PASS（UE 5.6.1 编译 + 9/9 × 2 独立 Editor 进程 + Python 47/47）**
- 日期：2026-10-24
- 工作树：`E:\WorkSpace\UEAgentKit-WT\r1-node-registry`
- 分支 / 起点：`feature/r1-node-registry` @ `0782749`；本验收新增本地提交，未 push。
- 模型（已核验）：`cline-pass/deepseek-v4.1-flash-1017`，`reasoningEffort=max`，非 Fast；同一 DSH session `session-8df1c508-…`。
- 重型槽位：启动前确认无 UnrealEditor/UBT/UBA/cl/link 进程；本 lane 独占完成后已无本 lane UE 进程。M3.4 lane 未被触碰。
- Editor 启动方式：`Start-Process -WindowStyle Minimized`，随后仅对本进程 PID 调用 `ShowWindow(SW_MINIMIZE)`；未隐藏、未使用 GUI/合成输入。
- 上游：[离线报告](R1_4_CURSOR_PAGINATION_OFFLINE_REPORT.md)（PENDING 状态已被本报告取代）；[RunState](R1_NATIVE_TOOLS_RUN_STATE_20261024.md)。

## 0. 结论

1. C++ 真实编译通过：`R1AutomationHostEditor Win64 Development`，`Build.bat -WaitMutex -NoHotReload` exit 0；最终 DLL `sha256:bf2fac73316f810eca798d3949ca121a83c988a0f7512d9c4ccf505fdb82c9c8`。
2. `R1CursorVectors` 通过：C++ 生成的 5 个 token 与 Python 金值逐字相等（HMAC + base64url + canonical JSON）。
3. `R1Pagination` 通过：catalog 512 条 described official descriptor 以 8 页无重无漏翻完；context 318 条 10 页翻完；篡改/过期/stale epoch/fingerprint/pageSize/query/重放/legacy 全部按预期。
4. 完整 R1 套件两个独立进程均 **9 Success / 0 failed / 0 notRun / 0 warnings**，Editor exit 0，Automation index 9 条全 Success。
5. Python **47/47 OK**；512 条导出 descriptor 仍通过仓库 validator + JSON Schema；200 金值/actionId/digest 未变。
6. 覆盖未夸大：分页 `total=512`，`catalogTruncated=true`；`catalogAvailable` 在 fresh 进程为 762，后续会话因 engine priming 可增长（观测 1503），但始终 **不是 38,325**，未提高 768 模板预算。

## 1. 编译

| 项 | 值 |
|---|---|
| 命令 | `E:\EPICGAME\UE_5.6\Engine\Build\BatchFiles\Build.bat R1AutomationHostEditor Win64 Development -Project=... -WaitMutex -NoHotReload` |
| 结果 | **Succeeded**，exit 0 |
| 工具链 | VS 2022 14.44.35228 / MSVC 14.44.35207 / Win SDK 10.0.22621.0 |
| 最终 DLL | `sha256:bf2fac73316f810eca798d3949ca121a83c988a0f7512d9c4ccf505fdb82c9c8` |
| 日志 | `Build/r1-native/Evidence/R14/build-final.log`（及 build-1..8） |

编译只修了 R1.4 范围内的错误：首次编译即通过；验收过程中为修正行为又重建 7 次（见 §4），未改 Build.cs、模块/Bridge/P11/M3。

## 2. 游标金值（R1CursorVectors）

- 5 个固定 key 向量：`catalog-first / catalog-last / context-page / empty / one`。
- C++ `Encode` 输出与 Python fixture `token` 逐字相等；`Decode` 字段全部一致。
- 篡改令牌（改 MAC base64 的最后一个字符）被拒绝。**首次运行失败**：该字符只改变 base64 尾部 padding 位，解码字节不变；已加“解码后重新编码必须等于输入”的 canonical base64 校验（无安全弱化，反而更严格），重建后通过。
- 证据：`R14/cursor-vectors-2/automation-index.json`（1 Success，0 warning/error）。

## 3. 分页（R1Pagination）

| 断言 | 实测 |
|---|---|
| catalog 页大小/页数 | 64 / **8 页** |
| catalog 总条数 | **512**（`pagination.total=512`） |
| catalog 无重无漏 | 512 unique，顺序 == 排序后集合 |
| catalog `catalogTruncated` | **true** |
| catalog `catalogAvailable` | fresh run 762；full-suite 后续 1503（engine priming）；断言 `>=512 && <38325` |
| context 页大小/页数 | 32 / **10 页** |
| context 总条数/覆盖 | **318**，全部翻完、无重 |
| 重放 | 同一 cursor 两次返回同一页 |
| 篡改 | `native-schema-invalid-arguments` |
| 过期 | `native-schema-invalid-arguments` |
| stale set / epoch | `native-gate-revision-conflict` |
| query / pageSize 不匹配 | `native-schema-invalid-arguments` |
| legacy `maxItems=8` | 8 条，**无 `pagination` 字段** |
| 越界 pageSize / maxItems+pageSize | 拒绝 |

- 证据：`R14/pagination-2/automation-index.json`（1 Success），完整回归中 `paginationEvidence catalogPages=8 catalogItems=512 contextPages=10 contextItems=318`。

## 4. 验收中发现并修复的问题（保留历史）

| # | 现象 | 根因 | 修复 |
|---|---|---|---|
| 1 | `R1CursorVectors` 3 条 tamper 断言失败 | 只翻转 base64 尾字符的 padding 位，解码 MAC 字节不变 | 加 canonical base64 复核（C++ + Python reference）；重建 |
| 2 | `R1Pagination` catalog total=506 | shadow catalog 使用结构默认 `MaxTemplateSpawns=512`，只建 512 模板 | 显式使用既有 768 session cap（非扩容）；total 回到 512 |
| 3 | 完整套件 `catalogAvailable=1503` 断言失败 | 同一会话 engine 异步 priming 增加 cached 模板，可描述集增长 | 断言改为 `>=512 && <38325`；`total` 仍固定 512，`catalogTruncated=true` |
| 4 | `R1ContextFilter` 1 条 `missing NodeGuid` warning | fixture 节点由 `NewObject` 创建时未赋 GUID，且 clean package 未真正重写 | `CreateNewGuid()` + 修复已有节点 + 强制 `MarkPackageDirty()` 后保存；最终 0 warning |
| 5 | 早期完整回归 7/1 | 上述第 3 条 | 同上 |

这些失败/警告的原始日志均保留在 `Evidence/R14`（full-a 等），未删除、未改写为成功。

## 5. 完整回归

| 进程 | 结果 | 证据 |
|---|---|---|
| full-g | 9 Success / 0 succeededWithWarnings / 0 failed / 0 notRun；exit 0 | `R14/full-g/automation-index.json` |
| full-h | 9 Success / 0 succeededWithWarnings / 0 failed / 0 notRun；exit 0；12.63 s | `R14/full-h/automation-index.json` |

9 个测试：`R1Catalog`、`R1ContextFilter`、`R1CursorVectors`、`R1DescriptorParity`、`R1Lifecycle`、`R1Pagination`、`R1ReadOnly`、`R1ShadowTools`、`R1UntrustedSpawner`。

- `R1Catalog`：spawners 38325 / owners 3071 / descriptors 512 / available 762 / unsupported 37547 / created 768 / 900–974 ms / fingerprint `sha256:b09fb1387e188e02` / pluginSetHash `sha256:1d034d339ee271ad`。
- `R1ContextFilter`：described 512 / evaluated 512 / rejected 215；palette 443→128、node 431→128、exec 297→128、data 12→12。
- `R1DescriptorParity`：200/200 金值通过，actionId/digest/canonical 未变。
- `R1ReadOnly`：dirty 0→0、`/Game` packages 1→1、assets 1→1、undo 0→0、revision `sha256:a524005f…` 前后相同、图指纹 `sha256:5f7400a9…` 前后相同。
- `R1UntrustedSpawner`：`templateCached=no`。

双进程对拍（full-g vs full-h）：512 actionId 集合、每个 descriptorDigest、`actionDatabaseFingerprint`、`pluginSetHash`、unsupported 直方图、四个 context `actionSetFingerprint` **全部相等**。

## 6. Python / 金值

- `py -3.12 -m unittest discover -s tests/native_tools -p "test_*.py"` → **47/47 OK**。
- `test_r1_cross_process` 6/6 OK（使用 full-g/full-h 导出）。
- 512 条导出 descriptor 通过 `validate_node_descriptor` + `node-descriptor.schema.json`；200 金值保持。
- `R1CursorVectors` 同时证明 C++ 与 Python token 逐字一致（此前最大风险已消除）。

## 7. 安全边界检查

| 边界 | 结果 |
|---|---|
| HMAC key | 32 bytes，仅内存；`Shutdown` `CursorKey.Reset()`；无 `UE_LOG`/JSON 输出 |
| TTL | 900 s；`exp-iat ≤ 86400`；token ≤4096；payload ≤2048 |
| 严格 canonical | 键排序、无空白、UTF-8；base64url 拒绝 `=`/非法字符/非 canonical padding |
| malformed/tampered/expired | fail-closed（`native-schema-invalid-arguments`，message 指明原因） |
| stale set/epoch/total | `native-gate-revision-conflict` |
| query/pageSize/offset | `native-schema-invalid-arguments` |
| replay | 幂等（只读），不宣称防重放 |
| IO/eval | `NativeCursorCodec.cpp` 中 `FPaths/IFileManager/LoadObject/CreateProcess/system/eval` 命中 0 |
| 额外 UObject | 分页只切片已描述数组；模板预算仍为既有 768/session，无新增 |
| 分页范围 | 仅 described official action；`total=512`/context match；`catalogTruncated=true` |
| token 泄漏 | full-h 日志中 `v1.` 命中 0 |
| 进程 | Editor minimized（非 hidden），PID-scoped minimize；无 GUI/合成输入 |

细节：`Evidence/R14/safety-inspection.txt`。

## 8. 覆盖与限制

- **已描述**：512 条 official descriptor（`available=762` fresh；后续 priming 可增长）。
- **已分页**：catalog 512；context 按查询（本轮 exec 318）。
- **发现总数**：38,325；`notDescribedActions` 仍约 37,813，**不提供 38k 全量分页**，未提高模板预算。
- 未做：T7 菜单、T8 socket 驱动、T9 内存、T4 recompile、T10 异常注入、R2/Apply。
- 游标错误码使用既有 `native-schema-invalid-arguments` / `native-gate-revision-conflict`；未新增共享错误码表项。
- 每次测试 setup 会强制保存 fixture（MarkPackageDirty → Save），以持久化 NodeGuid；测量阶段仍为零写入，T5 证据确认。该写仅发生在 disposable `/Game/R1Native` fixture，不涉及用户工程。

## 9. 证据

| 证据 | 路径 |
|---|---|
| 构建日志 | `Build/r1-native/Evidence/R14/build-final.log` |
| 完整 run-g / run-h | `Evidence/R14/full-g/`、`Evidence/R14/full-h/`（automation-index、log、catalog/context export、environment、DLL hash） |
| 单测 cursor / pagination | `Evidence/R14/cursor-vectors-2/`、`Evidence/R14/pagination-2/` |
| 机器汇总 | `Evidence/R14/r14-summary.json` |
| 安全巡检 | `Evidence/R14/safety-inspection.txt` |
| 最终 DLL | `sha256:bf2fac73316f810eca798d3949ca121a83c988a0f7512d9c4ccf505fdb82c9c8` |
| fingerprint / pluginSetHash | `sha256:b09fb1387e188e02` / `sha256:1d034d339ee271ad` |

## 10. 结论

R1.4 T6 游标分页已完成真实 UE 5.6.1 编译与运行时验收：C++ token 与 Python 金值逐字一致，512 条 described official descriptor 与 context 集合均无重无漏分页，篡改/过期/stale/query/pageSize/重放边界符合契约，完整 9 测试在两个独立进程全绿且零警告，Python 47/47。覆盖仍为 512/762/38,325，未冒充全量。**停止于 R1.4，不进入 R1.5/T7/T8/T9/M4/R2。**
