import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { useGame } from '../contexts/GameContext';
import { useWallet } from '../contexts/WalletContext';
import { Gamepad2, Lion, Monkey, Crown, ArrowRight, ChevronRight, CheckCircle, Clock, Users, Zap } from 'lucide-react';
import { cn } from '../utils/cn';

const games = [
  {
    game_code: 'teen_patti_pro',
    name: 'Teen Patti Pro',
    description: 'Classic 3-card poker with Indian rules. Bet on Player A, B, or C.',
    icon: '🃏',
    color: 'from-red-500 to-orange-500',
    stats: { players: '2.4M', rating: '4.8', rounds: '1.2M' },
    tags: ['Poker', 'Cards', 'Multiplayer'],
    features: ['Live multiplayer', 'Real-time chat', 'Tournaments', 'Private tables'],
  },
  {
    game_code: 'greedy_lion',
    name: 'Greedy Lion',
    description: 'Wheel of fortune with 5 betting options. High multipliers up to 20x.',
    icon: '🦁',
    color: 'from-yellow-500 to-orange-500',
    stats: { players: '890K', rating: '4.7', spins: '2.1M' },
    tags: ['Wheel', 'Slots', 'High RTP'],
    features: ['Auto-bet', 'Quick spin', 'Statistics', 'History'],
  },
  {
    game_code: 'monkey_wheel',
    name: 'Monkey Wheel',
    description: 'Jungle-themed wheel with 5 exotic options. Bonus rounds and jackpots.',
    icon: '🐒',
    color: 'from-green-500 to-emerald-500',
    stats: { players: '567K', rating: '4.6', spins: '890K' },
    tags: ['Wheel', 'Bonus rounds', 'Jackpots'],
    features: ['Bonus rounds', 'Free spins', 'Progressive jackpot', 'Daily rewards'],
  },
];

export function GamesHomePage() {
  const { games, currentGame, setCurrentGame, loading } = useGame();
  const { balance, currency } = useWallet();

  return (
    <div className="min-h-screen bg-gradient-to-b from-gray-900 via-gray-900 to-black">
      {/* Header */}
      <header className="fixed top-0 left-0 right-0 z-50 bg-gray-900/95 backdrop-blur-sm border-b border-gray-800">
        <div className="mx-auto max-w-full px-4">
          <div className="flex h-14 items-center justify-between">
            <div className="flex items-center gap-4">
              <span className="text-2xl">🎮</span>
              <span className="text-xl font-bold text-white hidden sm:block">DearLive Games</span>
            </div>
            <div className="flex items-center gap-4">
              <div className="hidden sm:flex items-center gap-2 bg-gray-800 rounded-lg px-3 py-1">
                <span className="text-yellow-400">💰</span>
                <span className="text-sm font-medium text-white">
                  {wallet.balance.toLocaleString()} {wallet.currency}
                </span>
              </div>
            </div>
          </div>
        </div>
      </header>

      <main className="pt-16 pb-24 px-4">
        <div className="mx-auto max-w-7xl py-12">
          {/* Hero Section */}
          <section className="mb-12">
            <div className="text-center mb-8">
              <h1 className="text-4xl md:text-5xl font-bold text-white mb-4">
                Choose Your Game
              </h1>
              <p className="text-lg text-gray-400 max-w-2xl mx-auto">
                Three unique games. One seamless experience. Real-time multiplayer action.
              </p>
            </div>
          </section>

          {/* Games Grid */}
          <section>
            <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
              {games.map((game) => (
                <article
                  key={game.game_code}
                  className={cn(
                    'relative group overflow-hidden rounded-2xl border border-gray-800 bg-gray-900/50',
                    'transition-all duration-300 hover:border-purple-500/50 hover:shadow-2xl hover:shadow-purple-500/10'
                  )}
                >
                  <Link to={`/game/${game.game_code}`} className="block h-full flex flex-col">
                    {/* Game Header */}
                    <div className={cn('relative h-48 bg-gradient-to-br', game.color, 'flex items-end p-6')}>
                      <div className="absolute inset-0 bg-gradient-to-t from-black/60 via-transparent to-transparent" />
                      <div className="relative z-10 p-6">
                        <div className="flex items-center gap-3 mb-2">
                          <span className="text-5xl">{game.icon}</span>
                          <div>
                            <h3 className="text-xl font-bold text-white">{game.name}</h3>
                            <p className="text-sm text-gray-300">{game.description}</p>
                          </div>
                        </div>
                        <div className="absolute top-4 right-4">
                          <span className={cn(
                            'px-2 py-1 rounded-full text-xs font-medium',
                            game.game_code === 'teen_patti_pro' ? 'bg-red-500/90 text-white' :
                            game.game_code === 'greedy_lion' ? 'bg-yellow-500/90 text-white' :
                            'bg-green-500/90 text-white'
                          )}>
                            {game.tags[0]}
                          </span>
                        </div>
                      </div>
                    </div>

                    {/* Game Body */}
                    <div className="p-5 flex-1 flex flex-col">
                      <p className="text-gray-300 text-sm mb-4 line-clamp-2">{game.description}</p>
                      
                      {/* Stats */}
                      <div className="flex items-center gap-4 mb-4 text-xs text-gray-400">
                        <span className="flex items-center gap-1">
                          <Users className="w-3 h-3" />
                          <span>{game.stats.players}</span>
                        </span>
                        <span className="flex items-center gap-1">
                          <Zap className="w-3 h-3" />
                          <span>{game.stats.spins || game.stats.rounds}</span>
                        </span>
                        <span className="flex items-center gap-1">
                          <Star className="w-3 h-3 fill-yellow-400 text-yellow-400" />
                          <span>{game.stats.rating}</span>
                        </span>
                      </div>

                      {/* Tags */}
                      <div className="flex flex-wrap gap-2 mb-4">
                        {game.tags.map((tag) => (
                          <span
                            key={tag}
                            className="px-2 py-0.5 text-xs bg-gray-800 rounded-full text-gray-300"
                          >
                            {tag}
                          </span>
                        ))}
                      </div>

                      {/* Features */}
                      <div className="mt-auto pt-4 border-t border-gray-800">
                        <p className="text-xs text-gray-500 mb-2">Features:</p>
                        <div className="flex flex-wrap gap-1">
                          {game.features.slice(0, 3).map((feature) => (
                            <span
                              key={feature}
                              className="px-2 py-0.5 text-xs bg-purple-500/20 text-purple-300 rounded"
                            >
                              {feature}
                            </span>
                          ))}
                          {game.features.length > 3 && (
                            <span className="px-2 py-0.5 text-xs bg-gray-700 text-gray-400 rounded">
                              +{game.features.length - 3} more
                            </span>
                          )}
                        </div>
                      </div>
                    </div>

                    {/* Play Button */}
                    <div className="p-4 border-t border-gray-800">
                      <button className={cn(
                        'w-full py-3 rounded-xl font-semibold text-lg transition-all',
                        'bg-gradient-to-r from-purple-600 to-pink-600 text-white',
                        'hover:from-purple-700 hover:to-pink-700',
                        'shadow-lg shadow-purple-500/25'
                      )}
                    >
                      Play Now →
                    </button>
                  </Link>
                </article>
              ))}
            </div>
          </section>
        </section>
      </main>
    </div>
  );
}

export { GamesHomePage };
