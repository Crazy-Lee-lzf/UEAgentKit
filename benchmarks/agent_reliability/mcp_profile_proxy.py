from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
from typing import Any


HIDDEN_TOOLS = {
    "ue_get_task_context",
    "ue_analyze_change_impact",
    "ue_analyze_semantic_diff",
    "ue_build_verification_plan",
    "ue_evaluate_trust_verdict",
}
HIDDEN_CAPABILITY_KEYS = {
    "impactanalysis",
    "semanticdiff",
    "taskcontext",
    "trustverdict",
    "verificationplan",
    "verificationtrust",
}
_REMOVE = object()


def _normalized_key(value: Any) -> str:
    return "".join(character for character in str(value).lower() if character.isalnum())


def _filter_value(value: Any) -> Any:
    if isinstance(value, dict):
        identity = str(value.get("name") or value.get("tool") or "")
        if identity in HIDDEN_TOOLS:
            return _REMOVE
        result: dict[str, Any] = {}
        for key, child in value.items():
            if str(key) in HIDDEN_TOOLS or _normalized_key(key) in HIDDEN_CAPABILITY_KEYS:
                continue
            filtered = _filter_value(child)
            if filtered is not _REMOVE:
                result[str(key)] = filtered
        return result
    if isinstance(value, list):
        result = []
        for child in value:
            filtered = _filter_value(child)
            if filtered is not _REMOVE:
                result.append(filtered)
        return result
    if isinstance(value, str):
        if value in HIDDEN_TOOLS:
            return _REMOVE
        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError:
                decoded = None
            if decoded is not None:
                filtered = _filter_value(decoded)
                if filtered is _REMOVE:
                    return _REMOVE
                return json.dumps(filtered, ensure_ascii=False, separators=(",", ":"))
        filtered_text = value
        for tool in HIDDEN_TOOLS:
            filtered_text = filtered_text.replace(tool, "[profile-hidden-tool]")
        return filtered_text
    return value


def filter_server_message(
    message: dict[str, Any],
    *,
    profile: str,
    pending: dict[str, dict[str, str]],
) -> dict[str, Any]:
    if profile != "legacy-low-level" or "id" not in message:
        return message
    request = pending.pop(str(message["id"]), {})
    method = request.get("method", "")
    result = message.get("result")
    if not isinstance(result, dict):
        return message
    if method == "initialize":
        result["instructions"] = (
            "UE Agent Kit benchmark MCP view. Use only the tools exposed by tools/list; "
            "hidden profile capabilities are unavailable."
        )
    filtered = _filter_value(result)
    if isinstance(filtered, dict):
        message["result"] = filtered
    return message


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply an R4 benchmark profile to an MCP stdio server.")
    parser.add_argument("--profile", choices=("full-r0-r3", "legacy-low-level"), required=True)
    parser.add_argument("server_command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.server_command and args.server_command[0] == "--":
        args.server_command = args.server_command[1:]
    if not args.server_command:
        parser.error("a fixed MCP server command is required after --")
    return args


def _forward_stderr(stream: Any) -> None:
    for chunk in iter(lambda: stream.read(8192), b""):
        sys.stderr.buffer.write(chunk)
        sys.stderr.buffer.flush()


def _raw_stdin_lines() -> Any:
    pending = b""
    while True:
        chunk = os.read(sys.stdin.fileno(), 8192)
        if not chunk:
            break
        pending += chunk
        while b"\n" in pending:
            line, pending = pending.split(b"\n", 1)
            yield line + b"\n"
    if pending:
        yield pending


def main() -> int:
    args = _parse_args()
    server = subprocess.Popen(
        args.server_command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if server.stdin is None or server.stdout is None or server.stderr is None:
        raise RuntimeError("MCP proxy failed to open child stdio")
    pending: dict[str, dict[str, str]] = {}

    def server_to_client() -> None:
        for raw_line in server.stdout:
            line = raw_line
            try:
                message = json.loads(raw_line)
            except json.JSONDecodeError:
                pass
            else:
                filtered = filter_server_message(message, profile=args.profile, pending=pending)
                line = (json.dumps(filtered, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()

    output_thread = threading.Thread(target=server_to_client, name="mcp-profile-output", daemon=True)
    error_thread = threading.Thread(
        target=_forward_stderr,
        args=(server.stderr,),
        name="mcp-profile-stderr",
        daemon=True,
    )
    output_thread.start()
    error_thread.start()
    def client_to_server() -> None:
        try:
            for raw_line in _raw_stdin_lines():
                try:
                    message = json.loads(raw_line)
                except json.JSONDecodeError:
                    message = None
                if isinstance(message, dict) and "id" in message:
                    method = str(message.get("method") or "")
                    params = message.get("params") or {}
                    tool = str(params.get("name") or "") if isinstance(params, dict) else ""
                    if (
                        args.profile == "legacy-low-level"
                        and method == "tools/call"
                        and tool in HIDDEN_TOOLS
                    ):
                        denied = {
                            "jsonrpc": "2.0",
                            "id": message["id"],
                            "error": {
                                "code": -32601,
                                "message": "Tool is unavailable in this benchmark profile",
                            },
                        }
                        sys.stdout.buffer.write(
                            (
                                json.dumps(denied, ensure_ascii=False, separators=(",", ":"))
                                + "\n"
                            ).encode()
                        )
                        sys.stdout.buffer.flush()
                        continue
                    pending[str(message["id"])] = {"method": method, "tool": tool}
                server.stdin.write(raw_line)
                server.stdin.flush()
        except (BrokenPipeError, OSError):
            pass
        finally:
            try:
                server.stdin.close()
            except (BrokenPipeError, OSError):
                pass

    input_thread = threading.Thread(
        target=client_to_server,
        name="mcp-profile-input",
        daemon=True,
    )
    input_thread.start()
    try:
        return_code = server.wait()
    finally:
        if server.poll() is None:
            server.kill()
            server.wait(timeout=15)
        output_thread.join(timeout=5)
        error_thread.join(timeout=5)
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
