# UE Agent Kit Roadmap

Current version: **0.9.0**. These are product directions, not shipping commitments or dates.

## P0: Blueprint core workflows and PIE diagnosis

- Extend the **default-off** Blueprint structure writer from the atomic patch to node/connection scenarios proven by real projects, without opening arbitrary graph CRUD.
- Improve live Editor reads and index freshness: re-proving after user saves, concurrency boundaries, and writes without an index.
- Read explicit Server/Client PIE Worlds, Actors, and Components to help diagnose code behavior.
- Improve C++/Blueprint provenance while keeping writes authorized and verifiable.

## P1: Common assets (DataTable / DataAsset first)

- **DataTable and gameplay DataAsset**: prioritize essential configuration reads and controlled writes.
- **Animation Blueprint, Montage, BlendSpace, Curve**: read first; add narrow writing only where real developer workflows justify it.
- **Level/World**: inspect selected Actors and bounded areas; avoid default full-map exports and arbitrary level editing.
- **Material, Mesh, Niagara, and other art assets**: primarily expose relevant parameters, metadata, and references, not full visual graph editing.

## Principles

Prefer proven, narrow workflows that reuse existing readers, indices, and safety gates. Do not bypass permissions, auto-save whole projects, or perform final P4 Submit on behalf of a user.

Always check `ue_get_capabilities` on the installed version for actual tools and permissions.
