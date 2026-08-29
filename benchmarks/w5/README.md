# W5 Benchmark Harness

W5 measures the W4 bounded Writer workflow on the DirectHost test project and
produces deterministic stage-level timing evidence under `Output/W5Acceptance`.

## Scope

- **DirectHost** (`Build/DirectHost/HostProject.uproject`) is the only project
  used for controlled write-path acceptance and timing.
- **Reforge** is strictly read-only and is measured separately (project size,
  asset counts, read-only export/index/query behavior).
- The 20-logical-operation scenario is always split into multiple legal W4
  bounded workflows (max 4 assets, max 8 ops/asset, max 16 ops/batch).

## Modules

```text
benchmarks/w5/__init__.py
benchmarks/w5/workloads.py    scenario definitions + W4 bound validation
benchmarks/w5/metrics.py      summary/percentiles/ratios/noise
benchmarks/w5/runner.py       offline commands + real UE service runner
```

## Commands

Offline summary:

```powershell
.\.venv\Scripts\python.exe -m benchmarks.w5.runner summarize `
  --attempts Output\W5Acceptance\<run>\attempts.jsonl `
  --output Output\W5Acceptance\<run>\summary.json
```

Resident real UE sample (DirectHost must be running with the Live Editor Bridge):

```powershell
.\.venv\Scripts\python.exe -m benchmarks.w5.runner run-resident `
  --scenario R5 --sample-index 1 --cache-state WarmLoaded `
  --database <index.sqlite3> --revision-export <revision-export> `
  --policy Output\W4Acceptance\acceptance-policy.json `
  --work-root Output\W3Acceptance\Workflow `
  --backup-root Backups\W3Acceptance `
  --project Build\DirectHost\HostProject.uproject `
  --engine-root E:\EPICGAME\UE_5.6 `
  --output Output\W5Acceptance\<run>\attempt-R5-1.json
```

Cold paired sample (wraps `RunPatch.ps1`):

```powershell
.\.venv\Scripts\python.exe -m benchmarks.w5.runner run-cold `
  --scenario R5 --sample-index 1 --launch-index 0 `
  --patch-path <patch.json> --policy <policy> --revision-export <export> `
  --project Build\DirectHost\HostProject.uproject `
  --engine-root E:\EPICGAME\UE_5.6 `
  --output Output\W5Acceptance\<run>\cold-R5-1-0.json
```

## Evidence layout

```text
Output/W5Acceptance/<run-id>/
  environment.json
  attempts.jsonl
  summary.json
  summary.md
  failures/
  cold-pairs/
```

Raw evidence is untracked. Only `benchmarks/w5` and the test are committed.
