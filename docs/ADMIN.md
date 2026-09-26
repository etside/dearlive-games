# Admin Guide — Teen Patti Pro

Everything an operator or administrator needs to run the game: how to get an
API key, what each role may do, what every endpoint changes, and what happens
when the database is not there.

## 1. The one thing to understand first

**Admin data lives in Postgres. The game does not fall back to defaults for
it.** If `DATABASE_URL` is not set, every `/api/v1/admin/*` route answers
`503` with a reason. This is deliberate. A dashboard of fabricated zeros looks
exactly like a real quiet trading day, and somebody will act on it.

The single exception is player appearance (§7), which degrades to the default
icon because it sits in the table-rendering path — a database outage must not
blank out every player's avatar.

## 2. Provisioning an API key

Auth is by API key, set by whoever administers the deployment. There is no
`superadmin` role; the ladder is `admin > operator > auditor`.

```bash
# In the server environment. Do not commit this.
export GAME_ADMIN_KEYS="key-for-admin:admin,key-for-operator:operator,key-for-auditor:auditor"
```

Format: `key:role` pairs, comma separated. Generate real keys with something
unpredictable — these are bearer credentials:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Send it in the `X-Admin-Key` header:

```bash
curl -H "X-Admin-Key: $ADMIN_KEY" https://games.example.com/api/v1/admin/whoami
```

| Role | May do |
| --- | --- |
| `auditor` | Every read. No writes. |
| `operator` | Reads + **table actions**: start/close/result/settle a round, cancel one, and write per-game config. No money, profit, packages, or platform settings. |
| `admin` | Everything, including profit/risk, platform settings, packages, player overrides. |

A key with no role, or an unknown role, is refused. So is a request with no
key at all (`403`).

### Optional PIN login

For a human at a browser console, `POST /api/v1/operator/auth` with a PIN works
alongside API keys. It needs two things configured, and is off by default:

```bash
OPERATOR_PIN_HASH=<bcrypt hash>   # required
OPERATOR_TOKEN_SECRET=<secret>    # required, signs the session token
```

Generate the hash with:

```bash
python3 -c "import bcrypt; print(bcrypt.hashpw(b'YOUR-PIN', bcrypt.gensalt()).decode())"
```

With `OPERATOR_PIN_HASH` unset the route answers `501` and API keys remain the
only path. That is the intended default: a PIN in an env file is weaker than a
key, and an unset PIN is honest about being unconfigured rather than silently
accepting anything. Two further `501`s are deliberate and point at the API-key
path instead — `bcrypt` not installed, and `OPERATOR_TOKEN_SECRET` unset (a
token cannot be signed without it).

## 3. Pointing the game at your API

The client finds the API in this order, first hit wins:

1. `?api=` query parameter — `.../?api=https://games.example.com`
2. `VITE_API_BASE` (React shell builds only)
3. Same origin as the page

Same-origin is the default because it is the one that needs no configuration:
serve the client and the API from one host and the game just works. Use `?api=`
when the game client is served from a CDN and the API is not.

## 4. Endpoints

Read paths take `auditor` or higher. Writes take `admin` unless noted.

### Profit and risk

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/api/v1/admin/profit-risk` | Active config + last 10 versions |
| `PUT` | `/api/v1/admin/profit-risk` | Saves a new version. Never edits in place. |
| `POST` | `/api/v1/admin/profit-risk/simulate` | Runs the seeded model. `auditor` is enough. |

Every save is a new row with its own version, so a bad change is rolled back by
pointing at the previous version rather than by trying to remember what it was.

`POST .../simulate` takes `{"rounds": 10000}` and optional overrides. It returns
`expected_profit`, `roi_pct`, `max_exposure` and `risk_level`, plus a
`model` field stating plainly that this is **not a measurement**. It is a
seeded model over your own parameters. Treat it as a sanity check on a
direction, never as an expected result.

```bash
curl -X POST -H "X-Admin-Key: $ADMIN_KEY" -H 'Content-Type: application/json' \
  -d '{"rounds": 50000, "base_house_edge_pct": 6}' \
  https://games.example.com/api/v1/admin/profit-risk/simulate
```

### Scheduling a change

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/api/v1/admin/scheduled-changes` | Filter: `target_type`, `target_id`, `status`, `limit` |
| `POST` | `/api/v1/admin/scheduled-changes` | `{target_type, target_id, payload, effective_at, reason}` |
| `DELETE` | `/api/v1/admin/scheduled-changes/{change_id}` | Cancels a pending change |
| `POST` | `/api/v1/admin/scheduled-changes/apply` | Runs the sweep now |

`target_type` is one of `profit_risk`, `game_config`, `settings`, `package` —
enforced by the schema, so the sweep cannot be handed a target it does not
understand.

