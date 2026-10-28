"""Narrow, high-confidence C++ function/method tracing for the code profile.

The module recognises only declaration shapes it can prove from masked C++
text: macro-decorated declarations (``UFUNCTION``), namespaced free functions,
in-class declarations, out-of-line definitions, and simple operator forms.
Anything else is reported as unknown; nothing is guessed.

It intentionally does not implement a C++ grammar. Templates are recognised as
a decoration but not instantiated, macros are not expanded, and dynamic
dispatch is never resolved to a concrete target.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


STRING_LITERAL_SENTINEL = "\x01"

CPP_KEYWORDS = frozenset(
    {
        "alignas",
        "alignof",
        "and",
        "asm",
        "auto",
        "bool",
        "break",
        "case",
        "catch",
        "char",
        "class",
        "const",
        "constexpr",
        "consteval",
        "constinit",
        "const_cast",
        "continue",
        "decltype",
        "default",
        "delete",
        "do",
        "double",
        "dynamic_cast",
        "else",
        "enum",
        "explicit",
        "export",
        "extern",
        "false",
        "float",
        "for",
        "friend",
        "goto",
        "if",
        "inline",
        "int",
        "long",
        "mutable",
        "namespace",
        "new",
        "noexcept",
        "not",
        "nullptr",
        "operator",
        "or",
        "private",
        "protected",
        "public",
        "register",
        "reinterpret_cast",
        "return",
        "short",
        "signed",
        "sizeof",
        "static",
        "static_assert",
        "static_cast",
        "struct",
        "switch",
        "template",
        "this",
        "throw",
        "true",
        "try",
        "typedef",
        "typeid",
        "typename",
        "union",
        "unsigned",
        "using",
        "virtual",
        "void",
        "volatile",
        "while",
        "xor",
    }
)

CPP_BUILTIN_TYPES = frozenset(
    {
        "bool",
        "char",
        "char16_t",
        "char32_t",
        "double",
        "float",
        "int",
        "int8",
        "int16",
        "int32",
        "int64",
        "long",
        "short",
        "signed",
        "size_t",
        "uint8",
        "uint16",
        "uint32",
        "uint64",
        "unsigned",
        "void",
        "wchar_t",
    }
)

# Declaration prefixes that carry no symbol identity of their own.
_QUALIFIER_DECORATIONS = frozenset(
    {
        "static",
        "virtual",
        "inline",
        "explicit",
        "constexpr",
        "consteval",
        "constinit",
        "friend",
        "mutable",
        "FORCEINLINE",
        "FORCENOINLINE",
        "UE_NODISCARD",
        "__forceinline",
        "__inline",
    }
)
_KNOWN_DECORATOR_MACROS = frozenset(
    {
        "UFUNCTION",
        "UCLASS",
        "USTRUCT",
        "UENUM",
        "UINTERFACE",
        "UPROPERTY",
        "GENERATED_BODY",
        "GENERATED_UCLASS_BODY",
        "GENERATED_IINTERFACE_BODY",
        "RIGVM_METHOD",
    }
)
_SUFFIX_WORDS = frozenset({"const", "volatile", "override", "final"})

_MACRO_INVOCATION_RE = re.compile(r"^(?P<name>[A-Za-z_]\w*)\s*\(")
_ACCESS_SPECIFIER_RE = re.compile(r"^(?:public|protected|private)\s*:\s*$")
_NON_FUNCTION_START_RE = re.compile(
    r"^(?:class|struct|union|enum|namespace|using|typedef|static_assert|#)\b"
)
_OPERATOR_TAIL_RE = re.compile(
    r"(?P<operator>operator\s*(?:"
    r"\(\)|\[\]|new\s*\[\]|delete\s*\[\]|new|delete|"
    r"[-+*/%<>=!&|^~,]+|"
    r"(?:const\s+)?[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*(?:\s*[*&]+)?"
    r"))\s*$"
)
_OPERATOR_WORD_RE = re.compile(r"\boperator\b")
_CALL_RE = re.compile(
    r"(?:(?P<receiver>[A-Za-z_]\w*)\s*(?P<hop>->|\.)\s*)?"
    r"(?:(?P<scope>[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*)\s*::\s*)?"
    r"(?P<name>[A-Za-z_]\w*)\s*\("
)
_STRUCTURAL_LINE_RE = re.compile(r"^[{};:,]*$")


@dataclass(frozen=True)
class CppCallSiteFact:
    """One syntactically visible call inside a parsed function body."""

    callee: str
    scope: str
    receiver: str
    line: int
    column: int


@dataclass(frozen=True)
class CppFunctionFact:
    """A declared and/or defined C++ function or method."""

    name: str
    qualified_name: str
    owner_qualified_name: str
    owner_kind: str
    signature: str
    parameters: tuple[str, ...]
    signature_key: str
    line: int
    end_line: int
    definition_line: int
    definition_end_line: int
    is_definition: bool
    is_declaration: bool
    is_virtual: bool
    is_static: bool
    is_const: bool
    is_abstract: bool
    is_template: bool
    is_reflected: bool
    is_operator: bool = False
    access: str = ""
    call_sites: tuple[CppCallSiteFact, ...] = ()


@dataclass(frozen=True)
class GatheredText:
    """Joined declaration text with a mapping back to source lines."""

    text: str
    spans: tuple[tuple[int, int, int], ...]
    end_index: int
    terminated: bool

    def locate(self, offset: int) -> tuple[int, int]:
        """Map a character offset to a ``(line_index, column_1based)`` pair."""
        if not self.spans:
            return 0, 1
        chosen = self.spans[0]
        for span in self.spans:
            if span[0] <= offset:
                chosen = span
            else:
                break
        start, line_index, column = chosen
        return line_index, column + max(0, offset - start)


@dataclass(frozen=True)
class _Decorations:
    text: str
    offset: int
    macros: tuple[str, ...]
    is_static: bool
    is_virtual: bool
    is_inline: bool
    is_template: bool
    is_reflected: bool


@dataclass(frozen=True)
class _Suffix:
    kind: str
    body_offset: int
    is_const: bool
    is_abstract: bool
    is_override: bool


def collapse(text: str) -> str:
    """Collapse all whitespace runs to single spaces."""
    return re.sub(r"\s+", " ", text).strip()


def is_macro_like(name: str) -> bool:
    """Return True for ALL_CAPS macro-ish identifiers."""
    return bool(name) and name.isupper() and any(char.isalpha() for char in name)


def _skip_balanced(text: str, open_index: int, open_char: str, close_char: str) -> int:
    depth = 0
    index = open_index
    while index < len(text):
        char = text[index]
        if char == open_char:
            depth += 1
        elif char == close_char:
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return -1


def strip_decorations(text: str) -> _Decorations:
    """Remove leading macro invocations, attributes, templates and qualifiers."""

    def _unterminated() -> _Decorations:
        return _Decorations(
            "",
            len(text),
            tuple(macros),
            is_static,
            is_virtual,
            is_inline,
            is_template,
            is_reflected,
        )

    macros: list[str] = []
    is_static = is_virtual = is_inline = is_template = is_reflected = False
    offset = 0
    rest = text
    while True:
        leading = len(rest) - len(rest.lstrip())
        offset += leading
        rest = rest[leading:]
        if not rest:
            break
        match = _MACRO_INVOCATION_RE.match(rest)
        if match and (
            match.group("name").isupper() or match.group("name") in _KNOWN_DECORATOR_MACROS
        ):
            close = _skip_balanced(rest, match.end() - 1, "(", ")")
            if close < 0:
                return _unterminated()
            name = match.group("name")
            macros.append(name)
            if name == "UFUNCTION":
                is_reflected = True
            offset += close + 1
            rest = rest[close + 1 :]
            continue
        if rest.startswith("[["):
            close = rest.find("]]")
            if close < 0:
                return _unterminated()
            offset += close + 2
            rest = rest[close + 2 :]
            continue
        match = re.match(r"template\s*<", rest)
        if match:
            close = _skip_balanced(rest, match.end() - 1, "<", ">")
            if close < 0:
                return _unterminated()
            is_template = True
            offset += close + 1
            rest = rest[close + 1 :]
            continue
        match = re.match(r"(?P<name>[A-Za-z_]\w*)\b", rest)
        if match and match.group("name") in _QUALIFIER_DECORATIONS:
            name = match.group("name")
            if name == "static":
                is_static = True
            elif name == "virtual":
                is_virtual = True
            elif name in {"inline", "FORCEINLINE", "FORCENOINLINE", "__forceinline", "__inline"}:
                is_inline = True
            offset += match.end()
            rest = rest[match.end() :]
            continue
        break
    return _Decorations(rest, offset, tuple(macros), is_static, is_virtual, is_inline, is_template, is_reflected)


def _declaration_terminator(text: str) -> tuple[int, bool]:
    depth = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif depth == 0 and char in ";{}":
            return index, True
        index += 1
    return -1, False


def gather_declaration(
    lines: list[str],
    start: int,
    *,
    max_lines: int = 80,
    max_chars: int = 4096,
) -> GatheredText:
    """Join a declaration starting at ``start`` until a top-level terminator."""
    parts: list[str] = []
    spans: list[tuple[int, int, int]] = []
    offset = 0
    end_index = start
    terminated = False
    for index in range(start, min(len(lines), start + max_lines)):
        raw = lines[index]
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            break
        if parts and _ACCESS_SPECIFIER_RE.match(stripped):
            break
        end_index = index
        leading = len(raw) - len(raw.lstrip())
        if parts:
            parts.append(" ")
            offset += 1
        spans.append((offset, index, leading + 1))
        parts.append(stripped)
        offset += len(stripped)
        if offset > max_chars:
            break
        _, done = _declaration_terminator("".join(parts))
        if done:
            terminated = True
            break
    return GatheredText("".join(parts), tuple(spans), end_index, terminated)


def _parse_suffix(suffix: str) -> _Suffix | None:
    """Validate the text after the parameter list of a declaration."""
    index = 0
    is_const = False
    is_abstract = False
    is_override = False
    while True:
        while index < len(suffix) and suffix[index].isspace():
            index += 1
        if index >= len(suffix):
            return None
        remaining = suffix[index:]
        if remaining.startswith(";"):
            return _Suffix("declaration", -1, is_const, is_abstract, is_override)
        if remaining.startswith("{"):
            return _Suffix("definition", index, is_const, is_abstract, is_override)
        if remaining.startswith(":"):
            brace = suffix.find("{", index)
            if brace < 0:
                return None
            return _Suffix("definition", brace, is_const, is_abstract, is_override)
        match = re.match(r"\[\s*\[.*?\]\s*\]", remaining)
        if match:
            index += match.end()
            continue
        match = re.match(r"(?:noexcept|throw|try)\b", remaining)
        if match:
            index += match.end()
            probe = suffix[index:]
            leading = len(probe) - len(probe.lstrip())
            if probe.lstrip().startswith("("):
                open_index = index + leading
                close = _skip_balanced(suffix, open_index, "(", ")")
                if close < 0:
                    return None
                index = close + 1
            continue
        match = re.match(r"&{1,2}", remaining)
        if match:
            index += match.end()
            continue
        match = re.match(r"[A-Za-z_]\w*", remaining)
        if match and match.group(0) in _SUFFIX_WORDS:
            word = match.group(0)
            if word == "const":
                is_const = True
            elif word == "override":
                is_override = True
            index += match.end()
            continue
        match = re.match(r"=\s*(?P<kind>default|delete)\b\s*;?", remaining)
        if match:
            return _Suffix(match.group("kind"), -1, is_const, is_abstract, is_override)
        match = re.match(r"=\s*0\b\s*;?", remaining)
        if match:
            return _Suffix("pure-virtual", -1, is_const, True, is_override)
        if remaining.startswith("->"):
            probe = suffix[index + 2 :]
            depth = 0
            for position, char in enumerate(probe):
                if char == "(":
                    depth += 1
                elif char == ")":
                    depth = max(0, depth - 1)
                elif depth == 0 and char == "{":
                    return _Suffix("definition", index + 2 + position, is_const, is_abstract, is_override)
                elif depth == 0 and char == ";":
                    return _Suffix("declaration", -1, is_const, is_abstract, is_override)
            return None
        return None


def split_parameters(text: str) -> tuple[str, ...]:
    """Split a parameter list on top-level commas, dropping default values."""
    parameters: list[str] = []
    current: list[str] = []
    round_depth = 0
    angle_depth = 0
    square_depth = 0
    brace_depth = 0
    for char in text:
        if char == "(":
            round_depth += 1
        elif char == ")":
            round_depth = max(0, round_depth - 1)
        elif char == "<":
            angle_depth += 1
        elif char == ">":
            angle_depth = max(0, angle_depth - 1)
        elif char == "[":
            square_depth += 1
        elif char == "]":
            square_depth = max(0, square_depth - 1)
        elif char == "{":
            brace_depth += 1
        elif char == "}":
            brace_depth = max(0, brace_depth - 1)
        if (
            char == ","
            and round_depth == 0
            and angle_depth == 0
            and square_depth == 0
            and brace_depth == 0
        ):
            parameters.append("".join(current))
            current = []
            continue
        current.append(char)
    parameters.append("".join(current))
    result: list[str] = []
    for parameter in parameters:
        normalized = collapse(parameter)
        if not normalized:
            continue
        if normalized == "void":
            continue
        result.append(normalized)
    return tuple(result)


def _drop_default_value(parameter: str) -> str:
    depth = 0
    angle_depth = 0
    for index, char in enumerate(parameter):
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(0, depth - 1)
        elif char == "<":
            angle_depth += 1
        elif char == ">":
            angle_depth = max(0, angle_depth - 1)
        elif char == "=" and depth == 0 and angle_depth == 0:
            return parameter[:index].strip()
    return parameter.strip()


def _strip_parameter_name(parameter: str) -> str:
    text = re.sub(r"\s*([*&])\s*", r"\1", parameter.strip())
    match = re.search(r"(?P<name>[A-Za-z_]\w*)$", text)
    if match is None or match.start() == 0:
        return text
    head = text[: match.start()].rstrip()
    if not head:
        return text
    tail_word = head.split(" ")[-1]
    if head[-1] in "*&>]":
        return head
    if head in CPP_BUILTIN_TYPES or tail_word in CPP_BUILTIN_TYPES:
        return head
    if tail_word[:1].isupper():
        return head
    return text


def parameter_type_key(parameter: str) -> str:
    """Normalize a parameter to a comparable type-only key."""
    normalized = collapse(_drop_default_value(parameter))
    if not normalized:
        return ""
    return collapse(_strip_parameter_name(normalized))


def _remove_template_arguments(text: str) -> str:
    output: list[str] = []
    depth = 0
    for char in text:
        if char == "<":
            depth += 1
            continue
        if char == ">":
            if depth:
                depth -= 1
            else:
                output.append(char)
            continue
        if depth == 0:
            output.append(char)
    return "".join(output).strip()


def parameter_looks_like_type(parameter: str) -> bool:
    """Return False for value initializers misread as parameter lists."""
    text = collapse(_drop_default_value(parameter))
    if not text or STRING_LITERAL_SENTINEL in text:
        return False
    if text in {"true", "false", "nullptr", "this", "NULL"}:
        return False
    if re.fullmatch(r"[0-9][\w.]*", text):
        return False
    if "=" in text:
        return False
    return "(" not in _remove_template_arguments(text)


def valid_parameter_list(raw: str, parameters: tuple[str, ...]) -> bool:
    """Reject initializer expressions such as ``FVector Location(0, 0, 0)``."""
    if not raw.strip():
        return True
    if not parameters:
        return False
    return all(parameter_looks_like_type(parameter) for parameter in parameters)


def clean_parameter_text(text: str) -> str:
    """Replace masked string-literal sentinels with an empty literal for display."""
    return re.sub(r"\x01+", '""', text)


def signature_key(parameters: tuple[str, ...], *, is_const: bool) -> str:
    keys = [parameter_type_key(parameter) for parameter in parameters]
    return ("const" if is_const else "") + "(" + "|".join(keys) + ")"


def _extract_declaration_name(
    prefix: str,
) -> tuple[str, tuple[str, ...], str, bool] | None:
    text = collapse(prefix)
    if not text:
        return None
    operator_match = _OPERATOR_TAIL_RE.search(text)
    if operator_match and _OPERATOR_WORD_RE.search(operator_match.group("operator")):
        name = collapse(operator_match.group("operator"))
        return_type = text[: operator_match.start()].strip()
        return name, (), return_type, True
    if _OPERATOR_WORD_RE.search(text):
        return None
    match = re.search(
        r"(?:(?P<scope>(?:[A-Za-z_]\w*::)*))(?P<name>~?[A-Za-z_]\w*)$", text
    )
    if match is None:
        return None
    scope_text = match.group("scope").rstrip(":")
    scope_parts = tuple(part for part in scope_text.split("::") if part)
    return match.group("name"), scope_parts, text[: match.start("scope")].strip(), False


def _valid_return_type(return_type: str) -> bool:
    if any(char in return_type for char in "=;{}"):
        return False
    if ":" in return_type.replace("::", ""):
        return False
    if any(word in {"public", "protected", "private"} for word in return_type.split()):
        return False
    if return_type.count("(") != return_type.count(")"):
        return False
    if re.search(r"[+\-/%!|^?]", return_type):
        return False
    return True


def _valid_function_name(name: str, *, is_operator: bool) -> bool:
    if is_operator:
        return bool(name) and name.startswith("operator")
    base = name.lstrip("~")
    if not base or base in CPP_KEYWORDS:
        return False
    if is_macro_like(base):
        return False
    return bool(re.fullmatch(r"[A-Za-z_]\w*", base))


def find_body_end(lines: list[str], line_index: int, column: int) -> tuple[int, bool]:
    """Return the line index of the brace closing a body opened at ``column``."""
    depth = 0
    started = False
    for index in range(line_index, len(lines)):
        line = lines[index]
        start = column if index == line_index else 0
        for position in range(start, len(line)):
            char = line[position]
            if char == "{":
                depth += 1
                started = True
            elif char == "}":
                depth -= 1
                if started and depth == 0:
                    return index, True
    return len(lines) - 1 if lines else line_index, False


def scan_call_sites(
    lines: list[str],
    start_index: int,
    end_index: int,
    *,
    start_column: int = 0,
) -> tuple[CppCallSiteFact, ...]:
    """Collect call-shaped tokens from a masked function body."""
    sites: list[CppCallSiteFact] = []
    seen: set[tuple[int, int]] = set()
    for index in range(start_index, min(end_index + 1, len(lines))):
        line = lines[index]
        if line.strip().startswith("#"):
            continue
        begin = start_column if index == start_index else 0
        for match in _CALL_RE.finditer(line, begin):
            name = match.group("name")
            if name in CPP_KEYWORDS or name == "operator" or is_macro_like(name):
                continue
            receiver = match.group("receiver") or ""
            scope = match.group("scope") or ""
            if receiver and receiver != "this" and receiver in CPP_KEYWORDS:
                continue
            if scope and any(part in CPP_KEYWORDS for part in scope.split("::")):
                continue
            key = (index, match.start("name"))
            if key in seen:
                continue
            seen.add(key)
            sites.append(
                CppCallSiteFact(
                    callee=name,
                    scope=scope,
                    receiver=receiver,
                    line=index + 1,
                    column=match.start("name") + 1,
                )
            )
    return tuple(sites)


def parse_function_declaration(
    lines: list[str],
    start: int,
    *,
    namespace_parts: tuple[str, ...] | None,
    owner_parts: tuple[str, ...] | None,
    owner_kind: str,
    access: str,
) -> tuple[CppFunctionFact | None, int, tuple[str, ...]]:
    """Parse one declaration starting at ``start``; return ``(fact, end, warnings)``."""
    warnings: list[str] = []
    gathered = gather_declaration(lines, start)
    if not gathered.text:
        return None, start, ()
    decorations = strip_decorations(gathered.text)
    body = decorations.text
    absolute = decorations.offset
    leading = len(body) - len(body.lstrip())
    absolute += leading
    body = body.lstrip()
    if not body:
        return None, gathered.end_index, ()
    if _NON_FUNCTION_START_RE.match(body) or not gathered.terminated:
        return None, gathered.end_index, ()

    paren_index = -1
    close_index = -1
    suffix: _Suffix | None = None
    first_paren = -1
    index = 0
    while index < len(body):
        char = body[index]
        if char != "(":
            index += 1
            continue
        if first_paren < 0:
            first_paren = index
        close = _skip_balanced(body, index, "(", ")")
        if close < 0:
            return None, gathered.end_index, ()
        candidate = _parse_suffix(body[close + 1 :])
        if candidate is not None:
            paren_index, close_index, suffix = index, close, candidate
            break
        index = close + 1
    if suffix is None or paren_index < 0:
        # Member variables, initializer expressions, and plain statements are not
        # functions; only a named-looking construct with a parameter list is
        # reported as unsupported.
        looks_named = bool(re.search(r"[A-Za-z0-9_]\s*$", body[: max(first_paren, 0)]))
        if first_paren >= 0 and looks_named and "=" not in body[:first_paren]:
            warnings.append("unsupported-function-declaration")
        return None, gathered.end_index, tuple(warnings)

    extracted = _extract_declaration_name(body[:paren_index])
    if extracted is None:
        looks_named = bool(re.search(r"[A-Za-z0-9_]\s*$", body[:paren_index]))
        if looks_named:
            warnings.append("unsupported-function-name")
        return None, gathered.end_index, tuple(warnings)
    name, scope_parts, return_type, is_operator = extracted

    if not _valid_function_name(name, is_operator=is_operator):
        if not is_operator and not is_macro_like(name.lstrip("~")) and name.lstrip("~") not in CPP_KEYWORDS:
            warnings.append(f"unsupported-function-name:{name}")
        return None, gathered.end_index, tuple(warnings)
    if not is_operator and not _valid_return_type(return_type):
        warnings.append(f"unsupported-return-type:{name}")
        return None, gathered.end_index, tuple(warnings)

    if not is_operator and not return_type and not name.startswith("~"):
        owner_hint = scope_parts[-1] if scope_parts else (owner_parts[-1] if owner_parts else "")
        if not owner_hint or name != owner_hint:
            if "=" not in body[:paren_index]:
                warnings.append(f"missing-return-type:{name}")
            return None, gathered.end_index, tuple(warnings)

    if owner_parts:
        owner = tuple(owner_parts)
        resolved_owner_kind = owner_kind
        qualified_parts = owner + (name,)
    elif scope_parts:
        prefix = tuple(namespace_parts or ())
        scope = tuple(scope_parts)
        if prefix and scope[: len(prefix)] == prefix:
            scope = scope[len(prefix) :]
        owner = scope
        resolved_owner_kind = "qualified"
        qualified_parts = prefix + scope + (name,)
    else:
        owner = ()
        resolved_owner_kind = "namespace" if namespace_parts else "global"
        qualified_parts = tuple(namespace_parts or ()) + (name,)

    parameters = split_parameters(body[paren_index + 1 : close_index])
    if not valid_parameter_list(body[paren_index + 1 : close_index], parameters):
        # Value initializers such as ``FVector Location(0, 0, 0)`` are not
        # functions; they are skipped rather than reported as unknown calls.
        return None, gathered.end_index, ()
    parameters = tuple(clean_parameter_text(parameter) for parameter in parameters)
    key = signature_key(parameters, is_const=suffix.is_const)
    declaration_line, _ = gathered.locate(absolute + paren_index)
    declaration_line += 1

    is_definition = suffix.kind in {"definition", "default", "delete"}
    is_declaration = suffix.kind in {"declaration", "pure-virtual"}
    definition_line = 0
    definition_end_line = 0
    call_sites: tuple[CppCallSiteFact, ...] = ()
    if suffix.kind == "definition" and suffix.body_offset >= 0:
        body_absolute = absolute + close_index + 1 + suffix.body_offset
        body_line_index, body_column = gathered.locate(body_absolute)
        definition_line = declaration_line
        body_end_index, balanced = find_body_end(lines, body_line_index, body_column - 1)
        definition_end_line = body_end_index + 1
        if not balanced:
            warnings.append(f"unbalanced-function-body:{name}")
        call_sites = scan_call_sites(
            lines,
            body_line_index,
            body_end_index,
            start_column=max(0, body_column - 1),
        )
    elif suffix.kind in {"default", "delete"}:
        definition_line = declaration_line
        definition_end_line = declaration_line

    signature = collapse(
        (return_type + " " if return_type else "")
        + name
        + "("
        + ", ".join(parameters)
        + ")"
        + (" const" if suffix.is_const else "")
    )
    fact = CppFunctionFact(
        name=name,
        qualified_name="::".join(qualified_parts),
        owner_qualified_name="::".join(owner),
        owner_kind=resolved_owner_kind,
        signature=signature,
        parameters=parameters,
        signature_key=key,
        line=declaration_line,
        end_line=gathered.end_index + 1,
        definition_line=definition_line,
        definition_end_line=definition_end_line,
        is_definition=is_definition,
        is_declaration=is_declaration,
        is_virtual=decorations.is_virtual,
        is_static=decorations.is_static,
        is_const=suffix.is_const,
        is_abstract=suffix.is_abstract,
        is_template=decorations.is_template,
        is_reflected=decorations.is_reflected,
        is_operator=is_operator,
        access=access,
        call_sites=call_sites,
    )
    return fact, gathered.end_index, tuple(warnings)


def is_access_specifier(line: str) -> bool:
    """Return True for an in-class ``public:``/``protected:``/``private:`` line."""
    return _ACCESS_SPECIFIER_RE.match(line.strip()) is not None


def access_for_specifier(line: str) -> str:
    return line.strip().split(":")[0].strip()


def is_structural_line(line: str) -> bool:
    """Return True for lines that cannot start a declaration."""
    stripped = line.strip()
    return not stripped or _STRUCTURAL_LINE_RE.match(stripped) is not None
