# N0 评审决定与 Preview 零降级迁移（review round 2）

- 状态：**设计级修订 — 未实现、未在 UE 中运行、未执行任何 Runtime 验证**
- 日期：2026-10-24
- 工作树：`E:\WorkSpace\UEAgentKit-WT\native-tools-r1`（`feature/native-tools-contract-r1`，父提交 `3704d58`），基线 `main` `c5ce3419`
- 输入：上一轮 `N0_ARCHITECTURE_CONTRACT.md`、`N0_R1_READINESS_AND_GATE.md`；本轮 review 结论“Q1 的 Export-Only 迁移不可接受”
- 输出：本文件 = Q1 替代方案 + Q2–Q8 决策建议 + R1 复审 + 迁移/回滚 gates；两处合同文件同步修订；RunState 更新
- 红线：不编码 R1/N1/R2，不启动 UE，不改 `Plugin/**`、`src/**`、`tests/**`，不 push，不删除既有 Python 预览路径

## 0. 一页结论

1. **Q1 原方案（P11 菜单只导出 Context）被否决**。它把“已验收功能”降级成“数据导出”，不满足零降级；且团队成员/用户会误以为自动布局仍然可用。
2. 可行替代：**Editor 侧仍是一等公民的实时 Preview**，布局算法留在编辑器进程外的 Python；中间是一条**新的、最小的、被授权的 loopback broker 通道**，Editor 作为**客户端**主动连接（因为现有 Bridge 只能由 MCP 连 Editor，插件没有任何反向请求能力，见 §2）。
3. 该方案不引入编辑器内 Python、不让菜单 `CreateProc`、不依赖 Computer Use、不阻塞 game thread（异步 worker 线程 + Ticker 回收）。
4. **迁移期间 P11 既有 Preview 原样保留并继续可用**；新路径默认关闭（opt-in），只有满足 §11 的 parity + P10/P11 regression + 回滚演练后才允许默认开启与后续退役。
5. 通用 Apply 仍然 fail-closed：本文件不新增任何写能力；preview 结果永不被自动应用。
6. Q2–Q8 按上一轮推荐原则给出**批准建议**，其中 Q2 增加一条用户明确要求的例外：**registry 不可用绝不能导致 legacy P12 功能启动失败**（opt-in 迁移）。
7. R1 复审结论：descriptor/`actionId` 规则可精确兼容现有 schema，但必须补三条硬约束（本地化字段禁入哈希、未知 spawner 不猜、第三方 spawner 不默认触发模板创建）；T1–T8 收紧并新增 T10。

## 1. 零降级的定义（可检验）

“保留 Preview” 不是“还能导出上下文”，而是同时满足：

| 编号 | 验收定义 | 检验方式 |
|---|---|---|
| Z1 | 在真实 Blueprint 节点/引脚右键菜单中执行 Auto Arrange，**能得到 LayoutPlan**（`planId` + `layoutHash` + `movedCount` + `nodeCount`） | P11 同类 UE 自动化 + 新 broker 自动化 |
| Z2 | 结果与旧路径**完全相同**：同一 fixture、同一图、同一选项下 `planId`、`layoutHash`、`movedNodeIds` 逐字相等 | parity 脚本 + UE 证据 |
| Z3 | 执行前后 `packageDirty=false`、无事务、无 Save、无 Compile、graph fingerprint 不变 | 只读断言（同 P11 T5 形态） |
| Z4 | 不阻塞 Editor：菜单点击后 UI 可继续操作，结果异步返回；最坏 5 s 超时并给出明确失败 | 设计审查 + 后续 Runtime 测量 |
| Z5 | broker 不可用/离线时行为明确：菜单给出稳定错误码与 receipt，不伪造 plan，不静默无反应 | 负例自动化 |
| Z6 | 迁移全程旧路径可回退，且旧路径在退役前一直可用 | feature flag + rollback 演练 |

## 2. 现状拓扑（source-backed，非推测）

### 2.1 三个确定事实

**事实 A：EditorBridge 是单向的“MCP → Editor”请求/响应服务，插件没有任何反向请求或本地服务发现能力。**

- 插件只创建监听 socket 并 `Accept`：`EditorBridge.cpp:463` `CreateSocket`、`:476` `Bind/Listen`、`:587` `Accept`。
- 请求处理是“读一行 → 处理 → 回一行 → 关连接”：`EditorBridge.cpp:687-710`，`:689` `bCloseAfterResponse = true`，`:736` hello 才把 `bCloseAfterResponse` 置回 false（保持连接）。
- 插件源码中**没有** `FSocket::Connect`、没有 outbound 连接、没有 broker 客户端。
  （证据：`Build/n0-contract/Evidence/07-preview-ipc-facts.txt`，grep `CreateSocket|Connect(|ISocketSubsystem` 只命中监听路径。）

**事实 B：Python 侧只作为客户端连接 Editor，从不监听。**

- `editor_bridge.py:672-677` `socket.create_connection(...)`，`:708-713` 超时/连接错误处理。
- 全 `src/ue_agent_kit` 中无 `listen(` / `bind(` / `accept(` / 服务端 socket。
  （同上证据文件。）

**事实 C：P11 菜单在 game thread 内同步执行 Python，并同步等待结果。**

