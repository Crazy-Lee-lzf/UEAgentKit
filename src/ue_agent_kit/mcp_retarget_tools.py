from __future__ import annotations

import json
from typing import Any

from .editor_bridge import LiveEditorBridgeService, LiveEditorError

RETARGET_INSPECT_CAPABILITY = "retarget.inspect"


def _assert_retarget_policy_capability(live_editor_service: LiveEditorBridgeService) -> None:
    policy_path = getattr(live_editor_service.config, "policy_path", None)
    if policy_path is None:
        raise LiveEditorError(
            "retarget_capability_unavailable",
            "The fixed Project Write Policy is unavailable for retarget capability checks.",
        )
    try:
        policy = json.loads(policy_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise LiveEditorError(
            "retarget_capability_unavailable",
            "The fixed Project Write Policy could not be read for retarget capability checks.",
        ) from exc
    capabilities = policy.get("retargetCapabilities")
    if not isinstance(capabilities, list) or RETARGET_INSPECT_CAPABILITY not in capabilities:
        raise LiveEditorError(
            "retarget_capability_unavailable",
            "The fixed Policy does not enable the retarget.inspect capability.",
        )


def register_retarget_tools(
    *,
    server: Any,
    live_editor_service: LiveEditorBridgeService,
    read_annotations: Any,
    error_response: Any,
) -> None:
    @server.tool(annotations=read_annotations)
    def ue_analyze_animation_retarget(
        sourceMesh: str,
        targetMesh: str,
        includeOptionalChains: bool = True,
        maxBoneDetails: int = 512,
    ) -> dict[str, Any]:
        """Read-only retarget compatibility analysis between two loaded Skeletal Meshes."""
        try:
            _assert_retarget_policy_capability(live_editor_service)
            return live_editor_service.call_tool(
                "ue_analyze_animation_retarget",
                {
                    "sourceMesh": sourceMesh,
                    "targetMesh": targetMesh,
                    "includeOptionalChains": includeOptionalChains,
                    "maxBoneDetails": maxBoneDetails,
                },
            )
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_analyze_animation_retarget", exc, read_only=True)
