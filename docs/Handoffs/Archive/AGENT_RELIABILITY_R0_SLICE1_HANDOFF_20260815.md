# UEAgentKit Agent Reliability R0 Slice 1 接手文档

> 日期：2026-08-15
> 分支：`feature/agent-reliability`（勿 Push；本地 Commit 已包含 R0.0 + R0.1）
> 完成内容：R0.0 现状审计 + R0.1 `ue_get_task_context` 第一条纵向切片
> 下一片：R0.2（自动相关资产扩展），未开始
> 前置阅读：`docs/Plans/AGENT_RELIABILITY_CONTEXT_ANALYSIS_PLAN_20260815.md`、`docs/Plans/AGENT_RELIABILITY_R0_AUDIT_AND_SCHEMA_20260815.md`

---

## 1. 已交付

```text
src/ue_agent_kit/task_context.py           TaskContextService + ue_get_task_context 注册（新增）
src/ue_agent_kit/tool_registry.py          query 组新增 ue_get_task_context（Tool 数 +1，全模式）
src/ue_agent_kit/mcp_server.py             capabilities.taskContext / instructions / project status /
                                           strict args / 注册顺序与 registry 一致
tests/python/test_task_context.py          14 个契约用例（T1–T10 + 参数校验 + MCP 注册/降级/严格参数）
tests/python/test_tool_registry.py         计数与顺序契约更新（6/18、39/51、89/101）
tests/python/test_mcp_server.py            同上的 counts/slices 更新
tests/python/test_agent_workflow.py        live+workflow 工具数 88→89
docs/Plans/AGENT_RELIABILITY_R0_AUDIT_AND_SCHEMA_20260815.md   R0.0 复用矩阵 + 最小 Schema + 边界
docs/Plans/AGENT_RELIABILITY_CONTEXT_ANALYSIS_PLAN_20260815.md 状态更新（§13 标记完成）
docs/ROADMAP.md / docs/PROJECT_STATUS.md    Tool 数、测试数与 R0 状态
spec/MCP_SERVER.md                         ue_get_task_context 契约章节
```

Request/Response 最终结构与风险 kind 全集见审计文档 §5/§6；预算裁剪阶梯见 §7。

## 2. 门禁结果

```text
Ruff                                 passed
Python 全量测试                       530/530（新增 14）
JSON Schema                          不受影响（无 schema 文件变更）
Tool Registry / MCP counts           契约测试更新并通过
UE5.6 Direct Build                   跳过（无 C++ 变更）
真实 UE5.6 Smoke                     跳过（未改变 Live Editor 行为，只消费既有 Bridge 调用）
git diff --check                     通过
```

## 3. R0.2 建议（未开始，需先决策）

候选：`ue_get_task_context` 的 `relevantAssets` 自动扩展（从目标资产引用/反向引用/符号关联派生多跳候选，带 whyIncluded 与置信边界）。注意第一版必须保持「确定性」与预算有界，参考 R1 的深度引用遍历经验；不要把 R1 的完整 Impact Analysis 塞进默认 Context。

## 4. 已知边界（R0.1 遗留，见审计文档 §8）

1. Memory FTS（unicode61）纯中文短语查询命中不可靠；stale 检测在无 ASCII token 时可能漏报（“未命中”≠“不存在”）。
2. revisionState 依赖 Workflow 模式 Revision Export；Offline/Live 模式降级为 unavailable。
3. workItemId / Change Set 深度绑定属 R0.3。
4. 自动多跳相关资产属 R0.2。

## 5. 纪律提醒

- 不 Push；不 Reset/Stash/Revert；不提交 Output/Backups/日志。
- Flash 子代理继续只做只读审计/测试补充，不独立改公共 Schema。
- 主 Agent（Pro）负责设计、门禁与 Commit。
