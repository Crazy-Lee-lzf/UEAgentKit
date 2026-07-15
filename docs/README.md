# UE Agent Kit 文档

本目录只包含面向用户和用户 AI 的正式文档，可以提交到 Git。

## 建议阅读顺序

1. `../README.md`：项目概览、当前能力和快速入口。
2. `CURRENT_STATUS.md`：当前版本已经实现什么、尚未实现什么。
3. `BUILD_AND_RUN.md`：构建、安装和运行方式。
4. `AI_USAGE.md`：AI 应如何查阅、检索和修改 UE 项目。
5. `PRODUCT_VISION.md`：最终产品目标和第一版范围。
6. `ARCHITECTURE.md`：系统架构、数据模型、索引和 Patch 设计。
7. `SAFE_WRITE_MODEL.md`：写入、编译验证、保存与回滚规则。
8. `PORTABILITY.md`：Python、路径、配置、Unicode 和发布的可移植性设计。
9. `ROADMAP.md`：公开开发路线。
10. `REFERENCE_POLICY.md`：第三方参考与独立实现规则。
11. `THIRD_PARTY_REFERENCE.md`：已研究项目及采用边界。
12. `RELEASE_DISTRIBUTION.md`：Source 与 Offline-bootstrap 发行方案。
13. `../spec/BPCTX_FORMAT.md`：BPCTX/1 格式规范。

## 文档受众

### 普通用户

优先阅读：

```text
README.md
CURRENT_STATUS.md
BUILD_AND_RUN.md
AI_USAGE.md
```

### AI 开发助手

在执行任务前，应按需读取：

```text
CURRENT_STATUS.md
ARCHITECTURE.md
SAFE_WRITE_MODEL.md
AI_USAGE.md
```

不要一次加载全部文档。先通过 `CURRENT_STATUS.md` 判断当前能力，再读取与任务直接相关的章节。

### 开发者

正式架构和接口约束仍以本目录为准。机器环境、实验记录、构建故障历史和内部任务审查存放在本地 `dev_docs/`，不属于公开文档。

## 文档维护规则

- 正式文档不得依赖某台机器的绝对路径才能理解。
- 示例可以使用占位符，如 `<UE_ROOT>`、`<PROJECT_ROOT>` 和 `<TOOL_ROOT>`。
- 当前功能变化后更新 `CURRENT_STATUS.md`。
- 架构或协议变化后更新 `ARCHITECTURE.md`。
- 新增写入操作后更新 `SAFE_WRITE_MODEL.md`。
- 用户可见工作流变化后更新 `README.md`、`BUILD_AND_RUN.md` 或 `AI_USAGE.md`。
- 内部试验、临时结论和未确认故障不要写入正式文档。
