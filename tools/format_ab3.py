#!/usr/bin/env python3
"""Style check / fix for .ab3 source (port from Format-Ab3.ps1).

Usage: python tools/format_ab3.py [--check] [--write] [--selftest] [--detail] [--root DIR]
Default --check; --check/--write mutually exclusive.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).parent))
from ab3_source import decode_ab3, encode_ab3, read_ab3_bytes, clear_cache, GENERATED_NAMES

# ---------------------------------------------------------------------------
# Configuration (mirrors PS1 script-level variables).
# ---------------------------------------------------------------------------

UNARY_WORDS = {
    "Then", "To", "Step", "If", "While", "Until", "Wend", "For",
    "AND", "OR", "And", "Or", "Not", "NOT", "Statement", "Function",
    "Return", "SHARED", "Dim", "Select", "Case", "Repeat", "Forever",
}

SPACE_BEFORE_OPEN = {
    "If", "While", "Until", "ElseIf", "For", "Select",
    "AND", "OR", "And", "Or", "Not", "NOT",
}

POINTER_AFTER_WORDS = {
    "If", "Then", "Else", "ElseIf", "While", "Until", "Wend", "For", "To", "Step",
    "AND", "OR", "And", "Or", "Not", "NOT", "Statement", "Function", "Return",
    "SHARED", "Dim", "Select", "Case", "Repeat", "Forever",
}

BOOL_NAMES = ["quitFlag", "levelOk"]
BOOL_CMP_ONLY: list[str] = []  # empty extension hooks
BOOL_CALLS: list[str] = []  # empty extension hooks
BOOL_FUNCTIONS: list[str] = []  # empty extension hooks

GENERATED_AB3 = GENERATED_NAMES


# ---------------------------------------------------------------------------
# Reading / writing .ab3 files.
# ---------------------------------------------------------------------------

def read_ab3_text(path: Path) -> str:
    """Read .ab3: strip UTF-8 BOM, decode latin-1, normalize CRLF/CR to LF."""
    return decode_ab3(read_ab3_bytes(path))


def write_ab3_text(path: Path, text: str) -> None:
    """Write .ab3: latin-1, LF only."""
    path.write_bytes(encode_ab3(text))


# ---------------------------------------------------------------------------
# Comment / banner tests.
# ---------------------------------------------------------------------------

def is_banner_comment(line: str) -> bool:
    """Check if a comment-only line is a banner (=== or --- after prefix strip)."""
    t = line.lstrip()
    if t.startswith(";;"):
        t = t[2:]
    elif t.startswith(";"):
        t = t[1:]
    else:
        return False
    return bool(re.match(r"^[=\-]{3,}", t.strip()))


def is_comment_only(line: str) -> bool:
    """Check if a line is comment-only (starts with ; after whitespace)."""
    t = line.lstrip()
    return t.startswith(";")


def is_const_line(line: str) -> bool:
    """Check if a line is a #CONST assignment: #NAME = value."""
    return bool(re.match(r"^\s*#[A-Za-z_][A-Za-z0-9_]*\s*=", line))


def get_const_value(rhs: str) -> int | None:
    """Parse a const value: $hex or decimal, return int or None."""
    v = rhs
    cut = v.find(";")
    if cut >= 0:
        v = v[:cut]
    v = v.strip()
    m = re.match(r"^\$([0-9A-Fa-f]+)$", v)
    if m:
        return int(m.group(1), 16)
    m = re.match(r"^-?\d+$", v)
    if m:
        return int(v)
    return None


def format_const_block(block: list[str]) -> list[str]:
    """Align names to block-max width, sort by numeric value (stable).

    Non-numeric values sort last, preserving original order.
    """
    parsed: list[dict] = []
    max_name = 0
    for ln in block:
        m = re.match(r"^(\s*)(#[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", ln)
        if not m:
            return block  # unparsable: leave as-is
        indent = m.group(1)
        name = m.group(2)
        rhs = m.group(3)
        trail = ""
        cut = rhs.find(";")
        val = rhs
        if cut >= 0:
            val = rhs[:cut].rstrip()
            trail = ";" + rhs[cut + 1:]
            if not trail.startswith(" ; "):
                body = rhs[cut + 1:]
                if body and body[0] == " ":
                    trail = " ;" + body
                else:
                    trail = " ; " + body
        num = get_const_value(val)
        if len(name) > max_name:
            max_name = len(name)
        parsed.append({
            "indent": indent, "name": name, "val": val.strip(), "trail": trail,
            "num": num, "ord": len(parsed),
        })

    # Sort: numeric first (by value), then non-numeric (by original order),
    # stable on original order within each group.
    def sort_key(p):
        if p["num"] is None:
            return (1, 0, p["ord"])
        return (0, p["num"], p["ord"])

    sorted_parsed = sorted(parsed, key=sort_key)
    return [p["indent"] + p["name"].ljust(max_name) + " = " + p["val"] + p["trail"] for p in sorted_parsed]


# ---------------------------------------------------------------------------
# Convert-CommentTells: fix Unicode em-dash / endash / ellipsis / smart quotes.
# Handles BOTH real Unicode code points AND the Latin-1-misread UTF-8 sequences.
# ---------------------------------------------------------------------------

