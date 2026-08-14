from __future__ import annotations

from typing import Any

# Fixable fields surfaced by the read-only additive Base Pose fix plan.
FIXABLE_FIELDS = (
    "additiveRefSequencePath",
    "additiveRefFrameIndex",
    "additiveAnimType",
    "additiveBasePoseType",
)


def _fix_item(
    field: str,
    current_value: Any,
    proposed_value: Any,
    can_auto_derive: bool,
    requires_user_selection: bool,
    reason: str,
) -> dict[str, Any]:
    return {
        "field": field,
        "currentValue": current_value,
        "proposedValue": proposed_value,
        "canAutoDerive": can_auto_derive,
        "requiresUserSelection": requires_user_selection,
        "reason": reason,
    }


def build_additive_fix_plan(asset: dict[str, Any]) -> dict[str, Any]:
    """Derive a read-only fix plan from an additive diagnosis item.

    The plan is never applied here; it only classifies which fields need to
    change and whether each change is auto-derivable or requires user selection.
    """
    classification = str(asset.get("classification") or "")
    asset_path = str(asset.get("assetPath") or "")
    base_pose_type = str(asset.get("basePoseTypeName") or "")
    ref_frame_index = asset.get("additiveRefFrameIndex")
    ref_sequence_path = str(asset.get("additiveRefSequencePath") or "")
    base_pose = asset.get("basePose") if isinstance(asset.get("basePose"), dict) else {}
    frame_count = base_pose.get("frameCount")

    if classification in ("non-additive", "additive-valid"):
        return {
            "classification": classification,
            "fixesNeeded": False,
            "referenceMutationRequired": False,
            "compositeRebuildRequired": False,
            "autoApplyAllowed": False,
            "requiresUserReview": False,
            "fixItems": [],
            "suggestedNextStep": (
                "The animation is not additive; no Base Pose fix applies."
                if classification == "non-additive"
                else "The Base Pose already resolves cleanly; no fix is required."
            ),
        }

    if classification in ("unsupported-composite", "load-failed"):
        return {
            "classification": classification,
            "fixesNeeded": False,
            "referenceMutationRequired": False,
            "compositeRebuildRequired": False,
            "autoApplyAllowed": False,
            "requiresUserReview": False,
            "fixItems": [],
            "suggestedNextStep": "Resolve the load or asset-type context before planning a fix.",
        }

    fix_items: list[dict[str, Any]] = []
    reference_mutation_required = False

    if classification == "additive-base-pose-ref-frame-invalid":
        upper_bound = frame_count - 1 if isinstance(frame_count, int) and frame_count > 0 else 0
        fix_items.append(
            _fix_item(
                field="additiveRefFrameIndex",
                current_value=ref_frame_index,
                proposed_value=0,
                can_auto_derive=True,
                requires_user_selection=False,
                reason=(
                    f"RefFrameIndex is outside the Base Pose Sequence range [0, {upper_bound}]; "
                    "clamp to frame 0."
                ),
            )
        )
        if base_pose_type == "AnimationFrame" and ref_sequence_path and ref_sequence_path == asset_path:
            reference_mutation_required = True
            fix_items.append(
                _fix_item(
                    field="additiveRefSequencePath",
                    current_value=ref_sequence_path,
                    proposed_value=None,
                    can_auto_derive=False,
                    requires_user_selection=True,
                    reason=(
                        "Base Pose Sequence is self-referential for an AnimationFrame base pose; "
                        "select the correct same-Skeleton base animation."
                    ),
                )
            )
    elif classification == "additive-missing-base-pose":
        reference_mutation_required = True
        fix_items.append(
            _fix_item(
                field="additiveRefSequencePath",
                current_value=ref_sequence_path,
                proposed_value=None,
                can_auto_derive=False,
                requires_user_selection=True,
                reason="Base Pose Sequence is missing; select a same-Skeleton base animation.",
            )
        )
    elif classification == "additive-base-pose-skeleton-mismatch":
        reference_mutation_required = True
        fix_items.append(
            _fix_item(
                field="additiveRefSequencePath",
                current_value=ref_sequence_path,
                proposed_value=None,
                can_auto_derive=False,
                requires_user_selection=True,
                reason="Base Pose Sequence belongs to a different Skeleton; select a base animation sharing the animation Skeleton.",
            )
        )

    requires_user_review = any(item["requiresUserSelection"] for item in fix_items)
    if reference_mutation_required:
        next_step = (
            "Select the correct same-Skeleton Base Pose animation, then re-run "
            "ue_evaluate_animation_with_base_pose to verify the combined Root/Pelvis scale."
        )
    else:
        next_step = (
            "Correct RefFrameIndex to a valid frame, then re-run "
            "ue_evaluate_animation_with_base_pose to verify the combined Root/Pelvis scale."
        )

    return {
        "classification": classification,
        "fixesNeeded": True,
        "referenceMutationRequired": reference_mutation_required,
        "compositeRebuildRequired": False,
        "autoApplyAllowed": False,
        "requiresUserReview": requires_user_review,
        "fixItems": fix_items,
        "suggestedNextStep": next_step,
    }
