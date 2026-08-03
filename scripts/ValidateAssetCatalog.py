from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
from pathlib import Path
from typing import Any


READER_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "static-mesh-v1": ("type", "readerVersion", "lodCount", "lods", "materialSlotCount", "materials", "bounds", "lightmap", "nanite", "collision", "socketCount", "sockets"),
    "skeletal-mesh-v1": ("type", "readerVersion", "skeletonPath", "physicsAssetPath", "lodCount", "bounds", "boneCount", "rawBoneCount", "rootBoneName", "materialSlotCount", "materials", "morphTargetCount", "morphTargets", "meshSocketCount", "meshSockets", "activeSocketCount", "activeSockets"),
    "skeleton-v1": ("type", "readerVersion", "boneCount", "rawBoneCount", "rootBoneName", "bones", "virtualBoneCount", "virtualBones", "socketCount", "sockets", "previewMeshPath", "compatibleSkeletonCount", "compatibleSkeletons", "curveMetadataCount", "curveMetadataNames"),
    "physics-asset-v1": ("type", "readerVersion", "previewSkeletalMeshPath", "bodyCount", "constraintCount", "disabledCollisionPairCount", "boundsBodyCount", "boundsBodies", "physicalAnimationProfileCount", "physicalAnimationProfiles", "constraintProfileCount", "constraintProfiles", "totalShapeCount", "bodies", "constraints"),
    "material-v1": ("type", "readerVersion", "domain", "blendMode", "twoSided", "shadingModels", "opacityMaskClipValue", "expressionCount", "expressionClasses"),
    "material-instance-v1": ("type", "readerVersion", "parentPath", "blendMode", "twoSided", "shadingModels", "scalarParameterCount", "scalarParameters", "vectorParameterCount", "vectorParameters", "textureParameterCount", "textureParameters", "staticSwitchParameterCount", "staticSwitchParameters"),
    "material-function-v1": ("type", "readerVersion", "description", "caption", "exposeToLibrary", "inputCount", "inputs", "outputCount", "outputs", "expressionCount", "expressionClasses", "commentCount"),
    "texture-2d-v1": ("type", "readerVersion", "source", "platform", "sizeX", "sizeY", "mipCount", "compressionSettings", "srgb", "lodGroup", "mipGenSettings", "filter", "addressX", "addressY", "neverStream", "virtualTextureStreaming"),
    "anim-sequence-v1": ("type", "readerVersion", "skeletonPath", "playLength", "rateScale", "sampledKeyCount", "samplingFrameRate", "retargetSource", "additiveType", "basePoseType", "rootMotion", "notifyCount", "notifies", "notifyReadError", "curveCount", "curves", "syncMarkerCount", "syncMarkers", "uniqueMarkerNames"),
    "anim-montage-v1": ("type", "readerVersion", "skeletonPath", "playLength", "rateScale", "samplingFrameRate", "hasRootMotion", "autoBlendOut", "sectionCount", "sections", "slotCount", "slots", "notifyCount", "notifies", "notifyReadError", "branchingPointMarkerCount"),
    "blend-space-v1": ("type", "readerVersion", "blendSpaceType", "skeletonPath", "notifyTriggerMode", "axes", "sampleCount", "samples"),
    "data-table-v1": ("type", "readerVersion", "rowStructPath", "rowCount", "rowNames", "rows"),
    "data-asset-v1": ("type", "readerVersion", "classPath", "hasPrimaryAssetId", "primaryAssetType", "primaryAssetName", "primaryAssetId", "propertyCount", "skippedPropertyCount", "conversionFailureCount", "properties"),
    "niagara-system-v1": ("type", "readerVersion", "effectTypePath", "deterministic", "randomSeed", "warmup", "fixedTick", "fixedBounds", "systemSpawnScript", "systemUpdateScript", "exposedParameterCount", "exposedParameters", "emitterCount", "emitters"),
    "world-v1": ("type", "readerVersion", "worldType", "persistentLevelPath", "persistentLevelPackage", "usingExternalActors", "loadedActorCount", "exportedActorCount", "actorListTruncated", "componentCount", "actorClassCounts", "componentClassCounts", "actors", "streamingLevelCount", "streamingLevels", "worldSettings", "worldPartition"),
}

