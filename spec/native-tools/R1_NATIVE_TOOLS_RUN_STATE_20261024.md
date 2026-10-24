# RunState — R1 原生节点注册表（2026-10-24，R1.4 离线更新）

- 当前轮次：**R1.4 / T6 游标分页（代码 + 离线契约完成；UE 编译/运行时 PENDING）**。R1.3 已 RUNTIME-VERIFIED；R1.5 影子工具仍为部分。**停止于 R1.4 离线，未进入 R2 / T7 / T8 / T9 / 通用 Apply。**
- 工作树：`E:\WorkSpace\UEAgentKit-WT\r1-node-registry`
- 分支 / 起点：`feature/r1-node-registry`，R1.4 从 `bcd3d45` 继续；本地提交未 push。
- 模型（已核验）：`cline-pass/deepseek-v4.1-flash-1017`，`reasoningEffort=max`，非 Fast；同一 DSH session `session-8df1c508-…`，未切换 Provider/模型。
- 并行 lane：`layout-broker-m1`（M3.4）独立 worktree；本 lane **未运行任何 UE 构建/Editor/UBT/UBA**，重型槽位未占用。
- UE：`5.6.1`（CL `44394996`）；宿 `Build/r1-native/R1AutomationHost`（gitignored）。

## 当前状态

### 已实现

1. **R1.0**：只读注册表生命周期、`off|shadow|active`（默认 off）、非致命清单、game-thread 断言。
2. **R1.1**：动作数据库目录、trust 分类、有界 unsupported + 全量直方图、稳定排序、fingerprint/pluginSetHash/identity、会话级覆盖冻结。
3. **R1.2**：descriptor 派生 + Python canonical 逐字节 `actionId`/`descriptorDigest`；200 金值；pins/constraints/graphKinds。
4. **R1.3（本轮）**：
   - `FNativeNodeRegistry::FilterContext`：对已加载 Blueprint 的 graph/node/pin 做只读上下文过滤；palette/node/pin 三种上下文；
   - 资产/图/节点/pin 全部 fail-closed（未加载资产、未知图、未知节点、未知 pin、非 K2）；
   - 使用引擎公开 `FBlueprintActionFilter`，只评估目录已描述的 512 条 official action；不枚举不可信 spawner、不触发其模板；
   - 上下文动作复用目录 `actionId`/`descriptorDigest`，按 actionId 排序，`actionSetFingerprint`（`ueak.node-action/1.context`，本地域）；
   - `graphKinds` 明确为模板提示，context `semantics` 标注权威来源；
   - 新增影子工具 `graph.nodeRegistry.filterContext`（readOnly、bridge `native.invoke`，默认 off 时不可见）。
5. **T3/T4/T5/T10**：
   - T3：palette/node/exec pin/data pin 正例；未知图/pin/资产/非 K2 负例；确定性；与目录 ID 一致；
   - T4：两个独立 `UnrealEditor-Cmd` 导出逐 actionId/digest/fingerprint/pluginSetHash/直方图/context 指纹对拍；
   - T5：真实 `/Game/R1Native/BP_R1ContextFixture`，测量 dirty/磁盘 revision/图指纹/事务/AssetRegistry 前后不变；
   - T10：不可信 spawner 在模板访问前拒绝，`templateCached=no`；无数据库注入。
6. **测试**：7 个 UE Automation；26 个 Python 离线测试（含 200 金值、512 导出 schema、context、T4、源码守卫）。

### 实测数据

| 项 | 值 |
|---|---|
| 构建 | **Succeeded**（VS 2022 14.44.35228；仅既有的非首选 MSVC / IKRig 警告） |
| 最终 DLL | `sha256:56d01e7d0103e9ec27eb81cf339f5eeead73888aa6c70f86a99a146cd49200ff` |
| 先前 DLL（R1.2 实测） | `sha256:15480cb8b2bf5edc1363e9d1ad208ead0241d94160f01e07e038b6f9d6fcfa05`（更早的 `74ded…` 作废） |
| UE 自动化 | run A/B 各 **7/7 Success，0 failed，0 not run** |
| spawner / owner | 38325 / 3071 |
| descriptor | 512（available 762，`truncated=true`，`complete=false`） |
| unsupported | 37547；budget 25823 / unknown 11638 / untrusted 80 / collision 6 |
| 模板/引脚 | created 768、cached 0、pinsAllocated 768；目录首次 926–1057 ms |
| 指纹 | `sha256:b09fb1387e188e02`（A/B 及此前多进程一致） |
| pluginSetHash | `sha256:1d034d339ee271ad` |
| context | described 512 / evaluated 512 / rejected 215（exec）；palette 443→128、node 431→128、exec 297→128、data 12→12；~20 ms（目录预热后） |
| context fingerprints | palette `sha256:f7a05fa9ba7b345d`、node `sha256:ded737ba727761a1`、exec `sha256:13e68f6732b07f76`、data `sha256:d852fd4e0f689f61` |
| T5 | dirty 0→0、`/Game` packages 1→1、assets 1→1、undo queue/count 0→0、revision `sha256:945c…` 前后相同、run 内图指纹相同 |
| 离线 | **26/26 OK**；512 导出 descriptor 通过 `validate_node_descriptor` + JSON Schema |
| 金值 | 200 条；仓库 blob sha256 `97942712580b08a109cda2d716de46d4fa29a237bf3a64bb8e308e5e649f4269`（LF；工作区 CRLF） |

