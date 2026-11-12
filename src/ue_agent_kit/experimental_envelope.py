"""Shared helpers for the opt-in experimental writers.

0-3.4: the MCP tools return an envelope (``{"schemaVersion": "1.0", ..., "result":
payload}``) while the writer modules validate and read the payload itself.
Requiring a caller to know which of the two to pass was a contract trap with no
safety value, so both are accepted.
"""

from __future__ import annotations

from typing import Any


def unwrap_tool_result(value: Any, marker: str) -> Any:
    """Return the payload from either the raw payload or the MCP tool envelope.

    A dict carrying ``marker`` is used as-is; otherwise its ``result`` is used
    when that carries the marker.  Anything else is returned unchanged so the
    caller's existing validation produces its own precise error.
    """

    if not isinstance(value, dict):
        return value
    if marker in value:
        return value
    inner = value.get("result")
    if isinstance(inner, dict) and marker in inner:
        return inner
    return value
