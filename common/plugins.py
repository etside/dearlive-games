"""Engine plugin registry — games are versioned, self-registering plugins.

Game 1 (Teen Patti Pro) is production code; Games 2/3 register as stubs with
status="planned" so the platform, API routing, admin and docs treat every game
uniformly. Adding a game = new module + @engine_plugin decorator, zero core edits.

Each plugin declares: id, name, version, status (live|planned|disabled),
config schema (TBC flags), and engine factory. Unknown ids NEVER fall back to
another game (fail closed).
"""
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional


@dataclass(frozen=True)
class EnginePlugin:
    game_id: str
    name: str
    version: str
    status: str  # live | planned | disabled
    tbc: tuple = ()
    engine_factory: Optional[Callable] = None
    description: str = ""
    entry: str = ""
    dearlive_code: str = ""


_REGISTRY: Dict[str, EnginePlugin] = {}


def engine_plugin(game_id: str, name: str, version: str, status: str = "live",
                  tbc: tuple = (), description: str = "", entry: str = "",
                  dearlive_code: str = ""):
    def deco(factory: Callable):
        if game_id in _REGISTRY:
            raise ValueError(f"duplicate engine plugin: {game_id}")
        _REGISTRY[game_id] = EnginePlugin(game_id, name, version, status, tbc,
                                          factory if status == "live" else None,
                                          description, entry, dearlive_code)
        return factory
    return deco


class UnknownGame(Exception):
    pass


class GameDisabled(Exception):
    pass


def get(game_id: str) -> EnginePlugin:
    try:
        return _REGISTRY[game_id]
    except KeyError:
        raise UnknownGame(f"unknown game {game_id!r}; registered: {sorted(_REGISTRY)}")


def alias(new_id: str, existing_id: str):
    """Register new_id as an alias of an already-registered game (same
    status/factory/description). Used for DearLive-code compat
    (e.g. teen_patti -> teen-patti-pro) without duplicating engines."""
    src = get(existing_id)
    if new_id in _REGISTRY:
        raise ValueError(f"duplicate engine plugin: {new_id}")
    _REGISTRY[new_id] = EnginePlugin(new_id, src.name, src.version,
                                     src.status, src.tbc, src.engine_factory,
                                     src.description + f" (alias of {existing_id})",
                                     src.entry, src.dearlive_code)


def create(game_id: str, *args, **kwargs):
    plugin = get(game_id)
    if plugin.status != "live" or plugin.engine_factory is None:
        raise GameDisabled(f"game {game_id!r} is {plugin.status}, not playable")
    return plugin.engine_factory(*args, **kwargs)


def catalog() -> List[dict]:
    return [{"game_id": p.game_id, "name": p.name, "version": p.version,
             "status": p.status, "tbc": list(p.tbc),
             "description": p.description, "entry": p.entry,
             "dearlive_code": p.dearlive_code} for p in _REGISTRY.values()]


def import_builtin_games():
    """Import all bundled game modules so their decorators run. Safe to call twice."""
    import games.teen_patti_pro.plugin  # noqa: F401
    import games.greedy.plugin  # noqa: F401
    import games.animal_wheel.plugin  # noqa: F401
