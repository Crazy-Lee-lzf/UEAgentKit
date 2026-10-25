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
