const API_BASE = import.meta.env.VITE_API_BASE || 'https://dearlive-games.vercel.app';

export async function api<T>(endpoint: string, options: RequestInit = {}): Promise<any> {
  const url = `${import.meta.env.VITE_API_BASE || 'https://dearlive-games.vercel.app'}${endpoint}`;
  
  const response = await fetch(endpoint.startsWith('http') ? endpoint : `${import.meta.env.VITE_API_BASE || 'https://dearlive-games.vercel.app'}${endpoint}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options.headers,
    },
    credentials: 'include',
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ message: 'Request failed' }));
    throw new Error(error.message || 'Request failed');
  }

  return response.json();
}

export const api = {
  get: (endpoint: string) => apiRequest('GET', endpoint),
  post: (endpoint: string, data: any) => apiRequest('POST', endpoint, data),
  put: (endpoint: string, data: any) => apiRequest('PUT', endpoint, data),
  delete: (endpoint: string) => apiRequest('DELETE', endpoint),
};

async function apiRequest(method: string, endpoint: string, data?: any) {
  const response = await fetch(`${import.meta.env.VITE_API_BASE || 'https://dearlive-games.vercel.app'}${endpoint}`, {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(typeof window !== 'undefined' && localStorage.getItem('super_admin_token') 
        ? { Authorization: `Bearer ${localStorage.getItem('super_admin_token')}` }
        : {}),
    },
    body: data ? JSON.stringify(data) : undefined,
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ message: 'Request failed' }));
    throw new Error(error.message || 'Request failed');
  }

  return response.json();
}
