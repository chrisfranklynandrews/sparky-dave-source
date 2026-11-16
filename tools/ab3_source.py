"""Shared helpers for ab3 checker tools (port from Format-Ab3 / Test-SourceSplit).

Read: raw bytes, strip UTF-8 BOM if present, decode iso-8859-1 (latin-1),
normalize CRLF/CR to LF. Write: iso-8859-1, LF only.
"""
from __future__ import annotations

import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Reading / writing .ab3 files: Latin-1, LF-only, BOM-stripped.
# ---------------------------------------------------------------------------

def read_ab3_bytes(path: Path) -> bytes:
    """Read raw bytes (for byte-level checks like LINEENDS)."""
    return path.read_bytes()


def decode_ab3(data: bytes) -> str:
    """Decode .ab3 bytes to text: strip UTF-8 BOM, decode latin-1, normalize CRLF/CR to LF."""
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    text = data.decode("iso-8859-1")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def read_ab3(path: Path) -> str:
    """Read and decode an .ab3 file."""
    return decode_ab3(read_ab3_bytes(path))


def encode_ab3(text: str) -> bytes:
    """Encode text back to .ab3 bytes (latin-1, LF only)."""
    return text.encode("iso-8859-1")


def read_lines(path: Path) -> list[str]:
    """Split file into lines (LF normalized, no trailing empty from final newline unless file ends without newline)."""
    return decode_ab3(read_ab3_bytes(path)).split("\n")


# ---------------------------------------------------------------------------
# Comment and string stripping.
# ---------------------------------------------------------------------------

def strip_comment(line: str) -> str:
    """Strip a trailing ';' comment, but not a ';' inside a string literal.

    Mirrors Strip-Comment from Test-SourceSplit.ps1 line-for-line: walks
    char by char, toggling an in-string flag on every double-quote, and
    returns the code before the first unquoted ';'.
    """
    in_str = False
    for i in range(len(line)):
        c = line[i]
        if c == '"':
            in_str = not in_str
            continue
        if c == ";" and not in_str:
            return line[:i]
    return line


def strip_strings(line: str) -> str:
    """Replace string contents with spaces so syntax checks don't inspect examples.

    Mirrors Strip-Strings from Test-SourceSplit.ps1: a paired-literal regex
    replacement of `"[^"]*"` -> single space. An unbalanced quote leaves the
    rest of the line visible instead of swallowing it -- the safer failure
    direction.
    """
    return re.sub(r'"[^"]*"', " ", line)


# ---------------------------------------------------------------------------
# Cached line accessors (port of Get-Code / Get-Syn with per-path caching).
# ---------------------------------------------------------------------------

_code_cache: dict[str, list[str]] = {}
_syn_cache: dict[str, list[str]] = {}


def get_code(path: Path) -> list[str]:
    """Return comment-stripped lines for a file, cached per path."""
    key = str(path)
    if key not in _code_cache:
        lines = read_lines(path)
        _code_cache[key] = [strip_comment(ln) for ln in lines]
    return _code_cache[key]


def get_syn(path: Path) -> list[str]:
    """Return comment- AND string-stripped lines for a file, cached per path."""
    key = str(path)
    if key not in _syn_cache:
        lines = read_lines(path)
        _syn_cache[key] = [strip_strings(strip_comment(ln)) for ln in lines]
    return _syn_cache[key]


def clear_cache() -> None:
    """Clear all cached line data (for --selftest fixtures that reuse paths)."""
    _code_cache.clear()
    _syn_cache.clear()


# ---------------------------------------------------------------------------
# Patterns (case-sensitivity matrix from plan section 3).
# ---------------------------------------------------------------------------

# Definition / call detection.
# Capture group 1: name. Capture group 2: parameter list.
# Case-insensitive (PS1 -match is case-insensitive by default).
DEF_PATTERN = re.compile(
    r"^\s*(?:Statement|Function)(?:\$|\.\w+)?\s+\$?([A-Za-z_]\w*)\s*\{(.*)$",
    re.IGNORECASE,
)

# XINCLUDE regex (IGNORECASE per plan L54).
XINCLUDE_PATTERN = re.compile(r'^XINCLUDE\s+"([^"]+)"$', re.IGNORECASE)

# IF FORM: block If carries a trailing Then.
# PS1: '^(?i)if\b.*\bthen\s*$'
IF_FORM_PATTERN = re.compile(r"^if\b.*\bthen\s*$", re.IGNORECASE)

# OPERATORS: case-sensitive (PS1 -cmatch equivalents).
# And/Or/Not/Shl/Shr/Mod must stay case-sensitive.
OPERATOR_CASE_SENSITIVE_PATTERN = re.compile(r"\b(And|Or|Not|Shl|Shr|Mod)\b")

# ElseIf must stay case-sensitive.
ELSEIF_CASE_SENSITIVE_PATTERN = re.compile(r"^ElseIf\b")

