# C-LAYOUT P11 — Editor Menu / Selection / Incremental 验收报告（2026-10-23）

> 结论：**P11 技术主验收通过（headless，无 Computer Use）**。菜单的注册、真实 ToolMenus
> 生成条目、以及由该条目真实触发的 handler 均已用 UE5.6 C++ Automation 验证；真人视觉点击
> 仍为 **NOT VERIFIED / 人工可选**。生产通用 Apply 未开放。

## 1. 逐项状态

| 项目 | 状态 | 证据 |
|---|---|---|
| 插件真实构建（UE5.6.1） | **PASS** | build 9，DLL `sha256:7EB2DB91…8E6A`，3,796,992 B |
| P11 fixture（15 节点、Data 1→2、Exec 2→1、Branch、Comment） | **PASS** | `p11-fixture-expected.json` |
| 菜单注册（ToolMenus 动态 Section） | **PASS** | `P11MenuContract` |
| 生成菜单包含条目（Node 上下文） | **PASS** | 同上，真实 `GenerateMenu` |
| 生成菜单包含条目（Pin 上下文） | **PASS** | 同上 |
| 空白 canvas 无条目 | **PASS** | 同上 |
| 错误 Host / 缺 marker 拒绝 | **PASS** | `IsAvailableFor` 断言 |
| 条目真实 Action 执行 handler + 只读 Plan | **PASS** | receipt `layout:eb3f6282b8414a43` |
| Handler 不改资产（dirty/Save/Compile） | **PASS** | receipt dirty before/after=false |
| Selection 真实移动（scope>0） | **PASS** | moved 2 / scope 5 |
| Selection 作用域外不动 | **PASS** | `selectionOutsideUnchanged=true` |
| Incremental 真实移动（scope>0） | **PASS** | moved 2 / scope 5 |
| Incremental fixed 节点不动 | **PASS** | `incrementalFixedUnchanged=true` |
| 严格 no-op | **PASS** | `changed=false`，包干净 |
| Undo / Redo / Save | **PASS** | 两种 scope 各一次 |
| 第二独立进程 Reload | **PASS** | 位置/拓扑/DLL 一致，包干净 |
| Python 全量（本 worktree） | **PASS** | 1241 tests / 17 skipped / OK / 107.329 s |
| 真人 GUI 可见/点击 | **NOT VERIFIED** | 人工可选，见 §7 |
| Slate 原生截图 | **未做** | 本阶段不需要 |

## 2. 基线与隔离

- P10 `graph-layout-integration` @ `d982d2474aca`，clean；本 worktree 从该 commit 创建。
- 分支 `feature/graph-layout-p11-editor`，worktree `graph-layout-p11-editor`。
- P9 `510deb0a`、Codex `e32d2fe`、corpus `96af114`、main `23bdbd6` 均未写。
- 只写 `Build/P11AutomationHost`（gitignored）；未对 Lyra/Reforge/正式工程 Apply。

## 3. 代码变更

| 文件 | 说明 |
|---|---|
| `BlueprintGraphLayoutP11Menu.h/.cpp` | ToolMenus 动态 Section；仅 Node/Pin 上下文加条目；真实 `FToolUIAction`；只读 receipt；测试用对话框抑制 |
| `BlueprintGraphLayoutP11Test.cpp` | Fixture / Menu / Scope / Reload 四个 Automation 测试 |
| `UEAgentKitEditorModule.cpp` | P11 菜单生命周期 |
| `UEAgentKitEditor.Build.cs` | 增加 `ToolMenus`、`PythonScriptPlugin` |
| `tests/python/p11_build_scope_probe.py` | 从真实导出构建 selection/incremental ApplyRequest |
| `tools/p11_open_fixture_editor.py` | 仅人工 GUI 抽查用的可选打开脚本 |

## 4. 菜单：为什么 P9 没显示，P11 怎么验证

- UE5.6 节点菜单由 ToolMenus 生成（`GraphEditor.GraphNodeContextMenu.<Class>`，公共根
  `GraphEditor.GraphContextMenu.Common`）。Selected-node extender 只在 `GraphNode`/`GraphPin`
  非空时执行。
- P9 使用的扩展段名 `GraphEditorGraphContextMenu` 在 UE5.6 源码中 **零出现**，因此条目被静默丢弃。
- P11 在公共根注册动态 Section，在 `AssembleMenu` 生成时按 context 决定是否添加条目。
- 测试用真实 `UEdGraph` / `UEdGraphNode` / `UEdGraphPin` / `UGraphNodeContextMenuContext`
  调用 `UToolMenus::GenerateMenu`，检查 Section `UEAgentKitGraphLayoutP11` 与条目
  `UEAgentKitAutoArrangePreviewP11`；并对生成条目调用真实的 `TryExecuteToolUIAction`。
- 结果：Node 与 Pin 上下文均出现条目并成功产出只读 Plan；空白 canvas 无 Section；错误
  project/marker 返回 false。

