# UEAgentKit 动画工具 P5–P9 详细执行计划

> 更新时间：2026-08-15
> 目标分支：`feature/live-editor-realtime-io`（worktree `E:\WorkSpace\UEAgentKit-RealtimeIO`）
> 上游计划：`docs/Plans/ANIMATION_TOOLS_FOLLOWUP_PLAN_20260806.md` §7–§11
> 当前基线：P0–P4 全部完成，32 提交领先 `main`，工作区干净，未推送
> 测试工程：`E:\WorkSpace\我的项目\我的项目.uproject`
> 目标接入工程：`E:\WorkSpace\ModelPreview\ModelPreview.uproject`

本计划把每个阶段拆成**可独立委派给 Agent 的子项**。每个子项标注：交付物、涉及文件、验收标准（完成检查）、依赖关系。主检查人按文末「统一门禁」逐项核对。

---

## 0. 统一门禁（每个子项完成后的检查清单）

任何子项声称完成时，主检查人必须验证：

```text
G1  Ruff 通过（src/ + tests/ 无新告警）
G2  Python 全量测试通过（pytest，新增用例计入总数）
G3  UE 5.6 Direct Plugin Build 通过（BuildPluginDirect.ps1）
G4  真实 UE5.6 Editor Smoke 通过（对应 TestMcp*.ps1 / 独立验证脚本）
G5  git diff --check 通过
G6  只本地 Commit，禁止 Push / Tag / Release / Reset / Stash / Revert
G7  未修改其他工程内容；可动「我的项目」+ 复制资产进来
G8  不提交 Build/ Output/ Backups/ Intermediate/ Saved/ 日志与测试生成资产
G9  文档已同步（新工具 docs/*.md + 本计划状态勾选 + tool_registry 计数一致）
```

**约束强调**（沿用既有 standing constraint，逐字保留）：仅本地 Commit；禁止 Push、Tag、Release、Reset、Stash、Revert；禁止提交 Build/、Output/、Backups/、Intermediate/、Saved/、日志与测试生成资产；不得处理 Composite asset mutation；不得修改其他工程内容（只能动「我的项目」+ 复制资产进来）。

---

## 1. P5 浮空诊断 Reader（`ue_diagnose_character_ground_contact`）

> **状态（2026-08-15）**：P5.1–P5.5 全部完成。C++ 读取器 `editor.diagnoseCharacterGroundContact`、Python 分类 `character_ground_contact.py`、MCP 工具 + `tool_registry` 注册 + 参数归一化、真实 UE5.6 Smoke、`docs/CHARACTER_GROUND_CONTACT_TOOL.md` 均已落地。**capability 决策变更**：P5.4 未新增独立 `character.ground-contact.inspect`，而是复用现有 `retarget.inspect`（与 `ue_diagnose_animation_scale` / `ue_diagnose_additive_animation` 等同门禁，避免重新引入 `retargetCapabilities` 不在 `POLICY_FIELDS` 的 policy 冲突）。

**目标**：只读诊断「浮空」来源（碰撞胶囊 / Mesh Offset / 动画），输出分类，不做任何修改。

**依赖**：无。可与 P6 / P7 并行。

### P5.1 C++ 只读读取器

- **交付物**：新增 C++ 方法 `TryDiagnoseCharacterGroundContactResult`（建议新文件 `EditorBridgeCharacterHandlers.cpp`，或并入 `EditorBridgeDiagnosticHandlers.cpp`，由执行 Agent 按现有 handler 风格决定），经 `EditorBridge` 暴露为 `diagnoseCharacterGroundContact`。
- **读取字段**：
  - Character Capsule `Radius` / `HalfHeight`；
  - `SkeletalMeshComponent` Relative Transform；
  - Mesh Bounds；
  - Skeleton Reference Root / Pelvis / Foot Transforms；
  - 动画最终 Root / Pelvis / Foot Component Pose（复用 additive evaluation 的 pose-composition 路径，注意 `FMemMark` / base-sampling 顺序陷阱，见 memory `additive-diagnosis-facts`）；
  - Foot Socket/Bone 到 Capsule 底部距离、左右脚最低点；
  - Root Motion Z / Retarget Root Z。
