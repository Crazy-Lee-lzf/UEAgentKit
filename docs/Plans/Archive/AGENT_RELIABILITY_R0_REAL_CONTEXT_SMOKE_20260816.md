# UEAgentKit R0 真实 Reforge Context Smoke 记录

> 日期：2026-08-16
> 分支：`feature/agent-reliability`
> 基线：`876997d`（R0.1 完成时）；本片在 `876997d` 之上新增 R0-S 记录与 R0.2 实现
> 物证目录：`Output/ReforgeContextSmoke/`（`R01-baseline/` 保留 R0.1 基线原始 JSON，顶层为 R0.2 复跑结果）

---

## 1. Smoke 环境与数据（全部为真实 Reforge）

- 项目：`E:\WorkSpace\Reforge\Reforge.uproject`（UE 5.6）。
- 索引：`BlueprintContextExport -Root=/Game/ModularOffroadCars/BP -Profile=logic -Format=json` 真实导出 → `index build` 导入
  `Output/ReforgeContextSmoke/Export` → `.data/reforge-context-smoke.sqlite3`。
  - manifest：`Success=48 Failure=0`，exporter `0.7.0`，profile `logic`，projectName `Reforge`，commandlet 墙钟 5.20s。
  - 索引统计：assets=48（Blueprint 40 / WidgetBlueprint 8）、symbols=1486、graphs=228、nodes=5373、references=4430。
- MCP 服务器模式：写工具启用（workflow + Revision Export 已配置，未调用任何写 Tool）、Project Memory 启用（Reforge 项目键，**空库**）、Live Editor 启用但**无编辑器进程**。
  - `project.sources`：index/revisionFreshness/memory/liveEditor/changeSet 全部为 true。
- 只读证据：本片只运行了 `BlueprintContextExport`（只读导出）与 MCP 只读查询 Tool；未调用 Plan/Apply/Patch/Save；未修改任何 Reforge 资产。
- 确定性项目事实（来自 export.log，与 Smoke 无因果关系）：4 个 Widget 蓝图（`WB_CargoSpawner` / `WB_CarSpawner` / `WB_MaterialCustomization` / `WB_ModuleCustomization`）存在 8 条既有编译错误——`Set InputPitchScale` / `Set InputYawScale` 节点上的「目标」引脚已不存在。这是 Reforge 资产自身的既有问题，导出器仍正常导出（failureCount=0）。
- 已知边界：Memory 为空库（无既有记录可聚合）；Live Editor 无进程（降级）；index 只覆盖 `/Game/ModularOffroadCars/BP` 子树（48 资产），不代表全项目索引。

---

## 2. R0.1 基线三 Case 观察

三 Case 使用同一 query：`vehicle customization module integration with BP_VehicleBase`。
S1/S3 的显式目标：`/Game/ModularOffroadCars/BP/Components/BP_VehicleBase.BP_VehicleBase`（Pawn 派生 Blueprint，19 graphs / 394 nodes / 46 variables）。

### 2.1 Case S1（显式目标，4096）

| 项 | 值 |
|---|---|
| estimatedTokens | 1274 / 4096（约 31%），`truncated=false` |
| targetAssets | 1 条 found，identity 9 字段完整，summary（components=14/graphs=19/nodes=394/pins=1146/symbols=140/variables=46）与 metadata（revision_value/exporter_version/…）完整 |
| revisionState | `overall=fresh`，三方 SHA-256（index=revision-export=disk）完全相等 |
| memory | included=true 但 records=[]/nodes=[]（空库，约 108 tokens 信封占用，无决策价值） |
| liveEditor | included=false，`reason=live-editor-unavailable`（无编辑器进程） |
| risks | 仅 `live-editor-unavailable`(info) 一条；high=0 |
| nextExpansions | 2 条：`ue_get_asset{sections:[symbols,references,graphs,nodes]}` + `ue_find_references{both,depth1}` |
| relevantAssets | `[]`（R0.1 恒为空） |

结论：默认预算下「目标是谁、数据是否新鲜一致、先扩哪个、有无风险」四项决策信息全部保留且只用了 31% 预算。**缺失**：symbols/graphs/nodes/references 的具体内容（只有计数）、相关资产池（relevantAssets 为空）。

### 2.2 Case S2（query-only，4096）

| 项 | 值 |
|---|---|
| estimatedTokens | 646 / 4096，`truncated=false` |
| targetAssets / relevantAssets / nextExpansions | 全部 `[]` |
| revisionState | `overall=unavailable`（无目标可比较） |
| 原始 ue_search 证据 | 整句 query 的 asset/symbol 搜索均为 **0 命中**（FTS 整句短语 + LIKE 整串都不命中）；逐 token 搜索有命中 |

逐 token 候选池（`S2-evidence.json`）：

