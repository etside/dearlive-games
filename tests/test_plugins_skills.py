"""Plugins, skills isolation, lookup-table parity (reference-inspired upgrade)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import plugins
from common.audit import AuditLog
from common.skills import Skill, SkillBus


class TestRegistry(unittest.TestCase):
    def test_catalog_lists_all_games(self):
        plugins.import_builtin_games()
        games = {g["game_id"]: g for g in plugins.catalog()}
        self.assertEqual(games["teen-patti-pro"]["status"], "live")
        self.assertEqual(games["greedy"]["status"], "planned")
        self.assertEqual(games["animal-food-wheel"]["status"], "planned")

    def test_unknown_game_fails_closed(self):
        plugins.import_builtin_games()
        with self.assertRaises(plugins.UnknownGame):
            plugins.create("blackjack")

    def test_planned_game_not_playable(self):
        plugins.import_builtin_games()
        with self.assertRaises(plugins.GameDisabled):
            plugins.create("greedy")

    def test_live_factory(self):
        plugins.import_builtin_games()
        from games.teen_patti_pro.config import TeenPattiConfig
        room = plugins.create("teen-patti-pro", "r1", TeenPattiConfig(confirmed=True))
        self.assertEqual(room.room_id, "r1")


class BoomSkill(Skill):
    skill_id = "boom"
    description = "always explodes (isolation test)"

    def handles(self):
        return ["on_bet", "on_settle"]

    def run(self, hook, payload):
        raise RuntimeError("boom")


class SeenSkill(Skill):
    skill_id = "seen"
    description = "records payloads"

    def __init__(self):
        self.calls = []

    def handles(self):
        return ["on_bet", "on_result"]

    def run(self, hook, payload):
        self.calls.append((hook, dict(payload)))
        payload["MUTATE"] = True  # must not affect engine state (copies)


class TestSkills(unittest.TestCase):
    def test_isolation_and_copies(self):
        audit = AuditLog()
        bus = SkillBus(audit)
        seen = SeenSkill()
        bus.register(seen)
        bus.register(BoomSkill())
        bus.enable("teen-patti-pro", "seen")
        bus.enable("teen-patti-pro", "boom")
        out = bus.emit("teen-patti-pro", "on_bet", {"bet_id": "b1"})
        by_skill = {o["skill"]: o for o in out}
        self.assertTrue(by_skill["seen"]["ok"])
        self.assertFalse(by_skill["boom"]["ok"])
        # failure audited, money path unaffected (emit returns, never raises)
        self.assertTrue(any(e["action"] == "skill.failed" for e in audit.entries))
        # engine payload not mutated through the copy
        self.assertEqual(seen.calls[0][1], {"bet_id": "b1"})

    def test_unknown_skill_rejected(self):
        bus = SkillBus()
        with self.assertRaises(KeyError):
            bus.enable("teen-patti-pro", "nope")

    def test_service_emits_post_commit(self):
        from common.wallet import MemoryWallet
        from games.teen_patti_pro.config import TeenPattiConfig
        from games.teen_patti_pro.service import TeenPattiService
        audit = AuditLog()
        bus = SkillBus(audit)
        seen = SeenSkill()
        bus.register(seen)
        bus.enable("teen-patti-pro", "seen")
        w = MemoryWallet()
        w.fund("p", 10000)
        s = TeenPattiService(config=TeenPattiConfig(confirmed=True), wallet=w, skills=bus)
        tok = s.tokens.mint("p", "r", "teen-patti-pro")
        s.open_session(tok.token)
        s.start_round("r")
        s.place_bet("r", "p", "A", 100, "k1")
        hooks = [h for h, _ in seen.calls]
        self.assertIn("on_bet", hooks)


class TestTable(unittest.TestCase):
    def test_build_and_stats(self):
        from games.teen_patti_pro import table
        n = table.build()
        self.assertEqual(n, 3 * 22100)
        st = table.stats()
        self.assertEqual(st["hands_per_mode"], 22100)
        self.assertTrue(all(v > 100 for v in st["strength_levels"].values()))

    def test_full_parity_with_live_evaluator(self):
        """Every table entry equals live evaluate_hand (single source of truth)."""
        import itertools
        from games.teen_patti_pro import table
        from games.teen_patti_pro.engine import build_deck, evaluate_hand
        table.build()
        deck = build_deck()
        for mode in ("lowest", "second", "highest"):
            for combo in itertools.combinations(deck, 3):
                hand = list(combo)
                self.assertEqual(table.score(hand, mode), evaluate_hand(hand, mode),
                                 f"table/live mismatch {hand} {mode}")

    def test_position_ordering(self):
        from games.teen_patti_pro import table
        table.build()
        aaa = [(14, "S"), (14, "H"), (14, "D")]
        low = [(2, "S"), (3, "H"), (5, "D")]
        self.assertLess(table.position(aaa), table.position(low))
        self.assertEqual(table.position(aaa), 0)  # strongest overall

    def test_score_hand_uses_table(self):
        from games.teen_patti_pro.engine import score_hand, evaluate_hand
        h = [(13, "S"), (12, "H"), (11, "D")]
        self.assertEqual(score_hand(h), evaluate_hand(h))


if __name__ == "__main__":
    unittest.main()
