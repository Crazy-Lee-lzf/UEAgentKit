# R1.6 READONLY RUNSTATE — 2026-10-25

- 状态：**R1.6 closeout PASS（T8 负向门禁补完；T9 观测性 PASS；T10 沿用 PARTIAL）**
- 工作树/分支：`E:\WorkSpace\UEAgentKit-WT\r1-node-registry` / `feature/r1-node-registry`
- 起点：`defb2f3`；R1.5 保持 PASS；未 push/merge/rebase/reset/clean
- 模型：`cline-pass/deepseek-v4.1-flash-1017`，non-Fast，reasoning max，同一 DSH session
- UE：5.6.1（CL 44394996）；默认 `NativeToolRegistry=off`，宿主 config 已恢复 OFF
- 最终 DLL：`sha256:709aff246075ddd7ffe429ab631026432fc5912d5302cfaf61f0a82e7a21ea4e`

## 结果

| 项 | 结果 |
|---|---|
| R1 Automation | 9/9 Success，0 warnings/errors |
| Python `native_tools` | 57/57 OK |
| Python `tests/python` | 1241/1241 OK（17 skipped） |
| socket shadow | 3 × 46/46 |
| capture / stale / expiry / shutdown / off | 4/4、2/2、4/4、2/2、3/3 |
| TTL expiry | TTL=2 s，等待 3 s → `cursor is expired` |
| epoch stale | 跨 Editor → `native-gate-revision-conflict` |
| shutdown | held socket 0.031 s 关闭 |
| page 256 / 257 | 618,137 B < 1,048,576；257 拒绝 |
| >1 MiB valid path | `UNREACHABLE_BY_VALID_REQUEST`（未削弱 page cap） |
| T5 | dirty 0、asset SHA `a524005f…` 不变；Undo/AssetRegistry 由 R1ReadOnly 覆盖 |

## 性能（3 个独立进程，观测性）

median：catalog cold **3719.88 ms**、warm **365.67 ms**、context p50 **78.66 ms**、p95 **85.27 ms**、peak WS **2,208,563,200 B**。
对比 R1.5 单次：cold +24.2%、warm +17.1%、p50 +38.1%、p95 +19.8%、peak +2.1%。含 Editor/Engine 基线，非生产 SLA。

## 安全

- test-only seam 必须同时满足 `-unattended` + `-UEAgentKitSocketTest`；key/TTL 仅环境变量；
- 默认 TTL 900 s、默认随机 session key 不变；非法/缺失 key 回退随机，TTL clamp 1–3600；
- 每次运行 test key 随机生成，不硬编码、不写日志；R16 扫描 0 bridge token / 0 raw authToken / 0 uppercase64；
- 未改 EditorBridge auth、capability、manifest 加载；未默认开启、未 T7、未 R2。

## 证据

`Build/r1-native/Evidence/R16/`：
`perf-1|2|3/`、`capture/`、`stale/`、`expiry/`、`shutdown/`、`off/`、`r1-regression/`、`r16-summary.json`、`seam-check.log`。

报告：`spec/native-tools/R1_6_SOCKET_BOUNDARY_PERFORMANCE_REPORT.md`。
