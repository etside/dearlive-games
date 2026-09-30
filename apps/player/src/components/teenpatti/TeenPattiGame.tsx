import React from "react";
import "./TeenPattiGame.css";

export type Seat = "A" | "B" | "C";

export type Player = {
  id: string;
  name: string;
  avatar?: string;
  seat: Seat;
  bet: number;
  pot: number;
};

export type Card = {
  rank?: string;
  suit?: "S" | "H" | "D" | "C";
  faceUp: boolean;
};

type Props = {
  roundId?: string;
  roomId?: string;
  countdown?: number;
  /** Seconds left in the betting window, server-authoritative. Null hides the number. */
  totalBet?: number;
  myBet?: number;
  balance?: number;
  players?: Partial<Record<Seat, Player>>;
  cards?: Partial<Record<Seat, Card[]>>;
  multiplier?: number;
  selectedChip?: number;
  /** Betting window open + seated (not spectator). Panels disabled otherwise. */
  canBet?: boolean;
  isSpectator?: boolean;
  /** Center banner text (phase name / urgent countdown). Null hides it. */
  banner?: string | null;
  bannerUrgent?: boolean;
  /** Bottom toast text. Null hides it. */
  notice?: string | null;
  onSelectChip?: (amount: number) => void;
  /** Seat the player tapped with the currently selected chip. */
  onBet?: (seat: Seat, amount: number) => void;
  onRepeat?: () => void;
  onBack?: () => void;
  /** @deprecated use onSelectChip */
  onChip?: (amount: number) => void;
};

const SEATS: Seat[] = ["A", "B", "C"];

export const CHIP_VALUES = [20, 100, 500, 1000];

const CARD_BACK_SRC = "/assets/games/teen-patti-pro/cards/card-back-teenpatti.png";

const suitSymbol = {
  S: "♠",
  H: "♥",
  D: "♦",
  C: "♣",
} as const;

const EMPTY_HAND: Card[] = [{ faceUp: false }, { faceUp: false }, { faceUp: false }];

export default function TeenPattiGame({
  roundId = "—",
  roomId = "—",
  countdown = 0,
  totalBet = 0,
  myBet = 0,
  balance = 0,
  players = {},
  cards = {},
  multiplier = 2.9,
  selectedChip = 1000,
  canBet = false,
  isSpectator = false,
  banner = null,
  bannerUrgent = false,
  notice = null,
  onSelectChip,
  onBet,
  onRepeat,
  onBack,
  onChip,
}: Props) {
  const pickChip = (value: number) => {
    (onSelectChip ?? onChip)?.(value);
  };

  return (
    <main className="teen-patti">
      {/* TOP GAME TOOLBAR */}
      <header className="game-toolbar">
        <button className="toolbar-btn back-btn" aria-label="Back" onClick={onBack}>
          ←
        </button>

        <div className="round-info">
          <strong>Round: {roundId}</strong>
          <span>Room: {roomId}</span>
        </div>

        {isSpectator && <div className="spectator-badge">Spectating</div>}

        <div className="toolbar-actions">
          <button className="toolbar-icon" aria-label="History">◷</button>
          <button className="toolbar-icon" aria-label="Leaderboard">♟</button>
          <button className="toolbar-icon" aria-label="Help">?</button>
          <button className="toolbar-icon" aria-label="Settings">⚙</button>
        </div>
      </header>

      {/* TABLE */}
      <section className="game-table">
        <div className="curtain curtain-left" />
        <div className="curtain curtain-right" />

        {/* COUNTDOWN — server-authoritative, monochrome unless urgent */}
        <div className={`countdown${bannerUrgent ? " urgent" : ""}`} aria-live="polite">
          <span>{countdown}</span>
        </div>

        {banner && (
          <div className={`phase-banner${bannerUrgent ? " urgent" : ""}`} role="status">
            {banner}
          </div>
        )}

        {/* CARD AREA */}
        <div className="card-area">
          {SEATS.map((seat) => (
            <div className="seat-cards" key={seat}>
              {(cards[seat] ?? EMPTY_HAND).map((card, index) => (
                <div
                  key={`${seat}-${index}`}
                  className={`playing-card ${card.faceUp ? "face-up" : "face-down"}`}
                >
                  {card.faceUp && card.rank && card.suit ? (
                    <div
                      className={`card-face ${
                        card.suit === "H" || card.suit === "D" ? "red" : "black"
                      }`}
                    >
                      <span>
                        {card.rank}
                        {suitSymbol[card.suit]}
                      </span>

                      <strong>{suitSymbol[card.suit]}</strong>
                    </div>
                  ) : (
                    <img src={CARD_BACK_SRC} alt="Card back" loading="lazy" />
                  )}
                </div>
              ))}
            </div>
          ))}
        </div>

        {/* TOTAL BET */}
        <div className="bet-summary">
          <div>
            Total Bet <b>{totalBet}</b>
          </div>
          <div>
            My total bet <b>{myBet}</b>
          </div>
        </div>

        {/* PLAYER CHAIRS */}
        <div className="chairs">
          {SEATS.map((seat) => {
            const player = players[seat];

            return <div className={`player-seat seat-${seat.toLowerCase()}`} key={seat}>
                <div className="player-avatar">
                  {player?.avatar ? (
                    <img src={player.avatar} alt={player.name} />
                  ) : (
                    <span>{seat}</span>
                  )}
                </div>

                <div className="chair">
                  <div className="chair-back" />
                  <div className="chair-seat" />
                </div>

                {player && <div className="player-name">{player.name}</div>}
              </div>;
          })}
        </div>

        {/* BETTING PANELS — tapping a panel bets the selected chip on that seat */}
        <div className="bet-panels">
          {SEATS.map((seat) => {
            const player = players[seat];

            return (
              <button
                key={seat}
                className={`bet-panel panel-${seat.toLowerCase()}`}
                disabled={!canBet}
                onClick={() => onBet?.(seat, selectedChip)}
                aria-label={`Bet ${selectedChip} on ${seat}`}
              >
                <div className="bet-amount">
                  {player?.pot ?? 0}/{player?.bet ?? 0}
                </div>

                <div className="multiplier">x{multiplier}</div>

                <div className="position-label">{seat}</div>
              </button>
            );
          })}
        </div>

        {notice && (
          <div className="table-toast" role="status">
            {notice}
          </div>
        )}
      </section>

      {/* BOTTOM BET BAR */}
      <footer className="bet-bar">
        <div className="balance">
          <span className="coin">●</span>
          <strong>{balance.toLocaleString()}</strong>
        </div>

        <div className="chips">
          {CHIP_VALUES.map((value) => (
            <button
              key={value}
              className={`chip ${selectedChip === value ? "selected" : ""}`}
              onClick={() => pickChip(value)}
              aria-pressed={selectedChip === value}
            >
              <span>{value >= 1000 ? `${value / 1000}K` : value}</span>
            </button>
          ))}
        </div>

        <button className="repeat-btn" onClick={onRepeat} disabled={!canBet}>
          Repeat
          <br />
          Bet
        </button>
      </footer>
    </main>
  );
}
