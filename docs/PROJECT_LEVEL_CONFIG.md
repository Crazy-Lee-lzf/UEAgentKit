# 项目级配置

UE Agent Kit 支持按 Unreal 项目绑定固定的 Project Profile 和 Write Policy。Profile 决定该项目默认使用哪份 Policy；Write Policy 决定允许写哪些项目、目录、资产类型、Operation 和字段。

## 推荐原则

- 每个真实项目使用独立的 `.uproject` 路径、SQLite Index、Memory Database 和 Work Root。
- 默认 profile 保持只读。
- 写入 profile 必须显式选择，并从隔离测试目录开始。
- 不要直接把示例 Policy 用于商业项目。

## 示例 Profile

仓库自带 `config/projects/` 只用于演示格式：

```json
{
  "schemaVersion": "1.0",
  "projects": {
    "ExampleProject": {
      "default": "example-read.json",
      "profiles": {
        "read": "example-read.json",
        "test-write": "example-write.json",
        "animation-write": "example-animation-write.json"
      }
    }
  }
}
```

真实项目应把 `ExampleProject`、资产目录和授权项替换成自己的值。

## 只读 Policy

```json
{
  "schemaVersion": "1.0",
  "validationEnabled": true,
  "commitEnabled": false,
  "allowedProjectNames": ["ExampleProject"],
  "allowedAssetRoots": ["/Game/UEAgentKitExamples"],
  "allowedOperations": [],
  "allowedAssetClasses": [],
  "requireRevision": true,
  "rejectDirtyPackages": true
}
```

`commitEnabled=false` 会关闭持久化写入。

## 最小写入 Policy

第一次接入真实项目时，建议只开放一个测试目录和少量 Operation，例如：

```json
{
  "schemaVersion": "1.0",
  "validationEnabled": true,
  "commitEnabled": true,
  "allowedProjectNames": ["ExampleProject"],
  "allowedAssetRoots": ["/Game/UEAgentKitTests"],
  "allowedOperations": ["setDataTableCell", "setDataTableRowFields"],
  "allowedAssetClasses": ["/Script/Engine.DataTable"],
  "requireRevision": true,
  "rejectDirtyPackages": true
}
```

确认 Plan、Dry Run、Save、Verify 和 rollback 流程稳定后，再逐步扩大允许范围。

## Source Control

Write Policy 和 P4 是两层独立约束：

```text
Write Policy
→ UE Agent Kit 是否允许修改这个资产/字段

P4 状态
→ 当前 workspace/client/opened/lock/have/head 是否适合协作修改
```

UE Agent Kit 不提供 Agent 侧 P4 Submit、Revert、P4-managed Delete 或通用 P4 命令透传；最终提交仍由人完成。

## 推荐接入顺序

```text
Clone
→ Build Plugin
→ 接入真实项目
→ 只读导出与索引
→ 启动只读 MCP
→ 检查 P4 mapping（如使用 P4）
→ 创建项目专属只读/测试写 Profile
→ 隔离资产 Plan / Dry Run
→ 再逐步开放真实配置目录
```

项目专属 Policy、本地数据库和 `.venv` 都应该在新项目环境重新建立，不要直接复制旧测试环境。
