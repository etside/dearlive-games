(function () {
  "use strict";
  var tokenKey = "operator_token";
  var app = document.getElementById("app");
  var logout = document.getElementById("logout");
  var path = window.location.pathname.replace(/\/+$/, "") || "/admin/dashboard";
  var endpoints = {
    "/admin/dashboard": "/api/v1/operator/admin/dashboard",
    "/admin/economy/currency": "/api/v1/operator/admin/currency",
    "/admin/economy/coin-config": "/api/v1/operator/admin/coin-config",
    "/admin/economy/wallets": "/api/v1/operator/admin/wallets",
    "/admin/economy/players": "/api/v1/operator/admin/players",
    "/admin/economy/transactions": "/api/v1/operator/admin/transactions",
    "/admin/audit": "/api/v1/operator/admin/audit"
  };
  function token() { return localStorage.getItem(tokenKey); }
  function validToken(value) {
    if (!value) return false;
    try { return JSON.parse(atob(value.split(".")[1])).exp * 1000 > Date.now(); }
    catch (_) { return false; }
  }
  function state(kind, message, retry) {
    app.innerHTML = "<section class='state " + kind + "'><p>" + message + "</p>" + (retry ? "<button data-retry>Retry</button>" : "") + "</section>";
    if (retry) app.querySelector("[data-retry]").addEventListener("click", load);
  }
  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) { return ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"})[c]; });
  }
  function render(data) {
    if (data == null || (Array.isArray(data) && !data.length) || (data && Object.keys(data).length === 0)) {
      state("empty", "No records yet.", false); return;
    }
    var text = esc(JSON.stringify(data, null, 2));
    app.innerHTML = "<section class='panel'><pre>" + text + "</pre></section>";
  }
  function request(endpoint) {
    return fetch(endpoint, { headers: { Authorization: "Bearer " + token() } }).then(function (response) {
      return response.text().then(function (text) {
        var body = {}; try { body = JSON.parse(text || "{}"); } catch (_) { body = {}; }
        if (!response.ok) throw new Error(body.message || "Request failed");
        return body.data;
      });
    });
  }
  function login(event) {
    event.preventDefault();
    var pin = document.getElementById("pin").value;
    fetch("/api/v1/operator/auth", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ pin: pin }) }).then(function (response) {
      return response.json().then(function (body) {
        if (!response.ok) throw new Error(body.message || "PIN authentication failed");
        localStorage.setItem(tokenKey, body.operator_token); window.location.href = "/admin/dashboard";
      });
    }).catch(function (error) { state("error", error.message, false); });
  }
  function load() {
    if (path === "/admin/login") {
      logout.hidden = true;
      app.innerHTML = "<section class='panel login'><h1>Operator sign in</h1><p>PIN-only access. No account signup.</p><form id='login-form'><label>Operator PIN<input id='pin' type='password' autocomplete='current-password' required></label><button type='submit'>Continue</button></form></section>";
      document.getElementById("login-form").addEventListener("submit", login); return;
    }
    if (!validToken(token())) { localStorage.removeItem(tokenKey); window.location.href = "/admin/login"; return; }
    logout.hidden = false;
    if (!endpoints[path]) { state("empty", "This section has no records configured.", false); return; }
    state("loading", "Loading…", false);
    request(endpoints[path]).then(render).catch(function (error) { state("error", error.message, true); });
  }
  logout.addEventListener("click", function () { localStorage.removeItem(tokenKey); window.location.href = "/admin/login"; });
  load();
})();
