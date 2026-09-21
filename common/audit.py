"""Append-only audit log (memory impl; production -> DB table audit_log)."""
import time
from typing import Any, Dict, List


class AuditLog:
    def __init__(self):
        self.entries: List[Dict[str, Any]] = []
        self._seq = 0

    def record(self, actor: str, action: str, entity: str, entity_id: str,
               before=None, after=None) -> dict:
        self._seq += 1
        ent = {"audit_id": f"aud-{self._seq}", "actor": actor, "action": action,
               "entity": entity, "entity_id": entity_id, "before": before,
               "after": after, "timestamp": int(time.time() * 1000)}
        self.entries.append(ent)
        return ent

    def list(self, entity: str = "", limit: int = 100) -> List[dict]:
        items = [e for e in self.entries if not entity or e["entity"] == entity]
        return items[-limit:]
