/** Server-authoritative game state. Replaces any client-side card/winner logic. */

import { useCallback, useEffect, useRef, useState } from "react";
import { GameSocket } from "./socket";
import { fetchSnapshot, placeBet as postBet } from "./api";
import type {
  ConnectionStatus,
  Seat,
  ServerEvent,
  SnapshotResponse,
} from "./types";
import { SEATS } from "./types";

export interface GameState {
  round: {
    roundId: string;
    roundNo: number;
    status: string;
    stateVersion: number;
    serverTime: string;
    bettingEndAt: string;
  } | null;
  seats: Record<Seat, { playerId: string | null; name: string; isMe: boolean }>;
  hands: Record<Seat, unknown[]>;
  panels: Record<Seat, { pot: number; myBet: number; multiplier: number }>;
  mySeat: Seat | null;
  isSpectator: boolean;
  balance: number;
  potTotal: number;
  myBet: number;
  winnerSeat: Seat | null;
  payout: number;
  connectionStatus: ConnectionStatus;
}

const EMPTY: GameState = {
  round: null,
  seats: {
    A: { playerId: null, name: "", isMe: false },
    B: { playerId: null, name: "", isMe: false },
    C: { playerId: null, name: "", isMe: false },
  },
  hands: { A: [], B: [], C: [] },
  panels: {
    A: { pot: 0, myBet: 0, multiplier: 2.9 },
    B: { pot: 0, myBet: 0, multiplier: 2.9 },
    C: { pot: 0, myBet: 0, multiplier: 2.9 },
  },
  mySeat: null,
  isSpectator: false,
  balance: 0,
  potTotal: 0,
  myBet: 0,
  winnerSeat: null,
  payout: 0,
  connectionStatus: "offline",
};

const SNAPSHOT_EVENTS = new Set([
  "snapshot",
  "round.created",
  "round.updated",
  "round.opened",
  "betting.closed",
  "cards.dealt",
  "result.declared",
  "settlement.completed",
  "round.closed",
  "balance.updated",
]);

export function useGameState(
  wsUrl: string,
  sessionToken: string,
  roomId: string,
) {
  const [state, setState] = useState<GameState>(EMPTY);
  const socketRef = useRef<GameSocket | null>(null);
  const consumedEvents = useRef(new Set<string>());

  const applySnapshot = useCallback((snap: SnapshotResponse | Record<string, never>) => {
    const s = snap as SnapshotResponse;
    if (!s || !s.round) return;

    const panels = { ...EMPTY.panels };
    for (const seat of SEATS) {
      panels[seat] = {
        pot: Number(s.seats?.[seat]?.pot) || 0,
        myBet: Number(s.seats?.[seat]?.bet) || 0,
        multiplier: Number(s.seats?.[seat]?.multiplier) || 2.9,
      };
    }

    const seats = { ...EMPTY.seats };
    for (const seat of SEATS) {
      const raw = s.seats?.[seat];
      seats[seat] = {
        playerId: raw?.player_id != null ? String(raw.player_id) : null,
        name: String(raw?.name ?? ""),
        isMe: Boolean(raw?.is_me),
      };
    }

    setState((prev) => ({
      ...prev,
      round: {
        roundId: String(s.round.round_id),
        roundNo: Number(s.round.round_no) || 0,
        status: String(s.round.status ?? ""),
        stateVersion: Number(s.round.state_version) || 0,
        serverTime: String(s.round.server_time ?? ""),
        bettingEndAt: String(s.round.betting_end_at ?? ""),
      },
      seats,
      hands: (s.hands ?? prev.hands) as Record<Seat, unknown[]>,
      panels,
      mySeat: (s.my_seat ?? null) as Seat | null,
      // Spectator only when the server says so explicitly (my_seat: null).
      // An absent field keeps the previous value — never guess.
      isSpectator: s.my_seat === null ? true : s.my_seat ? false : prev.isSpectator,
      balance: Number(s.balance) || prev.balance,
      potTotal: Number(s.pot_total) || 0,
      myBet: Number(s.my_bet) || 0,
      winnerSeat: (s.winner_seat ?? null) as Seat | null,
      payout: Number(s.payout) || 0,
    }));
  }, []);

  useEffect(() => {
    if (!sessionToken) return;

    // Initial snapshot — the only bootstrap. No local round fabrication.
    fetchSnapshot(sessionToken, roomId).then((snap) => {
      if (snap) applySnapshot(snap);
    });

    const socket = new GameSocket(wsUrl);
    socketRef.current = socket;

    const unsubscribe = socket.on((event: ServerEvent) => {
      // Idempotency — ignore duplicate event_id.
      if (consumedEvents.current.has(event.eventId)) return;
      consumedEvents.current.add(event.eventId);
      if (consumedEvents.current.size > 5000) consumedEvents.current.clear();

      switch (event.type) {
        case "connected":
          setState((prev) => ({ ...prev, connectionStatus: "live" }));
          break;
        case "disconnected":
          setState((prev) => ({ ...prev, connectionStatus: "offline" }));
          break;
        case "bet.accepted":
          break; // Confirmation path handled by the bet caller via snapshot.
        default:
          if (SNAPSHOT_EVENTS.has(event.type)) {
            applySnapshot(event.payload);
          } else if (event.payload?.round) {
            applySnapshot(event.payload);
          }
      }
    });

    setState((prev) => ({ ...prev, connectionStatus: "connecting" }));
    socket.connect(sessionToken, roomId);

    const onVisible = () => {
      if (!document.hidden) {
        fetchSnapshot(sessionToken, roomId).then((snap) => {
          if (snap) applySnapshot(snap);
        });
      }
    };
    document.addEventListener("visibilitychange", onVisible);

    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      unsubscribe();
      socket.close();
      socketRef.current = null;
    };
  }, [wsUrl, sessionToken, roomId, applySnapshot]);

  const placeBetAction = useCallback(
    (seat: Seat, amount: number) => {
      const roundId = state.round?.roundId;
      if (!roundId) return Promise.resolve({ ok: false, error: "No round" });
      return postBet(sessionToken, roundId, seat, amount);
    },
    [state.round, sessionToken],
  );

  return { state, placeBet: placeBetAction };
}
