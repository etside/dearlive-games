FOCUSED CODE REVIEW: Teen Patti Pro money paths. Invariants claimed: (1) room lock serializes bet-decision vs close; decision_time under lock, invariant decision_time<betting_end_at; (2) idempotency claim BEFORE debit; same key+payload replays, different payload raises; (3) debit-before-create with immediate compensation if window closed post-debit; (4) settle replay-safe via _settled flag + UNIQUE settlement.bet_id + idempotent credit; (5) ties split equally per winning POSITION then pro-rata within position, dust carried; (6) conservation payouts+carry_out==pot; (7) JEV advisory only.
--- CODE ---
### engine.py: place_bet (race-fixed decision under lock)

    def place_bet(self, player_id: str, position: str, amount: int,
                  idempotency_key: str, now_ms: int) -> Bet:
        """Full decision under lock: re-check window AFTER lock (race fix),
        replay same key, stamp decision_time, enforce invariant."""
        with self.lock:
            r = self.round
            if r is None:
                raise LifecycleError("No active round")
            # Idempotent replay first (same key -> same bet, no double debit).
            for b in r.bets:
                if b.idempotency_key == idempotency_key:
                    if (b.player_id, b.position, b.amount) != (player_id, position, amount):
                        from common import envelope as _e  # noqa
                        raise LifecycleError("DUPLICATE_REQUEST: key reused with different payload")
                    return b
            decision_time = now_ms  # server clock read under lock
            if not self._window_open(r, decision_time):
                raise LifecycleError("BETTING_CLOSED")
            v = self.validate_bet(player_id, position, amount, decision_time)
            if not v["ok"]:
                raise LifecycleError(v["code"] + ": " + v["message"])
            assert decision_time < r.betting_end_at_ms  # invariant, else no-debit reject above
            self._bet_seq += 1
            bet = Bet(f"{r.round_id}-b{self._bet_seq}", r.round_id, player_id,
                      position, amount, decision_time, idempotency_key)
            r.bets.append(bet)
            r.emit("bet.accepted", {"bet_id": bet.bet_id, "player": player_id,
                                    "position": position, "amount": amount}, decision_time)
            return bet

### engine.py: close_betting

    def close_betting(self, now_ms: int) -> Round:
        """Timer expiry path. Same lock: flips status first (race fix)."""
        with self.lock:
            r = self.round
            if r is None:
                raise LifecycleError("No active round")
            if r.status == RoundStatus.BETTING_OPEN:
                transition(r.status, RoundStatus.BETTING_CLOSED)
                r.status = RoundStatus.BETTING_CLOSED
                r.emit("betting.closed", {"round_id": r.round_id}, now_ms)
            return r

### engine.py: calculate_result

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

### engine.py: settle (tie-fair split, replay-safe)

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

### engine.py: cancel

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

### service.py: place_bet (wallet order)

    def place_bet(self, room_id: str, player_id: str, position: str,
                  amount: int, idempotency_key: str) -> dict:
        self._require_confirmed()
        if not idempotency_key:
            raise ServiceError(E.E_VALIDATION, "Idempotency-Key required")
        room = self._room(room_id)
        now = self._now()
        # 1-6: game/round/action/amount/min-max pre-validation (pure, no side effects)
        v = room.validate_bet(player_id, position, amount, now)
        if not v["ok"]:
            code = E.E_WINDOW_CLOSED if v["code"] == "BETTING_CLOSED" else E.E_VALIDATION
            self._fire("bet.rejected", {"room_id": room_id, "player_id": player_id,
                                        "reason": v["code"]})
            raise ServiceError(code, v["message"])
        # 7: balance check (read-only; debit below is the atomic guard)
        bal = self.wallet.get_balance(player_id)
        if bal.available < amount:
            self._fire("bet.rejected", {"room_id": room_id, "player_id": player_id,
                                        "reason": "INSUFFICIENT_BALANCE"})
            raise ServiceError(E.E_INSUFFICIENT, "Insufficient balance")
        # 8: idempotency claim BEFORE money moves
        payload_hash = _h({"room": room_id, "player": player_id, "pos": position, "amt": amount})
        try:
            replay = self.idempotency.claim(f"bet:{idempotency_key}", payload_hash)
        except IdempotencyConflict:
            raise ServiceError(E.E_DUPLICATE, "Idempotency key reused with different payload")
        if replay is not None:
            return replay  # exact replay, no second debit
        # 9: atomic debit (adapter idempotent on key too)
        try:
            self.wallet.debit(player_id, amount, ref=f"bet:{room_id}:{idempotency_key}",
                              idempotency_key=f"bet:{idempotency_key}")
        except InsufficientBalance:
            raise ServiceError(E.E_INSUFFICIENT, "Insufficient balance")
        except WalletError as exc:
            raise ServiceError(E.E_INTERNAL, f"Wallet debit failed: {exc}")
        # 10: create bet under room lock (re-checks window; race-fixed)
        try:
            bet = room.place_bet(player_id, position, amount, idempotency_key, self._now())
        except LifecycleError as exc:
            # Window slammed shut after debit: compensate immediately (append-only).
            msg = str(exc)
            if "BETTING_CLOSED" in msg:
                self.wallet.credit(player_id, amount,
                                   ref=f"bet-void:{room_id}:{idempotency_key}",
                                   idempotency_key=f"bet-void:{idempotency_key}")
                self._fire("bet.rejected", {"room_id": room_id, "player_id": player_id,
                                            "reason": "BETTING_CLOSED_RACE_REFUNDED"})
                raise ServiceError(E.E_WINDOW_CLOSED, "Betting closed (stake refunded)")
            raise ServiceError(E.E_CONFLICT, msg)
        result = {"bet_id": bet.bet_id, "round_id": bet.round_id, "position": position,
                  "amount": amount, "decision_time": bet.decision_time_ms}
        self.idempotency.complete(f"bet:{idempotency_key}", result)
        self.audit.record(player_id, "bet.place", "bet", bet.bet_id, after=result)
        self._fire("bet.accepted", {"bet_id": bet.bet_id, "room_id": room_id, **result})
        return result

    # ---- result + settle ----

### service.py: settle (exactly-once guard)

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

### idempotency.py: MemoryIdempotencyStore.claim/complete

    def claim(self, key: str, payload_hash: str, ttl_s: int = 86400) -> Optional[Dict[str, Any]]:
        """Atomically claim key. Returns stored result on replay, None if newly claimed.
        Raises IdempotencyConflict if key exists with a different payload hash."""
        raise NotImplementedError
    def complete(self, key: str, result: Dict[str, Any]) -> None:
        """Attach the completed result to a claimed key."""
        raise NotImplementedError