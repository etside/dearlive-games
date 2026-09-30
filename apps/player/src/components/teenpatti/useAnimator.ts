/** Event-keyed animation orchestrator — idempotent per event_id. */

import { useCallback, useEffect, useRef } from "react";

export type AnimationTrigger =
  | "round.reset"
  | "about.to.start"
  | "cards.dealt"
  | "chip.fly"
  | "cards.revealed"
  | "winner.highlight"
  | "payout.float"
  | "balance.tween";

export interface AnimationCommand {
  trigger: AnimationTrigger;
  eventId: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  data: any;
}

export function useAnimator(onTrigger: (cmd: AnimationCommand) => void) {
  const consumed = useRef(new Set<string>());
  const handler = useRef(onTrigger);
  handler.current = onTrigger;

  const trigger = useCallback((cmd: AnimationCommand) => {
    if (consumed.current.has(cmd.eventId)) return;
    consumed.current.add(cmd.eventId);
    if (consumed.current.size > 5000) consumed.current.clear();
    handler.current(cmd);
  }, []);

  useEffect(() => {
    return () => {
      consumed.current.clear();
    };
  }, []);

  return { trigger };
}
