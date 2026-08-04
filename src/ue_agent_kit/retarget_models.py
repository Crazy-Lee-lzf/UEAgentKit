from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

RETARGET_PLAN_SCHEMA_VERSION = "retarget-plan-v1"
MAX_RETARGET_PLAN_CHAINS = 64
MAX_RETARGET_CHAIN_NAME_LENGTH = 96
MAX_RETARGET_BONE_NAME_LENGTH = 128
LOW_CONFIDENCE_THRESHOLD = 0.70
MEDIUM_CONFIDENCE_THRESHOLD = 0.90

HIGH_RISK_BONE_KEYWORDS = (
    "hair",
    "tail",
    "ear",
    "skirt",
    "cloth",
    "ribbon",
    "piao",
    "accessory",
    "weapon",
)

REQUIRED_CHAIN_NAMES = (
    "Root",
    "Spine",
    "Neck",
    "Head",
    "LeftArm",
    "RightArm",
    "LeftLeg",
    "RightLeg",
)

OPTIONAL_CHAIN_NAMES = (
    "LeftClavicle",
    "RightClavicle",
    "LeftHand",
    "RightHand",
    "LeftFoot",
    "RightFoot",
    "LeftToe",
    "RightToe",
    "LeftThumb",
    "RightThumb",
    "LeftIndex",
    "RightIndex",
)

CONFIRMATION_PREFIX = "APPLY RETARGET SETUP"


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def plan_digest(plan: dict[str, Any]) -> str:
    canonical = json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return _sha256_bytes(canonical.encode("utf-8"))


@dataclass
class RetargetPlanRecord:
    plan_id: str
    digest: str
    plan: dict[str, Any]
    created_at_utc: str
    consumed: bool = False


def build_retarget_plan(
    *,
    plan_id: str,
    project_id: str,
    editor_session_id: str,
    created_at_utc: str,
    source_mesh: str,
    target_mesh: str,
    chain_profile: str,
    analysis: dict[str, Any],
    source_rig_name: str,
    target_rig_name: str,
    source_retarget_root: str,
    target_retarget_root: str,
    source_chains: list[dict[str, Any]],
    target_chains: list[dict[str, Any]],
    revisions: dict[str, str],
    affected_assets: list[str],
    warnings: list[str],
    blocking_issues: list[str],
    output_directory: str,
) -> dict[str, Any]:
    plan: dict[str, Any] = {
        "schemaVersion": RETARGET_PLAN_SCHEMA_VERSION,
        "planId": plan_id,
        "projectId": project_id,
        "editorSessionId": editor_session_id,
        "createdAtUtc": created_at_utc,
        "source": {"mesh": source_mesh, "ikRigName": source_rig_name, "retargetRoot": source_retarget_root},
        "target": {"mesh": target_mesh, "ikRigName": target_rig_name, "retargetRoot": target_retarget_root},
        "profile": chain_profile,
        "sourceIKRigAction": _action_for_existing(source_mesh, analysis),
        "targetIKRigAction": _action_for_existing(target_mesh, analysis),
        "retargeterAction": "create",
        "retargeter": {"name": pick_retargeter_name(source_mesh, target_mesh), "action": "create"},
        "chains": {"source": source_chains, "target": target_chains},
        "mappings": build_chain_mappings(source_chains, target_chains),
        "pose": {"poseName": "TargetPose_A", "rootTranslationOffset": [0.0, 0.0, 0.0], "boneRotationOffsets": []},
        "batchDefaults": {
            "includeReferencedAssets": True,
            "exportOnlyAnimatedBones": True,
            "retainAdditiveFlags": True,
            "overwriteExisting": False,
            "outputDirectory": output_directory,
        },
        "revisions": revisions,
        "affectedAssets": affected_assets,
        "warnings": warnings,
        "blockingIssues": blocking_issues,
        "confirmationText": f"{CONFIRMATION_PREFIX} {plan_id}",
    }
    return plan


def _action_for_existing(mesh_path: str, analysis: dict[str, Any]) -> str:
    existing = analysis.get("existingAssets", {})
    mesh_short = mesh_path.rsplit(".", 1)[-1]
    for side in ("sourceIKRig", "targetIKRig"):
        rig_state = existing.get(side, {})
        tag = rig_state.get("assetPath", "")
        if mesh_short in tag:
            return "reuse" if rig_state.get("exists") else "create"
    return "create"


