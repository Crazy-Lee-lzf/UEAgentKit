from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Sequence

from .backups import (
    create_backup_manifest,
    rollback_backup,
    verify_rollback_export,
)
from .config import DEFAULT_DATABASE, DEFAULT_MEMORY_DATABASE
from .database import assert_fts5_available, get_schema_version, open_database
from .fixtures import validate_fixture_plan, verify_fixture_export
from .indexer import build_index
from .knowledge_view import KnowledgeViewConfig, serve as serve_knowledge_view
from .memory_reports import (
    MAX_AUDIT_NODES,
    MAX_AUDIT_RECORDS,
    MAX_AUDIT_STATUS_EVENTS,
    MAX_AUDIT_WORK_ITEMS,
    build_memory_audit_report,
    memory_record_payload,
)
from .memory_service import ProjectMemoryService
from .memory_vector import (
    Model2VecProvider,
    backfill_embeddings,
    ensure_embeddings_for_records,
    vector_model_path_from_env,
)
from .project_memory import (
    MemoryRecordType,
    MemoryScopeType,
    MemoryStatus,
    open_project_memory_database,
)
from .patches import PATCH_SCHEMA_VERSION, get_operation_registry, validate_patch
from .queries import find_references, get_asset, get_stats, search_assets, search_symbols
from .schema import CURRENT_SCHEMA_VERSION


def _add_database_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE,
        help=f"SQLite database path. Default: {DEFAULT_DATABASE}",
    )


def _add_memory_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--memory-database",
        type=Path,
        default=DEFAULT_MEMORY_DATABASE,
        help=f"Writable Project Memory SQLite path. Default: {DEFAULT_MEMORY_DATABASE}",
    )
    parser.add_argument(
        "--project-key",
        default=os.environ.get("UEAK_PROJECT_KEY", ""),
        help="Fixed Project Key. Defaults to UEAK_PROJECT_KEY.",
    )


def _add_pagination_arguments(parser: argparse.ArgumentParser, *, default_limit: int) -> None:
    parser.add_argument("--limit", type=int, default=default_limit)
    parser.add_argument("--offset", type=int, default=0)


def _serialize_json(value: Any, *, compact: bool) -> str:
    if compact:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False)


