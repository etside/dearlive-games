# Greedy Lion and Monkey Wheel — Wheel Game Rules Specification

Status: **DRAFT — awaiting business sign-off.**
Config versions: `greedy-lion-1.0.0-tbc` / `greedy-1.0.0-tbc` · `confirmed: false`

This document describes **what the wheel engine actually does today**, not what it
should do. Every rule is traceable to code. Current values that are commercial
choices are presented as proposals for sign-off. Where the code has no rule, or
where the implementation and the surrounding documentation disagree, that is
called out explicitly.

The two games use the same `WheelService`; their bindings differ only in the
provider code, engine id, and option set. The provider mapping is:

| Game | Provider `game_code` | Engine id | Config factory |
|---|---|---|---|
| Greedy Lion | `greedy_lion` | `greedy-lion` | `greedy_lion_config` |
| Monkey Wheel | `monkey_wheel` | `greedy-monkey` | `greedy_config` |

`provider/games.py:14-28,179-191` defines the canonical provider codes,
aliases, engine ids, and bindings. `games/wheel_common/configs.py:49-61`
defines the engine ids, versions, and option sets.

There is no `games/wheel_common/engine.py` in the inspected tree. The actual
result implementation is `common/wheel.py:18-58`, imported by
`games/wheel_common/service.py:38`.

The existing `docs/GAME_RULES.md` is specifically a Teen Patti Pro specification
(`docs/GAME_RULES.md:1,15-16`); its Teen Patti values are not treated as wheel rules
here. Its `confirmed: false` real-money block is the relevant cross-reference,
but the wheel implementation's exact gate is described below.

---

## 1. Round shape

### 1.1 Options, seats, and table values

The wheel engine has **betting options, not fixed betting seats**. A bet stores
an `option_id`; there is no seat list or position validation in
`WheelBet` (`games/wheel_common/service.py:81-91`). The provider maps wheel
choices directly from the active option objects and uses `option_id` as the
action field (`provider/games.py:72-83,184-191`).

The provider's `seats` view is a sorted list of room members, not betting
positions (`provider/games.py:111-113`). The provider table catalog separately
has a `max_players` occupancy limit (`provider/tables.py:23-36`); this is not a
wheel option/seat rule enforced by `WheelService`.

| Item | Current engine value | Code |
|---|---:|---|
| Betting choices | Active `WheelOption` objects | `games/wheel_common/service.py:177-179`; `provider/games.py:72-83` |
| Default active state | `is_active=True` unless explicitly changed | `games/wheel_common/service.py:51-59` |
| Betting duration | 20,000 ms from `start_round` | `games/wheel_common/service.py:69-74,205-214` |
| Declared round duration | 30,000 ms, but this field is not used by the service flow | `games/wheel_common/service.py:69-70,205-214,468-490` |
| Allowed denominations | `20 / 100 / 500 / 1000` | `games/wheel_common/service.py:69-74`; `games/wheel_common/configs.py:49-61` |
| Minimum stake | 20 | `games/wheel_common/service.py:71-74,269-279` |
| Maximum stake per bet | 100,000 | `games/wheel_common/service.py:71-74,269-279` |
| Accepted bets per player per round | 50 | `games/wheel_common/service.py:73-74,275-279` |
| Auto-bet | Disabled by default for both configs | `games/wheel_common/service.py:75-76`; `games/wheel_common/configs.py:49-61` |
| Payout setting label | `stake_x_multiplier` | `games/wheel_common/service.py:76-77` |
| Rake/commission | No rake field or deduction exists in the wheel settlement path | `games/wheel_common/service.py:62-78,389-433` |
| Tie/no-winner policy | No tie or no-winner policy is implemented | `games/wheel_common/service.py:378-404`; `common/wheel.py:37-57` |
| Default confirmation | `confirmed=False`; TBC flags include options, payout, duration, auto, HOT, and earnings | `games/wheel_common/service.py:63-68`; `games/wheel_common/configs.py:1-6,49-61` |

The service's accepted bet amount is checked first against the denomination set,
then against min/max. Therefore the effective allowed stakes are exactly the
intersection of `{20, 100, 500, 1000}` and `[20, 100000]`, which is all four
configured denominations (`games/wheel_common/service.py:258-279`).

### 1.2 Lifecycle

The common status set is:

