"""Fixture / schema loading helpers (stdlib only)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CONTRACT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = CONTRACT_ROOT / "fixtures"
SCHEMA_ROOT = CONTRACT_ROOT / "schemas"
MANIFEST_PATH = FIXTURE_ROOT / "manifest.json"
VECTORS_PATH = CONTRACT_ROOT / "vectors" / "hash_vectors.json"


def load_json(path: Path | str) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_manifest() -> dict[str, Any]:
    return load_json(MANIFEST_PATH)


def fixture_path(relative: str) -> Path:
    return FIXTURE_ROOT / relative


def entries_by_kind(manifest: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [entry for entry in manifest["fixtures"] if entry["kind"] == kind]


def load_fixture(entry: dict[str, Any]) -> dict[str, Any]:
    return load_json(fixture_path(entry["file"]))


def load_graph_for(entry: dict[str, Any]) -> dict[str, Any]:
    graph_fixture = entry.get("graphFixture")
    if not graph_fixture:
        raise ValueError(f"Fixture {entry['fixtureId']} has no graphFixture")
    return load_json(fixture_path(graph_fixture))


def load_descriptors_for(entry: dict[str, Any]) -> list[dict[str, Any]]:
    return [load_json(fixture_path(path)) for path in entry.get("descriptorFixtures", [])]


def environment_for(entry: dict[str, Any]) -> dict[str, Any]:
    return dict(entry.get("environment", {}))


def load_schema(name: str) -> dict[str, Any]:
    return load_json(SCHEMA_ROOT / name)
