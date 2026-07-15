from __future__ import annotations

import json
import os
from pathlib import Path

import unreal


TOOL_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = Path(
    os.environ.get("UEAK_BLUEPRINT_REGISTRY_OUTPUT", TOOL_ROOT / "Output" / "BlueprintRegistryTags.json")
).resolve()
TAGS = ("BlueprintType", "ParentClass", "NativeParentClass", "GeneratedClass")

registry = unreal.AssetRegistryHelpers.get_asset_registry()
assets = registry.get_assets_by_path("/Game", recursive=True)
rows: list[dict[str, str]] = []

for asset_data in assets:
    try:
        class_name = str(asset_data.asset_class_path.asset_name)
    except Exception:
        class_name = str(asset_data.asset_class_path)

    if "Blueprint" not in class_name:
        continue

    row = {
        "asset_name": str(asset_data.asset_name),
        "package_name": str(asset_data.package_name),
        "asset_class": class_name,
    }

    for tag_name in TAGS:
        try:
            value = asset_data.get_tag_value(tag_name)
            row[tag_name] = "" if value is None else str(value)
        except Exception as exc:
            row[tag_name] = ""
            row[f"{tag_name}_error"] = str(exc)

    rows.append(row)

rows.sort(key=lambda item: item["package_name"])
OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
OUTPUT_PATH.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8", newline="\r\n")

unreal.log(f"Blueprint registry tag inventory written: {OUTPUT_PATH}, count={len(rows)}")
