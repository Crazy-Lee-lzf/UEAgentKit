# UE Agent Kit 0.7.0 本地 Release 交接

更新时间：2026-08-03

## 1. 本次目标

本次将本地 `main` 的 Realtime Foundation、注册式 Live Editor Write、Schema v3 Memory/Context MVP、分帧 Batch Task、持久化 Change Set 和大型项目性能方案收口为 **UE Agent Kit 0.7.0**。

本次只处理本地仓库和本地 Release 产物：

- 不 Push。
- 不 Fetch 远端作为发布前提。
- 不创建 Tag。
- 不创建 GitHub Release。
- 不修改任何远端分支或远端资产。

## 2. 0.7.0 已纳入范围

### Realtime Foundation

- `ue_get_editor_context`：聚合 Editor、World、Selection、Open Assets、Dirty Packages、Blueprint Graph Selection、Compile Errors 和 Output Log Cursor。
- `scanCurrentWorld`：只扫描当前已加载 World，Actor/Component 处理使用约 2 ms 单帧预算。
- Batch Task：进度、取消、超时、失效、部分结果和受限详情分页。
- Change Set schema v2：持久化 Task、Editor Session、Operation、Asset、Transaction、Save Receipt 和 Validation 生命周期。

### Live Editor Write

- 注册式 `OperationSpec` 和资产域执行器。
- 12 个受控 Operation：Data Asset 标量/引用/Struct/Array/Set/Map，Material Instance Scalar/Vector/Texture/Static Switch，DataTable Cell/RowFields/Add/Remove/Rename。
- 统一 Transaction/Evidence、No-op、失败恢复、精确 Undo/Discard、授权单资产 Save、Independent Verify 和可恢复 Journal。
- 继续拒绝任意 UObject Method、Shell、SQL、Python、Console、自动保存和 Save All。

### Schema v3 Memory

- 任意深度 Knowledge Tree。
- Active Work 与长期知识分离。
- 0–4 级渐进式披露和 Server Token Budget。
- 按需 Evidence。
- 五个高层 Tool：`ue_memory_get_context`、`ue_memory_expand_node`、`ue_memory_get_evidence`、`ue_memory_update_knowledge`、`ue_memory_update_work`。
- 保留 0.6.0 低层 Memory API 的兼容读取。

### 大型项目性能方案

完整方案：[`../PERFORMANCE_TEST_PLAN.md`](../PERFORMANCE_TEST_PLAN.md)

最终约束：

- 物理测试工程放在 E 盘 SSD。
- 目标 160–180 GB，项目硬上限 200 GB。
- 不需要现在创建真实 500 GB 工程。
- 500 GB 商业项目通过物理工程、500k Asset/10m Reference 逻辑库和 HDD 模拟组合覆盖。
- 每组关键测试分别运行 `NativeSSD` 和 `SimulatedHDD50`。
- `SimulatedHDD50` 使用 50 MB/s 顺序读写上限、8/10/15 ms 文件寻道档位和队列深度 1。
- 首次知识库和明确批处理允许较慢；普通搜索、变量修改、少量 Blueprint 节点修改、Compile、Undo 和单资产 Save 必须接近修改代码的体验。

## 3. 版本与 Tool 数量

```text
VersionName          0.7.0
Plugin Version       27
Unreal Engine        5.6
Target Platform      Win64

Offline              5 Tool（Memory 17）
Live                27 Tool（Memory 39）
Workflow            31 Tool（Memory 43）
Combined            53 Tool（Memory 65）
```

发布状态协议统一报告：

```text
publishedVersion = 0.7.0
developmentLine  = 0.7.0
```

下一开发线：

```text
0.8.0-dev  Context/Analysis、Blueprint 常用编辑、大型项目性能基准
0.9.0      Shared Knowledge Service、协作与冲突感知
```

## 4. 本地分支与 Worktree

```text
E:\WorkSpace\UEAgentKit
  feature/live-editor-realtime-io

E:\WorkSpace\UEAgentKit-Main
  main

E:\WorkSpace\UEAgentKit-MemoryContext
  feature/memory-context
```

本次 Release 源从本地 `main` 生成。两个功能分支继续保留，发布完成后从 `main` 同步，不删除 Worktree。

远端 SSH 之前不可用，本次按用户要求不处理远端。

## 5. 已完成验证

发布版本源、文档和协议更新后已通过：

```text
Ruff                         passed
Python unittest              334/334 passed
Portable release validation  passed
Schema meta validation       3/3
Example Patch validation     16/16
UTF-8 without BOM            passed
CRLF audit                   passed
Git diff --check             passed
```

Python 完整测试实测：

```text
Ran 334 tests in 26.499s
OK
```

正式 `BuildRelease.ps1` 也重新执行并通过了 Ruff、334 项测试、Schema 和示例校验，随后在 UAT BuildPlugin 前被本机 C++ 工具链检查阻止。

## 6. 当前唯一阻塞：本机缺少 MSVC 编译器

Visual Studio Installer 当前记录：

