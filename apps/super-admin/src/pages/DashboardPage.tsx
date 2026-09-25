import React from 'react';
import { Link } from 'react-router-dom';
import { Users, Key, Gamepad2, Wallet, TrendingUp, Activity, ArrowUpRight, ArrowDownRight } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '../../components/ui/Card';
import { Button } from '../components/ui/Button';

const stats = [
  { name: 'Total Operators', value: '24', change: '+12%', trend: 'up', icon: Users, color: 'bg-blue-500' },
  { name: 'Active API Keys', value: '156', change: '+8%', trend: 'up', icon: Key, color: 'bg-purple-500' },
  { name: 'Active Games', value: '3', change: '0%', trend: 'neutral', icon: Gamepad2, color: 'bg-green-500' },
  { name: 'Total Balance', value: '2.4M', change: '-2.3%', trend: 'down', icon: Wallet, color: 'bg-amber-500' },
];

export function DashboardPage() {
  const stats = [
    { name: 'Total Operators', value: '24', change: '+12%', trend: 'up', icon: Users, color: 'bg-blue-500' },
    { name: 'Active API Keys', value: '156', change: '+8%', trend: 'up', icon: Key, color: 'bg-purple-500' },
    { name: 'Active Games', value: '3', change: '0%', trend: 'neutral', icon: Gamepad2, color: 'bg-green-500' },
    { name: 'Total Balance', value: '2.4M', change: '-2.3%', trend: 'down', icon: Wallet, color: 'bg-amber-500' },
  ];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">Dashboard</h1>
          <p className="text-gray-500 mt-1">Overview of your platform</p>
        </div>
        <div className="flex gap-3">
          <button className="btn-primary">Create Operator</button>
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        {stats.map((stat) => (
          <Card key={stat.name}>
            <CardContent className="p-6">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium text-gray-500">{stat.name}</p>
                  <p className="text-3xl font-bold text-gray-900 mt-1">{stat.value}</p>
                </div>
                <div className={`p-3 rounded-xl ${stat.color}`}>
                  <stat.icon className="w-6 h-6 text-white" />
                </div>
              </div>
              <div className="flex items-center mt-4 text-sm">
                <span className={stat.trend === 'up' ? 'text-green-600' : stat.trend === 'down' ? 'text-red-600' : 'text-gray-500'}>
                  {stat.trend === 'up' ? <ArrowUpRight className="w-4 h-4 mr-1" /> : stat.trend === 'down' ? <ArrowDownRight className="w-4 h-4 mr-1" /> : null}
                  <span className="font-medium">{stat.change}</span>
                </span>
                <span className="text-gray-500 ml-2">vs last month</span>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Recent Activity</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              {[
                { action: 'New operator registered', target: 'Acme Gaming', time: '2 min ago', type: 'success' },
                { action: 'API key created', target: 'GameDev Studio', time: '15 min ago', type: 'info' },
                { action: 'Game config updated', target: 'Teen Patti Pro', time: '1 hour ago', type: 'warning' },
                { action: 'Webhook configured', target: 'Operator ABC', time: '3 hours ago', type: 'success' },
              ].map((activity, i) => (
                <div key={i} className="flex items-center gap-4 p-4 bg-gray-50 rounded-lg">
                  <div className="w-10 h-10 rounded-full bg-gray-100 flex items-center justify-center">
                    <Activity className="w-5 h-5 text-gray-600" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-gray-900">{activity.action}</p>
                    <p className="text-sm text-gray-500 truncate">{activity.target}</p>
                  </div>
                  <span className="text-xs text-gray-500">{activity.time}</span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>System Health</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              {[
                { name: 'API Latency', value: '42ms', status: 'healthy' },
                { name: 'WebSocket Connections', value: '1,234', status: 'healthy' },
                { name: 'Wallet Service', value: 'Operational', status: 'healthy' },
                { name: 'Redis Cache', value: '98% free', status: 'warning' },
              ].map((item) => (
                <div key={item.name} className="flex items-center justify-between">
                  <span className="text-sm text-gray-600">{item.name}</span>
                  <div className="flex items-center gap-2">
                    <span className={`px-2 py-1 text-xs font-medium rounded-full ${
                      item.status === 'healthy' ? 'bg-green-100 text-green-800' :
                      item.status === 'warning' ? 'bg-yellow-100 text-yellow-800' :
                      'bg-red-100 text-red-800'
                    }`}>
                      {item.status}
                    </span>
                    <span className="text-sm font-medium text-gray-900">{item.value}</span>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>
    );
  </div>
);
}

export { DashboardPage };
