# UE Agent Kit 0.7.0 发布说明

UE Agent Kit 0.7.0 面向 Unreal Engine 5.6，正式发布 Realtime Foundation、注册式 Live Editor Write、Schema v3 Project Memory、分帧批量任务和持久化 Change Set。该版本仍坚持固定工程、Policy、Revision、显式确认、Transaction、Evidence、Undo/Discard、授权保存、独立验证和 rollback 的安全闭环。

## 主要更新

### Realtime Foundation

- `ue_get_editor_context` 聚合 Editor、World、Selection、Open Assets、Dirty Packages、Blueprint Graph Selection、Compile Errors 和 Output Log Cursor，并返回分阶段耗时。
- `scanCurrentWorld` 使用约 2 ms 单帧预算处理当前已加载 World，支持进度、取消、超时、失效和部分结果。
- Batch Task 默认返回摘要，Actor 详情通过受限分页读取。
- Change Set schema v2 持久化 Task、Editor Session、Operation、Asset、Transaction、Save Receipt 和 Validation 生命周期。

### 注册式 Live Editor Write

- `ue_apply_asset_property_live` 通过 `OperationSpec` 和资产域执行器支持 12 个受控 Operation。
- 支持 Data Asset 标量、引用、Struct、Array、Set、Map；Material Instance Scalar、Vector、Texture、Static Switch；DataTable Cell、RowFields、Add、Remove、Rename。
- 统一 Transaction/Evidence、精确 Undo/Discard、授权单资产 Save、Independent Verify 和可恢复 Journal。
- 失败路径恢复原值和 Dirty 状态；No-op 不创建 Dirty 或 Undo。

### Schema v3 Project Memory

- Knowledge Tree 使用稳定 Path 和 Parent/Child 支持任意深度。
- Active Work 与长期知识分离，保存目标、TODO、阻塞和下一步。
- 五级渐进式披露和 Server 强制 Token Budget，默认摘要优先。
- 新增 `ue_memory_get_context`、`ue_memory_expand_node`、`ue_memory_get_evidence`、`ue_memory_update_knowledge` 和 `ue_memory_update_work`。
- 保留 0.6.0 低层 Memory API 的兼容读取。

### 大型项目性能规划

- 新增 `docs/PERFORMANCE_TEST_PLAN.md`。
- 物理测试工程目标 160–180 GB，硬上限 200 GB，存放于 SSD。
- 同一套基准分别运行 `NativeSSD` 和 `SimulatedHDD50`；后者采用 50 MB/s 顺序上限、8/10/15 ms 文件寻道档位和队列深度 1。
- 首次索引允许较慢，普通搜索、变量修改、少量 Blueprint 节点修改、Compile、Undo 和单资产保存作为最高优先级交互门禁。

## Tool 数量

```text
Offline             5 Tool（Memory 17）
Live               27 Tool（Memory 39）
Workflow           31 Tool（Memory 43）
Combined           53 Tool（Memory 65）
```

## 安全边界

0.7.0 仍不开放：

- 任意 SQL、Shell、Python 或文件系统访问。
- 任意 UObject Method、Console Command 或脚本执行。
- 自动保存、Save All 或未授权 Commit。
- PIE/SIE 中资产修改。
- 任意 Blueprint Graph、Material Graph、Anim State Machine、Control Rig、Sequencer 或 Niagara Graph 写入。
- 中央服务直接路由所有开发者的本地 Editor。

## 已知限制

- Blueprint Graph 常用节点修改尚未作为正式 Operation 发布。
- Shared Knowledge Service、团队权限、Source Control 冲突感知属于后续版本。
- 大型项目性能方案已完成，Fixture 和自动基准框架尚待实现。
- 独立 Verify、全量索引和大批量处理本身可以耗时较长，必须使用进度、取消和可恢复任务。

## 发布产物

```text
UEAgentKit-0.7.0-UE5.6-Win64.zip
ue_agent_kit-0.7.0-py3-none-any.whl
SHA256SUMS.txt
release-manifest.json
```

插件 ZIP 不包含 PDB、Intermediate、Saved、DerivedDataCache 或 HostProject。远端 Push、Tag 和 GitHub Release 不属于本次本地发布范围。
