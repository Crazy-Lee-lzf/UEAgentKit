#!/usr/bin/env python3
"""Explicit M2.0 offline conformance/adversarial evidence probe.

Starts one loopback broker, drives the three real-exporter golden cases through
the machine-readable schemas and the M1 transport, then records a bounded set of
adversarial results.  It never starts an Editor, never writes a Blueprint, and
never claims UE automation.
"""

from __future__ import annotations

import copy
import json
import platform
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT / "src"), str(ROOT / "tests" / "python")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import layout_broker_harness as harness  # noqa: E402
import layout_broker_m2_fixtures as m2  # noqa: E402

from ue_agent_kit.graph_layout_preview import build_preview  # noqa: E402
from ue_agent_kit.layout_broker import LayoutBrokerClient, LayoutBrokerServer  # noqa: E402
from ue_agent_kit.layout_broker import conformance, protocol  # noqa: E402


def utc_now() -> str:
    return (
        datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )


def exchange(port: int, request: dict[str, Any]) -> dict[str, Any]:
    raw = harness.raw_exchange(port, protocol.encode_message(request), timeout=3.0)
    payload = harness.parse_line(raw)
    if payload is None:
        raise RuntimeError("broker returned no JSON line")
    return payload


def main(argv: list[str] | None = None) -> int:
    del argv
    evidence_dir = ROOT / "Build" / "layout-broker" / "Evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    descriptor = evidence_dir / "m2-probe-descriptor.json"
    server = LayoutBrokerServer(
        descriptor_path=str(descriptor),
        project_name=m2.PROJECT_NAME,
        project_path_hash=m2.PROJECT_PATH_HASH,
        expect_session_id=m2.SESSION_ID,
    )
    golden = m2.load_golden_cases()
    cases: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    started = utc_now()
    server.start()
    try:
        client = LayoutBrokerClient.from_descriptor_file(
            str(descriptor),
            project_name=m2.PROJECT_NAME,
            project_path_hash=m2.PROJECT_PATH_HASH,
            session_id=m2.SESSION_ID,
        )
        for case in golden["cases"]:
            preflight, envelope, _ = m2.actual_inputs(case)
            request = client.build_request(preflight, envelope)
            validated = conformance.validate_request_conformance(
                request,
                project_name=m2.PROJECT_NAME,
                project_path_hash=m2.PROJECT_PATH_HASH,
                token=server.token,
                expected_session_id=m2.SESSION_ID,
            )
            payload = exchange(server.port, request)
            result = conformance.validate_response_conformance(
                payload, validated, token=server.token
            )
            mismatches = {
                key: {"expected": value, "actual": result[key]}
                for key, value in case["expected"].items()
                if result[key] != value
            }
            cases.append(
                {
                    "id": case["id"],
                    "origin": case["origin"],
                    "requestBytes": conformance.encoded_size(request),
                    "responseBytes": conformance.encoded_size(payload),
                    "planId": result["planId"],
                    "layoutHash": result["layoutHash"],
                    "mismatches": mismatches,
                }
            )
            checks.append(
                {
                    "name": f"golden:{case['id']}",
                    "ok": not mismatches,
                    "detail": "schema + semantic conformance, exact expected values",
                }
            )

        valid_request = client.build_request(*m2.actual_inputs(m2.case_by_id("linear")) [:2])
        bad_token = copy.deepcopy(valid_request)
        bad_token["token"] = "0" * 64
        bad_payload = exchange(server.port, bad_token)
        checks.append(
            {
                "name": "bad-token-error-schema",
                "ok": (
                    not bad_payload.get("ok")
                    and bad_payload["error"]["code"] == protocol.ERROR_TOKEN_REJECTED
                    and not conformance.schema_errors(
                        bad_payload, conformance.RESPONSE_SCHEMA
                    )
                ),
                "detail": "token rejected, error response conforms to schema",
            }
        )

        first = exchange(server.port, valid_request)
        duplicate = exchange(server.port, valid_request)
        checks.append(
            {
                "name": "duplicate-request",
                "ok": (
                    first.get("ok") is True
                    and not duplicate.get("ok")
                    and duplicate["error"]["code"] == protocol.ERROR_DUPLICATE_REQUEST
                ),
                "detail": "first request succeeds; same requestId is rejected without replay",
            }
        )

        forged_request = client.build_request(
            *m2.actual_inputs(m2.case_by_id("linear"))[:2]
        )
        forged_validated = conformance.validate_request_conformance(
            forged_request,
            project_name=m2.PROJECT_NAME,
            project_path_hash=m2.PROJECT_PATH_HASH,
            token=server.token,
            expected_session_id=m2.SESSION_ID,
        )
        forged = exchange(server.port, forged_request)
        forged["result"]["planId"] = "layout:" + "0" * 16
        m1_accepts = False
        try:
            protocol.validate_response(forged, forged_validated, token=server.token)
            m1_accepts = True
        except protocol.BrokerProtocolError:
            m1_accepts = False
        m2_rejects = False
        try:
            conformance.validate_response_conformance(
                forged, forged_validated, token=server.token
            )
        except conformance.ConformanceError:
            m2_rejects = True
        checks.append(
            {
                "name": "forged-plan-id",
                "ok": m1_accepts and m2_rejects,
                "detail": "known M1 gap: planId is not recomputed by M1; M2 rejects it",
            }
        )

        drift_preflight, drift_envelope, _ = m2.actual_inputs(m2.case_by_id("linear"))
        drift_envelope["packageRevision"] = "sha256:" + "0" * 64
        drift_request = client.build_request(drift_preflight, drift_envelope)
        drift_payload = exchange(server.port, drift_request)
        checks.append(
            {
                "name": "revision-drift",
                "ok": (
                    not drift_payload.get("ok")
                    and drift_payload["error"]["code"] == protocol.ERROR_WORKER_FAILED
                    and "planId" not in json.dumps(drift_payload)
                ),
                "detail": "revision conflict fails closed without a plan",
            }
        )

        scope_case = m2.case_by_id("p4_branch")
        scope_preflight, scope_envelope, _ = m2.actual_inputs(scope_case)
        scope = "n:c45aac7b-4d1e-d6df-866e-e1985e0ce6e0"
        scope_result = build_preview(
            scope_preflight,
            scope_envelope,
            mode="selection",
            scope_node_ids=[scope],
        )
        outside_held = all(
            item["deltaX"] == 0 and item["deltaY"] == 0
            for item in scope_result["positions"]
            if item["nodeId"] != scope
        )
        checks.append(
            {
                "name": "scope-source-capability",
                "ok": (
                    bool(scope_result["movedNodeIds"])
                    and set(scope_result["movedNodeIds"]) <= {scope}
                    and outside_held
                ),
                "detail": "build_preview selection scope holds outside nodes (no broker v1.1 yet)",
            }
        )

        proposed = copy.deepcopy(valid_request)
        proposed["schemaVersion"] = "ueak.layoutbroker/1.1"
        proposed["options"]["mode"] = "selection"
        proposed["options"]["scopeNodeIds"] = [scope]
        v11_schema_ok = not conformance.schema_errors(
            proposed, conformance.REQUEST_V11_SCHEMA
        )
        v11_payload = exchange(server.port, proposed)
        checks.append(
            {
                "name": "v11-draft-not-accepted-by-m1",
                "ok": (
                    v11_schema_ok
                    and not v11_payload.get("ok")
                    and v11_payload["error"]["code"]
                    == protocol.ERROR_VERSION_UNSUPPORTED
                ),
                "detail": "draft 1.1 schema validates; M1 server still rejects 1.1",
            }
        )
    finally:
        server.stop()

    checks.append(
        {
            "name": "descriptor-cleanup",
            "ok": not descriptor.exists(),
            "detail": "probe descriptor removed on stop",
        }
    )
    report = {
        "schemaVersion": "ueak.layoutbroker-m2-probe/1.0",
        "generatedAtUtc": utc_now(),
        "startedAtUtc": started,
        "ok": all(check["ok"] for check in checks),
        "evidenceLevel": "OFFLINE-PROVEN",
        "runtimeClaim": "No UE Editor, UE build, UI automation, or production route was used.",
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "host": platform.node(),
        },
        "broker": {
            "protocol": protocol.PROTOCOL_SCHEMA_VERSION,
            "brokerVersion": protocol.BROKER_VERSION,
            "algorithm": dict(protocol.ALGORITHM),
            "projectName": m2.PROJECT_NAME,
            "projectPathHash": m2.PROJECT_PATH_HASH,
            "descriptorSecurity": server.status()["descriptorSecurity"],
            "schemaDirectory": str(conformance.SCHEMA_DIR.relative_to(ROOT)),
        },
        "goldenOptions": golden["options"],
        "cases": cases,
        "checks": checks,
        "knownM1Gap": "M1 validates layoutHash but does not recompute planId; M2 conformance does.",
        "limitations": [
            (
                "No live P11 preflight/envelope capture exists in this worktree; "
                "snapshot shape is source-audited against EditorBridgeGraphHandlers.cpp "
                "and BuildPreflight."
            ),
            "v1.1 scope is a draft schema only; the M1 broker accepts 1.0 exclusively.",
            "No Editor client, menu, receipt, or UE automation is exercised.",
            "The third context fixture is derived from the linear exporter fixture.",
        ],
    }
    output = evidence_dir / "m2-conformance-probe.json"
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "evidence": str(output)}))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
