# Experimental Blueprint Graph-Layout Apply (MCP, explicit opt-in)

> **Status:** experimental, default-off, non-production. This is not the
> published v0.8.0 write surface and it does not claim an OwnerTrust/T10
> production approval. It exists so a disposable or explicitly opted-in
> project can exercise the real Preview → Apply → Undo/Redo → explicit Save
> loop without turning the feature on for everyone.

## What is actually callable

| Surface | State |
| --- | --- |
| `ue_preview_blueprint_graph_layout` | Available on a fixed-project Live Editor server (read-only). |
| `ue_apply_blueprint_graph_layout_experimental` | Registered only with `--enable-experimental-graph-layout-apply` **and** an Editor project that advertises `editor.applyBlueprintGraphLayoutExperimental`. |
| `ue_save_blueprint_graph_layout_experimental` | Same opt-in as Apply; separate explicit call. |
| Editor right-click menu | The existing `Auto Arrange (Preview)` entry is still limited to the isolated P11 disposable host. No general menu Apply entry is shipped here. |

The MCP path is the usable general entry. Do not describe it as enabled by
default.

## Opt-in (both sides are required)

1. Server operator:

   ```
   ue-agent-mcp --enable-live-editor --project <ExactProject.uproject> \
     --enable-experimental-graph-layout-apply
   ```

2. Editor project `Config/DefaultEngine.ini`:

   ```ini
   [UEAgentKit]
   ExperimentalGraphLayoutApply=True
   ```

3. Confirm at runtime:

   ```
   ue_get_capabilities
   ```

   `liveEditor.experimentalGraphLayoutApply.available` must be `true`, and the
   plain preview call must work. A tool schema existing in the checkout is not
   evidence that this project opted in.

## Shortest real loop

1. Open the exact Blueprint graph in an ordinary Blueprint Editor and focus it.
2. `ue_preview_blueprint_graph_layout(mode="full" | "selection" | "incremental")`.
   Keep the whole `result` object.
3. Ask the user to authorise the exact plan. The confirmation is
   `APPLY EXPERIMENTAL LAYOUT <planId>`.
4. `ue_apply_blueprint_graph_layout_experimental(preview=<result>, confirmation=...)`.
   The Editor re-checks identity, clean package, stopped PIE/SIE, current
   revision/fingerprint, opens exactly one transaction, applies position-only,
   then calls Undo and Redo and proves both readbacks. It never saves or
   compiles.
5. Inspect the returned result. `undoVerified` and `redoVerified` must both be
   `true` for a changed plan.
6. Ask the user separately to save. The confirmation is
   `SAVE EXPERIMENTAL LAYOUT <layoutHash>`.
7. `ue_save_blueprint_graph_layout_experimental(assetPath, graphGuid, layoutHash, expectedGraphFingerprint, confirmation)`.
   Pass `graphFingerprintAfterApply` from the Apply result as
   `expectedGraphFingerprint`. The Editor re-checks the exact focused graph,
   that live positions still equal `layoutHash`, that the graph fingerprint
   still equals the post-Apply value, and that the package is dirty. It saves
   only that package and returns an on-disk readback receipt.
8. Read back the receipt. `independentReloadVerified` is deliberately `false`:
   same-process Save is not independent-reload acceptance evidence.

## Failure codes are the contract

Representative fail-closed codes:

- `graph-layout-experimental-disabled` — either side did not opt in.
- `graph-layout-experimental-confirmation-required` — wrong/absent phrase.
- `graph-layout-revision-conflict`, `graph-layout-fingerprint-conflict` — preview is stale.
- `graph-layout-package-dirty`, `graph-layout-pie-active` — unsafe Editor state.
- `graph-layout-experimental-undo-redo-failed` — apply did not round-trip.
- `graph-layout-experimental-layout-stale` — live positions changed before Save.

The Apply request is built by the server from the exact preview result; callers
cannot supply a hand-written request, project path, endpoint, save flag, or
compile flag. Requests larger than 60 KiB are rejected; use selection or
incremental mode for those graphs.

## Independent reload evidence

Step 8 leaves `independentReloadVerified` false on purpose. To close that gap you
need evidence from a *different, later* process that read the saved bytes from
disk. `scripts/VerifyLayoutIndependentReload.ps1` produces and verifies exactly
that, without saving, recompiling, running automation tests, or opening an Editor
GUI:

```
.\scripts\VerifyLayoutIndependentReload.ps1 `
    -Asset /Game/UEAgentKitP11/BP_LayoutP11 `
    -ApplyEvidence Output\p11\apply.json `
    -SaveEvidence Output\p11\save.json `
    -EditorPid <pid of the Editor that saved> `
    -EditorStartedUtc <its start time, ISO-8601 UTC> `
    -EditorTerminatedUtc <its shutdown time, ISO-8601 UTC>
```

It refuses to run while an `UnrealEditor`/`UnrealEditor-Cmd` process is alive,
then starts **one** `UnrealEditor-Cmd.exe -run=BlueprintContextExport
-IncludeLayout -NullRHI -unattended` commandlet, records that process's real PID
and start time from the OS, converts the export into a probe
(`ueak.layout.independent-reload-probe/1.0`) and asks the offline harness
(`tools/layout_independent_reload_harness.py`) for a verdict
(`ueak.layout.independent-reload-evidence/1.0`). Exit code `0` means verified;
`2` means fail closed.

The verdict is `true` only when all of this holds, and every item is recomputed
locally rather than trusted from the JSON:

- the probe ran in a different process than the Save (different PID *and*
  different process start time) and started after the saving Editor terminated;
- the probe was read-only (no save, no compile, no package write);
- the file on disk is the saved file: SHA256 equals `save.assetSha256`,
  `readback.assetSha256` and the probe's `assetSha256`, and the derived
  `package-sha256-v1` revision equals `readback.packageRevision`;
- same `assetPath`, same `graphGuid`, identical node GUID set, and *exactly*
  equal int32 positions for every node;
- the layout hash recomputed from the reloaded positions equals the probe's,
  the Save's and the Apply's `layoutHash`, and the reloaded
  `graphFingerprint` equals the post-save one.

Anything else keeps `independentReloadVerified` false and lists the
`failClosedCodes`. A same-process readback, however consistent, can never be
upgraded to acceptance evidence. Probe provenance is recorded as
`attestation: self-reported` together with the exact command line, so the claim
stays auditable.