def convert_comment_tells(s: str) -> str:
    """Convert em-dash, endash, ellipsis, smart quotes, NBSP, and other >127 chars.

    Handles both real Unicode code points and the three-Latin-1-char sequences
    produced when UTF-8 is read as Latin-1 (e.g. E2 80 94 for em-dash).
    """
    if not s:
        return s

    result = []
    i = 0
    chars = s
    n = len(chars)
    while i < n:
        c = ord(chars[i])
        nxt = ord(chars[i + 1]) if i + 1 < n else 0

        # Real Unicode: em-dash / en-dash / ellipsis
        if c == 0x2014 or c == 0x2013:
            result.append("--")
            i += 1
            continue
        if c == 0x2026:
            result.append("...")
            i += 1
            continue

        # Latin-1-misread pair: 0x00E2 0x20AC ... (PS1's "misread-pair form")
        if c == 0xE2 and nxt == 0x20AC:
            n2 = ord(chars[i + 2]) if i + 2 < n else 0
            if n2 == 0x201D or n2 == 0x94:
                result.append("--")
                i += 3
                continue
            if n2 == 0x201C or n2 == 0x93:
                result.append("--")
                i += 3
                continue
            if n2 == 0xA6:
                result.append("...")
                i += 3
                continue

        # Latin-1-misread triple: E2 80 94 / 93 / A6 (UTF-8 bytes read as Latin-1)
        if c == 0xE2 and nxt == 0x80 and i + 2 < n:
            n2 = ord(chars[i + 2])
            if n2 == 0x94 or n2 == 0x93:
                result.append("--")
                i += 3
                continue
            if n2 == 0xA6:
                result.append("...")
                i += 3
                continue

        # Other >127 chars
        if c > 127:
            if c == 0xA0:
                result.append(" ")
            elif c == 0x2018 or c == 0x2019:
                result.append("'")
            elif c == 0x201C or c == 0x201D:
                result.append('"')
            else:
                result.append("?")
            i += 1
            continue

        result.append(chars[i])
        i += 1

    return "".join(result)


# ---------------------------------------------------------------------------
# XTRA range detection.
# ---------------------------------------------------------------------------

def get_xtra_range(lines: list[str]) -> tuple[int, int]:
    """Find ; XTRA ... ; /XTRA range. Returns (-1, -2) if not found."""
    start = -1
    end = -2
    for i, ln in enumerate(lines):
        t = ln.strip()
        if t == "; XTRA" or t == ";XTRA":
            start = i
        if t == "; /XTRA" or t == ";/XTRA":
            end = i
            break
    if start < 0:
        return (-1, -2)
    return (start, end)


# ---------------------------------------------------------------------------
# Tokenizer (port of Get-CodeTokens / Read-Ident from Format-Ab3.ps1).
# ---------------------------------------------------------------------------

class Token:
    __slots__ = ("kind", "text")

    def __init__(self, kind: str, text: str):
        self.kind = kind
        self.text = text

    def __repr__(self):
        return f"Token({self.kind}, {self.text!r})"


def read_ident(s: str, i: int) -> Token:
    """Read an identifier starting at position i.

    Handles: optional * / & / # prefix (pointer prefix when followed by letter),
    trailing $, \\field segments, .suffix segments.
    """
    n = len(s)
    start = i

    # Optional prefix char: * / & / #
    if i < n and s[i] in "*&#":
        if i + 1 < n and s[i + 1].isalpha() or (i + 1 < n and s[i + 1] == "_"):
            i += 1
        else:
            # Not followed by a letter: treat as TightOp (e.g., bare * or #)
            return Token("Other", s[start])

    # Must start with a letter or underscore to be an identifier.
    if i >= n or not (s[i].isalpha() or s[i] == "_"):
        return Token("Other", s[start])

    # Read identifier body.
    while i < n and (s[i].isalnum() or s[i] == "_"):
        i += 1

    # Trailing $ (string variable suffix).
    if i < n and s[i] == "$":
        i += 1

    # \field segments (e.g., *obj\x)
    while i + 1 < n and s[i] == "\\" and (s[i + 1].isalpha() or s[i + 1] == "_"):
        i += 1
        while i < n and (s[i].isalnum() or s[i] == "_"):
            i += 1

    # .suffix segments (e.g., arr.w, fn$)
    while i + 1 < n and s[i] == "." and (s[i + 1].isalpha() or s[i + 1] == "_"):
        i += 1
        while i < n and (s[i].isalnum() or s[i] == "_"):
            i += 1
        # Handle trailing $ on .suffix too (e.g., arr.w$)
        if i < n and s[i] == "$":
            i += 1

    return Token("Word", s[start:i])


