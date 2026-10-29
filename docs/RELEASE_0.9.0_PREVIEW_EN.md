# UE Agent Kit 0.9.0 Developer Preview 1 (Released)

**Latest published prerelease: `v0.9.0-dev1` (Python package version `0.9.0.dev1`).** The curated source has also been merged into the public `main`. The earlier `v0.8.0` remains the previous stable release; DP1 is an experimental preview, not production-stable.

- [GitHub Release and downloads](https://github.com/Crazy-Lee-lzf/UEAgentKit/releases/tag/v0.9.0-dev1)
- [Tagged source](https://github.com/Crazy-Lee-lzf/UEAgentKit/tree/v0.9.0-dev1)
- [GitHub Python 3.11–3.14 CI: 4/4 passing](https://github.com/Crazy-Lee-lzf/UEAgentKit/actions/runs/40908281817)

## Included capabilities

- **Blueprint reads:** Graphs, Nodes, Pins, variables, and references; distinguish indexed snapshots from live Editor state.
- **Experimental Blueprint edits:** restricted allowlisted nodes, controlled connections and variables, with separate Preview, exact confirmation, Apply, explicit Save and Verify.
- **Read-only PIE inspection:** server/client Worlds, selected Actors, transforms, net roles, Components and explicitly requested scalar properties, including reflected declaring-class provenance.
- **Static C++ investigation:** symbols, call sites and source evidence, without claiming live execution trace.
- **Existing workflows:** bounded DataTable/DataAsset/Material Instance operations, Project Memory and optional safe P4 tooling.

## Verification

- GitHub Actions: Python **3.11, 3.12, 3.13 and 3.14 all passed**.
- Windows Python release validation: **1,885 tests, 19 skipped, 0 failures**; Ruff passed.
- UE 5.6 / Win64 native UAT build: **120/120 actions succeeded** without an AutoSDK Junction.
- DLL extracted from the final release ZIP: native `UEAgentKit.P0Pie` automation **2/2 passed, zero errors/warnings**.
- Release ZIP, wheel, SHA256SUMS and manifest were re-downloaded from GitHub and their SHA-256 digests matched.

## Safety and limitations

Experimental GraphEdit is **off by default**, requires both an MCP flag and project configuration, and is for disposable test projects only. It is not arbitrary Graph CRUD and does not auto-compile or Save All; no unrestricted script execution is provided.

Prior integrated-candidate native tests included bounded Kismet CallFunction, same-Blueprint Pure Function integer literals, indexless pure-Live MCP and PIE scalar-property provenance. **This does not imply that every Blueprint MCP write path was independently end-to-end tested using the final distributable ZIP.** Do not claim generalized bool/double/string literal coverage, full GAS inspection, runtime Pin execution tracing, or generic Level/AnimGraph/MaterialGraph/NiagaraGraph writes.

## Compatibility and installation

**Unreal Engine 5.6 / Windows x64; Python 3.11–3.14.**

Download `UEAgentKit-0.9.0.dev1-UE5.6-Win64.zip` and copy the `UEAgentKit` directory into the project's `Plugins/` directory. Python users can install the `ue_agent_kit-0.9.0.dev1-py3-none-any.whl` artifact.

[Build and Run](BUILD_AND_RUN.md) · [Agent Quick Start](AGENT_QUICKSTART_EN.md) · [Developer Workflows](DEVELOPER_WORKFLOWS_EN.md)

Consult the deployed server's `ue_get_capabilities` for the actual enabled tools; do not apply legacy v0.8.0 tool counts to this preview.
