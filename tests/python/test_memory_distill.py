from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.database import open_database, set_metadata  # noqa: E402
from ue_agent_kit.memory_distill import (  # noqa: E402
    DISTILL_HARD_MAX_EVENTS,
    DISTILL_MAX_ARTIFACT_BYTES,
    MemoryDistillationService,
    RULE_SEMANTIC_DIFF,
)
from ue_agent_kit.memory_l0 import (  # noqa: E402
    MemoryEvidenceChainDraft,
    MemoryL0CaptureService,
    MemoryL0EventDraft,
)
from ue_agent_kit.memory_service import ProjectMemoryService  # noqa: E402
from ue_agent_kit.memory_tree import KnowledgeNodeDraft  # noqa: E402
from ue_agent_kit.project_memory import (  # noqa: E402
    MemoryRecordDraft,
    MemoryRecordType,
    MemorySourceKind,
    MemoryStatus,
    open_project_memory_database,
)


PROJECT = "distill-project"
ASSET = "/Game/Characters/Hero/DA_HeroStats.DA_HeroStats"
ASSET2 = "/Game/Combat/DA_Combat.DA_Combat"
POLICY = {
    "schemaVersion": "1.0",
    "allowedProjectNames": ["distill-project"],
    "allowedAssetRoots": ["/Game/"],
    "allowedAssetClasses": [],
    "allowedOperations": [],
    "commitEnabled": True,
}


