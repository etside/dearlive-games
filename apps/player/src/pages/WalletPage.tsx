import React, { useState, useEffect } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useWallet } from '../contexts/WalletContext';
import { useGame } from '../contexts/GameContext';
import { ArrowLeft, Plus, Minus, CreditCard, Coins, ArrowUpRight, ArrowDownRight, RefreshCw, Shield, AlertCircle, CheckCircle, X } from 'lucide-react';
import { cn } from '../utils/cn';
import { Button } from '../components/ui/Button';
import { Input } from '../components/ui/Input';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/Card';

export function WalletPage() {
  const navigate = useNavigate();
  const { balance, currency, refreshBalance, loading } = useWallet();
  const [showDeposit, setShowDeposit] = useState(false);
  const [depositAmount, setDepositAmount] = useState('');
  const [selectedMethod, setSelectedMethod] = useState<'card' | 'crypto' | 'bank'>('card');
  const [processing, setProcessing] = useState(false);

  useEffect(() => {
    refreshBalance();
  }, [refreshBalance]);

  const handleDeposit = async () => {
    if (!depositAmount || parseInt(depositAmount) < 10) return;
    setProcessing(true);
    try {
      // In real app, this would call the deposit API
      await new Promise(r => setTimeout(r, 1500));
      setShowDeposit(false);
      setDepositAmount('');
      // Refresh balance
    } catch (error) {
      console.error('Deposit failed:', error);
    } finally {
      setProcessing(false);
    }
  };

  return (
    <div className="min-h-screen bg-gray-900">
      {/* Header */}
      <header className="fixed top-0 left-0 right-0 z-50 bg-gray-900/95 backdrop-blur-sm border-b border-gray-800">
        <div className="mx-auto max-w-full px-4">
          <div className="flex h-14 items-center justify-between">
            <button onClick={() => navigate(-1)} className="p-2 rounded-lg text-gray-400 hover:bg-gray-800">
              <ArrowLeft className="w-6 h-6 text-white" />
            </button>
            <h1 className="text-xl font-bold text-white flex-1 text-center">Wallet</h1>
            <div className="w-10" />
          </div>
        </div>
      </header>

      <main className="pt-16 pb-24 px-4">
        <div className="mx-auto max-w-md">
          {/* Balance Card */}
          <div className="mb-6">
            <div className="bg-gradient-to-br from-purple-600 to-pink-600 rounded-2xl p-6 shadow-xl shadow-purple-500/25">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-white font-semibold">Available Balance</h2>
                <span className="px-2 py-1 text-xs bg-white/20 rounded-full text-purple-300">
                  {wallet.currency}
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

          {/* Quick Actions */}
          <div className="grid grid-cols-2 gap-4 mb-6">
            <Button
              onClick={() => setShowDeposit(true)}
              className="w-full bg-gradient-to-r from-purple-600 to-pink-600"
            >
              <Plus className="w-5 h-5 mr-2" />
              Deposit
            </Button>
            <Button variant="outline" className="w-full">
              <ArrowDownRight className="w-5 h-5 mr-2" />
              Withdraw
            </Button>
          </div>

          {/* Transaction History */}
          <div>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold text-white">Recent Transactions</h2>
              <Button variant="ghost" size="sm" onClick={() => navigate('/history')}>
                View All
                <ChevronRight className="w-4 h-4 ml-1" />
              </Button>
            </div>
            <div className="space-y-3">
              {[
                { type: 'credit', amount: 5000, desc: 'Deposit via Card', time: '2 min ago', status: 'completed' },
                { type: 'debit', amount: -100, desc: 'Bet on Teen Patti Pro', time: '5 min ago', status: 'completed' },
                { type: 'credit', amount: 2500, desc: 'Win on Greedy Lion', time: '12 min ago', status: 'completed' },
                { type: 'debit', amount: -50, desc: 'Bet on Monkey Wheel', time: '25 min ago', status: 'completed' },
                { type: 'credit', amount: 10000, desc: 'Deposit via Crypto', time: '1 hour ago', status: 'completed' },
              ].map((tx, i) => (
                <div key={i} className="flex items-center justify-between p-4 bg-gray-800/50 rounded-xl">
                  <div className="flex items-center gap-3">
                    <div className={cn('w-10 h-10 rounded-full flex items-center justify-center', tx.type === 'credit' ? 'bg-green-500/20' : 'bg-red-500/20')}>
                      {tx.type === 'credit' ? (
                        <ArrowUpRight className="w-5 h-5 text-green-400" />
                      ) : (
                        <ArrowDownRight className="w-5 h-5 text-red-400" />
                      )}
                    </div>
                    <div>
                      <p className="text-white font-medium">{tx.desc}</p>
                      <p className="text-xs text-gray-400">{tx.time}</p>
                    </div>
                  </div>
                  <div className="text-right">
                    <p className={cn('font-semibold', tx.type === 'credit' ? 'text-green-400' : 'text-red-400')}>
                      {tx.type === 'credit' ? '+' : ''}{tx.amount.toLocaleString()}
                    </p>
                    <span className={cn('text-xs px-2 py-0.5 rounded-full', tx.status === 'completed' ? 'bg-green-500/20 text-green-400' : 'bg-yellow-500/20 text-yellow-400')}>
                      {tx.status}
                    </span>
                  </div>
                ))}
              </div>
            </div>

            {/* Deposit Modal */}
            {showDeposit && (
              <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
                <div className="bg-gray-900 rounded-2xl p-6 w-full max-w-md">
                  <div className="flex items-center justify-between mb-6">
                    <h2 className="text-xl font-bold text-white">Deposit Funds</h2>
                    <button onClick={() => setShowDeposit(false)} className="text-gray-400 hover:text-white">
                      <X className="w-5 h-5" />
                    </button>
                  </div>
                  <div className="space-y-4">
                    <div>
                      <label className="block text-sm font-medium text-gray-300 mb-1">Amount ({wallet.currency})</label>
                      <Input
                        type="number"
                        value={depositAmount}
                        onChange={(e) => setDepositAmount(e.target.value)}
                        placeholder="Enter amount"
                        min="10"
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-300 mb-2">Payment Method</label>
                      <div className="grid grid-cols-3 gap-3">
                        {['card', 'crypto', 'bank'].map(method => (
                          <button
                            key={method}
                            onClick={() => setSelectedMethod(method as any)}
                            className={cn(
                              'p-4 rounded-xl border-2 text-center transition-all',
                              selectedMethod === method
                                ? 'border-purple-500 bg-purple-500/10'
                                : 'border-gray-700 hover:border-gray-600'
                            )}
                          >
                            <div className="text-2xl mb-1">
                              {method === 'card' && <CreditCard className="w-6 h-6 mx-auto" />}
                              {method === 'crypto' && <Coins className="w-6 h-6 mx-auto" />}
                              {method === 'bank' && <Banknote className="w-6 h-6 mx-auto" />}
                            </div>
                            <span className="capitalize font-medium">{method}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                    <Button
                      onClick={handleDeposit}
                      disabled={processing || !depositAmount || parseInt(depositAmount) < 10}
                      className="w-full mt-6"
                    >
                      {processing ? 'Processing...' : `Deposit ${depositAmount} ${wallet.currency}`}
                    </Button>
                  </div>
                </div>
              </div>
            ) : null}
          </div>
        </div>
      </main>
    </div>
  );
}

export { WalletPage };
