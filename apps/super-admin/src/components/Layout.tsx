import React, { useState } from 'react';
import { Link, useLocation, Outlet } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { 
  LayoutDashboard, Users, Key, Gamepad2, Wallet, 
  Gamepad2 as GamesIcon, Shield, Logs, Webhook, 
  Server, Settings, LogOut, Menu, X, ChevronDown
} from 'lucide-react';
import { cn } from '../../utils/cn';

export function Layout() {
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const { user, logout } = useAuth();
  const location = useLocation();

  const navigation = [
    { name: 'Dashboard', href: '/dashboard', icon: LayoutDashboard },
    { name: 'Operators', href: '/operators', icon: Users },
    { name: 'API Keys', href: '/api-keys', icon: Key },
    { name: 'Games', href: '/games', icon: GamesIcon },
    { name: 'Wallet', href: '/wallet', icon: Wallet },
    { name: 'Audit Logs', href: '/audit-logs', icon: Logs },
    { name: 'Webhooks', href: '/webhooks', icon: Webhook },
    { name: 'System Status', href: '/system-status', icon: Server },
    { name: 'Staging', href: '/staging', icon: Settings },
  ];

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Sidebar */}
      <aside className={cn(
        'fixed inset-y-0 left-0 z-50 w-64 bg-white border-r border-gray-200 transition-all duration-300',
        sidebarOpen ? 'w-64' : 'w-20'
      )}>
        <div className="flex h-16 items-center justify-between px-4 border-b border-gray-200">
          {sidebarOpen && (
            <Link to="/dashboard" className="flex items-center gap-2 text-xl font-bold text-gray-900">
              <span className="text-purple-600">🎮</span>
              <span>Super Admin</span>
            </Link>
          )}
          <button
            onClick={() => setSidebarOpen(!sidebarOpen)}
            className="p-2 rounded-lg text-gray-500 hover:bg-gray-100"
            aria-label={sidebarOpen ? 'Collapse sidebar' : 'Expand sidebar'}
          >
            {sidebarOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
          </button>
        </div>
        <nav className="flex-1 p-4 space-y-1 overflow-y-auto">
          {navigation.map((item) => {
            const isActive = location.pathname === item.href || location.pathname.startsWith(item.href + '/');
            return (
              <Link
                key={item.name}
                to={item.href}
                className={cn(
                  'flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors',
                  isActive
                    ? 'bg-purple-50 text-purple-700'
                    : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900'
                )}
              >
                <item.icon className={cn('w-5 h-5', sidebarOpen ? '' : 'mx-auto')} />
                {sidebarOpen && <span>{item.name}</span>}
              </Link>
            );
          })}
        </nav>
        <div className="p-4 border-t border-gray-200">
          <div className="flex items-center gap-3 px-3 py-2">
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-gray-900 truncate">{user?.name}</p>
              <p className="text-xs text-gray-500 truncate capitalize">{user?.role}</p>
            </div>
            <button onClick={logout} className="p-2 text-gray-400 hover:text-red-600 hover:bg-red-50 rounded-lg" title="Logout">
              <LogOut className="w-5 h-5" />
            </button>
          </div>
        </div>
      </aside>

      {/* Main Content */}
      <div className={cn('transition-all duration-300', sidebarOpen ? 'lg:ml-64' : 'lg:ml-20')}>
        <header className="sticky top-0 z-40 bg-white border-b border-gray-200">
          <div className="flex items-center justify-between h-16 px-6">
            <button
              onClick={() => setSidebarOpen(!sidebarOpen)}
              className="lg:hidden p-2 text-gray-500 hover:bg-gray-100 rounded-lg"
            >
              <Menu className="w-6 h-6" />
            </button>
            <div className="flex-1" />
            <div className="flex items-center gap-4">
              <div className="hidden sm:flex items-center gap-2 text-sm text-gray-500">
                <span className="px-2 py-1 bg-purple-100 text-purple-700 rounded-full text-xs font-medium">
                  Super Admin
                </span>
              </div>
            </div>
          </div>
        </header>
        <main className="p-6">
          <Outlet />
        </main>
      </div>
    </>
  );
}
