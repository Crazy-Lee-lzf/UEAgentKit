from __future__ import annotations

import hashlib
import http.client
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import quote

TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.active_work import (  # noqa: E402
    WorkItemDraft,
    WorkStatus,
    add_work_todo,
    block_work_item,
    create_work_item,
)
from ue_agent_kit.database import open_database  # noqa: E402
from ue_agent_kit.knowledge_view import (  # noqa: E402
    KnowledgeViewConfig,
    KnowledgeViewReadService,
    make_server,
)
from ue_agent_kit.memory_tree import KnowledgeNodeDraft, create_knowledge_node  # noqa: E402
from ue_agent_kit.project_memory import (  # noqa: E402
    MemoryArtifact,
    MemoryRecordDraft,
    MemoryRecordType,
    MemoryRevision,
    MemoryScope,
    MemoryScopeType,
    MemorySourceKind,
    mark_memory_record_superseded,
    open_project_memory_database,
)

PROJECT = "测试项目"
ASSET = "/Game/角色/BP_主角.BP_主角"


def _request(host: str, port: int, method: str, path: str) -> tuple[int, dict[str, object] | bytes]:
    connection = http.client.HTTPConnection(host, port, timeout=10)
    try:
        connection.request(method, path)
        response = connection.getresponse()
        body = response.read()
        content_type = response.getheader("Content-Type") or ""
        if "application/json" in content_type:
            return response.status, json.loads(body.decode("utf-8"))
        return response.status, body
    finally:
        connection.close()