def _write_json_file(path: Path, value: Any) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temporary = resolved.with_name(f".{resolved.name}.tmp")
    output = _serialize_json(value, compact=False) + "\n"
    try:
        temporary.write_text(output, encoding="utf-8", newline="\r\n")
        os.replace(temporary, resolved)
    finally:
        if temporary.exists():
            temporary.unlink()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ue-agent",
        description="UEAgentKit project index and query CLI.",
    )
    parser.add_argument("--compact", action="store_true", help="Emit compact JSON.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    index_parser = subparsers.add_parser("index", help="Build or inspect the SQLite index.")
    index_subparsers = index_parser.add_subparsers(dest="index_command", required=True)

    index_build = index_subparsers.add_parser("build", help="Import an export directory into SQLite.")
    _add_database_argument(index_build)
    index_build.add_argument("export_root", type=Path)
    index_build.add_argument("--force", action="store_true")
    index_build.add_argument("--project-key", default=os.environ.get("UEAK_PROJECT_KEY", ""))
    index_build.add_argument("--include-assets", action="store_true", help="Include per-asset results in JSON output.")
    index_build.add_argument(
        "--prune-prefix",
        default="",
        help="Explicit Unreal asset path prefix to prune, for example /Game. No prune occurs when omitted.",
    )

    index_stats = index_subparsers.add_parser("stats", help="Show index statistics.")
    _add_database_argument(index_stats)

    search_parser = subparsers.add_parser("search", help="Search assets or symbols.")
    search_subparsers = search_parser.add_subparsers(dest="search_command", required=True)

    search_assets_parser = search_subparsers.add_parser("assets", help="Search indexed assets.")
    _add_database_argument(search_assets_parser)
    search_assets_parser.add_argument("query", nargs="?", default="")
    search_assets_parser.add_argument("--class", dest="asset_class", default="")
    _add_pagination_arguments(search_assets_parser, default_limit=50)

    search_symbols_parser = search_subparsers.add_parser("symbols", help="Search indexed symbols.")
    _add_database_argument(search_symbols_parser)
    search_symbols_parser.add_argument("query", nargs="?", default="")
    search_symbols_parser.add_argument("--kind", default="")
    search_symbols_parser.add_argument("--asset", default="")
    search_symbols_parser.add_argument("--include-details", action="store_true")
    _add_pagination_arguments(search_symbols_parser, default_limit=50)

    references_parser = subparsers.add_parser("references", help="Find symbol references.")
    _add_database_argument(references_parser)
    references_parser.add_argument("query", nargs="?", default="")
    references_parser.add_argument("--kind", default="")
    references_parser.add_argument("--asset", default="")
    references_parser.add_argument("--source", default="")
    references_parser.add_argument("--target", default="")
    references_parser.add_argument("--target-asset", default="")
    references_parser.add_argument("--include-details", action="store_true")
    _add_pagination_arguments(references_parser, default_limit=100)

    asset_parser = subparsers.add_parser("asset", help="Get one indexed asset with symbols and references.")
    _add_database_argument(asset_parser)
    asset_parser.add_argument("asset_path")
    asset_parser.add_argument("--symbol-limit", type=int, default=200)
    asset_parser.add_argument("--reference-limit", type=int, default=500)
    asset_parser.add_argument("--node-limit", type=int, default=200)
    asset_parser.add_argument("--include-details", action="store_true")

    memory_parser = subparsers.add_parser(
        "memory",
        help="Inspect, validate, or export revision-aware Project Memory.",
    )
    memory_subparsers = memory_parser.add_subparsers(dest="memory_command", required=True)

    memory_status = memory_subparsers.add_parser("status", help="Show fixed-project Memory counts.")
    _add_memory_arguments(memory_status)

    memory_search = memory_subparsers.add_parser("search", help="Search current Project Memory records.")
    _add_memory_arguments(memory_search)
    memory_search.add_argument("query")
    memory_search.add_argument(
        "--record-type",
        dest="record_types",
        action="append",
        choices=tuple(item.value for item in MemoryRecordType),
        default=None,
    )
    memory_search.add_argument(
        "--status",
        dest="statuses",
        action="append",
        choices=tuple(item.value for item in MemoryStatus),
        default=None,
    )
    memory_search.add_argument(
        "--scope-type",
        choices=tuple(item.value for item in MemoryScopeType),
        default="",
    )
    memory_search.add_argument("--scope-key", default="")
    memory_search.add_argument("--limit", type=int, default=20)

    memory_backfill = memory_subparsers.add_parser(
        "backfill-embeddings",
        help=(
            "Deterministically backfill Project Memory embeddings using the optional "
            "local model2vec vector model (offline command; never runs on query paths)."
        ),
    )
    _add_memory_arguments(memory_backfill)
    memory_backfill.add_argument(
        "--model-dir",
        dest="model_dir",
        type=Path,
        default=None,
        help=(
            "Existing local model2vec model directory containing model.safetensors. "
            "Defaults to the UEAGENTKIT_MEMORY_VECTOR_MODEL environment variable."
        ),
    )
    memory_backfill.add_argument(
        "--batch-size",
        type=int,
        default=64,
        help="Bounded batch size for selection and embedding. Default: 64, hard max: 500.",
    )
    memory_backfill.add_argument(
        "--max-records",
        type=int,
        default=0,
        help="Optional bound on records processed in this run (0 = unbounded).",
    )

    memory_get = memory_subparsers.add_parser("get", help="Get one exact Memory record.")
    _add_memory_arguments(memory_get)
    memory_get.add_argument("record_id")

    memory_validate = memory_subparsers.add_parser(
        "validate",
        help="Mark Memory records stale when fixed index Revisions changed.",
    )
    _add_memory_arguments(memory_validate)
    memory_validate.add_argument(
        "--index-database",
        type=Path,
        default=DEFAULT_DATABASE,
        help=f"Immutable index used for Revision validation. Default: {DEFAULT_DATABASE}",
    )

    memory_distill = memory_subparsers.add_parser(
        "distill",
        help="Deterministically distill pending L0 events into L1 Project Memory records.",
    )
    _add_memory_arguments(memory_distill)
    memory_distill.add_argument(
        "--artifact-root",
        type=Path,
        required=True,
        help="Fixed M2 Writer work_root containing durable L0 artifacts.",
    )
    memory_distill.add_argument(
        "--index-database",
        type=Path,
        default=DEFAULT_DATABASE,
        help=f"Immutable index used for Revision source validation. Default: {DEFAULT_DATABASE}",
    )
    memory_distill.add_argument(
        "--policy",
        dest="policy_path",
        type=Path,
        required=True,
        help="Fixed Project Write Policy path used for policy-digest source validation.",
    )
    memory_distill.add_argument(
        "--max-events",
        type=int,
        default=100,
        help="Maximum pending L0 events to distill. Default: 100, hard max: 100.",
    )

    memory_export = memory_subparsers.add_parser(
        "export",
        help="Write a portable audit JSON with all records and status events.",
    )
    _add_memory_arguments(memory_export)
    memory_export.add_argument("--output", type=Path, required=True)
    memory_export.add_argument("--max-records", type=int, default=MAX_AUDIT_RECORDS)
    memory_export.add_argument("--max-nodes", type=int, default=MAX_AUDIT_NODES)
    memory_export.add_argument("--max-work-items", type=int, default=MAX_AUDIT_WORK_ITEMS)
    memory_export.add_argument(
        "--max-status-events",
        type=int,
        default=MAX_AUDIT_STATUS_EVENTS,
    )

    memory_build_context = memory_subparsers.add_parser(
        "build-context",
        help=(
            "Deterministically build the persisted L2/L3 Task Context injection "
            "snapshot (offline command; automatic Task Context only reads it)."
        ),
    )
    _add_memory_arguments(memory_build_context)
    memory_build_context.add_argument(
        "--index-database",
        type=Path,
        default=DEFAULT_DATABASE,
        help=f"Immutable fixed index used for asset-class facts. Default: {DEFAULT_DATABASE}",
    )
    memory_build_context.add_argument(
        "--max-l2-groups",
        type=int,
        default=8,
        help="Bounded deterministic L2 recipe groups. Default: 8, hard max: 8.",
    )
    memory_build_context.add_argument(
        "--max-l3-entries",
        type=int,
        default=48,
        help="Bounded deterministic L3 entries. Default: 48, hard max: 48.",
    )

    patch_parser = subparsers.add_parser("patch", help="Inspect or validate declarative Blueprint patches.")
    patch_subparsers = patch_parser.add_subparsers(dest="patch_command", required=True)

    patch_subparsers.add_parser("operations", help="List validation-only patch operations.")

    patch_validate = patch_subparsers.add_parser(
        "validate",
        help="Validate a patch without loading or modifying UObject data.",
    )
    patch_validate.add_argument("--patch", dest="patch_path", type=Path, required=True)
    patch_validate.add_argument("--policy", dest="policy_path", type=Path, required=True)
    patch_validate.add_argument("--export", dest="export_root", type=Path, required=True)
    patch_validate.add_argument("--report", dest="report_path", type=Path)

    patch_manifest = patch_subparsers.add_parser(
        "manifest",
        help="Create an auditable backup manifest from a successful Commit report.",
    )
    patch_manifest.add_argument("--patch", dest="patch_path", type=Path, required=True)
    patch_manifest.add_argument("--policy", dest="policy_path", type=Path, required=True)
    patch_manifest.add_argument("--report", dest="commit_report_path", type=Path, required=True)
    patch_manifest.add_argument("--backup-root", dest="backup_root", type=Path, required=True)
    patch_manifest.add_argument("--output", dest="manifest_output", type=Path)

    patch_rollback = patch_subparsers.add_parser(
        "rollback",
        help="Validate or explicitly restore one single-file package from a backup manifest.",
    )
    patch_rollback.add_argument("--manifest", dest="manifest_path", type=Path, required=True)
    patch_rollback.add_argument("--policy", dest="policy_path", type=Path, required=True)
    patch_rollback.add_argument("--project", dest="project_path", type=Path, required=True)
    patch_rollback.add_argument("--backup-root", dest="backup_root", type=Path, required=True)
    patch_rollback.add_argument("--mode", choices=("DryRun", "Commit"), default="DryRun")
    patch_rollback.add_argument("--report", dest="rollback_report_path", type=Path)

    patch_verify_rollback = patch_subparsers.add_parser(
        "verify-rollback",
        help="Verify a rollback using an independent Unreal asset export.",
    )
    patch_verify_rollback.add_argument(
        "--rollback-report",
        dest="rollback_report_path",
        type=Path,
        required=True,
    )
    patch_verify_rollback.add_argument("--export", dest="export_root", type=Path, required=True)
    patch_verify_rollback.add_argument("--report", dest="verification_report_path", type=Path)

    fixtures_parser = subparsers.add_parser(
        "fixtures",
        help="Validate write-fixture plans or verify independently reloaded fixtures.",
    )
    fixtures_subparsers = fixtures_parser.add_subparsers(dest="fixtures_command", required=True)

    fixtures_validate = fixtures_subparsers.add_parser(
        "validate",
        help="Validate a write-fixture plan without loading Unreal assets.",
    )
    fixtures_validate.add_argument("--plan", dest="fixture_plan_path", type=Path, required=True)
    fixtures_validate.add_argument("--report", dest="fixture_validation_report_path", type=Path)

    fixtures_verify = fixtures_subparsers.add_parser(
        "verify",
        help="Verify fixture classes and revisions from an independent Unreal export.",
    )
    fixtures_verify.add_argument("--fixture-report", dest="fixture_report_path", type=Path, required=True)
    fixtures_verify.add_argument("--export", dest="fixture_export_root", type=Path, required=True)
    fixtures_verify.add_argument("--report", dest="fixture_verification_report_path", type=Path)

    knowledge_view_parser = subparsers.add_parser(
        "knowledge-view",
        help="Serve the local read-only Knowledge Web UI (loopback only).",
    )
    _add_memory_arguments(knowledge_view_parser)
    _add_database_argument(knowledge_view_parser)
    knowledge_view_parser.add_argument("--port", type=int, default=8765)
    knowledge_view_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address. Loopback only; non-loopback addresses are rejected.",
    )

    return parser


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def _print_json(value: Any, *, compact: bool) -> None:
    print(_serialize_json(value, compact=compact))


