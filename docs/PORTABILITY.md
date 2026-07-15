# 可移植性设计

## 1. 目标

本项目必须能够在不同开发机器、不同 UE5.6 工程和不同版本控制环境中快速部署，不依赖某台机器的固定盘符、全局 Python 环境、全局插件安装或手工配置。

可移植性目标包括：

- 工具源码目录可整体迁移。
- UE 插件可以独立构建和安装。
- Python 工具使用项目本地虚拟环境。
- 所有配置支持相对路径和环境变量覆盖。
- 缓存、索引和导出结果可以重新生成。
- 支持英文和 Unicode 工程路径。
- Git、P4 和无版本控制环境均可使用。
- 查询与 Patch 数据不依赖某台机器的绝对路径。

## 2. Python 版本策略

### 开发基线

```text
CPython 3.12.x
```

### 最低兼容版本

```text
Python 3.11
```

### 不支持

```text
Python 3.9 及更早版本
```

项目应在 `pyproject.toml` 中声明：

```toml
requires-python = ">=3.11,<3.13"
```

第一版开发、锁依赖和发布验证以 Python 3.12 为基线。后续 CI 至少验证 Python 3.11 和 3.12。

## 3. 不依赖系统 Python

系统中的 `python.exe`、`python3.exe` 和 `py.exe` 只用于首次创建虚拟环境。

初始化完成后，所有脚本必须调用：

```text
<TOOL_ROOT>\.venv\Scripts\python.exe
```

不得依赖：

- PATH 中恰好排在前面的 Python。
- `py.exe` 的默认版本。
- Microsoft Store App Execution Alias。
- 用户全局安装的第三方包。
- UE 自带 Python 环境。

UE Editor 内置 Python 与本项目外部索引/MCP Python 环境应保持分离。

## 4. Python 初始化流程

后续提供统一脚本：

```text
scripts/setup_python.ps1
scripts/setup_python.cmd
```

脚本应：

1. 优先查找 Python 3.12。
2. 找不到时允许使用 Python 3.11。
3. 拒绝 Python 3.9 和更早版本。
4. 在项目根目录创建 `.venv`。
5. 升级项目虚拟环境内的 pip。
6. 按锁文件安装依赖。
7. 验证 SQLite、JSON、MCP 和项目模块导入。
8. 输出实际解释器路径和版本。

初始化脚本不得修改系统级 Python 配置。

## 5. 依赖管理

建议结构：

```text
pyproject.toml
requirements.lock
requirements-dev.lock
```

规则：

- `pyproject.toml` 描述项目和版本范围。
- 锁文件固定实际依赖版本。
- 运行依赖与开发测试依赖分离。
- 不提交 `.venv`。
- 不依赖用户全局 site-packages。
- 第一版优先使用 Python 标准库，减少外部依赖。

SQLite 优先使用 Python 标准库 `sqlite3`。只有标准库能力不足时，才引入额外数据库包。

## 6. 路径规则

### 6.1 工具根目录

所有脚本通过自身位置推导 `<TOOL_ROOT>`，不得硬编码工具绝对路径。

### 6.2 UE 路径

UE 根目录通过以下方式之一提供：

1. 命令行参数。
2. 项目本地配置。
3. 环境变量。
4. 已知安装位置自动发现。

自动发现失败时必须明确报错，不应静默选择其他引擎版本。

### 6.3 项目路径

项目路径必须支持：

- 空格。
- 中文和其他 Unicode 字符。
- 不同盘符。
- 相对路径和绝对路径。

所有进程调用必须将 executable 和 args 分开传递，不能依赖字符串拼接。

### 6.4 资产路径

UE 资产使用规范化 Object Path 或 Package Name，不在索引主键中存储本地 Content 绝对路径。

## 7. 配置分层

建议结构：

```text
config/
├─ defaults.json
├─ schema.json
└─ local.example.json

.local/
└─ config.json
```

规则：

- 默认配置和 Schema 可以提交 Git。
- 机器专属配置放在 `.local/`，不提交 Git。
- 环境变量和命令行参数可以覆盖本地配置。
- 配置文件中尽量使用相对路径。
- 不把 API Key、P4 密码或用户凭据写入仓库。

## 8. UE 插件安装

支持两种模式：

### 开发模式

使用项目级 Junction：

```text
<Project>\Plugins\UEAgentKit
→ <ToolRoot>\Build\Compiled\UEAgentKit
```

优点：构建后立即生效，不重复复制大型 PDB。

### 分发模式

将编译后的插件包复制到目标项目或独立压缩包中。

分发模式不得要求目标机器拥有源码、AutoSDK 或本机开发目录结构。

安装脚本应支持：

```text
-link
-copy
-remove
-status
```

## 9. 本地数据目录

可重新生成的数据不得进入源码仓库：

```text
.venv/
.local/
.data/
Build/
Output/
Backups/
AutoSDK/
```

建议用途：

```text
.data/index/       SQLite 索引
.data/cache/       增量导出缓存
.data/logs/        本地运行日志
.local/config.json 机器配置
```

删除 `.data` 后，工具应能够从 UE 项目重新构建索引。

## 10. 版本控制适配

核心功能不能强依赖 Git 或 P4。

版本控制层提供可选能力：

```text
status
isTracked
checkout/edit
add
revert
diff
```

没有版本控制时，工具仍依靠自己的外部备份和 Revision 检查保证安全。

Git/P4 适配不得把本地用户名、服务器地址或 Client 名写入公开配置。

## 11. Unicode 兼容性

以下内容必须作为持续测试项：

- 中文工程目录。
- 中文 `.uproject` 文件名。
- 中文资产目录和 Blueprint 名称。
- 中文变量、函数和注释。
- JSON 和 BPCTX UTF-8 输出。
- SQLite UTF-8 查询。
- PowerShell 和 Commandlet 参数传递。
- Git 和 P4 中文路径状态查询。

## 12. 发布形态

### 开发版

```text
源码
+ setup_python 脚本
+ BuildPlugin 脚本
+ 项目级插件挂载脚本
```

### 第一版分发包

```text
Compiled Plugin
Python Source
Locked Dependencies
Bootstrap Scripts
Public Documentation
```

### 后续独立分发

可以考虑：

- 随工具附带 Python Embeddable Package。
- 将 Python MCP/索引层打包为独立可执行文件。
- 提供完全不依赖系统 Python 的压缩包。

第一版不必立即捆绑 Python 运行时，但代码和目录设计必须允许后续加入，而不破坏协议和配置。

## 13. 验收标准

可移植性测试至少覆盖：

1. 将工具复制到不同路径后能重新构建。
2. 在没有全局第三方 Python 包的机器上初始化 `.venv`。
3. Python 3.11 和 3.12 均能运行查询层。
4. 英文、空格和中文工程路径均能运行。
5. 删除 Build、Output 和 `.data` 后可以重建。
6. 项目级 Junction 和复制安装两种模式都可用。
7. 不修改全局 UE、Python、Git 或 P4 配置即可运行。
8. 公开文档中不依赖开发者本机绝对路径。