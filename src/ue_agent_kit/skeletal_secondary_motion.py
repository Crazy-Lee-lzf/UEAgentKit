from __future__ import annotations

from typing import Any

# Stable classifications produced by the read-only skeletal secondary-motion inspection.
SECONDARY_MOTION_CLASSIFICATIONS = (
    "missing-bones",
    "missing-skin-weights",
    "no-animation-tracks",
    "no-secondary-motion-node",
    "no-physics-bodies",
    "cloth-data-present",
    "cloth-data-missing",
    "cloth-binding-incomplete",
)


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _bool(value: Any) -> bool:
    return value is True


def _cloth_binding_complete(mesh: dict[str, Any], cloth: dict[str, Any]) -> bool:
    """A cloth asset is considered fully bound when every asset covers every mesh LOD."""
    assets = cloth.get("assets") or []
    if not isinstance(assets, list) or not assets:
        return False
    lod_count = _int(mesh.get("lodCount"))
    for asset in assets:
        if not isinstance(asset, dict):
            return False
        if lod_count > 0 and _int(asset.get("numLods")) < lod_count:
            return False
    return True


def classify_secondary_motion(result: dict[str, Any]) -> str:
    mesh = result.get("skeletalMesh") or {}
    skeleton = result.get("skeleton") or {}
    physics = result.get("physics") or {}
    cloth = result.get("cloth") or {}
    animation = result.get("animation") or {}
    anim_blueprint = result.get("animationBlueprint") or {}

    if _int(skeleton.get("boneCount")) <= 0:
        return "missing-bones"
    if not _bool(mesh.get("hasSkinWeights")):
        return "missing-skin-weights"

    if animation.get("status") == "evaluated" and _int(animation.get("animatedBoneCount")) == 0:
        return "no-animation-tracks"

    if (
        anim_blueprint.get("status") == "evaluated"
        and _int(anim_blueprint.get("secondaryMotionNodeCount")) == 0
    ):
        return "no-secondary-motion-node"

    clothing_count = _int(cloth.get("assetCount"))
    if clothing_count > 0:
        if not _cloth_binding_complete(mesh, cloth):
            return "cloth-binding-incomplete"
        return "cloth-data-present"

    if _int(physics.get("bodyCount")) == 0 and _int(anim_blueprint.get("secondaryMotionNodeCount")) == 0:
        return "no-physics-bodies"

    return "cloth-data-missing"


def suggest_next_step(result: dict[str, Any], classification: str) -> str:
    if classification == "missing-bones":
        return "The Skeleton has no bones; import or assign a valid Skeleton to the Skeletal Mesh."
    if classification == "missing-skin-weights":
        return "The Skeletal Mesh has no skin weights; secondary motion cannot deform the mesh without them."
    if classification == "no-animation-tracks":
        return "The AnimSequence has no bone tracks for this Skeleton; verify the sequence was retargeted to the correct Skeleton."
    if classification == "no-secondary-motion-node":
        return "The Animation Blueprint has no AnimDynamics / RigidBody / Spring nodes; add one to drive secondary motion."
    if classification == "no-physics-bodies":
        return "No Physics Asset (or it has zero bodies) and no cloth/nodes; assign a Physics Asset or add secondary-motion nodes."
    if classification == "cloth-data-present":
        return "Cloth data is present and fully bound; verify the Chaos Cloth config values (stiffness, damping)."
    if classification == "cloth-binding-incomplete":
        return "Cloth assets exist but do not cover every LOD; bind the cloth to all LODs or remove unused LOD cloth sections."
    return "No cloth data present; secondary motion relies on Physics Asset or Animation Blueprint nodes."


def build_secondary_motion_item(result: dict[str, Any]) -> dict[str, Any]:
    classification = classify_secondary_motion(result)
    return {
        "skeletalMeshPath": (result.get("skeletalMesh") or {}).get("path", ""),
        "classification": classification,
        "suggestedNextStep": suggest_next_step(result, classification),
        "skeletalMesh": result.get("skeletalMesh"),
        "skeleton": result.get("skeleton"),
        "physics": result.get("physics"),
        "cloth": result.get("cloth"),
        "animation": result.get("animation"),
        "animationBlueprint": result.get("animationBlueprint"),
    }