`UPCOMING → BETTING_OPEN → BETTING_CLOSED → RESULT_PROCESSING → RESULT → SETTLED → CLOSED`

The status values and allowed transitions are defined in
`common/lifecycle.py:16-34`. `CLOSED` is terminal (`common/lifecycle.py:26-34`).

| Phase | Actual operation | Code |
|---|---|---|
| `UPCOMING` | Default status on a newly constructed round; not normally exposed as a persistent round phase | `games/wheel_common/service.py:94-110,207-216` |
| `BETTING_OPEN` | `start_round` creates the round, seed, and betting deadline, then transitions directly from `UPCOMING` | `games/wheel_common/service.py:199-225` |
| `BETTING_CLOSED` | `close_betting` transitions an open round; the result call requires this exact status | `games/wheel_common/service.py:244-255,353-361` |
| `RESULT_PROCESSING` | `publish_result` enters this phase while computing the authoritative outcome | `games/wheel_common/service.py:353-370` |
| `RESULT` | The result is stored and published | `games/wheel_common/service.py:367-376` |
| `SETTLED` | Settlement rows are created and payouts are credited | `games/wheel_common/service.py:378-435` |
| `CLOSED` | Settlement completion is recorded and the round is terminal | `games/wheel_common/service.py:415-421` |

`ensure_round` starts a round when a room has at least one member and no active
round; it returns `WAITING` when the room has no members
(`games/wheel_common/service.py:227-242`). The service itself does not schedule
a ticker. `sweep` is the explicit expiry path and closes, publishes, and settles
expired `BETTING_OPEN` rounds in that order (`games/wheel_common/service.py:468-490`).

Cancellation is separate from normal settlement. The common lifecycle allows
several pre-result states to transition to `CLOSED`
(`common/lifecycle.py:26-34`), while `WheelService.cancel_round` marks all
accepted bets `voided`, closes the round, and credits each stake back
(`games/wheel_common/service.py:492-513`). The service rejects cancellation
once the status is `RESULT`, `SETTLED`, or `CLOSED`
(`games/wheel_common/service.py:497-500`).

### 1.3 Round events and webhook names

Each round has a local event sequence and records the server timestamp when each
event is emitted (`games/wheel_common/service.py:113-117`). The service emits
local events for round start, betting close, result processing/publication, bet
acceptance, settlement, session completion, and cancellation
(`games/wheel_common/service.py:217-223,253-255,364-372,338-343,417-421,505-506`).

The webhook allow-list is:

`game.session.created`, `player.joined`, `player.left`, `round.started`,
`bet.accepted`, `bet.rejected`, `betting.closed`, `result.published`,
`settlement.completed`, `session.completed`, `round.cancelled`, and `error`.

This list is defined in `common/webhooks.py:15-18`; event envelopes and the
HMAC-SHA256 signing helpers are defined at `common/webhooks.py:21-32`.
The wheel service fires the listed event kinds at
`games/wheel_common/service.py:192-194,221-223,291-297,330-332,338-349,441-442,505-512`.
The local `result.processing` event is emitted at
`games/wheel_common/service.py:364`, but it is not in the webhook allow-list
(`common/webhooks.py:15-18`).

---

## 2. Per-game option tables

All default options are `is_active=True` by dataclass default
(`games/wheel_common/service.py:51-59`). The tables below reproduce the
configured `weight`, `multiplier`, icon, colour, and HOT flag exactly.

### 2.1 Greedy Lion

Config: `games/wheel_common/configs.py:35-46,59-61`.

| Option id | Display name | Weight | Payout multiplier | Icon | Colour | HOT |
|---|---|---:|---:|---|---|---|
| `cub` | Lion Cub | 30 | 2.0x | `🦁` | `#fde68a` | false |
| `mane` | Golden Mane | 25 | 2.5x | `🦁` | `#f59e0b` | false |
| `pride` | Pride | 20 | 3.0x | `🐾` | `#f97316` | true |
| `savanna` | Savanna King | 15 | 5.0x | `🌅` | `#ef4444` | false |
| `crown` | Lion Crown | 5 | 20.0x | `👑` | `#facc15` | true |

`hot` is a data/display flag on the option; the result calculation does not
read it (`games/wheel_common/service.py:51-59`; `common/wheel.py:25-57`).

### 2.2 Monkey Wheel