- 菜单 action：`P11Menu.cpp:197-215` → `BuildP9MenuPreviewInputs`（`EditorBridgeGraphHandlers.cpp:434+`）。
- 写 preflight/envelope 到 `Saved/UEAgentKit/P11Preview`：`P11Menu.cpp:271-282`。
- `IPythonScriptPlugin::ExecPythonCommand` 执行 `runpy.run_path(worker)`：`P11Menu.cpp:302-321`。
- 同步读回 `preview.json` 并写 `menu-invocation.json`：`P11Menu.cpp:332-366`。
- P11 自动化在 `TryExecuteToolUIAction` 返回后**立即**读 receipt 并断言 `planProduced=true`：`BlueprintGraphLayoutP11Test.cpp:818-848`。

→ 结论：**不存在**“Editor 向 MCP 反向要结果”的通道；若要保留菜单 Preview，必须新增通道，且必须承认 P11 当前是同步契约，异步化会改变测试形态（见 §7、§9）。

### 2.2 现状时序（as-is）

```text
User right-click (Blueprint node/pin)
  └─ UToolMenus dynamic section            P11Menu.cpp:132-195
      └─ ExecutePreview (game thread)      P11Menu.cpp:197-215
          ├─ BuildPreflight + Envelope     EditorBridgeGraphHandlers.cpp:434+
          ├─ write preflight/envelope      P11Menu.cpp:271-282
          ├─ IPythonScriptPlugin::Exec     P11Menu.cpp:302-321   [in-process Python]
          ├─ worker: build_preview         tools/p9_layout_preview_worker.py:39-53
          ├─ read preview.json             P11Menu.cpp:332-351
          └─ write receipt + modal         P11Menu.cpp:361-379
Agent (separately, unchanged):
  MCP -> TCP -> Editor: preflight, context      editor_bridge.py:672+
  MCP: build_preview locally                    mcp_graph_layout_preview.py:87-107
```

注意：MCP 的 `ue_preview_blueprint_graph_layout` 本来就把算法放在 **MCP 进程**里算（`graph_layout_preview.py:61-334`），不依赖编辑器内 Python。这意味着“算法在编辑器外”早有既成事实，缺的只是**菜单触发**这条链路。

## 3. 目标拓扑与“最小新通道”

### 3.1 目标

```text
User right-click
  └─ ToolMenus entry (native C++)
      └─ FUEAgentKitNativePreviewService (game thread, 不阻塞)
          ├─ 原生快照：preflight + context（复用既有只读构建，不写资产）
          ├─ 序列化 job（含 revision/fingerprint/session/token）
          ├─ hand-off 给 worker 线程（无 UObject）
          └─ Tick 回收 → 校验 → Receipt + 非模态通知
Worker thread
  └─ LayoutBrokerClient: connect 127.0.0.1:<descriptor.port>
        ├─ 发送 1 行 job（≤1 MiB）
        └─ 读取 1 行 result（≤1 MiB，5 s 超时，可取消）
Broker daemon (Python, 进程外，用户/CLI 启动)
  └─ build_preview(...)  ← 与 MCP/旧 worker 同一个函数
        └─ 返回 plan/positions/metrics（不写资产、不连 Editor）
```

### 3.2 为什么选“Editor 主动连 broker”，而不是：

| 备选 | 为什么不选 |
|---|---|
| 复用现有 EditorBridge 做反向请求 | 现有协议/实现是单向的（§2.1 事实 A/B）；改动它会把“MCP→Editor”安全模型反转，风险远大于新增专用通道，且需要改已验证的 bridge 代码 |
| 菜单 `CreateProc(py.exe)` / `CreateProc` 任意 worker | 用户明确禁止；且 headless 环境不可靠（P11 已记录 py.exe/cmd 失败，`P11Menu.cpp:298-301`） |
| 编辑器内 `IPythonScriptPlugin` | 用户明确禁止；也是本次要退役的对象 |
| 文件轮询（Editor 写 job 文件，broker 写 result 文件） | 可行但需要额外的目录 ACL/竞态/清理策略，且把“执行入口”变成文件路径，攻击面更大；**仅作为 broker 完全不可用时的后备研究项，不作为主方案** |
| Editor 内嵌算法（C++ 重写布局） | 属 N4，需 parity + 性能证据；不能作为本轮迁移前提 |
| Computer Use / 截图 | 明确禁止 |

### 3.3 最小新通道（只加这些）

1. **Broker descriptor**（Editor 只读）：`<Project>/Saved/UEAgentKit/LayoutBroker.json`。
2. **Broker 协议**：newline-delimited JSON，单请求单响应，loopback only，token 认证，`ueak.layoutbroker/1.0`。
3. **Editor 出站客户端**：`FSocket::Connect`（`Sockets.h:109`）+ `ISocketSubsystem::CreateSocket`（已在插件使用），运行在独立 worker 线程。
4. **Broker 进程**：Python，复用 `graph_layout_preview.build_preview`，由用户/CLI 启动（不由 Editor 启动）。
5. **不改**现有 `EditorBridge` 协议、端口、令牌、capability 表；不改 P7–P11 写路径。

## 4. Broker 协议规范

### 4.1 Descriptor（Editor 读取、broker 写入）