def get_code_tokens(s: str) -> list[Token]:
    """Tokenize a code string into tokens.

    Token kinds: Ws, Num ($hex or decimal), Word (identifier), Op (= + - < > <> <= >=),
    TightOp (/ * \\), Comma, Open, Close, Other.
    """
    toks: list[Token] = []
    n = len(s)
    i = 0
    while i < n:
        c = s[i]

        # Whitespace
        if c.isspace():
            j = i
            while j < n and s[j].isspace():
                j += 1
            toks.append(Token("Ws", " "))
            i = j
            continue

        # Hex number: $1234
        if c == "$" and i + 1 < n and s[i + 1] in "0123456789ABCDEFabcdef":
            j = i + 1
            while j < n and s[j] in "0123456789ABCDEFabcdef":
                j += 1
            toks.append(Token("Num", s[i:j]))
            i = j
            continue

        # Decimal number
        if c.isdigit():
            j = i
            while j < n and s[j].isdigit():
                j += 1
            if j < n and s[j] == "." and j + 1 < n and s[j + 1].isdigit():
                j += 1
                while j < n and s[j].isdigit():
                    j += 1
            toks.append(Token("Num", s[i:j]))
            i = j
            continue

        # Pointer/star or tight multiply
        if c == "*":
            # Find previous non-whitespace token
            prev = None
            for p in range(len(toks) - 1, -1, -1):
                if toks[p].kind != "Ws":
                    prev = toks[p]
                    break
            can_ptr = prev is None or prev.kind == "Open" or prev.kind == "Comma" or prev.kind == "Op"
            if not can_ptr and prev.kind == "Word" and prev.text in POINTER_AFTER_WORDS:
                can_ptr = True
            if can_ptr and i + 1 < n and (s[i + 1].isalpha() or s[i + 1] == "_"):
                # Read as identifier (pointer dereference)
                idt = read_ident(s, i)
                toks.append(idt)
                i = start_of_next(s, i, idt.text)
                continue
            toks.append(Token("TightOp", "*"))
            i += 1
            continue

        # & / # prefix or identifier start
        if c in "&#" or c.isalpha() or c == "_":
            idt = read_ident(s, i)
            toks.append(idt)
            i = start_of_next(s, i, idt.text)
            continue

        # Two-char operators
        two = s[i:i + 2]
        if two in ("<>", "<=", ">="):
            toks.append(Token("Op", two))
            i += 2
            continue

        # Single-char operators
        if c in "=+-<>":
            toks.append(Token("Op", c))
            i += 1
            continue

        # Tight operators: / * \ (but * is handled above)
        if c in "/\\":
            toks.append(Token("TightOp", c))
            i += 1
            continue

        # Comma
        if c == ",":
            toks.append(Token("Comma", ","))
            i += 1
            continue

        # Brackets
        if c in "([{":
            toks.append(Token("Open", c))
            i += 1
            continue

        if c in ")]}":
            toks.append(Token("Close", c))
            i += 1
            continue

        # Anything else
        toks.append(Token("Other", c))
        i += 1

    return toks


def start_of_next(s: str, start: int, text: str) -> int:
    """Return the index after the consumed text (handles read_ident returning full token)."""
    # read_ident returns the full identifier text, so we advance past it.
    return start + len(text)


# ---------------------------------------------------------------------------
# Spacing logic (port of Test-IsUnary / Test-NeedSpace / Format-Ab3Code).
# ---------------------------------------------------------------------------

def is_unary(prev: Token | None, op: str) -> bool:
    """Check if + or - is a unary operator."""
    if op != "-" and op != "+":
        return False
    if prev is None:
        return True
    if prev.kind in ("Open", "Comma", "Op", "TightOp"):
        return True
    if prev.kind == "Word" and prev.text in UNARY_WORDS:
        return True
    return False


def need_space(prev: Token | None, curr: Token, curr_unary: bool,
               space_prev_prev: Token | None) -> bool:
    """Decide whether a space is needed between prev and curr.

    Port of Test-NeedSpace from Format-Ab3.ps1, including __spacePrevPrev state.
    """
    if prev is None:
        return False
    if curr.kind in ("Comma", "Close"):
        return False
    if prev.kind == "Open":
        return False
    if prev.kind == "TightOp" or curr.kind == "TightOp":
        return False
    if prev.kind == "Comma":
        return True
    if curr_unary:
        return True
    if prev.kind == "Op":
        # unary + / - already emitted; no space before the operand
        if is_unary(space_prev_prev, prev.text):
            return False
        return True
    if curr.kind == "Op":
        return True
    if prev.kind == "Word" and curr.kind == "Word":
        return True
    if prev.kind == "Word" and curr.kind == "Num":
        return True
    if prev.kind == "Num" and curr.kind == "Word":
        return True
    if prev.kind == "Close" and curr.kind == "Word":
        return True
    if prev.kind == "Close" and curr.kind == "Num":
        return True
    if prev.kind == "Close" and curr.kind == "Open":
        return False
    if prev.kind == "Num" and curr.kind == "Open":
        return False
    if prev.kind == "Word" and curr.kind == "Open":
        return prev.text in SPACE_BEFORE_OPEN
    return False


def format_ab3_code(code: str, after_value: bool = False) -> str:
    """Format a code string with proper spacing.

    Port of Format-Ab3Code. Handles __spacePrevPrev state and AfterValue
    (code immediately following a string literal gets a synthetic Num '0' prev
    and suppresses its first space).
    """
    if code == "":
        return code
    toks = get_code_tokens(code)
    if not toks:
        return code.strip()

    sb: list[str] = []
    prev: Token | None = None
    if after_value:
        prev = Token("Num", "0")
    first = True
    space_prev_prev: Token | None = None

    for k, curr in enumerate(toks):
        if curr.kind == "Ws":
            continue
        unary = False
        if curr.kind == "Op":
            unary = is_unary(prev, curr.text)
        if curr.kind == "TightOp":
            # / and * stay tight. No leftover spaces, no new ones.
            sb.append(curr.text)
        else:
            n = need_space(prev, curr, unary, space_prev_prev)
            if first and after_value:
                n = False
            if n:
                sb.append(" ")
            sb.append(curr.text)
        first = False
        space_prev_prev = prev
        prev = curr

    return "".join(sb)


def test_ends_with_open(s: str) -> bool:
    """Check if string ends with an open bracket (possibly followed by whitespace)."""
    return bool(re.search(r"[\(\{\[]\s*$", s))


def test_starts_with_close_or_comma(s: str) -> bool:
    """Check if string starts with ), ], }, or comma."""
    t = s.lstrip()
    return len(t) > 0 and t[0] in ")]},"


