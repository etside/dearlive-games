"""Greedy engine plugin registration (Game 2, planned stub)."""
from common.plugins import engine_plugin


@engine_plugin("greedy", "Greedy", "0.1.0", status="planned",
               tbc=("G1-BR-03", "G1-BR-04", "G1-BR-05"),
               description="Circular food/ingredient betting (BRD Game 1). Not developed.")
def make_room(*args, **kwargs):
    from .engine import GreedyEngine  # noqa
    raise NotImplementedError("Greedy not developed yet")
