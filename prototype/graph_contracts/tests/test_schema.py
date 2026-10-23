from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ueak_contract import fixtures, layout, typed_graph  # noqa: E402
from ueak_contract.schema import (  # noqa: E402
    MINOR_NEWER_WARNING,
    SchemaError,
    check_schema,
    negotiate_version,
    validate_document,
)

SCHEMAS = (
    "typedgraph.schema.json",
    "node-descriptor.schema.json",
    "operation-descriptor.schema.json",
    "graphpatch.schema.json",
    "layout-plan.schema.json",
    "change-set-idempotency.schema.json",
)


class SchemaSubsetTests(unittest.TestCase):
    def test_all_schemas_meta_validate(self) -> None:
        for name in SCHEMAS:
            with self.subTest(schema=name):
                check_schema(fixtures.load_schema(name), path=name)

    def test_unsupported_keywords_rejected(self) -> None:
        for schema in ({"$ref": "#/x"}, {"type": "string", "format": "date"}, {"if": {}, "then": {}}, {"allOf": []}):
            with self.subTest(schema=schema):
                with self.assertRaises(SchemaError) as context:
                    check_schema(schema)
                self.assertEqual(context.exception.code, "contract-schema-keyword-unsupported")

    def test_subset_validation_semantics(self) -> None:
        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["name", "mode"],
            "properties": {
                "name": {"type": "string", "minLength": 2, "maxLength": 4, "pattern": "^[a-z]+$"},
                "mode": {"oneOf": [{"const": "a"}, {"type": "integer", "minimum": 1, "maximum": 2}]},
                "items": {"type": "array", "items": {"type": "boolean"}},
            },
        }
        self.assertEqual(validate_document({"name": "abc", "mode": "a"}, schema), [])
        self.assertEqual(validate_document({"name": "abc", "mode": 2}, schema), [])
        self.assertTrue(validate_document({"name": "A", "mode": "a"}, schema))
        self.assertTrue(validate_document({"name": "abc", "mode": 3}, schema))
        self.assertTrue(validate_document({"name": "abc", "mode": "a", "extra": 1}, schema))
        self.assertEqual(
            len(validate_document({"name": "abc", "mode": "a", "extra": 1}, schema, strict=False)),
            0,
        )
        self.assertTrue(validate_document({"name": "abc", "mode": True}, schema))

    def test_true_is_not_integer_or_enum_one(self) -> None:
        schema = {"oneOf": [{"const": 1}, {"const": 2}]}
        self.assertTrue(validate_document(True, schema))
        schema = {"type": "integer"}
        self.assertTrue(validate_document(True, schema))

    def test_version_negotiation(self) -> None:
        decision = negotiate_version(
            "ueak.typedgraph/1.4",
            namespace="ueak.typedgraph",
            supported_minor=0,
            unsupported_code="graph-schema-version-unsupported",
        )
        self.assertTrue(decision.accepted)
        self.assertTrue(decision.minor_newer)
        self.assertEqual(decision.diagnostics[0]["code"], MINOR_NEWER_WARNING)

        with self.assertRaises(SchemaError) as context:
            negotiate_version(
                "ueak.typedgraph/2.0",
                namespace="ueak.typedgraph",
                supported_minor=0,
                unsupported_code="graph-schema-version-unsupported",
            )
        self.assertEqual(context.exception.code, "graph-schema-version-unsupported")

    def test_missing_or_wrong_version_has_domain_code(self) -> None:
        graph = fixtures.load_json(fixtures.fixture_path("typedgraph/tg_empty.json"))
        graph.pop("schemaVersion")
        self.assertEqual(typed_graph.validate(graph).primary_code, "graph-schema-version-unsupported")
        graph["schemaVersion"] = "ueak.othergraph/1.0"
        self.assertEqual(typed_graph.validate(graph).primary_code, "graph-schema-version-unsupported")

        patch = fixtures.load_json(fixtures.fixture_path("graphpatch/gp_stale_revision.json"))
        patch.pop("schemaVersion")
        from ueak_contract.graph_patch import plan_patch

        report = plan_patch(patch, fixtures.load_json(fixtures.fixture_path("typedgraph/tg_linear_exec.json")))
        self.assertEqual(report.report.primary_code, "graphpatch-schema-version-unsupported")

    def test_fixtures_validate_against_schemas(self) -> None:
        manifest = fixtures.load_manifest()
        typedgraph_schema = fixtures.load_schema("typedgraph.schema.json")
        patch_schema = fixtures.load_schema("graphpatch.schema.json")
        for entry in manifest["fixtures"]:
            with self.subTest(fixture=entry["fixtureId"]):
                if entry["kind"] == "typedgraph":
                    issues = validate_document(fixtures.load_fixture(entry), typedgraph_schema)
                    self.assertEqual(issues, [], issues[:3])
                elif entry["kind"] == "graphpatch":
                    if entry["expected"]["previewErrorCode"] == "graphpatch-schema-version-unsupported":
                        # Deliberately unsupported major version; semantic validation covers the rejection.
                        continue
                    issues = validate_document(fixtures.load_fixture(entry), patch_schema)
                    self.assertEqual(issues, [], issues[:3])
                elif entry["kind"] == "node-descriptor":
                    issues = validate_document(
                        fixtures.load_fixture(entry), fixtures.load_schema("node-descriptor.schema.json")
                    )
                    self.assertEqual(issues, [], issues[:3])
                elif entry["kind"] == "operation-descriptor":
                    issues = validate_document(
                        fixtures.load_fixture(entry), fixtures.load_schema("operation-descriptor.schema.json")
                    )
                    self.assertEqual(issues, [], issues[:3])

    def test_layout_plans_validate_against_schema(self) -> None:
        manifest = fixtures.load_manifest()
        schema = fixtures.load_schema("layout-plan.schema.json")
        for entry in manifest["fixtures"]:
            if entry["kind"] != "layout":
                continue
            with self.subTest(fixture=entry["fixtureId"]):
                graph = fixtures.load_graph_for(entry)
                request = fixtures.load_fixture(entry)["request"]
                plan = layout.layout_graph(graph, request)
                self.assertEqual(plan.status, "ok", plan.errors)
                issues = validate_document(plan.plan, schema)
                self.assertEqual(issues, [], issues[:3])


if __name__ == "__main__":
    unittest.main()
