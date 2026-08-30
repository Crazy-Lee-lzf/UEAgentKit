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

完整方案：[`../../PERFORMANCE_TEST_PLAN.md`](../../PERFORMANCE_TEST_PLAN.md)

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

Visual Studio 更新后的正式构建实测：

```text
Ran 334 tests in 25.242s
OK

UAT BuildPlugin              passed
UnrealHeaderTool             passed
C++ actions                  48/48 passed
Parallel executor            56.63s
UnrealBuildTool total        72.30s
AutomationTool total         1m 14s
```

插件已由 UE5.6 AutomationTool 重新编译，不再复用旧 DLL。

## 6. 工具链恢复与 Release 脚本修复

Visual Studio 更新后，`BuildPlugin.ps1` 成功解析并通过 AutoSDK 提供以下工具链：

```text
Visual Studio installation
  D:\Program Files (x86)\Microsoft Visual Studio\2022\Community

Resolved MSVC root
  D:\Program Files (x86)\Microsoft Visual Studio\2022\Community\VC\Tools\MSVC\14.38.33130

UAT AutoSDK
  E:\WorkSpace\UEAgentKit-Main\AutoSDK

UnrealBuildTool selected
  Visual Studio 2022 14.38.33145
  Windows SDK 10.0.22621.0
  UBA disabled
```

此前 Reforge 使用的 `14.50.35717` 不再是本次构建的必要条件；UE5.6 已实际接受并使用恢复后的 14.38 工具链完成 48 个编译、链接和元数据动作。

第一次完整运行在插件成功打包后，Python Wheel 阶段失败：

```text
BackendUnavailable: Cannot import 'setuptools.build_meta'
Python wheel build failed.
```

根因不是项目源码，而是 Python 3.12 虚拟环境没有安装 `setuptools`，同时 `BuildRelease.ps1` 使用了 `--no-build-isolation`，导致 Pip 忽略 `pyproject.toml` 中声明的：

```toml
[build-system]
requires = ["setuptools>=77"]
build-backend = "setuptools.build_meta"
```

修复方式是移除 `--no-build-isolation`，恢复标准 PEP 517 隔离构建。隔离构建探针已成功自动安装构建依赖并生成 0.7.0 Wheel，不需要污染 UE Agent Kit 的运行虚拟环境。

## 7. 正式本地 Release 产物

正式 `BuildRelease.ps1` 产物位于：

```text
E:\WorkSpace\UEAgentKit-Main\Output\Release\0.7.0\
├─ UEAgentKit-0.7.0-UE5.6-Win64.zip
├─ ue_agent_kit-0.7.0-py3-none-any.whl
├─ SHA256SUMS.txt
└─ release-manifest.json
```

其中 Win64 ZIP 是本次使用 UE5.6、MSVC 14.38 和 Windows SDK 10.0.22621.0 重新构建的插件包。

插件包只保留允许的发布内容：

- `Binaries`
- `Config`
- `Content`
- `Resources`
- `Shaders`
- `Source`
- `LICENSE`
- 中英文发布说明
- `UEAgentKit.uplugin`

发布脚本会删除 `Intermediate`、`Saved`、`DerivedDataCache`、临时 `HostProject` 和不面向最终用户分发的 PDB 调试符号。

## 8. 最终产物核验

完整复跑后需要并已纳入自动检查：

- UAT BuildPlugin 成功。
- ZIP 内存在 `Binaries\Win64\UnrealEditor-UEAgentKitEditor.dll`。
- ZIP 内存在 `Binaries\Win64\UnrealEditor.modules`。
- ZIP 不含 PDB、`Intermediate`、`Saved`、`DerivedDataCache` 和 `HostProject`。
- `UEAgentKit.uplugin` 的 `VersionName` 为 0.7.0、`Version` 为 27。
- Python Wheel 元数据版本为 0.7.0。
- Release Manifest 记录实际 Git Commit、文件大小和 SHA-256。
- `SHA256SUMS.txt` 覆盖 Win64 插件 ZIP 与 Python Wheel。

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

0.7.0 的代码、版本、协议、测试、文档和本地二进制 Release 已完成收口。UE5.6 Win64 插件由恢复后的 MSVC 14.38 工具链重新编译，Python Wheel 使用标准 PEP 517 隔离环境构建。

当前没有本地发布阻塞。后续工作可从 `feature/performance-benchmarks` 开始；远端 Push、Tag 和 GitHub Release 仍按本次约束保持未执行。
