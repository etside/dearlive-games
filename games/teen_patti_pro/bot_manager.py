"""Stealth bot players for demo mode only.

Bots fill empty seats in demo rooms after 8 seconds if no real player joins.
They are indistinguishable from real players to other players.
"""

import os
import random
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional

if TYPE_CHECKING:
    from .service import TeenPattiService
else:
    from .service import TeenPattiService, ServiceError
from common.wallet import WalletError, InsufficientBalance
from common.wallet import WalletError, InsufficientBalance


# Curated realistic names (mixed regions, no "guest"/"player"/numbers)
BOT_NAMES = [
    "Rakib H", "Tanvir A", "Nusrat J", "Sakib K", "Farhan M",
    "Priya S", "Arjun D", "Meera P", "Anil V", "Kavya R",
    "Zayan", "Ayesha", "Rohan", "Sana", "Imran",
    "Hasan", "Fatima", "Kabir", "Leila", "Omar",
    "Saira", "Vikram", "Nina", "Arif", "Zara"
]

# Avatar pool (same as real players use)
BOT_AVATARS = [
    "/assets/teen-patti-pro/avatars/avatar-01.svg",
    "/assets/teen-patti-pro/avatars/avatar-02.svg",
    "/assets/teen-patti-pro/avatars/avatar-03.svg",
    "/assets/teen-patti-pro/avatars/avatar-04.svg",
    "/assets/teen-patti-pro/avatars/avatar-05.svg",
    "/assets/teen-patti-pro/avatars/avatar-06.svg",
    "/assets/teen-patti-pro/avatars/avatar-07.svg",
    "/assets/teen-patti-pro/avatars/avatar-08.svg",
]


@dataclass
class BotIdentity:
    player_id: str
    display_name: str
    avatar_url: str
    is_bot: bool = True
    bot_seed: str = field(default_factory=lambda: uuid.uuid4().hex[:16])


