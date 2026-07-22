# UE Agent Kit 路线图

更新时间：2026-07-21

当前版本为 **0.4.4**，支持 Unreal Engine 5.6。

UE Agent Kit 已完成项目级只读分析、SQLite/FTS 索引、Blueprint 低风险安全写入、通用非 Blueprint 标量属性写入、Material Instance Global Scalar/Vector/Texture/Static Switch，以及 DataTable 单 Row、单顶层标量字段写入。

## 下一整体目标：0.5.0

0.5.0 是下一阶段的整体产品目标，但会拆分为可独立验证和回退的 0.4.0、0.4.x 与 0.5.0 三个检查点。

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

## 0.5.0：MCP / Agent 第一版

首个只读检查点已完成：

- MCP Python SDK 固定稳定 v1 依赖范围，默认使用本地 `stdio`。
- `ue_search`、`ue_get_asset`、`ue_find_references` 已复用现有 SQLite 查询层。
- 数据库在服务器启动时固定，并以 `mode=ro&immutable=1` 打开；活动 SQLite Sidecar 会被拒绝，Tool 不能选择路径或执行任意 SQL。
- 已通过真实 MCP Client 握手、Tool 发现、中文查询、错误参数拒绝，以及数据库目录文件集合与 SHA-256 完全不变验证。

0.5.0 仍计划提供以下高层接口：

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
