"""JEV testgen gaps: concurrent settle, carry chain, timer sweep, limits, live WS."""
import socket
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.wallet import MemoryWallet
from games.teen_patti_pro.config import TeenPattiConfig
from games.teen_patti_pro.service import TeenPattiService

LIVE = TeenPattiConfig(confirmed=True)
T0 = 1_700_000_000_000


def svc_with(*players, amount=10000):
    w = MemoryWallet()
    for p in players:
        w.fund(p, amount)
    return TeenPattiService(config=LIVE, wallet=w), w


class TestConcurrentSettle(unittest.TestCase):
    def test_no_double_credit(self):
        s, w = svc_with("p1", "p2")
        tok = s.tokens.mint("p1", "r", "teen-patti-pro")
        s.open_session(tok.token)
        s.start_round("r")
        s.place_bet("r", "p1", "A", 100, "k1")
        s.place_bet("r", "p2", "B", 500, "k2")
        s.close_betting("r")
        s.publish_result("r")
        errs = []

        def go():
            try:
                s.settle("r")
            except Exception as e:  # noqa
                errs.append(e)

        threads = [threading.Thread(target=go) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errs, [])
        total = w.get_balance("p1").available + w.get_balance("p2").available
        # 20000 - 600 staked + 600 paid out (carry 0 case) OR carried; either way bounded
        self.assertLessEqual(total, 20000)
        self.assertGreaterEqual(total, 19400)


class TestCarryChain(unittest.TestCase):
    def test_carry_flows_to_next_pot(self):
        s, w = svc_with("p1")
        tok = s.tokens.mint("p1", "r", "teen-patti-pro")
        s.open_session(tok.token)
        # Round 1: no bets -> whole pot (0) carried; force carry by betting then... simplest:
        # bet on a loser is impossible to force; instead verify carry_in wiring:
        s.start_round("r")
        s.close_betting("r")
        s.publish_result("r")
        st1 = s.settle("r")
        self.assertEqual(st1["carry_out"], 0)
        # Round 2 with bets settles normally; carry_in==0
        s.start_round("r")
        s.place_bet("r", "p1", "A", 100, "k1")
        s.close_betting("r")
        s.publish_result("r")
        st2 = s.settle("r")
        paid = sum(x["payout"] for x in st2["settlements"])
        self.assertEqual(paid + st2["carry_out"], 100)
        # Round 3 sees previous carry_out as carry_in
        s.start_round("r")
        self.assertEqual(s.rooms["r"].round.carry_in, st2["carry_out"])


class TestSweep(unittest.TestCase):
    def test_expiry_auto_progresses(self):
        s, w = svc_with("p1", "p2")
        tok = s.tokens.mint("p1", "r", "teen-patti-pro")
        s.open_session(tok.token)
        s.rooms  # ensure room exists
        s._room("r")
        # start round with tiny window by monkey time
        s._now = lambda: T0
        s.start_round("r")
        s.place_bet("r", "p1", "A", 100, "k1")
        s.place_bet("r", "p2", "B", 500, "k2")
        s._now = lambda: T0 + 20_000 + 1  # past default guess_ms
        reports = s.sweep()
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]["actions"], ["closed", "result", "settled"])
        self.assertEqual(s.rooms["r"].round.status.value, "CLOSED")
        # sweep is idempotent: second run does nothing
        self.assertEqual(s.sweep(), [])

    def test_sweep_skips_unexpired(self):
        s, w = svc_with("p1")
        s._room("r")
        s._now = lambda: T0
        s.start_round("r")
        self.assertEqual(s.sweep(T0 + 100), [])


class TestLimits(unittest.TestCase):
    def test_denom_and_position_rejected(self):
        s, w = svc_with("p1")
        tok = s.tokens.mint("p1", "r", "teen-patti-pro")
        s.open_session(tok.token)
        s.start_round("r")
        from games.teen_patti_pro.service import ServiceError
        for pos, amt in (("Z", 100), ("A", 999), ("A", 0), ("A", -100)):
            with self.assertRaises(ServiceError, msg=f"{pos}/{amt}"):
                s.place_bet("r", "p1", pos, amt, f"k-{pos}-{amt}")
        self.assertEqual(w.get_balance("p1").available, 10000)  # untouched


class TestLiveWS(unittest.TestCase):
    def test_handshake_subscribe_snapshot(self):
        from games.teen_patti_pro.ws import Hub, handle
        import json as J
        s, w = svc_with("p1")
        tok = s.tokens.mint("p1", "r", "teen-patti-pro")
        sess = s.open_session(tok.token)
        s.start_round("r")
        hub = Hub(s)
        srv = socket.socket()
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        got = {}

        def server():
            conn, _ = srv.accept()
            handle(conn, hub)

        t = threading.Thread(target=server, daemon=True)
        t.start()
        c = socket.create_connection(("127.0.0.1", port), timeout=5)
        key = "dGhlIHNhbXBsZSBub25jZQ=="
        c.sendall(f"GET / HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                  f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode())
        resp = c.recv(1024).decode("latin-1")
        self.assertIn("101", resp)
        # send masked subscribe frame
        payload = J.dumps({"action": "subscribe", "room": "r",
                           "session": sess["session_id"]}).encode()
        mask = b"\x01\x02\x03\x04"
        frame = bytes([0x81, 0x80 | len(payload)]) + mask + bytes(
            b ^ mask[i % 4] for i, b in enumerate(payload))
        c.sendall(frame)
        c.settimeout(5)
        data = c.recv(65536)
        # server frame: 0x81, len, json
        ln = data[1] & 0x7F
        off = 2
        if ln == 126:
            ln = int.from_bytes(data[2:4], "big")
            off = 4
        msg = J.loads(data[off:off + ln].decode())
        self.assertEqual(msg["kind"], "snapshot")
        self.assertEqual(msg["data"]["room_id"], "r")
        c.close()
        srv.close()


if __name__ == "__main__":
    unittest.main()
