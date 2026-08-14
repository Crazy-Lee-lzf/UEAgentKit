from __future__ import annotations

import math
from typing import Any

# Stable classifications produced by the read-only character ground-contact diagnosis.
GROUND_CONTACT_CLASSIFICATIONS = (
    "mesh-offset-candidate",
    "capsule-size-candidate",
    "animation-root-z-candidate",
    "pelvis-offset-candidate",
    "foot-ik-needed",
    "insufficient-context",
)

# Thresholds in Unreal centimeters (1 UE unit == 1 cm).
_MESH_OFFSET_CM = 5.0          # mesh component translation magnitude that counts as "offset".
_FOOT_GROUND_CM = 5.0          # foot-to-capsule-bottom distance treated as "grounded".
_ROOT_Z_DRIFT_CM = 8.0         # root motion Z / root bone drift treated as a lift.
_BONE_Z_DELTA_CM = 8.0         # pelvis/root Z delta vs Skeleton Reference treated as a lift.
_CAPSULE_BOTTOM_OVERHANG_CM = 10.0  # capsule bottom above the foot by more than this -> capsule too short.


def _vec3(value: Any) -> tuple[float, float, float]:
    if not isinstance(value, dict):
        return (0.0, 0.0, 0.0)
    return (
        float(value.get("x") or 0.0),
        float(value.get("y") or 0.0),
        float(value.get("z") or 0.0),
    )


def _magnitude(value: Any) -> float:
    x, y, z = _vec3(value)
    return math.sqrt(x * x + y * y + z * z)


def _bone_z(bones: Any, bone_name: str) -> float | None:
    if not isinstance(bones, list):
        return None
    for bone in bones:
        if not isinstance(bone, dict) or str(bone.get("bone") or "") != bone_name:
            continue
        if bone.get("boneExists") is not True:
            return None
        location = bone.get("componentLocation")
        if not isinstance(location, dict):
            return None
        return float(location.get("z") or 0.0)
    return None


def _first_sample(result: dict[str, Any]) -> dict[str, Any] | None:
    samples = (result.get("animation") or {}).get("samples") or []
    if not isinstance(samples, list) or not samples:
        return None
    first = samples[0]
    return first if isinstance(first, dict) else None


def _capsule_bottom(capsule: dict[str, Any]) -> float:
    _, _, rel_z = _vec3(capsule.get("relativeLocation"))
    return rel_z - float(capsule.get("halfHeight") or 0.0)


def _approx_foot_to_capsule_bottom(
    mesh: dict[str, Any],
    capsule: dict[str, Any],
    foot_component_z: float,
) -> float:
    """Foot-to-capsule-bottom using mesh translation Z only (rotation/scale ignored)."""
    _, _, mesh_z = _vec3((mesh.get("relativeTransform") or {}).get("translation"))
    return mesh_z + foot_component_z - _capsule_bottom(capsule)


