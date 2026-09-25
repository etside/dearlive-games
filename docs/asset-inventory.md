# Asset Inventory — `Assets-all`

Source: `/storage/emulated/0/11labd/Assets-all/` (read-only, never modified)
Scanned: 2026-09-25 · 38 PNGs · 108,605,774 bytes (108 MB)

All 38 files are named with epoch-millisecond timestamps and carry no semantic
name, so classification was done by visual inspection of generated contact
sheets, not by filename. Contact sheets: `working/asset-scan/contact-{1,2,3}.png`.

Every asset is AI-generated and **carries a baked-in "Dreamina" watermark** in
the bottom-right region (see Risks). None is a unified sprite sheet except the
four listed below.

## Summary

| | count |
|---|---|
| Source files | 38 |
| — unified sheets (need splitting) | 4 → **12 assets** |
| — individual assets | 34 |
| **Total target assets** | **46** |
| Game coverage | `teen-patti-pro` only |
| Games with **zero** assets | `greedy-monkey`, `baby-king` |

## Sheets (4 files → 12 assets)

| # | filename | size | type | bg colour | yields | matches spec expectation |
|---|---|---|---|---|---|---|
| 32 | `1790365359113.png` | 1184×3552 | 5 badges stacked | (207,214,224) | `status-waiting`, `status-betting-open`, `status-betting-closed`, `status-dealing`, `status-result` | "status-badges → 5" ✅ |
| 35 | `1790365874766.png` | 1184×3552 | 3 buttons stacked | (76,77,95) | `btn-repeat`, `btn-history`, `btn-auto` | "action-buttons → 3" ✅ |
| 34 | `1790365820434.png` | 2880×1440 | 2 circular buttons | (249,230,190) | `btn-sound-on`, `btn-sound-off` | "sound-buttons → 2" ✅ |
| 33 | `1790365433004.png` | 2880×1440 | 2 items side-by-side | (226,229,234) | `frame-ring-gold-sm`, `suit-spade-gold` | "frames → 2-3" ✅ (2) |

All four predicted counts from the brief match exactly.

## Individuals (34 files)

| # | filename | size | identified as | target asset id |
|---|---|---|---|---|
| 00 | `1790363227097.png` | 2048² | red ornate chair | `seats/seat-red` |
| 01 | `1790363286305.png` | 2048² | blue ornate chair | `seats/seat-blue` |
| 02 | `1790363487752.png` | 2048² | green ornate chair | `seats/seat-green` |
| 03 | `1790363545846.png` | 1760×2464 | "TEEN PATTI PRO" card back | `cards/card-back-teenpatti` |
| 04 | `1790363644734.png` | 2048² | green chip **20** | `chips/chip-20` |
| 05 | `1790363648337.png` | 2048² | blue chip **100** | `chips/chip-100` |
| 06 | `1790363653670.png` | 2048² | purple chip **500** | `chips/chip-500` |
| 07 | `1790363660266.png` | 2048² | red chip **1K** | `chips/chip-1k` |
| 08 | `1790363792241.png` | 3552×1184 | WINNER gold banner | `ui/banner-winner` |
| 09 | `1790363835245.png` | 2880×1440 | "Online" pill, green dot | `avatars/status-online` |
| 10 | `1790363889846.png` | 2880×1440 | HOT badge with flame | `ui/badge-hot` |
| 11 | `1790363893478.png` | 2880×1440 | "Offline" pill, grey | `avatars/status-offline` |
| 12 | `1790363899018.png` | 3552×1184 | "Round: / Room:" pill | `ui/panel-round-room` |
| 13 | `1790363906438.png` | 2880×1440 | "POT:" ornate panel | `ui/panel-pot` |
| 14 | `1790363913031.png` | 2880×1440 | "You: 0" panel | `ui/panel-you` |
| 15 | `1790363940210.png` | 2048² | red back-arrow button | `ui/btn-back` |
| 16 | `1790363943631.png` | 2048² | purple "?" button | `ui/btn-help` |
| 17 | `1790364032350.png` | 1760×2464 | **blank card face, corner rank boxes** | `cards/card-face-template` ★ **Phase 7 input** |
| 18 | `1790364036324.png` | 2048² | black spade | `cards/suit-spade` |
| 19 | `1790364081341.png` | 2048² | red heart | `cards/suit-heart` |
| 20 | `1790364091888.png` | 2048² | red diamond | `cards/suit-diamond` |
| 21 | `1790364140236.png` | 2048² | plain cream card back | `cards/card-back-plain` |
| 22 | `1790364186827.png` | 2048² | "TEEN PATTI PRO" wordmark | `shared/logos/logo-teen-patti-pro` |
| 23 | `1790364239319.png` | 2048² | gold crown ornament | `ui/badge-crown` |
| 24 | `1790364398615.png` | 2048² | white glow ellipse | `shared/glow-white` |
| 25 | `1790364565360.png` | 2048² | gold ornate ring, dark centre | `avatars/avatar-frame-gold` |
| 26 | `1790364685478.png` | 2048² | purple gear button | `ui/btn-settings` |
| 27 | `1790364718733.png` | 2048² | gold ornate ring, bright | `ui/timer-ring` |
| 28 | `1790364763457.png` | 2048² | navy ring with glow | `avatars/avatar-frame-navy` |
| 29 | `1790364768421.png` | 2048² | avatar silhouette placeholder | `avatars/avatar-placeholder` |
| 30 | `1790364839407.png` | 3552×1184 | "YOU" gold badge | `ui/badge-you` |
| 31 | `1790364882531.png` | 3552×1184 | "Offline" pill, navy | `ui/badge-offline-navy` ⚠ dup of #11 |
| 36 | `1790366066962.png` | 2880×1440 | balance panel (coin + 0) | `ui/panel-balance` |
| 37 | `file_00000000991882088c05e573d77d512d.png` | 1254² | **black club** (only true RGBA file) | `cards/suit-club` |

