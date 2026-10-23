# N0 Native Tool Contract — Task RunState

- 任务：UEAgentKit N0 合同冻结候选 + R1 只读准备审查（review round 2：Preview 零降级修订）
- 路线：A 线（Native Tool Architecture）
- 模型：`cline-pass/deepseek-v4.1-flash-1017`（非 Fast；会话上下文声明，非运行时探测）
- 工作树：`E:\WorkSpace\UEAgentKit-WT\native-tools-r1`
- 分支：`feature/native-tools-contract-r1`
- 基线：`main` `c5ce3419c6962da49e874586f4e5dbd1457a4d0d`（P12）
- 日期：2026-10-23（round 1）/ 2026-10-24（round 2）
- 状态：**round 2 DELIVERED — 待人工评审**。未开始 R1/N1/R2 实现。

## 1. 授权与红线执行情况（两轮合计）

| 约束 | 执行 |
|---|---|
| 只在 `native-tools-r1` 工作 | 是 |
| 不推 GitHub / 不 merge / 不 reset / 不 clean | 是（仅本地 scoped commit） |
| 不删工作树、不动 Reforge / Lyra Golden / UETest | 是（未访问、未修改） |
| 不用 Computer Use / 键鼠注入 / 前台窗口 | 是（未使用任何 GUI 工具） |
| 不要求 UE 实例启动、不做重型构建 | 是（未启动 UE、未编译） |
| 不改既有 UE 插件运行代码 | 是（`git status` 仅 `spec/native-tools/` 文档） |
| 不开放通用 Apply | 是（合同继续 fail-closed） |
| RunState 与证据落位 | `spec/native-tools/`、`Build/n0-contract/Evidence/`（gitignored） |

## 2. Round 1（已完成，commit `3704d58`）

交付：N0 架构合同 v1、R1 readiness v1、RunState v1；证据 6 份。核心决定：单一 registry、`native.invoke`、门禁梯、revision/事务语义、Python 边界、R1 双通道。

## 3. Round 2 实际执行步骤

1. 只读复核：`git status -sb` / HEAD `3704d58` / 分支 / 基线；确认无未提交内容。
2. 精读既有实现并留证：
   - `BlueprintGraphLayoutP11Menu.cpp`（同步 in-process Python、receipt、modal）。
   - `EditorBridge.cpp`（只监听、请求后关连接）、`editor_bridge.py`（只 `create_connection`）→ 证明**无反向通道**。
   - `BlueprintGraphLayoutP11Test.cpp:818-848`（同步 receipt 断言）→ 证明异步化会改变测试形态。
   - `tools/p9_layout_preview_worker.py`、`mcp_graph_layout_preview.py`、`graph_layout_preview.py:61-334` → 证明算法可在进程外用同一函数。
