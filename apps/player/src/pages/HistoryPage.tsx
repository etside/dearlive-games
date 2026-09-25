import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useWallet } from '../contexts/WalletContext';
import { useGame } from '../contexts/GameContext';
import { ArrowLeft, Filter, Calendar, Download, ChevronDown, ChevronUp } from 'lucide-react';
import { cn } from '../utils/cn';
import { Button } from '../components/ui/Button';
import { Input } from '../components/ui/Input';
import { Badge } from '../components/ui/Badge';

const mockTransactions = [
  { id: 'txn_1', type: 'credit', amount: 5000, currency: 'COIN', desc: 'Deposit via Card', time: '2 min ago', status: 'completed', game: 'Teen Patti Pro' },
  { id: 'txn_2', type: 'debit', amount: 100, currency: 'COIN', desc: 'Bet on Teen Patti Pro', time: '5 min ago', status: 'completed', game: 'Teen Patti Pro' },
  { id: 'txn_3', type: 'credit', amount: 2500, currency: 'COIN', desc: 'Win on Greedy Lion', time: '12 min ago', status: 'completed', game: 'Greedy Lion' },
  { id: 'txn_4', type: 'debit', amount: 50, currency: 'COIN', desc: 'Bet on Monkey Wheel', time: '25 min ago', status: 'completed', game: 'Monkey Wheel' },
  { id: 'txn_5', type: 'credit', amount: 10000, currency: 'COIN', desc: 'Deposit via Crypto', time: '1 hour ago', status: 'completed', game: 'Wallet' },
  { id: 'txn_6', type: 'debit', amount: 200, currency: 'COIN', desc: 'Bet on Teen Patti Pro', time: '2 hours ago', status: 'completed', game: 'Teen Patti Pro' },
  { id: 'txn_7', type: 'credit', amount: 500, currency: 'COIN', desc: 'Win on Monkey Wheel', time: '3 hours ago', status: 'completed', game: 'Monkey Wheel' },
  { id: 'txn_8', type: 'debit', amount: 500, currency: 'COIN', desc: 'Bet on Greedy Lion', time: '4 hours ago', status: 'completed', game: 'Greedy Lion' },
];

export function HistoryPage() {
  const navigate = useNavigate();
  const { balance, currency } = useWallet();
  const { games } = useGame();
  const [filter, setFilter] = useState<'all' | 'credit' | 'debit'>('all');
  const [dateRange, setDateRange] = useState<'all' | 'today' | 'week' | 'month'>('all');
  const [search, setSearch] = useState('');

  const filteredTransactions = mockTransactions.filter(tx => {
    if (filter !== 'all' && tx.type !== filter) return false;
    if (search && !tx.desc.toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  });

  return (
    <div className="min-h-screen bg-gray-900">
      {/* Header */}
      <header className="fixed top-0 left-0 right-0 z-50 bg-gray-900/95 backdrop-blur-sm border-b border-gray-800">
        <div className="mx-auto max-w-full px-4">
          <div className="flex h-14 items-center justify-between">
            <button onClick={() => navigate(-1)} className="p-2 rounded-lg text-gray-400 hover:bg-gray-800">
              <ArrowLeft className="w-6 h-6 text-white" />
            </button>
            <h1 className="text-xl font-bold text-white flex-1 text-center">Transaction History</h1>
            <div className="w-10" />
          </div>
        </div>
      </header>

      <main className="pt-16 pb-24 px-4">
        <div className="mx-auto max-w-md">
          {/* Balance Summary */}
          <div className="mb-6">
            <div className="bg-gradient-to-br from-purple-600 to-pink-600 rounded-2xl p-6 shadow-xl shadow-purple-500/25">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-white font-semibold">Current Balance</h2>
                <span className="px-2 py-1 text-xs bg-white/20 rounded-full text-purple-300">
                  COIN
                </span>
              </div>
              <div className="text-4xl font-bold text-white mb-2">
                {wallet.balance.toLocaleString()}
              </div>
              <div className="flex items-center gap-2 text-purple-200 text-sm">
                <span className="flex items-center gap-1">
                  <span className="w-2 h-2 rounded-full bg-green-500" />
                  <span>Available</span>
                </span>
              </div>
            </div>
          </div>

          {/* Filters */}
          <div className="flex flex-wrap gap-2 mb-4">
            <div className="flex gap-2">
              {['all', 'credit', 'debit'].map(filter => (
                <button
                  key={filter}
                  onClick={() => setFilter(filter)}
                  className={cn(
                    'px-3 py-1.5 rounded-full text-sm font-medium transition-colors',
                    filter === 'all' ? 'bg-purple-600 text-white' : 'bg-gray-800 text-gray-300 hover:bg-gray-700'
                  )}
                >
                  {filter.charAt(0).toUpperCase() + filter.slice(1)}
                </button>
              ))}
            </div>
            <div className="flex gap-2 ml-auto">
              <select
                value={dateRange}
                onChange={(e) => setDateRange(e.target.value)}
                className="px-3 py-1.5 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white focus:outline-none focus:ring-2 focus:ring-purple-500"
              >
                <option value="all">All Time</option>
                <option value="today">Today</option>
                <option value="week">This Week</option>
                <option value="month">This Month</option>
              </select>
              <Input
                placeholder="Search transactions..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="w-48"
              />
            </div>
          </div>

          {/* Transaction List */}
          <div className="space-y-3">
            {filteredTransactions.length === 0 ? (
              <div className="text-center py-12 text-gray-500">
                No transactions found
              </div>
            ) : (
              <div className="space-y-3">
                {filteredTransactions.map((tx) => (
                  <div
                    key={tx.id}
                    className="flex items-center justify-between p-4 bg-gray-800/50 rounded-xl hover:bg-gray-800/50 transition-colors"
                  >
                    <div className="flex items-center gap-4 flex-1 min-w-0">
                      <div className={cn(
                        'w-10 h-10 rounded-xl flex items-center justify-center',
                        tx.type === 'credit' ? 'bg-green-500/20' : 'bg-red-500/20'
                      )}>
                        {tx.type === 'credit' ? (
                          <ArrowUpRight className="w-5 h-5 text-green-400" />
                        ) : (
                          <ArrowDownRight className="w-5 h-5 text-red-400" />
                        )}
                      </div>
                      <div className="min-w-0">
                        <p className="text-white font-medium truncate">{tx.desc}</p>
                        <p className="text-xs text-gray-400 flex items-center gap-1">
                          <span>{tx.game}</span>
                          <span className="text-gray-500">·</span>
                          <span>{tx.time}</span>
                        </p>
                      </div>
                    </div>
                    <div className="flex items-center gap-3">
                      <Badge variant={tx.type === 'credit' ? 'success' : 'destructive'}>
                        {tx.type === 'credit' ? '+' : '-'}{tx.amount.toLocaleString()}
                      </Badge>
                      <Badge variant={tx.status === 'completed' ? 'success' : 'warning'}>
                        {tx.status}
                      </Badge>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}

export { HistoryPage };
