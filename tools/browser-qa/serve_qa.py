#!/usr/bin/env python3
"""Local QA server for browser validation (NOT game logic).

Mirrors games/teen_patti_pro/api.py main() wiring exactly (sandbox stores,
confirmed sandbox config), then funds QA players in-process so the browser
flow can place real bets. No game code is modified; funding uses the same
wallet objects the HTTP server uses.

Env: QA_API_PORT (default 8901), QA_WS_PORT (default 8902), QA_HOST.
Ports default to Chrome-safe values (5002/5061 are ERR_UNSAFE_PORT).
"""
import os
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

HOST = os.environ.get("QA_HOST", "127.0.0.1")
API_PORT = int(os.environ.get("QA_API_PORT", "8901"))
WS_PORT = int(os.environ.get("QA_WS_PORT", "8902"))

QA_PLAYERS = {"qa-player": 20000, "qa-player-2": 20000}

from common.config import Settings  # noqa: E402
from games.teen_patti_pro.api import Handler  # noqa: E402
from games.teen_patti_pro.config import TeenPattiConfig  # noqa: E402
from games.teen_patti_pro.service import TeenPattiService  # noqa: E402
from games.wheel_common.configs import (  # noqa: E402
    baby_king_config, greedy_config, greedy_lion_config)
from games.wheel_common.service import WheelService  # noqa: E402
from integrations import build_stores  # noqa: E402

settings = Settings.from_env()
wallet, tokens, sessions, idem, note = build_stores()
cfg = TeenPattiConfig(confirmed=True)
Handler.svc = TeenPattiService(config=cfg, wallet=wallet, tokens=tokens,
                               sessions=sessions, idempotency=idem,
                               webhook_secret="dev-secret")
Handler.svc.admin_keys_note = note + " +qa-faucet"
for _mkcfg in (greedy_config, baby_king_config, greedy_lion_config):
    _wc = _mkcfg()
    _wc.confirmed = True
    _w, _t, _s, _i, _n = build_stores()
    Handler.wheels[_wc.game_id] = WheelService(
        config=_wc, wallet=_w, tokens=_t, sessions=_s, idempotency=_i,
        webhook_secret="dev-secret")
Handler.game_enabled = {}
Handler.game_packages = {}
Handler.game_labels = {}

# QA faucet: fund test players on every wallet (REST + each wheel service).
for pid, amount in QA_PLAYERS.items():
    for w in [wallet] + [svc.wallet for svc in Handler.wheels.values()]:
        try:
            w.fund(pid, amount)
        except AttributeError:
            pass
print(f"QA faucet funded: {QA_PLAYERS}", flush=True)

# Share the teen service with WS (same fanout pattern as ws.py main).
from games.teen_patti_pro import ws as ws_mod  # noqa: E402

hub = ws_mod.Hub(Handler.svc)
_orig_fire = Handler.svc._fire


def _fanout(kind, data):
    ev = _orig_fire(kind, data)
    room = data.get("room_id") or ""
    if room:
        hub.push(room, {"seq": -1, "kind": kind,
                        "serverTime": ev["serverTime"], **data})
    return ev


Handler.svc._fire = _fanout
threading.Thread(target=hub.tick_loop, daemon=True).start()

import socket  # noqa: E402

srv_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv_sock.bind((HOST, WS_PORT))
srv_sock.listen(50)


def _ws_loop():
    while True:
        conn, _ = srv_sock.accept()
        threading.Thread(target=ws_mod.handle, args=(conn, hub), daemon=True).start()


threading.Thread(target=_ws_loop, daemon=True).start()
print(f"QA WS: ws://{HOST}:{WS_PORT}", flush=True)

import json as _json  # noqa: E402
from http.server import BaseHTTPRequestHandler  # noqa: E402


class QAHandler(Handler):
    """Test-only issuance route. Refuses to boot under APP_ENV=production."""

    def do_POST(self):
        from urllib.parse import urlparse
        if urlparse(self.path).path == "/qa/mint":
            if os.environ.get("APP_ENV", "sandbox").lower() == "production":
                self.send_response(403)
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length", "0") or 0)
            try:
                body = _json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                body = {}
            player, room, game = (body.get("player", "qa-player"),
                                  body.get("room", "qa-room"),
                                  body.get("game", "teen-patti-pro"))
            # Resolve delivery aliases to the canonical service/game.
            canonical = {"monkey-wheel": "greedy-monkey",
                         "monkey_wheel": "greedy-monkey"}.get(game, game)
            svc = Handler.wheels.get(canonical, Handler.svc) if canonical != "teen-patti-pro" else Handler.svc
            tok = svc.tokens.mint(player, room, canonical).token
            payload = _json.dumps({"launch_token": tok}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        return super().do_POST()


srv = ThreadingHTTPServer((HOST, API_PORT), QAHandler)
print(f"QA API: http://{HOST}:{API_PORT} (confirmed sandbox + faucet)", flush=True)
try:
    srv.serve_forever()
except KeyboardInterrupt:
    pass
