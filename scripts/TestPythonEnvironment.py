from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import tomllib
from pathlib import Path


def main() -> int:
    tool_root = Path(__file__).resolve().parents[1]
    pyproject_path = tool_root / "pyproject.toml"

    if not ((3, 11) <= sys.version_info[:2] < (3, 13)):
        raise RuntimeError(f"Unsupported Python version: {sys.version}")

    if sys.prefix == sys.base_prefix:
        raise RuntimeError("Validation must run inside the project virtual environment.")

    with pyproject_path.open("rb") as stream:
        pyproject = tomllib.load(stream)

    requires_python = pyproject["project"]["requires-python"]
    if requires_python != ">=3.11,<3.13":
        raise RuntimeError(f"Unexpected requires-python value: {requires_python}")

    with tempfile.TemporaryDirectory(prefix="bct_") as temporary_root:
        unicode_root = Path(temporary_root) / "中文路径" / "空格 directory"
        unicode_root.mkdir(parents=True)
        database_path = unicode_root / "索引.sqlite3"

        connection = sqlite3.connect(database_path)
        try:
            connection.execute("CREATE TABLE sample (name TEXT NOT NULL)")
            connection.execute("INSERT INTO sample(name) VALUES (?)", ("中文项目",))
            value = connection.execute("SELECT name FROM sample").fetchone()[0]
            if value != "中文项目":
                raise RuntimeError("SQLite Unicode round-trip failed.")
        finally:
            connection.close()

        payload = {"项目": "中文项目", "路径": str(unicode_root)}
        encoded = json.dumps(payload, ensure_ascii=False)
        decoded = json.loads(encoded)
        if decoded != payload:
            raise RuntimeError("JSON Unicode round-trip failed.")

    result = {
        "python": sys.executable,
        "version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "virtual_environment": True,
        "sqlite": sqlite3.sqlite_version,
        "unicode": "ok",
        "tool_root": str(tool_root),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