- **涉及文件**：`Plugin/UEAgentKit/Source/UEAgentKitEditor/Private/EditorBridgeCharacterHandlers.cpp`（新）、`EditorBridge.h`/`EditorBridge.cpp`（注册方法）、`UEAgentKitEditorModule.cpp`（如需）。
- **验收**：G3；`editor.diagnoseCharacterGroundContact` 可被 bridge JSON-RPC 调用返回结构化结果。

### P5.2 Python 分类模块

- **交付物**：`src/ue_agent_kit/character_ground_contact.py`，含 `classify_ground_contact()`。
- **分类**：`mesh-offset-candidate` / `capsule-size-candidate` / `animation-root-z-candidate` / `pelvis-offset-candidate` / `foot-ik-needed` / `insufficient-context`。
- **参考模板**：`additive_diagnose.py`（只读分类 + feasibility 判定）。
- **验收**：G1、G2；单元测试覆盖 6 类分类分支。

### P5.3 MCP 工具 + capability 接线

- **交付物**：`ue_diagnose_character_ground_contact`，read-only。
- **涉及文件**：`mcp_retarget_tools.py` 或新 `mcp_character_tools.py`（由执行 Agent 决定，保持现有 retarget 工具的 capability gating 风格）、`tool_registry.py` 注册。
- **capability**：新增 `character.ground-contact.inspect`（不要复用 `retarget.inspect`）。
- **验收**：G1、G2；`tool_names_for_mode` 计数 +1；capability 缺失时返回明确错误码（对齐 `retarget_capability_unavailable`）。

### P5.4 Policy 字段

- **交付物**：policy 新增能力列表字段（如 `characterCapabilities`），并在 `patches.py` 的 `POLICY_FIELDS` 中登记。
- **注意**：沿用已知冲突——只读诊断能力字段不能与 write plan 共存于同一 policy（见 memory `additive-diagnosis-facts`「policy conflict」）。Smoke 保持「只读诊断」单目标，不混入 write plan。
- **验收**：G1、G2；`_validate_policy` 不因新字段报 unknown-field。

### P5.5 Smoke + 文档

- **交付物**：`tests/integration/mcp_live_character_ground_contact_smoke.py` + `scripts/TestMcpCharacterGroundContact.ps1` + `docs/CHARACTER_GROUND_CONTACT_TOOL.md`。
- **真实样本**：用测试工程里的角色（心月狐骨架或已有 Character BP）跑一次真实 Editor 诊断。
- **验收**：G4、G9；Smoke 报告返回完整分类字段；磁盘 Package / SQLite 零写入。

---

## 2. P6 尾巴 / 衣服 / Cloth Reader（`ue_inspect_skeletal_secondary_motion`）

> **状态（2026-08-15）**：P6.1–P6.5 全部完成。C++ 读取器 `editor.inspectSkeletalSecondaryMotion`、Python 分类 `skeletal_secondary_motion.py`（8 分类）、MCP 工具 + `tool_registry` 注册 + 参数归一化、真实 UE5.6 Smoke、`docs/SKELETAL_SECONDARY_MOTION_TOOL.md` 均已落地。**capability 决策与 P5 一致**：P6.4 未新增独立 `character.secondary-motion.inspect`，复用现有 `retarget.inspect`（见 §1 注）。

**目标**：只读结构化读取 SkeletalMesh 次级运动（附加骨骼链 / 蒙皮 / 物理 / Cloth / AnimBP 节点），不做修改。**明确禁止**实现通用 AnimGraph 节点写入。

**依赖**：无。可与 P5 / P7 并行（难度高于 P5，物理/Cloth 读取更繁重）。

### P6.1 C++ 读取器

- **交付物**：`TryInspectSkeletalSecondaryMotionResult`，暴露为 `inspectSkeletalSecondaryMotion`。
- **读取字段**：附加骨骼链、父子层级、每骨骼顶点影响数量、最大/平均 Skin Weight、动画轨道是否存在、Physics Asset 路径、Physics Body/Constraint、Clothing Asset 数量、Cloth Section/LOD Mapping、Chaos Cloth Config 摘要、AnimBP 中 AnimDynamics/RigidBody/Spring 节点。
- **涉及文件**：`EditorBridgeCharacterHandlers.cpp` 或新 `EditorBridgePhysicsHandlers.cpp`（由执行 Agent 决定）+ `EditorBridge.h/.cpp`。
- **验收**：G3；返回结构化 JSON。

