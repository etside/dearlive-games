/** Monotonic server-synchronized timer. Recomputes, never decrements locally. */

import { useEffect, useState } from "react";

export function useServerTimer(
  bettingEndAt: string | null,
  serverTime: string | null,
) {
  const [offset, setOffset] = useState(0);
  const [remaining, setRemaining] = useState(0);

  // Sync clock offset from server time.
  useEffect(() => {
    if (!serverTime) return;
    const t = Date.parse(serverTime);
    if (Number.isFinite(t)) setOffset(t - Date.now());
  }, [serverTime]);

  // Recompute remaining on a tick — recompute, never decrement.
  useEffect(() => {
    if (!bettingEndAt) {
      setRemaining(0);
      return;
    }
    const end = Date.parse(bettingEndAt);
    if (!Number.isFinite(end)) {
      setRemaining(0);
      return;
    }

    const tick = () => {
      const now = Date.now() + offset;
      setRemaining(Math.max(0, Math.ceil((end - now) / 1000)));
    };
    tick();
    const id = window.setInterval(tick, 100);
    return () => window.clearInterval(id);
  }, [bettingEndAt, offset]);

  return remaining;
}
