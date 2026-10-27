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

from ue_agent_kit.code_index import build_code_index  # noqa: E402
from ue_agent_kit.code_symbols import (  # noqa: E402
    code_function_stable_id,
    code_symbol_stable_id,
    extract_code_file_facts,
)
from ue_agent_kit.database import get_schema_version, open_database  # noqa: E402
from ue_agent_kit.schema import CURRENT_SCHEMA_VERSION  # noqa: E402


HERO_HEADER = (
    "#pragma once\n"
    "\n"
    '#include "CoreMinimal.h"\n'
    '#include "Hero.generated.h"\n'
    "\n"
    "UCLASS(Blueprintable)\n"
    "class GAME_API AHero : public AActor\n"
    "{\n"
    "    GENERATED_BODY()\n"
    "\n"
    "public:\n"
    "    UFUNCTION(BlueprintCallable, Category = \"Combat\")\n"
    "    void ApplyDamage(\n"
    "        float Amount,\n"
    "        AActor* Source);\n"
    "\n"
    "    FORCEINLINE int32 GetHealth() const;\n"
    "\n"
    "    void Reset();\n"
    "    void Reset(int32 Mode);\n"
    "\n"
    "protected:\n"
    "    virtual void BeginPlay() override;\n"
    "\n"
    "private:\n"
    "    int32 Stored = 0;\n"
    "};\n"
)

HERO_SOURCE = (
    '#include "Hero.h"\n'
    '#include "Combat/CombatMath.h"\n'
    "\n"
    "namespace Combat\n"
    "{\n"
    "    int32 ScaleDamage(int32 Value)\n"
    "    {\n"
    "        return Value * 2;\n"
    "    }\n"
    "}\n"
    "\n"
    "AHero::AHero(const FObjectInitializer& ObjectInitializer)\n"
    "    : Super(ObjectInitializer)\n"
    "{\n"
    "    Reset();\n"
    "}\n"
    "\n"
    "void AHero::ApplyDamage(float Amount, AActor* Source)\n"
    "{\n"
    "    // ScaleDamage(1) inside a comment must stay invisible.\n"
    "    const int32 Scaled = Combat::ScaleDamage(static_cast<int32>(Amount));\n"
    '    const FString Label = TEXT("ScaleDamage(2)");\n'
    "    if (IsValid(Source))\n"
    "    {\n"
    "        Source->ApplyDamage(1.0f, this);\n"
    "    }\n"
    "    this->BeginPlay();\n"
    "    Super::BeginPlay();\n"
    "}\n"
    "\n"
    "int32 AHero::GetHealth() const\n"
    "{\n"
    "    return Stored;\n"
    "}\n"
    "\n"
    "void AHero::BeginPlay()\n"
    "{\n"
    "    Combat::ScaleDamage(3);\n"
    "}\n"
)


def line_of(text: str, needle: str, *, occurrence: int = 0) -> int:
    """Return the 1-based line number of ``needle`` in ``text``."""
    matches = [
        index
        for index, line in enumerate(text.splitlines(), start=1)
        if needle in line
    ]
    return matches[occurrence]


class CppSymbolTraceTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ueak_p0_symbol_trace_")
        self.root = Path(self.temporary.name)
        self.project_root = self.root / "Project"
        self.public = self.project_root / "Source" / "Game" / "Public"
        self.private = self.project_root / "Source" / "Game" / "Private"
        self.public.mkdir(parents=True)
        self.private.mkdir(parents=True)
        (self.project_root / "Game.uproject").write_text("{}", encoding="utf-8")
        self.database_path = self.root / "index.sqlite3"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_header(self, name: str, text: str) -> Path:
        path = self.public / name
        path.write_text(text, encoding="utf-8")
        return path

    def write_source(self, name: str, text: str) -> Path:
        path = self.private / name
        path.write_text(text, encoding="utf-8")
        return path

    def facts(self, path: Path, asset_path: str = ""):
        resolved = asset_path or f"Source/Game/{path.parent.name}/{path.name}"
        return extract_code_file_facts(path, resolved)

    def build(self, **kwargs):
        with open_database(self.database_path) as connection:
            return build_code_index(connection, self.project_root, self.database_path, **kwargs)

    def symbol_rows(self, pattern: str = "cpp:function:%"):
        with open_database(self.database_path) as connection:
            return connection.execute(
                """
                SELECT s.stable_id, s.name, s.kind, s.class_path, s.owner_symbol_id,
                       s.details_json, a.asset_path
                FROM symbols AS s
                JOIN assets AS a ON a.id = s.asset_id
                WHERE s.stable_id LIKE ?
                ORDER BY s.stable_id
                """,
                (pattern,),
            ).fetchall()

    def call_rows(self):
        with open_database(self.database_path) as connection:
            return connection.execute(
                """
                SELECT stable_id, source_symbol_id, target_symbol_id, target_kind,
                       target_name, target_asset_path, target_path, details_json
                FROM references_table
                WHERE kind = 'calls'
                ORDER BY stable_id
                """
            ).fetchall()


