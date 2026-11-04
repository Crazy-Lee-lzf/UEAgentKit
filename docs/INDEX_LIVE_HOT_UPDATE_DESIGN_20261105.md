# In-process incremental index hot update — design (2026-11-05)

Status: **not implemented; design + gap analysis only.** This is stage 0 item
0-3.3. It is the remaining piece of "live-first": the write gate no longer needs
a fresh offline index (0-3.1), but reading a *saved* asset's canonical data still
goes through the offline export.

## Problem

`ue_refresh_asset_index` runs `RunExport.ps1` / `RunAssetCatalog.ps1` in a child
`UnrealEditor-Cmd`, then sets `snapshot-refresh-restart-required`
(`workflow_common.py:1251`). That is one cold process and one MCP restart per
refresh, which the user explicitly rejected for the normal loop.

The two constraints that make a naive "just write the SQLite index" wrong:

- the index is opened **immutable/read-only** and must be sidecar-free
  (`agent_api.py:196-205`, `database.py:559-560`); opening it for writes from a
  serving MCP session breaks the quiescent-snapshot contract;
- the bridge and the MCP are separate processes; the bridge cannot write the
  MCP's database safely, and the MCP must not trust a file the Editor wrote
  without proof.

## Proposed design: session-scoped canonical overlay

1. **Bridge emits the record.** After a save that the bridge performed (or a
   user save it observed), the bridge builds the same canonical asset payload the
   offline exporter writes — `FBlueprintContextExporter::BuildContextObject` for
   Blueprints, the equivalent material/DataTable canonical object for other
   asset classes — plus the post-save disk SHA-256 and package dirty=false.
2. **MCP stores it in a session overlay**, never in the immutable index:
   `live_asset_overlay[asset_path] = {revision, canonical, capturedAtUtc,
   editorSessionId, source}`. It is pruned like other session records and
   dropped on snapshot generation change.
3. **Read paths consult the overlay first** for that exact asset: graph context,
   `get_asset`, symbol/reference lookups scoped to one asset. Misses fall back to
   the frozen index exactly as today.
4. **Explicit tool** `ue_refresh_asset_index_live(asset_path)` asks the bridge for
   one asset's canonical record and installs it. It must return
   `indexFresh=false`, `overlayUpdated=true` and must **not** set
   `snapshot-refresh-restart-required`.
5. **Proof requirements** before installing an overlay entry:
   - the bridge reports the asset loaded/saved in the current Editor session;
   - the disk SHA the bridge reports equals the SHA the MCP reads from disk;
   - the asset class matches the index's recorded class (identity drift refuses);
   - otherwise a precise `live-index-refresh-*` code and no overlay write.

## Why an overlay, not a DB rewrite

- Keeps the immutable-snapshot invariants and `index-not-quiescent` semantics.
- Bounded blast radius: a bad record can only affect the one asset it names, and
  it is dropped on restart.
- No schema migration, no cross-process write coordination.

## Known gaps / why this is not implemented yet

- The bridge currently has **no** "canonical record for an arbitrary asset
  class" method; only Blueprint context (`editor.getBlueprintGraphContext`,
  0-3.2) exists. Material/DataTable canonical records would need new bridge
  readers and a defined schema shared with the exporter.
- Installing an overlay means two sources of truth for one asset; every read path
  must be audited to prefer the overlay consistently, or a caller can see index
  data for an asset the overlay owns.
- Revision proof needs the MCP to read the on-disk SHA for the asset's package;
  that mapping (object path → file) must be exact for single-file and
  multi-file (World/ExternalActors) packages.
- The 10-round acceptance needs the overlay to survive **user** saves that the
  bridge did not perform; detecting those requires a save hook or a poll on
  `editor.getDirtyAssets` plus disk SHA, which is a separate risk.

## Minimal next slice

Ship only the Blueprint half: `ue_refresh_asset_index_live(asset_path)` backed by
`editor.getBlueprintGraphContext` + disk SHA, overlay used by the Blueprint
graph-reading tools. Mark `limited` until Material/DataTable canonical readers
exist.