class KnowledgeViewServerFixture(unittest.TestCase):
    """Starts one loopback server on an ephemeral port over a seeded DB."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ueak_knowledge_view_")
        self.root = Path(self.temporary.name)
        self.memory_path = self.root / "中文目录" / "memory.sqlite3"
        self.asset_path = self.root / "index.sqlite3"
        self._seed_memory_database()
        self._seed_asset_database()
        config = KnowledgeViewConfig(
            memory_database=self.memory_path,
            database=self.asset_path,
            project_key=PROJECT,
            host="127.0.0.1",
            port=0,
        )
        self.config = config
        self.server = make_server(config)
        self.host, self.port = self.server.server_address[0], self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._shutdown)

    def _shutdown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)
        self.temporary.cleanup()

    def _seed_memory_database(self) -> None:
        with open_project_memory_database(self.memory_path) as connection:
            root = create_knowledge_node(
                connection,
                KnowledgeNodeDraft(
                    project_key=PROJECT,
                    path="/project",
                    node_type="project",
                    title="项目根",
                    summary="测试项目知识树根节点",
                ),
            )
            child = create_knowledge_node(
                connection,
                KnowledgeNodeDraft(
                    project_key=PROJECT,
                    path="/project/战斗系统",
                    node_type="system",
                    title="战斗系统",
                    summary="角色战斗与数值",
                    parent_node_id=root.node_id,
                ),
            )
            self.root_node_id = root.node_id
            self.child_node_id = child.node_id

            self.record_ids: dict[str, str] = {}
            drafts = {
                "valid": MemoryRecordDraft(
                    project_key=PROJECT,
                    record_type=MemoryRecordType.PROJECT_FACT,
                    subject_key="asset:主角:生命值",
                    title="主角默认生命值",
                    body="主角默认生命值为 100，受难度曲线加成。" * 20,
                    source_kind=MemorySourceKind.USER_CONFIRMED,
                    source_ref="conversation:user-confirmation-1",
                    confidence=0.95,
                    scopes=(
                        MemoryScope(MemoryScopeType.PROJECT, PROJECT),
                        MemoryScope(MemoryScopeType.ASSET, ASSET, {"assetClass": "Blueprint"}),
                    ),
                    revision_set=(
                        MemoryRevision(ASSET, "rev-1001", True),
                    ),
                    artifacts=(
                        MemoryArtifact(
                            "validationEvidence",
                            "Output/Validation/主角-生命值.json",
                            {"result": "passed"},
                        ),
                    ),
                    node_id=child.node_id,
                ),
                "stale": MemoryRecordDraft(
                    project_key=PROJECT,
                    record_type=MemoryRecordType.KNOWN_ISSUE,
                    subject_key="asset:主角:冲刺",
                    title="冲刺动画接地偏移",
                    body="冲刺动画在斜面上接地偏移 5cm。",
                    source_kind=MemorySourceKind.TOOL_OBSERVED,
                    confidence=0.8,
                    node_id=child.node_id,
                ),
                "unverified": MemoryRecordDraft(
                    project_key=PROJECT,
                    record_type=MemoryRecordType.PROJECT_FACT,
                    subject_key="asset:主角:护甲",
                    title="护甲减伤公式",
                    body="护甲减伤公式尚未验证。",
                    source_kind=MemorySourceKind.MODEL_INFERRED,
                    confidence=0.4,
                    node_id=child.node_id,
                ),
                "conflicted": MemoryRecordDraft(
                    project_key=PROJECT,
                    record_type=MemoryRecordType.DECISION_RECORD,
                    subject_key="decision:难度曲线",
                    title="难度曲线方案",
                    body="存在两个互相冲突的难度曲线方案。",
                    source_kind=MemorySourceKind.USER_CONFIRMED,
                    confidence=0.7,
                    node_id=child.node_id,
                ),
                "replacement": MemoryRecordDraft(
                    project_key=PROJECT,
                    record_type=MemoryRecordType.PROJECT_RULE,
                    subject_key="rule:命名",
                    title="资产命名规范 v2",
                    body="蓝图资产以 BP_ 前缀命名。",
                    source_kind=MemorySourceKind.USER_CONFIRMED,
                    confidence=1.0,
                    node_id=root.node_id,
                ),
            }
            for key, draft in drafts.items():
                from ue_agent_kit.project_memory import create_memory_record

                record = create_memory_record(connection, draft)
                self.record_ids[key] = record.record_id
            from ue_agent_kit.project_memory import create_memory_record as _create

            superseded = _create(
                connection,
                MemoryRecordDraft(
                    project_key=PROJECT,
                    record_type=MemoryRecordType.PROJECT_RULE,
                    subject_key="rule:命名",
                    title="资产命名规范 v1",
                    body="旧版命名规范，已被 v2 取代。",
                    source_kind=MemorySourceKind.USER_CONFIRMED,
                    confidence=1.0,
                    node_id=root.node_id,
                ),
            )
            mark_memory_record_superseded(
                connection,
                record_id=superseded.record_id,
                replacement_record_id=self.record_ids["replacement"],
                reason="规范升级到 v2",
            )
            self.record_ids["superseded"] = superseded.record_id
            connection.execute(
                "UPDATE memory_records SET status = 'stale' WHERE record_id = ?",
                (self.record_ids["stale"],),
            )
            connection.execute(
                "UPDATE memory_records SET status = 'conflicted' WHERE record_id = ?",
                (self.record_ids["conflicted"],),
            )
            connection.execute(
                "INSERT INTO memory_status_events(record_id, from_status, to_status, reason, changed_at_utc)"
                " VALUES (?, 'valid', 'stale', '测试注入的状态事件', '2026-08-29T00:00:00.000Z')",
                (self.record_ids["stale"],),
            )
            work = create_work_item(
                connection,
                WorkItemDraft(
                    project_key=PROJECT,
                    title="修复冲刺接地偏移",
                    description="修好冲刺动画在斜面的接地表现。",
                    next_action="录制斜面回归用例",
                    priority=80,
                    owner="agent",
                    status=WorkStatus.IN_PROGRESS,
                    node_ids=(child.node_id,),
                    asset_paths=(ASSET,),
                ),
            )
            block_work_item(
                connection,
                project_key=PROJECT,
                work_item_id=work.work_item_id,
                blocked_reason="等待动画组确认斜面用例",
            )
            add_work_todo(
                connection,
                project_key=PROJECT,
                work_item_id=work.work_item_id,
                text="定位偏移来源",
            )
            add_work_todo(
                connection,
                project_key=PROJECT,
                work_item_id=work.work_item_id,
                text="更新知识记录",
            )
            self.work_item_id = work.work_item_id
            # paginated filler records under the root node
            for index in range(6):
                _create(
                    connection,
                    MemoryRecordDraft(
                        project_key=PROJECT,
                        record_type=MemoryRecordType.TASK_RECORD,
                        subject_key=f"task:填充:{index}",
                        title=f"填充任务 {index}",
                        body=f"分页确定性验证记录 {index}",
                        source_kind=MemorySourceKind.USER_CONFIRMED,
                        confidence=1.0,
                        node_id=root.node_id,
                    ),
                )
            connection.commit()

    def _seed_asset_database(self) -> None:
        with open_database(self.asset_path) as connection:
            connection.execute(
                """
                INSERT INTO assets(
                    asset_path, package_name, asset_name, asset_class, blueprint_type,
                    parent_class, generated_class, status, revision_value, package_guid,
                    file_size, modified_utc, content_sha256, package_dirty, schema_version,
                    exporter_version, profile, canonical_sha256, canonical_relpath,
                    bpctx_relpath, summary_json, indexed_at_utc
                ) VALUES (?, '游戏', 'BP_主角', 'Blueprint', 'BlueprintClass',
                    'Object', '/Script/游戏.BP_主角_C', 0, 'rev-1001', 'guid-1',
                    1024, '2026-08-29T00:00:00Z', 'sha256-1', 0, '1.1',
                    '1', 'default', 'canonical-1', 'canonical/BP_主角.json',
                    '', '{}', '2026-08-29T00:00:00Z')
                """,
                (ASSET,),
            )
            connection.execute(
                """
                INSERT INTO assets(
                    asset_path, package_name, asset_name, asset_class, blueprint_type,
                    parent_class, generated_class, status, revision_value, package_guid,
                    file_size, modified_utc, content_sha256, package_dirty, schema_version,
                    exporter_version, profile, canonical_sha256, canonical_relpath,
                    bpctx_relpath, summary_json, indexed_at_utc
                ) VALUES ('/Game/角色/BP_敌人.BP_敌人', '游戏', 'BP_敌人', 'Blueprint', 'BlueprintClass',
                    'Object', '/Script/游戏.BP_敌人_C', 0, 'rev-1002', 'guid-2',
                    2048, '2026-08-29T00:00:00Z', 'sha256-2', 0, '1.1',
                    '1', 'default', 'canonical-2', 'canonical/BP_敌人.json',
                    '', '{}', '2026-08-29T00:00:00Z')
                """
            )
            connection.execute(
                """
                INSERT INTO references_table(
                    asset_id, stable_id, kind, source_symbol_id, target_symbol_id,
                    target_kind, target_name, target_asset_path, target_path,
                    graph_guid, graph_name, node_guid, node_class, node_title, details_json
                ) SELECT id, 'ref-1', 'hardReference', '', '', 'Blueprint', 'BP_主角',
                    ?, '', '', '', '', '', '', '{}'
                FROM assets WHERE asset_path = '/Game/角色/BP_敌人.BP_敌人'
                """,
                (ASSET,),
            )
            connection.commit()

    # shared helpers -----------------------------------------------------

    def get(self, path: str) -> tuple[int, dict[str, object] | bytes]:
        return _request(self.host, self.port, "GET", path)


class RoutingTests(KnowledgeViewServerFixture):
    def test_unknown_route_returns_404_json(self) -> None:
        status, payload = self.get("/definitely/not/here")
        self.assertEqual(404, status)
        self.assertIsInstance(payload, dict)
        error = payload["error"]
        self.assertEqual("notFound", error["code"])
        self.assertNotIn("Definitely", error["message"])

    def test_unknown_api_route_returns_404(self) -> None:
        status, payload = self.get("/api/unknown")
        self.assertEqual(404, status)
        self.assertEqual("notFound", payload["error"]["code"])  # type: ignore[index]

    def test_mutation_methods_answer_405(self) -> None:
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            status, payload = _request(self.host, self.port, method, "/api/records")
            self.assertEqual(405, status, method)
            self.assertEqual("methodNotAllowed", payload["error"]["code"])  # type: ignore[index]

    def test_unknown_query_parameter_is_rejected(self) -> None:
        status, payload = self.get("/api/records?sql=DROP%20TABLE")
        self.assertEqual(400, status)
        self.assertEqual("badRequest", payload["error"]["code"])  # type: ignore[index]

    def test_invalid_enum_value_is_rejected(self) -> None:
        status, payload = self.get("/api/records?status=not-a-status")
        self.assertEqual(400, status)
        self.assertEqual("badRequest", payload["error"]["code"])  # type: ignore[index]

    def test_page_limit_cap_is_enforced(self) -> None:
        status, payload = self.get("/api/records?limit=201")
        self.assertEqual(400, status)
        self.assertEqual("badRequest", payload["error"]["code"])  # type: ignore[index]
        status, payload = self.get("/api/records?limit=0")
        self.assertEqual(400, status)

    def test_missing_record_returns_404(self) -> None:
        status, payload = self.get("/api/record/mem_" + "0" * 32)
        self.assertEqual(404, status)
        self.assertEqual("notFound", payload["error"]["code"])  # type: ignore[index]

    def test_index_html_is_served_from_whitelist(self) -> None:
        status, body = self.get("/")
        self.assertEqual(200, status)
        self.assertIsInstance(body, bytes)
        self.assertIn(b"UEAgentKit", body[:200])
        status, body = self.get("/index.html")
        self.assertEqual(200, status)

    def test_index_html_only_allows_known_names(self) -> None:
        status, _ = self.get("/web/index.html")
        self.assertEqual(404, status)
        status, _ = self.get("/../../etc/passwd")
        self.assertEqual(404, status)


class ReadModelTests(KnowledgeViewServerFixture):
    def test_status_reports_counts_and_readonly(self) -> None:
        status, payload = self.get("/api/status")
        self.assertEqual(200, status)
        self.assertTrue(payload["readOnly"])  # type: ignore[index]
        memory = payload["memoryDatabase"]  # type: ignore[index]
        self.assertTrue(memory["present"])
        self.assertEqual(12, memory["recordCount"])
        self.assertEqual(2, memory["nodeCount"])
        self.assertEqual(1, memory["activeWorkCount"])
        self.assertEqual(
            {"conflicted": 2, "stale": 1, "superseded": 1, "unverified": 1, "valid": 7},
            memory["countsByStatus"],
        )
        asset = payload["assetDatabase"]  # type: ignore[index]
        self.assertTrue(asset["present"])
        self.assertEqual(2, asset["assetCount"])

    def test_tree_lazy_navigation(self) -> None:
        status, payload = self.get("/api/tree?limit=200")
        self.assertEqual(200, status)
        paths = [item["path"] for item in payload["items"]]  # type: ignore[index]
        self.assertEqual(["/project"], paths)
        root = payload["items"][0]  # type: ignore[index]
        self.assertEqual(1, root["childCount"])
        status, payload = self.get(
            "/api/tree?parent=" + str(root["nodeId"]) + "&limit=50"  # type: ignore[index]
        )
        self.assertEqual(200, status)
        self.assertEqual(["/project/战斗系统"], [item["path"] for item in payload["items"]])  # type: ignore[index]

    def test_tree_parent_missing_returns_404(self) -> None:
        status, payload = self.get("/api/tree?parent=kn_" + "0" * 32)
        self.assertEqual(404, status)
        self.assertEqual("notFound", payload["error"]["code"])  # type: ignore[index]

    def test_node_detail_lists_attached_records(self) -> None:
        status, payload = self.get("/api/node/" + self.child_node_id)
        self.assertEqual(200, status)
        self.assertEqual("战斗系统", payload["node"]["title"])  # type: ignore[index]
        self.assertEqual(0, payload["childCount"])  # type: ignore[index]
        record_ids = [item["recordId"] for item in payload["records"]]  # type: ignore[index]
        self.assertIn(self.record_ids["valid"], record_ids)
        self.assertEqual(4, len(record_ids))

    def test_record_list_filters_by_status_and_type(self) -> None:
        status, payload = self.get("/api/records?status=stale")
        self.assertEqual(200, status)
        self.assertEqual(1, payload["total"])  # type: ignore[index]
        self.assertEqual("knownIssue", payload["items"][0]["recordType"])  # type: ignore[index]
        status, payload = self.get("/api/records?type=projectRule")
        self.assertEqual(200, status)
        self.assertEqual(2, payload["total"])  # type: ignore[index]

    def test_record_list_is_deterministically_paginated(self) -> None:
        page_one = self.get("/api/records?limit=4&offset=0")[1]
        page_two = self.get("/api/records?limit=4&offset=4")[1]
        ids_one = [item["recordId"] for item in page_one["items"]]  # type: ignore[index]
        ids_two = [item["recordId"] for item in page_two["items"]]  # type: ignore[index]
        self.assertEqual(4, len(ids_one))
        self.assertEqual(4, len(ids_two))
        self.assertFalse(set(ids_one) & set(ids_two))
        repeat = self.get("/api/records?limit=4&offset=0")[1]
        self.assertEqual(ids_one, [item["recordId"] for item in repeat["items"]])  # type: ignore[index]
        # ordering must be updated_at desc then record_id desc within equal timestamps
        updated = [item["updatedAtUtc"] for item in page_one["items"]]  # type: ignore[index]
        self.assertEqual(updated, sorted(updated, reverse=True))

    def test_record_detail_exposes_evidence(self) -> None:
        status, payload = self.get("/api/record/" + self.record_ids["valid"])
        self.assertEqual(200, status)
        self.assertEqual("主角默认生命值", payload["title"])  # type: ignore[index]
        self.assertEqual("rev-1001", payload["revisionSet"][0]["revision"])  # type: ignore[index]
        self.assertTrue(payload["contentSha256"])  # type: ignore[index]
        self.assertEqual(
            "Output/Validation/主角-生命值.json",
            payload["artifacts"][0]["artifactRef"],  # type: ignore[index]
        )
        self.assertEqual(2, len(payload["scopes"]))  # type: ignore[index]

    def test_record_detail_exposes_status_history_and_inbound_relations(self) -> None:
        status, payload = self.get("/api/record/" + self.record_ids["stale"])
        self.assertEqual(200, status)
        histories = [
            event for event in payload["statusHistory"]  # type: ignore[index]
            if event["toStatus"] == "stale"
        ]
        self.assertTrue(histories)
        self.assertEqual("测试注入的状态事件", histories[0]["reason"])
        _, superseded_detail = self.get("/api/record/" + self.record_ids["superseded"])
        self.assertEqual("superseded", superseded_detail["status"])  # type: ignore[index]
        self.assertEqual(
            self.record_ids["replacement"],
            superseded_detail["supersededByRecordId"],  # type: ignore[index]
        )
        # same-subject auto-conflict links both directions with conflictsWith;
        # supersession adds a supersedes relation from the replacement record.
        inbound_kinds = [
            row["relationKind"] for row in superseded_detail["inboundRelations"]  # type: ignore[index]
        ]
        self.assertIn("supersedes", inbound_kinds)
        self.assertIn("conflictsWith", inbound_kinds)

    def test_work_list_and_detail(self) -> None:
        status, payload = self.get("/api/work")
        self.assertEqual(200, status)
        self.assertEqual(1, payload["total"])  # type: ignore[index]
        status, payload = self.get("/api/work/" + self.work_item_id)
        self.assertEqual(200, status)
        self.assertEqual("修复冲刺接地偏移", payload["title"])  # type: ignore[index]
        self.assertEqual("blocked", payload["status"])  # type: ignore[index]
        self.assertEqual("录制斜面回归用例", payload["nextAction"])  # type: ignore[index]
        self.assertEqual(2, len(payload["todos"]))  # type: ignore[index]
        self.assertIn(ASSET, payload["assetPaths"])  # type: ignore[index]
        self.assertIn(self.child_node_id, payload["nodeIds"])  # type: ignore[index]

    def test_search_finds_unicode_content(self) -> None:
        # unicode61 tokenizer keeps CJK runs as single tokens, so the query
        # must be a full token of the indexed text.
        status, payload = self.get("/api/search?q=" + quote("主角默认生命值"))
        self.assertEqual(200, status)
        ids = [hit["record"]["recordId"] for hit in payload["items"]]  # type: ignore[index]
        self.assertIn(self.record_ids["valid"], ids)

    def test_unicode_round_trip(self) -> None:
        status, payload = self.get("/api/record/" + self.record_ids["valid"])
        self.assertEqual(200, status)
        self.assertIn("难度曲线", payload["body"])  # type: ignore[index]
        _, node = self.get("/api/node/" + self.child_node_id)
        self.assertEqual("/project/战斗系统", node["node"]["path"])  # type: ignore[index]


class ReadOnlyProofTests(KnowledgeViewServerFixture):
    def _database_fingerprint(self, path: Path) -> tuple[str, dict[str, int], int]:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        with open_project_memory_database(path, readonly=True) as connection:
            counts = {
                "memory_records": int(
                    connection.execute("SELECT COUNT(*) FROM memory_records").fetchone()[0]
                ),
                "memory_status_events": int(
                    connection.execute("SELECT COUNT(*) FROM memory_status_events").fetchone()[0]
                ),
                "knowledge_nodes": int(
                    connection.execute("SELECT COUNT(*) FROM knowledge_nodes").fetchone()[0]
                ),
                "active_work_items": int(
                    connection.execute("SELECT COUNT(*) FROM active_work_items").fetchone()[0]
                ),
            }
            data_version = int(connection.execute("PRAGMA data_version").fetchone()[0])
        return digest, counts, data_version

    def test_exercising_every_route_does_not_modify_the_database(self) -> None:
        before = self._database_fingerprint(self.memory_path)
        routes = [
            "/api/status",
            "/api/tree?limit=200",
            "/api/tree?parent=" + self.child_node_id,
            "/api/node/" + self.child_node_id,
            "/api/records?limit=50",
            "/api/records?status=stale&limit=10",
            "/api/record/" + self.record_ids["valid"],
            "/api/record/" + self.record_ids["superseded"],
            "/api/work",
            "/api/work/" + self.work_item_id,
            "/api/search?q=" + quote("主角默认生命值"),
            "/",
        ]
        for route in routes:
            status, _ = self.get(route)
            self.assertEqual(200, status, route)
        after = self._database_fingerprint(self.memory_path)
        self.assertEqual(before, after)

    def test_memory_connection_is_readonly_at_sqlite_level(self) -> None:
        service = KnowledgeViewReadService(self.config)
        with service._memory_connection() as connection:  # noqa: SLF001
            with self.assertRaises(sqlite3.OperationalError) as context:
                connection.execute(
                    "INSERT INTO memory_records(record_id) VALUES ('nope')"
                )
            self.assertIn("readonly", str(context.exception).lower())

    def test_asset_connection_is_readonly_at_sqlite_level(self) -> None:
        service = KnowledgeViewReadService(self.config)
        with service._asset_connection() as connection:  # noqa: SLF001
            assert connection is not None
            with self.assertRaises(sqlite3.OperationalError) as context:
                connection.execute("DELETE FROM assets")
            self.assertIn("readonly", str(context.exception).lower())


class FailureModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ueak_knowledge_view_missing_")
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    def test_non_loopback_host_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            KnowledgeViewConfig(host="0.0.0.0", port=0)

    def test_missing_memory_database_reports_clear_error(self) -> None:
        config = KnowledgeViewConfig(
            memory_database=self.root / "不存在" / "memory.sqlite3",
            database=None,
            project_key=PROJECT,
            port=0,
        )
        server = make_server(config)
        host, port = server.server_address[0], server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            status, payload = _request(host, port, "GET", "/api/records")
            self.assertEqual(500, status)
            self.assertEqual("memoryDatabaseMissing", payload["error"]["code"])  # type: ignore[index]
            status, payload = _request(host, port, "GET", "/api/status")
            self.assertEqual(200, status)
            self.assertFalse(payload["memoryDatabase"]["present"])  # type: ignore[index]
            self.assertEqual(
                "memoryDatabaseMissing", payload["memoryDatabase"]["error"]  # type: ignore[index]
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=10)

    def test_schema_mismatch_is_reported_not_migrated(self) -> None:
        database_path = self.root / "wrong-schema.sqlite3"
        connection = sqlite3.connect(database_path)
        connection.execute("PRAGMA user_version = 99")
        connection.commit()
        connection.close()
        config = KnowledgeViewConfig(
            memory_database=database_path,
            database=None,
            project_key=PROJECT,
            port=0,
        )
        server = make_server(config)
        host, port = server.server_address[0], server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            status, payload = _request(host, port, "GET", "/api/records")
            self.assertEqual(500, status)
            self.assertEqual("memorySchemaMismatch", payload["error"]["code"])  # type: ignore[index]
            # the wrong-schema database must remain untouched (still v99, no tables added)
            check = sqlite3.connect(database_path)
            version = int(check.execute("PRAGMA user_version").fetchone()[0])
            tables = check.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0]
            check.close()
            self.assertEqual(99, version)
            self.assertEqual(0, tables)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=10)


if __name__ == "__main__":
    unittest.main()
