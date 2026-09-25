/* DearLive staging super-admin frontend — vanilla JS, same-origin /api only.
 * Includes all admin capabilities plus staging key issuance. The X-Admin-Key
 * and one-time issuance secrets live in JS variables for this page load only:
 * never localStorage/sessionStorage/cookies, never logged (no console calls),
 * secrets shown exactly once then erased from the DOM on dismiss/navigation.
 * Endpoints used (staging/wsgi.py + games/teen_patti_pro/api.py):
 *   POST /api/v1/staging/api-keys/provision   {pin,label,role,games,ttl_seconds}
 *   GET  /api/v1/staging/api-keys             (superadmin X-Admin-Key; no secrets)
 *   POST /api/v1/staging/api-keys/revoke      {key_id}
 *   POST /api/v1/staging/api-keys/rotate      {key_id,pin,ttl_seconds}
 *   GET /api/v1/admin/games
 *   GET /api/v1/admin/games/{game}/config
 *   PUT /api/v1/admin/games/{game}/config
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
    if (!KEY) { status("Enter a superadmin X-Admin-Key first (memory only).", "error"); return false; }
    return true;
  }

  function useKey(ev) {
    if (ev) ev.preventDefault();
    KEY = $("adminkey").value;
    $("adminkey").value = "";
    if (!KEY) { status("Paste a superadmin X-Admin-Key.", "error"); return; }
    status("Key accepted in memory. Checking admin reads…");
    loadInventory();
  }
  function forgetKey() {
    KEY = "";
    eraseSecret();
    $("key-out").hidden = true;
    $("adminkey").value = "";
    status("Key forgotten. Memory cleared.");
  }

  /* ---- one-time secret handling: show once, copyable, never stored ---- */
  function showSecretOnce(secret, contextLabel) {
    $("secret-text").textContent = secret;
    $("secret-box").hidden = false;
    $("copy-btn").focus();
    status(contextLabel + " — secret shown once below. Copy it now.", "ok");
  }
  function eraseSecret() {
    $("secret-text").textContent = "";
    $("secret-box").hidden = true;
  }
  function copySecret() {
    var t = $("secret-text").textContent;
    if (!t) { status("Nothing to copy — the secret was erased.", "error"); return; }
    function done() { status("Secret copied to clipboard.", "ok"); }
    function fallback() {
      var ta = document.createElement("textarea");
      ta.value = t;
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand("copy"); done(); }
      catch (e) { status("Copy failed — select the secret text manually.", "error"); }
      document.body.removeChild(ta);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(t).then(done, fallback);
    } else {
      fallback();
    }
  }

  function selectedGames() {
    return Array.prototype.filter.call(
      document.querySelectorAll('input[name="kgame"]:checked'),
      function (el) { return true; }
    ).map(function (el) { return el.value; });
  }

  function issueKey(ev) {
    if (ev) ev.preventDefault();
    if (!needKey()) return;
    var games = selectedGames();
    if (!games.length) { status("Select at least one game scope.", "error"); return; }
    var btn = $("issue-btn");
    busy(btn, true);
    eraseSecret();
    status("Issuing staging key…");
    var ttlRaw = $("k-ttl").value;
    var body = {
      pin: $("k-pin").value,
      label: $("k-label").value.trim(),
      role: $("k-role").value,
      games: games,
      ttl_seconds: ttlRaw === "" ? undefined : parseInt(ttlRaw, 10)
    };
    $("k-pin").value = ""; // PIN must not linger in the DOM
    api("/api/v1/staging/api-keys/provision", { method: "POST", body: body }
    ).then(function (res) {
      var d = unwrap(res, "Issue key");
      if (d.key_secret) showSecretOnce(d.key_secret, "Key " + d.key_id + " issued");
      else status("Key issued, but no secret was returned.", "error");
      listKeys();
    }).catch(function (err) {
      status(err.message, "error");
    }).then(function () { busy(btn, false); });
  }

  function listKeys() {
    if (!needKey()) return;
    var btn = $("list-btn");
    busy(btn, true);
    api("/api/v1/staging/api-keys").then(function (res) {
      var d = unwrap(res, "List keys");
      paintKeys(d.keys || []);
      status("Keys listed: " + (d.count || 0) + " (list responses never carry secrets).", "ok");
    }).catch(function (err) {
      status(err.message, "error");
    }).then(function () { busy(btn, false); });
  }

  function paintKeys(keys) {
    var tb = document.querySelector("#keys-tbl tbody");
    tb.innerHTML = "";
    if (!keys.length) {
      tb.innerHTML = "<tr><td colspan='6'>No keys.</td></tr>";
      return;
    }
    keys.forEach(function (k) {
      var tr = document.createElement("tr");
      function cell(t) { var td = document.createElement("td"); td.textContent = t; return td; }
      tr.appendChild(cell(k.key_id || ""));
      tr.appendChild(cell(k.label || ""));
      tr.appendChild(cell(k.role || ""));
      tr.appendChild(cell((k.games || []).join(", ")));
      tr.appendChild(cell(k.revoked ? "revoked" : "active"));
      var act = document.createElement("td");
      if (!k.revoked) {
        var b = document.createElement("button");
        b.type = "button";
        b.className = "mini";
        b.textContent = "Revoke";
        b.addEventListener("click", function () { revokeKey(k.key_id); });
        act.appendChild(b);
        var fill = document.createElement("button");
        fill.type = "button";
        fill.className = "mini ghost";
        fill.textContent = "Rotate→";
        fill.addEventListener("click", function () { $("rot-id").value = k.key_id; $("rot-id").focus(); });
        act.appendChild(fill);
      } else {
        act.textContent = "–";
      }
      tr.appendChild(act);
      tb.appendChild(tr);
    });
  }

  function revokeKey(keyId) {
    if (!needKey()) return;
    status("Revoking " + keyId + "…");
    api("/api/v1/staging/api-keys/revoke", { method: "POST", body: { key_id: keyId } }
    ).then(function (res) {
      unwrap(res, "Revoke");
      status("Key revoked: " + keyId, "ok");
      listKeys();
    }).catch(function (err) {
      status(err.message, "error");
    });
  }

  function rotateKey(ev) {
    if (ev) ev.preventDefault();
    if (!needKey()) return;
    var btn = $("rot-btn");
    busy(btn, true);
    eraseSecret();
    status("Rotating key…");
    var ttlRaw = $("rot-ttl").value;
    var body = {
      key_id: $("rot-id").value.trim(),
      pin: $("rot-pin").value,
      ttl_seconds: ttlRaw === "" ? undefined : parseInt(ttlRaw, 10)
    };
    $("rot-pin").value = "";
    api("/api/v1/staging/api-keys/rotate", { method: "POST", body: body }
    ).then(function (res) {
      var d = unwrap(res, "Rotate");
      if (d.key_secret) showSecretOnce(d.key_secret, "Rotated to " + d.key_id);
      else status("Rotated, but no new secret was returned.", "error");
      listKeys();
    }).catch(function (err) {
      status(err.message, "error");
    }).then(function () { busy(btn, false); });
  }

  /* ---- admin capabilities (same contracts as the admin app) ---- */
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
    api("/api/v1/admin/games/" + encodeURIComponent(gameId) + "/config", {
      method: "PUT", body: { enabled: on }
    }).then(function (res) {
      $("config").textContent = show(unwrap(res, "Config update"));
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

  function bearerOrExplain() {
    var tok = $("r-bearer").value.trim();
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
    var tok = bearerOrExplain();
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
    var tok = bearerOrExplain();
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
  $("issue-form").addEventListener("submit", issueKey);
  $("list-btn").addEventListener("click", listKeys);
  $("rotate-form").addEventListener("submit", rotateKey);
  $("copy-btn").addEventListener("click", copySecret);
  $("dismiss-btn").addEventListener("click", function () {
    eraseSecret();
    status("Secret erased from the page.");
  });
  $("inv-btn").addEventListener("click", loadInventory);
  document.querySelectorAll("#round-form [data-op]").forEach(function (b) {
    b.addEventListener("click", function () { roundOp(b.dataset.op); });
  });
  $("r-state").addEventListener("click", viewState);
  $("r-hist").addEventListener("click", viewHistory);
  $("wallet-form").addEventListener("submit", viewWallet);
  $("audit-btn").addEventListener("click", loadAudit);
  $("wh-btn").addEventListener("click", loadWebhooks);
  window.addEventListener("beforeunload", function () { KEY = ""; eraseSecret(); });
})();