def split_code_comment(line: str) -> tuple[str, str]:
    """Split a line into code part and trailing comment part.

    Returns (code, tail) where tail starts with ';' (or is empty).
    Handles ';' inside string literals.
    """
    in_str = False
    for i in range(len(line)):
        c = line[i]
        if c == '"':
            in_str = not in_str
            continue
        if not in_str and c == ";":
            return (line[:i], line[i:])
    return (line, "")


def add_formatted_code(sb: list[str], code: str) -> None:
    """Format a code fragment and append to sb, handling AfterValue spacing."""
    after_string = bool(sb) and sb[-1].endswith('"')
    fmt = format_ab3_code(code, after_value=after_string)
    if fmt == "":
        return
    if after_string and not test_starts_with_close_or_comma(fmt):
        sb.append(" ")
    sb.append(fmt)


def format_ab3_work(work: str) -> str:
    """Process a line: format code fragments, copy string literals, handle comments.

    Port of Format-Ab3Work from Format-Ab3.ps1.
    """
    if work == "":
        return work

    sb: list[str] = []
    code: list[str] = []
    n = len(work)
    i = 0

    while i < n:
        c = work[i]

        # String literal
        if c == '"':
            # Flush any accumulated code.
            if code:
                add_formatted_code(sb, "".join(code))
                code.clear()

            # Read the full string literal including doubled "" escapes.
            j = i + 1
            while j < n:
                if work[j] == '"':
                    if j + 1 < n and work[j + 1] == '"':
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            lit = work[i:j]  # includes both quotes and doubling

            # Add a space before the string if sb is non-empty and doesn't end with open bracket.
            if sb and not test_ends_with_open("".join(sb)):
                sb.append(" ")
            sb.append(lit)
            i = j
            continue

        # Comment
        if c == ";":
            if code:
                add_formatted_code(sb, "".join(code))
                code.clear()
            body = convert_comment_tells(work[i + 1:])
            if body and body[0] != " ":
                body = " " + body
            if sb:
                sb.append(" ;")
            else:
                sb.append(";")
            sb.append(body)
            return "".join(sb)

        code.append(c)
        i += 1

    # Flush remaining code.
    if code:
        add_formatted_code(sb, "".join(code))
    return "".join(sb)


def format_ab3_logical_line(line: str) -> str:
    """Format one logical line: preserve indent, format code, handle comments.

    Port of Format-Ab3LogicalLine from Format-Ab3.ps1.
    """
    if line == "":
        return line
    if line.strip() == "":
        return line

    indent = ""
    m = re.match(r"^\s*", line)
    if m:
        indent = m.group(0)
    work = line[len(indent):]

    # Comment-only lines: only strip LLM tells (em-dash etc).
    if work.startswith(";"):
        return indent + convert_comment_tells(work)

    return indent + format_ab3_work(work)


# ---------------------------------------------------------------------------
# Comment prefix setting (port of Set-CommentPrefixes).
# ---------------------------------------------------------------------------

def set_comment_prefixes(lines: list[str], xtra_start: int, xtra_end: int) -> list[str]:
    """Set ;; for comment-only runs of 2+ lines or banners; ; for single lines.

    Lines in the XTRA range are left untouched.
    """
    n = len(lines)
    is_c = [False] * n
    for i in range(n):
        if xtra_start <= i <= xtra_end:
            is_c[i] = False
            continue
        is_c[i] = is_comment_only(lines[i])

    i = 0
    result = list(lines)
    while i < n:
        if not is_c[i]:
            i += 1
            continue
        j = i
        while j < n and is_c[j]:
            j += 1
        run = j - i
        banner = False
        for k in range(i, j):
            if is_banner_comment(lines[k]):
                banner = True
                break
        block = run >= 2 or banner
        for k in range(i, j):
            ln = result[k]
            ind_m = re.match(r"^\s*", ln)
            indent = ind_m.group(0) if ind_m else ""
            rest = ln[len(indent):]
            if rest.startswith(";;"):
                rest = rest[2:]
            elif rest.startswith(";"):
                rest = rest[1:]
            rest = convert_comment_tells(rest)
            prefix = ";;" if block else ";"
            result[k] = indent + prefix + rest
        i = j

    return result


# ---------------------------------------------------------------------------
# Const block setting (port of Set-ConstBlocks).
# ---------------------------------------------------------------------------

def set_const_blocks(lines: list[str], xtra_start: int, xtra_end: int) -> list[str]:
    """Process consecutive #NAME = value lines: align, sort by numeric value."""
    n = len(lines)
    i = 0
    out: list[str] = []
    while i < n:
        if xtra_start <= i <= xtra_end:
            out.append(lines[i])
            i += 1
            continue
        if is_const_line(lines[i]):
            block: list[str] = []
            while i < n and is_const_line(lines[i]):
                if xtra_start <= i <= xtra_end:
                    break
                block.append(lines[i])
                i += 1
            out.extend(format_const_block(block))
            continue
        out.append(lines[i])
        i += 1
    return out


# ---------------------------------------------------------------------------
# Bool literal setting (port of Set-BoolLiterals + Apply-Ab3BoolCode).
# ---------------------------------------------------------------------------

def get_bool_name_alt(names: list[str]) -> str:
    """Build an alternation regex for bool names, sorted by length (desc)."""
    esc = [re.escape(name) for name in sorted(names, key=len, reverse=True)]
    return "|".join(esc)


