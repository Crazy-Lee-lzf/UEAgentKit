from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import layout_broker_harness as harness  # noqa: E402
import layout_broker_m2_fixtures as m2  # noqa: E402

from ue_agent_kit.editor_bridge import LiveEditorError  # noqa: E402
from ue_agent_kit.graph_layout_preview import build_preview  # noqa: E402
from ue_agent_kit.layout_broker import conformance, protocol  # noqa: E402


class M2ConformanceCase(unittest.TestCase):
    def setUp(self) -> None:
        self.server, self.descriptor_path = harness.start_broker(
            self,
            project_name=m2.PROJECT_NAME,
            project_path_hash=m2.PROJECT_PATH_HASH,
            expect_session_id=m2.SESSION_ID,
        )
        self.client = harness.client_for(
            self.server,
            project_name=m2.PROJECT_NAME,
            project_path_hash=m2.PROJECT_PATH_HASH,
            session_id=m2.SESSION_ID,
        )

    def request_for(self, case: dict) -> tuple[dict, dict, dict, dict]:
        preflight, envelope, document = m2.actual_inputs(case)
        request = self.client.build_request(preflight, envelope)
        return request, preflight, envelope, document

    def exchange(self, request: dict) -> dict:
        raw = harness.raw_exchange(
            self.server.port, protocol.encode_message(request), timeout=3.0
        )
        payload = harness.parse_line(raw)
        self.assertIsNotNone(payload, "the broker did not return a JSON line")
        return payload  # type: ignore[return-value]

    def assert_conformance_error(self, callable_) -> None:
        with self.assertRaises(conformance.ConformanceError):
            callable_()


class SchemaAndGoldenTests(M2ConformanceCase):
    def test_all_schema_documents_are_valid_draft_2020_12(self) -> None:
        for name in (
            conformance.REQUEST_SCHEMA,
            conformance.RESPONSE_SCHEMA,
            conformance.DESCRIPTOR_SCHEMA,
            conformance.REQUEST_V11_SCHEMA,
        ):
            with self.subTest(schema=name):
                schema = conformance.load_schema(name)
                from jsonschema import Draft202012Validator

                Draft202012Validator.check_schema(schema)

    def test_actual_p11_envelope_shape_is_the_source_verified_ten_fields(self) -> None:
        case = m2.case_by_id("linear")
        preflight, envelope, document = m2.actual_inputs(case)
        self.assertEqual(set(envelope), set(m2.ENVELOPE_KEYS))
        self.assertTrue(m2.PREFLIGHT_KEYS <= set(preflight))
        self.assertEqual(envelope["schemaVersion"], "ueak.graphlayoutcontext/1.0")
        self.assertNotIn("sessionId", envelope)
        self.assertNotIn("pluginBuildHash", envelope)
        self.assertIs(envelope["context"], document)

    def test_golden_cases_match_actual_build_preview(self) -> None:
        golden = m2.load_golden_cases()
        for case in golden["cases"]:
            with self.subTest(case=case["id"]):
                preflight, envelope, _ = m2.actual_inputs(case)
                options = golden["options"]
                result = build_preview(
                    preflight,
                    envelope,
                    mode=options["mode"],
                    spacing_x=options["spacingX"],
                    spacing_y=options["spacingY"],
                    target_width=options["targetWidth"],
                    seed=options["seed"],
                )
                for key, expected in case["expected"].items():
                    self.assertEqual(result[key], expected, key)
                self.assertEqual(
                    result["layoutHash"],
                    __import__(
                        "ue_agent_kit.graph_contracts.layout", fromlist=["recompute_layout_hash"]
                    ).recompute_layout_hash(result["plan"]),
                )
                self.assertEqual(
                    result["planId"],
                    conformance.recompute_plan_id(
                        result,
                        spacing_x=golden["options"]["spacingX"],
                        spacing_y=golden["options"]["spacingY"],
                        target_width=golden["options"]["targetWidth"],
                        seed=golden["options"]["seed"],
                    ),
                )

    def test_golden_request_and_response_conform_and_match(self) -> None:
        golden = m2.load_golden_cases()
        for case in golden["cases"]:
            with self.subTest(case=case["id"]):
                request, _, _, _ = self.request_for(case)
                validated = conformance.validate_request_conformance(
                    request,
                    project_name=m2.PROJECT_NAME,
                    project_path_hash=m2.PROJECT_PATH_HASH,
                    token=self.server.token,
                    expected_session_id=m2.SESSION_ID,
                )
                self.assertLessEqual(
                    conformance.encoded_size(request), protocol.MAX_MESSAGE_BYTES
                )
                payload = self.exchange(request)
                result = conformance.validate_response_conformance(
                    payload, validated, token=self.server.token
                )
                for key, expected in case["expected"].items():
                    self.assertEqual(result[key], expected, key)
                self.assertEqual(payload["readOnly"], True)
                self.assertEqual(payload["algorithm"], protocol.ALGORITHM)
                self.assertEqual(
                    payload["schemaVersion"], protocol.PROTOCOL_SCHEMA_VERSION
                )

    def test_descriptor_conforms_to_schema_and_semantics(self) -> None:
        descriptor = self.server.descriptor
        self.assertIsNotNone(descriptor)
        conformance.validate_descriptor_conformance(
            descriptor,
            project_name=m2.PROJECT_NAME,
            project_path_hash=m2.PROJECT_PATH_HASH,
        )
        self.assertEqual(
            conformance.schema_errors(descriptor, conformance.DESCRIPTOR_SCHEMA), []
        )

    def test_descriptor_schema_rejects_non_loopback_and_extra_fields(self) -> None:
        descriptor = self.server.descriptor
        assert descriptor is not None
        for name, mutator in {
            "non-loopback": lambda value: value.update({"address": "0.0.0.0"}),
            "extra": lambda value: value.update({"debug": True}),
            "short-token": lambda value: value.update({"token": "a" * 16}),
            "zero-port": lambda value: value.update({"port": 0}),
            "jobs": lambda value: value.update({"maxConcurrentJobs": 2}),
        }.items():
            with self.subTest(case=name):
                value = copy.deepcopy(descriptor)
                mutator(value)
                self.assertTrue(
                    conformance.schema_errors(value, conformance.DESCRIPTOR_SCHEMA),
                    name,
                )


