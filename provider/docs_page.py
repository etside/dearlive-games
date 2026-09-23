"""Self-contained API reference page served at /docs.

Renders the spec fetched from /openapi.json with no external assets, so the
reference works offline (Termux, air-gapped integration hosts).
"""
import html
import json

_CSS = """
:root { color-scheme: light dark; }
body { font: 15px/1.55 ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
       margin: 0; padding: 2rem 1.25rem 4rem; }
main { max-width: 62rem; margin: 0 auto; }
h1 { font-size: 1.6rem; margin: 0 0 .25rem; }
h2 { font-size: 1.05rem; margin: 2rem 0 .5rem; border-bottom: 1px solid #8884; padding-bottom: .3rem; }
p.lead { margin: .25rem 0 1rem; opacity: .8; }
.op { border: 1px solid #8884; border-radius: 8px; padding: .75rem 1rem; margin: .75rem 0; }
.method { display: inline-block; font: 600 12px/1 ui-monospace, monospace; letter-spacing: .04em;
          padding: .35rem .5rem; border-radius: 4px; background: #8883; margin-right: .5rem; }
.path { font: 600 14px ui-monospace, monospace; word-break: break-all; }
.summary { margin: .5rem 0 0; }
.desc { margin: .4rem 0 0; opacity: .85; }
table { border-collapse: collapse; width: 100%; margin-top: .5rem; font-size: 14px; }
th, td { text-align: left; padding: .35rem .5rem; border-bottom: 1px solid #8883; vertical-align: top; }
code { font: 13px ui-monospace, monospace; background: #8882; padding: .1rem .3rem; border-radius: 3px; }
pre { background: #8881; padding: .75rem; border-radius: 6px; overflow-x: auto;
      font: 13px ui-monospace, monospace; }
.muted { opacity: .7; font-size: 13px; }
"""


def _esc(value):
    return html.escape(str(value if value is not None else ""))


def render(spec: dict) -> str:
    info = spec.get("info", {})
    auth = (spec.get("x-provider") or {}).get("authentication", {})
    ws = (spec.get("x-provider") or {}).get("websocket", {})
    blocks = []
    for path, methods in spec.get("paths", {}).items():
        for method, op in methods.items():
            if method not in ("get", "post", "put", "delete", "patch"):
                continue
            responses = "".join(
                f"<tr><td><code>{_esc(code)}</code></td><td>{_esc(desc)}</td></tr>"
                for code, desc in op.get("responses", {}).items())
            params = "".join(
                f"<tr><td><code>{_esc(p.get('name'))}</code></td>"
                f"<td>{_esc(p.get('in'))}</td>"
                f"<td>{'yes' if p.get('required') else 'no'}</td>"
                f"<td>{_esc(p.get('description', ''))}</td></tr>"
                for p in op.get("parameters", []))
            params_html = (f"<table><tr><th>Parameter</th><th>In</th><th>Required</th>"
                           f"<th>Notes</th></tr>{params}</table>") if params else ""
            blocks.append(
                f"<div class='op'><div><span class='method'>{_esc(method.upper())}</span>"
                f"<span class='path'>{_esc(path)}</span></div>"
                f"<p class='summary'>{_esc(op.get('summary', ''))}</p>"
                f"<p class='desc'>{_esc(op.get('description', ''))}</p>"
                f"{params_html}"
                f"<table><tr><th>Response</th><th>Meaning</th></tr>{responses}</table>"
                f"</div>")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(info.get('title', 'API'))}</title>
<style>{_CSS}</style></head>
<body><main>
<h1>{_esc(info.get('title', 'API'))}</h1>
<p class="lead">Version {_esc(info.get('version', ''))} &middot;
<a href="/openapi.json">openapi.json</a></p>
<p class="desc">{_esc(info.get('description', ''))}</p>
<h2>Authentication</h2>
<p>Security scheme <code>{_esc(', '.join(spec.get('components', {}).get('securitySchemes', {})))}</code>:
signed requests only. Headers
<code>{_esc(', '.join(auth.get('headers', [])))}</code>,
{_esc(auth.get('algorithm', ''))} over the canonical string
<code>{_esc(auth.get('canonical', ''))}</code>,
signature in lowercase hex. {_esc(auth.get('path_note', ''))} Timestamp window
{_esc(auth.get('timestamp_window_seconds', ''))}s; {_esc(auth.get('replay', ''))}.
The secret never reaches a player app.</p>
<h2>Realtime</h2>
<p>WebSocket <code>{_esc(ws.get('path', ''))}</code>, authenticated with
<code>{_esc(ws.get('auth', ''))}</code>.</p>
<p>Events: {_esc(', '.join(ws.get('events', [])))}.</p>
<p class="muted">Not emitted in V1 (documented, not simulated):
{_esc(', '.join(ws.get('v1_not_emitted', [])))}. {_esc(ws.get('v1_note', ''))}</p>
<h2>Endpoints</h2>
{''.join(blocks)}
<h2>Schemas</h2>
<pre>{_esc(json.dumps(spec.get('components', {}).get('schemas', {}), indent=1))}</pre>
</main></body></html>"""
