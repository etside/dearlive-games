"""Demo bot players: fill a table that only has one human on it.

Behaviour the operator asked for
--------------------------------
One real player opens a demo table. If nobody else joins within
``DEMO_BOT_FILL_DELAY_S`` seconds (default 40), bots take the remaining seats
so the table is playable. If a second real player does join inside that window,
the fill is cancelled and the table is left alone.

The hard guard
--------------
Bots may only act in a room that was explicitly marked as a demo room, and that
marking exists in exactly one place: the ``?operator=demo`` launch path in
games/teen_patti_pro/api.py, which calls :func:`mark_demo_room` as it mints the
session. This is deliberately not an environment variable and not a heuristic
on the player id.

The previous implementation gated on ``DEMO_MODE=1`` in the process
environment. That is the wrong shape for two reasons: the flag was global, so a
single staging deployment with it set would put bots into real-money
provider sessions in the same table, and nothing tied it to a request that
actually said "demo". A room either is marked or it is not, and only one code
path can mark it.

A second guard refuses outright in production, so a misconfigured
``operator=demo`` on a production host cannot introduce bot players either.

Stealth
-------
A bot is an ordinary member: it claims a seat through the service, bets through
the service, and is settled by the service. There is no ``is_bot`` flag on any
player-facing payload, because the snapshot is built by the engine from
``room.members`` and a bot is indistinguishable there. Nothing in this module
adds a field to a snapshot. A bot is visible as a bot only in this module's
own registry, which the admin surface can read and the client cannot.
"""

import logging
import os
import random
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

# Direct runtime import (not TYPE_CHECKING): typing.get_type_hints() resolves
# string annotations against the importing module's namespace, so a
# TYPE_CHECKING-only name breaks every module that does
# `from .bot_manager import create_bot_manager` (ws.py hit exactly this).
# No cycle: service.py never imports bot_manager or ws.
from .service import TeenPattiService

log = logging.getLogger(__name__)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


# How long a lone real player waits before bots take the free seats.
FILL_DELAY_S = _env_float("DEMO_BOT_FILL_DELAY_S", 40.0)
# The reaper's cadence. A second is also the engine's sweep cadence, so a bot
# acts inside the same tick that opens betting.
TICK_S = _env_float("DEMO_BOT_TICK_S", 1.0)
# Starting stack handed to a bot. The demo wallet opens an account at
# DEMO_WALLET_STARTING_BALANCE for any player id it is asked about, so this is
# only a floor to assert against, never a funding call.
BOT_STACK = int(_env_float("DEMO_BOT_STACK", 10_000))

# Rooms explicitly opened through ?operator=demo. Process-wide on purpose: the
# HTTP handler that mints a demo session and the WebSocket hub that owns the
# BotManager are different objects in the same process, and a room has to be
# visible to both.
_DEMO_ROOMS: Set[str] = set()
_DEMO_ROOMS_LOCK = threading.Lock()


def mark_demo_room(room_id: str) -> None:
    """Mark a room as a demo table. Called only by the demo launch path.

    The single point at which a table becomes bot-eligible. If this is never
    called for a room, that room never gets a bot, whatever the environment
    says.
    """
    with _DEMO_ROOMS_LOCK:
        _DEMO_ROOMS.add(room_id)


def clear_demo_room(room_id: str) -> None:
    with _DEMO_ROOMS_LOCK:
        _DEMO_ROOMS.discard(room_id)


def is_demo_room(room_id: str) -> bool:
    with _DEMO_ROOMS_LOCK:
        return room_id in _DEMO_ROOMS


def _production() -> bool:
    return os.environ.get("APP_ENV", "sandbox").strip().lower() == "production"


# Curated realistic names (mixed regions, no "guest"/"player"/numbers)
BOT_NAMES = [
    "Rakib H", "Tanvir A", "Nusrat J", "Sakib K", "Farhan M",
    "Priya S", "Arjun D", "Meera P", "Anil V", "Kavya R",
    "Zayan", "Ayesha", "Rohan", "Sana", "Imran",
    "Hasan", "Fatima", "Kabir", "Leila", "Omar",
    "Saira", "Vikram", "Nina", "Arif", "Zara"
]