READER_COUNT_ARRAY_PAIRS: dict[str, tuple[tuple[str, str], ...]] = {
    "static-mesh-v1": (("lodCount", "lods"), ("materialSlotCount", "materials"), ("socketCount", "sockets")),
    "skeletal-mesh-v1": (("materialSlotCount", "materials"), ("morphTargetCount", "morphTargets"), ("meshSocketCount", "meshSockets"), ("activeSocketCount", "activeSockets")),
    "skeleton-v1": (("boneCount", "bones"), ("virtualBoneCount", "virtualBones"), ("socketCount", "sockets"), ("compatibleSkeletonCount", "compatibleSkeletons"), ("curveMetadataCount", "curveMetadataNames")),
    "physics-asset-v1": (("bodyCount", "bodies"), ("constraintCount", "constraints"), ("boundsBodyCount", "boundsBodies")),
    "material-instance-v1": (("scalarParameterCount", "scalarParameters"), ("vectorParameterCount", "vectorParameters"), ("doubleVectorParameterCount", "doubleVectorParameters"), ("textureParameterCount", "textureParameters"), ("fontParameterCount", "fontParameters"), ("staticSwitchParameterCount", "staticSwitchParameters")),
    "material-function-v1": (("inputCount", "inputs"), ("outputCount", "outputs")),
    "anim-sequence-v1": (("notifyCount", "notifies"), ("curveCount", "curves"), ("syncMarkerCount", "syncMarkers")),
    "anim-montage-v1": (("sectionCount", "sections"), ("slotCount", "slots"), ("notifyCount", "notifies")),
    "blend-space-v1": (("sampleCount", "samples"),),
    "data-table-v1": (("rowCount", "rows"), ("rowCount", "rowNames")),
    "data-asset-v1": (("propertyCount", "properties"),),
    "niagara-system-v1": (("exposedParameterCount", "exposedParameters"), ("emitterCount", "emitters")),
    "world-v1": (("exportedActorCount", "actors"), ("streamingLevelCount", "streamingLevels")),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate UE Agent Kit generic asset catalog output.")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--expect-schema", default="1.1")
    parser.add_argument("--expect-exporter", default="")
    parser.add_argument("--expect-project", default="")
    parser.add_argument("--asset-file", type=Path)
    parser.add_argument("--allow-failures", action="store_true")
    return parser.parse_args()


def windows_extended_path(value: str) -> str:
    separator = chr(92)
    extended_prefix = separator * 2 + "?" + separator
    unc_prefix = separator * 2
    if value.startswith(extended_prefix):
        return value
    if value.startswith(unc_prefix):
        return extended_prefix + "UNC" + separator + value[2:]
    return extended_prefix + value


def filesystem_path(path: Path) -> str:
    value = str(path.resolve())
    return windows_extended_path(value) if os.name == "nt" else value