def _open_query_database(path: Path):
    context = open_database(path, readonly=True, migrate=False)
    connection = context.__enter__()
    try:
        version = get_schema_version(connection)
        if version != CURRENT_SCHEMA_VERSION:
            raise RuntimeError(
                f"Database schema is {version}; this CLI requires schema {CURRENT_SCHEMA_VERSION}."
            )
        assert_fts5_available(connection)
    except Exception:
        context.__exit__(*sys.exc_info())
        raise
    return context, connection


def _ensure_distill_embeddings(memory_service: ProjectMemoryService, *, produced_ids: tuple[str, ...]) -> dict[str, Any]:
    """M4 post-distill integration: ensure embeddings only when vector mode is
    explicitly configured. Vector-disabled distillation is unchanged; a vector
    failure is reported with a stable reason and never fails the distillation."""
    if vector_model_path_from_env() is None:
        return {"enabled": False, "reason": "vector-model-not-configured"}
    if not produced_ids:
        return {"enabled": True, "selected": 0, "created": 0, "rebuilt": 0, "failed": 0}
    try:
        provider = Model2VecProvider.from_local_dir(vector_model_path_from_env())
        with open_project_memory_database(memory_service.database_path) as connection:
            report = ensure_embeddings_for_records(
                connection,
                provider,
                project_key=memory_service.project_key,
                record_ids=produced_ids,
            )
        payload = {"enabled": True}
        payload.update(report.to_payload())
        return payload
    except Exception:
        return {"enabled": True, "error": "vector-backfill-failed"}


