# UE Agent Kit 路线图

更新时间：2026-07-21

当前版本为 **0.5.0**，支持 Unreal Engine 5.6。

UE Agent Kit 已完成项目级只读分析、SQLite/FTS 索引、Blueprint 低风险安全写入、通用非 Blueprint 标量属性写入、Material Instance Global Scalar/Vector/Texture/Static Switch，以及 DataTable 单 Row、单顶层标量字段写入。

## 当前整体版本：0.5.0

0.5.0 已完成；0.4.0、0.4.x 与 0.5.0 均保留为可独立验证和回退的检查点。

```text
0.4.0  常用非 Blueprint 专用写入
0.4.x  独立恢复与完整安全回归
0.5.0  MCP / Agent 第一版
```

## 0.4.0：常用非 Blueprint 专用写入（已完成）

0.4.0 已完成：

- Material Instance Vector 参数：已在 0.3.5 完成。
- Material Instance Texture 参数：已在 0.3.6 完成。
- Material Instance Static Switch 参数：已在 0.3.7 完成。
- DataTable 单 Row、单顶层标量字段修改：已在 0.4.0 完成。

所有操作继续遵循：

- 精确 Policy 白名单。
- 当前 Asset Revision 校验。
- 默认 Dry Run。
- Dry Run 完整内存回滚和磁盘哈希不变。
- Commit 前外部备份。
- Commit 后独立 UE 进程重载验证。
- 单资产、单 Operation。

0.4.0 已完成全部真实资产 Dry Run、Commit、唯一备份、独立 UE 进程重载和过期 Revision 拒绝验证。当前活动阶段为 0.4.x。

## 0.4.x：恢复与安全回归

0.4.1 已完成：

- 独立 `rollback` 命令，默认 Dry Run，显式 Commit。
- Commit 自动生成 Backup Manifest、回滚前安全副本、唯一回执和恢复后 Revision 验证。

0.4.2 已完成：

- 声明式 Write Fixture Plan。
- Create/Reset、源资产类检查、目标边界、Sidecar 拒绝和独立 UE 重载验证。

0.4.3 已完成：

- Bool、Byte、Int32、Int64、Float、Double、String、Name、Text、`FEnumProperty` 和 enum-backed Byte Property 的完整真实 UE 覆盖。
- 11/11 Dry Run、11/11 Commit、逐次备份/Manifest/独立重载和最终 Reset。
- 未授权、属性不存在、错误类型、Revision 冲突、数值越界和非法 Enum 的零写入失败回归。

0.4.4 已完成：

- Dirty Package 通过受限测试注入，在任何属性修改前由现有 Policy 门拒绝。
- 真实临时 `.uexp` Sidecar 触发 Package Sidecar 拒绝，并在测试后可靠清理。
- SaveFailure 在 Commit 备份后注入，验证目标 Revision 保持不变、原始备份可用且不生成成功 Manifest；若实际保存已改变磁盘，执行器会复制备份并复核恢复 Revision。
- 完整矩阵达到 11/11 Dry Run、11/11 Commit 和 9/9 零写入失败路径。

0.4.x 的恢复与核心安全回归目标已完成，下一阶段进入 0.5.0 MCP / Agent 接入。

Array、Set、Map、对象引用和任意 Struct 不会直接通过宽松文本导入开放；必须先定义稳定的 JSON 值模型和可验证 Diff。

## 0.5.0：MCP / Agent 第一版（已完成）

0.5.0 提供两种本地 `stdio` 模式：

- 默认三 Tool 只读查询：`ue_search`、`ue_get_asset`、`ue_find_references`。
- 固定项目八 Tool 完整工作流，额外提供 `ue_plan_patch`、`ue_dry_run_patch`、`ue_apply_patch`、`ue_verify_asset`、`ue_rollback_patch`。

完整模式的安全边界：

- Database、Engine、Project、Policy、Revision Export、工作目录和备份目录均在 Server 启动时固定，Tool 参数不能覆盖。
- SQLite 使用不可变只读快照，拒绝活动 `-wal`、`-shm` 和 `-journal`。
- Patch 仍限制为单资产、单 Operation，并复用现有 Policy、Revision、Commandlet、Backup Manifest 和 rollback。
- Plan 文件和 Policy 在 Server 会话内锁定摘要；外部修改会拒绝继续执行。
- Commit 必须先成功 Dry Run，再提供一次性 Receipt 和精确 `COMMIT <planId>` 确认。
- Rollback Commit 必须先成功 rollback Dry Run，再提供一次性 Receipt 和精确 `ROLLBACK <applyReceipt>` 确认。
- `ue_verify_asset` 使用独立 UE 进程重新导出并核对保存后的 SHA-256 Revision。

真实 UE5.6 MCP Client 回归已完成：八 Tool 发现、Dry Run 磁盘不变、错误确认拒绝、Commit、Receipt 单次使用、独立重载、rollback Dry Run、显式恢复，以及最终 `.uasset` 哈希完全还原。

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
