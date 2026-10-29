"""Curate a release using only reviewed Git blobs (never walk the worktree).

Dry-run by default. This intentionally excludes ignored/untracked files entirely:
in particular linked-worktree .git metadata, virtualenvs, build outputs, and
external tool installations must never be enumerated as release extras.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
from pathlib import Path, PurePosixPath

BLOCKED_COMPONENTS = frozenset(
    {".git", ".venv", "autosdk", "build", "output", "saved",
     "intermediate", "deriveddatacache", "node_modules", ".cache"}
)
DOC_OVERRIDES = frozenset({"README.md", "README_EN.md", "CHANGELOG.md", ".gitignore"})
MAX_BLOB_BYTES = 64 * 1024 * 1024


class UnsafeReleasePath(ValueError):
    """A manifest/release path cannot be safely used."""


def validate_relative(path: str) -> Path:
    if not isinstance(path, str) or not path or "\\" in path or "\0" in path:
        raise UnsafeReleasePath("Invalid release path")
    p = PurePosixPath(path)
    if p.is_absolute() or str(p) != path or any(
        c in {"", ".", ".."} or c.lower() in BLOCKED_COMPONENTS or ":" in c
        for c in p.parts
    ):
        raise UnsafeReleasePath(f"Forbidden release path: {path}")
    return Path(*p.parts)


def reject_reparse(path: Path) -> None:
    """Reject symlinks, Windows Junctions and any other reparse point in ancestry."""
    for candidate in (path, *path.parents):
        try:
            attrs = candidate.lstat()
        except FileNotFoundError:
            continue
        if (
            stat.S_ISLNK(attrs.st_mode)
            or bool(getattr(attrs, "st_file_attributes", 0) & 0x400)
            or candidate.is_junction()
        ):
            raise UnsafeReleasePath(f"Reparse point is prohibited: {candidate}")


def checked_destination(root: Path, path: str) -> Path:
    suffix = validate_relative(path)
    dest = root / suffix
    reject_reparse(dest)
    if os.path.commonpath([str(root), str(dest)]) != str(root):
        raise UnsafeReleasePath("Target is outside release worktree")
    return dest


def git(repo: Path, *args: str) -> bytes:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode:
        raise RuntimeError(
            f"Git command failed ({args[0]}): {proc.stderr.decode(errors='replace')[:500]}"
        )
    return proc.stdout


def tree_blobs(repo: Path, ref: str) -> dict[str, tuple[str, str]]:
    output: dict[str, tuple[str, str]] = {}
    for rec in git(repo, "ls-tree", "-rz", "--full-tree", ref).split(b"\0"):
        if not rec:
            continue
        head, raw_path = rec.split(b"\t", 1)
        mode, kind, sha = head.decode("ascii").split(" ")
        path = raw_path.decode("utf-8", "strict")
        if kind not in {"blob", "commit"}:
            raise UnsafeReleasePath(f"Unsupported object type: {path}")
        output[path] = (mode, sha)
    return output


def build_plan(
    root: Path,
    manifest: dict,
    source_tree: dict[str, tuple[str, str]],
    docs_tree: dict[str, tuple[str, str]],
    tracked: set[str],
    review_keep: set[str],
) -> tuple[list[tuple[str, Path, str]], list[Path]]:
    entries = manifest["files"]
    indexed = {entry["path"]: entry for entry in entries}
    if len(indexed) != len(entries):
        raise ValueError("Manifest has duplicate entries")
    if any(indexed.get(p, {}).get("status") != "NEEDS_REVIEW" for p in review_keep):
        raise ValueError("Only explicitly reviewed files can be manually included")
    changes: list[tuple[str, Path, str]] = []
    deletes: list[Path] = []
    for name, entry in sorted(indexed.items()):
        status = entry["status"]
        if status not in {"KEEP", "EXCLUDE", "NEEDS_REVIEW"}:
            raise ValueError(f"Unrecognized manifest status: {status}")
        dest = checked_destination(root, name)
        if status == "EXCLUDE":
            if name in tracked:
                deletes.append(dest)
            continue
        if status == "NEEDS_REVIEW" and name not in review_keep:
            if name in tracked:
                raise ValueError(f"Tracked file needs explicit review: {name}")
            continue
        preferred = docs_tree if name.startswith("docs/") or name in DOC_OVERRIDES else source_tree
        alternate = source_tree if preferred is docs_tree else docs_tree
        blob = preferred.get(name) or alternate.get(name)
        if not blob:
            raise ValueError(f"Requested public file absent from source Git tree: {name}")
        mode, sha = blob
        if mode not in {"100644", "100755"}:
            raise UnsafeReleasePath(f"Source Git entry is not a regular file: {name} ({mode})")
        if name not in tracked and dest.exists():
            raise UnsafeReleasePath(f"Refusing to overwrite an untracked/ignored file: {name}")
        changes.append((name, dest, sha))
    unknown = tracked - indexed.keys()
    if unknown:
        raise ValueError(f"Tracked files missing from reviewed manifest: {sorted(unknown)[:12]}")
    return changes, deletes


def curate(
    repo: Path,
    manifest_path: Path,
    source_ref: str,
    docs_ref: str,
    review_keep: set[str],
    apply: bool,
) -> dict:
    root = repo.absolute()
    reject_reparse(root)
    if Path(git(root, "rev-parse", "--show-toplevel").decode().strip()).resolve() != root.resolve():
        raise ValueError("Working-tree root mismatch")
    if git(root, "status", "--porcelain", "-z"):
        raise ValueError("Release worktree must be clean")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    source = tree_blobs(root, source_ref)
    docs = tree_blobs(root, docs_ref)
    tracked = {x.decode("utf-8") for x in git(root, "ls-files", "-z").split(b"\0") if x}
    changes, deletes = build_plan(root, manifest, source, docs, tracked, review_keep)

    # Validate all data and destination paths BEFORE the first write.
    data: list[tuple[Path, bytes]] = []
    for name, dest, sha in changes:
        payload = git(root, "cat-file", "blob", sha)
        if len(payload) > MAX_BLOB_BYTES:
            raise ValueError(f"Blob exceeds per-file size limit: {name}")
        data.append((dest, payload))
    if apply:
        for dest, payload in data:
            checked_destination(root, dest.relative_to(root).as_posix())
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(payload)
        for dest in deletes:
            checked_destination(root, dest.relative_to(root).as_posix())
            dest.unlink(missing_ok=True)
    return {
        "applied": apply,
        "sourceRef": source_ref,
        "docsRef": docs_ref,
        "gitBlobFiles": len(changes),
        "trackedExclusions": len(deletes),
        "explicitLocalReviewFiles": sorted(review_keep),
        "ignoredAndUntracked": "never scanned or deleted",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--source-ref", required=True)
    parser.add_argument("--docs-ref", required=True)
    parser.add_argument("--review-keep", action="append", default=[])
    parser.add_argument("--apply", action="store_true", help="write; otherwise dry-run")
    args = parser.parse_args()
    print(json.dumps(
        curate(
            args.repo, args.manifest, args.source_ref, args.docs_ref,
            set(args.review_keep), args.apply,
        ),
        ensure_ascii=False, indent=2,
    ))


if __name__ == "__main__":
    main()
