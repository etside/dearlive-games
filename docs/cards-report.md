# Cards report (Phase 7)

52 card faces generated from the Phase 4 vector output.

- `node scripts/generate-cards.mjs --out assets/games/teen-patti-pro/cards`
- 13 ranks x 4 suits = **52/52**, none missing
- valid XML (`xmllint --noout`): **52/52**
- embedded raster: **0**
- `viewBox` present: **52/52**
- size: max **38.6 KB** (`card-10-diamond`), min 27.3 KB -- all under the
  50 KB `card-face` limit. Total 1.6 MB.
- no `<image>`, no external references: each file is self-contained

## Construction

Each card is the traced blank `card-face-template.svg` plus:
- rank text top-left with the suit glyph beneath it
- the same pair rotated 180 degrees at bottom-right
- a large centre suit at ~40% scale, 0.92 opacity
- spade/club `#1a1a1f`, heart/diamond `#c8102e`

The suit is defined once in `<defs>` and drawn three times with `<use>`.
Inlining it three times was the first implementation and produced **114 KB per
card** -- 5x over the limit, purely from triplicated path data.

`card-face-template.svg` was re-traced for this phase: a flat cream card with a
gold border does not need 10 colours at a fine trace width. 37.9 KB -> 12.7 KB.
