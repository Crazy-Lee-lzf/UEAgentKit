from __future__ import annotations

import argparse
import collections
import hashlib
import json
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate UEAgentKit export output.")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--asset-file", type=Path)
    parser.add_argument("--expect-schema", default="")
    parser.add_argument("--expect-exporter", default="")
    parser.add_argument("--expect-project", default="")
    parser.add_argument("--require-symbol-kind", action="append", default=[])
    parser.add_argument("--require-reference-kind", action="append", default=[])
    parser.add_argument("--allow-failures", action="store_true")
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def collect_bpctx_records(path: Path) -> tuple[list[str], dict[str, list[list[str]]]]:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    records: dict[str, list[list[str]]] = collections.defaultdict(list)
    for line in lines:
        if not line:
            continue
        fields = line.split("|")
        records[fields[0]].append(fields)
    return lines, records


def validate_pair(
    canonical_path: Path,
    bpctx_path: Path,
    expected_schema: str,
    expected_exporter: str,
    expected_project: str,
    required_symbol_kinds: set[str],
    required_reference_kinds: set[str],
) -> dict[str, Any]:
    errors: list[str] = []
    canonical = load_json(canonical_path)
    lines, records = collect_bpctx_records(bpctx_path)

    schema_version = str(canonical.get("schemaVersion", ""))
    exporter_version = str(canonical.get("exporterVersion", ""))
    project_name = str(canonical.get("projectName", ""))
    revision = canonical.get("revision", {})
    symbols = canonical.get("symbols", [])
    references = canonical.get("references", [])
    summary = canonical.get("summary", {})

    if expected_schema and schema_version != expected_schema:
        errors.append(f"schemaVersion={schema_version}, expected {expected_schema}")
    if expected_exporter and exporter_version != expected_exporter:
        errors.append(f"exporterVersion={exporter_version}, expected {expected_exporter}")
    if expected_project and project_name != expected_project:
        errors.append(f"projectName={project_name}, expected {expected_project}")

    revision_value = str(revision.get("value", ""))
    content_sha256 = str(revision.get("contentSha256", ""))
    if revision.get("available") and not revision_value:
        errors.append("revision is available but value is empty")
    if content_sha256 and revision_value != f"sha256:{content_sha256}":
        errors.append("revision value does not match contentSha256")

    symbol_ids = [str(item.get("id", "")) for item in symbols]
    reference_ids = [str(item.get("id", "")) for item in references]
    if any(not value for value in symbol_ids):
        errors.append("one or more symbols have an empty id")
    if any(not value for value in reference_ids):
        errors.append("one or more references have an empty id")
    if len(symbol_ids) != len(set(symbol_ids)):
        errors.append("duplicate symbol ids")
    if len(reference_ids) != len(set(reference_ids)):
        errors.append("duplicate reference ids")

    if summary.get("symbols") != len(symbols):
        errors.append("summary.symbols does not match symbols array")
    if summary.get("references") != len(references):
        errors.append("summary.references does not match references array")

    symbol_kinds = collections.Counter(str(item.get("kind", "")) for item in symbols)
    missing_symbol_kinds = sorted(required_symbol_kinds - set(symbol_kinds))
    if missing_symbol_kinds:
        errors.append(f"missing required symbol kinds: {', '.join(missing_symbol_kinds)}")

    reference_kinds = collections.Counter(str(item.get("kind", "")) for item in references)
    missing_kinds = sorted(required_reference_kinds - set(reference_kinds))
    if missing_kinds:
        errors.append(f"missing required reference kinds: {', '.join(missing_kinds)}")

    if len(records.get("H", [])) != 1:
        errors.append(f"BPCTX header count is {len(records.get('H', []))}, expected 1")
    else:
        header = records["H"][0]
        if expected_schema and f"schema={expected_schema}" not in header:
            errors.append("BPCTX header schema does not match")
        if expected_exporter and f"exporter={expected_exporter}" not in header:
            errors.append("BPCTX header exporter does not match")
        if expected_project and f"project={expected_project}" not in header:
            errors.append("BPCTX header project does not match")

    if len(records.get("R", [])) != 1:
        errors.append(f"BPCTX revision count is {len(records.get('R', []))}, expected 1")
    else:
        revision_record = records["R"][0]
        if len(revision_record) < 2 or revision_record[1] != revision_value:
            errors.append("BPCTX revision value does not match Canonical JSON")
        if content_sha256 and f"sha256={content_sha256}" not in revision_record:
            errors.append("BPCTX SHA-256 does not match Canonical JSON")

    if len(records.get("S", [])) != len(symbols):
        errors.append("BPCTX S count does not match Canonical symbols")
    if len(records.get("D", [])) != len(references):
        errors.append("BPCTX D count does not match Canonical references")

    short_symbols = {record[1] for record in records.get("S", []) if len(record) > 1}
    short_graphs = {record[1] for record in records.get("G", []) if len(record) > 1}
    short_nodes = {record[1] for record in records.get("N", []) if len(record) > 1}
    for record in records.get("D", []):
        if len(record) < 6:
            errors.append(f"invalid BPCTX D record: {'|'.join(record)}")
            continue

        source, target = record[3], record[4]
        if source.startswith("s") and source not in short_symbols:
            errors.append(f"unknown BPCTX source symbol: {source}")
        if target.startswith("s") and target not in short_symbols:
            errors.append(f"unknown BPCTX target symbol: {target}")

        keyed_fields = {
            field.split("=", 1)[0]: field.split("=", 1)[1]
            for field in record[5:]
            if "=" in field
        }
        graph_id = keyed_fields.get("graph", "")
        node_id = keyed_fields.get("node", "")
        if graph_id and graph_id not in short_graphs:
            errors.append(f"unknown BPCTX graph id: {graph_id}")
        if node_id and node_id not in short_nodes:
            errors.append(f"unknown BPCTX node id: {node_id}")

    return {
        "canonical": str(canonical_path),
        "bpctx": str(bpctx_path),
        "assetPath": canonical.get("assetPath", ""),
        "schemaVersion": schema_version,
        "exporterVersion": exporter_version,
        "projectName": project_name,
        "revision": revision_value,
        "symbols": len(symbols),
        "symbolKinds": dict(symbol_kinds),
        "references": len(references),
        "referenceKinds": dict(reference_kinds),
        "bpctxLines": len(lines),
        "bpctxRecordCounts": {key: len(value) for key, value in sorted(records.items())},
        "errors": errors,
    }


