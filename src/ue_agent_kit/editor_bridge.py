from __future__ import annotations

import hashlib
import json
import socket
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .tool_registry import LIVE_EDITOR_METHODS, TOOL_DEFINITIONS_BY_NAME

DESCRIPTOR_SCHEMA_VERSION = "1.0"
PROTOCOL_SCHEMA_VERSION = "1.0"
MAX_DESCRIPTOR_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 2.0


LIVE_LOG_VERBOSITIES = {
    "fatal",
    "error",
    "warning",
    "display",
    "log",
    "verbose",
    "veryverbose",
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
            "currentPieSessionId": result.get("currentPieSessionId", 0),
            "capturedPieState": result.get("capturedPieState", "unavailable"),
            "currentLevel": result.get("currentLevel", ""),
            "dirtyPackageCount": result.get("dirtyPackageCount", 0),
        }

    def call_tool(self, tool_name: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        method = LIVE_EDITOR_METHODS.get(tool_name)
        if method is None:
            raise ValueError(f"Unsupported Live Editor Tool: {tool_name}")
        normalized_params = self._normalize_tool_params(tool_name, params or {})
        result = self.call_method(method, normalized_params)
        definition = TOOL_DEFINITIONS_BY_NAME[tool_name]
        return {
            "schemaVersion": "1.0",
            "tool": tool_name,
            "ok": True,
            "readOnly": definition.read_only,
            "source": "live-editor-memory",
            "liveEditor": {
                "state": "available",
                "projectName": self.config.project_name,
                "serverVersion": self.server_version,
            },
            "result": result,
        }

    @staticmethod
    def _normalize_tool_params(tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(params, dict):
            raise LiveEditorError("live-editor-invalid-parameters", "Live Editor Tool parameters must be an object.")
        if tool_name == "ue_get_output_log":
            allowed = {
                "category",
                "minimumVerbosity",
                "keyword",
                "sinceSequence",
                "sinceUtc",
                "untilUtc",
                "pieSessionId",
                "limit",
            }
            LiveEditorBridgeService._reject_unknown_params(params, allowed)
            normalized = {
                "category": LiveEditorBridgeService._bounded_string(params.get("category", ""), "category", 128),
                "minimumVerbosity": LiveEditorBridgeService._normalize_verbosity(params.get("minimumVerbosity", "log")),
                "keyword": LiveEditorBridgeService._bounded_string(params.get("keyword", ""), "keyword", 256),
                "sinceSequence": LiveEditorBridgeService._bounded_integer(params.get("sinceSequence", 0), "sinceSequence", 0),
                "pieSessionId": LiveEditorBridgeService._bounded_integer(params.get("pieSessionId", -1), "pieSessionId", -1),
                "limit": LiveEditorBridgeService._bounded_integer(params.get("limit", 100), "limit", 1, 100),
            }
            since_utc = LiveEditorBridgeService._normalize_utc(params.get("sinceUtc", ""), "sinceUtc")
            until_utc = LiveEditorBridgeService._normalize_utc(params.get("untilUtc", ""), "untilUtc")
            if since_utc:
                normalized["sinceUtc"] = since_utc
            if until_utc:
                normalized["untilUtc"] = until_utc
            if since_utc and until_utc and since_utc > until_utc:
                raise LiveEditorError("live-editor-invalid-parameters", "sinceUtc must not be later than untilUtc.")
            return normalized
        if tool_name == "ue_get_compile_errors":
            allowed = {"assetPath", "sinceSequence", "pieSessionId", "limit"}
            LiveEditorBridgeService._reject_unknown_params(params, allowed)
            asset_path = LiveEditorBridgeService._bounded_string(params.get("assetPath", ""), "assetPath", 512)
            if asset_path:
                LiveEditorBridgeService._validate_game_object_path(asset_path)
            return {
                "assetPath": asset_path,
                "sinceSequence": LiveEditorBridgeService._bounded_integer(params.get("sinceSequence", 0), "sinceSequence", 0),
                "pieSessionId": LiveEditorBridgeService._bounded_integer(params.get("pieSessionId", -1), "pieSessionId", -1),
                "limit": LiveEditorBridgeService._bounded_integer(params.get("limit", 100), "limit", 1, 100),
            }
        if tool_name == "ue_inspect_asset_live":
            allowed = {"assetPath"}
            LiveEditorBridgeService._reject_unknown_params(params, allowed)
            asset_path = LiveEditorBridgeService._bounded_string(params.get("assetPath", ""), "assetPath", 512)
            LiveEditorBridgeService._validate_game_object_path(asset_path)
            return {"assetPath": asset_path}
        if tool_name in {"ue_open_asset", "ue_focus_asset", "ue_sync_content_browser", "ue_compile_blueprint"}:
            allowed = {"assetPath"}
            LiveEditorBridgeService._reject_unknown_params(params, allowed)
            asset_path = LiveEditorBridgeService._bounded_string(params.get("assetPath", ""), "assetPath", 512)
            LiveEditorBridgeService._validate_game_object_path(asset_path)
            return {"assetPath": asset_path}
        if tool_name == "ue_focus_actor":
            allowed = {"actorGuid"}
            LiveEditorBridgeService._reject_unknown_params(params, allowed)
            actor_guid = LiveEditorBridgeService._bounded_string(params.get("actorGuid", ""), "actorGuid", 64)
            LiveEditorBridgeService._validate_guid(actor_guid, "actorGuid")
            return {"actorGuid": actor_guid.lower()}
        if tool_name == "ue_validate_asset":
            allowed = {"assetPath", "maxIssues"}
            LiveEditorBridgeService._reject_unknown_params(params, allowed)
            asset_path = LiveEditorBridgeService._bounded_string(params.get("assetPath", ""), "assetPath", 512)
            LiveEditorBridgeService._validate_game_object_path(asset_path)
            return {
                "assetPath": asset_path,
                "maxIssues": LiveEditorBridgeService._bounded_integer(params.get("maxIssues", 100), "maxIssues", 1, 200),
            }
        if tool_name == "ue_validate_folder":
            allowed = {"packagePath", "recursive", "maxAssets", "maxIssues"}
            LiveEditorBridgeService._reject_unknown_params(params, allowed)
            package_path = LiveEditorBridgeService._bounded_string(params.get("packagePath", ""), "packagePath", 512)
            LiveEditorBridgeService._validate_game_package_path(package_path)
            recursive = params.get("recursive", True)
            if not isinstance(recursive, bool):
                raise LiveEditorError("live-editor-invalid-parameters", "recursive must be a boolean.")
            return {
                "packagePath": package_path.rstrip("/"),
                "recursive": recursive,
                "maxAssets": LiveEditorBridgeService._bounded_integer(params.get("maxAssets", 100), "maxAssets", 1, 500),
                "maxIssues": LiveEditorBridgeService._bounded_integer(params.get("maxIssues", 100), "maxIssues", 1, 200),
            }
        if params:
            raise LiveEditorError(
                "live-editor-invalid-parameters",
                f"{tool_name} does not accept parameters.",
            )
        return {}

    @staticmethod
    def _reject_unknown_params(params: dict[str, Any], allowed: set[str]) -> None:
        unknown = sorted(set(params) - allowed)
        if unknown:
            raise LiveEditorError(
                "live-editor-invalid-parameters",
                f"Unsupported Live Editor parameter: {unknown[0]}",
            )

    @staticmethod
    def _bounded_string(value: Any, name: str, maximum: int) -> str:
        if not isinstance(value, str):
            raise LiveEditorError("live-editor-invalid-parameters", f"{name} must be a string.")
        if len(value) > maximum or any(ord(character) < 32 for character in value):
            raise LiveEditorError("live-editor-invalid-parameters", f"{name} is outside the allowed text boundary.")
        return value

    @staticmethod
    def _bounded_integer(value: Any, name: str, minimum: int, maximum: int | None = None) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise LiveEditorError("live-editor-invalid-parameters", f"{name} must be an integer.")
        if value < minimum or (maximum is not None and value > maximum):
            raise LiveEditorError("live-editor-invalid-parameters", f"{name} is outside the allowed range.")
        return value

    @staticmethod
    def _normalize_verbosity(value: Any) -> str:
        if not isinstance(value, str):
            raise LiveEditorError("live-editor-invalid-parameters", "minimumVerbosity must be a string.")
        normalized = value.casefold()
        if normalized not in LIVE_LOG_VERBOSITIES:
            raise LiveEditorError("live-editor-invalid-parameters", "minimumVerbosity is unsupported.")
        return normalized

    @staticmethod
    def _normalize_utc(value: Any, name: str) -> str:
        if value == "":
            return ""
        if not isinstance(value, str) or len(value) > 64:
            raise LiveEditorError("live-editor-invalid-parameters", f"{name} must be an ISO-8601 UTC timestamp.")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise LiveEditorError("live-editor-invalid-parameters", f"{name} must be an ISO-8601 UTC timestamp.") from exc
        if parsed.tzinfo is None:
            raise LiveEditorError("live-editor-invalid-parameters", f"{name} must include a UTC offset.")
        return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    @staticmethod
    def _validate_guid(value: str, name: str) -> None:
        compact = value.replace("-", "")
        if len(compact) != 32 or any(character not in "0123456789abcdefABCDEF" for character in compact):
            raise LiveEditorError("live-editor-invalid-parameters", f"{name} must be a valid GUID.")

    @staticmethod
    def _validate_game_package_path(package_path: str) -> None:
        normalized = package_path.rstrip("/")
        if (
            not normalized.startswith("/Game/")
            or normalized == "/Game"
            or "." in normalized
            or "//" in normalized
            or "\\" in normalized
            or ":" in normalized
            or ".." in normalized
            or any(ord(character) < 32 for character in normalized)
        ):
            raise LiveEditorError(
                "live-editor-invalid-parameters",
                "packagePath must be a non-root /Game package path without an object name.",
            )

    @staticmethod
    def _validate_game_object_path(asset_path: str) -> None:
        if (
            not asset_path
            or not asset_path.startswith("/Game/")
            or "\\" in asset_path
            or ":" in asset_path
            or ".." in asset_path
            or any(ord(character) < 32 for character in asset_path)
        ):
            raise LiveEditorError("live-editor-invalid-parameters", "assetPath must be an exact /Game Object Path.")
        package_path, separator, object_name = asset_path.rpartition(".")
        if not separator or not object_name or "/" in object_name or package_path.rfind("/") >= len(package_path) - 1:
            raise LiveEditorError("live-editor-invalid-parameters", "assetPath must be an exact /Game Object Path.")

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
