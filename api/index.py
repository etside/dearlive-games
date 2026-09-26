"""Serverless WSGI entry point (staging/UAT only).

`app` is the WSGI application (see staging/wsgi.py). All game logic, routing,
validation, RBAC and envelopes are the shared implementation; only transport
and Redis-backed state are staging-specific.

Any WSGI host will serve this -- Vercel functions, AWS Lambda with a WSGI
adapter, Gunicorn/uWSGI behind nginx, or a container. Nothing here is
platform-specific; the host only needs to import `app` and set
REDIS_HOST/PORT/USERNAME/PASSWORD/TLS/DB.

Refuses APP_ENV=production at import: the staging routes are compiled out
there, and production runs games/teen_patti_pro/api.py instead. See
docs/DEPLOYMENT.md for how to choose a host.
"""
from staging.wsgi import app  # noqa: F401
