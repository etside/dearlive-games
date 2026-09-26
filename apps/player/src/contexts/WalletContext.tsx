import React, { createContext, useContext, useState, useEffect, ReactNode } from 'react';
import { apiUrl } from '../config/apiBase';

interface WalletState {
  balance: number;
  currency: string;
  loading: boolean;
  refreshBalance: () => Promise<void>;
}

const WalletContext = createContext<WalletState | null>(null);

export function WalletProvider({ children }: { children: React.ReactNode }) {
  const [balance, setBalance] = useState(0);
  const [currency, setCurrency] = useState('COIN');
  const [loading, setLoading] = useState(true);

  const fetchBalance = async () => {
    try {
      const response = await fetch(apiUrl(`/api/v1/players/me/balance`), {
        headers: {
          'Authorization': `Bearer ${localStorage.getItem('player_session_token')}`,
        },
      });
      if (response.ok) {
        const data = await response.json();
        if (data.success) {
          setBalance(data.data.available);
          setCurrency(data.data.currency);
        }
      } catch (error) {
        console.error('Failed to fetch balance:', error);
      }
    };

  useEffect(() => {
    if (localStorage.getItem('player_session_token')) {
      fetchBalance();
    }
    setLoading(false);
  }, []);

  const refreshBalance = async () => {
    setLoading(true);
    await fetchBalance();
    setLoading(false);
  };

  return (
    <WalletContext.Provider value={{ balance, currency, loading, refreshBalance }}>
      {children}
    </WalletContext.Provider>
  );
}

export function useWallet() {
  const context = useContext(WalletContext);
  if (!context) throw new Error('useWallet must be used within WalletProvider');
  return context;
}
