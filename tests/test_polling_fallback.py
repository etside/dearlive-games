import unittest
from pathlib import Path


class PollingFallbackTests(unittest.TestCase):
    def test_teen_patti_uses_optional_websocket_polling(self):
        source = Path("games/teen_patti_pro/client/game.js").read_text()
        self.assertIn("setInterval(refresh, 1500)", source)
        self.assertIn("setInterval(refreshWallet, 3000)", source)
        self.assertIn("setTimeout(() => { wsRetryTimer = null; connect(); }, 30000)", source)
        self.assertIn("window.DL_WEBSOCKET_URL", source)

    def test_wheel_uses_optional_websocket_polling(self):
        source = Path("games/wheel_common/client.html").read_text()
        self.assertIn("setInterval(refresh, 1500)", source)
        self.assertIn("setInterval(refreshWallet, 3000)", source)
        self.assertIn("setTimeout(() => { wsRetryTimer = null; connect(); }, 30000)", source)
        self.assertIn("window.DL_WEBSOCKET_URL", source)

    def test_wallet_balance_route_is_available_to_clients(self):
        source = Path("games/teen_patti_pro/api.py").read_text()
        self.assertIn('path == "/api/v1/wallet/balance"', source)


if __name__ == "__main__":
    unittest.main()
