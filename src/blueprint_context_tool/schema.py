from __future__ import annotations

from dataclasses import dataclass


CURRENT_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Migration:
    version: int
    description: str
    sql: str


MIGRATIONS = (
    Migration(
        version=1,
        description="Initial asset, symbol, graph, node, reference, and FTS schema",
        sql=r"""
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    description TEXT NOT NULL,
    applied_at_utc TEXT NOT NULL
);

CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE assets (
    id INTEGER PRIMARY KEY,
    asset_path TEXT NOT NULL UNIQUE,
    package_name TEXT NOT NULL DEFAULT '',
    asset_name TEXT NOT NULL DEFAULT '',
    asset_class TEXT NOT NULL DEFAULT '',
    blueprint_type TEXT NOT NULL DEFAULT '',
    parent_class TEXT NOT NULL DEFAULT '',
    generated_class TEXT NOT NULL DEFAULT '',
    skeleton_generated_class TEXT NOT NULL DEFAULT '',
    status INTEGER NOT NULL DEFAULT 0,
    revision_value TEXT NOT NULL DEFAULT '',
    package_guid TEXT NOT NULL DEFAULT '',
    file_size INTEGER NOT NULL DEFAULT 0,
    modified_utc TEXT NOT NULL DEFAULT '',
    content_sha256 TEXT NOT NULL DEFAULT '',
    package_dirty INTEGER NOT NULL DEFAULT 0 CHECK (package_dirty IN (0, 1)),
    schema_version TEXT NOT NULL,
    exporter_version TEXT NOT NULL,
    profile TEXT NOT NULL,
    canonical_sha256 TEXT NOT NULL,
    canonical_relpath TEXT NOT NULL,
    bpctx_relpath TEXT NOT NULL DEFAULT '',
    summary_json TEXT NOT NULL DEFAULT '{}',
    indexed_at_utc TEXT NOT NULL
);

CREATE INDEX assets_package_name_idx ON assets(package_name);
CREATE INDEX assets_asset_name_idx ON assets(asset_name);
CREATE INDEX assets_parent_class_idx ON assets(parent_class);
CREATE INDEX assets_revision_idx ON assets(revision_value);

CREATE TABLE symbols (
    id INTEGER PRIMARY KEY,
    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    stable_id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    symbol_asset_path TEXT NOT NULL DEFAULT '',
    guid TEXT NOT NULL DEFAULT '',
    owner_symbol_id TEXT NOT NULL DEFAULT '',
    parent_symbol_id TEXT NOT NULL DEFAULT '',
    class_path TEXT NOT NULL DEFAULT '',
    graph_guid TEXT NOT NULL DEFAULT '',
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX symbols_asset_idx ON symbols(asset_id);
CREATE INDEX symbols_kind_name_idx ON symbols(kind, name);
CREATE INDEX symbols_guid_idx ON symbols(guid);
CREATE INDEX symbols_graph_guid_idx ON symbols(graph_guid);

CREATE TABLE graphs (
    id INTEGER PRIMARY KEY,
    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    guid TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT '',
    schema_path TEXT NOT NULL DEFAULT '',
    node_count INTEGER NOT NULL DEFAULT 0,
    details_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(asset_id, guid, name)
);

CREATE INDEX graphs_asset_idx ON graphs(asset_id);
CREATE INDEX graphs_name_idx ON graphs(name);
CREATE INDEX graphs_guid_idx ON graphs(guid);

CREATE TABLE nodes (
    id INTEGER PRIMARY KEY,
    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    graph_id INTEGER REFERENCES graphs(id) ON DELETE CASCADE,
    graph_guid TEXT NOT NULL DEFAULT '',
    guid TEXT NOT NULL DEFAULT '',
    object_name TEXT NOT NULL DEFAULT '',
    node_class TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    comment TEXT NOT NULL DEFAULT '',
    details_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(asset_id, graph_guid, guid, object_name)
);

CREATE INDEX nodes_asset_idx ON nodes(asset_id);
CREATE INDEX nodes_graph_idx ON nodes(graph_id);
CREATE INDEX nodes_guid_idx ON nodes(guid);
CREATE INDEX nodes_class_idx ON nodes(node_class);

CREATE TABLE references_table (
    id INTEGER PRIMARY KEY,
    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    stable_id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    source_symbol_id TEXT NOT NULL DEFAULT '',
    target_symbol_id TEXT NOT NULL DEFAULT '',
    target_kind TEXT NOT NULL DEFAULT '',
    target_name TEXT NOT NULL DEFAULT '',
    target_asset_path TEXT NOT NULL DEFAULT '',
    target_path TEXT NOT NULL DEFAULT '',
    graph_guid TEXT NOT NULL DEFAULT '',
    graph_name TEXT NOT NULL DEFAULT '',
    node_guid TEXT NOT NULL DEFAULT '',
    node_class TEXT NOT NULL DEFAULT '',
    node_title TEXT NOT NULL DEFAULT '',
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX references_asset_idx ON references_table(asset_id);
CREATE INDEX references_kind_idx ON references_table(kind);
CREATE INDEX references_source_idx ON references_table(source_symbol_id);
CREATE INDEX references_target_idx ON references_table(target_symbol_id);
CREATE INDEX references_target_name_idx ON references_table(target_name);
CREATE INDEX references_target_asset_idx ON references_table(target_asset_path);
CREATE INDEX references_graph_node_idx ON references_table(graph_guid, node_guid);

CREATE VIRTUAL TABLE assets_fts USING fts5(
    asset_path,
    asset_name,
    package_name,
    parent_class,
    generated_class,
    content='assets',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER assets_ai AFTER INSERT ON assets BEGIN
    INSERT INTO assets_fts(rowid, asset_path, asset_name, package_name, parent_class, generated_class)
    VALUES (new.id, new.asset_path, new.asset_name, new.package_name, new.parent_class, new.generated_class);
END;

CREATE TRIGGER assets_ad AFTER DELETE ON assets BEGIN
    INSERT INTO assets_fts(assets_fts, rowid, asset_path, asset_name, package_name, parent_class, generated_class)
    VALUES ('delete', old.id, old.asset_path, old.asset_name, old.package_name, old.parent_class, old.generated_class);
END;

CREATE TRIGGER assets_au AFTER UPDATE ON assets BEGIN
    INSERT INTO assets_fts(assets_fts, rowid, asset_path, asset_name, package_name, parent_class, generated_class)
    VALUES ('delete', old.id, old.asset_path, old.asset_name, old.package_name, old.parent_class, old.generated_class);
    INSERT INTO assets_fts(rowid, asset_path, asset_name, package_name, parent_class, generated_class)
    VALUES (new.id, new.asset_path, new.asset_name, new.package_name, new.parent_class, new.generated_class);
END;

CREATE VIRTUAL TABLE symbols_fts USING fts5(
    stable_id,
    name,
    kind,
    symbol_asset_path,
    class_path,
    details_json,
    content='symbols',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER symbols_ai AFTER INSERT ON symbols BEGIN
    INSERT INTO symbols_fts(rowid, stable_id, name, kind, symbol_asset_path, class_path, details_json)
    VALUES (new.id, new.stable_id, new.name, new.kind, new.symbol_asset_path, new.class_path, new.details_json);
END;

CREATE TRIGGER symbols_ad AFTER DELETE ON symbols BEGIN
    INSERT INTO symbols_fts(symbols_fts, rowid, stable_id, name, kind, symbol_asset_path, class_path, details_json)
    VALUES ('delete', old.id, old.stable_id, old.name, old.kind, old.symbol_asset_path, old.class_path, old.details_json);
END;

CREATE TRIGGER symbols_au AFTER UPDATE ON symbols BEGIN
    INSERT INTO symbols_fts(symbols_fts, rowid, stable_id, name, kind, symbol_asset_path, class_path, details_json)
    VALUES ('delete', old.id, old.stable_id, old.name, old.kind, old.symbol_asset_path, old.class_path, old.details_json);
    INSERT INTO symbols_fts(rowid, stable_id, name, kind, symbol_asset_path, class_path, details_json)
    VALUES (new.id, new.stable_id, new.name, new.kind, new.symbol_asset_path, new.class_path, new.details_json);
END;

CREATE VIRTUAL TABLE nodes_fts USING fts5(
    guid,
    title,
    object_name,
    node_class,
    comment,
    details_json,
    content='nodes',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER nodes_ai AFTER INSERT ON nodes BEGIN
    INSERT INTO nodes_fts(rowid, guid, title, object_name, node_class, comment, details_json)
    VALUES (new.id, new.guid, new.title, new.object_name, new.node_class, new.comment, new.details_json);
END;

CREATE TRIGGER nodes_ad AFTER DELETE ON nodes BEGIN
    INSERT INTO nodes_fts(nodes_fts, rowid, guid, title, object_name, node_class, comment, details_json)
    VALUES ('delete', old.id, old.guid, old.title, old.object_name, old.node_class, old.comment, old.details_json);
END;

CREATE TRIGGER nodes_au AFTER UPDATE ON nodes BEGIN
    INSERT INTO nodes_fts(nodes_fts, rowid, guid, title, object_name, node_class, comment, details_json)
    VALUES ('delete', old.id, old.guid, old.title, old.object_name, old.node_class, old.comment, old.details_json);
    INSERT INTO nodes_fts(rowid, guid, title, object_name, node_class, comment, details_json)
    VALUES (new.id, new.guid, new.title, new.object_name, new.node_class, new.comment, new.details_json);
END;
""",
    ),
)
