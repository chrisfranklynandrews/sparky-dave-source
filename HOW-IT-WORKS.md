# How it works

A short tour for the curious. The idea: **Super Mario Bros's behaviour is the specification, and the engine is Amiga-native**, not an emulator and not a port of the NES code. The physics and timing numbers come from the cartridge (checked against a disassembly for reference values); the logic was written from scratch in AmiBlitz 3.13, and checked by eye and by a test program.

## The shape

```
level data   area files read from disk, one per area (the lha's data/areas)
sim          player physics, objects, collision, at a fixed tick rate
renderer     two Copper lists (HUD and world), dual playfield, blitter objects, Mario as a hardware sprite
sound        the NES sound chip's behaviour, played on Paula
screens      covers, the title and the menus, drawn as an overlay on the back playfield
```

`game/SparkyDave.ab3` is the main file: it lists every include in compile order, sets up the display and runs the main loop. The code is in `game/includes/`, one subsystem a file, and the files never include each other. Files above the single `BLITZ` directive are compiled in Amiga mode (data, file input and output); files below it in Blitz mode (the display and the game). A file name says what is in it: `collision`, `enemies`, `fireballs`, `floaters`, `hud`, `levelend`, `life`, `parallax`, `pipes`, `player`, `sound`, `tileset` and so on.

## The display

- A **4 + 4 bitplane dual playfield**, fetch mode 1: the world and its objects in front, the clouds and hills behind. The back playfield scrolls in two bands at its own speeds, split by the Copper, which is the parallax.
- **Two stacked Copper lists**: a fixed HUD band over the world, and the world below. A third, taller list takes over when Mario goes above the top of the screen.
- The world is **hardware-scrolled over a whole-level bitmap**, double-buffered. Each area is stamped into it once, when it is entered.
- **Mario is a hardware sprite** (an attached 15-colour pair in the All-Stars look). Everything else is a blitter object.
- Memory: the game wants 2 MB of Chip RAM and some Fast RAM; the All-Stars tile sets are kept in Fast RAM. It checks the machine before it opens anything and says what is missing.

## The sim and its timing

The simulation runs at a **fixed tick rate**, separate from the display: 60 ticks a second in the NTSC build (the cartridge's own numbers) and 50 in the PAL build (our own retuned variant, scaled to 50 where the cartridge's PAL version left the NTSC numbers alone). The physics numbers live in two variant files, `sparkydave.physics.ntsc.ab3` and `sparkydave.physics.pal.ab3`; the file the game includes is a plain copy of one of them (`tools/build_variant.py`, or the compile scripts). The sound engine runs at 60 ticks a second on both machines.

## The look

Two looks share one engine: the NES art, and the art of the Super Nintendo's All-Stars remake. A second pair of files, `sparkydave.look.nes.ab3` and `sparkydave.look.allstars.ab3`, holds the few numbers that differ. Both are 256 pixels wide, the cartridge's width. Two physics variants in two looks makes four builds.

## Levels

Every area (a level, a bonus room, a pipe's destination) is one binary **area file**, read from disk a level at a time: the map, a collision class for each tile, and the objects. The files are made from hand-made Tiled maps by tools that are not published; they are in the lha. What the game does with them is in `sparkydave.areaload.ab3`, `sparkydave.areas.*.ab3` and `sparkydave.map*.ab3`.

## Sound

The sound is the cartridge's own sound engine, ported to Blitz, driving a **model of the NES's audio chip** (the APU) that is played on Paula. The music and effect tables are the cartridge's, byte for byte, in `sparkydave.sound.data.ab3`. This is the part of the project written most hands-off, by an AI assistant, working from the disassembly.

## How it was built

New ideas were tried first in small standalone programs, the **proofs** in `game/proofs/`: a scroll method, a Copper list change, a collision check, an optimisation, compiled and measured in UAE before anything in `game/includes/` was touched, then folded in. They are here as the record of what was tried, including the things that were tried and dropped. Most of them read graphics that are not in the lha, so most will not run (see the README).

The standing check is `game/tests/test-harness.ab3`, a batch program that plays scenarios (running, ceilings, walls, palettes, the level order, continues and so on) and writes a pass or fail for each assertion, for both physics variants in both looks. Some assertions record a known gap on purpose.

The source checkers in `tools/` keep the include order, the calls-before-definitions rule and the style honest, because the AmiBlitz compiler has no forward declarations and its errors cascade.
