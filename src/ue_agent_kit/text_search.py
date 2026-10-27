"""CJK-aware text projection shared by Project Memory index and query paths.

SQLite FTS5's ``unicode61`` tokenizer stores a contiguous run of Han, Kana, or
Hangul characters as a single token. A query for a shorter substring therefore
cannot match a longer run through ``MATCH``: ``护甲`` does not match the token
``护甲升级链``. Project Memory schema v7 solves this by persisting an *expanded*
search projection (``memory_records.search_text``) in which every CJK character
is a separate token: ``护甲升级链`` becomes ``护 甲 升 级 链``. A phrase query
``"护 甲"`` then matches the substring while the reversed ``"甲 护"`` does not.

The same projection must be applied to writes and reads. This module is the
single implementation used by the write path (:func:`build_search_text`), the
FTS5 query builder (:func:`expand_query_term`), and the bounded literal
fallback, so full-width/half-width compatibility variants resolve to the same
tokens and ASCII behavior is unchanged.
"""

from __future__ import annotations

import unicodedata

# Inclusive Unicode ranges that participate in the per-character expansion.
# Han: CJK Unified Ideographs, Ext A, compatibility ideographs, and the
# supplementary-plane extensions. Kana/Hangul runs have the same unicode61
# "one long token" problem, so they are included. CJK punctuation is excluded
# on purpose: it is not a searchable character and stays a token separator.
_CJK_RANGES: tuple[tuple[int, int], ...] = (
    (0x1100, 0x11FF),  # Hangul Jamo
    (0x2E80, 0x2EFF),  # CJK Radicals Supplement
    (0x2F00, 0x2FDF),  # Kangxi Radicals
    (0x3041, 0x3096),  # Hiragana
    (0x309D, 0x309F),  # Hiragana iteration marks
    (0x30A1, 0x30FA),  # Katakana
    (0x30FC, 0x30FF),  # Katakana prolonged sound mark / iteration marks
    (0x3105, 0x312F),  # Bopomofo
    (0x3131, 0x318E),  # Hangul Compatibility Jamo
    (0x31F0, 0x31FF),  # Katakana Phonetic Extensions
    (0x1B000, 0x1B16F),  # Kana Supplement / Extended-A / Small Kana Extension
    (0x3400, 0x4DBF),  # CJK Unified Ideographs Extension A
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
    (0xA960, 0xA97F),  # Hangul Jamo Extended-A
    (0xAC00, 0xD7AF),  # Hangul Syllables
    (0xD7B0, 0xD7FF),  # Hangul Jamo Extended-B
    (0xF900, 0xFAFF),  # CJK Compatibility Ideographs
    (0x20000, 0x2A6DF),  # CJK Unified Ideographs Extension B
    (0x2A700, 0x2B73F),  # CJK Unified Ideographs Extension C
    (0x2B740, 0x2B81F),  # CJK Unified Ideographs Extension D
    (0x2B820, 0x2CEAF),  # CJK Unified Ideographs Extensions E/F
    (0x2CEB0, 0x2EBEF),  # CJK Unified Ideographs Extension F/I
    (0x2EBF0, 0x2EE5F),  # CJK Unified Ideographs Extension I
    (0x2F800, 0x2FA1F),  # CJK Compatibility Ideographs Supplement
    (0x30000, 0x3134F),  # CJK Unified Ideographs Extension G
    (0x31350, 0x323AF),  # CJK Unified Ideographs Extension H
    (0x323B0, 0x3347F),  # CJK Unified Ideographs Extension J
)

# A literal ASCII token inserted between projected source fields. FTS5 phrase
# adjacency ignores punctuation and pure whitespace, so without a real token a
# query such as ``甲护`` could match a title ending in ``甲`` followed by a body
# starting with ``护``. The boundary token keeps fields independent.
_CJK_FIELD_BOUNDARY = "ueakfieldboundary"

# Asset Index schema v2 uses a much smaller projection than Project Memory v7
# (duplicating ASCII on a multi-gigabyte index is the cost v2 exists to avoid).
# It still has to stop FTS5 phrase adjacency from splicing two CJK runs across
# the punctuation/JSON structure that was dropped. U+3164 HANGUL FILLER is a
# single invisible Hangul letter: unicode61 indexes it as its own token, so it
# breaks a phrase, and it is itself a Han/Kana/Hangul character, so
# ``search_text`` stays "only CJK characters separated by spaces" as specified.
_CJK_RUN_BOUNDARY = "\u3164"


