/** REST client — all writes are server-authoritative. */

import { apiUrl } from "../../config/apiBase";
import type { Seat, SnapshotResponse } from "./types";

export type { SnapshotResponse };

function uuid(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export async function fetchSnapshot(
  sessionToken: string,
  roomId: string,
): Promise<SnapshotResponse | null> {
  try {
    const res = await fetch(
      apiUrl(`/api/v1/games/teen-patti-pro/rounds/current?room=${encodeURIComponent(roomId)}`),
      { headers: { Authorization: `Bearer ${sessionToken}` } },
    );
    if (!res.ok) return null;
    const json = await res.json();
    return (json.data ?? json) as SnapshotResponse;
  } catch {
    return null;
  }
}

export async function placeBet(
  sessionToken: string,
  roundId: string,
  seat: Seat,
  amount: number,
): Promise<{ ok: boolean; error?: string; betId?: string }> {
  const idemKey = uuid();
  try {
    const res = await fetch(
      apiUrl(`/api/v1/games/teen-patti-pro/rounds/${encodeURIComponent(roundId)}/bets`),
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${sessionToken}`,
          "Idempotency-Key": idemKey,
        },
        body: JSON.stringify({ position: seat, amount: Number(amount) }),
      },
    );
    const json = await res.json();
    if (!res.ok || !json.success) {
      return { ok: false, error: json.message || "Bet rejected" };
    }
    return { ok: true, betId: json.data?.bet_id };
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
}
