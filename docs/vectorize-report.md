# Vectorize report (Phase 4)

vtracer / pngtosvg / autotrace / svgo are unavailable in this environment
(apt has no autotrace candidate; npm is blocked by a security wrapper that
intercepts its `env` exec). `potrace` and `xmllint` were installed via apt and
are used instead: colour art is quantised and traced as one potrace mask per
palette colour, stacked back-to-front, which is the same construction as
vtracer's `--hierarchical stacked`. An SVG optimiser equivalent to
`svgo --multipass` is implemented in `optimise_svg()`.

- assets in: 41   traced OK: 41   failed: 0
- workers: 4   trace raster width: 512px
- elapsed: 25.1s

| asset | category | trace | paths | raster | XML | viewBox | KB | limit | status |
|---|---|---|---|---|---|---|---|---|---|
| `avatar-frame-navy` | frame-ring | corner | 4 | no | ok | yes | 8.6 | 50KB | OK |
| `avatar-placeholder` | avatar | spline | 7 | no | ok | yes | 36.5 | 50KB | OK |
| `badge-crown` | icon | spline | 3 | no | ok | yes | 17.6 | 20KB | OK |
| `badge-hot` | badge-pill | polygon | 12 | no | ok | yes | 25.7 | 50KB | OK |
| `badge-you` | badge-pill | polygon | 13 | no | ok | yes | 32.4 | 50KB | OK |
| `banner-winner` | panel-wide | spline | 14 | no | ok | yes | 25.3 | 50KB | OK |
| `btn-auto` | button-pill | polygon | 11 | no | ok | yes | 9.0 | 50KB | OK |
| `btn-back` | button-round | spline | 13 | no | ok | yes | 33.0 | 50KB | OK |
| `btn-help` | button-round | spline | 9 | no | ok | yes | 35.1 | 50KB | OK |
| `btn-history` | button-pill | polygon | 12 | no | ok | yes | 30.5 | 50KB | OK |
| `btn-repeat` | button-pill | polygon | 13 | no | ok | yes | 26.0 | 50KB | OK |
| `btn-settings` | button-round | spline | 11 | no | ok | yes | 42.9 | 50KB | OK |
| `btn-sound-off` | button-round | spline | 12 | no | ok | yes | 37.6 | 50KB | OK |
| `btn-sound-on` | button-round | spline | 12 | no | ok | yes | 38.0 | 50KB | OK |
| `card-back-teenpatti` | card-back | corner | 4 | no | ok | yes | 27.8 | 50KB | OK |
| `card-face-template` | card-face | spline | 6 | no | ok | yes | 37.9 | 50KB | OK |
| `chip-100` | chip | spline | 11 | no | ok | yes | 37.3 | 50KB | OK |
| `chip-1k` | chip | spline | 9 | no | ok | yes | 40.6 | 50KB | OK |
| `chip-20` | chip | spline | 11 | no | ok | yes | 33.8 | 50KB | OK |
| `chip-500` | chip | spline | 10 | no | ok | yes | 44.9 | 50KB | OK |
| `decorative-ring` | frame-ring | spline | 6 | no | ok | yes | 37.3 | 50KB | OK |
| `frame-ring-gold-sm` | frame-ring | corner | 6 | no | ok | yes | 42.3 | 50KB | OK |
| `panel-balance` | panel-wide | spline | 13 | no | ok | yes | 17.4 | 50KB | OK |
| `panel-pot` | panel-wide | spline | 13 | no | ok | yes | 32.2 | 50KB | OK |
| `panel-round-room` | panel-wide | spline | 14 | no | ok | yes | 25.9 | 50KB | OK |
| `panel-you` | panel-wide | spline | 13 | no | ok | yes | 26.7 | 50KB | OK |
| `seat-blue` | chair | spline | 8 | no | ok | yes | 22.9 | 50KB | OK |
| `seat-green` | chair | spline | 8 | no | ok | yes | 43.0 | 50KB | OK |
| `seat-red` | chair | corner | 7 | no | ok | yes | 43.9 | 50KB | OK |
| `status-betting-closed` | badge-pill | polygon | 12 | no | ok | yes | 22.7 | 50KB | OK |
| `status-betting-open` | badge-pill | polygon | 11 | no | ok | yes | 27.0 | 50KB | OK |
| `status-dealing` | badge-pill | polygon | 11 | no | ok | yes | 21.6 | 50KB | OK |
| `status-offline` | badge-pill | polygon | 13 | no | ok | yes | 29.9 | 50KB | OK |
| `status-online` | badge-pill | polygon | 12 | no | ok | yes | 23.0 | 50KB | OK |
| `status-result` | badge-pill | polygon | 11 | no | ok | yes | 23.5 | 50KB | OK |
| `status-waiting` | badge-pill | polygon | 11 | no | ok | yes | 28.2 | 50KB | OK |
| `suit-club` | icon | spline | 3 | no | ok | yes | 10.0 | 20KB | OK |
| `suit-diamond` | icon | spline | 3 | no | ok | yes | 14.2 | 20KB | OK |
| `suit-heart` | icon | spline | 3 | no | ok | yes | 10.0 | 20KB | OK |
| `suit-spade` | icon | spline | 2 | no | ok | yes | 7.6 | 20KB | OK |
| `timer-ring` | frame-ring | corner | 5 | no | ok | yes | 44.9 | 50KB | OK |

## Checks applied to every SVG

- `xmllint --noout` — must pass
- no `<image>` element (no embedded raster)
- `viewBox` present
- < 50KB (20KB for icons)
