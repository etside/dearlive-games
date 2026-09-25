/* DearLive staging admin frontend — vanilla JS, same-origin /api only.
 * The X-Admin-Key lives in a JS variable for this page load only: it is never
 * written to localStorage/sessionStorage/cookies, never logged, and only sent
 * as the X-Admin-Key request header.
 * Endpoints used (all exist in games/teen_patti_pro/api.py):
 *   GET /api/v1/admin/games
 *   GET /api/v1/admin/games/{game}/config
 *   PUT /api/v1/admin/games/{game}/config            (superadmin only)
 *   POST /api/v1/games/{game}/rooms/{room}/rounds/{start|close|result|settle}
 *   GET /api/v1/games/{game}/rounds/current?room=    (Bearer player session)
 *   GET /api/v1/games/{game}/history?room=&limit=    (Bearer player session)
 *   GET /api/v1/games/teen-patti-pro/rooms/{room}/wallet (Bearer player session)
 *   GET /api/v1/admin/audit?limit=
 *   GET /api/v1/admin/webhooks
 */
(function () {
  "use strict";

  var KEY = ""; // memory only

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
  function busy(btn, on) { if (btn) btn.disabled = !!on; }

  function api(path, opts) {
    opts = opts || {};
    var headers = { "Content-Type": "application/json" };
    if (KEY) headers["X-Admin-Key"] = KEY;
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
  function unwrap(res, what) {
    var b = res.body || {};
    if (res.http >= 200 && res.http < 300 && b.success) return b.data;
    throw new Error(what + " failed [HTTP " + res.http + " / " +
      (b.code || "no-code") + "]: " + (b.message || "request failed"));
  }
  function needKey() {
    if (!KEY) { status("Enter an X-Admin-Key first (kept in memory only).", "error"); return false; }
    return true;
  }

  function useKey(ev) {
    if (ev) ev.preventDefault();
    KEY = $("adminkey").value;
    $("adminkey").value = ""; // don't leave the secret in the DOM
    if (!KEY) { status("Paste an X-Admin-Key.", "error"); return; }
    status("Key accepted in memory. Checking admin reads…");
    loadInventory();
  }
  function forgetKey() {
    KEY = "";
    $("key-out").hidden = true;
    $("adminkey").value = "";
    status("Key forgotten. Memory cleared.");
  }

  function loadInventory() {
    if (!needKey()) return;
    var btn = $("inv-btn");
    busy(btn, true);
    status("Loading game inventory…");
    api("/api/v1/admin/games").then(function (res) {
      var games = unwrap(res, "Inventory");
      var list = Array.isArray(games) ? games : (games.games || games.inventory || []);
      paintInventory(list);
      $("key-out").hidden = false;
      $("o-keyfp").textContent = "(in memory — not displayed, not stored)";
      $("o-reads").textContent = "OK (key valid, auditor rank or higher)";
      $("o-games").textContent = list.map(function (g) { return g.game_id; }).join(", ") || "none";
      status("Inventory loaded: " + list.length + " game(s).", "ok");
    }).catch(function (err) {
      $("key-out").hidden = true;
      status(err.message + " — check the key and try again.", "error");
    }).then(function () { busy(btn, false); });
  }

  function paintInventory(list) {
    var tb = document.querySelector("#games-tbl tbody");
    tb.innerHTML = "";
    if (!list.length) {
      tb.innerHTML = "<tr><td colspan='5'>No games reported.</td></tr>";
      return;
    }
    list.forEach(function (g) {
      var tr = document.createElement("tr");
      function cell(t) { var td = document.createElement("td"); td.textContent = t; return td; }
      tr.appendChild(cell(g.game_id || ""));
      tr.appendChild(cell(g.name || ""));
      tr.appendChild(cell(g.status || ""));
      tr.appendChild(cell(g.enabled === false ? "disabled" : "enabled"));
      var act = document.createElement("td");
      [["View", function () { viewConfig(g.game_id); }],
       ["Enable", function () { setEnabled(g.game_id, true); }],
       ["Disable", function () { setEnabled(g.game_id, false); }]
      ].forEach(function (pair) {
        var b = document.createElement("button");
        b.type = "button";
        b.className = "mini" + (pair[0] === "View" ? " ghost" : "");
        b.textContent = pair[0];
        b.addEventListener("click", pair[1]);
        act.appendChild(b);
      });
      tr.appendChild(act);
      tb.appendChild(tr);
    });
  }

  function viewConfig(gameId) {
    if (!needKey()) return;
    status("Loading config for " + gameId + "…");
    api("/api/v1/admin/games/" + encodeURIComponent(gameId) + "/config").then(function (res) {
      $("config").textContent = show(unwrap(res, "Config"));
      status("Config loaded for " + gameId + ".", "ok");
    }).catch(function (err) {
      $("config").textContent = err.message;
      status(err.message, "error");
    });
  }

  function setEnabled(gameId, on) {
    if (!needKey()) return;
    status((on ? "Enabling " : "Disabling ") + gameId + "…");
    // Only superadmin keys may PUT config; operator/admin keys get a clear
    // FORBIDDEN denial from the backend, shown verbatim below.
    api("/api/v1/admin/games/" + encodeURIComponent(gameId) + "/config", {
      method: "PUT", body: { enabled: on }
    }).then(function (res) {
      var d = unwrap(res, "Config update");
      $("config").textContent = show(d);
      status(gameId + (on ? " enabled." : " disabled."), "ok");
      loadInventory();
    }).catch(function (err) {
      $("config").textContent = err.message;
      status(err.message, "error");
    });
  }

  function roundOp(op) {
    if (!needKey()) return;
    var game = $("r-game").value;
    var room = $("r-room").value.trim() || "staging-room";
    status("Round " + op + " on " + game + " / " + room + "…");
    api("/api/v1/games/" + encodeURIComponent(game) + "/rooms/" +
      encodeURIComponent(room) + "/rounds/" + op, { method: "POST", body: {} }
    ).then(function (res) {
      $("round-out").textContent = show(unwrap(res, "Round " + op));
      status("Round " + op + " done.", "ok");
    }).catch(function (err) {
      $("round-out").textContent = err.message;
      status(err.message, "error");
    });
  }

  function bearerOrExplain(inputId) {
    var tok = $(inputId).value.trim();
    if (!tok) {
      $("round-out").textContent =
        "State/history views need a player Bearer session: the backend exposes no " +
        "admin-key state endpoint. Paste a player session_id to read via the existing " +
        "player view, or use round controls + audit instead.";
      status("Player Bearer token required for state views.", "error");
      return null;
    }
    return tok;
  }

  function viewState() {
    var tok = bearerOrExplain("r-bearer");
    if (!tok) return;
    var game = $("r-game").value;
    var room = $("r-room").value.trim() || "staging-room";
    api("/api/v1/games/" + encodeURIComponent(game) + "/rounds/current?room=" +
      encodeURIComponent(room), { headers: { Authorization: "Bearer " + tok } }
    ).then(function (res) {
      $("round-out").textContent = show(unwrap(res, "State"));
      status("State loaded.", "ok");
    }).catch(function (err) {
      $("round-out").textContent = err.message;
      status(err.message, "error");
    });
  }

  function viewHistory() {
    var tok = bearerOrExplain("r-bearer");
    if (!tok) return;
    var game = $("r-game").value;
    var room = $("r-room").value.trim() || "staging-room";
    api("/api/v1/games/" + encodeURIComponent(game) + "/history?room=" +
      encodeURIComponent(room) + "&limit=50",
      { headers: { Authorization: "Bearer " + tok } }
    ).then(function (res) {
      $("round-out").textContent = show(unwrap(res, "History"));
      status("History loaded.", "ok");
    }).catch(function (err) {
      $("round-out").textContent = err.message;
      status(err.message, "error");
    });
  }

  function viewWallet(ev) {
    if (ev) ev.preventDefault();
    var tok = $("w-bearer").value.trim();
    var room = $("w-room").value.trim() || "staging-room";
    if (!tok) {
      $("wallet-out").textContent =
        "MISSING: no admin-key wallet endpoint exists in the backend. " +
        "Paste a player session_id to read the existing teen wallet view instead.";
      status("Player Bearer token required for wallet view.", "error");
      return;
    }
    api("/api/v1/games/teen-patti-pro/rooms/" + encodeURIComponent(room) + "/wallet",
      { headers: { Authorization: "Bearer " + tok } }
    ).then(function (res) {
      $("wallet-out").textContent = show(unwrap(res, "Wallet"));
      status("Balance loaded.", "ok");
    }).catch(function (err) {
      $("wallet-out").textContent = err.message;
      status(err.message, "error");
    });
  }

  function loadAudit() {
    if (!needKey()) return;
    api("/api/v1/admin/audit?limit=100").then(function (res) {
      $("audit").textContent = show(unwrap(res, "Audit"));
      status("Audit loaded.", "ok");
    }).catch(function (err) {
      $("audit").textContent = err.message;
      status(err.message, "error");
    });
  }

  function loadWebhooks() {
    if (!needKey()) return;
    api("/api/v1/admin/webhooks").then(function (res) {
      $("webhooks").textContent = show(unwrap(res, "Webhooks"));
      status("Webhooks loaded.", "ok");
    }).catch(function (err) {
      $("webhooks").textContent = err.message;
      status(err.message, "error");
    });
  }

  $("key-form").addEventListener("submit", useKey);
  $("key-clear").addEventListener("click", forgetKey);
  $("inv-btn").addEventListener("click", loadInventory);
  document.querySelectorAll("#round-form [data-op]").forEach(function (b) {
    b.addEventListener("click", function () { roundOp(b.dataset.op); });
  });
  $("r-state").addEventListener("click", viewState);
  $("r-hist").addEventListener("click", viewHistory);
  $("wallet-form").addEventListener("submit", viewWallet);
  $("audit-btn").addEventListener("click", loadAudit);
  $("wh-btn").addEventListener("click", loadWebhooks);
  window.addEventListener("beforeunload", function () { KEY = ""; });
})();