### P6.2 Python 分类模块

- **交付物**：`src/ue_agent_kit/skeletal_secondary_motion.py`，含 `classify_secondary_motion()`。
- **分类**：`missing-bones` / `missing-skin-weights` / `no-animation-tracks` / `no-secondary-motion-node` / `no-physics-bodies` / `cloth-data-present` / `cloth-data-missing` / `cloth-binding-incomplete`。
- **验收**：G1、G2；单元测试覆盖分类分支。

### P6.3 MCP 工具 + capability

- **交付物**：`ue_inspect_skeletal_secondary_motion`，read-only，capability `character.secondary-motion.inspect`。
- **涉及文件**：MCP tools 模块 + `tool_registry.py`。
- **验收**：G1、G2；计数 +1。

### P6.4 Policy 字段

- **交付物**：policy 能力字段登记（复用 P5.4 的字段或新增 `characterCapabilities` 列表项）。
- **验收**：同 P5.4。

### P6.5 Smoke + 文档

- **交付物**：`tests/integration/mcp_live_secondary_motion_smoke.py` + `scripts/TestMcpSecondaryMotion.ps1` + `docs/SKELETAL_SECONDARY_MOTION_TOOL.md`。
- **验收**：G4、G9。

---

## 3. P7 项目级可写配置

> **状态（2026-08-15）**：P7.1–P7.3 全部完成。`config.py` 新增 `resolve_project_policy`（manifest 驱动的项目路径 → Policy 解析）、`mcp_server.py` 新增 `--policy-profile` 自动解析、`RunMcp.ps1` 新增 `-PolicyProfile` 并支持省略 `-Policy` 自动解析；`config/projects/` 落地 `manifest.json` + 三个示例 Policy（`my-project-write.json` / `model-preview-read.json` / `model-preview-animation-write.json`）；`patches.py` 将 `retargetCapabilities` 登记进 `POLICY_FIELDS` 并校验（修复其此前无法通过 `_validate_policy` 的冲突）；`docs/PROJECT_LEVEL_CONFIG.md` + `tests/python/test_config.py` 落地。

**目标**：把「我的项目可写 / ModelPreview 默认只读 / ModelPreview 动画写单独 Policy」固化为项目级配置，禁止用 `allowedAssetRoots=/Game` 之类实现「取消只读」。

**依赖**：无（当前 `config.py` 尚无 policy 加载逻辑，需新做）。可与 P5 / P6 并行。

### P7.1 配置加载器

- **交付物**：`config.py` 新增项目级 policy 解析（项目路径 → policy 文件），并接入 `RunMcp.ps1` / CLI 的 `-Policy` 选择逻辑。
- **涉及文件**：`src/ue_agent_kit/config.py`、`scripts/RunMcp.ps1`、`ue-agent.py`（CLI）。
- **验收**：G1、G2；给定项目路径能解析到正确 policy，缺失时回退到现有显式 `-Policy`。

### P7.2 三个示例配置

- **交付物**：
  - `config/projects/my-project-write.json`（允许测试目录 + 心月狐动画目录写入）；
  - `config/projects/model-preview-read.json`（默认只读）；
  - `config/projects/model-preview-animation-write.json`（范围只覆盖确认后的动画目录）。
- **验收**：三个文件 schema 合法，`_validate_policy` 通过。

### P7.3 文档 + 单测

- **交付物**：`docs/PROJECT_LEVEL_CONFIG.md`；配置解析单测。
- **验收**：G1、G2、G9。

---

## 4. P8 ModelPreview 接入（执行手册，非代码）

