from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.change_sets import ChangeSetOperationRecord, ChangeSetRecord  # noqa: E402
from ue_agent_kit.verification_evidence import (  # noqa: E402
    MAX_VERIFICATION_EVIDENCE_RECORDS,
    VerificationEvidenceStore,
)
from ue_agent_kit.verification_trust import (  # noqa: E402
    MAX_REQUIRED_AUTOMATION_TESTS,
    VerificationTrustError,
    build_verification_plan,
    evaluate_trust_verdict,
)


ASSET = "/Game/Tests/DA_Target.DA_Target"
BLUEPRINT = "/Game/Tests/BP_Target.BP_Target"
CONSUMER = "/Game/Tests/WBP_Consumer.WBP_Consumer"
EXTRA = "/Game/Tests/DA_Extra.DA_Extra"
REVISION = "sha256:" + "a" * 64
SESSION = "editor-session-r3"
CHANGE_SET_ID = "cs_r3verification"


class FakeImpactService:
    def __init__(self, response: dict[str, Any] | None = None) -> None:
        self.response = response or {
            "summary": {"truncated": False, "truncationReasons": []},
            "directConsumers": [],
            "indirectConsumers": [],
            "validationTargets": [],
            "analysisGaps": [],
            "risks": [],
        }
        self.calls: list[dict[str, Any]] = []

    def analyze_change_impact(self, affected_assets: list[str], **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"affectedAssets": list(affected_assets), **copy.deepcopy(kwargs)})
        return copy.deepcopy(self.response)