```text
Display Name       Visual Studio Community 2026
Installation Path  D:\Program Files (x86)\Microsoft Visual Studio\2022\Community
Installation       incomplete / cancelled
MSVC directory     VC\Tools\MSVC\14.50.35717
cl.exe             not present
clang-cl.exe       not present
```

Reforge 的历史编译配置证明该工具链此前确实存在并被 UE5.6 使用：

```text
E:\WorkSpace\Reforge\.vscode\compileCommands_Reforge.json
  D:\Program Files (x86)\Microsoft Visual Studio\2022\Community\
  VC\Tools\MSVC\14.50.35717\bin\Hostx64\x64\cl.exe
```

Reforge 的 `.vsconfig` 同时要求：

- `Microsoft.VisualStudio.Component.VC.Tools.x86.x64`
- `Microsoft.VisualStudio.Component.VC.14.38.17.8.x86.x64`
- `Microsoft.VisualStudio.Component.VC.Llvm.Clang`
- `Microsoft.VisualStudio.Component.Windows11SDK.22621`
- Native Desktop 与 Native Game 工作负载

当前 Visual Studio Installer 仅保留相关 `_package.json` 清单和官方下载地址，28.5 MB 的 HostX64→TargetX64 编译器 VSIX 载荷本身不在本地缓存中，因此不能通过路径修正或直接复制恢复。

`BuildRelease.ps1` 的失败信息：

```text
An x64 MSVC toolchain was not found.
Pass -MsvcToolsRoot or set UEAK_MSVC_TOOLS_ROOT.
```

因此本次不能声称生成了经过重新编译的：

```text
UEAgentKit-0.7.0-UE5.6-Win64.zip
```

不能直接复用旧 DLL，因为旧二进制中的版本字符串仍属于 0.6.0 / 0.7.0-dev，和 0.7.0 协议不一致。

## 7. 本次本地 Release 产物

在缺少编译器的前提下，本次生成可验证的源码 Release：

```text
Output\Release\0.7.0\
├─ UEAgentKit-0.7.0-Source.zip
├─ ue_agent_kit-0.7.0-py3-none-any.whl
├─ SHA256SUMS.txt
├─ release-manifest.json
└─ UEAgentKit-0.7.0-LocalReleaseBundle.zip
```

源码 ZIP 由 `git archive` 从干净 Release Commit 生成，因此不包含：

- `.git`
- `.venv`
- `.local`
- `Build`
- `Output`
- `Backups`
- `Intermediate`
- `Saved`
- `DerivedDataCache`
- 日志、缓存和本地配置

总 Bundle 包含源码 ZIP、Python Wheel、SHA-256、Manifest、中文交接文档和中英文发布说明。

## 8. 补齐正式 Win64 插件包

完成 Visual Studio 的“使用 C++ 的桌面开发”安装，并确认存在 x64 `cl.exe` 与 Windows SDK 后，在干净 `main` 上执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\BuildRelease.ps1 `
  -EngineRoot E:\EPICGAME\UE_5.6 `
  -PythonExecutable E:\WorkSpace\UEAgentKit\.venv\Scripts\python.exe
```

通过后应得到：

```text
UEAgentKit-0.7.0-UE5.6-Win64.zip
ue_agent_kit-0.7.0-py3-none-any.whl
SHA256SUMS.txt
release-manifest.json
```

需要重新确认：

- UAT BuildPlugin passed。
- ZIP 内存在 `Binaries\Win64\UnrealEditor-UEAgentKitEditor.dll`。
- ZIP 不含 PDB、Intermediate、Saved、DerivedDataCache 和 HostProject。
- `UEAgentKit.uplugin` 的 `VersionName` 为 0.7.0、`Version` 为 27。
- Release Manifest 的 Git Commit、文件大小和 SHA-256 正确。

## 9. 后续开发入口

性能实现建议新建：

```text
feature/performance-benchmarks
```

首批工作：

1. 统一阶段 Timing Envelope 和 JSON 报告。
2. Reforge 日常查询与 Live Write 基线。
3. DarkRuins 真实 UE5.6 中型样本只读基线。
4. 500k Asset / 10m Reference 逻辑数据库。
5. 50 GB → 100 GB → 160–180 GB 物理 Fixture 分阶段生成。
6. `NativeSSD` 与 `SimulatedHDD50` 双档回归。
7. Blueprint 变量和 1–5 Node 修改 → Compile → Undo 的日常交互门禁。

正式实现性能 Fixture 前，不解压 `F:\UELecture` 中的其他压缩素材。

## 10. 当前交接结论

0.7.0 的代码、版本、协议、测试和文档已经完成本地收口。源码 Release 与 Python Wheel 可立即用于审阅、后续开发和有工具链机器上的编译。

唯一未完成项是本机缺少 C++ 编译器导致的 UE5.6 Win64 预编译插件 ZIP。该问题不应通过复用或篡改旧 DLL 绕过；补装 MSVC 后重新运行正式 Release 脚本即可完成最终二进制交付。
