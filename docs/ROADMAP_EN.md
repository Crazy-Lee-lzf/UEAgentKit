# UE Agent Kit Roadmap

The latest published release is **0.8.0**, targeting Unreal Engine 5.6.

This roadmap describes public product direction rather than a fixed schedule. Future work is prioritized by repeated blockers observed in real projects.

## Current stage: real-project validation and stability

0.8.0 already combines asset/Blueprint queries, Project Memory, Knowledge Web, controlled Writer workflows, verification/Trust, and bounded P4 collaboration. The next priority is to validate the complete workflow in real commercial UE5 projects rather than maximizing tool count:

```text
Project audit
→ index / context
→ impact analysis
→ Write Policy
→ P4 readiness
→ Plan / Apply
→ Save / Verify
→ Semantic Diff / Trust
→ human source-control finalization
```

Repeated reliability, performance, and usability issues discovered in real projects take priority.

## Candidate directions

These areas may expand when real usage demonstrates value:

- broader structured Blueprint Graph editing;
- bounded Level Actor / Component editing;
- additional high-value asset writers;
- stronger shared Knowledge Service support for teams;
- further Memory compression and long-term knowledge governance;
- richer P4 / Git collaboration context while keeping destructive final source-control actions human-controlled.

## Long-term principles

1. **Understanding before breadth**: improve context, references, impact analysis, and verification before adding more actions.
2. **Narrow capabilities over universal scripting**: every new write domain needs explicit Policy, Diff, Undo/Recovery, and verification semantics.
3. **Read-only by default**: writes and source-control mutations require explicit enablement.
4. **Do not bypass the safety model** through shell, arbitrary Python, console commands, or generic P4 passthrough.
5. **Real-project driven**: do not expand tool families merely for coverage metrics.
6. **Humans keep final authority**, especially for P4 submit/revert/delete and other high-impact team operations.
