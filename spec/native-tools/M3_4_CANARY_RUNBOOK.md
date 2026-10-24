# M3.4 Canary Runbook and Rollback Playbook

- Status: **design/approval template only**; not wired to production and not approved for M4.
- NativeLayoutPreview default remains **off**; legacy fallback remains enabled.
- All checks are local/read-only; no service install, no Editor-side process creation, no production project writes.

## A. Preconditions for a future approved canary

1. **Z4 explicit user approval** for an isolated production-like canary (not granted in M3.4).
2. A named canary project with an explicit `CanaryOptIn.json` and a project path hash present in the
   approved allowlist. No project is selected by this repository.
3. A production-like Blueprint set (at least three materially different graphs), with owner sign-off and a
   backup/restore procedure.
4. A locally installed, manually started Python broker; the Editor never starts Python/CMD/PowerShell.
5. Confirmed legacy P11 selection/incremental behavior and rollback owner.

## B. Canary enablement (manual, future M4 only)

1. Install/run the broker **manually** with a unique token. It writes
   `<Project>/Saved/UEAgentKit/LayoutBroker.json`; no Windows service.
2. Verify the descriptor is current-user-only/protected; do not copy or print the token.
3. Run the read-only eligibility check:
   `py -3.12 tools/m34_canary_preflight.py --project <project> --allowlist <approved-list>`
   It must return `ELIGIBLE`; any `BLOCKED` stops the canary.
4. Set `[UEAgentKit] NativeLayoutPreview=on` and `LegacyLayoutPreviewFallback=true` for the named canary
   only, then restart the Editor. This is a future M4 action; M3.4 does not perform it.
5. Run positive native previews on the three approved graphs and compare receipt planId/layoutHash/positions
   to the canonical Python result. No save/compile/undo is expected.
6. Exercise broker-missing/timeout and verify fallback; exercise token/DACL/stale/graph-drift and verify
   **no** fallback. Verify receipts preserve the native error and readOnly flags.

## C. Observability review (offline proposal)

Run the read-only evaluator on the accumulated evidence:

`py -3.12 tools/m34_telemetry_eval.py --evidence <evidence-dir> --out <report.json>`

Review native success, fallback, error codes, busy, latency percentiles, security/identity errors, and
unexpected write flags. **Do not treat the M3.3 negative matrix as production telemetry**: it intentionally
contains security failures and fallback cases. Apply thresholds only to a positive-only production stream.

Proposed, **unapproved** stop thresholds:

- any token/DACL/identity/plan/stale failure;
- any `saved`, `compiled`, or unexpected dirty write;
- fallback rate > 5%;
- native failure rate > 2%;
- latency/regression review if p95 exceeds the agreed baseline.

## D. Rollback playbook

1. Stop taking new native previews; finish/cancel any in-flight operation.
2. Set `NativeLayoutPreview=off` in the canary config and restart the Editor.
3. Verify the P11/legacy preview path, selection/incremental behavior, and that no broker descriptor is
   present/used. The Editor must not auto-connect or auto-start a broker.
4. Stop the manual broker; remove only its own descriptor if safe, using the broker's owner-token path.
5. Preserve receipts/logs, record the last known native planId/layoutHash, and compare package state.
6. If any write/identity fault occurred, stop and escalate; do not re-enable without owner review.
7. Re-run the frozen regression suite (P10/P11, broker tests, full Python) before another attempt.

## E. Risks / open issues

- Legacy fallback is the old synchronous Python worker; it may block a completion tick.
- There is no user-facing progress/cancel UI beyond busy rejection in this slice.
- Broker deployment/ownership and descriptor installation remain manual operational work.
- Real production-like graphs and scale are not yet exercised; macro-instance parity is not claimed.
- M4 production menu eligibility is not implemented in this pre-M4 stage.
- Z4 approval is still missing; this document is not authorization.