The sweep runs every 60 seconds in the API process, plus on demand via
`.../apply`. It is safe to run from several processes at once: a row is claimed
with a conditional `UPDATE` from `PENDING` to `APPLYING`, and only the process
whose update matched applies it. The boot banner prints
`config-sweeper=on|off`; it is `off` when there is no database.

A change that **fails** is marked `FAILED` with the reason and is never
retried automatically. A payload that violates a constraint will violate it
every time, and silently retrying it would bury the real error under a log full
of identical failures.

A scheduled change never affects a round already in play: a round snapshots
its config version when it starts.

### Players

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/api/v1/admin/player-overrides` | Filter: `player_id`, `limit` |
| `POST` | `/api/v1/admin/player-overrides` | `player_id`, `token_delta`, `house_edge_pct`, `reason` |
| `DELETE` | `/api/v1/admin/player-overrides/{override_id}` | Revokes |

`reason` is **required** by the schema. A balance adjustment with no stated
cause cannot be audited later, and this is the exact row an auditor will ask
about.

Deletes are soft everywhere in this API. Packages are archived and overrides
revoked, never removed: both can already be referenced by wallet transactions,
and hard-deleting either would orphan money records.

### Packages and platform

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/api/v1/admin/packages` | `?active=true` to hide archived |
| `POST` | `/api/v1/admin/packages` | Create |
| `PUT` | `/api/v1/admin/packages/{package_id}` | Update |
| `DELETE` | `/api/v1/admin/packages/{package_id}` | Archive |
| `GET` | `/api/v1/admin/settings` | Platform key/values |
| `PUT` | `/api/v1/admin/settings` | Write, actor recorded |
| `GET` | `/api/v1/admin/audit` | In-process audit log |
| `GET` | `/api/v1/admin/dashboard` | KPIs |
| `GET` | `/api/v1/admin/settlement-health` | Works with no database |

### Table actions (`operator`)

`POST` on `/api/v1/games/teen-patti-pro/rooms/{room}/rounds/start`, `/close`,
`/result`, `/settle`, plus `POST .../rounds/{round_id}/cancel` and
`PUT /api/v1/admin/games/{game_id}/config`. A key may also be **scoped** to specific games, via a
separate variable:

```bash
GAME_ADMIN_SCOPES="key-for-operator:teen-patti-pro,other-key:teen-patti-pro|teen-patti"
```

Games are `|`-separated. A key listed here may only act on the named games, and
a request for any other game is refused — whatever its role level. A key
**absent** from this map is unrestricted. Note this is a separate variable:
putting `key:operator:teen-patti-pro` in `GAME_ADMIN_KEYS` does not scope
anything, it makes the role the unrecognised string `operator:teen-patti-pro`
and the key is then refused outright.

### Turning the game off

| Method | Path | Notes |
| --- | --- | --- |
| `POST` | `/api/v1/admin/games/{game_id}/enable` | |
| `POST` | `/api/v1/admin/games/{game_id}/disable` | Optional `{status, message}` |

Disable stops new sessions. Rounds already in flight settle normally — pulling
a rug out from under a live pot is not an acceptable way to stop taking bets.

## 5. Status codes worth knowing

| Code | Meaning |
| --- | --- |
| `503` | No database, or the migration is not applied. The body names the reason. |
| `502` | The query itself failed (bad schema, dropped connection). The message is the exception **type** only — driver messages name tables and columns and are not echoed to the client. Check the server log. |
| `404` | No such row. |
| `422` | Invalid input. |

## 6. Applying the schema

```bash
export DATABASE_URL='postgresql://user:pass@host:5432/dbname'
./scripts/apply-migration.sh
```

Applies `db/migrations/005_admin_deep_control.up.sql` and verifies it.
`006_scheduled_config` creates the scheduling table. Both are idempotent.

Until this is run, every admin route except settlement health returns `503`.

## 7. Player appearance

An operator styles a player in the admin panel; the game resolves that style by
player id.

```bash
# Set
curl -X PUT -H "X-Admin-Key: $ADMIN_KEY" -H 'Content-Type: application/json' \
  -d '{"appearance": {"avatar": "/assets/games/teen-patti-pro/avatars/avatar-frame-navy.svg",
                      "title": "Diamond"}}' \
  https://games.example.com/api/v1/admin/players/player-42/appearance

# Read (admin view)
curl -H "X-Admin-Key: $ADMIN_KEY" \
  https://games.example.com/api/v1/admin/players/player-42/appearance
```

The client reads it **without** a key, because a player rendering their own
table must not need admin credentials:

```
GET /api/v1/players/{player_id}/appearance
  → {"avatar": "...", "frame": "...", "title": "", "source": "dearlive"|"default"}
```

`source` is `"default"` when the player has no style configured, and the
`avatar` is the default icon. The client draws its fallback circle if the image
has not loaded yet, and swaps to the default icon if the URL is broken, so a
missing avatar never costs a player their seat.
