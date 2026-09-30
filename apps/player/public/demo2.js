/* ============================================================
   TEEN PATTI PRO — DEMO PAGE
   Server-authoritative. No fake animation. No client authority.
   ============================================================ */
(() => {
'use strict';

/* ───────── CONFIG ───────── */
const API_BASE   = location.origin;
const WS_URL     = (location.protocol === 'https:' ? 'wss://' : 'ws://')
                 + location.host + '/ws';
const ASSET_BASE = '/assets/teen-patti-pro';

const CFG = {
  cardW: 0, cardH: 0, chairW: 0, panelH: 140,
  phase: {
    ABOUT_TO_START: 1000,
    INITIAL_DEAL:  1500,
    HOLD_RESULT:   5000,
    BETTING_CLOSED: 500,
  },
  cardDealMs:     300,
  cardFlipMs:     300,
  chipFlyMs:      600,
  balanceTweenMs: 200,
  potTweenMs:     200,
};

/* ───────── ASSETS ───────── */
const ASSETS = {};
function loadAssets() {
  const suits = ['spade','heart','diamond','club'];
  const ranks = ['a','2','3','4','5','6','7','8','9','10','j','q','k'];
  ASSETS.cardBack = loadImg(`${ASSET_BASE}/cards/card-back-teenpatti.svg`);
  for (const r of ranks) for (const s of suits) {
    ASSETS[`card-${r}-${s}`] = loadImg(`${ASSET_BASE}/cards/card-${r}-${s}.svg`);
  }
  ASSETS.chairA = loadImg(`${ASSET_BASE}/seats/seat-red.svg`);
  ASSETS.chairB = loadImg(`${ASSET_BASE}/seats/seat-blue.svg`);
  ASSETS.chairC = loadImg(`${ASSET_BASE}/seats/seat-green.svg`);
  ASSETS.coin   = loadImg(`${ASSET_BASE}/ui/coin.svg`);
  ASSETS.winner = loadImg(`${ASSET_BASE}/ui/winner-banner.svg`);
  for (const d of ['20','100','500','1000']) {
    ASSETS[`chip-${d}`] = loadImg(`${ASSET_BASE}/chips/chip-${d}.svg`);
  }
}
function loadImg(src) {
  const i = new Image();
  i.decoding = 'async';
  i.src = src;
  return i;
}
function imgReady(img) {
  return img && img.complete && img.naturalWidth > 0;
}

/* ───────── CARD RANK / SUIT MAPPER ───────── */
const RANK_LABELS = {
  a:'A', 1:'A', 2:'2', 3:'3', 4:'4', 5:'5', 6:'6', 7:'7',
  8:'8', 9:'9', 10:'10', 11:'J', 12:'Q', 13:'K', j:'J', q:'Q', k:'K'
};
const SUIT_SYMBOL = { s:'♠', h:'♥', d:'♦', c:'♣',
                     spade:'♠', heart:'♥', diamond:'♦', club:'♣' };

function normalizeCard(raw) {
  if (!raw) return null;
  if (typeof raw === 'object') {
    return { rank: String(raw.rank ?? '').toLowerCase(),
             suit: String(raw.suit ?? '').toLowerCase(),
             revealed: !!raw.revealed };
  }
  const s = String(raw).trim().toLowerCase();
  const m = s.match(/^([a-z0-9]+)[-_]?([shdc]|spade|heart|diamond|club)$/);
  if (!m) return { rank: s, suit: '', revealed: true };
  let r = m[1];
  const suitMap = { s:'spade', h:'heart', d:'diamond', c:'club' };
  return { rank: r, suit: suitMap[m[2]] || m[2], revealed: true };
}
function rankLabel(rank) {
  if (!rank) return '?';
  const r = String(rank).toLowerCase();
  return RANK_LABELS[r] ?? r.toUpperCase();
}
function suitSymbol(suit) {
  if (!suit) return '';
  return SUIT_SYMBOL[String(suit).toLowerCase()] || '';
}

/* ───────── CLOCK SYNC (SERVER-AUTHORITATIVE) ───────── */
const Clock = {
  offsetMs: 0,   // serverTime - localTime
  synced: false,
  sync(serverTimeIso) {
    if (!serverTimeIso) return;
    const t = Date.parse(serverTimeIso);
    if (!Number.isFinite(t)) return;
    this.offsetMs = t - Date.now();
    this.synced = true;
  },
  now() { return Date.now() + this.offsetMs; }
};

/* ───────── STATE ───────── */
const State = {
  session: null,
  room: null,
  me: null,

  snap: null,               // latest server snapshot
  roundId: null,
  stateVersion: 0,
  status: null,

  phase: 'INIT',            // client-side phase machine
  phaseStartedAt: 0,

  hands: { A: [], B: [], C: [] },
  revealed: { A: 0, B: 0, C: 0 }, // count of revealed cards per seat
  seats: {
    A: { playerId: null, name: '', pot: 0, bet: 0, isMe: false },
    B: { playerId: null, name: '', pot: 0, bet: 0, isMe: false },
    C: { playerId: null, name: '', pot: 0, bet: 0, isMe: false },
  },
  potTotal: 0,
  myBet: 0,
  balance: 0,
  winnerSeat: null,
  payout: 0,

  // animation trackers (populated by phase transitions)
  anim: {
    cardDealStarted: { A: 0, B: 0, C: 0 },
    cardFlipStarted: { A: {}, B: {}, C: {} },
    chipFlights: [],       // {fromX, fromY, toX, toY, startMs, seat}
    payoutFloats: [],      // {seat, text, startMs, x, y}
    winnerGlowStart: 0,
    potTweens: { A:null, B:null, C:null, total:null },
    balanceTween: null,
  },

  selectedChip: null,
  betState: 'IDLE',        // IDLE | PENDING | CONFIRMED | REJECTED
  betMessage: '',
  isSpectator: false,
};

/* ───────── CANVAS ───────── */
const canvas = document.getElementById('demo-canvas');
const ctx = canvas.getContext('2d');
let DPR = 1, W = 0, H = 0;

function resizeCanvas() {
  DPR = Math.min(window.devicePixelRatio || 1, 3);
  W = window.innerWidth;
  H = window.innerHeight;
  canvas.width  = Math.floor(W * DPR);
  canvas.height = Math.floor(H * DPR);
  canvas.style.width  = W + 'px';
  canvas.style.height = H + 'px';
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);

  CFG.cardW  = Math.max(52, Math.round(W * 0.16));
  CFG.cardH  = Math.round(CFG.cardW * 1.4);
  CFG.chairW = Math.min(140, Math.round(W * 0.28));
}
window.addEventListener('resize', resizeCanvas);
window.addEventListener('orientationchange', () => setTimeout(resizeCanvas, 120));

/* ───────── WEBSOCKET ───────── */
let ws = null;
let wsReconnectAttempts = 0;
const WS_MAX_BACKOFF = 8000;

function connectWS() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
  setConn('reconnecting');
  try { ws = new WebSocket(WS_URL); } catch { scheduleReconnect(); return; }

  ws.onopen = () => {
    wsReconnectAttempts = 0;
    setConn('live');
    // Subscribe with session + room
    ws.send(JSON.stringify({
      type: 'subscribe',
      session: State.session,
      room: State.room,
    }));
  };
  ws.onmessage = (evt) => {
    let msg; try { msg = JSON.parse(evt.data); } catch { return; }
    handleWSMessage(msg);
  };
  ws.onclose = () => { setConn('offline'); scheduleReconnect(); };
  ws.onerror = () => {};
}
function scheduleReconnect() {
  wsReconnectAttempts++;
  const delay = Math.min(WS_MAX_BACKOFF, 500 * Math.pow(2, wsReconnectAttempts));
  setTimeout(connectWS, delay);
}
function setConn(state) {
  const el = document.getElementById('conn-status');
  el.textContent = state;
  el.className = 'conn ' + state;
}

