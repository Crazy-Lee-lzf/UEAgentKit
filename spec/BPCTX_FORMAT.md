# BPCTX/1 格式规范

BPCTX 是面向语言模型的高密度纯文本视图，不代替 Canonical JSON。完整事实、字段兼容和索引重建仍以 Canonical JSON 为准。

## 1. 基本规则

- 编码：UTF-8。
- 行尾：CRLF。
- 每行一条记录，字段使用 `|` 分隔。
- 文件内使用短 ID，避免重复输出长资产路径、类路径和 GUID。
- 值中的反斜杠、竖线和换行分别转义为 `\\`、`\p`、`\n`。
- 缺省可选字段不输出。
- 布尔值使用 `0` 或 `1`。
- 读取器必须忽略不认识的记录类型和可选键值字段，以保持 BPCTX/1 的向后兼容扩展能力。

## 2. 文件头

```text
H|BPCTX|1|engine=<engine-version>|profile=<profile>|schema=<canonical-schema>|exporter=<exporter-version>
```

示例：

```text
H|BPCTX|1|engine=5.6.1|profile=logic|schema=1.1|exporter=0.2.2
```

`BPCTX|1` 是 BPCTX 主版本。新增可选记录或字段不自动提升主版本；发生不兼容的解析规则变化时才提升。

## 3. 记录类型

```text
H|BPCTX|1|engine=...|profile=...|schema=...|exporter=...
A|a0|asset-path|blueprint-kind|parent=...|generated=...
R|revision-value|available=1|dirty=0|guid=...|size=...|mtime=...|sha256=...
I|i0|interface-class
V|v0|name|type|default=...|flags=...
C|c0|name|class|parent=cX
F|f0|name|flags=...
G|g0|name|kind|schema=...
N|n0|g0|node-class|semantic-title|comment=...
P|n0.p0|in|type|name|default=...|links=n1.p2,n3.p0
S|s0|symbol-kind|name|stable=...|asset=...|guid=...|owner=sX|parent=sX|class=...|graph=gX
D|d0|reference-kind|source|target|stable=...|target-kind=...|name=...|asset=...|path=...|graph=gX|node=nX
X|key|value
```

## 4. 资产 Revision

`R` 记录用于检测 AI 上下文与磁盘资产之间的版本冲突。

```text
R|sha256:<hex>|available=1|dirty=0|guid=<package-guid>|size=<bytes>|mtime=<utc>|sha256=<hex>
```

字段：

- 第 2 字段：可直接比较的 Revision Value。
- `available`：是否获得可用于冲突检测的 Revision。
- `dirty`：当前 Package 是否存在未保存修改。
- `guid`：Package Persistent GUID。
- `size`：磁盘文件大小。
- `mtime`：磁盘文件 UTC 修改时间。
- `sha256`：磁盘文件内容 SHA-256。

Revision 必须与导出 Profile、Graph Filter、JSON 缩进和 BPCTX 输出选项无关。同一个未修改 `.uasset` 使用不同 Profile 导出时，Revision Value 必须一致。

## 5. 类型表示

```text
bool
int
real
name
string
text
object</Script/Engine.Actor>
class</Script/Engine.Actor>
struct</Script/CoreUObject.Vector>
array<object</Script/Engine.Actor>>
set<name>
map<name,object</Script/Engine.Actor>>
exec
delegate<SignatureName>
wildcard
```

类型修饰可追加：

```text
&
const
weak
soft
```

## 6. Symbol

`S` 记录是 Canonical Symbol 的紧凑视图。

```text
S|s0|asset|BP_Test|stable=asset\p/Game/Test/BP_Test.BP_Test
S|s1|variable|Health|stable=variable\p/Game/Test/BP_Test.BP_Test\p<guid>|guid=<guid>|owner=s0
S|s2|graph|EventGraph|stable=graph\p/Game/Test/BP_Test.BP_Test\p<guid>|guid=<guid>|owner=s0|graph=g0
```

第一批 Symbol Kind：

```text
asset
variable
component
function
graph
```

字段：

- `sN`：文件内短 Symbol ID。
- `stable`：跨重复导出保持稳定的完整 Symbol ID。
- `asset`：仅当 Symbol 所属资产不同于当前文件资产时输出。
- `guid`：UE 原生稳定 GUID，存在时输出。
- `owner`：所属 Symbol，优先使用本文件短 ID。
- `parent`：组件或其他层级父 Symbol。
- `class`：组件或相关类型路径。
- `graph`：对应 BPCTX Graph 短 ID。