def apply_ab3_bool_code(code: str, fn: str) -> str:
    """Rewrite True/False for boolean names in a code string."""
    if not code:
        return code

    if BOOL_NAMES:
        name_alt = get_bool_name_alt(BOOL_NAMES)
        code = re.sub(
            r"(?<![A-Za-z0-9_])(?P<name>" + name_alt + r")(?P<suf>(?:\.w)?(?:\([^)]*\))?)\s*(?P<op>=|<>)\s*(?P<val>0(?![0-9.])|-1(?![0-9]))",
            lambda m: m.group("name") + m.group("suf") + " " + m.group("op") + " " +
                      ("True" if m.group("val") == "-1" else "False"),
            code,
        )

    if BOOL_CMP_ONLY:
        cmp_alt = get_bool_name_alt(BOOL_CMP_ONLY)
        code = re.sub(
            r"(?<![A-Za-z0-9_])(?P<name>" + cmp_alt + r")(?P<suf>(?:\.w)?(?:\([^)]*\))?)\s*(?P<op=>=|<>)0(?![0-9.])",
            lambda m: m.group("name") + m.group("suf") + " " + m.group("op") + " False",
            code,
        )

    if BOOL_CALLS:
        call_alt = get_bool_name_alt(BOOL_CALLS)
        code = re.sub(
            r"(?<![A-Za-z0-9_])(?P<name>" + call_alt + r")(?P<args>\{[^}]*\}|\([^)]*\))\s*=\s*0(?![0-9.])",
            lambda m: m.group("name") + m.group("args") + " = False",
            code,
        )

    # Function-scoped Return -1/0 -> True/False (currently no-op: BOOL_FUNCTIONS is empty)
    if fn in BOOL_FUNCTIONS:
        code = re.sub(r"Return - 1\b", "Return True", code)
        code = re.sub(r"Return -1\b", "Return True", code)
        code = re.sub(r"Return 0\b", "Return False", code)

    return code


def set_bool_literals(lines: list[str], xtra_start: int, xtra_end: int) -> list[str]:
    """Per-line bool literal rewriting.

    Tracks the current Function name for the Return -1/0 -> True/False hook.
    """
    fn = ""
    out: list[str] = []
    for i, ln in enumerate(lines):
        if xtra_start <= i <= xtra_end:
            out.append(ln)
            continue
        code, tail = split_code_comment(ln)
        t = code.strip()
        if re.match(r"^Function(?:\.[A-Za-z$]+)?\s+(\w+)\s*\{", t):
            fn = re.match(r"^Function(?:\.[A-Za-z$]+)?\s+(\w+)\s*\{", t).group(1)
        elif t == "End Function":
            fn = ""
        elif re.match(r"^Statement\b", t):
            fn = ""
        new_code = apply_ab3_bool_code(code, fn)
        out.append(new_code + tail)
    return out


# ---------------------------------------------------------------------------
# File ending repair (port of Repair-Ab3FileEnding + helpers).
# ---------------------------------------------------------------------------

def get_ab3_section_title(lines: list[str]) -> str | None:
    """Find the title after a banner comment."""
    for i in range(len(lines) - 1):
        if not is_banner_comment(lines[i]):
            continue
        t = lines[i + 1].strip()
        if t.startswith(";;"):
            t = t[2:].strip()
        elif t.startswith(";"):
            t = t[1:].strip()
        else:
            continue
        if t != "":
            return t
    return None


def get_ab3_fallback_title(leaf_name: str, lines: list[str] | None = None) -> str:
    """Build a fallback title from the filename stem or last function name.

    If leaf_name has a stem after removing 'sparkydave.' parts, use that.
    Otherwise, try to extract a title from the last Function/Statement name
    in the lines (CamelCase -> 'CAMEL CASE').
    """
    # Try filename first.
    stem = Path(leaf_name).stem if leaf_name else ""
    parts = [p for p in stem.split(".") if p != "sparkydave" and p != ""]
    if parts:
        return (" ".join(parts)).upper() + " (BLITZ MODE)"

    # No useful filename: try to derive from last Function/Statement name.
    if lines:
        for ln in reversed(lines):
            t = ln.strip()
            # Match: Function[.type] Name{ or Statement Name{
            m = re.match(r"^(?:Function(?:\.[A-Za-z$]+)?|Statement)\s+(\w+)\s*\{", t, re.IGNORECASE)
            if m:
                name = m.group(1)
                # Convert CamelCase to space-separated words.
                words = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\d|\b)", name)
                if not words:
                    words = [name]
                return (" ".join(words)).upper() + " (BLITZ MODE)"

    # Truly nothing to go on.
    return ""


def get_ab3_closing_comment(lines: list[str], leaf_name: str) -> str:
    """Build the ;; END OF <TITLE> comment.

    Title comes from the banner comment title, or from the fallback (filename
    or function name). Always ensures '(BLITZ MODE)' suffix.
    """
    title = get_ab3_section_title(lines)
    if title is None:
        title = get_ab3_fallback_title(leaf_name, lines)
    if "(BLITZ MODE)" not in title:
        title = title + " (BLITZ MODE)"
    return f";; END OF {title}"


