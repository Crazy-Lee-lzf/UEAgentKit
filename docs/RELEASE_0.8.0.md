# UE Agent Kit 0.8.0 发布说明

UE Agent Kit 0.8.0 面向 **Unreal Engine 5.6 / Win64**，重点是把项目理解、受控写入、验证、Memory 和 P4 协作整合成一套可用于真实项目的 Agent 工作流。

## 新增与改进

### 更完整的 Agent 工作流

- Task Context 和相关资产发现。
- Reverse-reference Impact Analysis。
- Change-Set 绑定的 Semantic Diff。
- Verification Plan 和 Trust Verdict。
- Resident Writer 的 Fast Verify、Checkpoint Strong Verify、显式 Save 和恢复链路。
- 多 Operation / 多资产的有界规划与执行。

### Project Memory

- 确定性 L0 自动捕获和 L1 蒸馏。
- FTS5 召回，以及可选 Vector + RRF hybrid recall。
- 持久化 L2/L3 上下文和有界自动注入。
- Revision-aware stale / superseded / conflict 管理。

### Knowledge Web

新增只读 Knowledge Web，用于浏览资产、引用、Symbol、Memory 和验证证据，不修改 Unreal 项目。

### P4 / Perforce

新增 opt-in Source Control 支持：

- mapping / opened / lock / have/head；
- exact-file `p4 edit`；
- 严格 safe sync；
- pending changelist 查询/创建/更新；
- exact-file `reopen`；
- resolve preview；
- 满足条件的普通文本 `resolve -am`；
- 持久化审计记录。

仍然明确禁止 Agent 执行 P4 Submit、P4 Revert 和 P4-managed Delete；`.uasset/.umap` 不自动做内容 Resolve。

### Fresh clone 与公开仓库卫生

- 修复 Windows launcher 行尾，使 fresh clone 不再依赖本机 `.git/info/attributes`。
- P4 capability evidence 已去除私有 client/host/server/fixture 标识。
- Release 文档只保留面向用户的产品内容；内部开发计划和交接记录不再属于公开发布树。

## 兼容性

```text
Unreal Engine 5.6
Windows 10 / 11
Python 3.11 / 3.12
P4 CLI（可选）
```

0.8.0 继续保持固定项目 MCP、安全 Write Policy、Revision gating、显式 Save 和独立 Verify 模型。

## 升级

建议从 GitHub tag `v0.8.0` 获取固定版本，然后重新建立 Python 环境和 Plugin 构建产物：

```bat
scripts\setup_python.cmd -WithMcp
scripts\BuildPluginDirect.cmd -EngineRoot "<UE_5.6>"
```

不要从旧工作目录复制 `.venv`、`Build/Compiled`、Memory SQLite 或测试 Output；真实项目应重新建立自己的索引、Memory 和 Write Policy。

## 已知限制

- 不提供通用 Blueprint Graph CRUD。
- 不提供任意 Level Actor 编辑。
- 不提供通用 Material Graph / Niagara / Sequencer / Control Rig 写入。
- 不提供 arbitrary Python / Console / Shell / UObject Method。
- 不自动 Save All。
- P4 Submit / Revert / Delete 由人执行。

## 验证

0.8.0 发布候选在正式打包前通过 portable Python full suite、Ruff、compileall、Release Validation、Schema/Patch example 校验和 fresh-clone 验证。正式 Release 构建还会生成 UE5.6 Win64 Plugin ZIP、Python wheel、SHA-256 校验和与 release manifest。
