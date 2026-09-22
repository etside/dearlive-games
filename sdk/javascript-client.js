/* DearLive Games SDK (browser/WebView) — configurable, no hardcoded hosts.
 * The DearLive developer sets apiBase/wsBase to their own GAMES_BASE_URL.
 * Covers: launch → session → state → bet (idempotent) → result → history →
 * wallet → reconnect. Server is authoritative; this renders snapshots only.
 */
(function (global) {
  'use strict';
  function uuid() {
    if (global.crypto && crypto.randomUUID) return crypto.randomUUID();
    return 'xxxxxxxx-xxxx-4xxx'.replace(/x/g, () =>
      ((Math.random() * 16) | 0).toString(16));
  }
  function Client(opts) {
    opts = opts || {};
    if (!opts.apiBase) throw new Error('apiBase (GAMES_BASE_URL) required');
    this.apiBase = String(opts.apiBase).replace(/\/$/, '');
    this.wsBase = String(opts.wsBase || opts.apiBase
      .replace(/^http/, 'ws')).replace(/\/$/, '');
    this.session = opts.session || '';
    this.room = opts.room || 'default';
  }
  Client.prototype._headers = function (extra) {
    return Object.assign({ Authorization: 'Bearer ' + this.session },
      extra || {});
  };
  Client.prototype._call = async function (path, opts) {
    opts = opts || {};
    const r = await fetch(this.apiBase + path, {
      method: opts.method || 'GET',
      headers: this._headers(opts.headers),
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    const j = await r.json();
    if (!j.success) { const e = new Error(j.code + ': ' + j.message); e.code = j.code; throw e; }
    return j.data;
  };
  Client.prototype.openSession = async function (launchToken) {
    const d = await this._call('/api/v1/sessions',
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: { launch_token: launchToken } });
    this.session = d.session_id; this.room = d.room_id || this.room;
    return d;
  };
  Client.prototype.state = function () {
    return this._call('/api/v1/games/teen-patti-pro/rounds/current?room='
      + encodeURIComponent(this.room));
  };
  Client.prototype.placeBet = function (position, amount, key) {
    return this._call('/api/v1/games/teen-patti-pro/rooms/'
      + encodeURIComponent(this.room) + '/bets', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Idempotency-Key': key || uuid() },
      body: { position, amount },
    });
  };
  Client.prototype.result = function (roundId) {
    return this._call('/api/v1/games/teen-patti-pro/rounds/'
      + encodeURIComponent(roundId) + '/result?room=' + encodeURIComponent(this.room));
  };
  Client.prototype.history = function () {
    return this._call('/api/v1/games/teen-patti-pro/history?room='
      + encodeURIComponent(this.room));
  };
  Client.prototype.wallet = function () {
    return this._call('/api/v1/games/teen-patti-pro/rooms/'
      + encodeURIComponent(this.room) + '/wallet');
  };
  Client.prototype.reconnect = function (lastSeenSeq) {
    return this._call('/api/v1/games/teen-patti-pro/rooms/'
      + encodeURIComponent(this.room) + '/reconnect', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: { session_id: this.session, last_seen_seq: lastSeenSeq || 0 },
    });
  };
  Client.prototype.subscribe = function (handlers) {
    handlers = handlers || {};
    const ws = new WebSocket(this.wsBase + '?session='
      + encodeURIComponent(this.session) + '&room=' + encodeURIComponent(this.room));
    ws.onopen = () => ws.send(JSON.stringify(
      { type: 'subscribe', session: this.session, room: this.room }));
    ws.onmessage = (m) => {
      let msg; try { msg = JSON.parse(m.data); } catch { return; }
      const h = handlers[msg.type] || handlers.message;
      if (h) h(msg);
    };
    return ws;
  };
  global.DearLiveGames = { Client, uuid };
}(typeof window !== 'undefined' ? window : globalThis));
