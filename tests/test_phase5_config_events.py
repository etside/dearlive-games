import dataclasses
import unittest

from common.wallet import MemoryWallet
from games.teen_patti_pro.config import TeenPattiConfig
from games.teen_patti_pro.service import TeenPattiService


class Phase5ConfigEventTest(unittest.TestCase):
    """Config snapshotting and event routing for the single shipped game.

    Previously parameterised across Teen Patti plus the two wheel games; the
    wheel engines are gone, so these now cover Teen Patti alone. The behaviour
    under test -- a round captures the config version it started with, and the
    next round picks up the new one -- is engine-independent, so the coverage
    is unchanged in kind.
    """

    def _service(self, **kw):
        return TeenPattiService(
            config=TeenPattiConfig(confirmed=True, **kw), wallet=MemoryWallet())

    def test_teen_patti_config_is_snapshotted_and_next_round_refreshes(self):
        config = TeenPattiConfig(confirmed=True, version="round-a")
        service = TeenPattiService(config=config, wallet=MemoryWallet())
        service.start_round("room-a")
        first = service.rooms["room-a"].round
        self.assertEqual(first.config_snapshot["version"], "round-a")
        service.config = dataclasses.replace(config, version="round-b", max_bet=2000)
        service.close_betting("room-a")
        service.publish_result("room-a")
        service.settle("room-a")
        service.start_round("room-a")
        second = service.rooms["room-a"].round
        self.assertEqual(second.config_snapshot["version"], "round-b")
        self.assertEqual(second.config_version, "round-b")

    def test_lifecycle_events_are_room_routable(self):
        service = self._service()
        service.start_round("teen-room")
        service.close_betting("teen-room")
        service.publish_result("teen-room")
        service.settle("teen-room")
        for kind in ("round.started", "betting.closed", "result.published",
                     "settlement.completed"):
            event = next(item for item in service.event_log if item["kind"] == kind)
            self.assertEqual(event["data"]["room_id"], "teen-room")

    def test_reconnects_to_the_current_snapshot(self):
        service = self._service()
        service.wallet.fund("phase5-player", 10000)
        session = service.open_session(
            service.tokens.mint("phase5-player", "phase5-room",
                                "teen-patti-pro").token)
        service.start_round("phase5-room")
        service.place_bet("phase5-room", "phase5-player", "A", 20, "phase5-bet")
        state = service.reconnect(session["session_id"], 0)
        self.assertIn("snapshot", state)
        self.assertEqual(state["snapshot"]["room_id"], "phase5-room")
        self.assertTrue(state["missed_events"])


if __name__ == "__main__":
    unittest.main()
