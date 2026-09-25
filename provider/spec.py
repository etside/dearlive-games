"""OpenAPI 3.1 contract — the single source of truth for the provider API.

docs/openapi.yaml and docs/postman_collection.json are generated from this
module (tools/gen_provider_artifacts.py), and /openapi.json + /docs serve it at
runtime, so the published contract can never drift from the implementation.
"""

PROVIDER_SECURITY = [{
    "ProviderHmac": ["X-API-Key", "X-Timestamp", "X-Nonce", "X-Signature"],
}]

ERROR_SCHEMA = {"$ref": "#/components/schemas/Error"}
ENVELOPE_ERROR = {
    "description": "Error envelope",
    "content": {"application/json": {"schema": ERROR_SCHEMA}},
}

ENVELOPE = {
    "description": "Standard response envelope",
    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Envelope"}}},
}


def _ok(ref_name, description="Success"):
    return {"description": description,
            "content": {"application/json": {
                "schema": {"$ref": f"#/components/schemas/{ref_name}"}}}}


def _errors(*codes):
    mapping = {
        400: ("BAD_REQUEST", "Malformed request"),
        401: ("UNAUTHENTICATED", "Missing, expired, replayed or badly signed request"),
        402: ("INSUFFICIENT_BALANCE", "Player balance too low"),
        403: ("FORBIDDEN", "Not permitted for this credential or player"),
        404: ("NOT_FOUND", "Unknown session, table or transaction"),
        405: ("BAD_REQUEST", "Method not allowed for this path"),
        409: ("STATE_CONFLICT", "Invalid state transition or duplicate key"),
        413: ("BAD_REQUEST", "Request body too large"),
        422: ("VALIDATION_ERROR", "Request failed validation"),
        429: ("RATE_LIMITED", "Rate limit exceeded"),
        500: ("INTERNAL_ERROR", "Server could not complete the request"),
        502: ("INTERNAL_ERROR", "Upstream wallet or identity error"),
        503: ("UNAVAILABLE", "Staging credential service is not configured"),
    }
    out = {}
    for code in codes:
        name, desc = mapping[code]
        out[str(code)] = {
            "description": f"{desc} (code: {name})",
            "content": {"application/json": {"schema": ERROR_SCHEMA}},
        }
    return out


