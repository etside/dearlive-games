/* DearLive staging player frontend — vanilla JS, same-origin /api only.
 * Endpoints used (all exist in staging/wsgi.py or games/teen_patti_pro/api.py):
 *   POST /api/v1/staging/test-login
 *   POST /api/v1/staging/test-wallet/grant
 *   POST /api/v1/games/{gameId}/sessions
 *   GET  /api/v1/games/teen-patti-pro/rooms/{room}/wallet   (teen only)
 *   GET  /api/v1/games/{game}/rounds/current?room=
 *   GET  /api/v1/games/{game}/history?room=&limit=
 *   GET  /api/v1/games/{game}/rounds/{roundId}/result?room=
 *   GET  /api/v1/games/{game}/results/recent?room=&limit=
 *   GET  /api/v1/games/teen-patti-pro                      (public seats/denoms)
 *   GET  /api/v1/games/{game}/assets                        (wheel options)
 *   POST /api/v1/games/{game}/rounds/{roundId}/bets?room=   (Idempotency-Key)
 */
(function () {
  "use strict";

  var GAMES = {
    "teen-patti-pro": { label: "Teen Patti Pro", route: "/teen-patti-pro/", wallet: true },
    "greedy-lion": { label: "Greedy Lion", route: "/greedy-lion/", wallet: false },
    "greedy-monkey": { label: "Monkey Wheel", route: "/greedy-monkey/", wallet: false }
  };

  // In-memory session; persisted only to sessionStorage so a same-tab trip to
  // the game client and back (Return home) keeps working. Cleared on logout.
  var S = { player: "", game: "teen-patti-pro", room: "staging-room",
            launchToken: "", sessionId: "" };
  try {
    var saved = JSON.parse(sessionStorage.getItem("dl-player") || "null");
    if (saved && typeof saved === "object") {
      ["player", "game", "room", "launchToken", "sessionId"].forEach(function (k) {
        if (typeof saved[k] === "string") S[k] = saved[k];
      });
      if (!GAMES[S.game]) S.game = "teen-patti-pro";
    }
  } catch (e) { /* storage unavailable: stay in memory */ }
  function persist() {
    try { sessionStorage.setItem("dl-player", JSON.stringify(S)); } catch (e) {}
  }
  function clearPersist() {
    try { sessionStorage.removeItem("dl-player"); } catch (e) {}
  }

  function $(id) { return document.getElementById(id); }
  var statusEl = $("status");
  function status(msg, kind) {
    statusEl.textContent = msg;
    statusEl.dataset.kind = kind || "";
  }
  function show(obj) {
    try { return JSON.stringify(obj, null, 2); }
    catch (e) { return String(obj); }
  }
  function short(tok) {
    if (!tok) return "–";
    return tok.length > 18 ? tok.slice(0, 10) + "…" + tok.slice(-6) : tok;
  }

  function api(path, opts) {
    opts = opts || {};
    var headers = { "Content-Type": "application/json" };
    Object.keys(opts.headers || {}).forEach(function (k) { headers[k] = opts.headers[k]; });
    return fetch(path, {
      method: opts.method || "GET",
      headers: headers,
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body)
    }).then(function (res) {
      return res.text().then(function (text) {
        var body = null;
        try { body = text ? JSON.parse(text) : null; } catch (e) { body = { raw: text }; }
        return { http: res.status, body: body };
      });
    });
  }
  // Backend envelope is {success, code, message, data}. ok=>{data}, else throw.
  function unwrap(res, what) {
    var b = res.body || {};
    if (res.http >= 200 && res.http < 300 && b.success) return b.data;
    var code = b.code || ("HTTP_" + res.http);
    throw new Error(what + " failed [" + code + "]: " + (b.message || "request failed"));
  }
  function busy(btn, on) { if (btn) btn.disabled = !!on; }

  function paintLogin() {
    $("player").value = S.player || "qa-player";
    $("game").value = GAMES[S.game] ? S.game : "teen-patti-pro";
    $("room").value = S.room || "staging-room";
    var hasLogin = !!S.launchToken;
    $("login-out").hidden = !hasLogin && !S.sessionId;
    $("o-player").textContent = S.player || "–";
    $("o-game").textContent = S.game || "–";
    $("o-room").textContent = S.room || "–";
    $("o-token").textContent = short(S.launchToken);
    $("o-session").textContent = S.sessionId || "–";
    paintLaunch();
  }
  function paintLaunch() {
    var link = $("launch-link");
    if (S.sessionId && GAMES[S.game]) {
      var url = GAMES[S.game].route + "?session=" + encodeURIComponent(S.sessionId) +
        "&room=" + encodeURIComponent(S.room);
      link.href = url;
      link.hidden = false;
      link.textContent = "Open " + GAMES[S.game].label + " client ↗";
    } else {
      link.hidden = true;
      link.href = "#";
    }
  }

  function doLogin(ev) {
    if (ev) ev.preventDefault();
    var btn = $("login-btn");
    busy(btn, true);
    status("Logging in…");
    var player = $("player").value.trim() || "qa-player";
    var game = $("game").value;
    var room = $("room").value.trim() || "staging-room";
    api("/api/v1/staging/test-login", {
      method: "POST", body: { player: player, game: game, room: room }
    }).then(function (res) {
      var d = unwrap(res, "Staging login");
      S.player = d.player_id || player;
      S.game = d.game_id || game;
      S.room = d.room_id || room;
      S.launchToken = d.launch_token || "";
      S.sessionId = "";
      persist();
      paintLogin();
      status("Logged in as " + S.player + " on " + S.game + ".", "ok");
      return refreshViews();
    }).catch(function (err) {
      status(err.message, "error");
    }).then(function () { busy(btn, false); });
  }

  function doGrant(ev) {
    if (ev) ev.preventDefault();
    if (!S.player) { status("Log in first.", "error"); return; }
    var btn = $("fund-btn");
    busy(btn, true);
    status("Granting TEST coins…");
    var amount = parseInt($("amount").value, 10);
    var key = "grant-" + S.player + "-" + Date.now();
    api("/api/v1/staging/test-wallet/grant", {
      method: "POST",
      body: { player: S.player, amount: amount, idempotency_key: key, game: S.game }
    }).then(function (res) {
      var d = unwrap(res, "TEST funding");
      status("Granted. Available: " + d.available + " TEST.", "ok");
      return refreshViews();
    }).catch(function (err) {
      status(err.message, "error");
    }).then(function () { busy(btn, false); });
  }

  function openSession() {
    if (!S.launchToken) { status("Log in first to get a launch token.", "error"); return; }
    var btn = $("open-session-btn");
    busy(btn, true);
    status("Opening session…");
    api("/api/v1/games/" + encodeURIComponent(S.game) + "/sessions", {
      method: "POST", body: { launch_token: S.launchToken }
    }).then(function (res) {
      var d = unwrap(res, "Open session");
      S.sessionId = d.session_id || "";
      S.player = d.player_id || S.player;
      S.room = d.room_id || S.room;
      persist();
      paintLogin();
      status("Session open: " + S.sessionId, "ok");
      return refreshViews();
    }).catch(function (err) {
      status(err.message, "error");
    }).then(function () { busy(btn, false); });
  }

  function authHeaders() {
    if (!S.sessionId) throw new Error("Open a session first.");
    return { Authorization: "Bearer " + S.sessionId };
  }
  function roomQ() { return "?room=" + encodeURIComponent(S.room); }

  function loadBalance() {
    var el = $("balance"), note = $("balance-note");
    if (!S.sessionId) { el.textContent = "Open a session first."; note.hidden = true; return Promise.resolve(); }
    if (!GAMES[S.game].wallet) {
      // No wheel wallet-balance route exists in the backend (only
      // GET /api/v1/games/teen-patti-pro/rooms/{room}/wallet). Report, don't invent.
      el.textContent = "No wallet-balance endpoint for " + S.game + " in the backend.";
      note.textContent = "MISSING: backend exposes GET rooms/{room}/wallet for teen-patti-pro only. " +
        "Wheel balances are visible inside the game client after launch.";
      note.hidden = false;
      return Promise.resolve();
    }
    note.hidden = true;
    el.textContent = "Loading…";
    return api("/api/v1/games/teen-patti-pro/rooms/" + encodeURIComponent(S.room) + "/wallet", {
      headers: authHeaders()
    }).then(function (res) {
      el.textContent = show(unwrap(res, "Balance"));
    }).catch(function (err) {
      el.textContent = err.message;
    });
  }

  function loadState() {
    var el = $("state");
    if (!S.sessionId) { el.textContent = "Open a session first."; return Promise.resolve(); }
    el.textContent = "Loading…";
    return api("/api/v1/games/" + encodeURIComponent(S.game) + "/rounds/current" + roomQ(), {
      headers: authHeaders()
    }).then(function (res) {
      var d = unwrap(res, "State");
      el.textContent = show(d);
      var rid = d.round_id || (d.round && d.round.round_id) || "current";
      var resEl = $("result");
      if (d.status === "RESULT" || d.status === "SETTLED" || d.status === "CLOSED") {
        api("/api/v1/games/" + encodeURIComponent(S.game) + "/rounds/" +
          encodeURIComponent(rid) + "/result" + roomQ(), { headers: authHeaders() }
        ).then(function (r2) {
          try { resEl.textContent = show(unwrap(r2, "Result")); }
          catch (e) { resEl.textContent = e.message; }
        }).catch(function (e) { resEl.textContent = e.message; });
      } else {
        resEl.textContent = "No published result (status: " + (d.status || "none") + ").";
      }
    }).catch(function (err) {
      el.textContent = err.message;
    });
  }

  function loadHistory() {
    var el = $("history");
    if (!S.sessionId) { el.textContent = "Open a session first."; return Promise.resolve(); }
    el.textContent = "Loading…";
    return api("/api/v1/games/" + encodeURIComponent(S.game) + "/history" + roomQ() + "&limit=50", {
      headers: authHeaders()
    }).then(function (res) {
      el.textContent = show(unwrap(res, "History"));
    }).catch(function (err) {
      el.textContent = err.message;
    });
  }

  function loadRecent() {
    var el = $("recent");
    if (!S.sessionId) { el.textContent = "Open a session first."; return Promise.resolve(); }
    el.textContent = "Loading…";
    return api("/api/v1/games/" + encodeURIComponent(S.game) + "/results/recent" + roomQ() + "&limit=20", {
      headers: authHeaders()
    }).then(function (res) {
      el.textContent = show(unwrap(res, "Recent results"));
    }).catch(function (err) {
      // Teen Patti has no recent-results view in the backend (404). Not an error in flow.
      el.textContent = err.message;
    });
  }

  function loadChoices() {
    var sel = $("choice"), src = $("choice-src");
    sel.innerHTML = "";
    if (!S.sessionId) { src.textContent = "(open a session first)"; return Promise.resolve(); }
    src.textContent = "(loading…)";
    function fill(list, source) {
      sel.innerHTML = "";
      list.forEach(function (c) {
        var o = document.createElement("option");
        o.value = c.value;
        o.textContent = c.label;
        sel.appendChild(o);
      });
      src.textContent = source;
    }
    if (S.game === "teen-patti-pro") {
      return api("/api/v1/games/teen-patti-pro").then(function (res) {
        var d = unwrap(res, "Game info");
        var seats = d.seats || ["A", "B", "C"];
        fill(seats.map(function (s) { return { value: s, label: "Seat " + s }; }),
          "(seats from GET /api/v1/games/teen-patti-pro)");
      }).catch(function (err) {
        src.textContent = err.message;
      });
    }
    return api("/api/v1/games/" + encodeURIComponent(S.game) + "/assets").then(function (res) {
      var d = unwrap(res, "Assets");
      var opts = d.options || [];
      fill(opts.map(function (o) {
        return { value: o.option_id, label: o.name + " ×" + o.multiplier };
      }), "(options from GET /api/v1/games/" + S.game + "/assets)");
    }).catch(function (err) {
      src.textContent = err.message;
    });
  }

  function placeBet(ev) {
    if (ev) ev.preventDefault();
    try { authHeaders(); } catch (e) { status(e.message, "error"); return; }
    var btn = $("bet-btn");
    busy(btn, true);
    status("Placing bet…");
    var choice = $("choice").value;
    var amount = parseInt($("bet-amount").value, 10);
    var body = S.game === "teen-patti-pro"
      ? { position: choice, amount: amount }
      : { option_id: choice, amount: amount };
    var key = "bet-" + S.player + "-" + Date.now();
    var rid = "current";
    try {
      var st = JSON.parse($("state").textContent || "null");
      if (st && st.round_id) rid = st.round_id;
    } catch (e) {}
    api("/api/v1/games/" + encodeURIComponent(S.game) + "/rounds/" +
      encodeURIComponent(rid) + "/bets" + roomQ(), {
        method: "POST", body: body,
        headers: { Authorization: "Bearer " + S.sessionId, "Idempotency-Key": key }
      }).then(function (res) {
      unwrap(res, "Bet");
      status("Bet accepted.", "ok");
      return refreshViews();
    }).catch(function (err) {
      status(err.message, "error");
    }).then(function () { busy(btn, false); });
  }

  function refreshViews() {
    return Promise.resolve()
      .then(loadBalance).then(loadState).then(loadHistory).then(loadRecent).then(loadChoices);
  }

  function logout() {
    S = { player: "", game: "teen-patti-pro", room: "staging-room", launchToken: "", sessionId: "" };
    clearPersist();
    paintLogin();
    $("balance").textContent = "Log in first.";
    $("state").textContent = "Log in first.";
    $("history").textContent = "–";
    $("recent").textContent = "–";
    $("result").textContent = "–";
    $("choice").innerHTML = "";
    status("Logged out. Session cleared.");
  }

  $("login-form").addEventListener("submit", doLogin);
  $("fund-form").addEventListener("submit", doGrant);
  $("bet-form").addEventListener("submit", placeBet);
  $("open-session-btn").addEventListener("click", openSession);
  $("logout-btn").addEventListener("click", logout);
  $("refresh-btn").addEventListener("click", function () {
    if (!S.sessionId) { status("Open a session first.", "error"); return; }
    status("Refreshing…");
    refreshViews().then(function () { status("Refreshed.", "ok"); });
  });
  $("hist-btn").addEventListener("click", loadHistory);
  $("recent-btn").addEventListener("click", loadRecent);
  $("choices-btn").addEventListener("click", loadChoices);

  paintLogin();
  if (S.sessionId) refreshViews();
})();
