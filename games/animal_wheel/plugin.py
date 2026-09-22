"""Baby King engine plugin registration (Game 3, planned stub).

Display name "Baby King" on the wheel slot (DearLive current wheel logic —
WheelDriver: weighted HMAC pick + landing angle — same as Food Wheel).
Deterministic outcome logic lives in .engine (parity); betting/service
integration stays planned until business confirms rules.
"""
from common import plugins
from common.plugins import engine_plugin


@engine_plugin("baby-king", "Baby King", "0.1.0", status="planned",
               tbc=("G2-BR-02", "G2-BR-03", "G2-BR-04", "G2-BR-05"),
               description="Baby King wheel betting (wheel slot). Outcome engine ready; not playable yet.",
               entry="/baby-king/", dearlive_code="food_wheel")
def make_room(*args, **kwargs):
    from .engine import BabyKingEngine  # noqa
    raise NotImplementedError("Baby King not developed yet")


# Back-compat aliases (previous stub ids + DearLive food-wheel code).
plugins.alias("animal-food-wheel", "baby-king")
plugins.alias("food-wheel", "baby-king")
plugins.alias("food_wheel", "baby-king")
