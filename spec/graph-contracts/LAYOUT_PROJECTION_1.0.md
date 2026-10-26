# Bounded Layout Projection 1.0

## Purpose

`editor.getFocusedBlueprintGraphLayoutProjection` is a read-only companion to
the focused layout preflight. It exists because the canonical focused-graph JSON
for a large EventGraph is dominated by pin records (the 134-node fixture carries
about 1,300 pins), while the layout planner needs only geometry, pin/link
topology and comment bounds. The endpoint makes that projection pageable and
leaves the existing full-context endpoint and its 700 KiB fail-closed gate
unchanged.

## Identity and bounds

- Exact focused Blueprint graph only; no asset or graph parameter is accepted.
- Parameters: `offset` (>= 0), `limit` (1..256, default 64), optional
  `expectedPackageRevision`, optional `expectedGraphFingerprint`.
- One page is serialized back to at most 512 KiB; a larger page fails with
  `graph-layout-projection-page-too-large` so the client can retry a smaller
  `limit`. The bridge response remains below its 1 MiB limit.
- Response schema: `ueak.graphlayoutprojection/1.0`.

## Page fields

Identity: `assetPath`, `assetClass`, `graphGuid`, `graphName`, `graphKind`,
`graphClassPath`, `schemaClassPath`, `packageRevision`, `packageDirty`,
`graphFingerprint`.

Counts: `nodeCount`, `pinCount`, `edgeCount`, `commentCount`.

Hashes: `nodeSetHash`, `pinSetHash`, `edgeSetHash`, `projectionHash`. They are
SHA-256 over newline-terminated canonical lines and are independent of paging.

Page cursor: `offset`, `limit`, `returnedNodeCount`, `hasMore`, `nextOffset`.

Page content: `nodes` (identity, class, kind, x/y/width/height), `pins`
(direction, kind, type category/key, flags), `edges` (source/target pin and
node IDs), `comments` (node ID, text, bounds). Edges are emitted exactly once,
on the page owning their source node.

## Client completeness rules

The client requests pages until `hasMore` is false, rejects non-contiguous
offsets, duplicate/missing nodes, pins, edges or comments, and recomputes the
four hashes over the concatenated set. It also re-reads the focused preflight
after the last page and rejects any changed revision, fingerprint, node count,
dirty state or PIE state. Only then is a canonical document reconstructed and
the normal layout planner invoked. Apply still requires the existing explicit
opt-in write gate and its exact focused-graph, clean-package, revision,
fingerprint and layout-hash checks.

## Observed capacity

Real `BP_NodeCoverage` EventGraph on 2026-10-26: the canonical export is
1,311,335 bytes; its EventGraph is 779,954 bytes, of which 669,924 bytes
(85.9%) are pin records for 1,308 pins across 134 nodes. The projection has 0
edges and 0 comments because this fixture is intentionally disconnected. It
needs 3 pages at `limit=64`; the largest compact page is 181,576 bytes, a 5.7x
reduction from the EventGraph body. Projection hash:
`sha256:da772976ddca3d0eef7756b07a060c359354e3cf7b6ca4d0b34e92cdeea4e409`.
The old full-context call still fails with `graph-layout-context-too-large`.
