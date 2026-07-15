from __future__ import annotations

import os
from pathlib import Path


TOOL_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE = Path(
    os.environ.get("BCT_DATABASE", TOOL_ROOT / ".data" / "blueprint_context.sqlite3")
).expanduser().resolve()
