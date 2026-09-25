import React, { createContext, useContext, useState, useEffect, ReactNode } from 'react';

interface Game {
  game_code: string;
  name: string;
  status: string;
  engine: string;
  currencies: string[];
  min_bet: number;
  max_bet: number;
  max_players: number;
  tables: string[];
  actions: string[];
  realtime: {
    protocol: string;
    path: string;
    auth: string;
  };
}

interface GameContextType {
  games: Game[];
  currentGame: string | null;
  setCurrentGame: (gameCode: string) => void;
  refreshGames: () => Promise<void>;
}

const GameContext = createContext<GameContextType | null>(null);

export function GameProvider({ children }: { children: ReactNode }) {
  const [games, setGames] = useState<Game[]>([]);
  const [currentGame, setCurrentGame] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchGames = async () => {
    try {
      const response = await fetch(`${import.meta.env.VITE_API_BASE || 'https://dearlive-games.vercel.app'}/api/v1/games`);
      if (response.ok) {
        const data = await response.json();
        if (data.success && data.data?.games) {
          setGames(data.data.games);
        }
      } catch (error) {
        console.error('Failed to fetch games:', error);
      } finally {
        setLoading(false);
      }
    };

  useEffect(() => {
    fetchGames();
  }, []);

  return (
    <GameContext.Provider value={{ games, currentGame, setCurrentGame, refreshGames: fetchGames }}>
      {children}
    </GameContext.Provider>
  );
}

export function useGame() {
  const context = useContext(GameContext);
  if (!context) throw new Error('useGame must be used within GameProvider');
  return context;
}