Config: `games/wheel_common/configs.py:9-20,49-51`.

| Option id | Display name | Weight | Payout multiplier | Icon | Colour | HOT |
|---|---|---:|---:|---|---|---|
| `banana` | Banana | 30 | 2.0x | `🍌` | `#fde68a` | false |
| `apple` | Apple | 25 | 2.5x | `🍎` | `#ef4444` | false |
| `grapes` | Grapes | 20 | 3.0x | `🍇` | `#8b5cf6` | false |
| `mango` | Mango | 15 | 5.0x | `🥭` | `#f97316` | true |
| `crown` | Crown | 5 | 20.0x | `👑` | `#facc15` | true |

Weights are selection weights, not payout multipliers. The result function
uses weights to select an option and reads the selected option's multiplier for
the result (`common/wheel.py:25-57`).

---

## 3. Betting

### 3.1 Exact service validation order

`WheelService.place_bet` enforces the following order (`games/wheel_common/service.py:281-350`):

1. **Config confirmation:** `confirmed=False` raises `E.TBC_BLOCKED` before any
   other place-bet check (`games/wheel_common/service.py:172-175,281-285`).
2. **Idempotency key:** an empty `idempotency_key` is rejected
   (`games/wheel_common/service.py:281-285`).
3. **Room lookup and current time:** `_room` lazily creates a room if needed,
   and `now` is captured; there is no separate “room exists” check
   (`games/wheel_common/service.py:161-164,286-288`).
4. **Round status and window:** the round must exist, be `BETTING_OPEN`, and have
   `now < betting_end_at_ms` (`games/wheel_common/service.py:258-264`).
5. **Option:** the option must exist among active options
   (`games/wheel_common/service.py:265-268`).
6. **Denomination:** amount must be an exact configured denomination
   (`games/wheel_common/service.py:269-271`).
7. **Minimum/maximum:** amount must then be within configured min/max
   (`games/wheel_common/service.py:272-274`).
8. **Per-round count:** the player must have fewer than 50 bets whose status is
   `accepted` (`games/wheel_common/service.py:275-279`).
9. **Balance read:** the wallet balance is read and compared with the stake; no
   money moves in this step (`games/wheel_common/service.py:294-298`).
10. **Payload hash and idempotency claim:** the payload hash contains room,
    player, option, and amount, and the claim is made before debit
    (`games/wheel_common/service.py:299-306`).
11. **Replay:** a completed same-key/same-payload claim returns the stored
    result without a second debit (`games/wheel_common/service.py:302-306`;
    `common/idempotency.py:20-29,42-59`).
12. **Atomic debit:** the wallet is debited with the bet idempotency key
    (`games/wheel_common/service.py:307-313`).
13. **Room-lock duplicate check and second window check:** after debit, the
    service checks for an existing bet with the key, then checks the round
    status/deadline again (`games/wheel_common/service.py:314-326`).
14. **Bet creation and event:** the bet is appended, the local event is emitted,
    and the placement history snapshot is written
    (`games/wheel_common/service.py:333-343`).
15. **Idempotency completion, audit, and webhook:** the result is completed in
    the idempotency store, audited, and emitted as `bet.accepted`
    (`games/wheel_common/service.py:344-350`).

The ordering is therefore **idempotency claim before debit**, with a second
deadline check after debit. The first validation is not a full serialization of
the whole operation; the second check is the explicit race guard.

A rejected validation emits `bet.rejected` before raising. Window failures map
to the window-closed error; option, denomination, range, and per-round failures
map to validation errors (`games/wheel_common/service.py:288-293`).
Insufficient balance is checked after all `_validate` checks and before the
idempotency claim (`games/wheel_common/service.py:294-298`).

### 3.2 Idempotency and compensation

The idempotency claim is `bet:{idempotency_key}`. The payload hash omits
`auto`, round id, and the literal idempotency key because the key is already in
the store key; it hashes room, player, option, and amount
(`games/wheel_common/service.py:299-303`). A different payload under the same
key raises a duplicate-request conflict
(`games/wheel_common/service.py:301-304`; `common/idempotency.py:42-50`).

The explicit post-debit compensation path is the late-window race: if the
betting window closes between the initial validation/debit and the room-lock
recheck, the service credits the full stake back with a
`bet-void:{room_id}:{idempotency_key}` idempotency key, emits
`BETTING_CLOSED_RACE_REFUNDED`, and raises a window-closed error
(`games/wheel_common/service.py:324-332`).

