# Assets / UI-UX / Engine parity with DearLive current

Source of truth: Uradhura repo (`platform/assets`, `platform/apps/api`
game drivers + seed themes, `.opencode/skills/teenpatti-pro-ux`).
The game package mirrors them; nothing is redesigned.

## Game names (requested rename)

| # | Catalog (`GET /api/v1/games`) | DearLive code | Engine | Status |
|---|---|---|---|---|
| 1 | Teen Patti Pro (`teen-patti-pro`) | `teen_patti` (alias) | `games/teen_patti_pro/engine.py` | live |
| 2 | Greedy Lion (`greedy-lion`) | `greedy_lion` (alias) | `games/greedy_lion/engine.py:spin` + `wheel_common/service.py` | live (TBC-gated) |
| 3 | Monkey Wheel (`monkey-wheel`, alias `monkey_wheel`) | `greedy_monkey` engine (alias) | `games/greedy/engine.py:spin` + `wheel_common/service.py` | live (TBC-gated) |

Old ids still resolve (aliases) so existing tokens/links keep working.
`entry` + `dearlive_code` are in the catalog response so the DearLive
developer maps Games-section tiles 1:1.

## Assets (Teen Patti Pro — casino-red/gold, DearLive-verified)

Verified 2026-09-23 against `DearLive.apk` (gold/pink-romance brand,
cyan-gold games icon) and BRD G3 (A/B/C, cards, Guessing timer, pots,
chips 20/100/500/1K, Repeat, balance, Back/Help/Sound/Menu, round #,
connection, history — all render in the Canvas client).

Copied from `platform/assets/games/teen-patti/` into
`games/teen_patti_pro/client/assets/` (+ `assets.json` manifest with the
DearLive catalog keys), then re-skinned from navy-tech to the casino
language the Canvas client actually renders — geometry untouched:
seats p1/p2/p3 → casino badges A(red)/B(blue)/C(green) with gold trim;
table bg → green felt + gold rail + gold title; card back → DearLive red
with gold TP monogram. Chips (blue/cyan/gold/violet) and winner-glow were
already casino-gold and are unchanged. Served at
`/teen-patti-pro/assets/*`. Theme + palette in `client/theme.json`
(served at `/teen-patti-pro/theme.json`): teen_patti seed theme
(red gradient bg, gold accent, seat colors) + catalog palette
(deepNavy/electricBlue/violet/cyan) + DearLive gold/rose + live `canvas`
skin section. `client/index.html` uses these as CSS variables;
`client/game.js` fetches `theme.json` at boot and applies `canvas.*`
over compiled defaults — re-skin without touching game logic.
Handover guide for the DearLive team: `docs/handover-dearlive.md`.

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
