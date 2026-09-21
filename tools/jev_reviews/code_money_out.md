MONEY-OUT REVIEW (result/settle/cancel). Claim: replay-safe, exactly-once, tie-fair split, conservation, cancel only pre-RESULT.
### engine.calculate_result

    def calculate_result(self, now_ms: int) -> Round:
        with self.lock:
            r = self.round
            if r is None:
                raise LifecycleError("No active round")
            if r.status != RoundStatus.BETTING_CLOSED:
                raise LifecycleError(f"Result requires BETTING_CLOSED, have {r.status}")
            scored = {p: evaluate_hand(h) for p, h in r.hands.items()}
            best = max(scored.values())
            r.winner_positions = sorted(p for p, s in scored.items() if s == best)
            transition(r.status, RoundStatus.RESULT)
            r.status = RoundStatus.RESULT
            r.emit("result.published", {
                "round_id": r.round_id,
                "winners": r.winner_positions,
                "hands": {p: [f"{rk}{st}" for rk, st in h] for p, h in r.hands.items()},
                "deck_commit": r.deck_commit, "seed": r.seed_hex}, now_ms)
            return r

### engine.settle

    def settle(self, now_ms: int) -> List[dict]:
        """Deterministic, idempotent-by-construction: re-running returns the
        SAME rows (service layer additionally guards UNIQUE settlement.bet_id)."""
        with self.lock:
            r = self.round
            if r is None:
                raise LifecycleError("No active round")
            if r._settled:
                return r.settlements  # replay after CLOSED: identical rows, no double-pay
            if r.status != RoundStatus.RESULT:
                raise LifecycleError(f"Settle requires RESULT, have {r.status}")
            pot = sum(b.amount for b in r.bets if b.status == "accepted") + r.carry_in
            rake = pot * self.config.rake_bps // 10_000
            distributable = pot - rake
            winners = set(r.winner_positions)
            win_bets = [b for b in r.bets if b.position in winners and b.status == "accepted"]
            rows: List[dict] = []
            for b in r.bets:  # losers recorded with payout 0 (traceability)
                if b.status == "accepted" and b.position not in winners:
                    b.status = "lost"
                    rows.append({"settlement_id": f"stl-{b.bet_id}", "bet_id": b.bet_id,
                                 "player_id": b.player_id, "payout": 0,
                                 "config_version": r.config_version})
            if win_bets and distributable > 0:
                # Tie-fair split: distributable divided EQUALLY among winning
                # POSITIONS first (no bias to heavily-staked side), then
                # pro-rata by stake within each position. Floor dust -> carry.
                npos = len(winners)
                per_pos = distributable // npos
                dust = distributable - per_pos * npos
                for pos in sorted(winners):
                    pbets = [b for b in win_bets if b.position == pos]
                    stake = sum(b.amount for b in pbets)
                    paid = 0
                    for i, b in enumerate(sorted(pbets, key=lambda x: x.bet_id)):
                        share = per_pos * b.amount // stake if stake else 0
                        if i == len(pbets) - 1:
                            share = per_pos - paid
                        else:
                            paid += share
                        b.status = "won"
                        rows.append({"settlement_id": f"stl-{b.bet_id}", "bet_id": b.bet_id,
                                     "player_id": b.player_id, "payout": share,
                                     "config_version": r.config_version})
                r.carry_out = dust
            else:
                # No winnable bets (zero bets, or no bets on winners):
                # carry distributable forward (JEV tie fix); losers already recorded.
                r.carry_out = distributable
            self.carry_over = r.carry_out
            r.settlements = rows
            r._settled = True
            transition(r.status, RoundStatus.SETTLED)
            r.status = RoundStatus.SETTLED
            r.emit("settlement.completed", {"round_id": r.round_id, "pot": pot,
                                            "rake": rake, "carry_out": r.carry_out}, now_ms)
            transition(r.status, RoundStatus.CLOSED)
            r.status = RoundStatus.CLOSED
            r.emit("session.completed", {"round_id": r.round_id}, now_ms)
            return rows

### engine.cancel

    def cancel(self, reason: str, now_ms: int) -> List[Bet]:
        """Void path: accepted bets -> voided (service layer compensates wallet).
        Only before RESULT."""
        with self.lock:
            r = self.round
            if r is None:
                raise LifecycleError("No active round")
            if r.status in (RoundStatus.RESULT, RoundStatus.SETTLED, RoundStatus.CLOSED):
                raise LifecycleError("Too late to cancel")
            voided = [b for b in r.bets if b.status == "accepted"]
            for b in voided:
                b.status = "voided"
            transition(r.status, RoundStatus.CLOSED)
            r.status = RoundStatus.CLOSED
            r.emit("round.cancelled", {"round_id": r.round_id, "reason": reason,
                                       "voided": len(voided)}, now_ms)
            return voided

    # ---- views ----

### service.settle

    def settle(self, room_id: str, round_id: str = "") -> dict:
        self._require_confirmed()
        room = self._room(room_id)
        rows = room.settle(self._now())
        credited = []
        for row in rows:
            if row["bet_id"] in self.settled_bet_ids:
                continue  # UNIQUE settlement.bet_id: never pay twice
            self.settled_bet_ids.add(row["bet_id"])
            if row["payout"] > 0:
                self.wallet.credit(row["player_id"], row["payout"],
                                   ref=f"settle:{row['bet_id']}",
                                   idempotency_key=f"settle:{row['bet_id']}")
            credited.append(row)
            self.audit.record("system", "settlement.credit", "settlement",
                              row["settlement_id"], after=row)
        self._fire("settlement.completed", {"round_id": room.round.round_id,
                                            "settlements": len(rows)})
        return {"round_id": room.round.round_id, "settlements": rows,
                "carry_out": room.round.carry_out}

### service.cancel_round

    def cancel_round(self, room_id: str, reason: str, actor: str) -> dict:
        room = self._room(room_id)
        voided = room.cancel(reason, self._now())
        for b in voided:
            self.wallet.credit(b.player_id, b.amount, ref=f"cancel:{b.bet_id}",
                               idempotency_key=f"cancel:{b.bet_id}")
        self.audit.record(actor, "round.cancel", "round", room.round.round_id,
                          after={"reason": reason, "voided": len(voided)})
        self._fire("round.cancelled", {"round_id": room.round.round_id,
                                       "reason": reason})
        return {"voided": len(voided)}

    # ---- views ----