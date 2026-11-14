from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest import mock

TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
TESTS_ROOT = Path(__file__).resolve().parent
for path in (SRC_ROOT, TESTS_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from test_memory_mount import default_scope, make_record, write_pack  # noqa: E402
from ue_agent_kit.memory_mount import (  # noqa: E402
    AgentContextProjector,
    AgentContextRequest,
    ContextLimits,
    DemoCallerContext,
    ErrorCode,
    HmacClaimVerifier,
    MemoryMountError,
    ReadOnlyMountAdapter,
    TestClaimIssuer,
    build_integrity_receipt,
)
from ue_agent_kit.memory_mount.service import QueryResult  # noqa: E402

try:
    import jsonschema  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - optional dependency
    jsonschema = None


SECRET = b"r71-context-test-only-secret-0123456789"
ISSUER = "r71-test-issuer"
AUDIENCE = "r71-context-test"


def write_receipt(pack: Path) -> None:
    manifest = json.loads((pack / "pack.json").read_text(encoding="utf-8"))
    receipt = build_integrity_receipt(manifest)
    (pack / "pack.integrity.json").write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


class ContextTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="r71-context-")
        self.root = Path(self.temporary.name)
        self.pack_root = self.root / "packs"
        self.pack_root.mkdir()
        self.now = [1_000.0]
        self._adapters: list[ReadOnlyMountAdapter] = []

    def tearDown(self) -> None:
        for adapter in self._adapters:
            adapter.close()
        self.temporary.cleanup()

    def issuer(self) -> TestClaimIssuer:
        return TestClaimIssuer(
            secret=SECRET,
            issuer=ISSUER,
            audience=AUDIENCE,
            clock=lambda: self.now[0],
        )

    def verifier(self) -> HmacClaimVerifier:
        return HmacClaimVerifier(
            secret=SECRET,
            issuer=ISSUER,
            audience=AUDIENCE,
            clock=lambda: self.now[0],
        )

    def verified(self, *, project_key: str = "UEAgentKit", ttl: float = 600.0) -> Any:
        envelope = self.issuer().issue(
            principal="alice",
            project_key=project_key,
            session_id="context-session",
            ttl_seconds=ttl,
        )
        return self.verifier().verify(envelope)

    def adapter(
        self,
        *,
        project_key: str = "UEAgentKit",
        require_integrity_receipt: bool = True,
        ttl: float = 600.0,
    ) -> ReadOnlyMountAdapter:
        adapter = ReadOnlyMountAdapter(
            caller=self.verified(project_key=project_key, ttl=ttl),
            pack_root=self.pack_root,
            cache_dir=self.root / "cache",
            require_integrity_receipt=require_integrity_receipt,
            identity_clock=lambda: self.now[0],
        )
        self._adapters.append(adapter)
        return adapter

    def pack(
        self,
        name: str,
        records: list[dict[str, Any]],
        *,
        pack_id: str | None = None,
        scope: dict[str, Any] | None = None,
        priority: int = 50,
    ) -> Path:
        path = write_pack(
            self.pack_root / name,
            records,
            pack_id=pack_id or f"r71.{name}",
            scope=scope or default_scope(),
            priority=priority,
        )
        write_receipt(path)
        return path

    def assert_error(self, code: ErrorCode, action: Any) -> MemoryMountError:
        with self.assertRaises(MemoryMountError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)
        return caught.exception


