"""V2 visualization endpoints tests (Track V, feature/knowledge-web-view).

Covers the frozen V2 contract from
docs/Plans/UEAGENTKIT_V2_KNOWLEDGE_VISUALIZATION_DETAILED_PLAN_20260829.md:

- /api/graph   (bounded, root-required, direction-aware BFS, node-limit truncation)
- /api/impact  (inbound consumers + countsByKind + paging)
- /api/coverage(counts only, zero-record nodes included, totals before paging)
- /api/timeline(recordUpdated default; statusChanged opt-in)
- /api/stale   (groupBy nodePath/scope/recordType/ageBucket, sample record ids)

Plus the V1 security regression applied to every new route (405 / 400 / 404 /
read-only proof). All assertions are deterministic; fixture DBs are built in
setUp with the same style as test_knowledge_view.py.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.database import open_database  # noqa: E402
from ue_agent_kit.knowledge_view import (  # noqa: E402
    GRAPH_STRESS_LIMIT,
    KnowledgeViewConfig,
    make_server,
)
from ue_agent_kit.memory_tree import KnowledgeNodeDraft, create_knowledge_node  # noqa: E402
from ue_agent_kit.project_memory import (  # noqa: E402
    MemoryRecordDraft,
    MemoryRecordType,
    MemoryScope,
    MemoryScopeType,
    MemorySourceKind,
    create_memory_record,
    open_project_memory_database,
)

PROJECT = "测试项目"
A = "/Game/Maps/MapA"
B = "/Game/Blueprints/BP_B"
C = "/Game/Blueprints/BP_C"
D = "/Game/Characters/BP_D"
E = "/Game/Misc/Isolated"
F = "/Game/Misc/Consumed"
EXT = "/Engine/External/Shared"
UNICODE = "/Game/角色/主角"


def _request(host: str, port: int, method: str, path: str) -> tuple[int, dict[str, object] | bytes]:
    connection = http.client.HTTPConnection(host, port, timeout=15)
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


class VisualizationServerFixture(unittest.TestCase):
    """Loopback server over deterministic memory + asset fixture DBs."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ueak_v2_")
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

    # ------------------------------------------------------------------
    # memory fixture
    # ------------------------------------------------------------------

    def _seed_memory_database(self) -> None:
        now = datetime.now(timezone.utc)
        with open_project_memory_database(self.memory_path) as connection:
            root = create_knowledge_node(
                connection,
                KnowledgeNodeDraft(
                    project_key=PROJECT,
                    path="/project",
                    node_type="project",
                    title="项目根",
                    summary="项目根节点",
                ),
            )
            combat = create_knowledge_node(
                connection,
                KnowledgeNodeDraft(
                    project_key=PROJECT,
                    path="/project/combat",
                    node_type="system",
                    title="战斗系统",
                    summary="角色战斗与数值",
                    parent_node_id=root.node_id,
                ),
            )
            ui = create_knowledge_node(
                connection,
                KnowledgeNodeDraft(
                    project_key=PROJECT,
                    path="/project/ui",
                    node_type="system",
                    title="界面系统",
                    summary="零记录节点（盲区样例）",
                    parent_node_id=root.node_id,
                ),
            )
            self.root_node_id = root.node_id
            self.combat_node_id = combat.node_id
            self.ui_node_id = ui.node_id

            drafts = {
                "valid_a": MemoryRecordDraft(
                    project_key=PROJECT,
                    record_type=MemoryRecordType.PROJECT_FACT,
                    subject_key="asset:combat:hp",
                    title="主角默认生命值",
                    body="主角默认生命值为 100。",
                    source_kind=MemorySourceKind.USER_CONFIRMED,
                    confidence=0.95,
                    node_id=combat.node_id,
                ),
                "valid_b": MemoryRecordDraft(
                    project_key=PROJECT,
                    record_type=MemoryRecordType.PROJECT_FACT,
                    subject_key="asset:combat:dash",
                    title="冲刺距离",
                    body="冲刺距离 3 米。",
                    source_kind=MemorySourceKind.USER_CONFIRMED,
                    confidence=0.9,
                    node_id=combat.node_id,
                ),
                "stale": MemoryRecordDraft(
                    project_key=PROJECT,
                    record_type=MemoryRecordType.KNOWN_ISSUE,
                    subject_key="asset:combat:offset",
                    title="冲刺接地偏移",
                    body="冲刺动画在斜面接地偏移 5cm。",
                    source_kind=MemorySourceKind.TOOL_OBSERVED,
                    confidence=0.8,
                    node_id=combat.node_id,
                    scopes=(
                        MemoryScope(MemoryScopeType.ASSET, "asset:combat"),
                    ),
                ),
                "conflicted": MemoryRecordDraft(
                    project_key=PROJECT,
                    record_type=MemoryRecordType.DECISION_RECORD,
                    subject_key="decision:curve",
                    title="难度曲线方案",
                    body="存在两个互相冲突的难度曲线方案。",
                    source_kind=MemorySourceKind.USER_CONFIRMED,
                    confidence=0.7,
                    node_id=combat.node_id,
                    scopes=(
                        MemoryScope(MemoryScopeType.ASSET, "decision:curve"),
                    ),
                ),
                "unverified": MemoryRecordDraft(
                    project_key=PROJECT,
                    record_type=MemoryRecordType.PROJECT_FACT,
                    subject_key="asset:combat:armor",
                    title="护甲减伤公式",
                    body="护甲减伤公式尚未验证。",
                    source_kind=MemorySourceKind.MODEL_INFERRED,
                    confidence=0.4,
                    node_id=combat.node_id,
                ),
            }
            self.record_ids: dict[str, str] = {}
            for key, draft in drafts.items():
                record = create_memory_record(connection, draft)
                self.record_ids[key] = record.record_id

            superseded = create_memory_record(
                connection,
                MemoryRecordDraft(
                    project_key=PROJECT,
                    record_type=MemoryRecordType.PROJECT_FACT,
                    subject_key="rule:names",
                    title="旧版冲刺规则",
                    body="旧版冲刺规则，已被新规则取代。",
                    source_kind=MemorySourceKind.USER_CONFIRMED,
                    confidence=1.0,
                    node_id=combat.node_id,
                    scopes=(
                        MemoryScope(MemoryScopeType.ASSET, "rule:names"),
                    ),
                ),
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
                "UPDATE memory_records SET status = 'superseded' WHERE record_id = ?",
                (self.record_ids["superseded"],),
            )
            # deterministic ages for the stale/conflicted/superseded input set
            age_updates = {
                "stale": timedelta(days=2),
                "conflicted": timedelta(days=15),
                "superseded": timedelta(days=100),
            }
            for key, delta in age_updates.items():
                stamp = (now - delta).isoformat()
                connection.execute(
                    "UPDATE memory_records SET updated_at_utc = ? WHERE record_id = ?",
                    (stamp, self.record_ids[key]),
                )
            # injected status-change events (deterministic timestamps)
            connection.execute(
                "INSERT INTO memory_status_events"
                "(record_id, from_status, to_status, reason, changed_at_utc)"
                " VALUES (?, 'valid', 'stale', '测试注入的状态事件', ?)",
                (self.record_ids["stale"], (now - timedelta(days=5)).isoformat()),
            )
            connection.execute(
                "INSERT INTO memory_status_events"
                "(record_id, from_status, to_status, reason, changed_at_utc)"
                " VALUES (?, 'valid', 'superseded', '测试注入的取代事件', ?)",
                (
                    self.record_ids["superseded"],
                    (now - timedelta(days=4)).isoformat(),
                ),
            )
            connection.commit()

    # ------------------------------------------------------------------
    # asset fixture
    # ------------------------------------------------------------------

    def _insert_asset(
        self,
        connection: sqlite3.Connection,
        asset_path: str,
        asset_name: str,
        asset_class: str = "Blueprint",
    ) -> None:
        connection.execute(
            """
            INSERT INTO assets(
                asset_path, package_name, asset_name, asset_class, blueprint_type,
                parent_class, generated_class, status, revision_value, package_guid,
                file_size, modified_utc, content_sha256, package_dirty, schema_version,
                exporter_version, profile, canonical_sha256, canonical_relpath,
                bpctx_relpath, summary_json, indexed_at_utc
            ) VALUES (?, '游戏', ?, ?, 'BlueprintClass',
                'Object', '/Script/游戏.Example_C', 0, 'rev-1', 'guid-1',
                1024, '2026-08-29T00:00:00Z', 'sha256-1', 0, '1.1',
                '1', 'default', 'canonical-1', 'canonical/example.json',
                '', '{}', '2026-08-29T00:00:00Z')
            """,
            (asset_path, asset_name, asset_class),
        )

    def _insert_reference(
        self,
        connection: sqlite3.Connection,
        source_path: str,
        target_path: str,
        kind: str,
        stable_id: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO references_table(
                asset_id, stable_id, kind, source_symbol_id, target_symbol_id,
                target_kind, target_name, target_asset_path, target_path,
                graph_guid, graph_name, node_guid, node_class, node_title, details_json
            ) SELECT id, ?, ?, '', '', 'Blueprint', ?, ?,
                '', '', '', '', '', '', '{}'
            FROM assets WHERE asset_path = ?
            """,
            (stable_id, kind, target_path, target_path, source_path),
        )

    def _seed_asset_database(self) -> None:
        with open_database(self.asset_path) as connection:
            for path, name in (
                (A, "MapA"),
                (B, "BP_B"),
                (C, "BP_C"),
                (D, "BP_D"),
                (E, "Isolated"),
                (F, "Consumed"),
                (UNICODE, "主角"),
            ):
                self._insert_asset(
                    connection,
                    path,
                    name,
                    "World" if path == A else "Blueprint",
                )
            for index in range(3):
                self._insert_asset(
                    connection,
                    f"/Game/Consumers/X{index + 1}",
                    f"X{index + 1}",
                    "Blueprint",
                )
            self._insert_reference(connection, A, B, "hardReference", "ref-1")
            self._insert_reference(connection, A, B, "softReference", "ref-2")
            self._insert_reference(connection, A, C, "hardReference", "ref-3")
            self._insert_reference(connection, C, D, "hardReference", "ref-4")
            self._insert_reference(connection, B, A, "hardReference", "ref-5")
            self._insert_reference(connection, A, A, "softReference", "ref-6")
            self._insert_reference(connection, A, EXT, "hardReference", "ref-7")
            self._insert_reference(connection, UNICODE, A, "hardReference", "ref-8")
            for index in range(3):
                self._insert_reference(
                    connection,
                    f"/Game/Consumers/X{index + 1}",
                    F,
                    "hardReference",
                    f"ref-c{index}",
                )
            connection.commit()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def get(self, path: str) -> tuple[int, dict[str, object] | bytes]:
        return _request(self.host, self.port, "GET", path)

    def _graph(self, **params: str) -> tuple[int, dict[str, object]]:
        query = "&".join(f"{key}={quote(value)}" for key, value in params.items())
        status, payload = self.get(f"/api/graph?{query}")
        assert isinstance(payload, dict)
        return status, payload


class GraphTests(VisualizationServerFixture):
    def test_root_is_required(self) -> None:
        status, payload = self._graph(root="")
        self.assertEqual(400, status)
        self.assertEqual("badRequest", payload["error"]["code"])  # type: ignore[index]

    def test_missing_root_asset_returns_404(self) -> None:
        status, payload = self._graph(root="/Game/Does/Not/Exist")
        self.assertEqual(404, status)
        self.assertEqual("assetNotFound", payload["error"]["code"])  # type: ignore[index]

    def test_depth_zero_returns_root_only(self) -> None:
        status, payload = self._graph(root=A, depth="0")
        self.assertEqual(200, status)
        self.assertEqual(1, payload["meta"]["nodeCount"])  # type: ignore[index]
        self.assertEqual(0, payload["meta"]["edgeCount"])  # type: ignore[index]
        self.assertIsNone(payload["truncated"])  # type: ignore[index]
        self.assertTrue(payload["nodes"][0]["root"])  # type: ignore[index]

    def test_outgoing_aggregates_kinds_counts_and_self_loop(self) -> None:
        status, payload = self._graph(root=A, depth="1", direction="outgoing")
        self.assertEqual(200, status)
        meta = payload["meta"]  # type: ignore[index]
        self.assertEqual(3, meta["nodeCount"])
        self.assertEqual(3, meta["edgeCount"])
        self.assertFalse(meta["truncated"])
        self.assertIsNone(payload["truncated"])  # type: ignore[index]
        paths = [node["assetPath"] for node in payload["nodes"]]  # type: ignore[index]
        self.assertEqual([A, B, C], paths)
        edges = {edge["source"] + "->" + edge["target"]: edge for edge in payload["edges"]}  # type: ignore[index]
        self.assertEqual({"hardReference", "softReference"}, set(edges[A + "->" + B]["kinds"]))
        self.assertEqual(2, edges[A + "->" + B]["referenceCount"])
        self.assertFalse(edges[A + "->" + B]["selfLoop"])
        self.assertTrue(edges[A + "->" + A]["selfLoop"])
        self.assertNotIn(A + "->" + EXT, edges)  # external target is dropped
        by_path = {node["assetPath"]: node for node in payload["nodes"]}  # type: ignore[index]
        self.assertEqual(4, by_path[A]["referenceCount"])  # B(2) + C(1) + self(1)
        self.assertEqual(2, by_path[B]["referenceCount"])

    def test_depth_two_reaches_back_edge(self) -> None:
        status, payload = self._graph(root=A, depth="2", direction="outgoing")
        self.assertEqual(200, status)
        meta = payload["meta"]  # type: ignore[index]
        self.assertEqual(4, meta["nodeCount"])
        self.assertEqual(5, meta["edgeCount"])
        paths = {node["assetPath"] for node in payload["nodes"]}  # type: ignore[index]
        self.assertEqual({A, B, C, D}, paths)
        edges = {edge["source"] + "->" + edge["target"] for edge in payload["edges"]}  # type: ignore[index]
        self.assertIn(B + "->" + A, edges)
        self.assertIn(C + "->" + D, edges)

    def test_incoming_direction(self) -> None:
        status, payload = self._graph(root=B, depth="1", direction="incoming")
        self.assertEqual(200, status)
        meta = payload["meta"]  # type: ignore[index]
        self.assertEqual(2, meta["nodeCount"])
        self.assertEqual(1, meta["edgeCount"])
        by_path = {node["assetPath"]: node for node in payload["nodes"]}  # type: ignore[index]
        self.assertTrue(by_path[B]["root"])
        edge = payload["edges"][0]  # type: ignore[index]
        self.assertEqual(A, edge["source"])
        self.assertEqual(B, edge["target"])
        self.assertEqual({"hardReference", "softReference"}, set(edge["kinds"]))

    def test_both_direction_union(self) -> None:
        status, payload = self._graph(root=A, depth="1", direction="both")
        self.assertEqual(200, status)
        meta = payload["meta"]  # type: ignore[index]
        self.assertEqual(4, meta["nodeCount"])
        self.assertEqual(5, meta["edgeCount"])
        edges = {edge["source"] + "->" + edge["target"] for edge in payload["edges"]}  # type: ignore[index]
        self.assertIn(B + "->" + A, edges)  # back edge found via target anchor
        self.assertIn(UNICODE + "->" + A, edges)  # consumer found via target anchor

    def test_node_limit_truncation_is_in_band(self) -> None:
        status, payload = self._graph(root=A, depth="2", limit="2")
        self.assertEqual(200, status)
        self.assertEqual(2, payload["meta"]["nodeCount"])  # type: ignore[index]
        self.assertTrue(payload["meta"]["truncated"])  # type: ignore[index]
        truncated = payload["truncated"]  # type: ignore[index]
        self.assertEqual("nodeLimit", truncated["reason"])
        self.assertEqual(2, truncated["limit"])
        self.assertEqual(2, truncated["count"])

    def test_stress_required_above_1000_and_caps(self) -> None:
        status, payload = self._graph(root=A, limit="1001")
        self.assertEqual(400, status)
        self.assertIn("stress=1", payload["error"]["message"])  # type: ignore[index]
        status, payload = self._graph(root=A, limit="1001", stress="1")
        self.assertEqual(200, status)
        status, payload = self._graph(root=A, limit=str(GRAPH_STRESS_LIMIT + 1), stress="1")
        self.assertEqual(400, status)
        status, payload = self._graph(root=A, stress="2")
        self.assertEqual(400, status)

    def test_invalid_depth_and_direction_rejected(self) -> None:
        for depth in ("-1", "4"):
            status, payload = self._graph(root=A, depth=depth)
            self.assertEqual(400, status, depth)
        for direction in ("sideways", ""):
            status, payload = self._graph(root=A, direction=direction)
            self.assertEqual(400, status, direction)

    def test_unknown_parameter_rejected(self) -> None:
        status, payload = self.get("/api/graph?root=" + quote(A) + "&evil=1")
        self.assertEqual(400, status)
        self.assertEqual("badRequest", payload["error"]["code"])  # type: ignore[index]

    def test_unicode_root_round_trip(self) -> None:
        status, payload = self._graph(root=UNICODE, depth="1")
        self.assertEqual(200, status)
        self.assertEqual(UNICODE, payload["meta"]["root"])  # type: ignore[index]
        self.assertEqual(2, payload["meta"]["nodeCount"])  # type: ignore[index]
        paths = {node["assetPath"] for node in payload["nodes"]}  # type: ignore[index]
        self.assertEqual({UNICODE, A}, paths)

    def test_valid_root_without_edges_returns_root_only(self) -> None:
        status, payload = self._graph(root=E, depth="1", direction="both")
        self.assertEqual(200, status)
        self.assertEqual(1, payload["meta"]["nodeCount"])  # type: ignore[index]
        self.assertEqual(0, payload["meta"]["edgeCount"])  # type: ignore[index]
        self.assertIsNone(payload["truncated"])  # type: ignore[index]

    def test_limit_bounds_rejected(self) -> None:
        status, payload = self._graph(root=A, limit="0")
        self.assertEqual(400, status)
        status, payload = self._graph(root=A, limit="abc")
        self.assertEqual(400, status)


class ImpactTests(VisualizationServerFixture):
    def test_consumers_kinds_and_counts(self) -> None:
        status, payload = self.get("/api/impact/" + quote(B, safe=""))
        self.assertEqual(200, status)
        payload = payload  # type: ignore[assignment]
        self.assertEqual(B, payload["asset"]["assetPath"])  # type: ignore[index]
        self.assertEqual(1, payload["totalConsumerAssets"])  # type: ignore[index]
        self.assertEqual({"hardReference": 1, "softReference": 1}, payload["countsByKind"])  # type: ignore[index]
        consumer = payload["consumers"][0]  # type: ignore[index]
        self.assertEqual(A, consumer["assetPath"])
        self.assertEqual({"hardReference", "softReference"}, set(consumer["kinds"]))
        self.assertEqual(2, consumer["referenceCount"])
        self.assertIsNone(payload["truncated"])  # type: ignore[index]

    def test_missing_asset_returns_404(self) -> None:
        status, payload = self.get("/api/impact/" + quote("/Game/No/Asset", safe=""))
        self.assertEqual(404, status)
        self.assertEqual("assetNotFound", payload["error"]["code"])  # type: ignore[index]

    def test_empty_consumers_is_valid(self) -> None:
        status, payload = self.get("/api/impact/" + quote(E, safe=""))
        self.assertEqual(200, status)
        self.assertEqual([], payload["consumers"])  # type: ignore[index]
        self.assertEqual({}, payload["countsByKind"])  # type: ignore[index]
        self.assertEqual(0, payload["totalConsumerAssets"])  # type: ignore[index]

    def test_kind_filter(self) -> None:
        status, payload = self.get("/api/impact/" + quote(B, safe="") + "?kind=hardReference")
        self.assertEqual(200, status)
        self.assertEqual({"hardReference": 1}, payload["countsByKind"])  # type: ignore[index]
        self.assertEqual(1, payload["totalConsumerAssets"])  # type: ignore[index]

    def test_paging_truncation(self) -> None:
        page1 = self.get("/api/impact/" + quote(F, safe="") + "?limit=2&offset=0")
        self.assertEqual(200, page1[0])
        payload = page1[1]  # type: ignore[assignment]
        self.assertEqual(3, payload["totalConsumerAssets"])  # type: ignore[index]
        self.assertEqual(2, len(payload["consumers"]))  # type: ignore[index]
        self.assertEqual("limit", payload["truncated"]["reason"])  # type: ignore[index]
        page2 = self.get("/api/impact/" + quote(F, safe="") + "?limit=2&offset=2")
        payload2 = page2[1]  # type: ignore[assignment]
        self.assertEqual(1, len(payload2["consumers"]))  # type: ignore[index]
        self.assertIsNone(payload2["truncated"])  # type: ignore[index]

    def test_unicode_asset_segment(self) -> None:
        status, payload = self.get("/api/impact/" + quote(UNICODE, safe=""))
        self.assertEqual(200, status)
        self.assertEqual(UNICODE, payload["asset"]["assetPath"])  # type: ignore[index]

    def test_slash_route_segment_rejected(self) -> None:
        status, payload = self.get("/api/impact/" + quote(A))
        self.assertEqual(400, status)
        self.assertEqual("badRequest", payload["error"]["code"])  # type: ignore[index]

    def test_unknown_parameter_rejected(self) -> None:
        status, payload = self.get("/api/impact/" + quote(B, safe="") + "?evil=1")
        self.assertEqual(400, status)
        self.assertEqual("badRequest", payload["error"]["code"])  # type: ignore[index]

    def test_page_bounds_enforced(self) -> None:
        status, payload = self.get("/api/impact/" + quote(B, safe="") + "?limit=201")
        self.assertEqual(400, status)
        status, payload = self.get("/api/impact/" + quote(B, safe="") + "?offset=-1")
        self.assertEqual(400, status)


class CoverageTests(VisualizationServerFixture):
    def test_status_splits_and_zero_record_nodes(self) -> None:
        status, payload = self.get("/api/coverage?limit=200")
        self.assertEqual(200, status)
        nodes = {node["path"]: node for node in payload["nodes"]}  # type: ignore[index]
        self.assertIn("/project/ui", nodes)
        self.assertEqual(0, nodes["/project/ui"]["recordCount"])
        self.assertIsNone(nodes["/project/ui"]["lastUpdatedUtc"])
        combat = nodes["/project/combat"]
        self.assertEqual(6, combat["recordCount"])
        self.assertEqual(2, combat["validCount"])
        self.assertEqual(1, combat["staleCount"])
        self.assertEqual(1, combat["conflictedCount"])
        self.assertEqual(1, combat["supersededCount"])
        self.assertEqual(1, combat["unverifiedCount"])
        self.assertIsNotNone(combat["lastUpdatedUtc"])

    def test_totals_before_paging(self) -> None:
        status, payload = self.get("/api/coverage?limit=1&offset=0")
        self.assertEqual(200, status)
        totals = payload["totals"]  # type: ignore[index]
        self.assertEqual(6, totals["recordCount"])
        self.assertEqual(2, totals["validCount"])
        self.assertEqual(1, totals["staleCount"])
        self.assertEqual(1, totals["conflictedCount"])
        self.assertEqual(1, totals["supersededCount"])
        self.assertEqual(1, totals["unverifiedCount"])
        self.assertEqual(1, len(payload["nodes"]))  # type: ignore[index]
        self.assertEqual("limit", payload["truncated"]["reason"])  # type: ignore[index]

    def test_path_prefix_filter(self) -> None:
        status, payload = self.get("/api/coverage?pathPrefix=/project/combat")
        self.assertEqual(200, status)
        self.assertEqual(1, len(payload["nodes"]))  # type: ignore[index]
        self.assertEqual("/project/combat", payload["nodes"][0]["path"])  # type: ignore[index]
        self.assertEqual(6, payload["totals"]["recordCount"])  # type: ignore[index]

    def test_no_percentage_emitted(self) -> None:
        status, payload = self.get("/api/coverage")
        self.assertEqual(200, status)
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("percent", serialized)
        self.assertNotIn("ratio", serialized)

    def test_page_bounds_enforced(self) -> None:
        status, payload = self.get("/api/coverage?limit=201")
        self.assertEqual(400, status)
        status, payload = self.get("/api/coverage?offset=-1")
        self.assertEqual(400, status)


class TimelineTests(VisualizationServerFixture):
    def test_record_updated_default(self) -> None:
        status, payload = self.get("/api/timeline?limit=200")
        self.assertEqual(200, status)
        events = payload["events"]  # type: ignore[index]
        self.assertEqual(6, len(events))
        self.assertTrue(all(event["kind"] == "recordUpdated" for event in events))
        timestamps = [event["timestampUtc"] for event in events]
        self.assertEqual(sorted(timestamps, reverse=True), timestamps)
        self.assertTrue(all(event["eventId"].endswith("#updated") for event in events))
        self.assertIsNone(events[0]["fromStatus"])

    def test_status_changed_is_opt_in(self) -> None:
        status, payload = self.get("/api/timeline?limit=200&includeStatusEvents=true")
        self.assertEqual(200, status)
        events = payload["events"]  # type: ignore[index]
        kinds = [event["kind"] for event in events]
        self.assertIn("statusChanged", kinds)
        # 6 creation events + 2 injected transitions (stale/superseded)
        self.assertEqual(14, len(events))
        changed = [event for event in events if event["kind"] == "statusChanged"]
        self.assertTrue(all(event["fromStatus"] is not None for event in changed))
        self.assertTrue(all(event["toStatus"] is not None for event in changed))
        timestamps = [event["timestampUtc"] for event in events]
        self.assertEqual(sorted(timestamps, reverse=True), timestamps)

    def test_filters(self) -> None:
        status, payload = self.get("/api/timeline?recordType=decisionRecord")
        self.assertEqual(200, status)
        events = payload["events"]  # type: ignore[index]
        self.assertEqual(1, len(events))
        self.assertEqual("decisionRecord", events[0]["recordType"])
        status, payload = self.get("/api/timeline?status=stale")
        self.assertEqual(200, status)
        events = payload["events"]  # type: ignore[index]
        self.assertEqual(1, len(events))
        self.assertEqual("stale", events[0]["status"])

    def test_invalid_filters_rejected(self) -> None:
        status, payload = self.get("/api/timeline?status=not-a-status")
        self.assertEqual(400, status)
        status, payload = self.get("/api/timeline?recordType=nope")
        self.assertEqual(400, status)

    def test_paging(self) -> None:
        status, payload = self.get("/api/timeline?limit=4&offset=0")
        self.assertEqual(200, status)
        self.assertEqual(4, len(payload["events"]))  # type: ignore[index]
        self.assertEqual("limit", payload["truncated"]["reason"])  # type: ignore[index]
        status, payload = self.get("/api/timeline?limit=4&offset=4")
        self.assertEqual(200, status)
        self.assertEqual(2, len(payload["events"]))  # type: ignore[index]
        self.assertIsNone(payload["truncated"])  # type: ignore[index]


class StaleTests(VisualizationServerFixture):
    def test_group_by_node_path(self) -> None:
        status, payload = self.get("/api/stale?groupBy=nodePath")
        self.assertEqual(200, status)
        buckets = payload["buckets"]  # type: ignore[index]
        self.assertEqual(1, len(buckets))
        bucket = buckets[0]
        self.assertEqual("/project/combat", bucket["groupKey"])
        self.assertEqual(3, bucket["recordCount"])
        self.assertEqual(
            {"stale": 1, "conflicted": 1, "superseded": 1}, bucket["byStatus"]
        )
        self.assertEqual(3, len(bucket["sampleRecordIds"]))

    def test_group_by_scope(self) -> None:
        status, payload = self.get("/api/stale?groupBy=scope")
        self.assertEqual(200, status)
        buckets = {bucket["groupKey"]: bucket for bucket in payload["buckets"]}  # type: ignore[index]
        self.assertEqual({"asset:combat", "decision:curve", "rule:names"}, set(buckets))
        for key, bucket in buckets.items():
            self.assertEqual(1, bucket["recordCount"], key)
            self.assertEqual(3, payload["totals"]["recordCount"])  # type: ignore[index]

    def test_group_by_record_type(self) -> None:
        status, payload = self.get("/api/stale?groupBy=recordType")
        self.assertEqual(200, status)
        buckets = {bucket["groupKey"]: bucket for bucket in payload["buckets"]}  # type: ignore[index]
        self.assertEqual({"knownIssue", "decisionRecord", "projectFact"}, set(buckets))
        self.assertEqual(3, payload["totals"]["recordCount"])  # type: ignore[index]

    def test_group_by_age_bucket(self) -> None:
        status, payload = self.get("/api/stale?groupBy=ageBucket")
        self.assertEqual(200, status)
        buckets = {bucket["groupKey"]: bucket for bucket in payload["buckets"]}  # type: ignore[index]
        self.assertEqual({"0-7d", "8-30d", "90d+"}, set(buckets))
        self.assertEqual(1, buckets["0-7d"]["recordCount"])
        self.assertEqual(1, buckets["8-30d"]["recordCount"])
        self.assertEqual(1, buckets["90d+"]["recordCount"])

    def test_totals_and_sample_bound(self) -> None:
        status, payload = self.get("/api/stale?groupBy=recordType&limit=1")
        self.assertEqual(200, status)
        self.assertEqual(3, payload["totals"]["recordCount"])  # type: ignore[index]
        self.assertEqual(
            {"stale": 1, "conflicted": 1, "superseded": 1},
            payload["totals"]["byStatus"],  # type: ignore[index]
        )
        self.assertEqual("limit", payload["truncated"]["reason"])  # type: ignore[index]
        self.assertLessEqual(len(payload["buckets"][0]["sampleRecordIds"]), 5)  # type: ignore[index]

    def test_invalid_group_by_rejected(self) -> None:
        status, payload = self.get("/api/stale?groupBy=whatever")
        self.assertEqual(400, status)
        self.assertEqual("badRequest", payload["error"]["code"])  # type: ignore[index]


class SecurityRegressionTests(VisualizationServerFixture):
    V2_ROUTES = (
        "/api/graph?root=" + quote(A),
        "/api/impact/" + quote(B, safe=""),
        "/api/coverage",
        "/api/timeline",
        "/api/stale",
    )

    def test_mutation_methods_answer_405_on_v2_routes(self) -> None:
        for route in self.V2_ROUTES:
            for method in ("POST", "PUT", "PATCH", "DELETE"):
                status, payload = _request(self.host, self.port, method, route)
                self.assertEqual(405, status, f"{method} {route}")
                self.assertEqual("methodNotAllowed", payload["error"]["code"])  # type: ignore[index]

    def test_unknown_query_parameter_rejected_on_all_v2_routes(self) -> None:
        cases = (
            ("/api/graph?root=" + quote(A) + "&sql=DROP%20TABLE", "/api/graph"),
            ("/api/impact/" + quote(B, safe="") + "?sql=1", "/api/impact"),
            ("/api/coverage?sql=1", "/api/coverage"),
            ("/api/timeline?sql=1", "/api/timeline"),
            ("/api/stale?sql=1", "/api/stale"),
        )
        for path, _label in cases:
            status, payload = self.get(path)
            self.assertEqual(400, status, path)
            self.assertEqual("badRequest", payload["error"]["code"])  # type: ignore[index]

    def test_no_traceback_leak(self) -> None:
        status, payload = self.get("/api/impact/" + quote(B, safe="") + "?limit=abc")
        self.assertEqual(400, status)
        message = str(payload["error"]["message"])  # type: ignore[index]
        self.assertNotIn("Traceback", message)
        self.assertNotIn("File ", message)

    def _db_fingerprint(self, path: Path) -> tuple[str, str, int]:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        with open_database(path, readonly=True, migrate=False) as connection:
            counts = json.dumps(
                {
                    table: int(
                        connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    )
                    for table in ("assets", "references_table")
                },
                sort_keys=True,
            )
            data_version = int(connection.execute("PRAGMA data_version").fetchone()[0])
        return digest, counts, data_version

    def _memory_fingerprint(self) -> tuple[str, int]:
        connection = sqlite3.connect(
            f"file:{self.memory_path}?mode=ro", uri=True, timeout=10
        )
        try:
            data_version = int(connection.execute("PRAGMA data_version").fetchone()[0])
        finally:
            connection.close()
        digest = hashlib.sha256(self.memory_path.read_bytes()).hexdigest()
        return digest, data_version

    def test_exercising_v2_routes_does_not_modify_databases(self) -> None:
        before_memory = self._memory_fingerprint()
        before_asset = self._db_fingerprint(self.asset_path)

        self.get("/api/graph?root=" + quote(A) + "&depth=2&direction=both")
        self.get("/api/graph?root=" + quote(A) + "&limit=1001&stress=1")
        self.get("/api/impact/" + quote(B, safe="") + "?kind=hardReference")
        self.get("/api/impact/" + quote(F, safe="") + "?limit=2&offset=2")
        self.get("/api/coverage?pathPrefix=/project")
        self.get("/api/timeline?includeStatusEvents=true")
        self.get("/api/stale?groupBy=scope")
        self.get("/api/stale?groupBy=ageBucket")

        after_memory = self._memory_fingerprint()
        after_asset = self._db_fingerprint(self.asset_path)
        self.assertEqual(before_memory, after_memory)
        self.assertEqual(before_asset, after_asset)


if __name__ == "__main__":
    unittest.main()
