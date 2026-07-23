from __future__ import annotations

import hashlib
import json
import socket
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DESCRIPTOR_SCHEMA_VERSION = "1.0"
PROTOCOL_SCHEMA_VERSION = "1.0"
MAX_DESCRIPTOR_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 2.0

LIVE_EDITOR_METHODS = {
    "ue_editor_status": "editor.status",
    "ue_get_selection": "editor.getSelection",
    "ue_get_open_assets": "editor.getOpenAssets",
    "ue_get_dirty_assets": "editor.getDirtyAssets",
    "ue_get_current_level": "editor.getCurrentLevel",
    "ue_get_pie_state": "editor.getPieState",
}


class LiveEditorError(RuntimeError):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass(frozen=True)
class LiveEditorBridgeConfig:
    project_path: Path
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    @property
    def descriptor_path(self) -> Path:
        return self.project_path.resolve().parent / "Saved" / "UEAgentKit" / "EditorBridge.json"

    @property
    def project_name(self) -> str:
        return self.project_path.stem

    @property
    def project_path_hash(self) -> str:
        normalized = str(self.project_path.resolve()).replace("\\", "/").casefold()
        digest = hashlib.sha1(normalized.encode("utf-8"), usedforsecurity=False).hexdigest()
        return f"sha1:{digest}"


