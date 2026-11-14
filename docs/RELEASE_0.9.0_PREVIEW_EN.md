# UE Agent Kit 0.9.0 Developer Preview 1 (local candidate 0.9.0.dev1)

**Not released.** The current public version is **v0.8.0**. This is neither an upgrade notice nor permission for production writes.

## Proposed capabilities

- **Blueprint reads:** Graphs, Nodes, Pins, variables and references; distinguish saved index snapshots from live Editor state.
- **Experimental Blueprint edits:** bounded allowlisted nodes, exact link/unlink, and controlled variable operations; Preview, confirmation, explicit Save and Verify are separate.
- **Read-only PIE diagnosis:** select Server/Client Worlds and inspect exact Actor identity, Transform, NetRole, Components, and explicitly requested scalar properties, including reflected declaring classes.
- **C++ investigation:** search symbols, static call sites and provenance without claiming runtime dispatch.
- **Existing tools:** limited DataTable/DataAsset/Material Instance updates, Project Memory and opt-in safe P4 operations.

## Safety and limits

Experimental GraphEdit is **off by default and outside the stable Write Policy allowlist**. Both an MCP flag and a project configuration switch are required, and only disposable test projects may use it. It is not arbitrary Graph CRUD and does not auto-compile or Save All.

The local integrated candidate passed UE 5.6.1 native acceptance for constrained Kismet CallFunction, same-Blueprint-owned Pure Function calls with unlinked input literals (native-tested with int), indexless pure-Live MCP startup, and ue_get_pie_scalars with reflected declaring-class provenance. **Candidate code acceptance is not a public release.** Native evidence does not yet cover bool/double/string input literals, Blueprint-declared property provenance, or PIE client scalar values. PIE does not provide executed pin traces or complete GAS state. General Level, AnimGraph, MaterialGraph, and NiagaraGraph writes remain unavailable.

## Compatibility and usage

Target: **UE 5.6 / Win64, Python 3.11–3.14** (published 0.8.0: 3.11–3.12). [Build and Run](BUILD_AND_RUN.md) · [Agent Quick Start](AGENT_QUICKSTART_EN.md) · [Developer Workflows](DEVELOPER_WORKFLOWS_EN.md).

The frozen build's `ue_get_capabilities` is authoritative. Publication requires real UE/MCP acceptance, privacy and copyright review, and explicit user approval. No GitHub tag or Release exists for this candidate.
