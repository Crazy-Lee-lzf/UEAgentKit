# UE Agent Kit RC1 Release Checklist

Status: **internal experimental technology preview; public publication is not approved.**  
Package version: **0.8.0**. Do not bump versions, create a tag, or create a GitHub Release from this checklist automatically.

## 1. Candidate identity

| Field | Value |
|---|---|
| Stable base | `main@8c316aa` |
| RC1 prep branch base | `a39752d` (docs scope correction; no code behavior change) |
| Final candidate SHA | Record in the signed release manifest at freeze; do not infer it from this document |
| Package/plugin version | `0.8.0` (unchanged) |
| Tag / GitHub Release | none; creation requires explicit approval |

## 2. Controller-local evidence

These are internal acceptance records, not logs stored in the public tree and not re-run by this checklist.

| ID | Evidence | Result |
|---|---|---|
| `CTRL-FULL` | Full Python suite | 1840 tests / 18 skipped / 0 failed |
| `CTRL-NATIVE` | Native actions | 57 actions, BUILD SUCCEEDED |
| `CTRL-PERF-IDX-WP` | Real performance / index / World Partition checks | 132 checks |
| `RC1-BP-E2E` | Five Blueprint MCP write families | Real MCP E2E on an isolated UE 5.6.1 disposable host: Preview → Apply → Undo/Redo → explicit Save → fresh-process reload |
| `RC1-STATIC` | Release validation and targeted Python tests | `ValidateRelease` plus release/tool/MCP tests pass locally |

Additional details belong in the internal controller handoff, not in this public checklist.

## 3. Blocking decisions before any push

- [ ] **Distribution channel decided by the user/controller.** This document does not decide it.
- [ ] **Public-history privacy decision completed.** The 120-commit local delta includes internal documents and local paths in history; history must not be rewritten as part of this task.
- [ ] **Current-tree private references resolved or explicitly accepted for the chosen channel.** A prior audit found multiple files with local/private references; do not publish them to a public channel without review.
- [ ] **Explicit user approval for `git push`.** No push, tag, release, or `origin/main` update is authorized by this checklist.
- [ ] **License/legal review completed** for the nine Native source files carrying Epic Games copyright headers. Do not remove copyright notices or change the project license without author/legal approval.

## 4. Documentation checklist

- [ ] `docs/RELEASE_RC1.md` and `docs/RELEASE_RC1_EN.md` are linked from `README.md`/`README_EN.md`, `docs/README.md`, and `CHANGELOG.md`.
- [ ] Both language versions state: internal experimental preview, version remains 0.8.0, no GitHub Release/tag, not a full LLMToolkit replacement, no arbitrary GraphPatch, no OwnerTrust.
- [ ] Both versions state the dual opt-in, default OFF, and that experimental writers do not use the Write Policy allowlist.
- [ ] Both versions state clean/non-PIE/focused asset, revision/fingerprint/confirmation checks, Preview/Apply/Undo/Redo, explicit Save, and fresh-process reload.
- [ ] Both versions state: Save receipts are process-local; no auto compile/save; variable creation may leave a pending compile; nested graph writes and complex/multi-asset concurrency are not covered; live-only MCP needs an index DB.
- [ ] `docs/BLUEPRINT_AGENT_E2E_USAGE.md` is linked rather than copying internal run logs.
- [ ] No public RC1 document contains absolute host paths, Windows account names, internal session identifiers, cost data, private project names, or internal handoff paths.

## 5. Repository safety checklist

- [ ] `.gitignore` covers root `Saved/` (EditorBridge descriptor with per-session `authToken`), `*.sqlite3`, `*.sqlite`, `*.db`, and `.env`/local env files.
- [ ] `.gitignore` does not use a broad `*.json` rule and does not hide tracked JSON schemas/fixtures.
- [ ] Legitimate test database fixtures remain unignored via a narrow negation path (for example `tests/fixtures/**`).
- [ ] `git check-ignore` is tested on temporary paths only; no real secret or production database is created.
- [ ] `git status --short` is clean except intended release-doc changes and gitignored output.
- [ ] Tracked files contain no unexpected binary/build/output/database/log artifact.
- [ ] Secret scan of the current tree and history has no high-confidence token/private key/password finding; documented privacy-path findings are handled by the distribution decision.
- [ ] No existing tracked file is deleted or removed from history by this preparation.

## 6. Build and validation checklist

- [ ] `python scripts/ValidateRelease.py --skip-tests --skip-ruff --require-release-docs` passes.
- [ ] `python -m pytest tests/python/test_release_validation.py -q` passes (plus any RC1 docs checks maintained by the project).
- [ ] `git diff --check` passes.
- [ ] `git status --short` reviewed.
- [ ] `git check-ignore` checks pass for the intended paths.
- [ ] Controller Full/Native evidence is attached to the exact frozen candidate SHA.
- [ ] Fresh-clone check confirms the plugin package contains no `Build/`, `Output/`, `Saved/`, `.data/`, database, log, or auth token.
- [ ] Default-OFF check: without the MCP flag and project ini, experimental tools are absent or fail closed.
- [ ] The two-gate opt-in test is run only in an isolated disposable project, never a production project.
- [ ] Release artifact SHA-256 and manifest match the frozen candidate.

## 7. Publication options (decision required; do not choose here)

| Option | Status | Requirement |
|---|---|---|
| Private/internal artifact or private repository | Recommended for this preview | User/controller approval; no public history exposure |
| Public curated snapshot with a new root release history | Allowed only after explicit decision | Clean tree, new publication history, do not rewrite existing Git history |
| Direct public push of the complete current `main` | **NO-GO** | Must first resolve current-tree private references and the local-only history privacy exposure |

No branch, tag, or release is constructed by this preparation branch.

## 8. Sign-off

| Role | Name/decision | Date |
|---|---|---|
| Release owner | | |
| Controller technical acceptance | | |
| Privacy/publication decision | | |
| License/legal (Epic headers) | | |
| Final push authorization | | |