```json
{
  "schemaVersion": "ueak.layoutbroker-descriptor/1.0",
  "address": "127.0.0.1",
  "port": 52173,
  "token": "b7f1c2d3e4a5968778695a4b3c2d1e0f",
  "projectName": "P11AutomationHost",
  "projectPathHash": "sha1:0d1e2f3a4b5c6d7e8f90123456789abcdef01234",
  "brokerVersion": "0.9.0",
  "supportedContractVersions": ["ueak.layoutbroker/1.0"],
  "algorithms": [
    { "id": "p0-layered", "version": "1.0.0" }
  ],
  "startedAtUtc": "2026-10-24T00:10:00Z",
  "processId": 12345,
  "maxConcurrentJobs": 1
}
```

Editor 校验（任一失败 → `native-preview-broker-rejected`，不连接）：

| 检查 | 规则 |
|---|---|
| schemaVersion | 精确 `ueak.layoutbroker-descriptor/1.0` |
| address | 必须是 `127.0.0.1`（拒绝 `0.0.0.0`、主机名、IPv6、域名） |
| port | 1–65535，且非本 Editor 的 bridge 端口 |
| token | 32–128 位十六进制；只用于本会话，不落日志/receipt/通知 |
| projectPathHash | 与本 Editor 的 `ProjectPathHash` 精确相等（同 `EditorBridge.cpp:85-99` 算法） |
| projectName | 精确相等 |
| supportedContractVersions | 含 `ueak.layoutbroker/1.0` |
| algorithms | 必须含 `p0-layered/1.0.0`（与 `build_preview` 实现版本对齐） |
| startedAtUtc / processId | 年龄 ≤ 12 h；**进程存在性不强制**（跨权限域可能查不到），但 descriptor 过期即拒绝 |
| 文件形态 | 必须是普通文件；拒绝重解析点/目录（实现时用 `IPlatformFile` 属性检查，具体 API 待 Runtime 验证） |

### 4.2 Request（Editor → Broker，单行，≤1 MiB）

```json
{
  "schemaVersion": "ueak.layoutbroker/1.0",
  "requestId": "6f0d1c2b-3a4e-4d5f-8a9b-0c1d2e3f4a5b",
  "sessionId": "editor-session-3f6d2a1c",
  "projectName": "P11AutomationHost",
  "projectPathHash": "sha1:0d1e2f3a4b5c6d7e8f90123456789abcdef01234",
  "token": "b7f1c2d3e4a5968778695a4b3c2d1e0f",
  "sentAtUtc": "2026-10-24T00:12:01Z",
  "deadlineMs": 5000,
  "algorithm": { "id": "p0-layered", "version": "1.0.0" },
  "options": { "mode": "full", "spacingX": 96, "spacingY": 64, "targetWidth": 1800, "seed": 0 },
  "snapshot": {
    "preflight": {},
    "contextEnvelope": {}
  }
}
```

约束：

- `snapshot.preflight` / `snapshot.contextEnvelope` 就是现有 Bridge 只读方法的原始结果（`ueak.graphlayoutpreflight/1.0`、`ueak.graphlayoutcontext/1.0`），**不重新定义数据结构**。
- `options` 与 `ue_preview_blueprint_graph_layout` 默认值一致（96/64/1800/0，`mcp_graph_layout_preview.py:30-35`），保证与旧 worker（`tools/p9_layout_preview_worker.py:39-47`）同参数。
- 请求中**没有任何文件系统路径、命令、URL、可执行名**；broker 也不得从请求中读出任何可执行内容。

### 4.3 Response（Broker → Editor，单行，≤1 MiB）

成功：

```json
{
  "schemaVersion": "ueak.layoutbroker/1.0",
  "requestId": "6f0d1c2b-3a4e-4d5f-8a9b-0c1d2e3f4a5b",
  "ok": true,
  "readOnly": true,
  "algorithm": { "id": "p0-layered", "version": "1.0.0" },
  "durationMs": 42,
  "result": {
    "schemaVersion": "ueak.graphlayoutpreview/1.0",
    "planId": "layout:1a2b3c4d",
    "layoutHash": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
    "baseGraphHash": "sha256:9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a0b9c8d7e6f5a4b3c2d1e0f9a8b",
    "assetPath": "/Game/UEAgentKitP11/BP_LayoutP11.BP_LayoutP11",
    "graphGuid": "01234567-89ab-cdef-0123-456789abcdef",
    "nodeCount": 11,
    "movedCount": 9,
    "positions": []
  }
}
```

失败：

```json
{
  "schemaVersion": "ueak.layoutbroker/1.0",
  "requestId": "6f0d1c2b-3a4e-4d5f-8a9b-0c1d2e3f4a5b",
  "ok": false,
  "error": {
    "code": "graph-layout-preview-invalid-options",
    "message": "spacing_x must be an integer from 1 through 4096.",
    "retryable": false
  }
}
```

`result` 的字段集合必须与 `build_preview` 返回值一致（`graph_layout_preview.py:295-334`）；Editor 只接受其中用于展示/校验的子集，并**重新计算** `layoutHash`。

> **Correction 2026-10-24（M2.0 offline audit）**：实际 `build_preview` 返回的 `baseGraphHash` 是
> `sha256:<64 lowercase hex>`（`graph_contracts` 的 graph `semanticHash`），不是上面旧示例的 `sha1:`。
> 示例已更正；Editor 必须按实际格式校验并重算，不得硬编码旧示例。M1/M2 的 wire 校验均使用 `sha256`。

### 4.4 稳定错误码（preview/broker）

