import React, { useEffect } from 'react';
import { useParams, useNavigate, useSearchParams } from 'react-router-dom';
import { useGame } from '../contexts/GameContext';
import TeenPattiGame from '../components/teenpatti/TeenPattiGame';
import { useTeenPattiTable } from '../components/teenpatti/useTeenPattiTable';

const TEEN_CODES = new Set(['teen-patti-pro', 'teen_patti', 'teen_patti_pro']);

function TeenPattiScreen({ room, gameCode }: { room: string; gameCode: string }) {
  const navigate = useNavigate();
  const t = useTeenPattiTable(room);
  return (
    <TeenPattiGame
      roundId={t.roundId}
      roomId={room}
      countdown={t.countdown}
      totalBet={t.totalBet}
      myBet={t.myBet}
      balance={t.balance}
      players={t.players}
      cards={t.cards}
      selectedChip={t.selectedChip}
      canBet={t.canBet}
      isSpectator={t.isSpectator}
      banner={t.banner}
      bannerUrgent={t.bannerUrgent}
      notice={t.notice ?? (t.conn === 'live' ? null : 'Reconnecting…')}
      onSelectChip={t.setSelectedChip}
      onBet={(seat, amount) => void t.placeBet(seat, amount)}
      onRepeat={() => undefined}
      onBack={() => navigate('/')}
    />
  );
}

export function GamePage() {
  const { gameCode } = useParams<{ gameCode: string }>();
  const [search] = useSearchParams();
  const { games, refreshGames } = useGame();

  useEffect(() => {
    void refreshGames();
  }, [refreshGames]);

  const code = gameCode ?? '';
  const room = search.get('room') || 'teen-patti-low';

  if (TEEN_CODES.has(code)) {
    return <TeenPattiScreen room={room} gameCode={code} />;
  }

  const game = games.find((g) => g.game_code === code);

  return (
    <div className="min-h-screen bg-gray-900 flex flex-col items-center justify-center p-8 text-center">
      <h1 className="text-xl font-bold text-white">{game?.name ?? code}</h1>
      <p className="mt-2 text-sm text-gray-400">
        This game has no dedicated screen yet. Open Teen Patti Pro from the lobby.
      </p>
    </div>
  );
}

export default GamePage;
