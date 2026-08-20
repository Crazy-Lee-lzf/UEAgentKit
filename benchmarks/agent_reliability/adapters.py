from __future__ import annotations

import abc
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class AgentRunRequest:
    case: dict[str, Any]
    profile: str
    attempt_index: int
    visible_tools: tuple[str, ...]
    prompt: str
    prompt_fingerprint: str
    fixture_fingerprint: str
    output_dir: Path
    mcp_arguments: tuple[str, ...] = ()


@dataclass
class AgentRunResult:
    runtime: dict[str, Any]
    final_text: str
    trace: list[dict[str, Any]]
    usage: dict[str, Any]
    termination: dict[str, Any]
    raw_trace_path: Path | None = None
    diagnostics: list[str] = field(default_factory=list)


class AgentAdapter(abc.ABC):
    @abc.abstractmethod
    def describe_runtime(self) -> dict[str, Any]:
        raise NotImplementedError

    @abc.abstractmethod
    def run(self, request: AgentRunRequest) -> AgentRunResult:
        raise NotImplementedError

    def close(self) -> None:
        return None


class ImportedAgentRunAdapter(AgentAdapter):
    """Load immutable, structured attempts exported by a real Agent harness."""

    def __init__(self, import_root: Path) -> None:
        self.import_root = import_root.resolve()

    def describe_runtime(self) -> dict[str, Any]:
        return {
            "adapter": "imported-agent-run",
            "importRoot": self.import_root.as_posix(),
            "model": UNAVAILABLE,
        }

    def run(self, request: AgentRunRequest) -> AgentRunResult:
        source = (
            self.import_root
            / request.case["caseId"]
            / request.profile
            / f"attempt-{request.attempt_index:03d}.json"
        ).resolve()
        if self.import_root not in source.parents:
            raise ValueError("Imported attempt escaped the configured import root")
        payload = json.loads(source.read_text(encoding="utf-8"))
        if payload.get("caseId") != request.case["caseId"]:
            raise ValueError(f"Imported caseId mismatch: {source}")
        if payload.get("profile") != request.profile:
            raise ValueError(f"Imported profile mismatch: {source}")
        if int(payload.get("attemptIndex", 0)) != request.attempt_index:
            raise ValueError(f"Imported attemptIndex mismatch: {source}")
        provenance = payload.get("provenance")
        if not isinstance(provenance, dict) or not provenance.get("rawTraceSha256"):
            raise ValueError(f"Imported attempt lacks immutable trace provenance: {source}")
        return AgentRunResult(
            runtime=dict(payload.get("runtime") or {}),
            final_text=str(payload.get("finalText") or ""),
            trace=list(payload.get("trace") or []),
            usage=dict(payload.get("usage") or {}),
            termination=dict(payload.get("termination") or {}),
            raw_trace_path=source,
            diagnostics=list(payload.get("diagnostics") or []),
        )
