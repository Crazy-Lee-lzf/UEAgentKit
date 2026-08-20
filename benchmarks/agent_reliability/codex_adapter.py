from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .adapters import AgentAdapter, AgentRunRequest, AgentRunResult, UNAVAILABLE
from .codex_trace import parse_codex_jsonl
from .io import redact
from .profiles import HIGH_LEVEL_R0_R3_TOOLS


@dataclass(frozen=True)
class McpLaunchConfig:
    command: str
    args: tuple[str, ...]
    cwd: Path
    profile_proxy: Path | None = None
    startup_timeout_seconds: int = 30
    tool_timeout_seconds: int = 1800
    required: bool = True


def _toml_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _toml_array(values: tuple[str, ...]) -> str:
    return "[" + ",".join(_toml_string(value) for value in values) + "]"


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value.replace("\r\n", "\n").replace("\n", "\r\n"), encoding="utf-8", newline="")


class CodexCliAgentAdapter(AgentAdapter):
    def __init__(
        self,
        *,
        executable: str,
        model: str,
        reasoning_effort: str,
        service_tier: str,
        mcp: McpLaunchConfig,
        output_schema: Path,
        disabled_mcp_servers: tuple[str, ...] = ("wmux",),
    ) -> None:
        self.executable = executable
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.service_tier = service_tier
        self.mcp = mcp
        self.output_schema = output_schema.resolve()
        self.disabled_mcp_servers = disabled_mcp_servers

    def describe_runtime(self) -> dict[str, Any]:
        completed = subprocess.run(
            [self.executable, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return {
            "adapter": "codex-cli",
            "cliVersion": (completed.stdout or completed.stderr).strip() or UNAVAILABLE,
            "model": self.model,
            "modelSnapshot": UNAVAILABLE,
            "reasoningEffort": self.reasoning_effort,
            "serviceTier": self.service_tier,
            "temperature": "not-configurable",
            "maxOutputTokens": "not-configurable",
            "sessionIsolation": "codex-exec-ephemeral",
        }

    def _mcp_command(self, request: AgentRunRequest) -> tuple[str, tuple[str, ...]]:
        server_args = (*self.mcp.args, *request.mcp_arguments)
        if self.mcp.profile_proxy is None:
            return self.mcp.command, server_args
        return (
            os.fspath(Path(sys.executable)),
            (
                os.fspath(self.mcp.profile_proxy),
                "--profile",
                request.profile,
                "--",
                self.mcp.command,
                *server_args,
            ),
        )

    def _build_command(self, request: AgentRunRequest, session_root: Path, last_message: Path) -> list[str]:
        mcp_command, mcp_args = self._mcp_command(request)
        command = [
            self.executable,
            "exec",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "-C",
            os.fspath(session_root),
            "--model",
            self.model,
            "-c",
            f"model_reasoning_effort={_toml_string(self.reasoning_effort)}",
            "-c",
            f"service_tier={_toml_string(self.service_tier)}",
        ]
        for server in self.disabled_mcp_servers:
            command.extend(["-c", f"mcp_servers.{server}.enabled=false"])
        command.extend(
            [
                "-c",
                f"mcp_servers.ueagentkit.command={_toml_string(mcp_command)}",
                "-c",
                "mcp_servers.ueagentkit.enabled=true",
                "-c",
                f"mcp_servers.ueagentkit.required={str(self.mcp.required).lower()}",
                "-c",
                f"mcp_servers.ueagentkit.args={_toml_array(mcp_args)}",
                "-c",
                f"mcp_servers.ueagentkit.cwd={_toml_string(os.fspath(self.mcp.cwd))}",
                "-c",
                f"mcp_servers.ueagentkit.enabled_tools={_toml_array(request.visible_tools)}",
                "-c",
                f"mcp_servers.ueagentkit.startup_timeout_sec={self.mcp.startup_timeout_seconds}",
                "-c",
                f"mcp_servers.ueagentkit.tool_timeout_sec={self.mcp.tool_timeout_seconds}",
                "--output-schema",
                os.fspath(self.output_schema),
                "--json",
                "--output-last-message",
                os.fspath(last_message),
                "-",
            ]
        )
        return command

    @staticmethod
    def _terminate_tree(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
            )
        else:
            process.kill()

    def run(self, request: AgentRunRequest) -> AgentRunResult:
        request.output_dir.mkdir(parents=True, exist_ok=True)
        session_root = request.output_dir / "session"
        session_root.mkdir(parents=True, exist_ok=True)
        events_path = request.output_dir / "codex-events.jsonl"
        stderr_path = request.output_dir / "codex-stderr.txt"
        last_message_path = request.output_dir / "last-message.json"
        command = self._build_command(request, session_root, last_message_path)
        started = time.perf_counter_ns()
        process = subprocess.Popen(
            command,
            cwd=self.mcp.cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
        timed_out = False
        try:
            stdout, stderr = process.communicate(
                input=request.prompt,
                timeout=request.case["maxElapsedSeconds"],
            )
        except subprocess.TimeoutExpired:
            timed_out = True
            self._terminate_tree(process)
            stdout, stderr = process.communicate(timeout=15)
        elapsed_ms = round((time.perf_counter_ns() - started) / 1_000_000)
        _write_text(events_path, str(redact(stdout)))
        _write_text(stderr_path, str(redact(stderr)))
        parsed = parse_codex_jsonl(events_path)
        final_text = (
            last_message_path.read_text(encoding="utf-8", errors="replace")
            if last_message_path.is_file()
            else parsed["finalText"]
        )
        trace = parsed["trace"]
        calls_by_tool: dict[str, int] = {}
        for call in trace:
            name = str(call.get("tool") or call.get("kind") or "unknown")
            calls_by_tool[name] = calls_by_tool.get(name, 0) + 1
        usage = {
            **parsed["usage"],
            "toolCalls": len(trace),
            "toolCallsByTool": dict(sorted(calls_by_tool.items())),
            "highLevelToolCalls": sum(
                count for name, count in calls_by_tool.items() if name in HIGH_LEVEL_R0_R3_TOOLS
            ),
            "elapsedMs": elapsed_ms,
            "humanInterventions": 0,
            "agentRetries": sum("Reconnecting..." in message for message in parsed["diagnostics"]),
        }
        termination = {
            **parsed["termination"],
            "exitCode": process.returncode,
            "timedOut": timed_out,
        }
        if timed_out:
            termination.update({"status": "timeout", "reason": "runner-timeout"})
        elif process.returncode and termination["status"] == "unknown":
            termination.update({"status": "failed", "reason": "non-zero-exit"})
        runtime = {
            **self.describe_runtime(),
            "threadId": parsed["threadId"],
            "profile": request.profile,
            "promptFingerprint": request.prompt_fingerprint,
            "fixtureFingerprint": request.fixture_fingerprint,
            "visibleTools": list(request.visible_tools),
        }
        return AgentRunResult(
            runtime=runtime,
            final_text=str(redact(final_text)),
            trace=trace,
            usage=usage,
            termination=termination,
            raw_trace_path=events_path,
            diagnostics=parsed["diagnostics"],
        )
