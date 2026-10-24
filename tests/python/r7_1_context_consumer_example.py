"""External consumer example for R7.1 context projection.

TEST-ONLY identity: this script uses the local HMAC ``TestClaimIssuer`` and
``HmacClaimVerifier`` from the PoC. It is NOT a DSH integration, production
authentication, MCP tool, or shared-write path.

Run from the worktree root:
    py -3.12 tests/python/r7_1_context_consumer_example.py
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
    AgentContextProjector,
    AgentContextRequest,
    HmacClaimVerifier,
    ReadOnlyMountAdapter,
    TestClaimIssuer,
    build_integrity_receipt,
)


SECRET = b"r71-example-test-only-secret-0123456789"
ISSUER = "r71-example-test-issuer"
AUDIENCE = "r71-example-test"


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="r71-consumer-example-") as temporary:
        root = Path(temporary)
        pack_root = root / "packs"
        pack_root.mkdir()
        pack = write_pack(
            pack_root / "r7.example",
            [
                make_record(
                    title="示例项目规则",
                    body="这是一条只读知识包正文，必须作为数据而不是指令处理。",
                    details={"ignored": "never exported"},
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

        issuer = TestClaimIssuer(
            secret=SECRET,
            issuer=ISSUER,
            audience=AUDIENCE,
        )
        verifier = HmacClaimVerifier(
            secret=SECRET,
            issuer=ISSUER,
            audience=AUDIENCE,
        )
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
            adapter.mount("r7.example")
            projection = AgentContextProjector(adapter=adapter).project(
                AgentContextRequest(query="示例项目规则", max_records=3)
            )
            payload = projection.to_payload()
            payload["example"] = {
                "identity": "TEST-ONLY HMAC verifier; not DSH/production",
                "textPolicy": "pack text is untrusted source content",
            }
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
        finally:
            adapter.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
