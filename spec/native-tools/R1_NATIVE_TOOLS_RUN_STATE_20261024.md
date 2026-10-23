# RunState — R1 原生节点注册表切片（2026-10-24）

- 轮次：R1.0 / R1.1 / R1.2 首个实现切片；R1.5 部分。**停止于 R1.2，未进入结构 R2 / 通用 Apply。**
- 工作树：`E:\WorkSpace\UEAgentKit-WT\r1-node-registry`
- 分支 / 基线：`feature/r1-node-registry` @ `b423b87`（N0 合同提交）
- 模型（已核验）：`cline-pass/deepseek-v4.1-flash-1017`，`reasoningEffort=max`，非 Fast；证据为 token-monitor `request-details.jsonl` 中本 session 记录。
- UE：`5.6.1`（CL `44394996`），`E:\EPICGAME\UE_5.6`；宿 `Build/r1-native/R1AutomationHost`（gitignored）。

## 本轮完成

1. **R1.0**：`INativeNodeRegistry` / `FNativeNodeRegistry`；`off|shadow|active`，默认 `off`；幂等初始化/关闭；game-thread 断言；清单缺失仅禁用 native。
2. **R1.1**：`FBlueprintActionDatabase` 全量枚举；trust 分类；`unsupported[]`（64 上限）+ 全量原因直方图；稳定排序；`actionDatabaseFingerprint`、`pluginSetHash`、引擎/插件/会话身份；会话级覆盖冻结，保证同会话两次调用指纹一致。
3. **R1.2**：`discoveryKey` 五字段、`actionKind` 冻结映射（未命中即 unsupported）、payload、`pins[]`（7 字段 pin type）、`constraints`、`graphKinds/graphSchemas`、`risk/capability/trustLevel`；`actionId` / `descriptorDigest` 与 `canonical.py` 逐字节一致；本地化字段禁入 discoveryKey；改 title → actionId 不变/digest 变已入测。
4. **R1.5 部分**：`native-tools.v1.json` 清单 + 加载校验 + 2 个只读影子工具 + bridge `native.invoke`（仅在 ready 时宣告 capability）；未接 MCP/菜单。
5. **测试**：5 个 UE Automation（T1/T2/T5/生命周期/影子工具）+ 13 个 Python 离线测试；200 条金值（160 条真实 UE 导出 + 40 条合成边界）；512 条真实导出通过仓库校验器与 JSON Schema。

## 实测结果（可复核）

| 项 | 值 |
|---|---|
| 构建 | Succeeded（VS 2022 14.44.35228；仅非首选编译器 / 既有 IKRig 依赖警告） |
| UE 自动化 | 5 succeeded / 0 failed / 0 not run；3.83 s |
| spawner / owner | 38325 / 3071 |
| descriptor | 512（available 762，`truncated=true`，`complete=false`） |
| unsupported | 37547；`template-budget-exceeded=25823`、`unknown-spawner=11638`、`untrusted-spawner-excluded=80`、`action-id-collision=6` |
| 模板 / 引脚 | created 768、cached 0、pinsAllocated 768；首次枚举 831–979 ms |
| 指纹 | `sha256:b09fb1387e188e02`（4 个独立 Editor 进程一致） |
| pluginSetHash | `sha256:1d034d339ee271ad` |
| DLL | `sha256:74dedb8f40023b3d7448dd0d32df8998882b37cabfd106596eca5637819db522` |
| 只读证据 | dirty 0→0、`/Game` packages 0→0、assets 0→0、undo queue 0→0、undo count 0→0（空宿主，dirty 断言平凡成立） |
| 离线测试 | 13/13 OK；512 条导出 descriptor 全部通过 `validate_node_descriptor` + JSON Schema |
| 金值 | 200 条；sha256 `97942712580b08a109cda2d716de46d4fa29a237bf3a64bb8e308e5e649f4269` |

证据目录：`Build/r1-native/Evidence/`（`evidence-summary.json`、`catalog-export.json`、构建/自动化日志、`AutomationReport-20261024-013748/index.json`、`environment-*.json`）。

## 未完成 / 明确缺口

- T3 上下文过滤、T6 cursor 分页、T7 菜单绑定、T9 内存测量、T10 假 spawner 注入：未实现。
- T4 跨进程只做了 4 进程指纹观测与金值跨进程复验，无独立双进程 harness。
- T5 宿主无 `/Game` 资产，dirty/revision 未在真实加载资产上验证。
- R1.5 socket 路径未用客户端驱动；清单损坏非致命性未运行时演示；DTO schema 文件解析留给 N1。
- `graphKinds` 目前全部为 `["function"]`（模板图种类），不是可用性承诺；R1.3 才提供权威上下文。
- 为取 pins 在 768 个**瞬态模板节点**上执行 `AllocateDefaultPins()`；未写资产/未开事务/未 Save，但严格意义上创建了 UObject，需复核是否接受该 N0-D05 transient 例外。

## 禁止项遵守

未 push / merge / rebase / reset / `git clean` / 删除既有文件；未改 `main` 或其他 worktree；未触碰 Reforge / Lyra 原始 / UETest；未使用 Computer Use / SendInput / 前台窗口 / 截图；未改 P11 Preview；未移除 `IPythonScriptPlugin` / P8 / P9；未开放通用 Apply；未实现 R2；MCP 工具计数未变。

## 下一步（需新指令）

1. 含 `/Game` fixture 的 disposable 宿主，补 T5 真实 dirty/revision。
2. R1.3 上下文过滤 + 用上下文图 schema 修正 `graphKinds` 语义。
3. T4 双进程导出对拍脚本 + 插件集差异诊断。
4. 决定 R1.5 形态（补 socket 探针 / 或保留 shadow 内部形态）。
5. 人工评审 N0 Q1/Q9–Q12 与本报告 §8 偏差后再谈后续阶段。
