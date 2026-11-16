#!/usr/bin/env python3
"""Choose the physics variant and the look the game is built with.

    python tools/build_variant.py ntsc      copy sparkydave.physics.ntsc.ab3 over sparkydave.physics.ab3
    python tools/build_variant.py pal       the same for the PAL file
    python tools/build_variant.py           say which variant sparkydave.physics.ab3 is now
    python tools/build_variant.py --check   exit 1 if it matches none of the variant files

The same for the look (the NES art, or the All-Stars art: a second axis next to the physics, notes:
the main project's LOOK_VARIANTS note), with the word look first:

    python tools/build_variant.py look nes        copy sparkydave.look.nes.ab3 over sparkydave.look.ab3
    python tools/build_variant.py look allstars   the same for the All-Stars file
    python tools/build_variant.py look            say which look sparkydave.look.ab3 is now
    python tools/build_variant.py look --check    exit 1 if it matches none of the look files

sparkydave.physics.ab3 is the file game/SparkyDave.ab3 includes. It is a plain copy of
one of the variant files (the pal-ntsc-variants plan (git history), the main project's PHYSICS_VARIANTS note),
kept in git so a fresh checkout compiles. Edit the variant files, never the copy.

This module is also where tools/check_source_split.py gets its idea of what a variant is, so the
naming rule, the discovery of the files and the "is the copy one of them" test live here once.
"""

from __future__ import annotations

import re
import argparse
import sys
from pathlib import Path

INCLUDES = Path(__file__).resolve().parent.parent / "game" / "includes"

# The two axes. Each has a copy the game includes, and one file for each of its variants.
KINDS = ("physics", "look")
COPY_NAMES = {kind: f"sparkydave.{kind}.ab3" for kind in KINDS}
COPY_NAME = COPY_NAMES["physics"]
LOOK_COPY_NAME = COPY_NAMES["look"]

# sparkydave.<kind>.<name>.ab3, but not the copy itself.
VARIANT_PATTERNS = {kind: re.compile(rf"^sparkydave\.{kind}\.(?!ab3$)[a-z]+\.ab3$") for kind in KINDS}
VARIANT_PATTERN = VARIANT_PATTERNS["physics"]
CONSTANT_PATTERN = re.compile(r"^#([A-Za-z0-9_]+)\s*=\s*(-?\$?[0-9a-fA-F]+)", re.M)


def is_variant_file(name: str, kind: str = "physics") -> bool:
    return VARIANT_PATTERNS[kind].match(name) is not None


def is_any_variant_file(name: str) -> bool:
    """A variant file of either axis (the include-order check lets these sit in includes/ unreferenced)."""
    return any(is_variant_file(name, kind) for kind in KINDS)


def variant_files(includes: Path = INCLUDES, kind: str = "physics") -> dict[str, Path]:
    """Variant name (ntsc, pal; nes, allstars) to its file, in name order."""
    return {path.name.split(".")[2]: path for path in sorted(includes.glob(f"sparkydave.{kind}.*.ab3")) if is_variant_file(path.name, kind)}


def current_variant(includes: Path = INCLUDES, kind: str = "physics") -> str | None:
    """The variant the copy is byte-identical to, or None."""
    copy = includes / COPY_NAMES[kind]
    if not copy.exists():
        return None
    data = copy.read_bytes()
    for name, path in variant_files(includes, kind).items():
        if path.read_bytes() == data:
            return name
    return None


def read_constants(path: Path) -> dict[str, int]:
    """Every #NAME = number in a variant file (decimal, negative or $hex)."""
    values: dict[str, int] = {}
    for name, raw in CONSTANT_PATTERN.findall(path.read_text(encoding="utf-8", errors="replace")):
        sign = -1 if raw.startswith("-") else 1
        digits = raw.lstrip("-")
        values[name] = sign*(int(digits[1:], 16) if digits.startswith("$") else int(digits))
    return values


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Copy a physics variant (ntsc, pal) or, after the word look, a look (nes, allstars) over the file the game includes.",
        epilog="With no name it says which is in place; --check fails if the copy matches no variant file.")
    parser.add_argument("words", nargs="*", metavar="[look] NAME", help="ntsc or pal; or look, then nes or allstars")
    parser.add_argument("--check", action="store_true", help="fail if the copy matches no variant file")
    args = parser.parse_args(argv[1:])
    argv = argv[:1] + args.words + (["--check"] if args.check else [])
    kind = "physics"
    if len(argv) > 1 and argv[1] == "look":
        kind = "look"
        argv = argv[:1] + argv[2:]
    copy_name = COPY_NAMES[kind]
    found = variant_files(kind=kind)
    if not found:
        print(f"no variant files (sparkydave.{kind}.<name>.ab3) in", INCLUDES)
        return 1
    now = current_variant(kind=kind)
    word = "look" if kind == "look" else "variant"
    prefix = "look " if kind == "look" else ""
    if len(argv) == 1:
        print(f"{copy_name} is the", now if now else "UNKNOWN (matches no variant file)", word)
        return 0
    if argv[1] == "--check":
        if now is None:
            print(f"{copy_name} matches no variant file; run: python tools/build_variant.py {prefix}" + " or ".join(found))
            return 1
        print(f"{copy_name} is the", now, word)
        return 0
    name = argv[1].lower()
    if name not in found:
        print(f"unknown {word}", name, "- have:", ", ".join(found))
        return 2
    (INCLUDES / copy_name).write_bytes(found[name].read_bytes())
    print(f"{copy_name} is now the", name, word)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