def load_json(path: Path) -> Any:
    with open(filesystem_path(path), encoding="utf-8-sig") as stream:
        return json.load(stream)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(filesystem_path(path), "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_asset(
    path: Path,
    *,
    expected_schema: str,
    expected_exporter: str,
    expected_project: str,
) -> tuple[dict[str, Any], list[str]]:
    data = load_json(path)
    errors: list[str] = []
    asset_path = str(data.get("assetPath", ""))
    revision = data.get("revision", {})
    symbols = data.get("symbols", [])
    references = data.get("references", [])
    summary = data.get("summary", {})
    registry = data.get("assetRegistry", {})
    reader_name = str(data.get("assetReader", ""))
    reader_status = str(data.get("assetReaderStatus", ""))
    reader_error = str(data.get("assetReaderError", ""))
    asset_details = data.get("assetDetails", {})

    if not asset_path:
        errors.append("assetPath is empty")
    if data.get("profile") != "asset-index":
        errors.append(f"profile={data.get('profile')!r}, expected 'asset-index'")
    if expected_schema and str(data.get("schemaVersion", "")) != expected_schema:
        errors.append(f"schemaVersion={data.get('schemaVersion')!r}, expected {expected_schema!r}")
    if expected_exporter and str(data.get("exporterVersion", "")) != expected_exporter:
        errors.append(f"exporterVersion={data.get('exporterVersion')!r}, expected {expected_exporter!r}")
    if expected_project and str(data.get("projectName", "")) != expected_project:
        errors.append(f"projectName={data.get('projectName')!r}, expected {expected_project!r}")
    if not str(data.get("assetClass", "")):
        errors.append("assetClass is empty")
    if not str(data.get("packageName", "")):
        errors.append("packageName is empty")
    if not isinstance(registry, dict) or not str(registry.get("assetClassPath", "")):
        errors.append("assetRegistry.assetClassPath is empty")
    if reader_status not in {"not-handled", "disabled", "success", "failed"}:
        errors.append(f"invalid assetReaderStatus: {reader_status!r}")
    if not reader_name:
        errors.append("assetReader is empty")
    if reader_status == "success" and (not isinstance(asset_details, dict) or not asset_details):
        errors.append("successful specialized reader has empty assetDetails")
    if reader_status == "success" and reader_name in READER_REQUIRED_FIELDS:
        missing_fields = [
            field for field in READER_REQUIRED_FIELDS[reader_name] if field not in asset_details
        ]
        if missing_fields:
            errors.append(
                f"{reader_name} assetDetails missing fields: {', '.join(missing_fields)}"
            )
        for count_field, array_field in READER_COUNT_ARRAY_PAIRS.get(reader_name, ()):
            array_value = asset_details.get(array_field)
            if not isinstance(array_value, list):
                errors.append(f"{reader_name}.{array_field} is not an array")
            elif asset_details.get(count_field) != len(array_value):
                errors.append(
                    f"{reader_name}.{count_field} does not match {array_field} length"
                )
        if reader_name in {"anim-sequence-v1", "anim-montage-v1"} and asset_details.get("notifyReadError"):
            errors.append(f"{reader_name} notifyReadError is not empty")
        if reader_name == "texture-2d-v1":
            source = asset_details.get("source", {})
            if source.get("available") and (asset_details.get("sizeX", 0) <= 0 or asset_details.get("sizeY", 0) <= 0):
                errors.append("texture-2d-v1 source is available but dimensions are invalid")
        if reader_name == "data-asset-v1":
            failed_properties = [
                item.get("name", "")
                for item in asset_details.get("properties", [])
                if not item.get("conversionSucceeded")
            ]
            if asset_details.get("conversionFailureCount") != len(failed_properties):
                errors.append("data-asset-v1 conversionFailureCount is inconsistent")
            has_primary_asset_id = bool(asset_details.get("hasPrimaryAssetId"))
            primary_asset_fields = (
                asset_details.get("primaryAssetType", ""),
                asset_details.get("primaryAssetName", ""),
                asset_details.get("primaryAssetId", ""),
            )
            if has_primary_asset_id and not all(primary_asset_fields):
                errors.append("data-asset-v1 valid PrimaryAssetId fields are incomplete")
            if not has_primary_asset_id and any(primary_asset_fields):
                errors.append("data-asset-v1 invalid PrimaryAssetId fields must be empty")
            if failed_properties:
                errors.append(
                    "data-asset-v1 property conversion failures: " + ", ".join(failed_properties)
                )
        if reader_name == "niagara-system-v1":
            for emitter in asset_details.get("emitters", []):
                if emitter.get("rendererCount") != len(emitter.get("renderers", [])):
                    errors.append("niagara-system-v1 rendererCount is inconsistent")
                if emitter.get("scriptCount") != len(emitter.get("scripts", [])):
                    errors.append("niagara-system-v1 scriptCount is inconsistent")
                if emitter.get("statelessModuleCount") != len(emitter.get("statelessModules", [])):
                    errors.append("niagara-system-v1 statelessModuleCount is inconsistent")
                is_stateless = emitter.get("mode") == "Stateless"
                has_stateless_emitter = bool(emitter.get("statelessEmitterAvailable"))
                if is_stateless and not has_stateless_emitter:
                    errors.append("niagara-system-v1 stateless emitter data is unavailable")
                if not is_stateless and has_stateless_emitter:
                    errors.append("niagara-system-v1 standard emitter exposes stateless data")
                if not has_stateless_emitter and (
                    emitter.get("statelessSpawnInfoCount", 0) != 0
                    or emitter.get("statelessModuleCount", 0) != 0
                ):
                    errors.append("niagara-system-v1 unavailable stateless emitter has nonzero counts")
        if reader_name == "world-v1":
            loaded_actor_count = int(asset_details.get("loadedActorCount", 0))
            exported_actor_count = int(asset_details.get("exportedActorCount", 0))
            if exported_actor_count > loaded_actor_count:
                errors.append("world-v1 exportedActorCount exceeds loadedActorCount")
            if bool(asset_details.get("actorListTruncated")) != (exported_actor_count < loaded_actor_count):
                errors.append("world-v1 actorListTruncated is inconsistent")
            if sum(int(item.get("count", 0)) for item in asset_details.get("actorClassCounts", [])) != loaded_actor_count:
                errors.append("world-v1 actorClassCounts are inconsistent")
            if sum(int(item.get("count", 0)) for item in asset_details.get("componentClassCounts", [])) != int(asset_details.get("componentCount", 0)):
                errors.append("world-v1 componentClassCounts are inconsistent")
            for actor in asset_details.get("actors", []):
                if sum(int(item.get("count", 0)) for item in actor.get("componentClasses", [])) != int(actor.get("componentCount", 0)):
                    errors.append("world-v1 actor componentClasses are inconsistent")
            world_partition = asset_details.get("worldPartition", {})
            actor_desc_count = int(world_partition.get("actorDescCount", 0))
            exported_actor_desc_count = int(world_partition.get("exportedActorDescCount", 0))
            if exported_actor_desc_count != len(world_partition.get("actorDescs", [])):
                errors.append("world-v1 exportedActorDescCount is inconsistent")
            if exported_actor_desc_count > actor_desc_count:
                errors.append("world-v1 exportedActorDescCount exceeds actorDescCount")
            if bool(world_partition.get("actorDescListTruncated")) != (exported_actor_desc_count < actor_desc_count):
                errors.append("world-v1 actorDescListTruncated is inconsistent")
            if sum(int(item.get("count", 0)) for item in world_partition.get("actorDescClassCounts", [])) != actor_desc_count:
                errors.append("world-v1 actorDescClassCounts are inconsistent")
            actor_desc_metadata_available = bool(world_partition.get("actorDescMetadataAvailable"))
            if not world_partition.get("available") and actor_desc_count != 0:
                errors.append("world-v1 unavailable world partition has actor descriptions")
            if not actor_desc_metadata_available and actor_desc_count != 0:
                errors.append("world-v1 unavailable actor descriptor metadata has actor descriptions")
    if reader_status == "failed" and not reader_error:
        errors.append("failed specialized reader has empty assetReaderError")
    if len(symbols) != 1 or symbols[0].get("kind") != "asset":
        errors.append("generic asset must contain exactly one asset symbol")
    elif symbols[0].get("assetReader") != reader_name:
        errors.append("asset symbol assetReader does not match root assetReader")
    elif symbols[0].get("assetReaderStatus") != reader_status:
        errors.append("asset symbol assetReaderStatus does not match root assetReaderStatus")
    elif symbols[0].get("assetDetails", {}) != asset_details:
        errors.append("asset symbol assetDetails does not match root assetDetails")
    if summary.get("symbols") != len(symbols):
        errors.append("summary.symbols does not match symbols array")
    if summary.get("references") != len(references):
        errors.append("summary.references does not match references array")

    symbol_ids = {str(item.get("id", "")) for item in symbols}
    if "" in symbol_ids:
        errors.append("one or more symbols have an empty id")
    reference_ids = [str(item.get("id", "")) for item in references]
    if any(not value for value in reference_ids):
        errors.append("one or more references have an empty id")
    if len(reference_ids) != len(set(reference_ids)):
        errors.append("duplicate reference ids")
    for reference in references:
        if str(reference.get("sourceSymbolId", "")) not in symbol_ids:
            errors.append(f"reference has unknown sourceSymbolId: {reference.get('id', '')}")
        if not str(reference.get("targetSymbolId", "")):
            errors.append(f"reference has empty targetSymbolId: {reference.get('id', '')}")

    revision_value = str(revision.get("value", ""))
    content_sha256 = str(revision.get("contentSha256", ""))
    if revision.get("available"):
        if len(content_sha256) != 64:
            errors.append("available revision does not contain a 64-character SHA-256")
        if revision_value != f"sha256:{content_sha256}":
            errors.append("revision value does not match contentSha256")

    return (
        {
            "file": str(path),
            "assetPath": asset_path,
            "assetClass": data.get("assetClass", ""),
            "tags": len(registry.get("tags", {})) if isinstance(registry, dict) else 0,
            "references": len(references),
            "revisionAvailable": bool(revision.get("available")),
            "readerStatus": reader_status,
        },
        errors,
    )


