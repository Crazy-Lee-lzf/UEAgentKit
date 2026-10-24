"""Offline R1 node-registry contract tests.

These tests do not need Unreal Engine.  They validate:

* the 200-sample golden fixture against the *existing* Python contract
  implementation and the packaged JSON Schema;
* the compatibility-first hashing facts the native registry must reproduce
  (``actionId`` ignores localized text, ``descriptorDigest`` does not);
* negative branches (localized fields in ``discoveryKey``, unknown top-level
  fields, unknown actionKind);
* an optional UE catalog export produced by the R1 automation run;
* a source guard that keeps the R1 native module free of write APIs.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ue_agent_kit.graph_contracts import descriptors  # noqa: E402
from ue_agent_kit.graph_contracts.canonical import canonical_json  # noqa: E402

GOLDEN_PATH = ROOT / "Plugin" / "UEAgentKit" / "Resources" / "NativeTools" / "Golden" / "node-descriptor-golden.v1.json"
SCHEMA_PATH = ROOT / "src" / "ue_agent_kit" / "graph_contracts" / "schemas" / "node-descriptor.schema.json"
EXPORT_PATH = ROOT / "Build" / "r1-native" / "Evidence" / "catalog-export.json"
NATIVE_SOURCE_DIR = ROOT / "Plugin" / "UEAgentKit" / "Source" / "UEAgentKitEditor" / "Private" / "Native"

ACTION_ID_RE = re.compile(r"^action:[0-9a-f]{32}$")
FINGERPRINT_RE = re.compile(r"^sha256:[0-9a-f]{16}$")
WRITE_API_PATTERNS = (
    r"MarkPackageDirty\s*\(",
    r"FScopedTransaction\b",
    r"SavePackage\s*\(",
    r"SaveAsset\s*\(",
    r"\.Modify\s*\(",
    r"->Modify\s*\(",
    r"SpawnActor\s*<",
    r"CreateProc\s*\(",
    r"Trans\s*->",
    r"IPythonScriptPlugin",
)


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


class GoldenFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.golden = _load_json(GOLDEN_PATH)
        cls.samples = cls.golden["samples"]

    def test_fixture_shape(self) -> None:
        self.assertEqual(self.golden["schemaVersion"], "ueak.native-golden/1.0")
        self.assertEqual(self.golden["sampleCount"], 200)
        self.assertEqual(len(self.samples), 200)
        ids = [sample["sampleId"] for sample in self.samples]
        self.assertEqual(len(ids), len(set(ids)))

    def test_canonical_projection_matches_python(self) -> None:
        for sample in self.samples:
            with self.subTest(sample=sample["sampleId"]):
                projection = descriptors.discovery_key_projection(sample["discoveryKey"])
                self.assertEqual(sample["expectedCanonicalDiscoveryKey"], canonical_json(projection))

    def test_action_id_matches_python(self) -> None:
        for sample in self.samples:
            with self.subTest(sample=sample["sampleId"]):
                expected = descriptors.make_action_id(sample["discoveryKey"])
                self.assertEqual(sample["expectedActionId"], expected)
                self.assertRegex(sample["expectedActionId"], ACTION_ID_RE)

    def test_descriptor_is_contract_valid(self) -> None:
        for sample in self.samples:
            with self.subTest(sample=sample["sampleId"]):
                report = descriptors.validate_node_descriptor(sample["descriptor"])
                self.assertEqual([], report.diagnostics)
                self.assertEqual(sample["descriptor"]["actionId"], sample["expectedActionId"])
                self.assertEqual(
                    sample["descriptor"]["descriptorDigest"],
                    sample["expectedDescriptorDigest"],
                )

    def test_descriptor_matches_packaged_json_schema(self) -> None:
        try:
            import jsonschema  # type: ignore
        except ImportError:  # pragma: no cover - development convenience only
            self.skipTest("jsonschema is not installed")
        schema = _load_json(SCHEMA_PATH)
        for sample in self.samples:
            with self.subTest(sample=sample["sampleId"]):
                jsonschema.validate(sample["descriptor"], schema)

    def test_samples_cover_every_action_kind(self) -> None:
        kinds = {sample["discoveryKey"]["actionKind"] for sample in self.samples}
        self.assertEqual(set(descriptors.ACTION_KINDS), kinds)

    def test_digest_includes_localized_text_but_action_id_does_not(self) -> None:
        document = json.loads(json.dumps(self.samples[0]["descriptor"]))
        action_before = document["actionId"]
        digest_before = document["descriptorDigest"]

        document["title"] = document["title"] + " (renamed)"
        self.assertEqual(action_before, descriptors.make_action_id(document["discoveryKey"]))
        self.assertNotEqual(digest_before, descriptors.node_descriptor_digest(document))

        document["descriptorDigest"] = descriptors.node_descriptor_digest(document)
        report = descriptors.validate_node_descriptor(document)
        self.assertEqual([], report.diagnostics)

    def test_negative_localized_field_inside_discovery_key(self) -> None:
        document = json.loads(json.dumps(self.samples[0]["descriptor"]))
        document["discoveryKey"]["title"] = "Print String"
        document["actionId"] = descriptors.make_action_id(document["discoveryKey"])
        report = descriptors.validate_node_descriptor(document)
        codes = {item["code"] for item in report.diagnostics}
        self.assertIn("graphpatch-context-mismatch", codes)

        import jsonschema  # type: ignore

        schema = _load_json(SCHEMA_PATH)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(document, schema)

    def test_negative_extra_top_level_field(self) -> None:
        document = json.loads(json.dumps(self.samples[0]["descriptor"]))
        document["pluginSetHash"] = "sha256:0000000000000000"
        import jsonschema  # type: ignore

        schema = _load_json(SCHEMA_PATH)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(document, schema)

    def test_negative_unknown_action_kind(self) -> None:
        document = json.loads(json.dumps(self.samples[0]["descriptor"]))
        document["discoveryKey"]["actionKind"] = "unknownKind"
        report = descriptors.validate_node_descriptor(document)
        codes = {item["code"] for item in report.diagnostics}
        self.assertIn("graphpatch-context-mismatch", codes)


class CatalogExportTests(unittest.TestCase):
    """Validates the descriptor document exported by the UE automation run."""

    def test_export_if_present(self) -> None:
        if not EXPORT_PATH.exists():
            self.skipTest(f"no UE catalog export at {EXPORT_PATH.relative_to(ROOT)}")
        payload = _load_json(EXPORT_PATH)
        self.assertEqual(payload["schemaVersion"], "ueak.native.catalog-export/1.0")
        exported = payload["descriptors"]
        self.assertIsInstance(exported, list)
        self.assertGreater(len(exported), 0)

        for index, document in enumerate(exported):
            with self.subTest(index=index, actionId=document.get("actionId")):
                report = descriptors.validate_node_descriptor(document)
                self.assertEqual([], report.diagnostics)
                self.assertEqual(
                    document["descriptorDigest"],
                    descriptors.node_descriptor_digest(document),
                )
                self.assertEqual(
                    document["actionId"],
                    descriptors.make_action_id(document["discoveryKey"]),
                )

        try:
            import jsonschema  # type: ignore
        except ImportError:  # pragma: no cover
            jsonschema = None
        if jsonschema is not None:
            schema = _load_json(SCHEMA_PATH)
            for document in exported:
                jsonschema.validate(document, schema)

        summary = payload.get("summary", {})
        self.assertEqual(summary.get("descriptorCount"), len(exported))

    def test_export_contains_at_least_two_hundred_descriptors(self) -> None:
        if not EXPORT_PATH.exists():
            self.skipTest("no UE catalog export")
        payload = _load_json(EXPORT_PATH)
        self.assertGreaterEqual(len(payload["descriptors"]), 200)


class NativeSourceGuardTests(unittest.TestCase):
    """N0-D05: read-only R1 code must not contain write or process-spawn APIs."""

    def test_native_sources_are_write_free(self) -> None:
        # N0-D05 applies to the read tool implementation.  Automation tests are
        # allowed to *observe* GEditor->Trans (queue length) to prove read-only
        # behaviour, so they are excluded from the write-API scan.
        sources = sorted(path for path in NATIVE_SOURCE_DIR.glob("*") if not path.name.endswith("Tests.cpp"))
        self.assertTrue(sources, "native registry sources are missing")
        offenders = []
        for path in sources:
            if path.suffix not in (".cpp", ".h"):
                continue
            text = path.read_text(encoding="utf-8")
            for line_number, line in enumerate(text.splitlines(), start=1):
                stripped = line.strip()
                if stripped.startswith("//") or stripped.startswith("*"):
                    continue
                for pattern in WRITE_API_PATTERNS:
                    if re.search(pattern, line):
                        offenders.append(f"{path.name}:{line_number}: {pattern} :: {stripped}")
        self.assertEqual([], offenders)


if __name__ == "__main__":
    unittest.main()