def _policy_digest(policy: dict) -> str:
    return hashlib.sha256(
        json.dumps(policy, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class MemoryDistillationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ueak_memory_distill_")
        self.root = Path(self.temporary.name)
        self.artifact_root = self.root / "workflow"
        self.artifact_root.mkdir()
        self.memory_path = self.root / "memory.sqlite3"
        self.index_path = self.root / "index.sqlite3"
        self.policy_path = self.root / "policy.json"
        self.policy_path.write_text(
            json.dumps(
                POLICY,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        self.memory = ProjectMemoryService(database_path=self.memory_path, project_key=PROJECT)
        root = self.memory.create_node(
            KnowledgeNodeDraft(
                project_key=PROJECT,
                path="/project",
                node_type="project",
                title=PROJECT,
                summary="Distill test root.",
            )
        )
        self.root_node_id = root.node_id
        self.l0 = MemoryL0CaptureService(
            database_path=self.memory_path,
            project_key=PROJECT,
            artifact_root=self.artifact_root,
        )
        self.service = MemoryDistillationService(
            memory_database=self.memory_path,
            project_key=PROJECT,
            artifact_root=self.artifact_root,
            index_database=self.index_path,
            policy_path=self.policy_path,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _artifact(self, relative: str, payload: dict) -> Path:
        path = self.artifact_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        return path

    def _verified_event(self, *, relative: str, asset_paths=(), revision: str = "") -> MemoryL0EventDraft:
        payload: dict = {
            "schemaVersion": "1.0",
            "projectName": PROJECT,
            "assetPath": asset_paths[0] if asset_paths else ASSET,
            "assetRevisions": [
                {
                    "assetPath": asset_paths[0] if asset_paths else ASSET,
                    "revision": revision,
                    "revisionStable": True,
                }
            ]
            if revision
            else [],
        }
        path = self._artifact(relative, payload)
        return self.l0.artifact_draft(
            artifact_path=path,
            event_kind="checkpoint_set",
            lifecycle_state="verified",
            outcome="success",
            asset_paths=asset_paths or (ASSET,),
            change_set_id="cs_verified",
            details={"checkpointSetId": "cps_verified"},
        )

    def test_verified_event_produces_deterministic_l1_and_rerun_reuses(self) -> None:
        revision = "sha256:" + "a" * 64
        draft = self._verified_event(relative="verified/one.json", asset_paths=(ASSET,), revision=revision)
        self.l0.append_event(draft)
        first = self.service.distill(max_events=100)
        self.assertEqual(first.selected_count, 1)
        self.assertEqual(first.evaluated_count, 1)
        self.assertEqual(first.distilled_count, 1)
        self.assertEqual(first.produced_record_count, 1)
        self.assertEqual(first.failed_count, 0)
        self.assertEqual(first.deferred_count, 0)
        self.assertEqual(first.pending_after, 0)
        record_id = first.produced_record_ids[0]
        self.assertRegex(record_id, r"^mem_[0-9a-f]{32}$")

        second = self.service.distill(max_events=100)
        self.assertEqual(second.selected_count, 0)
        self.assertEqual(second.reused_record_count, 0)

        # Crash-equivalent: L1 exists but event still pending -> reuse + mark.
        event_id = self.l0.list_events()[0].event_id
        with open_project_memory_database(self.memory_path) as connection:
            connection.execute(
                "UPDATE memory_l0_events SET distilled = 0 WHERE event_id = ?",
                (event_id,),
            )
            connection.commit()
        crash = self.service.distill(max_events=100)
        self.assertEqual(crash.produced_record_count, 0)
        self.assertEqual(crash.reused_record_count, 1)
        self.assertEqual(crash.reused_record_ids, (record_id,))
        self.assertEqual(crash.distilled_count, 1)

    def test_missing_or_tampered_artifact_stays_pending(self) -> None:
        revision = "sha256:" + "b" * 64
        draft = self._verified_event(relative="verified/missing.json", asset_paths=(ASSET,), revision=revision)
        self.l0.append_event(draft)
        artifact = self.artifact_root / "verified" / "missing.json"
        artifact.write_text('{"tampered": true}', encoding="utf-8")
        result = self.service.distill(max_events=100)
        self.assertEqual(result.selected_count, 1)
        self.assertEqual(result.deferred_count, 1)
        self.assertEqual(result.produced_record_count, 0)
        self.assertEqual(result.distilled_count, 0)
        self.assertEqual(result.pending_after, 1)
        self.assertEqual(result.deferred[0]["reasonCode"], "distill-artifact-digest-mismatch")

    def test_rejection_without_policy_digest_never_produces_project_rule(self) -> None:
        self.l0.capture_rejection(
            operation="apply_asset_property_live",
            error_code="revision-mismatch",
            asset_paths=(ASSET,),
            change_set_id="cs_reject",
            target_identity="asset-property:IntValue",
        )
        result = self.service.distill(max_events=100)
        self.assertEqual(result.distilled_count, 1)
        self.assertEqual(result.produced_record_count, 1)
        with open_project_memory_database(self.memory_path) as connection:
            record_type = connection.execute(
                "SELECT record_type FROM memory_records WHERE record_id = ?",
                (result.produced_record_ids[0],),
            ).fetchone()[0]
        self.assertEqual(record_type, MemoryRecordType.KNOWN_ISSUE.value)
        self.assertNotEqual(record_type, MemoryRecordType.PROJECT_RULE.value)

    def test_policy_rejection_with_exact_digest_produces_project_rule(self) -> None:
        digest = _policy_digest(POLICY)
        self.l0.capture_rejection(
            operation="apply_asset_property_live",
            error_code="policy-rejected",
            asset_paths=(ASSET,),
            change_set_id="cs_policy",
            target_identity="asset-property:IntValue",
            policy_digest=digest,
        )
        result = self.service.distill(max_events=100)
        self.assertEqual(result.distilled_count, 1)
        self.assertEqual(result.produced_record_count, 1, result.failed)
        with open_project_memory_database(self.memory_path) as connection:
            row = connection.execute(
                "SELECT record_type, details_json FROM memory_records WHERE record_id = ?",
                (result.produced_record_ids[0],),
            ).fetchone()
        self.assertEqual(row[0], MemoryRecordType.PROJECT_RULE.value)
        self.assertIn("policyDigest", json.loads(row[1])["distillation"])

    def test_policy_rejection_without_digest_never_becomes_rule_and_old_event_is_evaluated(self) -> None:
        self.l0.capture_rejection(
            operation="apply_asset_property_live",
            error_code="policy-rejected",
            asset_paths=(ASSET,),
            change_set_id="cs_policy_old",
            target_identity="asset-property:IntValue",
        )
        result = self.service.distill(max_events=100)
        self.assertEqual(result.produced_record_count, 1)
        with open_project_memory_database(self.memory_path) as connection:
            record_type = connection.execute(
                "SELECT record_type FROM memory_records WHERE record_id = ?",
                (result.produced_record_ids[0],),
            ).fetchone()[0]
        self.assertEqual(record_type, MemoryRecordType.KNOWN_ISSUE.value)

    def test_policy_digest_change_marks_policy_rule_stale(self) -> None:
        digest = _policy_digest(POLICY)
        self.l0.capture_rejection(
            operation="apply_asset_property_live",
            error_code="policy-rejected",
            asset_paths=(ASSET,),
            policy_digest=digest,
        )
        first = self.service.distill(max_events=100)
        self.assertEqual(first.produced_record_count, 1, first.failed)
        record_id = first.produced_record_ids[0]
        validation = self.service.validate_source_bindings()
        self.assertNotIn(record_id, validation["staleRecordIds"])

        changed_policy = dict(POLICY)
        changed_policy["commitEnabled"] = False
        self.policy_path.write_text(
            json.dumps(
                changed_policy,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        validation = self.service.validate_source_bindings()
        self.assertIn(record_id, validation["staleRecordIds"])
        with open_project_memory_database(self.memory_path) as connection:
            status = connection.execute(
                "SELECT status FROM memory_records WHERE record_id = ?",
                (record_id,),
            ).fetchone()[0]
        self.assertEqual(status, MemoryStatus.STALE.value)

    def _build_index(self, revision: str) -> None:
        """Build/refresh a minimal immutable index reporting one asset Revision."""
        with open_database(self.index_path) as connection:
            set_metadata(connection, "project_key", PROJECT)
            connection.execute(
                """
                INSERT INTO assets(
                    asset_path, revision_value, schema_version,
                    exporter_version, profile, indexed_at_utc,
                    canonical_sha256, canonical_relpath
                )
                VALUES (?, ?, '1.1', '0.0.0-test', 'logic', '2026-08-31T00:00:00Z',
                        'sha256:' || ?, 'canonical/test.json')
                ON CONFLICT(asset_path) DO UPDATE SET revision_value = excluded.revision_value
                """,
                (ASSET, revision, revision.removeprefix("sha256:")),
            )
            connection.commit()

    def test_asset_revision_change_makes_distilled_fact_stale(self) -> None:
        revision = "sha256:" + "a" * 64
        self.l0.append_event(
            self._verified_event(relative="verified/src.json", asset_paths=(ASSET,), revision=revision)
        )
        result = self.service.distill(max_events=100)
        record_id = result.produced_record_ids[0]

        # Index still agrees with the exact bound Revision -> fact stays current.
        self._build_index(revision)
        validation = self.service.validate_source_bindings()
        self.assertNotIn(record_id, validation["staleRecordIds"])
        self.assertEqual(validation["assetBindingsChecked"], 1)
        self.assertTrue(validation["indexDatabasePresent"])

        # Index reports a different Revision -> the bound fact becomes stale.
        self._build_index("sha256:" + "b" * 64)
        validation = self.service.validate_source_bindings()
        self.assertIn(record_id, validation["staleRecordIds"])
        self.assertEqual(validation["reasons"][record_id]["reason"], "revision-set-mismatch")
        self.assertEqual(validation["reasons"][record_id]["kind"], "assetRevision")
        self.assertEqual(
            validation["reasons"][record_id]["mismatchedRevisions"][0]["currentRevision"],
            "sha256:" + "b" * 64,
        )
        with open_project_memory_database(self.memory_path) as connection:
            status = connection.execute(
                "SELECT status FROM memory_records WHERE record_id = ?", (record_id,)
            ).fetchone()[0]
        self.assertEqual(status, MemoryStatus.STALE.value)

    def test_missing_index_revision_also_invalidates(self) -> None:
        revision = "sha256:" + "c" * 64
        self.l0.append_event(
            self._verified_event(relative="verified/gone.json", asset_paths=(ASSET,), revision=revision)
        )
        result = self.service.distill(max_events=100)
        record_id = result.produced_record_ids[0]
        # Index no longer knows the asset at all.
        self._build_index(revision)
        with open_database(self.index_path) as connection:
            connection.execute("DELETE FROM assets WHERE asset_path = ?", (ASSET,))
            connection.commit()
        validation = self.service.validate_source_bindings()
        self.assertIn(record_id, validation["staleRecordIds"])
        self.assertEqual(validation["reasons"][record_id]["missingAssetPaths"], [ASSET])

    def test_supersession_requires_matching_live_write_evidence(self) -> None:
        change_set_payload = {
            "schemaVersion": "2.0",
            "projectName": PROJECT,
            "changeSetId": "cs_no_evidence",
            "status": "no-op",
            "operations": [
                {
                    "receipt": "live_old",
                    "planId": "plan_old",
                    "assetPath": ASSET,
                    "operation": "setVariableDefault",
                    "status": "superseded",
                    "stableTargetKey": "blueprint-variable:Health",
                    "target": {"variableName": "Health", "propertyPath": ""},
                    "oldValue": 50,
                    "expectedValue": 50,
                    "afterValue": 75,
                    "newValue": 75,
                }
            ],
        }
        path = self._artifact("change-sets/cs_no_evidence.json", change_set_payload)
        self.l0.append_event(
            self.l0.artifact_draft(
                artifact_path=path,
                event_kind="change_set",
                lifecycle_state="superseded",
                outcome="superseded",
                asset_paths=(ASSET,),
                change_set_id="cs_no_evidence",
                details={"operationCount": 1},
            )
        )
        result = self.service.distill(max_events=100)
        # Change Set values alone are never enough: no durable live-write
        # journal -> no decisionRecord.
        self.assertEqual(result.produced_record_count, 0)
        self.assertEqual(result.distilled_count, 1)

        # A durable journal with different values must not corroborate either.
        live_payload = {
            "schemaVersion": "1.0",
            "projectName": PROJECT,
            "assetPath": ASSET,
            "operation": "setVariableDefault",
            "beforeValue": 50,
            "afterValue": 999,
            "target": {"variableName": "Health"},
            "stableTargetKey": "blueprint-variable:Health",
        }
        live_path = self._artifact("live-write-journal/mismatch.json", live_payload)
        self.l0.append_event(
            self.l0.artifact_draft(
                artifact_path=live_path,
                event_kind="live_write",
                lifecycle_state="superseded",
                outcome="superseded",
                asset_paths=(ASSET,),
                change_set_id="cs_no_evidence",
                details={"operation": "setVariableDefault"},
            )
        )
        with open_project_memory_database(self.memory_path) as connection:
            connection.execute(
                "UPDATE memory_l0_events SET distilled = 0 WHERE change_set_id = ?",
                ("cs_no_evidence",),
            )
            connection.commit()
        second = self.service.distill(max_events=100)
        self.assertEqual(second.produced_record_count, 0)
        self.assertEqual(second.pending_after, 0)

    def test_semantic_diff_verified_produces_fact_and_artifact_binding(self) -> None:
        revision = "sha256:" + "c" * 64
        payload = {
            "schemaVersion": "1.0",
            "assetPath": ASSET,
            "assetRevisions": [{"assetPath": ASSET, "revision": revision, "revisionStable": True}],
            "summary": {"missingExpectedCount": 0, "unexpectedCount": 0, "analysisGapCount": 0, "totalAssetCount": 1},
        }
        path = self._artifact("semantic/verified.json", payload)
        draft = self.l0.artifact_draft(
            artifact_path=path,
            event_kind="semantic_diff",
            lifecycle_state="verified",
            outcome="success",
            asset_paths=(ASSET,),
            change_set_id="cs_semantic",
            details={"changeSetId": "cs_semantic"},
        )
        self.l0.append_event(draft)
        result = self.service.distill(max_events=100)
        self.assertEqual(result.produced_record_count, 1)
        with open_project_memory_database(self.memory_path) as connection:
            row = connection.execute(
                "SELECT record_type, source_ref FROM memory_records WHERE record_id = ?",
                (result.produced_record_ids[0],),
            ).fetchone()
        self.assertEqual(row[0], MemoryRecordType.PROJECT_FACT.value)
        self.assertTrue(row[1].startswith(f"distill:{RULE_SEMANTIC_DIFF}:"))

    def test_live_write_resident_only_is_evaluated_with_no_output(self) -> None:
        payload = {"schemaVersion": "1.0", "assetPath": ASSET, "operation": "setVariableDefault"}
        path = self._artifact("live/resident.json", payload)
        draft = self.l0.artifact_draft(
            artifact_path=path,
            event_kind="live_write",
            lifecycle_state="applied",
            outcome="success",
            asset_paths=(ASSET,),
            change_set_id="cs_live",
            details={"operation": "setVariableDefault"},
        )
        self.l0.append_event(draft)
        result = self.service.distill(max_events=100)
        self.assertEqual(result.distilled_count, 1)
        self.assertEqual(result.produced_record_count, 0)
        self.assertEqual(result.pending_after, 0)

    def test_knowledge_node_placement_creates_parents_and_no_orphan(self) -> None:
        revision = "sha256:" + "d" * 64
        self.l0.append_event(
            self._verified_event(
                relative="verified/tree.json",
                asset_paths=(ASSET,),
                revision=revision,
            )
        )
        result = self.service.distill(max_events=100)
        self.assertEqual(result.produced_record_count, 1, result.failed)
        record_id = result.produced_record_ids[0]
        with open_project_memory_database(self.memory_path) as connection:
            node = connection.execute(
                """
                SELECT n.path, n.parent_node_id, r.node_id
                FROM memory_records AS r
                JOIN knowledge_nodes AS n ON n.node_id = r.node_id
                WHERE r.record_id = ?
                """,
                (record_id,),
            ).fetchone()
            self.assertIsNotNone(node)
            self.assertEqual(node[0], "/project/content/characters/hero")
            self.assertNotEqual(node[1], "")
            # Every node except root has a parent; root is /project.
            rows = connection.execute(
                "SELECT path, parent_node_id FROM knowledge_nodes ORDER BY path"
            ).fetchall()
            paths = {str(row[0]) for row in rows}
            self.assertIn("/project", paths)
            self.assertIn("/project/content", paths)
            self.assertIn("/project/content/characters", paths)
            self.assertIn("/project/content/characters/hero", paths)
            for row in rows:
                if str(row[0]) == "/project":
                    self.assertIsNone(row[1])
                else:
                    self.assertIsNotNone(row[1])

    def _second_fixture(self) -> tuple[MemoryDistillationService, MemoryL0CaptureService, Path]:
        """Build a second independent fixture with identical deterministic inputs."""
        holder = tempfile.TemporaryDirectory(prefix="ueak_memory_distill_second_")
        self.addCleanup(holder.cleanup)
        root = Path(holder.name)
        artifact_root = root / "workflow"
        artifact_root.mkdir()
        memory_path = root / "memory.sqlite3"
        policy_path = root / "policy.json"
        policy_path.write_text(
            json.dumps(POLICY, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        memory = ProjectMemoryService(database_path=memory_path, project_key=PROJECT)
        memory.create_node(
            KnowledgeNodeDraft(
                project_key=PROJECT,
                path="/project",
                node_type="project",
                title=PROJECT,
                summary="Second distill fixture root.",
            )
        )
        l0 = MemoryL0CaptureService(
            database_path=memory_path,
            project_key=PROJECT,
            artifact_root=artifact_root,
        )
        service = MemoryDistillationService(
            memory_database=memory_path,
            project_key=PROJECT,
            artifact_root=artifact_root,
            index_database=root / "index.sqlite3",
            policy_path=policy_path,
        )
        return service, l0, artifact_root

    def _node_for_record(self, memory_path: Path, record_id: str) -> tuple[str, str]:
        with open_project_memory_database(memory_path) as connection:
            row = connection.execute(
                """
                SELECT n.node_id, n.path
                FROM knowledge_nodes AS n
                JOIN memory_records AS r ON r.node_id = n.node_id
                WHERE r.record_id = ?
                """,
                (record_id,),
            ).fetchone()
        return str(row[0]), str(row[1])

    def test_knowledge_node_ids_are_deterministic_across_runs(self) -> None:
        revision = "sha256:" + "d" * 64
        self.l0.append_event(
            self._verified_event(relative="verified/det.json", asset_paths=(ASSET,), revision=revision)
        )
        first = self.service.distill(max_events=100)
        self.assertEqual(first.produced_record_count, 1, first.failed)
        first_node = self._node_for_record(self.memory_path, first.produced_record_ids[0])

        second_service, second_l0, second_artifacts = self._second_fixture()
        path = second_artifacts / "verified" / "det.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schemaVersion": "1.0",
                    "projectName": PROJECT,
                    "assetPath": ASSET,
                    "assetRevisions": [
                        {"assetPath": ASSET, "revision": revision, "revisionStable": True}
                    ],
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        second_l0.append_event(
            second_l0.artifact_draft(
                artifact_path=path,
                event_kind="checkpoint_set",
                lifecycle_state="verified",
                outcome="success",
                asset_paths=(ASSET,),
                change_set_id="cs_verified",
                details={"checkpointSetId": "cps_verified"},
            )
        )
        second = second_service.distill(max_events=100)
        self.assertEqual(second.produced_record_count, 1, second.failed)
        second_node = self._node_for_record(
            second_service.memory_database, second.produced_record_ids[0]
        )

        # Automatic Knowledge-node placement must be reproducible, not kn_<uuid4>.
        self.assertRegex(first_node[0], r"^kn_[0-9a-f]{32}$")
        self.assertEqual(first_node[1], "/project/content/characters/hero")
        self.assertEqual(first_node, second_node)

    def test_reuse_requires_exact_content_provenance_and_evidence(self) -> None:
        revision = "sha256:" + "a" * 64
        self.l0.append_event(
            self._verified_event(
                relative="verified/reuse.json", asset_paths=(ASSET,), revision=revision
            )
        )
        first = self.service.distill(max_events=100)
        record_id = first.produced_record_ids[0]

        # Crash-equivalent replay against the untouched record is a safe reuse.
        event_id = self.l0.list_events()[0].event_id
        self._reset_event(event_id)
        replay = self.service.distill(max_events=100)
        self.assertEqual(replay.reused_record_ids, (record_id,))
        self.assertEqual(replay.failed_count, 0)

        # Same deterministic id + same distill: source_ref, but different
        # content/evidence digests -> must fail closed, not be overwritten.
        with open_project_memory_database(self.memory_path) as connection:
            connection.execute(
                "UPDATE memory_records SET content_sha256 = ?, evidence_sha256 = ? WHERE record_id = ?",
                ("sha256:" + "0" * 64, "sha256:" + "1" * 64, record_id),
            )
            connection.commit()
        self._reset_event(event_id)
        tampered = self.service.distill(max_events=100)
        self.assertEqual(tampered.failed_count, 1)
        self.assertEqual(tampered.failed[0]["reasonCode"], "distill-record-content-mismatch")
        self.assertEqual(tampered.reused_record_count, 0)
        self.assertEqual(tampered.pending_after, 1)

    def test_reuse_rejects_record_from_a_different_source(self) -> None:
        revision = "sha256:" + "b" * 64
        self.l0.append_event(
            self._verified_event(
                relative="verified/collide.json", asset_paths=(ASSET,), revision=revision
            )
        )
        first = self.service.distill(max_events=100)
        record_id = first.produced_record_ids[0]
        event_id = self.l0.list_events()[0].event_id

        # Replace the distilled record with a human-authored one at the exact
        # same deterministic id.
        with open_project_memory_database(self.memory_path) as connection:
            connection.execute("DELETE FROM memory_status_events WHERE record_id = ?", (record_id,))
            connection.execute("DELETE FROM memory_revisions WHERE record_id = ?", (record_id,))
            connection.execute("DELETE FROM memory_artifacts WHERE record_id = ?", (record_id,))
            connection.execute("DELETE FROM memory_records WHERE record_id = ?", (record_id,))
            connection.commit()
        self.memory.add_record(
            MemoryRecordDraft(
                project_key=PROJECT,
                record_type=MemoryRecordType.PROJECT_FACT,
                subject_key="manual:entry",
                title="Human-authored record",
                body="Not produced by M3 distillation.",
                source_kind=MemorySourceKind.USER_CONFIRMED,
                source_ref="manual:entry",
                record_id=record_id,
                node_id=self.root_node_id,
            )
        )
        self._reset_event(event_id)
        collided = self.service.distill(max_events=100)
        self.assertEqual(collided.failed_count, 1)
        self.assertEqual(collided.failed[0]["reasonCode"], "distill-record-collision")
        self.assertEqual(collided.reused_record_count, 0)
        self.assertEqual(collided.pending_after, 1)

    def _reset_event(self, event_id: str) -> None:
        """Simulate a crash after L1 creation but before the distilled flag."""
        with open_project_memory_database(self.memory_path) as connection:
            connection.execute(
                "UPDATE memory_l0_events SET distilled = 0 WHERE event_id = ?",
                (event_id,),
            )
            connection.commit()

    def test_project_wide_issue_attaches_to_project(self) -> None:
        self.l0.capture_rejection(
            operation="unknown_tool",
            error_code="workflow-error",
            asset_paths=(),
            change_set_id="cs_global",
            target_identity="",
        )
        result = self.service.distill(max_events=100)
        record_id = result.produced_record_ids[0]
        with open_project_memory_database(self.memory_path) as connection:
            node_path = connection.execute(
                "SELECT path FROM knowledge_nodes WHERE node_id = (SELECT node_id FROM memory_records WHERE record_id = ?)",
                (record_id,),
            ).fetchone()[0]
        self.assertEqual(node_path, "/project")

    def test_evidence_chain_evaluates_only_linked_events(self) -> None:
        chain = self.l0.create_evidence_chain(
            MemoryEvidenceChainDraft(
                project_key=PROJECT,
                hypothesis="Writer preserved the requested value.",
                context={"changeSetId": "cs_chain"},
            )
        )
        revision = "sha256:" + "e" * 64
        support_draft = self._verified_event(
            relative="verified/chain.json",
            asset_paths=(ASSET,),
            revision=revision,
        )
        support_draft = replace(support_draft, hypothesis_id=chain.chain_id)
        self.l0.append_event(support_draft)
        # Unlinked rejection must not influence the chain.
        unlinked = self.l0.rejection_draft(
            operation="apply_asset_property_live",
            error_code="policy-rejected",
            asset_paths=(ASSET,),
        )
        self.l0.append_event(unlinked)
        verdicts = self.service.evaluate_evidence_chains()
        self.assertEqual(verdicts[chain.chain_id], "supported")

    def test_evidence_chain_mixed_is_inconclusive(self) -> None:
        chain = self.l0.create_evidence_chain(
            MemoryEvidenceChainDraft(
                project_key=PROJECT,
                hypothesis="Mixed chain.",
            )
        )
        revision = "sha256:" + "f" * 64
        support = replace(
            self._verified_event(relative="verified/mixed.json", asset_paths=(ASSET,), revision=revision),
            hypothesis_id=chain.chain_id,
        )
        self.l0.append_event(support)
        reject = self.l0.rejection_draft(
            operation="apply_asset_property_live",
            error_code="revision-mismatch",
            asset_paths=(ASSET,),
        )
        self.l0.append_event(replace(reject, hypothesis_id=chain.chain_id))
        verdicts = self.service.evaluate_evidence_chains()
        self.assertEqual(verdicts[chain.chain_id], "inconclusive")

    def test_supersession_change_set_produces_decision_record(self) -> None:
        # The M2 live-write journal (not Change Set serialization) is the
        # durable supersession source. Fixture uses exact before/after values.
        # It is appended first so the live_write event itself can carry the
        # superseded durable evidence.
        live_payload = {
            "schemaVersion": "1.0",
            "projectName": PROJECT,
            "assetPath": ASSET,
            "operation": "setVariableDefault",
            "valueKind": "int",
            "beforeValue": 50,
            "afterValue": 75,
            "target": {"variableName": "Health"},
            "stableTargetKey": "blueprint-variable:Health",
        }
        live_path = self._artifact("live-write-journal/live_superseded.json", live_payload)
        live_draft = self.l0.artifact_draft(
            artifact_path=live_path,
            event_kind="live_write",
            lifecycle_state="superseded",
            outcome="superseded",
            asset_paths=(ASSET,),
            change_set_id="cs_super",
            details={"operation": "setVariableDefault"},
        )
        self.l0.append_event(live_draft)
        change_set_payload = {
            "schemaVersion": "2.0",
            "projectName": PROJECT,
            "changeSetId": "cs_super",
            "status": "no-op",
            "operations": [
                {
                    "receipt": "live_old",
                    "planId": "plan_old",
                    "assetPath": ASSET,
                    "operation": "setVariableDefault",
                    "status": "superseded",
                    "stableTargetKey": "blueprint-variable:Health",
                    "target": {"variableName": "Health", "propertyPath": ""},
                    "oldValue": 50,
                    "expectedValue": 50,
                    "afterValue": 75,
                    "newValue": 75,
                }
            ],
        }
        path = self._artifact("change-sets/cs_super.json", change_set_payload)
        draft = self.l0.artifact_draft(
            artifact_path=path,
            event_kind="change_set",
            lifecycle_state="superseded",
            outcome="superseded",
            asset_paths=(ASSET,),
            change_set_id="cs_super",
            details={"operationCount": 1},
        )
        self.l0.append_event(draft)
        result = self.service.distill(max_events=100)
        self.assertEqual(result.produced_record_count, 1)
        with open_project_memory_database(self.memory_path) as connection:
            record_type = connection.execute(
                "SELECT record_type FROM memory_records WHERE record_id = ?",
                (result.produced_record_ids[0],),
            ).fetchone()[0]
        self.assertEqual(record_type, MemoryRecordType.DECISION_RECORD.value)

    def test_impact_analysis_is_source_gated(self) -> None:
        payload = {
            "schemaVersion": "1.0",
            "assetPath": ASSET,
            "assetRevisions": [{"assetPath": ASSET, "revision": "sha256:" + "1" * 64, "revisionStable": True}],
        }
        path = self._artifact("impact/analysis.json", payload)
        # The M2 allowlist does not include impact_analysis; appending must
        # fail, proving M3 does not add a production capture source.
        # artifact_draft defers allowlist validation until append; assert append too.
        draft = self.l0.artifact_draft(
            artifact_path=path,
            event_kind="impact_analysis",
            lifecycle_state="verified",
            outcome="success",
            asset_paths=(ASSET,),
            change_set_id="cs_impact",
            details={},
        )
        with self.assertRaisesRegex(ValueError, "allowlist"):
            self.l0.append_event(draft)

    def test_100_event_hard_bound(self) -> None:
        for index in range(DISTILL_HARD_MAX_EVENTS + 5):
            payload = {"schemaVersion": "1.0", "assetPath": ASSET}
            path = self._artifact(f"bound/{index}.json", payload)
            self.l0.append_event(
                self.l0.artifact_draft(
                    artifact_path=path,
                    event_kind="live_write",
                    lifecycle_state="applied",
                    outcome="success",
                    asset_paths=(ASSET,),
                    change_set_id=f"cs_bound_{index}",
                    details={"operationCount": 1},
                )
            )
        # live_write resident events evaluate no-output, all 105 get marked if selected.
        with self.assertRaisesRegex(ValueError, "100"):
            self.service.distill(max_events=DISTILL_HARD_MAX_EVENTS + 1)
        result = self.service.distill(max_events=DISTILL_HARD_MAX_EVENTS)
        self.assertEqual(result.selected_count, DISTILL_HARD_MAX_EVENTS)
        self.assertEqual(result.distilled_count, DISTILL_HARD_MAX_EVENTS)
        self.assertEqual(result.pending_after, 5)

    def test_oversized_artifact_is_deferred_not_crashed(self) -> None:
        revision = "sha256:" + "g" * 64
        draft = self._verified_event(relative="verified/large.json", asset_paths=(ASSET,), revision=revision)
        self.l0.append_event(draft)
        artifact = self.artifact_root / "verified" / "large.json"
        artifact.write_bytes(b"x" * (DISTILL_MAX_ARTIFACT_BYTES + 1))
        result = self.service.distill(max_events=100)
        self.assertEqual(result.deferred_count, 1)
        self.assertEqual(result.pending_after, 1)
        self.assertEqual(result.deferred[0]["reasonCode"], "distill-artifact-oversized")

    def test_distill_does_not_touch_memory_disabled_or_create_threads(self) -> None:
        # No background/daemon/thread creation by the distiller module.
        import threading

        before = [thread.name for thread in threading.enumerate()]
        self.service.distill(max_events=10)
        after = [thread.name for thread in threading.enumerate()]
        self.assertEqual(before, after)
        # Service is only constructible explicitly; MCP request path should not
        # instantiate it (covered by isolation test in CLI / task context).
        self.assertIsNotNone(self.service)


if __name__ == "__main__":
    unittest.main()
