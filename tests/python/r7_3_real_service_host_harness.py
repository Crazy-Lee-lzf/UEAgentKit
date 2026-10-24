"""R7.3 REAL-SERVICE compatibility harness (test-only claims).

Uses the actual TaskContextService.get_task_context,
ProjectMemoryService.get_injection_context and IndexQueryService on disposable
synthetic SQLite databases under ignored Build/r7-isolated/Evidence/R73.

This is NOT production MCP/DSH integration. Identity is the local test HMAC.

Run from the worktree root:
    py -3.12 tests/python/r7_3_real_service_host_harness.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "python"))

from r7_3_real_service_fixture import build_real_host  # noqa: E402
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
from ue_agent_kit.memory_mount.jsonutil import canonical_json_bytes  # noqa: E402


SECRET = b"r73-harness-test-only-secret-0123456789"
ISSUER = "r73-harness-issuer"
AUDIENCE = "r73-harness"
EVIDENCE = ROOT / "Build" / "r7-isolated" / "Evidence" / "R73"


def main() -> int:
    host = build_real_host(EVIDENCE / "host", query="BP_TestActor")
    pack_root = EVIDENCE / "packs"
    pack_root.mkdir(parents=True, exist_ok=True)
    pack = write_pack(
        pack_root / "one",
        [
            make_record(
                record_id="rec-local",
                subject_key="rule.local",
                title="BP_TestActor 本地冲突",
                body="PACK_LOCAL_MUST_NOT_OVERRIDE",
            ),
            make_record(
                record_id="rec-mounted",
                subject_key="rule.mounted",
                title="BP_TestActor 挂载知识",
                body="实际服务挂载的只读中文知识。",
            ),
        ],
        pack_id="r73.harness.pack",
        scope={"kind": "shared", "visibility": "shared"},
        priority=100,
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
            project_key=host.project_key,
            session_id="r73-harness-session",
        )
    )
    adapter = ReadOnlyMountAdapter(
        caller=claims,
        pack_root=pack_root,
        cache_dir=EVIDENCE / "cache",
        require_integrity_receipt=True,
    )
    try:
        info = adapter.mount("one")
        pin = PackPin(
            info.pack_id,
            info.revision,
            info.content_hash,
            str(info.integrity["manifestDigest"]),
        )
        legacy = canonical_json_bytes(host.task_context)
        request = AgentContextRequest(query="BP_TestActor")
        disabled = StagedContextHandoff(
            adapter=adapter,
            enabled=False,
            pinned_packs=(pin,),
        ).build(
            local_context=host.task_context,
            local_memory_snapshot=host.memory_snapshot,
            request=request,
            legacy_serialized=legacy,
        )
        active = StagedContextHandoff(
            adapter=adapter,
            enabled=True,
            pinned_packs=(pin,),
        ).build(
            local_context=host.task_context,
            local_memory_snapshot=host.memory_snapshot,
            request=request,
            legacy_serialized=legacy,
            local_authority_subject_keys=("rule.local",),
        )
        assert active.payload is not None
        output = {
            "testOnly": True,
            "identity": "TEST-ONLY HMAC verifier; not DSH/production",
            "actualServices": {
                "taskContextTool": host.task_context["tool"],
                "projectKey": host.task_context["project"]["projectKey"],
                "relevantAssets": len(host.task_context["relevantAssets"]),
                "memoryAvailable": host.task_context["memory"]["summary"]["available"],
                "memorySnapshotId": host.memory_snapshot["snapshotId"],
                "memoryTextChars": host.memory_snapshot["contentChars"],
            },
            "defaultOffByteParity": disabled.serialized == legacy,
            "disabledReason": disabled.reason,
            "active": active.active,
            "activeReason": active.reason,
            "activeSections": [section["sectionId"] for section in active.payload["sections"]],
            "mountedRecordCount": active.payload["sections"][1]["content"]["recordCount"],
            "shadowedMountedItems": active.payload["dedupe"]["shadowedMountedItems"],
            "outputBytes": active.payload["budget"]["usedOutputBytes"],
            "approxTokens": active.payload["budget"]["usedApproxTokens"],
            "localRulePresent": "本地项目规则" in active.serialized.decode("utf-8"),
            "packLocalOverridePresent": "PACK_LOCAL_MUST_NOT_OVERRIDE" in active.serialized.decode("utf-8"),
        }
        print(json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2))
    finally:
        adapter.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