def select_chains_from_analysis(
    analysis: dict[str, Any],
    *,
    include_optional: bool,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Selects one non-ambiguous high-confidence candidate per semantic chain.

    Returns (chains, warnings, blocking_issues). Low-confidence candidates are
    never written into the plan; medium confidence requires human review.
    """
    chains: list[dict[str, Any]] = []
    warnings: list[str] = []
    blocking_issues: list[str] = []
    chain_reports = analysis.get("chainCandidates", [])
    matched_required = set()
    for report in chain_reports:
        chain_name = str(report.get("chain", "") or report.get("chainName", ""))
        required = str(report.get("required", "optional")).capitalize()
        candidates = report.get("candidates", [])
        if not isinstance(candidates, list) or not candidates:
            if required == "Required":
                blocking_issues.append(f"Required chain {chain_name} has no candidates.")
            continue
        if report.get("ambiguous"):
            candidates_sorted = sorted(candidates, key=lambda item: float(item.get("confidence", 0.0)), reverse=True)
            pelvis_candidates = [c for c in candidates_sorted if _is_pelvis_like_start(str(c.get("startBone", "")))]
            if pelvis_candidates and required == "Required":
                best = pelvis_candidates[0]
                warnings.append(
                    f"Chain {chain_name} ambiguity was resolved by pelvis-preference: {best.get('startBone')}."
                )
            else:
                blocking_issues.append(f"Chain {chain_name} is ambiguous between similar candidates.")
                continue
        else:
            best = max(candidates, key=lambda item: float(item.get("confidence", 0.0)))
        confidence = float(best.get("confidence", 0.0))
        if confidence < LOW_CONFIDENCE_THRESHOLD:
            if required == "Required":
                blocking_issues.append(f"Required chain {chain_name} confidence is too low ({confidence:.2f}).")
            else:
                warnings.append(f"Optional chain {chain_name} skipped: low confidence ({confidence:.2f}).")
            continue
        if confidence < MEDIUM_CONFIDENCE_THRESHOLD:
            warnings.append(f"Chain {chain_name} confidence {confidence:.2f} requires human review.")
        start_bone = str(best.get("startBone", ""))
        end_bone = str(best.get("endBone", ""))
        if _has_high_risk_keyword(chain_name, start_bone, end_bone):
            warnings.append(
                f"Chain {chain_name} touches accessory bones ({start_bone}..{end_bone}); accessory mapping is disabled."
            )
            if required == "Required":
                blocking_issues.append(f"Required chain {chain_name} would map accessory bones and is blocked.")
            continue
        if not include_optional and required != "Required":
            continue
        side = str(best.get("side", ""))
        if side not in {"Left", "Right", "Center"}:
            side = "Left" if chain_name.startswith("Left") else ("Right" if chain_name.startswith("Right") else "Center")
        chains.append(
            {
                "chain": chain_name,
                "required": required,
                "side": side,
                "startBone": start_bone,
                "endBone": end_bone,
            }
        )
        if required == "Required":
            matched_required.add(chain_name)
    for required_chain in REQUIRED_CHAIN_NAMES:
        if required_chain not in matched_required:
            blocking_issues.append(f"Required chain {required_chain} has no accepted candidate.")
    return chains, warnings, blocking_issues


def _is_pelvis_like_start(bone: str) -> bool:
    normalized = bone.casefold()
    return normalized in {"pelvis", "hips"} or "pelvis" in normalized or "hips" in normalized


def _has_high_risk_keyword(chain_name: str, start_bone: str, end_bone: str) -> bool:
    combined = f"{chain_name} {start_bone} {end_bone}".casefold()
    return any(keyword in combined for keyword in HIGH_RISK_BONE_KEYWORDS)


def pick_retarget_root(analysis: dict[str, Any], mesh_side: str) -> str:
    candidates = analysis.get(f"{mesh_side}RetargetRootCandidates", [])
    if isinstance(candidates, list) and candidates:
        return str(candidates[0])
    return ""


def pick_rig_name(mesh_path: str, prefix: str) -> str:
    short_name = mesh_path.rsplit(".", 1)[-1].rsplit("/", 1)[-1]
    return f"{prefix}{short_name}"


def pick_retargeter_name(source_mesh: str, target_mesh: str) -> str:
    source_short = source_mesh.rsplit(".", 1)[-1].rsplit("/", 1)[-1]
    target_short = target_mesh.rsplit(".", 1)[-1].rsplit("/", 1)[-1]
    return f"IKRetargeter_{source_short}_to_{target_short}"


def build_chain_mappings(
    source_chains: list[dict[str, Any]],
    target_chains: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Builds explicit target→source chain mappings from the plan chains.

    Both the source and target IK Rigs are written with the same semantic chain
    names, so every chain present on both sides is mapped by name. Chains that
    exist on only one side are intentionally omitted; the C++ mapping stage
    reports them as unmapped rather than failing.
    """
    source_by_name = {str(chain.get("chain", "")): chain for chain in source_chains}
    mappings: list[dict[str, str]] = []
    for target_chain in target_chains:
        chain_name = str(target_chain.get("chain", ""))
        if not chain_name or chain_name not in source_by_name:
            continue
        mappings.append(
            {
                "targetChain": chain_name,
                "sourceChain": chain_name,
                "required": str(target_chain.get("required", "optional")).capitalize(),
            }
        )
    return mappings
