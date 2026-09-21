"""Common game-engine interface. Every game (Teen Patti now; Greedy and
Animal Wheel later) implements this. The interface is transport-free:
service layers (REST/WS) call it; it never touches sockets, wallets or DB.
"""
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class CommonGameEngine(ABC):
    game_id: str = ""

    # --- sessions ---
    @abstractmethod
    def createSession(self, player_id: str, room_id: str) -> Dict[str, Any]: ...
    @abstractmethod
    def joinSession(self, session_id: str, player_id: str) -> Dict[str, Any]: ...
    @abstractmethod
    def leaveSession(self, session_id: str, player_id: str) -> Dict[str, Any]: ...

    # --- state / actions ---
    @abstractmethod
    def getState(self, room_id: str, viewer_player_id: str) -> Dict[str, Any]:
        """Visibility-safe snapshot (hole cards etc. redacted pre-result)."""

    @abstractmethod
    def validateAction(self, room_id: str, player_id: str, action: Dict[str, Any]) -> Dict[str, Any]:
        """Pure validation. Returns {ok, code?, message?}. No side effects."""

    @abstractmethod
    def applyAction(self, room_id: str, player_id: str, action: Dict[str, Any]) -> Dict[str, Any]:
        """Validate + apply + return resulting events. Atomic per room."""

    # --- rounds ---
    @abstractmethod
    def startRound(self, room_id: str) -> Dict[str, Any]: ...
    @abstractmethod
    def endRound(self, room_id: str) -> Dict[str, Any]:
        """Close betting (BETTING_CLOSED). Separate from result."""

    @abstractmethod
    def calculateResult(self, room_id: str, round_id: str) -> Dict[str, Any]:
        """Authoritative result. Deterministic given stored seed + actions."""

    @abstractmethod
    def settle(self, room_id: str, round_id: str) -> Dict[str, Any]:
        """Idempotent settlement rows. Exactly-once per bet enforced by caller
        via UNIQUE settlement.bet_id; engine output is deterministic."""

    @abstractmethod
    def cancel(self, room_id: str, round_id: str, reason: str) -> Dict[str, Any]: ...

    # --- reliability ---
    @abstractmethod
    def handleTimeout(self, room_id: str, round_id: str) -> Dict[str, Any]:
        """Server timer expiry: close betting / force result path."""

    @abstractmethod
    def handleReconnect(self, session_id: str, last_seen_seq: int) -> Dict[str, Any]:
        """Snapshot + missed events since seq (visibility-safe)."""