def _is_combining_or_format(character: str) -> bool:
    category = unicodedata.category(character)
    return category[0] == "M" or category == "Cf"


def is_cjk_character(character: str) -> bool:
    """Return whether ``character`` is one Han, Kana, or Hangul character.

    Only a single code point can be a CJK character. Combining marks (including
    variation selectors) and CJK punctuation return ``False``; a multi-character
    string also returns ``False`` so callers can use this as a predicate without
    pre-splitting.
    """
    if not isinstance(character, str):
        raise TypeError("character must be a string.")
    if len(character) != 1:
        return False
    code_point = ord(character)
    return any(start <= code_point <= end for start, end in _CJK_RANGES)


def has_cjk(text: str) -> bool:
    """Return whether ``text`` contains at least one CJK character."""
    if not isinstance(text, str):
        raise TypeError("text must be a string.")
    return any(is_cjk_character(character) for character in text)


def project_cjk(text: str) -> str:
    """Project text into the canonical form shared by indexing and querying.

    NFKC normalization folds full-width ASCII (``ＡＫ４７`` -> ``AK47``),
    half-width Katakana, CJK compatibility ideographs, and composed forms onto
    one representation. Runs of Unicode whitespace collapse to one ASCII space
    and leading/trailing whitespace is removed. Case folding is deliberately
    left to FTS5's tokenizer so the ASCII path keeps its historical behavior.
    """
    if not isinstance(text, str):
        raise TypeError("text must be a string.")
    normalized = unicodedata.normalize("NFKC", text)
    return " ".join(normalized.split())


def expand_cjk(text: str) -> str:
    """Insert one space around every CJK character in the projected text.

    ``"护甲升级链"`` becomes ``"护 甲 升 级 链"`` and ``"AK47护甲"`` becomes
    ``"AK47 护 甲"``. Combining marks and format characters stay attached to
    their base character so the tokenizer sees one grapheme. The function is
    idempotent: re-expanding already-expanded text produces the same string.
    """
    projected = project_cjk(text)
    if not projected:
        return ""
    pieces: list[str] = []
    previous_is_cjk = False
    has_output = False
    for character in projected:
        if character.isspace():
            if has_output and pieces[-1] != " ":
                pieces.append(" ")
            previous_is_cjk = False
            continue
        if _is_combining_or_format(character):
            # Attach to the previous grapheme without introducing a boundary.
            pieces.append(character)
            continue
        current_is_cjk = is_cjk_character(character)
        if has_output and (previous_is_cjk or current_is_cjk):
            if pieces[-1] != " ":
                pieces.append(" ")
        pieces.append(character)
        previous_is_cjk = current_is_cjk
        has_output = True
    return "".join(pieces).strip()


def _space_cjk_run(run: str) -> str:
    """Join a CJK run's base characters with spaces, keeping marks attached."""
    pieces: list[str] = []
    for character in run:
        if pieces and not _is_combining_or_format(character):
            pieces.append(" ")
        pieces.append(character)
    return "".join(pieces)


def _quote_fts_phrase(value: str) -> str:
    """Quote one literal token sequence for FTS5 (embedded quotes doubled)."""
    return '"' + value.replace('"', '""') + '"'


def expand_query_term(term: str) -> str:
    """Build an FTS5 expression for one literal query term.

    Pure ASCII terms keep the historical quoted-token form (``AK47`` ->
    ``"AK47"``). A CJK run becomes a character-spaced phrase so it can match a
    substring of a longer source run. A mixed term is split into its ASCII and
    CJK runs joined with ``AND``: ``AK47护甲`` -> ``"AK47" AND "护 甲"``. The
    empty string is returned when the term contains nothing searchable.
    """
    projected = project_cjk(term)
    if not projected:
        return ""
    fragments: list[str] = []
    run: list[str] = []
    run_is_cjk = False

    def flush() -> None:
        nonlocal run, run_is_cjk
        if not run:
            return
        value = "".join(run)
        if run_is_cjk:
            fragments.append(_quote_fts_phrase(_space_cjk_run(value)))
        elif any(character.isalnum() for character in value):
            fragments.append(_quote_fts_phrase(value))
        run = []
        run_is_cjk = False

    for character in projected:
        if character.isspace():
            flush()
            continue
        if run and _is_combining_or_format(character):
            run.append(character)
            continue
        current_is_cjk = is_cjk_character(character)
        if run and current_is_cjk != run_is_cjk:
            flush()
        run.append(character)
        run_is_cjk = current_is_cjk
    flush()
    return " AND ".join(fragments)


