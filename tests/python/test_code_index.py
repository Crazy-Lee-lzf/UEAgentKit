from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.agent_api import IndexQueryService  # noqa: E402
from ue_agent_kit.cli import build_parser  # noqa: E402
from ue_agent_kit.code_index import build_code_index  # noqa: E402
from ue_agent_kit.database import open_database  # noqa: E402
from ue_agent_kit.freshness import IndexFreshnessTracker  # noqa: E402
from ue_agent_kit.indexer import _prune_assets  # noqa: E402
from ue_agent_kit.mcp_query_tools import register_query_tools  # noqa: E402
from ue_agent_kit.queries import search_assets, search_symbols  # noqa: E402


class FakeServer:
    def __init__(self) -> None:
        self.tools: dict[str, object] = {}

    def tool(self, *, annotations: object) -> object:
        del annotations

        def decorate(function: object) -> object:
            self.tools[getattr(function, "__name__")] = function
            return function

        return decorate


class CodeIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ueak_code_index_")
        self.root = Path(self.temporary.name)
        self.project_root = self.root / "Project"
        self.project_root.mkdir(parents=True)
        self.project_path = self.project_root / "TestProject.uproject"
        self.project_path.write_text("{}", encoding="utf-8")
        self.source_root = self.project_root / "Source" / "TestProject"
        (self.source_root / "Public").mkdir(parents=True)
        (self.source_root / "Private").mkdir(parents=True)
        self.header = self.source_root / "Public" / "FooActor.h"
        self.source = self.source_root / "Private" / "FooActor.cpp"
        self.header.write_text("class FFooActor {};\n", encoding="utf-8")
        self.source.write_text('#include "FooActor.h"\n', encoding="utf-8")
        (self.source_root / "README.txt").write_text("ignored", encoding="utf-8")
        self.database_path = self.root / "index.sqlite3"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _build(self) -> object:
        with open_database(self.database_path) as connection:
            return build_code_index(connection, self.project_root, self.database_path)

    def test_file_index_is_incremental_and_prunes_deleted_sources(self) -> None:
        first = self._build()
        self.assertEqual((first.added, first.updated, first.skipped, first.deleted, first.failed), (2, 0, 0, 0, 0))

        second = self._build()
        self.assertEqual((second.added, second.updated, second.skipped, second.deleted, second.failed), (0, 0, 2, 0, 0))

        time.sleep(0.01)
        self.source.write_text('#include "FooActor.h"\nint GValue = 1;\n', encoding="utf-8")
        third = self._build()
        self.assertEqual((third.added, third.updated, third.skipped, third.deleted, third.failed), (0, 1, 1, 0, 0))

        self.header.unlink()
        fourth = self._build()
        self.assertEqual((fourth.added, fourth.updated, fourth.skipped, fourth.deleted, fourth.failed), (0, 0, 1, 1, 0))

    def test_update_preserves_asset_id_and_rolls_back_on_failure(self) -> None:
        self._build()
        asset_path = "Source/TestProject/Private/FooActor.cpp"
        with open_database(self.database_path) as connection:
            before = connection.execute(
                "SELECT id, revision_value FROM assets WHERE asset_path = ?",
                (asset_path,),
            ).fetchone()
            self.assertIsNotNone(before)
            asset_id = int(before["id"])
            old_revision = str(before["revision_value"])

        time.sleep(0.01)
        self.source.write_text('#include "FooActor.h"\nint GValue = 7;\n', encoding="utf-8")
        updated = self._build()
        self.assertEqual(updated.updated, 1)

        with open_database(self.database_path) as connection:
            after = connection.execute(
                "SELECT id, revision_value FROM assets WHERE asset_path = ?",
                (asset_path,),
            ).fetchone()
            self.assertEqual(int(after["id"]), asset_id)
            self.assertNotEqual(str(after["revision_value"]), old_revision)

            stable_revision = str(after["revision_value"])
            connection.execute(
                """
                CREATE TRIGGER fail_code_update
                BEFORE UPDATE ON assets
                WHEN OLD.profile = 'code'
                BEGIN
                    SELECT RAISE(ABORT, 'forced-code-update-failure');
                END
                """
            )
            connection.commit()

            time.sleep(0.01)
            self.source.write_text('#include "FooActor.h"\nint GValue = 8;\n', encoding="utf-8")
            failed = build_code_index(connection, self.project_root, self.database_path)
            self.assertEqual(failed.failed, 1)

            preserved = connection.execute(
                "SELECT id, revision_value FROM assets WHERE asset_path = ?",
                (asset_path,),
            ).fetchone()
            self.assertEqual(int(preserved["id"]), asset_id)
            self.assertEqual(str(preserved["revision_value"]), stable_revision)

    def test_unreal_prune_never_deletes_code_profile_rows(self) -> None:
        self._build()
        with open_database(self.database_path) as connection:
            deleted = _prune_assets(connection, set(), "Source")
            self.assertEqual(deleted, [])
            remaining = search_assets(connection, "", profile="code")
            self.assertEqual(len(remaining), 2)

    def test_asset_search_hides_code_by_default_and_filters_fts_and_like(self) -> None:
        self._build()
        with open_database(self.database_path) as connection:
            self.assertEqual(search_assets(connection, "FooActor"), [])

            code_results = search_assets(connection, "FooActor", profile="code")
            self.assertEqual(
                {item["asset_path"] for item in code_results},
                {
                    "Source/TestProject/Private/FooActor.cpp",
                    "Source/TestProject/Public/FooActor.h",
                },
            )
            self.assertTrue(all(item["profile"] == "code" for item in code_results))

            connection.execute("DROP TABLE assets_fts")
            fallback_results = search_assets(connection, "FooActor", profile="code")
            self.assertEqual(
                {item["asset_path"] for item in fallback_results},
                {
                    "Source/TestProject/Private/FooActor.cpp",
                    "Source/TestProject/Public/FooActor.h",
                },
            )
            self.assertEqual(search_assets(connection, "FooActor"), [])

    def test_c1_indexes_types_inheritance_and_includes_without_path_based_symbol_ids(self) -> None:
        base = self.source_root / "Public" / "BaseTypes.h"
        derived = self.source_root / "Public" / "DerivedTypes.h"
        base.write_text(
            "namespace Combat {\n"
            "class FBaseType {\n"
            "};\n"
            "}\n",
            encoding="utf-8",
        )
        derived.write_text(
            '#include "BaseTypes.h"\n'
            "namespace Combat {\n"
            "struct FDerivedType : public FBaseType {\n"
            "};\n"
            "enum class EMode : uint8 {\n"
            "    A,\n"
            "};\n"
            "}\n",
            encoding="utf-8",
        )

        result = self._build()
        self.assertGreaterEqual(result.symbols, 4)
        self.assertGreaterEqual(result.references, 3)

        with open_database(self.database_path) as connection:
            self.assertEqual(search_symbols(connection, "FDerivedType"), [])
            symbols = search_symbols(connection, "", profile="code", limit=100)
            by_name = {item["name"]: item for item in symbols}

            self.assertEqual(by_name["FBaseType"]["stable_id"], "cpp:type:Combat::FBaseType")
            self.assertEqual(by_name["FDerivedType"]["stable_id"], "cpp:type:Combat::FDerivedType")
            self.assertEqual(by_name["EMode"]["stable_id"], "cpp:type:Combat::EMode")
            self.assertNotIn("Source/", by_name["FDerivedType"]["stable_id"])

            inheritance = connection.execute(
                """
                SELECT source_symbol_id, target_symbol_id, target_asset_path, target_name
                FROM references_table
                WHERE kind = 'inherits' AND source_symbol_id = ?
                """,
                ("cpp:type:Combat::FDerivedType",),
            ).fetchone()
            self.assertIsNotNone(inheritance)
            self.assertEqual(inheritance["target_symbol_id"], "cpp:type:Combat::FBaseType")
            self.assertEqual(
                inheritance["target_asset_path"],
                "Source/TestProject/Public/BaseTypes.h",
            )

            include = connection.execute(
                """
                SELECT target_asset_path, target_path
                FROM references_table
                WHERE kind = 'include'
                  AND asset_id = (
                      SELECT id FROM assets
                      WHERE asset_path = 'Source/TestProject/Public/DerivedTypes.h'
                  )
                """,
            ).fetchone()
            self.assertIsNotNone(include)
            self.assertEqual(include["target_path"], "BaseTypes.h")
            self.assertEqual(
                include["target_asset_path"],
                "Source/TestProject/Public/BaseTypes.h",
            )

    def test_c1_allman_namespace_and_raw_string_keep_qualified_identity(self) -> None:
        header = self.source_root / "Public" / "AllmanType.h"
        header.write_text(
            "namespace Allman\n"
            "{\n"
            "class FAllmanType\n"
            "{\n"
            "};\n"
            'static const char* Text = R"(raw { braces } text)";\n'
            "}\n",
            encoding="utf-8",
        )
        result = self._build()
        self.assertEqual(result.failed, 0)

        with open_database(self.database_path) as connection:
            rows = search_symbols(connection, "FAllmanType", profile="code")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["stable_id"], "cpp:type:Allman::FAllmanType")

    def test_c1_successful_files_refresh_semantics_even_when_another_file_fails(self) -> None:
        good = self.source_root / "Public" / "GoodType.h"
        good.write_text("class FOldGoodType {\n};\n", encoding="utf-8")
        self._build()

        with open_database(self.database_path) as connection:
            self.assertEqual(len(search_symbols(connection, "FOldGoodType", profile="code")), 1)
            connection.execute(
                """
                CREATE TRIGGER fail_one_code_update
                BEFORE UPDATE ON assets
                WHEN OLD.asset_path = 'Source/TestProject/Private/FooActor.cpp'
                BEGIN
                    SELECT RAISE(ABORT, 'forced-one-file-failure');
                END
                """
            )
            connection.commit()

            time.sleep(0.01)
            self.source.write_text('#include "FooActor.h"\nint GChanged = 9;\n', encoding="utf-8")
            good.write_text("class FNewGoodType {\n};\n", encoding="utf-8")

            result = build_code_index(connection, self.project_root, self.database_path)
            self.assertEqual(result.failed, 1)
            self.assertEqual(search_symbols(connection, "FOldGoodType", profile="code"), [])
            refreshed = search_symbols(connection, "FNewGoodType", profile="code")
            self.assertEqual(len(refreshed), 1)
            self.assertEqual(refreshed[0]["stable_id"], "cpp:type:FNewGoodType")

    def test_c1_partial_source_root_preserves_other_code_semantics(self) -> None:
        other_header = self.project_root / "Source" / "OtherModule" / "Public" / "OtherType.h"
        other_header.parent.mkdir(parents=True)
        other_header.write_text(
            "namespace Other {\n"
            "class FOtherType {\n"
            "};\n"
            "}\n",
            encoding="utf-8",
        )
        self._build()

        with open_database(self.database_path) as connection:
            before = search_symbols(connection, "FOtherType", profile="code")
            self.assertEqual(len(before), 1)

            subset = build_code_index(
                connection,
                self.project_root,
                self.database_path,
                source_roots=("Source/TestProject",),
            )
            self.assertEqual(subset.failed, 0)

            after = search_symbols(connection, "FOtherType", profile="code")
            self.assertEqual(len(after), 1)
            self.assertEqual(after[0]["stable_id"], "cpp:type:Other::FOtherType")

    def test_c1_duplicate_fqn_is_fail_closed(self) -> None:
        first = self.source_root / "Public" / "DuplicateA.h"
        second = self.source_root / "Public" / "DuplicateB.h"
        definition = "namespace Combat {\nclass FDuplicate {\n};\n}\n"
        first.write_text(definition, encoding="utf-8")
        second.write_text(definition, encoding="utf-8")

        result = self._build()
        self.assertTrue(
            any(
                warning.startswith("ambiguous-duplicate:cpp:type:Combat::FDuplicate:")
                for warning in result.semantic_warnings
            )
        )
        with open_database(self.database_path) as connection:
            self.assertEqual(
                search_symbols(connection, "FDuplicate", profile="code"),
                [],
            )

    def test_agent_api_can_search_code_symbols_with_relative_path_prefix(self) -> None:
        self._build()
        service = IndexQueryService(self.database_path)
        payload = service.search(
            "FFooActor",
            scope="symbols",
            profile="code",
            path_prefix="Source/TestProject/Public",
        )
        self.assertEqual(payload["filters"]["profile"], "code")
        self.assertEqual(len(payload["results"]), 1)
        self.assertEqual(payload["results"][0]["stable_id"], "cpp:type:FFooActor")

    def test_index_status_surfaces_code_knowledge_counts(self) -> None:
        self._build()
        status = IndexQueryService(self.database_path).check()
        code = status["codeKnowledge"]
        self.assertTrue(code["available"])
        self.assertEqual(code["profile"], "code")
        self.assertEqual(code["assetClass"], "CppSourceFile")
        self.assertEqual(code["assets"], 2)
        self.assertEqual(code["types"], 1)
        self.assertEqual(code["functions"], 0)
        self.assertEqual(code["properties"], 0)
        self.assertEqual(code["references"]["include"], 1)
        self.assertEqual(code["references"]["inherits"], 0)
        self.assertEqual(code["references"]["implements"], 0)
        self.assertTrue(code["lastCodeIndexedAtUtc"])
        self.assertFalse(code["reflection"]["imported"])

    def test_agent_api_get_asset_supports_code_paths_and_code_symbols(self) -> None:
        self._build()
        service = IndexQueryService(self.database_path)
        asset_path = "Source/TestProject/Public/FooActor.h"

        payload = service.get_asset(
            asset_path,
            sections=("identity", "summary", "metadata", "symbols"),
            include_details=True,
        )
        self.assertTrue(payload["found"])
        self.assertEqual(payload["assetPath"], asset_path)
        self.assertEqual(payload["asset"]["profile"], "code")
        self.assertEqual(payload["asset"]["asset_class"], "CppSourceFile")
        self.assertEqual(
            [item["stable_id"] for item in payload["asset"]["symbols"]],
            ["cpp:type:FFooActor"],
        )
        self.assertEqual(
            payload["asset"]["symbols"][0]["details"]["qualifiedName"],
            "FFooActor",
        )

        with self.assertRaisesRegex(ValueError, "safe project-relative indexed path"):
            service.get_asset("C:/Project/Source/FooActor.h")

    def test_agent_api_find_references_supports_code_asset_paths(self) -> None:
        self._build()
        service = IndexQueryService(self.database_path)
        source_path = "Source/TestProject/Private/FooActor.cpp"
        target_path = "Source/TestProject/Public/FooActor.h"

        outgoing = service.find_references(asset_path=source_path)
        self.assertEqual(len(outgoing["results"]), 1)
        self.assertEqual(outgoing["results"][0]["kind"], "include")
        self.assertEqual(outgoing["results"][0]["target_asset_path"], target_path)

        targeted = service.find_references(target_asset_path=target_path)
        self.assertEqual(len(targeted["results"]), 1)
        self.assertEqual(targeted["results"][0]["asset_path"], source_path)

        with self.assertRaisesRegex(ValueError, "safe project-relative indexed path"):
            service.find_references(asset_path="../Source/Foo.cpp")

    def test_c1_symbol_search_profile_applies_to_fts_and_like_fallback(self) -> None:
        self._build()
        with open_database(self.database_path) as connection:
            self.assertEqual(search_symbols(connection, "FFooActor"), [])
            fts_results = search_symbols(connection, "FFooActor", profile="code")
            self.assertEqual(len(fts_results), 1)

            connection.execute("DROP TABLE symbols_fts")
            self.assertEqual(search_symbols(connection, "FFooActor"), [])
            fallback = search_symbols(connection, "FFooActor", profile="code")
            self.assertEqual(len(fallback), 1)
            self.assertEqual(fallback[0]["stable_id"], "cpp:type:FFooActor")

    def test_agent_api_symbol_continuation_keeps_code_profile_identity(self) -> None:
        another = self.source_root / "Public" / "AnotherType.h"
        another.write_text("class FAnotherType {\n};\n", encoding="utf-8")
        self._build()
        service = IndexQueryService(self.database_path)

        first = service.search("", scope="symbols", profile="code", limit=1)
        self.assertEqual(first["filters"]["profile"], "code")
        token = first["pagination"]["continuationToken"]
        self.assertTrue(token)

        second = service.search(scope="symbols", profile="", continuation_token=token)
        self.assertEqual(second["filters"]["profile"], "code")
        self.assertEqual(second["pagination"]["source"], "continuation-token")
        self.assertNotEqual(
            first["results"][0]["stable_id"],
            second["results"][0]["stable_id"],
        )

    def test_agent_api_continuation_keeps_code_profile_identity(self) -> None:
        self._build()
        service = IndexQueryService(self.database_path)

        self.assertEqual(service.search("")["results"], [])
        first = service.search("", profile="code", limit=1)
        self.assertEqual(first["filters"]["profile"], "code")
        token = first["pagination"]["continuationToken"]
        self.assertTrue(token)

        second = service.search(profile="", continuation_token=token)
        self.assertEqual(second["filters"]["profile"], "code")
        self.assertEqual(second["pagination"]["source"], "continuation-token")
        self.assertNotEqual(first["results"][0]["asset_path"], second["results"][0]["asset_path"])

    def test_mcp_query_tool_propagates_profile(self) -> None:
        self._build()
        service = IndexQueryService(self.database_path)
        server = FakeServer()

        register_query_tools(
            server=server,
            index_service=service,
            workflow_service=None,
            live_editor_service=None,
            read_annotations=object(),
            error_response=lambda tool, exc, read_only: {
                "tool": tool,
                "error": str(exc),
                "readOnly": read_only,
            },
            capabilities_response=lambda workflow, live: {},
            project_status_response=lambda index, workflow, live: {},
        )
        search = server.tools["ue_search"]
        payload = search(profile="code")  # type: ignore[operator]
        self.assertEqual(payload["filters"]["profile"], "code")
        self.assertEqual(len(payload["results"]), 2)

        symbols_payload = search(  # type: ignore[operator]
            query="FFooActor",
            scope="symbols",
            profile="code",
        )
        self.assertEqual(symbols_payload["filters"]["profile"], "code")
        self.assertEqual(len(symbols_payload["results"]), 1)

        get_asset = server.tools["ue_get_asset"]
        code_asset = get_asset(  # type: ignore[operator]
            asset_path="Source/TestProject/Public/FooActor.h",
            sections=["identity", "symbols"],
            include_details=True,
        )
        self.assertTrue(code_asset["found"])
        self.assertEqual(
            code_asset["asset"]["symbols"][0]["stable_id"],
            "cpp:type:FFooActor",
        )

        find_refs = server.tools["ue_find_references"]
        references = find_refs(  # type: ignore[operator]
            asset_path="Source/TestProject/Private/FooActor.cpp",
        )
        self.assertEqual(len(references["results"]), 1)
        self.assertEqual(references["results"][0]["kind"], "include")

    def test_code_freshness_uses_source_sha_size_and_mtime(self) -> None:
        self._build()
        service = IndexQueryService(self.database_path)
        tracker = IndexFreshnessTracker(service, self.project_path, self.root / "RevisionExport")
        asset_path = "Source/TestProject/Private/FooActor.cpp"

        fresh = tracker.inspect_asset(asset_path)
        self.assertEqual(fresh["state"], "fresh")
        self.assertTrue(fresh["comparisons"]["indexMatchesDisk"])
        self.assertTrue(fresh["comparisons"]["indexMatchesSourceSize"])
        self.assertTrue(fresh["comparisons"]["indexMatchesSourceMtime"])
        self.assertIsNone(fresh["comparisons"]["indexMatchesRevisionExport"])

        source_stat = self.source.stat()
        os.utime(self.source, (source_stat.st_atime, source_stat.st_mtime + 5))
        mtime_only = tracker.inspect_asset(asset_path)
        self.assertEqual(mtime_only["state"], "fresh")
        self.assertTrue(mtime_only["comparisons"]["indexMatchesDisk"])
        self.assertFalse(mtime_only["comparisons"]["indexMatchesSourceMtime"])

        time.sleep(0.01)
        self.source.write_text('#include "FooActor.h"\nint GChanged = 2;\n', encoding="utf-8")
        os.utime(self.source, None)
        stale = tracker.inspect_asset(asset_path)
        self.assertEqual(stale["state"], "stale")
        self.assertIn("index-disk-mismatch", stale["reason"])

        self.source.unlink()
        missing = tracker.inspect_asset(asset_path)
        self.assertEqual(missing["state"], "unavailable")
        self.assertIn("source-file-missing", missing["reason"])

    def test_missing_source_root_fails_before_prune(self) -> None:
        self._build()
        with open_database(self.database_path) as connection:
            with self.assertRaises(FileNotFoundError):
                build_code_index(
                    connection,
                    self.project_root,
                    self.database_path,
                    source_roots=("MissingSource",),
                )
            remaining = search_assets(connection, "", profile="code")
            self.assertEqual(len(remaining), 2)

    def test_cli_exposes_code_index_and_profile_filter(self) -> None:
        parser = build_parser()
        search_args = parser.parse_args(["search", "assets", "FooActor", "--profile", "code"])
        self.assertEqual(search_args.profile, "code")

        symbol_args = parser.parse_args(["search", "symbols", "FFooActor", "--profile", "code"])
        self.assertEqual(symbol_args.profile, "code")

        index_args = parser.parse_args(["index", "code", str(self.project_root), "--source-root", "Source"])
        self.assertEqual(index_args.index_command, "code")
        self.assertEqual(index_args.source_roots, ["Source"])


if __name__ == "__main__":
    unittest.main()