> **状态（2026-08-15，收口）**：执行手册已撰写（`docs/MODELPREVIEW_INTEGRATION_MANUAL.md`）。用户已显式授权「全流程」，**只读基线（步骤 1–4）已执行完成**：步骤 1 插件已 junction 到 `Build/Compiled/UEAgentKit`（旧拷贝备份为 `.bak-20260731`）；步骤 2 Editor Status 通过（`pluginVersion 0.7.0`、`projectName=ModelPreview`、`state=available`）；步骤 3 资产扫描导出 104 资产（98 AnimSequence + 3 AnimMontage + 2 AimOffsetBlendSpace + 1 BlendSpace，`failureCount=0`）并建成 SQLite 索引；步骤 4 比例诊断完成。**关键发现：ModelPreview 全部 98 个重定向 AnimSequence 的 Skeleton 引用损坏**（导出侧 `skeletonPath=""`、运行时 `ue_diagnose_animation_scale` 返回 `missing-skeleton`），而同一动画在「我的项目」有效。**写阶段（步骤 5–9）已废弃（2026-08-15）**：用户确认 P8 接入的目的只是验证工具正确性，只读基线已达成该验证目的（插件版本握手、资产索引、只读诊断均通过，并定位到 skeleton 引用损坏问题），因此不再推进 ModelPreview 写入，也无需修复骨架引用。P8 收口为「只读基线验证完成 + 写阶段放弃」。

**目标**：把已验证的工作流接入 `E:\WorkSpace\ModelPreview`。这是一份**有序执行手册**，Agent 按顺序逐步执行并回报，不是一次性代码任务。

**依赖**：P7（配置就绪）+ P5/P6（只读基线）。

```text
1. 安装同一编译插件（InstallProjectPlugin / ManageProjectPluginLink）
2. 只读 Editor Status
3. 只读资产扫描
4. 生成当前动画比例基线（复用 P1 Audit）
5. 选择复制出的测试动画（可复制资产进来）
6. Live Apply + Undo
7. 用户确认
8. 扩大 Policy 到正式动画目录（P7 的 model-preview-animation-write.json）
9. 保存和独立验证
```

**第一轮禁止修改**：Skeleton / Skeletal Mesh / Physics Asset / Animation Blueprint / Capsule / Cloth / 正式关卡。只验证 AnimSequence Root Lock / Root Track 工作流。

**验收（收口）**：步骤 1–4 只读基线可复现（插件版本握手 / 资产索引 / 只读诊断）；步骤 5–9 放弃，不再验收。

---

## 5. P9 发布与合并（检查清单）

**目标**：达到合入 `main` 的条件并落地。**合并属重大动作，需用户明确授权后才执行。**

```text
- 单资产诊断/修改 API 稳定
- 响应字段区分 Runtime 与 Persisted（P0.2 已做）
- 保存与回滚真实 UE5.6 回归通过
- 项目级 Policy 示例完成（P7）
- 工具数量与文档更新
- 与 Memory/Context 分支公共协议无冲突
- 版本归属 0.8.0-dev Realtime Animation Tools
```

**验收**：G1–G9 全绿；`git log main..HEAD` 提交信息清晰；README/ROADMAP 工具计数同步。

---

## 6. 两个收口尾巴（非阻塞，可选）

### A. P4 批量写入闭环（`setAdditiveBasePoseFix` 批量）

当前 `setAdditiveBasePoseFix` 只有单资产写闭环。批量按 P2 模式复制：

```text
A.1  ue_plan_additive_base_pose_fix_batch + ue_get_...（不变式批量计划）
A.2  批量 live apply / undo（分片 8）
A.3  批量 save / verify / refresh index / rollback（分片 2）
```

**依赖**：P2 批量框架已存在，直接复用。**注意**：计划 §6 明确「复合资产重建仍在范围外」，本尾巴只覆盖 AnimSequence 的 `RefPoseSeq`/`RefFrameIndex` 批量替换。

### B. P3 持久化切片（Retarget 输出 → P2 Batch Plan 桥接）

```text
B.1  Authorized Retarget Save / Independent Verify
B.2  Paired Revision Export + SQLite Refresh + MCP Restart
B.3  eligible AnimSequence suggestion → 现有 P2 Batch Plan
```

**依赖**：P3 只读切片已完成，本切片接续其持久化边界（见计划 §5「下一条切片」）。

---

## 7. 推荐委派顺序

```text
第一波（可并行）：P5、P6、P7 —— 三条独立只读/配置轨道
第二波（闭环补充）：B（P3 持久化）、A（P4 批量写入）
第三波（接入）：P8（依赖 P7 + P5/P6）
第四波（收口）：P9（需用户明确授权合并）
```

每个子项完成 → 主检查人按 §0 门禁核对 → 本地 commit（不推送）→ 勾选本计划对应状态。
