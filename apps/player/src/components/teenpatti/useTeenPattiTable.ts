import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiUrl, API_BASE } from "../../config/apiBase";
import type { Card, Player, Seat } from "./TeenPattiGame";

const SEATS: Seat[] = ["A", "B", "C"];
const WS_MAX_BACKOFF = 8000;

type Snapshot = {
  round?: {
    round_id?: string;
    round_no?: number;
    status?: string;
    state_version?: number;
    server_time?: string;
    betting_end_at?: string;
  };
  seats?: Record<string, {
    player_id?: string; playerId?: string;
    name?: string; player_name?: string;
    pot?: number; bet?: number;
    is_me?: boolean; isMe?: boolean;
  }>;
  hands?: Record<string, unknown[]>;
  pot_total?: number; potTotal?: number;
  my_bet?: number; myBet?: number;
  balance?: number;
  winner_seat?: Seat | null; winnerSeat?: Seat | null;
  my_seat?: Seat | null; mySeat?: Seat | null;
};

function normalizeCard(raw: unknown): Card | null {
  if (!raw) return null;
  if (typeof raw === "object") {
    const o = raw as Record<string, unknown>;
    const rank = String(o.rank ?? "").toLowerCase();
    const suitRaw = String(o.suit ?? "").toLowerCase();
    const suitMap: Record<string, Card["suit"]> = {
      s: "S", h: "H", d: "D", c: "C",
      spade: "S", heart: "H", diamond: "D", club: "C",
    };
    const faceUp = Boolean(o.revealed ?? o.faceUp ?? o.face_up ?? true);
    if (!faceUp) return { faceUp: false };
    const rankMap: Record<string, string> = {
      "1": "A", "11": "J", "12": "Q", "13": "K",
    };
    return {
      rank: (rankMap[rank] ?? rank).toUpperCase(),
      suit: (suitMap[suitRaw] ?? "S") as Card["suit"],
      faceUp: true,
    };
  }
  return { faceUp: false };
}

function wsUrl(): string {
  if (API_BASE.startsWith("http")) {
    const u = new URL(API_BASE);
    return `${u.protocol === "https:" ? "wss://" : "ws://"}${u.host}/ws`;
  }
  const proto = location.protocol === "https:" ? "wss://" : "ws://";
  return `${proto}${location.host}/ws`;
}

