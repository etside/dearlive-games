"""Redis connection configuration: env precedence and URL parsing.

Regression cover for a real deployment bug: the MinimalRedis constructor
defaulted `port=6379`, and because 6379 is truthy the expression
`port or os.environ[...]` always returned 6379, so a non-default REDIS_PORT
(hosted providers commonly use 12945) was silently ignored. Local tests missed
it because the local Redis listens on 6379.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from integrations.redis_store import MinimalRedis, _from_url

REDIS_VARS = ("REDIS_URL", "REDIS_HOST", "REDIS_PORT", "REDIS_USERNAME",
              "REDIS_PASSWORD", "REDIS_TLS", "REDIS_DB")


def build(env):
    """Construct settings without opening a socket."""
    with mock.patch.dict(os.environ, {k: "" for k in REDIS_VARS}, clear=False):
        os.environ.update(env)
        client = MinimalRedis.__new__(MinimalRedis)
        from integrations.redis_store import _env
        from_url = _from_url(_env("REDIS_URL", ""))
        client.host = env.get("REDIS_HOST") or env.get("REDIS_URL", "").split("@")[-1].split(":")[0] \
            or from_url.get("host", "127.0.0.1")
        client.port = int(env.get("REDIS_PORT") or from_url.get("port") or 6379)
        client.db = int(env.get("REDIS_DB") or from_url.get("db") or 0)
        client.username = env.get("REDIS_USERNAME") or from_url.get("username", "")
        client.password = env.get("REDIS_PASSWORD") or from_url.get("password", "")
        client.use_tls = env.get("REDIS_TLS", "false").lower() in ("1", "true", "yes") \
            or from_url.get("use_tls", False)
        return client


class RedisConfigTest(unittest.TestCase):
    def test_defaults_to_local_6379(self):
        client = build({})
        self.assertEqual(client.port, 6379)
        self.assertEqual(client.host, "127.0.0.1")
        self.assertFalse(client.use_tls)

    def test_env_port_is_not_overridden_by_constructor_default(self):
        client = build({"REDIS_PORT": "12945"})
        self.assertEqual(client.port, 12945)

    def test_url_is_parsed_when_discrete_vars_absent(self):
        client = build({"REDIS_URL": "rediss://default:sekret@foo.db.redis.io:12945/3"})
        self.assertEqual(client.host, "foo.db.redis.io")
        self.assertEqual(client.port, 12945)
        self.assertEqual(client.db, 3)
        self.assertEqual(client.username, "default")
        self.assertEqual(client.password, "sekret")
        self.assertTrue(client.use_tls)

    def test_explicit_vars_win_over_url(self):
        client = build({"REDIS_URL": "redis://u:p@url-host:1111/0",
                        "REDIS_HOST": "explicit-host", "REDIS_PORT": "2222",
                        "REDIS_TLS": "false"})
        self.assertEqual(client.host, "explicit-host")
        self.assertEqual(client.port, 2222)
        self.assertFalse(client.use_tls)

    def test_tls_flag_respected(self):
        self.assertTrue(build({"REDIS_TLS": "true"}).use_tls)
        self.assertFalse(build({"REDIS_TLS": "false"}).use_tls)

    def test_malformed_url_falls_back(self):
        self.assertEqual(_from_url("not-a-url"), {})
        self.assertEqual(_from_url(""), {})

    def test_constructor_signature_uses_none_sentinels(self):
        import inspect
        params = inspect.signature(MinimalRedis.__init__).parameters
        self.assertIsNone(params["port"].default)
        self.assertIsNone(params["db"].default)


if __name__ == "__main__":
    unittest.main()
