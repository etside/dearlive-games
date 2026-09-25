import dataclasses
import unittest

from common.wallet import MemoryWallet
from games.teen_patti_pro.config import TeenPattiConfig
from games.teen_patti_pro.service import TeenPattiService
from games.wheel_common.configs import baby_king_config, greedy_config
from games.wheel_common.service import WheelService


class Phase5ConfigEventTest(unittest.TestCase):
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

    def test_wheel_config_is_snapshotted_and_next_round_refreshes(self):
        config = greedy_config()
        config = dataclasses.replace(config, confirmed=True, version="wheel-a")
        service = WheelService(config=config, wallet=MemoryWallet())
        service.start_round("wheel-room")
        first = service.rooms["wheel-room"].round
        self.assertEqual(first.config_snapshot["version"], "wheel-a")
        service.config = dataclasses.replace(config, version="wheel-b", min_bet=40)
        service.close_betting("wheel-room")
        service.publish_result("wheel-room")
        service.settle("wheel-room")
        service.start_round("wheel-room")
        second = service.rooms["wheel-room"].round
        self.assertEqual(second.config_snapshot["version"], "wheel-b")
        self.assertEqual(second.config_version, "wheel-b")

    def test_lifecycle_events_are_room_routable_for_all_games(self):
        teen = TeenPattiService(config=TeenPattiConfig(confirmed=True), wallet=MemoryWallet())
        teen.start_round("teen-room")
        teen.close_betting("teen-room")
        teen.publish_result("teen-room")
        teen.settle("teen-room")
        for kind in ("round.started", "betting.closed", "result.published", "settlement.completed"):
            event = next(item for item in teen.event_log if item["kind"] == kind)
            self.assertEqual(event["data"]["room_id"], "teen-room")

        wheel = WheelService(config=dataclasses.replace(greedy_config(), confirmed=True), wallet=MemoryWallet())
        wheel.start_round("wheel-room")
        wheel.close_betting("wheel-room")
        wheel.publish_result("wheel-room")
        wheel.settle("wheel-room")
        for kind in ("round.started", "betting.closed", "result.published", "settlement.completed"):
            event = next(item for item in wheel.event_log if item["kind"] == kind)
            self.assertEqual(event["data"]["room_id"], "wheel-room")

    def test_each_game_reconnects_to_the_current_snapshot(self):
        configs = [
            ("teen-patti-pro", TeenPattiService(TeenPattiConfig(confirmed=True), MemoryWallet())),
            ("greedy-monkey", WheelService(dataclasses.replace(greedy_config(), confirmed=True), MemoryWallet())),
            ("baby-king", WheelService(dataclasses.replace(baby_king_config(), confirmed=True), MemoryWallet())),
        ]
        for game_id, service in configs:
            wallet = service.wallet
            wallet.fund("phase5-player", 10000)
            if isinstance(service, TeenPattiService):
                session = service.open_session(service.tokens.mint("phase5-player", "phase5-room", game_id).token)
                service.start_round("phase5-room")
                service.place_bet("phase5-room", "phase5-player", "A", 20, f"{game_id}-bet")
            else:
                session = service.open_session(service.tokens.mint("phase5-player", "phase5-room", game_id).token)
                service.start_round("phase5-room")
                service.place_bet("phase5-room", "phase5-player", service.config.options[0].option_id, 20, f"{game_id}-bet")
            state = service.reconnect(session["session_id"], 0)
            self.assertIn("snapshot", state)
            self.assertEqual(state["snapshot"]["room_id"], "phase5-room")
            self.assertTrue(state["missed_events"])


if __name__ == "__main__":
    unittest.main()