export function useTeenPattiTable(room: string) {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [clockOffsetMs, setClockOffsetMs] = useState(0);
  const [selectedChip, setSelectedChip] = useState(1000);
  const [notice, setNotice] = useState<string | null>(null);
  const [betPending, setBetPending] = useState(false);
  const [conn, setConn] = useState<"live" | "offline" | "reconnecting">("offline");
  const noticeTimer = useRef<number | undefined>(undefined);
  const reconnectAttempts = useRef(0);

  const session = useMemo(
    () =>
      new URLSearchParams(location.search).get("session") ??
      localStorage.getItem("player_session_token") ??
      "",
    [],
  );

  const toast = useCallback((text: string) => {
    setNotice(text);
    window.clearTimeout(noticeTimer.current);
    noticeTimer.current = window.setTimeout(() => setNotice(null), 2200);
  }, []);

  // Initial snapshot (authoritative; no client-side round fabrication).
  useEffect(() => {
    if (!session) return;
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch(
          apiUrl(`/api/v1/games/teen-patti-pro/rounds/current?room=${encodeURIComponent(room)}`),
          { headers: { Authorization: `Bearer ${session}` } },
        );
        const j = await r.json();
        if (!cancelled && j?.data) setSnap(j.data as Snapshot);
      } catch {
        /* WS will fill in; staying silent avoids a fake error state */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [room, session]);

  // Server clock sync: every snapshot carries server_time; the countdown is
  // derived from betting_end_at minus the synced clock, recomputed on render.
  useEffect(() => {
    const t = snap?.round?.server_time;
    if (!t) return;
    const parsed = Date.parse(t);
    if (Number.isFinite(parsed)) setClockOffsetMs(parsed - Date.now());
  }, [snap]);

  // WebSocket subscribe with backoff. Every message is authoritative.
  useEffect(() => {
    if (!session) return;
    let ws: WebSocket | null = null;
    let closed = false;

    const connect = () => {
      if (closed) return;
      setConn("reconnecting");
      try {
        ws = new WebSocket(wsUrl());
      } catch {
        schedule();
        return;
      }
      ws.onopen = () => {
        reconnectAttempts.current = 0;
        setConn("live");
        ws?.send(JSON.stringify({ type: "subscribe", session, room }));
      };
      ws.onmessage = (evt) => {
        let msg: { type?: string; data?: Snapshot; snapshot?: Snapshot } | null = null;
        try {
          msg = JSON.parse(evt.data as string);
        } catch {
          return;
        }
        if (!msg) return;
        if (msg.type === "snapshot" || msg.type === "round.updated" || msg.type === "round.created") {
          const data = msg.data ?? msg.snapshot ?? (msg as unknown as Snapshot);
          if ((data as Snapshot)?.round) setSnap(data as Snapshot);
        } else if (msg.type === "bet.accepted") {
          setBetPending(false);
          toast("Bet accepted");
        } else if (msg.type === "bet.rejected") {
          setBetPending(false);
          const reason = (msg as { reason?: string }).reason ?? "unknown";
          toast(`Bet rejected: ${reason}`);
        }
      };
      ws.onclose = () => {
        setConn("offline");
        schedule();
      };
    };
    const schedule = () => {
      if (closed) return;
      reconnectAttempts.current += 1;
      const delay = Math.min(WS_MAX_BACKOFF, 500 * 2 ** reconnectAttempts.current);
      window.setTimeout(() => {
        if (!closed) connect();
      }, delay);
    };

    connect();
    const onVisible = () => {
      if (!document.hidden && (!ws || ws.readyState === WebSocket.CLOSED)) connect();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      closed = true;
      document.removeEventListener("visibilitychange", onVisible);
      ws?.close();
    };
  }, [room, session, toast]);

  const serverNow = useCallback(() => Date.now() + clockOffsetMs, [clockOffsetMs]);

  const countdown = useMemo(() => {
    const end = snap?.round?.betting_end_at;
    if (!end) return 0;
    return Math.max(0, Math.ceil((Date.parse(end) - serverNow()) / 1000));
  }, [snap, serverNow]);

  const status = (snap?.round?.status ?? "").toUpperCase();
  const bettingOpen = status === "BETTING_OPEN";

  const players = useMemo(() => {
    const out: Partial<Record<Seat, Player>> = {};
    for (const seat of SEATS) {
      const s = snap?.seats?.[seat];
      if (!s) continue;
      out[seat] = {
        id: String(s.player_id ?? s.playerId ?? seat),
        name: String(s.name ?? s.player_name ?? seat),
        seat,
        bet: Number(s.bet) || 0,
        pot: Number(s.pot) || 0,
      };
    }
    return out;
  }, [snap]);

  const cards = useMemo(() => {
    const out: Partial<Record<Seat, Card[]>> = {};
    const revealedAll = status === "RESULT_DECLARED" || status === "SETTLED";
    for (const seat of SEATS) {
      const raw = snap?.hands?.[seat] ?? [];
      out[seat] = raw.map((c, i) => {
        const n = normalizeCard(c);
        // Only the first card is face-up while guessing; full reveal at result.
        if (!revealedAll && i > 0) return { faceUp: false };
        return n ?? { faceUp: false };
      });
    }
    return out;
  }, [snap, status]);

  const mySeat = snap?.my_seat ?? snap?.mySeat ?? undefined;
  const isSpectator = mySeat === null;

  const banner = useMemo<string | null>(() => {
    if (status === "ABOUT_TO_START" || status === "UPCOMING") return "About to Start";
    if (status === "BETTING_CLOSED") return "Betting Closed";
    if (bettingOpen && countdown <= 3 && countdown > 0) return String(countdown);
    return null;
  }, [status, bettingOpen, countdown]);

  const placeBet = useCallback(
    async (seat: Seat, amount: number) => {
      if (isSpectator || betPending) return;
      if (!bettingOpen) {
        toast("Betting is closed");
        return;
      }
      const roundId = snap?.round?.round_id;
      if (!roundId) {
        toast("No live round");
        return;
      }
      setBetPending(true);
      toast(`Placing ${amount} on ${seat}…`);
      const idempotencyKey =
        (crypto.randomUUID && crypto.randomUUID()) || `${Date.now()}-${Math.random()}`;
      try {
        const res = await fetch(
          apiUrl(`/api/v1/games/teen-patti-pro/rounds/${roundId}/bets`),
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              Authorization: `Bearer ${session}`,
              "Idempotency-Key": idempotencyKey,
            },
            body: JSON.stringify({ position: seat, amount: Number(amount) }),
          },
        );
        const json = await res.json();
        if (!res.ok || !json.success) throw new Error(json.message || "Bet rejected");
        // Confirmation arrives via WS bet.accepted; keep pending until then.
        window.setTimeout(() => setBetPending(false), 4000);
      } catch (e) {
        setBetPending(false);
        toast(`Bet failed: ${e instanceof Error ? e.message : "unknown"}`);
      }
    },
    [isSpectator, betPending, bettingOpen, snap, session, toast],
  );

  return {
    roundId: snap?.round?.round_id ?? String(snap?.round?.round_no ?? "—"),
    roomId: room,
    countdown,
    totalBet: Number(snap?.pot_total ?? snap?.potTotal) || 0,
    myBet: Number(snap?.my_bet ?? snap?.myBet) || 0,
    balance: Number(snap?.balance) || 0,
    players,
    cards,
    selectedChip,
    setSelectedChip,
    canBet: bettingOpen && !isSpectator && !betPending,
    isSpectator,
    banner,
    bannerUrgent: bettingOpen && countdown <= 3 && countdown > 0,
    notice,
    conn,
    placeBet,
  };
}
