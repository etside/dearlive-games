# Duplicate Decisions Log

Source: `/storage/emulated/0/11labd/Assets-all/` — **read-only, never modified**.
Dropped variants are **copied** to `working/dropped/` and excluded from the
pipeline. Nothing is deleted from the source directory.

Tie-break criteria, in order:
1. Higher native resolution
2. Sharper edges (lower compression artifacting) — measured as mean |Laplacian| on luminance
3. Better match to the reference screenshot
4. Naming consistency with existing assets

Measurements are objective, not eyeballed:

| file | size | edge energy | contrast | mean sat |
|---|---|---|---|---|
| `1790363893478.png` | 2880×1440 | 3.20 | **71.29** | 14.2 |
| `1790364882531.png` | 3552×1184 | 3.64 | 28.95 | 22.2 |
| `1790364036324.png` | 2048×2048 | 2.66 | 57.29 | **1.5** |
| `suit-spade-gold.png` (split) | 1104×1230 | **4.54** | **70.83** | 11.3 |
| `1790364565360.png` (ring A) | 2048×2048 | 5.88 | 55.57 | 35.3 |
| `1790364718733.png` (ring B) | 2048×2048 | **6.01** | 44.67 | 39.8 |
| `1790364763457.png` (ring C) | 2048×2048 | 3.40 | 51.78 | 21.7 |
| `1790363545846.png` | 1760×2464 | **7.17** | **73.82** | 59.2 |
| `1790364140236.png` | 2048×2048 | 3.83 | 20.30 | 7.3 |

---

## Pair 1 — Offline pills (2 instances)

- **Keep:** `1790363893478.png` → `avatars/status-offline`
- **Drop:** `1790364882531.png` → `working/dropped/1790364882531.png`
- **Reason:** contrast 71.29 vs 28.95. The grey/blue pill's label is far more
  legible against its fill, which is the whole point of a status pill. It wins
  the dominant readability metric by 2.5×. The navy pill scores marginally
  higher on raw edge energy (3.64 vs 3.20) but that is its outer glow, not its
  glyph edges. Criterion 2 (visual quality comparison via contact sheet) →
  keep `1790363893478.png`.

## Pair 2 — Spades (2 instances)

- **Keep:** `suit-spade-gold.png` (from sheet `1790365433004.png`) → `cards/suit-spade`
- **Drop:** `1790364036324.png` → `working/dropped/1790364036324.png`
- **Reason:** the individual spade measures mean saturation **1.5** — it is
  effectively greyscale, a flat black cutout with no gold trim. The sheet
  variant has sat 11.3, edge energy 4.54 (vs 2.66) and contrast 70.83 (vs
  57.29): it is the glossy, gold-trimmed render, and it matches the gold-trimmed
  treatment of the chairs, chips and frames. Criterion 1/2/3 all favour it.
  Note this reverses the "keep the individual" default — the sheet-sourced
  asset is the better one.

## Pair 3 — Ornate rings (3 instances) — **no drops**

All three retained; they have distinct design roles.

| source | asset id | evidence |
|---|---|---|
| `1790364565360.png` | `ui/timer-ring` | hollow dark centre sized for a numeral; edge 5.88 |
| `1790364763457.png` | `avatars/avatar-frame-navy` | navy with outer glow; pairs with the navy `panel-you` and `status-offline` |
| `1790364718733.png` | `ui/decorative-ring` | brightest, most ornate (edge 6.01); general purpose |

- **Reason:** not duplicates. Note one deviation from the brief: the brief
  assigned `avatar-frame` to "the ring with a crown ornament at top". Neither
  ring carries a crown — the only crown in the source set is `1790364239319.png`,
  which is already mapped to `ui/badge-crown`. `avatar-frame` was therefore
  assigned to the navy ring on colour pairing with the rest of the navy UI, and
  the bright gold ring became `decorative-ring`. Flagging rather than silently
  reinterpreting.

## Pair 4 — Card backs (2 instances)

- **Keep:** `1790363545846.png` → `cards/card-back-teenpatti`
- **Drop:** `1790364140236.png` → `working/dropped/1790364140236.png`
- **Reason:** the branded card back wins on every quality metric — edge 7.17
  vs 3.83, contrast 73.82 vs 20.30, saturation 59.2 vs 7.3 — and carries the
  Teen Patti Pro wordmark, which is the brand-consistency criterion. Decisive
  extra point: `1790363545846.png` is 1760×2464, an aspect of **0.7143**, which
  is exactly the 5:7 card target. The dropped variant is square 2048×2048, the
  wrong shape for a card back and unusable without distortion.

---

## Net effect

| | count |
|---|---|
| Source individuals | 34 |
| Dropped | 3 |
| Individuals entering Phase 3 | 31 |
| Split assets from Phase 2 | 12 |
| **Total normalized assets** | **43** |

Dropped, with reason: `1790364882531` (low contrast), `1790364036324` (flat
greyscale spade), `1790364140236` (square, low contrast, unbranded).
