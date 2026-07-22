# UE Agent Kit 0.5.0 Release Notes

UE Agent Kit 0.5.0 targets Unreal Engine 5.6 and exposes the policy, revision, dry-run, backup-manifest, independent-verification, and rollback guarantees from 0.4.4 through a local MCP workflow.

## MCP tools

Default read-only mode:

```text
ue_search
ue_get_asset
ue_find_references
```

Fixed-project full mode additionally exposes:

```text
ue_plan_patch
ue_dry_run_patch
ue_apply_patch
ue_verify_asset
ue_rollback_patch
```

## Safety model

- Local `stdio` only; no TCP listener.
- Database, Engine, Project, Policy, Revision Export, work root, and backup root are fixed at server startup.
- SQLite uses an immutable read-only snapshot and rejects active `-wal`, `-shm`, and `-journal` sidecars.
- Tools do not accept arbitrary filesystem paths, shell commands, SQL, commandlets, or UObject calls.
- Patches remain single-asset and single-operation with exact Policy authorization.
- Stored plans and the Policy are digest-locked for the session.
- Commit requires a successful dry run, a one-time receipt, and exact `COMMIT <planId>` confirmation.
- Rollback commit requires a rollback dry run, a one-time receipt, and exact `ROLLBACK <applyReceipt>` confirmation.
- An independent Unreal process reloads the committed or restored asset and verifies its SHA-256 revision.
- All plans and receipts expire when the MCP server exits.
- After keeping a committed change, stop the server and rebuild the Revision Export and SQLite index before planning the same asset again.

## Verified real workflow

A real MCP client completed eight-tool discovery, zero-write dry run, invalid-confirmation rejection, commit, receipt-reuse rejection, independent reload, rollback dry run, explicit restore, exact final package SHA-256 restoration, and unchanged immutable index files.

## Release verification

```text
Python tests                       118/118 passed
Read-only MCP stdio smoke          passed
Full eight-tool MCP workflow       passed
Scalar Dry Run                     11/11 passed
Scalar Commit                      11/11 passed
Expected zero-write failures        9/9 passed
Final scalar fixture reset         passed
UE5.6 Direct plugin build          passed
UE5.6 UAT Win64 package build      passed
Release ZIP validation             passed
```

SHA-256 for `UEAgentKit-0.5.0-UE5.6-Win64.zip`:

```text
a1516bcc0e63d1e00c7628ad5a9c2fcc69fdf8c9452cbff401f00bc990eab4e2
```

This ZIP contains the installable UE5.6/Win64 plugin only. Use the GitHub-generated source archive or the full repository for the Python CLI, MCP server, scripts, specifications, and tests.

## Full-mode launch

```bat
scripts\RunMcp.cmd ^
  -Database "<TOOL_ROOT>\.data\ue_agent_kit.sqlite3" ^
  -EnableWriteTools ^
  -EnableCommitTools ^
  -EngineRoot "E:\Path\To\UE_5.6" ^
  -ProjectPath "E:\Path\To\Project.uproject" ^
  -Policy "E:\Path\To\write-policy.json" ^
  -RevisionExport "E:\Path\To\RevisionExport"
```

Omit `-EnableCommitTools` to allow planning and dry run without asset save or restore.
