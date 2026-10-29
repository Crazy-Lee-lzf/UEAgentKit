# 专用 Tool 文档索引（面向用户与 AI Agent）

此索引只指向与 UE Agent Kit 使用有关的工具说明。实际能调用的 Tool 及输入/返回 Schema，**始终以当前服务器的 `ue_get_capabilities` 和 MCP 工具定义为准**。文档中出现的阶段验收结果不代表在其他 UE 版本、项目或当前用户环境中可用。

## AI Agent 使用规则

在使用任何专用 Tool 前先确认：

| 检查项 | 操作 |
| --- | --- |
| 环境 | 核对部署版本、目标 UE 版本、插件和 Editor 是否在线 |
| 能力 | 先调用 `ue_get_capabilities`；缺失的 Tool 不能假装存在 |
| 输入 | 严格依照本会话 Tool Schema 填参数；不得凭自然语言臆造 ID、路径、Receipt |
| 权限和副作用 | 将只读诊断、规划、Editor 内存修改、Save/Commit 分开 |
| 输出 | 返回 Source、Target、Revision、状态和可核验证据，不把推断当事实 |
| 错误 | 返回实际错误码及阻塞条件，不绕过 Dirty/Policy/Revision/Session 门禁 |
| 恢复 | 修改类 Tool 应具备独立 Verify；失败时按对应文档的 Undo/Discard/rollback 流程操作 |
| 已验证范围 | 本地 Smoke/自动化只对所列测试版本与测试工程有效，不是生产授权 |

通用读取与受控写入顺序见 [AI Agent 快速接入](AGENT_QUICKSTART.md)，具体 MCP 行为见 [MCP Server](../spec/MCP_SERVER.md) 和 [备份/回滚](../spec/BACKUP_AND_ROLLBACK.md)。

## 公开专用工具说明

| 功能领域 | Tool 说明 |
| --- | --- |
| Additive 动画诊断 | [Additive Animation Diagnosis](ADDITIVE_ANIMATION_DIAGNOSIS_TOOL.md)、[Evaluation](ADDITIVE_ANIMATION_EVALUATION_TOOL.md) |
| Additive Base Pose 计划与修复 | [Fix Plan](ADDITIVE_BASE_POSE_FIX_PLAN_TOOL.md)、[Write](ADDITIVE_BASE_POSE_FIX_WRITE_TOOL.md) |
| 动画比例审计/修复 | [Scale Audit](ANIMATION_SCALE_AUDIT_TOOL.md)、[Scale Fix](ANIMATION_SCALE_FIX_TOOL.md)、[Batch Fix](ANIMATION_SCALE_FIX_BATCH_TOOL.md) |
| 角色接地与二次运动 | [Ground Contact](CHARACTER_GROUND_CONTACT_TOOL.md)、[Secondary Motion](SKELETAL_SECONDARY_MOTION_TOOL.md) |
| 重定向后处理 | [Retarget Postprocess](RETARGET_POSTPROCESS_TOOL.md) |

这些链接是**产品文档导航**，不保证所有 Tool 均随 v0.9.0-dev1 以同一模式默认开放。只读能力通常不意味着可写；计划生成不等于 Apply；在 Editor 内完成操作也不等于资产已保存或独立验证。

## 为新增 Tool 编写公开说明

公开的单 Tool 文档应能让 AI Agent 不依赖内部研发报告而完成安全调用：用途、最小运行环境和启动模式、只读/修改标记、输入参数与返回结构、错误码、最小调用示例、需要的人工授权、失败与回滚、安全限制、测试的 UE 版本和范围。运行日志、私人测试工程、临时本机路径及阶段 RunState 应留在内部 Development。

如页面缺少某项内容，Agent 应读取对应版本的 MCP Schema 或报告该字段尚未由公开文档证明，**不得补造默认值或错误码**。
