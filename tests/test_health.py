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
        with patch("staging.wsgi._probe_database", return_value="ok"), \
             patch("staging.wsgi._probe_redis", return_value="ok"), \
             patch("staging.wsgi._probe_websocket", return_value="ok"):
            response, payload = self.call_health()
        self.assertEqual(response["status"], 200)
        self.assertEqual(set(payload), {
            "status", "version", "timestamp", "uptime_seconds", "services", "test_count"
        })
        self.assertIsInstance(payload["uptime_seconds"], int)
        self.assertEqual(payload["test_count"], 198)

    def test_health_reports_services(self):
        with patch("staging.wsgi._probe_database", return_value="ok"), \
             patch("staging.wsgi._probe_redis", return_value="ok"), \
             patch("staging.wsgi._probe_websocket", return_value="ok"):
            _, payload = self.call_health()
        self.assertEqual(payload["services"], {
            "database": "ok", "redis": "ok", "websocket": "ok"
        })
        self.assertEqual(payload["status"], "ok")

    def test_health_status_code_matches_service_state(self):
        with patch("staging.wsgi._probe_database", return_value="unavailable"), \
             patch("staging.wsgi._probe_redis", return_value="ok"), \
             patch("staging.wsgi._probe_websocket", return_value="ok"):
            response, payload = self.call_health()
        self.assertEqual(response["status"], 503)
        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["services"]["database"], "unavailable")


if __name__ == "__main__":
    unittest.main()
