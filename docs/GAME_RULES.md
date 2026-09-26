# Teen Patti Pro — Game Rules Specification

Status: **DRAFT — awaiting business sign-off.**
Config version: `tpp-1.0.0-tbc` · `confirmed: false`

This document describes **what the engine actually does today**, not what it
should do. Every rule is traceable to code. Nothing here is invented; where a
rule is a business decision, the current engine default is presented as a
*proposal* for sign-off.

While `confirmed` is `false` the service refuses real-money actions with
`403 TBC_RULE_UNCONFIRMED`. That is intentional: this spec is the artefact that
unblocks sign-off, not a substitute for it.

Source of truth: `games/teen_patti_pro/engine.py` (rules) and
`games/teen_patti_pro/config.py` (values).

---

## 1. Round shape

| Item | Value | Code |
|---|---|---|
| Seats | 3 fixed positions: **A, B, C** | `config.seats` |
| Cards per seat | 3 | `config.cards_per_hand` |
| Betting window | 20 s from round start | `config.guess_ms` |
| Bets per player per round | 50 | `config.max_bets_per_player_per_round` |
| Allowed stakes | 20 / 100 / 500 / 1000 | `config.denoms` |
| Stake range | 20 – 100,000 | `config.min_bet`, `config.max_bet` |
| Jokers | 0 (deck is 52 cards) | `config.jokers` |
| Rake | 0 bps (no rake) | `config.rake_bps` |
| Tie remainder | carried to next round | `config.tie_policy` |
| A-2-3 straight | lowest straight | `config.ace_low_rank` |

### Lifecycle

`UPCOMING → BETTING_OPEN → BETTING_CLOSED → RESULT_PROCESSING → RESULT →
SETTLED → CLOSED`

The engine advances the round only on explicit calls; the service drives it.
A round is settled exactly once: the engine returns identical rows on replay and
the service additionally guards `UNIQUE settlement.bet_id`.

### Round start

A round begins when a table has at least one seated player and no active round
(`TeenPattiService.ensure_round`). On start the engine:

1. increments the round number and stamps `round_id = {room}-r{n}`,
2. generates `seed_hex` (32 hex chars from `secrets.token_hex`),
3. builds the deck and shuffles it with a Fisher–Yates driven by that seed,
4. records `deck_commitment = sha256(repr(deck))` for later audit,
5. deals cards `deck[0:3]` → A, `deck[3:6]` → B, `deck[6:9]` → C,
6. sets `betting_end_at_ms = now + 20 s`.

The shuffle is deterministic given the seed, so any round can be replayed and
audited from `seed_hex` + `deck_commitment` alone.

---

## 2. Hand ranking (G3-BR-01)

Higher wins. Categories, strongest first:

| Rank | Category | Tie-break |
|---|---|---|
| 6 | Trail (three of a kind) | the repeated rank |
| 5 | Pure sequence (straight flush) | full descending rank tuple |
| 4 | Sequence (straight) | full descending rank tuple |
| 3 | Colour (flush) | ranks, descending |
| 2 | Pair | pair rank, then the odd card |
| 1 | High card | ranks, descending |

Exact implementation: `engine.evaluate_hand`, with plain hands served from a
precomputed lookup table (`games/teen_patti_pro/table.py`) and a fail-open to
the live evaluator if the table ever misses.

### Straights

A hand is a sequence when its three distinct ranks are consecutive. Both
`A-K-Q` and `A-2-3` are admitted.

### A-2-3 placement (A23-RANK)

Configurable via `config.ace_low_rank`, three implemented variants:

| Value | A-2-3 tie-break | Meaning |
|---|---|---|
| `lowest` *(default/proposal)* | `(3,)` | below `2-3-4` — modern casino default |
| `second` | `(14, 3, 2)` | just below `A-K-Q` — "esrrhs" behaviour |
| `highest` | `(15,)` | above `A-K-Q` — traditional / Teen Patti Gold style |

Changing this value changes outcomes, so it requires a new config version and
re-confirmation.

---

## 3. Jokers (JOKER)

Default `0`, so the deck is the standard 52. The engine supports `0..3`.

When enabled, jokers are wild and are resolved *before* ranking by
`best_expansion`, which may only substitute cards from the 52-card deck that
are **not already in the hand** (no duplicates):

| Jokers in hand | Resolution |
|---|---|
| 3 | three Aces (global maximum) |
| 2 | trail of the remaining card's rank, in its other two suits |
| 1 | exhaustive search over all legal substitutes, best hand wins |

Jokers sit inside the single shuffled deck, so their positions are covered by
the same seed and deck commitment.

---

## 4. Betting (SEATS, DENOMS)

Bets are placed against **positions A/B/C**, not against individual players.
The acting player is recorded on the bet, but the wager is on a seat.

Order of enforcement in `TeenPattiService.place_bet` — the order matters and is
covered by tests:

