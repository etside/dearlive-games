# V1 Business Rules Pending

**Status: BLOCKED — not implemented, not approved, not assumed.**

This file lists every business decision that is still open for V1. The engines
are running with their current code defaults, and the `confirmed` flag is
deliberately `false` for all three games, so every money-moving path is
refused until these are approved. **Nothing here is implemented and no value
is invented.** Approving a row means deciding the value; implementation follows
in a separate, explicitly authorised change.

Release baseline: `b08f318` · V1 scope: Teen Patti Pro, Greedy Lion, Monkey
Wheel (`baby-king` stays `planned` and excluded).

---

## 1. Teen Patti Pro — sign-off checklist

Source: `docs/GAME_RULES.md` §7. Current values are code defaults, offered as the
starting proposal only.

| ID | Decision | Current default (proposal) | Approved value |
|---|---|---|---|
| G3-BR-01 | Hand ranking order | trail > pure seq > seq > colour > pair > high | |
| G3-BR-02 | Cards per position | 3 | |
| G3-BR-03 | Pot, payout and tie handling | rake 0 bps; dead heat paid pro-rata by stake | |
| G3-BR-04 | Betting window | 20 s | |
| G3-BR-05-MECH | RNG mechanism | server CSPRNG seed + SHA-256 deck commitment, deterministic replay | |
| DENOMS | Stake denominations | 20 / 100 / 500 / 1000 | |
| SEATS | Betting positions | fixed A / B / C, betting on positions not players | |
| TIE-REMAINDER | Uneven remainder | carry the remainder to the next round | |
| A23-RANK | A-2-3 straight placement | `lowest` (below 2-3-4) | |
| JOKER | Wild jokers | none; standard 52-card deck | |

**Commercial values still not proposed at all** (there is no sensible default
to inherit — these need a number from the business):

| Decision | Question |
|---|---|
| Rake / commission | Teen Patti currently takes 0 bps. What commission applies, and is it deducted from the pot before payouts? |
| Round trigger | What starts a round in production: a scheduler, a minimum number of seated players, or both? |
| Currency | The engine is currency-agnostic and the provider enforces one configured currency. Which is live? |
| Maximum exposure | Per-bet maximum is 100,000 and 50 bets per player per round. Is that intended? |

## 2. Greedy Lion and Monkey Wheel — sign-off checklist

Source: `GAME_RULES_WHEELS.md` §8 (that document was removed with the wheel games; this section is retained as a record only).

| ID | Decision | Current default (proposal) | Approved value |
|---|---|---|---|
| W-OPTS-GL | Greedy Lion options, names, multipliers, icons, HOT flags | cub 2.0x, mane 2.5x, pride 3.0x HOT, savanna 5.0x, crown 20.0x HOT | |
| W-OPTS-MW | Monkey Wheel options, names, multipliers, icons, HOT flags | banana 2.0x, apple 2.5x, grapes 3.0x, mango 5.0x HOT, crown 20.0x HOT | |
| W-WEIGHTS | Outcome weights | 30 / 25 / 20 / 15 / 5 for both games | |
| W-BETTING | Betting window | 20 s (`betting_duration_ms`) | |
| W-LIMITS | Denominations and max bet | denoms 20/100/500/1000, max 100,000 | |
| W-AUTOBET | Auto Bet default and cap | disabled by default; 1–100 rounds | |
| W-RAKE | Wheel rake | **no rake exists in the engine** | |

**Wheel-specific gaps that also need a decision** (detail in
`GAME_RULES_WHEELS.md` §9.2 — removed, see note above):

| Decision | Why it is needed |
|---|---|
| Wheel economic model | Wheels pay `stake x multiplier`. There is no pot, tie, no-winner or carry-over rule. Confirm that is intended, or specify one. |
| `payout_rule` field | Declared in config but never read. Either the config surface should change or the field should be wired up. |
| `round_duration_ms` | Declared as 30 s but never used; the live window is 20 s. Confirm which is correct. |
| Invalid option weights | Non-positive weights silently become `1.0`, which can change odds. Should this fail loudly instead? |
| Fairness commitment | The raw server seed is returned before settlement and no pre-result hash commitment is exposed. Specify whether a commitment is required. |
| Client seed | Fixed, not player-controlled. Specify whether players must contribute a seed. |
| Max exposure per game | Provider tables cap at 10,000 while the engine allows 100,000. Approve the real ceiling. |
| Table seat cap | Provider "seats" are room members, but a wheel has no betting positions. Decide whether a member cap is meaningful. |

## 3. Cross-cutting decisions

| Decision | Question |
|---|---|
| Wheel earnings retention | `today_earnings` uses server local date and is in-memory. Approve a timezone and a retention policy. |
| Limits policy | No aggregate exposure, pot cap or payout cap exists anywhere. Specify one. |
| Betting window ownership | Is the round timer business-owned (operators may close early) or purely server-driven? |
| Documentation delivery | Webhook payload documentation is broader than what the service emits, and delivery currently uses an in-memory log. Approve building real delivery with retry and DLQ. |

## 4. How to unblock

1. Fill in an **Approved value** for every row above, or mark it N/A.
2. Confirm the commercial values that currently have no default (rake,
   round trigger, currency, maximum exposure).
3. Approve the change explicitly.

Only then will a new config version be cut with `confirmed=True`, followed by a
re-run of the full suite and browser flows. Until then, `confirmed` stays
`false` by design and real-money play remains blocked.
