from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Sequence

from .config import DEFAULT_DATABASE
from .database import assert_fts5_available, get_schema_version, open_database
from .indexer import build_index
from .queries import find_references, get_asset, get_stats, search_assets, search_symbols
from .schema import CURRENT_SCHEMA_VERSION


def _add_database_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE,
        help=f"SQLite database path. Default: {DEFAULT_DATABASE}",
    )


def _add_pagination_arguments(parser: argparse.ArgumentParser, *, default_limit: int) -> None:
    parser.add_argument("--limit", type=int, default=default_limit)
    parser.add_argument("--offset", type=int, default=0)


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

    return parser


def _print_json(value: Any, *, compact: bool) -> None:
    if compact:
        output = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    else:
        output = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False)
    print(output)


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


def run(args: argparse.Namespace) -> tuple[Any, int]:
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
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result, exit_code = run(args)
        _print_json(result, compact=args.compact)
        return exit_code
    except (FileNotFoundError, ValueError, RuntimeError, sqlite3.Error) as exc:
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