# CALL FORM: known math/science functions missing call parentheses.
# Case-insensitive (PS1 -match). Negative lookahead for optional whitespace + '('.
CALL_FORM_PATTERN = re.compile(
    r"\b(Xor|Log10|ASin|ACos|ATan|HCos|HSin|HTan|QAngle|QWrap|QAbs|QFrac|QLimit|OLimit|Sgn|Sqr|Sin|Cos|Tan|Exp|Log|Abs|Frac|Int)\b(?!\s*\()",
    re.IGNORECASE,
)

# SHARED declarations (IGNORECASE).
SHARED_PATTERN = re.compile(r"^SHARED\s+(.*)$", re.IGNORECASE)

# For-loop variable (IGNORECASE).
FOR_VAR_PATTERN = re.compile(r"^For\s+([A-Za-z_]\w*)\s*=", re.IGNORECASE)

# End Statement / End Function / End (IGNORECASE).
END_PROC_PATTERN = re.compile(r"^(End\s+Statement|End\s+Function|End)$", re.IGNORECASE)

# File-scope Dim (column 1). IGNORECASE.
DIM_PATTERN = re.compile(r"^Dim\s+([A-Za-z_]\w*)", re.IGNORECASE)

# File-scope scalar declaration: name.type? =
# IGNORECASE.
SCALAR_DECL_PATTERN = re.compile(r"^([A-Za-z_]\w*)\s*\.\s*[wqlsb]?\s*=", re.IGNORECASE)

# File-scope array assignment: name(args) =
# IGNORECASE.
ARRAY_ASSIGN_PATTERN = re.compile(r"^([A-Za-z_]\w*)\s*\([^\)]*\)\s*=", re.IGNORECASE)

# SHARED with empty parens (array import): SHARED name()
SHARED_ARRAY_PATTERN = re.compile(r"^SHARED\s+(.*)$", re.IGNORECASE)

# Mode directives: BLITZ / AMIGA (for flatten state).
BLITZ_PATTERN = re.compile(r"^BLITZ$", re.IGNORECASE)
AMIGA_PATTERN = re.compile(r"^AMIGA$", re.IGNORECASE)

# Generated includes are written by build_assets.py and are not formatter inputs.
GENERATED_NAMES = {
    "sparkydave.level.meta.ab3",
    "sparkydave.metatile.classes.ab3",
    "sparkydave.areas.table.ab3",
    "sparkydave.sound.data.ab3",
    "sparkydave.mario-allstars.data.ab3",
    "sparkydave.allstars-palette.data.ab3",
    "sparkydave.jukebox-scenes.data.ab3",
}

# Blitz-only drawing commands (IGNORECASE).
MODE_PATTERN = re.compile(
    r"^(Cls|Boxf|Blit|BBlit|BBlitMode|BlitMode|Scroll|BlockScroll|BitMapOutput|RawStatus|UnBuffer|UseBuffer|UseFront|Use\s+BitMap|DisplaySprite|InFront|Plot)\b",
    re.IGNORECASE,
)

# Call detection in flattened code (IGNORECASE on name, case-sensitive lookbehind class).
# PS1: '(?<![\w\$\\.])([A-Za-z_]\w*)\s*\{'
CALL_PATTERN = re.compile(r"(?<![\w\$\\.])([A-Za-z_]\w*)\s*\{", re.IGNORECASE)

# Comment-only line detection.
def is_comment_only(line: str) -> bool:
    return line.lstrip().startswith(";")


# ---------------------------------------------------------------------------
# Path resolution for XINCLUDE paths (AmigaDOS BSUF semantics).
# ---------------------------------------------------------------------------

def resolve_include_path(from_file: Path, path_str: str) -> tuple[Path | None, str | None]:
    """Resolve an XINCLUDE path relative to the including file's directory.

    AmigaDOS BSUF: a leading '/' means one directory up (not volume root).
    '..' is a literal directory name (not a traversal). Backslash is invalid.
    A path that is all slashes names a directory, not a file.

    Returns (target_path, error_message). On success error_message is None.
    On failure target_path is None and error_message describes the problem.
    """
    # Reject '..' (literal dir name, not a traversal).
    if ".." in Path(path_str).parts:
        return None, "uses '..' in '" + path_str + "' -- AmigaDOS has no '..'; a leading '/' is the parent operator"

    # Reject backslash.
    if "\\" in path_str:
        return None, "uses a backslash in '" + path_str + "' -- XINCLUDE paths use '/'"

    # Leading '/' = parent of including file's directory, one per slash.
    dirs = from_file.parent
    probe = path_str
    while probe.startswith("/"):
        dirs = dirs.parent
        probe = probe[1:]

    # After consuming slashes, rest is relative to dirs.
    rel = probe

    # A path that is all slashes (or ends with /) names a directory, not a file.
    if rel == "" or rel.endswith("/"):
        return dirs / rel, "names a directory, not a file"

    target = dirs / rel
    if not target.exists():
        return None, "'" + path_str + "' does not resolve to a file (tried '" + str(target) + "')"

    return target, None
