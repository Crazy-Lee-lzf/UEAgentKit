from __future__ import annotations

import abc
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .io import fingerprint_json, sha256_file


PACKAGE_SUFFIXES = {".uasset", ".ubulk", ".uexp", ".umap", ".uptnl"}


def capture_package_inventory(root: Path) -> dict[str, dict[str, Any]]:
    root = root.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Fixture inventory root does not exist: {root}")
    inventory: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_file() and path.suffix.lower() in PACKAGE_SUFFIXES:
            relative = path.relative_to(root).as_posix()
            inventory[relative] = {"sha256": sha256_file(path), "bytes": path.stat().st_size}
    return inventory


def capture_tree_digest(root: Path, pattern: str = "*") -> dict[str, Any]:
    root = root.resolve()
    files = {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in sorted(root.rglob(pattern), key=lambda item: item.as_posix())
        if path.is_file()
    }
    return {"files": files, "fingerprint": fingerprint_json(files)}


@dataclass
class FixtureSession:
    case_id: str
    setup_id: str
    cleanup_id: str
    attempt_root: Path
    before: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class FixtureAdapter(abc.ABC):
    @abc.abstractmethod
    def setup(self, case: dict[str, Any], attempt_root: Path) -> FixtureSession:
        raise NotImplementedError

    @abc.abstractmethod
    def capture_after(
        self,
        case: dict[str, Any],
        session: FixtureSession,
        agent_result: Any,
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abc.abstractmethod
    def cleanup(self, case: dict[str, Any], session: FixtureSession) -> dict[str, Any]:
        raise NotImplementedError

    def mcp_arguments(self, case: dict[str, Any], session: FixtureSession) -> tuple[str, ...]:
        return tuple(session.metadata.get("mcpArguments") or ())


SetupHook = Callable[[dict[str, Any], Path], FixtureSession]
CaptureHook = Callable[[dict[str, Any], FixtureSession, Any], dict[str, Any]]
CleanupHook = Callable[[dict[str, Any], FixtureSession], dict[str, Any]]


class RegisteredFixtureAdapter(FixtureAdapter):
    """Dispatch only to code-registered hooks; case JSON can never execute commands."""

    def __init__(
        self,
        *,
        setup_hooks: dict[str, SetupHook],
        capture_hooks: dict[str, CaptureHook],
        cleanup_hooks: dict[str, CleanupHook],
    ) -> None:
        self.setup_hooks = dict(setup_hooks)
        self.capture_hooks = dict(capture_hooks)
        self.cleanup_hooks = dict(cleanup_hooks)

    def setup(self, case: dict[str, Any], attempt_root: Path) -> FixtureSession:
        setup_id = case["setupId"]
        if setup_id not in self.setup_hooks or setup_id not in self.capture_hooks:
            raise ValueError(f"Unregistered fixture setup hook: {setup_id}")
        return self.setup_hooks[setup_id](case, attempt_root)

    def capture_after(
        self,
        case: dict[str, Any],
        session: FixtureSession,
        agent_result: Any,
    ) -> dict[str, Any]:
        return self.capture_hooks[session.setup_id](case, session, agent_result)

    def cleanup(self, case: dict[str, Any], session: FixtureSession) -> dict[str, Any]:
        cleanup_id = case["cleanupId"]
        if cleanup_id not in self.cleanup_hooks:
            raise ValueError(f"Unregistered fixture cleanup hook: {cleanup_id}")
        return self.cleanup_hooks[cleanup_id](case, session)