def repair_ab3_file_ending(lines: list[str], leaf_name: str) -> list[str]:
    """Ensure every include ends with: blank line, closing comment, trailing newline.

    Port of Repair-Ab3FileEnding from Format-Ab3.ps1.
    """
    work = list(lines)

    # Drop trailing empty entry from split (represents final newline).
    if work and work[-1] == "":
        work.pop()
    # Drop trailing blank lines.
    while work and work[-1].strip() == "":
        work.pop()
    if not work:
        return [f";; END OF {leaf_name}", ""]

    if is_comment_only(work[-1]):
        # Keep with ;; prefix.
        existing = work[-1]
        ind_m = re.match(r"^\s*", existing)
        indent = ind_m.group(0) if ind_m else ""
        rest = existing[len(indent):]
        if rest.startswith(";;"):
            rest = rest[2:]
        elif rest.startswith(";"):
            rest = rest[1:]
        closing = indent + ";;" + rest
        work.pop()
        while work and work[-1].strip() == "":
            work.pop()
    else:
        closing = get_ab3_closing_comment(lines, leaf_name)

    work.append("")
    work.append(closing)
    work.append("")
    return work


# ---------------------------------------------------------------------------
# File formatting (port of Format-Ab3FileText).
# ---------------------------------------------------------------------------

def format_ab3_file_text(text: str, is_main: bool, leaf_name: str = "") -> str:
    """Format a full .ab3 file text.

    Port of Format-Ab3FileText from Format-Ab3.ps1.
    """
    ended = text.endswith("\n")
    # Normalize line endings.
    raw = text.replace("\r\n", "\n").replace("\r", "\n")

    # Split into lines.
    lines = raw.split("\n")

    # XTRA range (main file only).
    if is_main:
        x_start, x_end = get_xtra_range(lines)
    else:
        x_start, x_end = -1, -2

    # Format each line (skip XTRA range).
    formatted: list[str] = []
    for i, ln in enumerate(lines):
        if x_start <= i <= x_end:
            formatted.append(ln)
        else:
            formatted.append(format_ab3_logical_line(ln))

    # Apply comment prefixes.
    formatted = set_comment_prefixes(formatted, x_start, x_end)
    # Apply const block formatting.
    formatted = set_const_blocks(formatted, x_start, x_end)
    # Apply bool literal rewriting.
    formatted = set_bool_literals(formatted, x_start, x_end)

    # Repair file ending (includes only) -- always repair for non-main files.
    if not is_main:
        formatted = repair_ab3_file_ending(formatted, leaf_name or "")

    out = "\n".join(formatted)
    # Ensure output ends with newline iff input did.
    if ended and not out.endswith("\n"):
        out += "\n"
    return out


# ---------------------------------------------------------------------------
# File discovery and style checking.
# ---------------------------------------------------------------------------

def get_live_ab3_files(amega_root: Path) -> list[Path]:
    """Get live .ab3 files: SparkyDave.ab3 + includes/*.ab3 (sorted, minus generated)."""
    files: list[Path] = []
    main = amega_root / "SparkyDave.ab3"
    if main.exists():
        if main.is_symlink() or not main.is_file():
            raise ValueError(f"Refusing non-regular source file: {main}")
        files.append(main)
    inc = amega_root / "includes"
    if inc.exists():
        for f in sorted(inc.glob("*.ab3"), key=lambda p: p.name):
            if f.name in GENERATED_AB3:
                continue
            if f.is_symlink() or not f.is_file():
                raise ValueError(f"Refusing non-regular source file: {f}")
            files.append(f)
    return [f for f in files if f.exists()]


def get_ab3_style_problems(amega_root: Path, detail: bool) -> list[str]:
    """Check all live .ab3 files for style problems.

    Returns a list of problem strings (leaf:line, leaf:line + detail lines).
    """
    problems: list[str] = []
    for f in get_live_ab3_files(amega_root):
        leaf = f.name
        orig = read_ab3_text(f)
        is_main = leaf == "SparkyDave.ab3"
        fmt = format_ab3_file_text(orig, is_main, leaf)
        if orig == fmt:
            continue

        a = orig.split("\n")
        b = fmt.split("\n")
        lim = max(len(a), len(b))
        shown = 0
        for i in range(lim):
            al = a[i] if i < len(a) else "<missing>"
            bl = b[i] if i < len(b) else "<missing>"
            if al == bl:
                continue
            problems.append(f"{leaf}:{i + 1}")
            if detail:
                problems.append(f"   now: {al}")
                problems.append(f"   want: {bl}")
            shown += 1
            if not detail and shown >= 3:
                problems.append(f"{leaf}: ... more")
                break
    return problems


# ---------------------------------------------------------------------------
# Self-test (port of Invoke-Ab3SelfTest with additions per plan).
# ---------------------------------------------------------------------------

