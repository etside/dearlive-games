import io
import json
import unittest
from unittest.mock import patch

from staging.wsgi import app


class HealthEndpointTests(unittest.TestCase):
    def call_health(self):
        response = {}

        def start_response(status, headers):
            response["status"] = int(status.split(" ", 1)[0])
            response["headers"] = dict(headers)

        body = b"".join(app({
            "REQUEST_METHOD": "GET",
            "PATH_INFO": "/api/v1/health",
            "QUERY_STRING": "",
            "CONTENT_LENGTH": "0",
            "wsgi.input": io.BytesIO(),
        }, start_response))
        return response, json.loads(body)

    def test_health_returns_json(self):
        with patch("staging.wsgi._probe_database", return_value={"status": "ok"}), \
             patch("staging.wsgi._probe_redis", return_value={"status": "ok"}), \
             patch("staging.wsgi._probe_websocket", return_value={"status": "ok"}):
            response, payload = self.call_health()
        self.assertEqual(response["status"], 200)
        self.assertEqual(set(payload), {
            "status", "version", "timestamp", "uptime_seconds", "services"
        })
        self.assertIsInstance(payload["uptime_seconds"], int)

    def test_health_reports_no_hardcoded_test_count(self):
        # It used to assert test_count == 198, a literal that went stale on
        # every added test and told an operator nothing about service health.
        with patch("staging.wsgi._probe_database", return_value={"status": "ok"}), \
             patch("staging.wsgi._probe_redis", return_value={"status": "ok"}), \
             patch("staging.wsgi._probe_websocket", return_value={"status": "ok"}):
            _, payload = self.call_health()
        self.assertNotIn("test_count", payload)

    def test_health_reports_services(self):
        with patch("staging.wsgi._probe_database", return_value={"status": "ok"}), \
             patch("staging.wsgi._probe_redis", return_value={"status": "ok"}), \
             patch("staging.wsgi._probe_websocket", return_value={"status": "ok"}):
            _, payload = self.call_health()
        self.assertEqual(payload["services"], {
            "database": {"status": "ok"},
            "redis": {"status": "ok"},
            "websocket": {"status": "ok"},
        })
        self.assertEqual(payload["status"], "ok")

    def test_health_allows_missing_websocket(self):
        with patch("staging.wsgi._probe_database", return_value={"status": "ok"}), \
             patch("staging.wsgi._probe_redis", return_value={"status": "ok"}), \
             patch("staging.wsgi._probe_websocket", return_value={
                 "status": "not_configured", "reason": "WEBSOCKET_URL is not configured"
             }):
            response, payload = self.call_health()
        self.assertEqual(response["status"], 200)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["services"]["websocket"]["status"], "not_configured")

    def test_health_status_code_matches_service_state(self):
        with patch("staging.wsgi._probe_database", return_value={
            "status": "unavailable", "reason": "DATABASE_URL is not configured"
        }), patch("staging.wsgi._probe_redis", return_value={"status": "ok"}), \
             patch("staging.wsgi._probe_websocket", return_value={"status": "ok"}):
            response, payload = self.call_health()
        self.assertEqual(response["status"], 503)
        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["services"]["database"], {
            "status": "unavailable", "reason": "DATABASE_URL is not configured"
        })

    def test_probe_error_reports_unavailable_not_down(self):
        # Integrations branch on this value. A probe that ran and failed is
        # reported as "unavailable"; the vocabulary is ok | degraded |
        # unavailable, and "down" is not part of it.
        with patch("staging.wsgi._probe_database", return_value={
            "status": "error", "reason": "connection refused"
        }), patch("staging.wsgi._probe_redis", return_value={"status": "ok"}), \
             patch("staging.wsgi._probe_websocket", return_value={"status": "ok"}):
            response, payload = self.call_health()
        self.assertEqual(response["status"], 503)
        self.assertEqual(payload["status"], "unavailable")
        self.assertNotEqual(payload["status"], "down")

    def test_status_vocabulary_is_exactly_three_values(self):
        seen = set()
        for db, redis in (("ok", "ok"), ("unavailable", "ok"),
                          ("error", "ok"), ("ok", "error")):
            with patch("staging.wsgi._probe_database", return_value={"status": db}), \
                 patch("staging.wsgi._probe_redis", return_value={"status": redis}), \
                 patch("staging.wsgi._probe_websocket", return_value={"status": "ok"}):
                _, payload = self.call_health()
            seen.add(payload["status"])
        self.assertTrue(seen <= {"ok", "degraded", "unavailable"}, seen)


if __name__ == "__main__":
    unittest.main()