def main() -> int:
    args = parse_args()
    output_root = args.output.expanduser().resolve()
    manifest_path = output_root / "manifest.json"
    canonical_root = output_root / "canonical"
    bpctx_root = output_root / "bpctx"
    errors: list[str] = []

    if not manifest_path.is_file():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")
    if not canonical_root.is_dir():
        raise FileNotFoundError(f"Canonical directory not found: {canonical_root}")
    if not bpctx_root.is_dir():
        raise FileNotFoundError(f"BPCTX directory not found: {bpctx_root}")

    manifest = load_json(manifest_path)
    success_count = int(manifest.get("successCount", 0))
    manifest_project = str(manifest.get("projectName", ""))
    if args.expect_project and manifest_project != args.expect_project:
        errors.append(f"manifest projectName={manifest_project}, expected {args.expect_project}")
    failure_count = int(manifest.get("failureCount", 0))
    if failure_count and not args.allow_failures:
        errors.append(f"manifest contains {failure_count} failure(s)")

    canonical_files = sorted(canonical_root.rglob("*.json"))
    bpctx_files = sorted(bpctx_root.rglob("*.bpctx"))
    if len(canonical_files) != success_count:
        errors.append(f"canonical file count {len(canonical_files)} does not match successCount {success_count}")
    if len(bpctx_files) != success_count:
        errors.append(f"BPCTX file count {len(bpctx_files)} does not match successCount {success_count}")

    bpctx_by_key = {
        path.relative_to(bpctx_root).with_suffix("").as_posix(): path
        for path in bpctx_files
    }
    validations: list[dict[str, Any]] = []
    required_symbol_kinds = set(args.require_symbol_kind)
    required_reference_kinds = set(args.require_reference_kind)
    for canonical_path in canonical_files:
        key = canonical_path.relative_to(canonical_root).with_suffix("").as_posix()
        bpctx_path = bpctx_by_key.get(key)
        if bpctx_path is None:
            errors.append(f"matching BPCTX file not found for {canonical_path}")
            continue
        validation = validate_pair(
            canonical_path,
            bpctx_path,
            args.expect_schema,
            args.expect_exporter,
            args.expect_project,
            required_symbol_kinds,
            required_reference_kinds,
        )
        errors.extend(f"{key}: {message}" for message in validation["errors"])
        validations.append(validation)

    asset_result: dict[str, Any] = {}
    if args.asset_file:
        asset_path = args.asset_file.expanduser().resolve()
        if len(validations) != 1:
            errors.append("--asset-file requires exactly one exported asset")
        elif not asset_path.is_file():
            errors.append(f"asset file not found: {asset_path}")
        else:
            disk_sha256 = sha256(asset_path)
            canonical_sha256 = load_json(canonical_files[0]).get("revision", {}).get("contentSha256", "")
            asset_result = {
                "path": str(asset_path),
                "sha256": disk_sha256,
                "matchesRevision": disk_sha256 == canonical_sha256,
            }
            if disk_sha256 != canonical_sha256:
                errors.append("asset file SHA-256 does not match exported revision")

    result = {
        "output": str(output_root),
        "manifestSuccess": success_count,
        "manifestFailure": failure_count,
        "projectName": manifest_project,
        "assets": validations,
        "assetFile": asset_result,
        "errors": errors,
        "valid": not errors,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
