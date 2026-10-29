from __future__ import annotations

import ast
import contextlib
import hashlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any


TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.memory_mount import (  # noqa: E402
    ErrorCode,
    MemoryMountError,
    MemoryMountRegistry,
    MountLimits,
)
from ue_agent_kit.memory_mount.jsonutil import canonical_json_bytes  # noqa: E402
from ue_agent_kit.memory_mount.records import load_pack  # noqa: E402
from ue_agent_kit.memory_schema import (  # noqa: E402
    CURRENT_MEMORY_SCHEMA_VERSION,
    MEMORY_MIGRATIONS,
)

try:
    import jsonschema  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - optional dependency
    jsonschema = None


NOW = "2026-10-23T12:00:00Z"


def make_record(
    *,
    record_id: str = "rec-001",
    record_type: str = "projectFact",
    subject_key: str = "subject.default",
    title: str = "默认标题",
    body: str = "默认正文。",
    source_kind: str = "user-confirmed",
    status: str | None = None,
    scopes: list[dict[str, Any]] | None = None,
    details: dict[str, Any] | None = None,
    updated_at: str = NOW,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "recordId": record_id,
        "recordType": record_type,
        "subjectKey": subject_key,
        "title": title,
        "body": body,
        "sourceKind": source_kind,
        "updatedAtUtc": updated_at,
    }
    if status is not None:
        record["status"] = status
    if scopes is not None:
        record["scopes"] = scopes
    if details is not None:
        record["details"] = details
    return record


def default_scope(kind: str = "shared", **overrides: Any) -> dict[str, Any]:
    scope: dict[str, Any] = {"kind": kind, "visibility": kind}
    scope.update(overrides)
    return scope


def default_compatibility(**overrides: Any) -> dict[str, Any]:
    compatibility: dict[str, Any] = {
        "minReaderVersion": 1,
        "maxReaderVersion": 1,
        "memorySchema": 6,
        "tokenizer": "trigram",
        "requiredFeatures": ["fts5-trigram", "cjk-substring-fallback"],
    }
    compatibility.update(overrides)
    return compatibility