```text
native-preview-disabled                 feature flag 关闭
native-preview-broker-descriptor-missing
native-preview-broker-descriptor-invalid
native-preview-broker-unavailable       connect 失败/拒绝
native-preview-timeout                  超时
native-preview-cancelled                用户/会话取消
native-preview-token-rejected           401 语义
native-preview-version-unsupported      descriptor/protocol/算法版本
native-preview-request-too-large
native-preview-response-too-large
native-preview-response-invalid         JSON/形状不合法
native-preview-identity-mismatch        assetPath/graphGuid/session 不符
native-preview-revision-conflict        结果期间 package revision 变化
native-preview-fingerprint-conflict     结果期间图变化
native-preview-plan-hash-mismatch       重算 layoutHash 不等
native-preview-plan-invalid             shape/node 集不合法
native-preview-stale-snapshot           请求前快照已过期
native-preview-worker-failed            broker 内部错误
```

Editor 侧错误**不**返回 broker 的原始 message 给 UI（防止路径/令牌泄露），只保留 code + 有界的自有描述；原始响应写 receipt 的 `brokerDiagnostic` 时也要先脱敏。

### 4.5 Broker 侧硬要求

1. 只绑定 `127.0.0.1`，端口由 OS 分配；`maxConcurrentJobs=1`；每会话每分钟 ≤10 次。
2. 校验 token/projectPathHash/projectName/deadline/schema/尺寸；未知字段拒绝；请求 >1 MiB 直接断开。
3. 只能调用 `build_preview`（纯函数，`graph_layout_preview.py:61-334`），不得写文件、不得连 Editor、不得执行子进程。
4. 响应必须带 `requestId`；不得缓存跨请求结果；异常统一转 `ok=false`。
5. 退出时删除 descriptor（仅当 token 仍是自己写入的那个），与 EditorBridge 的 descriptor 生命周期策略一致（`LIVE_EDITOR_BRIDGE.md:69-70`）。

## 5. 时序图（含失败分支）

### 5.1 Happy path

```text
Menu            Service(game)        Worker            Broker
 | click            |                  |                 |
 |----------------->|                  |                 |
 |                  | resolve graph    |                 |
 |                  | build preflight  |                 |
 |                  | build envelope   |                 |
 |                  | read descriptor  |                 |
 |                  |----------------->|                 |
 |                  |  job(request)    | connect 127.0.0.1|
 |                  |                  |---------------->|
 |                  |                  |                 | verify token/project/schema
 |                  |                  |                 | build_preview (same fn)
 |                  |                  |<----------------|
 |                  |                  | validate size/id|
 |                  |<-----------------|                 |
 |                  | recheck revision/fingerprint        |
 |                  | recompute layoutHash, node set      |
 |                  | write receipt (P11 keys)            |
 |                  | non-modal notification              |
 |<-----------------|                  |                 |
```

### 5.2 Broker 不可用 + legacy 回退（opt-in 阶段）

```text
Menu -> Service: request
Service -> Descriptor: missing/invalid  => native-preview-broker-descriptor-missing
Service: if LegacyLayoutPreviewFallback && IsAvailable()
    -> legacy P11 path (in-process Python)   [unchanged]
    receipt.previewPath="legacy-python-in-process"
else
    receipt.ok=false; notification(error); PREVIEW NOT PRODUCED
```

### 5.3 超时 / 取消 / 会话结束

```text
t+5s    Worker: recv timeout -> native-preview-timeout -> socket close
t+any   Cancel: atomic flag + socket close -> native-preview-cancelled
end     Module shutdown / PIE start: cancel in-flight; receipt terminal state written
        (never leave job "running" after shutdown; audit records terminal reason)
```

### 5.4 结果期间状态变化（stale）

```text
Service tick: compare current packageRevision/graphFingerprint with snapshot
  mismatch -> native-preview-revision-conflict / -fingerprint-conflict
              receipt: ok=false, stale=true, plan NOT displayed as valid
              (仍保留 planId 供排查? 不放，避免误用；receipt 只留 resultDigest)
```

### 5.5 恶意/不合法 broker 响应

```text
response: unknown fields / huge / duplicate requestId / wrong assetPath
  -> native-preview-response-invalid | -identity-mismatch
response: plan shape ok but layoutHash mismatch
  -> recompute -> native-preview-plan-hash-mismatch
response: node set != snapshot node set
  -> native-preview-plan-invalid
任何分支：不写资产、不显示 plan、不改 dirty、不进入 Apply
```

## 6. 异步与线程设计（不阻塞 game thread）

### 6.1 组件与职责

| 组件 | 线程 | 职责 | 禁止 |
|---|---|---|---|
| `FUEAgentKitNativePreviewService` | game | 快照、状态机、deadline、结果校验、receipt、通知、审计 | 阻塞等待、spawn 进程 |
| `FUEAgentKitLayoutBrokerClient` | worker (`FRunnable`) | connect/send/recv/超时/取消/尺寸限制 | 访问 `GEditor`/UObject、写文件 |
| `FUEAgentKitLayoutBrokerDescriptor` | game | 读取/校验 descriptor | 写入 descriptor、缓存 token 到日志 |
| `FUEAgentKitNativePreviewMenu` | game | ToolMenus 条目 + 上下文提取 | 业务逻辑、第二套校验 |

### 6.2 状态机

```text
Idle
 └─(Request)-> Snapshotting (game)
      ├─(fail)-> Failed
      └─(ok)-> Queued
            └─(worker start)-> Connecting
                  ├─(fail)-> Failed(broker-unavailable)
                  └─-> Sending -> Awaiting
                        ├─(timeout)-> Failed(timeout)
                        ├─(cancel)-> Cancelled
                        ├─(io error)-> Failed(io)
                        └─(response)-> Validating (game)
                              ├─(invalid)-> Failed(*)
                              └─(ok)-> Completed -> (receipt + notification)
```