SELFTEST_LINE_CASES = [
    ("x=y+3", "x = y + 3"),
    ("If x=y Then", "If x = y Then"),
    ("ByteXor{a.w,b.w}", "ByteXor{a.w, b.w}"),
    ("#NAME=1", "#NAME = 1"),
    ("pc=pc/2", "pc = pc/2"),
    ("pc = pc / 2", "pc = pc/2"),
    ("x=-1", "x = -1"),
    ("*obj\\x=*obj\\x+5", "*obj\\x = *obj\\x + 5"),
    ("*obj\\vx=*obj\\vx+acc*dirX", "*obj\\vx = *obj\\vx + acc*dirX"),
    ("acc *dirX", "acc*dirX"),
    ("If *obj\\vx > maxv", "If *obj\\vx > maxv"),
    ("If*obj\\vx > maxv", "If *obj\\vx > maxv"),
    ("AND($FFFF-#F_LEFT)", "AND ($FFFF - #F_LEFT)"),
    ("If pbot<=ay+8 Then", "If pbot <= ay + 8 Then"),
    ("If dirX<>0", "If dirX <> 0"),
    ("Left$(ln$,1)", "Left$(ln$, 1)"),
    ("Peek.b(adr+1)", "Peek.b(adr + 1)"),
    ("NEWTYPE.GameObj", "NEWTYPE.GameObj"),
    ("If (bits AND m)<>0", "If (bits AND m) <> 0"),
    ("For i=0 To #COPPER_COLORS-1", "For i = 0 To #COPPER_COLORS - 1"),
    ("fn$=packDir$+fgIff$", "fn$ = packDir$ + fgIff$"),
    ("SHARED packDir$,fgIff$", "SHARED packDir$, fgIff$"),
    ("  If x=1", "  If x = 1"),
    ("If vp > vpPeak Then vpPeak = vp", "If vp > vpPeak Then vpPeak = vp"),
    ("wrapX/8*8", "wrapX/8*8"),
    ("c.w=1+(dispIdx-1)-7*((dispIdx-1)/7)", "c.w = 1 + (dispIdx - 1) - 7*((dispIdx - 1)/7)"),
    ("MoveObject{&ply}", "MoveObject{&ply}"),
    ("quit=-1", "quit = -1"),
    ("flags.l=$10004", "flags.l = $10004"),
    ("Use BitMap 0", "Use BitMap 0"),
    ('If n$="NONE" Then Statement Return', 'If n$ = "NONE" Then Statement Return'),
    ('packDir$="assets/public/placeholder/"', 'packDir$ = "assets/public/placeholder/"'),
    ("If streamY Then oy=camY/tileSize", "If streamY Then oy = camY/tileSize"),
    ("x=y+3 ; trailing", "x = y + 3 ; trailing"),
    (";; already a block", ";; already a block"),
    ('fn$="pal-"+leaf$+".iff"', 'fn$ = "pal-" + leaf$ + ".iff"'),
    ('If Peek.b(adr)<>Asc("S") Then', 'If Peek.b(adr) <> Asc("S") Then'),
    ("Function Return -1", "Function Return -1"),
]


