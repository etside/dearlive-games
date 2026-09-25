import React, { useState } from 'react';
import { useWallet } from '../contexts/WalletContext';
import { useGame } from '../contexts/GameContext';
import { Menu, Wallet, LogOut, Settings, Menu as MenuIcon, X, ChevronDown } from 'lucide-react';
import { cn } from '../utils/cn';

export function Header() {
  const [showMenu, setShowMenu] = useState(false);
  const { balance, currency } = useWallet();
  const { currentGame, games } = useGame();

  return (
    <header className="fixed top-0 left-0 right-0 z-50 bg-gray-900/95 backdrop-blur-sm border-b border-gray-800">
      <div className="mx-auto max-w-full px-4">
        <div className="flex h-14 items-center justify-between">
          <div className="flex items-center gap-4">
            <button
              onClick={() => setShowMenu(!showMenu)}
              className="lg:hidden p-2 rounded-lg text-gray-400 hover:bg-gray-800"
              aria-label="Open menu"
            >
              <Menu className="w-6 h-6 text-white" />
            </button>
            
            <div className="flex items-center gap-2">
              <span className="text-xl font-bold text-white">🎮</span>
              <span className="text-lg font-bold text-white hidden sm:block">DearLive Games</span>
            </div>
          </div>

          <div className="flex items-center gap-4">
            <div className="hidden sm:flex items-center gap-2 bg-gray-800 rounded-lg px-3 py-1">
              <Wallet className="w-4 h-4 text-gray-400" />
              <span className="text-sm font-medium text-white">
                {wallet.balance.toLocaleString()} {wallet.currency}
              </span>
            </div>

            <div className="relative">
              <select
                className="appearance-none bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-sm text-white focus:outline-none focus:ring-2 focus:ring-purple-500"
                value={currentGame}
                onChange={(e) => setCurrentGame(e.target.value)}
              >
                {games.map(game => (
                  <option key={game.game_code} value={game.game_code}>
                    {game.name}
                  </option>
                ))}
              </select>
            </div>

            <div className="flex items-center gap-2">
              <button className="p-2 rounded-lg text-gray-400 hover:bg-gray-800">
                <Settings className="w-5 h-5" />
              </button>
              <div className="w-8 h-8 rounded-full bg-gradient-to-r from-purple-500 to-pink-500 flex items-center justify-center font-medium text-white text-sm">
                P
              </button>
            </div>
          </div>
        </div>
      </div>
    </header>
  );
}

export { Header };