The source header says post-debit failure “ALWAYS compensates”
(`games/wheel_common/service.py:11-17`), but the implemented method only has the
explicit compensation branch for the late-window race. There is no broad
`try/except` around every possible exception after debit and before bet creation
(`games/wheel_common/service.py:307-350`). This is a code/documentation gap, not
an additional rule inferred here.

### 3.3 Round and table limits

The engine's per-round count is 50 accepted bets per player, not 50 units of
stake (`games/wheel_common/service.py:73-74,275-279`). There is no aggregate
pot limit, payout limit, or table-specific per-round cap in `WheelService`.

The provider table catalog advertises different per-table ranges: Greedy Lion
and Monkey Wheel low tables are `10..100`, mid `100..1000`, and high
`1000..10000` (`provider/context.py:23-32`; `provider/tables.py:12-20,61-68`).
Table ranges are checked during table selection/session creation and join when
an amount is supplied (`provider/router.py:275-292,467-484`), but
`h_action` calls the engine directly and does not repeat the table range check
before `place_bet` (`provider/router.py:522-548`). The engine still enforces
its own denoms/min/max if the action reaches it.

---

## 4. Result and settlement maths

### 4.1 Result determination

A result can be published only from `BETTING_CLOSED`; it then transitions through
`RESULT_PROCESSING` and publishes `RESULT` (`games/wheel_common/service.py:353-376`).
The service passes active options as `{id, name, weight, multiplier}` plus the
server seed, client seed, and nonce to `wheel_outcome`
(`games/wheel_common/service.py:361-368`).

`wheel_outcome` uses a weighted cumulative selection:

1. Convert each option weight to a float; weights `<= 0` are silently replaced
   with `1.0` (`common/wheel.py:25-36`).
2. Compute `pick = fair_float(server_seed, client_seed, nonce, 0) * total`
   (`common/wheel.py:37`).
3. Select the first option whose cumulative weight is greater than `pick`
   (`common/wheel.py:38-44`).
4. Compute the visual angle as the selected segment base plus a second random
   float, rounded to two decimals (`common/wheel.py:45-47`).
5. Return the selected id/label and `payout_multiplier`; a non-positive
   multiplier in the input is returned as `1.0` (`common/wheel.py:48-57`).

For the default option sets, weights total 100. The result is one winning
option. There is no tie branch and no no-winner branch. The winner index is
initialized to the last index as a defensive fallback, but the documented
algorithm has no separate no-winner outcome (`common/wheel.py:37-47`).

### 4.2 Payout

Settlement requires `confirmed=True` and a round in `RESULT`
(`games/wheel_common/service.py:378-389`). For each accepted bet:

- If `bet.option_id == winning_option_id`, payout is
  `int(bet.amount * float(winner.payout_multiplier))`
  (`games/wheel_common/service.py:389-400`).
- Otherwise, payout is `0` and the bet status becomes `lost`
  (`games/wheel_common/service.py:392-400`).

The multiplier is a gross payout multiplier because the amount is multiplied by
the configured multiplier; the code does not subtract the stake before calculating
the credit. For example, a 20 stake on a 2.5x option produces an integer payout
of `int(20 * 2.5) == 50` (`games/wheel_common/service.py:395-404`).

There is no pot aggregation, no per-position distribution, no dead-heat
pro-rata split, and no remainder field. Every accepted bet receives its own
settlement row, including losers with `payout: 0`
(`games/wheel_common/service.py:401-405`). Losing stakes are not carried, and
there is no remainder carry-out path (`games/wheel_common/service.py:389-405`).

The `WheelConfig.payout_rule` is only a configured string
(`games/wheel_common/service.py:75-77`); the settlement implementation does not
read it and always applies `int(stake * multiplier)`.

### 4.3 Exactly-once handling and cancellation economics

`settle` returns the existing rows if the in-memory round flag is already set,
and uses a service-level settlement lock plus a `settled_bet_ids` set to skip a
previously credited bet (`games/wheel_common/service.py:378-386,422-435`).
Each positive payout is credited with `settle:{bet_id}` as the wallet
idempotency key (`games/wheel_common/service.py:423-432`).

