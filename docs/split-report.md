# Split report (Phase 2)

Background removed by corner-seeded flood fill, not alpha: 37 of 38
source PNGs are colour type 2 (RGB) with no alpha channel.

- min-island-area: 1000 or 0.8% of canvas, whichever is larger  padding: 8px
- sheets processed: 4  assets written: 12
- elapsed: 51.7s

| sheet | size | mode | seed RGB | tol | bg removed | islands kept | frag dropped | expected | status |
|---|---|---|---|---|---|---|---|---|---|
| `1790365359113.png` | 1184x3552 | manifest | (201.1, 208.1, 218.6) | 8 | 60.9% | 5 | 0 | 5 | OK |
| `1790365874766.png` | 1184x3552 | grid | (77.8, 79.7, 95.3) | 8 | 70.5% | 3 | 0 | 3 | OK |
| `1790365820434.png` | 2880x1440 | grid | (159.7, 152.0, 141.0) | 8 | 55.0% | 2 | 0 | 2 | OK |
| `1790365433004.png` | 2880x1440 | grid | (230.4, 233.0, 236.5) | 8 | 63.0% | 2 | 0 | 2 | OK |

## Output

- `working/split/status-badges/status-waiting.png` — 1079x429 (box 53,398 1132,827)
- `working/split/status-badges/status-betting-open.png` — 1083x450 (box 49,1004 1132,1454)
- `working/split/status-badges/status-betting-closed.png` — 1086x452 (box 46,1624 1132,2076)
- `working/split/status-badges/status-dealing.png` — 1082x440 (box 50,2246 1132,2686)
- `working/split/status-badges/status-result.png` — 1079x448 (box 51,2847 1130,3295)
- `working/split/action-buttons/btn-repeat.png` — 1130x600 (box 0,487 1130,1087)
- `working/split/action-buttons/btn-history.png` — 1073x458 (box 56,1552 1129,2010)
- `working/split/action-buttons/btn-auto.png` — 1110x1028 (box 54,2508 1164,3536)
- `working/split/sound-buttons/btn-sound-on.png` — 1269x1291 (box 52,0 1321,1291)
- `working/split/sound-buttons/btn-sound-off.png` — 1298x1251 (box 1556,167 2854,1418)
- `working/split/frames/frame-ring-gold-sm.png` — 1080x1084 (box 218,207 1298,1291)
- `working/split/frames/suit-spade-gold.png` — 1104x1230 (box 1616,187 2720,1417)
