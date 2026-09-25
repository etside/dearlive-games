import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { Plus, Search, Filter, MoreVertical, Edit, Trash2, Shield, Key, Activity, Mail, ShieldCheck } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/Card';
import { Button } from '../components/ui/Button';
import { Input } from '../components/ui/Input';
import { Table } from '../components/ui/Table';
import { Badge } from '../components/ui/Badge';

const mockOperators = [
  { id: 'op_1', name: 'Acme Gaming', email: 'ops@acmegaming.com', status: 'active', apiKeys: 3, games: ['teen_patti_pro', 'greedy_lion'], created: '2024-01-15' },
  { id: 'op_2', name: 'GameDev Studio', email: 'ops@gamedev.io', status: 'active', apiKeys: 5, games: ['teen_patti_pro', 'greedy_lion', 'monkey_wheel'], created: '2024-02-20' },
  { id: 'op_3', name: 'Lucky Games Inc', email: 'ops@luckygames.com', status: 'suspended', apiKeys: 1, games: ['teen_patti_pro'], created: '2024-03-10' },
  { id: 'op_4', name: 'Royal Casino', email: 'ops@royalcasino.com', status: 'active', apiKeys: 2, games: ['teen_patti_pro', 'monkey_wheel'], created: '2024-01-05' },
];

export function OperatorsPage() {
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  const filteredOperators = mockOperators.filter(op => 
    op.name.toLowerCase().includes(search.toLowerCase()) &&
    (statusFilter === 'all' || op.status === statusFilter)
  );

  const columns = [
    { header: 'Operator', accessor: (op: any) => (
      <div>
        <p className="font-medium">{op.name}</p>
        <p className="text-sm text-gray-500">{op.email}</p>
      </div>
    )},
    { header: 'Status', accessor: (op: any) => (
      <Badge variant={op.status === 'active' ? 'success' : op.status === 'suspended' ? 'destructive' : 'warning'}>
        {op.status}
      </Badge>
    )},
    { header: 'API Keys', accessor: (op: any) => op.apiKeys },
    { header: 'Games', accessor: (op: any) => (
      <div className="flex gap-1">
        {op.games.map(g => <Badge key={g} variant="outline" className="text-xs">{g}</Badge>)}
      </div>
    )},
    { header: 'Created', accessor: (op: any) => op.created },
    { header: 'Actions', accessor: (op: any) => (
      <div className="flex gap-2">
        <button className="p-2 text-gray-500 hover:text-blue-600 hover:bg-blue-50 rounded-lg">
          <Eye className="w-4 h-4" />
        </button>
        <button className="p-2 text-gray-500 hover:text-blue-600 hover:bg-blue-50 rounded-lg">
          <Edit className="w-4 h-4" />
        </button>
        <button className="p-2 text-gray-500 hover:text-red-600 hover:bg-red-50 rounded-lg">
          <Trash2 className="w-4 h-4" />
        </button>
      </div>
    )},
  ];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">Operators</h1>
          <p className="text-gray-500 mt-1">Manage operator accounts and API access</p>
        </div>
        <Button onClick={() => {}}>Create Operator</Button>
      </div>

      <div className="bg-white rounded-lg border border-gray-200">
        <div className="p-4 border-b border-gray-200 flex flex-col sm:flex-row gap-4">
          <div className="flex-1">
            <Input 
              placeholder="Search operators..." 
              value={search} 
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search by name or email..."
            />
          </div>
          <select 
            value={statusFilter} 
            onChange={(e) => setStatusFilter(e.target.value)}
            className="w-48 px-3 py-2 border border-gray-300 rounded-lg text-sm"
          >
            <option value="all">All Status</option>
            <option value="active">Active</option>
            <option value="suspended">Suspended</option>
            <option value="pending">Pending</option>
          </select>
        </div>
      </div>
      
      <Table
        data={filteredOperators}
        columns={[
          { header: 'Operator', accessor: (op) => <div><p className="font-medium">{op.name}</p><p className="text-sm text-gray-500">{op.email}</p></div> },
          { header: 'Status', accessor: (op) => <Badge variant={op.status === 'active' ? 'success' : op.status === 'suspended' ? 'destructive' : 'warning'}>{op.status}</Badge> },
          { header: 'API Keys', accessor: (op) => op.apiKeys },
          { header: 'Games', accessor: (op) => <div className="flex gap-1">{op.games.map(g => <Badge key={g} variant="outline" className="text-xs">{g}</Badge>)}</div> },
          { header: 'Created', accessor: (op) => op.created },
          { header: 'Actions', accessor: (op) => (
            <div className="flex gap-2">
              <button className="p-2 text-gray-500 hover:text-blue-600 hover:bg-blue-50 rounded-lg"><Eye className="w-4 h-4" /></button>
              <button className="p-2 text-gray-500 hover:text-blue-600 hover:bg-blue-50 rounded-lg"><Edit className="w-4 h-4" /></button>
              <button className="p-2 text-gray-500 hover:text-red-600 hover:bg-red-50 rounded-lg"><Trash2 className="w-4 h-4" /></button>
            </div>
          )},
        ]}
        data={filteredOperators}
        keyAccessor={(op) => op.id}
      />
    </div>
  );
}

export { OperatorsPage };