Cancellation marks accepted bets `voided`, closes the round, and credits each
stake with `cancel:{bet_id}` (`games/wheel_common/service.py:492-513`). A
cancelled bet is not paid a wheel multiplier.

The implementation is in-memory. There is no database-backed unique
`settlement.bet_id` in the wheel service itself; exact-once is only the local
flag/set plus the wallet adapter's required idempotency
(`games/wheel_common/service.py:94-111,140-158,378-435`;
`common/wallet.py:42-63`).

---

## 5. RNG and provable fairness

### 5.1 Seed and HMAC construction

At round start, the service generates:

```text
server_seed = secrets.token_hex(32)
server_seed_hash = sha256(server_seed.encode()).hexdigest()
```

(`games/wheel_common/service.py:205-214`.) `client_seed` defaults to the fixed
string `dearlive`, and `nonce` is the room's incrementing round number
(`games/wheel_common/service.py:94-104,205-214`). There is no service method in
the inspected flow that accepts a player-selected client seed.

For each random value, the code computes:

```text
message = f"{client_seed}:{nonce}:{instance}"
mac = HMAC-SHA256(key=server_seed, message=message)
float = int(mac[0:7], big-endian) / 2**56
```

(`common/wheel.py:18-22`). Instance `0` selects the weighted option and instance
`1` selects the angle jitter (`common/wheel.py:37-47`).

### 5.2 What can be independently checked

Given the raw server seed, `client_seed == "dearlive"`, nonce, active options,
weights, and the exact `common/wheel.py` algorithm, a verifier can independently
recompute the selected option, multiplier, result text, and angle
(`games/wheel_common/service.py:101-104,205-214,365-376`;
`common/wheel.py:18-57`). An operator who has both seed and result can also
recompute `sha256(server_seed)` and compare it with `server_seed_hash`
(`games/wheel_common/service.py:211-214`).

`publish_result` returns both the raw `server_seed` and its hash
(`games/wheel_common/service.py:371-376`). The local result event also includes
the raw seed (`games/wheel_common/service.py:371-372`). The webhook fired by the
service does not include the seed; it fires only the round id and the result
fields (`games/wheel_common/service.py:373-375`).

### 5.3 What is not verifiable

- A verifier with only `server_seed_hash` cannot recover the seed; SHA-256 is
  one-way, and the hash is not an HMAC signature over the result
  (`games/wheel_common/service.py:211-214`; `common/webhooks.py:21-32`).
- `state` exposes the config version, options, totals, and status but not the
  seed or seed hash (`games/wheel_common/service.py:548-578`). There is no
  pre-result commitment exposed in that state response.
- The code does not prove that the seed was generated fairly beyond using
  `secrets.token_hex`; it does not publish entropy proof or a commitment before
  the result (`games/wheel_common/service.py:205-214`).
- The client seed is fixed by the service default and is not player-controlled;
  consequently a player cannot independently vary the HMAC input
  (`games/wheel_common/service.py:101-104`).
- The HMAC result mechanism does not prove the selected options were the approved
  business options, that the wallet balance was correct, or that webhook
  delivery was durable. Those are separate trust boundaries
  (`games/wheel_common/service.py:299-313,361-376`;
  `common/webhooks.py:35-58`).

Auto-bets do not affect the RNG input: the seed and nonce are created before
`_apply_autobets` is called (`games/wheel_common/service.py:205-223`), and
`publish_result` passes only the round's seed, client seed, and nonce to
`wheel_outcome` (`games/wheel_common/service.py:365-368`).

---

## 6. Auto-bet

Auto-bet is disabled in the default `WheelConfig` for both games
(`games/wheel_common/service.py:73-76`; `games/wheel_common/configs.py:49-61`).
When enabled, `set_autobet` requires an active option, an exact configured
denomination within min/max, and a `rounds` value from 1 through 100
(`games/wheel_common/service.py:515-529`). It stores one fixed option, amount,
and remaining-round count per player and room
(`games/wheel_common/service.py:125-137,528-532`).

At the end of `start_round`, the service applies each remaining auto-bet
configuration. It calls the ordinary `place_bet` path with
`auto:{player_id}:{round_id}` and `auto=True`
(`games/wheel_common/service.py:223,534-542`). A successful auto-bet decrements
`rounds_left`; a `ServiceError` is swallowed and the configuration is retained
for a later round (`games/wheel_common/service.py:537-545`). Therefore an
insufficient balance, closed window, invalid configuration, or confirmation
block skips that round rather than consuming the remaining count.

