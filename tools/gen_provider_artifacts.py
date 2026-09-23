"""Generate docs/openapi.yaml and docs/postman_collection.json from the spec."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from provider.spec import SPEC
from provider.yamlgen import to_yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPENAPI_PATH = os.path.join(ROOT, "docs", "openapi.yaml")
POSTMAN_PATH = os.path.join(ROOT, "docs", "postman_collection.json")

SECURITY_HEADERS = [
    ("X-API-Key", "{{providerApiKey}}"),
    ("X-Timestamp", "{{timestamp}}"),
    ("X-Nonce", "{{nonce}}"),
    ("X-Signature", "{{signature}}"),
]


def _sample(schema, spec):
    ref = schema.get("$ref") if isinstance(schema, dict) else None
    if ref:
        name = ref.rsplit("/", 1)[-1]
        return _sample(spec["components"]["schemas"].get(name, {}), spec)
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), "string")
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    if kind == "object" or "properties" in schema:
        return {k: _sample(v, spec) for k, v in schema.get("properties", {}).items()
                if k in ("player_id", "amount", "currency", "reference",
                         "game_code", "round_id", "action", "position",
                         "original_reference", "reason", "table_id",
                         "session_id", "session_token")}
    if kind == "array":
        return [_sample(schema.get("items", {}), spec)]
    if kind == "integer":
        return schema.get("minimum", 1)
    if kind == "number":
        return 1
    if kind == "boolean":
        return True
    return "string"


def _params(op):
    """Resolve parameter objects, following local $ref into components."""
    out = []
    for param in op.get("parameters", []):
        ref = param.get("$ref")
        if ref:
            name = ref.rsplit("/", 1)[-1]
            param = SPEC["components"]["parameters"].get(name, {})
        out.append(param)
    return out


def _body(op):
    body = op.get("requestBody")
    if not body:
        return None
    content = body.get("content", {}).get("application/json")
    if not content:
        return None
    schema = content.get("schema", {})
    return json.dumps(_sample(schema, SPEC), indent=2)


def build_postman():
    items = []
    for path, methods in SPEC["paths"].items():
        for method, op in methods.items():
            if method not in ("get", "post", "put", "delete", "patch"):
                continue
            url_path = path
            for param in _params(op):
                token = "{" + param["name"] + "}"
                if param.get("in") == "path" and token in url_path:
                    url_path = url_path.replace(token, ":" + param["name"])
            query = [p for p in _params(op) if p.get("in") == "query"]
            url = {"raw": "{{baseUrl}}" + url_path, "host": ["{{baseUrl}}"],
                   "path": [seg for seg in url_path.split("/") if seg]}
            if query:
                url["query"] = [{"key": p["name"], "value": ""} for p in query]
            headers = [{"key": k, "value": v, "type": "text"} for k, v in SECURITY_HEADERS]
            for param in _params(op):
                if param.get("in") == "header" and param.get("required"):
                    headers.append({"key": param["name"], "value": "",
                                    "type": "text"})
            request = {"method": method.upper(), "header": headers, "url": url,
                       "description": op.get("description", op.get("summary", ""))}
            body = _body(op)
            if body is not None:
                request["header"].append({"key": "Content-Type",
                                          "value": "application/json", "type": "text"})
                request["body"] = {"mode": "raw", "raw": body,
                                   "options": {"raw": {"language": "json"}}}
            items.append({"name": op.get("summary", method.upper()),
                          "request": request,
                          "response": []})
    return {
        "info": {
            "name": SPEC["info"]["title"],
            "description": SPEC["info"]["description"],
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "auth": {"type": "apikey", "apikey": [
            {"key": "key", "value": "{{providerApiKey}}", "type": "string"},
            {"key": "value", "value": "{{providerApiKey}}", "type": "string"},
            {"key": "in", "value": "header", "type": "string"},
        ]},
        "variable": [
            {"key": "baseUrl", "value": "http://127.0.0.1:5002"},
            {"key": "providerApiKey", "value": "tp_live_xxx"},
            {"key": "providerSecret", "value": "replace-me"},
            {"key": "game", "value": "teen-patti",
             "description": "Game slug: teen-patti, greedy-lion or monkey-wheel. "
                            "Greedy Lion bets on option_id, Teen Patti on position."},
            {"key": "tableId", "value": "teen-patti-low",
             "description": "Table id for the selected game."},
            {"key": "timestamp", "value": ""},
            {"key": "nonce", "value": ""},
            {"key": "signature", "value": ""},
        ],
        "event": [{
            "listen": "prerequest",
            "script": {"type": "text/javascript", "exec": [
                "// Populate signed-request headers before each call.",
                "var base = require('crypto');",
                "pm.variables.set('timestamp', String(Math.floor(Date.now()/1000)));",
                "pm.variables.set('nonce', base.randomBytes(8).toString('hex'));",
                "var method = pm.request.method.toUpperCase();",
                "var path = pm.request.url.path.join('/');",
                "var body = pm.request.body ? pm.request.body.raw || '' : '';",
                "var canonical = [method, path, pm.variables.get('timestamp'),",
                "  pm.variables.get('nonce'),",
                "  base.createHash('sha256').update(body).digest('hex')].join('\\n');",
                "var sig = base.createHmac('sha256',",
                "  pm.variables.get('providerSecret')).update(canonical).digest('hex');",
                "pm.variables.set('signature', sig);",
            ]},
        }],
        "item": items,
    }


def main():
    with open(OPENAPI_PATH, "w", encoding="utf-8") as fh:
        fh.write("# Generated from provider/spec.py by tools/gen_provider_artifacts.py\n")
        fh.write("# Edit provider/spec.py, then regenerate. Do not hand-edit.\n")
        fh.write(to_yaml(SPEC))
    with open(POSTMAN_PATH, "w", encoding="utf-8") as fh:
        json.dump(build_postman(), fh, indent=2)
        fh.write("\n")
    print(f"wrote {OPENAPI_PATH}")
    print(f"wrote {POSTMAN_PATH}")


if __name__ == "__main__":
    main()
