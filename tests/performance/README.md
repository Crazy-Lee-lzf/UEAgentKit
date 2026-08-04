# 性能测试脚本

本目录保存可重复运行的大型项目性能基准。脚本只读取现有 SQLite 索引，不修改 Unreal 资产。

## benchmark_index_queries.py

测量以下查询的进程内 Warm 延迟与数据库重新打开成本：

- Index Stats。
- 资产文本搜索。
- Asset Class 过滤。
- Symbol 搜索。
- 单资产读取。
- 入向与出向引用查询。
- 深度 2 项目内引用遍历。
- 打开只读数据库后执行一次搜索。

运行方式：

```powershell
$env:PYTHONPATH = "E:\WorkSpace\UEAgentKit-Performance\src"

E:\WorkSpace\UEAgentKit\.venv\Scripts\python.exe `
    E:\WorkSpace\UEAgentKit-Performance\tests\performance\benchmark_index_queries.py `
    --database E:\WorkSpace\UEAgentKit-Performance\Output\Performance\DarkRuins\index_generated\darkruins_generated.sqlite3 `
    --output E:\WorkSpace\UEAgentKit-Performance\Output\Performance\DarkRuins\index_generated\query_benchmark.json
```

默认行为：

```text
Warmup       5 次
Measured   200 次
Open/Search 40 次
```

输出包含 `minMs`、`p50Ms`、`p95Ms`、`p99Ms`、`maxMs`、`meanMs` 和结果数量。基准会自动选择最长 Asset Path、最大入向引用目标和具有较多出向引用的资产，避免依赖固定样本名称。

DarkRuins 首轮结果见 [`../../docs/PERFORMANCE_BASELINE_DARKRUINS_20260803.md`](../../docs/PERFORMANCE_BASELINE_DARKRUINS_20260803.md)。
