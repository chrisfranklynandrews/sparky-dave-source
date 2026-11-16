# BUILD.md

How the source here becomes a program that runs. The reference machine is an Amiga 1200 (AGA, 68020, **2 MB Chip RAM, 2 MB Fast RAM**, PAL or NTSC, Kickstart 3.x), usually in UAE. No RTG, no 68060. Kickstarts, Workbench and AmiBlitz are not in this repository.

## One-time setup

1. An Amiga or UAE set up as the reference machine, with Workbench 3.x and **AmiBlitz 3.13** installed (`Work:AmiBlitz3.13/AmiBlitz3` is the path the scripts use; change it in the `S/` scripts if yours differs).
2. Mount this repository as a directory hard disk named `sparky-dave:` (read/write).
3. `execute sparky-dave:S/script-properties` once. The scripts in `S/` need the script bit (`+s`), which git does not keep.
4. Get the graphics (below).

## The graphics

The game reads its pictures and levels from `PROGDIR:data/`, the drawer of the released lha. They are not in this repository. Download the lha from <https://chrisfranklyn.itch.io/sparky-dave> (the release named in `SOURCE-REV`; the source and the files must match) and run:

```
execute sparky-dave:S/get-graphics Work:Downloads/SparkyDave-0.94.lha
```

It unpacks the archive into `RAM:SparkyGraphics` and tells you where to copy its `data` drawer: into `game/assets/` (git ignores it), files and the `areas` and `areas-allstars` drawers alike. It copies nothing itself, so check that your `sparky-dave:` assign is this source before you copy. `S/stage-data`, which the compile scripts run, then copies the files the game loads to `compiled/data/`. Run `get-graphics` again whenever you change to a different release.

## Compile and run

```
execute sparky-dave:S/run-sparky           compile (whichever physics and look are in place) and run
execute sparky-dave:S/compile-sparky       compile only, to compiled/SparkyDaveDev
execute sparky-dave:S/compile-sparky-bin   the four builds, NTSC and PAL in both looks
```

`compile-sparky-bin` makes `compiled/SparkyDaveNTSC`, `SparkyDavePal`, `SparkyDaveNTSCAllStars` and `SparkyDavePalAllStars`, and the launcher's helper `DetectVideo`, through `S/compile-sparky-one <variant> <look> <program>`. Compile errors are in `game/compile-<variant>-<look>.log`. **Fix the first compiler error only:** the compiler's errors cascade, and the rest are noise.

From the IDE: load `sparky-dave:game/SparkyDave.ab3` only (it `XINCLUDE`s `includes/`) and leave the IDE's includes directory unset. Compiling from the IDE with its debugger on is much slower than the scripts' build.

## Start the game

```
cd sparky-dave:compiled
execute SparkyDave            the NES look, the build for this machine's video standard
execute SparkyDave allstars   the All-Stars look
execute SparkyDave pal        force the PAL build (or ntsc)
execute SparkyDave debug      with the debug keys and the report files
```

The game uses `PROGDIR:` only: its data is in `PROGDIR:data/` and its settings in `PROGDIR:SparkyDave.settings`. At start it checks the machine (a 68020, Kickstart 3.0, AGA, enough Chip and Fast RAM) and that every data file is there, and says which is missing.

## The physics variants and the looks

`game/includes/sparkydave.physics.ab3` is the file the game includes: a copy of `sparkydave.physics.ntsc.ab3` or `sparkydave.physics.pal.ab3`. `sparkydave.look.ab3` is likewise a copy of `sparkydave.look.nes.ab3` or `sparkydave.look.allstars.ab3`. **Edit the variant files, never the copy.** The compile scripts switch them for each build and put the committed copies (NTSC, NES) back. On the PC:

```
python tools/build_variant.py ntsc        (or pal)
python tools/build_variant.py look nes    (or allstars)
```

## PC-side checks

Python 3, standard library only; run from the repository root:

```
python tools/check_source_split.py      the structure: include order, calls before definitions, BLITZ placement (about 4 s)
python tools/format_ab3.py --check      the style (about 15 s); --write fixes it
```

`git config core.hooksPath tools/hooks` runs both before each commit.

**"Array not yet dim'd" in UAE with the checker green** means the `sparky-dave:` mount is serving an older copy of the tree: check with `type sparky-dave:game/includes/sparkydave.map.ab3`.

## The behaviour tests

```
execute sparky-dave:S/run-tests
```

compiles `game/tests/test-harness.ab3` and runs it four times (each physics variant in each look), writing `game/tests/test-harness-results-<variant>-<look>.txt` and ending with "All behaviour tests passed" or the failures. One run: `execute sparky-dave:S/run-tests-one pal allstars`. The tests do not draw. A few assertions record a known failure on purpose; the names of those scenarios say so in the test program.

## Proofs

`game/proofs/*.ab3` are standalone programs that tried an idea before it went into the game, kept as a record. `execute sparky-dave:S/run-proof <name>` compiles `game/proofs/<name>.ab3` to `RAM:Proof` and runs it. Most of them read pictures that are not in the lha, so most **will not run** here; see the README.

## Language

AmiBlitz 3.13 syntax is in the manuals that come with the AmiBlitz install (`Amiblitz3.guide`, `BlitzProgrammers.guide`). Two house rules the checkers enforce: keyword casing is significant (`Then`, not `THEN`), and every `Statement` or `Function` must be above its first call in the flattened compile order.