def classify_ground_contact(result: dict[str, Any]) -> str:
    character = result.get("character") or {}
    capsule = character.get("capsule") or {}
    mesh = character.get("mesh") or {}
    animation = result.get("animation") or {}
    skeleton_reference = result.get("skeletonReference") or {}

    if not character.get("classPath"):
        return "insufficient-context"
    if capsule.get("present") is not True or mesh.get("present") is not True:
        return "insufficient-context"

    # 1. Mesh offset: the mesh component is translated relative to the actor/capsule.
    translation = (mesh.get("relativeTransform") or {}).get("translation") or {}
    if _magnitude(translation) > _MESH_OFFSET_CM:
        return "mesh-offset-candidate"

    bone_names = result.get("boneNames") or {}
    left_foot = str(bone_names.get("leftFoot") or "foot_l")
    right_foot = str(bone_names.get("rightFoot") or "foot_r")
    root_bone = str(bone_names.get("root") or "root")
    pelvis_bone = str(bone_names.get("pelvis") or "pelvis")

    if animation.get("status") == "evaluated":
        sample = _first_sample(result)
        if sample is None:
            return "insufficient-context"

        left = sample.get("leftFootToCapsuleBottom")
        right = sample.get("rightFootToCapsuleBottom")
        floats = (left is not None and float(left) > _FOOT_GROUND_CM) or (
            right is not None and float(right) > _FOOT_GROUND_CM
        )
        if floats:
            if float(animation.get("rootMotionZ") or 0.0) > _ROOT_Z_DRIFT_CM:
                return "animation-root-z-candidate"
            ref_bones = skeleton_reference.get("bones")
            sample_bones = sample.get("bones")
            ref_root_z = _bone_z(ref_bones, root_bone)
            sample_root_z = _bone_z(sample_bones, root_bone)
            if ref_root_z is not None and sample_root_z is not None and sample_root_z - ref_root_z > _BONE_Z_DELTA_CM:
                return "animation-root-z-candidate"
            ref_pelvis_z = _bone_z(ref_bones, pelvis_bone)
            sample_pelvis_z = _bone_z(sample_bones, pelvis_bone)
            if ref_pelvis_z is not None and sample_pelvis_z is not None and sample_pelvis_z - ref_pelvis_z > _BONE_Z_DELTA_CM:
                return "pelvis-offset-candidate"
            return "foot-ik-needed"

        # Grounded feet; still flag an over-tall capsule.
        ref_foot_z = _bone_z(skeleton_reference.get("bones"), left_foot)
        if ref_foot_z is None:
            ref_foot_z = _bone_z(skeleton_reference.get("bones"), right_foot)
        if ref_foot_z is not None:
            foot_bottom = _approx_foot_to_capsule_bottom(mesh, capsule, ref_foot_z)
            if foot_bottom < -_CAPSULE_BOTTOM_OVERHANG_CM:
                return "capsule-size-candidate"
        return "insufficient-context"

    # No evaluated animation: fall back to the Skeleton Reference Pose.
    ref_bones = skeleton_reference.get("bones")
    ref_foot_z = _bone_z(ref_bones, left_foot)
    if ref_foot_z is None:
        ref_foot_z = _bone_z(ref_bones, right_foot)
    if ref_foot_z is not None:
        foot_bottom = _approx_foot_to_capsule_bottom(mesh, capsule, ref_foot_z)
        if foot_bottom > _FOOT_GROUND_CM:
            return "foot-ik-needed"
        if foot_bottom < -_CAPSULE_BOTTOM_OVERHANG_CM:
            return "capsule-size-candidate"
    return "insufficient-context"


def suggest_next_step(result: dict[str, Any], classification: str) -> str:
    if classification == "mesh-offset-candidate":
        return "The Skeletal Mesh Component has a non-zero Relative Transform; verify its origin matches the Capsule."
    if classification == "capsule-size-candidate":
        return "The Capsule bottom sits well below the feet; review Capsule Half Height / relative Z."
    if classification == "animation-root-z-candidate":
        return "The animation root lifts off the ground (root motion Z or root bone drift); check the Root Track Z / Root Motion."
    if classification == "pelvis-offset-candidate":
        return "The Pelvis bone is lifted relative to the Skeleton Reference Pose; review the Pelvis track."
    if classification == "foot-ik-needed":
        return "The feet float above the Capsule bottom; Foot IK or an animation track fix may be required."
    return "Not enough context to isolate the floating source; provide a Character with Capsule + Mesh and a compatible AnimSequence."


def build_ground_contact_item(result: dict[str, Any]) -> dict[str, Any]:
    classification = classify_ground_contact(result)
    character = result.get("character") or {}
    animation = result.get("animation") or {}
    return {
        "characterPath": character.get("path", ""),
        "classPath": character.get("classPath", ""),
        "animationPath": animation.get("path", ""),
        "animationStatus": animation.get("status", ""),
        "skeletonCompatible": animation.get("skeletonCompatible"),
        "classification": classification,
        "suggestedNextStep": suggest_next_step(result, classification),
        "capsule": character.get("capsule"),
        "mesh": character.get("mesh"),
        "animation": animation,
        "skeletonReference": result.get("skeletonReference"),
    }