## 5. 真实 Scope 结果

Fixture：`/Game/UEAgentKitP11/BP_LayoutP11` 与 `BP_LayoutP11Inc`，各 15 节点、2 个 Reroute、
1 个 Comment，含 Data 1→2、Exec 2→1、IfThenElse。

- Selection：scope 5，moved 2；目标位置精确到达，作用域外不变，拓扑 hash 不变。
- Incremental：scope 5，moved 2；fixed 节点不变，拓扑 hash 不变。
- 两者均 Undo → 恢复原图、Redo → 回到目标，随后显式 Save。
- 严格 no-op：手构“当前位置 + moved=[]”的边界 Plan，`changed=false` 且包保持干净（明确标注为
  边界用例，不是算法输出）。
- 第二进程 Reload：selection/incremental 的位置与拓扑、DLL hash 全部一致，包干净。

## 6. 安全门禁

未放宽任何 P10/P9 门禁：Knot pin 证据、0 输入/环/类型不符仍 fail-closed；Revision/Dirty/
PIE/SIE/active transaction/asset/confirmation 检查保留；handler 仅只读 Preview，不存在
Apply/Save/Compile 调用；Comment move guard 未改。

## 7. 未证明 / 限制（不可误报）

1. **真人可见与点击 NOT VERIFIED**：headless 自动化证明注册、生成、Action 执行，不等于人眼
   看到菜单或鼠标点击。此前合成右键连原生菜单都未弹出，已按新政策停止该路线。
2. **无 Slate 截图**：本阶段不以截图作为证据，也未声称最小化/无显示设备截图可用。
3. **Worker 执行方式变更**：headless host 下 `py.exe` 与 `cmd /C` 子进程均返回 exit 2 且无
   输出；handler 改为同一进程内通过 `IPythonScriptPlugin` 执行同一 worker 脚本，receipt
   `workerMode=in-process-python`。算法与输出文件未变，但“外部 py 进程”路径在本 host 未验证。
4. 未开放生产/通用 Apply；未在 Lyra/Reforge 写入；未做 P12、未 push。
5. Selection/Incremental 只验证了受控 fixture 的一个正例；更大 scope、复杂 Comment 区域仍需
   后续覆盖。

## 8. 复现

```powershell
# 构建
scripts\BuildPlugin.ps1 -Method UAT -PackageDirectory Build\P11PluginBuild

# Fixture -> Plan -> Scope -> Reload -> Menu（各自独立进程）
UnrealEditor-Cmd.exe Build\P11AutomationHost\P11AutomationHost.uproject `
  -ExecCmds="Automation RunTests UEAgentKit.GraphLayout.P11FixtureContract" `
  -TestExit="Automation Test Queue Empty" -unattended -nopause -nosplash -nullrhi -NoSound -log
py -3.12 tests\python\p11_build_scope_probe.py --evidence Build\P11AutomationHost\Evidence
# P11ScopeContract / P11ReloadContract / P11MenuContract 同上

# Python
py -3.12 scripts\RunPythonTests.py full
```

## 9. 证据清单（`Build/P11AutomationHost`，gitignored）

| 文件 | 字节 | SHA256 |
|---|---|---|
| p11-context.json | 93,595 | `775937EC…94EEB` |
| p11-preflight.json | 6,708 | `342749BD…F01F` |
| p11-inc-context.json | 93,787 | `4E919A04…A77A` |
| p11-inc-preflight.json | 6,714 | `7490C663…1DC6` |
| p11-fixture-expected.json | 1,093 | `CCEE66B8…AAAF` |
| p11-selection-request.json | 9,476 | `853F1182…DEAA` |
| p11-incremental-request.json | 9,484 | `87607BBB…28B1` |
| p11-plan-summary.json | 1,976 | `4E3F70BB…4837` |
| p11-scope-result.json | 4,099 | `43D7FDC4…B7C1` |
| p11-reload-result.json | 690 | `15B33E73…F227` |
| menu-invocation.json | 1,023 | `226AC2E1…5347` |
| preview.json (menu) | 20,990 | `BE7DF292…0A44` |
| DLL | 3,796,992 | `7EB2DB91…8E6A` |

日志：`p11-python-full.log` `F350C624…16F2`；`p11-buildplugin-9.log` `E7B7E0AA…FD77`；
`p11-ue-fixture-final.log` `082C17D3…A50E`；`p11-ue-scope-final.log` `A1DB4A93…FB03`；
`p11-ue-reload-final.log` `3A9BE42D…8C3A`；`p11-ue-menu-final.log` `66517485…CE9C`；
`p11-plan-probe-final.log` `93FFC834…13B0`。

> 所有 PASS 均指本 worktree 的真实 headless 运行；GUI 人眼可用性需用户可选的一次人工抽查，
> 生产 Apply 仍未开放。