class FunctionFactExtractionTests(CppSymbolTraceTestBase):
    def test_ufunction_multiline_declaration_is_a_function_not_a_type(self) -> None:
        header = self.write_header("Hero.h", HERO_HEADER)
        facts = self.facts(header)

        self.assertEqual([symbol.qualified_name for symbol in facts.symbols], ["AHero"])
        self.assertEqual(facts.symbols[0].kind, "class")
        by_name = {fact.qualified_name: fact for fact in facts.functions}
        self.assertIn("AHero::ApplyDamage", by_name)

        apply_damage = by_name["AHero::ApplyDamage"]
        self.assertEqual(apply_damage.name, "ApplyDamage")
        self.assertEqual(apply_damage.owner_qualified_name, "AHero")
        self.assertEqual(apply_damage.owner_kind, "class")
        self.assertEqual(apply_damage.parameters, ("float Amount", "AActor* Source"))
        self.assertTrue(apply_damage.is_reflected)
        self.assertTrue(apply_damage.is_declaration)
        self.assertFalse(apply_damage.is_definition)
        self.assertEqual(apply_damage.access, "public")
        self.assertEqual(apply_damage.line, line_of(HERO_HEADER, "void ApplyDamage("))
        self.assertEqual(apply_damage.end_line, line_of(HERO_HEADER, "AActor* Source);"))
        self.assertNotIn("UFUNCTION", apply_damage.signature)

    def test_ufunction_macro_alone_never_becomes_a_symbol(self) -> None:
        header = self.write_header("Hero.h", HERO_HEADER)
        facts = self.facts(header)
        names = {fact.name for fact in facts.functions}
        self.assertNotIn("UFUNCTION", names)
        self.assertNotIn("GENERATED_BODY", names)
        self.assertNotIn("UCLASS", names)
        self.assertTrue(facts.warnings == (), facts.warnings)

    def test_inline_definition_keeps_declaration_anchor_and_body_span(self) -> None:
        header = self.write_header("Inline.h", (
            "class FInline\n"
            "{\n"
            "public:\n"
            "    FORCEINLINE int32 Compute(int32 Seed) const\n"
            "    {\n"
            "        return Seed + 1;\n"
            "    }\n"
            "};\n"
        ))
        facts = self.facts(header)
        self.assertEqual(len(facts.functions), 1)
        function = facts.functions[0]
        self.assertEqual(function.qualified_name, "FInline::Compute")
        self.assertTrue(function.is_definition)
        self.assertTrue(function.is_declaration is False)
        text = header.read_text(encoding="utf-8")
        self.assertEqual(function.line, line_of(text, "int32 Compute"))
        self.assertEqual(function.definition_line, function.line)
        self.assertEqual(function.definition_end_line, line_of(text, "return Seed + 1;") + 1)

    def test_comments_and_strings_do_not_produce_symbols(self) -> None:
        header = self.write_header("Noise.h", (
            "class FNoise\n"
            "{\n"
            "public:\n"
            "    // void GhostComment();\n"
            "    /* void GhostBlock(int32 Value); */\n"
            '    static const char* Text;\n'
            "    void Real();\n"
            "};\n"
            'static const char* FNoiseText = "void GhostString();";\n'
        ))
        facts = self.facts(header)
        names = sorted(fact.qualified_name for fact in facts.functions)
        self.assertEqual(names, ["FNoise::Real"])
        self.assertNotIn("GhostString", " ".join(names))

    def test_pure_virtual_and_override_are_declaration_anchors(self) -> None:
        header = self.write_header("Abstract.h", (
            "class FAbstract\n"
            "{\n"
            "public:\n"
            "    virtual void Tick(float DeltaSeconds) = 0;\n"
            "    virtual void BeginPlay() override;\n"
            "};\n"
        ))
        facts = self.facts(header)
        by_name = {fact.name: fact for fact in facts.functions}
        self.assertTrue(by_name["Tick"].is_abstract)
        self.assertTrue(by_name["Tick"].is_virtual)
        self.assertTrue(by_name["Tick"].is_declaration)
        self.assertTrue(by_name["BeginPlay"].is_virtual)
        self.assertTrue(by_name["BeginPlay"].is_declaration)

    def test_operators_and_destructors_get_stable_identity_names(self) -> None:
        header = self.write_header("Operators.h", (
            "class FThing\n"
            "{\n"
            "public:\n"
            "    ~FThing();\n"
            "    bool operator==(const FThing& Other) const;\n"
            "    FThing& operator=(const FThing& Other);\n"
            "    int32 operator()(int32 Value) const;\n"
            "};\n"
        ))
        facts = self.facts(header)
        qualified = sorted(fact.qualified_name for fact in facts.functions)
        self.assertEqual(
            qualified,
            [
                "FThing::operator()",
                "FThing::operator=",
                "FThing::operator==",
                "FThing::~FThing",
            ],
        )

    def test_namespaced_free_function_and_file_local_definition(self) -> None:
        header = self.write_header("Math.h", "namespace Combat\n{\nint32 Clamp(int32 Value);\n}\n")
        source = self.write_source("Math.cpp", (
            '#include "Math.h"\n'
            "namespace Combat\n"
            "{\n"
            "    int32 Clamp(int32 Value)\n"
            "    {\n"
            "        return Value;\n"
            "    }\n"
            "}\n"
            "\n"
            "static void LocalHelper()\n"
            "{\n"
            "    Combat::Clamp(1);\n"
            "}\n"
        ))
        header_facts = self.facts(header)
        self.assertEqual(header_facts.functions[0].qualified_name, "Combat::Clamp")
        self.assertEqual(header_facts.functions[0].owner_kind, "namespace")

        source_facts = self.facts(source)
        by_name = {fact.name: fact for fact in source_facts.functions}
        self.assertIn("Clamp", by_name)
        self.assertEqual(by_name["Clamp"].qualified_name, "Combat::Clamp")
        self.assertIn("LocalHelper", by_name)
        self.assertEqual(by_name["LocalHelper"].owner_kind, "global")
        self.assertEqual(by_name["LocalHelper"].owner_qualified_name, "")

    def test_declarations_in_cpp_files_are_indexed_but_types_stay_header_only(self) -> None:
        header = self.write_header("HeaderOnly.h", "class FDeclared {\n};\n")
        source = self.write_source("Extra.cpp", (
            "class FPrivateOnly\n"
            "{\n"
            "};\n"
            "\n"
            "void FreeFunction(int32 Value)\n"
            "{\n"
            "}\n"
        ))
        source_facts = self.facts(source)
        self.assertEqual(source_facts.symbols, ())
        self.assertEqual([fact.qualified_name for fact in source_facts.functions], ["FreeFunction"])
        header_facts = self.facts(header)
        self.assertEqual([symbol.qualified_name for symbol in header_facts.symbols], ["FDeclared"])

    def test_value_initializers_are_not_indexed_as_functions(self) -> None:
        header = self.write_header("Values.h", (
            "class FValues\n"
            "{\n"
            "public:\n"
            '    FString Label("Text");\n'
            "    FVector Location(0, 0, 0);\n"
            "    void Real();\n"
            "};\n"
        ))
        facts = self.facts(header)
        self.assertEqual(
            sorted(fact.qualified_name for fact in facts.functions),
            ["FValues::Real"],
        )

    def test_string_default_argument_stays_out_of_the_type_key(self) -> None:
        header = self.write_header("Defaults.h", (
            "class FDefaults\n"
            "{\n"
            "public:\n"
            '    void Notify(const FString& Message = TEXT("Hi"));\n'
            "};\n"
        ))
        facts = self.facts(header)
        self.assertEqual(len(facts.functions), 1)
        function = facts.functions[0]
        self.assertEqual(function.qualified_name, "FDefaults::Notify")
        self.assertEqual(function.signature_key, "(const FString&)")
        self.assertTrue(all("\x01" not in parameter for parameter in function.parameters))

    def test_template_and_default_parameter_signature_is_normalized(self) -> None:
        header = self.write_header("Defaults.h", (
            "class FDefaults\n"
            "{\n"
            "public:\n"
            "    template <typename T>\n"
            "    void Apply(const TArray<T>& Items, int32 Count = 0);\n"
            "};\n"
        ))
        facts = self.facts(header)
        self.assertEqual(len(facts.functions), 1)
        function = facts.functions[0]
        self.assertEqual(function.qualified_name, "FDefaults::Apply")
        self.assertTrue(function.is_template)
        self.assertEqual(function.parameters, ("const TArray<T>& Items", "int32 Count = 0"))
        self.assertEqual(function.signature_key, "(const TArray<T>&|int32)")


