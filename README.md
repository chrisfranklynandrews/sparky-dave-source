# Sparky Dave -- the Blitz Basic source

Super Mario Bros (NES) worlds 1 and 2, written from scratch for the **Amiga 1200 (AGA)** in **AmiBlitz 3.13**. It is an Amiga-native engine that follows the NES game's behaviour, not an emulator: a dual-playfield display with a two-band parallax, hardware scrolling, a hardware-sprite Mario, blitter objects, and the cartridge's sound engine ported onto Paula. Two looks (the NES art and the Super Nintendo All-Stars art) and two builds (NTSC at 60 ticks a second, PAL retuned to 50).

**Play it:** the released game is on itch.io: <https://chrisfranklyn.itch.io/sparky-dave>.

[`HOW-IT-WORKS.md`](HOW-IT-WORKS.md) is a short tour of the engine. This repository is the program's source, the standing test program and the proofs. It is a **copy** of the source of the main project, made by a script, so it is updated in batches and `SOURCE-REV` says which commit of the main project it is. Pull requests against the copy can be read but will have to be re-made by hand in the main project.

## The graphics are not here

The art, and the level files, are **not in this repository**. They belong to Nintendo or were made from their game, and the tools that made them are not published. The game reads them from the data drawer of the released lha. To build and run the source you take them from that lha:

1. Download the `SparkyDave-<version>.lha` that `SOURCE-REV` names from the itch.io page above.
2. Run `execute sparky-dave:S/get-graphics <path to the lha>`. It unpacks the lha into `RAM:` and tells you to copy its `data` drawer into `game/assets/` (git ignores that folder).
3. Compile and run as [`game/BUILD.md`](game/BUILD.md) says.

Source and lha must be from the same release: the file formats (the area files above all) change between versions.

## What is here

| Path | What |
|---|---|
| `game/SparkyDave.ab3`, `game/includes/` | The game: the main file lists every `XINCLUDE`, the includes are the subsystems. The physics and look files for each variant are in there too |
| `game/launcher/` | The helper that finds out whether the machine is PAL or NTSC |
| `game/tests/test-harness.ab3` | The behaviour tests (every scenario, both physics variants, both looks) |
| `game/proofs/` | The proofs: small standalone programs that tried an idea before it went into the game |
| `S/` | AmigaDOS scripts: compile, run, test, run a proof |
| `tools/` | The source checkers (Python 3, standard library only) |
| `HOW-IT-WORKS.md` | A short tour of how the engine fits together |

### About the proofs

The proofs are kept as a record of how things were worked out. Many of them read pictures that are **not** in the lha (a picture of a level, a scrap of test art), so most of them **will not run** from this repository; read them, do not expect them to start. A proof that reads nothing from `game/assets/`, or only files the lha has, does run with `execute sparky-dave:S/run-proof <name>`.

## Requirements

An Amiga (or UAE set up as one): AGA, 68020, 2 MB Chip RAM, 2 MB Fast RAM, Kickstart 3.x, **AmiBlitz 3.13**. The AmiBlitz manuals are not included; they come with the AmiBlitz install.

## Legal

Super Mario Bros and its graphics, music, level designs and characters belong to Nintendo. This is an unofficial fan project, not endorsed by Nintendo, and it contains no ROM. Parts of the source (the music and sound tables, the level objects, the jukebox scenes) are data taken from the cartridge's game. The [MIT licence](LICENSE) covers the code the author wrote. It does **not** cover that cartridge data or the graphics in the lha, which belong to Nintendo.