class InvalidRequestSchemaTests(M2ConformanceCase):
    def test_schema_rejects_structural_violations(self) -> None:
        request, _, _, _ = self.request_for(m2.case_by_id("linear"))

        def mutate(mutator) -> dict:
            value = copy.deepcopy(request)
            mutator(value)
            return value

        cases = {
            "missing-deadline": lambda v: v.pop("deadlineMs"),
            "extra-top-level": lambda v: v.update({"debug": True}),
            "bad-request-id": lambda v: v.update({"requestId": v["requestId"].upper()}),
            "bad-token-shape": lambda v: v.update({"token": "A" * 64}),
            "v11-version": lambda v: v.update({"schemaVersion": "ueak.layoutbroker/1.1"}),
            "deadline-too-large": lambda v: v.update({"deadlineMs": 5001}),
            "deadline-bool": lambda v: v.update({"deadlineMs": True}),
            "selection-in-v1": lambda v: v["options"].update({"mode": "selection"}),
            "options-extra": lambda v: v["options"].update({"scopeNodeIds": ["n:x"]}),
            "bad-algorithm": lambda v: v["algorithm"].update({"version": "2.0.0"}),
            "missing-envelope": lambda v: v["snapshot"].pop("contextEnvelope"),
            "missing-preflight-session": lambda v: v["snapshot"]["preflight"].pop(
                "sessionId"
            ),
            "missing-context": lambda v: v["snapshot"]["contextEnvelope"].pop("context"),
            "context-not-object": lambda v: v["snapshot"]["contextEnvelope"].update(
                {"context": "not-an-object"}
            ),
            "relative-asset": lambda v: v["snapshot"]["preflight"].update(
                {"assetPath": "Game/BP.BP"}
            ),
            "bad-time": lambda v: v.update({"sentAtUtc": "yesterday"}),
            "empty-project": lambda v: v.update({"projectName": ""}),
            "short-token": lambda v: v.update({"token": "a" * 31}),
        }
        for name, mutator in cases.items():
            with self.subTest(case=name):
                errors = conformance.schema_errors(
                    mutate(mutator), conformance.REQUEST_SCHEMA
                )
                self.assertTrue(errors, f"{name} unexpectedly passed the schema")

    def test_valid_request_size_stays_under_one_mib(self) -> None:
        for case in m2.load_golden_cases()["cases"]:
            with self.subTest(case=case["id"]):
                request, _, _, _ = self.request_for(case)
                self.assertLessEqual(
                    conformance.encoded_size(request), protocol.MAX_MESSAGE_BYTES
                )

    def test_projected_worst_case_context_still_fits_one_mib(self) -> None:
        request, preflight, _, _ = self.request_for(m2.case_by_id("linear"))
        # Actual C++ bounds the canonical context at 700 KiB and the preflight
        # node transport at 512 entries.  Exercise that budget without sending it.
        request["snapshot"]["preflight"]["nodeCount"] = 512
        template = copy.deepcopy(preflight["nodes"][0])
        request["snapshot"]["preflight"]["nodes"] = [
            copy.deepcopy(template) for _ in range(512)
        ]
        request["snapshot"]["contextEnvelope"]["context"]["m2BudgetPadding"] = (
            "x" * (690 * 1024)
        )
        size = conformance.encoded_size(request)
        self.assertLess(size, protocol.MAX_MESSAGE_BYTES)
        self.assertLessEqual(
            conformance.check_message_size(request), protocol.MAX_MESSAGE_BYTES
        )

    def test_oversized_request_fails_conformance_size_check(self) -> None:
        request, _, _, _ = self.request_for(m2.case_by_id("linear"))
        request["snapshot"]["contextEnvelope"]["context"]["m2Padding"] = "x" * (
            protocol.MAX_MESSAGE_BYTES + 1
        )
        with self.assertRaises(conformance.ConformanceError):
            conformance.check_message_size(request)