class FunctionSymbolIndexTests(CppSymbolTraceTestBase):
    def test_header_declaration_and_cpp_definition_become_one_symbol(self) -> None:
        self.write_header("Hero.h", HERO_HEADER)
        self.write_source("Hero.cpp", HERO_SOURCE)
        result = self.build()
        self.assertEqual(result.failed, 0, result.errors)

        rows = {row["stable_id"]: row for row in self.symbol_rows()}
        stable_id = code_function_stable_id("AHero::ApplyDamage")
        self.assertIn(stable_id, rows)
        row = rows[stable_id]
        self.assertEqual(row["kind"], "function")
        self.assertEqual(row["name"], "ApplyDamage")
        self.assertEqual(row["class_path"], "AHero")
        self.assertEqual(row["owner_symbol_id"], code_symbol_stable_id("AHero"))
        self.assertEqual(row["asset_path"], "Source/Game/Public/Hero.h")

        details = json.loads(row["details_json"])
        self.assertEqual(details["evidence"], "cpp-source")
        self.assertEqual(details["language"], "cpp")
        self.assertEqual(details["anchor"]["assetPath"], "Source/Game/Public/Hero.h")
        self.assertEqual(
            details["anchor"]["line"],
            line_of(HERO_HEADER, "void ApplyDamage("),
        )
        anchors = {
            (anchor["assetPath"], anchor["line"]): anchor for anchor in details["anchors"]
        }
        declaration_anchor = anchors[
            ("Source/Game/Public/Hero.h", line_of(HERO_HEADER, "void ApplyDamage("))
        ]
        definition_anchor = anchors[
            ("Source/Game/Private/Hero.cpp", line_of(HERO_SOURCE, "void AHero::ApplyDamage"))
        ]
        self.assertTrue(declaration_anchor["isDeclaration"])
        self.assertTrue(definition_anchor["isDefinition"])
        self.assertEqual(
            definition_anchor["definitionLine"],
            line_of(HERO_SOURCE, "void AHero::ApplyDamage"),
        )
        self.assertTrue(details["isReflected"])
        self.assertNotIn("Source/", stable_id)

    def test_overloads_fail_closed_with_an_explicit_warning(self) -> None:
        self.write_header("Hero.h", HERO_HEADER)
        result = self.build()
        self.assertEqual(result.failed, 0, result.errors)

        ids = {row["stable_id"] for row in self.symbol_rows()}
        self.assertNotIn(code_function_stable_id("AHero::Reset"), ids)
        self.assertTrue(
            any(
                warning.startswith("ambiguous-overload:cpp:function:AHero::Reset:")
                for warning in result.semantic_warnings
            ),
            result.semantic_warnings,
        )
        self.assertEqual(result.functions, len(ids))
        self.assertGreaterEqual(result.functions, 3)

    def test_unknown_declaration_shapes_are_reported_or_skipped_never_fabricated(self) -> None:
        self.write_header("Shapes.h", (
            "DECLARE_DYNAMIC_MULTICAST_DELEGATE_TwoParams(FOnHit, AActor*, HitActor, float, Damage);\n"
            "typedef void (*FOnRawHit)(int32 Value);\n"
            "\n"
            "class FShapes\n"
            "{\n"
            "public:\n"
            "    UPROPERTY(EditAnywhere)\n"
            "    int32 Health = 100;\n"
            "};\n"
        ))
        result = self.build()
        self.assertEqual(result.failed, 0, result.errors)
        self.assertEqual(self.symbol_rows(), [])
        self.assertEqual(result.functions, 0)

    def test_index_is_idempotent_and_anchor_follows_edited_body(self) -> None:
        header = self.write_header("Idem.h", (
            "class FIdem\n"
            "{\n"
            "public:\n"
            "    void Run();\n"
            "};\n"
        ))
        source = self.write_source("Idem.cpp", (
            '#include "Idem.h"\n'
            "void FIdem::Run()\n"
            "{\n"
            "    Step();\n"
            "}\n"
        ))
        first = self.build()
        second = self.build()
        self.assertEqual(
            (first.functions, first.calls, first.unknown_calls),
            (second.functions, second.calls, second.unknown_calls),
        )
        self.assertEqual(len(self.symbol_rows()), 1)
        self.assertEqual(self.call_rows(), [])

        source.write_text(
            '#include "Idem.h"\n'
            "\n"
            "void FIdem::Run()\n"
            "{\n"
            "    Step();\n"
            "}\n",
            encoding="utf-8",
        )
        third = self.build(force=True)
        self.assertEqual(third.failed, 0, third.errors)
        details = json.loads(self.symbol_rows()[0]["details_json"])
        definition_anchor = next(
            anchor for anchor in details["anchors"] if anchor["isDefinition"]
        )
        self.assertEqual(
            definition_anchor["definitionLine"],
            line_of(source.read_text(encoding="utf-8"), "void FIdem::Run()"),
        )

    def test_reflection_owned_function_row_survives_code_reindex(self) -> None:
        self.write_header("Hero.h", HERO_HEADER)
        self.write_source("Hero.cpp", HERO_SOURCE)
        self.build()

        stable_id = code_function_stable_id("AHero::ApplyDamage")
        with open_database(self.database_path) as connection:
            row = connection.execute(
                "SELECT id, details_json FROM symbols WHERE stable_id = ?",
                (stable_id,),
            ).fetchone()
            details = json.loads(row["details_json"])
            details["evidence"] = "ue-reflection"
            connection.execute(
                "UPDATE symbols SET details_json = ? WHERE id = ?",
                (json.dumps(details), int(row["id"])),
            )
            connection.commit()

        rebuilt = self.build(force=True)
        self.assertEqual(rebuilt.failed, 0, rebuilt.errors)
        with open_database(self.database_path) as connection:
            rows = connection.execute(
                "SELECT details_json FROM symbols WHERE stable_id = ?",
                (stable_id,),
            ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0]["details_json"])["evidence"], "ue-reflection")

    def test_two_definition_files_fail_closed_instead_of_merging(self) -> None:
        self.write_source("LocalA.cpp", (
            "static void LocalHelper(int32 Value)\n"
            "{\n"
            "}\n"
        ))
        self.write_source("LocalB.cpp", (
            "static void LocalHelper(int32 Value)\n"
            "{\n"
            "}\n"
        ))
        result = self.build()
        self.assertEqual(result.failed, 0, result.errors)
        ids = {row["stable_id"] for row in self.symbol_rows()}
        self.assertNotIn(code_function_stable_id("LocalHelper"), ids)
        self.assertTrue(
            any(
                warning.startswith("ambiguous-definition:cpp:function:LocalHelper:")
                for warning in result.semantic_warnings
            ),
            result.semantic_warnings,
        )

    def test_partial_source_root_keeps_other_module_function_symbols(self) -> None:
        other = self.project_root / "Source" / "Other" / "Public"
        other.mkdir(parents=True)
        (other / "Other.h").write_text(
            "namespace Other\n{\nvoid Shared();\n}\n", encoding="utf-8"
        )
        self.write_header("Hero.h", HERO_HEADER)
        self.write_source("Hero.cpp", HERO_SOURCE)
        self.build()

        subset = self.build(source_roots=("Source/Game",))
        self.assertEqual(subset.failed, 0, subset.errors)
        ids = {row["stable_id"] for row in self.symbol_rows()}
        self.assertIn(code_function_stable_id("Other::Shared"), ids)
        self.assertIn(code_function_stable_id("AHero::ApplyDamage"), ids)
        self.assertFalse(
            any("symbol-id-owned" in warning for warning in subset.semantic_warnings),
            subset.semantic_warnings,
        )

    def test_schema_version_and_type_identity_are_unchanged(self) -> None:
        self.write_header("Hero.h", HERO_HEADER)
        self.write_source("Hero.cpp", HERO_SOURCE)
        self.build()
        with open_database(self.database_path) as connection:
            self.assertEqual(get_schema_version(connection), CURRENT_SCHEMA_VERSION)
            type_rows = connection.execute(
                "SELECT stable_id FROM symbols WHERE stable_id LIKE 'cpp:type:%'"
            ).fetchall()
        self.assertEqual([row["stable_id"] for row in type_rows], ["cpp:type:AHero"])
        function_ids = [row["stable_id"] for row in self.symbol_rows()]
        self.assertTrue(function_ids)
        self.assertTrue(all(row.startswith("cpp:function:") for row in function_ids))
        self.assertTrue(all("Source/Game" not in row for row in function_ids))


