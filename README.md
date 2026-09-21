# DearLive Games — Game 1: Teen Patti Pro (API-first, server-authoritative)

Plugs into the DearLive host app via its WebView games pattern
(`GAMES_BASE_URL` + Redis launch tokens). Games 2 (Greedy) and 3 (Animal Wheel)
are interface stubs only.

## Quick start (dev/demo — NOT real money: `confirmed=False` default)
```
python3 -m unittest discover -s tests
python3 -m games.teen_patti_pro.api --port 5002 --confirmed &
python3 -m games.teen_patti_pro.ws --port 5003 &
# open: http://127.0.0.1:5002/teen-patti-pro/?session=<id>&room=default
```
Full flow needs a funded wallet + launch token (see tests/test_api.py pattern).

## Layout
`common/` engine iface, lifecycle, envelope, idempotency, wallet/session iface,
audit, HMAC webhooks · `games/teen_patti_pro/` config, engine, service, api, ws,
client · `games/greedy|animal_wheel/` stubs · `admin/` RBAC · `tools/jev_review.py`
+ `tools/jev_reviews/` (all JEV reviews stored) · `tests/` (29) · `docs/`.

## JEV role
Advisory review layer (`http://127.0.0.1:8080` Zen): requirements, design, code,
tests. Stored in `tools/jev_reviews/*.result.json`. Real finds fixed so far:
close-vs-bet race (mutex+invariant), tie unfairness (dead-heat split), unlocked
idempotency claim, uncompensated post-debit failure, unlocked settle credits,
missing timer sweep (implemented). JEV never touches RNG/state/money.

## Rules
TBC items live in config with flags (`docs/business-tbc.md`); `confirmed=False`
blocks money paths. CLIENT API REQUIRED: DearLive wallet endpoints, mint endpoint
+ Redis schema, WebView viewport spec (see docs/wallet-integration.md,
docs/launch-integration.md).