3. 复核 UE 5.6.1 头文件（出站 socket、通知、worker 线程、配置、Ticker），记录 SHA-256 与行号。
4. 交付/修订三份文档：新增迁移与评审决定文件；合同升级 v2（D-01 opt-in、D-09 例外、D-12 修订、D-16 新增、§7/§8.4/§9/§11/§12/§13/§14/§15/§16）；readiness 复审（哈希输入清单、schema 精确兼容、未知/第三方 spawner 策略、T1–T10、worktree/merge、§7 作废旧顺序）。
5. 扩展只读探针：三份文档全部 ```json 块解析、broker 请求/响应/descriptor 规则、跨文档 preview 错误码一致性、Q1 作废标记、旧 S0–S5 作废标记。
6. 运行纯 Python 测试（tool registry 合同）+ 探针；写证据 07/08/09；提交 scoped commit（仅文档）。

## 4. 交付物

| 文件 | 轮次 | 作用 |
|---|---|---|
| `spec/native-tools/N0_ARCHITECTURE_CONTRACT.md` | v2 | 单一 registry（opt-in/非致命）、统一入口、DTO、门禁、线程/生命周期、revision/事务、错误/审计、兼容、Python 边界、Preview 决定 D-16、UE API 风险 |
| `spec/native-tools/N0_R1_READINESS_AND_GATE.md` | v2 | R1 双通道选型、稳定 ID/哈希输入清单、schema 精确兼容、spawner 策略、T1–T10、作废旧 Preview 顺序、worktree/merge、门槛 |
| `spec/native-tools/N0_REVIEW_DECISIONS_AND_PREVIEW_MIGRATION.md` | 新增 | Q1 修订 + broker 协议/时序/安全/迁移 gates/回滚 + Q2–Q8 建议 + R1 复审 delta + 下一轮任务分解 |
| `spec/native-tools/N0_NATIVE_TOOLS_RUN_STATE_20261023.md` | 更新 | 本文件 |

未创建/未修改：`Plugin/**`、`src/**`、`tests/**`、`tools/**`、`spec/graph-contracts/**`、`spec/LIVE_EDITOR_BRIDGE.md`、`spec/MCP_SERVER.md`。

## 5. Round 2 关键决定

1. **Q1 原方案作废**：Editor 菜单不得降级为“只导出 Context”；改为 broker 保留实时 LayoutPlan（Z1–Z6 零降级定义）。
2. **最小新通道**：Editor 作为客户端连 loopback broker（descriptor + `ueak.layoutbroker/1.0` + token/projectHash/session/requestId）；**不改**既有 Bridge 协议；不假设反向请求（已用源码证明不存在）。
3. **异步不阻塞**：game thread 只做快照/校验/展示；socket I/O 在 `FRunnable` worker；5 s deadline、1 MiB 上限、单 in-flight、可取消、终态必写 receipt。
4. **安全**：descriptor 校验（loopback/token/project/schema/算法版本/过期）、请求不含路径/命令、结果重算 `layoutHash` + node 集相等 + revision/fingerprint 复核、token 不进 UI/receipt/日志、重放防护。
5. **迁移**：M0–M6 分阶段，`NativeToolRegistry` 与 `NativeLayoutPreview` 两个独立 flag，默认 off；旧 P11 路径保留到 parity + P10/P11 regression + 回滚演练 + 用户批准。
6. **Q2 例外**：registry 缺失/损坏不得导致 legacy P12 启动失败或功能缺失（`off` 默认非致命）。
7. **R1 复审**：`actionId` 与现有 Python 精确兼容；`descriptorDigest` **当前包含**本地化文本（反直觉但为兼容事实，T2 显式断言）；未知 spawner 不猜 `special`；第三方 spawner 默认不触发模板创建（T10）；R1 用独立 worktree（见 readiness §9.1）。

## 6. 真实检查结果（round 2）

| 检查 | 命令/方式 | 结果 |
|---|---|---|
| 三份文档 JSON 解析 | `py -3.12 Build/n0-contract/probe_validate_docs.py` | 14/14 块解析；`exit=0` |
| 清单规则 / broker 协议规则 | 同上 | 2 条工具条目、4 条 broker 文档（descriptor/request/success/failure）通过；`exit=0` |
| 仓库 schema 对拍 | 同上（`validate_document`） | 2/2 通过（node-descriptor、graph-layout-apply） |
| 跨文档 preview 错误码 | 同上 | 20 个 `native-preview-*` 全部在合同 §11.2 中存在 |
| Q1 作废 / 旧顺序作废标记 | 同上 | 合同含“原建议作废”，readiness 含“已由 review round 2 修订” |
| Python 基线测试 | `py -3.12 tests/python/test_tool_registry.py` | `Ran 4 tests ... OK`，`exit=0` |
| UE 运行/Automation/编译 | — | **未执行**（本轮禁止，无 `RUNTIME-VERIFIED` 结论） |

## 7. 证据（`Build/n0-contract/Evidence/`，gitignored）

| 文件 | 内容 |
|---|---|
| `00-environment-and-git.txt` | 时间、分支、HEAD、clean、UE 版本 |
| `01-python-injection-and-process-sites.txt` | `IPythonScriptPlugin` / `CreateProc` / `py.exe` / worker 路径 |
| `02-ue56-public-api-verification.txt` | R1 相关引擎头文件 SHA-256 + 行号 |
| `03-python-baseline-unittest.txt` | tool registry 测试输出 |
| `05-contract-counts.txt` | capability/分派/Python 计数 |
| `06-json-block-validation.txt` | round 1 文档探针输出 |
| `07-preview-ipc-facts.txt` | **本轮**：插件只监听、Python 只连接、P11 in-process Python、`build_preview` 复用点 |
| `08-preview-broker-header-checks.txt` | **本轮**：出站 socket/通知/worker/配置/Ticker 头文件 SHA-256 + 行号 |
| `09-json-validation-v2.txt` | **本轮**：三份文档 14 块 + broker 规则 + 跨文档码校验 |

## 8. 已记录的既有问题（未修改）

- `spec/LIVE_EDITOR_BRIDGE.md:37` 称 Live+Workflow = 101 Tool；实测与 `test_tool_registry.py` 均为 **102**。建议单独 doc-only commit 修正（Q6）。
- `descriptorDigest` 目前包含本地化文本：改文案会改 digest。R1 采用兼容优先，并在 T2 中显式测试；若要稳定需 descriptor 1.1（R1-Q7）。

## 9. 待人工裁决

- N0：Q1（修订方案，最关键）、Q9 broker 启动/授权、Q10 Preview 可见范围、Q11 回退策略、Q12 展示形态、Q6 文档漂移修正时点。
- R1：R1-Q1–R1-Q9（fixture、第三方 spawner、资产动作、引擎版本、性能阈值、MCP 暴露、digest 本地化、未受信模板开关、合并前提）。

## 10. 下一步（未获新指令不做）

1. 人工评审 round 2 三份文档，确认 Q1 修订与 Q9–Q12。
2. 批准后由协调者把 N0 docs-only 分支合并到 main；R1 从合并后的 main 建独立 worktree（readiness §9.1）。
3. R1 实现按 `N0_REVIEW_DECISIONS_AND_PREVIEW_MIGRATION.md` §13 的 10 步任务分解执行；自动化 T1–T10 需一次 UE 构建 + disposable host。
4. N1（broker）与 R1 可并行设计，但 UE 回归必须串行；R2 仍需独立授权。

## 11. 未做/不可声称

- 未实现 broker、未写任何 C++、未创建 `native-tools.v1.json`、未改任何插件/ Python 运行代码。
- 未运行 UE、未编译、未获得任何 `RUNTIME-VERIFIED` 结论（尤其是异步菜单、`FSocket::Connect`、通知可用性、模板缓存行为）。
- 未修改旧 Preview 路径；未删除 `IPythonScriptPlugin`/`py.exe`；未改变 MCP 工具数量与模式计数。
