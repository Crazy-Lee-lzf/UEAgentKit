# N0 Native Tool Contract — Task RunState

- 任务：UEAgentKit N0 合同冻结候选 + R1 只读准备审查（不编码 R1/R2/R7）
- 路线：A 线（Native Tool Architecture）
- 模型：`cline-pass/deepseek-v4.1-flash-1017`（非 Fast；会话上下文声明，非运行时探测）
- 工作树：`E:\WorkSpace\UEAgentKit-WT\native-tools-r1`
- 分支：`feature/native-tools-contract-r1`
- 基线：`main` `c5ce3419c6962da49e874586f4e5dbd1457a4d0d`（P12）；本任务开工时 worktree clean
- 日期：2026-10-23
- 状态：**DELIVERED — 待人工评审**。未开始 R1 实现。

## 1. 授权与红线执行情况

| 约束 | 执行 |
|---|---|
| 只在 `native-tools-r1` 工作 | 是（所有写入均在该目录） |
| 不推 GitHub / 不 merge / 不 reset / 不 clean | 是（仅本地 scoped commit） |
| 不删工作树、不动 Reforge / Lyra Golden / UETest | 是（未访问、未修改） |
| 不用 Computer Use / 鼠标键盘注入 / 前台窗口控制 | 是（未使用任何 GUI 工具） |
| 不要求 UE 实例启动 | 是（未启动 UE、未编译） |
| 不与他人共用可变目录 | 是（本 worktree 独占） |
| 不改既有 UE 插件运行代码 | 是（`git status` 仅新增 `spec/native-tools/`） |
| 不开放通用 Apply | 是（合同明确继续 fail-closed） |
| RunState 与证据落位 | `spec/native-tools/`（RunState），`Build/n0-contract/Evidence/`（证据，已 gitignore） |

## 2. 实际执行步骤