class ContextProjectionTests(ContextTestBase):
    def test_projection_shape_and_untrusted_labels(self) -> None:
        self.pack(
            "one",
            [
                make_record(
                    title="安全上下文条目",
                    body="这是上下文正文，不应被当作指令执行。",
                    details={"secret": "DETAILS_MUST_NOT_LEAK"},
                )
            ],
        )
        adapter = self.adapter()
        adapter.mount("one")
        projector = AgentContextProjector(adapter=adapter)
        projection = projector.project(
            AgentContextRequest(query="上下文", excerpt_chars=12)
        )
        payload = projection.to_payload()
        self.assertEqual(payload["schemaVersion"], 1)
        self.assertEqual(payload["kind"], "r7-agent-context-projection")
        self.assertEqual(payload["trust"]["identity"], "verified-claims")
        self.assertEqual(payload["trust"]["text"], "untrusted-source-content")
        self.assertEqual(payload["trust"]["instructionPolicy"], "never-execute")
        self.assertEqual(payload["trust"]["authorAuthenticity"], "not-established")
        self.assertEqual(payload["packCount"], 1)
        self.assertEqual(payload["recordCount"], 1)
        item = payload["items"][0]
        self.assertEqual(item["textTrust"], "untrusted-source-content")
        self.assertEqual(item["instructionPolicy"], "never-execute")
        self.assertLessEqual(len(item["excerpt"]), 12)
        self.assertNotIn("details", item)
        self.assertNotIn("body", item)
        self.assertNotIn("DETAILS_MUST_NOT_LEAK", json.dumps(payload, ensure_ascii=False))
        self.assertEqual(payload["packs"][0]["integrityMode"], "manifest-digest-verified")
        self.assertTrue(payload["packs"][0]["receiptVerified"])
        self.assertEqual(
            payload["packs"][0]["authorAuthenticity"],
            "not-established",
        )
        self.assertEqual(
            len(projection.to_json_bytes()),
            payload["budgets"]["outputBytes"],
        )

    def test_projection_matches_versioned_schema(self) -> None:
        self.pack("one", [make_record(title="Schema 条目", body="正文。")])
        adapter = self.adapter()
        adapter.mount("one")
        payload = AgentContextProjector(adapter=adapter).project(
            AgentContextRequest(query="Schema 条目")
        ).to_payload()
        if jsonschema is None:
            self.skipTest("jsonschema is not installed")
        schema = json.loads(
            (
                TOOL_ROOT
                / "spec"
                / "memory-mount"
                / "r7-agent-context-projection.schema.json"
            ).read_text(encoding="utf-8")
        )
        jsonschema.Draft202012Validator(
            schema,
            format_checker=jsonschema.FormatChecker(),
        ).validate(payload)

    def test_demo_or_raw_adapter_is_rejected(self) -> None:
        with self.assertRaises(MemoryMountError) as caught:
            AgentContextProjector(adapter=object())  # type: ignore[arg-type]
        self.assertEqual(caught.exception.code, ErrorCode.IDENTITY_REQUIRED)
        with self.assertRaises(MemoryMountError) as caught:
            ReadOnlyMountAdapter(
                caller=DemoCallerContext(principal="alice", project_key="UEAgentKit"),
                pack_root=self.pack_root,
                cache_dir=self.root / "cache",
            )
        self.assertEqual(caught.exception.code, ErrorCode.IDENTITY_REQUIRED)

    def test_expired_claim_at_query_fails_closed(self) -> None:
        self.pack("one", [make_record(title="过期上下文")])
        adapter = self.adapter(ttl=10)
        adapter.mount("one")
        self.now[0] = 1_020.0
        projector = AgentContextProjector(adapter=adapter)
        self.assert_error(
            ErrorCode.IDENTITY_EXPIRED,
            lambda: projector.project(AgentContextRequest(query="过期上下文")),
        )

    def test_tampered_receipt_blocks_context_construction(self) -> None:
        pack = self.pack("one", [make_record(title="收据篡改条目")])
        manifest_path = pack / "pack.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["priority"] = 99
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        adapter = self.adapter()
        self.assert_error(
            ErrorCode.INTEGRITY_RECEIPT_MISMATCH,
            lambda: adapter.mount("one"),
        )

    def test_wrong_project_pack_is_rejected(self) -> None:
        self.pack(
            "other",
            [make_record(title="其他项目")],
            scope=default_scope("project", projectKey="OtherProject"),
        )
        adapter = self.adapter(project_key="UEAgentKit")
        self.assert_error(ErrorCode.PROJECT_MISMATCH, lambda: adapter.mount("other"))

    def test_pack_limit_fails_closed(self) -> None:
        for index in range(2):
            self.pack(f"p{index}", [make_record(record_id=f"rec-{index}", title=f"条目{index}")])
        adapter = self.adapter()
        adapter.mount("p0")
        adapter.mount("p1")
        limits = replace(ContextLimits(), max_packs=1)
        projector = AgentContextProjector(adapter=adapter, limits=limits)
        self.assert_error(
            ErrorCode.CONTEXT_PACK_LIMIT,
            lambda: projector.project(
                AgentContextRequest(query="条目", max_packs=1)
            ),
        )

    def test_record_limit_is_bounded_and_labeled(self) -> None:
        self.pack(
            "many",
            [
                make_record(
                    record_id=f"rec-{index}",
                    subject_key=f"subject.{index}",
                    title=f"条目 {index}",
                    body="正文。",
                )
                for index in range(5)
            ],
        )
        adapter = self.adapter()
        adapter.mount("many")
        limits = replace(ContextLimits(), max_records=2)
        projector = AgentContextProjector(adapter=adapter, limits=limits)
        projection = projector.project(
            AgentContextRequest(query="条目", max_records=2)
        )
        self.assertEqual(len(projection.items), 2)
        self.assertTrue(projection.truncated)
        self.assertIn("r7-context-record-limit", projection.warnings)

    def test_output_byte_budget_truncates_tail(self) -> None:
        self.pack(
            "many",
            [
                make_record(
                    record_id=f"rec-{index}",
                    subject_key=f"subject.{index}",
                    title=f"输出预算条目 {index}",
                    body="这是一段用于输出预算测试的正文。",
                )
                for index in range(5)
            ],
        )
        adapter = self.adapter()
        adapter.mount("many")
        limits = replace(ContextLimits(), max_output_bytes=2_500)
        projector = AgentContextProjector(adapter=adapter, limits=limits)
        projection = projector.project(
            AgentContextRequest(
                query="输出预算条目",
                max_records=5,
                max_output_bytes=2_500,
                excerpt_chars=40,
            )
        )
        self.assertLess(len(projection.items), 5)
        self.assertTrue(projection.truncated)
        self.assertIn("r7-context-output-truncated", projection.warnings)
        self.assertLessEqual(len(projection.to_json_bytes()), 2_500)
        self.assertEqual(
            len(projection.to_json_bytes()),
            projection.budgets["outputBytes"],
        )

    def test_approx_token_budget_truncates_tail(self) -> None:
        self.pack(
            "many",
            [
                make_record(
                    record_id=f"rec-{index}",
                    subject_key=f"token.subject.{index}",
                    title=f"令牌预算条目 {index}",
                    body="这是一段较长的正文。" * 8,
                )
                for index in range(4)
            ],
        )
        adapter = self.adapter()
        adapter.mount("many")
        limits = replace(ContextLimits(), max_approx_tokens=80)
        projector = AgentContextProjector(adapter=adapter, limits=limits)
        projection = projector.project(
            AgentContextRequest(
                query="令牌预算条目",
                max_records=4,
                max_approx_tokens=80,
                excerpt_chars=100,
            )
        )
        self.assertLessEqual(projection.budgets["approxTokens"], 80)
        self.assertTrue(projection.truncated)
        self.assertIn("r7-context-output-truncated", projection.warnings)

    def test_short_chinese_query_uses_bounded_fallback(self) -> None:
        self.pack("cn", [make_record(title="共享契约审核", body="中文短查询正文。")])
        adapter = self.adapter()
        adapter.mount("cn")
        projection = AgentContextProjector(adapter=adapter).project(
            AgentContextRequest(query="契约")
        )
        self.assertEqual(len(projection.items), 1)
        self.assertEqual(projection.items[0]["matchReason"], "cjk-substring-fallback")

    def test_normal_fts_query(self) -> None:
        self.pack("cn", [make_record(title="共享契约审核与知识包", body="中文正文。")])
        adapter = self.adapter()
        adapter.mount("cn")
        projection = AgentContextProjector(adapter=adapter).project(
            AgentContextRequest(query="契约审核")
        )
        self.assertEqual(len(projection.items), 1)
        self.assertEqual(projection.items[0]["matchReason"], "fts5-trigram")

    def test_dedupe_across_packs(self) -> None:
        self.pack(
            "shared",
            [make_record(record_id="rec-shared", title="共享知识条目", body="相同内容。")],
            pack_id="r71.shared",
            scope=default_scope("shared"),
            priority=10,
        )
        self.pack(
            "project",
            [make_record(record_id="rec-project", title="共享知识条目", body="相同内容。")],
            pack_id="r71.project",
            scope=default_scope("project", projectKey="UEAgentKit"),
            priority=10,
        )
        adapter = self.adapter()
        adapter.mount("shared")
        adapter.mount("project")
        projection = AgentContextProjector(adapter=adapter).project(
            AgentContextRequest(query="知识条目")
        )
        self.assertEqual(len(projection.items), 1)
        self.assertEqual(projection.items[0]["packId"], "r71.project")
        self.assertNotIn("conflict", projection.items[0])
        self.assertEqual(
            [pack["packId"] for pack in projection.packs],
            ["r71.project", "r71.shared"],
        )

    def test_conflict_alternatives_only_when_requested(self) -> None:
        self.pack(
            "shared",
            [make_record(record_id="rec-shared", title="冲突知识条目", body="共享版本。")],
            pack_id="r71.shared",
            scope=default_scope("shared"),
            priority=99,
        )
        self.pack(
            "project",
            [make_record(record_id="rec-project", title="冲突知识条目", body="项目版本。")],
            pack_id="r71.project",
            scope=default_scope("project", projectKey="UEAgentKit"),
            priority=1,
        )
        adapter = self.adapter()
        adapter.mount("shared")
        adapter.mount("project")
        projector = AgentContextProjector(adapter=adapter)
        default = projector.project(AgentContextRequest(query="冲突知识条目"))
        self.assertEqual(len(default.items), 1)
        self.assertEqual(default.items[0]["packId"], "r71.project")
        self.assertIn("conflict", default.items[0])
        self.assertNotIn("selected", default.items[0])

        expanded = projector.project(
            AgentContextRequest(query="冲突知识条目", include_conflicts=True)
        )
        self.assertEqual(len(expanded.items), 2)
        self.assertEqual(expanded.items[0]["packId"], "r71.project")
        self.assertTrue(expanded.items[0]["selected"])
        self.assertFalse(expanded.items[1]["selected"])
        self.assertEqual(expanded.items[0]["conflict"]["state"], "unresolved")

    def test_stable_repeated_projection(self) -> None:
        self.pack(
            "stable",
            [
                make_record(record_id="rec-a", subject_key="a", title="稳定条目 A", body="A。"),
                make_record(record_id="rec-b", subject_key="b", title="稳定条目 B", body="B。"),
            ],
        )
        adapter = self.adapter()
        adapter.mount("stable")
        projector = AgentContextProjector(adapter=adapter)
        request = AgentContextRequest(query="稳定条目")
        first = projector.project(request).to_json_bytes()
        second = projector.project(request).to_json_bytes()
        third = projector.project(request).to_json_bytes()
        self.assertEqual(first, second)
        self.assertEqual(second, third)

    def test_record_file_order_does_not_change_item_order(self) -> None:
        records = [
            make_record(record_id="rec-a", subject_key="a", title="顺序条目 A", body="A。"),
            make_record(record_id="rec-b", subject_key="b", title="顺序条目 B", body="B。"),
        ]
        self.pack("ordered", records)
        adapter_a = self.adapter()
        adapter_a.mount("ordered")
        payload_a = AgentContextProjector(adapter=adapter_a).project(
            AgentContextRequest(query="顺序条目")
        ).to_payload()

        other_root = self.root / "packs-reversed"
        other_root.mkdir()
        previous_root = self.pack_root
        self.pack_root = other_root
        try:
            self.pack("reversed", list(reversed(records)))
            adapter_b = self.adapter()
            adapter_b.mount("reversed")
            payload_b = AgentContextProjector(adapter=adapter_b).project(
                AgentContextRequest(query="顺序条目")
            ).to_payload()
        finally:
            self.pack_root = previous_root
        self.assertEqual(
            [
                (item["recordId"], item["excerpt"], item["contentSha256"])
                for item in payload_a["items"]
            ],
            [
                (item["recordId"], item["excerpt"], item["contentSha256"])
                for item in payload_b["items"]
            ],
        )

    def test_no_leakage_of_paths_claims_details_or_full_text(self) -> None:
        full_body = "FULL_BODY_SENTINEL_" + ("长正文" * 40)
        pack = self.pack(
            "leak",
            [
                make_record(
                    title="泄漏检查条目",
                    body=full_body,
                    details={"secret": "DETAILS_SECRET"},
                )
            ],
        )
        manifest_path = pack / "pack.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["provenance"]["sourceRef"] = str(self.root / "cache")
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        write_receipt(pack)
        adapter = self.adapter()
        adapter.mount("leak")
        projection = AgentContextProjector(adapter=adapter).project(
            AgentContextRequest(query="泄漏检查条目", excerpt_chars=16)
        )
        text = projection.to_json_bytes().decode("utf-8")
        self.assertNotIn(str(self.root), text)
        self.assertNotIn(str(self.root / "cache"), text)
        self.assertIn("[redacted-path]", text)
        self.assertNotIn("context-session", text)
        self.assertNotIn("alice", text.lower())
        self.assertNotIn("DETAILS_SECRET", text)
        self.assertNotIn(full_body, text)
        self.assertNotIn('"details"', text)
        self.assertNotIn('"body"', text)
        self.assertNotIn("FULL_BODY_SENTINEL_", text)
        self.assertLessEqual(len(projection.items[0]["excerpt"]), 16)

    def test_invalid_adapter_item_fails_closed(self) -> None:
        self.pack("one", [make_record(title="坏条目")])
        adapter = self.adapter()
        adapter.mount("one")
        malformed = QueryResult(
            items=(
                {
                    "globalId": "r71.one:rec-001",
                    "packId": "r71.one",
                    "recordId": "rec-001",
                    "recordType": "projectFact",
                    "subjectKey": "subject.default",
                    "title": "坏条目",
                    "bodyPreview": "正文",
                    "bodyTruncated": False,
                    "sourceKind": "user-confirmed",
                    "confidence": 1.0,
                    "status": "valid",
                    "scopes": [],
                    "updatedAtUtc": "2026-10-23T12:00:00Z",
                    # contentSha256 deliberately missing
                    "matchReason": "fts5-trigram",
                    "rank": {"scopeTier": 0, "priority": 50, "statusRank": 2},
                    "provenance": {"kind": "authored", "producer": "test", "sourceRef": ""},
                },
            ),
            warnings=(),
            retrieval_mode="fts5-trigram",
            candidate_count=1,
            conflict_count=0,
            mounted_pack_count=1,
            truncated=False,
        )
        projector = AgentContextProjector(adapter=adapter)
        with mock.patch.object(adapter, "query", return_value=malformed):
            self.assert_error(
                ErrorCode.CONTEXT_INVALID_RECORD,
                lambda: projector.project(AgentContextRequest(query="坏条目")),
            )

    def test_invalid_request_budget_is_rejected(self) -> None:
        adapter = self.adapter()
        projector = AgentContextProjector(adapter=adapter)
        for request in (
            AgentContextRequest(query=""),
            AgentContextRequest(query="bad\x00query"),
            AgentContextRequest(query="x", max_records=0),
            AgentContextRequest(query="x", max_packs=9),
            AgentContextRequest(query="x", max_output_bytes=100),
            AgentContextRequest(query="x", max_approx_tokens=1),
            AgentContextRequest(query="x", excerpt_chars=1_001),
            AgentContextRequest(query="x", timeout_ms=2_001),
            AgentContextRequest(query="x", record_types=("not-a-type",)),
            AgentContextRequest(query="x", scope_type="not-a-scope"),
        ):
            with self.subTest(request=request):
                self.assert_error(
                    ErrorCode.CONTEXT_REQUEST_INVALID,
                    lambda r=request: projector.project(r),
                )

    def test_no_mounts_is_empty_not_an_error(self) -> None:
        adapter = self.adapter()
        projection = AgentContextProjector(adapter=adapter).project(
            AgentContextRequest(query="任何内容")
        )
        self.assertEqual(projection.items, ())
        self.assertEqual(projection.packs, ())
        self.assertEqual(projection.retrieval_mode, "empty")
        self.assertIn("r7-context-pack-text-untrusted", projection.warnings)

    def test_pack_files_unchanged_by_projection(self) -> None:
        pack = self.pack("one", [make_record(title="只读检查条目")])
        before = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(pack.iterdir())
            if path.is_file()
        }
        adapter = self.adapter()
        adapter.mount("one")
        AgentContextProjector(adapter=adapter).project(
            AgentContextRequest(query="只读检查条目")
        )
        after = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(pack.iterdir())
            if path.is_file()
        }
        self.assertEqual(before, after)

    def test_index_poisoning_rebuilds_context(self) -> None:
        self.pack("one", [make_record(title="缓存污染条目", body="原始上下文正文。")])
        adapter = self.adapter()
        info = adapter.mount("one")
        index_path = adapter._registry._mounts[info.pack_id].index.path
        adapter.close()
        connection = sqlite3.connect(str(index_path))
        try:
            connection.execute(
                "UPDATE pack_records SET body = '被污染正文。' WHERE record_id = 'rec-001'"
            )
            connection.commit()
        finally:
            connection.close()
        adapter2 = self.adapter()
        info2 = adapter2.mount("one")
        self.assertFalse(info2.index_reused)
        projection = AgentContextProjector(adapter=adapter2).project(
            AgentContextRequest(query="原始上下文正文")
        )
        self.assertEqual(len(projection.items), 1)
        self.assertIn("原始上下文正文", projection.items[0]["excerpt"])


if __name__ == "__main__":
    unittest.main()
