from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


TOOL_ROOT = Path(__file__).resolve().parents[2]


def payload(result: Any, tool: str) -> dict[str, Any]:
    value = result.structuredContent
    if not isinstance(value, dict):
        raise RuntimeError(f"{tool} returned no structured object: {result}")
    return value


def output_path_for(source_path: str, output_directory: str, suffix: str) -> str:
    asset_name = source_path.rsplit("/", 1)[-1].split(".", 1)[0]
    output_name = f"{asset_name}{suffix}"
    return f"{output_directory.rstrip('/')}/{output_name}.{output_name}"


def first_track(asset: dict[str, Any], bone: str) -> dict[str, Any] | None:
    for track in asset.get("tracks", []):
        if str(track.get("bone", "")).casefold() == bone.casefold():
            return track
    return None


async def run(args: argparse.Namespace) -> dict[str, Any]:
    source_paths = json.loads(args.source_json.read_text(encoding="utf-8-sig"))
    if not isinstance(source_paths, list) or not all(isinstance(value, str) for value in source_paths):
        raise ValueError("source-json must contain an array of Object Paths")
    output_paths = [
        output_path_for(source_path, args.output_directory, args.suffix)
        for source_path in source_paths
    ]
    parameters = StdioServerParameters(
        command="powershell.exe",
        args=[
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(TOOL_ROOT / "scripts" / "RunMcp.ps1"),
            "-Database",
            str(args.database),
            "-EnableLiveEditor",
            "-ProjectPath",
            str(args.project),
            "-LiveEditorTimeoutSeconds",
            "60",
            "-EnableWriteTools",
            "-EngineRoot",
            str(args.engine_root),
            "-Policy",
            str(args.policy),
            "-RevisionExport",
            str(args.revision_export),
            "-WorkRoot",
            str(args.work_root),
            "-BackupRoot",
            str(args.backup_root),
        ],
        cwd=TOOL_ROOT,
        encoding="utf-8",
        encoding_error_handler="replace",
    )
    assets: list[dict[str, Any]] = []
    args.error_log.parent.mkdir(parents=True, exist_ok=True)
    with args.error_log.open("w", encoding="utf-8", newline="\n") as stderr:
        async with stdio_client(parameters, errlog=stderr) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                for offset in range(0, len(output_paths), 32):
                    batch = output_paths[offset : offset + 32]
                    response = payload(
                        await session.call_tool(
                            "ue_diagnose_animation_scale",
                            {
                                "animationPaths": batch,
                                "boneNames": ["Root", "Bip001Pelvis"],
                                "loadIfNeeded": True,
                            },
                        ),
                        "ue_diagnose_animation_scale",
                    )
                    assets.extend(response.get("result", {}).get("assets", []))

    status_counts = Counter(str(asset.get("status", "unknown")) for asset in assets)
    root_motion_counts = Counter()
    additive_counts = Counter()
    root_scale_counts = Counter()
    exceptional_assets: list[dict[str, Any]] = []
    for asset in assets:
        if asset.get("status") != "success":
            continue
        root_motion_counts[(bool(asset.get("enableRootMotion")), bool(asset.get("forceRootLock")))] += 1
        additive_counts[int(asset.get("additiveAnimType", -1))] += 1
        root_track = first_track(asset, "Root")
        first_scale = root_track.get("firstScale") if root_track else None
        if isinstance(first_scale, dict):
            rounded = tuple(round(float(first_scale.get(axis, 0.0)), 6) for axis in ("x", "y", "z"))
            root_scale_counts[rounded] += 1
            if rounded not in {(1.0, 1.0, 1.0), (100.0, 100.0, 100.0), (0.01, 0.01, 0.01)}:
                exceptional_assets.append(asset)
        else:
            root_scale_counts[("missing",)] += 1

    return {
        "sourceCount": len(source_paths),
        "outputCount": len(output_paths),
        "statusCounts": {key: value for key, value in sorted(status_counts.items())},
        "rootMotionCounts": {
            f"enable={enabled},forceLock={locked}": count
            for (enabled, locked), count in sorted(root_motion_counts.items())
        },
        "additiveCounts": {str(key): value for key, value in sorted(additive_counts.items())},
        "rootScaleCounts": {str(key): value for key, value in sorted(root_scale_counts.items(), key=lambda item: str(item[0]))},
        "exceptionalAssets": exceptional_assets,
        "assets": assets,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan retarget output animation scale metadata.")
    parser.add_argument("--engine-root", required=True, type=Path)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--revision-export", required=True, type=Path)
    parser.add_argument("--work-root", required=True, type=Path)
    parser.add_argument("--backup-root", required=True, type=Path)
    parser.add_argument("--source-json", required=True, type=Path)
    parser.add_argument("--output-directory", required=True)
    parser.add_argument("--suffix", default="_XinYueHu")
    parser.add_argument("--error-log", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = asyncio.run(run(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\r\n",
    )
    print(json.dumps({key: value for key, value in report.items() if key != "assets"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
