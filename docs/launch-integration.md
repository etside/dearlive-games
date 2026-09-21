# Launch / WebView integration [CLIENT API REQUIRED]

Proven DearLive pattern (APK `.env` + Leadercc vendor notes):
1. Host app calls prod API (`https://api.dearlive.pro/api/v1`) → mints launch token
   into the SAME Redis the game server reads (wrong-Redis ⇒ connection error 10001).
2. WebView opens `{GAMES_BASE_URL}/teen-patti-pro/?session=&room=` — dev: this repo's
   `:5002/teen-patti-pro/`; prod: `https://games.dearlive.pro` (or CDN).
3. Client: `POST /api/v1/sessions {launch_token}` (single-use, 2-min TTL) → session;
   WS subscribe; REST bets; server snapshot drives Canvas UI (normalized coords,
   DPR-aware; no fixed viewport assumed — real container metrics still TBC).

Needed from DearLive team: mint endpoint path + Redis schema/key-prefix/TTL +
`GAMES_BASE_URL` value + WebView viewport/safe-area spec + session-token format.
Until then: `MemoryTokenStore` (dev) + `TokenStore` Redis impl interface ready.