# The avatar art that actually exists in the pack. The previous list pointed at
# eight files (avatar-01..08) that are not in the repository, so every bot
# resolved to a broken image. A bot now wears exactly what a real player with
# no custom avatar wears, which is also the point: the fallback is the shared
# one, so a bot is not visually marked.
BOT_AVATAR = "/assets/games/teen-patti-pro/avatars/avatar-placeholder.svg"
BOT_FRAMES = [
    "/assets/games/teen-patti-pro/avatars/avatar-frame-navy.svg",
    "/assets/games/teen-patti-pro/avatars/frame-ring-gold-sm.svg",
]


@dataclass
class BotIdentity:
    player_id: str
    display_name: str
    avatar_url: str
    # Internal bookkeeping. None of this is ever attached to a snapshot.
    bet_round_id: str = ""
    bet_at_ms: int = 0
    bot_seed: str = field(default_factory=lambda: uuid.uuid4().hex[:16])

    def reset_for_round(self, round_id: str, now_ms: int, delay_ms: int) -> None:
        self.bet_round_id = round_id
        self.bet_at_ms = now_ms + delay_ms


class BotManager:
    """Fills demo tables that have one real player and no company.

    Real players always win seats. A bot is removed the moment a real player
    needs its seat, and a second real player joining inside the fill window
    cancels the fill entirely.
    """

    def __init__(self, service: TeenPattiService,
                 fill_delay_s: Optional[float] = None, start: bool = True):
        self.service = service
        self.fill_delay_s = FILL_DELAY_S if fill_delay_s is None else fill_delay_s
        self._bots: Dict[str, List[BotIdentity]] = {}   # room -> bots
        self._armed: Dict[str, int] = {}                # room -> deadline ms
        self._used_names: Dict[str, Set[str]] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        if start:
            # start=False leaves the reaper parked so a caller can drive ticks
            # by hand. The betting tests need that: with a live reaper, a new
            # round can open between two assertions, and the test then fails
            # for a reason that has nothing to do with the bot.
            self._thread = threading.Thread(
                target=self._run, name="tpp-demo-bots", daemon=True)
            self._thread.start()

    # -- lifecycle -------------------------------------------------------
    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop.wait(TICK_S):
            try:
                self._tick()
            except Exception:
                # The reaper must outlive any single room's fault. A crash here
                # would silently stop every demo table filling.
                log.exception("demo bot tick failed")

    def _tick(self) -> None:
        if _production():
            return
        now = int(time.time() * 1000)
        with self._lock:
            rooms = list(self._armed)
        for room_id in rooms:
            try:
                if not is_demo_room(room_id):
                    with self._lock:
                        self._armed.pop(room_id, None)
                    continue
                real = self._count_real(room_id)
                if real >= 2:
                    # Company arrived: stand the fill down.
                    with self._lock:
                        self._armed.pop(room_id, None)
                    continue
                if real == 0:
                    with self._lock:
                        self._armed.pop(room_id, None)
                        self._purge_room(room_id)
                    continue
                deadline = self._armed.get(room_id, 0)
                if now < deadline:
                    continue
                with self._lock:
                    self._armed.pop(room_id, None)
                self._fill(room_id)
            except Exception:
                log.exception("demo bot fill failed room=%s", room_id)
        # Drive betting for bots that are already seated.
        with self._lock:
            seated = {r: list(b) for r, b in self._bots.items()}
        for room_id, bots in seated.items():
            for bot in bots:
                try:
                    self._maybe_bet(room_id, bot, now)
                except Exception:
                    log.exception("demo bot bet failed room=%s bot=%s",
                                  room_id, bot.player_id)

    # -- membership ------------------------------------------------------
    def on_player_join(self, room_id: str, player_id: str,
                       is_real: bool = True) -> None:
        """Called on every WebSocket subscribe."""
        if not is_real or _production():
            return
        if not is_demo_room(room_id):
            return
        with self._lock:
            # A real player takes precedence: give up a bot seat so the human
            # is not made a spectator at a table they opened.
            self._displace_one(room_id, player_id)
            real = self._count_real(room_id)
            if real >= 2:
                self._armed.pop(room_id, None)
            elif real == 1:
                self._armed[room_id] = (int(time.time() * 1000)
                                        + int(self.fill_delay_s * 1000))
            else:
                self._armed.pop(room_id, None)

    def on_player_leave(self, room_id: str, player_id: str) -> None:
        if not is_demo_room(room_id) or _production():
            return
        with self._lock:
            if self._count_real(room_id) <= 1:
                # Down to a lone player again: re-arm, since the table is empty
                # of company and would otherwise sit idle.
                self._armed[room_id] = (int(time.time() * 1000)
                                        + int(self.fill_delay_s * 1000))
            else:
                self._purge_room(room_id)

    def _purge_room(self, room_id: str) -> None:
        """Drop every bot in a room and free its seats. Caller holds the lock."""
        for bot in self._bots.pop(room_id, []):
            self._release_seat(room_id, bot)
        self._used_names.pop(room_id, None)

    def _count_real(self, room_id: str) -> int:
        try:
            room = self.service._room(room_id)
        except Exception:
            return 0
        bots = {b.player_id for b in self._bots.get(room_id, [])}
        return sum(1 for pid in room.members if pid not in bots)

    def _seated(self, room_id: str) -> Set[str]:
        try:
            room = self.service._room(room_id)
        except Exception:
            return set()
        return {(m or {}).get("seat") for m in room.members.values()
                if (m or {}).get("seat")}

    # -- filling ---------------------------------------------------------
    def _fill(self, room_id: str) -> None:
        """Seat enough bots to make the table playable.

        Two seats, so one bot can be displaced by a real player and the table
        still has someone to play against. Never displaces a seated human.
        """
        room = self.service._room(room_id)
        seats = list(self.service.config.seats)
        while True:
            taken = self._seated(room_id)
            free = [s for s in seats if s not in taken]
            if len(taken) >= 2 or not free:
                break
            if not self._spawn_bot(room_id):
                break

    def _spawn_bot(self, room_id: str) -> bool:
        """Seat one bot. Returns False if it could not be seated."""
        bot = self._identity(room_id)
        try:
            # The demo wallet opens an account at its starting balance for any
            # player id, so there is nothing to fund here. The old code called
            # debit(-10000) to "credit" the bot; the adapter rejects a
            # non-positive amount, so every spawn raised and was swallowed, and
            # no bot ever appeared.
            out = self.service.claim_seat(room_id, bot.player_id, "auto")
        except Exception as exc:
            log.warning("demo bot could not be seated room=%s: %s",
                        room_id, exc)
            return False
        if not out.get("seat"):
            return False
        bot.reset_for_round("", 0, 0)
        self._bots.setdefault(room_id, []).append(bot)
        try:
            self.service.audit.record(
                "system", "bot.seated", "bot", bot.player_id,
                after={"room_id": room_id, "seat": out.get("seat"),
                       "source": "demo-fill"})
        except Exception:
            pass
        return True

    def _identity(self, room_id: str) -> BotIdentity:
        used = self._used_names.setdefault(room_id, set())
        available = [n for n in BOT_NAMES if n not in used] or BOT_NAMES
        name = random.choice(available)
        used.add(name)
        return BotIdentity(
            player_id="demo_bot_" + uuid.uuid4().hex[:12],
            display_name=name,
            avatar_url=random.choice(BOT_FRAMES),
        )

    def _displace_one(self, room_id: str, real_player_id: str) -> None:
        """Release one bot seat for a real player. Caller holds the lock."""
        try:
            room = self.service._room(room_id)
        except Exception:
            return
        mine = (room.members.get(real_player_id) or {}).get("seat")
        if mine:
            return  # the human already has a seat; nothing to give up
        for bot in list(self._bots.get(room_id, [])):
            self._release_seat(room_id, bot)
            return

    def _release_seat(self, room_id: str, bot: BotIdentity) -> None:
        """Free a bot's seat and re-seat the human if one was waiting."""
        bots = self._bots.get(room_id, [])
        if bot in bots:
            bots.remove(bot)
        try:
            self.service.leave_table(room_id, bot.player_id)
        except Exception as exc:
            log.warning("demo bot seat release failed room=%s: %s", room_id, exc)
        try:
            self.service.audit.record(
                "system", "bot.released", "bot", bot.player_id,
                after={"room_id": room_id, "reason": "real-player-priority"})
        except Exception:
            pass
        if not bots:
            self._bots.pop(room_id, None)

    # -- playing ---------------------------------------------------------
    def _maybe_bet(self, room_id: str, bot: BotIdentity, now_ms: int) -> None:
        try:
            room = self.service._room(room_id)
        except Exception:
            return
        r = room.round
        if r is None or r.status.value != "BETTING_OPEN":
            # New round (or betting closed): clear so the next window re-arms.
            if r is not None and r.round_id != bot.bet_round_id:
                bot.bet_round_id = ""
            return
        if bot.bet_round_id != r.round_id:
            # First look at this round: give it a human delay, jittered so two
            # bots do not bet on the same millisecond.
            delay = random.randint(2500, 9000)
            bot.reset_for_round(r.round_id, now_ms, now_ms + delay)
            return
        if now_ms < bot.bet_at_ms:
            return
        position = self._choose_position(room, bot)
        amount = self._choose_chip()
        try:
            self.service.place_bet(
                room_id, bot.player_id, position, amount,
                "demo-bot-%s-%s" % (bot.player_id, r.round_id))
            log.info("demo bot bet room=%s seat=%s pos=%s amt=%s",
                     room_id, bot.player_id[-4:], position, amount)
        except Exception as exc:
            # An out-of-window or duplicate bet is normal; the next round
            # re-arms. Anything else is worth seeing.
            log.info("demo bot bet skipped room=%s: %s", room_id, exc)
            bot.bet_round_id = r.round_id

    def _choose_position(self, room, bot: BotIdentity) -> str:
        """Pick a position to back, spreading the table's money."""
        seats = list(self.service.config.seats)
        pots: Dict[str, int] = {}
        if room.round:
            for bet in room.round.bets:
                if bet.status in ("accepted", "won"):
                    pots[bet.position] = pots.get(bet.position, 0) + bet.amount
        # Favour the quietest seat most of the time, so the pot is not stacked
        # on one side, but not always: a flat distribution reads as a bot.
        order = sorted(seats, key=lambda s: (pots.get(s, 0), s))
        if random.random() < 0.7:
            return order[0]
        return random.choice(seats)

    def _choose_chip(self) -> int:
        """Pick a chip the table actually accepts.

        Read from the live config rather than hardcoded. The previous version
        returned 1000/10000/50000 while the shipped config's denominations are
        (20, 100, 500, 1000), so 70% of bets were rejected with "Amount not a
        configured denomination" -- logged at info and skipped, which is why the
        table looked populated but idle. Weights favour the larger chips, which
        is what the bar shows first, but every candidate is a real denomination.
        """
        denoms = [int(d) for d in (getattr(self.service.config, "denoms", ()) or ())
                  if int(d) > 0]
        if not denoms:
            return 0
        if len(denoms) == 1:
            return denoms[0]
        # Largest first, and pick from the top two thirds: a table where every
        # opponent opens with the max chip is not a table anyone plays.
        ordered = sorted(denoms, reverse=True)
        pool = ordered[:max(2, (len(ordered) * 2) // 3)]
        r = random.random()
        if r < 0.5 and len(pool) > 1:
            return pool[0]
        if r < 0.85 and len(pool) > 2:
            return pool[1]
        return random.choice(pool)

    # -- introspection (admin only) --------------------------------------
    def bot_ids(self, room_id: str) -> Set[str]:
        with self._lock:
            return {b.player_id for b in self._bots.get(room_id, [])}

    def room_report(self, room_id: str) -> dict:
        with self._lock:
            bots = list(self._bots.get(room_id, []))
            return {
                "room_id": room_id,
                "demo_room": is_demo_room(room_id),
                "bots": [{"player_id": b.player_id,
                          "display_name": b.display_name}
                         for b in bots],
                "bot_count": len(bots),
                "real_players": self._count_real(room_id),
                "fill_armed": room_id in self._armed,
                "fill_deadline_ms": self._armed.get(room_id, 0),
            }


def create_bot_manager(service: TeenPattiService) -> BotManager:
    return BotManager(service)
