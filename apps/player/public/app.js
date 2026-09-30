/* Teen Patti Pro home page.
 *
 * Everything on this page is read from a real endpoint. The previous version
 * of this file drove a staging QA console -- POST /api/v1/staging/test-login,
 * POST /api/v1/staging/test-wallet/grant, POST /api/v1/games/{id}/sessions --
 * and none of those routes exist, so every control on the page returned 404
 * and the one honest line in the markup said as much in a footnote. A page
 * whose buttons all fail is worse than a page with no buttons, so this reads
 * the two endpoints that do answer: /health and the public game descriptor.
 *
 * No value here is a sample. If a request fails, the field says so.
 */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };

  function api(path) {
    return fetch(path, { headers: { 'Accept': 'application/json' } })
      .then(function (res) {
        return res.json().catch(function () { return {}; }).then(function (body) {
          if (!res.ok || (body && body.success === false)) {
            var msg = (body && (body.message || body.code)) || ('HTTP ' + res.status);
            throw new Error(msg);
          }
          return body.data;
        });
      });
  }

  function set(id, text) {
    var el = $(id);
    if (el) el.textContent = text;
  }

  function fail(id, err) {
    var el = $(id);
    if (!el) return;
    el.textContent = 'unavailable (' + (err && err.message ? err.message : 'error') + ')';
    el.classList.add('bad');
  }

  function fmtTime(iso) {
    if (!iso) return 'unknown';
    var d = new Date(iso);
    return isNaN(d.getTime()) ? String(iso) : d.toLocaleTimeString();
  }

  function fmtCoins(n) {
    // The denominations are plain integers; no compact form, so a chip value
    // is never rounded into something the player cannot bet.
    return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  }

  function loadHealth() {
    return api('/health').then(function (d) {
      set('live-status', (d && d.game) ? 'ok' : 'ok');
      set('live-config', (d && d.config) || 'unknown');
      set('live-confirmed', d && d.confirmed ? 'yes' : 'no');
      set('live-time', fmtTime(d && d.serverTime));
    }).catch(function (err) { fail('live-status', err); });
  }

  function loadConfig() {
    return api('/api/v1/games/teen-patti-pro').then(function (d) {
      d = d || {};
      set('cfg-seats', (d.seats || []).join(' · ') || 'unknown');
      set('cfg-denoms', (d.denoms || []).map(fmtCoins).join(' · ') || 'unknown');
      set('cfg-bets', fmtCoins(d.min_bet) + ' – ' + fmtCoins(d.max_bet));
      var secs = d.guess_ms ? Math.round(d.guess_ms / 1000) : 0;
      set('cfg-window', secs ? secs + 's' : 'unknown');
      var tbc = d.tbc || [];
      var note = document.getElementById('cfg-tbc');
      if (note) {
        // Stated plainly rather than hidden: these are the rules still marked
        // to-be-confirmed for the running build.
        note.textContent = tbc.length
          ? ('Pending confirmation: ' + tbc.join(', '))
          : 'No rules pending confirmation.';
        note.hidden = false;
      }
    }).catch(function (err) { fail('cfg-seats', err); });
  }

  function markActiveNav() {
    var here = window.location.pathname;
    var links = document.querySelectorAll('[data-page]');
    for (var i = 0; i < links.length; i++) {
      var page = links[i].getAttribute('data-page');
      var href = links[i].getAttribute('href') || '';
      var isHome = (page === 'home' && (here === '/' || here === '/index.html'));
      if (isHome || (page !== 'home' && href.split('?')[0] === here)) {
        links[i].classList.add('active');
        links[i].setAttribute('aria-current', 'page');
      }
    }
  }

  function start() {
    markActiveNav();
    loadHealth();
    loadConfig();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
