import React from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { QueryClientProvider } from '@tanstack/react-query';
import { GameProvider } from './contexts/GameContext';
import { WalletProvider } from './contexts/WalletContext';
import { GamesHomePage } from './pages/GamesHomePage';
import { GamePage } from './pages/GamePage';
import { WalletPage } from './pages/WalletPage';
import { HistoryPage } from './pages/HistoryPage';
import { SettingsPage } from './pages/SettingsPage';
import { GameCanvas } from './components/GameCanvas';
import { Header } from './components/Header';
import { ToastContainer } from './components/ToastContainer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 1000 * 60 * 5,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<GamesHomePage />} />
      <Route path="/game/:gameCode" element={<GamePage />} />
      <Route path="/wallet" element={<WalletPage />} />
      <Route path="/history" element={<HistoryPage />} />
      <Route path="/settings" element={<SettingsPage />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export function App() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 1000 * 60 * 5,
        retry: 1,
        refetchOnWindowFocus: false,
      },
    },
  });

  return (
    <QueryClientProvider client={queryClient}>
      <GameProvider>
        <WalletProvider>
          <div className="min-h-screen bg-gray-900 text-white">
            <Header />
            <main className="pb-24">
              <AppRoutes />
            </main>
            <ToastContainer />
          </div>
        </WalletProvider>
      </GameProvider>
    </QueryClientProvider>
  );
}

export default App;
