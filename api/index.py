"""Vercel serverless entry (staging/UAT only).

`app` is the WSGI application (see staging/wsgi.py). All game logic,
routing, validation, RBAC and envelopes are the shared implementation;
only transport + Redis-backed state are staging-specific. Requires
Upstash-compatible Redis env (REDIS_HOST/PORT/USERNAME/PASSWORD/TLS/DB).
Refuses APP_ENV=production at import (staging routes are compiled out).
"""
from staging.wsgi import app  # noqa: F401
