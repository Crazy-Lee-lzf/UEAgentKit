#!/usr/bin/env python3
"""Manually start the M1 layout preview broker (offline, loopback only).

This is the only supported entry point.  It never starts itself, never talks to
the Editor bridge, and never spawns a child process.  It prints one JSON status
line to stdout (no token, no descriptor path) and serves until Ctrl+C,
SIGTERM, or ``--run-seconds`` expires.

Example (disposable host only):

    py -3.12 tools/run_layout_broker.py ^
        --descriptor Build/layout-broker/Evidence/LayoutBroker.json ^
        --project-name P11AutomationHost ^
        --project-path C:/Projects/P11AutomationHost/P11AutomationHost.uproject
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ue_agent_kit.layout_broker import protocol  # noqa: E402
from ue_agent_kit.layout_broker.descriptor import (  # noqa: E402
    build_project_path_hash,
)
from ue_agent_kit.layout_broker.server import LayoutBrokerServer  # noqa: E402


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="M1 layout preview broker (offline, loopback only)")
    parser.add_argument("--descriptor", type=Path, required=True, help="descriptor output path")
    parser.add_argument("--project-name", required=True)
    identity = parser.add_mutually_exclusive_group(required=True)
    identity.add_argument("--project-path", type=Path, help="absolute .uproject path")
    identity.add_argument("--project-path-hash", help="precomputed sha1:<40 hex>")
    parser.add_argument("--expect-session-id", required=True)
    parser.add_argument(
        "--rate-limit",
        type=int,
        default=protocol.DEFAULT_RATE_LIMIT,
        help="valid requests per session per 60 s window",
    )
    parser.add_argument(
        "--allow-unverified-acl",
        action="store_true",
        help="continue when descriptor permissions cannot be proven (disposable host only)",
    )
    parser.add_argument(
        "--run-seconds",
        type=float,
        default=None,
        help="bounded lifetime for an evidence run; omit to serve until interrupted",
    )
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )
    try:
        if args.project_path is not None:
            if args.project_path.suffix.casefold() != ".uproject":
                raise ValueError("--project-path must point at a .uproject file")
            project_path_hash = build_project_path_hash(str(args.project_path))
        else:
            project_path_hash = str(args.project_path_hash)
        if not protocol._PROJECT_HASH_RE.match(project_path_hash):  # noqa: SLF001
            raise ValueError("project path hash must be sha1:<40 lowercase hex>")

        server = LayoutBrokerServer(
            descriptor_path=str(args.descriptor),
            project_name=args.project_name,
            project_path_hash=project_path_hash,
            expect_session_id=args.expect_session_id,
            rate_limit=args.rate_limit,
            allow_unverified_acl=args.allow_unverified_acl,
        )
        server.start()
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        code = getattr(exc, "code", "layout-broker-startup-failed")
        message = getattr(exc, "message", str(exc))
        print(
            json.dumps(
                {
                    "schemaVersion": "ueak.layoutbroker-run/1.0",
                    "ok": False,
                    "error": {"code": code, "message": protocol.sanitize_message(message)},
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return 2

    print(json.dumps(server.status(), sort_keys=True), flush=True)

    stop_signal = threading.Event()

    def _handle_signal(signum: int, frame: object) -> None:
        del signum, frame
        stop_signal.set()

    previous_handler = None
    try:
        previous_handler = signal.signal(signal.SIGTERM, _handle_signal)
    except (ValueError, OSError):
        previous_handler = None

    try:
        if args.run_seconds is not None:
            deadline = max(0.0, float(args.run_seconds))
            stop_signal.wait(deadline)
        else:
            while not stop_signal.wait(0.5):
                pass
    except KeyboardInterrupt:
        pass
    finally:
        if previous_handler is not None:
            try:
                signal.signal(signal.SIGTERM, previous_handler)
            except (ValueError, OSError):
                pass
        final = server.status()
        server.stop()
        logging.getLogger("ueak.layout_broker").info(
            "broker run summary: completed=%s rejected=%s timedOut=%s cancelled=%s",
            final.get("completed"),
            final.get("rejected"),
            final.get("timedOut"),
            final.get("cancelled"),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
