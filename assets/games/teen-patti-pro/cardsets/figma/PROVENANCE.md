# Figma Playing Cards (Community) — extracted set

Source: `/storage/emulated/0/Download/Figma.pdf`, single page, 612x792pt.

## What is in here

| Path       | Contents                                   |
| ---------- | ------------------------------------------ |
| `d1/`      | 52 faces — orange/red gradient pips        |
| `d2/`      | 52 faces — grey pips with a red accent     |
| `d3/`      | 52 faces — flat solid pips                 |
| `backs/`   | `back-plain.png` (lattice), `back-monogram.png` (BP monogram) |

156 faces + 2 backs, all at **59x87**.

## How they were extracted

The page embeds a single 1080x2123 raster (`pdfimages -list` reports exactly
one image). Rendering the page at 400 DPI only upscales it 2.07x — edge
sharpness falls from 5.02 to 0.21 mean gradient — so **59x87 is the real
resolution ceiling**. There is no vector card art to recover.

Geometry, measured rather than assumed:

- Columns: white bodies on the grey panel, pitch `(966-70)/12 = 74.667`, width
  59. Cards touch horizontally, so runs are recovered from a scanline inside a
  row and the panel gaps used as separators.
- Rows: full-width grey bands separate the rows. Card row spans are the
  complement of those bands — `[gap_end, next_gap_start)`. The header band
  (y 306-392, holding the two backs) is excluded; it is the same height as a
  card row and would otherwise be mistaken for one.
- Suffixes vary: rows are 86 or 87 px tall, so no crop assumes a fixed height.

## Column order is DESCENDING

The sheets run **A, K, Q, J, 10, 9, 8, 7, 6, 5, 4, 3, 2** left to right — the
Ace is the leftmost column and the rest descend. Row order is hearts, spades,
diamonds, clubs.

This was confirmed by eye on the first and last column of every row before any
crop was named. Assuming the usual ascending order would have mislabelled all
156 cards while still looking plausible — the single most dangerous thing about
this source.

## Relationship to the live art

The live set at `../cards/` is 111x185, cropped from a different sheet. These
are ~53% the width and ~47% the height, so they are a *lower-resolution*
alternative look, not an upgrade.
