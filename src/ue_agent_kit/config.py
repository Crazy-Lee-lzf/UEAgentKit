from __future__ import annotations

import json
import os
from pathlib import Path


TOOL_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE = Path(
    os.environ.get("UEAK_DATABASE", TOOL_ROOT / ".data" / "ue_agent_kit.sqlite3")
).expanduser().resolve()

DEFAULT_MEMORY_DATABASE = Path(
    os.environ.get(
        "UEAK_MEMORY_DATABASE",
        TOOL_ROOT / ".data" / "ue_agent_kit_memory.sqlite3",
    )
).expanduser().resolve()

PROJECT_POLICIES_DIR = TOOL_ROOT / "config" / "projects"
PROJECT_POLICY_MANIFEST = PROJECT_POLICIES_DIR / "manifest.json"
PROJECT_POLICY_MANIFEST_SCHEMA_VERSION = "1.0"


def _load_json_object(path: Path) -> dict:
    """Read one JSON object file, rejecting a non-object root.

    Raises FileNotFoundError when the file is missing and ValueError when the
    content is malformed or not a JSON object.
    """
    value = json.loads(path.expanduser().resolve().read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def resolve_project_policy(
    project_path: Path | str | None,
    *,
    profile: str | None = None,
    manifest_path: Path | None = None,
) -> Path | None:
    """Resolve the project-level Write Policy for a .uproject file.

    The mapping is (project name, optional profile) -> policy file, declared in
    ``config/projects/manifest.json``. The project name is the .uproject file
    stem, matching the ``allowedProjectNames`` and Revision Export ``projectName``
    convention used by the write workflow.

    Returns:
        The absolute policy file path, or ``None`` when there is no project-level
        mapping (no manifest, or the project is not listed). Callers fall back to
        an explicit ``--policy`` in that case.

    Raises:
        ValueError: the project path is not a .uproject file, the manifest is
            malformed, the project entry or selected profile is missing a policy
            file, or the resolved policy file does not exist on disk.
    """
    if project_path is None:
        return None
    path = Path(project_path).expanduser().resolve()
    if path.suffix.lower() != ".uproject":
        raise ValueError(f"Project path must point to a .uproject file: {path}")
    project_name = path.stem

    manifest_file = (manifest_path or PROJECT_POLICY_MANIFEST).expanduser().resolve()
    if not manifest_file.is_file():
        return None

    manifest = _load_json_object(manifest_file)
    projects = manifest.get("projects")
    if not isinstance(projects, dict):
        raise ValueError(
            f"Project policy manifest has no projects object: {manifest_file}"
        )
    entry = projects.get(project_name)
    if entry is None:
        return None
    if not isinstance(entry, dict):
        raise ValueError(
            f"Project policy manifest entry must be an object: {project_name}"
        )

    filename: object = None
    if profile:
        profiles = entry.get("profiles")
        if not isinstance(profiles, dict) or profile not in profiles:
            raise ValueError(
                f"Unknown policy profile {profile!r} for project {project_name!r}."
            )
        filename = profiles[profile]
    else:
        filename = entry.get("default")
    if not isinstance(filename, str) or not filename:
        raise ValueError(
            f"Project {project_name!r} has no resolvable policy file in the manifest."
        )

    policy_path = (manifest_file.parent / filename).resolve()
    if not policy_path.is_file():
        raise ValueError(f"Project policy file does not exist: {policy_path}")
    return policy_path