def write_pack(
    root: Path,
    records: list[dict[str, Any]],
    *,
    pack_id: str = "r7.test.pack",
    scope: dict[str, Any] | None = None,
    priority: int = 50,
    provenance: dict[str, Any] | None = None,
    compatibility: dict[str, Any] | None = None,
    manifest_overrides: dict[str, Any] | None = None,
    canonical: bool = True,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    if canonical:
        records_bytes = b"".join(canonical_json_bytes(record) + b"\n" for record in records)
    else:
        records_bytes = b"".join(
            json.dumps(record, ensure_ascii=False).encode("utf-8") + b"\n" for record in records
        )
    content_hash = "sha256:" + hashlib.sha256(records_bytes).hexdigest()
    manifest: dict[str, Any] = {
        "schemaVersion": 1,
        "packId": pack_id,
        "contentHash": content_hash,
        "revision": "2026-10-23T12:00:00Z/1",
        "provenance": provenance
        or {
            "kind": "authored",
            "producer": "test:builder",
            "sourceRef": "tests/python/test_memory_mount.py",
            "createdAtUtc": NOW,
        },
        "priority": priority,
        "scope": scope or default_scope(),
        "compatibility": compatibility or default_compatibility(),
        "recordCount": len(records),
        "title": "R7 test pack",
    }
    if manifest_overrides:
        manifest.update(manifest_overrides)
    (root / "pack.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    (root / "records.jsonl").write_bytes(records_bytes)
    return root


class MemoryMountTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="r7-mount-test-")
        self.root = Path(self.temporary.name)
        self._registries: list[MemoryMountRegistry] = []

    def tearDown(self) -> None:
        for registry in self._registries:
            registry.close()
        self.temporary.cleanup()

    def registry(
        self,
        *,
        principal: str = "alice",
        project_key: str = "UEAgentKit",
        limits: MountLimits = MountLimits(),
        clock: Any = None,
    ) -> MemoryMountRegistry:
        registry = MemoryMountRegistry.for_demo(
            principal=principal,
            project_key=project_key,
            cache_dir=self.root / "cache",
            limits=limits,
            clock=clock,
        )
        self._registries.append(registry)
        return registry

    def assert_error(self, code: ErrorCode, action: Any) -> MemoryMountError:
        with self.assertRaises(MemoryMountError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)
        return caught.exception


class ManifestValidationTests(MemoryMountTestBase):
    def test_valid_manifest_matches_jsonschema(self) -> None:
        pack = write_pack(
            self.root / "pack",
            [make_record(scopes=[{"scopeType": "project", "scopeKey": "UEAgentKit"}])],
        )
        content = load_pack(pack, limits=MountLimits())
        self.assertEqual(content.manifest.pack_id, "r7.test.pack")
        self.assertEqual(content.manifest.record_count, 1)
        if jsonschema is None:
            self.skipTest("jsonschema is not installed")
        manifest_schema = json.loads(
            (TOOL_ROOT / "spec" / "memory-mount" / "r7-pack-manifest.schema.json").read_text(
                encoding="utf-8"
            )
        )
        record_schema = json.loads(
            (TOOL_ROOT / "spec" / "memory-mount" / "r7-pack-record.schema.json").read_text(
                encoding="utf-8"
            )
        )
        manifest_data = json.loads((pack / "pack.json").read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator(
            manifest_schema,
            format_checker=jsonschema.FormatChecker(),
        ).validate(manifest_data)
        record_data = json.loads((pack / "records.jsonl").read_text(encoding="utf-8").strip())
        jsonschema.Draft202012Validator(
            record_schema,
            format_checker=jsonschema.FormatChecker(),
        ).validate(record_data)

    def test_unknown_manifest_field_rejected(self) -> None:
        pack = write_pack(self.root / "pack", [make_record()], manifest_overrides={"extra": 1})
        error = self.assert_error(
            ErrorCode.MANIFEST_INVALID,
            lambda: load_pack(pack, limits=MountLimits()),
        )
        self.assertIn("unknownFields", error.details)

    def test_unsupported_schema_version_rejected(self) -> None:
        pack = write_pack(self.root / "pack", [make_record()], manifest_overrides={"schemaVersion": 2})
        self.assert_error(
            ErrorCode.MANIFEST_UNSUPPORTED_SCHEMA,
            lambda: load_pack(pack, limits=MountLimits()),
        )

    def test_signature_is_not_claimed(self) -> None:
        pack = write_pack(
            self.root / "pack",
            [make_record()],
            manifest_overrides={
                "provenance": {
                    "kind": "authored",
                    "producer": "test",
                    "sourceRef": "test",
                    "createdAtUtc": NOW,
                    "signature": {"alg": "ed25519", "keyId": "k1", "value": "..."},
                }
            },
        )
        self.assert_error(
            ErrorCode.SIGNATURE_UNSUPPORTED,
            lambda: load_pack(pack, limits=MountLimits()),
        )

    def test_unsupported_tokenizer_rejected(self) -> None:
        pack = write_pack(
            self.root / "pack",
            [make_record()],
            compatibility=default_compatibility(tokenizer="unicode61"),
        )
        self.assert_error(
            ErrorCode.MANIFEST_INCOMPATIBLE,
            lambda: load_pack(pack, limits=MountLimits()),
        )

    def test_unknown_required_feature_rejected(self) -> None:
        pack = write_pack(
            self.root / "pack",
            [make_record()],
            compatibility=default_compatibility(
                requiredFeatures=["fts5-trigram", "vector-search"]
            ),
        )
        self.assert_error(
            ErrorCode.MANIFEST_INCOMPATIBLE,
            lambda: load_pack(pack, limits=MountLimits()),
        )

    def test_project_scope_requires_project_key(self) -> None:
        pack = write_pack(
            self.root / "pack",
            [make_record()],
            scope={"kind": "project", "visibility": "project"},
        )
        self.assert_error(
            ErrorCode.MANIFEST_INVALID,
            lambda: load_pack(pack, limits=MountLimits()),
        )

    def test_personal_scope_requires_owner(self) -> None:
        pack = write_pack(
            self.root / "pack",
            [make_record()],
            scope={"kind": "personal", "visibility": "personal"},
        )
        self.assert_error(
            ErrorCode.MANIFEST_INVALID,
            lambda: load_pack(pack, limits=MountLimits()),
        )


class PackIntegrityTests(MemoryMountTestBase):
    def test_pack_record_count_limit_rejected(self) -> None:
        pack = write_pack(self.root / "pack", [make_record(), make_record(record_id="rec-002")])
        self.assert_error(
            ErrorCode.PACK_TOO_LARGE,
            lambda: load_pack(
                pack,
                limits=replace(MountLimits(), max_records_per_pack=1),
            ),
        )

    def test_hash_mismatch_rejected(self) -> None:
        pack = write_pack(self.root / "pack", [make_record()])
        with (pack / "records.jsonl").open("ab") as handle:
            handle.write(b" ")
        self.assert_error(
            ErrorCode.PACK_HASH_MISMATCH,
            lambda: load_pack(pack, limits=MountLimits()),
        )

    def test_noncanonical_record_line_rejected(self) -> None:
        pack = write_pack(self.root / "pack", [make_record()], canonical=False)
        self.assert_error(
            ErrorCode.PACK_RECORDS_INVALID,
            lambda: load_pack(pack, limits=MountLimits()),
        )

    def test_duplicate_record_id_rejected(self) -> None:
        record = make_record()
        pack = write_pack(self.root / "pack", [record, record])
        self.assert_error(
            ErrorCode.PACK_DUPLICATE_ID,
            lambda: load_pack(pack, limits=MountLimits()),
        )

    def test_record_count_mismatch_rejected(self) -> None:
        pack = write_pack(
            self.root / "pack",
            [make_record()],
            manifest_overrides={"recordCount": 2},
        )
        self.assert_error(
            ErrorCode.PACK_RECORDS_INVALID,
            lambda: load_pack(pack, limits=MountLimits()),
        )

    def test_empty_pack_is_valid(self) -> None:
        pack = write_pack(self.root / "pack", [])
        content = load_pack(pack, limits=MountLimits())
        self.assertEqual(content.record_count, 0)
        registry = self.registry()
        registry.mount(pack)
        result = registry.query("任意查询")
        self.assertEqual(result.items, ())

    def test_pack_root_must_be_a_directory(self) -> None:
        file_path = self.root / "not-a-pack"
        file_path.write_text("{}", encoding="utf-8")
        self.assert_error(
            ErrorCode.PACK_PATH_INVALID,
            lambda: load_pack(file_path, limits=MountLimits()),
        )

    def test_missing_pack_file(self) -> None:
        pack = self.root / "pack"
        pack.mkdir()
        self.assert_error(
            ErrorCode.PACK_NOT_FOUND,
            lambda: load_pack(pack, limits=MountLimits()),
        )

    def test_symlinked_pack_file_rejected(self) -> None:
        pack = write_pack(self.root / "pack", [make_record()])
        real_manifest = pack / "pack.real.json"
        (pack / "pack.json").rename(real_manifest)
        try:
            os.symlink(real_manifest, pack / "pack.json")
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is not available")
        self.assert_error(
            ErrorCode.PACK_PATH_INVALID,
            lambda: load_pack(pack, limits=MountLimits()),
        )

    def test_records_path_must_be_regular_file(self) -> None:
        pack = write_pack(self.root / "pack", [make_record()])
        (pack / "records.jsonl").unlink()
        (pack / "records.jsonl").mkdir()
        self.assert_error(
            ErrorCode.PACK_PATH_INVALID,
            lambda: load_pack(pack, limits=MountLimits()),
        )


class MountPermissionTests(MemoryMountTestBase):
    def test_shared_pack_mounts_for_anonymous_session(self) -> None:
        pack = write_pack(self.root / "pack", [make_record()])
        registry = self.registry(principal="anyone")
        info = registry.mount(pack)
        self.assertEqual(info.scope.kind, "shared")
        self.assertFalse(info.already_mounted)

    def test_personal_pack_is_owner_only(self) -> None:
        pack = write_pack(
            self.root / "pack",
            [make_record()],
            scope=default_scope("personal", owner="alice"),
        )
        self.assert_error(
            ErrorCode.PERMISSION_DENIED,
            lambda: self.registry(principal="bob").mount(pack),
        )
        info = self.registry(principal="alice").mount(pack)
        self.assertEqual(info.scope.owner, "alice")

    def test_authorization_precedes_record_parsing(self) -> None:
        pack = write_pack(
            self.root / "pack",
            [make_record()],
            scope=default_scope("personal", owner="alice"),
        )
        with (pack / "records.jsonl").open("ab") as handle:
            handle.write(b"tampered")
        self.assert_error(
            ErrorCode.PERMISSION_DENIED,
            lambda: self.registry(principal="bob").mount(pack),
        )

    def test_project_pack_requires_matching_project(self) -> None:
        pack = write_pack(
            self.root / "pack",
            [make_record()],
            scope=default_scope("project", projectKey="UEAgentKit"),
        )
        self.assert_error(
            ErrorCode.PROJECT_MISMATCH,
            lambda: self.registry(project_key="OtherProject").mount(pack),
        )
        info = self.registry(project_key="UEAgentKit").mount(pack)
        self.assertEqual(info.scope.project_key, "UEAgentKit")

    def test_empty_principal_rejected(self) -> None:
        self.assert_error(
            ErrorCode.PERMISSION_DENIED,
            lambda: MemoryMountRegistry.for_demo(
                principal=" ",
                project_key="UEAgentKit",
                cache_dir=self.root / "cache",
            ),
        )

    def test_idempotent_mount_and_revision_conflict(self) -> None:
        pack = write_pack(self.root / "pack", [make_record()])
        registry = self.registry()
        first = registry.mount(pack)
        second = registry.mount(pack)
        self.assertFalse(first.already_mounted)
        self.assertTrue(second.already_mounted)

        other = write_pack(
            self.root / "pack-v2",
            [make_record(body="不同的正文。")],
            pack_id="r7.test.pack",
        )
        self.assert_error(
            ErrorCode.PACK_REVISION_CONFLICT,
            lambda: registry.mount(other),
        )

    def test_same_content_from_another_path_is_rejected(self) -> None:
        first = write_pack(self.root / "pack-a", [make_record()])
        second = write_pack(self.root / "pack-b", [make_record()])
        registry = self.registry()
        registry.mount(first)
        self.assert_error(ErrorCode.PACK_ALREADY_MOUNTED, lambda: registry.mount(second))

    def test_unmount_and_unknown_mount(self) -> None:
        pack = write_pack(self.root / "pack", [make_record()])
        registry = self.registry()
        info = registry.mount(pack)
        self.assertEqual(len(registry.list_mounts()), 1)
        registry.unmount(info.pack_id)
        self.assertEqual(registry.list_mounts(), ())
        result = registry.query("默认正文")
        self.assertIn("r7-no-mounted-packs", result.warnings)
        self.assert_error(
            ErrorCode.PACK_NOT_MOUNTED,
            lambda: registry.unmount(info.pack_id),
        )

    def test_cache_directory_inside_pack_is_rejected(self) -> None:
        pack = write_pack(self.root / "pack", [make_record()])
        registry = MemoryMountRegistry.for_demo(
            principal="alice",
            project_key="UEAgentKit",
            cache_dir=pack / "cache",
        )
        self._registries.append(registry)
        self.assert_error(ErrorCode.PACK_PATH_INVALID, lambda: registry.mount(pack))

    def test_mount_limit(self) -> None:
        registry = self.registry(limits=replace(MountLimits(), max_mounted_packs=1))
        first = write_pack(self.root / "pack-a", [make_record()], pack_id="r7.test.a")
        second = write_pack(self.root / "pack-b", [make_record()], pack_id="r7.test.b")
        registry.mount(first)
        self.assert_error(ErrorCode.MOUNT_LIMIT, lambda: registry.mount(second))


class QueryTests(MemoryMountTestBase):
    def _mount_one(self, records: list[dict[str, Any]], **kwargs: Any) -> MemoryMountRegistry:
        pack = write_pack(self.root / "pack", records, **kwargs)
        registry = self.registry()
        registry.mount(pack)
        return registry

    def test_chinese_trigram_query(self) -> None:
        registry = self._mount_one(
            [
                make_record(
                    record_id="rec-cn",
                    title="共享契约审核与知识包",
                    body="这是共享契约审核与知识包的中文正文。",
                )
            ]
        )
        result = registry.query("契约审核")
        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.items[0]["matchReason"], "fts5-trigram")
        self.assertIn("契约审核", result.items[0]["title"])

    def test_short_chinese_query_uses_bounded_fallback(self) -> None:
        registry = self._mount_one(
            [make_record(title="共享契约审核", body="中文短查询正文。")]
        )
        result = registry.query("契约")
        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.items[0]["matchReason"], "cjk-substring-fallback")
        self.assertEqual(result.retrieval_mode, "fts5-trigram+substring-fallback")

    def test_ascii_trigram_substring(self) -> None:
        registry = self._mount_one(
            [make_record(title="Memory Mount contract", body="Read-only knowledge pack.")]
        )
        result = registry.query("Mount")
        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.items[0]["matchReason"], "fts5-trigram")

    def test_record_type_and_scope_filters(self) -> None:
        registry = self._mount_one(
            [
                make_record(
                    record_id="rec-rule",
                    record_type="projectRule",
                    title="规则条目",
                    body="规则内容。",
                    scopes=[{"scopeType": "asset", "scopeKey": "/Game/A.BP_A"}],
                ),
                make_record(
                    record_id="rec-fact",
                    record_type="projectFact",
                    title="事实条目",
                    body="事实内容。",
                ),
            ]
        )
        rules = registry.query("条目", record_types=("projectRule",))
        self.assertEqual([item["recordId"] for item in rules.items], ["rec-rule"])
        scoped = registry.query(
            "条目",
            scope_type="asset",
            scope_key="/Game/A.BP_A",
        )
        self.assertEqual([item["recordId"] for item in scoped.items], ["rec-rule"])
        self.assert_error(
            ErrorCode.QUERY_INVALID,
            lambda: registry.query("条目", scope_key="/Game/A.BP_A"),
        )

    def test_exact_duplicate_collapses_across_packs(self) -> None:
        shared = write_pack(
            self.root / "shared",
            [make_record(record_id="rec-shared", title="共享知识条目", body="相同内容。")],
            pack_id="r7.test.shared",
            scope=default_scope("shared"),
            priority=10,
        )
        project = write_pack(
            self.root / "project",
            [make_record(record_id="rec-project", title="共享知识条目", body="相同内容。")],
            pack_id="r7.test.project",
            scope=default_scope("project", projectKey="UEAgentKit"),
            priority=10,
        )
        registry = self.registry()
        registry.mount(shared)
        registry.mount(project)
        result = registry.query("知识条目")
        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.items[0]["packId"], "r7.test.project")
        self.assertNotIn("conflict", result.items[0])
        self.assertEqual(result.candidate_count, 2)

    def test_conflict_precedence_and_include_conflicts(self) -> None:
        shared = write_pack(
            self.root / "shared",
            [make_record(record_id="rec-shared", title="知识包条目", body="共享版本。")],
            pack_id="r7.test.shared",
            scope=default_scope("shared"),
            priority=90,
        )
        personal = write_pack(
            self.root / "personal",
            [make_record(record_id="rec-personal", title="知识包条目", body="个人版本。")],
            pack_id="r7.test.personal",
            scope=default_scope("personal", owner="alice"),
            priority=90,
        )
        project = write_pack(
            self.root / "project",
            [make_record(record_id="rec-project", title="知识包条目", body="项目版本。")],
            pack_id="r7.test.project",
            scope=default_scope("project", projectKey="UEAgentKit"),
            priority=1,
        )
        registry = self.registry()
        for pack in (shared, personal, project):
            registry.mount(pack)
        result = registry.query("知识包")
        self.assertEqual(len(result.items), 1)
        winner = result.items[0]
        self.assertEqual(winner["packId"], "r7.test.project")
        self.assertEqual(winner["conflict"]["state"], "unresolved")
        self.assertEqual(winner["conflict"]["alternativeCount"], 2)
        self.assertEqual(
            winner["conflict"]["alternativePackIds"],
            ["r7.test.personal", "r7.test.shared"],
        )
        self.assertEqual(result.conflict_count, 1)
        self.assertIn("r7-conflict-unresolved", result.warnings)

        expanded = registry.query("知识包", include_conflicts=True)
        self.assertEqual(len(expanded.items), 3)
        self.assertEqual(
            [item["packId"] for item in expanded.items],
            ["r7.test.project", "r7.test.personal", "r7.test.shared"],
        )
        self.assertEqual([item["selected"] for item in expanded.items], [True, False, False])

    def test_priority_tiebreak_within_tier(self) -> None:
        low = write_pack(
            self.root / "low",
            [make_record(record_id="rec-low", title="优先级条目", body="低优先级。")],
            pack_id="r7.test.low",
            priority=10,
        )
        high = write_pack(
            self.root / "high",
            [make_record(record_id="rec-high", title="优先级条目", body="高优先级。")],
            pack_id="r7.test.high",
            priority=90,
        )
        registry = self.registry()
        registry.mount(low)
        registry.mount(high)
        result = registry.query("优先级条目")
        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.items[0]["packId"], "r7.test.high")

    def test_body_preview_and_details_are_not_returned(self) -> None:
        registry = self._mount_one(
            [
                make_record(
                    title="预览条目",
                    body="甲" * 50,
                    details={"secret": "do-not-return"},
                )
            ]
        )
        result = registry.query("预览条目", body_preview_chars=10)
        item = result.items[0]
        self.assertEqual(item["bodyPreview"], "甲" * 10)
        self.assertTrue(item["bodyTruncated"])
        self.assertNotIn("details", item)
        self.assertNotIn("do-not-return", json.dumps(result.to_payload(), ensure_ascii=False))

    def test_limit_and_output_cap(self) -> None:
        records = [
            make_record(
                record_id=f"rec-{index}",
                subject_key=f"subject.{index}",
                title=f"容量条目 {index}",
                body="容量正文。",
            )
            for index in range(6)
        ]
        pack = write_pack(self.root / "pack", records)
        limits = replace(MountLimits(), max_output_chars=1_500)
        registry = self.registry(limits=limits)
        registry.mount(pack)
        result = registry.query("容量条目", limit=6, body_preview_chars=1)
        self.assertLessEqual(len(result.items), 6)
        self.assertTrue(result.truncated)
        self.assertIn("r7-output-truncated", result.warnings)
        self.assertLessEqual(
            len(json.dumps(result.to_payload(), ensure_ascii=False, separators=(",", ":"))),
            limits.max_output_chars,
        )

    def test_short_query_scan_limit_is_fail_closed(self) -> None:
        records = [
            make_record(record_id=f"rec-{index}", subject_key=f"subject.{index}")
            for index in range(2)
        ]
        pack = write_pack(self.root / "pack", records)
        limits = replace(MountLimits(), max_short_query_scan_records=1)
        registry = self.registry(limits=limits)
        registry.mount(pack)
        self.assert_error(ErrorCode.QUERY_TOO_BROAD, lambda: registry.query("条目"))

    def test_single_item_output_too_large(self) -> None:
        pack = write_pack(self.root / "pack", [make_record(title="超大条目")])
        limits = replace(MountLimits(), max_output_chars=100)
        registry = self.registry(limits=limits)
        registry.mount(pack)
        self.assert_error(ErrorCode.OUTPUT_TOO_LARGE, lambda: registry.query("超大条目"))

    def test_result_limit_warning(self) -> None:
        records = [
            make_record(
                record_id=f"rec-{index}",
                subject_key=f"subject.{index}",
                title=f"条目 {index}",
                body="正文。",
            )
            for index in range(3)
        ]
        registry = self._mount_one(records)
        result = registry.query("条目", limit=1)
        self.assertEqual(len(result.items), 1)
        self.assertTrue(result.truncated)
        self.assertIn("r7-result-limit-truncated", result.warnings)

    def test_timeout_is_fail_closed(self) -> None:
        pack = write_pack(self.root / "pack", [make_record(title="超时条目")])
        ticks = iter([0.0, 1.0])
        registry = self.registry(clock=lambda: next(ticks))
        registry.mount(pack)
        self.assert_error(
            ErrorCode.QUERY_TIMEOUT,
            lambda: registry.query("超时条目", timeout_ms=10),
        )

    def test_invalid_queries_and_filters(self) -> None:
        registry = self._mount_one([make_record(title="校验条目")])
        for query in ("", "   ", "a" * 129, "bad\x00query"):
            self.assert_error(ErrorCode.QUERY_INVALID, lambda q=query: registry.query(q))
        self.assert_error(
            ErrorCode.QUERY_INVALID,
            lambda: registry.query("校验条目", limit=0),
        )
        self.assert_error(
            ErrorCode.QUERY_INVALID,
            lambda: registry.query("校验条目", record_types=("not-a-type",)),
        )
        self.assert_error(
            ErrorCode.QUERY_INVALID,
            lambda: registry.query("校验条目", scope_type="not-a-scope"),
        )
        self.assert_error(
            ErrorCode.QUERY_INVALID,
            lambda: registry.query("校验条目", body_preview_chars=4_001),
        )


