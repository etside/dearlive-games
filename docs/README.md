# Documentation index

Start at the [top-level README](../README.md) for the 5-minute quickstart.

## Core

| Doc | Contents |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | components, topology, entrypoints |
| [API.md](API.md) | REST surface, envelopes, error codes |
| [WEBSOCKET.md](WEBSOCKET.md) | push protocol and frames |
| [STATE-MACHINE.md](STATE-MACHINE.md) | round lifecycle |
| [GAME_RULES.md](GAME_RULES.md) | Teen Patti Pro ruleset |
| [GAME_RULES_WHEELS.md](GAME_RULES_WHEELS.md) | Greedy Monkey / Baby King rules |

## Integration

| Doc | Contents |
|---|---|
| [INTEGRATION.md](INTEGRATION.md) | platform ↔ games contract |
| [ADMIN.md](ADMIN.md) | admin API keys, roles, every endpoint, scheduling |
| [PROVIDER-INTEGRATION.md](PROVIDER-INTEGRATION.md) | B2B HMAC API guide |
| [IDEMPOTENCY.md](IDEMPOTENCY.md) | idempotency keys, retries, errors |
| [webhooks.md](webhooks.md) | settlement webhooks |
| [wallet-integration.md](wallet-integration.md) | wallet adapter contract |
| [realtime.md](realtime.md) | realtime model |

## Operating

| Doc | Contents |
|---|---|
| [DEPLOYMENT.md](DEPLOYMENT.md) | local, Docker, any host, base URL, production |
| [DEVELOPMENT.md](DEVELOPMENT.md) | layout, tests, spec generation |
| [SECURITY.md](SECURITY.md) | threat model, signing, known gaps |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | common failures |
| [UAT.md](UAT.md) | acceptance matrix |

## Specifications & history

- [`api/openapi.yaml`](../api/openapi.yaml) — generated OpenAPI 3.1 spec
  (source: `provider/spec.py`)
- [`postman_collection.json`](postman_collection.json) — generated
- [business-tbc.md](business-tbc.md), [V1_BUSINESS_RULES_PENDING.md](../V1_BUSINESS_RULES_PENDING.md)
  — rules awaiting business sign-off
- [residual-defects.md](residual-defects.md), [traceability.md](traceability.md),
  [duplicate-decisions.md](duplicate-decisions.md) — project records
- [handover-dearlive.md](handover-dearlive.md), [dearlive-request.md](dearlive-request.md),
  [launch-integration.md](launch-integration.md) — partner context

## Asset pipeline

[asset-inventory.md](asset-inventory.md), [assets-ui.md](assets-ui.md),
[cards-report.md](cards-report.md), [normalize-report.md](normalize-report.md),
[split-report.md](split-report.md), [vectorize-report.md](vectorize-report.md)
