import React from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import { Layout } from './components/Layout';
import { LoginPage } from './pages/LoginPage';
import { DashboardPage } from './pages/DashboardPage';
import { OperatorsPage } from './pages/OperatorsPage';
import { ApiKeysPage } from './pages/ApiKeysPage';
import { GamesPage } from './pages/GamesPage';
import { GameConfigPage } from './pages/GameConfigPage';
import { WalletPage } from './pages/WalletPage';
import { AuditLogsPage } from './pages/AuditLogsPage';
import { WebhooksPage } from './pages/WebhooksPage';
import { SystemStatusPage } from './pages/SystemStatusPage';
import { StagingProvisionPage } from './pages/StagingProvisionPage';
import { PrivateRoute } from './components/PrivateRoute';
import { Toaster } from './components/Toaster';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 1000 * 60 * 5,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

function PrivateRoute({ children, requiredPermission }: { children: React.ReactNode; requiredPermission?: string }) {
  const { isAuthenticated, isLoading, hasPermission } = useAuth();
  const location = useLocation();

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="animate-spin rounded-full h-12 w-12 border-4 border-purple-600 border-t-transparent"></div>
      </div>
    );
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" state={{ from: window.location.pathname }} replace />;
  }

  if (requiredPermission && !hasPermission(requiredPermission)) {
    return <Navigate to="/dashboard" replace />;
  }

  return <>{children}</>;
}

function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<PrivateRoute><Layout /></Route>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="dashboard" element={<DashboardPage />} />
        <Route path="operators" element={<OperatorsPage />} />
        <Route path="api-keys" element={<ApiKeysPage />} />
        <Route path="games" element={<GamesPage />} />
        <Route path="games/:gameId/config" element={<GameConfigPage />} />
        <Route path="wallet" element={<WalletPage />} />
        <Route path="audit-logs" element={<AuditLogsPage />} />
        <Route path="webhooks" element={<WebhooksPage />} />
        <Route path="system-status" element={<SystemStatusPage />} />
        <Route path="staging" element={<StagingProvisionPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  );
}

function PrivateRoute({ children, requiredPermission }: { children: React.ReactNode; requiredPermission?: string }) {
  const { isAuthenticated, isLoading, hasPermission } = useAuth();
  const location = useLocation();

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="animate-spin rounded-full h-12 w-12 border-4 border-purple-600 border-t-transparent"></div>
      </div>
    );
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" state={{ from: window.location.pathname }} replace />;
  }

  if (requiredPermission && !hasPermission(requiredPermission)) {
    return <Navigate to="/dashboard" replace />;
  }

  return <>{children}</>;
}

function App() {
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
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route element={<PrivateRoute><Layout /></Route>
            <Route path="/" element={<Navigate to="/dashboard" replace />} />
            <Route path="dashboard" element={<DashboardPage />} />
            <Route path="operators" element={<OperatorsPage />} />
            <Route path="api-keys" element={<ApiKeysPage />} />
            <Route path="games" element={<GamesPage />} />
            <Route path="games/:gameId/config" element={<GameConfigPage />} />
            <Route path="wallet" element={<WalletPage />} />
            <Route path="audit-logs" element={<AuditLogsPage />} />
            <Route path="webhooks" element={<WebhooksPage />} />
            <Route path="system-status" element={<SystemStatusPage />} />
            <Route path="staging" element={<StagingProvisionPage />} />
          </Route>
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Routes>
      </AuthProvider>
      <Toaster />
    </QueryClientProvider>
  );
}

export default App;
