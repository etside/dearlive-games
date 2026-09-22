"""Teen Patti Pro engine plugin registration (Game 1, live)."""
from common.plugins import engine_plugin
from .config import DEFAULT_CONFIG
from .engine import Room


@engine_plugin("teen-patti-pro", "Teen Patti Pro", "1.1.0", status="live",
               tbc=DEFAULT_CONFIG.tbc,
               description="3-seat Teen Patti guessing game; server-authoritative.")
def make_room(room_id: str, config=DEFAULT_CONFIG) -> Room:
    return Room(room_id, config)
