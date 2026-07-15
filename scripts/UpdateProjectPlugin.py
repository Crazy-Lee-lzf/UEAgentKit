from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path


TOOL_ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Enable or disable an Unreal plugin in a .uproject file.")
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--plugin", default="UEAgentKit")
    parser.add_argument("--enabled", choices=("true", "false"), required=True)
    parser.add_argument("--backup-root", type=Path, default=TOOL_ROOT / "Backups" / "ProjectFiles")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_path = args.project.expanduser().resolve()
    if not project_path.is_file() or project_path.suffix.lower() != ".uproject":
        raise FileNotFoundError(f"Unreal project file not found: {project_path}")

    enabled = args.enabled == "true"
    original_bytes = project_path.read_bytes()
    before_hash = hashlib.sha256(original_bytes).hexdigest()
    data = json.loads(original_bytes.decode("utf-8-sig"))

    plugins = data.setdefault("Plugins", [])
    if not isinstance(plugins, list):
        raise TypeError("The .uproject Plugins field must be an array.")

    existing = next((entry for entry in plugins if isinstance(entry, dict) and entry.get("Name") == args.plugin), None)
    changed = False
    if existing is None:
        entry: dict[str, object] = {"Name": args.plugin, "Enabled": enabled}
        if enabled:
            entry["TargetAllowList"] = ["Editor"]
        plugins.append(entry)
        changed = True
    else:
        if existing.get("Enabled") is not enabled:
            existing["Enabled"] = enabled
            changed = True
        if enabled and "TargetAllowList" not in existing:
            existing["TargetAllowList"] = ["Editor"]
            changed = True

    backup_path: Path | None = None
    if changed:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_directory = args.backup_root.expanduser().resolve() / project_path.stem / timestamp
        backup_directory.mkdir(parents=True, exist_ok=False)
        backup_path = backup_directory / project_path.name
        shutil.copy2(project_path, backup_path)

        backup_hash = sha256(backup_path)
        if backup_hash != before_hash:
            raise RuntimeError("Backup hash does not match the source .uproject file.")

        serialized = json.dumps(data, ensure_ascii=False, indent="\t") + "\n"
        output_bytes = serialized.replace("\n", "\r\n").encode("utf-8")

        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{project_path.stem}.", suffix=".tmp", dir=project_path.parent
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(file_descriptor, "wb") as stream:
                stream.write(output_bytes)
                stream.flush()
                os.fsync(stream.fileno())
            json.loads(temporary_path.read_text(encoding="utf-8"))
            os.replace(temporary_path, project_path)
        finally:
            temporary_path.unlink(missing_ok=True)

    result = {
        "project": str(project_path),
        "plugin": args.plugin,
        "enabled": enabled,
        "changed": changed,
        "backup": str(backup_path) if backup_path else "",
        "before_sha256": before_hash,
        "after_sha256": sha256(project_path),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
