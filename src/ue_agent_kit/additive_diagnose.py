from __future__ import annotations

from typing import Any

# Classifications produced by the read-only additive diagnosis.
ADDITIVE_CLASSIFICATIONS = (
    "non-additive",
    "additive-valid",
    "additive-missing-base-pose",
    "additive-base-pose-skeleton-mismatch",
    "additive-base-pose-ref-frame-invalid",
    "unsupported-composite",
    "load-failed",
)

# Base pose types that require an explicit Base Pose Sequence to evaluate.
_REQUIRES_SEQUENCE_BASE_POSE_TYPES = frozenset(
    {
        "AnimationScaled",
        "AnimationFrame",
        "LocalAnimationFrame",
    }
)

# Base pose types whose reference frame index must be inside the Base Pose range.
_FRAME_BASED_BASE_POSE_TYPES = frozenset(
    {
        "AnimationFrame",
        "LocalAnimationFrame",
    }
)


def classify_additive_animation(asset: dict[str, Any]) -> str:
    """Classify a raw additive diagnosis result into a stable category."""
    status = str(asset.get("status") or "")
    if status == "not-an-animation-sequence":
        return "unsupported-composite"
    if status != "success":
        return "load-failed"

    additive_type_name = str(asset.get("additiveTypeName") or "")
    if additive_type_name in ("", "None"):
        return "non-additive"

    base_pose_type_name = str(asset.get("basePoseTypeName") or "")
    if base_pose_type_name == "ReferencePose":
        # Reference-pose additive evaluates against the Skeleton reference pose;
        # no external Base Pose Sequence is required.
        return "additive-valid"

    if base_pose_type_name in _REQUIRES_SEQUENCE_BASE_POSE_TYPES:
        base_pose = asset.get("basePose")
        if not isinstance(base_pose, dict) or base_pose.get("refSequenceResolved") is not True:
            return "additive-missing-base-pose"
        if base_pose.get("skeletonCompatible") is not True:
            return "additive-base-pose-skeleton-mismatch"
        if base_pose_type_name in _FRAME_BASED_BASE_POSE_TYPES and base_pose.get("refFrameValid") is not True:
            return "additive-base-pose-ref-frame-invalid"
        return "additive-valid"

    return "additive-missing-base-pose"


def combined_evaluation_feasible(classification: str) -> bool:
    return classification == "additive-valid"


def suggest_next_step(asset: dict[str, Any], classification: str) -> str:
    if classification == "non-additive":
        return "This AnimSequence is not additive; no Base Pose is required for evaluation."
    if classification == "additive-valid":
        return "The Base Pose resolves cleanly; combined evaluation can proceed."
    if classification == "additive-missing-base-pose":
        return "The Base Pose Sequence is missing or unloaded; provide a valid Base Pose before combined evaluation."
    if classification == "additive-base-pose-skeleton-mismatch":
        return "The Base Pose Sequence belongs to a different Skeleton; replace it with one sharing the animation Skeleton."
    if classification == "additive-base-pose-ref-frame-invalid":
        return "The Base Pose reference frame is outside the Base Pose Sequence frame range."
    if classification == "unsupported-composite":
        return "Provide an AnimSequence asset; composite animation types are not handled by this diagnosis."
    return "Resolve the load or preview context, then rerun the diagnosis."


def build_additive_diagnosis_item(asset: dict[str, Any]) -> dict[str, Any]:
    classification = classify_additive_animation(asset)
    return {
        "assetPath": asset.get("assetPath", ""),
        "status": asset.get("status", ""),
        "skeletonPath": asset.get("skeletonPath", ""),
        "additiveAnimType": asset.get("additiveAnimType"),
        "additiveTypeName": asset.get("additiveTypeName", ""),
        "additiveBasePoseType": asset.get("additiveBasePoseType"),
        "basePoseTypeName": asset.get("basePoseTypeName", ""),
        "additiveRefFrameIndex": asset.get("additiveRefFrameIndex"),
        "additiveRefSequencePath": asset.get("additiveRefSequencePath", ""),
        "basePose": asset.get("basePose"),
        "classification": classification,
        "combinedEvaluationFeasible": combined_evaluation_feasible(classification),
        "suggestedNextStep": suggest_next_step(asset, classification),
    }
