"""Teen Patti Pro engine plugin registration (Game 1, live).

Display name "Teen Patti Pro" (DearLive current code `teen_patti` resolves
here as an alias — same engine, same records)."""
from common import plugins
from common.plugins import engine_plugin
from .config import DEFAULT_CONFIG
from .engine import Room


@engine_plugin("teen-patti-pro", "Teen Patti Pro", "1.1.0", status="live",
               tbc=DEFAULT_CONFIG.tbc,
               description="3-seat Teen Patti guessing game; server-authoritative.",
               entry="/teen-patti-pro/", dearlive_code="teen_patti")
def make_room(room_id: str, config=DEFAULT_CONFIG) -> Room:
    return Room(room_id, config)


# DearLive-current code compat (Uradhura seed internalCode `teen_patti`).
plugins.alias("teen_patti", "teen-patti-pro")
