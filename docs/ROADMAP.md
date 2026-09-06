# UE Agent Kit Roadmap

当前已发布版本为 **0.8.0**，支持 Unreal Engine 5.6。

Roadmap 只描述公开产品方向，不代表固定排期。后续功能优先级以真实项目使用中反复出现的阻塞为依据。

## 当前阶段：真实项目验证与稳定性

0.8.0 已经具备资产/Blueprint 查询、Project Memory、Knowledge Web、受控 Writer、验证/Trust 和 P4 协作基础。下一阶段重点不是继续增加 Tool 数量，而是在真实商业 UE5 项目中验证完整链路：

```text
Project audit
→ index / context
→ impact analysis
→ Write Policy
→ P4 readiness
→ Plan / Apply
→ Save / Verify
→ Semantic Diff / Trust
→ human source-control finalization
```

优先修复真实项目中影响可靠性、性能或可用性的重复问题。

## 候选方向

以下方向只有在真实需求证明价值时才扩展：

- 更广的 Blueprint Graph 结构化编辑。
- 受限 Level Actor / Component 编辑。
- 更多高价值 Asset Writer。
- 更强的多人共享 Knowledge Service。
- Memory 的进一步压缩和长期知识治理。
- 更完善的 P4 / Git collaboration context，但保持最终破坏性 Source Control 操作由人执行。

## 长期原则

1. **理解优先于操作数量**：先提高上下文、引用、影响分析和验证质量。
2. **窄能力优先于万能脚本**：新的写入域必须有明确的 Policy、Diff、Undo/Recovery 和验证语义。
3. **默认只读**：写入和 Source Control 修改必须显式启用。
4. **不绕过安全模型**：不通过 Shell、任意 Python、Console Command 或 generic P4 暴露无限能力。
5. **真实项目驱动**：没有实际 blocker 时，不为了覆盖率单独扩展 Tool family。
6. **人保留最终权限**：尤其是 P4 Submit / Revert / Delete 等不可逆或团队影响较大的操作。
