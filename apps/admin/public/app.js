/* Teen Patti Pro operator console.
 *
 * Plain ES5-compatible browser JavaScript, no build step and no framework: the
 * package is meant to be served straight off the game process, so anything
 * requiring bundling would be one more thing between the client and a working
 * console.
 *
 * Every section maps to a real endpoint under /api/v1/admin. Where the SRS
 * names a capability with no endpoint behind it (Reports), the section says so
 * in the UI rather than rendering a plausible-looking empty chart.
 */
(function () {
  "use strict";

  var GAME = "teen-patti-pro";
  var KEY_STORE = "tpp.admin.key";
  var app = document.getElementById("app");

  // ---------------------------------------------------------------- helpers

  function h(tag, attrs, kids) {
    var el = document.createElement(tag);
    // Children may arrive as a single node, a string, or nothing. This
    // crashed the whole console on "(kids || []).forEach is not a function"
    // the first time a caller passed a bare string, and a rendering helper
    // that takes a whole page down over one argument shape is not worth the
    // brevity.
    if (kids !== undefined && kids !== null && !Array.isArray(kids)) {
      kids = [kids];
    }
    // h("p", "text") is a natural thing to write; treat a non-object second
    // argument as children rather than as an attribute bag.
    if (attrs !== undefined && attrs !== null
        && (typeof attrs !== "object" || Array.isArray(attrs)
            || attrs instanceof Node)) {
      kids = kids === undefined ? [attrs] : [attrs].concat(kids);
      attrs = null;
    }
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        if (k === "class") el.className = attrs[k];
        else if (k === "text") el.textContent = attrs[k];
        else if (k === "html") el.innerHTML = attrs[k];
        else if (k.slice(0, 2) === "on") el.addEventListener(k.slice(2), attrs[k]);
        else if (attrs[k] !== null && attrs[k] !== undefined) el.setAttribute(k, attrs[k]);
      });
    }
    (kids || []).forEach(function (kid) {
      if (kid === null || kid === undefined || kid === false) return;
      el.appendChild(typeof kid === "string" ? document.createTextNode(kid) : kid);
    });
    return el;
  }

  function apiKey() { return sessionStorage.getItem(KEY_STORE) || ""; }

  function setKey(v) {
    if (v) sessionStorage.setItem(KEY_STORE, v);
    else sessionStorage.removeItem(KEY_STORE);
  }

  /* The API answers {success, code, message, data}. A 503 with no database is
   * the expected state of a fresh install, so it is called out by name rather
   * than shown as a generic failure. */
  function request(method, path, body) {
    var opts = { method: method, headers: { "X-Admin-Key": apiKey() } };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    return fetch("/api/v1" + path, opts).then(function (res) {
      return res.text().then(function (text) {
        var payload = {};
        try { payload = JSON.parse(text || "{}"); } catch (e) { payload = {}; }
        if (!res.ok || payload.success === false) {
          var err = new Error(payload.message || ("HTTP " + res.status));
          err.status = res.status;
          err.code = payload.code;
          throw err;
        }
        return payload.data;
      });
    });
  }

  function toast(message, kind) {
    var box = h("div", { class: "toast " + (kind || ""), role: "status",
                         text: message });
    document.body.appendChild(box);
    setTimeout(function () { box.remove(); }, 6000);
  }

  function stateNode(kind, message, retry) {
    var kids = [h("p", { class: "msg", text: message })];
    if (retry) kids.push(h("button", { onclick: retry, text: "Retry" }));
    return h("section", { class: "state " + kind }, kids);
  }

  function loading(label) {
    return stateNode("", (label || "Loading") + "…", null);
  }

  function fmt(v) {
    if (v === null || v === undefined) return "—";
    if (typeof v === "number") return v.toLocaleString();
    if (typeof v === "boolean") return v ? "yes" : "no";
    return String(v);
  }

  function table(cols, rows, emptyText) {
    if (!rows || !rows.length) return stateNode("", emptyText || "Nothing here yet.", null);
    var head = h("tr", null, cols.map(function (c) { return h("th", { text: c }); }));
    var body = rows.map(function (row) {
      return h("tr", null, cols.map(function (c) {
        var v = typeof c === "function" ? c(row) : row[c];
        if (v === null || v === undefined) v = "—";
        // A cell may be a DOM node, so a column can hold a button. String(v)
        // on a node is "[object HTMLButtonElement]", which is how action
        // columns used to render as literal that text.
        if (v instanceof Node) return h("td", { class: "actions" }, v);
        return h("td", { class: typeof v === "number" ? "mono" : null,
                         text: String(v) });
      }));
    });
    return h("div", { class: "tablewrap" },
             h("table", null, [h("thead", null, [head]), h("tbody", null, body)]));
  }

  /* Clipboard with a visible confirmation. navigator.clipboard is unavailable
   * on http:// and in older WebViews, so there is a textarea fallback rather
   * than a button that silently does nothing. */
  function copyText(value, button) {
    var original = button ? button.textContent : null;
    function done(ok) {
      if (!button) return;
      button.textContent = ok ? "Copied" : "Copy failed";
      setTimeout(function () { button.textContent = original; }, 1400);
    }
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(value).then(function () { done(true); },
                                                 function () { done(false); });
      return;
    }
    try {
      var ta = document.createElement("textarea");
      ta.value = value;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      done(document.execCommand("copy"));
      document.body.removeChild(ta);
    } catch (e) {
      done(false);
    }
  }

  function copyButton(value, label) {
    var btn = h("button", { class: "ghost", text: label || "Copy" });
    btn.addEventListener("click", function () { copyText(value, btn); });
    return btn;
  }

  function kpi(label, value) {
    return h("div", { class: "kpi" },
             [h("b", { text: fmt(value) }), h("span", { text: label })]);
  }

  /* Postgres timestamptz arrives as a Date from JSON, or as a string depending
   * on the driver. Show whichever, but never "Invalid Date". */
  function when(v) {
    if (!v) return "—";
    var d = new Date(v);
    return isNaN(d.getTime()) ? String(v) : d.toLocaleString();
  }

  // --------------------------------------------------------------- sections
  //
  // Each returns a DOM node. `mount` is called after the node is in the
  // document, so a section can render a shell then fill it.

  var SECTIONS = {};

  SECTIONS.dashboard = {
    label: "Dashboard",
    build: function (mount) {
      request("GET", "/admin/dashboard").then(function (d) {
        var k = d.kpis || {};
        mount.node.innerHTML = "";
        mount.node.appendChild(h("div", { class: "kpis" }, [
          kpi("Rounds", k.rounds),
          kpi("Bets", k.bets),
          kpi("Players", k.players),
          kpi("Wagered", k.wagered),
          kpi("Paid out", k.paid_out),
          kpi("Net", k.net)
        ]));
        if (k.settlement_health && k.settlement_health.available === false) {
          mount.node.appendChild(h("div", { class: "note bad" },
            ["Settlement health is unavailable. This is expected when no " +
             "database is configured; the numbers above are from memory only."]));
        }
        mount.node.appendChild(h("h2", { text: "Raw" }));
        mount.node.appendChild(pre(JSON.stringify(k, null, 2)));
      }).catch(function (e) { mount.fail(e); });
    }
  };

  SECTIONS.risk = {
    label: "Profit & Risk",
    build: function (mount) {
      request("GET", "/admin/profit-risk").then(function (d) {
        var active = d.active;
        mount.node.innerHTML = "";
        if (!active) {
          mount.node.appendChild(stateNode("",
            "No profit/risk configuration stored yet. Set the values below and save."));
        } else {
          mount.node.appendChild(h("div", { class: "card" }, [
            h("h3", { text: "Active configuration (v" + fmt(active.version) + ")" }),
            table(["Setting", "Value"], [
              { k: "Base house edge (%)", v: active.base_house_edge_pct },
              { k: "VIP adjustment (%)", v: active.vip_profit_adj_pct },
              { k: "Max payout / round", v: active.max_payout_per_round },
              { k: "RNG weight", v: active.rng_weight },
              { k: "Max daily loss / player", v: active.max_daily_loss_per_player }
            ], null)
          ]));
        }
        mount.node.appendChild(riskForm(active || {}));
        mount.node.appendChild(h("h2", { text: "Simulate" }));
        mount.node.appendChild(simulator());
      }).catch(function (e) { mount.fail(e); });
    }
  };

  function riskForm(v) {
    var fields = [
      ["base_house_edge_pct", "Base house edge (%)", "8.0"],
      ["vip_profit_adj_pct", "VIP adjustment (%)", "1.5"],
      ["max_payout_per_round", "Max payout per round", "10000"],
      ["rng_weight", "Jackpot weight", "1.0"],
      ["max_daily_loss_per_player", "Max daily loss / player", "500"]
    ];
    var inputs = {};
    var grid = h("div", { class: "grid" }, fields.map(function (f) {
      inputs[f[0]] = h("input", { type: "number", step: "any",
                                  value: v[f[0]] !== undefined ? v[f[0]] : f[2] });
      return h("div", { class: "field" }, [h("label", { text: f[1] }), inputs[f[0]]]);
    }));
    var out = h("div");
    return h("div", { class: "card" }, [
      h("h3", { text: "Configuration" }),
      grid,
      h("p", { class: "sub", text:
        "Saving creates a new version. A round already in play keeps the " +
        "version it started with." }),
      h("button", { class: "primary", text: "Save new version", onclick: function () {
        var body = {};
        fields.forEach(function (f) {
          var n = parseFloat(inputs[f[0]].value);
          if (!isNaN(n)) body[f[0]] = n;
        });
        request("PUT", "/admin/profit-risk", body).then(function (d) {
          toast("Saved version " + fmt(d.version), "ok");
        }).catch(function (e) { toast(e.message, "bad"); });
      } }),
      out
    ]);
  }

  function simulator() {
    var rounds = h("input", { type: "number", value: "10000", min: "1" });
    var edge = h("input", { type: "number", step: "any", placeholder: "use saved" });
    var out = h("div");
    return h("div", { class: "card" }, [
      h("div", { class: "row" }, [
        h("div", { class: "field", style: "width:150px" },
          [h("label", { text: "Rounds" }), rounds]),
        h("div", { class: "field", style: "width:150px" },
          [h("label", { text: "House edge override" }), edge])
      ]),
      h("button", { text: "Run simulation", onclick: function () {
        var body = { rounds: parseInt(rounds.value, 10) || 10000 };
        var e = parseFloat(edge.value);
        if (!isNaN(e)) body.base_house_edge_pct = e;
        out.innerHTML = "";
        out.appendChild(loading("Simulating"));
        request("POST", "/admin/profit-risk/simulate", body).then(function (d) {
          out.innerHTML = "";
          out.appendChild(h("div", { class: "note" },
            ["This is a seeded model over your parameters, not a measurement. " +
             "Treat it as a sanity check on a direction."]));
          out.appendChild(h("div", { class: "kpis" }, [
            kpi("Expected profit", d.expected_profit),
            kpi("ROI %", d.roi_pct),
            kpi("Max exposure", d.max_exposure),
            kpi("Risk", d.risk_level)
          ]));
        }).catch(function (err) { out.innerHTML = ""; out.appendChild(stateNode("bad", err.message)); });
      } }),
      out
    ]);
  }

  SECTIONS.players = {
    label: "Player Override",
    build: function (mount) {
      var id = h("input", { placeholder: "player id" });
      var out = h("div");
      function load() {
        out.innerHTML = "";
        out.appendChild(loading());
        request("GET", "/admin/players?limit=100").then(function (d) {
          out.innerHTML = "";
          out.appendChild(table(
            ["player_id", "override_id", "house_edge_pct", "token_delta",
             "reason", "expires_at"],
            d.players || [],
            "No overrides in place."));
        }).catch(function (e) { out.innerHTML = ""; out.appendChild(stateNode("bad", e.message)); });
      }
      mount.node.appendChild(h("div", { class: "card" }, [
        h("h3", { text: "Create or replace an override" }),
        h("div", { class: "row" }, [
          h("div", { class: "field", style: "flex:1;min-width:180px" },
            [h("label", { text: "Player id" }), id])
        ]),
        overrideForm(function () { load(); }),
        h("hr", { style: "border-color:var(--line);margin:16px 0" }),
        h("button", { onclick: load, text: "List overrides" }),
        out
      ]));
      load();
    }
  };

  function overrideForm(done) {
    var pid = h("input", { placeholder: "player id" });
    var edge = h("input", { type: "number", step: "any", placeholder: "inherit" });
    var delta = h("input", { type: "number", value: "0" });
    var loss = h("input", { type: "number", placeholder: "inherit" });
    var reason = h("input", { placeholder: "required — this is audited" });
    var exp = h("input", { placeholder: "ISO timestamp (optional)" });
    function submit() {
      if (!pid.value.trim()) return toast("Player id is required", "bad");
      if (!reason.value.trim()) {
        return toast("A reason is required — it goes in the audit trail", "bad");
      }
      var body = { reason: reason.value.trim() };
      if (edge.value !== "") body.house_edge_pct = parseFloat(edge.value);
      if (loss.value !== "") body.custom_loss_limit = parseFloat(loss.value);
      body.token_delta = parseInt(delta.value, 10) || 0;
      if (exp.value.trim()) body.expires_at = exp.value.trim();
      request("POST", "/admin/players/" + encodeURIComponent(pid.value.trim()) + "/override", body)
        .then(function () { toast("Override saved", "ok"); if (done) done(); })
        .catch(function (e) { toast(e.message, "bad"); });
    }
    return h("div", null, [
      h("div", { class: "grid" }, [
        h("div", { class: "field" }, [h("label", { text: "Player id" }), pid]),
        h("div", { class: "field" }, [h("label", { text: "House edge %" }), edge]),
        h("div", { class: " field" }, [h("label", { text: "Token delta" }), delta]),
        h("div", { class: "field" }, [h("label", { text: "Daily loss limit" }), loss]),
        h("div", { class: "field" }, [h("label", { text: "Expires at" }), exp])
      ]),
      h("div", { class: "field" }, [h("label", { text: "Reason (required)" }), reason]),
      h("div", { class: "row" }, [
        h("button", { class: "primary", text: "Save override", onclick: submit }),
        h("button", { class: "danger", text: "Revoke", onclick: function () {
          if (!pid.value.trim()) return toast("Player id is required", "bad");
          request("DELETE", "/admin/players/" + encodeURIComponent(pid.value.trim()) + "/override")
            .then(function (d) {
              toast(d.revoked ? "Override revoked" : "No live override to revoke", "ok");
              if (done) done();
            }).catch(function (e) { toast(e.message, "bad"); });
        } })
      ])
    ]);
  }

  SECTIONS.packages = {
    label: "Token Packages",
    build: function (mount) {
      var out = h("div");
      function load() {
        out.innerHTML = "";
        out.appendChild(loading());
        request("GET", "/admin/packages").then(function (d) {
          out.innerHTML = "";
          out.appendChild(table(
            ["package_id", "name", "coins", "price_minor", "currency",
             "bonus_percent", "is_active", ""],
            (d.packages || []).map(function (p) {
              p.__id = p.package_id;
              return p;
            }),
            "No packages defined."));
          var rows = out.querySelectorAll("tbody tr");
          Array.prototype.forEach.call(rows, function (tr, i) {
            var pkg = (d.packages || [])[i];
            if (!pkg) return;
            var cell = tr.lastChild;
            cell.appendChild(h("button", { text: "Archive", onclick: function () {
              request("DELETE", "/admin/packages/" + encodeURIComponent(pkg.package_id))
                .then(function () { toast("Archived", "ok"); load(); })
                .catch(function (e) { toast(e.message, "bad"); });
            } }));
          });
        }).catch(function (e) { out.innerHTML = ""; out.appendChild(stateNode("bad", e.message)); });
      }
      var name = h("input", { placeholder: "Starter pack" });
      var coins = h("input", { type: "number", value: "500" });
      var price = h("input", { type: "number", value: "499" });
      var cur = h("input", { value: "USD" });
      var bonus = h("input", { type: "number", value: "0" });
      mount.node.appendChild(h("div", { class: "card" }, [
        h("h3", { text: "New package" }),
        h("div", { class: "grid" }, [
          h("div", { class: "field" }, [h("label", { text: "Name" }), name]),
          h("div", { class: "field" }, [h("label", { text: "Coins" }), coins]),
          h("div", { class: "field" }, [h("label", { text: "Price (minor)" }), price]),
          h("div", { class: "field" }, [h("label", { text: "Currency" }), cur]),
          h("div", { class: "field" }, [h("label", { text: "Bonus %" }), bonus])
        ]),
        h("button", { class: "primary", text: "Create", onclick: function () {
          if (!name.value.trim()) return toast("Name is required", "bad");
          request("POST", "/admin/packages", {
            name: name.value.trim(),
            coins: parseInt(coins.value, 10) || 0,
            price_minor: parseInt(price.value, 10) || 0,
            currency: cur.value.trim() || "USD",
            bonus_percent: parseInt(bonus.value, 10) || 0
          }).then(function () { toast("Package created", "ok"); load(); })
            .catch(function (e) { toast(e.message, "bad"); });
        } }),
        h("hr", { style: "border-color:var(--line);margin:16px 0" }),
        h("button", { onclick: load, text: "Refresh" }),
        out
      ]));
      load();
    }
  };

  SECTIONS.rules = {
    label: "Game Rules",
    build: function (mount) {
      request("GET", "/admin/games/" + GAME + "/rules").then(function (d) {
        mount.node.innerHTML = "";
        var cur = d.rules || {};
        mount.node.appendChild(h("div", { class: "card" }, [
          h("h3", { text: "Current version: " + (d.version || "none stored") }),
          h("p", { class: "sub", text:
            "Confirmed: " + fmt(d.confirmed) + (d.tbc && d.tbc.length
              ? " · TBC: " + d.tbc.join(", ") : "") })
        ]));
        var area = h("textarea", { rows: "14",
          style: "font-family:ui-monospace,Menlo,monospace;font-size:.85rem" });
        area.value = JSON.stringify(cur, null, 2);
        mount.node.appendChild(h("div", { class: "card" }, [
          h("h3", { text: "Rules" }),
          h("p", { class: "sub", text:
            "JSON. Saving creates a new version; rounds in play keep theirs." }),
          area,
          h("div", { class: "row", style: "margin-top:10px" }, [
            h("button", { class: "primary", text: "Save new version", onclick: function () {
              var parsed;
              try { parsed = JSON.parse(area.value); }
              catch (e) { return toast("That is not valid JSON: " + e.message, "bad"); }
              if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
                return toast("Rules must be a JSON object", "bad");
              }
              request("PUT", "/admin/games/" + GAME + "/rules", parsed)
                .then(function () { toast("Rules saved", "ok"); })
                .catch(function (e) { toast(e.message, "bad"); });
            } }),
            h("button", { text: "Reload", onclick: function () { route(); } })
          ])
        ]));
        if (d.versions && d.versions.length) {
          mount.node.appendChild(h("h2", { text: "Version history" }));
          mount.node.appendChild(table(["version", "confirmed", "created_at"],
                                       d.versions));
        }
      }).catch(function (e) { mount.fail(e); });
    }
  };

  SECTIONS.toggle = {
    label: "Enable / Disable",
    build: function (mount) {
      var status = h("div");
      function refresh() {
        request("GET", "/admin/games").then(function (d) {
          var games = d.games || (Array.isArray(d) ? d : []);
          status.innerHTML = "";
          status.appendChild(table(["game_id", "name", "status", ""], games,
            "No games reported."));
        }).catch(function (e) {
          status.innerHTML = "";
          status.appendChild(stateNode("bad", e.message));
        });
      }
      function act(action) {
        return function () {
          request("POST", "/admin/games/" + GAME + "/" + action, {})
            .then(function (d) {
              toast(GAME + " " + (d.enabled ? "enabled" : "disabled"), "ok");
              refresh();
            }).catch(function (e) { toast(e.message, "bad"); });
        };
      }
      mount.node.appendChild(h("div", { class: "card" }, [
        h("h3", { text: GAME }),
        h("p", { class: "sub", text:
          "Disabling stops new sessions. Rounds already in flight settle " +
          "normally — a live pot is never pulled." }),
        h("div", { class: "row" }, [
          h("button", { class: "primary", text: "Enable", onclick: act("enable") }),
          h("button", { class: "danger", text: "Disable", onclick: act("disable") })
        ])
      ]));
      mount.node.appendChild(h("h2", { text: "Catalog" }));
      mount.node.appendChild(status);
      refresh();
    }
  };

  SECTIONS.audit = {
    label: "Audit Log",
    build: function (mount) {
      var out = h("div");
      function load() {
        out.innerHTML = "";
        out.appendChild(loading());
        request("GET", "/admin/audit?limit=200").then(function (d) {
          var rows = d.entries || [];
          out.innerHTML = "";
          out.appendChild(h("div", { class: "row", style: "margin-bottom:10px" }, [
            h("button", { text: "Refresh", onclick: load }),
            h("button", { text: "Export CSV", onclick: function () { exportCsv(rows); } })
          ]));
          out.appendChild(table(
            ["at", "actor", "action", "entity", "entity_id", "before", "after"],
            rows.map(function (r) {
              return {
                at: when(r.at || r.created_at || r.ts),
                actor: r.actor, action: r.action, entity: r.entity,
                entity_id: r.entity_id,
                before: brief(r.before), after: brief(r.after)
              };
            }),
            "No audit entries yet."));
        }).catch(function (e) { out.innerHTML = ""; out.appendChild(stateNode("bad", e.message)); });
      }
      mount.node.appendChild(h("p", { class: "sub", text:
        "Append-only. Corrections are made with compensating entries, never " +
        "by editing a row." }));
      mount.node.appendChild(out);
      load();
    }
  };

  function brief(v) {
    if (v === null || v === undefined) return "—";
    if (typeof v === "object") {
      var s = JSON.stringify(v);
      return s.length > 70 ? s.slice(0, 70) + "…" : s;
    }
    return String(v);
  }

  function exportCsv(rows) {
    if (!rows || !rows.length) return toast("Nothing to export", "bad");
    var cols = ["at", "actor", "action", "entity", "entity_id", "before", "after"];
    var esc = function (v) {
      var s = v === null || v === undefined ? "" : String(v);
      return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
    };
    var lines = [cols.join(",")];
    rows.forEach(function (r) {
      lines.push(cols.map(function (c) { return esc(r[c]); }).join(","));
    });
    var blob = new Blob([lines.join("\n")], { type: "text/csv" });
    var a = h("a", { href: URL.createObjectURL(blob), download: "audit.csv" });
    document.body.appendChild(a);
    a.click();
    a.remove();
    toast("Exported " + rows.length + " rows", "ok");
  }

  SECTIONS.reports = {
    label: "Reports",
    build: function (mount) {
      var out = h("div");
      out.appendChild(loading());
      request("GET", "/admin/settlement-health").then(function (d) {
        out.innerHTML = "";
        out.appendChild(h("div", { class: "note" },
          ["The SRS names a Reports section but no reports endpoint. What is " +
           "shown here is settlement health, which is the closest available " +
           "signal. A dedicated reporting query is not implemented."]));
        out.appendChild(h("div", { class: "kpis" }, [
          kpi("Pending settlements", d.pending),
          kpi("Failed settlements", d.failed),
          kpi("Unpaid winnings", d.unpaid),
          kpi("Retries queued", d.retries)
        ]));
        if (d.available === false || d.available === undefined) {
          out.appendChild(h("div", { class: "note bad" },
            ["Settlement health is unavailable. Expected with no database " +
             "configured."]));
        }
      }).catch(function (e) {
        out.innerHTML = "";
        out.appendChild(stateNode("bad", e.message));
      });
      mount.node.appendChild(out);
    }
  };

  SECTIONS.settings = {
    label: "Settings",
    build: function (mount) {
      request("GET", "/admin/settings").then(function (d) {
        var cur = d.settings || {};
        mount.node.innerHTML = "";
        var area = h("textarea", { rows: "14",
          style: "font-family:ui-monospace,Menlo,monospace;font-size:.85rem" });
        area.value = JSON.stringify(cur, null, 2);
        mount.node.appendChild(h("div", { class: "card" }, [
          h("h3", { text: "Platform settings" }),
          h("p", { class: "sub", text: "Key/value, written as a whole object." }),
          area,
          h("div", { class: "row", style: "margin-top:10px" }, [
            h("button", { class: "primary", text: "Save", onclick: function () {
              var parsed;
              try { parsed = JSON.parse(area.value); }
              catch (e) { return toast("That is not valid JSON: " + e.message, "bad"); }
              if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
                return toast("Settings must be a JSON object", "bad");
              }
              request("PUT", "/admin/settings", parsed)
                .then(function () { toast("Settings saved", "ok"); })
                .catch(function (e) { toast(e.message, "bad"); });
            } }),
            h("button", { text: "Reload", onclick: function () { route(); } })
          ])
        ]));
      }).catch(function (e) { mount.fail(e); });
    }
  };

  SECTIONS.scheduled = {
    label: "Scheduled",
    build: function (mount) {
      var out = h("div");
      function load() {
        out.innerHTML = "";
        out.appendChild(loading());
        request("GET", "/admin/scheduled-changes?limit=50").then(function (d) {
          out.innerHTML = "";
          var rows = d.changes || [];
          out.appendChild(h("div", { class: "row", style: "margin-bottom:10px" }, [
            h("button", { text: "Refresh", onclick: load }),
            h("button", { text: "Apply due now", onclick: function () {
              request("POST", "/admin/scheduled-changes/apply", {})
                .then(function (d) {
                  var r = d.report || {};
                  toast("Checked " + fmt(r.checked) + ", applied " +
                        fmt((r.applied || []).length) + ", failed " +
                        fmt((r.failed || []).length),
                        (r.failed || []).length ? "bad" : "ok");
                  load();
                }).catch(function (e) { toast(e.message, "bad"); });
            } })
          ]));
          out.appendChild(table(
            ["change_id", "target_type", "target_id", "effective_at", "status",
             "reason", ""],
            rows.map(function (c) {
              c.__id = c.change_id;
              return c;
            }),
            "Nothing scheduled."));
          var trs = out.querySelectorAll("tbody tr");
          Array.prototype.forEach.call(trs, function (tr, i) {
            var c = rows[i];
            if (!c) return;
            if (c.status === "PENDING") {
              tr.lastChild.appendChild(h("button", { text: "Cancel", onclick: function () {
                request("DELETE", "/admin/scheduled-changes/" + encodeURIComponent(c.change_id))
                  .then(function () { toast("Cancelled", "ok"); load(); })
                  .catch(function (e) { toast(e.message, "bad"); });
              } }));
            }
          });
        }).catch(function (e) { out.innerHTML = ""; out.appendChild(stateNode("bad", e.message)); });
      }
      mount.node.appendChild(h("p", { class: "sub", text:
        "Applied automatically every 60s, or on demand. A change that fails is " +
        "marked FAILED and is not retried — a bad payload fails identically " +
        "forever." }));
      mount.node.appendChild(scheduleForm(load));
      mount.node.appendChild(out);
      load();
    }
  };

  function scheduleForm(done) {
    var type = h("select", null, ["profit_risk", "game_config", "settings", "package"]
      .map(function (t) { return h("option", { value: t, text: t }); }));
    var target = h("input", { placeholder: "target id (blank for settings)" });
    var when_ = h("input", { placeholder: "2026-10-01T02:00:00+00:00" });
    var payload = h("textarea", { rows: "5",
      style: "font-family:ui-monospace,Menlo,monospace;font-size:.85rem" });
    payload.value = "{}";
    var reason = h("input", { placeholder: "why" });
    return h("div", { class: "card" }, [
      h("h3", { text: "Schedule a change" }),
      h("div", { class: "grid" }, [
        h("div", { class: "field" }, [h("label", { text: "Target type" }), type]),
        h("div", { class: "field" }, [h("label", { text: "Target id" }), target]),
        h("div", { class: "field" }, [h("label", { text: "Effective at (ISO)" }), when_])
      ]),
      h("div", { class: "field" }, [h("label", { text: "Payload (JSON)" }), payload]),
      h("div", { class: "field" }, [h("label", { text: "Reason" }), reason]),
      h("button", { class: "primary", text: "Schedule", onclick: function () {
        var parsed;
        try { parsed = JSON.parse(payload.value || "{}"); }
        catch (e) { return toast("Payload is not valid JSON: " + e.message, "bad"); }
        if (!when_.value.trim()) return toast("Effective-at is required", "bad");
        request("POST", "/admin/scheduled-changes", {
          target_type: type.value,
          target_id: target.value.trim(),
          payload: parsed,
          effective_at: when_.value.trim(),
          reason: reason.value.trim()
        }).then(function () { toast("Scheduled", "ok"); if (done) done(); })
          .catch(function (e) { toast(e.message, "bad"); });
      } })
    ]);
  }

  // ------------------------------------------------------------------ shell

  function pre(text) {
    return h("pre", { class: "card mono", style: "overflow:auto;max-height:340px",
                      text: text });
  }

  function signIn() {
    var input = h("input", { type: "password", placeholder: "X-Admin-Key",
                             autofocus: "autofocus" });
    function go() {
      var v = input.value.trim();
      if (!v) return;
      setKey(v);
      request("GET", "/admin/whoami").then(function (d) {
        route();
        toast("Signed in as " + (d.role || "admin"), "ok");
      }).catch(function (e) {
        setKey("");
        toast(e.message, "bad");
      });
    }
    input.addEventListener("keydown", function (e) { if (e.key === "Enter") go(); });
    app.innerHTML = "";
    app.appendChild(h("div", { style: "margin:auto;width:min(420px,92vw)" }, [
      h("div", { class: "card" }, [
        h("h1", { text: "Operator sign in" }),
        h("p", { class: "sub", text:
          "Paste an admin key. It is held in sessionStorage and sent as the " +
          "X-Admin-Key header." }),
        h("div", { class: "field" }, [h("label", { text: "Admin key" }), input]),
        h("button", { class: "primary", text: "Continue", onclick: go })
      ])
    ]));
  }


  /* ------------------------------------------------------------ rooms ----
   * A room is a table an operator opens and hands out links to. The join URL
   * is minted here and is the thing people actually share, so it is a
   * first-class column with a copy button rather than something to read off a
   * JSON blob.
   */
  SECTIONS.rooms = {
    label: "Rooms",
    build: function (mount) {
      var refresh = function () {
        mount.node.innerHTML = "";
        mount.node.appendChild(h("div", { class: "card" }, [
          h("h3", { text: "Create a room" }),
          varField("name", "Room name", "e.g. high-stakes"),
          varField("currency", "Currency", "COIN"),
          varField("chip_denoms", "Chip denominations",
                   "1000, 10000, 50000, 100000"),
          varField("betting_duration_sec", "Betting window (seconds)", "30"),
          h("button", { text: "Create room", onclick: function (ev) {
            var name = ev.target.form.elements.name.value.trim();
            if (!name) { toast("A room needs a name.", "bad"); return; }
            var denoms = ev.target.form.elements.chip_denoms.value
              .split(",").map(function (x) { return parseInt(x, 10); })
              .filter(function (n) { return n > 0; });
            request("POST", "/admin/rooms", {
              name: name,
              currency: ev.target.form.elements.currency.value.trim() || "COIN",
              chip_denoms: denoms.length ? denoms : undefined,
              betting_duration_sec: parseInt(
                ev.target.form.elements.betting_duration_sec.value, 10) || 30
            }).then(function () { refresh(); })
              .catch(function (e) { toast(e.message, "bad"); });
          } })
        ]));
        mount.node.appendChild(h("div", { class: "card" }, [
          h("h3", { text: "Rooms" }),
          h("p", { class: "sub", text:
            "Live state. Rooms reset when the service restarts; a link minted "
            + "for a room that no longer exists will not seat anyone." }),
          h("div", { class: "load" }, loadRooms(refresh))
        ]));
      };
      refresh();
    }
  };

  function varField(name, label, placeholder) {
    return h("label", { class: "field" }, [
      h("span", { text: label }),
      h("input", { name: name, placeholder: placeholder || "", required: false })
    ]);
  }

  function loadRooms(refresh) {
    return request("GET", "/admin/rooms").then(function (d) {
      var rooms = d.rooms || [];
      return table(
        ["name", "room_id", "active", "created", ""],
        rooms.map(function (r) {
          return {
            name: r.name,
            room_id: r.room_id,
            active: r.players_count,
            created: when(r.created_at),
            _r: r
          };
        }).map(function (row) {
          var del = h("button", { class: "ghost", text: "Delete" });
          del.addEventListener("click", function () {
            if (!window.confirm("Delete room " + row.room_id + "?")) return;
            request("DELETE", "/admin/rooms/" + encodeURIComponent(row.room_id))
              .then(refresh)
              .catch(function (e) { toast(e.message, "bad"); });
          });
          var actions = h("span", { class: "row-actions" }, [del]);
          if (row._r.join_url) actions.appendChild(
            copyButton(row._r.join_url, "Copy join URL"));
          return h("tr", null, [
            h("td", { text: String(row.name) }),
            h("td", { class: "mono", text: String(row.room_id) }),
            h("td", { class: "mono", text: String(row.active) }),
            h("td", { text: row.created }),
            h("td", { class: "actions" }, actions)
          ]);
        }),
        "No rooms yet. Create one to get started.");
    });
  }

  /* --------------------------------------------------------- sessions ----
   * Session links are how a player is admitted to a table. The URL is the
   * deliverable, so it is shown in full with a copy button, alongside when it
   * stops working -- a link nobody can date is a support ticket later.
   */
  SECTIONS.sessions = {
    label: "Sessions",
    build: function (mount) {
      var refresh = function () {
        mount.node.innerHTML = "";
        mount.node.appendChild(h("div", { class: "card" }, [
          h("h3", { text: "Mint a session link" }),
          varField("player_id", "Player id", "e.g. test_player_1"),
          varField("room", "Room", "teen-patti-low"),
          h("button", { text: "Generate URL", onclick: function (ev) {
            var f = ev.target.form.elements;
            var pid = f.player_id.value.trim();
            if (!pid) { toast("A player id is required.", "bad"); return; }
            request("POST", "/admin/sessions/mint",
                    { player_id: pid, room: f.room.value.trim() || undefined })
              .then(function (d) {
                toast("Minted for " + d.player_id + " — expires "
                      + when(d.expires_at), "ok");
                refresh();
              })
              .catch(function (e) { toast(e.message, "bad"); });
          } })
        ]));
        mount.node.appendChild(h("div", { class: "card" }, [
          h("h3", { text: "Bulk mint" }),
          h("p", { class: "sub", text:
            "One player id per line, up to 100. The CSV downloads with a "
            + "play_url per row. Minting is rate limited to 100 per hour." }),
          bulkForm(refresh)
        ]));
        mount.node.appendChild(h("div", { class: "card" }, [
          h("h3", { text: "Active sessions" }),
          h("div", { class: "load" }, loadSessions(refresh))
        ]));
      };
      refresh();
    }
  };

  function bulkForm(refresh) {
    var area = h("textarea", { name: "player_ids", rows: "5",
      placeholder: "player_one\nplayer_two\nplayer_three" });
    var room = h("input", { name: "room", placeholder: "teen-patti-low" });
    var run = h("button", { text: "Mint all" });
    run.addEventListener("click", function () {
      var ids = area.value.split("\n").map(function (x) { return x.trim(); })
        .filter(Boolean);
      if (!ids.length) { run.textContent = "Enter at least one player id"; return; }
      if (ids.length > 100) { run.textContent = "Maximum 100 per call"; return; }
      run.disabled = true;
      run.textContent = "Minting…";
      request("POST", "/admin/sessions/bulk",
              { player_ids: ids, room: room.value.trim() || undefined })
        .then(function (d) {
          var rows = d.sessions || [];
          downloadCsv(rows);
          if (d.errors && d.errors.length) {
            run.textContent = rows.length + " minted, "
              + d.errors.length + " failed";
          } else {
            run.textContent = rows.length + " minted — CSV downloaded";
          }
          refresh();
        })
        .catch(function (e) { run.textContent = e.message; })
        .then(function () {
          setTimeout(function () { run.disabled = false; }, 1500);
        });
    });
    return h("form", { class: "form", onsubmit: function (e) { e.preventDefault(); } }, [
      area, room, run
    ]);
  }

  function downloadCsv(rows) {
    // RFC 4180 quoting: a player id with a comma or a quote in it would
    // otherwise produce a CSV that silently shifts every later column.
    var esc = function (v) {
      var s = String(v === null || v === undefined ? "" : v);
      return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
    };
    var lines = ["player_id,room,play_url,expires_at"].concat(
      rows.map(function (r) {
        return [r.player_id, r.room, r.play_url, r.expires_at].map(esc).join(",");
      }));
    var blob = new Blob([lines.join("\r\n") + "\r\n"],
                        { type: "text/csv;charset=utf-8" });
    var a = h("a", { href: URL.createObjectURL(blob),
                     download: "teen-patti-sessions.csv" });
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
  }

  function loadSessions(refresh) {
    return request("GET", "/admin/sessions").then(function (d) {
      var rows = d.sessions || [];
      return table(
        ["player_id", "room", "created", "expires", ""],
        rows.map(function (r) {
          return h("tr", null, [
            h("td", { class: "mono", text: String(r.player_id) }),
            h("td", { class: "mono", text: String(r.room) }),
            h("td", { text: when(r.created_at) }),
            h("td", { text: when(r.expires_at) }),
            h("td", { class: "actions" }, sessionActions(r, refresh))
          ]);
        }),
        "No active sessions.");
    });
  }

  function sessionActions(r, refresh) {
    var wrap = h("span", { class: "row-actions" });
    if (r.play_url) wrap.appendChild(copyButton(r.play_url, "Copy play URL"));
    var revoke = h("button", { class: "ghost", text: "Revoke" });
    revoke.addEventListener("click", function () {
      request("DELETE", "/admin/sessions/"
              + encodeURIComponent(r.session_id))
        .then(refresh)
        .catch(function (e) { revoke.textContent = e.message; });
    });
    wrap.appendChild(revoke);
    return wrap;
  }

  function current() {
    var m = location.pathname.match(/^\/admin\/([a-z]+)/);
    return (m && SECTIONS[m[1]]) ? m[1] : "dashboard";
  }

  function route() {
    if (!apiKey()) return signIn();
    var name = current();
    var sec = SECTIONS[name];
    var node = h("div");
    app.innerHTML = "";
    var nav = h("nav", { class: "nav" }, Object.keys(SECTIONS).map(function (k) {
      return h("button", {
        "aria-current": k === name ? "page" : null,
        text: SECTIONS[k].label,
        onclick: function () {
          history.pushState(null, "", "/admin/" + k);
          route();
        }
      });
    }));
    app.appendChild(h("aside", { class: "side" }, [
      h("div", { class: "brand" }, ["Teen Patti Pro",
        h("small", { text: "operator console" })]),
      nav,
      h("div", { class: "side-foot" }, [
        h("button", { text: "Sign out", onclick: function () {
          setKey(""); signIn();
        } }),
        h("div", { style: "margin-top:8px", text: "One game: " + GAME })
      ])
    ]));
    app.appendChild(h("main", { class: "main" }, [
      h("h1", { text: sec.label }),
      h("p", { class: "sub", text: "SRS section 10" }),
      node
    ]));
    sec.build({
      node: node,
      fail: function (e) {
        node.innerHTML = "";
        var msg = e.message || "Request failed";
        if (e.status === 503) {
          msg = "The admin database is unavailable: " + msg +
                " Apply the migrations and set DATABASE_URL.";
        } else if (e.status === 403) {
          msg = "This key is not permitted: " + msg;
        }
        node.appendChild(stateNode("bad", msg, route));
      }
    });
  }

  window.addEventListener("popstate", route);
  route();
})();