稳定 ID 优先使用 UE 原生 GUID；没有 GUID 时才使用规范化名称回退。

## 7. Reference 和 Dependency

`D` 记录表示符号关系和跨图引用，不重复普通 Pin 连线。

```text
D|d0|inherits|s0|class\p/Script/Engine.Actor|stable=...
D|d1|reads|s3|s1|stable=...|target-kind=variable|name=Health|graph=g0|node=n4
D|d2|calls|s3|function\p/Script/Engine.KismetMathLibrary\pLerp|stable=...|target-kind=function|name=Lerp|path=/Script/Engine.KismetMathLibrary:Lerp|graph=g0|node=n7
```

第一批 Reference Kind：

```text
inherits
implements
reads
writes
calls
macro-calls
```

字段：

- 第 4 字段：Source Symbol。局部目标使用 `sN`，否则保留稳定 ID。
- 第 5 字段：Target Symbol。局部目标使用 `sN`，外部 Blueprint 或原生目标保留稳定 ID。
- `stable`：Reference 稳定 ID。
- `target-kind`：目标 Symbol 类型。
- `name`：目标名称。
- `asset`：目标 Blueprint 资产路径。
- `path`：原生函数或对象路径。
- `graph`：引用所在 Graph 短 ID。
- `node`：引用所在 Node 短 ID。
- `graph-guid`、`node-guid`：无法映射短 ID 时的回退位置。

Pin 连线仍由 `P` 记录的 `links=` 表达，不生成重复 `D` 记录。

## 8. Pin 连线

普通 Pin 连线直接放在输出 Pin 上，正常情况下只记录一次：

```text
P|n0.p1|out|exec|then|links=n1.p0
P|n0.p2|out|object</Script/Engine.Actor>|OtherActor|links=n2.p3
```

## 9. 推荐输出顺序

1. Header、Asset 和 Revision。
2. Interface、Variable、Component 和 Function。
3. Graph、Node 和 Pin。
4. Symbol。
5. Reference/Dependency。
6. 扩展记录。

节点默认按 Canonical JSON 顺序输出。布局信息仅在 `full` 或显式包含布局时输出。

## 10. 分片和索引

大型 Blueprint 可以拆分为：

```text
asset.bpctx
asset.g0.bpctx
asset.f3.bpctx
asset.defaults.bpctx
```

索引至少保存：

- 资产路径和 Revision。
- Symbol 稳定 ID、名称和类型。
- Graph 名称和 GUID。
- Reference Source、Target 和 Kind。
- Node GUID 与文件偏移。
- 父类、接口和资产依赖。

查询流程应先查结构化索引，再读取最小必要 BPCTX 分片。

## 11. 示例

```text
H|BPCTX|1|engine=5.6.1|profile=logic|schema=1.1|exporter=0.2.2
A|a0|/Game/Test/BP_Actor.BP_Actor|normal|parent=/Script/Engine.Actor|generated=/Game/Test/BP_Actor.BP_Actor_C
R|sha256:0123...|available=1|dirty=0|guid=...|size=20480|mtime=2026-07-15T12:00:00.000Z|sha256=0123...
G|g0|EventGraph|uber|schema=/Script/BlueprintGraph.EdGraphSchema_K2
N|n0|g0|/Script/BlueprintGraph.K2Node_Event|ReceiveBeginPlay
P|n0.p0|out|delegate|OutputDelegate
P|n0.p1|out|exec|then
S|s0|asset|BP_Actor|stable=asset\p/Game/Test/BP_Actor.BP_Actor
S|s1|graph|EventGraph|stable=graph\p/Game/Test/BP_Actor.BP_Actor\p<guid>|guid=<guid>|owner=s0|graph=g0
D|d0|inherits|s0|class\p/Script/Engine.Actor|stable=reference\pinherits\p...
```

## 12. 不应放入 BPCTX 的信息

- 未使用的编辑器缓存。
- 可从父类可靠恢复且当前资产未覆盖的重复默认值。
- 缩略图、截图和节点图片。
- 对象指针地址和运行时瞬态数据。
- 普通 Pin 连线的重复 Dependency 记录。

完整事实仍保存在 Canonical JSON，BPCTX 可以根据对应 Schema 和 Exporter 版本重新生成。
