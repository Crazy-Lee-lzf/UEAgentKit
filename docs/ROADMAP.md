# UE Agent Kit 路线图

更新时间：2026-07-21

当前版本为 **0.3.5**，支持 Unreal Engine 5.6。

UE Agent Kit 已完成项目级只读分析、SQLite/FTS 索引、Blueprint 低风险安全写入、通用非 Blueprint 标量属性写入，以及 Material Instance Global Scalar 与 Vector 参数写入。

## 下一整体目标：0.5.0

0.5.0 是下一阶段的整体产品目标，但会拆分为可独立验证和回退的 0.4.0、0.4.x 与 0.5.0 三个检查点。

```text
0.4.0  常用非 Blueprint 专用写入
0.4.x  独立恢复与完整安全回归
0.5.0  MCP / Agent 第一版
```

## 0.4.0：常用非 Blueprint 专用写入

计划开放：

- Material Instance Vector 参数：已在 0.3.5 完成。
- Material Instance Texture 参数。
- Material Instance Static Switch 参数。
- DataTable 单 Row、单标量字段修改。

所有操作继续遵循：

- 精确 Policy 白名单。
- 当前 Asset Revision 校验。
- 默认 Dry Run。
- Dry Run 完整内存回滚和磁盘哈希不变。
- Commit 前外部备份。
- Commit 后独立 UE 进程重载验证。
- 单资产、单 Operation。

## 0.4.x：恢复与安全回归

计划完成：

- 独立 `rollback` 命令。
- 备份 Manifest 和恢复后 Revision 验证。
- 自动生成和重置写入测试资产。
- 标量类型完整真实 UE 覆盖。
- 未授权、目标不存在、错误类型、Revision 冲突、Dirty Package、Sidecar 和保存失败测试。

Array、Set、Map、对象引用和任意 Struct 不会直接通过宽松文本导入开放；必须先定义稳定的 JSON 值模型和可验证 Diff。

## 0.5.0：MCP / Agent 第一版

计划提供少量高层接口：

```text
ue_search
ue_get_asset
ue_find_references
ue_plan_patch
ue_dry_run_patch
ue_apply_patch
ue_verify_asset
ue_rollback_patch
```

MCP 只是现有 SQLite、Patch、Policy、Revision、Commandlet 和 Rollback 的接入层，不会暴露任意 UObject 调用、Shell 或文件覆盖能力。

0.5.0 的目标闭环是：

```text
查找资产
→ 查看结构和引用
→ 生成 Patch 计划
→ Dry Run
→ 查看结构化结果
→ 显式 Commit
→ 独立验证
→ 必要时回滚
```

## 0.5.0 之后

后续阶段包括：

- Blueprint 变量新增、重命名和安全删除。
- 单资产多 Operation 原子事务。
- Blueprint 节点创建、删除和 Pin 连接。
- Widget Tree、Anim State Machine、Control Rig、Material Graph、Niagara、Behavior Tree 和 StateTree 专用适配器。
- Git / Perforce、CI、编辑器状态面板、审计和多 UE 版本支持。

## 版本原则

- 每个 Operation 独立完成 Schema、Policy、Dry Run、Commit、备份、重载和负面测试。
- 不以“编译通过”替代真实资产验证。
- 正式项目默认只读。
- 不直接编辑 `.uasset` 二进制文件。
