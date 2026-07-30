# UE Agent Kit 0.6.0 Release Notes

UE Agent Kit 0.6.0 targets Unreal Engine 5.6 and adds Revision-aware Project Memory. It extends the existing read-only index, policy-gated writes, independent verification, and rollback boundaries with traceable, invalidatable, conflict-preserving long-term project memory.

## Highlights

- Independent `memory.sqlite3`; the immutable project index is never modified or replaced.
- Six record types: Project Fact, Project Rule, Decision Record, Known Issue, Task Record, and Runtime Evidence.
- Three provenance classes: `user-confirmed`, `tool-observed`, and `model-inferred`.
- Five states: `valid`, `stale`, `conflicted`, `superseded`, and `unverified`.
- Project/Asset/Symbol/Property scopes with stable Revision Sets and Artifact bindings.
- Revision changes automatically mark invalid records stale without deleting history.
- Schema v2 semantic and evidence SHA-256 digests with read-time tamper detection for content, Revisions, and Artifacts.

## MCP and CLI

Project Memory is opt-in. Its database and Project Key are fixed at server startup and cannot be selected by Tool arguments.

New MCP tools:

- `ue_memory_search`
- `ue_memory_get`
- `ue_memory_add_rule`
- `ue_memory_record_finding`
- `ue_memory_record_task`
- `ue_memory_mark_superseded`
- `ue_memory_validate`

Without Memory, Offline/Live/Workflow/Combined remain 5/23/25/43 tools. Enabling Memory changes them to 12/30/32/50.

The CLI provides status, search, get, validate, and export. Audit exports include every Record and Status Event, digest verification, a stable Snapshot SHA-256, and redacted database paths.

## Workflow evidence closure

A successful `ue_verify_asset` returns `memoryTaskEvidence.arguments` with `outcome=succeeded`. A successful rollback Commit returns the same contract with `outcome=rolledBack`. Agents pass these arguments directly to `ue_memory_record_task` instead of reconstructing evidence from logs or local paths.

Task Records require a Canonical Patch digest, Backup Manifest ID, independent Validation Evidence ID, final/restored Revision Set, outcome, and conclusion.

## Validation

0.6.0 passed:

- Ruff and 245 Python tests.
- Three JSON Schemas and sixteen example Patches.
- Real MCP stdio Memory regression and Windows UTF-8 CLI/Audit regression.
- A real UE5.6 Commit → independent Verify → succeeded Task → rollback → rolledBack Task → Revision invalidation → Audit workflow.
- The Commit Task becomes `stale` after rollback while the rolledBack Task remains `valid`.
- The test `.uasset` SHA-256 is fully restored and the immutable SQLite index directory remains unchanged.

## Compatibility and upgrade

- MCP tool counts, ordering, and behavior remain compatible when Memory is disabled.
- Existing 0.5.5 project indexes require no migration because Memory uses a separate database.
- Memory Schema v1 databases migrate to v2 and backfill evidence digests on first open.
- Arbitrary Python, Console, Shell, UObject calls, unbounded writes, and silent conflict overwrites remain prohibited.

## Artifacts

```text
UEAgentKit-0.6.0-UE5.6-Win64.zip
ue_agent_kit-0.6.0-py3-none-any.whl
release-manifest.json
SHA256SUMS.txt
```
