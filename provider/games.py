"""Per-game bindings for the provider API.

One provider API and one credential serve every game. Each game is described
here so the router stays game-agnostic: how to reach its service, what its
action input is called, how to list playable choices, and how to normalise its
history into the provider contract.

Nothing here invents game rules. The bindings only adapt what each engine
already exposes.
"""
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

TEEN_CODE = "teen_patti_pro"
LION_CODE = "greedy_lion"
MONKEY_CODE = "monkey_wheel"

# URL segment per game, plus the aliases the existing clients use.
SLUGS = {TEEN_CODE: "teen-patti", LION_CODE: "greedy-lion", MONKEY_CODE: "monkey-wheel"}
ALIASES = {
    TEEN_CODE: {TEEN_CODE, "teen-patti-pro", "teenpatti", "teen_patti"},
    LION_CODE: {LION_CODE, "greedy-lion", "greedy_lion", "greedy-lion-pro"},
    MONKEY_CODE: {MONKEY_CODE, "monkey-wheel", "monkey_wheel", "greedy-monkey",
                  "greedy_monkey", "greedy"},
}
# canonical internal game_id used by the engines
ENGINE_IDS = {TEEN_CODE: "teen-patti-pro", LION_CODE: "greedy-lion",
              MONKEY_CODE: "greedy-monkey"}


def canonical_code(raw: str) -> Optional[str]:
    """Map any accepted alias to its provider game_code, or None."""
    value = str(raw or "").strip().lower()
    for code, names in ALIASES.items():
        if value in names:
            return code
    return None


@dataclass
class GameBinding:
    """How the provider talks to one game engine."""

    game_code: str
    label: str
    kind: str                      # "table_game" | "wheel"
    action_field: str              # request field naming the choice
    choice_field: str              # response field echoing the choice
    service: Callable[[object], object] = field(repr=False, default=None)
    default_tables: tuple = ()

    @property
    def slug(self) -> str:
        return SLUGS[self.game_code]

    @property
    def engine_id(self) -> str:
        return ENGINE_IDS[self.game_code]

    # -- service access --
    def resolve(self, ctx) -> object:
        return self.service(ctx)

    def room(self, ctx, table_id: str):
        service = self.resolve(ctx)
        rooms = getattr(service, "rooms", {}) or {}
        room = rooms.get(table_id)
        if room is None:
            room = service._room(table_id)  # lazy-create, engine-owned
        return service, room

    # -- choices (seats for teen patti, options for wheels) --
    def choices(self, ctx, table_id: str) -> List[dict]:
        service, room = self.room(ctx, table_id)
        if self.kind == "table_game":
            return [{"choice": seat, "label": seat,
                     "multiplier": None} for seat in service.config.seats]
        out = []
        for option in service._options(room):
            out.append({"choice": option.option_id, "label": option.name,
                        "multiplier": option.multiplier, "icon": option.icon,
                        "color_hex": option.color_hex, "hot": option.hot})
        return out

    # -- lifecycle --
    def ensure_round(self, ctx, table_id: str) -> dict:
        service, _ = self.room(ctx, table_id)
        fn = getattr(service, "ensure_round", None)
        if fn is not None:
            return fn(table_id)
        return service.start_round(table_id)

    def start_round(self, ctx, table_id: str, actor="system") -> dict:
        return self.resolve(ctx).start_round(table_id, actor)

    def close_betting(self, ctx, table_id: str) -> dict:
        return self.resolve(ctx).close_betting(table_id)

    def publish_result(self, ctx, table_id: str) -> dict:
        return self.resolve(ctx).publish_result(table_id)

    def settle(self, ctx, table_id: str) -> dict:
        return self.resolve(ctx).settle(table_id)

    def sweep(self, ctx, now_ms: int = 0):
        return self.resolve(ctx).sweep(now_ms)

    def state(self, ctx, table_id: str, player_id: str) -> dict:
        return self.resolve(ctx).state(table_id, player_id)

    def seats(self, ctx, table_id: str) -> List[str]:
        _, room = self.room(ctx, table_id)
        return sorted((getattr(room, "members", {}) or {}).keys())

    def join(self, ctx, table_id: str, player_id: str) -> dict:
        service, room = self.room(ctx, table_id)
        # Bind the room's own dict. An `or {}` here would silently replace an
        # empty members map with a throwaway copy and lose the seat.
        members = getattr(room, "members", None)
        if members is None:
            members = {}
            room.members = members
        already = player_id in members
        if not already:
            joiner = getattr(room, "create_session", None)
            if joiner is not None:
                joiner(player_id)
            else:
                members[player_id] = {"joined_at": service._now()
                                      if hasattr(service, "_now") else 0}
        return {"already_seated": already, "seats": sorted(members)}

    def leave(self, ctx, table_id: str, player_id: str) -> dict:
        service, room = self.room(ctx, table_id)
        members = getattr(room, "members", None)
        if members is None:
            members = {}
            room.members = members
        removed = player_id in members
        members.pop(player_id, None)
        return {"removed": removed, "seats": sorted(members)}

    # -- action --
    def act(self, ctx, table_id: str, player_id: str, choice: str,
            amount: int, idempotency_key: str) -> dict:
        service, _ = self.room(ctx, table_id)
        return service.place_bet(table_id, player_id, choice, amount,
                                 idempotency_key)

    # -- history normalised to the provider contract --
    def history(self, ctx, table_id: str, player_id: str, limit: int) -> dict:
        service, _ = self.room(ctx, table_id)
        if self.kind == "table_game":
            rounds = service.history(table_id, limit)
            return {"rounds": rounds, "bets": [], "earnings_today": None}
        results = service.recent_results(table_id, limit).get("results", [])
        bets, earnings = [], None
        if player_id:
            try:
                data = service.history(table_id, player_id, limit)
                bets = data.get("bets", [])
                earnings = data.get("earnings_today")
            except Exception:
                bets, earnings = [], None
        return {"rounds": results, "bets": bets, "earnings_today": earnings}


def _teen_service(ctx):
    return ctx.teen_service() if hasattr(ctx, "teen_service") else ctx.service


def _wheel_service(ctx, engine_id: str):
    wheels = ctx.wheel_services() if hasattr(ctx, "wheel_services") else {}
    return wheels.get(engine_id)


def _teen_tables():
    return ()


BINDINGS: Dict[str, GameBinding] = {
    TEEN_CODE: GameBinding(
        game_code=TEEN_CODE, label="Teen Patti Pro", kind="table_game",
        action_field="position", choice_field="position",
        service=_teen_service),
    LION_CODE: GameBinding(
        game_code=LION_CODE, label="Greedy Lion", kind="wheel",
        action_field="option_id", choice_field="option_id",
        service=lambda ctx: _wheel_service(ctx, "greedy-lion")),
    MONKEY_CODE: GameBinding(
        game_code=MONKEY_CODE, label="Monkey Wheel", kind="wheel",
        action_field="option_id", choice_field="option_id",
        service=lambda ctx: _wheel_service(ctx, "greedy-monkey")),
}


def binding_for_code(code: str) -> Optional[GameBinding]:
    return BINDINGS.get(canonical_code(code) or "")


def binding_for_slug(slug: str) -> Optional[GameBinding]:
    for binding in BINDINGS.values():
        if slug == binding.slug:
            return binding
    return None


def all_bindings() -> List[GameBinding]:
    return list(BINDINGS.values())


def game_codes() -> List[str]:
    return list(BINDINGS)
