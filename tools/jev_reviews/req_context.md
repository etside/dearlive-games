Teen Patti Pro (Game 1 of 3-game platform for DearLive host app). BRD/SRS v1.0 Game 3 (p11-12) + audit evidence. Scope: ONLY Game 1 now; Games 2-3 later via shared interfaces.

BRD CONFIRMED (Game 3):
- 3 positions A/B/C, each with card area; server-synced Guessing/decision countdown; chip denominations (screenshot shows 20/100/500/1K as REFERENCE, configurable); Repeat button; Pot + You/Your Bet amounts; player balance display; avatars/names (should).
- Lifecycle: UPCOMING→BETTING OPEN→CLOSED→RESULT→SETTLED→CLOSED. Server-authoritative result, auditable. Atomic debit, idempotent settlement (UNIQUE settlement.bet_id), immutable wallet txns. Response envelope {success,code,message,data,serverTime,requestId}.
- DB: game_table, game_player_position, card_hand, game_round, game_bet, game_result (+common wallet_account/wallet_transaction, audit_log).

BRD TBC (explicitly unconfirmed):
- G3-BR-01 variant/ranking rules unspecified. G3-BR-02 "appears 3 cards per position" unconfirmed. G3-BR-03 pot contribution, payout, tie handling unspecified. G3-BR-04 timer duration unspecified. G3-BR-05 deck/shuffle/RNG method must be server-side + auditable (mechanism up to us).
- Payout formula/rounding, min/max limits, round durations, RNG fairness approach, auth/wallet source, admin RBAC, viewport/sizes all TBC.

APK AUDIT (DearLive.apk, Flutter, package xyz.lrlive.app):
- Native lib/ STRIPPED (0 .so): Dart game logic unrecoverable. No teen/patti strings anywhere. WebView plugin present (generic P0/j3 smali hits).
- flutter_assets/.env PROVES architecture: API_BASE_URL=https://api.dearlive.pro/api/v1 (dev :6001); GAMES_BASE_URL=https://games.dearlive.pro (dev :5002 serving lrlive-games/ static files) => games are WebView-hosted static bundles.
- Launch-token flow PROVEN: vendor "Leadercc" game server calls prod API; tokens minted server-side into Redis; wrong-Redis mint => "Connection error 10001". Our module must mint/validate tokens against the SAME Redis.
- games-icon.png entry point lives in audio-room menu. Agora live rooms, Firebase/Google auth, RevenueCat, Cloudinary present. Backend dearlive-backend/ NOT available locally.
- Teen Patti gameplay video + UI screenshots NOT FOUND on device (11labd has only APK/PDF/asset-pack). Asset pack has NO Teen Patti table art (only generic banners/icons).

PROPOSED BUILD PLAN UNDER REVIEW:
- New repo dearlive-games: common/ (engine iface, lifecycle, wallet adapter iface, Redis session/token iface, HMAC webhooks, envelope, idempotency) + games/teen_patti_pro/ (deterministic engine, REST, WS, WebView client) + admin hooks + tests + docs. Games 2/3 as empty interface stubs.
- TBC rules implemented as VERSIONED CONFIG with explicit TBC flags + config_version stamped on every settlement; standard Teen Patti defaults (3 cards; trail>pure-seq>seq>color>pair>high; A-K-Q high, 2-3-5 low; single winner pot-takes-all minus configured rake=0 default) used ONLY as clearly-marked unconfirmed defaults requiring business sign-off before real-money use.
- DearLive auth/player/room/wallet reached ONLY via adapter interfaces marked CLIENT API REQUIRED (exact prod paths unknown — zero /api/v1/ strings recoverable from stripped APK).
- JEV (this reviewer) is ADVISORY ONLY: never authoritative for RNG/cards/state/balance/settlement.
