# Deployment (dev topology; prod mirrors with managed Redis/DB + TLS)

- `python3 -m games.teen_patti_pro.api --port 5002 [--confirmed]` (dev ONLY flag).
- `python3 -m games.teen_patti_pro.ws --port 5003 --api-port 5002` (shares service
  when embedded; standalone dev instance otherwise — prod MUST share one service).
- Static client served by API (`/teen-patti-pro/`); prod may use CDN + same API host.
- Scheduler: call `service.sweep()` every 1s (cron/thread/supervisor) for timer expiry.
- Env: `JEV_URL` (reviews), `JEV_API_KEY`, game admin keys via env in prod
  (never bundle secrets in WebView — per APK .env warning).
- Health: `GET /health` (API), WS subscribe smoke, sweep lag metric, settlement DLQ
  (webhook deliveries `pending()`), DB/Redis monitors.
- Replace Memory* stores with Redis/DB impls (same interfaces) before any real traffic.
