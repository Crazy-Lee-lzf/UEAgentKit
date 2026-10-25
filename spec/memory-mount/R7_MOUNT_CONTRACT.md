# R7 Memory Mount / Knowledge Pack — Experimental Read-only Contract

> **Status:** development candidate; **not part of the public v0.8.0 production service**. The Staged Context Handoff / Memory Mount path remains disabled by default in the local integration. This page documents scope and discovery, not authorization to turn it on.

## Purpose

A Knowledge Pack is a versioned, reviewable collection of records intended to be mounted read-only for a single Agent session. A mount is not a write/import into Project Memory, does not automatically share records between users, and does not establish a trusted identity.

The experimental schemas are retained at this location for source compatibility and offline validation:

- [Pack manifest](r7-pack-manifest.schema.json)
- [Pack record](r7-pack-record.schema.json)
- [Pack integrity](r7-pack-integrity.schema.json)
- [Claim envelope](r7-claim-envelope.schema.json)
- [Agent context projection](r7-agent-context-projection.schema.json)
- [Staged context handoff](r7-staged-context-handoff.schema.json)

A Schema file existing in the checkout **does not** mean a production MCP endpoint is active or permitted.

## Security and availability

- The public UE Agent Kit v0.8.0 does **not** provide a production Knowledge Pack mount capability.
- The later R7 staging work remains gated on trustworthy host principal/session/project identity, origin/integrity, visibility and retention policy, and explicit operator approval.
- Do not treat an arbitrary bearer token, workspace path, session ID or request ID as a trusted user identity.
- No cross-person sharing, network synchronization, arbitrary import, schema migration, Write Policy bypass, or automatic maintenance is authorized by this contract.
- Agents must call `ue_get_capabilities` on the connected server. If no appropriate Memory Mount tool is registered, report the feature as unavailable instead of synthesizing a call.

For currently available Project Memory behavior use [Project Memory](../PROJECT_MEMORY.md) and the [AI Agent Quick Start](../../docs/AGENT_QUICKSTART_EN.md). Internal R7 test reports and host identity reviews are kept separately from public documentation.
