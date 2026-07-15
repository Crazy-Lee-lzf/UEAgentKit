# 构建与运行

## 1. 环境要求

```text
Windows 10/11
Unreal Engine 5.6
Visual Studio C++ Toolchain
PowerShell 5.1 或更高版本
```

插件是 Editor-only C++ 插件，不用于打包后的游戏运行时。

## 2. 目录约定

本文使用以下占位符：

```text
<TOOL_ROOT>     BlueprintContextTool 根目录
<UE_ROOT>       Unreal Engine 5.6 安装目录
<PROJECT_ROOT>  需要加载插件的 UE 项目目录
```

示例：

```text
<TOOL_ROOT>\Plugin\BlueprintContextTool
<PROJECT_ROOT>\ProjectName.uproject
```

## 3. 构建插件

推荐使用直接构建脚本：

```powershell
powershell.exe -ExecutionPolicy Bypass -File <TOOL_ROOT>\scripts\BuildPluginDirect.ps1 \
  -EngineRoot "<UE_ROOT>" \
  -MsvcToolsRoot "<MSVC_TOOLS_ROOT>"
```

本机默认路径匹配时，也可以直接运行：

```bat
<TOOL_ROOT>\scripts\BuildPluginDirect.cmd
```

脚本会：

1. 查找可用的 x64 MSVC Toolchain。
2. 建立项目本地 AutoSDK 映射。
3. 创建独立 HostProject。
4. 通过 Junction 挂载插件源码。
5. 调用 UE5.6 `Build.bat`。
6. 禁用 UBA。
7. 将可安装插件复制到：

```text
<TOOL_ROOT>\Build\Compiled\BlueprintContextTool
```

构建成功标志：

```text
BUILD SUCCEEDED
```

并且以下文件存在：

```text
Build\Compiled\BlueprintContextTool\Binaries\Win64\UnrealEditor-BlueprintContextToolEditor.dll
```

## 4. 安装到项目

推荐使用项目级 Junction，不复制插件源码和大型 PDB：

```text
<PROJECT_ROOT>\Plugins\BlueprintContextTool
→ <TOOL_ROOT>\Build\Compiled\BlueprintContextTool
```

然后在 `.uproject` 的 `Plugins` 数组中启用：

```json
{
  "Name": "BlueprintContextTool",
  "Enabled": true,
  "TargetAllowList": [
    "Editor"
  ]
}
```

修改 `.uproject` 前必须备份。

Junction 只用于本地安装，不应进入 Git 或 P4。

## 5. 验证插件加载

启动编辑器或 `UnrealEditor-Cmd.exe`，检查日志中是否出现：

```text
Mounting Project plugin BlueprintContextTool
```

若插件未加载，检查：

- `.uproject` 是否启用插件。
- Junction 目标是否存在。
- 插件 DLL 是否与当前 UE5.6 构建匹配。
- 编辑器是否仍在使用旧 DLL。

## 6. 导出单个 Blueprint

使用导出脚本：

```bat
<TOOL_ROOT>\scripts\RunExport.cmd \
  -Asset "/Game/Folder/BP_Name" \
  -Profile logic \
  -Format both
```

常用参数：

```text
-Asset    单个资产路径
-Root     批量扫描的 Content 根路径
-Output   输出目录
-Profile  index|structure|logic|defaults|full|ai
-Format   json|bpctx|both
-Graph    只导出指定 Graph
```

## 7. 批量导出

```bat
<TOOL_ROOT>\scripts\RunExport.cmd \
  -Root "/Game/Folder" \
  -Profile ai \
  -Format both \
  -CompactJson
```

默认输出结构：

```text
Output\
├─ manifest.json
├─ canonical\
└─ bpctx\
```

每个资产独立记录成功或失败，单个损坏资产不应中断整个批量任务。

## 8. 输出校验

至少检查：

- Commandlet 退出码为 0。
- `manifest.json` 是有效 UTF-8 JSON。
- Canonical JSON 可以解析。
- BPCTX 第一行符合：

```text
H|BPCTX|1|...
```

- Manifest 中 `Success` 和 `Failure` 数量符合预期。

## 9. 当前写入能力

当前公开版本只读。尚未提供：

- Patch Commandlet。
- Dry Run 修改。
- Blueprint Commit。
- Rollback。

正式写入接口完成后，本文件会补充 Patch、验证和保存命令。当前能力边界以 `CURRENT_STATUS.md` 为准。

## 10. 清理

可以安全清理：

```text
Build\DirectHost\
Build\Compiled\
Output\
```

清理 Junction 时只删除链接本身，不要递归删除其目标目录。