class IndexIsolationTests(MemoryMountTestBase):
    def test_index_is_read_only(self) -> None:
        pack = write_pack(self.root / "pack", [make_record()])
        registry = self.registry()
        info = registry.mount(pack)
        index = registry._mounts[info.pack_id].index
        self.assertEqual(index.connection.execute("PRAGMA query_only").fetchone()[0], 1)
        with self.assertRaises(sqlite3.OperationalError):
            index.connection.execute("DELETE FROM pack_records")
        self.assertEqual(
            index.connection.execute("SELECT COUNT(*) FROM pack_records").fetchone()[0],
            1,
        )

    def test_index_is_independent_of_memory_schema(self) -> None:
        self.assertEqual(CURRENT_MEMORY_SCHEMA_VERSION, 7)
        self.assertEqual([migration.version for migration in MEMORY_MIGRATIONS], [1, 2, 3, 4, 5, 6, 7])
        pack = write_pack(self.root / "pack", [make_record()])
        registry = self.registry()
        info = registry.mount(pack)
        index = registry._mounts[info.pack_id].index
        self.assertEqual(index.connection.execute("PRAGMA user_version").fetchone()[0], 1)
        self.assertNotEqual(
            index.connection.execute("PRAGMA user_version").fetchone()[0],
            CURRENT_MEMORY_SCHEMA_VERSION,
        )

    def test_index_is_reused_and_rebuilt_after_tamper(self) -> None:
        pack = write_pack(self.root / "pack", [make_record()])
        registry = self.registry()
        first = registry.mount(pack)
        self.assertFalse(first.index_reused)
        index_path = registry._mounts[first.pack_id].index.path
        registry.unmount(first.pack_id)
        self.assertTrue(index_path.is_file())

        connection = sqlite3.connect(str(index_path))
        try:
            connection.execute(
                "UPDATE pack_index_meta SET value = 'sha256:" + "0" * 64 + "' "
                "WHERE key = 'contentHash'"
            )
            connection.commit()
        finally:
            connection.close()

        second = registry.mount(pack)
        self.assertFalse(second.index_reused)
        result = registry.query("默认正文")
        self.assertEqual(len(result.items), 1)

    def test_index_is_rebuilt_after_body_tamper(self) -> None:
        pack = write_pack(self.root / "pack", [make_record(body="原始正文内容。")])
        registry = self.registry()
        first = registry.mount(pack)
        index_path = registry._mounts[first.pack_id].index.path
        registry.unmount(first.pack_id)
        connection = sqlite3.connect(str(index_path))
        try:
            connection.execute(
                "UPDATE pack_records SET body = '被篡改的正文。' WHERE record_id = 'rec-001'"
            )
            connection.commit()
        finally:
            connection.close()
        second = registry.mount(pack)
        self.assertFalse(second.index_reused)
        result = registry.query("原始正文内容")
        self.assertEqual(len(result.items), 1)
        self.assertIn("原始正文内容", result.items[0]["bodyPreview"])

    def test_module_is_not_imported_by_production_services(self) -> None:
        package_root = SRC_ROOT / "ue_agent_kit"
        for name in ("memory_service.py", "mcp_server.py", "cli.py", "memory_context.py"):
            text = (package_root / name).read_text(encoding="utf-8")
            self.assertNotIn("memory_mount", text, name)
        for path in sorted((package_root / "memory_mount").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertFalse(
                            alias.name.startswith("ue_agent_kit"),
                            f"{path.name} imports {alias.name}",
                        )
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    self.assertFalse(
                        node.module.startswith("ue_agent_kit"),
                        f"{path.name} imports {node.module}",
                    )

    def test_package_has_no_production_database_references(self) -> None:
        for path in sorted((SRC_ROOT / "ue_agent_kit" / "memory_mount").glob("*.py")):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("coordination_memory", text, path.name)
            # The shipped package must not embed a workstation-specific path or
            # a private development worktree name.
            self.assertNotRegex(text, r"(?i)[A-Za-z]:[\\/]")


class CliTests(MemoryMountTestBase):
    def test_cli_verify_and_query(self) -> None:
        from ue_agent_kit.memory_mount.cli import main

        pack = write_pack(self.root / "pack", [make_record(title="命令行条目")])
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(main(["verify", str(pack)]), 0)
        verified = json.loads(stdout.getvalue())
        self.assertTrue(verified["verified"])
        self.assertEqual(verified["recordCount"], 1)

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = main(
                [
                    "query",
                    str(pack),
                    "--query",
                    "命令行条目",
                    "--principal",
                    "alice",
                    "--cache-dir",
                    str(self.root / "cli-cache"),
                ]
            )
        self.assertEqual(code, 0)
        queried = json.loads(stdout.getvalue())
        self.assertTrue(queried["ok"])
        self.assertEqual(len(queried["result"]["items"]), 1)


if __name__ == "__main__":
    unittest.main()
