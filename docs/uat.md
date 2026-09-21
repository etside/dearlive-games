# UAT — Teen Patti Pro (BRD §13 matrix, Game-3 row)

Run with `--confirmed` dev server + funded MemoryWallet (NOT real money).
Launch: client loads without error. Round: round_no + countdown sync. Bet: select
denom + seat, accepted only when valid. Balance: decreases exactly by stake.
Invalid: insufficient/closed/invalid → rejected, no debit. Multi: several bets
traceable. Close: post-expiry bets rejected. Result: authoritative, linked to round.
Settlement: correct + exactly once (re-settle safe). History: round/bet/result visible.
Repeat: replays as new keys. Auto: N/A (no auto in Game 1 scope). Admin: config/audit
visible. Audit: bet→wallet traceable by IDs. Evidence: keep settlement rows +
deck_commit/seed per round for review.