class InvalidResponseSchemaTests(M2ConformanceCase):
    def setUp(self) -> None:
        super().setUp()
        self.request, _, _, _ = self.request_for(m2.case_by_id("linear"))
        self.validated = conformance.validate_request_conformance(
            self.request,
            project_name=m2.PROJECT_NAME,
            project_path_hash=m2.PROJECT_PATH_HASH,
            token=self.server.token,
            expected_session_id=m2.SESSION_ID,
        )
        self.payload = self.exchange(self.request)

    def test_schema_rejects_structural_response_violations(self) -> None:
        cases = {
            "extra-result-field": lambda p: p["result"].update({"debug": True}),
            "missing-positions": lambda p: p["result"].pop("positions"),
            "bad-plan-id": lambda p: p["result"].update({"planId": "not-a-plan"}),
            "bad-layout-hash": lambda p: p["result"].update(
                {"layoutHash": "sha256:zz"}
            ),
            "node-count-string": lambda p: p["result"].update({"nodeCount": "4"}),
            "wrong-request-id": lambda p: p.update(
                {"requestId": "not-a-uuid"}
            ),
            "readonly-false": lambda p: p.update({"readOnly": False}),
            "duration-bool": lambda p: p.update({"durationMs": True}),
            "duration-too-large": lambda p: p.update({"durationMs": 7000}),
            "extra-top-level": lambda p: p.update({"debug": True}),
            "error-code-unknown": lambda p: p["error"].update(
                {"code": "native-preview-made-up"}
            ),
        }
        for name, mutator in cases.items():
            with self.subTest(case=name):
                value = copy.deepcopy(self.payload)
                if name == "error-code-unknown":
                    value = protocol.error_response(
                        self.request["requestId"], protocol.ERROR_RATE_LIMITED, "slow"
                    )
                mutator(value)
                errors = conformance.schema_errors(value, conformance.RESPONSE_SCHEMA)
                self.assertTrue(errors, f"{name} unexpectedly passed the schema")

    def test_forged_plan_id_is_rejected_by_actual_client_and_conformance(self) -> None:
        forged = copy.deepcopy(self.payload)
        forged["result"]["planId"] = "layout:" + "0" * 16
        # M2.1: the M1 response validator now recomputes planId as well.
        with self.assertRaises(protocol.BrokerProtocolError) as caught:
            protocol.validate_response(forged, self.validated, token=self.server.token)
        self.assertEqual(caught.exception.code, protocol.ERROR_PLAN_ID_MISMATCH)
        with self.assertRaises(protocol.BrokerProtocolError) as conformance_caught:
            conformance.validate_response_conformance(
                forged, self.validated, token=self.server.token
            )
        self.assertEqual(
            conformance_caught.exception.code, protocol.ERROR_PLAN_ID_MISMATCH
        )

    def test_forged_layout_hash_is_rejected_by_both_layers(self) -> None:
        forged = copy.deepcopy(self.payload)
        bad = "sha256:" + "0" * 64
        forged["result"]["layoutHash"] = bad
        forged["result"]["plan"]["layoutHash"] = bad
        with self.assertRaises(protocol.BrokerProtocolError) as caught:
            protocol.validate_response(forged, self.validated, token=self.server.token)
        self.assertEqual(caught.exception.code, protocol.ERROR_PLAN_HASH_MISMATCH)
        with self.assertRaises(protocol.BrokerProtocolError):
            conformance.validate_response_conformance(
                forged, self.validated, token=self.server.token
            )

    def test_semantically_wrong_request_id_is_rejected_by_conformance(self) -> None:
        forged = copy.deepcopy(self.payload)
        forged["requestId"] = "11111111-1111-4111-8111-111111111111"
        self.assertEqual(
            conformance.schema_errors(forged, conformance.RESPONSE_SCHEMA), []
        )
        with self.assertRaises(protocol.BrokerProtocolError):
            conformance.validate_response_conformance(
                forged, self.validated, token=self.server.token
            )

    def test_error_response_conforms_and_never_echoes_the_token(self) -> None:
        request = copy.deepcopy(self.request)
        request["token"] = "0" * 64
        payload = self.exchange(request)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], protocol.ERROR_TOKEN_REJECTED)
        self.assertEqual(
            conformance.schema_errors(payload, conformance.RESPONSE_SCHEMA), []
        )
        self.assertNotIn(self.server.token, json.dumps(payload))
        with self.assertRaises(protocol.BrokerProtocolError) as caught:
            conformance.validate_response_conformance(
                payload, self.validated, token=self.server.token
            )
        self.assertEqual(caught.exception.code, protocol.ERROR_TOKEN_REJECTED)

    def test_token_echo_is_rejected_by_semantic_validation(self) -> None:
        forged = copy.deepcopy(self.payload)
        forged["result"]["graphName"] = self.server.token
        with self.assertRaises(protocol.BrokerProtocolError):
            conformance.validate_response_conformance(
                forged, self.validated, token=self.server.token
            )


