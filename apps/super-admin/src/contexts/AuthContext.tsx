import React, { createContext, useContext, useState, useEffect, ReactNode } from 'react';
import { api } from '../utils/api';

interface User {
  id: string;
  email: string;
  role: 'superadmin' | 'admin' | 'viewer';
  name: string;
}

interface AuthContextType {
  user: User | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
  hasPermission: (permission: string) => boolean;
}

const AuthContext = createContext<AuthContextType | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const token = localStorage.getItem('super_admin_token');
    if (token) {
      // Validate token
      fetch('/api/auth/me', {
        headers: { Authorization: `Bearer ${token}` },
      })
        .then(res => res.ok ? res.json() : null)
        .then(data => {
          if (data?.user) setUser(data.user);
          else localStorage.removeItem('super_admin_token');
        })
        .finally(() => setIsLoading(false));
    } else {
      setIsLoading(false);
    }
  }, []);

  const login = async (email: string, password: string) => {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });
    if (!res.ok) throw new Error('Invalid credentials');
    const { token, user } = await res.json();
    localStorage.setItem('super_admin_token', token);
    setUser(user);
  };

  const logout = () => {
    localStorage.removeItem('super_admin_token');
    setUser(null);
  };

  const hasPermission = (permission: string) => {
    // Implement role-based permission check
    return true; // Simplified for now
  };

  return (
    <AuthContext.Provider value={{ user, isAuthenticated: !!user, isLoading, login, logout, hasPermission }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth must be used within AuthProvider');
  return context;
}
