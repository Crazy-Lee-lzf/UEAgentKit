# UE Agent Kit 0.9.0 Developer Preview 1 (published)

**Status: prerelease, published 2026-10-30.** This is historical release documentation, not an upgrade notice for the next version and not a production authorization. The stable release remains **0.8.0**.

- [GitHub Release and artifacts](https://github.com/Crazy-Lee-lzf/UEAgentKit/releases/tag/v0.9.0-dev1)
- [Source tag `v0.9.0-dev1`](https://github.com/Crazy-Lee-lzf/UEAgentKit/tree/v0.9.0-dev1)
- Version: `0.9.0.dev1`; environment: Unreal Engine 5.6 / Windows x64 / Python 3.11–3.14

## What DP1 includes

- **Blueprint reads**: graphs, nodes, pins, variables, and references, with saved index snapshots kept distinct from live Editor state.
- **Experimental Blueprint edits** (default off): creation/connection of eight primitive node types and variable accessors, pin defaults on unlinked inputs, top-level member variable creation, single-edge rewire, and isolated node removal; Preview, exact confirmation, Apply, explicit Save, and Verify are separate.
- **Read-only PIE diagnostics**: Server/Client worlds, actors, transforms, NetRole, components, and explicitly requested scalar properties, including reflected declaring classes.
- **C++ static analysis**: symbols, call sites, and provenance; no claim of runtime dispatch.
- **Existing capabilities**: bounded DataTable / Data Asset / Material Instance workflows, Project Memory, and opt-in P4 operations.
- The old single-writer tools (pin-default / variable / rewire / node-remove / graph-edit shell) were DP1's write entry points; they were later replaced by the unified atomic patch.

## Validation scope

- GitHub Actions: Python 3.11, 3.12, 3.13, and 3.14 all passed.
- Windows release validation passed: full Python tests, Ruff, and packaging checks had no failures.
- UE 5.6 Win64 native UAT passed without AutoSDK junctions.
- The DLL loaded from the published ZIP passed the `UEAgentKit.P0Pie` native tests with 0 errors and 0 warnings.
- The release ZIP, wheel, SHA256SUMS, and release-manifest were re-downloaded and compared file by file.

These results apply only to the frozen DP1 build; they do not generalize to other UE versions, projects, or later code.

## Installation

Download `UEAgentKit-0.9.0.dev1-UE5.6-Win64.zip` from the release, and copy its `UEAgentKit` folder into your project's `Plugins/` directory. Use the matching `ue_agent_kit-0.9.0.dev1-py3-none-any.whl` for Python.

## Limits

Experimental Blueprint writes are off by default, require both an MCP flag and a project ini opt-in, and are limited to disposable test projects. DP1 does not support general Level, AnimGraph, MaterialGraph, or NiagaraGraph writes, and does not provide executed pin traces or complete GAS state. Bool/double/string input literals, Blueprint-declared property provenance, and PIE client scalar values are outside DP1's native coverage.

See [0.9.0 Developer Preview 2](RELEASE_0.9.0_DP2_EN.md) for the next candidate and [0.8.0 release notes](RELEASE_0.8.0_EN.md) for the stable release.