Auto-bet therefore inherits the engine's denomination, min/max, per-player
per-round cap, balance, idempotency, and deadline rules because it calls
`place_bet`; its fixed configured count is limited separately to 1..100
(`games/wheel_common/service.py:258-279,516-545`). It does not create a second
RNG stream, select a different option, or alter the selected result. The result
is already determined by the seed/client-seed/nonce input
(`games/wheel_common/service.py:205-223,365-368`).

The default is both **disabled** and **not confirmed**. `set_autobet` itself
does not call `_require_confirmed`; it only checks `auto_allowed`
(`games/wheel_common/service.py:515-525`). If approval is enabled while
`confirmed` remains false, execution still fails at `place_bet` and the
remaining count is not decremented (`games/wheel_common/service.py:283-285,537-545`).

---

## 7. State, history, recent results, and earnings

### 7.1 State

`state` returns active options with id, display name, multiplier, icon, colour,
and HOT flag; the configured denoms; the round status/timer; option totals; total
stake; the player's option totals and total; a revealed winner at or after
`RESULT`; recent results; and config version
(`games/wheel_common/service.py:548-578`).

Totals include bets whose status is `accepted` or `won`; settled losing bets are
not included in `totals` or `total_bet` after settlement
(`games/wheel_common/service.py:558-578`). The winner is hidden before `RESULT`
and exposed in `RESULT`, `SETTLED`, and `CLOSED`
(`games/wheel_common/service.py:567-578`).

### 7.2 History and recent results

`history(room_id, player_id, limit)` returns the player's placement snapshots
from the room's in-memory `bet_history`, taking the last `limit` entries, plus
`earnings_today` (`games/wheel_common/service.py:137,340-343,580-583`).
Settlement patches those snapshots with final status and payout
(`games/wheel_common/service.py:407-414`).

`recent_results` returns the room's recent result strip, capped at 20 entries in
the service (`games/wheel_common/service.py:585-586`); `state` returns at most
the first 10 (`games/wheel_common/service.py:573-578`).

The provider adapter maps wheel history to a `rounds` field from recent results
and a `bets` field from the service history, and does not expose the service's
`earnings_today` field in that provider response
(`provider/games.py:150-163`; `provider/router.py:562-571`). Thus earnings are
available through the service `history` method but not in the provider response
shown here.

### 7.3 `today_earnings`

`today_earnings` returns:

```text
{player_id, day, net}
```

`day` is `datetime.date.today().isoformat()` in the server's local timezone, and
`net` is the sum of `payout - stake` for every settlement row credited for that
player (`games/wheel_common/service.py:423-458`). A win contributes gross
payout minus stake; a loss contributes `-stake` (`games/wheel_common/service.py:428-433`).

There is no scheduled reset. The stored entry is reset lazily to `{day, net: 0}`
when a settlement occurs on a new local calendar day, and `today_earnings`
returns a zero result without mutating storage when the stored day is stale
(`games/wheel_common/service.py:446-466`). The metric is in-memory and is not
persisted by `WheelService` (`games/wheel_common/service.py:140-158`).

---

## 8. Business sign-off checklist

The engine is blocked for real-money place-bet and settlement while
`confirmed: false`: `_require_confirmed` is called by `place_bet` and `settle`
(`games/wheel_common/service.py:172-175,281-285,378-380`). Current values below
are proposals for approval, not new rules. Empty approval cells intentionally
remain empty.