class CallReferenceTests(CppSymbolTraceTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.write_header("Hero.h", HERO_HEADER)
        self.write_source("Hero.cpp", HERO_SOURCE)
        self.result = self.build()
        self.assertEqual(self.result.failed, 0, self.result.errors)

    def test_qualified_call_creates_a_resolved_edge_with_anchors(self) -> None:
        target_id = code_function_stable_id("Combat::ScaleDamage")
        call_line = line_of(HERO_SOURCE, "Combat::ScaleDamage(static_cast")
        matches = [
            row
            for row in self.call_rows()
            if row["target_symbol_id"] == target_id
            and json.loads(row["details_json"])["callSite"]["line"] == call_line
        ]
        self.assertEqual(len(matches), 1)
        row = matches[0]
        self.assertEqual(row["source_symbol_id"], code_function_stable_id("AHero::ApplyDamage"))
        self.assertEqual(row["target_kind"], "function")
        self.assertEqual(row["target_name"], "ScaleDamage")
        self.assertEqual(row["target_asset_path"], "Source/Game/Private/Hero.cpp")
        self.assertTrue(row["target_path"].startswith("Source/Game/Private/Hero.cpp:"))
        details = json.loads(row["details_json"])
        self.assertEqual(details["evidence"], "cpp-source")
        self.assertEqual(details["resolution"], "qualified")
        self.assertTrue(details["resolved"])
        self.assertEqual(details["callSite"]["assetPath"], "Source/Game/Private/Hero.cpp")

    def test_this_call_resolves_and_unknown_receiver_is_reported(self) -> None:
        rows = self.call_rows()
        targets = {row["target_symbol_id"] for row in rows}
        self.assertIn(code_function_stable_id("AHero::BeginPlay"), targets)
        self.assertNotIn(code_function_stable_id("AHero::ApplyDamage"), targets)
        self.assertTrue(
            any(
                "unknown-call-target:Source/Game/Private/Hero.cpp:"
                in warning and ":unknown-receiver" in warning
                for warning in self.result.semantic_warnings
            ),
            self.result.semantic_warnings,
        )

    def test_super_call_is_unknown_and_never_fabricated(self) -> None:
        self.assertTrue(
            any(":unknown-super" in warning for warning in self.result.semantic_warnings),
            self.result.semantic_warnings,
        )
        targets = {row["target_symbol_id"] for row in self.call_rows()}
        self.assertNotIn(code_function_stable_id("AActor::BeginPlay"), targets)

    def test_comment_and_string_calls_do_not_create_edges(self) -> None:
        rows = self.call_rows()
        sites = {
            (json.loads(row["details_json"])["callSite"]["line"], row["target_symbol_id"])
            for row in rows
        }
        self.assertNotIn(
            (line_of(HERO_SOURCE, "// ScaleDamage(1)"), code_function_stable_id("Combat::ScaleDamage")),
            sites,
        )
        self.assertNotIn(
            (line_of(HERO_SOURCE, 'TEXT("ScaleDamage(2)")'), code_function_stable_id("Combat::ScaleDamage")),
            sites,
        )
        scale_sites = sorted(
            json.loads(row["details_json"])["callSite"]["line"]
            for row in rows
            if row["target_symbol_id"] == code_function_stable_id("Combat::ScaleDamage")
        )
        self.assertEqual(
            scale_sites,
            [
                line_of(HERO_SOURCE, "Combat::ScaleDamage(static_cast"),
                line_of(HERO_SOURCE, "Combat::ScaleDamage(3)"),
            ],
        )

    def test_overloaded_callee_is_not_linked(self) -> None:
        rows = self.call_rows()
        self.assertFalse(
            any(row["target_symbol_id"] == code_function_stable_id("AHero::Reset") for row in rows)
        )
        self.assertTrue(
            any(
                warning.startswith("ambiguous-overload:cpp:function:AHero::Reset:")
                for warning in self.result.semantic_warnings
            )
        )

    def test_bare_call_to_unique_free_function_resolves(self) -> None:
        self.write_header("Unique.h", (
            "namespace Util\n"
            "{\n"
            "int32 Compute(int32 Seed);\n"
            "\n"
            "class FCaller\n"
            "{\n"
            "public:\n"
            "    int32 Run();\n"
            "};\n"
            "}\n"
        ))
        self.write_source("Unique.cpp", (
            '#include "Unique.h"\n'
            "namespace Util\n"
            "{\n"
            "    int32 Compute(int32 Seed)\n"
            "    {\n"
            "        return Seed;\n"
            "    }\n"
            "\n"
            "    int32 FCaller::Run()\n"
            "    {\n"
            "        return Compute(7);\n"
            "    }\n"
            "}\n"
        ))
        result = self.build()
        self.assertEqual(result.failed, 0, result.errors)
        rows = [
            row
            for row in self.call_rows()
            if row["source_symbol_id"] == code_function_stable_id("Util::FCaller::Run")
        ]
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0]["target_symbol_id"],
            code_function_stable_id("Util::Compute"),
        )
        self.assertEqual(json.loads(rows[0]["details_json"])["resolution"], "unique-free-function")

    def test_ambiguous_bare_name_is_unknown(self) -> None:
        self.write_header("Ambiguous.h", (
            "namespace A\n"
            "{\n"
            "int32 GetHealth();\n"
            "}\n"
            "namespace B\n"
            "{\n"
            "int32 GetHealth();\n"
            "}\n"
            "\n"
            "void Probe();\n"
        ))
        self.write_source("Ambiguous.cpp", (
            '#include "Ambiguous.h"\n'
            "void Probe()\n"
            "{\n"
            "    GetHealth();\n"
            "}\n"
        ))
        result = self.build()
        self.assertEqual(result.failed, 0, result.errors)
        self.assertGreaterEqual(result.unknown_calls, 1)
        probe_edges = [
            row
            for row in self.call_rows()
            if row["source_symbol_id"] == code_function_stable_id("Probe")
        ]
        self.assertEqual(probe_edges, [])
        self.assertTrue(
            any(
                ":GetHealth:ambiguous-name" in warning
                for warning in result.semantic_warnings
            ),
            result.semantic_warnings,
        )

    def test_unknown_scope_on_a_project_type_is_reported(self) -> None:
        self.write_source("Missing.cpp", (
            '#include "Hero.h"\n'
            "void AHero::Missing()\n"
            "{\n"
            "    AHero::NotDeclared(1);\n"
            "}\n"
        ))
        result = self.build()
        self.assertEqual(result.failed, 0, result.errors)
        self.assertTrue(
            any(
                ":NotDeclared:unknown-scope" in warning
                for warning in result.semantic_warnings
            ),
            result.semantic_warnings,
        )
        self.assertEqual(
            [
                row
                for row in self.call_rows()
                if row["target_name"] == "NotDeclared"
            ],
            [],
        )

    def test_call_edges_are_not_trusted_impact_edges(self) -> None:
        with open_database(self.database_path) as connection:
            kinds = {
                row["kind"]
                for row in connection.execute(
                    "SELECT DISTINCT kind FROM references_table"
                )
            }
        self.assertIn("calls", kinds)
        self.assertIn("include", kinds)
        self.assertIn("inherits", kinds)


if __name__ == "__main__":
    unittest.main()