约束：单一 in-flight job；`Validating/Completed/Failed/Cancelled` 都是终态并写 receipt；`Queued` 前的失败也要写 receipt（否则用户/自动化看不到失败）。

### 6.3 不变量

| ID | 不变量 |
|---|---|
| P1 | game thread 任何时刻都不得等待 socket/worker；只允许原子状态轮询 |
| P2 | worker 线程不得持有/解引用任何 UObject；快照在 game thread 完成序列化 |
| P3 | 请求 ≤1 MiB、响应 ≤1 MiB、总 deadline ≤5 s（可配置但上有硬上限 10 s） |
| P4 | 单一 in-flight；重复请求返回 `native-preview-busy`（新码，加入 §4.4） |
| P5 | 取消/超时/关闭必须关闭 socket 并写终态 receipt；不得留“幽灵 job” |
| P6 | token、descriptor 绝对路径、broker 原始诊断不得进入 UI/通知/receipt/日志 |
| P7 | 结果校验失败时绝不展示 planId/layoutHash 为“有效结果” |

## 7. 结果展示与 receipt 兼容

### 7.1 展示

- 成功：非模态通知（`FSlateNotificationManager::AddNotification`，`NotificationManager.h:96`）显示 `planId / layoutHash / movedCount / nodeCount`，点击可打开 receipt 目录（不自动打开资产）。
- 失败：非模态错误通知（同一入口），只显示稳定 code + 简短建议。
- 自动化/无 Slate：通知可被 test seam 抑制；**receipt 是唯一必达证据**。
- 禁止：`FMessageDialog` 模态阻塞、自动 Apply、自动 Save。

### 7.2 receipt 兼容（关键）

P11 自动化断言了 `Saved/UEAgentKit/P11Preview/menu-invocation.json` 的键（`BlueprintGraphLayoutP11Test.cpp:828-848`）。新路径**必须**保留同名文件与这些键，并新增字段：

| 键 | 旧路径 | 新路径 |
|---|---|---|
| `ok` | 有 | 有（终态） |
| `planProduced` | 有 | 有（成功 true；失败 false） |
| `readOnly/saved/compiled` | 有 | 有（恒 true/false/false） |
| `packageDirtyBefore/After` | 有 | 有 |
| `contextKind/contextNodeGuid/contextPinName` | 有 | 有 |
| `assetPath/graphGuid/packageRevision/graphFingerprint` | 有 | 有 |
| `planId/layoutHash/nodeCount/movedCount` | 有 | 成功时有 |
| `workerMode` | `in-process-python` | `layout-broker` / `legacy-python-in-process` / `unavailable` |
| `previewPath`（新） | — | `native-broker` / `legacy-python-in-process` |
| `requestId`、`brokerVersion`、`algorithm`（新） | — | 有 |
| `stale`、`errorCode`（新） | 部分 | 有 |

## 8. 安全威胁模型（增量）

| 威胁 | 缓解 |
|---|---|
| 本机恶意进程伪造 broker | token（每次会话随机、只经 descriptor）+ projectPathHash + 端到端 requestId；descriptor 过期拒绝；同用户账户信任边界与现有 Bridge 相同（`LIVE_EDITOR_BRIDGE.md:462-464`） |
| Token 泄露到 UI/日志/receipt | 结构上不放入任何输出；日志过滤；receipt 只存 hash 前缀 |
| 重放旧结果 | requestId UUID + 单次使用；broker 拒绝重复 id；Editor 只接受当前 requestId |
| 任意路径/命令执行 | 请求只含标识符与内联快照；broker 不执行请求内任何字符串；无文件路径字段 |
| 超大响应/内存 DoS | 1 MiB 上限、解析前检查、单 in-flight、5 s deadline |
| 恶意 plan（越界坐标/删节点/换图） | 形状校验 + `ComputeLayoutHash` 重算 + node 集相等 + asset/graph/revision/fingerprint 相等；**preview 不写** |
| 过期 plan 被误当有效 | 结果时重查 revision/fingerprint，不一致即 `stale` 且不展示为有效 |
| Broker 卡死 | deadline + socket close + 终止态 receipt；不影响 Editor 其他功能 |
| 端口/descriptor 竞态 | 每次请求重读 descriptor；token 变化即重新校验；失败快速返回 |

## 9. 迁移阶段、feature flag、cutover、rollback

### 9.1 阶段（每阶段独立可回退）

| 阶段 | 内容 | 默认状态 | 旧路径 | 完成判据 |
|---|---|---|---|---|
| M0 | 只加“影子”骨架：registry（`off`）、broker descriptor 读取、客户端（不接菜单） | 关闭 | 原样 | 编译 + 单测；legacy 全绿 |
| M1 | broker daemon（Python，复用 `build_preview`）+ 协议/安全测试（无需 UE） | 手动启动 | 原样 | Python 测试：协议、token、超时、尺寸、重放 |
| M2 | 原生快照工具（`graph.preflight.get`/`graph.context.get`）+ 与 legacy 数据对拍 | `shadow` | 原样 | 字段级一致（除新增信封） |
| M3 | 菜单 items：`NativeLayoutPreview=on` 但 `LegacyFallback=true`；成功走 broker，broker 不可用回退 legacy | opt-in | 可用 | UE 自动化：broker happy + fallback + 只读断言 |
| M4 | 默认开启（仍保留 legacy 代码与回退开关） | 默认 on | 可用（回退） | parity（Z2）+ P10/P11 regression + 回滚演练 |
| M5 | 关闭回退（`LegacyFallback=false`） | on | 仅显式开启 | 负例：broker 不可用时明确失败，不静默 |
| M6 | 退役 legacy（删除 in-editor Python 与 py.exe 菜单） | — | 删除 | §9.4 全部满足 + 用户批准 |

