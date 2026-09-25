/* Operator Dashboard — Phase A Implementation
 * Operator-scoped admin dashboard for DearLive Games
 * Reuses design tokens from theme.json and follows lobby.html patterns
 */
(function () {
  'use strict';

  const API = '';
  const SESSION = ''; // Will be set from demo_token or session
  const ROOM = 'default';

  // Theme tokens from theme.json
  const THEME = {
    feltA: '#147a52', feltB: '#083a28',
    gold: '#ffd54a', goldDeep: '#f59e0b',
    seatA: '#ef4444', seatB: '#3b82f6', seatC: '#22c55e',
    text: '#ffffff', potText: '#ffe9a8',
  };

  const cv = document.getElementById('c');
  const ctx = cv ? cv.getContext('2d') : null;
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

  // Type scale
  function U() {
    const s = Math.max(0.9, Math.min(1.35, Math.min(W, H) / 420));
    return { s, f: n => Math.max(12, Math.round(n * s)) + 'px' };
  }

  function announce(text) {
    if (!liveEl) return;
    liveEl.textContent = '';
    setTimeout(() => { liveEl.textContent = text; }, 30);
  }

  // State
  const S = {
    snap: null,
    selDenom: 100,
    selPos: null,
    lastSeq: 0,
    connected: false,
    msg: '',
    msgKind: 'info',
    panel: null,
    sound: true,
    hist: [],
    kpis: {},
    charts: {},
    tables: [],
    activity: [],
    demoMode: false,
    demoExpiry: 0,
    timerInterval: null
  };

  const DENOMS = [20, 100, 500, 1000];
  const POS = ['A', 'B', 'C', 'D', 'E', 'F'];

  // Sound
  const SFX_FILES = {
    bet: 'bet.wav', win: 'win.wav', coin: 'coin.wav', lose: 'lose.wav',
    flip: 'card_flip.wav', click: 'click.wav'
  };
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

  // Toast/Error handling
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
  function announce(text) {
    if (!liveEl) return;
    liveEl.textContent = '';
    setTimeout(() => { liveEl.textContent = text; }, 30);
  }

  // API helper
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

  // Layout
  function layout() {
    const land = W > H;
    const cx = W / 2, top = H * (land ? 0.30 : 0.24) + SAFE.t;
    const rx = Math.min(W * 0.36, (land ? 260 : 300));
    const seats = {};
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

  // Demo mode
  const DEMO_TTL_MS = 30 * 60 * 1000;
  const SESSION_KEY = 'tpp_operator_demo_session';

  const S = {
    snap: null, selDenom: 100, selPos: null, lastSeq: 0, connected: false,
    msg: '', msgKind: 'info', panel: null, sound: true, hist: [],
    kpis: {}, charts: {}, tables: [], activity: [],
    demoMode: false, demoExpiry: 0, timerInterval: null
  };

  const DEMO_TTL_MS = 30 * 60 * 1000;
  const SESSION_KEY = 'tpp_operator_demo_session';

  const els = {
    cv: document.getElementById('c'), ctx: cv.getContext('2d'),
    errBox: document.getElementById('err'), errText: document.getElementById('errtext'),
    liveEl: document.getElementById('live')
  };

  // Demo mode functions
  function initDemoMode() {
    const params = new URLSearchParams(location.search);
    state.demoMode = params.get('mode') === 'demo';
    const demoBadge = document.getElementById('demoBadge');
    const timerBadge = document.getElementById('timerBadge');
    const timerText = document.getElementById('timerText');

    if (state.demoMode) {
      document.getElementById('demoBadge').classList.add('active');
      document.getElementById('timerBadge').classList.add('active');
      const stored = localStorage.getItem('tpp_operator_demo_session');
      if (stored) {
        try {
          const s = JSON.parse(stored);
          if (s.expiry > Date.now()) {
            state.demoExpiry = s.expiry;
            startTimer();
          } else {
            localStorage.removeItem('tpp_operator_demo_session');
            endDemo();
          }
        } else {
          state.demoExpiry = Date.now() + DEMO_TTL_MS;
          localStorage.setItem('tpp_operator_demo_session', JSON.stringify({ expiry: state.demoExpiry }));
          startTimer();
        }
      } else {
        document.getElementById('demoBadge').classList.remove('active');
        document.getElementById('timerBadge').classList.remove('active', 'warning', 'danger');
        clearInterval(state.timerInterval);
      }
    }

    function startTimer() {
      clearInterval(S.timerInterval);
      document.getElementById('timerBadge').classList.add('active');
      updateTimer();
      S.timerInterval = setInterval(updateTimer, 1000);
    }

    function updateTimer() {
      const remaining = Math.max(0, state.demoExpiry - Date.now());
      const m = Math.floor(remaining / 60000).toString().padStart(2, '0');
      const s = Math.floor((remaining % 60000) / 1000).toString().padStart(2, '0');
      document.getElementById('timerText').textContent = m + ':' + s;
      const badge = document.getElementById('timerBadge');
      badge.classList.remove('warning', 'danger');
      if (remaining <= 5 * 60 * 1000) badge.classList.add('danger');
      else if (remaining <= 10 * 60 * 1000) badge.classList.add('warning');
      if (remaining <= 0) endDemo();
    }

    function endDemo() {
      clearInterval(S.timerInterval);
      document.getElementById('timerBadge').classList.remove('active', 'warning', 'danger');
      document.getElementById('timerText').textContent = '00:00';
      status('Demo session ended. Play with real coins to continue.', 'info');
      renderGames();
    }

    function startTimer() {
      clearInterval(S.timerInterval);
      document.getElementById('timerBadge').classList.add('active');
      updateTimer();
      state.timerInterval = setInterval(updateTimer, 1000);
    }

    window.initDemoMode = initDemoMode;
    window.startTimer = startTimer;
    window.updateTimer = updateTimer;
    window.endDemo = endDemo;
  })();