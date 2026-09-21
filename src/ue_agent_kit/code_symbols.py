from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


HEADER_EXTENSIONS = frozenset({".h", ".hh", ".hpp", ".hxx"})
_INCLUDE_RE = re.compile(r'^\s*#\s*include\s+"([^"\r\n]+)"')
_NAMESPACE_RE = re.compile(
    r"^\s*(?:inline\s+)?namespace\s+([A-Za-z_]\w*(?:::[A-Za-z_]\w*)*)\s*\{"
)
_NAMESPACE_HEAD_RE = re.compile(
    r"^\s*(?:inline\s+)?namespace\s+([A-Za-z_]\w*(?:::[A-Za-z_]\w*)*)\s*$"
)
_ANON_NAMESPACE_RE = re.compile(r"^\s*namespace\s*\{")
_ANON_NAMESPACE_HEAD_RE = re.compile(r"^\s*namespace\s*$")
_RAW_STRING_RE = re.compile(r'(?:u8|u|U|L)?R"([^ ()\\\t\r\n]{0,16})\(')
_CLASS_START_RE = re.compile(r"^\s*(?:class|struct)\b")
_ENUM_START_RE = re.compile(r"^\s*enum(?:\s+(?:class|struct))?\b")
_CLASS_DECL_RE = re.compile(
    r"^(class|struct)\s+"
    r"(?:(?:[A-Za-z_]\w*_API)\s+)?"
    r"([A-Za-z_]\w*)"
    r"(?:\s+final)?"
    r"\s*(?::\s*(.*))?$"
)
_ENUM_DECL_RE = re.compile(
    r"^enum(?:\s+(?:class|struct))?\s+"
    r"([A-Za-z_]\w*)"
    r"(?:\s*:\s*[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*)?$"
)
_BASE_RE = re.compile(r"^(?:::)?[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*$")


@dataclass(frozen=True)
class CodeSymbolFact:
    kind: str
    name: str
    qualified_name: str
    bases: tuple[str, ...] = ()


