from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = ROOT / "src"
PYTHON_TESTS = ROOT / "tests" / "python"
for path in (SRC_ROOT, PYTHON_TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from test_indexer_queries import ASSET_A, REVISION_A, REVISION_B, make_asset, write_export  # noqa: E402
from ue_agent_kit.active_work import WorkItemDraft  # noqa: E402
from ue_agent_kit.cli import build_parser, main as cli_main, run  # noqa: E402
from ue_agent_kit.database import open_database  # noqa: E402
from ue_agent_kit.indexer import build_index  # noqa: E402
from ue_agent_kit.memory_reports import build_memory_audit_report  # noqa: E402
from ue_agent_kit.memory_service import ProjectMemoryService  # noqa: E402
from ue_agent_kit.memory_tasks import TaskOutcome, TaskOutcomeDraft  # noqa: E402
from ue_agent_kit.memory_tree import KnowledgeNodeDraft  # noqa: E402
from ue_agent_kit.project_memory import (  # noqa: E402
    MemoryRecordDraft,
    MemoryRecordType,
    MemoryRevision,
    MemoryScope,
    MemoryScopeType,
    MemorySourceKind,
)


PROJECT = "测试项目"


class ProjectMemoryCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ueak_memory_cli_")
        self.root = Path(self.temporary.name)
        self.memory_path = self.root / "memory" / "project-memory.sqlite3"
        self.index_path = self.root / "index" / "ueak.sqlite3"
        self.service = ProjectMemoryService(
            database_path=self.memory_path,
            project_key=PROJECT,
        )
        self.rule = self.service.add_record(
            MemoryRecordDraft(
                project_key=PROJECT,
                record_type=MemoryRecordType.PROJECT_RULE,
                subject_key="rule:text-format",
                title="Text format",
                body="Tracked text files use UTF-8 without BOM and CRLF.",
                source_kind=MemorySourceKind.USER_CONFIRMED,
                source_ref="test:user-confirmed",
                scopes=(MemoryScope(MemoryScopeType.PROJECT, PROJECT),),
            )
        )
        self.task = self.service.record_task_outcome(
            TaskOutcomeDraft(
                task_key="player-health",
                title="Validate player health",
                conclusion="The player health change was validated and retained.",
                outcome=TaskOutcome.SUCCEEDED,
                patch_ref="patch:player-health",
                backup_manifest_ref="backup-manifest:player-health",
                validation_evidence_ref="validation-evidence:player-health",
                revision_set=(MemoryRevision(ASSET_A, f"sha256:{REVISION_A}"),),
                scopes=(MemoryScope(MemoryScopeType.ASSET, ASSET_A),),
            )
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def parse(self, *arguments: str):
        return build_parser().parse_args(list(arguments))

    def memory_arguments(self) -> list[str]:
        return [
            "--memory-database",
            str(self.memory_path),
            "--project-key",
            PROJECT,
        ]

    def build_index(self, revision: str) -> None:
        export_root = self.root / f"export-{revision[0]}"
        write_export(
            export_root,
            [make_asset(ASSET_A, profile="logic", revision=revision, rich=True)],
        )
        with open_database(self.index_path) as connection:
            result = build_index(connection, export_root, self.index_path)
        self.assertEqual((result.added, result.failed), (1, 0))

    def test_memory_build_context_reports_deterministic_snapshot(self) -> None:
        self.build_index("sha256:" + REVISION_A)
        args = self.parse(
            "memory",
            "build-context",
            *self.memory_arguments(),
            "--index-database",
            str(self.index_path),
        )
        result, code = run(args)
        self.assertEqual(code, 0)
        self.assertEqual(result["tool"], "ue_memory_build_context")
        self.assertEqual(result["projectKey"], PROJECT)
        self.assertEqual(result["builtGeneration"], result["sourceGeneration"])
        self.assertRegex(result["snapshotId"], r"^ctxsnap_[0-9a-f]{32}$")
        self.assertRegex(result["indexSnapshotId"], r"^sha256:")
        self.assertGreaterEqual(result["l3Entries"], 1)
        self.assertLessEqual(result["estimatedTokens"], 800)

    def test_memory_build_context_reuse_is_idempotent_and_stable(self) -> None:
        self.build_index("sha256:" + REVISION_A)
        args = self.parse(
            "memory",
            "build-context",
            *self.memory_arguments(),
            "--index-database",
            str(self.index_path),
        )
        first, first_code = run(args)
        second, second_code = run(args)
        self.assertEqual((first_code, second_code), (0, 0))
        self.assertEqual(first["snapshotId"], second["snapshotId"])
        self.assertFalse(first["reused"])
        self.assertTrue(second["reused"])

    def test_memory_distill_reports_validation_and_chain_verdicts(self) -> None:
        artifact_root = self.root / "workflow"
        artifact_root.mkdir(parents=True, exist_ok=True)
        revision = "sha256:" + REVISION_A
        artifact = artifact_root / "verified" / "cli.json"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(
            json.dumps(
                {
                    "schemaVersion": "1.0",
                    "projectName": PROJECT,
                    "assetPath": ASSET_A,
                    "assetRevisions": [
                        {"assetPath": ASSET_A, "revision": revision, "revisionStable": True}
                    ],
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        policy_path = self.root / "policy.json"
        policy_path.write_text(
            json.dumps(
                {
                    "schemaVersion": "1.0",
                    "allowedProjectNames": [PROJECT],
                    "allowedAssetRoots": ["/Game/"],
                    "allowedAssetClasses": [],
                    "allowedOperations": [],
                    "commitEnabled": True,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        capture = self.service.l0_capture_service(artifact_root=artifact_root)
        capture.append_event(
            capture.artifact_draft(
                artifact_path=artifact,
                event_kind="checkpoint_set",
                lifecycle_state="verified",
                outcome="success",
                asset_paths=(ASSET_A,),
                change_set_id="cs_cli",
                details={"checkpointSetId": "cps_cli"},
            )
        )

        result, code = run(
            self.parse(
                "memory",
                "distill",
                *self.memory_arguments(),
                "--artifact-root",
                str(artifact_root),
                "--index-database",
                str(self.index_path),
                "--policy",
                str(policy_path),
            )
        )
        self.assertEqual(code, 0)
        self.assertEqual(result["selectedCount"], 1)
        self.assertEqual(result["evaluatedCount"], 1)
        self.assertEqual(result["distilledCount"], 1)
        self.assertEqual(result["producedRecordCount"], 1)
        self.assertTrue(str(result["producedRecordIds"][0]).startswith("mem_"))
        self.assertIn("sourceValidation", result)
        self.assertIn("evidenceChainVerdicts", result)
        self.assertEqual(result["sourceValidation"]["staleRecordIds"], [])
        self.assertEqual(result["pendingAfter"], 0)

        # The explicit offline command is idempotent: a rerun has nothing to do.
        rerun, rerun_code = run(
            self.parse(
                "memory",
                "distill",
                *self.memory_arguments(),
                "--artifact-root",
                str(artifact_root),
                "--index-database",
                str(self.index_path),
                "--policy",
                str(policy_path),
            )
        )
        self.assertEqual(rerun_code, 0)
        self.assertEqual(rerun["selectedCount"], 0)
        self.assertEqual(rerun["producedRecordCount"], 0)

    def test_status_search_and_get_use_fixed_project_without_exposing_database_path(self) -> None:
        status, status_code = run(
            self.parse("memory", "status", *self.memory_arguments())
        )
        self.assertEqual(status_code, 0)
        self.assertEqual(status["projectKey"], PROJECT)
        self.assertEqual(status["recordCount"], 2)
        self.assertEqual(status["countsByType"], {"projectRule": 1, "taskRecord": 1})
        self.assertNotIn(str(self.memory_path), json.dumps(status, ensure_ascii=False))

        search, search_code = run(
            self.parse(
                "memory",
                "search",
                "player health",
                *self.memory_arguments(),
                "--record-type",
                "taskRecord",
                "--scope-type",
                "asset",
                "--scope-key",
                ASSET_A,
            )
        )
        self.assertEqual(search_code, 0)
        self.assertEqual(search["resultCount"], 1)
        self.assertEqual(search["items"][0]["record"]["recordId"], self.task.record_id)

        fetched, fetched_code = run(
            self.parse(
                "memory",
                "get",
                self.task.record_id,
                *self.memory_arguments(),
            )
        )
        self.assertEqual(fetched_code, 0)
        self.assertEqual(fetched["record"]["evidenceSha256"], self.task.evidence_sha256)
        self.assertEqual(
            [item["artifactKind"] for item in fetched["record"]["artifacts"]],
            ["patch", "backupManifest", "validationEvidence"],
        )

    def test_validate_marks_revision_bound_task_stale(self) -> None:
        self.build_index(REVISION_B)
        result, exit_code = run(
            self.parse(
                "memory",
                "validate",
                *self.memory_arguments(),
                "--index-database",
                str(self.index_path),
            )
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(result["checkedRecordIds"], [self.task.record_id])
        self.assertEqual(result["staleRecordIds"], [self.task.record_id])
        self.assertEqual(
            result["reasons"][self.task.record_id]["mismatchedRevisions"][0]["currentRevision"],
            f"sha256:{REVISION_B}",
        )

        default_search, _ = run(
            self.parse("memory", "search", "player health", *self.memory_arguments())
        )
        self.assertEqual(default_search["resultCount"], 0)
        stale_search, _ = run(
            self.parse(
                "memory",
                "search",
                "player health",
                *self.memory_arguments(),
                "--status",
                "stale",
            )
        )
        self.assertEqual(stale_search["resultCount"], 1)

    def test_export_writes_portable_complete_audit_with_crlf(self) -> None:
        output = self.root / "reports" / "memory-audit.json"
        result, exit_code = run(
            self.parse(
                "memory",
                "export",
                *self.memory_arguments(),
                "--output",
                str(output),
            )
        )
        self.assertEqual(exit_code, 0)
        self.assertTrue(result["exported"])
        self.assertEqual(result["recordCount"], 2)
        self.assertEqual(result["statusEventCount"], 2)
        self.assertRegex(result["snapshotSha256"], r"^sha256:[0-9a-f]{64}$")

        data = output.read_bytes()
        self.assertFalse(data.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\n", data.replace(b"\r\n", b""))
        report = json.loads(data.decode("utf-8"))
        self.assertEqual(report["tool"], "ue_memory_export")
        self.assertEqual(report["projectKey"], PROJECT)
        self.assertEqual(report["recordCount"], 2)
        self.assertEqual(report["statusEventCount"], 2)
        self.assertEqual(report["nodeCount"], 0)
        self.assertEqual(report["activeWorkCount"], 0)
        self.assertEqual(report["knowledgeNodes"], [])
        self.assertEqual(report["activeWork"], [])
        self.assertEqual(len(report["records"]), 2)
        self.assertEqual(len(report["statusEvents"]), 2)
        self.assertTrue(report["integrity"]["allRecordDigestsVerified"])
        serialized = json.dumps(report, ensure_ascii=False)
        self.assertNotIn(str(self.memory_path), serialized)
        self.assertNotIn(str(self.index_path), serialized)

    def test_audit_appends_knowledge_tree_and_active_work_without_removing_v2_fields(self) -> None:
        root = self.service.create_node(
            KnowledgeNodeDraft(
                project_key=PROJECT,
                path="/project",
                node_type="project",
                title=PROJECT,
                summary="Audit project root.",
            )
        )
        node = self.service.create_node(
            KnowledgeNodeDraft(
                project_key=PROJECT,
                path="/project/combat",
                node_type="system",
                title="Combat",
                summary="Combat knowledge.",
            )
        )
        work = self.service.create_work(
            WorkItemDraft(
                project_key=PROJECT,
                title="Validate combat",
                description="Validate the combat asset.",
                next_action="Run tests.",
                node_ids=(node.node_id,),
                asset_paths=(ASSET_A,),
            )
        )
        work = self.service.add_todo(work_item_id=work.work_item_id, text="Run the smoke test.")
        report = build_memory_audit_report(self.service)

        self.assertEqual(report["recordCount"], 2)
        self.assertEqual(report["statusEventCount"], 2)
        self.assertEqual(report["nodeCount"], 2)
        self.assertEqual(report["activeWorkCount"], 1)
        self.assertEqual(report["countsByWorkStatus"], {"in_progress": 1})
        self.assertEqual([item["path"] for item in report["knowledgeNodes"]], [root.path, node.path])
        exported_work = report["activeWork"][0]
        self.assertEqual(exported_work["workItemId"], work.work_item_id)
        self.assertEqual(exported_work["nodeIds"], [node.node_id])
        self.assertEqual(exported_work["assetPaths"], [ASSET_A])
        self.assertEqual(exported_work["todos"][0]["text"], "Run the smoke test.")
        self.assertIn("records", report)
        self.assertIn("statusEvents", report)
        self.assertRegex(report["integrity"]["snapshotSha256"], r"^sha256:[0-9a-f]{64}$")

        with self.assertRaisesRegex(RuntimeError, "knowledge nodes"):
            build_memory_audit_report(self.service, max_nodes=1)
        second_work = self.service.create_work(
            WorkItemDraft(
                project_key=PROJECT,
                title="Second work item",
                description="Second audit item.",
                next_action="Inspect it.",
            )
        )
        self.assertTrue(second_work.work_item_id)
        with self.assertRaisesRegex(RuntimeError, "Active Work items"):
            build_memory_audit_report(self.service, max_work_items=1)

    def test_audit_snapshot_digest_is_stable_for_unchanged_data(self) -> None:
        first = build_memory_audit_report(self.service)
        second = build_memory_audit_report(self.service)
        self.assertNotEqual(first["generatedAtUtc"], "")
        self.assertEqual(
            first["integrity"]["snapshotSha256"],
            second["integrity"]["snapshotSha256"],
        )

    def test_audit_refuses_partial_snapshot_and_detects_tampering(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "maximum is 1"):
            build_memory_audit_report(self.service, max_records=1)

        with open_database(self.memory_path, readonly=False, migrate=False) as connection:
            connection.execute(
                "UPDATE memory_artifacts SET artifact_ref = ? WHERE record_id = ?",
                ("validation-evidence:tampered", self.task.record_id),
            )
            connection.commit()
        with self.assertRaisesRegex(RuntimeError, "evidence digest mismatch"):
            build_memory_audit_report(self.service)

    def test_cli_main_reports_missing_record_without_traceback(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = cli_main(
                [
                    "memory",
                    "get",
                    "mem_00000000000000000000000000000000",
                    *self.memory_arguments(),
                ]
            )
        self.assertEqual(exit_code, 2)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["error"], "KeyError")
        self.assertFalse(payload["valid"])
        self.assertNotIn(str(self.memory_path), output.getvalue())

    def test_process_smoke_and_runner_keep_utf8_cli_contract(self) -> None:
        cli_source = (SRC_ROOT / "ue_agent_kit" / "cli.py").read_text(encoding="utf-8")
        smoke_source = (ROOT / "tests" / "integration" / "memory_cli_smoke.py").read_text(
            encoding="utf-8"
        )
        runner_source = (ROOT / "scripts" / "TestMemoryCli.ps1").read_text(encoding="utf-8")
        self.assertIn('reconfigure(encoding="utf-8", errors="replace")', cli_source)
        for token in (
            "statusPassed",
            "searchPassed",
            "validationPassed",
            "exportPassed",
            "databasePathsRedacted",
            "auditCrLf",
        ):
            self.assertIn(token, smoke_source)
        self.assertIn("memory_cli_smoke.py", runner_source)

    def test_cli_requires_project_key_and_exposes_no_sql_argument(self) -> None:
        args = self.parse(
            "memory",
            "status",
            "--memory-database",
            str(self.memory_path),
            "--project-key",
            "",
        )
        with self.assertRaisesRegex(ValueError, "project_key"):
            run(args)
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                self.parse(
                    "memory",
                    "search",
                    "player",
                    *self.memory_arguments(),
                    "--sql",
                    "SELECT * FROM memory_records",
                )


if __name__ == "__main__":
    unittest.main()