证据：`Build/r1-native/Evidence/`（`evidence-summary-r13.json`、`catalog-export.json`、`context-export.json`、`t4/run-a|run-b`、`AutomationReport-20261024-022230|022249`、`build-r13-final.log`、`environment-*.json`）。

## 明确缺口 / 未完成

- **覆盖**：上下文仅覆盖 catalog 已描述 512 条（`notDescribedActions=37813`），未提高 768 模板预算；`complete=false` 如实声明。
- **palette 语义**：使用与 `MakePaletteMenu` 相同的 `FBlueprintActionFilter` 配置直接评估，未调用 menu builder（避免遍历不可信 spawner 模板）；菜单分组/文本/排序不在范围。
- **T6**：只有 `maxItems/truncated/totalAvailable`，无 cursor 分页；palette/node/exec 均命中 128 上限。
- **T7**：未接 ToolMenus。
- **T8**：进程内工具/参数/上下文负例已测；`native.invoke` socket 路径未用客户端驱动。
- **T9**：目录/上下文耗时已观测，未测模板缓存峰值内存。
- **T4 剩余**：未覆盖蓝图 recompile 后 actionId 稳定性。
- **T10 剩余**：未构造“会抛异常的第三方 spawner”注入；以真实临时 spawner 证明 fail-closed + 不建模板。
- **`graphKinds`**：仍是模板图提示（实测全 `["function"]`），不是可用性承诺。
- **瞬态模板**：为 pins 在 768 个 transient 模板节点上调用 `AllocateDefaultPins()`；未写资产/未开事务/未 Save，但严格意义创建了 UObject，需复核 N0-D05 transient 例外。
- **R1.5**：清单损坏非致命性未运行时演示；DTO schema 文件解析留给 N1。

## 禁止项遵守

未 push / merge / rebase / reset / `git clean` / 删除既有文件；未改 `main` 或其他 worktree；未触碰 Reforge / Lyra 原始 / UETest；未使用 Computer Use / SendInput / 前台窗口 / 截图；未改 P11 Preview；未移除 `IPythonScriptPlugin` / P8 / P9；未开放通用 Apply；未实现 R2；MCP 工具计数未变。

## R1.4 / T6（离线完成，UE PENDING）

- **实现**：`pageSize`（1–256）+ 不透明 `v1.<payload>.<hmac>` 游标；payload 绑定 kind/queryHash/setFingerprint/epoch/offset/total/pageSize/iat/exp；HMAC-SHA256（per-session key，128-bit 熵，仅内存）；TTL 900 s；token ≤4096。
- **分页**：catalog 512 已描述 descriptor；context match set（实测 palette 443 / node 431 / exec 297 / data 12）；按 actionId 排序，逐页无重无漏；最后一页 `exhausted=true`；`catalogTruncated=true / catalogAvailable=762` 与分页耗尽分开。
- **fail-closed**：tampered/malformed/expired → `native-schema-invalid-arguments`；query kind/身份变化 → `native-schema-invalid-arguments`；set/epoch/total 变化 → `native-gate-revision-conflict`；pageSize 不匹配/offset 越界 → invalid。重放同游标幂等（只读），不宣称防重放。
- **兼容**：未传 `pageSize/cursor` 时旧 `maxItems` 结果完全不变，无 `pagination` 字段；descriptor/schema/actionId/digest/200 金值未改。
- **离线测试**：**47/47 OK**（新增 21：5 个 token 金值、512 真实导出 ID 无重无漏、空/1/末页、越界、查询/stale/epoch/过期/篡改/重放、无 IO/eval 与常量守卫）。
- **未验证**：C++ 未编译、未在 UE 运行；`R1CursorVectors` / `R1Pagination` 已写但 **PENDING**。DLL 仍为 R1.3 的 `sha256:56d01e7d0103e9ec27eb81cf339f5eeead73888aa6c70f86a99a146cd49200ff`（本轮无构建）。
- **覆盖**：仍为 512 described / 762 available / 38,325 discovered；未提高 768 模板预算，未触碰 trust/template 边界。

## UE 重型槽位

本 lane 本轮未启动 UE；**未占用重型槽位**，由 M3.4 lane 使用。R1.4 的 UE 验证必须等用户/协调者释放槽位后执行，命令见 R1.4 报告 §7。

## 下一步（需新指令）

1. **先做 R1.4 UE 编译 + `R1CursorVectors`/`R1Pagination`**（需重型槽位），编译失败即 BLOCKED；通过后更新报告为 RUNTIME-VERIFIED 并记录新 DLL hash。
2. T7 菜单绑定 / T8 socket 驱动 / T9 内存测量。
3. T4 recompile 场景；T10 异常注入。
4. 评审 palette 镜像过滤、`graphKinds` 语义、cursor 错误码是否需要独立表项。
5. 人工评审 N0 Q1/Q9–Q12 与本报告偏差后再谈后续阶段；不进入 R2。
