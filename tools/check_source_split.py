#!/usr/bin/env python3
"""Structural gate (port from Test-SourceSplit.ps1) -- real scan of live tree.

Validates include structure, order, and BLITZ placement. Exit 0 = all pass, 1 = at least one FAIL.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).parent))
from ab3_source import (
    decode_ab3,
    read_ab3_bytes,
    read_lines,
    strip_comment,
    get_code,
    strip_strings,
    get_syn,
    clear_cache,
    DEF_PATTERN,
    XINCLUDE_PATTERN,
    IF_FORM_PATTERN,
    OPERATOR_CASE_SENSITIVE_PATTERN,
    ELSEIF_CASE_SENSITIVE_PATTERN,
    CALL_FORM_PATTERN,
    SHARED_PATTERN,
    FOR_VAR_PATTERN,
    END_PROC_PATTERN,
    DIM_PATTERN,
    SCALAR_DECL_PATTERN,
    ARRAY_ASSIGN_PATTERN,
    SHARED_ARRAY_PATTERN,
    BLITZ_PATTERN,
    AMIGA_PATTERN,
    MODE_PATTERN,
    CALL_PATTERN,
    resolve_include_path,
)
from build_variant import COPY_NAME, LOOK_COPY_NAME, current_variant, is_any_variant_file, read_constants, variant_files

CHECK_NAMES = [
    "INCLUDES", "XINCLUDE PATHS", "IF FORM", "OPERATORS", "CALL FORM",
    "SHARED COVERAGE", "ORPHAN BODY", "DUPLICATES", "COMMAND NAMES", "ORDER",
    "DIM ORDER", "PROOF DIM", "PROOF SHARED", "PROOF ORDER",
    "PROOF ORPHAN BODY", "CONSTANTS", "VARIANTS", "LOOKS", "MODE", "LINEENDS", "LINE LENGTH",
]

MAX_SOURCE_LINE = 250

# Library command names a Statement or Function must not reuse (tools/build_blitz_commands.py).
COMMAND_LIST = Path(__file__).resolve().parent / "blitz_commands.txt"


class FlatLine:
    """A line in the flattened tree: file leaf, line number, raw text, code (comment-stripped), below-blitz flag."""
    __slots__ = ("file", "line", "text", "code", "below")

    def __init__(self, file: str, line: int, text: str, code: str, below: bool):
        self.file = file
        self.line = line
        self.text = text
        self.code = code
        self.below = below


class CheckContext:
    """Holds results for all the checks, plus flatten bookkeeping."""
    def __init__(self):
        self.problems: dict[str, list[str]] = {n: [] for n in CHECK_NAMES}
        self.include_order: list[str] = []
        self.missing: list[str] = []
        self.nested: list[str] = []
        self.checked_paths: int = 0
        self.global_names_count: int = 0
        self.def_count: int = 0
        self.command_count: int = 0
        self.sub_root_count: int = 0
        self.flat_count: int = 0
        self.constant_count: int = 0


def scan_tree(root: Path) -> list[Path]:
    """Return all .ab3 files in scope: SparkyDave.ab3 + includes/*.ab3 + proofs/*.ab3 + tests/*.ab3."""
    files: list[Path] = []
    seen: set[str] = set()

    main_path = root / "SparkyDave.ab3"
    if main_path.exists() and main_path.is_file():
        files.append(main_path)
        seen.add(str(main_path.resolve()))

    for sub in ("includes", "proofs", "tests"):
        sub_dir = root / sub
        if sub_dir.exists():
            for f in sorted(sub_dir.glob("*.ab3")):
                key = str(f.resolve())
                if key not in seen:
                    files.append(f)
                    seen.add(key)

    return files


def scan_sub_roots(root: Path) -> tuple[list[Path], list[Path]]:
    """Return (sub_root_files, sub_root_dirs)."""
    sub_root_ab3: list[Path] = []
    sub_root_dirs: list[Path] = []
    for sub in ("proofs", "tests"):
        sub_dir = root / sub
        if sub_dir.exists():
            sub_root_dirs.append(sub_dir)
            for f in sorted(sub_dir.glob("*.ab3")):
                sub_root_ab3.append(f)
    return sub_root_ab3, sub_root_dirs


def is_sub_root(path: Path, sub_root_dirs: list[Path]) -> bool:
    """Check if a file is inside one of the sub-root directories."""
    rp = path.resolve()
    for sd in sub_root_dirs:
        try:
            rp.relative_to(sd.resolve())
            return True
        except ValueError:
            pass
    return False


def flatten_main(root: Path, ctx: CheckContext) -> list[FlatLine]:
    """Flatten the main file the way the compiler sees it.

    Records include order, missing targets, nested includes, and BLITZ/AMIGA state.
    """
    flat: list[FlatLine] = []
    below = False
    main_path = root / "SparkyDave.ab3"

    def _flatten(path: Path, is_main: bool) -> None:
        nonlocal below
        leaf = path.name
        for n, raw in enumerate(read_lines(path), 1):
            t = raw.strip()
            m = XINCLUDE_PATTERN.match(t)
            if m:
                rel = m.group(1)
                if not is_main:
                    ctx.nested.append(f"{leaf}:{n} includes another file: {rel}")
                # XINCLUDE paths relative to $Root (main + includes/ are under root).
                ctx.include_order.append(rel)
                target = (root / rel).resolve()
                try:
                    target.relative_to(root.resolve())
                except ValueError:
                    ctx.missing.append(f"{leaf}:{n} target escapes source root: {rel}")
                    continue
                if not target.exists():
                    ctx.missing.append(f"{leaf}:{n} target does not exist: {rel}")
                    continue
                _flatten(target, False)
                continue
            # column-1 mode directive in main file flips compile mode
            if is_main and raw == "BLITZ":
                below = True
            if is_main and raw == "AMIGA":
                below = False
            flat.append(FlatLine(
                file=leaf, line=n, text=raw, code=strip_comment(raw), below=below
            ))

    _flatten(main_path, True)
    return flat


def flatten_proof(path: Path) -> list[FlatLine]:
    """Flatten a standalone root (proofs/tests) recursively with a visited-set.

    Each proof tracks its own BLITZ/AMIGA state. Path resolution uses the same
    logic as the PATHS check (own directory, parent per leading slash).
    """
    flat: list[FlatLine] = []
    seen: set[str] = set()
    below = False

    def _flatten(p: Path) -> None:
        nonlocal below
        full = str(p.resolve())
        if full in seen:
            return
        seen.add(full)
        leaf = p.name
        for n, raw in enumerate(read_lines(p), 1):
            t = raw.strip()
            m = XINCLUDE_PATTERN.match(t)
            if m:
                rel = m.group(1)
                target, err = resolve_include_path(p, rel)
                if err is not None or target is None or not target.exists():
                    continue
                _flatten(target)
                continue
            if raw == "BLITZ":
                below = True
            if raw == "AMIGA":
                below = False
            flat.append(FlatLine(
                file=leaf, line=n, text=raw, code=strip_comment(raw), below=below
            ))

    _flatten(path)
    return flat


def get_global_names(root: Path, all_files: list[Path]) -> set[str]:
    """Collect global names from globals.ab3 scalars + Dim arrays from every file in scope.

    Uses case-insensitive comparison (PS1 HashSet with OrdinalIgnoreCase).
    """
    names: set[str] = set()
    global_file = root / "includes" / "sparkydave.globals.ab3"
    if global_file.exists():
        for raw in get_code(global_file):
            t = raw.strip()
            m = re.match(r"^([A-Za-z_]\w*)\s*\.\s*[wqlsb]?\s*=", t, re.IGNORECASE)
            if m:
                names.add(m.group(1))

    for f in all_files:
        for raw in get_code(f):
            t = raw.strip()
            m = DIM_PATTERN.match(t)
            if m:
                names.add(m.group(1))

    return names


def get_shared_problems(flat: list[FlatLine], global_names: set[str], leaf_override: str | None = None) -> list[str]:
    """Find globals touched inside a procedure without being in its SHARED list.

    Per PS1 Get-SharedProblems: walks the flat list, tracks procedure boundaries,
    excludes the procedure's own parameters, For-loop variables, and SHARED names.
    Case-sensitive on purpose -- Blitz keeps library commands and variables in
    separate namespaces.
    """
    out: list[str] = []
    if not global_names:
        return out

    sorted_names = sorted(global_names)
    # Match names case-sensitively. Blitz identifiers in procedure bodies are
    # distinct from library commands, so a differently cased token is not the
    # declared global being checked here.
    glob_re = re.compile(r"\b(" + "|".join(re.escape(name) for name in sorted_names) + r")\b")

    in_proc = False
    lname = ""
    proc_shared: set[str] = set()
    proc_locals: set[str] = set()

    for fl in flat:
        leaf = leaf_override if leaf_override else fl.file
        t = fl.code.strip()
        if t == "" or t.startswith(";"):
            continue
        if not in_proc:
            m = DEF_PATTERN.match(t)
            if m:
                in_proc = True
                lname = m.group(1)
                proc_shared = set()
                proc_locals = set()
                param_list = m.group(2)
                # Parameters: group 2 up to last }
                if param_list and "}" in param_list:
                    params_part = param_list.rsplit("}", 1)[0]
                    for pm in params_part.split(","):
                        pm_m = re.match(r"^([A-Za-z_]\w*)", pm.strip())
                        if pm_m:
                            proc_locals.add(pm_m.group(1))
            continue
        # Inside a procedure.
        m = SHARED_PATTERN.match(t)
        if m:
            for sm in m.group(1).split(","):
                sm_m = re.match(r"^([A-Za-z_]\w*)", sm.strip())
                if sm_m:
                    proc_shared.add(sm_m.group(1))
            continue
        m = FOR_VAR_PATTERN.match(t)
        if m:
            proc_locals.add(m.group(1))
        m = END_PROC_PATTERN.match(t)
        if m:
            in_proc = False
            continue
        # Check for global references.
        for gm in glob_re.finditer(t):
            g_orig = gm.group(1)
            if g_orig in proc_shared:
                continue
            if g_orig in proc_locals:
                continue
            out.append(f"{leaf} {lname} touches global '{g_orig}' without SHARED -- undeclared means local 0, silently")
    return out


def get_orphan_problems(flat: list[FlatLine], leaf_override: str | None = None) -> list[str]:
    """Find orphaned procedure bodies (deleted header leaves SHARED/End outside a proc).

    Two signals: indented SHARED outside a procedure, or End Statement/End Function
    outside a procedure.
    """
    out: list[str] = []
    in_proc = False

    for fl in flat:
        t = fl.code.strip()
        if t == "" or t.startswith(";"):
            continue
        leading = len(fl.text) - len(fl.text.lstrip())
        leaf = leaf_override if leaf_override else fl.file
        if not in_proc:
            if leading > 0 and t.startswith("SHARED"):
                out.append(f"{leaf}:{fl.line} indented SHARED outside a procedure -- the Statement/Function header above it may be missing")
            if re.match(r"^(End\s+Statement|End\s+Function)$", t, re.IGNORECASE):
                out.append(f"{leaf}:{fl.line} '{t}' outside a procedure -- the header for this body may be missing")
        if DEF_PATTERN.match(t):
            in_proc = True
        if END_PROC_PATTERN.match(t):
            in_proc = False
    return out


def get_dim_order_problems(flat: list[FlatLine]) -> list[str]:
    """Find file-scope array issues: assignment before Dim, or SHARED arr() without Dim.

    Per PS1 Get-DimOrderProblems:
    - Pass 1: column-1 Dims (file scope globals).
    - Pass 2: file-scope array assignment needs its Dim earlier.
    - Pass 3: SHARED arr() with no column-1 Dim in the root.
    """
    out: list[str] = []
    dim_at: dict[str, int] = {}
    dim_where: dict[str, str] = {}

    # Pass 1: column-1 Dims only (file scope = column 1).
    for i, fl in enumerate(flat):
        if fl.text and fl.text[0].isspace():
            continue  # not column 1
        code = fl.code
        if code.strip() == "":
            continue
        m = DIM_PATTERN.match(code)
        if m:
            nm = m.group(1).lower()
            if nm not in dim_at:
                dim_at[nm] = i
                dim_where[nm] = f"{fl.file}:{fl.line}"

    # Pass 2: file-scope array assignment needs its Dim earlier.
    for i, fl in enumerate(flat):
        if fl.text and fl.text[0].isspace():
            continue
        code = fl.code
        if code.strip() == "":
            continue
        m = ARRAY_ASSIGN_PATTERN.match(code)
        if m:
            nm = m.group(1).lower()
            whr = f"{fl.file}:{fl.line}"
            if nm not in dim_at:
                out.append(f"{whr}: {nm}(n) = ... at file scope, but no column-1 'Dim {nm}' precedes it -- UAE will report 'Array not yet dim'd' here and a syntax error on every line after")
            elif i < dim_at[nm]:
                out.append(f"{whr}: {nm}(n) = ... at file scope, before its Dim at {dim_where[nm]}")

    # Pass 3: SHARED arr() with no column-1 Dim.
    for fl in flat:
        code = fl.code
        m = SHARED_ARRAY_PATTERN.match(code.strip())
        if not m:
            continue
        whr = f"{fl.file}:{fl.line}"
        for sm in m.group(1).split(","):
            sm_m = re.match(r"^([A-Za-z_]\w*)\s*\(\s*\)", sm.strip())
            if not sm_m:
                continue
            nm = sm_m.group(1).lower()
            if nm in dim_at:
                continue
            out.append(f"{whr}: SHARED {nm}() but this compilation root has no column-1 'Dim {nm}' -- the array does not exist here, and UAE reports 'Array not yet dim'd' at the first access")
    return out


def get_order_problems(flat: list[FlatLine], def_at: dict[str, int], def_where: dict[str, str]) -> list[str]:
    """Find calls to a Procedure before its definition in flattened order."""
    out: list[str] = []
    for i, fl in enumerate(flat):
        code = fl.code
        if code.strip() == "":
            continue
        if DEF_PATTERN.match(code):
            continue  # a definition line is not a call
        for m in CALL_PATTERN.finditer(code):
            display_name = m.group(1)
            nm = display_name.lower()
            if nm not in def_at:
                continue
            if i < def_at[nm]:
                out.append(f"{fl.file}:{fl.line} calls {display_name} before its definition at {def_where[nm]}")
    return out


CONSTANT_DEF_PATTERN = re.compile(r"^\s*#([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")
CONSTANT_USE_PATTERN = re.compile(r"#([A-Za-z_][A-Za-z0-9_]*)")


def get_constant_problems(flat: list[FlatLine], label: str = "") -> tuple[list[str], int]:
    """Every `#NAME` used must be defined by a `#NAME = ...` line earlier in the flattened
    source. Comments and string literals are not code. A constant used on the right of its
    own definition line is a use of an earlier one. Names compare without case.

    The compiler's own message for a miss is `constant not found`, and one missing line
    fans out into dozens of errors, so the PC catches it first (a deleted block of
    constants once took 39 compiler errors to find).
    """
    problems: list[str] = []
    first_defined: dict[str, str] = {}
    for fl in flat:
        m = CONSTANT_DEF_PATTERN.match(strip_strings(fl.code))
        if m:
            first_defined.setdefault(m.group(1).upper(), f"{fl.file}:{fl.line}")
    defined: set[str] = set()
    prefix = f"{label}: " if label else ""
    for fl in flat:
        code = strip_strings(fl.code)
        m = CONSTANT_DEF_PATTERN.match(code)
        scan = m.group(2) if m else code
        for use in CONSTANT_USE_PATTERN.finditer(scan):
            name = use.group(1)
            if name.upper() in defined:
                continue
            if name.upper() in first_defined:
                problems.append(
                    f"{prefix}{fl.file}:{fl.line} uses #{name} before it is defined (defined at {first_defined[name.upper()]})"
                )
            else:
                problems.append(f"{prefix}{fl.file}:{fl.line} uses #{name}, which is never defined")
        if m:
            defined.add(m.group(1).upper())
    return problems, len(first_defined)


def run_checks(root: Path) -> CheckContext:
    """Run all the checks on the tree and return a CheckContext with results."""
    ctx = CheckContext()

    all_files = scan_tree(root)
    sub_root_ab3, sub_root_dirs = scan_sub_roots(root)
    flat = flatten_main(root, ctx)
    ctx.flat_count = len(flat)
    ctx.sub_root_count = len(sub_root_ab3)

    # Count XINCLUDE paths checked.
    for f in all_files:
        for raw in read_lines(f):
            if XINCLUDE_PATTERN.match(raw.strip()):
                ctx.checked_paths += 1

    # -----------------------------------------------------------------------
    # 1 INCLUDES
    # -----------------------------------------------------------------------
    p: list[str] = []
    p += ctx.missing
    p += ctx.nested
    inc_counts: dict[str, int] = {}
    for inc in ctx.include_order:
        inc_counts[inc] = inc_counts.get(inc, 0) + 1
    for inc, count in sorted(inc_counts.items()):
        if count > 1:
            p.append(f"XINCLUDE listed {count} times: {inc}")
    inc_dir = root / "includes"
    if inc_dir.exists():
        referenced = {Path(inc).name for inc in ctx.include_order}
        for f in sorted(inc_dir.glob("*.ab3")):
            if f.name not in referenced and not is_any_variant_file(f.name):
                p.append(f"orphan - in includes/ but never XINCLUDEd: {f.name}")
    ctx.problems["INCLUDES"].extend(sorted(set(p)))

    # -----------------------------------------------------------------------
    # 1b XINCLUDE PATHS
    # -----------------------------------------------------------------------
    for f in all_files:
        leaf = f.name
        sub_root = is_sub_root(f, sub_root_dirs)
        for ln_no, raw in enumerate(read_lines(f), 1):
            t = raw.strip()
            m = XINCLUDE_PATTERN.match(t)
            if not m:
                continue
            rel = m.group(1)

            # Reject '..' (AmigaDOS literal dir).
            if ".." in Path(rel).parts:
                fixed = rel.replace("../", "/")
                ctx.problems["XINCLUDE PATHS"].append(
                    f"{leaf}:{ln_no} uses '..' in '{rel}' -- AmigaDOS has no '..'; a leading '/' is the parent operator, so use '/{fixed}"
                )
                continue

            # Reject backslash.
            if "\\" in rel:
                ctx.problems["XINCLUDE PATHS"].append(
                    f"{leaf}:{ln_no} uses a backslash in '{rel}' -- XINCLUDE paths use '/'"
                )
                continue

            # All-slashes = directory not file.
            if rel.lstrip("/") == "":
                ctx.problems["XINCLUDE PATHS"].append(
                    f"{leaf}:{ln_no} '{rel}' names a directory, not a file"
                )
                continue

            # Sub-roots resolve against their own directory.
            if sub_root:
                target, err = resolve_include_path(f, rel)
                if err is not None:
                    ctx.problems["XINCLUDE PATHS"].append(f"{leaf}:{ln_no} {err}")

    # -----------------------------------------------------------------------
    # 1c IF FORM
    # -----------------------------------------------------------------------
    for f in all_files:
        leaf = f.name
        for ln_no, raw in enumerate(get_code(f), 1):
            t = raw.strip()
            if t == "" or t.startswith(";"):
                continue
            if IF_FORM_PATTERN.match(t):
                ctx.problems["IF FORM"].append(
                    f"{leaf}:{ln_no} block If carries a trailing Then -- write 'If cond' alone, and use Then only on a single-line If"
                )

    # -----------------------------------------------------------------------
    # 1d OPERATORS
    # -----------------------------------------------------------------------
    for f in all_files:
        leaf = f.name
        for ln_no, raw in enumerate(get_code(f), 1):
            t = raw.strip()
            if t == "" or t.startswith(";"):
                continue
            cm = OPERATOR_CASE_SENSITIVE_PATTERN.search(t)
            if cm:
                ctx.problems["OPERATORS"].append(
                    f"{leaf}:{ln_no} title-case '{cm.group(1)}' -- use uppercase Boolean/shift tokens"
                )
            if ELSEIF_CASE_SENSITIVE_PATTERN.match(t):
                ctx.problems["OPERATORS"].append(
                    f"{leaf}:{ln_no} 'ElseIf' is not an AmiBlitz keyword -- use a second mutually exclusive If, or an Else block"
                )

    # -----------------------------------------------------------------------
    # 1e CALL FORM
    # -----------------------------------------------------------------------
    for f in all_files:
        leaf = f.name
        for ln_no, raw in enumerate(get_syn(f), 1):
            t = raw.strip()
            if t == "" or t.startswith(";"):
                continue
            for m in CALL_FORM_PATTERN.finditer(t):
                fn = m.group(1)
                if fn.lower() == "xor":
                    ctx.problems["CALL FORM"].append(
                        f"{leaf}:{ln_no} uses infix XOR -- AmiBlitz 3.13 requires Xor(left, right) as a function"
                    )
                else:
                    ctx.problems["CALL FORM"].append(
                        f"{leaf}:{ln_no} uses '{fn}' without call parentheses -- write {fn}(arguments)"
                    )

    # -----------------------------------------------------------------------
    # 1f SHARED COVERAGE
    # -----------------------------------------------------------------------
    global_names = get_global_names(root, all_files)
    ctx.global_names_count = len(global_names)
    shared_p = get_shared_problems(flat, global_names)
    ctx.problems["SHARED COVERAGE"].extend(sorted(set(
        f"{msg} (note: {ctx.global_names_count} globals, every one touched inside a procedure is SHARED)"
        for msg in shared_p
    )))

    # -----------------------------------------------------------------------
    # 1g ORPHAN BODY
    # -----------------------------------------------------------------------
    ctx.problems["ORPHAN BODY"].extend(get_orphan_problems(flat))

    # -----------------------------------------------------------------------
    # 2 DUPLICATES
    # -----------------------------------------------------------------------
    def_at: dict[str, int] = {}
    def_where: dict[str, str] = {}
    for i, fl in enumerate(flat):
        m = DEF_PATTERN.match(fl.code)
        if m:
            nm = m.group(1).lower()
            whr = f"{fl.file}:{fl.line}"
            if nm in def_at:
                ctx.problems["DUPLICATES"].append(
                    f"{nm} defined at {def_where[nm]} and again at {whr}"
                )
            else:
                def_at[nm] = i
                def_where[nm] = whr
    ctx.def_count = len(def_at)

    # -----------------------------------------------------------------------
    # 2b COMMAND NAMES
    # -----------------------------------------------------------------------
    # SetCycle and CyclePalette were both Palette library commands; a Statement of that name
    # compiled into nonsense, far from the cause.
    if not COMMAND_LIST.exists():
        ctx.problems["COMMAND NAMES"].append(f"{COMMAND_LIST.name} is missing: run python tools/build_blitz_commands.py")
    else:
        commands = {line.strip().lower(): line.strip() for line in COMMAND_LIST.read_text(encoding="ascii").splitlines() if line.strip()}
        ctx.command_count = len(commands)
        for f in all_files:
            for ln_no, raw in enumerate(read_lines(f), 1):
                m = DEF_PATTERN.match(strip_comment(raw))
                if m and m.group(1).lower() in commands:
                    ctx.problems["COMMAND NAMES"].append(
                        f"{f.name}:{ln_no} {m.group(1)} is the library command {commands[m.group(1).lower()]}: rename it"
                    )

    # -----------------------------------------------------------------------
    # 3 ORDER
    # -----------------------------------------------------------------------
    ctx.problems["ORDER"].extend(get_order_problems(flat, def_at, def_where))

    # -----------------------------------------------------------------------
    # 3b DIM ORDER (main tree)
    # -----------------------------------------------------------------------
    ctx.problems["DIM ORDER"].extend(get_dim_order_problems(flat))

    # -----------------------------------------------------------------------
    # 3b2 CONSTANTS (main tree)
    # -----------------------------------------------------------------------
    constant_problems, ctx.constant_count = get_constant_problems(flat)
    ctx.problems["CONSTANTS"].extend(constant_problems)

    # -----------------------------------------------------------------------
    # 3c-3f PROOF checks (per standalone root)
    # -----------------------------------------------------------------------
    all_def_names = set(def_at.keys())

    for proof in sub_root_ab3:
        proof_leaf = proof.name
        proof_flat = flatten_proof(proof)

        # PROOF DIM
        ctx.problems["PROOF DIM"].extend(get_dim_order_problems(proof_flat))

        # CONSTANTS (the proof's own flattened tree)
        proof_constant_problems, _ = get_constant_problems(proof_flat, proof_leaf)
        ctx.problems["CONSTANTS"].extend(proof_constant_problems)

        # PROOF SHARED
        proof_names: set[str] = set()
        for fl in proof_flat:
            if fl.text and fl.text[0].isspace():
                continue  # column 1 only
            t = fl.code.strip()
            if t == "":
                continue
            m = DIM_PATTERN.match(t)
            if m:
                proof_names.add(m.group(1))
            else:
                m2 = SCALAR_DECL_PATTERN.match(t)
                if m2:
                    proof_names.add(m2.group(1))
        proof_shared = get_shared_problems(proof_flat, proof_names, proof_leaf)
        ctx.problems["PROOF SHARED"].extend(proof_shared)

        # PROOF ORDER
        proof_def_at: dict[str, int] = {}
        proof_def_where: dict[str, str] = {}
        for i, fl in enumerate(proof_flat):
            m = DEF_PATTERN.match(fl.code)
            if m:
                nm = m.group(1).lower()
                if nm not in proof_def_at:
                    proof_def_at[nm] = i
                    proof_def_where[nm] = f"{fl.file}:{fl.line}"
        ctx.problems["PROOF ORDER"].extend(get_order_problems(proof_flat, proof_def_at, proof_def_where))
        # Also: calls to routines defined in tree but not included by the proof.
        for fl in proof_flat:
            code = fl.code
            if code.strip() == "":
                continue
            if DEF_PATTERN.match(code):
                continue
            for m in CALL_PATTERN.finditer(code):
                display_name = m.group(1)
                nm = display_name.lower()
                if nm in proof_def_at:
                    continue
                if nm not in all_def_names:
                    continue
                ctx.problems["PROOF ORDER"].append(
                    f"{proof_leaf}:{fl.line} calls {display_name}, which this tree defines elsewhere but the proof does not include"
                )

        # PROOF ORPHAN BODY
        ctx.problems["PROOF ORPHAN BODY"].extend(get_orphan_problems(proof_flat, proof_leaf))

    # -----------------------------------------------------------------------
    # 4 MODE
    # -----------------------------------------------------------------------
    for fl in flat:
        t = fl.code.strip()
        if not fl.below and MODE_PATTERN.match(t):
            ctx.problems["MODE"].append(
                f"{fl.file}:{fl.line} Blitz-only '{t}' compiled above BLITZ"
            )

    # -----------------------------------------------------------------------
    # 5 LINEENDS
    # -----------------------------------------------------------------------
    for f in all_files:
        data = read_ab3_bytes(f)
        if b"\r" not in data:
            continue
        leaf = f.name
        cr_count = 0
        lf_count = 0
        first_cr = -1
        for i, b in enumerate(data):
            if b == 13:
                if first_cr < 0:
                    first_cr = i
                cr_count += 1
            elif b == 10:
                lf_count += 1
        line_no = 1
        for i in range(first_cr):
            if data[i] == 10:
                line_no += 1
        if lf_count == 0:
            kind = "CR-only, no LF at all"
        elif first_cr + 1 < len(data) and data[first_cr + 1] == 10:
            kind = "CRLF"
        else:
            kind = "bare CR mid-line"
        ctx.problems["LINEENDS"].append(
            f"{leaf} has {cr_count} CR byte(s) (0x0D): {kind}, first at line {line_no}; .ab3 in this tree are LF only"
        )

    # -----------------------------------------------------------------------
    # VARIANTS: the physics variants define the same constants, and the included copy is one of them
    # -----------------------------------------------------------------------
    if inc_dir.exists():
        variants = variant_files(inc_dir)
        constants = {name: read_constants(path) for name, path in variants.items()}
        if constants:
            everything = set().union(*(set(values) for values in constants.values()))
            for name, values in sorted(constants.items()):
                missing = sorted(everything - set(values))
                if missing:
                    ctx.problems["VARIANTS"].append(f"{variants[name].name} does not define: {', '.join(missing)}")
            # A fall must not skip every residue where a landing is allowed: the worst fall step
            # (#MARIO_FALL_MAX + 1) may not exceed #MT_FEET_MIN_SUBTILE.
            for name, values in sorted(constants.items()):
                if "MARIO_FALL_MAX" in values and "MT_FEET_MIN_SUBTILE" in values:
                    if values["MARIO_FALL_MAX"] + 1 > values["MT_FEET_MIN_SUBTILE"]:
                        ctx.problems["VARIANTS"].append(
                            f"{variants[name].name}: the worst fall step (MARIO_FALL_MAX + 1 = {values['MARIO_FALL_MAX'] + 1}) "
                            f"exceeds MT_FEET_MIN_SUBTILE ({values['MT_FEET_MIN_SUBTILE']}): Mario could fall past a landing"
                        )
            if not (inc_dir / COPY_NAME).exists():
                ctx.problems["VARIANTS"].append(f"{COPY_NAME} (the copy the game includes) is missing")
            elif current_variant(inc_dir) is None:
                ctx.problems["VARIANTS"].append(
                    f"{COPY_NAME} matches none of the variant files: run python tools/build_variant.py ntsc (or pal)"
                )

    # -----------------------------------------------------------------------
    # LOOKS: the look files (the NES art, the All-Stars art) define the same constants, the width is fixed (VIEW_W 256 and the display's
    # numbers are in sparkydave.constants.ab3, not the look files), and the included copy is one of them
    # -----------------------------------------------------------------------
    if inc_dir.exists():
        looks = variant_files(inc_dir, "look")
        look_constants = {name: read_constants(path) for name, path in looks.items()}
        if look_constants:
            everything = set().union(*(set(values) for values in look_constants.values()))
            for name, values in sorted(look_constants.items()):
                missing = sorted(everything - set(values))
                if missing:
                    ctx.problems["LOOKS"].append(f"{looks[name].name} does not define: {', '.join(missing)}")
                # The width is not a look's: a look file that defines it again would shadow the one fixed number.
                width_constants = sorted(c for c in values if c in ("VIEW_W", "SCROLL_BIAS", "LOOK_SHIFT_X") or c.startswith(("DISPLAY_", "LOOK_ADJUST_")))
                if width_constants:
                    ctx.problems["LOOKS"].append(
                        f"{looks[name].name} defines {', '.join(width_constants)}: the width is the same in both looks (sparkydave.constants.ab3)"
                    )
            # Every overlay page fits the row keys a page records (pageRowKey$(), #OVERLAY_ROWS_MAX a page): a longer page writes past
            # the array (the NES options page's tenth row against 9 did, and broke the jukebox's scene and the palette page).
            shared_constants = read_constants(inc_dir / "sparkydave.constants.ab3")
            rows_max = shared_constants.get("OVERLAY_ROWS_MAX")
            if rows_max is not None:
                for name, values in sorted(look_constants.items()):
                    page_rows = {row: values.get(row, shared_constants.get(row)) for row in
                                 ("TITLE_ROWS", "OPTIONS_ROWS", "PAUSE_ROWS", "CONTINUE_ROWS", "PAL_PAGE_ROWS")}
                    for row, count in sorted(page_rows.items()):
                        if count is not None and count > rows_max:
                            ctx.problems["LOOKS"].append(
                                f"{looks[name].name}: {row} = {count} is more than OVERLAY_ROWS_MAX ({rows_max}): the page's row keys overflow"
                            )
            if not (inc_dir / LOOK_COPY_NAME).exists():
                ctx.problems["LOOKS"].append(f"{LOOK_COPY_NAME} (the copy the game includes) is missing")
            elif current_variant(inc_dir, "look") is None:
                ctx.problems["LOOKS"].append(
                    f"{LOOK_COPY_NAME} matches none of the look files: run python tools/build_variant.py look nes (or allstars)"
                )

    # -----------------------------------------------------------------------
    # 6 LINE LENGTH
    # -----------------------------------------------------------------------
    # AmiBlitz source lines are limited to 250 characters; a longer one is cut, and the
    # compiler then reports "No terminating quote" or worse on a line that looks right.
    for f in all_files:
        for ln_no, raw in enumerate(read_lines(f), 1):
            if len(raw) > MAX_SOURCE_LINE:
                ctx.problems["LINE LENGTH"].append(
                    f"{f.name}:{ln_no} is {len(raw)} characters (AmiBlitz's limit is {MAX_SOURCE_LINE})"
                )

    # Deduplicate and sort per-check.
    for name in ctx.problems:
        ctx.problems[name] = sorted(set(ctx.problems[name]))

    return ctx


def _run_and_report(root: Path, detail: bool) -> int:
    """Run checks and print the report (mirrors PS1 Test-SourceSplit.ps1 output)."""
    clear_cache()
    ctx = run_checks(root)
    all_files = scan_tree(root)
    flat = flatten_main(root, CheckContext())  # re-flatten just for counts; harmless

    print(f"Source: {root / 'SparkyDave.ab3'}")
    print(f"Flattened: {ctx.flat_count} lines from 1 main file + {len(ctx.include_order)} includes")
    print()

    ok_notes = {
        "INCLUDES": f"{len(ctx.include_order)} includes, all present, none duplicated, none nested, no orphans",
        "XINCLUDE PATHS": f"{ctx.checked_paths} path(s): no '..', no backslash, sub-roots resolve against their own directory",
        "IF FORM": f"{len(all_files)} files, no block If carries a trailing Then",
        "OPERATORS": f"{len(all_files)} files, no title-case Boolean/shift token, no ElseIf",
        "CALL FORM": f"{len(all_files)} files, known math functions use parentheses",
        "SHARED COVERAGE": f"{ctx.global_names_count} globals, every one touched inside a procedure is SHARED",
        "ORPHAN BODY": f"{ctx.flat_count} lines in main tree, no orphaned procedure body",
        "DUPLICATES": f"{ctx.def_count} Statement/Function definitions, each defined once",
        "COMMAND NAMES": f"no Statement or Function reuses one of {ctx.command_count} library command names",
        "ORDER": "no call precedes its definition in flattened order",
        "DIM ORDER": "every file-scope array assignment follows its column-1 Dim",
        "PROOF DIM": f"{ctx.sub_root_count} standalone root(s) in proofs/ and tests/, every file-scope array assignment follows its column-1 Dim",
        "PROOF SHARED": f"{ctx.sub_root_count} standalone root(s) in proofs/ and tests/, every global they declare is SHARED where used",
        "PROOF ORDER": f"{ctx.sub_root_count} standalone root(s) in proofs/ and tests/, no call before its definition, no call to a routine they did not include",
        "PROOF ORPHAN BODY": f"{ctx.sub_root_count} standalone root(s) in proofs/ and tests/, no orphaned procedure body",
        "CONSTANTS": f"{ctx.constant_count} constants, every #NAME used is defined before its use (main tree and {ctx.sub_root_count} standalone roots)",
        "VARIANTS": "the physics variant files define the same constants and keep the fall-step bound; the included copy is one of them",
        "LOOKS": "the look files define the same constants and none of the width's; the included copy is one of them",
        "MODE": "drawing only below BLITZ (VWait: see the main project's BLITZ_CONVENTIONS note)",
        "LINEENDS": f"{len(all_files)} files, zero CR bytes",
        "LINE LENGTH": f"{len(all_files)} files, no line over {MAX_SOURCE_LINE} characters",
    }

    passes = 0
    fails = 0

    for name in CHECK_NAMES:
        probs = ctx.problems[name]
        if not probs:
            note = ok_notes.get(name, "sound")
            print(f"  PASS  {name:<11} {note}")
            passes += 1
        else:
            print(f"  FAIL  {name:<11} {len(probs)} problem(s)")
            if detail:
                for d in probs:
                    print(f"          {d}")
            else:
                for d in probs[:8]:
                    print(f"          {d}")
                if len(probs) > 8:
                    print(f"          ... {len(probs) - 8} more (-Detail to list all)")
            fails += 1

    print()
    # Not a failure: the All-Stars copy is right while testing that look. But the committed copy is the NES one (the project rules
    # section 3), and release_check.py fails on any other; this says so every run, so it is not left in a commit.
    look_now = current_variant(kind="look")
    if look_now is not None and look_now != "nes":
        print(f"NOTE: {LOOK_COPY_NAME} is the {look_now} look. Before committing: python tools/build_variant.py look nes")
    if fails == 0:
        print(f"{passes} check(s) passed. Include structure is sound.")
        print("Style is not checked here: run python tools/format_ab3.py --check. Then compile in UAE.")
        return 0
    print(f"{fails} check(s) FAILED, {passes} passed.")
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="check_source_split",
        description="Structural gate for AmiBlitz .ab3 source split",
    )
    ap.add_argument("--detail", action="store_true", help="List every offending line")
    ap.add_argument("--root", default=None, help="game folder")
    ap.add_argument("--selftest", action="store_true", help="Run built-in fixtures")
    args = ap.parse_args(argv)

    if args.selftest:
        return run_selftest()

    root = Path(args.root) if args.root else Path(__file__).resolve().parent.parent / "game"
    return _run_and_report(root, args.detail)


def run_selftest() -> int:
    """Build deterministic in-memory game trees and verify each check fires.

    Per plan section 4: clean tree (expect 20 PASS), one single-violation tree
    per check. Asserts exact check name and message substring.
    """
    failures = []

    def write_tree(treespec: dict[str, str], root: Path) -> None:
        for leaf, content in treespec.items():
            p = root / leaf
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content.encode("iso-8859-1"))

    # ------------------------------------------------------------------
    # Clean tree: main + 2 includes + 1 proof + 1 tests root.
    # ------------------------------------------------------------------
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_tree({
            "includes/sparkydave.globals.ab3":
                ";; header\nplayerX.w = 0\nplayerY.w = 0\n",
            "includes/sparkydave.constants.ab3":
                ";; const\n#TILE = 16\n",
            "includes/sparkydave.collision.ab3":
                "Statement CheckCollision{}\n  SHARED playerX\n  If playerX = 0 Then playerX = 1\nEnd Statement\n",
            "includes/sparkydave.player.ab3":
                "Statement MovePlayer{}\n  SHARED playerX, playerY\n  playerX = playerX + 1\nEnd Statement\n",
            "SparkyDave.ab3":
                'WBStartup\n'
                'XINCLUDE "includes/sparkydave.constants.ab3"\n'
                'XINCLUDE "includes/sparkydave.globals.ab3"\n'
                'XINCLUDE "includes/sparkydave.collision.ab3"\n'
                'XINCLUDE "includes/sparkydave.player.ab3"\n'
                'BLITZ\n'
                'Statement MainLoop{}\n  CheckCollision{}\n  MovePlayer{}\nEnd Statement\n'
                'AMIGA\nEnd\n',
            "proofs/proof.ab3":
                'XINCLUDE "/includes/sparkydave.constants.ab3"\n'
                'XINCLUDE "/includes/sparkydave.globals.ab3"\n'
                'XINCLUDE "/includes/sparkydave.collision.ab3"\n'
                'Dim arr.w(10)\narr(0) = 1\n',
            "tests/test.ab3":
                'XINCLUDE "/includes/sparkydave.constants.ab3"\n'
                'XINCLUDE "/includes/sparkydave.globals.ab3"\n'
                'XINCLUDE "/includes/sparkydave.player.ab3"\n'
                'Dim arr.w(10)\narr(0) = 2\n',
        }, root)
        clear_cache()
        ctx = run_checks(root)
        for name in CHECK_NAMES:
            if ctx.problems[name]:
                failures.append(f"clean tree: {name} expected PASS, got {len(ctx.problems[name])} problem(s)")

    # ------------------------------------------------------------------
    # One violation tree per check.
    # ------------------------------------------------------------------
    # 1 INCLUDES: missing include target
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_tree({
            "includes/sparkydave.globals.ab3": "playerX.w = 0\n",
            "SparkyDave.ab3": 'XINCLUDE "includes/sparkydave.missing.ab3"\nBLITZ\nEnd\n',
        }, root)
        clear_cache()
        ctx = run_checks(root)
        if not any("target does not exist" in p for p in ctx.problems["INCLUDES"]):
            failures.append("INCLUDES: missing target not detected")

    # 1b XINCLUDE PATHS: '..' in path
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_tree({
            "includes/sparkydave.globals.ab3": "x = 0\n",
            "SparkyDave.ab3": 'XINCLUDE "../includes/sparkydave.globals.ab3"\n',
        }, root)
        clear_cache()
        ctx = run_checks(root)
        if not any("uses '..'" in p for p in ctx.problems["XINCLUDE PATHS"]):
            failures.append("XINCLUDE PATHS: '..' not detected")

    # 1c IF FORM: block If with trailing Then
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_tree({
            "includes/x.ab3": "If flag = 0 Then\nEnd If\n",
            "SparkyDave.ab3": 'XINCLUDE "includes/x.ab3"\nBLITZ\nEnd\n',
        }, root)
        clear_cache()
        ctx = run_checks(root)
        if not any("trailing Then" in p for p in ctx.problems["IF FORM"]):
            failures.append("IF FORM: block If trailing Then not detected")

    # 1d OPERATORS: title-case And
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_tree({
            "includes/x.ab3": "If a = 0 And b = 0 Then\n",
            "SparkyDave.ab3": 'XINCLUDE "includes/x.ab3"\nBLITZ\nEnd\n',
        }, root)
        clear_cache()
        ctx = run_checks(root)
        if not any("title-case 'And'" in p for p in ctx.problems["OPERATORS"]):
            failures.append("OPERATORS: title-case And not detected")

    # 1e CALL FORM: Sqr without parens
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_tree({
            "includes/x.ab3": "x = Sqr y\n",
            "SparkyDave.ab3": 'XINCLUDE "includes/x.ab3"\nBLITZ\nEnd\n',
        }, root)
        clear_cache()
        ctx = run_checks(root)
        if not any("without call parentheses" in p for p in ctx.problems["CALL FORM"]):
            failures.append("CALL FORM: Sqr without parens not detected")

    # 1f SHARED COVERAGE: read global without SHARED
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_tree({
            "includes/sparkydave.globals.ab3": "playerX.w = 0\n",
            "includes/x.ab3": "Statement Foo{}\n  If playerX = 0 Then\nEnd Statement\n",
            "SparkyDave.ab3":
                'XINCLUDE "includes/sparkydave.globals.ab3"\n'
                'XINCLUDE "includes/x.ab3"\nBLITZ\nEnd\n',
        }, root)
        clear_cache()
        ctx = run_checks(root)
        if not any("touches global 'playerX' without SHARED" in p for p in ctx.problems["SHARED COVERAGE"]):
            failures.append("SHARED COVERAGE: read without SHARED not detected")

    # 1g ORPHAN BODY: indented SHARED outside procedure
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_tree({
            "includes/x.ab3": "  SHARED foo\n",
            "SparkyDave.ab3": 'XINCLUDE "includes/x.ab3"\nBLITZ\nEnd\n',
        }, root)
        clear_cache()
        ctx = run_checks(root)
        if not any("indented SHARED outside a procedure" in p for p in ctx.problems["ORPHAN BODY"]):
            failures.append("ORPHAN BODY: indented SHARED not detected")

    # 2 DUPLICATES: same Statement twice
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_tree({
            "includes/x.ab3": "Statement Foo{}\nEnd Statement\nStatement Foo{}\nEnd Statement\n",
            "SparkyDave.ab3": 'XINCLUDE "includes/x.ab3"\nBLITZ\nEnd\n',
        }, root)
        clear_cache()
        ctx = run_checks(root)
        if not any("defined at" in p and "again at" in p for p in ctx.problems["DUPLICATES"]):
            failures.append("DUPLICATES: duplicate Statement not detected")

    # 2b COMMAND NAMES: a Statement named like a library command
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_tree({
            "includes/x.ab3": "Statement SetCycle{}\nEnd Statement\n",
            "SparkyDave.ab3": 'XINCLUDE "includes/x.ab3"\nBLITZ\nEnd\n',
        }, root)
        clear_cache()
        ctx = run_checks(root)
        if not any("is the library command" in p for p in ctx.problems["COMMAND NAMES"]):
            failures.append("COMMAND NAMES: library command name not detected")

    # 3 ORDER: call before definition
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_tree({
            "includes/x.ab3": "Statement Main{}\n  Foo{}\nEnd Statement\nStatement Foo{}\nEnd Statement\n",
            "SparkyDave.ab3": 'XINCLUDE "includes/x.ab3"\nBLITZ\nEnd\n',
        }, root)
        clear_cache()
        ctx = run_checks(root)
        if not any("calls Foo before its definition" in p for p in ctx.problems["ORDER"]):
            failures.append("ORDER: call before definition not detected")

    # 10 DIM ORDER: array assignment before Dim
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_tree({
            "includes/x.ab3": "arr(0) = 1\nDim arr.w(10)\n",
            "SparkyDave.ab3": 'XINCLUDE "includes/x.ab3"\nBLITZ\nEnd\n',
        }, root)
        clear_cache()
        ctx = run_checks(root)
        if not any("before its Dim" in p for p in ctx.problems["DIM ORDER"]):
            failures.append("DIM ORDER: assignment before Dim not detected")

    # PROOF DIM: proof array assignment without Dim
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_tree({
            "includes/x.ab3": "Dim arr.w(10)\n",
            "proofs/proof.ab3": 'arr(0) = 1\nXINCLUDE "/includes/x.ab3"\n',
            "SparkyDave.ab3": 'XINCLUDE "includes/x.ab3"\nBLITZ\nEnd\n',
        }, root)
        clear_cache()
        ctx = run_checks(root)
        if not any("before its Dim" in p for p in ctx.problems["PROOF DIM"]):
            failures.append("PROOF DIM: proof array assignment without Dim not detected")

    # PROOF SHARED: proof reads own global without SHARED
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_tree({
            "includes/x.ab3": "Statement Foo{}\n  If proofVar = 0 Then\nEnd Statement\n",
            "proofs/proof.ab3": 'XINCLUDE "/includes/x.ab3"\nproofVar.w = 0\nFoo{}\n',
            "SparkyDave.ab3": 'XINCLUDE "includes/x.ab3"\nBLITZ\nEnd\n',
        }, root)
        clear_cache()
        ctx = run_checks(root)
        if not any("touches global 'proofVar' without SHARED" in p for p in ctx.problems["PROOF SHARED"]):
            failures.append("PROOF SHARED: proof global without SHARED not detected")

    # PROOF ORDER: proof calls routine defined in tree but not included
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_tree({
            "includes/x.ab3": "Statement Foo{}\nEnd Statement\n",
            "includes/y.ab3": "Statement Bar{}\nEnd Statement\n",
            "proofs/proof.ab3": 'XINCLUDE "/includes/y.ab3"\nFoo{}\n',
            "SparkyDave.ab3":
                'XINCLUDE "includes/x.ab3"\n'
                'XINCLUDE "includes/y.ab3"\nBLITZ\nEnd\n',
        }, root)
        clear_cache()
        ctx = run_checks(root)
        if not any("calls Foo, which this tree defines elsewhere but the proof does not include" in p
                   for p in ctx.problems["PROOF ORDER"]):
            failures.append("PROOF ORDER: call to non-included routine not detected")

    # PROOF ORPHAN BODY: indented SHARED in proof
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_tree({
            "includes/x.ab3": "",
            "proofs/proof.ab3": 'XINCLUDE "/includes/x.ab3"\n  SHARED proofVar\n',
            "SparkyDave.ab3": 'XINCLUDE "includes/x.ab3"\nBLITZ\nEnd\n',
        }, root)
        clear_cache()
        ctx = run_checks(root)
        if not any("indented SHARED outside a procedure" in p for p in ctx.problems["PROOF ORPHAN BODY"]):
            failures.append("PROOF ORPHAN BODY: indented SHARED not detected")

    # MODE: Blitz-only command above BLITZ
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_tree({
            "includes/x.ab3": "Cls\n",
            "SparkyDave.ab3": 'XINCLUDE "includes/x.ab3"\nBLITZ\nEnd\n',
        }, root)
        clear_cache()
        ctx = run_checks(root)
        if not any("compiled above BLITZ" in p for p in ctx.problems["MODE"]):
            failures.append("MODE: Blitz-only command above BLITZ not detected")

    # CONSTANTS: a name never defined, and one used before it is defined
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_tree({
            "includes/x.ab3": """#A = 1
y = #A + #MISSING
z = #LATER
#LATER = 2
#B = #A + 1 ; #NOTCODE
t$ = "#INSTRING"
""",
            "SparkyDave.ab3": """XINCLUDE "includes/x.ab3"
BLITZ
End
""",
        }, root)
        clear_cache()
        ctx = run_checks(root)
        notes = ctx.problems["CONSTANTS"]
        if not any("#MISSING, which is never defined" in p for p in notes):
            failures.append("CONSTANTS: a never-defined constant not detected")
        if not any("#LATER before it is defined" in p for p in notes):
            failures.append("CONSTANTS: a constant used before its definition not detected")
        if any("NOTCODE" in p or "INSTRING" in p for p in notes):
            failures.append("CONSTANTS: a comment or a string literal was treated as code")
        if len(notes) != 2:
            failures.append(f"CONSTANTS: expected 2 problems, got {len(notes)}")

    # LINEENDS: CRLF in a file
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_tree({
            "includes/x.ab3": "x = 1\r\ny = 2\r\n",
            "SparkyDave.ab3": 'XINCLUDE "includes/x.ab3"\nBLITZ\nEnd\n',
        }, root)
        clear_cache()
        ctx = run_checks(root)
        if not ctx.problems["LINEENDS"]:
            failures.append("LINEENDS: CR bytes not detected")

    # LINE LENGTH: a line over 250 characters
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_tree({
            "includes/x.ab3": "x = 1 ; " + "a" * 260 + chr(10),
            "SparkyDave.ab3": 'XINCLUDE "includes/x.ab3"' + chr(10) + "BLITZ" + chr(10) + "End" + chr(10),
        }, root)
        clear_cache()
        ctx = run_checks(root)
        if not ctx.problems["LINE LENGTH"]:
            failures.append("LINE LENGTH: a line over 250 characters not detected")

    # Check count
    if len(CHECK_NAMES) != 21:
        failures.append(f"expected 21 checks, got {len(CHECK_NAMES)}")

    if failures:
        print(f"SelfTest: {len(failures)} failure(s)")
        for f in failures:
            print(f"  FAIL  {f}")
        return 1
    print("SelfTest: clean tree 20 PASS + per-check violation trees all detected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