/* ───────── WEBSOCKET MESSAGE HANDLING ───────── */
function handleWSMessage(msg) {
  // Server-authoritative: every message is a full or partial snapshot
  if (msg.type === 'snapshot' || msg.type === 'round.updated') {
    applySnapshot(msg.data || msg.snapshot || msg);
  } else if (msg.type === 'bet.accepted') {
    onBetAccepted(msg);
  } else if (msg.type === 'bet.rejected') {
    onBetRejected(msg);
  } else if (msg.type === 'round.created') {
    applySnapshot(msg.data || msg);
  } else if (msg.type === 'settlement.completed') {
    onSettlement(msg);
  }
}

/* ───────── SNAPSHOT APPLICATION ───────── */
function applySnapshot(snap) {
  if (!snap || !snap.round) return;

  // Sync server clock from every snapshot
  Clock.sync(snap.round.server_time || snap.serverTime);

  const newRoundId = snap.round.round_id || snap.round.roundId;
  const newStatus  = snap.round.status;
  const newVersion = snap.round.state_version ?? snap.round.stateVersion ?? 0;

  const roundChanged = newRoundId !== State.roundId;
  const versionChanged = newVersion !== State.stateVersion;

  // Reset on new round
  if (roundChanged) {
    onRoundReset(newRoundId);
  }

  State.snap = snap;
  State.roundId = newRoundId;
  State.stateVersion = newVersion;
  State.status = newStatus;

  // Update seats
  if (snap.seats) {
    for (const seat of ['A','B','C']) {
      const s = snap.seats[seat] || {};
      State.seats[seat].playerId = s.player_id ?? s.playerId ?? null;
      State.seats[seat].name     = s.name ?? s.player_name ?? '';
      State.seats[seat].pot      = Number(s.pot) || 0;
      State.seats[seat].bet      = Number(s.bet) || 0;
      State.seats[seat].isMe     = !!(s.is_me ?? s.isMe);
    }
  }

  // Update hands
  if (snap.hands) {
    for (const seat of ['A','B','C']) {
      const raw = snap.hands[seat] || [];
      State.hands[seat] = raw.map(normalizeCard).filter(Boolean);
    }
  }

  State.potTotal    = Number(snap.pot_total ?? snap.potTotal) || 0;
  State.myBet       = Number(snap.my_bet ?? snap.myBet) || 0;
  State.balance     = Number(snap.balance) || 0;
  State.winnerSeat  = snap.winner_seat ?? snap.winnerSeat ?? null;
  State.payout      = Number(snap.payout) || 0;

  // Spectator detection
  if (snap.my_seat === null || snap.mySeat === null) {
    State.isSpectator = true;
  } else if (snap.my_seat || snap.mySeat) {
    State.isSpectator = false;
  }
  document.getElementById('spectator-badge').classList.toggle('hidden', !State.isSpectator);

  // Round label
  document.getElementById('round-label').textContent =
    `Round ${snap.round.round_no ?? snap.round.roundNo ?? '—'} • Room ${State.room}`;

  // Phase transitions (only animate on actual change)
  if (roundChanged || versionChanged) {
    drivePhaseMachine();
  }
}

