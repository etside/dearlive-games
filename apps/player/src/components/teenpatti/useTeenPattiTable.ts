/**
 * Table view-model for TeenPattiGame. Thin adapter over the server-authoritative
 * useGameState + useServerTimer — no card, timer, or winner logic lives here.
 */

import { useCallback, useMemo, useRef, useState } from "react";
import { API_BASE } from "../../config/apiBase";
import { useGameState } from "./useGameState";
import { useServerTimer } from "./useServerTimer";
import type { Card, Player, Seat } from "./types";
import { SEATS } from "./types";

const CARD_RANK_PNG: Record<string, string> = {
  a: "A", "1": "A",
  "2": "2", "3": "3", "4": "4", "5": "5",
  "6": "6", "7": "7", "8": "8", "9": "9", "10": "10",
  j: "J", "11": "J", q: "Q", "12": "Q", k: "K", "13": "K",
};
const CARD_SUIT_PNG: Record<string, string> = {
  s: "spade", spade: "spade",
  h: "heart", heart: "heart",
  d: "diamond", diamond: "diamond",
  c: "club", club: "club",
};

/** Real deck PNG URL, e.g. card-A-spade.png. Null when unmappable (text fallback). */
export function cardFacePng(rank?: string, suit?: string): string | null {
  if (!rank || !suit) return null;
  const r = CARD_RANK_PNG[rank.toLowerCase()];
  const s = CARD_SUIT_PNG[suit.toLowerCase()];
  if (!r || !s) return null;
  return `/assets/games/teen-patti-pro/cards/card-${r}-${s}.png`;
}

export const CARD_BACK_PNG = "/assets/games/teen-patti-pro/cards/card-back-teenpatti.png";

function normalizeCard(raw: unknown): Card | null {
  if (!raw) return null;
  if (typeof raw === "object") {
    const o = raw as Record<string, unknown>;
    const faceUp = Boolean(o.revealed ?? o.faceUp ?? o.face_up ?? true);
    if (!faceUp) return { faceUp: false };
    const rank = String(o.rank ?? "").toLowerCase();
    const suitRaw = String(o.suit ?? "").toLowerCase();
    const suitMap: Record<string, Card["suit"]> = {
      s: "S", h: "H", d: "D", c: "C",
      spade: "S", heart: "H", diamond: "D", club: "C",
    };
    return {
      rank: (CARD_RANK_PNG[rank] ?? rank).toUpperCase(),
      suit: (suitMap[suitRaw] ?? "S") as Card["suit"],
      faceUp: true,
    };
  }
  return { faceUp: false };
}

function wsUrlFromBase(): string {
  const override =
    (typeof import.meta !== "undefined" &&
      (import.meta as { env?: Record<string, string | undefined> }).env?.VITE_WS_URL) ||
    "";
  if (override) return override;
  if (API_BASE.startsWith("http")) {
    const u = new URL(API_BASE);
    return `${u.protocol === "https:" ? "wss://" : "ws://"}${u.host}/ws`;
  }
  const proto = location.protocol === "https:" ? "wss://" : "ws://";
  return `${proto}${location.host}/ws`;
}

export function useTeenPattiTable(room: string) {
  const session = useMemo(
    () =>
      new URLSearchParams(location.search).get("session") ??
      localStorage.getItem("player_session_token") ??
      "",
    [],
  );
  const wsUrl = useMemo(() => wsUrlFromBase(), []);
  const { state, placeBet: postBet } = useGameState(wsUrl, session, room);
  const countdown = useServerTimer(
    state.round?.bettingEndAt ?? null,
    state.round?.serverTime ?? null,
  );

  const [selectedChip, setSelectedChip] = useState(1000);
  const [notice, setNotice] = useState<string | null>(null);
  const [betPending, setBetPending] = useState(false);
  const noticeTimer = useRef<number | undefined>(undefined);

  const toast = useCallback((text: string) => {
    setNotice(text);
    window.clearTimeout(noticeTimer.current);
    noticeTimer.current = window.setTimeout(() => setNotice(null), 2200);
  }, []);

  const status = (state.round?.status ?? "").toUpperCase();
  const bettingOpen = status === "BETTING_OPEN";

  const players = useMemo(() => {
    const out: Partial<Record<Seat, Player>> = {};
    for (const seat of SEATS) {
      const s = state.seats[seat];
      if (!s.playerId && !s.name) continue;
      out[seat] = {
        id: s.playerId ?? seat,
        name: s.name || `Seat ${seat}`,
        seat,
        bet: state.panels[seat].myBet,
        pot: state.panels[seat].pot,
      };
    }
    return out;
  }, [state]);

  const cards = useMemo(() => {
    const out: Partial<Record<Seat, Card[]>> = {};
    const revealedAll = status === "RESULT_DECLARED" || status === "SETTLED";
    for (const seat of SEATS) {
      const raw = state.hands[seat] ?? [];
      out[seat] = raw.map((c, i) => {
        const n = normalizeCard(c);
        // Only the first card is face-up while guessing; full reveal at result.
        if (!revealedAll && i > 0) return { faceUp: false };
        return n ?? { faceUp: false };
      });
    }
    return out;
  }, [state, status]);

  const banner = useMemo<string | null>(() => {
    if (status === "ABOUT_TO_START" || status === "UPCOMING") return "About to Start";
    if (status === "BETTING_CLOSED") return "Betting Closed";
    if (bettingOpen && countdown <= 3 && countdown > 0) return String(countdown);
    return null;
  }, [status, bettingOpen, countdown]);

  const placeBet = useCallback(
    async (seat: Seat, amount: number) => {
      if (state.isSpectator || betPending) return;
      if (!bettingOpen) {
        toast("Betting is closed");
        return;
      }
      setBetPending(true);
      toast(`Placing ${amount} on ${seat}…`);
      const res = await postBet(seat, amount);
      if (res.ok) {
        // Snapshot via WS confirms; release the lock shortly after.
        window.setTimeout(() => setBetPending(false), 4000);
      } else {
        setBetPending(false);
        toast(`Bet failed: ${res.error ?? "unknown"}`);
      }
    },
    [state.isSpectator, betPending, bettingOpen, postBet, toast],
  );

  return {
    roundId: state.round ? String(state.round.roundNo || state.round.roundId) : "—",
    roomId: room,
    countdown,
    totalBet: state.potTotal,
    myBet: state.myBet,
    balance: state.balance,
    players,
    cards,
    selectedChip,
    setSelectedChip,
    canBet: bettingOpen && !state.isSpectator && !betPending,
    isSpectator: state.isSpectator,
    banner,
    bannerUrgent: bettingOpen && countdown <= 3 && countdown > 0,
    notice,
    conn: state.connectionStatus,
    connectionStatus: state.connectionStatus,
    winnerSeat: state.winnerSeat,
    multiplier: state.panels.B.multiplier,
    placeBet,
  };
}