1. config confirmed (real money blocked while unconfirmed)
2. `Idempotency-Key` present
3. room and round exist, betting window open
4. position is one of A/B/C
5. amount is an exact configured denomination
6. amount within min/max
7. per-player per-round bet count
8. balance sufficient (read-only check)
9. idempotency claimed **before** any money moves
10. atomic debit
11. bet created under the room lock
12. result published and event fired

If step 11 fails after the debit, the stake is **always** credited back as a
compensating transaction. A lost debit is treated as worse than a redundant
refund row.

Idempotency: the same key with the same payload replays the original bet with
no second debit; the same key with a different payload is rejected
`409 DUPLICATE_REQUEST`.

---

## 5. Settlement, pot and tie handling (G3-BR-03, TIE-REMAINDER)

On close → result → settle:

```
pot           = sum(accepted stakes) + carry_in
rake          = pot * rake_bps // 10_000        (0 by default)
distributable = pot - rake
winners       = all seats with the top hand score (dead heat allowed)
```

Losing bets are recorded with `payout: 0` for traceability.

**Dead heat.** If several seats tie for the best hand, every winning stake is
paid **pro-rata by stake** from `distributable`, not split per position. The
final winner in bet-id order receives the rounding remainder, so the payouts sum
exactly to `distributable`.

Why: an equal-per-position split can make a winner lose money. With stakes
A=100 / B=900 tied, an equal split of 500 pays B only 500 < 900 staked. Pro-rata
guarantees `payout >= stake` for every winning bet when `rake == 0`.

**No winnable bets.** If there are no bets, or no bets on winning seats, the
whole `distributable` is carried to the next round (`carry_out`) rather than
being paid to nobody. This is the `tie_policy: carry_over` default.

---

## 6. Authority and audit

The server is authoritative for cards, positions, pots, timers, winners and
settlement. The client never supplies a balance, winner, bet amount or result.

Every round stores `seed_hex` and `deck_commitment`; every settlement row
records `config_version`. Together they allow an independent replay to confirm
any past round.

Unrevealed hands are masked in every snapshot and are only revealed once the
round reaches `RESULT`. Reconnect returns a snapshot plus missed events since a
sequence number, with pre-result hands still redacted.

---

## 7. Business sign-off checklist

The engine is blocked for real money until each item below is approved. Current
engine defaults are shown as the **proposal**.

| ID | Rule | Proposal (current default) | Approve? |
|---|---|---|---|
| G3-BR-01 | Variant / ranking table | trail > pure seq > seq > colour > pair > high | ☐ |
| G3-BR-02 | 3 cards per position | 3 | ☐ |
| G3-BR-03 | Pot / payout / tie handling | rake 0, dead heat pro-rata by stake | ☐ |
| G3-BR-04 | Timer duration | 20 s betting window | ☐ |
| G3-BR-05-MECH | RNG mechanism | server CSPRNG seed + SHA-256 deck commitment, deterministic replay | ☐ |
| DENOMS | Stake denominations | 20 / 100 / 500 / 1000 | ☐ |
| SEATS | Fixed positions | A / B / C, betting on positions not players | ☐ |
| TIE-REMAINDER | Tie handling | carry the remainder to the next round | ☐ |
| A23-RANK | A-2-3 placement | `lowest` (below 2-3-4) | ☐ |
| JOKER | Wild jokers | none (standard 52-card deck) | ☐ |

**Not yet proposed, and needed for real money:**

| Item | Question |
|---|---|
| Rake | 0 bps today. What commission applies, and is it taken from the pot before payouts? |
| Round trigger | What starts a round in production — a scheduler, min players seated, or both? |
| Currency | Engine is currency-agnostic; the provider enforces one configured currency. Which? |
| Max exposure | Engine allows `max_bet` 100,000 per bet and 50 bets/player/round. Is that intended? |

Once all ten are approved, cut a new config version with `confirmed=True` and
re-run the full suite. Sign-off is a business decision and is deliberately not
made in code.

---

## 8. Known inconsistencies to resolve

These are real mismatches found while writing this spec. They are not rule
errors, but they will confuse players and operators.

| # | Issue | Detail | Suggested fix |
|---|---|---|---|
| 1 | Table minimum below lowest denomination | catalog `teen-patti-low.min_bet = 10`, but the lowest stake the engine accepts is `20` | set catalog minimum to the denomination, or add a 10 denomination |
| 2 | Table maximum below engine maximum | catalog tops out at 10,000; engine allows up to 100,000 | align the top table with the intended max exposure, after the rake/exposure questions above |
| 3 | Seats vs players | `max_players` is 6 per table, but there are only 3 betting positions (A/B/C) | clarify whether extra players are observers or whether seat count should match positions |
| 4 | Unconfirmed rules reachable in sandbox | `confirmed: false` still allows play in sandbox/dev | expected and intentional; keep it that way for UAT |

---

## 9. Change control

* Any change to `seats`, `denoms`, `ace_low_rank`, `jokers`, `rake_bps`,
  `tie_policy`, ranking or timer requires a **new `config_version`**.
* Settlements already stamped with an older version remain valid and auditable.
* Update this document in the same commit as the code change, and re-run
  `python3 -m unittest discover -s tests`.
