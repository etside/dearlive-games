/* Teen Patti Pro WebView client — Canvas 2D (GPU-composited in WebView).
 * Server-authoritative: renders ONLY server snapshot + WS events.
 * Never computes results/balances locally. Timer from serverTime only.
 * Launch: ?api=http://HOST:5002&ws=ws://HOST:5003&session=<id>&room=<room>
 */
(function () {
  'use strict';
  const q = new URLSearchParams(location.search);
  const API = (q.get('api') || window.location.origin).replace(/\/$/, '');
  // WS endpoint resolution, in priority order:
  //   1. ?ws=...            explicit launch override
  //   2. window.DL_WEBSOCKET_URL   host page may inject a configured value
  //   3. same-origin /ws   default, so a bare /teen-patti-pro URL just works
  // Previously this fell through to '' when neither override was present, and
  // `new WebSocket('')` threw, so the client silently degraded to polling and
  // sat on "connecting" forever. The suite missed it because the test only
  // asserted the DL_WEBSOCKET_URL identifier exists in the source, never that
  // a usable URL resolves. See tests/test_ws_url_resolution.py.
  const WS_RAW = q.get('ws') || window.DL_WEBSOCKET_URL || '';
  const WS = WS_RAW
    ? WS_RAW.replace(/\/$/, '')
    : (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws';
  const SESSION = q.get('session') || '';
  const DEMO_TOKEN = q.get('demo_token') || '';
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
    // Absolute: a relative 'theme.json' resolves to /theme.json and 404s,
    // the same class of bug as the game.js script reference.
    fetch('/teen-patti-pro/theme.json').then(r => r.json()).then(t => {
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

  const S = { snap: null, selDenom: 1000, selPos: null, lastSeq: 0, connected: false,
              msg: '', msgKind: 'info' };
  let roundPollTimer = null, walletPollTimer = null, wsRetryTimer = null;
  const DENOMS = [1000, 10000, 50000, 100000];
  const POS = ['A', 'B', 'C'];
  const SEAT_LABELS = { A: 'YOU', B: 'PLAYER A', C: 'ONLINE' };
  // Seat art. This pointed at 'assets/generated/seat-p4.svg' & friends, which
  // do not exist anywhere in the repo -- all three requests 404'd, the images
  // never loaded, and the chairs silently fell back to procedural shapes with
  // none of the specified colours. Use the real pack: green left, blue centre,
  // red right. Spelled literally rather than via ART_BASE because ART_BASE is
  // declared ~550 lines below this point and a const cannot be read before its
  // declaration.
  const SEAT_ASSETS = [
    '/assets/games/teen-patti-pro/seats/seat-green.svg',
    '/assets/games/teen-patti-pro/seats/seat-blue.svg',
    '/assets/games/teen-patti-pro/seats/seat-red.svg'
  ];
  const seatImages = SEAT_ASSETS.map(src => {
    const image = new Image();
    image.src = src;
    return image;
  });
  // Common HUD state (BRD common UI): panel overlay, sound/music toggle.
  S.panel = null; // null | 'help' | 'menu' | 'history'
  try { S.sound = localStorage.getItem('tpp_sound') !== 'off'; } catch (e) { S.sound = true; }
  S.hist = [];
  // Supplied master audio (assets/dearlive-master, served under
  // Master assets (lottie/gif/wav) live under the game's own mount:
  //   /teen-patti-pro/master/<kind>/<file>
  // These four paths were client-relative AND mis-ordered, so on
  // /teen-patti-pro they resolved to /master/... and every one 404'd --
  // a console error on every timer tick, with no effect on play because
  // the animation is decorative.
  const MASTER_BASE = '/teen-patti-pro/master/';
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
        a = new Audio(MASTER_BASE + 'wav/' + (SFX_FILES[name] || name));
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
    Sound.setMuted(!S.sound);
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

  // ---- HUD (UI-01 header, UI-02 round pill, UI-11 connection, UI-12 states) ----
  //
  // These live in the DOM, not on the canvas, so they are real buttons:
  // focusable, labelled, and announced. The canvas keeps the table.
  const hud = {
    roundNo: document.getElementById('roundNo'),
    room: document.getElementById('roomName'),
    conn: document.getElementById('connPill'),
    dot: document.querySelector('#connPill .dot'),
    pill: document.getElementById('roundPill'),
    seat: document.getElementById('youMarker'),
    connText: document.getElementById('connText'),
    latency: document.getElementById('latency'),
    veil: document.getElementById('veil'),
    veilTitle: document.getElementById('veilTitle'),
    veilText: document.getElementById('veilText'),
    veilSpin: document.getElementById('veilSpin'),
    veilBtn: document.getElementById('veilBtn')
  };

  // The room shown in the HUD must be the one the SERVER resolved, not the
  // one in the query string. ROOM comes from ?room=, which any client can
  // set: a session for table teen-patti-high opened with
  // ?room=BOGUS rendered "Room: BOGUS" while every request correctly went
  // to the authenticated room. The snapshot carries the real room_id.
  let AUTH_ROOM = '';
  function noteAuthoritativeRoom(snap) {
    if (snap && snap.room_id) AUTH_ROOM = String(snap.room_id);
  }
  function roomLabel() { return AUTH_ROOM || ROOM; }

  function setRoundPill(roundNo, room) {
    if (hud.roundNo) hud.roundNo.textContent = roundNo ? ('#' + roundNo) : '--';
    if (hud.room) hud.room.textContent = room ? String(room).slice(0, 12) : '';
    // Round/room panel art behind the pill. Optional; the pill keeps its own
    // styling when the art is absent.
    if (hud.pill && imageReady(UI_IMAGES.panelRoundRoom, 1, 1)) {
      hud.pill.style.backgroundImage = 'url("' + UI_ART.panelRoundRoom + '")';
      hud.pill.style.backgroundSize = '100% 100%';
      hud.pill.dataset.art = '1';
    }
  }

  // "You" panel: a small marker under the local player's seat. Optional.
  function markLocalSeat(seatId) {
    if (!hud.seat) return;
    const art = UI_IMAGES.panelYou;
    if (imageReady(art, 1, 1)) {
      hud.seat.style.backgroundImage = 'url("' + UI_ART.panelYou + '")';
      hud.seat.style.backgroundSize = 'contain';
      hud.seat.dataset.seat = seatId || '';
      hud.seat.hidden = false;
    }
  }

  function setConnection(state, text) {
    if (hud.conn) hud.conn.dataset.conn = state;
    if (hud.connText) hud.connText.textContent = text || state;
    // Swap the status icon when one is loaded; the coloured dot remains the
    // fallback so the indicator is never blank.
    if (hud.dot) {
      const art = STATUS_IMAGES[state];
      if (imageReady(art, 1, 1)) {
        hud.dot.style.backgroundImage = 'url("' + STATUS_ART[state] + '")';
        hud.dot.style.backgroundSize = 'contain';
        hud.dot.style.backgroundRepeat = 'no-repeat';
        hud.dot.dataset.art = '1';
      } else {
        hud.dot.style.backgroundImage = '';
        hud.dot.dataset.art = '';
      }
    }
  }

  /* Latency is measured, never estimated: a WebSocket ping round trip, falling
     back to the REST round trip when the socket is down. Showing a made-up
     number is worse than showing none, so an unmeasured value renders empty. */
  function setLatency(ms) {
    if (!hud.latency) return;
    if (ms === null || ms === undefined || isNaN(ms)) {
      hud.latency.textContent = '';
      hud.latency.dataset.state = 'ok';
      return;
    }
    hud.latency.textContent = ms + 'ms';
    hud.latency.dataset.state = ms > 400 ? 'bad' : (ms > 180 ? 'warn' : 'ok');
  }

  /* One veil for loading, empty and error. `kind` drives the affordance:
     loading gets a spinner and no button, empty explains itself, error offers
     a retry -- because a dead end with no action is the worst of the three. */
  // An empty table is not an error and not still-loading: say which one it is,
  // because "Connecting" over a table with no players reads as a hang.
  function setVeilForState(snap) {
    if (!snap || !snap.round) {
      setVeil('empty', 'Waiting for players',
        'No round is running at this table yet. The next one starts automatically.');
      return;
    }
    setVeil(null);
  }

  function setVeil(kind, title, text, onRetry) {
    if (!hud.veil) return;
    if (!kind) { hud.veil.dataset.show = '0'; return; }
    hud.veil.dataset.show = '1';
    if (hud.veilTitle) hud.veilTitle.textContent = title || '';
    if (hud.veilText) hud.veilText.textContent = text || '';
    if (hud.veilSpin) hud.veilSpin.style.display = kind === 'loading' ? 'block' : 'none';
    if (hud.veilBtn) {
      if (kind === 'error' && onRetry) {
        hud.veilBtn.style.display = 'block';
        hud.veilBtn.onclick = onRetry;
      } else {
        hud.veilBtn.style.display = 'none';
        hud.veilBtn.onclick = null;
      }
    }
  }

  function measureLatency() {
    const t0 = Date.now();
    fetch(API + '/health', { cache: 'no-store' })
      .then(function () { setLatency(Date.now() - t0); })
      .catch(function () { setLatency(null); });
  }

  function status(text, kind) {
    S.msg = text || '';
    S.msgKind = kind || 'info';
    if (text) setToast(text, kind);
  }

  // ===== ANIMATION LAYER (interruptible, reconnect-safe) =====
  const AnimLayer = (function () {
    const animations = new Map();
    let nextId = 0;
    const lottiePlayers = new Map();
    let lottieLib = null;

    function loadLottie() {
      if (lottieLib) return Promise.resolve(lottieLib);
      return new Promise((resolve, reject) => {
        const s = document.createElement('script');
        s.src = 'https://cdnjs.cloudflare.com/ajax/libs/bodymovin/5.9.6/lottie.min.js';
        s.onload = () => { lottieLib = window.lottie; resolve(lottieLib); };
        s.onerror = reject;
        document.head.appendChild(s);
      });
    }

    function playLottie(name, container, opts) {
      return loadLottie().then(() => {
        const url = MASTER_BASE + 'lottie/' + name + '.json';
        const anim = lottieLib.loadAnimation({
          container: container,
          renderer: 'svg',
          loop: opts.loop || false,
          autoplay: true,
          path: url
        });
        if (opts.onComplete) {
          anim.addEventListener('complete', () => { opts.onComplete(); cleanup(name); });
        }
        const key = name + '_' + Date.now();
        lottiePlayers.set(key, { anim, container });
        return { anim, key, destroy: () => { anim.destroy(); lottiePlayers.delete(key); } };
      }).catch(() => {
        // Fallback to GIF
        const img = document.createElement('img');
        img.src = MASTER_BASE + 'gif/' + name + '.gif.gif';
        img.style.width = '100%'; img.style.height = '100%';
        container.appendChild(img);
        return { destroy: () => img.remove() };
      });
    }

    function cleanup(key) { const p = lottiePlayers.get(key); if (p) p.destroy(); }

    function hardReset() {
      animations.forEach(a => a.cancelled = true);
      animations.clear();
      lottiePlayers.forEach(p => p.anim.destroy());
      lottiePlayers.clear();
    }

    function animate({ duration, easing = 'easeOutCubic', onFrame, onComplete }) {
      const id = ++nextId;
      const start = performance.now();
      const instance = { id, cancelled: false, promise: null };
      const easings = {
        linear: t => t,
        easeOutCubic: t => 1 - Math.pow(1 - t, 3),
        easeOutQuad: t => t * (2 - t),
        easeOutBack: t => 1 + (--t) * t * ((1.7 + 1) * t + 1.7),
        easeInOutCubic: t => t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2,
        easeOutElastic: t => t === 1 ? 1 : -Math.pow(2, 10 * t - 10) * Math.sin((t * 10 - 10.75) * 2 * Math.PI / 3)
      };
      const ease = easings[easing] || easings.easeOutCubic;
      // prefers-reduced-motion: apply the end state on the next frame, once.
      // Every animation funnels through here, so this is the single place
      // that has to honour it for the state to be correct without motion.
      if (REDUCED) {
        instance.promise = new Promise(resolve => {
          requestAnimationFrame(() => {
            if (!instance.cancelled) { onFrame(1, 1); onComplete && onComplete(); }
            resolve('done');
          });
        });
        animations.set(id, instance);
        return { id, promise: instance.promise, cancel: () => { instance.cancelled = true; } };
      }
      instance.promise = new Promise(resolve => {
        function step(now) {
          if (instance.cancelled) { resolve('cancelled'); return; }
          const t = Math.min(1, (now - start) / duration);
          const eased = ease(t);
          onFrame(eased, t);
          if (t < 1) requestAnimationFrame(step);
          else { resolve('done'); onComplete && onComplete(); }
        }
        requestAnimationFrame(step);
      });
      animations.set(id, instance);
      return { id, promise: instance.promise, cancel: () => { instance.cancelled = true; } };
    }

    function cancel(id) { const a = animations.get(id); if (a) a.cancelled = true; }

    function cancelAll() { animations.forEach(a => a.cancelled = true); animations.clear(); }


    // ===== 7 REQUIRED ANIMATIONS =====

    // 1. Card deal: stagger 150ms, bezier arc from deck center to each seat
    function animateDeal(deckPos, seats, cardsPerSeat, players) {
      if (REDUCED) return Promise.resolve();
      const seatEntries = Object.entries(seats);
      const promises = seatEntries.map(([pos, pt], si) => {
        const delay = si * 150;
        return new Promise(resolve => {
          setTimeout(() => {
            for (let ci = 0; ci < cardsPerSeat; ci++) {
              const cardDelay = ci * 60;
              setTimeout(() => {
                const cardEl = document.createElement('div');
                cardEl.className = 'anim-card';
                cardEl.style.position = 'absolute';
                cardEl.style.left = deckPos.x + 'px';
                cardEl.style.top = deckPos.y + 'px';
                cardEl.style.width = '48px';
                cardEl.style.height = '68px';
                cardEl.style.background = 'url(/assets/teen-patti/cards/card-back-teenpatti.svg) center/contain no-repeat';
                cardEl.style.pointerEvents = 'none';
                cardEl.style.zIndex = 1000;
                document.body.appendChild(cardEl);
                const ctrlX = deckPos.x + (pt.x - deckPos.x) * 0.5;
                const ctrlY = Math.min(deckPos.y, pt.y) - 120;
                const duration = 500 + Math.random() * 150;
                const startTime = performance.now();
                function animateFrame(now) {
                  const elapsed = now - startTime;
                  const t = Math.min(1, elapsed / duration);
                  const eased = 1 - Math.pow(1 - t, 3);
                  const x = (1 - t) * (1 - t) * deckPos.x + 2 * (1 - t) * t * ctrlX + t * t * pt.x;
                  const y = (1 - t) * (1 - t) * deckPos.y + 2 * (1 - t) * t * ctrlY + t * t * pt.y;
                  cardEl.style.left = x + 'px';
                  cardEl.style.top = y + 'px';
                  cardEl.style.transform = 'rotate(' + (t * 360) + 'deg)';
                  if (t < 1) {
                    requestAnimationFrame(animateFrame);
                  } else {
                    cardEl.remove();
                    if (ci === cardsPerSeat - 1 && si === seatEntries.length - 1) {
                      resolve();
                    }
                  }
                }
                requestAnimationFrame(animateFrame);
              }, ci * 60);
            }
          }, delay);
        });
      });
      return Promise.all(promises);
    }

    // 2. Card flip on CARDS_DEALT
    function animateCardFlip(seatPos, cardsData) {
      if (REDUCED) return Promise.resolve();
      return new Promise(resolve => {
        const container = document.createElement('div');
        container.style.position = 'absolute';
        container.style.left = (seatPos.x - 72) + 'px';
        container.style.top = (seatPos.y - 100) + 'px';
        container.style.width = '144px';
        container.style.height = '100px';
        container.style.pointerEvents = 'none';
        container.style.zIndex = 1000;
        document.body.appendChild(container);
        cardsData.forEach((card, i) => {
          const cardDiv = document.createElement('div');
          cardDiv.style.position = 'absolute';
          cardDiv.style.left = (i * 50) + 'px';
          cardDiv.style.top = '0';
          cardDiv.style.width = '48px';
          cardDiv.style.height = '68px';
          cardDiv.style.transformStyle = 'preserve-3d';
          cardDiv.style.transition = 'transform 0.6s cubic-bezier(0.4, 0, 0.2, 1)';
          cardDiv.style.transform = 'rotateY(0deg)';
          const front = document.createElement('div');
          front.style.position = 'absolute';
          front.style.width = '100%';
          front.style.height = '100%';
          front.style.backfaceVisibility = 'hidden';
          front.style.background = 'url(/assets/teen-patti/cards/card-back-teenpatti.svg) center/contain no-repeat';
          cardDiv.appendChild(front);
          const back = document.createElement('div');
          back.style.position = 'absolute';
          back.style.width = '100%';
          back.style.height = '100%';
          back.style.backfaceVisibility = 'hidden';
          back.style.transform = 'rotateY(180deg)';
          back.style.background = '#fff';
          back.style.display = 'flex';
          back.style.alignItems = 'center';
          back.style.justifyContent = 'center';
          back.style.fontSize = '24px';
          back.style.fontWeight = 'bold';
          back.style.color = (card.suit === '♥' || card.suit === '♦') ? '#c0392b' : '#1a1a1a';
          back.textContent = card.rank + card.suit;
          cardDiv.appendChild(back);
          container.appendChild(cardDiv);
          setTimeout(() => {
            cardDiv.style.transform = 'rotateY(180deg)';
          }, i * 150 + 200);
        });
        setTimeout(() => {
          container.remove();
          resolve();
        }, 1500);
      });
    }

    // 3. Timer pulse ≤5s
    let timerPulseInterval = null;
    function startTimerPulse(container, seconds) {
      if (REDUCED) return;
      const ring = container.querySelector('.timer-ring') || container;
      if (seconds <= 5) {
        timerPulseInterval = setInterval(() => {
          ring.style.animation = 'timer-pulse 0.5s ease-in-out';
          setTimeout(() => { ring.style.animation = ''; }, 500);
        }, 1000);
      }
    }
    function stopTimerPulse() {
      if (timerPulseInterval) {
        clearInterval(timerPulseInterval);
        timerPulseInterval = null;
      }
    }

    // 4. Chip fly on BET_ACCEPTED
    function animateChipBet(fromPos, toPos, amount) {
      if (REDUCED) return Promise.resolve();
      return new Promise(resolve => {
        const chip = document.createElement('div');
        chip.style.position = 'absolute';
        chip.style.left = fromPos.x + 'px';
        chip.style.top = fromPos.y + 'px';
        chip.style.width = '40px';
        chip.style.height = '40px';
        chip.style.borderRadius = '50%';
        chip.style.background = 'linear-gradient(135deg, #ffd700, #b8860b)';
        chip.style.boxShadow = '0 4px 12px rgba(0,0,0,0.4)';
        chip.style.display = 'flex';
        chip.style.alignItems = 'center';
        chip.style.justifyContent = 'center';
        chip.style.fontSize = '14px';
        chip.style.fontWeight = 'bold';
        chip.style.color = '#1a1a1a';
        chip.style.pointerEvents = 'none';
        chip.style.zIndex = 1000;
        chip.textContent = amount >= 1000 ? (amount/1000)+'K' : amount;
        document.body.appendChild(chip);
        const ctrlX = fromPos.x + (toPos.x - fromPos.x) * 0.3;
        const ctrlY = Math.min(fromPos.y, toPos.y) - 80;
        const duration = 400;
        const startTime = performance.now();
        function animateFrame(now) {
          const elapsed = now - startTime;
          const t = Math.min(1, elapsed / duration);
          const eased = 1 - Math.pow(1 - t, 3);
          const x = (1 - t) * (1 - t) * fromPos.x + 2 * (1 - t) * t * ctrlX + t * t * toPos.x;
          const y = (1 - t) * (1 - t) * fromPos.y + 2 * (1 - t) * t * ctrlY + t * t * toPos.y;
          chip.style.left = x + 'px';
          chip.style.top = y + 'px';
          chip.style.transform = 'scale(' + (1 - t * 0.3) + ')';
          if (t < 1) requestAnimationFrame(animateFrame);
          else { chip.remove(); resolve(); }
        }
        requestAnimationFrame(animateFrame);
      });
    }

    // 5. Win glow + confetti on RESULT_DECLARED
    function animateWin(winnerPositions) {
      if (REDUCED) return Promise.resolve();
      return new Promise(resolve => {
        winnerPositions.forEach(pos => {
          const glow = document.createElement('div');
          glow.style.position = 'absolute';
          glow.style.left = (pos.x - 60) + 'px';
          glow.style.top = (pos.y - 60) + 'px';
          glow.style.width = '120px';
          glow.style.height = '120px';
          glow.style.borderRadius = '50%';
          glow.style.border = '4px solid #ffd700';
          glow.style.boxShadow = '0 0 30px 10px rgba(255,215,0,0.6)';
          glow.style.pointerEvents = 'none';
          glow.style.zIndex = 1000;
          glow.style.animation = 'win-glow 1.5s ease-out forwards';
          document.body.appendChild(glow);
          setTimeout(() => glow.remove(), 1500);
        });
        const colors = ['#ffd700', '#ff6b6b', '#4ecdc4', '#ffe66d', '#ff6b9d'];
        for (let i = 0; i < 30; i++) {
          const conf = document.createElement('div');
          conf.style.position = 'absolute';
          conf.style.left = '50%';
          conf.style.top = '30%';
          conf.style.width = '10px';
          conf.style.height = '10px';
          conf.style.background = colors[Math.floor(Math.random() * colors.length)];
          conf.style.borderRadius = Math.random() > 0.5 ? '50%' : '0';
          conf.style.pointerEvents = 'none';
          conf.style.zIndex = 1001;
          document.body.appendChild(conf);
          const angle = Math.random() * Math.PI * 2;
          const velocity = 150 + Math.random() * 200;
          const gravity = 400;
          const startTime = performance.now();
          function step(now) {
            const elapsed = (now - startTime) / 1000;
            if (elapsed > 2.5) { conf.remove(); return; }
            const x = 0.5 * window.innerWidth + velocity * Math.cos(angle) * elapsed;
            const y = 0.3 * window.innerHeight + velocity * Math.sin(angle) * elapsed + 0.5 * gravity * elapsed * elapsed;
            conf.style.left = x + 'px';
            conf.style.top = y + 'px';
            conf.style.transform = 'rotate(' + (elapsed * 720) + 'deg)';
            requestAnimationFrame(step);
          }
          requestAnimationFrame(step);
        }
        setTimeout(resolve, 2500);
      });
    }

    // 6. Button press scale 0.96 - handled via CSS :active on buttons
    // Ensure CSS has .btn:active { transform: scale(0.96); }

    // 7. prefers-reduced-motion respected
    // REDUCED flag already checked at top of each animation

    // Add CSS keyframes dynamically
    const style = document.createElement('style');
    style.textContent = `
      @keyframes timer-pulse {
        0%, 100% { transform: scale(1); opacity: 1; }
        50% { transform: scale(1.1); opacity: 0.7; }
      }
      @keyframes win-glow {
        0% { transform: scale(0.8); opacity: 0; }
        50% { transform: scale(1.2); opacity: 1; }
        100% { transform: scale(1.5); opacity: 0; }
      }
    `;
    document.head.appendChild(style);

    // Expose methods on the returned object
    return { animate, cancel, cancelAll, hardReset, playLottie, cleanup,
      animateDeal, animateCardFlip, animateChipBet, animateWin,
      startTimerPulse, stopTimerPulse };

  })();

  // Sound manager (preloads, volume, fallbacks)
  const Sound = (function () {
    const cache = {};
    let muted = false;
    try { muted = localStorage.getItem('tpp_sound') === 'off'; } catch (e) {}
    const files = { bet: 'bet.wav', win: 'win.wav', coin: 'coin.wav', lose: 'lose.wav', flip: 'card_flip.wav', click: 'click.wav' };
    function play(name, volume = 1.0) {
      if (muted) return Promise.resolve();
      let audio = cache[name];
      if (!audio) {
        audio = new Audio(MASTER_BASE + 'wav/' + files[name]);
        audio.preload = 'auto';
        cache[name] = audio;
      }
      audio.currentTime = 0;
      audio.volume = volume;
      const p = audio.play();
      return p ? p.catch(() => beep(name === 'win')) : Promise.resolve();
    }
    function setMuted(m) { muted = m; try { localStorage.setItem('tpp_sound', m ? 'off' : 'on'); } catch (e) {} }
    function isMuted() { return muted; }
    return { play, setMuted, isMuted };
  })();

  // ===== GAME ANIMATIONS (use AnimLayer + Sound) =====
  // Card deal: deck -> seat with bezier arc, stagger, Lottie + sound
  async function animateDeal(deckPos, seatPositions, cardsPerPlayer, players) {
    // prefers-reduced-motion: land every card immediately, no arc, no stagger.
    if (REDUCED) { deckPos = deckPos || { x: 0, y: 0 }; for (let i = 0; i < (seatPositions || []).length; i++) { /* snapshot already draws the cards */ } return; }
    const L = layout();
    const totalCards = cardsPerPlayer * players.length;
    const cardW = L.cw, cardH = L.ch;
    for (let i = 0; i < totalCards; i++) {
      const playerIdx = i % players.length;
      const player = players[playerIdx];
      if (!player) continue;
      const seatKey = POS[playerIdx];
      const seatPos = L.seats[seatKey];
      if (!seatPos) continue;
      const cardIdx = Math.floor(i / players.length);
      const targetX = seatPos.x - cardW * 1.15 + cardIdx * (cardW + 5);
      const targetY = seatPos.y - cardH / 2;
      const cardEl = document.createElement('div');
      cardEl.style.position = 'absolute';
      cardEl.style.left = deckPos.x + 'px';
      cardEl.style.top = deckPos.y + 'px';
      cardEl.style.width = cardW + 'px';
      cardEl.style.height = cardH + 'px';
      cardEl.style.pointerEvents = 'none';
      cardEl.style.zIndex = 1000 + i;
      cardEl.innerHTML = renderCardBack();
      cv.parentElement.appendChild(cardEl);
      const ctrlX = (deckPos.x + targetX) / 2;
      const ctrlY = Math.min(deckPos.y, targetY) - 120;
      await AnimLayer.animate({
        duration: 400 + i * 80,
        easing: 'easeOutBack',
        onFrame: (eased) => {
          if (cardEl.parentElement) {
            const x = (1 - eased) ** 2 * deckPos.x + 2 * (1 - eased) * eased * ctrlX + eased ** 2 * targetX;
            const y = (1 - eased) ** 2 * deckPos.y + 2 * (1 - eased) * eased * ctrlY + eased ** 2 * targetY;
            const rot = (1 - eased) * (Math.random() * 30 - 15);
            cardEl.style.transform = `translate(${x - deckPos.x}px, ${y - deckPos.y}px) rotate(${rot}deg)`;
          }
        },
        onComplete: () => {
          if (cardEl.parentElement) cardEl.remove();
        }
      }).promise;
      if (i % players.length === players.length - 1) await Sound.play('flip', 0.6);
    }
  }

  // Card flip: scaleX 1 -> 0 -> 1 with texture swap
  function animateFlip(cardEl, faceUp, cardData) {
    // prefers-reduced-motion: swap the texture with no flip.
    if (REDUCED) { cardEl.innerHTML = faceUp ? renderCardFace(cardData) : renderCardBack(); return Promise.resolve(); }
    return AnimLayer.animate({
      duration: 350,
      easing: 'easeInOutCubic',
      onFrame: (eased) => {
        const scaleX = eased < 0.5 ? 1 - eased * 2 : (eased - 0.5) * 2;
        cardEl.style.transform = `scaleX(${Math.max(0.01, scaleX)})`;
        if (Math.abs(eased - 0.5) < 0.02) {
          // Swap texture at midpoint
          cardEl.innerHTML = faceUp ? renderCardFace(cardData) : renderCardBack();
        }
      }
    }).promise;
  }

  function renderCardBack() {
    return '<div style="width:100%;height:100%;background:#0b5fa5;border-radius:8px;display:flex;align-items:center;justify-content:center"><svg viewBox="0 0 48 64" width="70%" height="70%" aria-hidden="true"><path d="M10 42 8 18l10 9 6-14 6 14 10-9-2 24Z" fill="none" stroke="#ffd54a" stroke-width="3" stroke-linejoin="round"/><path d="M10 47h28" stroke="#ffd54a" stroke-width="3" stroke-linecap="round"/></svg></div>';
  }
  function renderCardFace(card) {
    if (!card || card === '**') return renderCardBack();
    const red = /[HD]$/.test(card);
    return '<div style="width:100%;height:100%;background:#f7f4ec;border-radius:8px;border:2px solid #8b0000;display:flex;align-items:center;justify-content:center;color:' + (red ? '#b71c1c' : '#212121') + ';font-weight:bold;font-size:1.2em">' + card + '</div>';
  }

  // Chip bet: arc from seat to pot, stack, glow Lottie + sound
  async function animateChipBet(seatPos, potPos, amount) {
    if (REDUCED) return;  // prefers-reduced-motion: no chip flight
    const chipEl = document.createElement('div');
    chipEl.style.position = 'absolute';
    chipEl.style.left = seatPos.x + 'px';
    chipEl.style.top = seatPos.y + 'px';
    chipEl.style.width = '48px';
    chipEl.style.height = '48px';
    chipEl.style.pointerEvents = 'none';
    chipEl.style.zIndex = 2000;
    const color = amount === 20 ? '#22c55e' : amount === 100 ? '#3b82f6' : amount === 500 ? '#8b5cf6' : '#ef4444';
    chipEl.innerHTML = '<div style="width:100%;height:100%;background:' + color + ';border-radius:50%;display:flex;align-items:center;justify-content:center;color:#fff;font-weight:bold;font-size:14px;box-shadow:0 4px 12px rgba(0,0,0,.4)">' + (amount >= 1000 ? (amount/1000)+'K' : amount) + '</div>';
    cv.parentElement.appendChild(chipEl);
    const ctrlX = (seatPos.x + potPos.x) / 2;
    const ctrlY = Math.min(seatPos.y, potPos.y) - 80;
    await AnimLayer.animate({
      duration: 500,
      easing: 'easeOutQuad',
      onFrame: (eased) => {
        if (chipEl.parentElement) {
          const x = (1 - eased) ** 2 * seatPos.x + 2 * (1 - eased) * eased * ctrlX + eased ** 2 * potPos.x;
          const y = (1 - eased) ** 2 * seatPos.y + 2 * (1 - eased) * eased * ctrlY + eased ** 2 * potPos.y;
          const scale = 1 + eased * 0.2;
          chipEl.style.transform = `translate(${x - seatPos.x}px, ${y - seatPos.y}px) scale(${scale})`;
        }
      },
      onComplete: () => {
        if (chipEl.parentElement) chipEl.remove();
        // Play chip glow Lottie at pot
        const glowContainer = document.createElement('div');
        glowContainer.style.position = 'absolute';
        glowContainer.style.left = (potPos.x - 40) + 'px';
        glowContainer.style.top = (potPos.y - 40) + 'px';
        glowContainer.style.width = '80px';
        glowContainer.style.height = '80px';
        glowContainer.style.pointerEvents = 'none';
        glowContainer.style.zIndex = 2001;
        cv.parentElement.appendChild(glowContainer);
        AnimLayer.playLottie('chip_glow', glowContainer, { loop: false, onComplete: () => glowContainer.remove() });
      }
    }).promise;
    await Sound.play('bet', 0.7);
    await Sound.play('coin', 0.5);
  }

  // Pot collection: chips fly from pot to winner(s)
  async function animatePotCollection(potPos, winnerPositions, amounts) {
    if (REDUCED) return;  // prefers-reduced-motion: no collection flight
    const container = document.createElement('div');
    container.style.position = 'absolute';
    container.style.left = (potPos.x - 60) + 'px';
    container.style.top = (potPos.y - 60) + 'px';
    container.style.width = '120px';
    container.style.height = '120px';
    container.style.pointerEvents = 'none';
    container.style.zIndex = 3000;
    cv.parentElement.appendChild(container);
    await AnimLayer.playLottie('coin_effect', container, { loop: false });
    for (let i = 0; i < winnerPositions.length; i++) {
      const wp = winnerPositions[i];
      const chipEl = document.createElement('div');
      chipEl.style.position = 'absolute';
      chipEl.style.left = potPos.x + 'px';
      chipEl.style.top = potPos.y + 'px';
      chipEl.style.width = '56px';
      chipEl.style.height = '56px';
      chipEl.style.pointerEvents = 'none';
      chipEl.style.zIndex = 3001;
      chipEl.innerHTML = '<div style="width:100%;height:100%;background:#ffd54a;border-radius:50%;display:flex;align-items:center;justify-content:center;color:#3a2f00;font-weight:bold;font-size:16px;box-shadow:0 4px 20px rgba(255,213,74,.6)">' + (amounts[i] >= 1000 ? (amounts[i]/1000)+'K' : amounts[i]) + '</div>';
      cv.parentElement.appendChild(chipEl);
      const ctrlX = (potPos.x + wp.x) / 2;
      const ctrlY = Math.min(potPos.y, wp.y) - 100;
      AnimLayer.animate({
        duration: 700,
        easing: 'easeInOutCubic',
        onFrame: (eased) => {
          if (chipEl.parentElement) {
            const x = (1 - eased) ** 2 * potPos.x + 2 * (1 - eased) * eased * ctrlX + eased ** 2 * wp.x;
            const y = (1 - eased) ** 2 * potPos.y + 2 * (1 - eased) * eased * ctrlY + eased ** 2 * wp.y;
            const scale = 1 + (1 - eased) * 0.3;
            chipEl.style.transform = `translate(${x - potPos.x}px, ${y - potPos.y}px) scale(${scale})`;
          }
        },
        onComplete: () => { if (chipEl.parentElement) chipEl.remove(); }
      });
      await new Promise(r => setTimeout(r, 150));
    }
    await Sound.play('coin', 0.8);
    await Sound.play('win', 0.7);
    container.remove();
  }

  // Timer pulse: Lottie synced to server timeout
  let timerPulseAnim = null;
  let pulseStyleInjected = false;
  function startTimerPulse(container, secsRemaining) {
    stopTimerPulse();
    if (!container) return;
    if (secsRemaining > 5) { container.dataset.pulse = ''; return; }
    // prefers-reduced-motion: state only, no motion.
    if (REDUCED) { container.dataset.pulse = 'ring'; return; }
    // The countdown is drawn by the SVG ring (avatars/timer-ring.svg) plus
    // the stroked progress arc in draw(). 'timer_pulse' was layered on top of
    // that and is a placeholder: one shape layer, solid fill [1, 0.25, 0.05,
    // 1] = #FF400D, on a 512x512 canvas -- a solid orange square, not a ring.
    // It used to 404 so nobody saw it; fixing MASTER_BASE made it resolve and
    // it painted a bright orange block over the timer. The pulse is the ring's
    // own scale/opacity, driven by secsRemaining.
    if (!pulseStyleInjected) {
      pulseStyleInjected = true;
      const st = document.createElement('style');
      // No id: this node is created once and never read back, and a runtime
      // id that is never looked up is a maintenance trap.
      st.textContent =
        '@keyframes tppTimerPulse{0%,100%{transform:scale(1);opacity:1}' +
        '50%{transform:scale(1.14);opacity:.62}}' +
        '[data-pulse="ring"]{animation:tppTimerPulse 1s ease-in-out infinite;' +
        'transform-origin:50% 50%}';
      document.head.appendChild(st);
    }
    // Faster as the clock runs out, so the urgency is legible.
    const period = secsRemaining <= 2 ? 0.5 : 0.9;
    container.style.animationDuration = period + 's';
    container.dataset.pulse = 'ring';
  }
  function stopTimerPulse() {
    if (timerPulseAnim) { timerPulseAnim.destroy(); timerPulseAnim = null; }
    // The pulse node only exists while the countdown is inside the threshold,
    // so this lookup is absent for most of the round and must be guarded.
    const pulseEl = document.getElementById('tpp-timer-pulse');
    if (!pulseEl) return;
    pulseEl.dataset.pulse = '';
    pulseEl.style.animationDuration = '';
  }

  // Winner celebration: fireworks + glow
  async function animateWin(winnerSeatPositions) {
    if (REDUCED) return;  // prefers-reduced-motion: no glow/fireworks
    const promises = winnerSeatPositions.map(pos => {
      const container = document.createElement('div');
      container.style.position = 'absolute';
      container.style.left = (pos.x - 80) + 'px';
      container.style.top = (pos.y - 80) + 'px';
      container.style.width = '160px';
      container.style.height = '160px';
      container.style.pointerEvents = 'none';
      container.style.zIndex = 4000;
      cv.parentElement.appendChild(container);
      return AnimLayer.playLottie('win_fireworks', container, { loop: false, onComplete: () => container.remove() });
    });
    await Promise.all(promises);
    await Sound.play('win', 0.8);
  }

  // Round reset: cards fly back to deck
  async function animateRoundReset(seatPositions, deckPos, players) {
    if (REDUCED) return;  // prefers-reduced-motion: cards are already gone
    const L = layout();
    const cardW = L.cw, cardH = L.ch;
    const promises = [];
    players.forEach((player, pIdx) => {
      if (!player) return;
      const seatKey = POS[pIdx];
      const seatPos = L.seats[seatKey];
      if (!seatPos) return;
      const hands = 3; // Teen Patti = 3 cards
      for (let c = 0; c < hands; c++) {
        const cardEl = document.createElement('div');
        cardEl.style.position = 'absolute';
        cardEl.style.left = (seatPos.x - cardW * 1.15 + c * (cardW + 5)) + 'px';
        cardEl.style.top = (seatPos.y - cardH / 2) + 'px';
        cardEl.style.width = cardW + 'px';
        cardEl.style.height = cardH + 'px';
        cardEl.style.pointerEvents = 'none';
        cardEl.style.zIndex = 1000;
        cardEl.innerHTML = renderCardBack();
        cv.parentElement.appendChild(cardEl);
        const ctrlX = (seatPos.x + deckPos.x) / 2;
        const ctrlY = Math.min(seatPos.y, deckPos.y) - 100;
        promises.push(AnimLayer.animate({
          duration: 400,
          easing: 'easeInQuad',
          onFrame: (eased) => {
            if (cardEl.parentElement) {
              const x = (1 - eased) ** 2 * (seatPos.x - cardW * 1.15 + c * (cardW + 5)) + 2 * (1 - eased) * eased * ctrlX + eased ** 2 * deckPos.x;
              const y = (1 - eased) ** 2 * (seatPos.y - cardH / 2) + 2 * (1 - eased) * eased * ctrlY + eased ** 2 * deckPos.y;
              cardEl.style.transform = `translate(${x - (seatPos.x - cardW * 1.15 + c * (cardW + 5))}px, ${y - (seatPos.y - cardH / 2)}px) rotate(${eased * 360}deg) scale(${1 - eased * 0.3})`;
            }
          },
          onComplete: () => { if (cardEl.parentElement) cardEl.remove(); }
        }).promise);
      }
    });
    await Promise.all(promises);
  }

  // ---- art pack preloading ----------------------------------------------
  //
  // Every art-pack image is optional. If one fails to load, the renderer falls
  // back to drawing the chip / seat / status procedurally, exactly as it did
  // before the pack was wired in. That is deliberate: this build cannot be
  // visually verified (no browser available), so an asset that renders badly
  // must degrade to the known-good drawing rather than to a blank space.
  function preloadImage(src) {
    const img = new Image();
    img.src = src;
    return img;
  }
  function imageReady(img, w, h) {
    return !!(img && img.complete && img.naturalWidth > 0 && w > 0 && h > 0);
  }
  const ART_BASE = '/assets/games/teen-patti-pro/';
  // Chip denomination -> art file. The pack names them by value, so the
  // mapping is a lookup rather than an index that could silently drift.
  const CHIP_ART = {
    20: CHIP_FACE(20), 100: CHIP_FACE(100),
    500: CHIP_FACE(500), 1000: CHIP_FACE(1000)
  };
  function CHIP_FACE(d) { return ART_BASE + 'chips/chip-' + (d >= 1000 ? '1k' : d) + '.svg'; }
  // Seats follow SRS section 1 and the reference: A green, B blue, C red.
  const SEAT_ART = { A: ART_BASE + 'seats/seat-green.svg',
                     B: ART_BASE + 'seats/seat-blue.svg',
                     C: ART_BASE + 'seats/seat-red.svg' };
  // Connection state -> status icon. SRS section 8 vocabulary; the pack
  // supplies one icon per state. Same fallback rule as chips and seats.
  const STATUS_ART = {
    live: ART_BASE + 'ui/status-online.svg',
    polling: ART_BASE + 'ui/status-betting-open.svg',
    connecting: ART_BASE + 'ui/status-waiting.svg',
    offline: ART_BASE + 'ui/status-offline.svg',
    error: ART_BASE + 'ui/status-offline.svg'
  };
  const STATUS_IMAGES = {};
  Object.keys(STATUS_ART).forEach(function (k) {
    STATUS_IMAGES[k] = preloadImage(STATUS_ART[k]);
  });
  const CHIP_IMAGES = {};
  DENOMS.forEach(function (d) { CHIP_IMAGES[d] = preloadImage(CHIP_ART[d]); });
  const SEAT_ART_IMAGES = {};
  Object.keys(SEAT_ART).forEach(function (k) {
    SEAT_ART_IMAGES[k] = preloadImage(SEAT_ART[k]);
  });

  // ---- UI art pack -------------------------------------------------------
  //
  // Buttons, panels, badges and status art. All optional, all behind
  // imageReady with the existing DOM/canvas element kept as the fallback, so
  // an asset that fails to load leaves the control exactly as functional and
  // as visible as it is now.
  const UI_ART = {
    btnBack: ART_BASE + 'ui/btn-back.svg',
    btnHelp: ART_BASE + 'ui/btn-help.svg',
    btnHistory: ART_BASE + 'ui/btn-history.svg',
    btnRepeat: ART_BASE + 'ui/btn-repeat.svg',
    btnAuto: ART_BASE + 'ui/btn-auto.svg',
    btnSettings: ART_BASE + 'ui/btn-settings.svg',
    btnSoundOn: ART_BASE + 'ui/btn-sound-on.svg',
    btnSoundOff: ART_BASE + 'ui/btn-sound-off.svg',
    panelBalance: ART_BASE + 'ui/panel-balance.svg',
    panelPot: ART_BASE + 'ui/panel-pot.svg',
    panelRoundRoom: ART_BASE + 'ui/panel-round-room.svg',
    panelYou: ART_BASE + 'ui/panel-you.svg',
    badgeHot: ART_BASE + 'ui/badge-hot.svg',
    badgeYou: ART_BASE + 'ui/badge-you.svg',
    bannerWinner: ART_BASE + 'ui/banner-winner.svg',
    statusResult: ART_BASE + 'ui/status-result.svg',
    statusBettingClosed: ART_BASE + 'ui/status-betting-closed.svg',
    statusDealing: ART_BASE + 'ui/status-dealing.svg'
  };
  const UI_IMAGES = {};
  Object.keys(UI_ART).forEach(function (k) { UI_IMAGES[k] = preloadImage(UI_ART[k]); });

  const SUIT_ART = { S: ART_BASE + 'cards/suit-spade.svg',
                     H: ART_BASE + 'cards/suit-heart.svg',
                     D: ART_BASE + 'cards/suit-diamond.svg',
                     C: ART_BASE + 'cards/suit-club.svg' };
  const SUIT_IMAGES = {};
  Object.keys(SUIT_ART).forEach(function (k) { SUIT_IMAGES[k] = preloadImage(SUIT_ART[k]); });
  // A crown marks the strongest category in the result banner.
  const CROWN_ART = ART_BASE + 'cards/badge-crown.svg';
  const CROWN_IMAGE = preloadImage(CROWN_ART);
  // card-face-template.svg: BUILD-TIME TEMPLATE, not a runtime asset.
  //
  // It is the traced blank face (corner rank boxes, empty centre) that
  // scripts/generate-cards.mjs consumes to compose the 52 faces in this
  // directory -- template plus rank text, suit glyph and a centre pip. The
  // dependency runs template -> faces, never faces -> template.
  //
  // Kept in assets/ because the pipeline resolves it there (generate-cards.mjs
  // line 105 uses it as the default --face path). It is listed here so a
  // future orphan audit reads it as a deliberate build input rather than dead
  // weight. The runtime never draws it.
  const CARD_FACE_TEMPLATE = ART_BASE + 'cards/card-face-template.svg';
  // Avatar adornments. decorative-ring sits under a seat avatar, timer-ring
  // wraps the countdown, and the gold frame is the default frame in an
  // operator-set appearance. All optional, all behind imageReady.
  const RING_DECORATIVE = preloadImage(ART_BASE + 'avatars/decorative-ring.svg');
  const RING_TIMER = preloadImage(ART_BASE + 'avatars/timer-ring.svg');
  const RING_GOLD_FRAME = preloadImage(ART_BASE + 'avatars/frame-ring-gold-sm.svg');

  // Round status -> status icon, for the status line above the table.
  function roundStatusArt(status) {
    switch (String(status || '').toUpperCase()) {
      case 'BETTING_OPEN': return UI_IMAGES.statusOnline;
      case 'BETTING_CLOSED': return UI_IMAGES.statusBettingClosed;
      case 'RESULT':
      case 'SETTLED':
      case 'CLOSED': return UI_IMAGES.statusResult;
      case 'DEALING': return UI_IMAGES.statusDealing;
      default: return UI_IMAGES.statusWaiting;
    }
  }

  // Header buttons: swap the glyph for pack art when it is available. The
  // text glyph stays as the accessible name and as the fallback.
  function applyButtonArt() {
    const pairs = [['hBack', 'btnBack'], ['hHist', 'btnHistory'],
                   ['hHelp', 'btnHelp'], ['hSound', 'btnSoundOn'],
                   ['hMenu', 'btnSettings']];
    pairs.forEach(function (pair) {
      const el = document.getElementById(pair[0]);
      const img = UI_IMAGES[pair[1]];
      if (el && imageReady(img, 1, 1)) {
        el.style.backgroundImage = 'url("' + UI_ART[pair[1]] + '")';
        el.style.backgroundSize = 'contain';
        el.style.backgroundRepeat = 'no-repeat';
        el.style.backgroundPosition = 'center';
        el.dataset.art = '1';
      }
    });
  }
  function applySoundArt(on) {
    const el = document.getElementById('hSound');
    const img = UI_IMAGES[on ? 'btnSoundOn' : 'btnSoundOff'];
    if (el && imageReady(img, 1, 1)) {
      el.style.backgroundImage = 'url("' + UI_ART[on ? 'btnSoundOn' : 'btnSoundOff'] + '")';
      el.style.backgroundSize = 'contain';
      el.style.backgroundRepeat = 'no-repeat';
      el.dataset.art = '1';
    }
  }

  // ---- card art ----------------------------------------------------------
  //
  // The pack ships all 52 faces plus a back. Engine card codes are rank
  // digits + a suit letter ("14h", "10c"); the pack names them
  // card-<rank>-<suit>.svg with a spelled-out suit. This maps between the two
  // and preloads on demand, so a face that is never dealt is never fetched.
  const RANK_SLUG = { '11': 'J', '12': 'Q', '13': 'K', '14': 'A' };
  const SUIT_SLUG = { S: 'spade', H: 'heart', D: 'diamond', C: 'club' };
  const CARD_BACK_ART = ART_BASE + 'cards/card-back-teenpatti.svg';
  const CARD_BACK_IMAGE = preloadImage(CARD_BACK_ART);
  const cardArtCache = {};
  function cardArt(face) {
    if (!face || face === '**' || face === 'JOK') return null;
    const m = String(face).match(/^(\d+)([SHDC])$/);
    if (!m) return null;
    const rank = RANK_SLUG[m[1]] || m[1];
    const src = ART_BASE + 'cards/card-' + rank + '-' + SUIT_SLUG[m[2]] + '.svg';
    if (!cardArtCache[src]) cardArtCache[src] = preloadImage(src);
    return cardArtCache[src];
  }

  // ---- player appearance (set in DearLive admin, by player id) ----
  //
  // An operator styles a player in the admin panel; the game resolves that
  // style by player id and falls back to the default icon when the player has
  // none, or when the lookup fails for any reason. Nothing here is allowed to
  // block or break the table: a missing avatar must never cost a player their
  // seat, so every failure path lands on the default.
  const DEFAULT_AVATAR = '/assets/games/teen-patti-pro/avatars/avatar-placeholder.svg';
  const DEFAULT_FRAME = '/assets/games/teen-patti-pro/avatars/avatar-frame-navy.svg';
  const appearances = new Map();   // player_id -> {avatar, frame, title, source}
  const avatarImages = new Map();  // url -> HTMLImageElement
  const frameImages = new Map();  // url -> HTMLImageElement

  function appearanceFor(playerId) {
    if (!playerId) return { avatar: DEFAULT_AVATAR, frame: DEFAULT_FRAME, title: '', source: 'default' };
    return appearances.get(playerId) ||
      { avatar: DEFAULT_AVATAR, frame: DEFAULT_FRAME, title: '', source: 'default' };
  }

  function loadFrameImage(url) {
    if (!url) return null;
    if (frameImages.has(url)) return frameImages.get(url);
    const img = new Image();
    img.onerror = () => { img.src = ''; };
    img.src = url;
    frameImages.set(url, img);
    return img;
  }

  function loadAvatarImage(url) {
    if (!url) return null;
    if (avatarImages.has(url)) return avatarImages.get(url);
    const img = new Image();
    // A broken avatar URL leaves complete=true with a zero-size image, so
    // onerror swaps in the default rather than drawing a blank rect.
    img.onerror = () => { img.src = DEFAULT_AVATAR; };
    img.src = url;
    avatarImages.set(url, img);
    return img;
  }

  async function refreshAppearance(playerId) {
    if (!playerId || appearances.has(playerId)) return appearanceFor(playerId);
    const fallback = appearanceFor(playerId);
    try {
      const r = await fetch(API + '/api/v1/players/' +
        encodeURIComponent(playerId) + '/appearance');
      const j = await r.json();
      if (j && j.success && j.data && j.data.appearance) {
        const a = j.data.appearance;
        appearances.set(playerId, {
          avatar: a.avatar || DEFAULT_AVATAR,
          frame: a.frame || DEFAULT_FRAME,
          title: a.title || '',
          source: a.source || 'dearlive'
        });
        loadAvatarImage(appearances.get(playerId).avatar);
        loadFrameImage(appearances.get(playerId).frame);
        return appearances.get(playerId);
      }
    } catch (e) { /* offline, 404, or a store that is down: keep the default */ }
    return fallback;
  }

  // Fetch every seat's style once per round. Fire-and-forget: the table is
  // already drawable with defaults, so there is no reason to make a player
  // wait on an admin lookup to see their chips.
  function refreshAppearances(s) {
    const seats = (s && s.seats) || {};
    Object.keys(seats).forEach(pid => { refreshAppearance(pid); });
  }

  // Expose for reconnect handling
  window.__tppAnim = { AnimLayer, animateDeal, animateFlip, animateChipBet, animatePotCollection, startTimerPulse, stopTimerPulse, animateWin, animateRoundReset };
  async function api(path, opts) {
    opts = opts || {};
    const authHeader = DEMO_TOKEN ? 'Demo ' + DEMO_TOKEN : 'Bearer ' + SESSION;
    opts.headers = Object.assign({ 'Authorization': authHeader }, opts.headers || {});
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
  // ===== PALACE RENDERER =====
  // Art root: assets/teen-patti/, cropped from the labelled contact sheet.
  const PAL = '/assets/teen-patti/';
  const PAL_ART = {
    bg:        PAL + 'background/palace-background.svg',
    logo:      PAL + 'branding/teen-patti-pro-logo.svg',
    back:      PAL + 'navigation/back.svg',
    clock:     PAL + 'navigation/clock.svg',
    gear:      PAL + 'navigation/gear.svg',
    help:      PAL + 'navigation/help.svg',
    stOnline:  PAL + 'status/status-online.svg',
    stOffline: PAL + 'status/status-offline.svg',
    stHot:     PAL + 'status/status-hot.svg',
    cardBack:  PAL + 'cards/card-back-teenpatti.svg',
    cardFront: PAL + 'cards/card-front.svg',
    coin:      PAL + 'icons/coin.svg',
    trophy:    PAL + 'icons/trophy.svg',
    repeat:    PAL + 'ui/btn-repeat.svg',
    winner:    PAL + 'ui/winner-banner.svg',
    roundBar:  PAL + 'ui/round-room-panel.svg',
    panel:     { A: PAL + 'ui/panel-red.svg', B: PAL + 'ui/panel-blue.svg',
                 C: PAL + 'ui/panel-green.svg' },
    seat:      { A: PAL + 'seats/seat-red.svg', B: PAL + 'seats/seat-blue.svg',
                 C: PAL + 'seats/seat-green.svg' },
    chip:      { 1000: PAL + 'chips/chip-1k.svg', 10000: PAL + 'chips/chip-10k.svg',
                 50000: PAL + 'chips/chip-50k.svg', 100000: PAL + 'chips/chip-100k.svg' }
  };
  const PAL_IMG = {};
  Object.keys(PAL_ART).forEach(function (k) {
    if (k === 'panel' || k === 'seat' || k === 'chip') return;
    PAL_IMG[k] = preloadImage(PAL_ART[k]);
  });
  ['A', 'B', 'C'].forEach(function (p) {
    PAL_IMG['panel' + p] = preloadImage(PAL_ART.panel[p]);
    PAL_IMG['seat' + p] = preloadImage(PAL_ART.seat[p]);
  });
  Object.keys(PAL_ART.chip).forEach(function (d) {
    PAL_IMG['chip' + d] = preloadImage(PAL_ART.chip[d]);
  });

  // Reference palette, lifted from the palace art rather than invented.
  const PAL_THEME = {
    gold: '#ffd54a', goldDeep: '#b8860b', ink: '#1b1033',
    cream: '#fff6dc', panelInk: '#3a1030', shadow: 'rgba(12,6,28,.55)'
  };

  function palaceLayout() {
    // One grid, weights normalised over the space the canvas owns. Every band
    // is a fraction of that space, so the frame holds together from 360x640 to
    // a tablet instead of each element anchoring itself to H separately.
    const top = SAFE.t + 4;
    const usable = Math.max(1, H - top - SAFE.b);
    const w = { toolbar: 0.78, roundbar: 0.34, timer: 1.00, cards: 1.70,
                total: 0.44, chairs: 1.46, panels: 2.24, bottom: 1.40 };
    const sum = Object.keys(w).reduce(function (a, k) { return a + w[k]; }, 0);
    const band = {}, y = {};
    let acc = top;
    Object.keys(w).forEach(function (k) {
      band[k] = w[k] / sum;
      y[k] = acc;
      acc += usable * band[k];
    });
    y.bottom = acc;
    const colW = (W - SAFE.l - SAFE.r) / 3;
    const seats = {};
    POS.forEach(function (p, i) {
      seats[p] = { x: SAFE.l + colW * (i + 0.5), y: y.chairs + usable * band.chairs * 0.52 };
    });
    const cw = Math.max(24, Math.min(34, colW * 0.235)), ch = cw * 1.42;
    // The pot/deck anchor. Animation code asks for `L.y.centre` /
    // `L.band.centre` (the old layout's centre band), which this grid does not
    // define, so those reads were undefined and every deck/pot position came
    // out NaN: cards flew from off-screen and chip arcs never landed. The pot
    // lives between the cards row and the chairs, so anchor it there.
    const pot = { x: W / 2, y: y.chairs + usable * band.chairs * 0.12 };
    return { top: top, usable: usable, band: band, y: y, seats: seats,
             cw: cw, ch: ch, colW: colW, cx: W / 2, land: W > H,
             pot: pot, y_centre: pot.y, band_centre: 0.12 * band.chairs,
             // Aliases the animation layer already reads.
             centre: { x: pot.x, y: pot.y } };
  }

  // Centre-of-table position used by the deal/flip/chip animations. Kept as a
  // function so the three call sites cannot drift apart again.
  function potPos(L) { return L.pot || { x: L.cx, y: L.y.chairs + L.usable * L.band.chairs * 0.12 }; }

  // The old layout() is still called by the tap handler for seat hit-testing;
  // it now answers with palace geometry so the two cannot disagree.
  function layout() { return palaceLayout(); }

  function palaceBackground(L, u) {
    const bg = PAL_IMG.bg;
    if (imageReady(bg, 1, 1)) {
      // Cover-fit: the art is 552x342, so scale to fill and crop the overflow
      // rather than letterboxing it and leaving dead bands.
      const sc = Math.max(W / bg.naturalWidth, H / bg.naturalHeight);
      const dw = bg.naturalWidth * sc, dh = bg.naturalHeight * sc;
      ctx.drawImage(bg, (W - dw) / 2, (H - dh) / 2, dw, dh);
    } else {
      const g = ctx.createLinearGradient(0, 0, 0, H);
      g.addColorStop(0, '#3a1a5c'); g.addColorStop(0.5, '#2a1145');
      g.addColorStop(1, '#160a26');
      ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
    }
    // Warm key light from the chandelier, then a soft vignette. Both are
    // multiply-ish overlays so the painted art still reads through.
    const key = ctx.createRadialGradient(W * 0.5, H * 0.30, 10,
                                         W * 0.5, H * 0.30, Math.max(W, H) * 0.62);
    key.addColorStop(0, 'rgba(255,214,120,.20)');
    key.addColorStop(1, 'rgba(255,180,60,0)');
    ctx.fillStyle = key; ctx.fillRect(0, 0, W, H);
    const vg = ctx.createRadialGradient(W / 2, H * 0.44, Math.min(W, H) * 0.34,
                                        W / 2, H * 0.44, Math.max(W, H) * 0.80);
    vg.addColorStop(0, 'rgba(0,0,0,0)'); vg.addColorStop(1, 'rgba(10,4,22,.62)');
    ctx.fillStyle = vg; ctx.fillRect(0, 0, W, H);
  }

  function palaceToolbar(L, u) {
    const s = S.snap || {};
    const h = L.y.toolbar + L.usable * L.band.toolbar * 0.5;
    const r = Math.max(15, Math.min(21, L.usable * L.band.toolbar * 0.34));
    // Back
    S._ctl = [];
    if (imageReady(PAL_IMG.back, 1, 1)) {
      ctx.drawImage(PAL_IMG.back, SAFE.l + 2, h - r, r * 2, r * 2);
    } else {
      ctx.fillStyle = '#c0392b'; ctx.beginPath();
      ctx.arc(SAFE.l + 2 + r, h, r, 0, 7); ctx.fill();
    }
    S._ctl.push({ x: SAFE.l + 2 + r, y: h, r: r, act: 'back' });

    // POT pill, immediately right of back
    const potTxt = 'POT: ' + fmtCompact(num(s.pot_total));
    ctx.font = '600 ' + u.f(12);
    const pw = ctx.measureText(potTxt).width + r * 2.4;
    const px = SAFE.l + 2 + r * 2 + 8;
    ctx.fillStyle = 'rgba(28,14,54,.88)';
    rr(px, h - r * 0.78, pw, r * 1.56, r * 0.78); ctx.fill();
    ctx.strokeStyle = PAL_THEME.gold; ctx.lineWidth = 1.4; ctx.stroke();
    if (imageReady(PAL_IMG.coin, 1, 1)) {
      const cr = r * 0.62;
      ctx.drawImage(PAL_IMG.coin, px + r * 0.34, h - cr, cr * 2, cr * 2);
    }
    ctx.fillStyle = PAL_THEME.cream; ctx.textAlign = 'left';
    ctx.fillText(potTxt, px + r * 1.5, h + 1);
    ctx.textAlign = 'center';

    // Right cluster: clock, avatar, help, gear, trophy
    const ir = r * 0.86;
    const gap = ir * 2 + 5;
    let x = W - SAFE.r - ir - 2;
    function icon(img, act, fallback) {
      if (imageReady(img, 1, 1)) ctx.drawImage(img, x - ir, h - ir, ir * 2, ir * 2);
      else { ctx.fillStyle = fallback; ctx.beginPath();
             ctx.arc(x, h, ir, 0, 7); ctx.fill(); }
      if (act) S._ctl.push({ x: x, y: h, r: ir * 1.15, act: act });
      x -= gap;
    }
    icon(PAL_IMG.trophy, 'ranking', '#c9a227');
    icon(PAL_IMG.gear, 'menu', '#6b4fa0');
    icon(PAL_IMG.help, 'help', '#6b4fa0');
    // avatar: the local player's own look, falling back to a neutral disc
    const av = loadAvatarImage(appearanceFor(null).avatar);
    if (av && av.complete && av.naturalWidth) {
      ctx.save(); ctx.beginPath(); ctx.arc(x, h, ir, 0, 7); ctx.clip();
      ctx.drawImage(av, x - ir, h - ir, ir * 2, ir * 2); ctx.restore();
      ctx.strokeStyle = PAL_THEME.gold; ctx.lineWidth = 1.6;
      ctx.beginPath(); ctx.arc(x, h, ir, 0, 7); ctx.stroke();
    } else {
      ctx.fillStyle = '#4b3a72'; ctx.beginPath(); ctx.arc(x, h, ir, 0, 7); ctx.fill();
      ctx.strokeStyle = PAL_THEME.gold; ctx.lineWidth = 1.6; ctx.stroke();
    }
    x -= gap;
    icon(PAL_IMG.clock, null, '#6b4fa0');
  }

  function palaceRoundBar(L, u) {
    const s = S.snap || {};
    const h = L.y.roundbar + L.usable * L.band.roundbar * 0.5;
    // "10ms  Round: #N" -- the reference shows latency then round, both left.
    const ping = S.connected ? Math.max(1, Math.round(num(S.pingMs))) : 0;
    ctx.textAlign = 'left';
    ctx.font = '600 ' + u.f(11);
    ctx.fillStyle = S.connected ? '#7ef29a' : '#ff9c9c';
    ctx.fillText(ping + 'ms', SAFE.l + 2, h);
    const w1 = ctx.measureText(ping + 'ms').width;
    ctx.fillStyle = PAL_THEME.cream;
    ctx.fillText('Round: ' + (s.round_no || s.round_id || '—'),
                 SAFE.l + 2 + w1 + 8, h);
    ctx.textAlign = 'right';
    const stTxt = s.status || (S.connected ? 'POLLING' : 'OFFLINE');
    ctx.fillStyle = S.connected ? '#9be7ff' : '#ffb4b4';
    ctx.fillText(stTxt, W - SAFE.r - 2, h);
    ctx.textAlign = 'center';
  }

  function palaceTimer(L, u) {
    const s = S.snap || {};
    const cy = L.y.timer + L.usable * L.band.timer * 0.5;
    const r = Math.max(20, Math.min(30, L.usable * L.band.timer * 0.30));
    let secs = null;
    if (s.status === 'BETTING_OPEN' && s.betting_end_at) {
      const skew = (S.srvNow || Date.now()) - (S.locNow || Date.now());
      secs = Math.max(0, (s.betting_end_at - (Date.now() + skew)) / 1000);
    }
    // gold ring, dark centre, number inside -- per the reference badge
    ctx.beginPath(); ctx.arc(L.cx, cy, r, 0, 7);
    ctx.fillStyle = 'rgba(20,10,40,.86)'; ctx.fill();
    ctx.lineWidth = Math.max(3, r * 0.16); ctx.strokeStyle = PAL_THEME.gold; ctx.stroke();
    if (secs !== null) {
      const frac = Math.max(0, Math.min(1, secs / (num(s.betting_seconds) || 30)));
      ctx.beginPath();
      ctx.arc(L.cx, cy, r, -Math.PI / 2, -Math.PI / 2 + frac * Math.PI * 2);
      ctx.strokeStyle = secs < 5 ? '#ff8a8a' : '#ffe9a8';
      ctx.lineWidth = Math.max(2, r * 0.10); ctx.stroke();
      ctx.fillStyle = PAL_THEME.gold;
      ctx.font = 'bold ' + u.f(Math.round(r * 0.82));
      ctx.fillText(String(Math.ceil(secs)), L.cx, cy + 1);
    } else {
      ctx.fillStyle = PAL_THEME.cream;
      ctx.font = 'bold ' + u.f(Math.round(r * 0.46));
      const t = s.status ? s.status.replace(/_/g, ' ').slice(0, 9) : 'WAIT';
      ctx.fillText(t, L.cx, cy + 1);
    }
  }

  function palaceCards(L, u) {
    const s = S.snap || {};
    // 3 columns x 3 cards, gold-back while the round is live.
    const reveal = s.status === 'RESULT' || s.status === 'SETTLED' ||
                   s.status === 'CLOSED' || s.status === 'REVEAL';
    const hands = s.hands || {};
    POS.forEach(function (p) {
      const pt = L.seats[p];
      const top = L.y.cards + 4;
      const n = 3;
      const gap = L.cw * 0.14;
      const totalW = n * L.cw + (n - 1) * gap;
      const x0 = pt.x - totalW / 2;
      const h = (hands[p] && hands[p].length) ? hands[p] : ['**', '**', '**'];
      h.slice(0, n).forEach(function (face, j) {
        const f = reveal ? face : '**';
        card(x0 + j * (L.cw + gap), top, L.cw, L.ch, f);
      });
    });
  }

  function palaceTotalBet(L, u) {
    const s = S.snap || {};
    const h = L.y.total + L.usable * L.band.total * 0.5;
    const pot = num(s.pot_total), mine = num(s.my_bet);
    ctx.font = '600 ' + u.f(12);
    ctx.fillStyle = PAL_THEME.cream;
    ctx.fillText('Total Bet ' + pot, L.cx, h - u.f(7) * 0.6);
    ctx.fillStyle = '#ffe9a8';
    ctx.fillText('My Total Bet ' + mine, L.cx, h + u.f(8) * 0.6);
  }

  function palaceChairs(L, u) {
    const s = S.snap || {};
    const size = Math.max(64, Math.min(104, L.usable * L.band.chairs * 0.62));
    const occ = s.seatOccupancy || {};
    POS.forEach(function (p, i) {
      const pt = L.seats[p];
      const active = !!(s.turn_position === p || s.active_position === p);
      const win = s.winners && s.winners.indexOf(p) >= 0;
      const mine = s.mySeat === p;
      if (active || win) {
        ctx.save();
        if (!REDUCED) { ctx.shadowColor = win ? '#fff0a0' : '#ffd54a'; ctx.shadowBlur = 22; }
        ctx.strokeStyle = win ? '#fff0a0' : '#ffd54a';
        ctx.lineWidth = 3; ctx.beginPath();
        ctx.arc(pt.x, pt.y, size * 0.60, 0, 7); ctx.stroke(); ctx.restore();
      }
      const art = PAL_IMG['seat' + p];
      if (imageReady(art, 1, 1)) {
        ctx.drawImage(art, pt.x - size / 2, pt.y - size * 0.56, size, size * 0.86);
      } else {
        ctx.fillStyle = p === 'A' ? '#c0392b' : p === 'B' ? '#2471a3' : '#1e8449';
        rr(pt.x - size * 0.32, pt.y - size * 0.30, size * 0.64, size * 0.52, 8); ctx.fill();
      }
      // seat label, then the occupant underneath
      const ly = pt.y + size * 0.40;
      ctx.font = 'bold ' + u.f(15);
      ctx.fillStyle = PAL_THEME.gold;
      ctx.fillText(p, pt.x, ly);
      const who = occ[p];
      ctx.font = u.f(10);
      ctx.fillStyle = who ? PAL_THEME.cream : 'rgba(255,246,220,.55)';
      const tag = !who ? 'OPEN' : (mine ? 'YOU' : String(who).slice(0, 10));
      ctx.fillText(tag, pt.x, ly + u.f(12));
    });
  }

  function palacePanels(L, u) {
    const s = S.snap || {};
    const top = L.y.panels;
    const hgt = L.usable * L.band.panels * 0.92;
    const pw = L.colW * 0.90;
    S._panels = [];
    POS.forEach(function (p, i) {
      const cx = L.seats[p].x;
      const px = cx - pw / 2;
      const art = PAL_IMG['panel' + p];
      const mine = num(s.seats && s.seats[p]);
      const pot = num(s.pots && s.pots[p]);
      const mult = num((s.multipliers || {})[p]) || 2.9;
      const sel = S.selPos === p;
      if (imageReady(art, 1, 1)) {
        ctx.drawImage(art, px, top, pw, hgt);
      } else {
        ctx.fillStyle = p === 'A' ? '#c0392b' : p === 'B' ? '#2471a3' : '#1e8449';
        rr(px, top, pw, hgt, 10); ctx.fill();
      }
      if (sel) {
        ctx.strokeStyle = PAL_THEME.gold; ctx.lineWidth = 3;
        rr(px - 2, top - 2, pw + 4, hgt + 4, 11); ctx.stroke();
      }
      // header strip: my bet / seat pot, then the multiplier, as in the panel.
      ctx.fillStyle = 'rgba(0,0,0,.30)';
      rr(px + pw * 0.08, top + hgt * 0.06, pw * 0.84, hgt * 0.15, 6); ctx.fill();
      ctx.fillStyle = '#fff';
      ctx.font = '600 ' + u.f(12);
      ctx.fillText(mine + '/' + pot, cx, top + hgt * 0.135);
      ctx.font = 'bold ' + u.f(Math.max(15, Math.round(hgt * 0.20)));
      ctx.fillStyle = 'rgba(255,255,255,.92)';
      ctx.fillText('x' + mult.toFixed(1), cx, top + hgt * 0.44);
      ctx.font = u.f(10);
      ctx.fillStyle = 'rgba(255,255,255,.85)';
      ctx.fillText(p + (s.mySeat === p ? '  YOU' : ''), cx, top + hgt * 0.66);
      S._panels.push({ p: p, x: cx, y: top + hgt * 0.45, w: pw, h: hgt });
    });
  }

  // Traditional Teen Patti action buttons (BLIND / CHAAL / PACK / SHOW /
  // SIDESHOW) are deliberately NOT drawn. This game is a 3-seat
  // highest-hand / seat-betting variant, per the naming decision recorded in
  // games/teen_patti_pro/plugin.py; none of those mechanics exist in the
  // engine and showing them implies a game that is not being played. The
  // controls that do apply are seat selection, chip denomination and Repeat.

  function palaceBottom(L, u) {
    const h = L.y.bottom;
    const bh = Math.min(L.usable * L.band.bottom * 0.94, 62);
    const by = h + (L.usable * L.band.bottom - bh) / 2;
    // bar plate
    const g = ctx.createLinearGradient(0, by, 0, by + bh);
    g.addColorStop(0, 'rgba(58,20,84,.94)');
    g.addColorStop(1, 'rgba(26,10,44,.96)');
    ctx.fillStyle = g; rr(SAFE.l, by, W - SAFE.l - SAFE.r, bh, 12); ctx.fill();
    ctx.strokeStyle = 'rgba(255,213,74,.55)'; ctx.lineWidth = 1.4; ctx.stroke();

    // coin balance pill, bottom-left
    const cr = bh * 0.30;
    const pillW = Math.max(74, W * 0.24);
    const px = SAFE.l + 6;
    ctx.fillStyle = 'rgba(16,8,32,.92)';
    rr(px, by + bh * 0.18, pillW, bh * 0.64, bh * 0.32); ctx.fill();
    if (imageReady(PAL_IMG.coin, 1, 1)) {
      ctx.drawImage(PAL_IMG.coin, px + bh * 0.10, by + bh * 0.5 - cr, cr * 2, cr * 2);
    }
    ctx.fillStyle = PAL_THEME.cream;
    ctx.font = '600 ' + u.f(13);
    ctx.textAlign = 'left';
    // never render a dash: an unresolved balance is a bug, not a state
    ctx.fillText(S.balance === undefined || S.balance === null
                   ? '0' : fmtCompact(num(S.balance)),
                 px + cr * 2 + bh * 0.10, by + bh * 0.5 + 1);
    ctx.textAlign = 'center';

    // chip row, centred
    S._chips = [];
    const repeatW = Math.min(96, W * 0.24);
    const avail = W - SAFE.l - SAFE.r - pillW - repeatW - 30;
    const cs = Math.min(bh * 0.74, avail / DENOMS.length - 6);
    const cx0 = px + pillW + 10 + (avail - cs * DENOMS.length) / 2 + cs / 2;
    const cy = by + bh * 0.5;
    DENOMS.forEach(function (d, i) {
      const x = cx0 + i * cs;
      const sel = S.selDenom === d;
      const art = PAL_IMG['chip' + d];
      if (imageReady(art, 1, 1)) {
        if (sel) {
          ctx.beginPath(); ctx.arc(x, cy, cs * 0.56, 0, 7);
          ctx.strokeStyle = PAL_THEME.gold; ctx.lineWidth = 2.5; ctx.stroke();
        }
        ctx.drawImage(art, x - cs * 0.46, cy - cs * 0.46, cs * 0.92, cs * 0.92);
      } else {
        ctx.fillStyle = sel ? PAL_THEME.gold : '#8e44ad';
        ctx.beginPath(); ctx.arc(x, cy, cs * 0.42, 0, 7); ctx.fill();
        ctx.fillStyle = '#fff'; ctx.font = 'bold ' + u.f(10);
        ctx.fillText(chipLabel(d), x, cy);
      }
      S._chips.push({ d: d, x: x, y: cy, r: cs * 0.52 });
    });

    // repeat, bottom-right
    const rw = repeatW, rh = bh * 0.66;
    const rx = W - SAFE.r - 6 - rw, ry = by + (bh - rh) / 2;
    if (imageReady(PAL_IMG.repeat, 1, 1)) {
      ctx.drawImage(PAL_IMG.repeat, rx, ry, rw, rh);
    } else {
      ctx.fillStyle = '#2f6fd0'; rr(rx, ry, rw, rh, 8); ctx.fill();
      ctx.fillStyle = '#fff'; ctx.font = '600 ' + u.f(12);
      ctx.fillText('Repeat', rx + rw / 2, ry + rh / 2);
    }
    S._repeat = { x: rx + rw / 2, y: ry + rh / 2, r: Math.max(rw, rh) * 0.5 };
  }

  function chipLabel(d) { return d >= 1000 ? (d / 1000) + 'K' : String(d); }
  function fmtCompact(n) {
    if (!Number.isFinite(n)) return '0';
    if (n >= 1e6) return (n / 1e6).toFixed(2) + 'M';
    if (n >= 1000) return (n / 1000).toFixed(2) + 'K';
    return String(Math.round(n));
  }

  function draw(now) {
    const L = palaceLayout();
    const u = U();
    const s = S.snap;
    ctx.clearRect(0, 0, W, H);
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    palaceBackground(L, u);
    palaceToolbar(L, u);
    palaceRoundBar(L, u);
    palaceTimer(L, u);
    palaceCards(L, u);
    palaceTotalBet(L, u);
    palaceChairs(L, u);
    palacePanels(L, u);
    palaceBottom(L, u);

    // winner banner over the panels
    if (s && s.winners && s.winners.length &&
        (s.status === 'RESULT' || s.status === 'SETTLED' || s.status === 'CLOSED')) {
      const bw = Math.min(W * 0.74, 320), bh = bw * 0.24;
      const by = L.y.panels + L.usable * L.band.panels * 0.18;
      if (imageReady(PAL_IMG.winner, 1, 1)) {
        ctx.drawImage(PAL_IMG.winner, W / 2 - bw / 2, by, bw, bh);
      } else {
        ctx.fillStyle = 'rgba(0,0,0,.6)'; rr(W / 2 - bw / 2, by, bw, bh, 10); ctx.fill();
      }
      ctx.fillStyle = PAL_THEME.gold;
      ctx.font = 'bold ' + u.f(14);
      ctx.fillText('WINNER: ' + s.winners.join(' & '), W / 2, by + bh / 2);
    }

    if (S.msg) {
      ctx.fillStyle = S.msgKind === 'error' ? '#ffb4b4'
                    : (S.msgKind === 'success' ? '#bbf7d0' : '#ffe9a8');
      ctx.font = u.f(12);
      ctx.fillText(S.msg, W / 2, L.y.bottom - 6);
    }
    if (!S.snap && !S._everConnected) {
      ctx.fillStyle = 'rgba(10,4,22,.72)';
      rr(W / 2 - 130, H * 0.46, 260, 46, 12); ctx.fill();
      ctx.fillStyle = '#fff'; ctx.font = '600 ' + u.f(14);
      ctx.fillText('Connecting to table…', W / 2, H * 0.46 + 23);
    } else if (S._everConnected && !S.connected) {
      ctx.fillStyle = 'rgba(150,20,20,.92)';
      rr(W / 2 - 110, H * 0.46, 220, 40, 10); ctx.fill();
      ctx.fillStyle = '#fff'; ctx.font = '600 ' + u.f(14);
      ctx.fillText('Reconnecting…', W / 2, H * 0.46 + 20);
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
      'Choose a seat, then tap again to bet.',
      'Chips: 20 / 100 / 500 / 1K.',
      'RPT repeats your last bets.',
      'Timer is server time. Results are',
      'server-dealt and auditable.',
      'Keys: 1-4 chip, Enter bet.',
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
    // Traditional Teen Patti action buttons were removed (see the draw loop);
    // only chips, Repeat and seat selection are hit-testable.
    for (const c of (S._chips || [])) {
      if ((x - c.x) ** 2 + (y - c.y) ** 2 < c.r * c.r) {
        S.selDenom = c.d; status('Chip ' + c.d + ' selected', 'info'); return;
      }
    }
    const rp = S._repeat;
    if (rp && (x - rp.x) ** 2 + (y - rp.y) ** 2 < rp.r * rp.r) { doRepeat(); return; }
    for (const pn of (S._panels || [])) {
      if (x > pn.x - pn.w / 2 && x < pn.x + pn.w / 2 &&
          y > pn.y - pn.h / 2 && y < pn.y + pn.h / 2) {
        if (S.selPos === pn.p) { placeBet(pn.p); S.selPos = null; }
        else { S.selPos = pn.p; status('Seat ' + pn.p + ' selected — tap again to bet ' + chipLabel(S.selDenom), 'info'); }
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
      // Chip animation + sound
      const L = layout();
      const seatPos = L.seats[pos];
      const potP = potPos(L);
      if (seatPos) window.__tppAnim.animateChipBet(seatPos, potP, S.selDenom);
      await Sound.play('bet');
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
      refreshAppearances(S.snap);
      noteAuthoritativeRoom(S.snap);
      setRoundPill(S.snap && S.snap.round_no, roomLabel());
      markLocalSeat((S.snap && S.snap.seats) ? 'A' : null);
      setVeilForState(S.snap);
      const key = (S.snap && S.snap.round_id) + ':' + ((S.snap && S.snap.winners || []).join(','));
      
      // Round reset: animate cards flying back to deck before new deal
      if (S._roundId && S.snap && S.snap.round_id !== S._roundId) {
        const L = layout();
        const deckPos = potPos(L);
        const players = (prev && prev.players) || [];
        await window.__tppAnim.animateRoundReset(L.seats, deckPos, players);
        
        // New server round -> deal moment
        const newPlayers = (S.snap && S.snap.players) || [];
        window.__tppAnim.animateDeal(deckPos, L.seats, 3, newPlayers);
        S._placedThisRound = false;
        announce('New round ' + (S.snap.round_id || ''));
      }
      if (S.snap) S._roundId = S.snap.round_id;
      if (S._lastWinKey && key !== S._lastWinKey && S.snap.winners && S.snap.winners.length) {
        // Authoritative result published: card flip reveal + win celebration
        const L = layout();
        
        // Flip cards for all players who had hands (showdown reveal)
        if (prev && prev.hands) {
          const cardW = L.cw, cardH = L.ch;
          for (const [pos, hands] of Object.entries(prev.hands)) {
            if (!hands) continue;
            const seatPos = L.seats[pos];
            if (!seatPos) continue;
            for (let i = 0; i < hands.length; i++) {
              const cardData = hands[i];
              if (cardData === '**') continue; // Already face down
              // Create a temporary card element at the seat position for the flip
              const cardEl = document.createElement('div');
              cardEl.style.position = 'absolute';
              cardEl.style.left = (seatPos.x - cardW * 1.15 + i * (cardW + 5)) + 'px';
              cardEl.style.top = (seatPos.y - cardH / 2) + 'px';
              cardEl.style.width = cardW + 'px';
              cardEl.style.height = cardH + 'px';
              cardEl.style.zIndex = 2000;
              cardEl.innerHTML = renderCardBack();
              cv.parentElement.appendChild(cardEl);
              window.__tppAnim.animateFlip(cardEl, true, cardData);
              // Remove after animation
              setTimeout(() => { if (cardEl.parentElement) cardEl.remove(); }, 400);
            }
          }
        }
        
        const winners = S.snap.winners;
        const winnerPositions = winners.map(w => L.seats[w]).filter(Boolean);
        const potP = potPos(L);
        if (winnerPositions.length) {
          window.__tppAnim.animateWin(winnerPositions);
          const amounts = winners.map(w => S.snap.pots?.[w] || 0);
          window.__tppAnim.animatePotCollection(potP, winnerPositions, amounts);
        }
        if (S._placedThisRound) { Sound.play('win'); Sound.play('coin'); }
        else Sound.play('lose');
        status('Result · winner ' + S.snap.winners.join(' and '), 'success');
      } else if (prev && prev.status === 'BETTING_OPEN' && S.snap.status !== 'BETTING_OPEN') {
        status('Betting closed — waiting for the result.', 'info');
      }
      // Timer pulse sync
      if (S.snap && S.snap.status === 'BETTING_OPEN' && S.snap.betting_end_at) {
        const skew = (S.srvNow || Date.now()) - (S.locNow || Date.now());
        const secs = Math.max(0, (S.snap.betting_end_at - (Date.now() + skew)) / 1000);
        if (secs <= 10) {
          const L = layout();
          const timerContainer = document.getElementById('tpp-timer-pulse');
          if (!timerContainer) {
            const tc = document.createElement('div');
            tc.id = 'tpp-timer-pulse';
            tc.style.position = 'absolute';
            tc.style.left = (L.cx - 40) + 'px';
            tc.style.top = (potPos(L).y - 40) + 'px';
            tc.style.width = '80px';
            tc.style.height = '80px';
            tc.style.pointerEvents = 'none';
            tc.style.zIndex = 500;
            cv.parentElement.appendChild(tc);
            window.__tppAnim.startTimerPulse(tc, secs);
          }
        } else {
          window.__tppAnim.stopTimerPulse();
          const tc = document.getElementById('tpp-timer-pulse');
          if (tc) tc.remove();
        }
      } else {
        window.__tppAnim.stopTimerPulse();
        const tc = document.getElementById('tpp-timer-pulse');
        if (tc) tc.remove();
      }
      S._lastWinKey = key;
      S.srvNow = Date.now(); S.locNow = Date.now();
      if (S.msgKind === 'error') clearToast();
    } catch (e) {
      S._everConnected = true;
      status(friendlyError(e), 'error');
    }
  }
  async function refreshWallet(attempt) {
    try {
      const w = await api('/api/v1/wallet/balance');
      if (w && typeof w.available === 'number') {
        if (w.available !== S.balance) announce('Balance ' + w.available);
        S.balance = w.available;
        S._balanceRetries = 0;
        return true;
      }
    } catch (e) { }
    // The wallet can still be warming up when the first frame lands (the
    // session and its wallet are minted in the same request). Retry briefly so
    // the HUD resolves to a number instead of sitting on "BAL --".
    const n = (attempt || 0) + 1;
    S._balanceRetries = n;
    if (n < 6) setTimeout(function () { refreshWallet(n); }, 700 * n);
    return false;
  }
  function startPolling() {
    S.polling = true;
    if (!roundPollTimer) roundPollTimer = setInterval(refresh, 1500);
    if (!walletPollTimer) walletPollTimer = setInterval(refreshWallet, 3000);
  }
  function stopRoundPolling() {
    if (roundPollTimer) { clearInterval(roundPollTimer); roundPollTimer = null; }
    S.polling = false;
  }
  function scheduleWsRetry() {
    if (wsRetryTimer) return;
    wsRetryTimer = setTimeout(() => { wsRetryTimer = null; connect(); }, 30000);
  }


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
    if (k === 's' || k === 'S') { toggleSound(); applySoundArt(S.sound); status('Sound ' + (S.sound ? 'on' : 'off'), 'info'); e.preventDefault(); }
  });
  // Header wiring (UI-01). Each button drives the same code path as its
  // keyboard shortcut, so there is one implementation of "toggle sound"
  // rather than two that can drift.
  function wireHud() {
    const on = function (id, fn) {
      const el = document.getElementById(id);
      if (el) el.addEventListener('click', function (e) { e.preventDefault(); fn(); });
    };
    on('hBack', function () {
      // Prefer a real history entry so Back does not leave the game; fall back
      // to the lobby, which is where a player without history actually is.
      if (window.history.length > 1) window.history.back();
      else window.location.href = './lobby.html';
    });
      on('hHist', function () { openHistory(); });
    on('hSound', function () {
      toggleSound();
      const b = document.getElementById('hSound');
      if (b) b.setAttribute('aria-pressed', S.sound ? 'true' : 'false');
      applySoundArt(S.sound);
      status('Sound ' + (S.sound ? 'on' : 'off'), 'info');
    });
    on('hHelp', function () { S.panel = S.panel === 'help' ? null : 'help'; });
    on('hMenu', function () { S.panel = S.panel === 'menu' ? null : 'menu'; });
    const sb = document.getElementById('hSound');
    if (sb) sb.setAttribute('aria-pressed', S.sound ? 'true' : 'false');
    // Pack art for the header buttons. The glyphs stay underneath as the
    // accessible name and as the fallback if an icon does not load.
    applyButtonArt();
    applySoundArt(S.sound);
    // Re-apply once images have had a chance to decode.
    setTimeout(function () { applyButtonArt(); applySoundArt(S.sound); }, 400);
    setTimeout(function () { applyButtonArt(); applySoundArt(S.sound); }, 1500);
    setRoundPill(null, ROOM);
    setConnection('connecting', 'connecting');
    setVeil('loading', 'Connecting', 'Finding your table.');
  }

  function connect() {
    if (!SESSION) {
      status('No session — open via the launch URL from your operator, or view demo.html', 'error');
      setConnection('offline', 'no session');
      setVeil('error', 'No session',
        'This table needs a launch session. Open it from the lobby, or view demo.html.',
        function () { window.location.reload(); });
      return;
    }
    if (!WS) {
      S.connected = true; S._everConnected = true;
      window.__tppAnim.AnimLayer.hardReset();
      startPolling();
      // The HUD has to say "polling", not "connecting": polling is a working
      // connection, just not a live one, and leaving it on "connecting" tells a
      // perfectly playable table that it is broken.
      setConnection('polling', 'polling');
      measureLatency();
      refresh();
      refreshWallet();
      return;
    }
    let ws;
    try { ws = new WebSocket(WS); } catch (e) { startPolling(); scheduleWsRetry(); return; }
    ws.onopen = () => {
      S.connected = true; S._everConnected = true;
      setConnection('live', 'live');
      setVeil(null);
      measureLatency();
      setInterval(measureLatency, 15000);
      stopRoundPolling();
      window.__tppAnim.AnimLayer.hardReset();
      ws.send(JSON.stringify({ action: 'subscribe', room: ROOM, session: SESSION }));
      refresh();
      // Live mode stops the wallet poll, so the balance has to be fetched here
      // or the HUD sits on "BAL --" for the whole session.
      refreshWallet();
    };
    ws.onmessage = ev => {
      try {
        const m = JSON.parse(ev.data);
        if (m.kind === 'snapshot' && m.data) {
          S.snap = m.data; S.srvNow = Date.now(); S.locNow = Date.now();
          refreshAppearances(m.data);
          noteAuthoritativeRoom(m.data);
          setRoundPill(m.data.round_no, roomLabel());
          setVeil(null);
        }
        else if (m.kind === 'event' && m.data) {
          if (m.data.seq > S.lastSeq) S.lastSeq = m.data.seq;
          // Handle specific event types for animations
          const kind = m.data.kind || m.data.event;
          if (kind === 'round.created' || kind === 'round.started' || kind === 'ROUND_CREATED' || kind === 'ROUND_STARTED') {
            // New round dealt - trigger deal animation
            const L = layout();
            const deckPos = potPos(L);
            const players = (S.snap && S.snap.players) || [];
            window.__tppAnim.animateDeal(deckPos, L.seats, 3, players);
          } else if (kind === 'bet.accepted' || kind === 'BET_ACCEPTED') {
            // Chip bet animation
            const L = layout();
            const seatKey = m.data.player_id || m.data.position;
            const seatPos = L.seats[seatKey];
            const potP = potPos(L);
            if (seatPos) window.__tppAnim.animateChipBet(seatPos, potP, m.data.amount);
          } else if (kind === 'result.published' || kind === 'settlement.completed' || kind === 'RESULT_DECLARED' || kind === 'SETTLEMENT_COMPLETED') {
            // Winner celebration + pot collection
            const L = layout();
            const winners = m.data.winners || m.data.winner_positions || [];
            const winnerPositions = winners.map(w => L.seats[w]).filter(Boolean);
            const potP = potPos(L);
            if (winnerPositions.length) {
              window.__tppAnim.animateWin(winnerPositions);
              const amounts = winners.map(w => m.data.pots?.[w] || 0);
              window.__tppAnim.animatePotCollection(potP, winnerPositions, amounts);
            }
          } else if (kind === 'betting.closed' || kind === 'BETTING_CLOSED') {
            // Timer pulse stop
            window.__tppAnim.stopTimerPulse();
          } else if (kind === 'turn_changed' || kind === 'TURN_CHANGED') {
            // Timer pulse if low time
            const secs = m.data.timeout_seconds || m.data.seconds_remaining || 0;
            const L = layout();
            if (secs <= 5) {
              const timerContainer = document.createElement('div');
              timerContainer.style.position = 'absolute';
              timerContainer.style.left = (L.cx - 40) + 'px';
              timerContainer.style.top = (potPos(L).y - 40) + 'px';
              timerContainer.style.width = '80px';
              timerContainer.style.height = '80px';
              timerContainer.style.pointerEvents = 'none';
              timerContainer.style.zIndex = 500;
              cv.parentElement.appendChild(timerContainer);
              window.__tppAnim.startTimerPulse(timerContainer, secs);
            } else {
              window.__tppAnim.stopTimerPulse();
            }
          }
          refresh();
        } else if (m.kind === 'pong') { /* keepalive */ }
        else if (m.kind === 'error') { showErr('WS: ' + (m.data && m.data.message)); }
      } catch (e) { /* ignore malformed */ }
    };
    ws.onclose = () => {
      S.connected = false;
      startPolling();
      window.__tppAnim.AnimLayer.hardReset();
      scheduleWsRetry();
      // Reconnecting is not the same as offline: polling still works, so the
      // table stays playable and the player is told the truth about why the
      // socket went away.
      if (S._everConnected) {
        setConnection('polling', 'polling');
        setVeil(null);
        setLatency(null);
        measureLatency();
      } else {
        setConnection('offline', 'offline');
        setVeil('error', 'Cannot reach the table',
          'The connection dropped before it was established.',
          function () { window.location.reload(); });
      }
    };
    ws.onerror = () => { try { ws.close(); } catch (e) { /* noop */ } };
    setInterval(() => { try { ws.readyState === 1 && ws.send(JSON.stringify({ action: 'ping' })); } catch (e) { /* noop */ } }, 25000);
  }
  wireHud();
  refresh(); connect(); requestAnimationFrame(draw);
})();
