/* Teen Patti Pro WebView client — Canvas 2D (GPU-composited in WebView).
 * Server-authoritative: renders ONLY server snapshot + WS events.
 * Never computes results/balances locally. Timer from serverTime only.
 * Launch: ?api=http://HOST:5002&ws=ws://HOST:5003&session=<id>&room=<room>
 */
(function () {
  'use strict';
  const q = new URLSearchParams(location.search);
  const API = (q.get('api') || window.location.origin).replace(/\/$/, '');
  const WS = (q.get('ws') || '').replace(/\/$/, '');
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
  const errText = document.getElementById('errtext');
  const liveEl = document.getElementById('live');
  let W = 0, H = 0, DPR = 1, SAFE = { t: 0, b: 0, l: 0, r: 0 };
  const REDUCED = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  function readSafeAreas() {
    try {
      const cs = getComputedStyle(document.documentElement);
      const n = k => parseFloat(cs.getPropertyValue(k)) || 0;
      SAFE = { t: n('--sat'), b: n('--sab'), l: n('--sal'), r: n('--sar') };
    } catch (e) { SAFE = { t: 0, b: 0, l: 0, r: 0 }; }
  }
  function resize() {
    DPR = Math.min(3, window.devicePixelRatio || 1);
    W = window.innerWidth; H = window.innerHeight;
    cv.width = W * DPR; cv.height = H * DPR;
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    readSafeAreas();
  }
  window.addEventListener('resize', resize);
  window.addEventListener('orientationchange', () => setTimeout(resize, 120));
  resize();

  // Type scale: one multiplier for the whole UI, floored so no label drops
  // below 12px on a small phone. Callers ask for a base size, not a raw px.
  function U() {
    const s = Math.max(0.9, Math.min(1.35, Math.min(W, H) / 420));
    return { s, f: n => Math.max(12, Math.round(n * s)) + 'px' };
  }
  function announce(text) {
    if (!liveEl) return;
    liveEl.textContent = '';
    setTimeout(() => { liveEl.textContent = text; }, 30);
  }

  const S = { snap: null, selDenom: 100, selPos: null, lastSeq: 0, connected: false,
              msg: '', msgKind: 'info' };
  const DENOMS = [20, 100, 500, 1000];
  const POS = ['A', 'B', 'C'];
  // Common HUD state (BRD common UI): panel overlay, sound/music toggle.
  S.panel = null; // null | 'help' | 'menu' | 'history'
  try { S.sound = localStorage.getItem('tpp_sound') !== 'off'; } catch (e) { S.sound = true; }
  S.hist = [];
  // Supplied master audio (assets/dearlive-master, served under
  // master/teen-patti-pro/wav/): played on REAL server state transitions
  // only — never on render. Oscillator fallback if a file is absent.
  const SFX_FILES = { bet: 'bet.wav', win: 'win.wav', coin: 'coin.wav', lose: 'lose.wav',
                      flip: 'card_flip.wav', click: 'click.wav' };
  const sfxCache = {};
  function sfx(name) {
    if (!S.sound) return;
    try {
      let a = sfxCache[name];
      if (!a) {
        a = new Audio('master/teen-patti-pro/wav/' + (SFX_FILES[name] || name));
        sfxCache[name] = a;
      }
      a.currentTime = 0;
      const p = a.play();
      if (p && p.catch) p.catch(() => beep(name === 'win'));
    } catch (e) { beep(name === 'win'); }
  }
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

  // Feedback has three channels: a canvas status line, a DOM toast, and a
  // screen-reader announcement. Errors persist until the next success so a
  // failed bet is never silently swallowed.
  let toastTimer = null;
  function showErr(t) { errBox.style.display = t ? 'block' : 'none'; }
  function setToast(text, kind) {
    if (!text) { showErr(''); return; }
    errText.textContent = text;
    errBox.dataset.kind = kind || 'info';
    showErr('1');
    if (toastTimer) clearTimeout(toastTimer);
    if (kind !== 'error') toastTimer = setTimeout(() => showErr(''), 3800);
    announce(text);
  }
  function clearToast() { if (toastTimer) clearTimeout(toastTimer); showErr(''); }
  function status(text, kind) {
    S.msg = text || '';
    S.msgKind = kind || 'info';
    if (text) setToast(text, kind);
  }
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
    const cx = W / 2, top = H * (land ? 0.30 : 0.24) + SAFE.t;
    const rx = Math.min(W * 0.36, (land ? 260 : 300));
    const seats = {};
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
    const L = layout(), s = S.snap, u = U();
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';

    // header: room + connection + server-driven countdown
    ctx.fillStyle = '#fff'; ctx.font = '600 ' + u.f(15);
    ctx.fillText('TEEN PATTI PRO · ' + ROOM, W / 2, 22 + SAFE.t * 0.4);
    ctx.fillStyle = S.connected ? '#7CFC98' : '#ff9b9b'; ctx.font = u.f(13);
    ctx.fillText(S.connected ? (S.polling ? '● POLLING' : '● LIVE') : '○ OFFLINE', W / 2, 42 + SAFE.t * 0.4);
    // round number (server-authoritative)
    ctx.fillStyle = '#ffe9a8'; ctx.font = u.f(12);
    const rnd = s ? (s.round_no ? ('ROUND ' + s.round_no) : (s.round_id || '')) : '—';
    ctx.fillText(rnd, W / 2, 58 + SAFE.t * 0.4);
    // top-left controls: Back | Help | Sound | Menu ; top-right: History
    S._ctl = [];
    const ctl = [['‹', 'back'], ['?', 'help'], [S.sound ? '♪' : '✕', 'sound'], ['≡', 'menu']];
    const step = Math.min(46, (W * 0.52) / ctl.length);
    ctl.forEach((c, i) => {
      const x = SAFE.l + 24 + i * step, y = 26 + SAFE.t * 0.4;
      ctx.save();
      ctx.fillStyle = 'rgba(0,0,0,.55)'; rr(x - 18, y - 16, 36, 32, 8); ctx.fill();
      ctx.fillStyle = '#fff'; ctx.font = 'bold ' + u.f(15);
      ctx.fillText(c[0], x, y + 1); ctx.restore();
      S._ctl.push({ act: c[1], x, y, r: 24 });
    });
    const hx = W - SAFE.r - 32;
    ctx.save();
    ctx.fillStyle = 'rgba(0,0,0,.55)'; rr(hx - 26, 11 + SAFE.t * 0.4, 52, 32, 8); ctx.fill();
    ctx.fillStyle = '#fff'; ctx.font = 'bold ' + u.f(12); ctx.fillText('HIST', hx, 27 + SAFE.t * 0.4);
    ctx.restore();
    S._ctl.push({ act: 'history', x: hx, y: 26 + SAFE.t * 0.4, r: 28 });
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
      ctx.strokeStyle = secs < 5 ? '#ff8a8a' : '#ffd54a';
      ctx.beginPath(); ctx.arc(L.cx, L.top + (L.land ? 96 : 150), 34, -Math.PI / 2, -Math.PI / 2 + frac * Math.PI * 2); ctx.stroke();
      ctx.fillStyle = '#fff'; ctx.font = 'bold ' + u.f(20);
      ctx.fillText(secs.toFixed(0), L.cx, L.top + (L.land ? 96 : 150));
      ctx.font = u.f(12); ctx.fillStyle = '#ffe9a8';
      ctx.fillText(secs < 5 ? 'CLOSING SOON' : (s.status === 'BETTING_OPEN' ? 'GUESSING' : s.status), L.cx, L.top + (L.land ? 128 : 182));
    } else if (s) {
      ctx.fillStyle = '#fff'; ctx.font = 'bold ' + u.f(13);
      ctx.fillText(s.status || '—', L.cx, L.top + (L.land ? 96 : 150));
    }
    ctx.restore();

    // pot
    ctx.fillStyle = '#ffe9a8'; ctx.font = '600 ' + u.f(15);
    const pot = s ? s.pot_total : 0, mine = s ? s.my_bet : 0;
    ctx.fillText('POT ' + pot + '   ·   YOU ' + mine, W / 2, H * 0.115);

    // seats
    POS.forEach(p => {
      const pt = L.seats[p], sel = S.selPos === p;
      const win = s && s.winners && s.winners.indexOf(p) >= 0;
      if (win) {
        ctx.save();
        if (!REDUCED) { ctx.shadowColor = '#ffd54a'; ctx.shadowBlur = 26; }
        ctx.strokeStyle = '#ffd54a'; ctx.lineWidth = 4;
        ctx.beginPath(); ctx.arc(pt.x, pt.y, 66, 0, 7); ctx.stroke(); ctx.restore();
      }
      // avatar
      ctx.save();
      ctx.fillStyle = sel ? '#ffd54a' : '#123f31';
      ctx.beginPath(); ctx.arc(pt.x, pt.y - 62, 22, 0, 7); ctx.fill();
      ctx.lineWidth = sel ? 3 : 2; ctx.strokeStyle = sel ? '#7a5c00' : '#ffd54a'; ctx.stroke();
      ctx.fillStyle = '#fff'; ctx.font = 'bold ' + u.f(16); ctx.fillText(p, pt.x, pt.y - 62);
      ctx.restore();
      // cards
      const hands = (s && s.hands && s.hands[p]) || ['**', '**', '**'];
      hands.forEach((f, i) => card(pt.x - L.cw * 1.15 + i * (L.cw + 5), pt.y - L.ch / 2, L.cw, L.ch, f));
      // pot per position
      ctx.fillStyle = '#d7f5dd'; ctx.font = u.f(13);
      const pv = (s && s.pots && s.pots[p]) || 0;
      ctx.fillText(p + ' · ' + pv, pt.x, pt.y + L.ch / 2 + 16);
    });

    // winners banner
    if (s && s.winners && s.winners.length && (s.status === 'RESULT' || s.status === 'SETTLED' || s.status === 'CLOSED')) {
      ctx.fillStyle = 'rgba(0,0,0,.55)'; rr(W / 2 - 150, H * 0.47, 300, 44, 12); ctx.fill();
      ctx.fillStyle = '#ffd54a'; ctx.font = 'bold ' + u.f(18);
      ctx.fillText('WINNER: ' + s.winners.join(' & '), W / 2, H * 0.47 + 23);
    }

    // bottom: balance + chips + actions
    const by = H - Math.max(150, H * 0.20) - SAFE.b;
    ctx.fillStyle = 'rgba(0,0,0,.45)'; ctx.fillRect(0, by - 14, W, H - by + 14 + SAFE.b);
    ctx.fillStyle = '#fff'; ctx.font = '600 ' + u.f(15);
    ctx.fillText('BAL ' + (S.balance !== undefined ? S.balance : '—'), W / 2, by + 4);
    S._chips = [];
    const cw2 = Math.min(64, W / (DENOMS.length + 0.6));
    DENOMS.forEach((d, i) => {
      const x = W / 2 - (DENOMS.length - 1) * cw2 / 2 + i * cw2, y = by + 44;
      const sel = S.selDenom === d;
      // DearLive reference chip colors: 20 green, 100 blue, 500 purple, 1K red.
      const face = d === 20 ? '#22c55e' : d === 100 ? '#3b82f6' : d === 500 ? '#8b5cf6' : '#ef4444';
      ctx.save();
      ctx.fillStyle = sel ? '#ffd54a' : face;
      ctx.beginPath(); ctx.arc(x, y, 24, 0, 7); ctx.fill();
      ctx.lineWidth = sel ? 4 : 2; ctx.strokeStyle = sel ? '#7a5c00' : '#c8e6c9'; ctx.stroke();
      ctx.fillStyle = sel ? '#3a2f00' : '#fff'; ctx.font = 'bold ' + u.f(12);
      ctx.fillText(d >= 1000 ? (d / 1000) + 'K' : '' + d, x, y);
      ctx.restore();
      S._chips.push({ d, x, y, r: 28 });
    });
    // repeat + status msg
    S._repeat = { x: W - 52, y: by + 44, r: 28 };
    ctx.save(); ctx.fillStyle = '#155e43'; ctx.beginPath(); ctx.arc(S._repeat.x, S._repeat.y, 24, 0, 7); ctx.fill();
    ctx.strokeStyle = '#c8e6c9'; ctx.lineWidth = 2; ctx.stroke();
    ctx.fillStyle = '#fff'; ctx.font = 'bold ' + u.f(12); ctx.fillText('RPT', S._repeat.x, S._repeat.y); ctx.restore();
    if (S.msg) {
      ctx.fillStyle = S.msgKind === 'error' ? '#ffb4b4' : (S.msgKind === 'success' ? '#bbf7d0' : '#ffe9a8');
      ctx.font = u.f(13);
      ctx.fillText(S.msg, W / 2, by + 84);
    }
    ctx.fillStyle = 'rgba(255,255,255,.75)'; ctx.font = u.f(12);
    ctx.fillText('tap a seat, then tap again to bet · server-authoritative', W / 2, H - 12 - SAFE.b);

    // first-load and connection states, so the table is never a blank felt
    if (!S.snap && !S._everConnected) {
      ctx.fillStyle = 'rgba(0,0,0,.45)'; rr(W / 2 - 130, H * 0.44, 260, 48, 12); ctx.fill();
      ctx.fillStyle = '#fff'; ctx.font = '600 ' + u.f(15);
      ctx.fillText('Connecting to table…', W / 2, H * 0.44 + 24);
    } else if (S._everConnected && !S.connected) {
      ctx.fillStyle = 'rgba(120,20,20,.9)'; rr(W / 2 - 110, H * 0.44, 220, 40, 10); ctx.fill();
      ctx.fillStyle = '#fff'; ctx.font = '600 ' + u.f(14);
      ctx.fillText('Reconnecting…', W / 2, H * 0.44 + 20);
    }
    if (S.panel) drawPanel(u);
    requestAnimationFrame(draw);
  }

  function drawPanel(u) {
    ctx.save();
    ctx.fillStyle = 'rgba(0,0,0,.72)'; ctx.fillRect(0, 0, W, H);
    const pw = Math.min(W - 40, 420), ph = Math.min(H - 120, 380);
    const px = W / 2 - pw / 2, py = H / 2 - ph / 2;
    ctx.fillStyle = '#123f31'; rr(px, py, pw, ph, 14); ctx.fill();
    ctx.lineWidth = 2; ctx.strokeStyle = '#ffd54a'; ctx.stroke();
    ctx.fillStyle = '#ffd54a'; ctx.font = 'bold ' + u.f(16);
    const title = S.panel === 'help' ? 'HELP' : S.panel === 'menu' ? 'MENU' : 'HISTORY / RESULT';
    ctx.fillText(title, W / 2, py + 28);
    ctx.fillStyle = '#fff'; ctx.font = u.f(13);
    let lines = [];
    if (S.panel === 'help') lines = [
      'Tap a seat (A/B/C), tap again to bet.',
      'Chips: 20 / 100 / 500 / 1K.',
      'RPT repeats your last bets.',
      'Timer is server time. Results are',
      'server-dealt and auditable.',
      'Keys: 1-4 chip, A/B/C seat, Enter bet.',
      'Tap outside to close.'];
    else if (S.panel === 'menu') lines = [
      'Sound: ' + (S.sound ? 'ON (tap ♪ to mute)' : 'OFF (tap ✕ to unmute)'),
      'Session: ' + (SESSION ? SESSION.slice(0, 18) + '…' : 'none (demo mode)'),
      'Room: ' + ROOM,
      'Timer and results come from the server.',
      'Tap outside to close.'];
    else lines = S.hist.length ? S.hist.slice(-10).map(
      h => (h.round_id || '').slice(-6) + ' · ' + h.position + ' · ' + h.amount + ' · ' + h.status)
      : ['No bets yet this round.'];
    lines.forEach((t, i) => ctx.fillText(t, W / 2, py + 58 + i * 22));
    ctx.fillStyle = '#ffd54a'; ctx.font = 'bold ' + u.f(13);
    ctx.fillText('tap outside to close', W / 2, py + ph - 16);
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
    sfx('click');
    const r = cv.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top;
    if (S.panel) {  // tap outside panel closes it
      const b = S._panelBox;
      if (!b || x < b.x || x > b.x + b.w || y < b.y || y > b.y + b.h) S.panel = null;
      return;
    }
    for (const c of (S._ctl || [])) {
      if ((x - c.x) ** 2 + (y - c.y) ** 2 < c.r * c.r) {
        if (c.act === 'back') { try { history.back(); } catch (e2) { status('No previous page to go back to', 'info'); } return; }
        if (c.act === 'sound') { toggleSound(); return; }
        if (c.act === 'help') { S.panel = 'help'; return; }
        if (c.act === 'menu') { S.panel = 'menu'; return; }
        if (c.act === 'history') { openHistory(); return; }
      }
    }
    for (const c of (S._chips || [])) {
      if ((x - c.x) ** 2 + (y - c.y) ** 2 < c.r * c.r) {
        S.selDenom = c.d; status('Chip ' + c.d + ' selected', 'info'); return;
      }
    }
    const rp = S._repeat;
    if (rp && (x - rp.x) ** 2 + (y - rp.y) ** 2 < rp.r * rp.r) { doRepeat(); return; }
    const L = layout();
    for (const p of POS) {
      const pt = L.seats[p];
      if (Math.hypot(x - pt.x, y - pt.y) < 110) {
        if (S.selPos === p) { placeBet(p); S.selPos = null; }
        else { S.selPos = p; status('Seat ' + p + ' selected — tap again to bet ' + S.selDenom, 'info'); }
        return;
      }
    }
  });

  const BETS = '/api/v1/games/teen-patti-pro/rooms/' + encodeURIComponent(ROOM) + '/bets';
  // One in-flight bet at a time. Without this a fast double tap could submit
  // two independent bets (each call mints its own idempotency key), which is a
  // player-money bug, not just a visual one.
  let betBusy = false;
  function friendlyError(e) {
    const m = String((e && e.message) || e || 'error');
    if (/INSUFFICIENT_BALANCE/.test(m)) return 'Not enough balance for that chip.';
    if (/BETTING_CLOSED|BETTING_CLOSED_RACE/.test(m)) return 'Betting closed for this round.';
    if (/DUPLICATE_REQUEST/.test(m)) return 'That bet was already accepted.';
    if (/VALIDATION_ERROR/.test(m)) return 'That chip is not allowed on this table.';
    if (/UNAUTHENTICATED|Unknown session/i.test(m)) return 'Session expired — reopen the game.';
    if (/FAILED|Fetch|NetworkError/i.test(m)) return 'Network problem — retrying.';
    return m.replace(/^[A-Z_]+:\s*/, '');
  }
  async function placeBet(pos) {
    if (betBusy) { status('Please wait — bet still sending…', 'info'); return; }
    if (S.snap && S.snap.status !== 'BETTING_OPEN') {
      status('Betting is closed for this round.', 'error');
      return;
    }
    betBusy = true;
    status('Placing ' + S.selDenom + ' on ' + pos + '…', 'info');
    try {
      const d = await api(BETS, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Idempotency-Key': uuid() },
        body: JSON.stringify({ position: pos, amount: S.selDenom })
      });
      status('Bet accepted · ' + S.selDenom + ' on ' + pos, 'success');
      S._placedThisRound = true;
      sfx('bet');
    } catch (e) {
      status(friendlyError(e), 'error');
    } finally {
      setTimeout(() => { betBusy = false; }, 400);
      refresh();
    }
  }
  async function doRepeat() {
    status('Repeating your last bets…', 'info');
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
      status(bets.length ? ('Repeated ' + bets.length + ' bet(s)') : 'Nothing to repeat',
             bets.length ? 'success' : 'info');
    } catch (e) { status(friendlyError(e), 'error'); }
    refresh();
  }
  async function refresh() {
    try {
      const prev = S.snap;
      S.snap = await api('/api/v1/games/teen-patti-pro/rounds/current?room=' + encodeURIComponent(ROOM));
      const key = (S.snap && S.snap.round_id) + ':' + ((S.snap && S.snap.winners || []).join(','));
      if (S._roundId && S.snap && S.snap.round_id !== S._roundId) {
        // New server round -> deal moment: flip sound, reset participation.
        sfx('flip');
        S._placedThisRound = false;
        announce('New round ' + (S.snap.round_id || ''));
      }
      if (S.snap) S._roundId = S.snap.round_id;
      if (S._lastWinKey && key !== S._lastWinKey && S.snap.winners && S.snap.winners.length) {
        // Authoritative result published: win jingle only if we took part.
        if (S._placedThisRound) { sfx('win'); sfx('coin'); }
        else sfx('lose');
        status('Result · winner ' + S.snap.winners.join(' and '), 'success');
      } else if (prev && prev.status === 'BETTING_OPEN' && S.snap.status !== 'BETTING_OPEN') {
        status('Betting closed — waiting for the result.', 'info');
      }
      S._lastWinKey = key;
      S.srvNow = Date.now(); S.locNow = Date.now();
      try {
        const w = await api('/api/v1/games/teen-patti-pro/rooms/' + encodeURIComponent(ROOM) + '/wallet');
        if (w.available !== S.balance) announce('Balance ' + w.available);
        S.balance = w.available;
      } catch (e) { /* wallet optional in snapshot loop */ }
      if (S.msgKind === 'error') clearToast();
    } catch (e) {
      S._everConnected = true;
      status(friendlyError(e), 'error');
    }
  }
  // Keyboard parity for every canvas control (WCAG 2.1.1). Pointer users are
  // unaffected; this also makes the table scriptable in browser QA.
  window.addEventListener('keydown', e => {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const k = e.key;
    if (k === 'Escape') { S.panel = null; return; }
    if (S.panel) return;
    if (k >= '1' && k <= '4') {
      const d = DENOMS[Number(k) - 1];
      if (d) { S.selDenom = d; status('Chip ' + d + ' selected', 'info'); }
      e.preventDefault(); return;
    }
    const seat = k.toUpperCase();
    if (POS.indexOf(seat) >= 0) {
      if (S.selPos === seat) { placeBet(seat); S.selPos = null; }
      else { S.selPos = seat; status('Seat ' + seat + ' selected — press Enter to bet ' + S.selDenom, 'info'); }
      e.preventDefault(); return;
    }
    if (k === 'Enter') { if (S.selPos) { placeBet(S.selPos); S.selPos = null; } e.preventDefault(); return; }
    if (k === 'r' || k === 'R') { doRepeat(); e.preventDefault(); return; }
    if (k === 'h' || k === 'H') { openHistory(); e.preventDefault(); return; }
    if (k === '?') { S.panel = 'help'; e.preventDefault(); return; }
    if (k === 'm' || k === 'M') { S.panel = 'menu'; e.preventDefault(); return; }
    if (k === 's' || k === 'S') { toggleSound(); status('Sound ' + (S.sound ? 'on' : 'off'), 'info'); e.preventDefault(); }
  });
  function connect() {
    if (!SESSION) {
      status('No session — open via the launch URL from your operator, or view demo.html', 'error');
      return;
    }
    if (!WS) {
      // Staging/serverless transport: no WebSocket — authoritative polling.
      S.connected = true; S._everConnected = true;
      S.polling = true;
      refresh();
      setInterval(refresh, 2000);
      return;
    }
    let ws;
    try { ws = new WebSocket(WS); } catch (e) { showErr('WS: ' + e.message); return; }
    ws.onopen = () => {
      S.connected = true; S._everConnected = true;
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