def run(args: argparse.Namespace) -> tuple[Any, int]:
    if args.command == "knowledge-view":
        summary = serve_knowledge_view(
            KnowledgeViewConfig(
                memory_database=args.memory_database,
                database=args.database,
                project_key=args.project_key,
                host=args.host,
                port=args.port,
            )
        )
        return summary, 0

    if args.command == "memory":
        memory_service = ProjectMemoryService(
            database_path=args.memory_database,
            project_key=args.project_key,
        )
        if args.memory_command == "status":
            status = memory_service.status()
            return {
                "schemaVersion": "1.0",
                "tool": "ue_memory_status",
                "projectKey": status.project_key,
                "memorySchemaVersion": status.schema_version,
                "recordCount": status.record_count,
                "nodeCount": status.node_count,
                "activeWorkCount": status.active_work_count,
                "countsByType": status.counts_by_type,
                "countsByStatus": status.counts_by_status,
            }, 0
        if args.memory_command == "search":
            kwargs: dict[str, Any] = {
                "query": args.query,
                "record_types": tuple(args.record_types or ()),
                "scope_type": args.scope_type or None,
                "scope_key": args.scope_key,
                "limit": args.limit,
            }
            if args.statuses is not None:
                kwargs["statuses"] = tuple(args.statuses)
            result = memory_service.search_records(**kwargs)
            return {
                "schemaVersion": "1.0",
                "tool": "ue_memory_search",
                "projectKey": memory_service.project_key,
                "resultCount": len(result.hits),
                "retrieval": result.to_payload(),
                "items": [
                    {"rank": hit.rank, "record": memory_record_payload(hit.record)}
                    for hit in result.hits
                ],
            }, 0
        if args.memory_command == "get":
            return {
                "schemaVersion": "1.0",
                "tool": "ue_memory_get",
                "projectKey": memory_service.project_key,
                "record": memory_record_payload(memory_service.get_record(args.record_id)),
            }, 0
        if args.memory_command == "validate":
            result = memory_service.validate_against_index(args.index_database)
            return {
                "schemaVersion": "1.0",
                "tool": "ue_memory_validate",
                "projectKey": result.project_key,
                "indexedAssetCount": result.indexed_asset_count,
                "checkedRecordIds": list(result.invalidation.checked_record_ids),
                "staleRecordIds": list(result.invalidation.stale_record_ids),
                "reasons": result.invalidation.reasons,
            }, 0
        if args.memory_command == "backfill-embeddings":
            model_dir = args.model_dir if args.model_dir is not None else vector_model_path_from_env()
            if model_dir is None:
                return {
                    "schemaVersion": "1.0",
                    "tool": "ue_memory_backfill_embeddings",
                    "projectKey": memory_service.project_key,
                    "valid": False,
                    "error": "vector-model-not-configured",
                    "message": (
                        "Provide --model-dir or set UEAGENTKIT_MEMORY_VECTOR_MODEL to an "
                        "existing local model2vec model directory."
                    ),
                }, 2
            provider = Model2VecProvider.from_local_dir(model_dir)
            with open_project_memory_database(memory_service.database_path) as connection:
                report = backfill_embeddings(
                    connection,
                    provider,
                    project_key=memory_service.project_key,
                    batch_size=args.batch_size,
                    max_records=args.max_records,
                )
            payload = {
                "schemaVersion": "1.0",
                "tool": "ue_memory_backfill_embeddings",
                "projectKey": memory_service.project_key,
            }
            payload.update(report.to_payload())
            return payload, 0 if report.failed == 0 else 1
        if args.memory_command == "distill":
            distiller = memory_service.distillation_service(
                artifact_root=args.artifact_root,
                index_database=args.index_database,
                policy_path=args.policy_path,
            )
            distillation = distiller.distill(max_events=args.max_events)
            payload = distillation.to_payload()
            payload["projectKey"] = memory_service.project_key
            payload["sourceValidation"] = distiller.validate_source_bindings()
            payload["evidenceChainVerdicts"] = distiller.evaluate_evidence_chains()
            payload["embeddingBackfill"] = _ensure_distill_embeddings(
                memory_service,
                produced_ids=distillation.produced_record_ids + distillation.reused_record_ids,
            )
            return payload, 0
        if args.memory_command == "export":
            report = build_memory_audit_report(
                memory_service,
                max_records=args.max_records,
                max_status_events=args.max_status_events,
                max_nodes=args.max_nodes,
                max_work_items=args.max_work_items,
            )
            _write_json_file(args.output, report)
            return {
                "schemaVersion": "1.0",
                "tool": "ue_memory_export",
                "projectKey": memory_service.project_key,
                "exported": True,
                "output": str(args.output),
                "recordCount": report["recordCount"],
                "statusEventCount": report["statusEventCount"],
                "nodeCount": report["nodeCount"],
                "activeWorkCount": report["activeWorkCount"],
                "snapshotSha256": report["integrity"]["snapshotSha256"],
            }, 0
        if args.memory_command == "build-context":
            result = memory_service.build_context(
                index_database=args.index_database,
                max_l2_groups=args.max_l2_groups,
                max_l3_entries=args.max_l3_entries,
            )
            return result.to_payload(), 0
        raise RuntimeError("Unsupported Project Memory command.")

    if args.command == "patch" and args.patch_command == "operations":
        return {
            "schemaVersion": PATCH_SCHEMA_VERSION,
            "validationOnly": True,
            "willLoadOrModifyUObjects": False,
            "willWriteDisk": False,
            "commitSupported": False,
            "operations": get_operation_registry(),
        }, 0

    if args.command == "patch" and args.patch_command == "validate":
        result = validate_patch(args.patch_path, args.policy_path, args.export_root)
        if args.report_path is not None:
            _write_json_file(args.report_path, result)
        return result, 0 if result["valid"] else 1

    if args.command == "patch" and args.patch_command == "manifest":
        result = create_backup_manifest(
            args.patch_path,
            args.policy_path,
            args.commit_report_path,
            args.backup_root,
            output_path=args.manifest_output,
        )
        return result, 0

    if args.command == "patch" and args.patch_command == "rollback":
        result = rollback_backup(
            args.manifest_path,
            args.policy_path,
            args.project_path,
            args.backup_root,
            commit=args.mode == "Commit",
            report_path=args.rollback_report_path,
        )
        return result, 0 if result["valid"] and (args.mode != "Commit" or result["restored"]) else 1

    if args.command == "patch" and args.patch_command == "verify-rollback":
        result = verify_rollback_export(args.rollback_report_path, args.export_root)
        if args.verification_report_path is not None:
            _write_json_file(args.verification_report_path, result)
        return result, 0 if result["verified"] else 1

    if args.command == "fixtures" and args.fixtures_command == "validate":
        result = validate_fixture_plan(args.fixture_plan_path)
        if args.fixture_validation_report_path is not None:
            _write_json_file(args.fixture_validation_report_path, result)
        return result, 0 if result["valid"] else 1

    if args.command == "fixtures" and args.fixtures_command == "verify":
        result = verify_fixture_export(args.fixture_report_path, args.fixture_export_root)
        if args.fixture_verification_report_path is not None:
            _write_json_file(args.fixture_verification_report_path, result)
        return result, 0 if result["verified"] else 1

    if args.command == "index" and args.index_command == "build":
        with open_database(args.database) as connection:
            result = build_index(
                connection,
                args.export_root,
                args.database,
                force=args.force,
                prune_prefix=args.prune_prefix,
                project_key=args.project_key,
            )
        return result.to_dict(include_assets=args.include_assets), 0 if result.failed == 0 and not result.errors else 1

    context, connection = _open_query_database(args.database)
    try:
        if args.command == "index" and args.index_command == "stats":
            return get_stats(connection), 0
        if args.command == "search" and args.search_command == "assets":
            return {
                "query": args.query,
                "assetClass": args.asset_class,
                "limit": args.limit,
                "offset": args.offset,
                "results": search_assets(
                    connection,
                    args.query,
                    asset_class=args.asset_class,
                    limit=args.limit,
                    offset=args.offset,
                ),
            }, 0
        if args.command == "search" and args.search_command == "symbols":
            return {
                "query": args.query,
                "kind": args.kind,
                "asset": args.asset,
                "limit": args.limit,
                "offset": args.offset,
                "results": search_symbols(
                    connection,
                    args.query,
                    kind=args.kind,
                    asset_path=args.asset,
                    limit=args.limit,
                    offset=args.offset,
                    include_details=args.include_details,
                ),
            }, 0
        if args.command == "references":
            return {
                "query": args.query,
                "kind": args.kind,
                "asset": args.asset,
                "source": args.source,
                "target": args.target,
                "targetAsset": args.target_asset,
                "limit": args.limit,
                "offset": args.offset,
                "results": find_references(
                    connection,
                    query=args.query,
                    kind=args.kind,
                    asset_path=args.asset,
                    source_symbol_id=args.source,
                    target_symbol_id=args.target,
                    target_asset_path=args.target_asset,
                    limit=args.limit,
                    offset=args.offset,
                    include_details=args.include_details,
                ),
            }, 0
        if args.command == "asset":
            result = get_asset(
                connection,
                args.asset_path,
                symbol_limit=args.symbol_limit,
                reference_limit=args.reference_limit,
                node_limit=args.node_limit,
                include_details=args.include_details,
            )
            if result is None:
                return {"assetPath": args.asset_path, "found": False}, 2
            return {"found": True, "asset": result}, 0
    finally:
        context.__exit__(None, None, None)

    raise RuntimeError("Unsupported command.")


def main(argv: Sequence[str] | None = None) -> int:
    _configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result, exit_code = run(args)
        _print_json(result, compact=args.compact)
        return exit_code
    except KeyError as exc:
        message = str(exc.args[0]) if exc.args else "Requested record was not found."
        _print_json(
            {
                "error": type(exc).__name__,
                "message": message,
                "valid": False,
            },
            compact=args.compact,
        )
        return 2
    except FileNotFoundError as exc:
        _print_json(
            {
                "error": type(exc).__name__,
                "message": str(exc),
                "valid": False,
            },
            compact=args.compact,
        )
        return 2
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        _print_json(
            {
                "error": type(exc).__name__,
                "message": str(exc),
                "valid": False,
            },
            compact=args.compact,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