class DriftAndScopeTests(M2ConformanceCase):
    def test_revision_fingerprint_and_identity_drift_fail_closed(self) -> None:
        preflight, envelope, _ = m2.actual_inputs(m2.case_by_id("linear"))
        cases = {
            "asset-identity": (
                lambda p, e: e.update({"assetPath": "/Game/Other/BP.Other"}),
                "graph-layout-context-identity-mismatch",
            ),
            "graph-identity": (
                lambda p, e: e.update({"graphGuid": "00000000-0000-4000-8000-000000000000"}),
                "graph-layout-context-identity-mismatch",
            ),
            "revision": (
                lambda p, e: e.update({"packageRevision": "sha256:" + "0" * 64}),
                "graph-layout-revision-conflict",
            ),
            "fingerprint": (
                lambda p, e: e.update({"graphFingerprint": "sha1:" + "0" * 40}),
                "graph-layout-fingerprint-conflict",
            ),
            "node-count": (
                lambda p, e: p.update({"nodeCount": p["nodeCount"] + 1}),
                "graph-layout-context-incomplete",
            ),
            "package-dirty": (
                lambda p, e: p.update({"packageDirty": True}),
                "graph-layout-package-dirty",
            ),
            "pie-active": (
                lambda p, e: p.update({"pieState": "playing"}),
                "graph-layout-pie-active",
            ),
        }
        for name, (mutator, code) in cases.items():
            with self.subTest(case=name):
                current_preflight = copy.deepcopy(preflight)
                current_envelope = copy.deepcopy(envelope)
                mutator(current_preflight, current_envelope)
                with self.assertRaises(LiveEditorError) as caught:
                    build_preview(current_preflight, current_envelope)
                self.assertEqual(caught.exception.code, code)

    def test_drift_is_reported_by_the_broker_as_worker_failed_without_a_plan(self) -> None:
        preflight, envelope, _ = m2.actual_inputs(m2.case_by_id("linear"))
        envelope["packageRevision"] = "sha256:" + "0" * 64
        request = self.client.build_request(preflight, envelope)
        payload = self.exchange(request)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], protocol.ERROR_WORKER_FAILED)
        self.assertNotIn("planId", json.dumps(payload))

    def test_session_drift_is_rejected_before_dispatch(self) -> None:
        request, _, _, _ = self.request_for(m2.case_by_id("linear"))
        request["sessionId"] = "another-session"
        with self.assertRaises(protocol.BrokerProtocolError) as caught:
            conformance.validate_request_conformance(
                request,
                project_name=m2.PROJECT_NAME,
                project_path_hash=m2.PROJECT_PATH_HASH,
                token=self.server.token,
                expected_session_id=m2.SESSION_ID,
            )
        self.assertEqual(caught.exception.code, protocol.ERROR_IDENTITY_MISMATCH)

    def test_v11_scope_extension_is_draft_schema_only_and_m1_rejects_it(self) -> None:
        case = m2.case_by_id("p4_branch")
        preflight, envelope, document = m2.actual_inputs(case)
        scope = "n:c45aac7b-4d1e-d6df-866e-e1985e0ce6e0"
        request, _, _, _ = self.request_for(case)
        proposed = copy.deepcopy(request)
        proposed["schemaVersion"] = "ueak.layoutbroker/1.1"
        proposed["options"]["mode"] = "selection"
        proposed["options"]["scopeNodeIds"] = [scope]
        self.assertEqual(
            conformance.schema_errors(proposed, conformance.REQUEST_V11_SCHEMA), []
        )
        missing_scope = copy.deepcopy(proposed)
        missing_scope["options"].pop("scopeNodeIds")
        self.assertTrue(
            conformance.schema_errors(missing_scope, conformance.REQUEST_V11_SCHEMA)
        )
        full_with_scope = copy.deepcopy(proposed)
        full_with_scope["options"]["mode"] = "full"
        self.assertTrue(
            conformance.schema_errors(full_with_scope, conformance.REQUEST_V11_SCHEMA)
        )
        self.assertTrue(
            conformance.schema_errors(proposed, conformance.REQUEST_SCHEMA)
        )

        payload = self.exchange(proposed)
        self.assertFalse(payload["ok"])
        self.assertEqual(
            payload["error"]["code"], protocol.ERROR_VERSION_UNSUPPORTED
        )

        result = build_preview(
            preflight,
            envelope,
            mode="selection",
            scope_node_ids=[scope],
            spacing_x=96,
            spacing_y=64,
            target_width=1800,
            seed=0,
        )
        self.assertEqual(result["plan"]["mode"], "selection")
        self.assertTrue(set(result["movedNodeIds"]) <= {scope})
        outside = [
            item for item in result["positions"] if item["nodeId"] != scope
        ]
        self.assertTrue(all(item["deltaX"] == 0 and item["deltaY"] == 0 for item in outside))
        self.assertGreaterEqual(len(result["movedNodeIds"]), 1)
        self.assertEqual(len(document["graphs"][0]["nodes"]), result["nodeCount"])

    def test_v10_schema_still_rejects_scope_field(self) -> None:
        request, _, _, _ = self.request_for(m2.case_by_id("linear"))
        request["options"]["scopeNodeIds"] = ["n:x"]
        self.assertTrue(
            conformance.schema_errors(request, conformance.REQUEST_SCHEMA)
        )


if __name__ == "__main__":
    unittest.main()
