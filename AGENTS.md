# AGENTS.md

面向在本仓库工作的 Code Agent。仓库事实优先于对话记忆、缓存摘要和历史交接文档。

## 1. 启动阅读顺序

```text
1. README.md
2. docs/PROJECT_STATUS.md       当前版本对外能力
3. docs/TOOL_CATALOG.md         Tool 索引与安全调用规则
4. 与本任务直接相关的 spec/ 契约
```

只读上面这些。`docs/RELEASE_*.md` 是历史发布说明，`dev_docs/` 是内部材料，
两者仅在具体问题需要时按需读取，不要预先加载全量文档。

## 2. 项目基线

- 产品：UE Agent Kit，Unreal Engine 5.6 / Windows x64。
- Python：3.11–3.14。
- 版本号以 `pyproject.toml` 与 `Plugin/UEAgentKit/UEAgentKit.uplugin` 为准，两处必须一致；
  未经发布决策不要单方面改版本号。

## 3. 文档分层

| 位置 | 性质 | 是否进 Git |
|---|---|---|
| `README.md`、`docs/` | 面向使用者的公开文档 | 是 |
| `spec/` | 机器契约与 schema | 是（内部验收报告除外） |
| `dev_docs/` | 内部开发材料 | **否**（`.gitignore` 排除） |
| `Output/`、`Build/`、`Backups/`、`.data/` | 构建产物、测试证据、本地数据 | 否 |

规则：

- 当前事实只写 `docs/PROJECT_STATUS.md`（对外）。内部状态写在 `dev_docs/CURRENT_STATE.md`。
- 不要新建第二个「状态」或「交接」文件来并行政令。
- 内部路径、机器名、进程号、工具链、测试资产名只允许出现在 `dev_docs/`。
- **不得 `git add -f dev_docs`**，也不得用 `!` 规则反向放行内部材料。

## 4. 安全边界（不得为便利放宽）

- 默认只读；写入必须显式启用。
- 写入保留 Policy / Revision / Dirty / Dry Run / 显式 Commit / 备份 / 独立验证 / rollback。
- 不提供任意 UObject 方法执行，也不提供任意 Python / Shell / Console 执行。
- 不做通用 Blueprint Graph CRUD、通用 Level Actor CRUD，也不做
  Material Graph / Niagara / Sequencer / Control Rig 的通用写入。
- 不自动 Save All；Save 永远是独立显式动作。
- P4 Submit / Revert / Delete 以及 `.uasset` / `.umap` 内容 resolve 由人执行。
- 实验性能力默认 OFF，并同时受 MCP CLI flag 与项目 ini 双门禁约束；
  它们不受项目 Write Policy allowlist 覆盖，不要混淆两者的适用范围。

## 5. 证据纪律

- Save 成功、独立 reload 成功、日志干净、模型自信，都**不等于**任务成功。
- 成功需要独立验证证据；证据缺失按缺失处理，不得推断通过。
- 不要在文档、注释或回答里引用未复测的测试数量或性能数字。
- 真实 UE 证据必须写明环境与范围，不得外推为生产默认可用。

## 6. 提交门禁

- 一项能力一项提交，不混入无关改动。
- 文本文件 UTF-8 **无 BOM** + **CRLF**，以 `.gitattributes` 为准。
- `git diff --check` 通过。
- Ruff 与相关 Python 测试通过。
- 改动 C++ 时执行 UE 5.6 Direct Build。
- 改动 Live Editor 行为时执行对应真实 UE 冒烟。
- 不提交 `Output/`、`Build/`、`Backups/`、`.data/`、日志、缓存、本地配置或测试资产。

## 7. Git 纪律

- 未经明确授权：不 push、不建 tag、不建 GitHub Release。
- 不 `reset` / `rebase` / `stash` / `revert`，不丢弃他人或其它 Agent 的工作。
- 不删除未知的未跟踪文件。
- 需要历史时用 `git log` / `git show` 只读查看，不改写历史。

## 8. 复杂度原则

- 优先复用既有抽象，不并行造第二套 Writer、Snapshot、Verify、Reference Graph、
  Memory 或 Change Set 系统。
- 不为提升覆盖率单独扩展 Tool family；扩展以真实项目中反复出现的阻塞为依据。