| ID | Business decision embedded in the engine | Current value | Approve |
|---|---|---|:---:|
| W-OPTS-GL | Greedy Lion option ids, names, weights, multipliers, icons, colours, HOT flags | `cub`/2.0x, `mane`/2.5x, `pride`/3.0x HOT, `savanna`/5.0x, `crown`/20.0x HOT; see §2.1 | ☐ |
| W-OPTS-MW | Monkey Wheel option ids, names, weights, multipliers, icons, colours, HOT flags | `banana`/2.0x, `apple`/2.5x, `grapes`/3.0x, `mango`/5.0x HOT, `crown`/20.0x HOT; see §2.2 | ☐ |
| W-WEIGHTS | Probability weights and weighted selection | Greedy Lion 30/25/20/15/5; Monkey Wheel 30/25/20/15/5 | ☐ |
| W-PAYOUT-RULE | Winning payout basis | `stake_x_multiplier`; integer `stake * multiplier`, gross payout | ☐ |
| W-LOSSES | Treatment of losing stakes | Loser settlement payout `0`; no pot or carry-out | ☐ |
| W-TIES | Tie/no-winner treatment | No tie or no-winner rule; one weighted option is selected | ☐ |
| W-RAKE | Rake/commission | No field and no deduction; effective current path is no rake | ☐ |
| W-DURATION | Betting window | 20,000 ms from `start_round` | ☐ |
| W-ROUND-DURATION | Declared round duration | 30,000 ms field, currently not enforced by the service | ☐ |
| DENOMS | Stake denominations | `20 / 100 / 500 / 1000` | ☐ |
| LIMITS | Min/max and per-player per-round accepted bet count | 20..100,000; 50 accepted bets/player/round | ☐ |
| W-TABLE-LIMITS | Provider table min/max/max players | Defaults 10..100, 100..1000, 1000..10000; max players 6; see §3.3 | ☐ |
| W-AUTO | Auto-bet availability | Disabled by default; if approved, rounds 1..100, fixed option/amount | ☐ |
| W-AUTO-FAIL | Failed auto-bet behavior | Skip round and retain remaining count | ☐ |
| W-HOT | HOT/recommendation display flags | `pride`, `crown` for Greedy Lion; `mango`, `crown` for Monkey Wheel | ☐ |
| W-EARNINGS | Definition of today's earnings | Local calendar-day net of `payout - stake`; in-memory | ☐ |
| W-SEATS | Betting seat model | Options, not fixed positions; provider members are occupancy | ☐ |
| W-TRIGGER | Round start trigger | Explicit start or `ensure_round` when at least one room member exists; no in-service scheduler | ☐ |
| W-CURRENCY | Currency | Engine is currency-agnostic; memory wallet/provider defaults are `COIN` | ☐ |
| W-CONFIRMATION | When real money is permitted | `confirmed: false`; bet placement and settlement raise the TBC block | ☐ |

### Not yet proposed, and needed for real money

| Item | Question that business must answer |
|---|---|
| Option approval | Are the default option names, weights, multipliers, icons, colours, and HOT flags the approved commercial catalogue? |
| Commission | Is zero rake intentional, or should a commission be introduced? No commission mechanism currently exists. |
| Table exposure | Should the engine max remain 100,000 and 50 bets/player/round, or should table-level exposure and payout caps be added? |
| Table profile | Should provider low/mid/high ranges be authoritative, and should every action enforce them? See §3.3 and §9. |
| Round duration | Is the 20-second betting window the complete round duration, or should the declared 30-second field be enforced? |
| Auto-bet | Should auto-bet be enabled, and what should happen on insufficient funds or a missed round? Current behavior is skip and retain. |
| Earnings | Is local-date net payout-minus-stake the operator-facing definition of earnings, and must it be persisted/exported? |
| Client seed | Should the fixed `dearlive` client seed remain, or should players/providers supply a client-controllable seed? |
| Fairness disclosure | Should a pre-result seed hash commitment be exposed, and when should the raw seed be revealed? |
| Webhook delivery | Is an in-memory delivery log sufficient, or is a durable signed outbox/transport required? |
| Authentication and currency | Which production session, wallet, and currency contract is authoritative outside the in-memory adapters? |

Sign-off must produce a new config version with `confirmed=True`; changing
business values is not silently inferred from this document. The current configs
remain `*-tbc` and `confirmed=False` (`games/wheel_common/configs.py:49-61`;
`games/wheel_common/service.py:63-68`).

---

## 9. Known gaps, TBC items, and inconsistencies

### 9.1 Resolved in the money-path hardening pass

Found while documenting this spec, then fixed. Each is pinned by a regression
test in `tests/test_wheel_gaps.py`.

