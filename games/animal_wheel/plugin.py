"""Animal Wheel engine plugin registration (Game 3, planned stub)."""
from common.plugins import engine_plugin


@engine_plugin("animal-food-wheel", "Animal/Food Wheel", "0.1.0", status="planned",
               tbc=("G2-BR-02", "G2-BR-03", "G2-BR-04", "G2-BR-05"),
               description="Central-character wheel betting (BRD Game 2). Not developed.")
def make_room(*args, **kwargs):
    from .engine import AnimalWheelEngine  # noqa
    raise NotImplementedError("Animal Wheel not developed yet")
