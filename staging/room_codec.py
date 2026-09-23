"""Room snapshot codec for serverless staging (no engine changes).

Rooms/Rounds/Bets are dataclasses + plain objects with JSON-friendly fields
(Card = tuple, RoundStatus = str Enum). encode() freezes a room to JSON;
decode_*() rebuilds live objects (fresh locks) without touching engine code.
"""
import dataclasses
import json
from enum import Enum
from typing import Any, Dict

_MARKERS = {"__tuple__", "__enum__", "__set__"}


def encode(obj: Any) -> Any:
    if isinstance(obj, Enum):
        return {"__enum__": f"{type(obj).__module__}.{type(obj).__name__}",
                "value": obj.value}
    if isinstance(obj, tuple):
        return {"__tuple__": [encode(x) for x in obj]}
    if isinstance(obj, (set, frozenset)):
        return {"__set__": [encode(x) for x in obj]}
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {"__class__": f"{type(obj).__module__}.{type(obj).__name__}",
                "fields": {f.name: encode(getattr(obj, f.name))
                           for f in dataclasses.fields(obj)}}
    if isinstance(obj, dict):
        return {str(k): encode(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [encode(x) for x in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    if hasattr(obj, "__dict__"):
        return {"__class__": f"{type(obj).__module__}.{type(obj).__name__}",
                "fields": {k: encode(v) for k, v in vars(obj).items()
                           if not k.startswith("_lock") and k != "lock"}}
    raise TypeError(f"unencodable {type(obj)}")


def _resolve(classname: str, registry: Dict[str, type]) -> type:
    if classname in registry:
        return registry[classname]
    mod, _, name = classname.rpartition(".")
    if not mod:
        raise TypeError(f"unknown class {classname}")
    import importlib
    return getattr(importlib.import_module(mod), name)


def decode(node: Any, registry: Dict[str, type]) -> Any:
    if isinstance(node, dict) and set(node) == {"__enum__", "value"}:
        cls = _resolve(node["__enum__"], registry)
        return cls(node["value"])
    if isinstance(node, dict) and set(node) == {"__tuple__"}:
        return tuple(decode(x, registry) for x in node["__tuple__"])
    if isinstance(node, dict) and set(node) == {"__set__"}:
        return {decode(x, registry) for x in node["__set__"]}
    if isinstance(node, dict) and set(node) == {"__class__", "fields"}:
        cls = _resolve(node["__class__"], registry)
        fields = {k: decode(v, registry) for k, v in node["fields"].items()}
        if dataclasses.is_dataclass(cls):
            known = {f.name for f in dataclasses.fields(cls)}
            fields = {k: v for k, v in fields.items() if k in known}
            return cls(**fields)
        obj = cls.__new__(cls)
        obj.__dict__.update(fields)
        import threading
        if "lock" not in obj.__dict__:
            obj.lock = threading.Lock()
        return obj
    if isinstance(node, dict):
        return {k: decode(v, registry) for k, v in node.items()}
    if isinstance(node, list):
        return [decode(x, registry) for x in node]
    return node


def dumps(obj: Any) -> str:
    return json.dumps(encode(obj), separators=(",", ":"))


def loads(raw: str, registry: Dict[str, type]) -> Any:
    return decode(json.loads(raw), registry)