### 9.2 Feature flag（不可被 Agent 参数覆盖）

```ini
[UEAgentKit]
NativeToolRegistry=off        ; off|shadow|active
NativeLayoutPreview=off       ; off|on
LegacyLayoutPreviewFallback=true
```

- 读取：`GConfig`（`Misc/ConfigCacheIni.h`），仅在启动时读一次；运行期不可改（防注入）。
- 测试覆盖：仅当命令行含 `-UEAgentKitAutomationChild` 且显式 `-UEAgentKitNativePreviewTest=<mode>` 时生效；**MCP tool 参数永远不能设置这些 flag**。
- `NativeToolRegistry=off` 意味着**清单缺失/损坏也不影响 legacy**（见 N0-D01 修订）。

### 9.3 Cutover 判据（全部满足）

1. Z1–Z6 全部有可复核证据。
2. parity：同一 fixture 下 `planId`/`layoutHash`/`movedNodeIds` 与 legacy 逐字相等（N ≥ 3 个不同图：EventGraph、Function、含 macro/cast）。
3. P10（11 节点 Apply + Undo/Redo/Save/Reload）与 P11（Selection/Incremental/Menu/Scope）回归**不降级**：断言集合不得减少。
4. 负例：dirty、PIE、截断图、broker down、timeout、错误 token、错误 revision、超大响应、恶意 plan 全部稳定拒绝且零写入。
5. 回滚演练：flag 改回后 1 次重启内恢复 legacy 行为，receipt `previewPath` 正确。
6. 资源：同机单 UE 构建/Editor；broker 与 Editor 生命周期互不拖死。

### 9.4 退役 gates（删除旧代码前）

- 连续 ≥2 个验收周期默认走 native 且无回退事件；
- 旧路径的测试改为“已删除”而**不是**弱化断言：P11 的 `planProduced/readOnly/dirty` 断言迁移到 native 路径后数量不减少；
- `IPythonScriptPlugin`、`py.exe`、`PythonScriptPlugin` 三个 grep 门禁为 0；
- 用户明确批准退役。

### 9.5 回滚矩阵

| 故障 | 动作 | 结果 |
|---|---|---|
| broker 崩溃 | Editor 不重启；下次请求重新读 descriptor | 失败码 + receipt；legacy 可回退 |
| 版本不匹配 | 拒绝连接 | `native-preview-version-unsupported` |
| 新路径产生错误 plan | 关闭 flag | 立即回到 legacy |
| P11 回归失败 | 停止 cutover，保留 legacy | 不影响已发布行为 |

## 10. Q2–Q8 决策建议（草拟批准）

| 编号 | 建议 | 理由 | 状态 |
|---|---|---|---|
| Q1（修订） | **否决 Export-Only；采用本文件 broker 方案** | 零降级要求；见 §1–§9 | 建议批准（替代原 Q1） |
| Q2 | **批准** runtime manifest + compiled immutable safety，**但加例外**：registry 必须 opt-in/非致命，缺清单时 legacy P12 功能照常启动 | 用户明确要求；避免把文档迁移变成可用性回归 | 批准（含例外） |
| Q3 | **批准** single `native.invoke` + legacy aliases | 单一 handler，避免双实现 | 批准 |
| Q4 | **批准** audit 写 `Saved/`、不含敏感字段；仅 write/test-only 持久化；环形缓冲 256；文件按 session 滚动、单文件 ≤4 MiB | 可诊断且不拖慢只读路径 | 批准 |
| Q5 | **批准** Content Browser 上下文条目推迟 | 无已验证扩展点，避免猜测 | 批准 |
| Q6 | **批准** 101→102 文档更正，但**单独 doc-only commit**，不与代码/合同混提 | 可审查、可回滚 | 批准 |
| Q7 | **批准** `editor`/`workflow` 作为 native 清单私有 domain 值，不改 `ueak.operation/1.0` | 避免 schema 语义漂移 | 批准 |
| Q8 | **批准** 1 MiB 上限 + 显式分页（cursor/`nextCursor`/`truncated`/`totalAvailable`），禁止静默丢弃 | 大图/大目录可预期 | 批准 |

保留待用户裁决（新增）：

| 编号 | 问题 | 选项 |
|---|---|---|
| Q9 | broker 由谁启动/授权 | (a) 用户手动 `scripts\RunLayoutBroker.ps1`（推荐，最小信任面）；(b) MCP server 启动时顺带起 broker（方便但扩大 MCP 权限）；(c) 安装为 Windows 服务（重，暂不建议） |
| Q10 | 原生 Preview 是否一开始就对**所有工程**可见，还是先只在 disposable host | 推荐：先 disposable + opt-in，稳定后再放开（避免用户误解为生产 Apply） |
| Q11 | 失败时默认是否回退 legacy | 推荐：M3–M4 默认回退；M5 起不默认回退（明确失败） |
| Q12 | 通知 vs 结果面板 | 推荐：先非模态通知 + receipt；面板（plan 预览列表）另立阶段，避免首版 UI 复杂化 |

