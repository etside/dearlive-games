/** Shared Teen Patti Pro types. Single source of truth for the table UI. */

export type Seat = "A" | "B" | "C";
export type Suit = "S" | "H" | "D" | "C";
export type ConnectionStatus = "offline" | "connecting" | "live";

export type Card = {
  id?: string;
  rank?: string;
  suit?: Suit;
  faceUp: boolean;
};

export type Player = {
  id: string;
  name: string;
  avatar?: string;
  seat: Seat;
  bet: number;
  pot: number;
};

export const SEATS: Seat[] = ["A", "B", "C"];

/** Authoritative round snapshot served by the engine. No client fabrication. */
export interface SnapshotResponse {
  round: {
    round_id: string;
    round_no: number;
    status: string;
    state_version?: number;
    server_time: string;
    betting_end_at: string;
  };
  seats: Record<Seat, {
    player_id?: string;
    name?: string;
    pot?: number;
    bet?: number;
    multiplier?: number;
    is_me?: boolean;
  }>;
  hands: Record<Seat, unknown[]>;
  winner_seat: Seat | null;
  payout: number;
  balance: number;
  pot_total: number;
  my_bet: number;
  my_seat: Seat | null;
}

/** Normalized server event. The engine may send snake_case or camelCase. */
export type ServerEvent = {
  eventId: string;
  type: string;
  roundId: string;
  stateVersion: number;
  serverTime: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  payload: any;
  requestId: string;
};
