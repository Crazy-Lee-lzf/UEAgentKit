"""Real Reforge read-only Impact Smoke for ue_analyze_change_impact (R1).

Runs the UE Agent Kit MCP server in read-only mode against the fixed Reforge
immutable index (.data/reforge-context-smoke.sqlite3) and calls
ue_analyze_change_impact for the four R1 cases:

    S1  Fan-out direct case on BP_VehicleBase (depth 1)
    S2  Indirect case on BP_SphereTraceWheel_V2 (depth 2, real two-hop chains)
    S3  Multi-target case (BP_VehicleBase + BP_CarChangeSender_Base)
    S4  No-consumer boundary case on BP_GM_main (zero incoming references)

Every call is read-only. No Reforge asset is modified and no Unreal Editor
process is started; the immutable index built during R0-S is reused as-is.

Usage:
    .venv\\Scripts\\python.exe scripts\\reforge_impact_smoke.py ^
        --database .data\\reforge-context-smoke.sqlite3 ^
        --output-dir Output\\ReforgeContextSmoke\\R1-impact
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parents[1]

from mcp import ClientSession  # noqa: E402
from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: E402


BP_VEHICLE_BASE = "/Game/ModularOffroadCars/BP/Components/BP_VehicleBase.BP_VehicleBase"
BP_CAR_CHANGE_SENDER_BASE = (
    "/Game/ModularOffroadCars/BP/Components/VehicleCustomization/BP_CarChangeSender_Base.BP_CarChangeSender_Base"
)
BP_SPHERE_TRACE_WHEEL = (
    "/Game/ModularOffroadCars/BP/Components/BP_SphereTraceWheel_V2.BP_SphereTraceWheel_V2"
)
BP_GM_MAIN = "/Game/ModularOffroadCars/BP/Components/BP_GM_main.BP_GM_main"

CASE_SPECS = [
    {
        "id": "S1",
        "name": "fan-out-direct",
        "args": {
            "target_asset_paths": [BP_VEHICLE_BASE],
            "max_depth": 1,
            "max_output_tokens": 8192,
        },
    },
    {
        "id": "S2",
        "name": "indirect-two-hop",
        "args": {
            "target_asset_paths": [BP_SPHERE_TRACE_WHEEL],
            "max_depth": 2,
            "max_output_tokens": 32768,
        },
    },
    {
        "id": "S3",
        "name": "multi-target-shared-consumers",
        "args": {
            "target_asset_paths": [BP_VEHICLE_BASE, BP_CAR_CHANGE_SENDER_BASE],
            "max_depth": 2,
            "max_output_tokens": 32768,
        },
    },
    {
        "id": "S4",
        "name": "no-consumer-boundary",
        "args": {
            "target_asset_paths": [BP_GM_MAIN],
            "max_depth": 2,
            "max_output_tokens": 4096,
        },
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


def _summarize_impact(payload: dict) -> dict:
    summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
    direct = payload.get("directConsumers", []) if isinstance(payload, dict) else []
    indirect = payload.get("indirectConsumers", []) if isinstance(payload, dict) else []
    risks = payload.get("risks", []) if isinstance(payload, dict) else []
    validation = payload.get("validationTargets", []) if isinstance(payload, dict) else []
    output_budget = payload.get("outputBudget", {}) if isinstance(payload, dict) else {}
    return {
        "ok": payload.get("ok"),
        "tool": payload.get("tool"),
        "directConsumerCount": summary.get("directConsumerCount"),
        "indirectConsumerCount": summary.get("indirectConsumerCount"),
        "visitedAssetCount": summary.get("visitedAssetCount"),
        "visitedEdgeCount": summary.get("visitedEdgeCount"),
        "maxDepthRequested": summary.get("maxDepthRequested"),
        "maxDepthReached": summary.get("maxDepthReached"),
        "pathCount": summary.get("pathCount"),
        "truncated": summary.get("truncated"),
        "truncationReasons": summary.get("truncationReasons"),
        "frontierOmittedCount": summary.get("frontierOmittedCount"),
        "unknownReferenceKindCount": summary.get("unknownReferenceKindCount"),
        "directConsumerSample": [item.get("assetPath") for item in direct[:5]],
        "indirectConsumerSample": [item.get("assetPath") for item in indirect[:5]],
        "samplePath": (
            indirect[0].get("paths")[0] if indirect and indirect[0].get("paths") else None
        ),
        "riskKinds": [str(risk.get("kind", "")) for risk in risks],
        "validationTierCounts": {
            tier: sum(1 for item in validation if item.get("tier") == tier) for tier in (0, 1, 2)
        },
        "estimatedTokens": output_budget.get("estimatedTokens"),
        "maxTokens": output_budget.get("maxTokens"),
        "outputTruncated": output_budget.get("truncated"),
        "runtimeSensitivityState": (payload.get("runtimeSensitiveConsumers") or {}).get(
            "classificationState"
        ),
    }


async def _run_smoke(args: argparse.Namespace) -> dict:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            str(TOOL_ROOT / "scripts" / "ue-agent-mcp.py"),
            "--database",
            str(args.database),
        ],
        cwd=TOOL_ROOT,
        encoding="utf-8",
        encoding_error_handler="replace",
    )
    results: dict = {"cases": {}}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with (args.output_dir / "server-stderr.log").open("w", encoding="utf-8", newline="\n") as error_log:
        async with stdio_client(parameters, errlog=error_log) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                initialize_result = await session.initialize()
                tools_result = await session.list_tools()
                capabilities_result = await session.call_tool("ue_get_capabilities", {})
                project_status_result = await session.call_tool("ue_get_project_status", {})
                results["protocolVersion"] = initialize_result.protocolVersion
                results["serverName"] = initialize_result.serverInfo.name
                results["capabilities"] = capabilities_result.structuredContent
                results["projectStatus"] = project_status_result.structuredContent
                results["toolNames"] = [tool.name for tool in tools_result.tools]
                for spec in CASE_SPECS:
                    started = time.perf_counter()
                    case_payload = await session.call_tool("ue_analyze_change_impact", spec["args"])
                    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 2)
                    case_results = {
                        "impact": case_payload.structuredContent,
                        "elapsedMs": elapsed_ms,
                        "evidence": {},
                    }
                    if spec["id"] == "S1":
                        cross_check = await session.call_tool(
                            "ue_find_references",
                            {"asset_path": BP_VEHICLE_BASE, "direction": "incoming", "depth": 1, "limit": 100},
                        )
                        case_results["evidence"]["findReferencesIncoming"] = cross_check.structuredContent
                    results["cases"][spec["id"]] = case_results
    return results


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=TOOL_ROOT / "Output" / "ReforgeContextSmoke" / "R1-impact",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    results = asyncio.run(_run_smoke(args))
    for case_id, case_results in results["cases"].items():
        _write_json(args.output_dir / f"{case_id}-impact.json", case_results["impact"])
        if case_results["evidence"]:
            _write_json(args.output_dir / f"{case_id}-evidence.json", case_results["evidence"])
    _write_json(args.output_dir / "smoke-results.json", results)
    summary = {
        "protocolVersion": results.get("protocolVersion"),
        "serverName": results.get("serverName"),
        "toolCount": len(results.get("toolNames", [])),
        "hasImpactTool": "ue_analyze_change_impact" in results.get("toolNames", []),
        "impactCapability": (results.get("capabilities") or {}).get("impactAnalysis"),
        "cases": {
            case_id: {
                **{key: value for key, value in case_results.items() if key != "impact"},
                "summary": _summarize_impact(case_results["impact"]),
            }
            for case_id, case_results in results["cases"].items()
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    _write_json(args.output_dir / "smoke-summary.json", summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