## 11. R1 复审（合同与测试）

### 11.1 stable actionId/hash 的精确兼容

| 项 | 结论 | 必须的加固 |
|---|---|---|
| `actionId` | 由 `discovery_key_projection` + `make_action_id`（`descriptors.py:86-104`）唯一决定 | C++ 必须逐字节复刻 `canonical_json`（UTF-8 键排序、`.17g` 浮点、无空格）；对 200 条样本做 golden 对拍 |
| `descriptorDigest` | 排除自身与 `discoveredAtUtc`（`descriptors.py:107-120`） | 明确“哪些字段进哈希”的清单；任何新增字段必须默认不进 digest 除非契约升级 |
| 本地化字段 | `title/menuName/category/keywords` 是 schema **必填**但**禁入** `discoveryKey` | 允许出现在结果、排除出所有哈希；T2 增加负例（把 title 放进 discoveryKey 必须被拒） |
| `actionDatabaseFingerprint` | 由排序后的 actionId 集合决定 | 跨进程比较；插件构建/引擎版本变化时显式标记 `identityChanged=true`，不静默 |
| 冲突 | 32 hex（128 bit） | 加载时检测重复 actionId → `unsupported` 并告警，不得覆盖先到者之外的行为 |
| 5.7 兼容 | 未验证 | 只承诺 5.6.1；升级时先跑 T4/T6 再谈 |

### 11.2 NodeRegistry 只读对原 schema 的精确兼容

- 输出必须直接通过 `validate_node_descriptor`（`descriptors.py:139`）与 `node-descriptor.schema.json`；**不得**输出 schema 之外的顶层字段（`additionalProperties:false`）。
- `graphKinds` 必须用 `typed_graph` 词汇（`event/function/macro/ubergraph/delegate-signature/other`，`typed_graph.py:380`）；按 D-01，`GT_Ubergraph` 映射为 `event`（V1 Apply 不接受 `ubergraph`），映射表与理由写进 R1.2。
- `pins[].type` 必须包含 schema 要求的 7 个字段；`kind` 由 `PinCategory == PC_Exec`（`EdGraphSchema_K2.h:366,733`）决定，不能用方向猜。
- `risk`/`capability`/`trustLevel` 必须来自冻结映射表，不得按类名猜测。
- 只读工具的结果如需分页，必须走 Q8 的分页契约；截断时 `complete=false`。

### 11.3 Asset spawner / unknown 行为

| 类别 | 行为 | 理由 |
|---|---|---|
| `BlueprintAssetNodeSpawner` 等资产动作 | 标 `not-a-node-action`，不计入 `unsupported[]`，不参与 `complete` 判定 | 避免把“不是节点动作”误报为缺陷 |
| 未知/无模板/映射未命中 | 计入 `unsupported[]`（类名 + 原因码，≤64 项），`complete=false` | 不猜 `special`，防止引擎升级静默改哈希 |
| 第三方/项目 spawner | 默认**不触发**模板创建；只读 `GetCachedTemplateNode()`；为 null 即 `unsupported`（原因 `template-not-cached`） | `GetTemplateNode()` 会创建 transient Blueprint/Node 并可能执行 spawner 自定义代码（`BlueprintNodeSpawner.cpp:339-351`、`BlueprintNodeTemplateCache.cpp:78-96`）；不可信代码不应因“读目录”被触发 |
| 官方 spawner | 允许 `GetTemplateNode()`（有界、计数、耗时限制） | 需要 pin 模板与菜单文本 |
| 任何 spawner 抛错/超时 | 单条计入 `unsupported`，整体继续 | 一个坏 spawner 不得毁掉整个目录 |

### 11.4 跨进程一致性与项目/插件差异

- 同一引擎构建 + 同一插件构建 + 同一 fixture：`actionId` 集合与 `descriptorDigest`（除 `discoveredAtUtc`）必须一致；`actionDatabaseFingerprint` 允许因“加载的插件集合不同”而不同，但必须在结果中带 `pluginSetHash` 供解释。
- 项目插件注册的 spawner 顺序可能受加载顺序影响 → 目录必须**排序后**再哈希（actionId 升序），不得依赖 `FActionRegistry` 的 map 迭代顺序。
- 第三方插件缺失/版本变化导致的差异，必须显式列出 `unsupported`/`identityChanged`，不得静默。

### 11.5 T1–T8 收紧 + 新增 T10

| ID | 追加要求 |
|---|---|
| T1 | 断言 `unsupported[]` 原因码分布；`complete` 与 `unsupported` 严格互补；两次调用 fingerprint 相同 |
| T2 | 用**仓库自带** `validate_node_descriptor` 校验，而不是 C++ 侧另写校验；含“本地化字段混入 discoveryKey 必须失败”的负例 |
| T3 | 双向断言（已知在/已知不在）；pin 上下文必须真的改变集合；非 K2 图稳定拒绝 |
| T4 | 两个独立进程 + 蓝图 recompile 后；`pluginSetHash` 相同前提下 actionId 集合完全一致 |
| T5 | 除 dirty/revision/undo 栈外，追加“无 transient 资产被保存、AssetRegistry 无新增” |
| T6 | 分页游标稳定性：同请求两次分页结果一致；`truncated` 与 `totalAvailable` 自洽 |
| T7 | 菜单在 `RegistryMode=shadow` 下**不改变** legacy 条目；`active` 下才替换/新增；卸载后无残留 section |
| T8 | 未知 toolId/版本/surface/flag 关闭 → 精确码；错误不含本机路径 |
| T10（新） | 第三方/不可信 spawner 安全：只读 `GetCachedTemplateNode`，不触发模板创建；构造一个“会抛错的假 spawner”验证整体不失败（测试注入需在测试模块内，不改生产代码） |

