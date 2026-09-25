# Normalize report (Phase 3)

Watermark detection is geometric + colour-density in the bottom-right
region (last 20% w x 12% h, S<15, 100<=V<=200, >=40px cluster); tesseract
is confirmation only. Removal is a crop when the mark falls outside the
content bbox, and a median inpaint when it overlaps the artwork.

- assets processed: **41**
- watermarks detected: **31** / 41
- residual after removal: **3**
- failures: **0**
- dropped variants quarantined to `working/dropped/`: 3
- elapsed: 404.7s

## Per asset

| asset | cat | src | mark px | method | post px | background | out | KB | status |
|---|---|---|---|---|---|---|---|---|---|
| `seat-red` | chair | 2048x2048 | 3172 | inpaint(median30px,feather8px) | 0 | flood tol=8 removed 61% | 1024x1024 | 1045 | OK |
| `seat-blue` | chair | 2048x2048 | 4077 | inpaint(median30px,feather8px) | 0 | flood tol=8 removed 56% | 1024x1024 | 987 | OK |
| `seat-green` | chair | 2048x2048 | 3100 | inpaint(median30px,feather8px) | 0 | flood tol=8 removed 69% | 1024x1024 | 931 | OK |
| `card-back-teenpatti` | card-back | 1760x2464 | 10168 | none | 10168 | flood tol=8 removed 47% | 500x700 | 476 | **RESIDUAL** |
| `card-face-template` | card-face | 1760x2464 | 2295 | inpaint(median30px,feather8px)+hardfill | 0 | REJECTED (tolerance would eat artwork) | 500x700 | 308 | OK |
| `suit-spade` | icon | 1104x1230 | 719 | inpaint(median30px,feather8px) | 6 | done in phase 2 | 512x512 | 183 | OK |
| `suit-heart` | icon | 2048x2048 | 3077 | inpaint(median30px,feather8px) | 0 | flood tol=8 removed 72% | 512x512 | 254 | OK |
| `suit-diamond` | icon | 2048x2048 | 3128 | inpaint(median30px,feather8px) | 0 | flood tol=8 removed 74% | 512x512 | 231 | OK |
| `suit-club` | icon | 1254x1254 | 0 | skipped (verified clean) | 0 | flood tol=8 removed 54% | 512x512 | 240 | OK |
| `chip-20` | chip | 2048x2048 | 3311 | inpaint(median30px,feather8px) | 0 | flood tol=8 removed 63% | 512x512 | 320 | OK |
| `chip-100` | chip | 2048x2048 | 2962 | inpaint(median30px,feather8px) | 0 | flood tol=8 removed 55% | 512x512 | 313 | OK |
| `chip-500` | chip | 2048x2048 | 3451 | inpaint(median30px,feather8px) | 0 | flood tol=8 removed 69% | 512x512 | 313 | OK |
| `chip-1k` | chip | 2048x2048 | 2797 | inpaint(median30px,feather8px) | 0 | flood tol=8 removed 55% | 512x512 | 303 | OK |
| `panel-pot` | panel-wide | 2880x1440 | 42275 | inpaint(median30px,feather8px) | 98446 | flood tol=8 removed 88% | 1024x256 | 197 | **RESIDUAL** |
| `panel-you` | panel-wide | 2880x1440 | 1319 | inpaint(median30px,feather8px) | 0 | flood tol=8 removed 59% | 1024x256 | 272 | OK |
| `panel-round-room` | panel-wide | 3552x1184 | 607 | inpaint(median30px,feather8px) | 0 | flood tol=8 removed 43% | 1024x256 | 291 | OK |
| `panel-balance` | panel-wide | 2880x1440 | 4159 | inpaint(median30px,feather8px) | 0 | flood tol=8 removed 57% | 1024x256 | 166 | OK |
| `avatar-placeholder` | avatar | 2048x2048 | 2567 | inpaint(median30px,feather8px) | 0 | flood tol=8 removed 67% | 512x512 | 140 | OK |
| `avatar-frame-navy` | frame-ring | 2048x2048 | 2952 | inpaint(median30px,feather8px) | 0 | flood tol=8 removed 44% | 1024x1024 | 972 | OK |
| `status-online` | badge-pill | 2880x1440 | 1267 | inpaint(median30px,feather8px) | 0 | flood tol=8 removed 67% | 1024x256 | 249 | OK |
| `status-offline` | badge-pill | 2880x1440 | 1347 | inpaint(median30px,feather8px) | 0 | flood tol=8 removed 61% | 1024x256 | 238 | OK |
| `btn-back` | button-round | 2048x2048 | 2975 | inpaint(median30px,feather8px) | 0 | flood tol=8 removed 45% | 512x512 | 259 | OK |
| `btn-help` | button-round | 2048x2048 | 3009 | inpaint(median30px,feather8px) | 0 | flood tol=8 removed 51% | 512x512 | 294 | OK |
| `btn-settings` | button-round | 2048x2048 | 3122 | inpaint(median30px,feather8px) | 0 | flood tol=8 removed 47% | 512x512 | 322 | OK |
| `badge-hot` | badge-pill | 2880x1440 | 95566 | none | 95566 | flood tol=8 removed 68% | 1024x256 | 207 | **RESIDUAL** |
| `badge-you` | badge-pill | 3552x1184 | 1694 | inpaint(median30px,feather8px) | 0 | flood tol=8 removed 82% | 1024x256 | 253 | OK |
| `badge-crown` | icon | 2048x2048 | 3083 | inpaint(median30px,feather8px) | 0 | flood tol=8 removed 85% | 512x512 | 230 | OK |
| `banner-winner` | panel-wide | 3552x1184 | 3331 | inpaint(median30px,feather8px) | 9 | flood tol=8 removed 50% | 1024x256 | 239 | OK |
| `timer-ring` | frame-ring | 2048x2048 | 3682 | inpaint(median30px,feather8px) | 0 | flood tol=8 removed 61% | 1024x1024 | 1210 | OK |
| `decorative-ring` | frame-ring | 2048x2048 | 3210 | inpaint(median30px,feather8px) | 0 | flood tol=8 removed 48% | 1024x1024 | 1128 | OK |
| `btn-repeat` | button-pill | 1130x600 | 0 | skipped (verified clean) | 0 | done in phase 2 | 1024x256 | 138 | OK |
| `btn-history` | button-pill | 1073x458 | 0 | skipped (verified clean) | 0 | done in phase 2 | 1024x256 | 191 | OK |
| `btn-auto` | button-pill | 1110x1028 | 2610 | inpaint(median30px,feather8px) | 0 | done in phase 2 | 1024x256 | 46 | OK |
| `btn-sound-on` | button-round | 1269x1291 | 0 | skipped (verified clean) | 0 | done in phase 2 | 512x512 | 262 | OK |
| `btn-sound-off` | button-round | 1298x1251 | 4783 | inpaint(median30px,feather8px) | 0 | done in phase 2 | 512x512 | 261 | OK |
| `frame-ring-gold-sm` | frame-ring | 1080x1084 | 0 | skipped (verified clean) | 0 | done in phase 2 | 1024x1024 | 1177 | OK |
| `status-waiting` | badge-pill | 1079x429 | 0 | skipped (verified clean) | 0 | done in phase 2 | 1024x256 | 172 | OK |
| `status-betting-open` | badge-pill | 1083x450 | 0 | skipped (verified clean) | 0 | done in phase 2 | 1024x256 | 198 | OK |
| `status-betting-closed` | badge-pill | 1086x452 | 0 | skipped (verified clean) | 0 | done in phase 2 | 1024x256 | 199 | OK |
| `status-dealing` | badge-pill | 1082x440 | 0 | skipped (verified clean) | 0 | done in phase 2 | 1024x256 | 195 | OK |
| `status-result` | badge-pill | 1079x448 | 0 | skipped (verified clean) | 0 | done in phase 2 | 1024x256 | 188 | OK |

