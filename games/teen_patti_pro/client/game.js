/* Teen Patti Pro WebView client — Canvas 2D (GPU-composited in WebView).
 * Server-authoritative: renders ONLY server snapshot + WS events.
 * Never computes results/balances locally. Timer from serverTime only.
 * Launch: ?api=http://HOST:5002&ws=ws://HOST:5003&session=<id>&room=<room>
 */
(function () {
  'use strict';
  const q = new URLSearchParams(location.search);
  const API = (q.get('api') || 'http://127.0.0.1:5002').replace(/\/$/, '');
  const WS = (q.get('ws') || 'ws://127.0.0.1:5003').replace(/\/$/, '');
  const SESSION = q.get('session') || '';
  const ROOM = q.get('room') || 'default';

  // Live skin tokens: theme.json (same directory) overrides these at boot;
  // compiled defaults keep the BRD casino look when the file is absent
  // (e.g. file:// or CDN without the manifest).
  const THEME = {
    feltA: '#147a52', feltB: '#083a28',
    gold: '#ffd54a', goldDeep: '#f59e0b',
    seatA: '#ef4444', seatB: '#3b82f6', seatC: '#22c55e',
    text: '#ffffff', potText: '#ffe9a8',
  };
  fetch('theme.json').then(r => r.json()).then(t => {
    try {
      const c = t.canvas || {}, th = t.theme || {};
      if (c.feltA) THEME.feltA = c.feltA;
      if (c.feltB) THEME.feltB = c.feltB;
      if (c.gold || th.accent) THEME.gold = c.gold || th.accent;
      if (c.goldDeep) THEME.goldDeep = c.goldDeep;
      if (Array.isArray(th.seats)) {
        if (th.seats[0]) THEME.seatA = th.seats[0];
        if (th.seats[1]) THEME.seatB = th.seats[1];
        if (th.seats[2]) THEME.seatC = th.seats[2];
      }
      if (th.text) THEME.text = th.text;
    } catch (e) { /* keep defaults */ }
  }).catch(() => {});

  const cv = document.getElementById('c'), ctx = cv.getContext('2d');
  const errBox = document.getElementById('err');
  let W = 0, H = 0, DPR = 1;
  function resize() {
    DPR = Math.min(3, window.devicePixelRatio || 1);
    W = window.innerWidth; H = window.innerHeight;
    cv.width = W * DPR; cv.height = H * DPR;
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  }
  window.addEventListener('resize', resize); resize();

  const S = { snap: null, selDenom: 100, selPos: null, lastSeq: 0, connected: false, msg: '' };
  const DENOMS = [20, 100, 500, 1000];
  const POS = ['A', 'B', 'C'];
  // Common HUD state (BRD common UI): panel overlay, sound/music toggle.
  S.panel = null; // null | 'help' | 'menu' | 'history'
  try { S.sound = localStorage.getItem('tpp_sound') !== 'off'; } catch (e) { S.sound = true; }
  S.hist = [];
  function beep(win) {
    if (!S.sound) return;
    try {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return;
      const ac = beep._ac || (beep._ac = new AC());
      const o = ac.createOscillator(), g = ac.createGain();
      o.connect(g); g.connect(ac.destination);
      o.frequency.value = win ? 880 : 440; g.gain.value = 0.06;
      o.start(); o.stop(ac.currentTime + 0.12);
    } catch (e) { /* audio optional */ }
  }
  function toggleSound() {
    S.sound = !S.sound;
    try { localStorage.setItem('tpp_sound', S.sound ? 'on' : 'off'); } catch (e) {}
  }

  function showErr(t) { errBox.style.display = t ? 'block' : 'none'; errBox.textContent = t || ''; }
  async function api(path, opts) {
    opts = opts || {};
    opts.headers = Object.assign({ 'Authorization': 'Bearer ' + SESSION }, opts.headers || {});
    const r = await fetch(API + path, opts);
    const j = await r.json();
    if (!j.success) throw new Error(j.code + ': ' + j.message);
    return j.data;
  }
  function uuid() {
    return ([1e7] + -1e3 + -4e3 + -8e3 + -1e11).replace(/[018]/g,
      c => (c ^ crypto.getRandomValues(new Uint8Array(1))[0] & 15 >> c / 4).toString(16));
  }

  // ---- layout (normalized 0..1, portrait-first, adapts to landscape) ----
  function layout() {
    const land = W > H;
    const cx = W / 2, top = H * (land ? 0.30 : 0.24);
    const rx = Math.min(W * 0.36, (land ? 260 : 300));
    const seats = {};
    const angles = land ? [-0.45, 0.5, Math.PI - 0.05] : [-2.25, -0.85, -1.57 + Math.PI * 0 + 1.57];
    // portrait: A left, B right, C top-center
    const pp = land
      ? [{ x: cx - rx, y: top }, { x: cx + rx, y: top }, { x: cx, y: top - H * 0.16 }]
      : [{ x: cx - rx, y: top + H * 0.10 }, { x: cx + rx, y: top + H * 0.10 }, { x: cx, y: top - H * 0.13 }];
    POS.forEach((p, i) => { seats[p] = { x: pp[i].x, y: pp[i].y }; });
    const cw = Math.min(W * 0.13, 64), ch = cw * 1.42;
    return { cx, top, seats, cw, ch, land };
  }

  function rr(x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }
  function card(x, y, w, h, face) {
    ctx.save();
    ctx.shadowColor = 'rgba(0,0,0,.4)'; ctx.shadowBlur = 8; ctx.shadowOffsetY = 3;
    rr(x, y, w, h, 7);
    ctx.fillStyle = '#f7f4ec'; ctx.fill();
    ctx.shadowColor = 'transparent';
    ctx.lineWidth = 2; ctx.strokeStyle = '#8b0000'; ctx.stroke();
    ctx.fillStyle = '#222'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    if (face === '**' || !face) {
      ctx.fillStyle = '#0b5fa5';
      rr(x + 6, y + 6, w - 12, h - 12, 5); ctx.fill();
      ctx.fillStyle = '#ffd54a'; ctx.font = `bold ${w * 0.32}px system-ui`;
      ctx.fillText('TP', x + w / 2, y + h / 2);
    } else {
      ctx.font = `bold ${w * 0.30}px system-ui`;
      const red = /[HD]$/.test(face);
      ctx.fillStyle = red ? '#b71c1c' : '#212121';
      ctx.fillText(face, x + w / 2, y + h / 2);
    }
    ctx.restore();
  }

  function draw(now) {
    ctx.clearRect(0, 0, W, H);
    // felt
    const g = ctx.createRadialGradient(W / 2, H * 0.42, 60, W / 2, H * 0.42, Math.max(W, H) * 0.75);
    g.addColorStop(0, THEME.feltA); g.addColorStop(1, THEME.feltB);
    ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
    const L = layout(), s = S.snap;
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';

    // header: room + connection + server-driven countdown
    ctx.fillStyle = '#fff'; ctx.font = '600 15px system-ui';
    ctx.fillText('TEEN PATTI PRO · ' + ROOM, W / 2, 22);
    ctx.fillStyle = S.connected ? '#7CFC98' : '#ff7b7b'; ctx.font = '13px system-ui';
    ctx.fillText(S.connected ? '● LIVE' : '○ OFFLINE', W / 2, 42);
    // round number (server-authoritative)
    ctx.fillStyle = '#ffe9a8'; ctx.font = '12px system-ui';
    const rnd = s ? (s.round_no ? ('ROUND ' + s.round_no) : (s.round_id || '')) : '—';
    ctx.fillText(rnd, W / 2, 58);
    // top-left controls: Back | Help | Sound | Menu ; top-right: History
    S._ctl = [];
    const ctl = [['‹', 'back'], ['?', 'help'], [S.sound ? '♪' : '✕', 'sound'], ['≡', 'menu']];
    ctl.forEach((c, i) => {
      const x = 26 + i * 44, y = 26;
      ctx.save();
      ctx.fillStyle = 'rgba(0,0,0,.5)'; rr(x - 17, y - 15, 34, 30, 8); ctx.fill();
      ctx.fillStyle = '#fff'; ctx.font = 'bold 15px system-ui';
      ctx.fillText(c[0], x, y + 1); ctx.restore();
      S._ctl.push({ act: c[1], x, y, r: 22 });
    });
    const hx = W - 30;
    ctx.save();
    ctx.fillStyle = 'rgba(0,0,0,.5)'; rr(hx - 24, 11, 48, 30, 8); ctx.fill();
    ctx.fillStyle = '#fff'; ctx.font = 'bold 12px system-ui'; ctx.fillText('HIST', hx, 27);
    ctx.restore();
    S._ctl.push({ act: 'history', x: hx, y: 26, r: 26 });
    let secs = null;
    if (s && s.status === 'BETTING_OPEN' && s.betting_end_at) {
      // serverTime-anchored: estimate server now from last snapshot skew
      const skew = (S.srvNow || Date.now()) - (S.locNow || Date.now());
      secs = Math.max(0, (s.betting_end_at - (Date.now() + skew)) / 1000);
    }
    // timer ring
    ctx.save();
    ctx.lineWidth = 7; ctx.strokeStyle = 'rgba(255,255,255,.25)';
    ctx.beginPath(); ctx.arc(L.cx, L.top + (L.land ? 96 : 150), 34, 0, 7); ctx.stroke();
    if (secs !== null) {
      const frac = Math.min(1, secs / 20);
      ctx.strokeStyle = secs < 5 ? '#ff5252' : '#ffd54a';
      ctx.beginPath(); ctx.arc(L.cx, L.top + (L.land ? 96 : 150), 34, -Math.PI / 2, -Math.PI / 2 + frac * Math.PI * 2); ctx.stroke();
      ctx.fillStyle = '#fff'; ctx.font = 'bold 20px system-ui';
      ctx.fillText(secs.toFixed(0), L.cx, L.top + (L.land ? 96 : 150));
      ctx.font = '11px system-ui'; ctx.fillStyle = '#ffe9a8';
      ctx.fillText(s.status === 'BETTING_OPEN' ? 'GUESSING' : s.status, L.cx, L.top + (L.land ? 128 : 182));
    } else if (s) {
      ctx.fillStyle = '#fff'; ctx.font = 'bold 13px system-ui';
      ctx.fillText(s.status || '—', L.cx, L.top + (L.land ? 96 : 150));
    }
    ctx.restore();

    // pot
    ctx.fillStyle = '#ffe9a8'; ctx.font = '600 15px system-ui';
    const pot = s ? s.pot_total : 0, mine = s ? s.my_bet : 0;
    ctx.fillText('POT ' + pot + '   ·   YOU ' + mine, W / 2, H * 0.115);

    // seats
    POS.forEach(p => {
      const pt = L.seats[p], sel = S.selPos === p;
      const win = s && s.winners && s.winners.indexOf(p) >= 0;
      if (win) {
        ctx.save(); ctx.shadowColor = '#ffd54a'; ctx.shadowBlur = 26;
        ctx.strokeStyle = '#ffd54a'; ctx.lineWidth = 4;
        ctx.beginPath(); ctx.arc(pt.x, pt.y, 66, 0, 7); ctx.stroke(); ctx.restore();
      }
      // avatar
      ctx.save();
      ctx.fillStyle = sel ? '#ffd54a' : '#123f31';
      ctx.beginPath(); ctx.arc(pt.x, pt.y - 62, 22, 0, 7); ctx.fill();
      ctx.lineWidth = sel ? 3 : 2; ctx.strokeStyle = sel ? '#7a5c00' : '#ffd54a'; ctx.stroke();
      ctx.fillStyle = '#fff'; ctx.font = 'bold 16px system-ui'; ctx.fillText(p, pt.x, pt.y - 62);
      ctx.restore();
      // cards
      const hands = (s && s.hands && s.hands[p]) || ['**', '**', '**'];
      hands.forEach((f, i) => card(pt.x - L.cw * 1.15 + i * (L.cw + 5), pt.y - L.ch / 2, L.cw, L.ch, f));
      // pot per position
      ctx.fillStyle = '#c8e6c9'; ctx.font = '13px system-ui';
      const pv = (s && s.pots && s.pots[p]) || 0;
      ctx.fillText(p + ' · ' + pv, pt.x, pt.y + L.ch / 2 + 16);
    });

    // winners banner
    if (s && s.winners && s.winners.length && (s.status === 'RESULT' || s.status === 'SETTLED' || s.status === 'CLOSED')) {
      ctx.fillStyle = 'rgba(0,0,0,.55)'; rr(W / 2 - 150, H * 0.47, 300, 44, 12); ctx.fill();
      ctx.fillStyle = '#ffd54a'; ctx.font = 'bold 18px system-ui';
      ctx.fillText('WINNER: ' + s.winners.join(' & '), W / 2, H * 0.47 + 23);
    }

    // bottom: balance + chips + actions
    const by = H - Math.max(150, H * 0.20);
    ctx.fillStyle = 'rgba(0,0,0,.45)'; ctx.fillRect(0, by - 14, W, H - by + 14);
    ctx.fillStyle = '#fff'; ctx.font = '600 15px system-ui';
    ctx.fillText('BAL ' + (S.balance !== undefined ? S.balance : '—'), W / 2, by + 4);
    S._chips = [];
    const cw2 = Math.min(64, W / (DENOMS.length + 0.6));
    DENOMS.forEach((d, i) => {
      const x = W / 2 - (DENOMS.length - 1) * cw2 / 2 + i * cw2, y = by + 44;
      const sel = S.selDenom === d;
      ctx.save();
      ctx.fillStyle = sel ? '#ffd54a' : '#155e43';
      ctx.beginPath(); ctx.arc(x, y, 24, 0, 7); ctx.fill();
      ctx.lineWidth = sel ? 4 : 2; ctx.strokeStyle = sel ? '#7a5c00' : '#c8e6c9'; ctx.stroke();
      ctx.fillStyle = sel ? '#3a2f00' : '#fff'; ctx.font = 'bold 12px system-ui';
      ctx.fillText(d >= 1000 ? (d / 1000) + 'K' : '' + d, x, y);
      ctx.restore();
      S._chips.push({ d, x, y, r: 26 });
    });
    // repeat + status msg
    S._repeat = { x: W - 52, y: by + 44, r: 26 };
    ctx.save(); ctx.fillStyle = '#155e43'; ctx.beginPath(); ctx.arc(S._repeat.x, S._repeat.y, 24, 0, 7); ctx.fill();
    ctx.strokeStyle = '#c8e6c9'; ctx.lineWidth = 2; ctx.stroke();
    ctx.fillStyle = '#fff'; ctx.font = 'bold 12px system-ui'; ctx.fillText('RPT', S._repeat.x, S._repeat.y); ctx.restore();
    if (S.msg) { ctx.fillStyle = '#ffd54a'; ctx.font = '13px system-ui'; ctx.fillText(S.msg, W / 2, by + 84); }
    ctx.fillStyle = 'rgba(255,255,255,.55)'; ctx.font = '11px system-ui';
    ctx.fillText('tap a seat, then tap again to bet · server-authoritative', W / 2, H - 12);
    if (S.panel) drawPanel();
    requestAnimationFrame(draw);
  }

  function drawPanel() {
    ctx.save();
    ctx.fillStyle = 'rgba(0,0,0,.72)'; ctx.fillRect(0, 0, W, H);
    const pw = Math.min(W - 40, 420), ph = Math.min(H - 120, 380);
    const px = W / 2 - pw / 2, py = H / 2 - ph / 2;
    ctx.fillStyle = '#123f31'; rr(px, py, pw, ph, 14); ctx.fill();
    ctx.lineWidth = 2; ctx.strokeStyle = '#ffd54a'; ctx.stroke();
    ctx.fillStyle = '#ffd54a'; ctx.font = 'bold 16px system-ui';
    const title = S.panel === 'help' ? 'HELP' : S.panel === 'menu' ? 'MENU' : 'HISTORY / RESULT';
    ctx.fillText(title, W / 2, py + 28);
    ctx.fillStyle = '#fff'; ctx.font = '13px system-ui';
    let lines = [];
    if (S.panel === 'help') lines = [
      'Tap a seat (A/B/C), tap again to bet.',
      'Chips: 20 / 100 / 500 / 1K.',
      'RPT repeats your last bets.',
      'Timer is server time. Results are',
      'server-dealt and auditable.',
      'Tap ✕ panel or anywhere outside to close.'];
    else if (S.panel === 'menu') lines = [
      'Sound: ' + (S.sound ? 'ON (tap ♪ to mute)' : 'OFF (tap ✕ to unmute)'),
      'Session: ' + (SESSION ? SESSION.slice(0, 18) + '…' : 'none (demo mode)'),
      'Room: ' + ROOM,
      'Tap RECONNECT below to resync state.'];
    else lines = S.hist.length ? S.hist.slice(-10).map(
      h => (h.round_id || '').slice(-6) + ' · ' + h.position + ' · ' + h.amount + ' · ' + h.status)
      : ['No bets yet this round.'];
    lines.forEach((t, i) => ctx.fillText(t, W / 2, py + 58 + i * 22));
    // close + (menu) reconnect hints
    ctx.fillStyle = '#ffd54a'; ctx.font = 'bold 13px system-ui';
    ctx.fillText(S.panel === 'menu' ? 'tap RECONNECT in console · tap outside to close' : 'tap outside to close', W / 2, py + ph - 16);
    ctx.restore();
    S._panelBox = { x: px, y: py, w: pw, h: ph };
  }
  async function openHistory() {
    S.panel = 'history';
    try {
      const h = await api('/api/v1/games/teen-patti-pro/history?room=' + encodeURIComponent(ROOM));
      S.hist = h.bets || [];
    } catch (e) { S.hist = []; }
  }

  cv.addEventListener('pointerdown', async e => {
    const r = cv.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top;
    if (S.panel) {  // tap outside panel closes it
      const b = S._panelBox;
      if (!b || x < b.x || x > b.x + b.w || y < b.y || y > b.y + b.h) S.panel = null;
      return;
    }
    for (const c of (S._ctl || [])) {
      if ((x - c.x) ** 2 + (y - c.y) ** 2 < c.r * c.r) {
        if (c.act === 'back') { try { history.back(); } catch (e2) { S.msg = 'Back: no history'; } return; }
        if (c.act === 'sound') { toggleSound(); return; }
        if (c.act === 'help') { S.panel = 'help'; return; }
        if (c.act === 'menu') { S.panel = 'menu'; return; }
        if (c.act === 'history') { openHistory(); return; }
      }
    }
    for (const c of (S._chips || [])) {
      if ((x - c.x) ** 2 + (y - c.y) ** 2 < c.r * c.r) { S.selDenom = c.d; S.msg = ''; return; }
    }
    const rp = S._repeat;
    if (rp && (x - rp.x) ** 2 + (y - rp.y) ** 2 < rp.r * rp.r) { doRepeat(); return; }
    const L = layout();
    for (const p of POS) {
      const pt = L.seats[p];
      if (Math.hypot(x - pt.x, y - pt.y) < 110) {
        if (S.selPos === p) { placeBet(p); S.selPos = null; }
        else { S.selPos = p; S.msg = 'Seat ' + p + ' selected — tap again to bet ' + S.selDenom; }
        return;
      }
    }
  });

  const BETS = '/api/v1/games/teen-patti-pro/rooms/' + encodeURIComponent(ROOM) + '/bets';
  async function placeBet(pos) {
    S.msg = 'Placing ' + S.selDenom + ' on ' + pos + '…';
    try {
      const d = await api(BETS, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Idempotency-Key': uuid() },
        body: JSON.stringify({ position: pos, amount: S.selDenom })
      });
      S.msg = 'Accepted ' + d.bet_id;
    } catch (e) { S.msg = String(e.message || e); showErr(''); }
    refresh();
  }
  async function doRepeat() {
    S.msg = 'Repeat: replays last round bets (server-side)…';
    try {
      const h = await api('/api/v1/games/teen-patti-pro/history?room=' + encodeURIComponent(ROOM));
      const bets = (h.bets || []).slice(-3);
      for (const b of bets) {
        await api(BETS, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Idempotency-Key': uuid() },
          body: JSON.stringify({ position: b.position, amount: b.amount })
        });
      }
      S.msg = bets.length ? 'Repeated ' + bets.length + ' bet(s)' : 'Nothing to repeat';
    } catch (e) { S.msg = String(e.message || e); }
    refresh();
  }
  async function refresh() {
    try {
      S.snap = await api('/api/v1/games/teen-patti-pro/rounds/current?room=' + encodeURIComponent(ROOM));
      const key = (S.snap && S.snap.round_id) + ':' + ((S.snap && S.snap.winners || []).join(','));
      if (S._lastWinKey && key !== S._lastWinKey && S.snap.winners && S.snap.winners.length) beep(true);
      S._lastWinKey = key;
      S.srvNow = Date.now(); S.locNow = Date.now();
      try {
        const w = await api('/api/v1/games/teen-patti-pro/rooms/' + encodeURIComponent(ROOM) + '/wallet');
        S.balance = w.available;
      } catch (e) { /* wallet optional in snapshot loop */ }
      showErr('');
    } catch (e) { showErr('API: ' + e.message); }
  }
  function connect() {
    if (!SESSION) {
      showErr('No session — open via ?session=<id>&room=<room> (launch token redeem first), or watch the offline demo: demo.html');
      S.msg = 'No live session — see demo.html for an offline engine replay';
      return;
    }
    let ws;
    try { ws = new WebSocket(WS); } catch (e) { showErr('WS: ' + e.message); return; }
    ws.onopen = () => {
      S.connected = true;
      ws.send(JSON.stringify({ action: 'subscribe', room: ROOM, session: SESSION }));
      refresh();
    };
    ws.onmessage = ev => {
      try {
        const m = JSON.parse(ev.data);
        if (m.kind === 'snapshot' && m.data) { S.snap = m.data; S.srvNow = Date.now(); S.locNow = Date.now(); }
        else if (m.kind === 'event' && m.data) {
          if (m.data.seq > S.lastSeq) S.lastSeq = m.data.seq;
          refresh();
        } else if (m.kind === 'pong') { /* keepalive */ }
        else if (m.kind === 'error') { showErr('WS: ' + (m.data && m.data.message)); }
      } catch (e) { /* ignore malformed */ }
    };
    ws.onclose = () => { S.connected = false; setTimeout(connect, 3000); };
    ws.onerror = () => { try { ws.close(); } catch (e) { /* noop */ } };
    setInterval(() => { try { ws.readyState === 1 && ws.send(JSON.stringify({ action: 'ping' })); } catch (e) { /* noop */ } }, 25000);
  }
  refresh(); connect(); requestAnimationFrame(draw);
})();