def project_cjk_only(text: str) -> str:
    """Return only the expanded Han/Kana/Hangul characters of ``text``.

    The Asset Index schema v2 stores a much smaller projection than Project
    Memory v7: duplicating ASCII on a multi-gigabyte asset index costs hundreds
    of megabytes, so ``symbols.search_text`` and ``nodes.search_text`` contain
    only CJK characters, each separated by one ASCII space. Everything else
    (ASCII tokens, punctuation, JSON structure) is dropped; the original
    columns still carry the full text and are searched as before.

    ``"AK47护甲升级链"`` becomes ``"护 甲 升 级 链"``. Two CJK runs separated by
    punctuation or JSON structure are joined with an invisible Hangul filler
    token (``护甲，升级`` -> ``护 甲 <U+3164> 升 级``) so FTS5 phrase adjacency
    cannot splice a false substring across the dropped separator. Combining
    marks and format characters are dropped because they are not independently
    searchable; NFKC normalization in :func:`project_cjk` has already folded
    compatibility variants onto one code point.
    """
    if not isinstance(text, str):
        raise TypeError("text must be a string.")
    projected = project_cjk(text)
    characters: list[str] = []
    seen_cjk = False
    pending_boundary = False
    for character in projected:
        if _is_combining_or_format(character):
            continue
        if is_cjk_character(character):
            if seen_cjk and pending_boundary:
                characters.append(_CJK_RUN_BOUNDARY)
            characters.append(character)
            seen_cjk = True
            pending_boundary = False
        elif seen_cjk:
            pending_boundary = True
    return " ".join(characters)


def build_cjk_search_text(*fields: str) -> str:
    """Build a persisted CJK-only projection for one Asset Index row.

    Each source field is reduced with :func:`project_cjk_only`. Non-empty
    fields are joined with the same CJK-safe run-boundary token so a phrase
    cannot splice the end of one source field onto the start of the next inside
    the single ``search_text`` column. The boundary is omitted when only one
    field contains CJK, keeping the common row's projection a plain sequence of
    CJK characters and spaces.

    The projection never duplicates ASCII source text; that is the storage
    contract that keeps schema v2 affordable on an existing multi-gigabyte
    index.
    """
    for value in fields:
        if not isinstance(value, str):
            raise TypeError("search text fields must be strings.")
    projected = [value for value in (project_cjk_only(field) for field in fields) if value]
    if not projected:
        return ""
    return f" {_CJK_RUN_BOUNDARY} ".join(projected)


def symbol_search_text(
    *,
    stable_id: str = "",
    name: str = "",
    kind: str = "",
    symbol_asset_path: str = "",
    class_path: str = "",
    details_json: str = "",
) -> str:
    """CJK-only projection for one ``symbols`` row (all FTS-indexed fields)."""
    return build_cjk_search_text(
        stable_id,
        name,
        kind,
        symbol_asset_path,
        class_path,
        details_json,
    )


def node_search_text(
    *,
    guid: str = "",
    title: str = "",
    object_name: str = "",
    node_class: str = "",
    comment: str = "",
    details_json: str = "",
) -> str:
    """CJK-only projection for one ``nodes`` row (all FTS-indexed fields)."""
    return build_cjk_search_text(
        guid,
        title,
        object_name,
        node_class,
        comment,
        details_json,
    )


def build_search_text(
    subject_key: str = "",
    title: str = "",
    body: str = "",
    source_ref: str = "",
    details_json: str = "",
) -> str:
    """Build the persisted, expanded search projection for one Memory record.

    All FTS5-indexed source fields are projected and expanded, then joined with
    an internal field-boundary token. The result is deterministic and contains
    no leading/trailing whitespace. It is stored as the real
    ``memory_records.search_text`` column and indexed by the v7 external-content
    FTS5 table.
    """
    fields = (subject_key, title, body, source_ref, details_json)
    for value in fields:
        if not isinstance(value, str):
            raise TypeError("search text fields must be strings.")
    expanded = [expand_cjk(value) for value in fields]
    return f" {_CJK_FIELD_BOUNDARY} ".join(value for value in expanded if value)


__all__ = [
    "build_cjk_search_text",
    "build_search_text",
    "expand_cjk",
    "expand_query_term",
    "has_cjk",
    "is_cjk_character",
    "node_search_text",
    "project_cjk",
    "project_cjk_only",
    "symbol_search_text",
]
