from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Literal, Sequence

from .agent_api import IndexQueryService, IndexSnapshotError
from .config import DEFAULT_DATABASE

try:
    from mcp.server.fastmcp import FastMCP
except ModuleNotFoundError as exc:  # pragma: no cover - exercised in dependency-free installs
    FastMCP = None  # type: ignore[assignment,misc]
    _MCP_IMPORT_ERROR: ModuleNotFoundError | None = exc
else:
    _MCP_IMPORT_ERROR = None


MCP_SERVER_NAME = "UE Agent Kit"
MCP_SERVER_INSTRUCTIONS = (
    "Read-only access to the UE Agent Kit SQLite index. "
    "Use ue_search to locate assets or symbols, ue_get_asset for one exact asset path, "
    "and ue_find_references for dependency and Blueprint reference edges. "
    "This server cannot execute shell commands, load Unreal objects, or write assets."
)


def _error_response(tool: str, error: Exception) -> dict[str, Any]:
    message = str(error)
    if isinstance(error, FileNotFoundError):
        code = "database-not-found"
        message = "The configured UE Agent Kit database was not found."
    elif isinstance(error, OSError):
        code = "filesystem-error"
        message = "The configured read-only index could not be accessed."
    elif isinstance(error, IndexSnapshotError):
        code = "index-not-quiescent"
    elif isinstance(error, ValueError):
        code = "invalid-arguments"
    elif isinstance(error, sqlite3.Error):
        code = "database-error"
    else:
        code = "index-error"
    return {
        "schemaVersion": "1.0",
        "tool": tool,
        "ok": False,
        "readOnly": True,
        "error": {
            "code": code,
            "type": type(error).__name__,
            "message": message,
        },
    }


def create_mcp_server(database_path: Path):
    if FastMCP is None:
        raise RuntimeError(
            "MCP support is not installed. Run scripts\\setup_python.cmd -WithMcp "
            "or install the mcp optional dependency."
        ) from _MCP_IMPORT_ERROR

    service = IndexQueryService(database_path)
    service.check()
    server = FastMCP(
        MCP_SERVER_NAME,
        instructions=MCP_SERVER_INSTRUCTIONS,
        json_response=True,
    )

    @server.tool()
    def ue_search(
        query: str = "",
        scope: Literal["assets", "symbols"] = "assets",
        asset_class: str = "",
        kind: str = "",
        asset_path: str = "",
        limit: int = 20,
        offset: int = 0,
        include_details: bool = False,
    ) -> dict[str, Any]:
        """Search indexed Unreal assets or Blueprint symbols with bounded pagination."""
        try:
            return service.search(
                query,
                scope=scope,
                asset_class=asset_class,
                kind=kind,
                asset_path=asset_path,
                limit=limit,
                offset=offset,
                include_details=include_details,
            )
        except (FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return _error_response("ue_search", exc)

    @server.tool()
    def ue_get_asset(
        asset_path: str,
        symbol_limit: int = 100,
        reference_limit: int = 200,
        graph_limit: int = 100,
        node_limit: int = 100,
        include_details: bool = False,
    ) -> dict[str, Any]:
        """Get one exact indexed Unreal asset with bounded symbols, references, graphs, and nodes."""
        try:
            return service.get_asset(
                asset_path,
                symbol_limit=symbol_limit,
                reference_limit=reference_limit,
                graph_limit=graph_limit,
                node_limit=node_limit,
                include_details=include_details,
            )
        except (FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return _error_response("ue_get_asset", exc)

    @server.tool()
    def ue_find_references(
        query: str = "",
        kind: str = "",
        asset_path: str = "",
        source_symbol_id: str = "",
        target_symbol_id: str = "",
        target_asset_path: str = "",
        limit: int = 50,
        offset: int = 0,
        include_details: bool = False,
    ) -> dict[str, Any]:
        """Find indexed dependency or Blueprint reference edges using explicit filters."""
        try:
            return service.find_references(
                query=query,
                kind=kind,
                asset_path=asset_path,
                source_symbol_id=source_symbol_id,
                target_symbol_id=target_symbol_id,
                target_asset_path=target_asset_path,
                limit=limit,
                offset=offset,
                include_details=include_details,
            )
        except (FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return _error_response("ue_find_references", exc)

    return server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ue-agent-mcp",
        description="Run the read-only UE Agent Kit MCP server over stdio.",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE,
        help=f"Read-only SQLite index path. Default: {DEFAULT_DATABASE}",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate the database and print index status without starting MCP.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        service = IndexQueryService(args.database)
        if args.check:
            print(json.dumps(service.check(), ensure_ascii=False, indent=2))
            return 0
        server = create_mcp_server(args.database)
        server.run(transport="stdio")
        return 0
    except (FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(
            json.dumps(_error_response("ue_agent_kit_mcp", exc), ensure_ascii=False),
            file=sys.stderr,
        )
        return 2 if isinstance(exc, FileNotFoundError) else 1


if __name__ == "__main__":
    raise SystemExit(main())
