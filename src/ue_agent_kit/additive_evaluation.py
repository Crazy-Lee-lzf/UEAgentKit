from __future__ import annotations

from typing import Any

from .additive_diagnose import classify_additive_animation, combined_evaluation_feasible

# C++ evaluation.status values emitted by editor.evaluateAnimationWithBasePose.
# "evaluated" is the only value that means an actual combined pose was produced.
EVALUATION_STATUSES = (
    "evaluated",
    "skipped-non-additive",
    "skipped-missing-base-pose",
    "skipped-skeleton-mismatch",
    "unavailable",
    "skeleton-empty",
    "bone-container-invalid",
)


def evaluation_feasible(asset: dict[str, Any]) -> bool:
    """Whether the C++ side actually produced a combined pose for this asset."""
    evaluation = asset.get("evaluation")
    return isinstance(evaluation, dict) and evaluation.get("status") == "evaluated"


def _suggest_evaluation_next_step(classification: str, evaluation_status: str) -> str:
    if evaluation_status == "evaluated":
        if classification == "additive-base-pose-ref-frame-invalid":
            return "Combined pose was evaluated with the engine-clamped Base Pose reference frame; fix RefFrameIndex to trust the result."
        return "Combined evaluation succeeded; inspect base, additiveDelta, and combined per bone."
    if evaluation_status == "skipped-non-additive":
        return "This AnimSequence is not additive; use ue_diagnose_animation_scale instead."
    if evaluation_status == "skipped-missing-base-pose":
        return "The Base Pose Sequence is missing or unloaded; provide a valid Base Pose before combined evaluation."
    if evaluation_status == "skipped-skeleton-mismatch":
        return "The Base Pose Sequence belongs to a different Skeleton; replace it with one sharing the animation Skeleton."
    if classification == "unsupported-composite":
        return "Provide an AnimSequence asset; composite animation types are not handled by this evaluation."
    if classification == "load-failed":
        return "Resolve the load or preview context, then rerun the evaluation."
    return "Resolve the evaluation context, then rerun."


def build_additive_evaluation_item(asset: dict[str, Any]) -> dict[str, Any]:
    classification = classify_additive_animation(asset)
    evaluation = asset.get("evaluation") if isinstance(asset.get("evaluation"), dict) else {}
    evaluation_status = str(evaluation.get("status") or "unavailable")
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
        "evaluation": evaluation,
        "evaluationFeasible": evaluation_status == "evaluated",
        "suggestedNextStep": _suggest_evaluation_next_step(classification, evaluation_status),
    }
