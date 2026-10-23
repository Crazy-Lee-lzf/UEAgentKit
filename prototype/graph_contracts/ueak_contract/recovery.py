"""Persistent Recovery Marker and Save/Apply blocking.

Clause IDs: TX-RECOVERY-MARKER, TX-SAVE-GATE.

A marker is not a return value: it is a file on disk under the Work Root.  Its
presence blocks (a) ``ue_save_authorized_asset``, (b) any GraphPatch Apply,
(c) any layout write-back for the same asset, until a human clears it with
matching recovery evidence.  This is the persistent part of CR-TX-02.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .canonical import DOMAIN_ARTIFACT, is_sha256, short_digest
from .errors import ContractError
from .ids import is_asset_path

MARKER_VERSION = "ueak.recovery-marker/1.0"
MARKER_DIRNAME = "graphpatch-recovery"
BLOCKING_STATUSES = ("pending-recovery", "manual-recovery")


def asset_key(project_name: str, asset_path: str) -> str:
    if not project_name or not is_asset_path(asset_path):
        raise ValueError("A valid projectName and /Game/... assetPath are required")
    return short_digest({"projectName": project_name, "assetPath": asset_path}, DOMAIN_ARTIFACT, 16)


def marker_path(root: Path | str, project_name: str, asset_path: str) -> Path:
    return Path(root) / MARKER_DIRNAME / (asset_key(project_name, asset_path) + ".json")


def make_marker(
    *,
    project_name: str,
    asset_path: str,
    patch_id: str,
    transaction_id: str,
    base_revision: str,
    base_graph_hash: str,
    unresolved_operations: list[dict[str, Any]] | None = None,
    evidence: dict[str, Any] | None = None,
    created_at_utc: str | None = None,
) -> dict[str, Any]:
    if not is_sha256(base_graph_hash):
        raise ValueError("base_graph_hash must be sha256:<64 hex>")
    return {
        "schemaVersion": MARKER_VERSION,
        "projectName": project_name,
        "assetPath": asset_path,
        "patchId": patch_id,
        "transactionId": transaction_id,
        "status": "pending-recovery",
        "baseRevision": base_revision,
        "baseGraphHash": base_graph_hash,
        "createdAtUtc": created_at_utc or datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "unresolvedOperations": list(unresolved_operations or []),
        "evidence": dict(evidence or {}),
        "clearPolicy": {
            "requiresUserConfirmation": True,
            "requiresRecoveryEvidence": True,
            "requiresRestoredBaseGraphHash": True,
            "acceptDiverged": False,
        },
    }


class RecoveryStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def path_for(self, project_name: str, asset_path: str) -> Path:
        return marker_path(self.root, project_name, asset_path)

    def load(self, project_name: str, asset_path: str) -> dict[str, Any] | None:
        path = self.path_for(project_name, asset_path)
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def is_pending(self, project_name: str, asset_path: str) -> bool:
        marker = self.load(project_name, asset_path)
        return bool(marker and marker.get("status") in BLOCKING_STATUSES)

    def write(self, marker: dict[str, Any]) -> Path:
        if marker.get("schemaVersion") != MARKER_VERSION:
            raise ValueError("Unsupported recovery marker version")
        path = self.path_for(str(marker["projectName"]), str(marker["assetPath"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".marker-", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(marker, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return path

    def clear(
        self,
        project_name: str,
        asset_path: str,
        *,
        user_confirmed: bool,
        recovery_evidence_sha256: str,
        restored_graph_hash: str,
        accept_diverged: bool = False,
    ) -> bool:
        """Clear only with explicit confirmation + matching evidence + restored hash.

        ``accept_diverged`` is intentionally ineffective in this candidate: the
        D-10 decision is unapproved, so the strict default is the only allowed
        behaviour and a diverged graph raises ``graphpatch-recovery-clear-denied``.
        """

        marker = self.load(project_name, asset_path)
        if marker is None:
            return False
        if not user_confirmed:
            raise ContractError("graphpatch-recovery-clear-denied", "User confirmation is required to clear a recovery marker")
        evidence = marker.get("evidence") if isinstance(marker.get("evidence"), dict) else {}
        expected = evidence.get("recoveryEvidenceSha256", "")
        if not expected or not is_sha256(recovery_evidence_sha256) or recovery_evidence_sha256 != expected:
            raise ContractError(
                "graphpatch-recovery-clear-denied",
                "Recovery evidence does not match the marker",
                context={"expected": expected, "actual": recovery_evidence_sha256},
            )
        if restored_graph_hash != marker.get("baseGraphHash"):
            raise ContractError(
                "graphpatch-recovery-clear-denied",
                "Graph hash was not restored to the marker's baseGraphHash",
                context={"expected": marker.get("baseGraphHash"), "actual": restored_graph_hash},
            )
        if accept_diverged:
            raise ContractError(
                "graphpatch-recovery-clear-denied",
                "accept_diverged is not permitted in the G0.1 candidate (D-10 is unapproved)",
            )
        self.path_for(project_name, asset_path).unlink()
        return True


def authorize_save(project_name: str, asset_path: str, store: RecoveryStore) -> None:
    if store.is_pending(project_name, asset_path):
        raise ContractError(
            "graphpatch-recovery-required",
            "A pending recovery marker blocks Save for this asset",
            path="$.assetPath",
            context={"assetPath": asset_path},
        )


def authorize_apply(project_name: str, asset_path: str, store: RecoveryStore) -> None:
    if store.is_pending(project_name, asset_path):
        raise ContractError(
            "graphpatch-recovery-required",
            "A pending recovery marker blocks GraphPatch Apply for this asset",
            path="$.target.assetPath",
            context={"assetPath": asset_path},
        )


@dataclass
class RecoveryDecision:
    allowed: bool
    marker: dict[str, Any] | None
    reason: str
