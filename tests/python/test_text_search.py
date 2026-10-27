from __future__ import annotations

import sys
import unittest
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.text_search import (  # noqa: E402
    build_search_text,
    expand_cjk,
    expand_query_term,
    has_cjk,
    is_cjk_character,
    project_cjk,
)


class CjkCharacterTests(unittest.TestCase):
    def test_han_kana_and_hangul_are_cjk(self) -> None:
        for character in (
            "\u4e00",  # Han: 一
            "\u62a4",  # Han: 护
            "\u3042",  # Hiragana: あ
            "\u30a2",  # Katakana: ア
            "\u30fc",  # Katakana prolonged sound mark
            "\uac00",  # Hangul syllable: 가
            "\u1100",  # Hangul Jamo
            "\u3131",  # Hangul compatibility Jamo
            "\uf900",  # CJK compatibility ideograph
            "\U00020000",  # CJK Extension B
            "\U0002F800",  # CJK compatibility ideograph supplement
            "\U0001B000",  # Kana Supplement
            "\U00031350",  # CJK Extension H
        ):
            with self.subTest(character=character):
                self.assertTrue(is_cjk_character(character))

    def test_non_cjk_is_rejected(self) -> None:
        for character in (
            "A",
            "7",
            "_",
            " ",
            "\u3002",  # ideographic full stop (CJK punctuation)
            "\uff0c",  # full-width comma
            "\u3099",  # combining voiced sound mark
            "\U0001f600",  # emoji
            "",
            "\u62a4\u7532",  # multi-character input is not a character
        ):
            with self.subTest(character=character):
                self.assertFalse(is_cjk_character(character))
        with self.assertRaises(TypeError):
            is_cjk_character(7)  # type: ignore[arg-type]

    def test_has_cjk_scans_mixed_text(self) -> None:
        self.assertTrue(has_cjk("AK47\u62a4\u7532"))
        self.assertTrue(has_cjk("\u30a6\u30a3\u30c3\u30c8"))
        self.assertTrue(has_cjk("\ubc29\uc5b4\uad6c"))
        self.assertFalse(has_cjk("AK47 inventory slot"))
        self.assertFalse(has_cjk(""))
        with self.assertRaises(TypeError):
            has_cjk(None)  # type: ignore[arg-type]


class ProjectCjkTests(unittest.TestCase):
    def test_nfkc_folds_full_width_and_compatibility_characters(self) -> None:
        self.assertEqual(project_cjk("\uff21\uff2b\uff14\uff17"), "AK47")
        self.assertEqual(project_cjk("\uff76\uff9e"), "\u30ac")  # half-width ガ -> full-width
        self.assertEqual(project_cjk("\uf900"), "\u8c48")  # compatibility ideograph

    def test_whitespace_is_collapsed_and_trimmed(self) -> None:
        self.assertEqual(project_cjk("  \u62a4\u7532\n\t AK47  "), "\u62a4\u7532 AK47")
        self.assertEqual(project_cjk(""), "")
        self.assertEqual(project_cjk("\u3000\u62a4\u3000\u7532\u3000"), "\u62a4 \u7532")

    def test_input_is_not_mutated_and_types_are_enforced(self) -> None:
        original = "\uff21\uff2b"
        self.assertEqual(project_cjk(original), "AK")
        self.assertEqual(original, "\uff21\uff2b")
        with self.assertRaises(TypeError):
            project_cjk(1)  # type: ignore[arg-type]


