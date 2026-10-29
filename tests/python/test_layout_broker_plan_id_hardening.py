from __future__ import annotations

import copy
import json
import socket
import sys
import threading
import unittest
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import layout_broker_harness as harness  # noqa: E402
import layout_broker_m2_fixtures as m2  # noqa: E402

from ue_agent_kit.graph_layout_preview import build_preview  # noqa: E402
from ue_agent_kit.graph_contracts.layout import recompute_layout_hash  # noqa: E402
from ue_agent_kit.layout_broker import BrokerClientError, LayoutBrokerClient  # noqa: E402
from ue_agent_kit.layout_broker import descriptor as descriptor_module  # noqa: E402
from ue_agent_kit.layout_broker import protocol  # noqa: E402
from ue_agent_kit.layout_broker.planid import PlanIdError, plan_id_from_result  # noqa: E402

FAKE_TOKEN = "5e2b8c4a1d9f7306" * 4


def _forged_plan_id(result: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(result)
    value["planId"] = "layout:" + "0" * 16
    return value


def _tampered_position_and_hash(result: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(result)
    plan_position = value["plan"]["positions"][0]
    node_id = plan_position["nodeId"]
    plan_position["x"] += 10
    for item in value["positions"]:
        if item["nodeId"] == node_id:
            item["afterX"] += 10
            item["deltaX"] += 10
            break
    recomputed = recompute_layout_hash(value["plan"])
    value["layoutHash"] = recomputed
    value["plan"]["layoutHash"] = recomputed
    return value


class FakeBroker:
    """One-shot loopback broker that returns a caller-chosen success payload."""

    def __init__(self, factory: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        self._factory = factory
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(1)
        self._listener.settimeout(5.0)
        self.port = int(self._listener.getsockname()[1])
        self.request: dict[str, Any] | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            connection, _ = self._listener.accept()
            with connection:
                connection.settimeout(5.0)
                buffer = bytearray()
                while b"\n" not in buffer:
                    chunk = connection.recv(65536)
                    if not chunk:
                        return
                    buffer.extend(chunk)
                self.request = json.loads(bytes(buffer.split(b"\n", 1)[0]))
                connection.sendall(protocol.encode_message(self._factory(self.request)))
        except OSError:
            pass

    def close(self) -> None:
        try:
            self._listener.close()
        except OSError:
            pass


class PlanIdHardeningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.preflight, self.envelope, self.document = m2.actual_inputs(
            m2.case_by_id("linear")
        )
        self.direct = build_preview(self.preflight, self.envelope)

    def test_shared_helper_matches_three_golden_plan_ids(self) -> None:
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
                expected = plan_id_from_result(
                    result,
                    spacing_x=options["spacingX"],
                    spacing_y=options["spacingY"],
                    target_width=options["targetWidth"],
                    seed=options["seed"],
                )
                self.assertEqual(expected, case["expected"]["planId"])

    def test_helper_commits_to_request_options(self) -> None:
        expected = plan_id_from_result(
            self.direct, spacing_x=96, spacing_y=64, target_width=1800, seed=0
        )
        self.assertEqual(expected, self.direct["planId"])
        self.assertNotEqual(
            expected,
            plan_id_from_result(self.direct, spacing_x=96, spacing_y=65, target_width=1800, seed=0),
        )
        seed_one = copy.deepcopy(self.direct)
        seed_one["plan"]["seed"] = 1
        self.assertNotEqual(
            expected,
            plan_id_from_result(seed_one, spacing_x=96, spacing_y=64, target_width=1800, seed=1),
        )

    def test_helper_rejects_incomplete_plan(self) -> None:
        missing_packing = copy.deepcopy(self.direct)
        missing_packing["plan"].pop("packing")
        with self.assertRaises(PlanIdError):
            plan_id_from_result(missing_packing, spacing_x=96, spacing_y=64, target_width=1800, seed=0)
        missing_positions = copy.deepcopy(self.direct)
        missing_positions["plan"].pop("positions")
        with self.assertRaises(PlanIdError):
            plan_id_from_result(missing_positions, spacing_x=96, spacing_y=64, target_width=1800, seed=0)

    def test_protocol_validate_plan_id_rejects_forgery(self) -> None:
        accepted = protocol.validate_preview_result(self.direct, self.preflight)
        self.assertEqual(
            protocol.validate_plan_id(accepted, spacing_x=96, spacing_y=64, target_width=1800, seed=0),
            self.direct["planId"],
        )
        forged = _forged_plan_id(accepted)
        with self.assertRaises(protocol.BrokerProtocolError) as caught:
            protocol.validate_plan_id(forged, spacing_x=96, spacing_y=64, target_width=1800, seed=0)
        self.assertEqual(caught.exception.code, protocol.ERROR_PLAN_ID_MISMATCH)


class ActualServerPlanIdTests(PlanIdHardeningTests):
    def _server_with(self, preview_fn):
        server, path = harness.start_broker(
            self,
            project_name=m2.PROJECT_NAME,
            project_path_hash=m2.PROJECT_PATH_HASH,
            expect_session_id=m2.SESSION_ID,
            preview_fn=preview_fn,
        )
        return server, path

    def test_actual_server_rejects_forged_plan_id(self) -> None:
        def forged(preflight, envelope, **options):
            return _forged_plan_id(build_preview(preflight, envelope, **options))

        server, _ = self._server_with(forged)
        client = harness.client_for(
            server,
            project_name=m2.PROJECT_NAME,
            project_path_hash=m2.PROJECT_PATH_HASH,
            session_id=m2.SESSION_ID,
        )
        with self.assertRaises(BrokerClientError) as caught:
            client.preview(self.preflight, self.envelope)
        self.assertEqual(caught.exception.code, protocol.ERROR_WORKER_FAILED)
        self.assertEqual(
            server.status()["lastErrorCode"], protocol.ERROR_PLAN_ID_MISMATCH
        )

    def test_actual_server_rejects_forged_plan_id_with_recomputed_hash(self) -> None:
        def tampered(preflight, envelope, **options):
            return _tampered_position_and_hash(
                build_preview(preflight, envelope, **options)
            )

        server, _ = self._server_with(tampered)
        client = harness.client_for(
            server,
            project_name=m2.PROJECT_NAME,
            project_path_hash=m2.PROJECT_PATH_HASH,
            session_id=m2.SESSION_ID,
        )
        with self.assertRaises(BrokerClientError) as caught:
            client.preview(self.preflight, self.envelope)
        self.assertEqual(caught.exception.code, protocol.ERROR_WORKER_FAILED)
        self.assertEqual(
            server.status()["lastErrorCode"], protocol.ERROR_PLAN_ID_MISMATCH
        )


class ActualClientPlanIdTests(PlanIdHardeningTests):
    def _client_for_fake(self, fake: FakeBroker) -> LayoutBrokerClient:
        descriptor = descriptor_module.build_descriptor(
            port=fake.port,
            token=FAKE_TOKEN,
            project_name=m2.PROJECT_NAME,
            project_path_hash=m2.PROJECT_PATH_HASH,
            process_id=4321,
        )
        self.addCleanup(fake.close)
        return LayoutBrokerClient(
            descriptor,
            project_name=m2.PROJECT_NAME,
            project_path_hash=m2.PROJECT_PATH_HASH,
            session_id=m2.SESSION_ID,
        )

    def test_actual_client_rejects_forged_plan_id(self) -> None:
        def factory(request):
            result = json.loads(json.dumps(self.direct))
            result["planId"] = "layout:" + "0" * 16
            return protocol.success_response(request["requestId"], result, 5)

        fake = FakeBroker(factory)
        client = self._client_for_fake(fake)
        with self.assertRaises(BrokerClientError) as caught:
            client.preview(self.preflight, self.envelope)
        self.assertEqual(caught.exception.code, protocol.ERROR_PLAN_ID_MISMATCH)

    def test_actual_client_rejects_forged_plan_id_with_recomputed_hash(self) -> None:
        def factory(request):
            result = _tampered_position_and_hash(self.direct)
            result["planId"] = "layout:" + "0" * 16
            return protocol.success_response(request["requestId"], result, 5)

        fake = FakeBroker(factory)
        client = self._client_for_fake(fake)
        with self.assertRaises(BrokerClientError) as caught:
            client.preview(self.preflight, self.envelope)
        self.assertEqual(caught.exception.code, protocol.ERROR_PLAN_ID_MISMATCH)

    def test_actual_client_accepts_canonical_result(self) -> None:
        def factory(request):
            return protocol.success_response(
                request["requestId"], self.direct, 5
            )

        fake = FakeBroker(factory)
        client = self._client_for_fake(fake)
        result = client.preview(self.preflight, self.envelope)
        self.assertEqual(result["planId"], self.direct["planId"])


class GoldenByteEquivalenceTests(PlanIdHardeningTests):
    def test_three_golden_results_remain_exactly_equal(self) -> None:
        server, _ = harness.start_broker(
            self,
            project_name=m2.PROJECT_NAME,
            project_path_hash=m2.PROJECT_PATH_HASH,
            expect_session_id=m2.SESSION_ID,
        )
        client = harness.client_for(
            server,
            project_name=m2.PROJECT_NAME,
            project_path_hash=m2.PROJECT_PATH_HASH,
            session_id=m2.SESSION_ID,
        )
        golden = m2.load_golden_cases()
        for case in golden["cases"]:
            with self.subTest(case=case["id"]):
                preflight, envelope, _ = m2.actual_inputs(case)
                direct = build_preview(preflight, envelope)
                through_broker = client.preview(preflight, envelope)
                self.assertEqual(
                    json.dumps(through_broker, sort_keys=True),
                    json.dumps(direct, sort_keys=True),
                )
                self.assertEqual(
                    through_broker["planId"], case["expected"]["planId"]
                )


if __name__ == "__main__":
    unittest.main()
