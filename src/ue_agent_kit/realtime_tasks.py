from __future__ import annotations

from typing import Any

from .editor_bridge import LiveEditorBridgeService, LiveEditorError

BATCH_TASK_OPERATIONS = ("scanCurrentWorld",)
BATCH_TASK_DEFAULT_MAX_ACTORS = 2000
BATCH_TASK_DEFAULT_MAX_COMPONENTS_PER_ACTOR = 200
BATCH_TASK_DEFAULT_TIMEOUT_SECONDS = 60


def register_batch_task_tools(
    *,
    server: Any,
    live_editor_service: LiveEditorBridgeService,
    read_annotations: Any,
    tool_annotations_type: Any,
    error_response: Any,
) -> None:
    action_annotations = tool_annotations_type(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )

    def call(tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            return live_editor_service.call_tool(tool_name, params)
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response(tool_name, exc, read_only=False)

    @server.tool(annotations=action_annotations)
    def ue_start_batch_task(
        operation: str = BATCH_TASK_OPERATIONS[0],
        max_actors: int = BATCH_TASK_DEFAULT_MAX_ACTORS,
        max_components_per_actor: int = BATCH_TASK_DEFAULT_MAX_COMPONENTS_PER_ACTOR,
        timeout_seconds: int = BATCH_TASK_DEFAULT_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """Start one frame-stepped read-only World scan Batch Task and return its taskId and initial progress."""
        return call(
            "ue_start_batch_task",
            {
                "operation": operation,
                "maxActors": max_actors,
                "maxComponentsPerActor": max_components_per_actor,
                "timeoutSeconds": timeout_seconds,
            },
        )

    @server.tool(annotations=read_annotations)
    def ue_get_batch_task(task_id: str) -> dict[str, Any]:
        """Return the bounded progress, summary, and terminal details of one registered Batch Task."""
        return call("ue_get_batch_task", {"taskId": task_id})

    @server.tool(annotations=action_annotations)
    def ue_cancel_batch_task(task_id: str) -> dict[str, Any]:
        """Cancel the single running Batch Task bound to this Editor session."""
        return call("ue_cancel_batch_task", {"taskId": task_id})