| token | scope | 命中数 | 是否截断 | 首屏 |
|---|---|---|---|---|
| `vehicle` | assets | 8 | page-limit | BP_Vehicle_SmallFrame_Standart、BP_VehicleBase、BP_VehicleMovement、BP_CarChangeSender_*… |
| `customization` | assets | 8 | page-limit | BP_CarChangeSender_*、BP_CarMaterialManager、BP_Engine_Base、BP_FuelTankData |
| `module` | assets | 6 | 无（完整） | BP_CarChangeSender_Modules、ModuleObjects/*、WB_ModuleCustomization |
| `BP_VehicleBase` | assets | 7 | 无（完整） | BP_VehicleBase + 6 个 Frame 子类 |
| `VehicleBase` | symbols | 8 | page-limit | 命中的资产与 asset 搜索基本重叠，symbol 页未带来新资产 |

结论：R0.1 的 query-only 输出对 Agent 没有可引用的下一步资产；「整句搜索 0 命中、分词搜索有命中」是 R0.2 必须分词的直接证据。

### 2.3 Case S3（显式目标，1024）

| 项 | 值 |
|---|---|
| estimatedTokens | 880 / 1024（86%），`truncated=true` |
| truncationReason | `target-asset-metadata,target-asset-summary,revision-comparisons,project-stats` |
| 保留 | target identity 全 9 字段、revisionState 核心（overall=fresh + 3 个 sha256，仅去 comparisons）、risks/riskSummary、nextExpansions 2 条、degradedSources |
| 裁剪 | target metadata、target summary、revision comparisons、project.stats |

结论：低预算下 risks / target identity / revision 核心优先于其它可展开内容，符合 R0.1 阶梯设计。客观顺序观察：target summary（规模计数）被裁而 nextExpansions 仍提示对同一资产展开——「扩哪个」保留、「目标多大」先丢，R0.2 不改变该既有顺序（只把候选插在更早的可裁档位）。

---

## 3. 四个核心问题结论

1. **4096 默认预算下决策信息是否保留？** 是。S1 仅用 31% 预算且零裁剪，目标身份/规模计数/三方一致性/风险/扩展路径全保留。
2. **显式目标一次 Task Context 是否足够开始分析？** 足够开始方向性分析（确认目标、新鲜度、风险、扩展路径），不足以直接改代码——仍需按 nextExpansions 调 `ue_get_asset` / `ue_find_references` 拿具体 symbols/edges。
3. **query-only 现在缺失什么最小候选？** 缺「分词驱动的相关资产候选」。整句搜索 0 命中；`BP_VehicleBase`（7，完整）+ `module`（6，完整）两个池就已覆盖焦点资产、继承者与 module 系，二者不相交，合计 13 条以内即可让 Agent 知道下一步看什么。
4. **R0.2 应返回什么候选？** 以分词 Asset Search 为主（symbol 补充在本证据中未带来新资产，只提供符号级证据）；候选必须带可解释的 `whyIncluded/matchKind`；无 score/confidence。Top N 依据：两完整池合计 13，取 8 可覆盖焦点 + 主关联族，符合「小型候选摘要」定位且不与预算冲突。

---

## 4. R0.2 复跑验证（同一真实环境）

在 R0.2 实现落地后，同一 Smoke 脚本复跑三 Case：

| Case | estimatedTokens | truncated | relevantAssets |
|---|---|---|---|
| S1（显式目标，4096） | 1982 | false | 8 条；显式目标 BP_VehicleBase 被排除 |
| S2（query-only，4096） | 1365 | false | 8 条；首位 = BP_VehicleBase（matchCount=3，asset-name-exact） |
| S3（显式目标，1024） | 880 | true | 0 条；truncationReason=`relevant-assets-metadata,relevant-assets-count,target-asset-metadata,...` |

S2 的 8 条候选（顺序即返回顺序）：BP_VehicleBase（3 term，asset-name-exact）→ BP_CarChangeSender_Modules（3 term）→ BP_CarChangeSender_Base（2 term + matchedSymbol `CustomizationZone`）→ CargoSpawner/CarSpawner/Materials（各 2 term）→ BP_Vehicle_SmallFrame_Standart（2 term）→ BP_Engine_Base（2 term）。排序键 =（matchCount 降序，首个命中 term 位置，assetPath）——确定性且可解释。

S3 证明 §4.5 预算关系成立：候选 metadata → 候选数量 先于 target metadata/summary 被裁剪，target identity / risks / revision 核心全部保留。

---

## 5. R0-S 停止条件核对（§3.6）

| 停止条件 | 结果 |
|---|---|
| 默认 4096 经常无法保留 target/revision/risk 核心 | 未触发（1274/4096 零裁剪） |
| Live/Memory 某一来源失败拖垮整体 | 未触发（live 不可用只降级该 section + info 风险；memory 空库正常聚合） |
| risks 出现非确定性推断 | 未触发（仅 live-editor-unavailable 一条确定事实） |
| 显式资产路径无法稳定定位真实资产 | 未触发（BP_VehicleBase found=true，revisionState fresh） |
| 响应明显超过 budget 且无法解释 | 未触发（880/1024，reason 逐步可解释） |

→ 按交接进入 R0.2。

---

## 6. 下一步（不自动执行）

- R0.3 / R1 未开始。R0.2 已知边界：候选只来自索引子树；`module` 类语义对应性来自路径/名称命中而非引用图；符号补充在真实数据上未带来新资产；Memory FTS CJK 限制不变。
