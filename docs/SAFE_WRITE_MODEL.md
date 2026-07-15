# Blueprint 安全写入模型

## 1. 目标

写入能力必须优先保证：

- 不静默破坏 `.uasset`。
- 不基于过期上下文覆盖用户修改。
- 不在编译失败时保存。
- 不允许 AI 绕过操作白名单任意调用 UObject。
- 每次修改都有备份、Diff、日志和恢复路径。

## 2. 项目分级

### 生产或正式项目

用途：真实复杂项目的只读兼容性、性能和检索测试。

默认策略：

```text
ReadOnly = true
AllowPatchDryRun = false
AllowCommit = false
```

除非用户显式修改项目策略，否则不得对该类项目的 `.uasset` 执行写入测试。

### 写入沙箱

用途：创建、修改、删除和导入专用测试 Blueprint。

要求：

- 自动测试资产位于明确允许的测试目录。
- 不直接修改模板或生产资产；需要时先复制到测试目录。
- 可以故意制造编译错误，用于验证门禁和回滚。
- 可以测试 Unicode 工程路径、资产路径和版本控制兼容性。

### 专用资产测试工程

用途：补充验证角色、动画和复杂导入资产。

默认不修改已有生产资产。需要写入时先复制到专用测试目录，并将复制资产纳入同一备份、Dry Run、编译和回滚流程。
## 3. Patch 生命周期

### 3.1 Plan

AI 根据当前导出和索引生成 Patch：

```text
Asset
BaseRevision
Mode
Operations
ExpectedChanges
RiskLevel
```

### 3.2 Validate

执行前校验：

- 目标项目是否允许写入。
- 目标资产是否位于允许目录。
- 操作是否在白名单。
- `baseRevision` 是否匹配当前资产。
- 值是否能按目标 Property/Pin 类型解析。
- 是否存在未保存的编辑器修改。
- 资产是否只读或被其他进程占用。

### 3.3 Backup

Commit 前必须建立外部备份。备份不得只依赖 Editor Undo。

建议目录：

```text
UEAgentKit/Backups/Assets/<Project>/<Timestamp>/
```

记录：

- 原始 `.uasset` 相对路径。
- 文件大小、时间戳和 SHA-256。
- Patch 文件。
- 修改前 Canonical JSON。
- 修改前 BPCTX。
- 项目和引擎版本。

### 3.4 Apply In Memory

操作对象应先调用 `Modify()`，并在适用时使用 `FScopedTransaction`。

Blueprint 结构修改优先使用：

- `FBlueprintEditorUtils`
- `FKismetEditorUtilities`
- Blueprint Schema 的连接校验
- `FGraphNodeCreator`

不得直接修改无法保证一致性的内部数组并立即保存。

### 3.5 Refresh and Compile

应用后执行：

```text
RefreshAllNodes
MarkBlueprintAsStructurallyModified / MarkBlueprintAsModified
CompileBlueprint
```

具体调用根据操作类型选择，避免所有操作都强制结构性重编译。

编译结果至少记录：

- Blueprint Status。
- Error 数量。
- Warning 数量。
- Compiler Results Log 摘要。

### 3.6 Re-export and Diff

修改后重新导出同一资产，比较：

- Revision。
- 变量定义和默认值。
- 组件属性。
- 图、节点、Pin 和连线。
- 预期变更是否发生。
- 非预期结构变更是否出现。

Dry Run 返回 Diff，但不保存。

### 3.7 Commit

只有以下条件全部满足才保存：

- 显式 `commit=true`。
- Backup 成功。
- Patch 全部操作成功。
- 编译无 Error。
- Diff 与 ExpectedChanges 一致。
- 没有检测到资产版本冲突。

保存后重新加载或重新扫描，确认磁盘资产与内存结果一致。

## 4. 回滚层级

### Level 1：内存回滚

Dry Run 或未保存修改直接撤销 Transaction，或重新加载 Package。

### Level 2：文件备份恢复

已保存但验证失败时，从外部备份恢复 `.uasset`，然后重新扫描资产注册表并验证。

### Level 3：版本控制恢复

后续接入 Git LFS 或 Perforce 时，可使用版本控制恢复。版本控制不能替代本工具自己的外部备份。

## 5. 第一版操作白名单

### setVariableDefault

风险：低。

验证：

- 变量存在。
- 类型可解析。
- Generated Class 和 CDO 可用。
- 修改后默认值重新导出一致。

### addVariable

风险：中。

验证：

- 名称合法且不冲突。
- 类型受支持。
- Blueprint 编译成功。
- Skeleton/Generated Class 中能找到新属性。

### removeVariable

风险：中到高。

验证：

- 默认拒绝删除存在引用的变量。
- 必须先执行引用查询。
- 只有显式允许同时清理引用时才执行后续阶段。

### renameVariable

风险：中。

验证：

- 使用 Blueprint 工具 API 更新引用。
- 修改前后引用数量一致。
- 不允许仅修改变量描述名称而留下失效节点。

### setComponentProperty

风险：低到中。

验证：

- 只允许可编辑、非瞬态、非弃用属性。
- 对 Object 引用执行资产路径和类型校验。
- 修改组件模板，不直接改运行时实例。

### setPinDefault

风险：中。

验证：

- Pin 未连接，或操作明确允许断开连接。
- Schema 接受该默认值。
- 节点重构后 Pin 仍能按 ID 或稳定键定位。

## 6. 高风险操作

第一版默认禁止：

- 直接修改 Pin 数组。
- 直接写 NodeGuid、GraphGuid 或 PinId。
- 任意调用编辑器 UFunction。
- 删除存在引用的变量、函数或图。
- 自动修改多个资产后一次性保存。
- 不编译直接保存 Blueprint。
- 在策略标记为只读的项目中执行写入。
- 在用户未确认时自动 Commit。

## 7. 写入测试要求

每项写入功能至少覆盖：

1. Dry Run 不改变 `.uasset` 哈希。
2. Commit 后修改生效。
3. 关闭并重新打开 UE 后仍然生效。
4. 修改后 Blueprint 编译无错误。
5. 错误输入不会保存。
6. 版本冲突会拒绝执行。
7. 回滚后文件哈希或结构恢复。
8. 备份、Patch、Diff 和日志完整。

## 8. P4 与 Git

第一版不强制接入 P4。后续版本控制适配层应提供：

```text
isTracked
isWritable
checkout/edit
add
revert
status
```

本地 Git 用于工具源码版本控制。UE 测试工程是否进入 Git 单独决定；不要把 DerivedDataCache、Intermediate、Saved 和插件编译产物提交。
