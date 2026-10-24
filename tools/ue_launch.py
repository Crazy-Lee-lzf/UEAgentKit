"""Launch UnrealEditor-Cmd minimized; never hidden and never touch other processes.

The harness uses STARTUPINFO/SW_SHOWMINIMIZED for the child and, because UE
Slate may create a top-level window after process startup, a bounded
EnumWindows monitor that minimizes only windows owned by that exact child PID.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
import time
from typing import Any

SW_SHOWMINIMIZED = 2
SW_MINIMIZE = 6
STARTF_USESHOWWINDOW = 0x1

_MONITOR_STATES: dict[int, dict[str, Any]] = {}
_MONITOR_LOCK = threading.Lock()


def _startupinfo() -> subprocess.STARTUPINFO | None:
    if os.name != "nt":
        return None
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = SW_SHOWMINIMIZED
    return info


def _monitor(process: subprocess.Popen[Any]) -> None:
    state: dict[str, Any] = {
        "pid": process.pid,
        "found": 0,
        "minimized": 0,
        "error": "",
        "boundedSeconds": 300.0,
    }
    with _MONITOR_LOCK:
        _MONITOR_STATES[process.pid] = state
    if os.name != "nt":
        return
    started = time.monotonic()
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(
            ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p
        )

        def visit(hwnd: int, _lparam: int) -> bool:
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == process.pid and user32.IsWindowVisible(hwnd):
                state["found"] += 1
                user32.ShowWindow(hwnd, SW_MINIMIZE)
                state["minimized"] += 1
            return True

        callback = callback_type(visit)
        while process.poll() is None and time.monotonic() - started < 300.0:
            user32.EnumWindows(callback, 0)
            time.sleep(0.2)
    except Exception as exc:  # pragma: no cover - platform dependent
        state["error"] = f"{type(exc).__name__}: {exc}"


def popen_minimized(args: Any, **kwargs: Any) -> subprocess.Popen[Any]:
    """Popen with a minimized child window, plus a PID-scoped minimize monitor."""
    kwargs.setdefault("startupinfo", _startupinfo())
    process = subprocess.Popen(args, **kwargs)
    if os.name == "nt":
        threading.Thread(target=_monitor, args=(process,), daemon=True).start()
    return process


def monitor_state(pid: int) -> dict[str, Any]:
    with _MONITOR_LOCK:
        return dict(_MONITOR_STATES.get(pid, {}))
