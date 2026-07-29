from __future__ import annotations

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
