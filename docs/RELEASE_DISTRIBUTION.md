# 发行与离线部署策略

UE Agent Kit 计划提供两种发行包。

## Source

纯源码版本，包含：

- UE 插件源码。
- Python 源码。
- 构建与运行脚本。
- 文档、规范和测试。
- `pyproject.toml` 与锁定文件。

不包含：

- `.venv`。
- 编译后的插件。
- 数据库、导出结果和缓存。
- 本机路径、私有配置和测试项目内容。

## Offline-bootstrap

包含 Source 的全部内容，并额外包含：

- 离线 Wheelhouse。
- 带哈希的精确依赖锁定文件。
- 第三方许可证正文和 Notice。
- `wheel-manifest.json`。
- 离线环境初始化脚本。

不捆绑 Python Runtime，也不直接分发预建 `.venv`。用户需要已安装受支持的 CPython，初始化脚本负责创建本地虚拟环境并只从 Wheelhouse 安装。

初始支持矩阵：

```text
Windows x64
CPython 3.11
CPython 3.12（推荐）
```

Wheelhouse 建议结构：

```text
wheelhouse/
├─ common/
├─ cp311-win_amd64/
└─ cp312-win_amd64/
```

安装必须使用本地文件、固定哈希，并在完成后运行依赖一致性检查。遇到不支持的 Python、架构、缺失 Wheel、未知许可证或哈希不匹配时直接失败，不尝试在线下载或源码构建。

## 当前状态

当前 Python 运行时仅使用标准库，没有第三方运行时依赖，因此现阶段不需要实际维护 Wheelhouse。正式引入 MCP SDK 或其他依赖后，再针对精确锁定版本构建 Offline-bootstrap。

## 第三方合规文件

Offline-bootstrap 至少应包含：

```text
THIRD_PARTY_NOTICES.md
licenses/
wheel-manifest.json
sbom.spdx.json
```

每个实际分发文件记录：

- 包名和版本。
- Wheel 文件名。
- SHA-256。
- 上游来源。
- SPDX License ID。
- 许可证正文位置。
- 直接依赖或传递依赖关系。

## UE 版本兼容

Python、SQLite、协议和查询层应尽量保持与 UE 版本无关。UE Editor C++ 插件必须针对每个支持的 UE Minor 单独编译和验证。发行预编译插件时不得把一个 UE 版本的二进制宣称为通用二进制。
