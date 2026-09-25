import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useWallet } from '../contexts/WalletContext';
import { useGame } from '../contexts/GameContext';
import { ArrowLeft, User, Bell, Shield, Palette, LogOut, Save, Loader2 } from 'lucide-react';
import { cn } from '../utils/cn';
import { Button } from '../components/ui/Button';
import { Input } from '../components/ui/Input';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/Card';
import { Switch } from '../components/ui/Switch';

export function SettingsPage() {
  const navigate = useNavigate();
  const { balance, currency, refreshBalance } = useWallet();
  const { games, currentGame, setCurrentGame } = useGame();
  const [saving, setSaving] = useState(false);
  const [settings, setSettings] = useState({
    sound: true,
    music: true,
    animations: true,
    notifications: true,
    autoBet: false,
    autoBetAmount: 100,
    autoBetRounds: 10,
    theme: 'dark',
    language: 'en',
  });

  const handleSave = async () => {
    setSaving(true);
    await new Promise(r => setTimeout(r, 1000));
    setSaving(false);
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
            <h1 className="text-xl font-bold text-white flex-1 text-center">Settings</h1>
            <div className="w-10" />
          </div>
        </div>
      </header>

      <main className="pt-16 pb-24 px-4">
        <div className="mx-auto max-w-2xl space-y-6">
          {/* Profile Section */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <User className="w-5 h-5" />
                Profile
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid gap-4 sm:grid-cols-2">
                <div>
                  <label className="block text-sm font-medium text-gray-400 mb-1">Display Name</label>
                  <Input value="Player123" readOnly className="bg-gray-800" />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-400 mb-1">Player ID</label>
                  <Input value="player_12345" readOnly className="bg-gray-800" />
                </div>
              </div>
              <div className="pt-4 border-t border-gray-800">
                <Button onClick={() => {}} variant="secondary" className="w-full">
                  Edit Profile
                </Button>
              </div>
            </CardContent>
          </Card>

          {/* Game Preferences */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Gamepad2 className="w-5 h-5" />
                Game Preferences
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-6">
              <div>
                <label className="block text-sm font-medium text-gray-400 mb-2">Default Game</label>
                <select
                  value={settings.defaultGame || ''}
                  onChange={(e) => setSettings(s => ({ ...s, defaultGame: e.target.value }))}
                  className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-purple-500"
                >
                  <option value="">Last Played</option>
                  {games.map(g => (
                    <option key={g.game_code} value={g.game_code}>
                      {g.name}
                    </option>
                  ))}
                </select>
              </div>

              <div className="grid gap-4 sm:grid-cols-2">
                <label className="flex items-center gap-3">
                  <Switch checked={settings.sound} onCheckedChange={c => setSettings(s => ({ ...s, sound: c }))} />
                  <span className="text-white">Sound Effects</span>
                </label>
                <label className="flex items-center gap-3">
                  <Switch checked={settings.music} onCheckedChange={c => setSettings(s => ({ ...s, music: c }))} />
                  <span className="text-white">Background Music</span>
                </label>
                <label className="flex items-center gap-3">
                  <Switch checked={settings.animations} onCheckedChange={c => setSettings(s => ({ ...s, animations: c }))} />
                  <span className="text-white">Animations</span>
                </label>
                <label className="flex items-center gap-3">
                  <Switch checked={settings.notifications} onCheckedChange={c => setSettings(s => ({ ...s, notifications: c }))} />
                  <span className="text-white">Push Notifications</span>
                </label>
              </div>

              <div className="pt-4 border-t border-gray-800">
                <label className="block text-sm font-medium text-gray-400 mb-2">Auto-Bet Settings</label>
                <div className="grid gap-4 sm:grid-cols-3">
                  <div>
                    <label className="block text-sm font-medium text-gray-400 mb-1">Default Bet Amount</label>
                    <Input
                      type="number"
                      value={settings.autoBetAmount}
                      onChange={(e) => setSettings(s => ({ ...s, autoBetAmount: parseInt(e.target.value) || 0 }))}
                      min="1"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-400 mb-1">Max Rounds</label>
                    <Input
                      type="number"
                      value={settings.autoBetRounds}
                      onChange={(e) => setSettings(s => ({ ...s, autoBetRounds: parseInt(e.target.value) || 0 }))}
                      min="1"
                      max="100"
                    />
                  </div>
                  <div>
                    <label className="flex items-center gap-3">
                      <Switch checked={settings.autoBet} onCheckedChange={c => setSettings(s => ({ ...s, autoBet: c }))} />
                      <span className="text-white">Enable Auto-Bet by Default</span>
                    </label>
                  </div>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Appearance */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Palette className="w-5 h-5" />
                Appearance
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-400 mb-2">Theme</label>
                <div className="flex gap-3">
                  {['dark', 'light', 'system'].map(theme => (
                    <label
                      key={theme}
                      className={cn(
                        'flex-1 p-4 rounded-xl border-2 transition-all',
                        settings.theme === theme
                          ? 'border-purple-500 bg-purple-500/10'
                          : 'border-gray-700 hover:border-gray-600'
                      )}
                      onClick={() => setSettings(s => ({ ...s, theme }))}
                    >
                      <div className="text-center">
                        <span className="text-2xl mb-1">
                          {theme === 'dark' && '🌙'}
                          {theme === 'light' && '☀️'}
                          {theme === 'system' && '💻'}
                        </span>
                        <p className="capitalize text-sm font-medium text-white">{theme}</p>
                      </div>
                    </label>
                  ))}
                </div>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-400 mb-2">Language</label>
                <select
                  value={settings.language}
                  onChange={(e) => setSettings(s => ({ ...s, language: e.target.value }))}
                  className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-purple-500"
                >
                  <option value="en">English</option>
                  <option value="es">Español</option>
                  <option value="hi">हिन्दी</option>
                  <option value="zh">中文</option>
                </select>
              </div>
            </CardContent>
          </Card>

          {/* Security */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Shield className="w-5 h-5" />
                Security
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid gap-4 sm:grid-cols-2">
                <Button variant="outline" className="w-full">
                  <ShieldCheck className="w-5 h-5 mr-2" />
                  Two-Factor Authentication
                </Button>
                <Button variant="outline" className="w-full">
                  <Key className="w-5 h-5 mr-2" />
                  Change Password
                </Button>
                <Button variant="outline" className="w-full">
                  <Activity className="w-5 h-5 mr-2" />
                  Login History
                </Button>
                <Button variant="outline" className="w-full" variant="destructive">
                  <LogOut className="w-5 h-5 mr-2" />
                  Logout All Sessions
                </Button>
              </div>
            </CardContent>
          </Card>

          {/* Save Button */}
          <Button onClick={handleSave} disabled={saving} className="w-full mt-6">
            {saving ? <Loader2 className="w-5 h-5 mr-2 animate-spin" /> : <Save className="w-5 h-5 mr-2" />}
            Save Settings
          </Button>
        </div>
      </main>
    </div>
  );
}

export { SettingsPage };