class FakeWorkflow:
    def __init__(
        self,
        root: Path,
        *,
        asset_path: str = ASSET,
        operation: str = "setAssetProperty",
        operation_status: str = "verified",
        semantic: dict[str, Any] | None = None,
        impact: dict[str, Any] | None = None,
        recovery: bool = True,
        current_revision: str = REVISION,
        memory_dirty: bool = False,
    ) -> None:
        self._lock = threading.RLock()
        self.index_service = FakeImpactService(impact)
        self.record = ChangeSetRecord(
            change_set_id=CHANGE_SET_ID,
            task_id="task_r3verification",
            editor_session_id=SESSION,
            title="R3 verification fixture",
            status=operation_status,
            created_at_utc="2026-08-20T00:00:00.000Z",
            updated_at_utc="2026-08-20T00:00:01.000Z",
            operations=[
                ChangeSetOperationRecord(
                    receipt="apply_r3verification",
                    plan_id="plan_r3verification",
                    asset_path=asset_path,
                    operation=operation,
                    transaction_id="transaction-r3",
                    editor_session_id=SESSION,
                    status=operation_status,
                    created_at_utc="2026-08-20T00:00:00.000Z",
                    updated_at_utc="2026-08-20T00:00:01.000Z",
                    save_receipt="save-r3" if operation_status == "verified" else "",
                )
            ],
        )
        self._plans = {
            "plan_r3verification": SimpleNamespace(
                digest="sha256:" + "b" * 64,
                patch={
                    "assets": [
                        {
                            "assetPath": asset_path,
                            "expectedAssetClass": (
                                "/Script/Engine.Blueprint"
                                if operation in {"setVariableDefault", "setComponentProperty", "setPinDefault"}
                                else "/Script/Engine.PrimaryDataAsset"
                            ),
                            "expectedRevision": REVISION,
                            "operations": [
                                {
                                    "operationId": "operation-r3",
                                    "operation": operation,
                                    "target": {"propertyPath": "Value"},
                                    "value": 7,
                                }
                            ],
                        }
                    ]
                },
            )
        }
        plan_record = self._plans["plan_r3verification"]
        plan_record.digest = "sha256:" + hashlib.sha256(
            json.dumps(
                plan_record.patch,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        self.root = root
        plan_directory = self._plan_directory("plan_r3verification")
        plan_directory.mkdir(parents=True, exist_ok=True)
        (plan_directory / "patch.json").write_text(json.dumps(plan_record.patch), encoding="utf-8")
        self.current_revision = current_revision
        self.memory_dirty = memory_dirty
        project_path = root / "HostProject.uproject"
        policy_path = root / "policy-r3.json"
        backup_root = root / "backups"
        backup_root.mkdir(exist_ok=True)
        asset_class = (
            "/Script/Engine.Blueprint"
            if operation in {"setVariableDefault", "setComponentProperty", "setPinDefault"}
            else "/Script/Engine.PrimaryDataAsset"
        )
        package_name = asset_path.split(".", 1)[0].removeprefix("/Game/")
        package_file = root / "Content" / f"{package_name}.uasset"
        package_file.parent.mkdir(parents=True, exist_ok=True)
        if not package_file.is_file():
            package_file.write_bytes(f"current:{asset_path}".encode())
        manifest = backup_root / "plan_r3verification.manifest.json"
        if recovery:
            before_bytes = f"backup:{asset_path}".encode()
            backup_file = backup_root / f"{package_file.name}.bak"
            backup_file.write_bytes(before_bytes)
            before_revision = "sha256:" + hashlib.sha256(before_bytes).hexdigest()
            after_revision = "sha256:" + hashlib.sha256(package_file.read_bytes()).hexdigest()
            authorization_keys = (
                [f"{asset_class}#Value"]
                if operation in {"setAssetProperty", "setAssetReferenceProperty", "setAssetStructuredProperty"}
                else []
            )
            policy = {
                "commitEnabled": True,
                "allowedProjectNames": ["HostProject"],
                "allowedAssetRoots": ["/Game/Tests"],
                "allowedAssetClasses": [asset_class],
                "allowedOperations": [operation],
                "allowedAssetProperties": authorization_keys,
                "allowedMaterialParameters": [],
                "allowedDataTableFields": [],
            }
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            policy_revision = "sha256:" + hashlib.sha256(policy_path.read_bytes()).hexdigest()
            manifest.write_text(
                json.dumps({
                    "schemaVersion": "1.0",
                    "manifestId": "manifest-r3",
                    "projectName": "HostProject",
                    "assetPath": asset_path,
                    "assetClass": asset_class,
                    "operation": operation,
                    "target": {"propertyPath": "Value"},
                    "operationCount": 1,
                    "operations": [{
                        "operationId": "operation-r3",
                        "operation": operation,
                        "target": {"propertyPath": "Value"},
                        "authorizationKeys": authorization_keys,
                    }],
                    "beforeRevision": before_revision,
                    "afterRevision": after_revision,
                    "packageKind": "single-uasset",
                    "backup": {
                        "relativePath": backup_file.relative_to(backup_root).as_posix(),
                        "revision": before_revision,
                        "size": len(before_bytes),
                    },
                    "source": {"policySha256": policy_revision},
                }),
                encoding="utf-8",
            )
        self._applies = {
            "apply_r3verification": SimpleNamespace(manifest_path=manifest)
        }
        self._live_applies: dict[str, Any] = {}
        self.config = SimpleNamespace(
            backup_root=backup_root,
            policy_path=policy_path,
            project_path=project_path,
        )
        self.semantic = semantic or semantic_result(asset_path=asset_path)
        self.semantic_calls: list[dict[str, Any]] = []

    def _resolve_change_set(self, change_set_id: str) -> ChangeSetRecord:
        if change_set_id != CHANGE_SET_ID:
            raise ValueError("unknown change set")
        return self.record

    def _plan_directory(self, plan_id: str) -> Path:
        return self.root / "plans" / plan_id

    def _reconcile_change_set(self, record: ChangeSetRecord, *, persist: bool) -> None:
        del record, persist

    def get_asset_state(self, asset_path: str) -> dict[str, Any]:
        return {
            "assetPath": asset_path,
            "sources": {
                "disk": {"revision": self.current_revision},
                "memory": {"packageDirty": self.memory_dirty},
            },
        }

    def analyze_semantic_diff(self, change_set_id: str, **kwargs: Any) -> dict[str, Any]:
        self.semantic_calls.append({"changeSetId": change_set_id, **copy.deepcopy(kwargs)})
        return copy.deepcopy(self.semantic)


def semantic_result(
    *,
    asset_path: str = ASSET,
    stage: str = "verified",
    missing: int = 0,
    unexpected: int = 0,
    gaps: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "evidenceStage": {"selected": stage},
        "assets": [
            {
                "assetPath": asset_path,
                "beforeRevision": "sha256:" + "0" * 64,
                "afterRevision": REVISION,
                "stageEvidenceRevision": REVISION,
                "summary": {
                    "expectedCount": 1,
                    "matchedCount": 0 if missing else 1,
                    "missingExpectedCount": missing,
                    "unexpectedCount": unexpected,
                },
                "unexpectedChanges": (
                    [{"changeId": "unexpected-r3", "assetPath": asset_path}]
                    if unexpected else []
                ),
                "analysisGaps": copy.deepcopy(gaps or []),
            }
        ],
    }


def assertion_by_kind(payload: dict[str, Any], kind: str, subject: str = "") -> dict[str, Any]:
    return next(
        item
        for item in payload["assertions"]
        if item["kind"] == kind and (not subject or item["subject"] == subject)
    )


class EvidenceFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ueak_r3_evidence_")
        self.root = Path(self.temporary.name)
        self.project = self.root / "HostProject.uproject"
        self.project.write_text("{}", encoding="utf-8")
        self.package = self.root / "Content" / "Tests" / "DA_Target.uasset"
        self.package.parent.mkdir(parents=True)
        self.package.write_bytes(b"final-r3-package")
        self.revision = "sha256:" + hashlib.sha256(self.package.read_bytes()).hexdigest()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def make_store(self, maximum_records: int = 8) -> VerificationEvidenceStore:
        return VerificationEvidenceStore(
            project_name="HostProject",
            project_path=self.project,
            maximum_records=maximum_records,
        )

    def capture(
        self,
        store: VerificationEvidenceStore,
        *,
        tool: str,
        result: dict[str, Any],
        subject: str = ASSET,
        session: str = SESSION,
        revision: str | None = None,
        evidence_id: str = "evidence-r3",
    ) -> dict[str, Any]:
        params = {"testName": subject} if tool == "ue_run_automation_test" else {"assetPath": subject}
        token = store.begin_registered_tool(tool, params)
        validation = {
            "evidenceId": evidence_id,
            "projectName": "HostProject",
            "editorSessionId": session,
            "startedAtUtc": "2026-08-20T00:00:00.000Z",
            "completedAtUtc": "2026-08-20T00:00:01.000Z",
            "observedAtUtc": "2026-08-20T00:00:01.000Z",
            "revisionCoverage": "not-applicable" if tool == "ue_run_automation_test" else "complete",
            "revisionSet": [] if tool == "ue_run_automation_test" else [
                {
                    "assetPath": subject,
                    "revision": revision or self.revision,
                    "revisionAfter": revision or self.revision,
                    "revisionStable": True,
                    "packageDirtyBefore": False,
                    "packageDirtyAfter": False,
                }
            ],
        }
        response_result = copy.deepcopy(result)
        response_result.setdefault("assetPath", "" if tool == "ue_run_automation_test" else subject)
        response_result.setdefault("testName", subject if tool == "ue_run_automation_test" else "")
        response_result["validationEvidence"] = validation
        record = store.finish_registered_tool(
            token,
            {"tool": tool, "ok": True, "result": response_result},
        )
        self.assertIsNotNone(record)
        assert record is not None
        return record


class VerificationEvidenceStoreTests(EvidenceFixture):
    def test_store_accepts_only_registered_successful_project_bound_outputs(self) -> None:
        store = self.make_store()
        original = {"assetPath": ASSET, "nested": {"value": 1}}
        token = store.begin_registered_tool("ue_compile_blueprint", original)
        original["nested"]["value"] = 99
        self.assertEqual(token.params["nested"]["value"], 1)
        self.assertIsNone(store.begin_registered_tool("ue_arbitrary_tool", {}))
        self.assertIsNone(store.finish_registered_tool(token, {"tool": "wrong", "ok": True, "result": {}}))
        self.assertEqual(store.snapshot(), [])

        bad_project = {
            "tool": "ue_compile_blueprint",
            "ok": True,
            "result": {
                "compiled": True,
                "succeeded": True,
                "assetPath": ASSET,
                "validationEvidence": {"projectName": "OtherProject"},
            },
        }
        self.assertIsNone(store.finish_registered_tool(token, bad_project))
        self.assertEqual(store.status()["recordCount"], 0)

    def test_store_is_bounded_deduplicated_and_returns_deep_copies(self) -> None:
        store = self.make_store(maximum_records=2)
        for index in range(3):
            self.capture(
                store,
                tool="ue_validate_asset",
                result={"result": "valid", "numChecked": 1},
                evidence_id=f"evidence-{index}",
            )
        self.assertEqual([item["evidenceId"] for item in store.snapshot()], ["evidence-1", "evidence-2"])
        found = store.find(kind="data-validation", subject=ASSET)
        found[0]["result"] = "tampered"
        self.assertEqual(store.find(kind="data-validation", subject=ASSET)[0]["result"], "valid")
        status = store.status()
        self.assertEqual(status["recordCount"], 2)
        self.assertTrue(status["bounded"])
        self.assertFalse(status["persistent"])
        self.assertFalse(status["arbitraryIngest"])

    def test_store_constructor_and_diagnostics_bounds(self) -> None:
        for invalid in (0, MAX_VERIFICATION_EVIDENCE_RECORDS + 1):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.make_store(maximum_records=invalid)
        store = self.make_store()
        record = self.capture(
            store,
            tool="ue_compile_blueprint",
            result={
                "compiled": True,
                "succeeded": True,
                "result": "success-with-warnings",
                "diagnostics": [{"message": str(index)} for index in range(40)],
            },
        )
        self.assertEqual(len(record["diagnostics"]), 32)
        self.assertTrue(record["diagnosticsTruncated"])
        self.assertTrue(record["warnings"])

    def test_compile_fallback_requires_explicit_clean_session_binding(self) -> None:
        store = self.make_store()
        token = store.begin_registered_tool("ue_compile_blueprint", {"assetPath": ASSET})
        missing_dirty = store.finish_registered_tool(
            token,
            {
                "tool": "ue_compile_blueprint",
                "ok": True,
                "result": {
                    "assetPath": ASSET,
                    "editorSessionId": SESSION,
                    "compiled": True,
                    "succeeded": True,
                    "result": "success",
                },
            },
        )
        self.assertIsNotNone(missing_dirty)
        assert missing_dirty is not None
        self.assertEqual(missing_dirty["revisionCoverage"], "unavailable")
        self.assertEqual(missing_dirty["revisionSet"], [])
        self.assertIsNone(missing_dirty["packageDirtyAfter"])

        clean_token = store.begin_registered_tool("ue_compile_blueprint", {"assetPath": ASSET})
        clean = store.finish_registered_tool(
            clean_token,
            {
                "tool": "ue_compile_blueprint",
                "ok": True,
                "result": {
                    "assetPath": ASSET,
                    "editorSessionId": SESSION,
                    "compiled": True,
                    "succeeded": True,
                    "result": "success",
                    "packageDirtyBefore": False,
                    "packageDirtyAfter": False,
                },
            },
        )
        self.assertIsNotNone(clean)
        assert clean is not None
        self.assertEqual(clean["revisionCoverage"], "complete")
        self.assertTrue(clean["revisionSet"][0]["revisionStable"])


class VerificationPlanTests(EvidenceFixture):
    def test_request_boundaries_and_normalization(self) -> None:
        workflow = FakeWorkflow(self.root)
        for impact_depth in (-1, 3, True, 1.5):
            with self.subTest(impact_depth=impact_depth), self.assertRaises(VerificationTrustError):
                build_verification_plan(workflow, CHANGE_SET_ID, impact_depth=impact_depth)  # type: ignore[arg-type]
        invalid_assets: list[Any] = [
            ["/Game/Tests/Bad.Other"],
            [ASSET, ASSET.lower()],
            [f"/Game/Tests/A{index}.A{index}" for index in range(9)],
        ]
        for assets in invalid_assets:
            with self.subTest(assets=assets), self.assertRaises(VerificationTrustError):
                build_verification_plan(workflow, CHANGE_SET_ID, extra_validation_assets=assets)
        with self.assertRaises(VerificationTrustError):
            build_verification_plan(
                workflow,
                CHANGE_SET_ID,
                required_automation_tests=[f"Test.{index}" for index in range(MAX_REQUIRED_AUTOMATION_TESTS + 1)],
            )
        with self.assertRaises(VerificationTrustError):
            build_verification_plan(workflow, CHANGE_SET_ID, required_automation_tests=[" Test.Exact"])

        result = build_verification_plan(
            workflow,
            CHANGE_SET_ID,
            required_automation_tests=["Test.Z", "Test.A"],
            extra_validation_assets=[EXTRA],
        )
        self.assertEqual(result["request"]["requiredAutomationTests"], ["Test.A", "Test.Z"])
        self.assertEqual(result["request"]["extraValidationAssets"], [EXTRA])

    def test_plan_is_deterministic_read_only_and_has_data_asset_rules(self) -> None:
        workflow = FakeWorkflow(self.root)
        before = copy.deepcopy(workflow.record)
        first = build_verification_plan(workflow, CHANGE_SET_ID)
        second = build_verification_plan(workflow, CHANGE_SET_ID)
        self.assertEqual(first, second)
        self.assertEqual(workflow.record, before)
        self.assertEqual(first["planFingerprint"], second["planFingerprint"])
        required = {
            item["kind"]
            for item in first["assertions"]
            if item["requirement"] == "required" and item["subject"] == ASSET
        }
        self.assertEqual(required, {"freshness", "persistence", "semantic", "data-validation"})
        self.assertNotIn("compile", required)
        self.assertEqual(workflow.index_service.calls[0]["max_depth"], 1)
        self.assertEqual(first["nextActions"][-1], {
            "tool": "ue_evaluate_trust_verdict",
            "arguments": {"change_set_id": CHANGE_SET_ID},
            "reason": "After every Required assertion is closed, evaluate the scoped final Trust verdict.",
        })
        self.assertFalse(first["evidenceLifecycle"]["capturedActionEvidencePersistent"])
        self.assertTrue(first["evidenceLifecycle"]["restartInvalidatesCapturedActionEvidence"])
        self.assertEqual(first["evidenceLifecycle"]["indexRefreshTiming"], "after-scoped-trust-verdict")

    def test_blueprint_and_reference_sensitive_domain_rules(self) -> None:
        blueprint = FakeWorkflow(
            self.root,
            asset_path=BLUEPRINT,
            operation="setPinDefault",
            semantic=semantic_result(asset_path=BLUEPRINT),
        )
        plan = build_verification_plan(blueprint, CHANGE_SET_ID)
        compile_assertion = assertion_by_kind(plan, "compile", BLUEPRINT)
        self.assertEqual(compile_assertion["requirement"], "required")
        self.assertEqual(compile_assertion["sourceRule"], "blueprint-narrow-write-explicit-compile")

        impact = {
            "summary": {"truncated": False, "truncationReasons": []},
            "directConsumers": [
                {
                    "assetPath": CONSUMER,
                    "assetClass": "/Script/UMGEditor.WidgetBlueprint",
                    "impactedTargets": [ASSET],
                }
            ],
            "indirectConsumers": [],
            "validationTargets": [{"assetPath": CONSUMER, "tier": 1}],
            "analysisGaps": [],
            "risks": [],
        }
        reference = FakeWorkflow(
            self.root,
            operation="setAssetReferenceProperty",
            impact=impact,
        )
        reference_plan = build_verification_plan(reference, CHANGE_SET_ID)
        self.assertEqual(assertion_by_kind(reference_plan, "reference-impact", ASSET)["requirement"], "required")
        consumer_compile = assertion_by_kind(reference_plan, "compile", CONSUMER)
        self.assertEqual(consumer_compile["sourceRule"], "reference-sensitive-direct-blueprint-consumer")
        self.assertEqual(reference_plan["scope"]["validationTargets"], impact["validationTargets"])

    def test_reference_sensitive_depth_zero_is_blocking_and_no_op_avoids_fake_requirements(self) -> None:
        reference = FakeWorkflow(self.root, operation="renameDataTableRow")
        plan = build_verification_plan(reference, CHANGE_SET_ID, impact_depth=0)
        self.assertIn("trust-reference-scope-unknown", {item["code"] for item in plan["risks"]})
        self.assertTrue(any(item["blocking"] for item in plan["risks"]))

        no_op = FakeWorkflow(
            self.root,
            operation_status="no-op",
            semantic=semantic_result(stage="persisted"),
            recovery=False,
        )
        no_op_plan = build_verification_plan(no_op, CHANGE_SET_ID)
        persistence = assertion_by_kind(no_op_plan, "persistence", ASSET)
        self.assertEqual((persistence["requirement"], persistence["status"]), ("informational", "not-applicable"))
        kinds = {item["kind"] for item in no_op_plan["assertions"]}
        self.assertNotIn("data-validation", kinds)
        self.assertNotIn("recovery", kinds)

    def test_multi_operation_rules_and_fingerprint_survive_plan_recovery(self) -> None:
        workflow = FakeWorkflow(self.root)
        workflow.record.operations[0].operation = "multiOperationTransaction"
        patch = workflow._plans["plan_r3verification"].patch
        patch["assets"][0]["operations"] = [
            {
                "operationId": "pin-r3",
                "operation": "setPinDefault",
                "target": {"graphName": "EventGraph", "nodeGuid": "A", "pinName": "Value"},
                "value": "7",
            },
            {
                "operationId": "reference-r3",
                "operation": "setAssetReferenceProperty",
                "target": {"propertyPath": "Value"},
                "value": {"assetPath": EXTRA},
            },
        ]
        workflow._plans["plan_r3verification"].digest = "sha256:" + hashlib.sha256(
            json.dumps(patch, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        (workflow._plan_directory("plan_r3verification") / "patch.json").write_text(
            json.dumps(patch),
            encoding="utf-8",
        )
        active = build_verification_plan(workflow, CHANGE_SET_ID, impact_depth=0)
        self.assertEqual(assertion_by_kind(active, "compile", ASSET)["requirement"], "required")
        self.assertEqual(assertion_by_kind(active, "reference-impact", ASSET)["requirement"], "required")

        workflow._plans = {}
        recovered = build_verification_plan(workflow, CHANGE_SET_ID, impact_depth=0)
        self.assertEqual(recovered["planFingerprint"], active["planFingerprint"])
        self.assertEqual(
            [(item["kind"], item["subject"]) for item in recovered["assertions"]],
            [(item["kind"], item["subject"]) for item in active["assertions"]],
        )


class TrustVerdictTests(EvidenceFixture):
    def make_store_with_validation(
        self,
        *,
        session: str = SESSION,
        revision: str = REVISION,
        result: str = "valid",
    ) -> VerificationEvidenceStore:
        store = self.make_store()
        self.capture(
            store,
            tool="ue_validate_asset",
            result={"result": result, "numChecked": 1, "numInvalid": int(result == "invalid")},
            session=session,
            revision=revision,
            evidence_id="validation-r3",
        )
        return store

    def test_service_reaches_verified_and_is_read_only(self) -> None:
        workflow = FakeWorkflow(self.root)
        store = self.make_store_with_validation()
        before_record = copy.deepcopy(workflow.record)
        before_evidence = store.snapshot()
        result = evaluate_trust_verdict(workflow, store, CHANGE_SET_ID)
        self.assertEqual(result["verdict"]["state"], "verified")
        self.assertEqual(workflow.record, before_record)
        self.assertEqual(store.snapshot(), before_evidence)
        self.assertEqual(result["verificationScope"]["verifiedAssets"], [ASSET])
        self.assertEqual(result["recommendedNextActions"], [])
        self.assertTrue(result["evidenceLifecycle"]["restartInvalidatesCapturedActionEvidence"])

    def test_missing_and_unexpected_semantic_changes_are_failed(self) -> None:
        for missing, unexpected, reason in (
            (1, 0, "trust-semantic-missing-expected-change"),
            (0, 1, "trust-semantic-unexpected-change"),
        ):
            with self.subTest(reason=reason):
                workflow = FakeWorkflow(
                    self.root,
                    semantic=semantic_result(missing=missing, unexpected=unexpected),
                )
                result = evaluate_trust_verdict(
                    workflow,
                    self.make_store_with_validation(),
                    CHANGE_SET_ID,
                )
                self.assertEqual(result["verdict"]["state"], "failed")
                self.assertIn(reason, result["verdict"]["reasonCodes"])
                self.assertEqual(assertion_by_kind(result, "semantic")["status"], "fail")
                if unexpected:
                    self.assertEqual(result["unexpectedChanges"][0]["changeId"], "unexpected-r3")

    def test_compile_validation_and_automation_capture_can_pass_or_fail(self) -> None:
        workflow = FakeWorkflow(
            self.root,
            asset_path=BLUEPRINT,
            operation="setVariableDefault",
            semantic=semantic_result(asset_path=BLUEPRINT),
        )
        store = self.make_store()
        self.capture(
            store,
            tool="ue_validate_asset",
            subject=BLUEPRINT,
            result={"result": "valid", "numChecked": 1},
            revision=REVISION,
            evidence_id="validation-blueprint",
        )
        self.capture(
            store,
            tool="ue_compile_blueprint",
            subject=BLUEPRINT,
            result={"compiled": True, "succeeded": True, "result": "success"},
            revision=REVISION,
            evidence_id="compile-blueprint",
        )
        self.capture(
            store,
            tool="ue_run_automation_test",
            subject="UEAgentKit.R3.Exact",
            result={"state": "success", "successful": True, "warningCount": 0},
            evidence_id="automation-r3",
        )
        passed = evaluate_trust_verdict(
            workflow,
            store,
            CHANGE_SET_ID,
            required_automation_tests=["UEAgentKit.R3.Exact"],
        )
        self.assertEqual(passed["verdict"]["state"], "verified")
        self.assertEqual(assertion_by_kind(passed, "compile")["applicability"], "exact-asset-revision")
        self.assertEqual(assertion_by_kind(passed, "automation")["applicability"], "project-session")

        failed_store = self.make_store()
        self.capture(
            failed_store,
            tool="ue_validate_asset",
            subject=BLUEPRINT,
            result={"result": "valid", "numChecked": 1},
            revision=REVISION,
            evidence_id="validation-blueprint-fail-case",
        )
        self.capture(
            failed_store,
            tool="ue_compile_blueprint",
            subject=BLUEPRINT,
            result={"compiled": True, "succeeded": False, "result": "failed"},
            revision=REVISION,
            evidence_id="compile-failed",
        )
        failed = evaluate_trust_verdict(workflow, failed_store, CHANGE_SET_ID)
        self.assertEqual(failed["verdict"]["state"], "failed")
        self.assertIn("trust-compile-failed", failed["verdict"]["reasonCodes"])

    def test_wrong_session_and_revision_are_insufficient_evidence(self) -> None:
        cases = (
            ("old-session", REVISION, "trust-evidence-session-mismatch"),
            (SESSION, "sha256:" + "f" * 64, "trust-evidence-revision-mismatch"),
        )
        for session, revision, reason in cases:
            with self.subTest(reason=reason):
                result = evaluate_trust_verdict(
                    FakeWorkflow(self.root),
                    self.make_store_with_validation(session=session, revision=revision),
                    CHANGE_SET_ID,
                )
                self.assertEqual(result["verdict"]["state"], "insufficient-evidence")
                validation = assertion_by_kind(result, "data-validation")
                self.assertEqual(validation["status"], "unknown")
                self.assertEqual(validation["reasonCode"], reason)
                self.assertEqual(
                    result["recommendedNextActions"][-1]["tool"],
                    "ue_evaluate_trust_verdict",
                )

    def test_current_revision_and_dirty_memory_gate_real_and_no_op_evidence(self) -> None:
        cases = (
            (
                FakeWorkflow(self.root, current_revision="sha256:" + "f" * 64),
                self.make_store_with_validation(),
                "trust-current-revision-mismatch",
            ),
            (
                FakeWorkflow(self.root, memory_dirty=True),
                self.make_store_with_validation(),
                "trust-current-memory-dirty",
            ),
            (
                FakeWorkflow(
                    self.root,
                    operation_status="no-op",
                    semantic=semantic_result(stage="persisted"),
                    recovery=False,
                    current_revision="sha256:" + "e" * 64,
                ),
                self.make_store(),
                "trust-current-revision-mismatch",
            ),
        )
        for workflow, store, reason in cases:
            with self.subTest(reason=reason, status=workflow.record.status):
                result = evaluate_trust_verdict(workflow, store, CHANGE_SET_ID)
                self.assertEqual(result["verdict"]["state"], "insufficient-evidence")
                self.assertIn(reason, result["verdict"]["reasonCodes"])
                self.assertEqual(assertion_by_kind(result, "freshness")["status"], "unknown")

    def test_global_semantic_truncation_blocks_required_coverage_once(self) -> None:
        semantic = semantic_result()
        semantic["analysisGaps"] = [{
            "code": "semantic-diff-truncated",
            "assetPath": "",
            "message": "bounded",
        }]
        result = evaluate_trust_verdict(
            FakeWorkflow(self.root, semantic=semantic),
            self.make_store_with_validation(),
            CHANGE_SET_ID,
        )
        self.assertEqual(result["verdict"]["state"], "insufficient-evidence")
        self.assertEqual(assertion_by_kind(result, "semantic")["status"], "unknown")
        self.assertEqual(
            sum(item.get("code") == "semantic-diff-truncated" for item in result["analysisGaps"]),
            1,
        )

    def test_extra_validation_evidence_must_match_current_revision(self) -> None:
        store = self.make_store_with_validation()
        self.capture(
            store,
            tool="ue_validate_asset",
            subject=EXTRA,
            result={"result": "valid", "numChecked": 1},
            revision="sha256:" + "f" * 64,
            evidence_id="validation-extra-stale",
        )
        result = evaluate_trust_verdict(
            FakeWorkflow(self.root),
            store,
            CHANGE_SET_ID,
            extra_validation_assets=[EXTRA],
        )
        extra = assertion_by_kind(result, "data-validation", EXTRA)
        self.assertEqual(result["verdict"]["state"], "insufficient-evidence")
        self.assertEqual(extra["status"], "unknown")
        self.assertEqual(extra["reasonCode"], "trust-evidence-revision-mismatch")

    def test_invalid_recovery_manifest_does_not_count_as_material(self) -> None:
        workflow = FakeWorkflow(self.root)
        manifest = workflow.config.backup_root / "plan_r3verification.manifest.json"
        manifest.write_text("{}", encoding="utf-8")
        result = evaluate_trust_verdict(
            workflow,
            self.make_store_with_validation(),
            CHANGE_SET_ID,
        )
        recovery = assertion_by_kind(result, "recovery")
        self.assertEqual(result["verdict"]["state"], "verified")
        self.assertEqual(recovery["status"], "unknown")
        self.assertEqual(recovery["requirement"], "informational")

    def test_suspicious_and_no_op_verified_complete_the_four_states(self) -> None:
        suspicious = evaluate_trust_verdict(
            FakeWorkflow(
                self.root,
                recovery=False,
                impact={
                    "summary": {"truncated": False, "truncationReasons": []},
                    "directConsumers": [],
                    "indirectConsumers": [],
                    "validationTargets": [],
                    "analysisGaps": [],
                    "risks": [{
                        "code": "high-fanout-target",
                        "severity": "medium",
                        "subject": ASSET,
                        "message": "The bounded target has high static-reference fanout.",
                    }],
                },
            ),
            self.make_store_with_validation(),
            CHANGE_SET_ID,
        )
        self.assertEqual(suspicious["verdict"]["state"], "suspicious")
        self.assertEqual(assertion_by_kind(suspicious, "recovery")["status"], "unknown")
        self.assertIn("high-fanout-target", suspicious["verdict"]["reasonCodes"])

        no_op = FakeWorkflow(
            self.root,
            operation_status="no-op",
            semantic=semantic_result(stage="persisted"),
            recovery=False,
        )
        no_op_result = evaluate_trust_verdict(no_op, self.make_store(), CHANGE_SET_ID)
        self.assertEqual(no_op_result["verdict"]["state"], "verified")
        self.assertEqual(assertion_by_kind(no_op_result, "persistence")["status"], "not-applicable")

    def test_data_validation_not_applicable_preserves_unreal_semantics(self) -> None:
        store = self.make_store()
        self.capture(
            store,
            tool="ue_validate_asset",
            result={
                "result": "not-validated",
                "numChecked": 0,
                "numUnableToValidate": 0,
                "numSkipped": 1,
            },
            revision=REVISION,
        )
        result = evaluate_trust_verdict(FakeWorkflow(self.root), store, CHANGE_SET_ID)
        validation = assertion_by_kind(result, "data-validation")
        self.assertEqual(validation["status"], "not-applicable")
        self.assertEqual(validation["applicability"], "not-applicable")
        self.assertEqual(result["verdict"]["state"], "verified")

    def test_minimum_token_envelope_keeps_verdict_and_required_failures(self) -> None:
        workflow = FakeWorkflow(self.root, semantic=semantic_result(missing=1))
        plan = build_verification_plan(workflow, CHANGE_SET_ID, max_output_tokens=256)
        self.assertTrue(plan["outputBudget"]["truncated"])
        self.assertIn("minimum-envelope-exceeds-budget", plan["outputBudget"]["truncationReasons"])
        self.assertTrue(any(item["requirement"] == "required" for item in plan["assertions"]))
        verdict = evaluate_trust_verdict(
            workflow,
            self.make_store_with_validation(),
            CHANGE_SET_ID,
            max_output_tokens=256,
        )
        self.assertEqual(verdict["verdict"]["state"], "failed")
        self.assertIn("trust-semantic-missing-expected-change", verdict["verdict"]["reasonCodes"])
        self.assertIn("minimum-envelope-exceeds-budget", verdict["outputBudget"]["truncationReasons"])
        self.assertEqual(assertion_by_kind(verdict, "semantic")["status"], "fail")


if __name__ == "__main__":
    unittest.main()