/* ───────── ROUND RESET ───────── */
function onRoundReset(newRoundId) {
  State.roundId = newRoundId;
  State.hands = { A: [], B: [], C: [] };
  State.revealed = { A: 0, B: 0, C: 0 };
  State.winnerSeat = null;
  State.payout = 0;
  State.anim.chipFlights = [];
  State.anim.payoutFloats = [];
  State.anim.winnerGlowStart = 0;
  State.anim.potTweens = { A:null, B:null, C:null, total:null };
  State.betState = 'IDLE';
  State.betMessage = '';
  setPhase('ROUND_RESET');
}

/* ───────── PHASE MACHINE ───────── */
function setPhase(phase) {
  State.phase = phase;
  State.phaseStartedAt = performance.now();
}

function drivePhaseMachine() {
  const status = (State.status || '').toUpperCase();
  const now = performance.now();
  const elapsed = now - State.phaseStartedAt;

  switch (State.phase) {
    case 'ROUND_RESET':
      // Reset visuals for 200ms, then move on
      if (elapsed > 200) {
        if (status === 'ABOUT_TO_START' || status === 'UPCOMING') {
          setPhase('ABOUT_TO_START');
          showBanner('About to Start');
        } else if (status === 'BETTING_OPEN') {
          startInitialDeal();
        } else {
          // Late join: jump to whatever phase matches current status
          jumpToCurrentPhase(status);
        }
      }
      break;

    case 'ABOUT_TO_START':
      if (elapsed > CFG.phase.ABOUT_TO_START || status === 'BETTING_OPEN') {
        hideBanner();
        startInitialDeal();
      }
      break;

    case 'INITIAL_DEAL':
      if (elapsed > CFG.phase.INITIAL_DEAL) {
        setPhase('GUESSING');
      }
      break;

    case 'GUESSING':
      if (status === 'BETTING_CLOSED') {
        hideBanner();
        showBanner('Betting Closed');
        setPhase('BETTING_CLOSED');
      }
      break;

    case 'BETTING_CLOSED':
      if (elapsed > CFG.phase.BETTING_CLOSED) {
        hideBanner();
        startReveal();
      }
      break;

    case 'REVEAL':
      // Reveal is driven by scheduled flips; when all 3 seats have 3 revealed, move on
      if (State.revealed.A >= 3 && State.revealed.B >= 3 && State.revealed.C >= 3) {
        if (elapsed > 800) setPhase('HAND_EVAL');
      }
      break;

    case 'HAND_EVAL':
      if (elapsed > 800) {
        if (State.winnerSeat) {
          setPhase('WINNER_DECLARED');
          State.anim.winnerGlowStart = performance.now();
          spawnPayoutFloat(State.winnerSeat, State.payout);
        } else {
          setPhase('SETTLED');
        }
      }
      break;

    case 'WINNER_DECLARED':
      if (status === 'SETTLED' || elapsed > 3000) {
        setPhase('SETTLED');
      }
      break;

    case 'SETTLED':
      if (elapsed > 500) {
        setPhase('HOLD');
      }
      break;

    case 'HOLD':
      if (elapsed > CFG.phase.HOLD_RESULT) {
        setPhase('ROUND_RESET');
        State.phaseStartedAt = performance.now();
      }
      break;
  }
}

function jumpToCurrentPhase(status) {
  // Reconnect: reconstruct without replaying past animations
  if (status === 'BETTING_OPEN') {
    State.revealed = { A:1, B:1, C:1 };
    setPhase('GUESSING');
  } else if (status === 'BETTING_CLOSED') {
    State.revealed = { A:1, B:1, C:1 };
    setPhase('BETTING_CLOSED');
  } else if (status === 'RESULT_PROCESSING') {
    State.revealed = { A:3, B:3, C:3 };
    setPhase('HAND_EVAL');
  } else if (status === 'RESULT_DECLARED' || status === 'SETTLED') {
    State.revealed = { A:3, B:3, C:3 };
    setPhase('WINNER_DECLARED');
    State.anim.winnerGlowStart = performance.now();
  } else if (status === 'CLOSED') {
    setPhase('HOLD');
  }
}

/* ───────── PHASE ACTIONS ───────── */
function startInitialDeal() {
  setPhase('INITIAL_DEAL');
  const base = performance.now();
  // Deal 1 face-up + 2 backs per seat (A → B → C)
  const seats = ['A', 'B', 'C'];
  seats.forEach((seat, seatIdx) => {
    for (let i = 0; i < 3; i++) {
      const delay = seatIdx * CFG.cardDealMs + i * 80;
      const key = `${seat}-${i}`;
      State.anim.cardDealStarted[key] = base + delay;
    }
  });
  // Card 0 is face-up (revealed = 1). Cards 1 and 2 stay face-down.
  State.revealed = { A: 1, B: 1, C: 1 };
  // After deal completes, transition
  setTimeout(() => {
    if (State.phase === 'INITIAL_DEAL') setPhase('GUESSING');
  }, CFG.phase.INITIAL_DEAL);
}