1. 只读核验：`pwd` / 分支 / HEAD / `git status --porcelain` / `git worktree list`；确认为 `c5ce341` 基线且 clean。
2. 只读参考：`handoffs/UEAGENTKIT_NEXT_CHAT_HANDOFF_20261023.md`、`UEAGENTKIT_NATIVE_PARALLEL_REVIEW_20261023.md`、`UEAGENTKIT_WORKSPACE_HYGIENE_20261023.md`、`goals/UEAGENTKIT_MAIN.md`（2026-10-23 相关段）。
3. 精查插件源码：`BlueprintGraphLayoutP11Menu.{h,cpp}`、`P8Menu.cpp`、`P9Menu.cpp`（哈希比对确认仅记号差异）、`P9TestOnly.cpp`（build marker/project identity）、`EditorBridge.{h,cpp}`（协议、capability 表、分派、test-only 门禁）、`EditorBridgeGraphHandlers.cpp`、`EditorBridgeAutomationHandlers.cpp`、`BlueprintGraphLayoutApply.{h,cpp}`（revision/fingerprint/事务/读回）、`BlueprintContextAnalysis.cpp`（BuildAssetRevision）、`UEAgentKitEditorModule.cpp`、`UEAgentKitEditor.Build.cs`、`EditorBridgeNavigationHandlers/WriteHandlers` 名称索引。
4. 精查 Python 基线：`tool_registry.py`（122 条定义）、`mcp_graph_layout_preview.py`、`mcp_graph_layout_test_only.py`、`editor_bridge.py`（错误/能力门禁）、`graph_contracts/{canonical,descriptors,graph_patch}.py`、`schemas/{node-descriptor,operation-descriptor}.schema.json`、`spec/{LIVE_EDITOR_BRIDGE,MCP_SERVER}.md`、`spec/graph-contracts/{README,D01_D13_USER_DECISIONS}.md`。
5. UE 5.6.1 头文件核验（本机引擎安装，只读；路径见证据 `02-ue56-public-api-verification.txt`）：`BlueprintActionDatabase.h`、`BlueprintActionFilter.h`、`BlueprintActionMenuBuilder.h`、`BlueprintActionMenuUtils.h`、`BlueprintActionMenuItem.h`、`BlueprintNodeSpawner.h`、`BlueprintNodeTemplateCache.cpp`、`EdGraphSchema.h`、`EdGraphNode.h`、`GraphEditorModule.h`。
6. 交付两份文档 + 本 RunState；证据 6 份写入 `Build/n0-contract/Evidence/`。
7. 自检：文档内全部 ```json 块解析 + 清单规则校验（10/10 通过）；`test_tool_registry.py` 4 项通过；`git diff --check` 与文档一致性复查（见 §5）。

## 3. 交付物

| 文件 | 作用 |
|---|---|
| `spec/native-tools/N0_ARCHITECTURE_CONTRACT.md` | 单一版本化 ToolRegistry、DTO、统一入口、门禁、线程/生命周期、revision/事务/保存/撤销、错误与审计、兼容性、Python 边界、UE API 风险 |
| `spec/native-tools/N0_R1_READINESS_AND_GATE.md` | R1 只读选型（目录 + 上下文双通道）、功能拆分、稳定标识、UE 5.6 自动化清单、预览路径替代顺序、R2 门槛、N0 验收 |
| `spec/native-tools/N0_NATIVE_TOOLS_RUN_STATE_20261023.md` | 本文件 |

未创建/未修改：任何 `Plugin/**`、`src/**`、`tests/**`、`spec/graph-contracts/**`、`spec/LIVE_EDITOR_BRIDGE.md`、`spec/MCP_SERVER.md`、构建脚本。

## 4. 关键决定（摘要）

1. **单一来源**：运行时加载 `native-tools.v1.json`；C++ 启动校验；安全常量编译进 DLL，清单只引用 `gateProfile`。
2. **统一入口**：`native.invoke` + 同一 handler 服务菜单/MCP/CLI；legacy 方法作为 alias 过渡，禁止第二份实现。
3. **readonly 强制**：合同字段 + 运行时断言 + 静态检查三重；read 工具禁止事务/Modify/Dirty/Save。
4. **test-only 冻结**：精确工程路径 + 名称 + `.uproject` SHA-256 + 同目录 build marker 的 DLL SHA-256 + 资产白名单 + 确认短语。
5. **revision 双源**：`package-sha256-v1` 与 `sha1-length-prefixed-graph-state-v1`，写前锁内重算；`guid:` 修订不可写。
6. **事务语义**：单事务、同事务读回、Dirty 确认、失败恢复并 Cancel；Save 永远独立；不提供通用 Redo。
7. **Python 边界**：布局算法留在 MCP/离线；**UE 进程内不再执行 Python**（P11 路径结构性移除），P8/P9 `py.exe` 退役；`UnrealEditor-Cmd` 自动化子进程保留。
8. **R1 选型**：目录通道（`FBlueprintActionDatabase`）+ 上下文通道（`FBlueprintActionContext` + `MakeContextMenu`），共享 descriptor 与 `actionId`；未知 spawner 显式 `unsupported`，不猜 `special`。
9. **文档即契约**：示例 JSON 机器校验；MCP 模式计数冻结（10/45/67/24/102/116）。

## 5. 真实检查结果

| 检查 | 命令/方式 | 结果 |
|---|---|---|
| 文档 JSON 解析 + 清单规则 | `py -3.12 Build/n0-contract/probe_validate_docs.py` | 10/10 块解析；2 条工具条目、1 条 descriptor、2 次**仓库自带 schema 校验**（`node-descriptor.schema.json`、`graph-layout-apply.schema.json`）全部通过；`exit=0` |
| Python 基线（tool registry 合同） | `py -3.12 tests/python/test_tool_registry.py` | `Ran 4 tests ... OK`，`exit=0` |
| 计数核对 | 脚本统计 | capability 字符串 33；`Method == TEXT(...)` 分支 53、去重 48；Python 全量 122；MCP 模式 10/45/67/24/102/116 |
| 文档一致性 | `git diff --check <base>..HEAD` | `exit=0`，无空白错误；工作树 clean |
| 变更范围 | `git diff --name-status <base>..HEAD` | 仅 3 个 `A`（新增文档），无既有文件改动 |
| UE 运行/Automation | — | **未执行**（本轮明确不启动 UE） |
| UE 编译 | — | **未执行** |

已发现的基线不一致（**未修改**，仅记录）：

- `spec/LIVE_EDITOR_BRIDGE.md:37` 与 `spec/MCP_SERVER.md` 相关段落称 “Live + Workflow = 101 Tool”，实测 `tool_names_for_mode(live_editor_enabled=True, workflow_enabled=True)` 为 **102**，`tests/python/test_tool_registry.py` 的 `EXPECTED_ALL_TOOLS` 长度亦为 102。属既有文档漂移，N0 不改外部文档（见 Q6）。

## 6. 证据（`Build/n0-contract/Evidence/`，gitignored，不入库）

| 文件 | 内容 |
|---|---|
| `00-environment-and-git.txt` | 时间、分支、HEAD、clean 状态、UE 版本（5.6.1 / CL 44394996） |
| `01-python-injection-and-process-sites.txt` | `IPythonScriptPlugin`、`CreateProc`、`py.exe`、worker 路径的全部命中及行号 |
| `02-ue56-public-api-verification.txt` | 每个引擎头文件的 SHA-256 + 被引用声明行号 |
| `03-python-baseline-unittest.txt` | tool registry 测试输出与退出码 |
| `05-contract-counts.txt` | capability/分派/Python 计数 |
| `06-json-block-validation.txt` | 文档 JSON 解析、清单规则、仓库 schema 对拍输出 |
| `04-commit-verification.txt` | 提交后核验：`show --stat`、相对基线变更路径、`diff --check` 退出码、工作树状态、文档 SHA-256 |

本地交付 commit：`docs(native-tools): freeze N0 contract candidate and R1 readiness gate`（单 commit，仅含本文档；未 push；确切 SHA 以 `git log -1` 为准）。

## 7. 待人工裁决

N0：Q1–Q8（见合同 §15；其中 Q1 编辑器内彻底无 Python、Q2 运行时清单、Q3 `native.invoke` 并存、Q6 101/102 文档漂移最关键）。
R1：R1-Q1–R1-Q6（见 readiness §11；fixture 内容、第三方 spawner、资产动作归类、引擎版本承诺、性能阈值、是否暴露 MCP）。

## 8. 下一步（未获新指令不做）

1. 人工评审并批准 N0；对 Q1–Q8 给出结论。
2. 批准后才建 R1 独立 worktree（不与本目录共用），按 readiness §4 拆分实现 R1.1–R1.6。
3. R1 自动化（T1–T8）需要一次 UE 5.6 构建 + disposable host；同机一次只允许一个重型 UE 任务。
4. R2 在 R1 合同冻结且 T1–T8 有可复核证据后才讨论；本任务不构成 R2 授权。

## 9. 未做/不可声称

- 未实现任何 native 工具，未生成 `native-tools.v1.json`，未创建 `Resources/` 目录。
- 未运行 UE，未获得任何 `RUNTIME-VERIFIED` 结论（尤其动作数据库 prime 成本、模板缓存上限行为、菜单替代路径）。
- 未修改 Python 布局算法，未改变 MCP 工具数量或模式计数。
- 未把 P7–P11 的历史证据当作 R1 证据。