## Risks and blockers found in Phase 1

### 1. The alpha-based splitter in Phase 2 cannot work as specified
**37 of 38 files have no alpha channel at all** — PNG colour type 2 (RGB),
0.0% transparent pixels. The only genuine RGBA file is #37, the club suit
(54.7% transparent).

The brief's splitter detects "transparent regions using alpha channel". That
will find exactly one island (the whole canvas) on 37 files. **Phase 2 must
switch to corner-seeded background flood-fill with a colour tolerance.**

### 2. Backgrounds are not uniformly white
Corner-sampled background per file:

- near-white `(252,253,255)` … `(255,255,255)` — most individuals
- **`(88,88,90)` dark grey** — #08 WINNER banner
- **`(76,77,95)` dark navy** — #35 action-button sheet
- **`(119,128,143)`, `(130,137,153)`** — #30, #31
- **`(185,153,114)` tan** — #36 balance panel
- **`(207,214,224)`, `(226,229,234)`** — sheets #32, #33
- `(249,230,190)` warm cream — sheet #34

Border std-dev is low (3.5–9.4) so a tolerance-based flood fill is viable, but
the seed colour must be sampled per file, not hardcoded. Note the WINNER banner
(#08) and balance panel (#36) have **dark/tan** backgrounds — assets that are
themselves gold-framed will lose their frame if the tolerance is too loose.

### 3. Every asset has a baked-in "Dreamina" watermark
Thousands of watermark-glyph pixels sit in the bottom-right ~28%×13% of every
image. **Vectorising preserves the watermark** — it would ship inside the SVG
and be visible in-game. It must be cropped or masked out before Phase 4, or
Phase 4 must run on a cleaned copy. This is the single biggest quality risk
in the pipeline and needs a decision.

### 4. `greedy-monkey` and `baby-king` have no assets at all
Phase 6 expects a per-game subtree for all three games. Every one of the 38
files is Teen Patti art (Teen Patti card back, wordmark, card-face template,
suits, chips, chairs). **0 wheel assets, 0 monkey/king character art.** Steps
5 and 6 of the original plan (wheel UI: banana/apple/mango options, centre
character) cannot be satisfied from this folder. Those games will keep their
existing art or need a separate source.

### 5. Gaps against the Phase 6 target list
| Expected | Present? |
|---|---|
| `badge-player.svg` | ❌ missing |
| `badge-new.svg` | ❌ missing |
| `card-{rank}-{suit}.svg` ×52 | ⏳ Phase 7 from #17 — feasible |
| `shared/nav/*`, `shared/thumbnails/*` | ❌ missing |
| `greedy-monkey/*`, `baby-king/*` | ❌ missing (see #4) |

### 6. Duplicates needing a keep/drop decision
- #11 `status-offline` (grey) vs #31 `badge-offline-navy` (navy) — same label, two styles
- #18 `suit-spade` vs sheet #33 spade — gold-trimmed variant
- #25 / #27 / #28 — three similar ornate rings; #27 assigned to `timer-ring`
- #03 / #21 — two different card backs (Teen Patti vs plain)
- #09 "Online" and #10 "HOT" are pill badges, not in the Phase 6 list

### 7. Good news
- Chip denominations **20 / 100 / 500 / 1K** match `TeenPattiConfig.denoms = (20, 100, 500, 1000)` exactly.
- `card-face-template` (#17) is a clean blank face with corner rank boxes and
  empty centre — exactly what Phase 7 needs to generate 52 faces.
- All four suits are present (#18 spade, #19 heart, #20 diamond, #37 club).
- All five round-status badges the engine emits are covered by sheet #32.

## Decision needed before Phase 2

1. **Watermark handling** — crop it out, mask it, or accept it in the output?
2. **Background removal** — proceed with corner-flood-fill across all 38?
3. **Duplicates** — keep both variants, or pick one per logical asset?
4. **greedy-monkey / baby-king** — out of scope for this folder, or is there
   another source directory?
