from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

DATA_TABLES_ROOT = "/Game/UEAgentKitWriteTests/DataTables"
ROW_STRUCT = "/Script/UEAgentKitEditor.UEAgentKitDataTableFixtureRow"

FIXTURE_IDS = {
    "cell": "data-table-cell-asset",
    "fields": "data-table-fields-asset",
    "add": "data-table-add-asset",
    "rename": "data-table-rename-asset",
    "remove": "data-table-remove-asset",
    "scalar": "data-table-non-table-asset",
}

FIXTURE_ASSETS = {
    "cell": f"{DATA_TABLES_ROOT}/DT_CellTarget.DT_CellTarget",
    "fields": f"{DATA_TABLES_ROOT}/DT_FieldsTarget.DT_FieldsTarget",
    "add": f"{DATA_TABLES_ROOT}/DT_AddTarget.DT_AddTarget",
    "rename": f"{DATA_TABLES_ROOT}/DT_RenameTarget.DT_RenameTarget",
    "remove": f"{DATA_TABLES_ROOT}/DT_RemoveTarget.DT_RemoveTarget",
    "scalar": f"{DATA_TABLES_ROOT}/DA_ScalarNonTable.DA_ScalarNonTable",
}

# These three writes are authorized-saved.  fields and add stay in Editor memory
# only, so a fresh process must still see their original rows on disk.
EXPECTED_DISK_ROWS: dict[str, list[dict[str, Any]]] = {
    "cell": [
        {"Name": "RowAlpha", "Count": 42, "Label": "Alpha", "bEnabled": True},
        {"Name": "RowBeta", "Count": 2, "Label": "Beta", "bEnabled": False},
    ],
    "fields": [
        {"Name": "RowBeta", "Count": 2, "Label": "Beta", "bEnabled": False},
    ],
    "add": [
        {"Name": "RowGamma", "Count": 3, "Label": "Gamma", "bEnabled": True},
    ],
    "rename": [
        {"Name": "RowGammaRenamed", "Count": 3, "Label": "Gamma", "bEnabled": True},
    ],
    "remove": [],
}

SAVED_ASSETS = ("cell", "remove", "rename")


def load_canonical(reload_root: Path) -> dict[str, dict[str, Any]]:
    assets: dict[str, dict[str, Any]] = {}
    canonical_root = reload_root / "canonical"
    if not canonical_root.is_dir():
        raise RuntimeError(f"Reload export has no canonical directory: {canonical_root}")
    for path in sorted(canonical_root.rglob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Reload canonical JSON is unreadable: {path}: {exc}") from exc
        if not isinstance(value, dict):
            continue
        asset_path = value.get("assetPath")
        if isinstance(asset_path, str) and asset_path:
            assets[asset_path] = value
    return assets


def normalize_rows(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        normalized.append(
            {
                "Name": row.get("Name"),
                "Count": row.get("Count"),
                "Label": row.get("Label"),
                "bEnabled": row.get("bEnabled"),
            }
        )
    normalized.sort(key=lambda item: str(item["Name"]))
    return normalized


def run(args: argparse.Namespace) -> dict[str, Any]:
    fixture_report = json.loads(args.fixture_report.read_text(encoding="utf-8"))
    fixtures = {str(item["id"]): item for item in fixture_report["fixtures"]}
    client_report = json.loads(args.client_report.read_text(encoding="utf-8"))
    canonical = load_canonical(args.reload_root)
    errors: list[str] = []
    revisions: dict[str, str] = {}
    checked: list[str] = []

    for name, fixture_id in FIXTURE_IDS.items():
        expected_asset = FIXTURE_ASSETS[name]
        entry = fixtures.get(fixture_id)
        if entry is None:
            errors.append(f"Fixture report has no entry {fixture_id}")
            continue
        if expected_asset not in canonical:
            errors.append(f"Reload export is missing {expected_asset}")
            continue
        value = canonical[expected_asset]
        checked.append(name)
        if value.get("assetPath") != expected_asset:
            errors.append(f"{name}: assetPath mismatch {value.get('assetPath')!r}")
        if value.get("assetClass") != entry.get("assetClass"):
            errors.append(f"{name}: assetClass mismatch {value.get('assetClass')!r}")
        revision = value.get("revision", {})
        revision_value = revision.get("value", "") if isinstance(revision, dict) else ""
        revisions[name] = str(revision_value)
        initial_revision = str(entry.get("revision", ""))
        if not str(revision_value).startswith("sha256:"):
            errors.append(f"{name}: reload Revision is not SHA-256")
        if name in SAVED_ASSETS:
            if revision_value == initial_revision:
                errors.append(f"{name}: saved asset Revision did not change")
            expected_after = str(
                client_report.get("saves", {}).get(name, {}).get("afterRevision", "")
            )
            if expected_after and revision_value != expected_after:
                errors.append(
                    f"{name}: reload Revision {revision_value} != saved Revision {expected_after}"
                )
        elif revision_value != initial_revision:
            errors.append(f"{name}: untouched asset Revision changed")

        details = value.get("assetDetails", {})
        if name == "scalar":
            if isinstance(details, dict) and details.get("type") == "data-table":
                errors.append("scalar: non-DataTable asset was exported as data-table")
            continue
        if not isinstance(details, dict) or details.get("type") != "data-table":
            errors.append(f"{name}: assetDetails is not data-table")
            continue
        if details.get("rowStructPath") != ROW_STRUCT:
            errors.append(f"{name}: rowStructPath mismatch {details.get('rowStructPath')!r}")
        expected_rows = EXPECTED_DISK_ROWS[name]
        actual_rows = normalize_rows(details.get("rows"))
        if actual_rows != expected_rows:
            errors.append(
                f"{name}: persisted rows mismatch: expected {expected_rows} actual {actual_rows}"
            )
        if details.get("rowCount") != len(expected_rows):
            errors.append(f"{name}: rowCount mismatch {details.get('rowCount')!r}")

    if len(checked) != len(FIXTURE_IDS):
        errors.append(f"Only {len(checked)} of {len(FIXTURE_IDS)} fixtures were reloaded")

    report = {
        "schemaVersion": "1.0",
        "tool": "mcp-live-data-table-undo-save-reload-verify",
        "verified": not errors,
        "reloadRoot": str(args.reload_root),
        "checkedAssets": checked,
        "revisions": revisions,
        "savedAssets": {name: FIXTURE_ASSETS[name] for name in SAVED_ASSETS},
        "untouchedAssets": {
            name: FIXTURE_ASSETS[name]
            for name in ("fields", "add", "scalar")
        },
        "expectedDiskRows": EXPECTED_DISK_ROWS,
        "errors": errors,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"verified": True, "checked": len(checked)}, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reload-root", type=Path, required=True)
    parser.add_argument("--fixture-report", type=Path, required=True)
    parser.add_argument("--client-report", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
