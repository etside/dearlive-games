# STAGING ASSET QA

Evidence-backed asset checklist (local QA runs; deployed-URL column filled
after Vercel deploy). Status PASS requires: file exists + served live (200,
correct MIME) + browser-loaded + event-wired (audio) or host-mapped (Lottie/GIF).

## Teen Patti Pro

| Asset | Type | Exists | Deployed | Browser-tested | Event-tested | Status |
|---|---|---|---|---|---|---|
| table-bg.svg | SVG | yes | pending URL | yes (render shot) | RENDER | PASS* |
| seat-p1/2/3.svg | SVG | yes | pending URL | yes | RENDER | PASS* |
| card-back.svg | SVG | yes | pending URL | yes | RENDER | PASS* |
| card-face.svg | SVG | yes | pending URL | yes | RENDER | PASS* |
| chip-blue/cyan/gold/violet.svg | SVG | yes | pending URL | yes | RENDER | PASS* |
| winner-glow.svg | SVG | yes | pending URL | yes | RENDER | PASS* |
| theme.json (live canvas skin) | JSON | yes | pending URL | yes (felt applied) | LOAD | PASS* |
| assets.json | JSON | yes | pending URL | yes | LOAD | PASS* |
| bet.wav | WAV 0.14s | yes | pending URL | yes (served audio/wav) | BET_ACCEPTED | PASS* |
| win.wav | WAV 0.75s | yes | pending URL | yes | RESULT(win) | PASS* |
| coin.wav | WAV 0.16s | yes | pending URL | yes | SETTLED(win) | PASS* |
| lose.wav | WAV 0.45s | yes | pending URL | yes | RESULT(lose) | PASS* |
| card_flip.wav | WAV 0.18s | yes | pending URL | yes | new round_id | PASS* |
| click.wav | WAV 0.07s | yes | pending URL | yes | tap | PASS* |
| card_deal.json | Lottie 2s | yes | pending URL | served, host-mapped | DEALING | PASS* |
| chip_glow.json | Lottie 2s | yes | pending URL | served, host-mapped | BET_ACCEPTED | PASS* |
| timer_pulse.json | Lottie 2s | yes | pending URL | served, host-mapped | TIMER≤5s | PASS* |
| coin_effect.json | Lottie 2s | yes | pending URL | served, host-mapped | SETTLED | PASS* |
| win_fireworks.json | Lottie 2s | yes | pending URL | served, host-mapped | WIN | PASS* |
| 5 GIF twins | GIF 18f | yes | pending URL | served, host-mapped | same as Lottie | PASS* |

*PASS on local QA stack; Deployed column flips after the Vercel URL run.
Lottie/GIF play in the DearLive host player (manifest event map); the
static client renders procedural canvas FX on identical transitions.

## Greedy Lion / Monkey Wheel

| Asset | Type | Status |
|---|---|---|
| option icons/colors/HOT (`/api/v1/games/{id}/assets`) | config-driven | PASS* (served live, rendered in canvas shots) |
| generic UI sounds (click/bet/coin fallback) | WAV | PASS* (served; documented fallback, not originals) |
| lion/monkey original animation packs | — | **BLOCKED: not supplied** (fallbacks documented in asset-manifest.json) |

## Bugs found by browser QA (fixed)

1. `Math.Sin` typo killed the wheel render loop (CDP exception evidence).
2. Wheel history snapshots lacked settlement status (now patched on settle).
3. Chrome `ERR_UNSAFE_PORT` on :5002/:5061 (QA uses :8901/:8902; Vercel uses standard ports).

## Performance

Master pack 6.7MB total; largest single file 562KB GIF (host-loaded on
demand, never render-blocking). No duplicates. No global preloading.
