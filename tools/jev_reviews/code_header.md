CODE UNDER REVIEW: Teen Patti Pro deterministic engine (games/teen_patti_pro/engine.py) + service layer (service.py). Python stdlib only. Server-authoritative. Key invariants claimed:
(a) per-round mutex serializes bet-decision vs close; decision_time stamped under lock; invariant decision_time < betting_end_at enforced;
(b) idempotent bet replay by key; key-reuse-with-different-payload raises;
(c) debit-before-create with immediate compensation if window slammed shut post-debit;
(d) settle deterministic + replay-safe (_settled flag) + UNIQUE settlement.bet_id at service + idempotent wallet credit;
(e) tie split: equal per winning POSITION then pro-rata within position, dust carried (no seat bias);
(f) pot conservation: payouts + carry_out == pot;
(g) reconnect snapshot redacts hole cards pre-RESULT; result.published event redacted in replay pre-reveal;
(h) cancel only before RESULT, void + compensating credit.
JEV is ADVISORY ONLY. Flag real bugs/races/security holes. Do not invent DearLive APIs.
--- BEGIN CODE ---
