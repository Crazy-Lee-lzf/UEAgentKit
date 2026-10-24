"""TEST-ONLY host harness for the R7.2 staged context handoff.

This is not a DSH/MCP integration. It uses the local HMAC TestClaimIssuer and
synthetic task-context / memory-injection snapshots to show end-to-end
composability with the existing read-only APIs.

Run from the worktree root:
    py -3.12 tests/python/r7_2_staged_host_harness.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "python"))

from test_memory_mount import make_record, write_pack  # noqa: E402
from ue_agent_kit.memory_mount import (  # noqa: E402
    AgentContextRequest,
    HmacClaimVerifier,
    PackPin,
    ReadOnlyMountAdapter,
    StagedContextHandoff,
    TestClaimIssuer,
    build_integrity_receipt,
)


SECRET = b"r72-harness-test-only-secret-0123456789"
ISSUER = "r72-harness-issuer"
AUDIENCE = "r72-harness"


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="r72-host-harness-") as temporary:
        root = Path(temporary)
        pack_root = root / "packs"
        pack_root.mkdir()
        pack = write_pack(
            pack_root / "r7.example",
            [
                make_record(
                    title="示例项目知识",
                    body="这条知识来自挂载包，只能作为引用的数据。",
                )
            ],
            pack_id="r7.example",
        )
        manifest = json.loads((pack / "pack.json").read_text(encoding="utf-8"))
        (pack / "pack.integrity.json").write_text(
            json.dumps(
                build_integrity_receipt(manifest),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )

        issuer = TestClaimIssuer(secret=SECRET, issuer=ISSUER, audience=AUDIENCE)
        verifier = HmacClaimVerifier(secret=SECRET, issuer=ISSUER, audience=AUDIENCE)
        claims = verifier.verify(
            issuer.issue(
                principal="example-agent",
                project_key="UEAgentKit",
                session_id="example-session",
            )
        )
        adapter = ReadOnlyMountAdapter(
            caller=claims,
            pack_root=pack_root,
            cache_dir=root / "cache",
            require_integrity_receipt=True,
        )
        try:
            info = adapter.mount("r7.example")
            pin = PackPin(
                info.pack_id,
                info.revision,
                info.content_hash,
                str(info.integrity["manifestDigest"]),
            )
            task_context = {
                "schemaVersion": "1.3",
                "tool": "ue_get_task_context",
                "request": {"query": "示例项目知识"},
                "project": {"projectKey": "UEAgentKit"},
                "memory": {
                    "summary": {
                        "available": True,
                        "stale": False,
                        "reason": "",
                        "snapshotId": "sha256:" + "1" * 64,
                        "injectionHash": "sha256:" + "2" * 64,
                        "injectionText": "L3: 本地项目规则优先。",
                        "l3Count": 1,
                        "l2Count": 0,
                        "contentChars": 14,
                        "estimatedTokens": 4,
                    }
                },
                "outputBudget": {
                    "maxTokens": 4_000,
                    "estimatedTokens": 100,
                    "truncated": False,
                },
            }
            memory_snapshot = {
                "available": True,
                "stale": False,
                "reason": "",
                "snapshotId": "sha256:" + "1" * 64,
                "injectionHash": "sha256:" + "2" * 64,
                "text": "L3: 本地项目规则优先。",
                "l3Count": 1,
                "l2Count": 0,
                "contentChars": 14,
                "estimatedTokens": 4,
            }
            legacy = b'{"legacy":"exact-bytes","space": 1}'
            request = AgentContextRequest(query="示例项目知识")

            disabled = StagedContextHandoff(
                adapter=adapter,
                enabled=False,
                pinned_packs=(pin,),
            ).build(
                local_context=task_context,
                request=request,
                legacy_serialized=legacy,
            )
            active = StagedContextHandoff(
                adapter=adapter,
                enabled=True,
                pinned_packs=(pin,),
            ).build(
                local_context=task_context,
                local_memory_snapshot=memory_snapshot,
                request=request,
                legacy_serialized=legacy,
            )
            output = {
                "testOnly": True,
                "identity": "TEST-ONLY HMAC verifier; not DSH/production",
                "defaultOffByteParity": disabled.serialized == legacy,
                "disabledReason": disabled.reason,
                "active": active.active,
                "activeReason": active.reason,
                "activePayload": active.payload,
            }
            print(json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2))
        finally:
            adapter.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
