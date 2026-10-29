from __future__ import annotations

import ast
import contextlib
import inspect
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable


TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
TESTS_ROOT = Path(__file__).resolve().parent
for path in (SRC_ROOT, TESTS_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from test_memory_mount import (  # noqa: E402
    default_scope,
    make_record,
    write_pack,
)
from ue_agent_kit.memory_mount import (  # noqa: E402
    AUTHOR_AUTHENTICITY_NOT_ESTABLISHED,
    DemoCallerContext,
    ErrorCode,
    HmacClaimVerifier,
    MemoryMountError,
    MemoryMountRegistry,
    MountLimits,
    ReadOnlyMountAdapter,
    TestClaimIssuer,
    VerifiedCallerContext,
    build_integrity_receipt,
)
from ue_agent_kit.memory_mount.jsonutil import canonical_json_bytes  # noqa: E402

try:
    import jsonschema  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - optional dependency
    jsonschema = None


SECRET = b"r7-test-only-hmac-secret-0123456789"
ISSUER = "r7-test-issuer"
AUDIENCE = "r7-readonly-test"


def write_integrity_receipt(
    pack: Path,
    *,
    manifest_tamper: Callable[[dict[str, Any]], None] | None = None,
    receipt_tamper: Callable[[dict[str, Any]], None] | None = None,
    signature: Any = None,
) -> dict[str, Any]:
    manifest = json.loads((pack / "pack.json").read_text(encoding="utf-8"))
    if manifest_tamper is not None:
        manifest_tamper(manifest)
        (pack / "pack.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
    receipt = build_integrity_receipt(manifest)
    if signature is not None:
        receipt["signature"] = signature
    if receipt_tamper is not None:
        receipt_tamper(receipt)
    (pack / "pack.integrity.json").write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    return receipt


class ReadinessTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="r7-readiness-")
        self.root = Path(self.temporary.name)
        self._registries: list[MemoryMountRegistry | ReadOnlyMountAdapter] = []
        self.now = [1_000.0]

    def tearDown(self) -> None:
        for item in self._registries:
            item.close()
        self.temporary.cleanup()

    def issuer(self) -> TestClaimIssuer:
        return TestClaimIssuer(
            secret=SECRET,
            issuer=ISSUER,
            audience=AUDIENCE,
            clock=lambda: self.now[0],
        )

    def verifier(
        self,
        *,
        issuer: str = ISSUER,
        audience: str = AUDIENCE,
    ) -> HmacClaimVerifier:
        return HmacClaimVerifier(
            secret=SECRET,
            issuer=issuer,
            audience=audience,
            clock=lambda: self.now[0],
        )

    def verified(self, *, project_key: str = "UEAgentKit", ttl: float = 600.0) -> VerifiedCallerContext:
        envelope = self.issuer().issue(
            principal="alice",
            project_key=project_key,
            session_id="session-1",
            ttl_seconds=ttl,
        )
        return self.verifier().verify(envelope)

    def track(self, item: Any) -> Any:
        self._registries.append(item)
        return item

    def assert_error(self, code: ErrorCode, action: Any) -> MemoryMountError:
        with self.assertRaises(MemoryMountError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)
        return caught.exception


class IdentityReadinessTests(ReadinessTestBase):
    def test_demo_context_is_explicitly_untrusted(self) -> None:
        demo = DemoCallerContext(principal="alice", project_key="UEAgentKit")
        self.assertFalse(demo.is_verified)
        self.assertEqual(demo.trust_mode, "untrusted-demo")
        pack = write_pack(self.root / "pack", [make_record()])
        registry = self.track(
            MemoryMountRegistry.for_demo(
                principal="alice",
                project_key="UEAgentKit",
                cache_dir=self.root / "cache",
            )
        )
        self.assertFalse(registry.is_verified)
        self.assertEqual(registry.trust_mode, "untrusted-demo")
        registry.mount(pack)

    def test_verified_context_cannot_be_constructed_directly(self) -> None:
        with self.assertRaises(TypeError):
            VerifiedCallerContext(
                principal="alice",
                session_id="s",
                project_key="UEAgentKit",
                issuer=ISSUER,
                audience=AUDIENCE,
                issued_at_epoch=1_000.0,
                expires_at_epoch=2_000.0,
                claim_id="claim-1",
            )

    def test_hmac_claim_verifier_accepts_valid_claims(self) -> None:
        context = self.verified()
        self.assertTrue(context.is_verified)
        self.assertEqual(context.principal, "alice")
        self.assertEqual(context.session_id, "session-1")
        self.assertEqual(context.project_key, "UEAgentKit")
        self.assertEqual(context.trust_mode, "verified-claims")
        context.assert_valid(now=1_001.0, audience=AUDIENCE, project_key="UEAgentKit")

    def test_hmac_claim_verifier_rejects_tampered_payload(self) -> None:
        envelope = json.loads(
            self.issuer().issue(
                principal="alice",
                project_key="UEAgentKit",
                session_id="session-1",
            )
        )
        envelope["payload"]["principal"] = "mallory"
        self.assert_error(
            ErrorCode.IDENTITY_SIGNATURE_INVALID,
            lambda: self.verifier().verify(
                canonical_json_bytes(envelope).decode("utf-8")
            ),
        )

    def test_hmac_claim_verifier_rejects_expired_claim(self) -> None:
        envelope = self.issuer().issue(
            principal="alice",
            project_key="UEAgentKit",
            session_id="session-1",
            ttl_seconds=10,
        )
        self.now[0] = 1_011.0
        self.assert_error(
            ErrorCode.IDENTITY_EXPIRED,
            lambda: self.verifier().verify(envelope),
        )

    def test_hmac_claim_verifier_rejects_wrong_issuer_and_audience(self) -> None:
        envelope = self.issuer().issue(
            principal="alice",
            project_key="UEAgentKit",
            session_id="session-1",
        )
        self.assert_error(
            ErrorCode.IDENTITY_ISSUER_MISMATCH,
            lambda: self.verifier(issuer="other-issuer").verify(envelope),
        )
        self.assert_error(
            ErrorCode.IDENTITY_AUDIENCE_MISMATCH,
            lambda: self.verifier(audience="other-audience").verify(envelope),
        )

    def test_from_verified_rejects_demo_context(self) -> None:
        self.assert_error(
            ErrorCode.IDENTITY_REQUIRED,
            lambda: MemoryMountRegistry.from_verified(
                caller=DemoCallerContext(principal="alice", project_key="UEAgentKit"),
                cache_dir=self.root / "cache",
            ),
        )

    def test_verified_registry_mounts_and_queries_with_injected_claims(self) -> None:
        pack = write_pack(self.root / "pack", [make_record(title="已验证条目")])
        context = self.verified()
        registry = self.track(
            MemoryMountRegistry.from_verified(
                caller=context,
                cache_dir=self.root / "cache",
                identity_clock=lambda: self.now[0],
            )
        )
        registry.mount(pack)
        result = registry.query("已验证条目")
        self.assertEqual(len(result.items), 1)
        self.assertTrue(registry.is_verified)

    def test_verified_claim_expiry_is_checked_on_query(self) -> None:
        pack = write_pack(self.root / "pack", [make_record(title="过期条目")])
        context = self.verified(ttl=10)
        registry = self.track(
            MemoryMountRegistry.from_verified(
                caller=context,
                cache_dir=self.root / "cache",
                identity_clock=lambda: self.now[0],
            )
        )
        registry.mount(pack)
        self.now[0] = 1_020.0
        self.assert_error(ErrorCode.IDENTITY_EXPIRED, lambda: registry.query("过期条目"))

    def test_project_claim_mismatch_is_explicit(self) -> None:
        context = self.verified(project_key="ProjectA")
        self.assert_error(
            ErrorCode.IDENTITY_PROJECT_MISMATCH,
            lambda: context.assert_valid(now=1_000.0, project_key="ProjectB"),
        )

    def test_cli_remains_explicitly_untrusted_demo(self) -> None:
        from ue_agent_kit.memory_mount.cli import main

        pack = write_pack(self.root / "pack", [make_record(title="CLI 条目")])
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(
                [
                    "query",
                    str(pack),
                    "--query",
                    "CLI 条目",
                    "--principal",
                    "alice",
                    "--cache-dir",
                    str(self.root / "cli-cache"),
                ]
            )
        self.assertEqual(code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["identity"]["trustMode"], "untrusted-demo")
        self.assertFalse(payload["identity"]["verified"])
        self.assertIn("r7-untrusted-demo-identity", stderr.getvalue())


class IntegrityReadinessTests(ReadinessTestBase):
    def test_pack_without_receipt_is_explicitly_legacy_unverified(self) -> None:
        pack = write_pack(self.root / "pack", [make_record()])
        content = self._load(pack)
        self.assertFalse(content.integrity.receipt_present)
        self.assertFalse(content.integrity.receipt_verified)
        self.assertEqual(content.integrity.integrity_mode, "records-only-unverified-manifest")
        self.assertIn("r7-manifest-integrity-unverified", content.integrity.warnings)
        self.assertEqual(
            content.integrity.author_authenticity,
            AUTHOR_AUTHENTICITY_NOT_ESTABLISHED,
        )

    def test_valid_receipt_verifies_manifest_invariants(self) -> None:
        pack = write_pack(self.root / "pack", [make_record()])
        write_integrity_receipt(pack)
        content = self._load(pack)
        self.assertTrue(content.integrity.receipt_present)
        self.assertTrue(content.integrity.receipt_verified)
        self.assertEqual(content.integrity.integrity_mode, "manifest-digest-verified")
        self.assertFalse(content.integrity.signed)
        self.assertEqual(
            content.integrity.author_authenticity,
            AUTHOR_AUTHENTICITY_NOT_ESTABLISHED,
        )
        self.assertIn("manifestDigest", content.integrity.checked_invariants)
        self.assertIn("scopeDigest", content.integrity.checked_invariants)
        self.assertIn("provenanceDigest", content.integrity.checked_invariants)

    def test_receipt_schema_is_machine_readable(self) -> None:
        pack = write_pack(self.root / "pack", [make_record()])
        receipt = write_integrity_receipt(pack)
        if jsonschema is None:
            self.skipTest("jsonschema is not installed")
        schema = json.loads(
            (TOOL_ROOT / "spec" / "memory-mount" / "r7-pack-integrity.schema.json").read_text(
                encoding="utf-8"
            )
        )
        jsonschema.Draft202012Validator(
            schema,
            format_checker=jsonschema.FormatChecker(),
        ).validate(receipt)

    def test_manifest_priority_tamper_is_detected(self) -> None:
        pack = write_pack(self.root / "pack", [make_record()])
        write_integrity_receipt(pack)

        def tamper(manifest: dict[str, Any]) -> None:
            manifest["priority"] = 99

        self._rewrite_manifest(pack, tamper)
        error = self.assert_error(
            ErrorCode.INTEGRITY_RECEIPT_MISMATCH,
            lambda: self._load(pack),
        )
        self.assertIn(error.details["field"], {"priority", "manifestDigest"})

    def test_manifest_scope_tamper_is_detected_by_scope_digest(self) -> None:
        pack = write_pack(self.root / "pack", [make_record()])
        original = write_integrity_receipt(pack)

        def tamper(manifest: dict[str, Any]) -> None:
            manifest["scope"] = {
                "kind": "project",
                "projectKey": "UEAgentKit",
                "visibility": "project",
            }

        new_manifest = self._rewrite_manifest(pack, tamper)
        new_receipt = build_integrity_receipt(new_manifest)
        new_receipt["scopeDigest"] = original["scopeDigest"]
        (pack / "pack.integrity.json").write_text(
            json.dumps(new_receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        error = self.assert_error(
            ErrorCode.INTEGRITY_RECEIPT_MISMATCH,
            lambda: self._load(pack),
        )
        self.assertEqual(error.details["field"], "scopeDigest")

    def test_manifest_provenance_tamper_is_detected_by_provenance_digest(self) -> None:
        pack = write_pack(self.root / "pack", [make_record()])
        original = write_integrity_receipt(pack)

        def tamper(manifest: dict[str, Any]) -> None:
            manifest["provenance"]["producer"] = "someone-else"

        new_manifest = self._rewrite_manifest(pack, tamper)
        new_receipt = build_integrity_receipt(new_manifest)
        new_receipt["provenanceDigest"] = original["provenanceDigest"]
        (pack / "pack.integrity.json").write_text(
            json.dumps(new_receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        error = self.assert_error(
            ErrorCode.INTEGRITY_RECEIPT_MISMATCH,
            lambda: self._load(pack),
        )
        self.assertEqual(error.details["field"], "provenanceDigest")

    def test_receipt_content_hash_tamper_is_detected(self) -> None:
        pack = write_pack(self.root / "pack", [make_record()])
        write_integrity_receipt(
            pack,
            receipt_tamper=lambda receipt: receipt.__setitem__(
                "contentHash", "sha256:" + "0" * 64
            ),
        )
        error = self.assert_error(
            ErrorCode.INTEGRITY_RECEIPT_MISMATCH,
            lambda: self._load(pack),
        )
        self.assertEqual(error.details["field"], "contentHash")

    def test_signed_receipt_is_not_claimed_as_trusted(self) -> None:
        pack = write_pack(self.root / "pack", [make_record()])
        write_integrity_receipt(pack, signature={"alg": "ed25519", "keyId": "k1", "value": "..."})
        self.assert_error(
            ErrorCode.SIGNATURE_UNSUPPORTED,
            lambda: self._load(pack),
        )

    def test_receipt_symlink_is_rejected(self) -> None:
        pack = write_pack(self.root / "pack", [make_record()])
        write_integrity_receipt(pack)
        real_receipt = pack / "pack.integrity.real.json"
        (pack / "pack.integrity.json").rename(real_receipt)
        try:
            os.symlink(real_receipt, pack / "pack.integrity.json")
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is not available")
        self.assert_error(
            ErrorCode.PACK_PATH_INVALID,
            lambda: self._load(pack),
        )

    def test_adapter_requires_receipt_when_configured(self) -> None:
        pack_root = self.root / "packs"
        pack_root.mkdir()
        pack = write_pack(pack_root / "r7.test.pack", [make_record()])
        adapter = self.track(
            ReadOnlyMountAdapter(
                caller=self.verified(),
                pack_root=pack_root,
                cache_dir=self.root / "cache",
                require_integrity_receipt=True,
                identity_clock=lambda: self.now[0],
            )
        )
        self.assert_error(
            ErrorCode.INTEGRITY_RECEIPT_REQUIRED,
            lambda: adapter.mount("r7.test.pack"),
        )
        self.assertEqual(adapter.list_mounts(), ())
        write_integrity_receipt(pack)
        info = adapter.mount("r7.test.pack")
        self.assertTrue(info.integrity["receiptVerified"])

    def test_adapter_rejects_demo_or_raw_caller(self) -> None:
        pack_root = self.root / "packs"
        pack_root.mkdir()
        with self.assertRaises(MemoryMountError) as caught:
            ReadOnlyMountAdapter(
                caller=DemoCallerContext(principal="alice", project_key="UEAgentKit"),
                pack_root=pack_root,
                cache_dir=self.root / "cache",
            )
        self.assertEqual(caught.exception.code, ErrorCode.IDENTITY_REQUIRED)
        with self.assertRaises(MemoryMountError) as caught:
            ReadOnlyMountAdapter(
                caller="alice",  # type: ignore[arg-type]
                pack_root=pack_root,
                cache_dir=self.root / "cache",
            )
        self.assertEqual(caught.exception.code, ErrorCode.IDENTITY_REQUIRED)

    def test_adapter_confines_untrusted_pack_names(self) -> None:
        pack_root = self.root / "packs"
        pack_root.mkdir()
        adapter = self.track(
            ReadOnlyMountAdapter(
                caller=self.verified(),
                pack_root=pack_root,
                cache_dir=self.root / "cache",
                require_integrity_receipt=False,
                identity_clock=lambda: self.now[0],
            )
        )
        for name in ("../escape", "a/b", "a\\b", "C:\\absolute", ".", "..", "", " bad"):
            with self.subTest(name=name):
                self.assert_error(ErrorCode.PACK_PATH_INVALID, lambda n=name: adapter.mount(n))

    def test_adapter_short_chinese_and_multipack_conflict(self) -> None:
        pack_root = self.root / "packs"
        pack_root.mkdir()
        write_pack(
            pack_root / "r7.project",
            [
                make_record(
                    record_id="rec-project",
                    title="知识冲突条目",
                    body="项目版本。",
                )
            ],
            pack_id="r7.project",
            scope=default_scope("project", projectKey="UEAgentKit"),
            priority=1,
        )
        write_pack(
            pack_root / "r7.shared",
            [
                make_record(
                    record_id="rec-shared",
                    title="知识冲突条目",
                    body="共享版本。",
                )
            ],
            pack_id="r7.shared",
            scope=default_scope("shared"),
            priority=99,
        )
        adapter = self.track(
            ReadOnlyMountAdapter(
                caller=self.verified(),
                pack_root=pack_root,
                cache_dir=self.root / "cache",
                require_integrity_receipt=False,
                identity_clock=lambda: self.now[0],
            )
        )
        adapter.mount("r7.project")
        adapter.mount("r7.shared")
        result = adapter.query("知识")
        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.items[0]["packId"], "r7.project")
        self.assertEqual(result.items[0]["matchReason"], "cjk-substring-fallback")
        self.assertEqual(result.items[0]["conflict"]["state"], "unresolved")

    def test_adapter_cache_poisoning_rebuilds_from_verified_pack(self) -> None:
        pack_root = self.root / "packs"
        pack_root.mkdir()
        pack = write_pack(pack_root / "r7.test.pack", [make_record(body="原始缓存正文。")])
        write_integrity_receipt(pack)
        adapter = self.track(
            ReadOnlyMountAdapter(
                caller=self.verified(),
                pack_root=pack_root,
                cache_dir=self.root / "cache",
                require_integrity_receipt=True,
                identity_clock=lambda: self.now[0],
            )
        )
        info = adapter.mount("r7.test.pack")
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
        info2 = adapter.mount("r7.test.pack")
        self.assertFalse(info2.index_reused)
        result = adapter.query("原始缓存正文")
        self.assertEqual(len(result.items), 1)

    def test_adapter_is_not_wired_into_production(self) -> None:
        package_root = SRC_ROOT / "ue_agent_kit"
        for name in ("memory_service.py", "mcp_server.py", "cli.py", "memory_context.py"):
            text = (package_root / name).read_text(encoding="utf-8")
            self.assertNotIn("ReadOnlyMountAdapter", text, name)
            self.assertNotIn("memory_mount", text, name)
        for path in sorted((package_root / "memory_mount").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertFalse(alias.name.startswith("ue_agent_kit"), path.name)
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    self.assertFalse(node.module.startswith("ue_agent_kit"), path.name)

    def test_public_api_does_not_accept_raw_identity_arguments(self) -> None:
        init_parameters = inspect.signature(MemoryMountRegistry.__init__).parameters
        self.assertIn("caller", init_parameters)
        self.assertNotIn("principal", init_parameters)
        self.assertNotIn("project_key", init_parameters)
        mount_parameters = inspect.signature(ReadOnlyMountAdapter.mount).parameters
        self.assertEqual(list(mount_parameters), ["self", "pack_name"])

    def _load(self, pack: Path) -> Any:
        from ue_agent_kit.memory_mount.records import load_pack

        return load_pack(pack, limits=MountLimits())

    def _rewrite_manifest(
        self,
        pack: Path,
        mutate: Callable[[dict[str, Any]], None],
    ) -> dict[str, Any]:
        path = pack / "pack.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        mutate(manifest)
        path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        return manifest


if __name__ == "__main__":
    unittest.main()