class BotManager:
    """Manages stealth bot players in demo rooms.

    Bots are only spawned in demo mode (operator=demo), never in production.
    They fill empty seats after 8 seconds if no other real player joins.
    Real players always displace bots.
    """

    def __init__(self, service: TeenPattiService):
        self.service = service
        self._bots: Dict[str, List[BotIdentity]] = {}  # room_id -> [bot identities]
        self._timers: Dict[str, threading.Timer] = {}
        self._lock = threading.Lock()
        self._used_names: Dict[str, List[str]] = {}  # room -> used names

    def _is_demo_mode(self) -> bool:
        """Check if we're in demo mode (never run bots in production)."""
        return os.environ.get("APP_ENV", "sandbox").lower() != "production" \
            and os.environ.get("DEMO_MODE", "0") == "1"

    def on_player_join(self, room_id: str, player_id: str, is_real: bool = True) -> None:
        """Called when a player (real or bot) joins a room."""
        if not self._is_demo_mode():
            return

        with self._lock:
            # Remove any existing bot from the seat this real player took
            if is_real:
                bots = self._bots.get(room_id, [])
                for bot in bots:
                    if bot.player_id in self.service._room(room_id).members:
                        self._remove_bot_from_room(room_id, bot)
                        break

                # Cancel any pending bot spawn timer
                if room_id in self._timers:
                    self._timers[room_id].cancel()
                    del self._timers[room_id]

                # If seats are now full, no need to spawn
                if self._count_real_players(room_id) >= 3:
                    return

                # Schedule bot spawn after 8 seconds if only 1 real player
                if self._count_real_players(room_id) == 1:
                    self._schedule_bot_spawn(room_id)

    def _count_real_players(self, room_id: str) -> int:
        """Count real (non-bot) players in a room."""
        try:
            room = self.service._room(room_id)
            count = 0
            for pid in room.members:
                if pid not in [b.player_id for b in self._bots.get(room_id, [])]:
                    count += 1
            return count
        except Exception:
            return 0

    def _schedule_bot_spawn(self, room_id: str) -> None:
        """Schedule bot spawn after 8 seconds."""
        def spawn():
            with self._lock:
                # Double-check conditions haven't changed
                real_count = self._count_real_players(room_id)
                if real_count >= 3:
                    return
                if real_count == 0:
                    return

                # Spawn bots to fill remaining seats
                seats_needed = 3 - self._count_real_players(room_id)
                for _ in range(seats_needed):
                    self._spawn_bot(room_id)

        timer = threading.Timer(8.0, spawn)
        timer.daemon = True
        self._timers[room_id] = timer
        timer.start()

    def _spawn_bot(self, room_id: str) -> None:
        """Spawn a bot to fill an empty seat."""
        try:
            # Generate bot identity
            bot = self._generate_bot_identity(room_id)

            # Ensure wallet has starting balance
            self.service.wallet.debit(
                bot.player_id, -10000,  # negative = credit
                f"bot_init:{room_id}",
                f"bot_init_{bot.player_id}"
            )

            # Seat the bot
            self.service.claim_seat(room_id, bot.player_id, "auto")

            # Track the bot
            if room_id not in self._bots:
                self._bots[room_id] = []
            self._bots[room_id].append(bot)

            # Log to audit
            self.service.audit.record(
                "system", "bot.spawned", "bot",
                f"{bot.player_id} seated in {room_id}")

            # Schedule bot actions for this round
            self._schedule_bot_actions(room_id, bot)

        except Exception as e:
            # Silent failure - bots are best effort
            pass

    def _generate_bot_identity(self, room_id: str) -> BotIdentity:
        """Generate a realistic bot identity."""
        used = self._used_names.get(room_id, [])

        # Pick unused name
        available = [n for n in BOT_NAMES if n not in used]
        if not available:
            available = BOT_NAMES  # fallback

        name = random.choice(available)
        self._used_names.setdefault(room_id, []).append(name)

        return BotIdentity(
            player_id=f"player_{uuid.uuid4().hex[:12]}",
            display_name=name,
            avatar_url=random.choice(BOT_AVATARS)
        )

    def _schedule_bot_actions(self, room_id: str, bot: BotIdentity) -> None:
        """Schedule bot's bet action with human-like timing."""
        def act():
            try:
                # Wait for BETTING_OPEN phase
                room = self.service._room(room_id)
                if not room.round or room.round.status.value != "BETTING_OPEN":
                    return

                # Human-like delay: 3-8 seconds after round start
                delay = random.uniform(3, 8)
                time.sleep(delay)

                # Check if round still in BETTING_OPEN
                room = self.service._room(room_id)
                if not room.round or room.round.status.value != "BETTING_OPEN":
                    return

                # Choose seat with lowest current bet (slight preference)
                seat = self._choose_seat_for_bot(room, bot.player_id)
                if not seat:
                    return

                # Choose chip (weighted: 60% small, 30% mid, 10% large)
                chip = self._choose_chip()

                # Place bet
                self.service.place_bet(
                    room_id=room_id,
                    player_id=bot.player_id,
                    position=seat,
                    amount=chip,
                    idempotency_key=f"bot_{bot.player_id}_{int(time.time()*1000)}"
                )
            except Exception:
                pass  # Silent - bots are best effort

        threading.Thread(target=act, daemon=True).start()

    def _choose_seat_for_bot(self, room, bot_id: str) -> Optional[str]:
        """Choose a seat for the bot to bet on."""
        # Find bot's own seat
        bot_seat = None
        for pos, pid in room.members.items():
            if pid == bot_id:
                bot_seat = pos
                break

        # Prefer seats with lowest current bet
        pots = {}
        if room.round:
            for bet in room.round.bets:
                if bet.status in ("accepted", "won"):
                    pots[bet.position] = pots.get(bet.position, 0) + bet.amount

        # Sort positions by current pot (ascending)
        positions = ["A", "B", "C"]
        positions.sort(key=lambda p: pots.get(p, 0))

        for pos in positions:
            if pos != bot_seat:  # Don't bet on own seat preference
                return pos

        return bot_seat if bot_seat else "A"

    def _choose_chip(self) -> int:
        """Choose chip denomination with weighted distribution."""
        r = random.random()
        if r < 0.6:
            return 1000   # 1K - 60%
        elif r < 0.9:
            return 10000  # 10K - 30%
        else:
            return 50000  # 50K - 10%

    def _remove_bot_from_room(self, room_id: str, bot: BotIdentity) -> None:
        """Remove a bot from a room (when real player takes its seat)."""
        try:
            bots = self._bots.get(room_id, [])
            if bot in bots:
                bots.remove(bot)
                # Return seat to available
                self.service._room(room_id).leave(bot.player_id)
                # Return bot's balance
                try:
                    bal = self.service.wallet.get_balance(bot.player_id)
                    if bal.available > 0:
                        self.service.wallet.debit(
                            bot.player_id, bal.available,
                            f"bot_remove:{room_id}",
                            f"bot_remove_{bot.player_id}"
                        )
                except Exception:
                    pass
        except Exception:
            pass

    def on_round_end(self, room_id: str) -> None:
        """Clean up bots after round ends if needed."""
        with self._lock:
            bots = self._bots.get(room_id, [])
            for bot in bots:
                # Bots persist across rounds in demo
                pass

    def on_room_empty(self, room_id: str) -> None:
        """Clean up when room becomes empty."""
        with self._lock:
            if room_id in self._bots:
                del self._bots[room_id]
            if room_id in self._used_names:
                del self._used_names[room_id]
            if room_id in self._timers:
                self._timers[room_id].cancel()
                del self._timers[room_id]


def create_bot_manager(service: "TeenPattiService") -> "BotManager":
    """Factory to create bot manager."""
    return BotManager(service)