### 11.6 “R1 单独 worktree” 与合并策略（澄清）

- 本 worktree（`native-tools-r1`）**只承载 N0 合同与证据**；R1 代码不在这里写（避免文档/证据与编译产物混在一起）。
- 推荐顺序：
  1. N0 评审通过 → 将 `feature/native-tools-contract-r1`（docs-only）合并到 `main`（fast-forward 或单个 merge commit，由协调者执行）。
  2. 从更新后的 `main` 新建 `feature/r1-node-registry` + 独立 worktree（含自己的 `Build/R1AutomationHost`）。
  3. 若 N0 合并被推迟：R1 可从 N0 tip 分支以保证合同 lineage；待 N0 合并后把 `main` merge 进 R1（**不 rebase、不 reset**），并在 R1 报告中记录合同版本。
- R1 需要一次 UE 5.6 构建 + 独立 disposable host；同机一次只允许一个重型 UE 任务；R1 与任何 N1/N2 写路径不同时进行。
- R1 不改 `BlueprintGraphLayoutApply`/EditorBridge 协议；只新增只读模块与 registry 条目。

## 12. 需要 UE Runtime 验证的不确定项（本轮未执行）

| 编号 | 不确定项 | 验证方式 |
|---|---|---|
| U1 | 异步菜单在 `TryExecuteToolUIAction` 返回后 receipt 尚未写入 | 改为 latent automation 等待终态；断言集合不减少 |
| U2 | 非模态通知在 headless/-NullRHI 下是否可创建 | test seam 抑制 + 记录“未验证” |
| U3 | `FSocket::Connect` 在 Editor 进程/防火墙下的行为 | 本机 loopback 自动化 |
| U4 | descriptor 重解析点/ACL 检查 API 细节 | Windows 文件属性实测 |
| U5 | 模板缓存上限对 descriptor 稳定性的影响 | 大目录 T6/T9 |
| U6 | broker 5 s deadline 对超大图是否足够 | T9 性能测量后定阈值 |
| U7 | 旧 worker 与新 broker 的 `planId` 是否逐字一致 | parity 脚本（同一 `build_preview`，预期一致但需证明） |

## 13. 下一轮 R1 开发可执行任务分解（仅分解，不编码）

| 顺序 | 任务 | 产出 | 依赖 | 完成判据 |
|---|---|---|---|---|
| 1 | 建 `feature/r1-node-registry` worktree（从 N0 合并后的 main 或 N0 tip） | 分支 + RunState | 用户批准 N0 | 独立目录、无并发重型任务 |
| 2 | R1.0 数据层骨架：`FNativeNodeRegistry` 只读模块 + 数据模型 | 编译通过的空实现 | 1 | 不触碰写路径；registry `off` 时零行为变化 |
| 3 | R1.1 目录枚举 + `unsupported[]` + `pluginSetHash` | 目录 JSON | 2 | T1 通过 |
| 4 | R1.2 descriptor 派生（discoveryKey/actionKind/pins/constraints） | descriptor 投影 | 3 | T2 + Python golden 对拍 |
| 5 | R1.3 上下文过滤（node/pin/palette） | filterContext | 4 | T3 双向断言 |
| 6 | R1.4 稳定 ID + 跨进程 fingerprint | 指纹工具 | 4 | T4 |
| 7 | R1.5 只读工具注册（registry `shadow`） | 工具 + 清单条目 | 5,6 | T7 + 无 legacy 改变 |
| 8 | R1.6 分页/边界/错误码 | 分页结果 | 7 | T6/T8 |
| 9 | R1.7 第三方 spawner 安全策略 | 策略 + 测试 | 4 | T10 |
| 10 | R1.8 证据与报告（含 U1–U7 中与 R1 相关的项） | 报告 + 哈希 | 全部 | 可复核证据 |

## 14. 需 ChatGPT / 用户审核的问题

1. 是否批准 Q1 修订（broker 方案）作为 N0 的正式方向，并接受“P11 菜单由同步变为异步、receipt 补齐后测试改为 latent 等待”这一契约变化？
2. Q9：broker 由用户手动启动（推荐）还是由 MCP server 托管？
3. Q10：原生 Preview 先限于 disposable host + opt-in，还是直接对所有工程可见？
4. Q11：M3–M4 默认回退 legacy 是否可接受（我推荐是），以及 M5 之后“明确失败不回退”的时间点。
5. Q12：首版只用非模态通知 + receipt 是否满足“结果展示”，还是必须提供结果面板？
6. R1 合并策略：先合并 N0 docs-only 再开 R1 分支（推荐），还是允许 R1 直接从 N0 tip 起步并稍后合并 main？
7. 是否同意 T10（第三方 spawner 不触发模板创建）作为硬约束——这会牺牲部分第三方动作的描述完整度，换取“读目录不执行第三方代码”。
