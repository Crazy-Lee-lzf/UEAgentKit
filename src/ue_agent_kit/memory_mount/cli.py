"""Thin offline CLI for the R7 read-only PoC.

Examples (PYTHONPATH=src):

    py -3.12 -m ue_agent_kit.memory_mount verify <pack>
    py -3.12 -m ue_agent_kit.memory_mount query <pack> --query "契约审核" \
        --principal local-user --project-key UEAgentKit

The CLI never opens Project Memory or the coordination database.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

from .errors import MemoryMountError
from .limits import MountLimits
from .records import load_pack
from .service import MemoryMountRegistry


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass


def _print_json(value: Any, *, pretty: bool) -> None:
    if pretty:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
    else:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    print(text)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ue_agent_kit.memory_mount",
        description="Offline read-only R7 knowledge-pack verifier and query tool.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    verify = subparsers.add_parser("verify", help="Verify manifest, hash, records, receipt.")
    verify.add_argument("pack")
    verify.add_argument("--pretty", action="store_true")

    query = subparsers.add_parser("query", help="Mount one pack and run one query.")
    query.add_argument("pack")
    query.add_argument("--query", required=True)
    query.add_argument(
        "--principal",
        default="local-user",
        help="UNTRUSTED demo label; NOT authentication.",
    )
    query.add_argument(
        "--project-key",
        default="",
        help="UNTRUSTED demo project label; NOT an authenticated claim.",
    )
    query.add_argument("--cache-dir", default="")
    query.add_argument("--record-type", action="append", default=[])
    query.add_argument("--scope-type", default="")
    query.add_argument("--scope-key", default="")
    query.add_argument("--limit", type=int, default=20)
    query.add_argument("--include-conflicts", action="store_true")
    query.add_argument("--body-preview-chars", type=int, default=None)
    query.add_argument("--timeout-ms", type=int, default=None)
    query.add_argument("--pretty", action="store_true")
    return parser


def _verify(args: argparse.Namespace) -> int:
    limits = MountLimits()
    content = load_pack(args.pack, limits=limits)
    counts = Counter(record.record_type for record in content.records)
    payload = {
        "ok": True,
        "verified": True,
        "identity": {"trustMode": "offline-verifier", "verified": False},
        "pack": content.manifest.to_payload(),
        "integrity": content.integrity.to_payload(),
        "recordCount": content.record_count,
        "countsByType": dict(sorted(counts.items())),
    }
    _print_json(payload, pretty=args.pretty)
    return 0


def _query(args: argparse.Namespace) -> int:
    cache_context: tempfile.TemporaryDirectory[str] | None = None
    if args.cache_dir:
        cache_dir = Path(args.cache_dir)
    else:
        cache_context = tempfile.TemporaryDirectory(prefix="r7-mount-cache-")
        cache_dir = Path(cache_context.name)
    registry: MemoryMountRegistry | None = None
    try:
        registry = MemoryMountRegistry.for_demo(
            principal=args.principal,
            project_key=args.project_key,
            cache_dir=cache_dir,
        )
        print(
            "warning: r7-untrusted-demo-identity "
            "(--principal/--project-key are not authentication)",
            file=sys.stderr,
        )
        mount_info = registry.mount(args.pack)
        result = registry.query(
            args.query,
            record_types=tuple(args.record_type),
            scope_type=args.scope_type,
            scope_key=args.scope_key,
            limit=args.limit,
            include_conflicts=args.include_conflicts,
            body_preview_chars=args.body_preview_chars,
            timeout_ms=args.timeout_ms,
        )
        payload = {
            "ok": True,
            "identity": {
                "trustMode": "untrusted-demo",
                "verified": False,
                "warning": "r7-untrusted-demo-identity",
            },
            "mount": mount_info.to_payload(),
            "result": result.to_payload(),
        }
        _print_json(payload, pretty=args.pretty)
        return 0
    finally:
        if registry is not None:
            registry.close()
        if cache_context is not None:
            cache_context.cleanup()


def main(argv: Sequence[str] | None = None) -> int:
    _configure_stdio()
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            return _verify(args)
        if args.command == "query":
            return _query(args)
        parser.error(f"unknown command: {args.command}")
        return 2
    except MemoryMountError as exc:
        _print_json(exc.to_payload(), pretty=getattr(args, "pretty", False))
        print(f"{exc.code.value}: {exc.message}", file=sys.stderr)
        return exc.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
