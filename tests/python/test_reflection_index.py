from __future__ import annotations

import json
import sys
import tempfile
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
from ue_agent_kit.queries import search_symbols  # noqa: E402
from ue_agent_kit.reflection_index import index_reflection_export  # noqa: E402


class ReflectionIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ueak_reflection_index_")
        self.root = Path(self.temporary.name)
        self.project_root = self.root / "Project"
        public = self.project_root / "Source" / "Game" / "Public"
        public.mkdir(parents=True)
        (self.project_root / "Game.uproject").write_text("{}", encoding="utf-8")
        (public / "Base.h").write_text("class ABase {\n};\n", encoding="utf-8")
        (public / "Interface.h").write_text("class IFoo {\n};\n", encoding="utf-8")
        (public / "Hero.h").write_text(
            "class AHero : public ABase, public IFoo {\n};\n",
            encoding="utf-8",
        )
        self.database_path = self.root / "index.sqlite3"
        with open_database(self.database_path) as connection:
            result = build_code_index(
                connection,
                self.project_root,
                self.database_path,
                project_key="Game",
            )
            self.assertEqual(result.failed, 0)
        self.export_path = self.root / "reflection.json"
        self._write_export()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _type_record(self) -> dict[str, object]:
        return {
            "stableId": "cpp:type:AHero",
            "kind": "class",
            "cppName": "AHero",
            "reflectionName": "Hero",
            "module": "Game",
            "objectPath": "/Script/Game.Hero",
            "flagsValue": "1",
            "metadata": {"BlueprintType": "true"},
            "superCppName": "ABase",
            "superObjectPath": "/Script/Game.Base",
            "interfaces": [
                {
                    "cppName": "IFoo",
                    "stableId": "cpp:type:IFoo",
                    "objectPath": "/Script/Game.Foo",
                }
            ],
            "properties": [
                {
                    "stableId": "cpp:property:AHero::Health",
                    "name": "Health",
                    "ownerCppName": "AHero",
                    "ownerStableId": "cpp:type:AHero",
                    "cppType": "float",
                    "flagsValue": "2",
                    "functionParameter": False,
                    "outParameter": False,
                    "referenceParameter": False,
                    "constParameter": False,
                    "metadata": {"Category": "Stats"},
                }
            ],
            "functions": [
                {
                    "stableId": "cpp:function:AHero::SetHealth",
                    "name": "SetHealth",
                    "ownerCppName": "AHero",
                    "ownerStableId": "cpp:type:AHero",
                    "flagsValue": "4",
                    "metadata": {"BlueprintCallable": "true"},
                    "returnType": "bool",
                    "parameters": [
                        {
                            "stableId": "cpp:parameter:AHero::SetHealth::Value",
                            "name": "Value",
                            "cppType": "float",
                            "ownerStableId": "cpp:function:AHero::SetHealth",
                        }
                    ],
                }
            ],
        }

    def _write_export(
        self,
        *,
        project_name: str = "Game",
        types: list[dict[str, object]] | None = None,
    ) -> None:
        payload = {
            "schemaVersion": "reflection-1.0",
            "exporterVersion": "0.8.0",
            "engineVersion": "5.6-test",
            "projectName": project_name,
            "createdUtc": "2026-09-21T00:00:00Z",
            "profile": "code-reflection",
            "modules": ["Game"],
            "types": types if types is not None else [self._type_record()],
            "summary": {},
        }
        self.export_path.write_text(json.dumps(payload), encoding="utf-8")

    def test_reflection_enriches_type_and_adds_function_property_and_interfaces(self) -> None:
        with open_database(self.database_path) as connection:
            result = index_reflection_export(connection, self.export_path)
            self.assertEqual(result.errors, [])
            self.assertEqual(result.matched_types, 1)
            self.assertEqual(result.unmatched_types, 0)
            self.assertEqual(result.functions, 1)
            self.assertEqual(result.properties, 1)
            self.assertEqual(result.references, 2)

            owner = search_symbols(
                connection,
                "AHero",
                profile="code",
                include_details=True,
            )
            type_row = next(item for item in owner if item["stable_id"] == "cpp:type:AHero")
            self.assertEqual(
                type_row["details"]["reflection"]["objectPath"],
                "/Script/Game.Hero",
            )
            self.assertEqual(
                type_row["details"]["reflectionEvidence"],
                "ue-reflection",
            )

            function = search_symbols(
                connection,
                "SetHealth",
                profile="code",
                kind="function",
                include_details=True,
            )
            self.assertEqual(len(function), 1)
            self.assertEqual(function[0]["stable_id"], "cpp:function:AHero::SetHealth")
            self.assertEqual(function[0]["owner_symbol_id"], "cpp:type:AHero")
            self.assertEqual(function[0]["class_path"], "bool")
            self.assertEqual(function[0]["details"]["evidence"], "ue-reflection")

            property_rows = search_symbols(
                connection,
                "Health",
                profile="code",
                kind="property",
                include_details=True,
            )
            self.assertEqual(len(property_rows), 1)
            self.assertEqual(property_rows[0]["stable_id"], "cpp:property:AHero::Health")
            self.assertEqual(property_rows[0]["class_path"], "float")

            inheritance = connection.execute(
                """
                SELECT target_symbol_id, target_asset_path, target_path, details_json
                FROM references_table
                WHERE stable_id = 'cpp:inherits:cpp:type:AHero:ABase'
                """
            ).fetchone()
            self.assertEqual(inheritance["target_symbol_id"], "cpp:type:ABase")
            self.assertTrue(str(inheritance["target_asset_path"]).endswith("/Base.h"))
            self.assertEqual(inheritance["target_path"], "/Script/Game.Base")
            self.assertEqual(
                json.loads(inheritance["details_json"])["reflection"]["evidence"],
                "ue-reflection",
            )

            interface = connection.execute(
                """
                SELECT target_symbol_id, target_asset_path, target_path
                FROM references_table
                WHERE stable_id = 'cpp:implements:cpp:type:AHero:cpp:type:IFoo'
                """
            ).fetchone()
            self.assertIsNotNone(interface)
            self.assertEqual(interface["target_symbol_id"], "cpp:type:IFoo")
            self.assertTrue(str(interface["target_asset_path"]).endswith("/Interface.h"))
            self.assertEqual(interface["target_path"], "/Script/Game.Foo")

    def test_index_status_surfaces_reflection_counts(self) -> None:
        with open_database(self.database_path) as connection:
            result = index_reflection_export(connection, self.export_path)
            self.assertEqual(result.errors, [])

        status = IndexQueryService(self.database_path).check()
        code = status["codeKnowledge"]
        self.assertTrue(code["available"])
        self.assertEqual(code["functions"], 1)
        self.assertEqual(code["properties"], 1)
        self.assertEqual(code["references"]["implements"], 1)
        self.assertTrue(code["reflection"]["imported"])
        self.assertEqual(code["reflection"]["project"], "Game")
        self.assertEqual(code["reflection"]["schemaVersion"], "reflection-1.0")
        self.assertEqual(code["reflection"]["matchedTypes"], 1)
        self.assertEqual(code["reflection"]["unmatchedTypes"], 0)

    def test_reflection_import_is_idempotent_and_removes_stale_children(self) -> None:
        with open_database(self.database_path) as connection:
            first = index_reflection_export(connection, self.export_path)
            self.assertEqual(first.errors, [])
            second = index_reflection_export(connection, self.export_path)
            self.assertEqual(second.errors, [])
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM symbols WHERE stable_id = 'cpp:function:AHero::SetHealth'"
                ).fetchone()[0],
                1,
            )

            record = self._type_record()
            record["functions"] = []
            record["properties"] = []
            self._write_export(types=[record])
            third = index_reflection_export(connection, self.export_path)
            self.assertEqual(third.errors, [])
            self.assertEqual(third.functions, 0)
            self.assertEqual(third.properties, 0)
            self.assertEqual(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM symbols
                    WHERE owner_symbol_id = 'cpp:type:AHero'
                      AND kind IN ('function', 'property')
                    """
                ).fetchone()[0],
                0,
            )

    def test_code_rebuild_preserves_reflection_enrichment(self) -> None:
        with open_database(self.database_path) as connection:
            imported = index_reflection_export(connection, self.export_path)
            self.assertEqual(imported.errors, [])

            rebuilt = build_code_index(
                connection,
                self.project_root,
                self.database_path,
                project_key="Game",
            )
            self.assertEqual(rebuilt.failed, 0)

            owner = connection.execute(
                """
                SELECT details_json
                FROM symbols
                WHERE stable_id = 'cpp:type:AHero'
                """
            ).fetchone()
            self.assertIsNotNone(owner)
            details = json.loads(owner["details_json"])
            self.assertEqual(details["reflectionEvidence"], "ue-reflection")
            self.assertEqual(details["reflection"]["objectPath"], "/Script/Game.Hero")

            self.assertEqual(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM symbols
                    WHERE owner_symbol_id = 'cpp:type:AHero'
                      AND kind = 'function'
                      AND stable_id = 'cpp:function:AHero::SetHealth'
                    """
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM symbols
                    WHERE owner_symbol_id = 'cpp:type:AHero'
                      AND kind = 'property'
                      AND stable_id = 'cpp:property:AHero::Health'
                    """
                ).fetchone()[0],
                1,
            )

            interface = connection.execute(
                """
                SELECT details_json
                FROM references_table
                WHERE stable_id = 'cpp:implements:cpp:type:AHero:cpp:type:IFoo'
                """
            ).fetchone()
            self.assertIsNotNone(interface)
            self.assertEqual(
                json.loads(interface["details_json"])["evidence"],
                "ue-reflection",
            )

            inheritance = connection.execute(
                """
                SELECT details_json
                FROM references_table
                WHERE stable_id = 'cpp:inherits:cpp:type:AHero:ABase'
                """
            ).fetchone()
            self.assertIsNotNone(inheritance)
            inheritance_details = json.loads(inheritance["details_json"])
            self.assertEqual(
                inheritance_details["reflection"]["evidence"],
                "ue-reflection",
            )
            self.assertEqual(inheritance_details["baseName"], "ABase")

    def test_code_rebuild_preserves_reflection_only_inheritance(self) -> None:
        record = self._type_record()
        record["superCppName"] = "AReflectionOnlyBase"
        record["superObjectPath"] = "/Script/Engine.ReflectionOnlyBase"
        self._write_export(types=[record])

        stable_id = "cpp:inherits:cpp:type:AHero:AReflectionOnlyBase"
        with open_database(self.database_path) as connection:
            imported = index_reflection_export(connection, self.export_path)
            self.assertEqual(imported.errors, [])
            before = connection.execute(
                """
                SELECT details_json, target_symbol_id, target_asset_path
                FROM references_table
                WHERE stable_id = ?
                """,
                (stable_id,),
            ).fetchone()
            self.assertIsNotNone(before)
            self.assertEqual(before["target_symbol_id"], "")
            self.assertEqual(before["target_asset_path"], "")

            rebuilt = build_code_index(
                connection,
                self.project_root,
                self.database_path,
                project_key="Game",
            )
            self.assertEqual(rebuilt.failed, 0)

            after = connection.execute(
                """
                SELECT details_json, target_symbol_id, target_asset_path
                FROM references_table
                WHERE stable_id = ?
                """,
                (stable_id,),
            ).fetchone()
            self.assertIsNotNone(after)
            details = json.loads(after["details_json"])
            self.assertEqual(details["reflection"]["evidence"], "ue-reflection")
            self.assertEqual(
                details["reflection"]["superObjectPath"],
                "/Script/Engine.ReflectionOnlyBase",
            )
            self.assertFalse(details["reflection"]["resolved"])
            self.assertEqual(after["target_symbol_id"], "")
            self.assertEqual(after["target_asset_path"], "")

    def test_code_rebuild_clears_stale_implements_target(self) -> None:
        with open_database(self.database_path) as connection:
            imported = index_reflection_export(connection, self.export_path)
            self.assertEqual(imported.errors, [])

            interface_path = (
                self.project_root
                / "Source"
                / "Game"
                / "Public"
                / "Interface.h"
            )
            interface_path.write_text("// interface removed\n", encoding="utf-8")

            rebuilt = build_code_index(
                connection,
                self.project_root,
                self.database_path,
                project_key="Game",
            )
            self.assertEqual(rebuilt.failed, 0)

            implements = connection.execute(
                """
                SELECT target_symbol_id, target_kind, target_asset_path, details_json
                FROM references_table
                WHERE stable_id = 'cpp:implements:cpp:type:AHero:cpp:type:IFoo'
                """
            ).fetchone()
            self.assertIsNotNone(implements)
            self.assertEqual(implements["target_symbol_id"], "")
            self.assertEqual(implements["target_kind"], "class")
            self.assertEqual(implements["target_asset_path"], "")
            details = json.loads(implements["details_json"])
            self.assertEqual(details["evidence"], "ue-reflection")
            self.assertFalse(details["resolved"])

            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM symbols WHERE stable_id = 'cpp:type:IFoo'"
                ).fetchone()[0],
                0,
            )

    def test_unmatched_reflected_type_is_reported_without_creating_orphan_symbols(self) -> None:
        ghost = self._type_record()
        ghost["stableId"] = "cpp:type:AGhost"
        ghost["cppName"] = "AGhost"
        ghost["properties"] = []
        ghost["functions"] = []
        self._write_export(types=[ghost])

        with open_database(self.database_path) as connection:
            result = index_reflection_export(connection, self.export_path)
            self.assertEqual(result.errors, [])
            self.assertEqual(result.matched_types, 0)
            self.assertEqual(result.unmatched_types, 1)
            self.assertEqual(result.warnings, ["unmatched-type:cpp:type:AGhost"])
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM symbols WHERE stable_id = 'cpp:type:AGhost'"
                ).fetchone()[0],
                0,
            )

    def test_invalid_child_rolls_back_one_type_atomically(self) -> None:
        record = self._type_record()
        record["functions"] = [
            {
                "stableId": "cpp:function:WrongOwner::SetHealth",
                "name": "SetHealth",
                "ownerStableId": "cpp:type:AHero",
                "returnType": "void",
            }
        ]
        self._write_export(types=[record])

        with open_database(self.database_path) as connection:
            before = connection.execute(
                "SELECT details_json FROM symbols WHERE stable_id = 'cpp:type:AHero'"
            ).fetchone()["details_json"]
            result = index_reflection_export(connection, self.export_path)
            self.assertEqual(len(result.errors), 1)
            self.assertEqual(result.matched_types, 0)
            after = connection.execute(
                "SELECT details_json FROM symbols WHERE stable_id = 'cpp:type:AHero'"
            ).fetchone()["details_json"]
            self.assertEqual(after, before)
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM symbols WHERE owner_symbol_id = 'cpp:type:AHero' AND kind = 'property'"
                ).fetchone()[0],
                0,
            )

    def test_invalid_parameter_identity_rolls_back_one_type_atomically(self) -> None:
        record = self._type_record()
        function = record["functions"][0]
        function["parameters"][0]["stableId"] = "cpp:parameter:Wrong::Value"
        self._write_export(types=[record])

        with open_database(self.database_path) as connection:
            before = connection.execute(
                "SELECT details_json FROM symbols WHERE stable_id = 'cpp:type:AHero'"
            ).fetchone()["details_json"]
            result = index_reflection_export(connection, self.export_path)
            self.assertEqual(len(result.errors), 1)
            self.assertIn("Invalid reflected parameter identity", result.errors[0])
            self.assertEqual(result.matched_types, 0)
            after = connection.execute(
                "SELECT details_json FROM symbols WHERE stable_id = 'cpp:type:AHero'"
            ).fetchone()["details_json"]
            self.assertEqual(after, before)
            self.assertEqual(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM symbols
                    WHERE owner_symbol_id = 'cpp:type:AHero'
                      AND kind IN ('function', 'property')
                    """
                ).fetchone()[0],
                0,
            )

    def test_project_identity_mismatch_is_rejected(self) -> None:
        self._write_export(project_name="OtherProject")
        with open_database(self.database_path) as connection:
            with self.assertRaisesRegex(RuntimeError, "does not match project key"):
                index_reflection_export(connection, self.export_path)

    def test_cli_exposes_reflection_index_command(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            ["index", "reflection", str(self.export_path), "--project-key", "Game"]
        )
        self.assertEqual(args.index_command, "reflection")
        self.assertEqual(args.reflection_export, self.export_path)
        self.assertEqual(args.project_key, "Game")


if __name__ == "__main__":
    unittest.main()