## Category consistency (3.5)

| category | assets | distinct sizes | verdict |
|---|---|---|---|
| avatar | 1 | [(512, 512)] | OK |
| badge-pill | 9 | [(1024, 256)] | OK |
| button-pill | 3 | [(1024, 256)] | OK |
| button-round | 5 | [(512, 512)] | OK |
| card-back | 1 | [(500, 700)] | OK |
| card-face | 1 | [(500, 700)] | OK |
| chair | 3 | [(1024, 1024)] | OK |
| chip | 4 | [(512, 512)] | OK |
| frame-ring | 4 | [(1024, 1024)] | OK |
| icon | 5 | [(512, 512)] | OK |
| panel-wide | 5 | [(1024, 256)] | OK |

## Dropped variants (quarantined, source untouched)

| file | reason | bytes |
|---|---|---|
| `1790364882531.png` | low contrast (28.95 vs 71.29) - Pair 1 | 2,586,624 |
| `1790364036324.png` | flat greyscale spade, sat 1.5 - Pair 2 | 1,738,752 |
| `1790364140236.png` | square 2048x2048, low contrast, unbranded - Pair 4 | 2,555,904 |

## Generated in Phase 6 (not from source)

- `badge-player.svg` (badge-pill) — recolour badge-you gold -> blue, text set at runtime
- `badge-new.svg` (badge-pill) — pill from badge-you, green gradient, text NEW


## Visual QA (contact-sheet review, not counters)

Automated counters reported 3 residuals. Reviewing
`working/asset-scan/normalized-verify2.png` found **more damage than the counters
reported**, because a clean alpha count says nothing about whether the ARTWORK
survived. Recorded rather than shipped silently.

### Watermark removal: PASS
The `豆包 AI 生成` mark is absent from all 41 normalised assets. Verified on the
contact sheet, not by detector return value alone.

### Background removal: 4 assets DAMAGED
The flood fill cannot separate an ornate frame from a dark or saturated
backdrop, so it ate the frame:

| asset | damage | root cause |
|---|---|---|
| `panel-pot` | **severe** — ornate gold frame gone, panel reduced to a gold smear | background (88,88,90) is close to the frame's shadow tones |
| `panel-you` | outer blue oval frame lost, ragged edges | navy frame vs navy panel interior |
| `panel-balance` | top edge chewed, ragged notches | tan background (185,153,114) close to the gold trim |
| `avatar-placeholder` | silhouette over-eroded into a blob | near-white bg floods into the pale silhouette |

These four share one property: a thin, light-coloured ornate element sitting on
a backdrop of similar tone. Same failure mode as the status-badges glow halos in
Phase 2, and the same fix applies -- an explicit mask/box manifest rather than a
heuristic. **Not fixed in this pass.**

### 3.3 background completion: NOT EFFECTIVE
The beige wedge carried over from Phase 2 is still present on `btn-repeat`,
`btn-history`, `btn-auto` and all five `status-*` badges (a light diagonal
streak at the left edge). `fill_foreign_pixels` did not catch it: the function
tests opacity *inside the content bbox*, where the artwork is opaque, so the
`min_frac` gate almost never fires. The logic needs to test the region *outside*
the content bbox. **Not fixed in this pass.**

### Category consistency: PASS
All 12 categories emit exactly one distinct output size. No outliers.
