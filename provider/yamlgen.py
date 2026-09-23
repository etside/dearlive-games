"""Minimal YAML emitter for the OpenAPI document.

Strings are emitted as JSON double-quoted scalars, which is valid YAML, so the
output needs no escaping logic and round-trips through any YAML parser. Only
the subset used by the spec (dict/list/str/int/float/bool/None) is supported.
"""
import json


def _scalar(value) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return json.dumps(str(value), ensure_ascii=False)


def _key(name) -> str:
    return json.dumps(str(name), ensure_ascii=False)


def _emit(value, indent: int, lines: list) -> None:
    pad = "  " * indent
    if isinstance(value, dict):
        if not value:
            lines[-1] += " {}"
            return
        for key, item in value.items():
            if isinstance(item, (dict, list)) and item:
                lines.append(f"{pad}{_key(key)}:")
                _emit(item, indent + 1, lines)
            elif isinstance(item, dict):
                lines.append(f"{pad}{_key(key)}: {{}}")
            elif isinstance(item, list):
                lines.append(f"{pad}{_key(key)}: []")
            else:
                lines.append(f"{pad}{_key(key)}: {_scalar(item)}")
        return
    if isinstance(value, list):
        if not value:
            lines[-1] += " []"
            return
        for item in value:
            if isinstance(item, dict) and item:
                first = True
                for key, sub in item.items():
                    prefix = f"{pad}- " if first else f"{pad}  "
                    if isinstance(sub, (dict, list)) and sub:
                        lines.append(f"{prefix}{_key(key)}:")
                        _emit(sub, indent + 2, lines)
                    elif isinstance(sub, dict):
                        lines.append(f"{prefix}{_key(key)}: {{}}")
                    elif isinstance(sub, list):
                        lines.append(f"{prefix}{_key(key)}: []")
                    else:
                        lines.append(f"{prefix}{_key(key)}: {_scalar(sub)}")
                    first = False
            elif isinstance(item, list) and item:
                lines.append(f"{pad}-")
                _emit(item, indent + 1, lines)
            else:
                lines.append(f"{pad}- {_scalar(item)}")
        return
    lines.append(f"{pad}{_scalar(value)}")


def to_yaml(data) -> str:
    lines: list = []
    _emit(data, 0, lines)
    return "\n".join(lines) + "\n"
