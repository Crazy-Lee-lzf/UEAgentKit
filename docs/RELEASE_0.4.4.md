# UE Agent Kit 0.4.4 发布说明

UE Agent Kit 0.4.4 面向 Unreal Engine 5.6，正式版本基线提交为 `daea768`。

> 0.5.0 已在 0.4.4 之后正式发布；本文仅记录 0.4.4 的历史范围。

## 版本重点

### 项目级读取与索引

- 通用资产目录、Asset Registry Tags、Package 元数据、SHA-256 Revision 和依赖导出。
- Static Mesh、Skeletal Mesh、Skeleton、Physics Asset、Material、Texture、Animation、DataTable、Data Asset、Niagara 和 World 专用 Reader。
- Blueprint Graph、Node、Pin、Variable、Function、Interface、Macro、Dynamic Cast 和 Event Dispatcher 语义导出。
- SQLite/FTS5 项目索引，支持 Asset、Symbol 和 Reference 查询。

### 安全写入

0.4.4 继承并稳定了以下受控写入能力：

- Blueprint：变量默认值、组件属性、Pin 默认值、Blueprint Description。
- 通用非 Blueprint 资产：单个标量反射属性。
- Material Instance：唯一 Global Scalar、Vector、Texture 和 Static Switch 参数。
- DataTable：现有 Row 的单个顶层标量字段。

所有写入继续要求：

- 单资产、单 Operation。
- Policy 精确授权项目、目录、Asset Class、Operation 和属性/参数/字段。
- SHA-256 Revision 一致。
- Package 非 Dirty，且不存在独立 Package Sidecar。
- Dry Run 恢复内存状态并保持磁盘 Revision 不变。
- Commit 前生成外部备份，成功后生成 Backup Manifest。

### Rollback 与测试资产

- Backup Manifest 记录 Policy 哈希、授权键、变更前后 Revision、备份哈希和大小。
- Rollback 默认 Dry Run；显式 Commit 前检查当前 Revision，恢复前创建安全副本，并在独立 UE 进程中验证恢复结果。
- Write Fixture Plan 支持安全目录内的 Create/Reset、计划哈希锁定、源类型检查和独立重载验证。

## 0.4.4 新增安全回归

0.4.4 在 0.4.3 的完整标量矩阵上新增三类真实失败路径：

1. **Dirty Package**：通过严格限定的测试注入，在任何属性修改前触发现有 Dirty Package 门禁。
2. **真实 Sidecar**：创建临时 `.uexp`，确认单文件 Package 限制会拒绝执行，并在测试后清理。
3. **SaveFailure**：Commit 备份完成后注入保存失败，确认目标 Revision 不变、原始备份可用，且不会生成成功 Manifest；若磁盘已发生变化，执行器包含备份复制与 Revision 复核路径。

## 验证结果

0.4.4 正式回归结果：

```text
UE5.6 plugin build       passed
Python tests             101/101 passed
Scalar Dry Run           11/11 passed
Scalar Commit            11/11 passed
Expected failures         9/9 passed
Independent reload       passed
Final fixture reset      passed
Failure disk SHA-256     unchanged
```

九类零写入失败路径包括：

- 未授权属性。
- 过期 Revision。
- 错误 JSON 类型。
- Byte 越界。
- 非法 Enum 名称。
- 目标属性不存在。
- Dirty Package。
- Package Sidecar。
- SaveFailure。

## 不在 0.4.4 范围内

- Array、Set、Map、任意 Struct 和通用对象引用写入。
- 多资产事务或单资产多 Operation 原子事务。
- Blueprint 节点创建、删除和连线编辑。
- Widget Tree、Anim State Machine、Control Rig、Material Graph、Niagara Graph 等专用图结构编辑。
- MCP 写入、验证和回滚 Tool。

这些能力必须先建立稳定 JSON 值模型、结构化 Diff 和可验证回滚语义，不能通过宽松文本导入直接开放。

## 从 0.2.5 升级

GitHub 远端此前停留在 0.2.5。升级到当前代码后，需要重新构建 UE5.6 插件，并建议重新导出资产目录和 Blueprint Canonical 数据，再重建 SQLite 索引。

```bat
scripts\BuildPlugin.cmd
scripts\RunAssetCatalog.cmd -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject"
scripts\RunExport.cmd -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject"
scripts\ue-agent.cmd index build Output\AssetCatalog --database .data\ue_agent_kit.sqlite3
```

执行任何写入前，应基于新导出重新获取 Revision，并使用 0.4.4 Policy、Patch Schema 和测试 Fixture 验证流程。
