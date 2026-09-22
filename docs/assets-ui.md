# Assets / UI-UX / Engine parity with DearLive current

Source of truth: Uradhura repo (`platform/assets`, `platform/apps/api`
game drivers + seed themes, `.opencode/skills/teenpatti-pro-ux`).
The game package mirrors them; nothing is redesigned.

## Game names (requested rename)

| # | Catalog (`GET /api/v1/games`) | DearLive code | Engine | Status |
|---|---|---|---|---|
| 1 | Teen Patti Pro (`teen-patti-pro`) | `teen_patti` (alias) | `games/teen_patti_pro/engine.py` | live |
| 2 | Greedy Monkey (`greedy-monkey`) | `greedy_monkey` (alias; legacy `greedy` too) | `games/greedy/engine.py:spin` | planned |
| 3 | Baby King (`baby-king`) | `food_wheel` (alias; legacy `animal-food-wheel`, `food-wheel` too) | `games/animal_wheel/engine.py:spin` | planned |

Old ids still resolve (aliases) so existing tokens/links keep working.
`entry` + `dearlive_code` are in the catalog response so the DearLive
developer maps Games-section tiles 1:1.

## Assets (Teen Patti Pro — exact copies)

Copied verbatim from `platform/assets/games/teen-patti/` into
`games/teen_patti_pro/client/assets/` (+ `assets.json` manifest with the
DearLive catalog keys). Served at `/teen-patti-pro/assets/*`:

cards back/face, chips blue/cyan/gold/violet, winner-glow effect,
seats p1/p2/p3, table bg. Theme + palette in `client/theme.json`
(served at `/teen-patti-pro/theme.json`): teen_patti seed theme
(red gradient bg, gold accent, seat colors) + catalog palette
(deepNavy/electricBlue/violet/cyan). `client/index.html` uses these
as CSS variables; Canvas logic untouched.

Greedy Monkey / Baby King have NO bundled art in DearLive current —
wheel segments are operator-configured per option (`icon` emoji +
`colorHex`, seed `gameThemeData`). The developer supplies those via
their existing game-config API; the engines render whatever options
they receive. Do not invent art here.

## UI-UX (teenpatti-pro-ux skill, applied)

One primary action per view; dim (never hide) non-actionable states;
branded card backs; staggered 60ms deal fly-in; chip-glow on acting seat;
gold highlight on active seat; showdown flip → grade banner; server-tick
batching, transform/opacity-only animations; skill-game language only
(no real-money_copy). UX engine events: EV_DEAL / EV_ACT / EV_FLIP /
EV_WIN / EV_TICK (see `client/theme.json:uxEvents`).

## Engine (exact same logic)

- Teen Patti Pro: 3 seats × 3 cards, deterministic deal from round seed,
  ranking trail > pure_seq > seq > color > pair > high, A-2-3 valid low
  straight — identical to `TeenPattiDriver` (same hierarchy, same Ace-low
  rule, same FairRandom-style deterministic shuffle family).
- Greedy Monkey / Baby King: `common/wheel.py:wheel_outcome` mirrors
  `WheelDriver.generateOutcome` exactly — HMAC-SHA256 float from
  `{serverSeed, clientSeed, nonce}` (`{clientSeed}:{nonce}:{instance}`
  message, 7-byte float), weight-proportional pick, landing angle =
  segment base + jitter (`instance:1` float × segment × 0.9).
  Parity tests: `tests/test_dearlive_parity.py`.
