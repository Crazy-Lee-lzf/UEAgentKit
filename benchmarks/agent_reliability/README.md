# UEAgentKit Agent Reliability Benchmark

This package runs the R4 real-Agent benchmark against deterministic Reforge
read-only fixtures and recoverable DirectHost write fixtures. It compares the
same Agent, model, prompt, initial state, policy, and revision under two MCP
tool profiles:

- `full-r0-r3`: the production tool registry, including R0 task context, R1
  impact, R2 semantic diff, and R3 verification/trust tools.
- `legacy-low-level`: the same production server and safety gates with exactly
  those five R0-R3 tools hidden by the benchmark proxy.

The benchmark does not use an LLM as judge. Agent output is only a claim;
ground truth comes from fixed index facts, Canonical exports, package hashes,
revision exports, policy hashes, live Editor state, and deterministic R2/R3
evidence.

## Safety boundaries

- Reforge cases are read-only. Commit is disabled and no Editor is launched.
- DirectHost setup and cleanup are fixed allowlisted hooks; case JSON cannot
  contain commands, executable paths, Python, console commands, or secrets.
- Every DirectHost attempt owns its Editor process and captures package bytes,
  Canonical state, revision export, database, policy, dirty state, and process
  state.
- Cleanup restores package bytes atomically and verifies exact recovery. A
  cleanup failure latches the runner and skips all later mutation attempts.
- The benchmark MCP server is `required`; failure to initialize aborts the
  Agent turn instead of silently running without UE tools.
- MCP work paths stay below the attempt's ignored `Output` directory. MCP
  backup paths are mapped to an attempt-specific child of
  `Backups/AgentReliabilityBenchmark`.
- Raw attempts, including failures, are retained below ignored `Output`.
  Never commit `Output`, `Backups`, Editor logs, temporary assets, or local
  credentials.

## Layout

```text
benchmarks/agent_reliability/
  cases/                    versioned case definitions
  schemas/                  case and strict Agent-result JSON Schema
  adapters.py               Agent adapter protocol/import adapter
  codex_adapter.py          Codex CLI real-Agent adapter
  mcp_profile_proxy.py      Full/Legacy MCP view
  real_fixtures.py          Reforge and DirectHost fixtures
  grader.py                 deterministic ground-truth grading
  metrics.py                aggregate and paired metrics
  runner.py                 scheduling, retention, and fail-closed control
scripts/
  run_agent_reliability_benchmark.py
  summarize_agent_reliability_benchmark.py
```

## Prerequisites

- Repository virtual environment with project dependencies installed.
- UE 5.6 at `E:\EPICGAME\UE_5.6`, or `--engine-root`.
- DirectHost at `Build/DirectHost/HostProject.uproject`, or
  `--directhost-project`.
- Reforge project, fixed SQLite index, revision export, and read-only policy.
- Codex CLI authenticated locally. Credentials are not read from case files or
  serialized into benchmark results.
- A fixed model must be supplied for a real run.

Useful environment overrides are `UEAK_BENCHMARK_CODEX`,
`UEAK_BENCHMARK_MODEL`, `UEAK_BENCHMARK_REASONING`,
`UEAK_BENCHMARK_SERVICE_TIER`, `UEAK_ENGINE_ROOT`, and
`UEAK_REFORGE_PROJECT`.

## Run

Validate all cases and the 15 Full / 9 Legacy matrix without launching UE:

```powershell
$env:PYTHONPATH = 'src'
.venv\Scripts\python.exe scripts\run_agent_reliability_benchmark.py --dry-validate
```

Run the fixed fixture preflight:

```powershell
$env:PYTHONPATH = 'src'
.venv\Scripts\python.exe scripts\run_agent_reliability_benchmark.py `
  --fixture-preflight `
  --output-dir Output\AgentReliabilityBenchmark\<fresh-preflight-id>
```

Run a selected calibration case:

```powershell
$env:PYTHONPATH = 'src'
.venv\Scripts\python.exe scripts\run_agent_reliability_benchmark.py `
  --case r4-readonly-discovery-001 `
  --profiles full-r0-r3 legacy-low-level `
  --model gpt-5.6-sol `
  --reasoning-effort low `
  --service-tier priority `
  --output-dir Output\AgentReliabilityBenchmark\<fresh-calibration-id>
```

Omit `--case` for the formal interleaved 24-attempt matrix. Every output
directory must be fresh.

## Outputs and recomputation

```text
Output/AgentReliabilityBenchmark/<run-id>/
  run.json
  attempts/*.json
  traces/*.json
  ground-truth/*.json
  attempt-data/...          raw Codex JSONL and fixture evidence
  summary.json
```

`run.json` records the fixed runtime and tool profiles. Each attempt records
the prompt/fixture fairness fingerprints, Agent claim, trace, exact token
usage when available, deterministic grade, and cleanup result.

Recompute the summary only from retained attempts:

```powershell
.venv\Scripts\python.exe scripts\summarize_agent_reliability_benchmark.py `
  Output\AgentReliabilityBenchmark\<run-id>
```

Check that the calculation has not drifted:

```powershell
.venv\Scripts\python.exe scripts\summarize_agent_reliability_benchmark.py `
  Output\AgentReliabilityBenchmark\<run-id> --check
```

The summary reports task and semantic correctness, trusted completion, both
false-success denominators, wrong asset, unintended change, stale/dirty
detection, exact recovery, tool calls, high-level calls, token usage, elapsed
time, human intervention, retries, failure taxonomy, and paired Full-minus-
Legacy deltas.

## Focused verification

```powershell
$env:PYTHONPATH = 'src'
.venv\Scripts\python.exe -m unittest `
  tests.python.test_agent_reliability_benchmark -q
.venv\Scripts\ruff.exe check benchmarks\agent_reliability `
  tests\python\test_agent_reliability_benchmark.py `
  scripts\run_agent_reliability_benchmark.py `
  scripts\summarize_agent_reliability_benchmark.py
```
