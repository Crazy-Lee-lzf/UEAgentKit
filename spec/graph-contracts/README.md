# Graph contracts v1 — Graph Layout stage preview

Status: **stage checkpoint**, not a production write contract. It contains the accepted Graph
Layout design decisions, the Python layout engine, the UE5.6 editor plugin, the read-only MCP
preview tool, and the disposable-host automation evidence. General production Apply is not
exposed.

## Documents

1. `D01_D13_USER_DECISIONS.md` — approved design decisions and explicit no-go boundaries.
2. `G0_3_APPROVED_DELTA.md` — normative deltas relative to the candidate baseline.
3. `G0_4_RUNTIME_ADAPTER.md` — runtime adapter notes.
4. `C_LAYOUT_P11_EDITOR_SCOPE_ACCEPTANCE_REPORT_20261023.md` — final editor menu / selection /
   incremental acceptance evidence, with the exact verification levels.
5. `C_LAYOUT_P12_RELEASE_NOTES_20261023.md` — this stage's capabilities and limitations.
6. `../LIVE_EDITOR_BRIDGE.md`, `../MCP_SERVER.md` — bridge and MCP surface documentation.
7. `../../prototype/graph_contracts/` — offline candidate reference implementation, schemas and
   fixtures. It is kept because the runtime regression tests import it for parity checks.

## Tests

Run from the repository root:

```powershell
py -3.12 scripts\RunPythonTests.py full
```

UE-side evidence was produced with `UnrealEditor-Cmd` automation on disposable projects
(`Build/P7AutomationHost`, `P8AutomationHost`, `P9AutomationHost`, `P11AutomationHost`), which are
not part of this repository.

## Safety

- Graph layout apply is a test-only, exact-project/asset gated surface; do not treat the
  disposable-host acceptance as general production permission.
- Reroute (knot) moves require exact pin type/link evidence; dangling/cyclic/mismatched knots fail
  closed. Comment frame movement remains fail-closed outside the disposable proof.
- No production general Apply endpoint, no one-click auto-arrange for user projects.
- The Python engine is authoritative for this stage; a C++ native tool architecture is future work.
