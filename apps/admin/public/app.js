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
        return h("td", { class: typeof v === "number" ? "mono" : null,
                         text: String(v) });
      }));
    });
    return h("div", { class: "tablewrap" },
             h("table", null, [h("thead", null, [head]), h("tbody", null, body)]));
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