function startReveal() {
  setPhase('REVEAL');
  // Sequential flip: card 2 for A,B,C then card 3 for A,B,C
  const now = performance.now();
  const stagger = 250;
  const seats = ['A', 'B', 'C'];
  let t = 0;
  for (let cardIdx = 1; cardIdx < 3; cardIdx++) {
    for (const seat of seats) {
      const delay = t * stagger;
      State.anim.cardFlipStarted[seat][cardIdx] = now + delay;
      setTimeout(() => {
        State.revealed[seat] = Math.max(State.revealed[seat], cardIdx + 1);
      }, delay + CFG.cardFlipMs);
      t++;
    }
  }
}

function spawnChipFlight(seat, amount) {
  const { fromX, fromY } = chipBarPosition();
  const { toX, toY } = potPosition(seat);
  State.anim.chipFlights.push({
    seat, amount, fromX, fromY, toX, toY,
    startMs: performance.now(),
    duration: CFG.chipFlyMs,
  });
}

function spawnPayoutFloat(seat, amount) {
  const { toX, toY } = potPosition(seat);
  State.anim.payoutFloats.push({
    seat, text: `+${amount}`,
    x: toX, y: toY - 40,
    startMs: performance.now(),
    duration: 1500,
  });
}

/* ───────── BETTING (PLAYER) ───────── */
async function placeBet(seat) {
  if (State.isSpectator) return;
  if (State.betState === 'PENDING') return;   // prevent double-tap
  if (!State.selectedChip) { showToast('Select a chip first'); return; }
  if (State.phase !== 'GUESSING') { showToast('Betting is closed'); return; }

  State.betState = 'PENDING';
  showToast(`Placing ${State.selectedChip} on ${seat}...`);

  const idempotencyKey = (crypto.randomUUID && crypto.randomUUID()) ||
                         `${Date.now()}-${Math.random()}`;

  try {
    const res = await fetch(
      `${API_BASE}/api/v1/games/teen-patti-pro/rounds/${State.roundId}/bets`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${State.session}`,
          'Idempotency-Key': idempotencyKey,
        },
        body: JSON.stringify({
          position: seat,
          amount: Number(State.selectedChip),
        }),
      }
    );
    const json = await res.json();
    if (!res.ok || !json.success) throw new Error(json.message || 'Bet rejected');
    // Success confirmed via WS event; keep PENDING until then
    spawnChipFlight(seat, State.selectedChip);
    setTimeout(() => {
      if (State.betState === 'PENDING') State.betState = 'CONFIRMED';
    }, 1200);
  } catch (e) {
    State.betState = 'REJECTED';
    showToast(`Bet failed: ${e.message}`);
    setTimeout(() => { State.betState = 'IDLE'; }, 1500);
  }
}

function onBetAccepted(msg) {
  State.betState = 'CONFIRMED';
  showToast('Bet accepted');
  if (msg && msg.seat) spawnChipFlight(msg.seat, msg.amount || State.selectedChip);
  setTimeout(() => { State.betState = 'IDLE'; }, 1500);
}
function onBetRejected(msg) {
  State.betState = 'REJECTED';
  showToast(`Bet rejected: ${msg.reason || 'unknown'}`);
  setTimeout(() => { State.betState = 'IDLE'; }, 1500);
}
function onSettlement(msg) {
  // Server confirms settlement; start balance tween
  const newBalance = Number(msg.balance) || State.balance;
  State.anim.balanceTween = {
    from: State.balance, to: newBalance,
    startMs: performance.now(), duration: 600,
  };
}

/* ───────── TOAST / BANNER ───────── */
let toastTimer = null;
function showToast(text) {
  const el = document.getElementById('toast');
  el.textContent = text;
  el.classList.remove('hidden');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add('hidden'), 2200);
}
function showBanner(text) {
  const el = document.getElementById('phase-banner');
  el.textContent = text;
  el.classList.remove('hidden', 'shown', 'urgent');
  void el.offsetWidth;
  el.classList.add('shown');
}
function hideBanner() {
  document.getElementById('phase-banner').classList.add('hidden');
}
function updateUrgent(remaining) {
  const banner = document.getElementById('phase-banner');
  if (State.phase === 'GUESSING' && remaining <= 3) {
    if (banner.classList.contains('hidden')) showBanner(String(Math.max(0, Math.ceil(remaining))));
    banner.textContent = String(Math.max(0, Math.ceil(remaining)));
    banner.classList.add('urgent');
  } else if (State.phase === 'GUESSING') {
    banner.classList.remove('urgent');
    banner.classList.add('hidden');
  }
}

/* ───────── HELPERS ───────── */
function easeOutCubic(t) { return 1 - Math.pow(1 - t, 3); }
function easeOutBack(t)  { const c1=1.70158,c3=c1+1; return 1+c3*Math.pow(t-1,3)+c1*Math.pow(t-1,2); }
function clamp01(t) { return t < 0 ? 0 : t > 1 ? 1 : t; }
function lerp(a, b, t) { return a + (b - a) * t; }

function chairCenter(seat) {
  const margin = 16;
  const gap = 8;
  const panelW = (W - 2*margin - 2*gap) / 3;
  const idx = seat === 'A' ? 0 : seat === 'B' ? 1 : 2;
  return { x: margin + idx * (panelW + gap) + panelW / 2, w: panelW };
}
function potPosition(seat) {
  const c = chairCenter(seat);
  return { toX: c.x, toY: H * 0.55 };
}
function chipBarPosition() {
  return { fromX: W / 2, fromY: H - 60 };
}

/* ───────── RENDER LOOP ───────── */
let rafHandle = null;
function startRenderLoop() {
  if (rafHandle) return;
  const tick = () => {
    updateAnimations();
    drivePhaseMachine();
    draw();
    rafHandle = requestAnimationFrame(tick);
  };
  rafHandle = requestAnimationFrame(tick);
}

function updateAnimations() {
  const now = performance.now();
  // Balance tween
  const bt = State.anim.balanceTween;
  if (bt) {
    const t = clamp01((now - bt.startMs) / bt.duration);
    State.balance = Math.round(lerp(bt.from, bt.to, easeOutCubic(t)));
    if (t >= 1) State.anim.balanceTween = null;
  }
  // Remove expired payout floats
  State.anim.payoutFloats = State.anim.payoutFloats.filter(
    f => now - f.startMs < f.duration
  );
  // Remove expired chip flights
  State.anim.chipFlights = State.anim.chipFlights.filter(
    f => now - f.startMs < f.duration + 300
  );
}

/* ───────── DRAWING ───────── */
function draw() {
  // Background: palace gradient (fallback if asset missing)
  ctx.fillStyle = '#1a0d2b';
  ctx.fillRect(0, 0, W, H);
  drawBackgroundGlow();

  drawToolbar();
  drawTimer();
  drawCards();
  drawTotalBetLine();
  drawChairs();
  drawPanels();
  drawBottomBar();
  drawChips();
  drawWinnerBanner();
  drawPayoutFloats();
  drawPhaseOverlays();
}

function drawBackgroundGlow() {
  const g = ctx.createLinearGradient(0, 0, 0, H);
  g.addColorStop(0, 'rgba(80,20,90,0.55)');
  g.addColorStop(0.5, 'rgba(120,30,60,0.35)');
  g.addColorStop(1, 'rgba(20,10,40,0.9)');
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, W, H);
}

function drawToolbar() {
  // Top bar background
  ctx.fillStyle = 'rgba(15, 8, 30, 0.85)';
  ctx.fillRect(0, 0, W, 52);

  // Back arrow
  ctx.fillStyle = '#f5c451';
  ctx.font = 'bold 20px system-ui';
  ctx.textAlign = 'center';
  ctx.fillText('←', 30, 34);

  // Round + Room
  ctx.fillStyle = '#fff';
  ctx.font = '13px system-ui';
  ctx.textAlign = 'center';
  ctx.fillText(
    `Round ${State.snap?.round?.round_no ?? '—'} • ${State.room || 'demo'}`,
    W / 2, 32
  );

  // Right icons (help, gear)
  ctx.fillStyle = '#f5c451';
  ctx.font = 'bold 16px system-ui';
  ctx.fillText('?', W - 60, 32);
  ctx.fillText('⚙', W - 28, 32);
}

function drawTimer() {
  if (!State.snap?.round?.betting_end_at && State.phase !== 'GUESSING') {
    // Outside betting — show status icon
    return;
  }
  const cx = W / 2;
  const cy = 130;
  const R = 32;

  let seconds = 0;
  let showNumber = false;

  if (State.phase === 'GUESSING' && State.snap?.round?.betting_end_at) {
    const end = Date.parse(State.snap.round.betting_end_at);
    seconds = Math.max(0, Math.ceil((end - Clock.now()) / 1000));
    showNumber = true;
    updateUrgent(seconds);
  }

  // Ring
  ctx.beginPath();
  ctx.arc(cx, cy, R, 0, Math.PI * 2);
  ctx.strokeStyle = seconds <= 3 && showNumber ? '#ef4444' : '#f5c451';
  ctx.lineWidth = 4;
  ctx.stroke();

  // Inner fill
  ctx.beginPath();
  ctx.arc(cx, cy, R - 4, 0, Math.PI * 2);
  ctx.fillStyle = 'rgba(20, 10, 40, 0.85)';
  ctx.fill();

  // Number
  if (showNumber) {
    ctx.fillStyle = seconds <= 3 ? '#ef4444' : '#fff';
    ctx.font = 'bold 26px system-ui';
    ctx.textAlign = 'center';
    ctx.fillText(String(seconds), cx, cy + 9);
  }
}

function drawCards() {
  const seats = ['A', 'B', 'C'];
  const cardY = 200;
  for (const seat of seats) {
    const c = chairCenter(seat);
    const cards = State.hands[seat] || [];
    const groupW = 3 * CFG.cardW + 2 * 6;
    const startX = c.x - groupW / 2;

    for (let i = 0; i < 3; i++) {
      const x = startX + i * (CFG.cardW + 6);

      // Deal-in animation: slide from deck
      const dealStart = State.anim.cardDealStarted[`${seat}-${i}`] || 0;
      const dealElapsed = performance.now() - dealStart;
      let dx = 0, dy = 0, alpha = 1;
      if (dealStart && dealElapsed < CFG.cardDealMs) {
        const t = clamp01(dealElapsed / CFG.cardDealMs);
        const fromX = W / 2 - x;
        const fromY = 60 - cardY;
        dx = fromX * (1 - easeOutCubic(t));
        dy = fromY * (1 - easeOutCubic(t));
      } else if (dealStart && dealElapsed < 50) {
        alpha = 0;
      }

      const revealedCount = State.revealed[seat] || 0;
      const isRevealed = i < revealedCount;

      // Flip animation: rotateY scale on X axis
      let flipScale = 1;
      const flipStart = State.anim.cardFlipStarted[seat]?.[i];
      if (flipStart) {
        const ft = clamp01((performance.now() - flipStart) / CFG.cardFlipMs);
        flipScale = Math.abs(Math.cos(ft * Math.PI));
      }

      ctx.save();
      ctx.globalAlpha = alpha;
      ctx.translate(x + dx + CFG.cardW / 2, cardY + dy + CFG.cardH / 2);
      ctx.scale(Math.max(0.01, flipScale), 1);

      if (isRevealed && cards[i]) {
        drawCardFace(cards[i], -CFG.cardW / 2, -CFG.cardH / 2);
      } else {
        drawCardBack(-CFG.cardW / 2, -CFG.cardH / 2);
      }
      ctx.restore();
    }
  }
}

function drawCardBack(x, y) {
  if (imgReady(ASSETS.cardBack)) {
    ctx.drawImage(ASSETS.cardBack, x, y, CFG.cardW, CFG.cardH);
  } else {
    // Skeleton fallback — never a solid color
    ctx.fillStyle = 'rgba(255,255,255,0.08)';
    ctx.fillRect(x, y, CFG.cardW, CFG.cardH);
    ctx.strokeStyle = 'rgba(255,255,255,0.15)';
    ctx.lineWidth = 1;
    ctx.strokeRect(x + 0.5, y + 0.5, CFG.cardW - 1, CFG.cardH - 1);
  }
}

function drawCardFace(card, x, y) {
  const key = `card-${card.rank}-${card.suit}`;
  const img = ASSETS[key];
  if (imgReady(img)) {
    ctx.drawImage(img, x, y, CFG.cardW, CFG.cardH);
  } else {
    // Fallback: white card with rank + suit text
    ctx.fillStyle = '#fff';
    ctx.fillRect(x, y, CFG.cardW, CFG.cardH);
    ctx.strokeStyle = '#333';
    ctx.lineWidth = 1;
    ctx.strokeRect(x + 0.5, y + 0.5, CFG.cardW - 1, CFG.cardH - 1);
    const isRed = card.suit === 'heart' || card.suit === 'diamond';
    ctx.fillStyle = isRed ? '#c0392b' : '#111';
    ctx.font = `bold ${CFG.cardW * 0.35}px system-ui`;
    ctx.textAlign = 'center';
    ctx.fillText(rankLabel(card.rank), x + CFG.cardW / 2, y + CFG.cardH / 2 + 4);
    ctx.font = `${CFG.cardW * 0.28}px system-ui`;
    ctx.fillText(suitSymbol(card.suit), x + CFG.cardW / 2, y + CFG.cardH / 2 + CFG.cardH * 0.35);
  }
}

function drawTotalBetLine() {
  const y = 355;
  ctx.fillStyle = 'rgba(0,0,0,0.55)';
  const w = W * 0.62;
  roundRect(W/2 - w/2, y - 18, w, 46, 10);
  ctx.fill();
  ctx.strokeStyle = '#f5c451';
  ctx.lineWidth = 1.5;
  roundRect(W/2 - w/2, y - 18, w, 46, 10);
  ctx.stroke();

  ctx.fillStyle = '#fff';
  ctx.font = 'bold 13px system-ui';
  ctx.textAlign = 'center';
  ctx.fillText(`Total Bet ${State.potTotal}`, W/2, y);
  ctx.fillText(`My total bet ${State.myBet}`, W/2, y + 18);
}

function drawChairs() {
  const y = H * 0.56;
  const size = CFG.chairW;
  for (const seat of ['A','B','C']) {
    const c = chairCenter(seat);
    const img = seat === 'A' ? ASSETS.chairA
              : seat === 'B' ? ASSETS.chairB
              : ASSETS.chairC;
    if (imgReady(img)) {
      ctx.drawImage(img, c.x - size/2, y - size/2, size, size);
    } else {
      // Skeleton chair shape — never a solid fill
      ctx.fillStyle = 'rgba(255,255,255,0.06)';
      ctx.beginPath();
      ctx.arc(c.x, y, size/2, 0, Math.PI*2);
      ctx.fill();
    }

    // Seat label
    ctx.fillStyle = '#f5c451';
    ctx.font = 'bold 12px system-ui';
    ctx.textAlign = 'center';
    ctx.fillText(seat, c.x, y + size/2 + 18);

    // Winner glow
    if (State.winnerSeat === seat) {
      drawWinnerGlow(c.x, y, size);
    }
  }
}

function drawWinnerGlow(cx, cy, size) {
  const elapsed = performance.now() - State.anim.winnerGlowStart;
  const phase = (elapsed % 1200) / 1200;
  const opacity = 0.6 + 0.25 * Math.sin(phase * Math.PI * 2);
  const scale = 1 + 0.05 * Math.sin(phase * Math.PI * 2);
  const R = (size / 2 + 12) * scale;
  const grad = ctx.createRadialGradient(cx, cy, R * 0.4, cx, cy, R);
  grad.addColorStop(0, `rgba(245, 196, 81, ${opacity})`);
  grad.addColorStop(1, 'rgba(245, 196, 81, 0)');
  ctx.fillStyle = grad;
  ctx.beginPath();
  ctx.arc(cx, cy, R, 0, Math.PI * 2);
  ctx.fill();
}

function drawPanels() {
  const margin = 16, gap = 8;
  const panelW = (W - 2*margin - 2*gap) / 3;
  const panelY = H - 260;
  const panelH = CFG.panelH;

  const bgColors = { A: '#a82828', B: '#1e4fb8', C: '#1e7a3c' };

  for (const seat of ['A','B','C']) {
    const idx = seat === 'A' ? 0 : seat === 'B' ? 1 : 2;
    const x = margin + idx * (panelW + gap);
    const s = State.seats[seat];

    // Background
    ctx.fillStyle = bgColors[seat];
    roundRect(x, panelY, panelW, panelH, 12);
    ctx.fill();

    // Gold border
    ctx.strokeStyle = State.seats[seat].isMe ? '#ffd700' : '#f5c451';
    ctx.lineWidth = State.seats[seat].isMe ? 3 : 2;
    roundRect(x, panelY, panelW, panelH, 12);
    ctx.stroke();

    // Text
    ctx.fillStyle = '#fff';
    ctx.textAlign = 'center';
    const cx = x + panelW / 2;

    ctx.font = 'bold 14px system-ui';
    ctx.fillText(`POT: ${s.pot}`, cx, panelY + 30);

    ctx.font = '13px system-ui';
    ctx.fillText(`You: ${s.bet}`, cx, panelY + 55);

    ctx.font = 'bold 22px system-ui';
    ctx.fillText(`x2.9`, cx, panelY + 105);

    // Hand label (winner declared only)
    if (State.winnerSeat === seat) {
      ctx.fillStyle = '#f5c451';
      ctx.font = 'bold 12px system-ui';
      ctx.fillText('WINNER', cx, panelY + 130);
    }
  }
}

function drawBottomBar() {
  const y = H - 80;

  // Coin balance pill
  const bw = 120, bh = 48;
  ctx.fillStyle = 'rgba(20, 10, 40, 0.85)';
  roundRect(16, y - 8, bw, bh, 24);
  ctx.fill();
  ctx.strokeStyle = '#f5c451';
  ctx.lineWidth = 1.5;
  roundRect(16, y - 8, bw, bh, 24);
  ctx.stroke();

  if (imgReady(ASSETS.coin)) {
    ctx.drawImage(ASSETS.coin, 24, y + 2, 32, 32);
  }
  ctx.fillStyle = '#fff';
  ctx.font = 'bold 15px system-ui';
  ctx.textAlign = 'left';
  ctx.fillText(formatCompact(State.balance), 64, y + 24);

  // Repeat button
  const rw = 124, rh = 44;
  ctx.fillStyle = 'rgba(60, 60, 60, 0.9)';
  roundRect(W - 140, y - 6, rw, rh, 22);
  ctx.fill();
  ctx.strokeStyle = '#fff';
  ctx.lineWidth = 1.5;
  roundRect(W - 140, y - 6, rw, rh, 22);
  ctx.stroke();
  ctx.fillStyle = '#fff';
  ctx.font = 'bold 14px system-ui';
  ctx.textAlign = 'center';
  ctx.fillText('Repeat', W - 140 + rw/2, y + 20);
}

function drawChips() {
  const denoms = [20, 100, 500, 1000];
  const chipD = 48, gap = 12;
  const totalW = denoms.length * chipD + (denoms.length - 1) * gap;
  const startX = (W - totalW) / 2;
  const y = H - 80;

  denoms.forEach((d, i) => {
    const cx = startX + i * (chipD + gap);
    const img = ASSETS[`chip-${d}`];
    if (imgReady(img)) {
      ctx.drawImage(img, cx, y - 6, chipD, chipD);
    } else {
      ctx.fillStyle = 'rgba(255,255,255,0.06)';
      ctx.beginPath();
      ctx.arc(cx + chipD/2, y - 6 + chipD/2, chipD/2, 0, Math.PI*2);
      ctx.fill();
    }
    // Selected ring
    if (State.selectedChip === d && !State.isSpectator) {
      ctx.strokeStyle = '#ffd700';
      ctx.lineWidth = 3;
      ctx.beginPath();
      ctx.arc(cx + chipD/2, y - 6 + chipD/2, chipD/2 + 3, 0, Math.PI*2);
      ctx.stroke();
    }
  });
}

function drawWinnerBanner() {
  if (State.phase !== 'WINNER_DECLARED' && State.phase !== 'SETTLED' && State.phase !== 'HOLD') return;
  if (!State.winnerSeat) return;

  const elapsed = performance.now() - State.phaseStartedAt;
  const t = clamp01(elapsed / 400);
  const alpha = easeOutCubic(t);
  const scale = 0.85 + 0.15 * easeOutBack(t);

  const cx = W / 2;
  const cy = 175;   // above cards row

  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.translate(cx, cy);
  ctx.scale(scale, scale);

  if (imgReady(ASSETS.winner)) {
    const bw = Math.min(W * 0.7, 320);
    ctx.drawImage(ASSETS.winner, -bw/2, -30, bw, 60);
  } else {
    ctx.fillStyle = '#f5c451';
    ctx.strokeStyle = '#8b6914';
    ctx.lineWidth = 2;
    const bw = Math.min(W * 0.7, 320);
    roundRect(-bw/2, -30, bw, 60, 30);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = '#1a1a1a';
    ctx.font = 'bold 22px system-ui';
    ctx.textAlign = 'center';
    ctx.fillText('WINNER', 0, 8);
  }
  ctx.restore();
}

function drawPayoutFloats() {
  for (const f of State.anim.payoutFloats) {
    const t = clamp01((performance.now() - f.startMs) / f.duration);
    ctx.globalAlpha = 1 - t;
    ctx.fillStyle = '#4ade80';
    ctx.font = 'bold 20px system-ui';
    ctx.textAlign = 'center';
    ctx.fillText(f.text, f.x, f.y - 60 * t);
    ctx.globalAlpha = 1;
  }
}

function drawPhaseOverlays() {
  // Nothing extra — banner is a DOM element
}

/* ───────── UTILITY ───────── */
function roundRect(x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function formatCompact(n) {
  if (!Number.isFinite(n)) return '0';
  if (Math.abs(n) >= 1e9) return (n/1e9).toFixed(1).replace(/\.0$/,'') + 'B';
  if (Math.abs(n) >= 1e6) return (n/1e6).toFixed(1).replace(/\.0$/,'') + 'M';
  if (Math.abs(n) >= 1e3) return (n/1e3).toFixed(1).replace(/\.0$/,'') + 'K';
  return String(n);
}

/* ───────── INPUT ───────── */
canvas.addEventListener('pointerdown', (e) => {
  if (State.isSpectator) return;
  const { x, y } = canvasPointFromEvent(e);

  // Chip bar hit test
  const denoms = [20, 100, 500, 1000];
  const chipD = 48, gap = 12;
  const totalW = denoms.length * chipD + (denoms.length - 1) * gap;
  const startX = (W - totalW) / 2;
  const chipY = H - 80 - 6;
  for (let i = 0; i < denoms.length; i++) {
    const cx = startX + i * (chipD + gap);
    if (x >= cx && x <= cx + chipD && y >= chipY && y <= chipY + chipD) {
      State.selectedChip = denoms[i];
      return;
    }
  }

  // Seat hit test (panels + chairs)
  const margin = 16, gp = 8;
  const panelW = (W - 2*margin - 2*gp) / 3;
  const panelY = H - 260;
  const panelH = CFG.panelH;
  for (const seat of ['A','B','C']) {
    const idx = seat === 'A' ? 0 : seat === 'B' ? 1 : 2;
    const px = margin + idx * (panelW + gp);
    if (x >= px && x <= px + panelW && y >= panelY && y <= panelY + panelH) {
      placeBet(seat);
      return;
    }
  }
});

function canvasPointFromEvent(e) {
  const rect = canvas.getBoundingClientRect();
  return { x: e.clientX - rect.left, y: e.clientY - rect.top };
}

document.getElementById('btn-exit').addEventListener('click', () => {
  const url = new URLSearchParams(location.search).get('return_url');
  location.href = url || '/';
});

/* ───────── INIT ───────── */
async function init() {
  resizeCanvas();
  loadAssets();

  // Extract session + room from query params
  const qs = new URLSearchParams(location.search);
  State.session = qs.get('session');
  State.room = qs.get('room') || 'teen-patti-low';

  if (!State.session) {
    // No session — try minting demo session
    try {
      const r = await fetch(`${API_BASE}/teen-patti-pro?operator=demo&user=demo2_${Date.now()}`,
                            { redirect: 'manual' });
      // Server should 302 → /teen-patti-pro/?session=...
      // Browser will follow; but we handle the case where user lands here without session
      showBanner('No session — use ?operator=demo URL');
      return;
    } catch { showBanner('No session'); return; }
  }

  // Fetch initial snapshot
  try {
    const r = await fetch(
      `${API_BASE}/api/v1/games/teen-patti-pro/rounds/current?room=${State.room}`,
      { headers: { Authorization: `Bearer ${State.session}` } }
    );
    const j = await r.json();
    if (j && j.data) applySnapshot(j.data);
  } catch {}

  // Open WS
  connectWS();

  // Render loop
  startRenderLoop();
}

window.addEventListener('load', init);
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) {
    // Re-sync when tab returns to foreground
    connectWS();
  }
});

})();
