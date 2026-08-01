# 双分支与 Worktree 协作规范

更新时间：2026-08-01

## 当前工作区

```text
E:/WorkSpace/UEAgentKit
    branch: feature/live-editor-realtime-io

E:/WorkSpace/UEAgentKit-MemoryContext
    branch: feature/memory-context
```

两个目录共享同一个 Git 仓库对象数据库，但拥有独立工作树、Index、当前分支和未提交修改，因此可以同时在 VS Code 中打开并行开发。

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

## 同步规则

日常只允许：

```text
main → feature/live-editor-realtime-io
main → feature/memory-context
```

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

## 首次里程碑

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

### 第一次汇合

共同确定并合入：

```text
TaskContext
ChangeSet
EvidenceReference
AssetIdentity
RevisionReference
```

## Git 操作原则

- 两个 Worktree 开始工作前先 `git fetch origin`；
- 功能分支定期同步最新 `main`；
- 合入使用 PR 或等价审查流程；
- 禁止 Force Push `main`；
- 本地 VS Code Workspace、缓存、构建结果和测试项目不提交；
- 推送后验证 `origin/<branch>...HEAD = 0 0`。
