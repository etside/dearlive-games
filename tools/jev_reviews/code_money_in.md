MONEY-IN REVIEW (bet placement + idempotency). Claim: lock serializes decision vs close; claim-before-debit; compensation on race.
### engine.place_bet

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

### engine.close_betting

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

### service.place_bet

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

### idempotency claim/complete

    def claim(self, key: str, payload_hash: str, ttl_s: int = 86400) -> Optional[Dict[str, Any]]:
        """Atomically claim key. Returns stored result on replay, None if newly claimed.
        Raises IdempotencyConflict if key exists with a different payload hash."""
        raise NotImplementedError
    def complete(self, key: str, result: Dict[str, Any]) -> None:
        """Attach the completed result to a claimed key."""
        raise NotImplementedError