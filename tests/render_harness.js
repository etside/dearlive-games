/* Run the client's real draw path against a stub canvas.
 *
 * "The table is empty but the background is there" has one cause: the draw
 * loop threw after palaceBackground. requestAnimationFrame(draw) is the last
 * statement in draw(), so an exception anywhere in the chain stops the loop
 * permanently -- one frame with a background, then nothing, forever. That is
 * indistinguishable from "the rest of the render is broken" unless you
 * actually execute it.
 *
 * So: load game.js with a stub canvas and a stub fetch, feed it a real
 * snapshot, and let it throw. The first exception is the bug.
 *
 *   node tests/render_harness.js [snapshot.json]
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const CLIENT = path.join(__dirname, '..', 'games', 'teen_patti_pro', 'client');
const src = fs.readFileSync(path.join(CLIENT, 'game.js'), 'utf8');
const html = fs.readFileSync(path.join(CLIENT, 'index.html'), 'utf8');

const snapshotArg = process.argv[2];
const snapshot = snapshotArg
  ? JSON.parse(fs.readFileSync(snapshotArg, 'utf8'))
  : {
      room_id: 'teen-patti-low', round_id: 'r1', round_no: 1,
      status: 'BETTING_OPEN', serverTime: Date.now(),
      betting_end_at: Date.now() + 20000,
      pots: { A: 100 }, pot_total: 100, my_bet: 0, carry_in: 0,
      hands: { A: ['**', '**', '**'], B: ['**', '**', '**'], C: ['**', '**', '**'] },
      winners: [], config_version: 'v', currency: 'COIN',
      seatOccupancy: { A: 'p1', B: null, C: null },
      members: [{ playerId: 'p1', seat: 'A', joinedAt: 1, status: 'seated' }],
      mySeat: 'A', isSpectator: false, availableSeats: ['B', 'C'],
      balance: 10000, denoms: [20, 100, 500, 1000], max_bet: 100000,
    };

// ---- canvas stub: records the calls the renderer makes -------------------
const calls = [];
let current = 'none';
function makeCtx() {
  const rec = (name) => (...a) => { calls.push(current + '.' + name); };
  return new Proxy({
    canvas: { width: 390, height: 844 },
    measureText: (t) => ({ width: String(t).length * 6 }),
    createLinearGradient: () => ({ addColorStop: () => {} }),
    createRadialGradient: () => ({ addColorStop: () => {} }),
    createPattern: () => null,
    save() { calls.push(current + '.save'); },
    restore() { calls.push(current + '.restore'); },
  }, {
    get(target, prop) {
      if (prop in target) return target[prop];
      if (typeof prop !== 'string') return undefined;
      if (prop === 'then' || prop === Symbol.toPrimitive) return undefined;
      // Any other method becomes a recording no-op. Enumerating canvas methods
      // is a losing game -- a stub that reports an unknown method as a client
      // bug has already lied twice, and "beginPath is not a function" is a
      // harness defect, not a rendering defect.
      return rec(prop);
    },
    set() { return true; },
  });
}
const ctx = makeCtx();

// ---- element stub ---------------------------------------------------------
function el(id, tag) {
  const node = {
    id, tagName: (tag || 'div').toUpperCase(), dataset: {}, style: {},
    className: '', children: [], hidden: false, textContent: '',
    innerHTML: '', value: '', width: 390, height: 844,
    clientWidth: 390, clientHeight: 844,
    classList: { add() {}, remove() {}, contains: () => false },
    appendChild(c) { this.children.push(c); return c; },
    removeChild(c) { return c; },
    remove() {},
    setAttribute() {}, getAttribute: () => null, removeAttribute() {},
    addEventListener() {}, removeEventListener() {},
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 390, height: 844 }),
    getContext: () => ctx, focus() {}, click() {}, closest: () => null,
    querySelector: () => null, querySelectorAll: () => [],
  };
  return node;
}

const nodes = {};
const document = {
  readyState: 'complete',
  documentElement: { style: {} },
  head: { appendChild() {} },
  body: { appendChild() {}, removeChild() {}, style: {} },
  createElement(tag) { const n = el('', tag); return n; },
  createTextNode(t) { return { textContent: t }; },
  getElementById(id) { return nodes[id] || (nodes[id] = el(id)); },
  querySelector() { return null; },
  querySelectorAll() { return []; },
  addEventListener() {},
};
nodes['tpp-canvas'] = (() => {
  const c = el('tpp-canvas', 'canvas');
  c.getContext = () => ctx;
  return c;
})();

// ---- window / timers ------------------------------------------------------
let frames = 0;
const rafQueue = [];
const sandbox = {
  console,
  document,
  navigator: { userAgent: 'node', onLine: true },
  location: { search: '?session=tok&room=teen-patti-low&api=http://x',
              pathname: '/teen-patti-pro/', protocol: 'http:',
              origin: 'http://x', href: 'http://x/' },
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  performance: { now: () => Date.now() },
  requestAnimationFrame(fn) { if (frames < 3) { frames++; rafQueue.push(fn); } return frames; },
  cancelAnimationFrame() {},
  setTimeout(fn, ms) { if (ms <= 50) rafQueue.push(fn); return 1; },
  clearTimeout() {},
  setInterval() { return 1; },
  clearInterval() {},
  matchMedia: () => ({ matches: false, addEventListener() {} }),
  addEventListener() {},
  // Report a successful load, so the artwork paths are exercised too. With a
  // permanently-broken Image every imageReady() is false and the harness only
  // ever tests the fallback rendering -- which is exactly the path that was
  // not broken.
  Image: function () {
    return {
      complete: true, naturalWidth: 64, naturalHeight: 64, width: 64, height: 64,
      set src(v) { this._src = v; },
    };
  },
  WebSocket: function () { this.close = () => {}; },
  Blob: function () {},
  URL: { createObjectURL: () => 'blob:', revokeObjectURL() {} },
  fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve({ success: true, data: snapshot }) }),
  URLSearchParams: class { constructor(s) { this.s = s; } get(k) { return null; } },
  Uint8Array, Math, JSON, Date, Object, Array, String, Number, Boolean,
  isFinite, parseInt, parseFloat, Error, Promise, Set, Map, RegExp, Symbol,
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.self = sandbox;

let failure = null;
try {
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox, { filename: 'game.js' });
  // Drive a few frames, naming each so a call can be attributed.
  for (let i = 0; i < 3 && rafQueue.length; i++) {
    const fn = rafQueue.shift();
    current = 'frame' + i;
    try { fn(Date.now()); } catch (e) { failure = e; break; }
  }
} catch (e) {
  failure = e;
}

if (failure) {
  console.error('\nRENDER THREW: ' + failure.name + ': ' + failure.message);
  const st = (failure.stack || '').split('\n').slice(0, 6).join('\n');
  console.error(st);
  process.exit(1);
}

const drew = new Set(calls.map((c) => c.split('.')[1]));
const perFrame = {};
calls.forEach((c) => { const p = c.split('.'); perFrame[p[1]] = (perFrame[p[1]] || 0) + 1; });
console.log('\nrender completed, no exception');
console.log('canvas calls by method:');
Object.entries(perFrame).sort().forEach(([k, v]) => console.log('  ' + k.padEnd(14) + v));
const expected = ['drawImage', 'fillText', 'arc', 'fillRect'];
const missing = expected.filter((m) => !drew.has(m));
console.log(missing.length ? '\nMISSING: ' + missing.join(', ')
                          : '\nall core draw calls present');
process.exit(missing.length ? 1 : 0);
