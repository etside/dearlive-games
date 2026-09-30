# Asset Validation Report

Generated 2026-09-30 against https://api.ura-dhura.com. Every path below is extracted
from `games/teen_patti_pro/client/game.js` -- the authoritative list is what the client can request,
not what sits on disk. Dynamic constructions (`chipArt`, `cardArt`) are expanded for every
denomination the config can name and every rank/suit the engine can deal.

Checks per asset: **A.** HTTP 200 · **B.** non-zero bytes · **C.** SVG parses as XML / PNG magic ·
**D.** no external references · **E.** intrinsic size present.

## SAFE (used in game)

| Path | Size | Type |
|------|------|------|
| `/assets/games/teen-patti-pro/avatars/avatar-frame-navy.svg` | 8 KB | SVG |
| `/assets/games/teen-patti-pro/avatars/avatar-placeholder.svg` | 36 KB | SVG |
| `/assets/games/teen-patti-pro/avatars/decorative-ring.svg` | 37 KB | SVG |
| `/assets/games/teen-patti-pro/avatars/frame-ring-gold-sm.svg` | 42 KB | SVG |
| `/assets/games/teen-patti-pro/avatars/timer-ring.svg` | 44 KB | SVG |
| `/assets/games/teen-patti-pro/cards/badge-crown.svg` | 17 KB | SVG |
| `/assets/games/teen-patti-pro/cards/card-10-club.png` | 29 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-10-diamond.png` | 28 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-10-heart.png` | 28 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-10-spade.png` | 28 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-2-club.png` | 22 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-2-diamond.png` | 21 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-2-heart.png` | 22 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-2-spade.png` | 21 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-3-club.png` | 24 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-3-diamond.png` | 22 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-3-heart.png` | 23 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-3-spade.png` | 22 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-4-club.png` | 24 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-4-diamond.png` | 23 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-4-heart.png` | 23 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-4-spade.png` | 22 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-5-club.png` | 25 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-5-diamond.png` | 24 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-5-heart.png` | 24 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-5-spade.png` | 23 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-6-club.png` | 26 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-6-diamond.png` | 25 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-6-heart.png` | 25 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-6-spade.png` | 24 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-7-club.png` | 29 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-7-diamond.png` | 26 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-7-heart.png` | 26 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-7-spade.png` | 26 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-8-club.png` | 27 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-8-diamond.png` | 26 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-8-heart.png` | 26 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-8-spade.png` | 26 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-9-club.png` | 29 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-9-diamond.png` | 28 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-9-heart.png` | 28 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-9-spade.png` | 27 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-A-club.png` | 27 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-A-diamond.png` | 25 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-A-heart.png` | 26 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-A-spade.png` | 25 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-J-club.png` | 45 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-J-diamond.png` | 46 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-J-heart.png` | 45 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-J-spade.png` | 44 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-K-club.png` | 45 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-K-diamond.png` | 46 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-K-heart.png` | 46 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-K-spade.png` | 45 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-Q-club.png` | 48 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-Q-diamond.png` | 47 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-Q-heart.png` | 46 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-Q-spade.png` | 46 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-back-teenpatti.png` | 57 KB | PNG |
| `/assets/games/teen-patti-pro/cards/card-face-template.svg` | 12 KB | SVG |
| `/assets/games/teen-patti-pro/cards/suit-club.svg` | 10 KB | SVG |
| `/assets/games/teen-patti-pro/cards/suit-diamond.svg` | 14 KB | SVG |
| `/assets/games/teen-patti-pro/cards/suit-heart.svg` | 10 KB | SVG |
| `/assets/games/teen-patti-pro/cards/suit-spade.svg` | 7 KB | SVG |
| `/assets/games/teen-patti-pro/seats/seat-blue.svg` | 22 KB | SVG |
| `/assets/games/teen-patti-pro/seats/seat-green.svg` | 43 KB | SVG |
| `/assets/games/teen-patti-pro/seats/seat-red.svg` | 43 KB | SVG |
| `/assets/games/teen-patti-pro/ui/badge-hot.svg` | 25 KB | SVG |
| `/assets/games/teen-patti-pro/ui/badge-you.svg` | 32 KB | SVG |
| `/assets/games/teen-patti-pro/ui/banner-winner.svg` | 25 KB | SVG |
| `/assets/games/teen-patti-pro/ui/btn-auto.svg` | 8 KB | SVG |
| `/assets/games/teen-patti-pro/ui/btn-back.svg` | 32 KB | SVG |
| `/assets/games/teen-patti-pro/ui/btn-help.svg` | 35 KB | SVG |
| `/assets/games/teen-patti-pro/ui/btn-history.svg` | 1 KB | SVG |
| `/assets/games/teen-patti-pro/ui/btn-repeat.svg` | 25 KB | SVG |
| `/assets/games/teen-patti-pro/ui/btn-settings.svg` | 969 B | SVG |
| `/assets/games/teen-patti-pro/ui/btn-sound-off.svg` | 37 KB | SVG |
| `/assets/games/teen-patti-pro/ui/btn-sound-on.svg` | 37 KB | SVG |
| `/assets/games/teen-patti-pro/ui/game-toolbar-frame.svg` | 709 B | SVG |
| `/assets/games/teen-patti-pro/ui/panel-balance.svg` | 17 KB | SVG |
| `/assets/games/teen-patti-pro/ui/panel-pot.svg` | 32 KB | SVG |
| `/assets/games/teen-patti-pro/ui/panel-round-room.svg` | 25 KB | SVG |
| `/assets/games/teen-patti-pro/ui/panel-you.svg` | 26 KB | SVG |
| `/assets/games/teen-patti-pro/ui/status-betting-closed.svg` | 22 KB | SVG |
| `/assets/games/teen-patti-pro/ui/status-betting-open.svg` | 26 KB | SVG |
| `/assets/games/teen-patti-pro/ui/status-dealing.svg` | 21 KB | SVG |
| `/assets/games/teen-patti-pro/ui/status-offline.svg` | 29 KB | SVG |
| `/assets/games/teen-patti-pro/ui/status-online.svg` | 23 KB | SVG |
| `/assets/games/teen-patti-pro/ui/status-result.svg` | 23 KB | SVG |
| `/assets/games/teen-patti-pro/ui/status-waiting.svg` | 28 KB | SVG |
| `/assets/teen-patti/background/palace-background.svg` | 332 KB | SVG |
| `/assets/teen-patti/branding/teen-patti-pro-logo.svg` | 182 KB | SVG |
| `/assets/teen-patti/cards/card-front.svg` | 33 KB | SVG |
| `/assets/teen-patti/chips/chip-100.svg` | 39 KB | SVG |
| `/assets/teen-patti/chips/chip-100k.svg` | 37 KB | SVG |
| `/assets/teen-patti/chips/chip-10k.svg` | 37 KB | SVG |
| `/assets/teen-patti/chips/chip-1k.svg` | 32 KB | SVG |
| `/assets/teen-patti/chips/chip-20.svg` | 37 KB | SVG |
| `/assets/teen-patti/chips/chip-500.svg` | 40 KB | SVG |
| `/assets/teen-patti/chips/chip-50k.svg` | 38 KB | SVG |
| `/assets/teen-patti/icons/coin.svg` | 30 KB | SVG |
| `/assets/teen-patti/icons/trophy.svg` | 76 KB | SVG |
| `/assets/teen-patti/navigation/back.svg` | 49 KB | SVG |
| `/assets/teen-patti/navigation/clock.svg` | 70 KB | SVG |
| `/assets/teen-patti/navigation/gear.svg` | 61 KB | SVG |
| `/assets/teen-patti/navigation/help.svg` | 52 KB | SVG |
| `/assets/teen-patti/seats/seat-blue.svg` | 78 KB | SVG |
| `/assets/teen-patti/seats/seat-green.svg` | 68 KB | SVG |
| `/assets/teen-patti/seats/seat-red.svg` | 74 KB | SVG |
| `/assets/teen-patti/status/status-hot.svg` | 65 KB | SVG |
| `/assets/teen-patti/status/status-offline.svg` | 48 KB | SVG |
| `/assets/teen-patti/status/status-online.svg` | 65 KB | SVG |
| `/assets/teen-patti/ui/btn-repeat.svg` | 55 KB | SVG |
| `/assets/teen-patti/ui/panel-blue.svg` | 949 B | SVG |
| `/assets/teen-patti/ui/panel-green.svg` | 949 B | SVG |
| `/assets/teen-patti/ui/panel-red.svg` | 949 B | SVG |
| `/assets/teen-patti/ui/round-room-panel.svg` | 117 KB | SVG |
| `/assets/teen-patti/ui/winner-banner.svg` | 106 KB | SVG |

### SKIPPED (broken, not used)

_None. Every referenced asset passed every check._

### Summary
- Total assets referenced: 118
- Safe: 118
- Skipped: 0
- Game renders without broken visuals: YES

> Correction to the usual assumption: the client reads from **two** mounts --
> `/assets/teen-patti/` (PAL: background, navigation, icons, cards, seats, chips, panels)
> and `/assets/games/teen-patti-pro/` (ART_BASE: HUD, card faces, avatar rings).
> There is no `/assets/teen-patti-pro/` mount; every path under it 404s. Card faces
> are PNG crops (`card-K-diamond.png`), not SVGs.
