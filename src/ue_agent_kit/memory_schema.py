from __future__ import annotations

from dataclasses import dataclass


CURRENT_MEMORY_SCHEMA_VERSION = 5


@dataclass(frozen=True)
class MemoryMigration:
    version: int
    description: str
    sql: str


MEMORY_MIGRATIONS = (
    MemoryMigration(
        version=1,
        description="Initial revision-aware project memory schema",
        sql=r"""
CREATE TABLE memory_schema_migrations (
    version INTEGER PRIMARY KEY,
    description TEXT NOT NULL,
    applied_at_utc TEXT NOT NULL
);

CREATE TABLE memory_records (
    record_id TEXT PRIMARY KEY,
    project_key TEXT NOT NULL,
    record_type TEXT NOT NULL CHECK (
        record_type IN (
            'projectFact',
            'projectRule',
            'decisionRecord',
            'knownIssue',
            'taskRecord',
            'runtimeEvidence'
        )
    ),
    subject_key TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK (
        source_kind IN ('user-confirmed', 'tool-observed', 'model-inferred')
    ),
    source_ref TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    status TEXT NOT NULL CHECK (
        status IN ('valid', 'stale', 'conflicted', 'superseded', 'unverified')
    ),
    content_sha256 TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    observed_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    superseded_by_record_id TEXT REFERENCES memory_records(record_id) ON DELETE SET NULL,
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX memory_records_project_status_idx
    ON memory_records(project_key, status, updated_at_utc DESC);
CREATE INDEX memory_records_subject_idx
    ON memory_records(project_key, record_type, subject_key);
CREATE INDEX memory_records_source_idx
    ON memory_records(project_key, source_kind);
CREATE INDEX memory_records_content_idx
    ON memory_records(project_key, content_sha256);

CREATE TABLE memory_scopes (
    record_id TEXT NOT NULL REFERENCES memory_records(record_id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    scope_type TEXT NOT NULL CHECK (
        scope_type IN (
            'project',
            'asset',
            'symbol',
            'graph',
            'node',
            'dataTableRow',
            'log',
            'file',
            'external'
        )
    ),
    scope_key TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(record_id, ordinal),
    UNIQUE(record_id, scope_type, scope_key)
);

CREATE INDEX memory_scopes_lookup_idx
    ON memory_scopes(scope_type, scope_key, record_id);

CREATE TABLE memory_revisions (
    record_id TEXT NOT NULL REFERENCES memory_records(record_id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    asset_path TEXT NOT NULL,
    revision TEXT NOT NULL,
    revision_stable INTEGER NOT NULL CHECK (revision_stable IN (0, 1)),
    PRIMARY KEY(record_id, ordinal),
    UNIQUE(record_id, asset_path)
);

CREATE INDEX memory_revisions_asset_idx
    ON memory_revisions(asset_path, revision, revision_stable, record_id);

CREATE TABLE memory_artifacts (
    record_id TEXT NOT NULL REFERENCES memory_records(record_id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    artifact_kind TEXT NOT NULL,
    artifact_ref TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(record_id, ordinal),
    UNIQUE(record_id, artifact_kind, artifact_ref)
);

CREATE INDEX memory_artifacts_lookup_idx
    ON memory_artifacts(artifact_kind, artifact_ref, record_id);

CREATE TABLE memory_relations (
    from_record_id TEXT NOT NULL REFERENCES memory_records(record_id) ON DELETE CASCADE,
    relation_kind TEXT NOT NULL CHECK (
        relation_kind IN ('conflictsWith', 'supersedes', 'supports', 'derivedFrom')
    ),
    to_record_id TEXT NOT NULL REFERENCES memory_records(record_id) ON DELETE CASCADE,
    created_at_utc TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(from_record_id, relation_kind, to_record_id),
    CHECK (from_record_id <> to_record_id)
);

CREATE INDEX memory_relations_target_idx
    ON memory_relations(to_record_id, relation_kind, from_record_id);

CREATE TABLE memory_status_events (
    event_id INTEGER PRIMARY KEY,
    record_id TEXT NOT NULL REFERENCES memory_records(record_id) ON DELETE CASCADE,
    from_status TEXT NOT NULL,
    to_status TEXT NOT NULL CHECK (
        to_status IN ('valid', 'stale', 'conflicted', 'superseded', 'unverified')
    ),
    reason TEXT NOT NULL,
    changed_at_utc TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX memory_status_events_record_idx
    ON memory_status_events(record_id, event_id);

CREATE VIRTUAL TABLE memory_records_fts USING fts5(
    subject_key,
    title,
    body,
    source_ref,
    details_json,
    content='memory_records',
    content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER memory_records_ai AFTER INSERT ON memory_records BEGIN
    INSERT INTO memory_records_fts(rowid, subject_key, title, body, source_ref, details_json)
    VALUES (new.rowid, new.subject_key, new.title, new.body, new.source_ref, new.details_json);
END;

CREATE TRIGGER memory_records_ad AFTER DELETE ON memory_records BEGIN
    INSERT INTO memory_records_fts(
        memory_records_fts,
        rowid,
        subject_key,
        title,
        body,
        source_ref,
        details_json
    ) VALUES (
        'delete',
        old.rowid,
        old.subject_key,
        old.title,
        old.body,
        old.source_ref,
        old.details_json
    );
END;

CREATE TRIGGER memory_records_au AFTER UPDATE ON memory_records BEGIN
    INSERT INTO memory_records_fts(
        memory_records_fts,
        rowid,
        subject_key,
        title,
        body,
        source_ref,
        details_json
    ) VALUES (
        'delete',
        old.rowid,
        old.subject_key,
        old.title,
        old.body,
        old.source_ref,
        old.details_json
    );
    INSERT INTO memory_records_fts(rowid, subject_key, title, body, source_ref, details_json)
    VALUES (new.rowid, new.subject_key, new.title, new.body, new.source_ref, new.details_json);
END;
""",
    ),
    MemoryMigration(
        version=2,
        description="Add evidence-bound audit digests",
        sql=r"""
ALTER TABLE memory_records
    ADD COLUMN evidence_sha256 TEXT NOT NULL DEFAULT '';

CREATE INDEX memory_records_evidence_idx
    ON memory_records(project_key, evidence_sha256);
""",
    ),
    MemoryMigration(
        version=3,
        description="Add knowledge tree, active work, and record bindings",
        sql=r"""
CREATE TABLE knowledge_nodes (
    node_id TEXT PRIMARY KEY,
    project_key TEXT NOT NULL,
    path TEXT NOT NULL,
    parent_node_id TEXT REFERENCES knowledge_nodes(node_id) ON DELETE RESTRICT,
    node_type TEXT NOT NULL CHECK (
        node_type IN ('project', 'system', 'feature', 'component', 'entity', 'implementation')
    ),
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(project_key, path)
);

CREATE INDEX knowledge_nodes_parent_idx
    ON knowledge_nodes(project_key, parent_node_id, path);
CREATE INDEX knowledge_nodes_type_idx
    ON knowledge_nodes(project_key, node_type, path);

ALTER TABLE memory_records
    ADD COLUMN node_id TEXT REFERENCES knowledge_nodes(node_id) ON DELETE RESTRICT;

CREATE INDEX memory_records_node_idx
    ON memory_records(project_key, node_id, status, updated_at_utc DESC);

CREATE TABLE active_work_items (
    work_item_id TEXT PRIMARY KEY,
    project_key TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('planned', 'in_progress', 'blocked', 'done', 'cancelled')
    ),
    priority INTEGER NOT NULL CHECK (priority >= 0 AND priority <= 100),
    description TEXT NOT NULL,
    next_action TEXT NOT NULL,
    blocked_reason TEXT NOT NULL,
    owner TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    completed_at_utc TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX active_work_items_project_status_idx
    ON active_work_items(project_key, status, priority DESC, updated_at_utc DESC);

CREATE TABLE active_work_node_links (
    work_item_id TEXT NOT NULL REFERENCES active_work_items(work_item_id) ON DELETE CASCADE,
    node_id TEXT NOT NULL REFERENCES knowledge_nodes(node_id) ON DELETE RESTRICT,
    PRIMARY KEY(work_item_id, node_id)
);

CREATE INDEX active_work_node_links_node_idx
    ON active_work_node_links(node_id, work_item_id);

CREATE TABLE active_work_asset_links (
    work_item_id TEXT NOT NULL REFERENCES active_work_items(work_item_id) ON DELETE CASCADE,
    asset_path TEXT NOT NULL CHECK (asset_path LIKE '/Game/%'),
    PRIMARY KEY(work_item_id, asset_path)
);

CREATE INDEX active_work_asset_links_asset_idx
    ON active_work_asset_links(asset_path, work_item_id);

CREATE TABLE active_work_todos (
    todo_id TEXT PRIMARY KEY,
    work_item_id TEXT NOT NULL REFERENCES active_work_items(work_item_id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    completed_at_utc TEXT NOT NULL DEFAULT ''
);

CREATE INDEX active_work_todos_work_idx
    ON active_work_todos(work_item_id, created_at_utc, todo_id);
""",
    ),
    MemoryMigration(
        version=4,
        description="Add deterministic L0 events and Evidence Chain foundation",
        sql=r"""
CREATE TABLE memory_evidence_chains (
    chain_id TEXT PRIMARY KEY,
    project_key TEXT NOT NULL,
    hypothesis TEXT NOT NULL,
    context_json TEXT NOT NULL DEFAULT '{}',
    verdict TEXT NOT NULL CHECK (verdict IN ('supported', 'rejected', 'inconclusive')),
    confidence TEXT NOT NULL CHECK (confidence IN ('high', 'medium', 'low')),
    created_at_utc TEXT NOT NULL,
    verified_at_utc TEXT NOT NULL DEFAULT '',
    superseded_by TEXT REFERENCES memory_evidence_chains(chain_id) ON DELETE SET NULL
);

CREATE INDEX memory_evidence_chain_project_idx
    ON memory_evidence_chains(project_key, verdict, created_at_utc);

CREATE TABLE memory_l0_events (
    event_id TEXT PRIMARY KEY,
    project_key TEXT NOT NULL,
    event_kind TEXT NOT NULL,
    occurred_at_utc TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    artifact_ref TEXT NOT NULL DEFAULT '',
    artifact_digest TEXT NOT NULL,
    lifecycle_state TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (
        outcome IN ('success', 'partial', 'failed', 'rejected', 'no-op', 'recovered', 'superseded')
    ),
    asset_paths_json TEXT NOT NULL DEFAULT '[]',
    change_set_id TEXT NOT NULL DEFAULT '',
    hypothesis_id TEXT REFERENCES memory_evidence_chains(chain_id) ON DELETE SET NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    distilled INTEGER NOT NULL DEFAULT 0 CHECK (distilled IN (0, 1)),
    UNIQUE(project_key, event_kind, source_ref, artifact_digest)
);

CREATE INDEX memory_l0_pending_idx
    ON memory_l0_events(project_key, distilled, occurred_at_utc, event_id);
CREATE INDEX memory_l0_change_set_idx
    ON memory_l0_events(project_key, change_set_id, occurred_at_utc, event_id);
CREATE INDEX memory_l0_hypothesis_idx
    ON memory_l0_events(project_key, hypothesis_id, occurred_at_utc, event_id)
    WHERE hypothesis_id IS NOT NULL;
""",
    ),
    MemoryMigration(
        version=5,
        description="Add optional Project Memory embedding storage (ordinary table, vector-extra independent)",
        sql=r"""
CREATE TABLE memory_embeddings (
    record_id           TEXT PRIMARY KEY
                        REFERENCES memory_records(record_id) ON DELETE CASCADE,
    model_id            TEXT NOT NULL,
    dim                 INTEGER NOT NULL CHECK (dim > 0),
    content_sha256      TEXT NOT NULL,
    embedding           BLOB NOT NULL,
    created_at_utc      TEXT NOT NULL,
    updated_at_utc      TEXT NOT NULL
);

CREATE INDEX memory_embeddings_model_idx
    ON memory_embeddings(model_id, record_id);
""",
    ),
)