| Gap | Status |
|---|---|
| A round was marked `_settled` and moved to `CLOSED` **before** wallet credits ran, so a credit failure was unrecoverable: a retry returned the cached rows and the player was never paid | **Fixed.** `_settled` and the transitions now happen only after every credit succeeds |
| The first attempt finalised bet statuses, so a retry recomputed **zero** rows and silently paid nobody | **Fixed.** An interrupted settlement resumes from `r.settlements` instead of recomputing from finalised bets |
| `cancel_round` refunds every accepted stake but never called `_require_confirmed`, so refunds were possible while business rules were unconfirmed | **Fixed.** Now a gated money path like `place_bet` and `settle` |
| `set_autobet` only checked `auto_allowed` and never `_require_confirmed`, so auto bets could be armed on an unconfirmed game | **Fixed.** Arming auto bets now requires confirmed rules |
| `place_bet` accepted non-integer amounts (strings, floats, `True`) | **Fixed.** Amount must be a positive integer |
| An empty active option set raised a bare `ValueError` from inside result processing | **Fixed.** `publish_result` raises a `ServiceError` and leaves the round for investigation |
| Failed auto bets were skipped silently, invisible to player and operator | **Fixed.** Each skip is audited as `autobet.skipped` with the reason |
| The provider action route did not re-check table limits although the contract promised enforcement | **Fixed.** `h_action` validates the amount against the table profile for every game |
| `earnings_today` was computed by the engine but dropped from provider history | **Fixed.** Returned when a player is identified |
| Every `greedy_lion_config()` / `greedy_config()` / `baby_king_config()` call returned a new config that **shared the same mutable `options` list**, so deactivating an option in one service silently disabled it in every other service in the process | **Fixed.** Each factory returns independent `WheelOption` copies |

### 9.2 Still open

Not yet resolved. Each needs a code change or a business decision; none is
safe to assume.

| # | Gap | Why it is still open |
|---:|---|---|
| 1 | No wheel rake, pot, tie, no-winner or carry-over rule | The wheel pays stake x multiplier, a different economic model from Teen Patti. Needs sign-off before implementation |
| 2 | `payout_rule` is configurable-looking but ignored | The only implementation is stake x multiplier. Wire it up or delete it; deleting changes the config surface |
| 3 | `round_duration_ms` is declared (30s) but unused; the real window is `betting_duration_ms` (20s) | Changing either changes game timing, so it is a business decision, not a bug fix |
| 4 | Non-positive option weights silently become weight 1 | Silently coercing an invalid weight can change odds; failing loudly alters behaviour for a misconfigured game |
| 5 | `result.processing` is not in the webhook event allow-list | Emitted locally only, so this is a documentation gap rather than a crash. `close_betting` was re-checked and is correct |
| 6 | Raw server seed is returned and locally emitted before settlement, and `state` exposes no pre-result commitment | Publishing the seed before settlement weakens the fairness guarantee; committing to a hash first is a design change |
| 7 | The client seed is fixed, not player-controlled | Provable fairness is weaker without a player contribution. Adding one is a protocol change |
| 8 | Exactly-once protection is process-local | The wallet adapter's idempotency is the real boundary; the in-process set is a fast path lost on restart. Documented, not changed |
| 9 | Earnings are local-date based and in-memory | Resets on restart and follows server local time. Needs a timezone and persistence decision |
| 10 | No aggregate exposure, pot cap or payout cap | A limits policy has not been agreed. Business decision |
| 11 | Idempotency storage defaults to in-memory, where an in-flight claim is indistinguishable from a new one | The Redis store is used in staging and production; the in-memory default is a sandbox convenience |
| 12 | Provider table limits (max 10,000) and engine limits (max 100,000) disagree | Needs sign-off on real maximum exposure per game |
| 13 | Provider "seats" are room members, not betting positions | The wheel has no fixed positions; a member cap may not be meaningful |
| 14 | Webhook payload documentation is broader than implementation, and delivery uses an in-memory log | Documentation and delivery hardening are separate work |


## 10. Change control

- Any change to option weights, multipliers, denominations, limits, betting
  duration, auto-bet approval, HOT flags, or the settlement rule requires a new
  config version and explicit re-confirmation. The current versions are
  `greedy-lion-1.0.0-tbc` and `greedy-1.0.0-tbc`
  (`games/wheel_common/configs.py:49-61`).
- Existing settlement rows stamp the round's `config_version`
  (`games/wheel_common/service.py:401-404`), but the in-memory implementation
  does not provide durable historical storage or database uniqueness by itself.
- This file is a behaviour specification. It does not authorize production use,
  change Python code, or override the requirement that real-money actions remain
  blocked while `confirmed: false`.