@dataclass(frozen=True)
class CodeFileFacts:
    asset_path: str
    includes: tuple[str, ...]
    symbols: tuple[CodeSymbolFact, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class CodeSemanticIndexResult:
    symbols: int
    references: int
    warnings: tuple[str, ...] = ()


def code_symbol_stable_id(qualified_name: str) -> str:
    return f"cpp:type:{qualified_name}"


def _mask_cpp_line(line: str, in_block_comment: bool) -> tuple[str, bool]:
    output: list[str] = []
    index = 0
    length = len(line)
    while index < length:
        if in_block_comment:
            end = line.find("*/", index)
            if end < 0:
                output.append(" " * (length - index))
                break
            output.append(" " * (end + 2 - index))
            index = end + 2
            in_block_comment = False
            continue

        if line.startswith("//", index):
            output.append(" " * (length - index))
            break
        if line.startswith("/*", index):
            output.append("  ")
            index += 2
            in_block_comment = True
            continue

        char = line[index]
        if char in {'"', "'"}:
            quote = char
            output.append(" ")
            index += 1
            escaped = False
            while index < length:
                current = line[index]
                output.append(" ")
                index += 1
                if escaped:
                    escaped = False
                    continue
                if current == "\\":
                    escaped = True
                    continue
                if current == quote:
                    break
            continue

        output.append(char)
        index += 1

    return "".join(output), in_block_comment


def _mask_raw_strings(text: str) -> tuple[str, list[str]]:
    warnings: list[str] = []
    chars = list(text)
    offset = 0
    while True:
        match = _RAW_STRING_RE.search(text, offset)
        if match is None:
            break
        delimiter = match.group(1)
        end_token = ")" + delimiter + '"'
        end = text.find(end_token, match.end())
        if end < 0:
            warnings.append("unterminated-raw-string")
            for index in range(match.start(), len(chars)):
                if chars[index] not in {"\r", "\n"}:
                    chars[index] = " "
            break
        end += len(end_token)
        for index in range(match.start(), end):
            if chars[index] not in {"\r", "\n"}:
                chars[index] = " "
        offset = end
    return "".join(chars), warnings


def _mask_cpp_text(text: str) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    masked: list[str] = []
    in_block_comment = False
    for line in text.splitlines():
        cleaned, in_block_comment = _mask_cpp_line(line, in_block_comment)
        masked.append(cleaned)
    if in_block_comment:
        warnings.append("unterminated-block-comment")
    return masked, warnings


def _parse_bases(value: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    bases: list[str] = []
    warnings: list[str] = []
    for raw_base in value.split(","):
        base = raw_base.strip()
        if not base:
            continue
        if any(token in base for token in ("<", ">", "(", ")", "[", "]", "...")):
            warnings.append(f"unsupported-base:{base}")
            continue
        words = [
            word
            for word in base.split()
            if word not in {"public", "private", "protected", "virtual"}
        ]
        normalized = " ".join(words).strip()
        if not _BASE_RE.fullmatch(normalized):
            warnings.append(f"unsupported-base:{base}")
            continue
        bases.append(normalized.removeprefix("::"))
    return tuple(bases), tuple(warnings)


def _gather_declaration(lines: list[str], start: int, max_lines: int = 8) -> str:
    parts: list[str] = []
    for index in range(start, min(len(lines), start + max_lines)):
        part = lines[index].strip()
        if not part:
            continue
        if part.startswith("#"):
            break
        parts.append(part)
        joined = " ".join(parts)
        brace_pos = joined.find("{")
        semicolon_pos = joined.find(";")
        if brace_pos >= 0 or semicolon_pos >= 0:
            return joined
        if len(joined) > 2048:
            break
    return " ".join(parts)


def _namespace_name(frames: list[tuple[int, tuple[str, ...] | None]]) -> tuple[str, ...] | None:
    parts: list[str] = []
    for _, frame_parts in frames:
        if frame_parts is None:
            return None
        parts.extend(frame_parts)
    return tuple(parts)


def extract_code_file_facts(source_path: Path, asset_path: str) -> CodeFileFacts:
    text = source_path.read_text(encoding="utf-8", errors="replace")
    includes = tuple(dict.fromkeys(
        match.group(1).replace("\\", "/")
        for line in text.splitlines()
        if (match := _INCLUDE_RE.match(line))
    ))
    warnings: list[str] = []
    decode_replacement = "\ufffd" in text
    if decode_replacement:
        warnings.append("decode-replacement")

    if source_path.suffix.casefold() not in HEADER_EXTENSIONS:
        return CodeFileFacts(asset_path=asset_path, includes=includes, symbols=(), warnings=tuple(warnings))

    if decode_replacement:
        return CodeFileFacts(asset_path=asset_path, includes=includes, symbols=(), warnings=tuple(warnings))

    raw_masked_text, raw_warnings = _mask_raw_strings(text)
    warnings.extend(raw_warnings)
    if "unterminated-raw-string" in raw_warnings:
        return CodeFileFacts(asset_path=asset_path, includes=includes, symbols=(), warnings=tuple(warnings))

    masked_lines, mask_warnings = _mask_cpp_text(raw_masked_text)
    warnings.extend(mask_warnings)
    if not masked_lines:
        return CodeFileFacts(asset_path=asset_path, includes=includes, symbols=(), warnings=tuple(warnings))

    symbols: list[CodeSymbolFact] = []
    namespace_frames: list[tuple[int, tuple[str, ...] | None]] = []
    brace_depth = 0
    pending_namespace: tuple[tuple[str, ...] | None, int] | None = None

    for line_index, line in enumerate(masked_lines):
        stripped_line = line.strip()
        pending_open: tuple[str, ...] | None | object = ...
        if pending_namespace is not None and stripped_line:
            pending_parts, pending_line = pending_namespace
            if stripped_line == "{":
                pending_open = pending_parts
            else:
                warnings.append(f"{asset_path}:{pending_line}:namespace-brace-not-immediate")
            pending_namespace = None

        namespace_match = _NAMESPACE_RE.match(line)
        namespace_head_match = _NAMESPACE_HEAD_RE.match(line)
        anonymous_match = _ANON_NAMESPACE_RE.match(line)
        anonymous_head_match = _ANON_NAMESPACE_HEAD_RE.match(line)
        namespace_parts = _namespace_name(namespace_frames)
        namespace_depth = namespace_frames[-1][0] if namespace_frames else 0
        at_namespace_scope = brace_depth == namespace_depth and namespace_parts is not None

        if at_namespace_scope and (_CLASS_START_RE.match(line) or _ENUM_START_RE.match(line)):
            declaration = _gather_declaration(masked_lines, line_index)
            brace_pos = declaration.find("{")
            semicolon_pos = declaration.find(";")
            if brace_pos >= 0 and (semicolon_pos < 0 or brace_pos < semicolon_pos):
                header = declaration[:brace_pos].strip()
                class_match = _CLASS_DECL_RE.fullmatch(header)
                enum_match = _ENUM_DECL_RE.fullmatch(header)
                if class_match:
                    kind = class_match.group(1)
                    name = class_match.group(2)
                    bases, base_warnings = _parse_bases(class_match.group(3) or "")
                    warnings.extend(
                        f"{asset_path}:{line_index + 1}:{warning}"
                        for warning in base_warnings
                    )
                    qualified = "::".join((*namespace_parts, name)) if namespace_parts else name
                    symbols.append(
                        CodeSymbolFact(
                            kind=kind,
                            name=name,
                            qualified_name=qualified,
                            bases=bases,
                        )
                    )
                elif enum_match:
                    name = enum_match.group(1)
                    qualified = "::".join((*namespace_parts, name)) if namespace_parts else name
                    symbols.append(
                        CodeSymbolFact(
                            kind="enum",
                            name=name,
                            qualified_name=qualified,
                        )
                    )

        opens = line.count("{")
        closes = line.count("}")
        if pending_open is not ...:
            namespace_frames.append((brace_depth + 1, pending_open))
        elif anonymous_match:
            namespace_frames.append((brace_depth + 1, None))
        elif namespace_match:
            namespace_frames.append(
                (
                    brace_depth + 1,
                    tuple(namespace_match.group(1).split("::")),
                )
            )
        elif at_namespace_scope and namespace_head_match:
            pending_namespace = (
                tuple(namespace_head_match.group(1).split("::")),
                line_index + 1,
            )
        elif at_namespace_scope and anonymous_head_match:
            pending_namespace = (None, line_index + 1)

        brace_depth += opens - closes
        if brace_depth < 0:
            warnings.append(f"{asset_path}:{line_index + 1}:brace-underflow")
            return CodeFileFacts(asset_path=asset_path, includes=includes, symbols=(), warnings=tuple(warnings))

        while namespace_frames and brace_depth < namespace_frames[-1][0]:
            namespace_frames.pop()

    if pending_namespace is not None:
        warnings.append(f"{asset_path}:{pending_namespace[1]}:namespace-missing-brace")
    if brace_depth != 0:
        warnings.append(f"{asset_path}:brace-imbalance:{brace_depth}")
        return CodeFileFacts(asset_path=asset_path, includes=includes, symbols=(), warnings=tuple(warnings))

    return CodeFileFacts(
        asset_path=asset_path,
        includes=includes,
        symbols=tuple(symbols),
        warnings=tuple(warnings),
    )


def _include_resolution_keys(asset_path: str) -> set[str]:
    normalized = asset_path.replace("\\", "/").strip("/")
    parts = normalized.split("/")
    keys = {normalized, parts[-1]}
    if len(parts) >= 3 and parts[0] == "Source":
        module = parts[1]
        remainder = parts[2:]
        if remainder and remainder[0] in {"Public", "Private", "Classes"}:
            remainder = remainder[1:]
        if remainder:
            relative = "/".join(remainder)
            keys.add(relative)
            keys.add(f"{module}/{relative}")
    return {key.casefold() for key in keys if key}


def _resolve_base(
    base: str,
    source_qualified_name: str,
    symbols_by_qualified_name: dict[str, tuple[str, str, str]],
) -> tuple[str, str, str, str]:
    base = base.removeprefix("::")
    candidates: list[str] = []
    if "::" in base:
        candidates.append(base)
    else:
        namespace_parts = source_qualified_name.split("::")[:-1]
        for count in range(len(namespace_parts), -1, -1):
            prefix = namespace_parts[:count]
            candidates.append("::".join((*prefix, base)) if prefix else base)

    for candidate in candidates:
        resolved = symbols_by_qualified_name.get(candidate)
        if resolved is not None:
            stable_id, kind, asset_path = resolved
            return stable_id, kind, asset_path, candidate
    return "", "", "", base


def _merge_existing_details(
    existing_json: str,
    source_details: dict[str, object],
) -> str:
    try:
        existing = json.loads(existing_json) if existing_json else {}
    except json.JSONDecodeError:
        existing = {}
    if not isinstance(existing, dict):
        existing = {}
    existing.update(source_details)
    return json.dumps(
        existing,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def rebuild_code_semantics(
    connection: sqlite3.Connection,
    facts: Iterable[CodeFileFacts],
    *,
    code_profile: str,
    source_asset_class: str,
) -> CodeSemanticIndexResult:
    fact_list = list(facts)
    asset_rows = connection.execute(
        """
        SELECT id, asset_path
        FROM assets
        WHERE profile = ? AND asset_class = ?
        ORDER BY asset_path
        """,
        (code_profile, source_asset_class),
    ).fetchall()
    asset_ids = {str(row["asset_path"]): int(row["id"]) for row in asset_rows}
    scanned_paths = {file_facts.asset_path for file_facts in fact_list}
    scanned_asset_ids = [
        asset_ids[asset_path]
        for asset_path in sorted(scanned_paths)
        if asset_path in asset_ids
    ]

    candidates: dict[str, list[tuple[str, CodeSymbolFact]]] = {}
    warnings: list[str] = []
    for file_facts in fact_list:
        warnings.extend(file_facts.warnings)
        if file_facts.asset_path not in asset_ids:
            continue
        for symbol in file_facts.symbols:
            stable_id = code_symbol_stable_id(symbol.qualified_name)
            candidates.setdefault(stable_id, []).append((file_facts.asset_path, symbol))

    duplicate_ids = {stable_id for stable_id, items in candidates.items() if len(items) > 1}

    existing_unscanned = connection.execute(
        """
        SELECT s.stable_id, s.kind, s.class_path, a.asset_path
        FROM symbols AS s
        JOIN assets AS a ON a.id = s.asset_id
        WHERE a.profile = ?
          AND a.asset_class = ?
        ORDER BY s.stable_id
        """,
        (code_profile, source_asset_class),
    ).fetchall()
    reserved_ids: dict[str, str] = {}
    symbols_by_qualified_name: dict[str, tuple[str, str, str]] = {}
    for row in existing_unscanned:
        asset_path = str(row["asset_path"])
        if asset_path in scanned_paths:
            continue
        stable_id = str(row["stable_id"])
        reserved_ids[stable_id] = asset_path
        qualified_name = str(row["class_path"])
        if stable_id.startswith("cpp:type:") and qualified_name:
            symbols_by_qualified_name[qualified_name] = (
                stable_id,
                str(row["kind"]),
                asset_path,
            )

    duplicate_ids.update(stable_id for stable_id in candidates if stable_id in reserved_ids)
    for stable_id in sorted(duplicate_ids):
        locations = sorted(path for path, _ in candidates.get(stable_id, []))
        if stable_id in reserved_ids:
            locations.append(reserved_ids[stable_id])
        warnings.append(f"ambiguous-duplicate:{stable_id}:{','.join(locations)}")

    valid_candidates = {
        stable_id: items[0]
        for stable_id, items in candidates.items()
        if stable_id not in duplicate_ids
    }
    candidate_asset_ids = {
        stable_id: asset_ids[asset_path]
        for stable_id, (asset_path, _) in valid_candidates.items()
    }
    current_type_targets: dict[str, tuple[str, str]] = {
        stable_id: (symbol.kind, asset_path)
        for stable_id, (asset_path, symbol) in valid_candidates.items()
    }
    for row in existing_unscanned:
        asset_path = str(row["asset_path"])
        if asset_path in scanned_paths:
            continue
        stable_id = str(row["stable_id"])
        if stable_id.startswith("cpp:type:"):
            current_type_targets.setdefault(
                stable_id,
                (str(row["kind"]), asset_path),
            )

    desired_inheritance_ids = {
        f"cpp:inherits:{stable_id}:{base}"
        for stable_id, (_, symbol) in valid_candidates.items()
        for base in symbol.bases
    }

    existing_type_details: dict[str, str] = {}
    existing_inheritance_details: dict[str, str] = {}
    placeholders = ",".join("?" for _ in scanned_asset_ids)
    if scanned_asset_ids:
        for row in connection.execute(
            f"""
            SELECT stable_id, details_json
            FROM symbols
            WHERE asset_id IN ({placeholders})
              AND stable_id LIKE 'cpp:type:%'
              AND kind IN ('class', 'struct', 'enum')
            """,
            scanned_asset_ids,
        ):
            existing_type_details[str(row["stable_id"])] = str(row["details_json"])

        inheritance_rows = connection.execute(
            f"""
            SELECT id, stable_id, source_symbol_id, target_symbol_id,
                   target_kind, target_name, target_asset_path, target_path,
                   details_json
            FROM references_table
            WHERE asset_id IN ({placeholders})
              AND kind = 'inherits'
            """,
            scanned_asset_ids,
        ).fetchall()
        for row in inheritance_rows:
            existing_inheritance_details[str(row["stable_id"])] = str(row["details_json"])

        valid_owner_ids = set(valid_candidates)
        child_rows = connection.execute(
            f"""
            SELECT id, stable_id, owner_symbol_id
            FROM symbols
            WHERE asset_id IN ({placeholders})
              AND kind IN ('function', 'property')
              AND (
                    stable_id LIKE 'cpp:function:%'
                 OR stable_id LIKE 'cpp:property:%'
              )
            """,
            scanned_asset_ids,
        ).fetchall()
        for row in child_rows:
            owner_symbol_id = str(row["owner_symbol_id"])
            if owner_symbol_id not in valid_owner_ids:
                connection.execute("DELETE FROM symbols WHERE id = ?", (int(row["id"]),))
                continue
            new_asset_id = candidate_asset_ids[owner_symbol_id]
            new_asset_path = valid_candidates[owner_symbol_id][0]
            connection.execute(
                """
                UPDATE symbols
                SET asset_id = ?, symbol_asset_path = ?
                WHERE id = ?
                """,
                (new_asset_id, new_asset_path, int(row["id"])),
            )

        implement_rows = connection.execute(
            f"""
            SELECT id, source_symbol_id, target_symbol_id, target_name,
                   target_asset_path, target_path, details_json
            FROM references_table
            WHERE asset_id IN ({placeholders})
              AND kind = 'implements'
            """,
            scanned_asset_ids,
        ).fetchall()
        for row in implement_rows:
            source_symbol_id = str(row["source_symbol_id"])
            try:
                details = json.loads(str(row["details_json"]) or "{}")
            except json.JSONDecodeError:
                details = {}
            if (
                source_symbol_id not in valid_owner_ids
                or not isinstance(details, dict)
                or details.get("evidence") != "ue-reflection"
            ):
                connection.execute(
                    "DELETE FROM references_table WHERE id = ?",
                    (int(row["id"]),),
                )
                continue

            target_candidate = str(details.get("targetStableIdCandidate", ""))
            resolved_target = current_type_targets.get(target_candidate)
            target_symbol_id = target_candidate if resolved_target is not None else ""
            target_kind = resolved_target[0] if resolved_target is not None else "class"
            target_asset_path = resolved_target[1] if resolved_target is not None else ""
            details["resolved"] = resolved_target is not None
            connection.execute(
                """
                UPDATE references_table
                SET asset_id = ?,
                    target_symbol_id = ?,
                    target_kind = ?,
                    target_asset_path = ?,
                    details_json = ?
                WHERE id = ?
                """,
                (
                    candidate_asset_ids[source_symbol_id],
                    target_symbol_id,
                    target_kind,
                    target_asset_path,
                    json.dumps(
                        details,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    int(row["id"]),
                ),
            )

        for row in inheritance_rows:
            row_id = int(row["id"])
            stable_id = str(row["stable_id"])
            source_symbol_id = str(row["source_symbol_id"])
            if source_symbol_id not in valid_owner_ids:
                connection.execute(
                    "DELETE FROM references_table WHERE id = ?",
                    (row_id,),
                )
                continue
            if stable_id in desired_inheritance_ids:
                continue

            try:
                details = json.loads(str(row["details_json"]) or "{}")
            except json.JSONDecodeError:
                details = {}
            reflection = details.get("reflection") if isinstance(details, dict) else None
            if (
                not isinstance(reflection, dict)
                or reflection.get("evidence") != "ue-reflection"
            ):
                connection.execute(
                    "DELETE FROM references_table WHERE id = ?",
                    (row_id,),
                )
                continue

            target_candidate = str(reflection.get("targetStableIdCandidate", ""))
            resolved_target = current_type_targets.get(target_candidate)
            target_symbol_id = target_candidate if resolved_target is not None else ""
            target_kind = resolved_target[0] if resolved_target is not None else ""
            target_asset_path = resolved_target[1] if resolved_target is not None else ""
            reflection["resolved"] = resolved_target is not None
            details["reflection"] = reflection
            connection.execute(
                """
                UPDATE references_table
                SET asset_id = ?,
                    target_symbol_id = ?,
                    target_kind = ?,
                    target_asset_path = ?,
                    details_json = ?
                WHERE id = ?
                """,
                (
                    candidate_asset_ids[source_symbol_id],
                    target_symbol_id,
                    target_kind,
                    target_asset_path,
                    json.dumps(
                        details,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    row_id,
                ),
            )

        connection.execute(
            f"""
            DELETE FROM references_table
            WHERE asset_id IN ({placeholders})
              AND kind = 'include'
            """,
            scanned_asset_ids,
        )
        if desired_inheritance_ids:
            desired_placeholders = ",".join("?" for _ in desired_inheritance_ids)
            connection.execute(
                f"""
                DELETE FROM references_table
                WHERE stable_id IN ({desired_placeholders})
                """,
                sorted(desired_inheritance_ids),
            )
        connection.execute(
            f"""
            DELETE FROM symbols
            WHERE asset_id IN ({placeholders})
              AND stable_id LIKE 'cpp:type:%'
              AND kind IN ('class', 'struct', 'enum')
            """,
            scanned_asset_ids,
        )

    inserted_symbol_facts: list[tuple[str, CodeSymbolFact, str]] = []
    for stable_id, items in sorted(candidates.items()):
        if stable_id in duplicate_ids:
            continue
        asset_path, symbol = items[0]
        asset_id = asset_ids[asset_path]
        source_details = {
            "language": "cpp",
            "qualifiedName": symbol.qualified_name,
            "bases": list(symbol.bases),
        }
        details_json = _merge_existing_details(
            existing_type_details.get(stable_id, ""),
            source_details,
        )
        connection.execute(
            """
            INSERT INTO symbols(
                asset_id,
                stable_id,
                kind,
                name,
                symbol_asset_path,
                class_path,
                details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                asset_id,
                stable_id,
                symbol.kind,
                symbol.name,
                asset_path,
                symbol.qualified_name,
                details_json,
            ),
        )
        symbols_by_qualified_name[symbol.qualified_name] = (stable_id, symbol.kind, asset_path)
        inserted_symbol_facts.append((asset_path, symbol, stable_id))

    include_key_map: dict[str, set[str]] = {}
    for asset_path in asset_ids:
        for key in _include_resolution_keys(asset_path):
            include_key_map.setdefault(key, set()).add(asset_path)

    reference_count = 0
    for file_facts in fact_list:
        asset_id = asset_ids.get(file_facts.asset_path)
        if asset_id is None:
            continue
        for include_literal in file_facts.includes:
            matches = include_key_map.get(include_literal.casefold(), set())
            target_asset_path = next(iter(matches)) if len(matches) == 1 else ""
            stable_id = f"cpp:include:{file_facts.asset_path}:{include_literal}"
            details = {
                "language": "cpp",
                "include": include_literal,
                "resolved": bool(target_asset_path),
            }
            connection.execute(
                """
                INSERT INTO references_table(
                    asset_id,
                    stable_id,
                    kind,
                    target_kind,
                    target_name,
                    target_asset_path,
                    target_path,
                    details_json
                ) VALUES (?, ?, 'include', ?, ?, ?, ?, ?)
                """,
                (
                    asset_id,
                    stable_id,
                    source_asset_class if target_asset_path else "",
                    include_literal.rsplit("/", 1)[-1],
                    target_asset_path,
                    include_literal,
                    json.dumps(details, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                ),
            )
            reference_count += 1

    for asset_path, symbol, source_stable_id in inserted_symbol_facts:
        asset_id = asset_ids[asset_path]
        for base in symbol.bases:
            target_symbol_id, target_kind, target_asset_path, resolved_name = _resolve_base(
                base,
                symbol.qualified_name,
                symbols_by_qualified_name,
            )
            stable_id = f"cpp:inherits:{source_stable_id}:{base}"
            source_details = {
                "language": "cpp",
                "sourceQualifiedName": symbol.qualified_name,
                "baseName": base,
                "resolvedQualifiedName": resolved_name if target_symbol_id else "",
                "resolved": bool(target_symbol_id),
            }
            details_json = _merge_existing_details(
                existing_inheritance_details.get(stable_id, ""),
                source_details,
            )
            connection.execute(
                """
                INSERT INTO references_table(
                    asset_id,
                    stable_id,
                    kind,
                    source_symbol_id,
                    target_symbol_id,
                    target_kind,
                    target_name,
                    target_asset_path,
                    target_path,
                    details_json
                ) VALUES (?, ?, 'inherits', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset_id,
                    stable_id,
                    source_stable_id,
                    target_symbol_id,
                    target_kind,
                    base,
                    target_asset_path,
                    resolved_name,
                    details_json,
                ),
            )
            reference_count += 1

    return CodeSemanticIndexResult(
        symbols=len(inserted_symbol_facts),
        references=reference_count,
        warnings=tuple(warnings),
    )
