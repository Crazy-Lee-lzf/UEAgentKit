# 双分支与 Worktree 协作规范

更新时间：2026-08-03

## 当前工作区

```text
E:/WorkSpace/UEAgentKit
    branch: feature/live-editor-realtime-io

E:/WorkSpace/UEAgentKit-MemoryContext
    branch: feature/memory-context

E:/WorkSpace/UEAgentKit-Main
    branch: main（里程碑集成与发布门禁）
```

三个目录共享同一个 Git 仓库对象数据库，但拥有独立工作树、Index、当前分支和未提交修改，因此两个功能分支可以同时在 VS Code 中并行开发，`main` Worktree 专门用于合并、文档同步和集成门禁。

## 分支职责

### feature/live-editor-realtime-io

负责运行中 UE Editor 的实时上下文、增删查改、批量任务、PIE 诊断、Change Set、Transaction、Undo/Discard 和保存前工作流。

### feature/memory-context

负责 Knowledge Tree、Active Work、Context Pack、Evidence、Revision stale、项目语义和长期任务连续性。

### main

只接收完成边界清晰、测试通过、文档同步的里程碑。`main` 是两个功能分支的共同基线和后续发布来源，不直接承载长时间试验性开发。

## 公共协议

以下内容不能在两个功能分支中分别定义：

- Project / Asset / Editor Session Identity；
- Task Context；
- Change Set；
- Operation Result Envelope；
- Evidence；
- Revision 与 Freshness；
- Error Model；
- Token / Result Budget。

公共协议应先形成独立提交并合入 `main`。两个功能分支再同步 `main`，避免长期产生两套不兼容格式。

## 当前集成状态

2026-08-03 首个 Realtime Foundation 与 Memory/Context MVP 已正式合入本地 `main`。两个功能分支不删除；本次集成门禁完成后，`main` 将分别同步回两个分支，后续继续并行开发。远端 SSH 当前不可用，因此本次只完成本地分支与 Worktree 集成，推送前必须重新 Fetch。

## 同步规则

日常同步与里程碑合入方向：

```text
feature/live-editor-realtime-io → main
feature/memory-context → main
main → feature/live-editor-realtime-io
main → feature/memory-context
```

功能开发先在对应长期分支完成；达到里程碑门禁后合入 `main`，随后再把新 `main` 同步回两个分支。

功能分支之间不直接互相 Merge。共享内容一律通过 `main` 交换。

出现以下情况时应尽快同步 `main`，不等待固定日期：公共 Schema/Envelope、Project/Asset Identity、MCP 注册、错误模型、测试基线或跨分支依赖发生变化。

## 合并节奏

按可使用的纵向里程碑合并，而不是积累数月后一次性合并。通常每 1–2 周至少形成一个候选里程碑，但完成边界比日期更重要。

一次合入必须满足：

1. 功能边界完整，不提交只有名字没有实现的公共接口；
2. 保持已发布协议兼容，或提供明确迁移；
3. Ruff、Python 测试和 `git diff --check` 通过；
4. C++ 变更可在 UE5.6 编译；
5. 涉及 Editor 行为时有真实 UE5.6 回归；
6. 双语公共文档同步；
7. 不把任意 Python、Console、UObject 或 Save All 带入默认 Agent 模式。

## 首次里程碑（2026-08-03 已完成）

### Realtime I/O

- 统一实时 Query/Batch Task 请求；
- 当前 Editor Context；
- 批量任务的进度、取消、摘要与展开；
- Change Set 最小生命周期；
- 现有 Live Write Operation 接入统一模型。

### Memory/Context

- Knowledge Tree Node、Path、Parent/Child；
- 现有 Memory Record 绑定 Knowledge Node；
- Active Work 最小模型；
- 旧 0.6.0 Memory API 兼容读取；
- 渐进式 Context 查询最小入口。

### 第一次汇合结果

共同确定并合入：

```text
TaskContext
ChangeSet
EvidenceReference
AssetIdentity
RevisionReference
```

实际集成结果：生产代码自动合并，仅文档入口与工具数量断言需要人工处理；合并后工具数量为 5/27/31/53，无 Memory/启用 Memory 对应为 17/39/43/65。集成门禁为 334/334 Python、Ruff、UE5.6 Plugin Build、Memory MCP Smoke 与真实 UE5.6 Closed Loop。

## Git 操作原则

- 两个 Worktree 开始工作前先 `git fetch origin`；
- 功能分支定期同步最新 `main`；
- 合入使用 PR 或等价审查流程；
- 禁止 Force Push `main`；
- 本地 VS Code Workspace、缓存、构建结果和测试项目不提交；
- 推送后验证 `origin/<branch>...HEAD = 0 0`。
