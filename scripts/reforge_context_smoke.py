"""Real Reforge read-only Context Smoke for ue_get_task_context (R0-S).

Runs the UE Agent Kit MCP server against the fixed Reforge index and Revision
Export, calls ue_get_task_context for the three R0-S cases (S1 explicit target,
S2 query-only, S3 low budget), collects raw evidence searches, and writes the
full results as JSON artifacts plus a compact summary.

Usage:
    .venv\\Scripts\\python.exe scripts\\reforge_context_smoke.py ^
        --database .data\\reforge-context-smoke.sqlite3 ^
        --engine-root E:\\EPICGAME\\UE_5.6 ^
        --project E:\\WorkSpace\\Reforge\\Reforge.uproject ^
        --policy config\\projects\\reforge-read.json ^
        --revision-export Output\\ReforgeContextSmoke\\Export ^
        --memory-database .data\\reforge-context-smoke-memory.sqlite3 ^
        --output-dir Output\\ReforgeContextSmoke

All calls are read-only. No Reforge asset is modified.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parents[1]

from mcp import ClientSession  # noqa: E402
from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: E402


S1_QUERY = "vehicle customization module integration with BP_VehicleBase"
S1_ASSET = "/Game/ModularOffroadCars/BP/Components/BP_VehicleBase.BP_VehicleBase"

CASE_SPECS = [
    {
        "id": "S1",
        "name": "explicit-target-default-budget",
        "args": {
            "query": S1_QUERY,
            "asset_paths": [S1_ASSET],
            "include_memory": True,
            "include_live_context": True,
            "max_output_tokens": 4096,
        },
        "evidence": [
            {"label": "projectStatus", "tool": "ue_get_project_status", "args": {}},
            {"label": "searchAssetsVehicleBase", "tool": "ue_search", "args": {
                "query": "VehicleBase", "scope": "assets", "limit": 10}},
        ],
    },
    {
        "id": "S2",
        "name": "query-only-default-budget",
        "args": {
            "query": S1_QUERY,
            "asset_paths": [],
            "include_memory": True,
            "include_live_context": True,
            "max_output_tokens": 4096,
        },
        "evidence": [
            {"label": "searchAssetsFullQuery", "tool": "ue_search", "args": {
                "query": S1_QUERY, "scope": "assets", "limit": 10}},
            {"label": "searchSymbolsFullQuery", "tool": "ue_search", "args": {
                "query": S1_QUERY, "scope": "symbols", "limit": 10}},
            {"label": "searchAssetsVehicle", "tool": "ue_search", "args": {
                "query": "vehicle", "scope": "assets", "limit": 8}},
            {"label": "searchAssetsCustomization", "tool": "ue_search", "args": {
                "query": "customization", "scope": "assets", "limit": 8}},
            {"label": "searchAssetsModule", "tool": "ue_search", "args": {
                "query": "module", "scope": "assets", "limit": 8}},
            {"label": "searchAssetsBPVehicleBase", "tool": "ue_search", "args": {
                "query": "BP_VehicleBase", "scope": "assets", "limit": 8}},
            {"label": "searchSymbolsVehicleBase", "tool": "ue_search", "args": {
                "query": "VehicleBase", "scope": "symbols", "limit": 8}},
        ],
    },
    {
        "id": "S3",
        "name": "explicit-target-low-budget",
        "args": {
            "query": S1_QUERY,
            "asset_paths": [S1_ASSET],
            "include_memory": True,
            "include_live_context": True,
            "max_output_tokens": 1024,
        },
        "evidence": [],
    },
]


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    data = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    try:
        temporary.write_text(data, encoding="utf-8", newline="\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _summarize_task_context(payload: dict) -> dict:
    output_budget = payload.get("outputBudget", {}) if isinstance(payload, dict) else {}
    risks = payload.get("risks", []) if isinstance(payload, dict) else []
    risk_kinds = [str(risk.get("kind", "")) for risk in risks if isinstance(risk, dict)]
    targets = payload.get("targetAssets", []) if isinstance(payload, dict) else []
    memory = payload.get("memory", {}) if isinstance(payload, dict) else {}
    live = payload.get("liveEditor", {}) if isinstance(payload, dict) else {}
    revision = payload.get("revisionState", {}) if isinstance(payload, dict) else {}
    return {
        "ok": payload.get("ok"),
        "estimatedTokens": output_budget.get("estimatedTokens"),
        "maxTokens": output_budget.get("maxTokens"),
        "truncated": output_budget.get("truncated"),
        "truncationReason": output_budget.get("truncationReason"),
        "riskSummary": payload.get("riskSummary"),
        "riskKinds": risk_kinds,
        "targetAssetsFound": [str(item.get("assetPath")) for item in targets
                              if isinstance(item, dict) and item.get("found")],
        "relevantAssetCount": len(payload.get("relevantAssets") or []),
        "nextExpansionCount": len(payload.get("nextExpansions") or []),
        "memoryIncluded": memory.get("included"),
        "memoryReason": memory.get("reason", ""),
        "liveIncluded": live.get("included"),
        "liveReason": live.get("reason", ""),
        "revisionOverall": revision.get("overall"),
        "revisionReason": revision.get("reason", ""),
        "degradedSources": [str(item.get("section")) for item in (payload.get("degradedSources") or [])],
    }


async def _run_smoke(args: argparse.Namespace) -> dict:
    server_args = [
        str(TOOL_ROOT / "scripts" / "ue-agent-mcp.py"),
        "--database", str(args.database),
        "--enable-write-tools",
        "--engine-root", str(args.engine_root),
        "--project", str(args.project),
        "--policy", str(args.policy),
        "--revision-export", str(args.revision_export),
        "--work-root", str(args.work_root),
        "--backup-root", str(args.backup_root),
        "--enable-live-editor",
        "--live-editor-timeout-seconds", "2.0",
    ]
    if args.memory_database is not None:
        server_args += [
            "--enable-project-memory",
            "--memory-database", str(args.memory_database),
        ]
    parameters = StdioServerParameters(
        command=sys.executable,
        args=server_args,
        cwd=TOOL_ROOT,
        encoding="utf-8",
        encoding_error_handler="replace",
    )
    results: dict = {"cases": {}}
    with (args.output_dir / "server-stderr.log").open("w", encoding="utf-8", newline="\n") as error_log:
        async with stdio_client(parameters, errlog=error_log) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                initialize_result = await session.initialize()
                tools_result = await session.list_tools()
                capabilities_result = await session.call_tool("ue_get_capabilities", {})
                results["protocolVersion"] = initialize_result.protocolVersion
                results["serverName"] = initialize_result.serverInfo.name
                results["capabilities"] = capabilities_result.structuredContent
                results["toolNames"] = [tool.name for tool in tools_result.tools]
                for spec in CASE_SPECS:
                    case_payload = await session.call_tool("ue_get_task_context", spec["args"])
                    case_results = {"taskContext": case_payload.structuredContent, "evidence": {}}
                    for evidence in spec["evidence"]:
                        evidence_payload = await session.call_tool(evidence["tool"], evidence["args"])
                        case_results["evidence"][evidence["label"]] = evidence_payload.structuredContent
                    results["cases"][spec["id"]] = case_results
    return results


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--engine-root", type=Path, required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--revision-export", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, default=TOOL_ROOT / "Output" / "ReforgeContextSmoke" / "Work")
    parser.add_argument("--backup-root", type=Path, default=TOOL_ROOT / "Backups" / "ReforgeContextSmoke")
    parser.add_argument("--memory-database", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=TOOL_ROOT / "Output" / "ReforgeContextSmoke")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    results = asyncio.run(_run_smoke(args))
    for case_id, case_results in results["cases"].items():
        _write_json(
            args.output_dir / f"{case_id}-task-context.json",
            case_results["taskContext"],
        )
        if case_results["evidence"]:
            _write_json(
                args.output_dir / f"{case_id}-evidence.json",
                case_results["evidence"],
            )
    _write_json(args.output_dir / "smoke-results.json", results)
    summary = {
        "protocolVersion": results.get("protocolVersion"),
        "serverName": results.get("serverName"),
        "toolCount": len(results.get("toolNames", [])),
        "cases": {
            case_id: _summarize_task_context(case_results["taskContext"])
            for case_id, case_results in results["cases"].items()
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
