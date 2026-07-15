from __future__ import annotations

import sys
from pathlib import Path


TOOL_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from blueprint_context_tool.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