def main() -> int:
    args = parse_args()
    output_root = args.output.expanduser().resolve()
    manifest_path = output_root / "manifest.json"
    canonical_root = output_root / "canonical"
    errors: list[str] = []

    if not manifest_path.is_file():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")
    if not canonical_root.is_dir():
        raise FileNotFoundError(f"Canonical directory not found: {canonical_root}")

    manifest = load_json(manifest_path)
    success_count = int(manifest.get("successCount", 0))
    failure_count = int(manifest.get("failureCount", 0))
    manifest_reader_success = int(manifest.get("readerSuccessCount", 0))
    manifest_reader_failure = int(manifest.get("readerFailureCount", 0))
    if manifest.get("profile") != "asset-index":
        errors.append(f"manifest profile={manifest.get('profile')!r}, expected 'asset-index'")
    if args.expect_schema and str(manifest.get("schemaVersion", "")) != args.expect_schema:
        errors.append("manifest schemaVersion does not match")
    if args.expect_exporter and str(manifest.get("exporterVersion", "")) != args.expect_exporter:
        errors.append("manifest exporterVersion does not match")
    if args.expect_project and str(manifest.get("projectName", "")) != args.expect_project:
        errors.append("manifest projectName does not match")
    if failure_count and not args.allow_failures:
        errors.append(f"manifest contains {failure_count} failure(s)")

    files = sorted(canonical_root.rglob("*.json"))
    if len(files) != success_count:
        errors.append(f"canonical file count {len(files)} does not match successCount {success_count}")

    assets: list[dict[str, Any]] = []
    class_counts: collections.Counter[str] = collections.Counter()
    seen_paths: set[str] = set()
    for path in files:
        result, asset_errors = validate_asset(
            path,
            expected_schema=args.expect_schema,
            expected_exporter=args.expect_exporter,
            expected_project=args.expect_project,
        )
        asset_path = str(result["assetPath"])
        if asset_path in seen_paths:
            asset_errors.append(f"duplicate assetPath: {asset_path}")
        seen_paths.add(asset_path)
        class_counts[str(result["assetClass"])] += 1
        errors.extend(f"{path.relative_to(output_root)}: {message}" for message in asset_errors)
        assets.append(result)

    reader_success_count = sum(item["readerStatus"] == "success" for item in assets)
    reader_failure_count = sum(item["readerStatus"] == "failed" for item in assets)
    if reader_success_count != manifest_reader_success:
        errors.append(
            f"reader success count {reader_success_count} does not match manifest {manifest_reader_success}"
        )
    if reader_failure_count != manifest_reader_failure:
        errors.append(
            f"reader failure count {reader_failure_count} does not match manifest {manifest_reader_failure}"
        )
    if reader_failure_count and not args.allow_failures:
        errors.append(f"manifest contains {reader_failure_count} specialized reader failure(s)")

    asset_file_result: dict[str, Any] = {}
    if args.asset_file:
        asset_file = args.asset_file.expanduser().resolve()
        if len(assets) != 1:
            errors.append("--asset-file requires exactly one exported asset")
        elif not asset_file.is_file():
            errors.append(f"asset file not found: {asset_file}")
        else:
            canonical = load_json(files[0])
            actual = sha256(asset_file)
            expected = str(canonical.get("revision", {}).get("contentSha256", ""))
            asset_file_result = {
                "path": str(asset_file),
                "sha256": actual,
                "matchesRevision": actual == expected,
            }
            if actual != expected:
                errors.append("asset file SHA-256 does not match exported revision")

    result = {
        "output": str(output_root),
        "projectName": manifest.get("projectName", ""),
        "successCount": success_count,
        "failureCount": failure_count,
        "readerSuccessCount": reader_success_count,
        "readerFailureCount": reader_failure_count,
        "canonicalFiles": len(files),
        "assetClasses": dict(sorted(class_counts.items())),
        "totalTags": sum(int(item["tags"]) for item in assets),
        "totalReferences": sum(int(item["references"]) for item in assets),
        "revisionsAvailable": sum(int(bool(item["revisionAvailable"])) for item in assets),
        "assetFile": asset_file_result,
        "errors": errors,
        "valid": not errors,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
