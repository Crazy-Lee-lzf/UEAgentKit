from __future__ import annotations

import json
import os
from pathlib import Path

import unreal


TOOL_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = Path(
    os.environ.get("BCT_BLUEPRINT_TYPE_OUTPUT", TOOL_ROOT / "Output" / "BlueprintTypeInventory.json")
).resolve()

registry = unreal.AssetRegistryHelpers.get_asset_registry()
assets = registry.get_assets_by_path("/Game", recursive=True)
rows: list[dict[str, str]] = []

for asset_data in assets:
    class_path = ""
    try:
        class_path = str(asset_data.asset_class_path)
    except Exception:
        try:
            class_path = str(asset_data.asset_class)
        except Exception:
            class_path = ""

    if "Blueprint" not in class_path:
        continue

    package_name = str(asset_data.package_name)
    asset_name = str(asset_data.asset_name)
    row = {
        "asset_name": asset_name,
        "package_name": package_name,
        "object_path": f"{package_name}.{asset_name}",
        "asset_class": class_path,
    }

    try:
        asset = asset_data.get_asset()
        row["loaded_class"] = asset.get_class().get_path_name() if asset else ""
        row["blueprint_type"] = str(asset.get_editor_property("blueprint_type")) if asset else ""
        parent_class = asset.get_editor_property("parent_class") if asset else None
        row["parent_class"] = parent_class.get_path_name() if parent_class else ""
    except Exception as exc:
        row["load_error"] = str(exc)

    rows.append(row)

rows.sort(key=lambda item: (item.get("asset_class", ""), item.get("package_name", "")))
OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
OUTPUT_PATH.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8", newline="\r\n")

unreal.log(f"Blueprint type inventory written: {OUTPUT_PATH}, count={len(rows)}")
