"""Open the P11 disposable fixture in a real Blueprint Editor (GUI run only).

Launched with `-ExecutePythonScript=<this file>`.  Asset editors cannot be
created during engine init (the Blueprint preview scene does not exist yet), so
the open is deferred to a Slate post-tick callback after the editor loop has
been running for a moment.  It never modifies the asset.
"""

from __future__ import annotations

import unreal

ASSET = "/Game/UEAgentKitP11/BP_LayoutP11"
DELAY_TICKS = 180

_state = {"ticks": 0, "opened": False, "handle": None}


def _open() -> None:
    asset = unreal.load_asset(ASSET)
    if asset is None:
        unreal.log_error(f"P11 fixture missing: {ASSET}")
        return
    subsystem = unreal.get_editor_subsystem(unreal.AssetEditorSubsystem)
    if subsystem is None:
        unreal.log_error("P11 AssetEditorSubsystem unavailable")
        return
    subsystem.open_editor_for_assets([asset])
    unreal.log("P11 fixture opened in the Blueprint Editor")


def _tick(delta_seconds: float) -> None:
    _state["ticks"] += 1
    if _state["opened"] or _state["ticks"] < DELAY_TICKS:
        return
    _state["opened"] = True
    handle = _state["handle"]
    if handle is not None:
        unreal.unregister_slate_post_tick_callback(handle)
    _open()


_state["handle"] = unreal.register_slate_post_tick_callback(_tick)
unreal.log("P11 fixture opener scheduled")