class LiveEditorBridgeService:
    def __init__(self, config: LiveEditorBridgeConfig, *, server_version: str) -> None:
        project_path = config.project_path.resolve()
        if project_path.suffix.casefold() != ".uproject":
            raise ValueError("Live Editor project_path must reference a .uproject file")
        if not project_path.is_file():
            raise FileNotFoundError(project_path)
        if not 0.1 <= config.timeout_seconds <= 30.0:
            raise ValueError("Live Editor timeout must be from 0.1 through 30 seconds")
        self.config = LiveEditorBridgeConfig(project_path, config.timeout_seconds)
        self.server_version = server_version

    def status(self) -> dict[str, Any]:
        try:
            result = self.call_method("editor.status")
        except LiveEditorError as exc:
            return {
                "configured": True,
                "state": "unavailable",
                "reasonCode": exc.code,
                "reason": str(exc),
                "retryable": exc.code in {
                    "live-editor-unavailable",
                    "live-editor-timeout",
                    "live-editor-connection-closed",
                },
            }
        return {
            "configured": True,
            "state": "available",
            "pluginVersion": result.get("pluginVersion", ""),
            "projectName": result.get("projectName", self.config.project_name),
            "engineVersion": result.get("engineVersion", ""),
            "processId": result.get("processId"),
            "sessionId": result.get("sessionId", ""),
            "capabilities": result.get("capabilities", []),
            "pieState": result.get("pieState", "unknown"),
            "currentLevel": result.get("currentLevel", ""),
            "dirtyPackageCount": result.get("dirtyPackageCount", 0),
        }

    def call_tool(self, tool_name: str) -> dict[str, Any]:
        method = LIVE_EDITOR_METHODS.get(tool_name)
        if method is None:
            raise ValueError(f"Unsupported Live Editor Tool: {tool_name}")
        result = self.call_method(method)
        return {
            "schemaVersion": "1.0",
            "tool": tool_name,
            "ok": True,
            "readOnly": True,
            "source": "live-editor-memory",
            "liveEditor": {
                "state": "available",
                "projectName": self.config.project_name,
                "serverVersion": self.server_version,
            },
            "result": result,
        }

    def call_method(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        descriptor = self._read_descriptor()
        request_id = uuid.uuid4().hex
        try:
            with socket.create_connection(
                ("127.0.0.1", descriptor["port"]),
                timeout=self.config.timeout_seconds,
            ) as connection:
                connection.settimeout(self.config.timeout_seconds)
                stream = connection.makefile("rwb", buffering=0)
                self._write_message(
                    stream,
                    {
                        "schemaVersion": PROTOCOL_SCHEMA_VERSION,
                        "requestId": request_id + "-hello",
                        "method": "hello",
                        "authToken": descriptor["authToken"],
                        "serverVersion": self.server_version,
                        "projectPathHash": self.config.project_path_hash,
                    },
                )
                hello = self._read_message(stream, request_id + "-hello")
                hello_result = self._unwrap_response(hello)
                if hello_result.get("pluginVersion") != self.server_version:
                    raise LiveEditorError(
                        "live-editor-version-mismatch",
                        "The running Editor Bridge version does not match the MCP Server version.",
                    )
                self._write_message(
                    stream,
                    {
                        "schemaVersion": PROTOCOL_SCHEMA_VERSION,
                        "requestId": request_id,
                        "method": method,
                        "params": params or {},
                    },
                )
                response = self._read_message(stream, request_id)
        except LiveEditorError:
            raise
        except socket.timeout as exc:
            raise LiveEditorError(
                "live-editor-timeout",
                "The configured Live Editor Bridge did not respond before the timeout.",
            ) from exc
        except (ConnectionRefusedError, ConnectionResetError, BrokenPipeError, OSError) as exc:
            raise LiveEditorError(
                "live-editor-unavailable",
                "The configured Unreal Editor Bridge is not reachable on localhost.",
            ) from exc
        result = self._unwrap_response(response)
        if not isinstance(result, dict):
            raise LiveEditorError(
                "live-editor-protocol-error",
                "The Live Editor Bridge returned a non-object result.",
            )
        return result

    def _read_descriptor(self) -> dict[str, Any]:
        path = self.config.descriptor_path
        try:
            raw = path.read_bytes()
        except FileNotFoundError as exc:
            raise LiveEditorError(
                "live-editor-unavailable",
                "The fixed project has no active UE Agent Kit Editor Bridge descriptor.",
            ) from exc
        except OSError as exc:
            raise LiveEditorError(
                "live-editor-unavailable",
                "The fixed Editor Bridge descriptor cannot be read.",
            ) from exc
        if not raw or len(raw) > MAX_DESCRIPTOR_BYTES:
            raise LiveEditorError(
                "live-editor-protocol-error",
                "The Editor Bridge descriptor size is invalid.",
            )
        try:
            descriptor = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LiveEditorError(
                "live-editor-protocol-error",
                "The Editor Bridge descriptor is not valid UTF-8 JSON.",
            ) from exc
        if not isinstance(descriptor, dict) or descriptor.get("schemaVersion") != DESCRIPTOR_SCHEMA_VERSION:
            raise LiveEditorError(
                "live-editor-protocol-error",
                "The Editor Bridge descriptor schema is unsupported.",
            )
        if descriptor.get("address") != "127.0.0.1":
            raise LiveEditorError(
                "live-editor-protocol-error",
                "The Editor Bridge descriptor is not bound to localhost.",
            )
        port = descriptor.get("port")
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise LiveEditorError("live-editor-protocol-error", "The Editor Bridge port is invalid.")
        token = descriptor.get("authToken")
        if not isinstance(token, str) or len(token) < 32 or len(token) > 256:
            raise LiveEditorError("live-editor-protocol-error", "The Editor Bridge authentication token is invalid.")
        if descriptor.get("projectName") != self.config.project_name:
            raise LiveEditorError(
                "live-editor-project-mismatch",
                "The Editor Bridge descriptor belongs to a different project.",
            )
        if descriptor.get("projectPathHash") != self.config.project_path_hash:
            raise LiveEditorError(
                "live-editor-project-mismatch",
                "The Editor Bridge descriptor does not match the fixed project path.",
            )
        if descriptor.get("pluginVersion") != self.server_version:
            raise LiveEditorError(
                "live-editor-version-mismatch",
                "The Editor Bridge descriptor version does not match the MCP Server version.",
            )
        capabilities = descriptor.get("capabilities")
        if not isinstance(capabilities, list) or not all(isinstance(item, str) for item in capabilities):
            raise LiveEditorError("live-editor-protocol-error", "The Editor Bridge capability list is invalid.")
        descriptor["capabilities"] = capabilities
        return descriptor

    @staticmethod
    def _write_message(stream: Any, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        stream.write(encoded)

    @staticmethod
    def _read_message(stream: Any, expected_request_id: str) -> dict[str, Any]:
        raw = stream.readline(MAX_RESPONSE_BYTES + 1)
        if not raw:
            raise LiveEditorError(
                "live-editor-connection-closed",
                "The Editor Bridge closed the connection before returning a response.",
            )
        if len(raw) > MAX_RESPONSE_BYTES:
            raise LiveEditorError("live-editor-protocol-error", "The Editor Bridge response exceeded the size limit.")
        try:
            response = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LiveEditorError("live-editor-protocol-error", "The Editor Bridge returned invalid JSON.") from exc
        if not isinstance(response, dict) or response.get("requestId") != expected_request_id:
            raise LiveEditorError("live-editor-protocol-error", "The Editor Bridge response requestId is invalid.")
        return response

    @staticmethod
    def _unwrap_response(response: dict[str, Any]) -> dict[str, Any]:
        if response.get("schemaVersion") != PROTOCOL_SCHEMA_VERSION:
            raise LiveEditorError("live-editor-protocol-error", "The Editor Bridge response schema is unsupported.")
        if response.get("ok") is True:
            result = response.get("result")
            if isinstance(result, dict):
                return result
            raise LiveEditorError("live-editor-protocol-error", "The Editor Bridge response has no result object.")
        error = response.get("error")
        if not isinstance(error, dict):
            raise LiveEditorError("live-editor-protocol-error", "The Editor Bridge error response is malformed.")
        code = str(error.get("code") or "live-editor-error")
        message = str(error.get("message") or "The Live Editor Bridge rejected the request.")
        details = error.get("details") if isinstance(error.get("details"), dict) else {}
        raise LiveEditorError(code, message, details=details)
