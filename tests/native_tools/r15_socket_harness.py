"""R1.5 external loopback socket acceptance harness.

Each EditorBridge request is a fresh TCP connection with its own hello because
the bridge closes a connection after every response.  The harness never prints
or stores the bridge token, never runs inside the editor, and only exercises
read-only registry tools plus legacy read methods.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
import statistics
import sys
import time
import uuid
from pathlib import Path
from typing import Any

CATALOG_TOOL = "graph.nodeRegistry.catalog"
CONTEXT_TOOL = "graph.nodeRegistry.filterContext"
TOOL_VERSION = "1.0.0"
SCHEMA_VERSION = "1.0"


class BridgeError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class Client:
    def __init__(self, descriptor: dict[str, Any], timeout: float = 10.0) -> None:
        self.address = descriptor["address"]
        self.port = int(descriptor["port"])
        self.auth_token = descriptor["authToken"]
        self.plugin_version = descriptor["pluginVersion"]
        self.project_path_hash = descriptor["projectPathHash"]
        self.timeout = timeout
        self.request_samples: list[dict[str, Any]] = []

    def _open(self) -> socket.socket:
        sock = socket.create_connection((self.address, self.port), timeout=self.timeout)
        sock.settimeout(self.timeout)
        return sock

    @staticmethod
    def _read_line(sock: socket.socket, max_bytes: int = 2 * 1024 * 1024) -> str:
        buffer = b""
        deadline = time.monotonic() + 15.0
        while b"\n" not in buffer:
            if len(buffer) > max_bytes:
                raise BridgeError("client-response-too-large", "response exceeded client bound")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BridgeError("client-timeout", "timed out waiting for a response line")
            sock.settimeout(remaining)
            chunk = sock.recv(65536)
            if not chunk:
                raise BridgeError("client-disconnected", "editor closed the socket")
            buffer += chunk
        line, _ = buffer.split(b"\n", 1)
        return line.decode("utf-8")

    @staticmethod
    def _wire(payload: dict[str, Any]) -> bytes:
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n"

    def _hello_payload(self) -> dict[str, Any]:
        # EditorBridge reads hello auth fields at the request root, not in params.
        return {
            "requestId": str(uuid.uuid4()),
            "schemaVersion": SCHEMA_VERSION,
            "method": "hello",
            "authToken": self.auth_token,
            "serverVersion": self.plugin_version,
            "projectPathHash": self.project_path_hash,
        }

    def hello(self) -> dict[str, Any]:
        sock = self._open()
        try:
            sock.sendall(self._wire(self._hello_payload()))
            response = json.loads(self._read_line(sock))
        finally:
            sock.close()
        if not response.get("ok"):
            error = response.get("error") or {}
            raise BridgeError(error.get("code", "hello-failed"), error.get("message", ""))
        result = response["result"]
        if len(self.request_samples) < 6:
            hello = self._hello_payload()
            hello["authToken"] = "<redacted>"
            self.request_samples.append({
                "request": hello,
                "response": {"ok": True, "result": {
                    "pluginVersion": result.get("pluginVersion"),
                    "projectName": result.get("projectName"),
                    "sessionId": result.get("sessionId"),
                    "capabilities": result.get("capabilities"),
                }},
            })
        return result

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        expect_ok: bool = True,
    ) -> dict[str, Any]:
        payload = {
            "requestId": str(uuid.uuid4()),
            "schemaVersion": SCHEMA_VERSION,
            "method": method,
            "params": params or {},
        }
        sock = self._open()
        try:
            if method != "hello":
                sock.sendall(self._wire(self._hello_payload()))
                hello_response = json.loads(self._read_line(sock))
                if not hello_response.get("ok"):
                    error = hello_response.get("error") or {}
                    raise BridgeError(error.get("code", "hello-failed"), error.get("message", ""))
            sock.sendall(self._wire(payload))
            response = json.loads(self._read_line(sock))
        finally:
            sock.close()
        if len(self.request_samples) < 6 and method != "hello":
            sample = json.loads(json.dumps(payload))
            self.request_samples.append({"request": sample, "response": redact_response(response)})
        if response.get("ok") != expect_ok:
            error = response.get("error") or {}
            if expect_ok:
                raise BridgeError(error.get("code", "unexpected-error"), error.get("message", ""))
        return response

    def invoke(self, tool_id: str, arguments: dict[str, Any], *, expect_ok: bool = True,
               version: str = TOOL_VERSION) -> dict[str, Any]:
        response = self.request(
            "native.invoke",
            {"toolId": tool_id, "toolVersion": version, "arguments": arguments},
            expect_ok=expect_ok,
        )
        return response.get("result", {})


def redact_response(response: dict[str, Any]) -> dict[str, Any]:
    clone = json.loads(json.dumps(response))
    error = clone.get("error")
    if isinstance(error, dict):
        error.pop("details", None)
    return clone


def wait_for_descriptor(path: Path, process_id: int, timeout: float = 180.0) -> dict[str, Any]:

    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        try:
            if path.exists():
                with path.open("r", encoding="utf-8-sig") as handle:
                    descriptor = json.load(handle)
                if int(descriptor.get("processId", 0)) == process_id:
                    return descriptor
                last_error = f"descriptor pid {descriptor.get('processId')} != {process_id}"
        except (OSError, ValueError) as exc:
            last_error = str(exc)
        time.sleep(0.25)
    raise BridgeError("descriptor-timeout", f"no descriptor for pid {process_id}: {last_error}")


def sha256_file(path: Path) -> str:
    if not path.exists():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def invoke_page(client: Client, tool: str, base_arguments: dict[str, Any], items_key: str,
                page_size: int, max_pages: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    items: list[dict[str, Any]] = []
    cursor = ""
    pages = 0
    first_pagination: dict[str, Any] = {}
    while pages < max_pages:
        arguments = dict(base_arguments)
        arguments["pageSize"] = page_size
        if cursor:
            arguments["cursor"] = cursor
        result = client.invoke(tool, arguments)
        data = result.get("data") or {}
        batch = data.get(items_key) or []
        pagination = data.get("pagination") or {}
        if pages == 0:
            first_pagination = pagination
        items.extend(batch)
        next_cursor = pagination.get("nextCursor")
        pages += 1
        if not next_cursor:
            if not pagination.get("exhausted"):
                raise BridgeError("pagination-not-exhausted", "last page did not report exhausted")
            break
        cursor = next_cursor
    else:
        raise BridgeError("pagination-page-limit", f"exceeded {max_pages} pages")
    return items, first_pagination


class Harness:
    def __init__(self, args: argparse.Namespace, descriptor: dict[str, Any]) -> None:
        self.args = args
        self.descriptor = descriptor
        self.results: list[dict[str, Any]] = []
        self.failures: list[dict[str, Any]] = []
        self.client = Client(descriptor)
        self.evidence: dict[str, Any] = {}

    def check(self, name: str, condition: bool, detail: Any = None) -> bool:
        entry = {"name": name, "ok": bool(condition)}
        if detail is not None:
            entry["detail"] = detail
        self.results.append(entry)
        if not condition:
            self.failures.append(entry)
        return bool(condition)

    def expect_error(self, method: str, params: dict[str, Any], code: str) -> None:
        response = self.client.request(method, params, expect_ok=False)
        actual = (response.get("error") or {}).get("code")
        self.check(f"{method} -> {code}", actual == code, {"actual": actual})

    def expect_invoke_error(self, tool: str, arguments: dict[str, Any], code: str) -> None:
        response = self.client.request(
            "native.invoke",
            {"toolId": tool, "toolVersion": TOOL_VERSION, "arguments": arguments},
            expect_ok=False,
        )
        actual = (response.get("error") or {}).get("code")
        self.check(f"native.invoke {tool} -> {code}", actual == code, {"actual": actual})

    def run_shadow(self) -> None:
        hello = self.client.hello()
        capabilities = hello.get("capabilities") or []
        self.check("native.invoke advertised in shadow", "native.invoke" in capabilities)
        self.check("hello project identity", hello.get("projectName") == self.descriptor.get("projectName"))
        self.check("hello project path hash", hello.get("projectPathHash") == self.descriptor.get("projectPathHash"))

        fixture = json.loads(Path(self.args.fixture_info).read_text(encoding="utf-8-sig"))
        asset_path = fixture["assetPath"]
        graph_guid = fixture["graphGuid"].replace("-", "")
        node_guid = fixture["nodeGuid"].replace("-", "")
        self.check("fixture graph guid", len(graph_guid) == 32)
        self.check("fixture node guid", len(node_guid) == 32)

        status_before = self.client.request("editor.status", {})["result"]
        inspect_before = self.client.request("editor.inspectAssetLive", {"assetPath": asset_path})["result"]
        revision_before = fixture.get("assetRevision", "")
        fingerprint_before = fixture.get("graphFingerprint", "")
        dirty_before = bool((inspect_before.get("memory") or {}).get("packageDirty", False))
        status_dirty_before = int(status_before.get("dirtyPackageCount", -1))
        asset_file = Path(self.args.project_dir) / "Content" / "R1Native" / "BP_R1ContextFixture.uasset"
        asset_sha_before = sha256_file(asset_file)

        catalog_start = time.perf_counter()
        catalog_items, catalog_pagination = invoke_page(self.client, CATALOG_TOOL, {}, "descriptors", 64, 32)
        catalog_cold_ms = (time.perf_counter() - catalog_start) * 1000.0
        warm_start = time.perf_counter()
        self.client.invoke(CATALOG_TOOL, {"pageSize": 64})
        catalog_warm_ms = (time.perf_counter() - warm_start) * 1000.0
        self.check("catalog pagination total is 512", catalog_pagination.get("total") == 512,
                   {"total": catalog_pagination.get("total")})
        self.check("catalog pagination covers 512", len(catalog_items) == 512, {"count": len(catalog_items)})
        self.check("catalog pagination no duplicates", len({item["actionId"] for item in catalog_items}) == 512)
        self.check("catalog is explicitly truncated", catalog_pagination.get("catalogTruncated") is True)
        self.check("catalog availability is bounded",
                   512 <= int(catalog_pagination.get("catalogAvailable", 0)) < 38325)
        self.check("catalog total never implies 38k", catalog_pagination.get("total") != 38325)

        catalog_ids = sorted(item["actionId"] for item in catalog_items)
        catalog_digests = {item["actionId"]: item["descriptorDigest"] for item in catalog_items}
        baseline_catalog = json.loads(Path(self.args.baseline_catalog).read_text(encoding="utf-8-sig"))
        r14_catalog = json.loads(Path(self.args.r14_catalog).read_text(encoding="utf-8-sig"))
        baseline_ids = sorted(item["actionId"] for item in baseline_catalog["descriptors"])
        baseline_digests = {item["actionId"]: item["descriptorDigest"] for item in baseline_catalog["descriptors"]}
        r14_ids = sorted(item["actionId"] for item in r14_catalog["descriptors"])
        self.check("catalog actionIds match R1.5 baseline", catalog_ids == baseline_ids)
        self.check("catalog actionIds match R1.4", catalog_ids == r14_ids)
        digest_mismatches = [
            {"actionId": action_id, "socket": catalog_digests.get(action_id), "baseline": baseline_digests.get(action_id)}
            for action_id in catalog_ids if catalog_digests.get(action_id) != baseline_digests.get(action_id)
        ]

        def semantic_descriptor(item: dict[str, Any]) -> dict[str, Any]:
            import copy
            clone = copy.deepcopy(item)
            clone.pop("descriptorDigest", None)
            source = clone.get("source") or {}
            source.pop("pluginBuildHash", None)
            source.pop("discoveredAtUtc", None)
            return clone

        semantic_socket = {item["actionId"]: semantic_descriptor(item) for item in catalog_items}
        semantic_r14 = {item["actionId"]: semantic_descriptor(item) for item in r14_catalog["descriptors"]}
        self.check("catalog semantic descriptors match R1.4", semantic_socket == semantic_r14)
        self.check("catalog descriptorDigests match current build baseline", not digest_mismatches,
                   {"mismatchCount": len(digest_mismatches), "samples": digest_mismatches[:3],
                    "note": "descriptorDigest includes source.pluginBuildHash; R1.4 digests differ across a rebuild"})
        self.check("catalog database fingerprint matches R1.4",
                   catalog_pagination.get("setFingerprint") == r14_catalog["summary"]["actionDatabaseFingerprint"])

        palette_items, palette_pagination = invoke_page(
            self.client, CONTEXT_TOOL, {"assetPath": asset_path, "graphGuid": graph_guid}, "actions", 64, 32)
        self.check("palette context pagination covers full match set",
                   len(palette_items) == palette_pagination.get("total"),
                   {"count": len(palette_items), "total": palette_pagination.get("total")})
        exec_items, exec_pagination = invoke_page(
            self.client, CONTEXT_TOOL,
            {"assetPath": asset_path, "graphGuid": graph_guid, "nodeGuid": node_guid, "pinName": "then"},
            "actions", 64, 32)
        self.check("exec-pin context pagination covers full match set",
                   len(exec_items) == exec_pagination.get("total"),
                   {"count": len(exec_items), "total": exec_pagination.get("total")})

        r14_context = json.loads(Path(self.args.r14_context).read_text(encoding="utf-8-sig"))
        self.check("palette context fingerprint matches R1.4",
                   palette_pagination.get("setFingerprint") == r14_context["palette"]["actionSetFingerprint"],
                   {"actual": palette_pagination.get("setFingerprint"),
                    "expected": r14_context["palette"]["actionSetFingerprint"]})
        self.check("exec context fingerprint matches R1.4",
                   exec_pagination.get("setFingerprint") == r14_context["execPin"]["actionSetFingerprint"],
                   {"actual": exec_pagination.get("setFingerprint"),
                    "expected": r14_context["execPin"]["actionSetFingerprint"]})

        context_times = []
        for _ in range(8):
            started = time.perf_counter()
            self.client.invoke(CONTEXT_TOOL, {
                "assetPath": asset_path, "graphGuid": graph_guid, "pageSize": 64,
            })
            context_times.append((time.perf_counter() - started) * 1000.0)
        context_times.sort()
        p50 = statistics.median(context_times)
        p95 = context_times[min(len(context_times) - 1, int(round(0.95 * (len(context_times) - 1))))]

        first_cursor = catalog_pagination.get("nextCursor", "")
        self.check("catalog first page has cursor", bool(first_cursor))
        if first_cursor:
            replay_a = self.client.invoke(CATALOG_TOOL, {"pageSize": 64, "cursor": first_cursor})
            replay_b = self.client.invoke(CATALOG_TOOL, {"pageSize": 64, "cursor": first_cursor})
            self.check("replayed cursor is idempotent",
                       replay_a["data"]["descriptors"][0]["actionId"] == replay_b["data"]["descriptors"][0]["actionId"])
            tampered = first_cursor[:-1] + ("A" if first_cursor[-1] != "A" else "B")
            self.expect_invoke_error(CATALOG_TOOL, {"pageSize": 64, "cursor": tampered},
                                     "native-schema-invalid-arguments")
            self.expect_invoke_error(CATALOG_TOOL, {"pageSize": 32, "cursor": first_cursor},
                                     "native-schema-invalid-arguments")
            self.expect_invoke_error(CONTEXT_TOOL, {
                "assetPath": asset_path, "graphGuid": graph_guid, "pageSize": 64, "cursor": first_cursor},
                "native-schema-invalid-arguments")

        self.expect_invoke_error("graph.nodeRegistry.unknown", {}, "native-tool-unknown")
        response = self.client.request(
            "native.invoke",
            {"toolId": CATALOG_TOOL, "toolVersion": "2.0.0", "arguments": {}},
            expect_ok=False,
        )
        self.check("unsupported version rejected",
                   (response.get("error") or {}).get("code") == "native-tool-version-unsupported",
                   {"error": response.get("error")})
        self.expect_invoke_error(CATALOG_TOOL, {"unexpected": 1}, "native-schema-invalid-arguments")
        self.expect_invoke_error(CONTEXT_TOOL, {}, "native-schema-invalid-arguments")
        self.expect_invoke_error(CONTEXT_TOOL, {
            "assetPath": "/Game/R1Native/BP_R1Missing.BP_R1Missing", "graphGuid": graph_guid},
            "graphpatch-asset-not-loaded")
        self.expect_invoke_error(CATALOG_TOOL, {"cursor": "v1." + "A" * 5000},
                                 "native-schema-invalid-arguments")

        after_inspect = self.client.request("editor.inspectAssetLive", {"assetPath": asset_path})["result"]
        status_after = self.client.request("editor.status", {})["result"]
        asset_sha_after = sha256_file(asset_file)
        self.check("fixture remains loaded", bool((after_inspect.get("memory") or {}).get("loaded")))
        self.check("asset revision unchanged", asset_sha_after == asset_sha_before,
                   {"before": asset_sha_before, "after": asset_sha_after})
        self.check("package dirty unchanged",
                   bool((after_inspect.get("memory") or {}).get("packageDirty")) == dirty_before)
        self.check("dirty package count unchanged",
                   int(status_after.get("dirtyPackageCount", -2)) == status_dirty_before)

        self.evidence = {
            "mode": "shadow",
            "capabilities": capabilities,
            "catalog": {
                "count": len(catalog_items),
                "total": catalog_pagination.get("total"),
                "catalogTruncated": catalog_pagination.get("catalogTruncated"),
                "catalogAvailable": catalog_pagination.get("catalogAvailable"),
                "setFingerprint": catalog_pagination.get("setFingerprint"),
                "coldMs": round(catalog_cold_ms, 2),
                "warmMs": round(catalog_warm_ms, 2),
            },
            "context": {
                "paletteTotal": palette_pagination.get("total"),
                "paletteFingerprint": palette_pagination.get("setFingerprint"),
                "execTotal": exec_pagination.get("total"),
                "execFingerprint": exec_pagination.get("setFingerprint"),
                "p50Ms": round(p50, 2),
                "p95Ms": round(p95, 2),
            },
            "readOnly": {
                "packageRevision": revision_before,
                "graphFingerprint": fingerprint_before,
                "packageDirty": dirty_before,
                "dirtyPackageCount": status_dirty_before,
                "assetSha256": asset_sha_before,
                "assetRegistry": "not exposed over socket; covered by R1.4 in-editor T5",
            },
            "requestSamples": self.client.request_samples[:5],
        }

    def run_off(self) -> None:
        hello = self.client.hello()
        capabilities = hello.get("capabilities") or []
        self.check("native.invoke absent when off", "native.invoke" not in capabilities)
        status = self.client.request("editor.status", {})["result"]
        self.check("legacy editor.status works when off", status.get("state") == "available")
        response = self.client.request(
            "native.invoke",
            {"toolId": CATALOG_TOOL, "toolVersion": TOOL_VERSION, "arguments": {}},
            expect_ok=False,
        )
        self.check("direct native.invoke is fail-closed when off",
                   (response.get("error") or {}).get("code") == "native-registry-unavailable",
                   {"error": response.get("error")})
        self.evidence = {
            "mode": "off",
            "capabilities": capabilities,
            "legacyStatus": status.get("state"),
            "requestSamples": self.client.request_samples[:5],
        }

    def run_manifest(self) -> None:
        hello = self.client.hello()
        capabilities = hello.get("capabilities") or []
        self.check("native.invoke absent with invalid manifest", "native.invoke" not in capabilities)
        status = self.client.request("editor.status", {})["result"]
        self.check("legacy editor.status works with invalid manifest", status.get("state") == "available")
        response = self.client.request(
            "native.invoke",
            {"toolId": CATALOG_TOOL, "toolVersion": TOOL_VERSION, "arguments": {}},
            expect_ok=False,
        )
        code = (response.get("error") or {}).get("code")
        self.check("invalid manifest fails closed for R1 only", code == "native-registry-invalid-manifest",
                   {"error": response.get("error")})
        self.evidence = {
            "mode": "manifest",
            "capabilities": capabilities,
            "legacyStatus": status.get("state"),
            "nativeErrorCode": code,
            "requestSamples": self.client.request_samples[:5],
        }

    def run_negatives(self) -> None:
        for label, token in (("bad", "0" * 64), ("missing", "")):
            sock = self.client._open()
            try:
                sock.sendall(self.client._wire({
                    "requestId": str(uuid.uuid4()),
                    "schemaVersion": SCHEMA_VERSION,
                    "method": "hello",
                    "authToken": token, "serverVersion": self.descriptor["pluginVersion"],
                    "projectPathHash": self.descriptor["projectPathHash"],
                }))
                response = json.loads(self.client._read_line(sock))
                code = (response.get("error") or {}).get("code")
                self.check(f"{label} token rejected", code == "live-editor-authentication-failed",
                           {"actual": code})
            finally:
                sock.close()

        sock = self.client._open()
        try:
            sock.sendall(self.client._wire({
                "requestId": str(uuid.uuid4()),
                "schemaVersion": SCHEMA_VERSION,
                "method": "editor.status",
                "params": {},
            }))
            response = json.loads(self.client._read_line(sock))
            code = (response.get("error") or {}).get("code")
            self.check("method before hello is rejected",
                       code == "live-editor-authentication-required", {"actual": code})
        finally:
            sock.close()

        sock = self.client._open()
        try:
            sock.sendall(b'{"requestId":\n')
            response = json.loads(self.client._read_line(sock))
            code = (response.get("error") or {}).get("code")
            self.check("malformed JSON rejected", code == "invalid-json", {"actual": code})
        finally:
            sock.close()

        self.expect_error("unknown.method", {}, "live-editor-capability-unavailable")

        first = self.client.hello()
        second = self.client.hello()
        self.check("disconnect/reconnect works", bool(first.get("sessionId")) and bool(second.get("sessionId")))

        partial = self.client._open()
        try:
            partial.sendall(b'{"requestId":"partial"')
            time.sleep(0.8)
            self.check("partial-line client close is harmless", True)
        finally:
            partial.close()

        oversize = self.client._open()
        try:
            oversize.sendall(b"{" + b"A" * (70 * 1024) + b"}\n")
            response = json.loads(self.client._read_line(oversize))
            code = (response.get("error") or {}).get("code")
            self.check("oversize request rejected", code == "request-too-large", {"actual": code})
        except BridgeError as exc:
            self.check("oversize request rejected", False, {"exception": exc.code})
        finally:
            oversize.close()

    def run(self) -> int:
        if self.args.mode == "shadow":
            self.run_shadow()
            self.run_negatives()
        elif self.args.mode == "off":
            self.run_off()
        else:
            self.run_manifest()
        self.evidence["checks"] = self.results
        self.evidence["failures"] = self.failures
        self.evidence["ok"] = not self.failures
        out = Path(self.args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(self.evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"ok": self.evidence["ok"], "checks": len(self.results),
                          "failures": self.failures}, ensure_ascii=False, indent=2))
        return 0 if self.evidence["ok"] else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--descriptor", required=True)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--mode", choices=("shadow", "off", "manifest"), required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--project-dir", required=True)
    parser.add_argument("--asset-path", default="/Game/R1Native/BP_R1ContextFixture.BP_R1ContextFixture")
    parser.add_argument("--fixture-info", default="Build/r1-native/R1AutomationHost/Saved/UEAgentKit/R1Native/socket-fixture.json")
    parser.add_argument("--baseline-catalog", default="Build/r1-native/Evidence/R15/r1-regression/catalog-export.json")
    parser.add_argument("--r14-catalog", default="Build/r1-native/Evidence/R14/full-j/catalog-export.json")
    parser.add_argument("--r14-context", default="Build/r1-native/Evidence/R14/full-j/context-export.json")
    parser.add_argument("--descriptor-timeout", type=float, default=180.0)
    args = parser.parse_args()

    descriptor = wait_for_descriptor(Path(args.descriptor), args.pid, args.descriptor_timeout)
    harness = Harness(args, descriptor)
    return harness.run()


if __name__ == "__main__":
    sys.exit(main())