class ExpandCjkTests(unittest.TestCase):
    def test_han_run_is_character_spaced(self) -> None:
        self.assertEqual(expand_cjk("\u62a4\u7532\u5347\u7ea7\u94fe"), "\u62a4 \u7532 \u5347 \u7ea7 \u94fe")

    def test_mixed_ascii_and_han_boundary(self) -> None:
        self.assertEqual(expand_cjk("AK47\u62a4\u7532"), "AK47 \u62a4 \u7532")
        self.assertEqual(expand_cjk("\u62a4\u7532AK47"), "\u62a4 \u7532 AK47")

    def test_japanese_and_korean_runs_are_character_spaced(self) -> None:
        self.assertEqual(expand_cjk("\u6b66\u5668\u5f37\u5316"), "\u6b66 \u5668 \u5f37 \u5316")
        self.assertEqual(expand_cjk("\ubc29\uc5b4\uad6c \uac15\ud654"), "\ubc29 \uc5b4 \uad6c \uac15 \ud654")

    def test_expansion_is_idempotent(self) -> None:
        once = expand_cjk("AK47\u62a4\u7532\u5347\u7ea7\u94fe")
        self.assertEqual(expand_cjk(once), once)

    def test_combining_marks_stay_attached(self) -> None:
        self.assertEqual(expand_cjk("\u304b\u3099"), "\u304c")
        self.assertEqual(expand_cjk("a\u0301"), "\u00e1")

    def test_full_width_input_matches_ascii_projection(self) -> None:
        self.assertEqual(expand_cjk("\uff21\uff2b\uff14\uff17\u62a4\u7532"), "AK47 \u62a4 \u7532")


class ExpandQueryTermTests(unittest.TestCase):
    def test_ascii_term_keeps_historical_quoted_shape(self) -> None:
        self.assertEqual(expand_query_term("AK47"), '"AK47"')
        self.assertEqual(expand_query_term("GetSlotColor"), '"GetSlotColor"')
        self.assertEqual(expand_query_term("AK47%"), '"AK47%"')

    def test_cjk_term_becomes_character_spaced_phrase(self) -> None:
        self.assertEqual(expand_query_term("\u62a4\u7532"), '"\u62a4 \u7532"')
        self.assertEqual(expand_query_term("\u7532\u62a4"), '"\u7532 \u62a4"')
        self.assertEqual(
            expand_query_term("\u62a4\u7532\u5347\u7ea7\u94fe"),
            '"\u62a4 \u7532 \u5347 \u7ea7 \u94fe"',
        )

    def test_mixed_term_is_split_into_anded_fragments(self) -> None:
        self.assertEqual(
            expand_query_term("AK47\u62a4\u7532"),
            '"AK47" AND "\u62a4 \u7532"',
        )
        self.assertEqual(
            expand_query_term("\u62a4\u7532AK47"),
            '"\u62a4 \u7532" AND "AK47"',
        )

    def test_full_width_query_is_projected_before_expansion(self) -> None:
        self.assertEqual(
            expand_query_term("\uff21\uff2b\uff14\uff17\u62a4\u7532"),
            '"AK47" AND "\u62a4 \u7532"',
        )

    def test_trailing_punctuation_is_dropped(self) -> None:
        self.assertEqual(expand_query_term("\u62a4\u7532\uff0c"), '"\u62a4 \u7532"')
        self.assertEqual(expand_query_term("%"), "")
        self.assertEqual(expand_query_term(""), "")

    def test_embedded_quote_is_doubled_for_fts5(self) -> None:
        self.assertEqual(expand_query_term('a"b'), '"a""b"')


class BuildSearchTextTests(unittest.TestCase):
    def test_all_fields_are_projected_and_separated(self) -> None:
        text = build_search_text(
            subject_key="design.armor",
            title="\u62a4\u7532\u5347\u7ea7\u94fe",
            body="AK47\u62a4\u7532\u5f88\u5f3a",
            source_ref="design/armor.md",
            details_json='{"note":"\u914d\u65b9"}',
        )
        self.assertIn("design.armor", text)
        self.assertIn("\u62a4 \u7532 \u5347 \u7ea7 \u94fe", text)
        self.assertIn("AK47 \u62a4 \u7532", text)
        self.assertIn("\u914d \u65b9", text)
        self.assertIn("ueakfieldboundary", text)
        self.assertEqual(text, text.strip())
        self.assertNotIn("  ", text)

    def test_deterministic_and_empty_tolerant(self) -> None:
        first = build_search_text(subject_key="a", title="", body="b")
        second = build_search_text(subject_key="a", title="", body="b")
        self.assertEqual(first, second)
        self.assertEqual(build_search_text(), "")

    def test_field_boundary_prevents_cross_field_phrase_adjacency(self) -> None:
        text = build_search_text(subject_key="\u7532", title="\u62a4")
        self.assertEqual(text, "\u7532 ueakfieldboundary \u62a4")

    def test_non_string_field_is_rejected(self) -> None:
        with self.assertRaises(TypeError):
            build_search_text(title=1)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main(verbosity=2)