SPEC = {
    "openapi": "3.1.0",
    "info": {
        "title": "Teen Patti Pro — B2B Game Provider API",
        "version": "1.0.0",
        "summary": "Single-integration provider API for operators and aggregators.",
        "description": (
            "One API connection and one API key per operator. The operator "
            "authenticates every server-to-server call with an HMAC-SHA256 "
            "signature, creates a player session, receives a launch URL, and "
            "drives wallet operations with idempotent debit/credit/rollback. "
            "The engine stays authoritative for cards, pots, timing, winners "
            "and settlement. Money of record is held by the operator; the "
            "provider keeps an immutable transaction ledger. "
            "V1 exposes the existing Teen Patti Pro engine only: the engine "
            "has a single betting phase and no player-turn/fold/show phase, "
            "so those events are not emitted."),
        "contact": {"name": "DearLive Games platform"},
        "license": {"name": "Proprietary", "identifier": "LicenseRef-Proprietary"},
    },
    "servers": [
        {"url": "https://api.example.com", "description": "Production"},
        {"url": "http://127.0.0.1:5002", "description": "Local / Termux"},
    ],
    "tags": [
        {"name": "Provider", "description": "Health and catalog"},
        {"name": "Sessions", "description": "Player session lifecycle and launch"},
        {"name": "Tables", "description": "Tables, seats, actions and state"},
        {"name": "Wallet", "description": "Idempotent money operations and ledger"},
        {"name": "Players", "description": "Operator-owned balances"},
        {"name": "Staging", "description": "UAT-only key and test-wallet issuance"},
    ],
    "security": PROVIDER_SECURITY,
    "paths": {
        "/api/v1/provider/health": {
            "get": {
                "tags": ["Provider"],
                "summary": "Liveness and readiness",
                "description": "Unauthenticated liveness probe. It reports whether "
                               "provider credentials are configured but never "
                               "reveals them.",
                "security": [],
                "responses": {"200": {"description": "Service health",
                                      "content": {"application/json": {"schema": {
                                          "$ref": "#/components/schemas/Health"}}}}},
            },
        },
        "/api/v1/games": {
            "get": {
                "tags": ["Provider"],
                "summary": "List games exposed by this provider",
                "responses": {"200": _ok("GamesResponse"), **ENVELOPE_ERROR,
                              **_errors(401, 429)},
            },
        },
        "/api/v1/sessions": {
            "post": {
                "tags": ["Sessions"],
                "summary": "Create a player game session (provider launch)",
                "description": "The operator supplies player identity; no player "
                               "registration or password exists in V1. Returns a "
                               "session id, a short-lived session token, a launch "
                               "URL and the expiry. The session token is the only "
                               "credential the player app ever sees. "
                               "Compatibility: an unsigned request carrying a "
                               "single-use launch_token redeems that token instead "
                               "(deprecated, player-facing, no operator rights).",
                "requestBody": {"required": True, "content": {"application/json": {
                    "schema": {"$ref": "#/components/schemas/SessionCreateRequest"}}}},
                "responses": {"201": {"description": "Session created",
                                      "content": {"application/json": {"schema": {
                                          "$ref": "#/components/schemas/SessionResponse"}}}},
                              **ENVELOPE_ERROR, **_errors(401, 404, 422, 429)},
            },
        },
        "/api/v1/sessions/{sessionId}": {
            "get": {
                "tags": ["Sessions"],
                "summary": "Read a session",
                "description": "An operator signature reads any session. A player "
                               "may read only their own session with its bearer token.",
                "parameters": [{"$ref": "#/components/parameters/SessionId"}],
                "responses": {"200": _ok("SessionState"), **ENVELOPE_ERROR,
                              **_errors(401, 404, 429)},
            },
            "delete": {
                "tags": ["Sessions"],
                "summary": "End a session and revoke its tokens",
                "parameters": [{"$ref": "#/components/parameters/SessionId"}],
                "responses": {"200": _ok("SessionEnded"), **ENVELOPE_ERROR,
                              **_errors(401, 404, 429)},
            },
        },
        "/api/v1/{game}/tables": {
            "get": {
                "tags": ["Tables"],
                "summary": "List tables for a game with live status",
                "parameters": [{"$ref": "#/components/parameters/Game"}],
                "responses": {"200": _ok("TableListResponse"), **ENVELOPE_ERROR,
                              **_errors(401, 429)},
            },
        },
        "/api/v1/{game}/tables/{tableId}/choices": {
            "get": {
                "tags": ["Tables"],
                "summary": "List the choices a table accepts for this game",
                "description": "Teen Patti returns its positions; the wheels return "
                               "their options with multipliers. Use choice_field to "
                               "name the field in an action request.",
                "parameters": [{"$ref": "#/components/parameters/Game"},
                               {"$ref": "#/components/parameters/TableId"}],
                "responses": {"200": _ok("ChoiceListResponse"), **ENVELOPE_ERROR,
                              **_errors(401, 404, 429)},
            },
        },
        "/api/v1/{game}/tables/{tableId}": {
            "get": {
                "tags": ["Tables"],
                "summary": "Read one table",
                "parameters": [{"$ref": "#/components/parameters/Game"},
                               {"$ref": "#/components/parameters/TableId"}],
                "responses": {"200": _ok("Table"), **ENVELOPE_ERROR,
                              **_errors(401, 404, 429)},
            },
        },
        "/api/v1/{game}/tables/{tableId}/join": {
            "post": {
                "tags": ["Tables"],
                "summary": "Seat a player at a table",
                "description": "Identify the player with a session id or "
                               "session_token. A session token always wins over a "
                               "caller-supplied player_id.",
                "parameters": [{"$ref": "#/components/parameters/Game"},
                               {"$ref": "#/components/parameters/TableId"}],
                "requestBody": {"required": True, "content": {"application/json": {
                    "schema": {"$ref": "#/components/schemas/JoinRequest"}}}},
                "responses": {"200": _ok("JoinResponse"), **ENVELOPE_ERROR,
                              **_errors(401, 404, 409, 422, 429)},
            },
        },
        "/api/v1/{game}/tables/{tableId}/leave": {
            "post": {
                "tags": ["Tables"],
                "summary": "Release a seat",
                "parameters": [{"$ref": "#/components/parameters/Game"},
                               {"$ref": "#/components/parameters/TableId"}],
                "requestBody": {"required": False, "content": {"application/json": {
                    "schema": {"$ref": "#/components/schemas/SessionRefRequest"}}}},
                "responses": {"200": _ok("LeaveResponse"), **ENVELOPE_ERROR,
                              **_errors(401, 422, 429)},
            },
        },
        "/api/v1/{game}/tables/{tableId}/action": {
            "post": {
                "tags": ["Tables"],
                "summary": "Submit the V1 player action (bet)",
                "description": "The V1 engine has one betting phase and no player "
                               "turn, fold or show action. Amounts are validated "
                               "against the server-side denomination and table "
                               "limits; the client is never trusted.",
                "parameters": [{"$ref": "#/components/parameters/Game"},
                               {"$ref": "#/components/parameters/TableId"},
                               {"$ref": "#/components/parameters/IdempotencyKey"}],
                "requestBody": {"required": True, "content": {"application/json": {
                    "schema": {"$ref": "#/components/schemas/ActionRequest"}}}},
                "responses": {"200": _ok("ActionResponse"), **ENVELOPE_ERROR,
                              **_errors(401, 402, 404, 409, 422, 429)},
            },
        },
        "/api/v1/{game}/tables/{tableId}/state": {
            "get": {
                "tags": ["Tables"],
                "summary": "Authoritative table state for one player",
                "parameters": [{"$ref": "#/components/parameters/Game"},
                               {"$ref": "#/components/parameters/TableId"},
                               {"name": "player_id", "in": "query", "required": False,
                                "schema": {"type": "string"},
                                "description": "Required unless a session bearer token is sent."}],
                "responses": {"200": _ok("StateResponse"), **ENVELOPE_ERROR,
                              **_errors(401, 422, 429)},
            },
        },
        "/api/v1/{game}/tables/{tableId}/history": {
            "get": {
                "tags": ["Tables"],
                "summary": "Settled round history",
                "parameters": [{"$ref": "#/components/parameters/Game"},
                               {"$ref": "#/components/parameters/TableId"},
                               {"name": "limit", "in": "query", "required": False,
                                "schema": {"type": "integer", "minimum": 1, "maximum": 200,
                                           "default": 50}}],
                "responses": {"200": _ok("HistoryResponse"), **ENVELOPE_ERROR,
                              **_errors(401, 429)},
            },
        },
        "/api/v1/players/{playerId}/balance": {
            "get": {
                "tags": ["Players"],
                "summary": "Operator-owned player balance",
                "parameters": [{"$ref": "#/components/parameters/PlayerId"}],
                "responses": {"200": _ok("BalanceResponse"), **ENVELOPE_ERROR,
                              **_errors(401, 404, 429, 502)},
            },
        },
        "/api/v1/wallet/debit": {
            "post": {
                "tags": ["Wallet"],
                "summary": "Debit a player (reserve stake)",
                "description": "Requires a unique Idempotency-Key and reference. A "
                               "replay returns the original transaction and never "
                               "debits twice.",
                "parameters": [{"$ref": "#/components/parameters/IdempotencyKey"}],
                "requestBody": {"required": True, "content": {"application/json": {
                    "schema": {"$ref": "#/components/schemas/WalletWriteRequest"}}}},
                "responses": {"200": _ok("Transaction"), **ENVELOPE_ERROR,
                              **_errors(401, 402, 409, 422, 429, 502)},
            },
        },
        "/api/v1/wallet/credit": {
            "post": {
                "tags": ["Wallet"],
                "summary": "Credit a player (payout)",
                "parameters": [{"$ref": "#/components/parameters/IdempotencyKey"}],
                "requestBody": {"required": True, "content": {"application/json": {
                    "schema": {"$ref": "#/components/schemas/WalletWriteRequest"}}}},
                "responses": {"200": _ok("Transaction"), **ENVELOPE_ERROR,
                              **_errors(401, 402, 409, 422, 429, 502)},
            },
        },
        "/api/v1/wallet/rollback": {
            "post": {
                "tags": ["Wallet"],
                "summary": "Roll back a previous debit",
                "description": "Appends a compensating credit bound to "
                               "original_reference. The original row is never "
                               "modified or deleted, and a second rollback of the "
                               "same reference is rejected.",
                "parameters": [{"$ref": "#/components/parameters/IdempotencyKey"}],
                "requestBody": {"required": True, "content": {"application/json": {
                    "schema": {"$ref": "#/components/schemas/WalletRollbackRequest"}}}},
                "responses": {"200": _ok("Transaction"), **ENVELOPE_ERROR,
                              **_errors(401, 403, 404, 409, 422, 429, 502)},
            },
        },
        "/api/v1/wallet/transactions/{playerId}": {
            "get": {
                "tags": ["Wallet"],
                "summary": "Immutable transaction ledger for a player",
                "parameters": [{"$ref": "#/components/parameters/PlayerId"},
                               {"name": "limit", "in": "query", "required": False,
                                "schema": {"type": "integer", "minimum": 1,
                                           "maximum": 200, "default": 50}}],
                "responses": {"200": _ok("TransactionListResponse"), **ENVELOPE_ERROR,
                              **_errors(401, 422, 429)},
            },
        },
        "/api/v1/staging/api-keys/provision": {
            "post": {
                "tags": ["Staging"],
                "summary": "Issue a short-lived scoped staging API key",
                "description": "Staging-only. A configured staging PIN mints one "
                               "operator or auditor provider credential restricted "
                               "to named V1 games. The secret is returned exactly "
                               "once; list, revoke, and audit records carry only "
                               "metadata.",
                "requestBody": {"required": True, "content": {"application/json": {
                    "schema": {"$ref": "#/components/schemas/StagingKeyProvisionRequest"}}}},
                "responses": {"201": {"description": "Staging API key issued",
                                       "content": {"application/json": {"schema": {
                                           "$ref": "#/components/schemas/StagingKeyIssued"}}}},
                              **ENVELOPE_ERROR, **_errors(401, 403, 422, 429, 500, 503)},
            },
        },
        "/api/v1/staging/api-keys": {
            "get": {
                "tags": ["Staging"],
                "summary": "List staging API key metadata",
                "description": "Superadmin X-Admin-Key only. Metadata never includes "
                               "a key secret.",
                "responses": {"200": _ok("StagingKeyListResponse"), **ENVELOPE_ERROR,
                              **_errors(401, 403)},
            },
        },
        "/api/v1/staging/api-keys/revoke": {
            "post": {
                "tags": ["Staging"],
                "summary": "Revoke a staging API key",
                "description": "Superadmin X-Admin-Key only. The secret is deleted "
                               "immediately; a short non-sensitive tombstone remains "
                               "until its original expiry.",
                "requestBody": {"required": True, "content": {"application/json": {
                    "schema": {"$ref": "#/components/schemas/StagingKeyRevokeRequest"}}}},
                "responses": {"200": _ok("StagingKeyRevoked"), **ENVELOPE_ERROR,
                              **_errors(401, 403, 404, 422)},
            },
        },
        "/api/v1/staging/api-keys/rotate": {
            "post": {
                "tags": ["Staging"],
                "summary": "Rotate a staging API key",
                "description": "Superadmin X-Admin-Key plus the staging PIN. Mints "
                               "an equivalent replacement and immediately revokes "
                               "the old key.",
                "requestBody": {"required": True, "content": {"application/json": {
                    "schema": {"$ref": "#/components/schemas/StagingKeyRotateRequest"}}}},
                "responses": {"201": {"description": "Staging API key rotated",
                                       "content": {"application/json": {"schema": {
                                           "$ref": "#/components/schemas/StagingKeyIssued"}}}},
                              **ENVELOPE_ERROR, **_errors(401, 403, 404, 422, 429, 500, 503)},
            },
        },
    },
    "components": {
        "securitySchemes": {
            "ProviderHmac": {
                "type": "apiKey",
                "in": "header",
                "name": "X-API-Key",
                "description": (
                    "Signed request authentication. Sign the canonical string "
                    "'METHOD\\nPATH\\nTIMESTAMP\\nNONCE\\nSHA256(raw_body)' with "
                    "HMAC-SHA256 using the shared secret and send the lowercase "
                    "hex digest in X-Signature. Timestamps outside the accepted "
                    "window and reused nonces are rejected. Static secrets are "
                    "never placed in a player app. Staging can also issue "
                    "short-lived, game-scoped TEST-only keys through the staging "
                    "API-key endpoints; those secrets are returned once and "
                    "never listed."),
            },
        },
        "parameters": {
            "SessionId": {"name": "sessionId", "in": "path", "required": True,
                          "schema": {"type": "string"}},
            "TableId": {"name": "tableId", "in": "path", "required": True,
                        "schema": {"type": "string"}},
            "Game": {"name": "game", "in": "path", "required": True,
                     "description": "Game slug. Teen Patti bets on positions, "
                                    "the wheels bet on options.",
                      "schema": {"type": "string",
                                 "enum": ["teen-patti", "greedy-monkey", "baby-king"]}},
            "PlayerId": {"name": "playerId", "in": "path", "required": True,
                         "schema": {"type": "string"}},
            "IdempotencyKey": {
                "name": "Idempotency-Key", "in": "header", "required": True,
                "schema": {"type": "string", "maxLength": 128},
                "description": "Unique per logical transaction. Repeats return the "
                               "original result; reuse with a different payload is rejected.",
            },
        },
        "schemas": {
            "Envelope": {
                "type": "object",
                "required": ["success", "code", "message", "serverTime"],
                "properties": {
                    "success": {"type": "boolean"},
                    "code": {"type": "string"},
                    "message": {"type": "string"},
                    "data": {"type": ["object", "array", "null"]},
                    "serverTime": {"type": "integer",
                                   "description": "Server clock in ms; client clocks are untrusted."},
                    "requestId": {"type": "string"},
                },
            },
            "Error": {
                "type": "object",
                "required": ["success", "code", "message"],
                "properties": {
                    "success": {"const": False},
                    "code": {"type": "string",
                             "enum": ["OK", "UNAUTHENTICATED", "INVALID_SIGNATURE",
                                      "INVALID_TIMESTAMP", "INVALID_NONCE",
                                      "REPLAYED_REQUEST", "FORBIDDEN", "NOT_FOUND",
                                      "VALIDATION_ERROR", "INSUFFICIENT_BALANCE",
                                      "DUPLICATE_REQUEST", "STATE_CONFLICT",
                                      "BETTING_CLOSED", "RATE_LIMITED",
                                      "TBC_RULE_UNCONFIRMED", "INTERNAL_ERROR",
                                      "UNAVAILABLE"]},
                    "message": {"type": "string"},
                    "data": {"type": ["object", "array", "null"]},
                    "serverTime": {"type": "integer"},
                    "requestId": {"type": "string"},
                },
            },
            "Health": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["ok", "degraded"]},
                    "game_code": {"type": "string"},
                    "engine": {"type": "string"},
                    "provider_api": {"type": "string"},
                    "provider_auth_configured": {"type": "boolean"},
                    "wallet_backend": {"type": "string"},
                    "currency": {"type": "string"},
                    "tables": {"type": "integer"},
                    "live_tables": {"type": "integer"},
                    "redis": {"type": "boolean"},
                    "serverTime": {"type": "integer"},
                },
            },
            "Game": {
                "type": "object",
                "properties": {
                    "game_code": {"type": "string"},
                    "name": {"type": "string"},
                    "status": {"type": "string"},
                    "engine": {"type": "string"},
                    "currencies": {"type": "array", "items": {"type": "string"}},
                    "min_bet": {"type": "integer"},
                    "max_bet": {"type": "integer"},
                    "max_players": {"type": "integer"},
                    "tables": {"type": "array", "items": {"type": "string"}},
                    "actions": {"type": "array", "items": {"type": "string"}},
                    "realtime": {"type": "object"},
                },
            },
            "GamesResponse": {
                "type": "object",
                "properties": {"games": {"type": "array",
                                         "items": {"$ref": "#/components/schemas/Game"}}},
            },
            "SessionCreateRequest": {
                "type": "object",
                "required": ["player_id"],
                "properties": {
                    "player_id": {"type": "string", "maxLength": 128,
                                  "description": "Operator-owned player identity."},
                     "game_code": {"type": "string", "default": "teen_patti_pro",
                                   "enum": ["teen_patti_pro", "baby_king", "monkey_wheel"]},
                    "currency": {"type": "string", "default": "COIN"},
                    "language": {"type": "string", "default": "en"},
                    "platform": {"type": "string", "default": "web"},
                    "return_url": {"type": "string", "format": "uri",
                                   "description": "Where the player app returns after play."},
                    "table_id": {"type": "string",
                                 "description": "Optional explicit table; otherwise "
                                                "matchmaking selects one."},
                    "amount": {"type": "integer", "minimum": 1,
                               "description": "Optional intended stake, used by "
                                              "matchmaking and range validation."},
                },
            },
            "SessionResponse": {
                "type": "object",
                "properties": {
                    "success": {"const": True},
                    "session_id": {"type": "string"},
                    "session_token": {"type": "string",
                                      "description": "Short-lived player credential (gst_...)."},
                    "token_type": {"type": "string", "const": "Bearer"},
                    "game_code": {"type": "string"},
                    "table_id": {"type": "string"},
                    "currency": {"type": "string"},
                    "language": {"type": "string"},
                    "platform": {"type": "string"},
                    "return_url": {"type": "string"},
                    "launch_url": {"type": "string", "format": "uri"},
                    "expires_at": {"type": "string", "format": "date-time"},
                    "expires_at_ms": {"type": "integer"},
                    "websocket_url": {"type": "string"},
                },
            },
            "SessionState": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "player_id": {"type": "string"},
                    "game_id": {"type": "string"},
                    "table_id": {"type": "string"},
                    "created_at": {"type": "string", "format": "date-time"},
                    "last_seen_at": {"type": "string", "format": "date-time"},
                    "active": {"type": "boolean"},
                    "table": {"type": ["object", "null"]},
                },
            },
            "SessionEnded": {
                "type": "object",
                "properties": {"session_id": {"type": "string"},
                               "ended": {"const": True},
                               "revoked_tokens": {"type": "integer"}},
            },
            "JoinRequest": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "session_token": {"type": "string"},
                    "player_id": {"type": "string",
                                  "description": "Server-to-server only; ignored when a session is supplied."},
                    "currency": {"type": "string"},
                    "amount": {"type": "integer", "minimum": 1},
                },
            },
            "JoinResponse": {
                "type": "object",
                "properties": {
                    "table_id": {"type": "string"},
                    "player_id": {"type": "string"},
                    "seats": {"type": "array", "items": {"type": "string"}},
                    "players": {"type": "integer"},
                    "max_players": {"type": "integer"},
                    "status": {"type": "string"},
                    "joined": {"const": True},
                    "already_seated": {"type": "boolean"},
                },
            },
            "LeaveResponse": {
                "type": "object",
                "properties": {"table_id": {"type": "string"},
                               "player_id": {"type": "string"},
                               "seats": {"type": "array", "items": {"type": "string"}},
                               "removed": {"type": "boolean"}},
            },
            "SessionRefRequest": {
                "type": "object",
                "properties": {"session_id": {"type": "string"},
                               "session_token": {"type": "string"},
                               "player_id": {"type": "string"}},
            },
            "ActionRequest": {
                "type": "object",
                "required": ["action", "amount"],
                "description": "Send the choice under the field the game reports in "
                               "choice_field: position for Teen Patti, option_id for "
                               "the wheels.",
                "properties": {
                    "action": {"type": "string", "enum": ["bet"],
                               "description": "V1 supports betting only."},
                    "position": {"type": "string", "enum": ["A", "B", "C"]},
                    "option_id": {"type": "string",
                                  "description": "Wheel option, e.g. cub, mane, pride."},
                    "amount": {"type": "integer", "minimum": 1},
                    "session_id": {"type": "string"},
                    "session_token": {"type": "string"},
                    "player_id": {"type": "string"},
                },
            },
            "ActionResponse": {
                "type": "object",
                "properties": {
                    "table_id": {"type": "string"},
                    "player_id": {"type": "string"},
                    "action": {"type": "string"},
                    "accepted": {"const": True},
                    "bet_id": {"type": "string"},
                    "round_id": {"type": "string"},
                    "position": {"type": "string"},
                    "amount": {"type": "integer"},
                    "decision_time": {"type": "integer"},
                },
            },
            "StateResponse": {
                "type": "object",
                "properties": {
                    "table_id": {"type": "string"},
                    "player_id": {"type": "string"},
                    "state": {"type": "object",
                              "description": "Authoritative snapshot; unrevealed hands stay masked."},
                },
            },
            "HistoryResponse": {
                "type": "object",
                "description": "Teen Patti returns settled rounds; the wheels return "
                               "their recent authoritative results. Player bets are "
                               "returned when the caller identifies a player.",
                "properties": {
                    "game_code": {"type": "string"},
                    "table_id": {"type": "string"},
                    "count": {"type": "integer"},
                    "rounds": {"type": "array", "items": {"type": "object"}},
                    "bets": {"type": "array", "items": {"type": "object"}},
                },
            },
            "BalanceResponse": {
                "type": "object",
                "properties": {"player_id": {"type": "string"},
                               "available": {"type": "integer"},
                               "currency": {"type": "string"}},
            },
            "WalletWriteRequest": {
                "type": "object",
                "required": ["player_id", "amount", "reference"],
                "properties": {
                    "player_id": {"type": "string"},
                    "amount": {"type": "integer", "minimum": 1},
                    "currency": {"type": "string"},
                    "game_code": {"type": "string"},
                    "round_id": {"type": "string"},
                    "reference": {"type": "string", "maxLength": 128},
                },
            },
            "WalletRollbackRequest": {
                "type": "object",
                "required": ["player_id", "original_reference", "reason"],
                "properties": {
                    "player_id": {"type": "string"},
                    "original_reference": {"type": "string"},
                    "reason": {"type": "string", "maxLength": 128},
                    "currency": {"type": "string"},
                },
            },
            "Transaction": {
                "type": "object",
                "properties": {
                    "txn_id": {"type": "string"},
                    "type": {"type": "string", "enum": ["debit", "credit", "rollback"]},
                    "status": {"type": "string", "enum": ["SUCCESS", "ROLLED_BACK"]},
                    "player_id": {"type": "string"},
                    "amount": {"type": "integer"},
                    "currency": {"type": "string"},
                    "reference": {"type": "string"},
                    "idempotency_key": {"type": "string"},
                    "game_code": {"type": "string"},
                    "round_id": {"type": "string"},
                    "original_reference": {"type": "string"},
                    "reverses_txn_id": {"type": "string"},
                    "reason": {"type": "string"},
                    "actor": {"type": "string"},
                    "replayed": {"type": "boolean",
                                 "description": "True when this response is the "
                                                "original result of a repeated key."},
                    "created_at": {"type": "integer"},
                },
            },
            "TransactionListResponse": {
                "type": "object",
                "properties": {
                    "player_id": {"type": "string"},
                    "count": {"type": "integer"},
                    "transactions": {"type": "array",
                                     "items": {"$ref": "#/components/schemas/Transaction"}},
                },
            },
            "StagingKeyProvisionRequest": {
                "type": "object",
                "required": ["pin", "label", "games"],
                "properties": {
                    "pin": {"type": "string",
                            "description": "Configured STAGING_PROVISION_PIN. "
                                           "Staging-only."},
                    "label": {"type": "string", "maxLength": 64},
                    "role": {"type": "string", "enum": ["operator", "auditor"],
                             "default": "operator"},
                    "games": {"type": "array", "minItems": 1,
                               "items": {"type": "string",
                                         "enum": ["teen_patti_pro", "baby_king",
                                                  "monkey_wheel"]}},
                    "ttl_seconds": {"type": "integer", "minimum": 900,
                                    "maximum": 2592000, "default": 86400},
                },
            },
            "StagingKeyIssued": {
                "type": "object",
                "description": "The key_secret is returned exactly once and must "
                               "be stored immediately by the Super Admin console.",
                "properties": {
                    "key_id": {"type": "string"},
                    "key_secret": {"type": "string"},
                    "label": {"type": "string"},
                    "role": {"type": "string", "enum": ["operator", "auditor"]},
                    "games": {"type": "array", "items": {"type": "string"}},
                    "environment": {"type": "string", "const": "staging"},
                    "test_only": {"type": "boolean", "const": True},
                    "created_at_ms": {"type": "integer"},
                    "expires_at_ms": {"type": "integer"},
                },
            },
            "StagingKeyMetadata": {
                "type": "object",
                "description": "Public key metadata. It never contains a secret.",
                "properties": {
                    "key_id": {"type": "string"},
                    "label": {"type": "string"},
                    "role": {"type": "string", "enum": ["operator", "auditor"]},
                    "games": {"type": "array", "items": {"type": "string"}},
                    "environment": {"type": "string", "const": "staging"},
                    "test_only": {"type": "boolean", "const": True},
                    "revoked": {"type": "boolean"},
                    "created_at_ms": {"type": "integer"},
                    "expires_at_ms": {"type": "integer"},
                    "revoked_at_ms": {"type": ["integer", "null"]},
                },
            },
            "StagingKeyListResponse": {
                "type": "object",
                "properties": {
                    "keys": {"type": "array",
                             "items": {"$ref": "#/components/schemas/StagingKeyMetadata"}},
                    "count": {"type": "integer"},
                },
            },
            "StagingKeyRevokeRequest": {
                "type": "object",
                "required": ["key_id"],
                "properties": {"key_id": {"type": "string"}},
            },
            "StagingKeyRevoked": {
                "type": "object",
                "properties": {"revoked": {"type": "boolean", "const": True}},
            },
            "StagingKeyRotateRequest": {
                "type": "object",
                "required": ["key_id", "pin"],
                "properties": {
                    "key_id": {"type": "string"},
                    "pin": {"type": "string"},
                    "ttl_seconds": {"type": "integer", "minimum": 900,
                                    "maximum": 2592000},
                },
            },
            "Table": {
                "type": "object",
                "properties": {
                    "table_id": {"type": "string"},
                    "label": {"type": "string"},
                    "min_bet": {"type": "integer"},
                    "max_bet": {"type": "integer"},
                    "max_players": {"type": "integer"},
                    "currency": {"type": "string"},
                    "enabled": {"type": "boolean"},
                    "seats": {"type": "array", "items": {"type": "string"}},
                    "live": {"type": "object",
                             "description": "Live round status, seat count and betting deadline."},
                },
            },
            "TableListResponse": {
                "type": "object",
                "properties": {
                    "game_code": {"type": "string"},
                    "slug": {"type": "string"},
                    "tables": {"type": "array",
                               "items": {"$ref": "#/components/schemas/Table"}}},
            },
            "ChoiceListResponse": {
                "type": "object",
                "properties": {
                    "game_code": {"type": "string"},
                    "table_id": {"type": "string"},
                    "choice_field": {"type": "string",
                                      "enum": ["position", "option_id"]},
                    "choices": {"type": "array",
                                "items": {"$ref": "#/components/schemas/Choice"}},
                },
            },
            "Choice": {
                "type": "object",
                "properties": {
                    "choice": {"type": "string"},
                    "label": {"type": "string"},
                    "multiplier": {"type": ["number", "null"],
                                   "description": "Payout multiplier for a wheel option."},
                    "icon": {"type": "string"},
                    "color_hex": {"type": "string"},
                    "hot": {"type": "boolean"},
                },
            },
        },
    },
    "x-provider": {
        "authentication": {
            "headers": ["X-API-Key", "X-Timestamp", "X-Nonce", "X-Signature"],
            "algorithm": "HMAC-SHA256",
            "canonical": "METHOD\\nPATH\\nTIMESTAMP\\nNONCE\\nSHA256(raw_body)",
            "path_note": "PATH excludes the query string; do not append it before signing.",
            "signature_encoding": "lowercase hex",
            "timestamp_window_seconds": 300,
            "replay": "nonces are single use inside the timestamp window",
        },
        "websocket": {
            "path": "/ws/game",
            "auth": "{\"session_token\": \"gst_...\"}",
            "events": ["room.joined", "game.waiting", "game.started", "card.dealt",
                       "bet.required", "bet.placed", "betting.closed",
                       "game.finished", "round.settled", "game.error"],
            "v1_not_emitted": ["turn.started", "player.folded", "show.requested"],
            "v1_note": "The V1 engine has a single betting phase and no player-turn "
                       "phase, so turn/fold/show events are documented as absent "
                       "rather than simulated.",
        },
    },
}


def openapi_json() -> str:
    import json
    return json.dumps(SPEC, indent=2, sort_keys=False)