def run_selftest(detail: bool = False) -> int:
    """Run built-in snippet tests. Port of Invoke-Ab3SelfTest.

    All 35 line cases, file-level comment/const case, XTRA case, em-dash case,
    True/False rewrite case, missing-ending case, messy-ending case, ending
    idempotency case, plus byte-level Latin-1-misread em-dash and idempotency.
    """
    fail = 0

    # --- 35+ line-level cases ---
    for inp, expected in SELFTEST_LINE_CASES:
        got = format_ab3_logical_line(inp)
        if got != expected:
            print(f"FAIL  [{inp}]")
            print(f"  got  [{got}]")
            print(f"  want [{expected}]")
            fail += 1

    # Idempotent: formatting an already-formatted line yields the same.
    again = format_ab3_logical_line("x = y + 3")
    if again != "x = y + 3":
        print(f"FAIL  idempotent x = y + 3 -> [{again}]")
        fail += 1

    # --- File-level comment/const case ---
    blk = (
        "; =======================================================\n"
        "; TITLE\n"
        "; =======================================================\n"
        "; Rooms\n"
        "#BB=2\n"
        "#AAA=1\n"
        "\n"
    )
    fmt = format_ab3_file_text(blk, is_main=False)
    expect = (
        ";; =======================================================\n"
        ";; TITLE\n"
        ";; =======================================================\n"
        ";; Rooms\n"
        "#AAA = 1\n"
        "#BB  = 2\n"
        "\n"
        ";; END OF TITLE (BLITZ MODE)\n"
    )
    if fmt != expect:
        print("FAIL  file-level comments/consts")
        print("--- got ---")
        print(fmt)
        print("--- want ---")
        print(expect)
        fail += 1

    # --- XTRA case (main file) ---
    xtra = (
        "; XTRA\n"
        '; ExePath         = "RAM:"\n'
        "; /XTRA\n"
        "WBStartup\n"
        "x=1\n"
        "\n"
    )
    xgot = format_ab3_file_text(xtra, is_main=True)
    if 'ExePath         = "RAM:"' not in xgot:
        print("FAIL  XTRA was rewritten")
        print(xgot)
        fail += 1
    if not re.search(r"(?m)^x = 1$", xgot):
        print("FAIL  code after XTRA not formatted")
        print(xgot)
        fail += 1

    # --- Em-dash case (real Unicode) ---
    em = format_ab3_logical_line("; dash \u2014 here")
    if "dash -- here" not in em:
        print(f"FAIL  emdash [{em}]")
        fail += 1

    # --- Byte-level Latin-1-misread em-dash ---
    # UTF-8 em-dash (E2 80 94) read as Latin-1 chars: \xe2\x80\x94
    misread = "; dash \xe2\x80\x94 here"
    misread_out = convert_comment_tells(misread)
    if "--" not in misread_out:
        print(f"FAIL  byte-level emdash [{misread_out}]")
        fail += 1

    # --- True/False rewrite case ---
    bool_in = (
        "quitFlag.w=0\n"
        "If quitFlag=0 Then quitFlag=-1\n"
        "If levelOk=0 Then\n"
        "While Eof(0)=0\n"
        "If got<>0 Then\n"
        "Function.w VerifySaveBank{}\n"
        "  Function Return -1\n"
        "End Function\n"
        "\n"
    )
    bool_got = format_ab3_file_text(bool_in, is_main=False)
    bool_want = (
        "quitFlag.w = False\n"
        "If quitFlag = False Then quitFlag = True\n"
        "If levelOk = False Then\n"
        "While Eof(0) = 0\n"
        "If got <> 0 Then\n"
        "Function.w VerifySaveBank{}\n"
        "  Function Return -1\n"
        "End Function\n"
        "\n"
        ";; END OF VERIFY SAVE BANK (BLITZ MODE)\n"
    )
    if bool_got != bool_want:
        print("FAIL  True/False rewrite")
        print("--- got ---")
        print(bool_got)
        print("--- want ---")
        print(bool_want)
        fail += 1

    # --- Missing ending repair ---
    end_missing = (
        "Statement Foo{}\n"
        "  x = 1\n"
        "End Statement\n"
        "\n"
    )
    end_got = format_ab3_file_text(end_missing, is_main=False, leaf_name="sparkydave.foo.ab3")
    end_want = (
        "Statement Foo{}\n"
        "  x = 1\n"
        "End Statement\n"
        "\n"
        ";; END OF FOO (BLITZ MODE)\n"
    )
    if end_got != end_want:
        print("FAIL  missing closing comment not added")
        print("--- got ---")
        print(end_got)
        print("--- want ---")
        print(end_want)
        fail += 1

    # --- Messy ending repair ---
    end_messy = (
        ";; =======================================================\n"
        ";; INPUT (BLITZ MODE)\n"
        ";; =======================================================\n"
        "\n"
        "Statement ReadControls{}\n"
        "  x = 1\n"
        "End Statement\n"
        ";; END OF INPUT (BLITZ MODE)\n"
        "\n"
        "\n"
        "\n"
    )
    end_messy_got = format_ab3_file_text(end_messy, is_main=False, leaf_name="sparkydave.input.ab3")
    end_messy_want = (
        ";; =======================================================\n"
        ";; INPUT (BLITZ MODE)\n"
        ";; =======================================================\n"
        "\n"
        "Statement ReadControls{}\n"
        "  x = 1\n"
        "End Statement\n"
        "\n"
        ";; END OF INPUT (BLITZ MODE)\n"
    )
    if end_messy_got != end_messy_want:
        print("FAIL  extra blank lines / no-blank-before-comment not normalized")
        print("--- got ---")
        print(end_messy_got)
        print("--- want ---")
        print(end_messy_want)
        fail += 1

    # --- Ending idempotency ---
    end_good_twice = format_ab3_file_text(end_messy_want, is_main=False, leaf_name="sparkydave.input.ab3")
    if end_good_twice != end_messy_want:
        print("FAIL  ending fix not idempotent")
        print("--- got ---")
        print(end_good_twice)
        print("--- want ---")
        print(end_messy_want)
        fail += 1

    # --- Idempotency: formatting an already-formatted tree rewrites 0 files ---
    with TemporaryDirectory() as tmp:
        root = Path(tmp) / "game"
        inc = root / "includes"
        inc.mkdir(parents=True)
        test_fn = inc / "sparkydave.test.ab3"
        original = "Statement Foo{}\n  x = 1\nEnd Statement\n\n;; END OF FOO (BLITZ MODE)\n"
        test_fn.write_bytes(encode_ab3(original))

        orig = read_ab3_text(test_fn)
        fmt = format_ab3_file_text(orig, is_main=False, leaf_name="sparkydave.test.ab3")
        if orig != fmt:
            print("FAIL  idempotency: formatting an already-formatted file changes it")
            print(f"  got:  [{fmt!r}]")
            print(f"  orig: [{orig!r}]")
            fail += 1

    if fail == 0:
        print(f"SelfTest: {len(SELFTEST_LINE_CASES)} line cases + file/XTRA/emdash/ending OK")
        return 0
    print(f"SelfTest: {fail} failure(s)")
    return 1


# ---------------------------------------------------------------------------
# Main CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="format_ab3",
        description="Style check / fix for AmiBlitz .ab3 source",
    )
    ap.add_argument("--check", action="store_true", help="Report violations, write nothing (default)")
    ap.add_argument("--write", action="store_true", help="Rewrite live .ab3 files in place")
    ap.add_argument("--selftest", action="store_true", help="Run built-in snippet tests")
    ap.add_argument("--detail", action="store_true", help="List every differing line")
    ap.add_argument("--root", default=None, help="game folder")
    args = ap.parse_args(argv)

    if args.check and args.write:
        ap.error("--check and --write are mutually exclusive")
    if not args.check and not args.write and not args.selftest:
        args.check = True  # default = check

    if args.selftest:
        return run_selftest(args.detail)

    root = Path(args.root) if args.root else Path(__file__).resolve().parent.parent / "game"
    live_files = get_live_ab3_files(root)

    if args.check:
        problems = get_ab3_style_problems(root, args.detail)
        if not problems:
            print(f"PASS  {len(live_files)} live .ab3 file(s) already match Format-Ab3 rules")
            return 0
        print(f"FAIL  {len(problems)} style problem(s)")
        for p in problems:
            print(f"  {p}")
        return 1

    # --write mode
    clear_cache()
    n = 0
    for f in live_files:
        leaf = f.name
        orig = read_ab3_text(f)
        is_main = leaf == "SparkyDave.ab3"
        fmt = format_ab3_file_text(orig, is_main, leaf)
        if orig == fmt:
            continue
        write_ab3_text(f, fmt)
        n += 1
        print(f"wrote {leaf}")
    print(f"Format-Ab3: {n} file(s) rewritten")
    return 0


if __name__ == "__main__":
    sys.exit(main())
