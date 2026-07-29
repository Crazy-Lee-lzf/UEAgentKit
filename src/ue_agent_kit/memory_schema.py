from __future__ import annotations

from dataclasses import dataclass


CURRENT_MEMORY_SCHEMA_VERSION = 1


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
)
