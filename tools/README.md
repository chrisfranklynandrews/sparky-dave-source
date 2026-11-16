# tools/

The PC-side checkers for the Blitz source. Python 3, standard library only; run each from the repository root.

| Script | What |
|---|---|
| `check_source_split.py` | The structure: the `XINCLUDE` list and order, duplicate definitions, calls before definitions, array `Dim` before first use, `BLITZ` placement, line endings, the physics and look copies |
| `format_ab3.py` | The style (`--check`, `--write`, `--selftest`); string literals and the PED `XTRA` header are never touched |
| `build_variant.py` | Puts a physics variant or a look in place: `python tools/build_variant.py ntsc\|pal`, `python tools/build_variant.py look nes\|allstars` |
| `ab3_source.py`, `blitz_commands.txt` | Helpers the checkers use (the source reader and the AmiBlitz command list) |
| `hooks/pre-commit` | Runs the first two before a commit; turn it on with `git config core.hooksPath tools/hooks` |

The scripts that make the graphics and the level files in the main project are not published; their output is in the released lha and in `game/includes/`.
