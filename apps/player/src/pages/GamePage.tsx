import React, { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useGame } from '../contexts/GameContext';
import { useWallet } from '../contexts/WalletContext';
import { GameCanvas } from '../components/GameCanvas';
import { ArrowLeft, RefreshCw, History, Wallet, Settings, X, ChevronDown, MessageSquare, Volume2, VolumeX } from 'lucide-react';
import { cn } from '../utils/cn';

export function GamePage() {
  const { gameCode } = useParams<{ gameCode: string }>();
  const navigate = useNavigate();
  const { games, currentGame, refreshGames } = useGame();
  const { balance, refreshBalance } = useWallet();
  const [showMenu, setShowMenu] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [showChat, setShowChat] = useState(false);
  const [soundOn, setSoundOn] = useState(true);

  const game = useGame().games.find(g => g.game_code === gameCode);

  useEffect(() => {
    refreshGames();
  }, [refreshGames]);

  if (!game) return null;

  return (
    <div className="min-h-screen bg-gray-900 flex flex-col">
      {/* Header */}
      <header className="fixed top-0 left-0 right-0 z-50 bg-gray-900/95 backdrop-blur-sm border-b border-gray-800">
        <div className="mx-auto max-w-full px-4">
          <div className="flex h-14 items-center justify-between">
            <div className="flex items-center gap-4">
              <button
                onClick={() => navigate(-1)}
                className="p-2 rounded-lg text-gray-400 hover:bg-gray-800 lg:hidden"
                aria-label="Back"
              >
                <ArrowLeft className="w-6 h-6 text-white" />
              </button>
              <div className="flex items-center gap-3 flex-1 min-w-0">
                <div className="flex items-center gap-2 truncate">
                  <span className="text-2xl">{game?.icon}</span>
                  <div className="hidden sm:block">
                    <h1 className="font-bold text-white truncate">{game?.name}</h1>
                    <p className="text-xs text-gray-400">{game?.description}</p>
                  </div>
                </div>
              <div className="flex items-center gap-2 ml-auto">
                <div className="flex items-center gap-2 bg-gray-800 rounded-lg px-3 py-1">
                  <span className="text-yellow-400">💰</span>
                  <span className="text-sm font-medium text-white">
                    {wallet.balance.toLocaleString()} {wallet.currency}
                  </span>
                </div>
              </div>
            </div>
            <div className="flex items-center gap-2 ml-auto">
              <button
                onClick={() => setShowChat(!showChat)}
                className={cn('p-2 rounded-lg', showChat ? 'bg-purple-500' : 'bg-gray-800', 'text-white')}
              >
                <MessageSquare className="w-5 h-5" />
              </button>
              <button
                onClick={() => setShowHistory(!showHistory)}
                className="p-2 rounded-lg text-gray-400 hover:bg-gray-800"
              >
                <History className="w-5 h-5" />
              </button>
              <button
                onClick={() => setShowSettings(!showSettings)}
                className="p-2 rounded-lg text-gray-400 hover:bg-gray-800"
              >
                <Settings className="w-5 h-5" />
              </button>
            </div>
          </div>
        </div>
      </header>

      <main className="flex-1 pt-16 pb-24">
        <GameCanvas gameCode={gameCode!} />
        
        {/* Betting Controls */}
        <div className="fixed bottom-0 left-0 right-0 bg-gray-900/95 backdrop-blur-sm border-t border-gray-800 p-4">
          <div className="mx-auto max-w-full px-4 py-4">
            {/* Chip Selection */}
            <div className="flex items-center justify-center gap-2 mb-4">
              {DENOMS.map(denom => (
                <button
                  key={denom}
                  className={cn(
                    'w-14 h-14 rounded-xl border-2 transition-all',
                    selectedDenom === denom
                      ? 'border-yellow-400 bg-yellow-400/20'
                      : 'border-gray-700 bg-gray-800',
                    'flex items-center justify-center text-white font-bold text-lg'
                  )}
                  onClick={() => setSelDenom(denom)}
                >
                  {denom >= 1000 ? `${denom / 1000}K` : denom}
                </button>
              ))}
            </div>

            {/* Bet Action */}
            <div className="flex items-center justify-center gap-4 mt-4">
              <button
                onClick={() => placeBet()}
                disabled={betBusy || !selectedPosition}
                className={cn(
                  'flex-1 max-w-xs py-3 rounded-xl font-bold text-lg transition-all',
                  'bg-gradient-to-r from-purple-600 to-pink-600 text-white',
                  'shadow-lg shadow-purple-500/25',
                  'disabled:opacity-50 disabled:cursor-not-allowed'
                )}
              >
                Bet {selDenom} on {selectedPosition}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export { GamePage };
