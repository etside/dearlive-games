"""Deterministic wheel randomness — parity with DearLive current.

Mirrors Uradhura `FairRandom` (HMAC-SHA256 keyed by serverSeed over
`{clientSeed}:{nonce}:{instance}`):
  float = first 7 digest bytes / 2**56  (in [0, 1))
Wheel outcome mirrors `WheelDriver.generateOutcome`: weighted pick by that
float, landing angle = segment base + jitter (second float, instance 1).

Pure stdlib; no sockets/wallet/DB. Both wheel games (Greedy Monkey,
Baby King) share this so their outcomes are identical given the same
seed + options — exactly like DearLive current (one WheelDriver, many codes).
"""
import hashlib
import hmac
from typing import Any, Dict, List


def fair_float(server_seed: str, client_seed: str, nonce: int, instance: int = 0) -> float:
    mac = hmac.new(server_seed.encode(), f"{client_seed}:{nonce}:{instance}".encode(),
                   hashlib.sha256).digest()
    value = int.from_bytes(mac[:7], "big")
    return value / 2 ** 56


def wheel_outcome(options: List[Dict[str, Any]], server_seed: str,
                  client_seed: str, nonce: int) -> Dict[str, Any]:
    """Deterministic wheel pick. Each option: {id, name, weight>0, multiplier}.

    Returns {winning_index, winning_option, angle, ...}. Same inputs ->
    same outputs (provably fair; reveal server_seed after settlement).
    """
    if not options:
        raise ValueError("wheel: no active options configured")
    weights = [float(o.get("weight", 1)) if float(o.get("weight", 1)) > 0 else 1.0
               for o in options]
    total = sum(weights)
    pick = fair_float(server_seed, client_seed, nonce, 0) * total
    cumulative, winner_idx = 0.0, len(options) - 1
    for i, w in enumerate(weights):
        cumulative += w
        if pick < cumulative:
            winner_idx = i
            break
    winner = options[winner_idx]
    segment = 360.0 / len(options)
    jitter = fair_float(server_seed, client_seed, nonce, 1) * segment * 0.9
    angle = round(winner_idx * segment + jitter, 2)
    mult = float(winner.get("multiplier", 1))
    return {
        "winning_index": winner_idx,
        "winning_option_id": winner.get("id", winner.get("name")),
        "winning_label": winner.get("name"),
        "result_text": f"{winner.get('name')} x{mult:.2f}",
        "angle": angle,
        "option_index": winner_idx,
        "option_count": len(options),
        "payout_multiplier": mult if mult > 0 else 1.0,
    }
