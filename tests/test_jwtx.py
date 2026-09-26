"""Tests for the standard-library HS256 token helper (common/jwtx.py).

This signs operator/admin bearer tokens, so the negative cases matter more
than the happy path: a forged or downgraded token must never authenticate.
"""
import json
import time
import unittest

from common import jwtx

SECRET = "unit-test-secret"


class EncodeDecodeTest(unittest.TestCase):
    def test_round_trip_preserves_claims(self):
        payload = {"scope": "operator", "iat": 1, "exp": int(time.time()) + 600,
                   "iss": "dearlive-games", "sub": "operator"}
        token = jwtx.encode(payload, SECRET)
        self.assertEqual(token.count("."), 2)
        self.assertEqual(jwtx.decode(token, SECRET), payload)

    def test_round_trip_without_exp(self):
        self.assertEqual(jwtx.decode(jwtx.encode({"a": 1}, SECRET), SECRET), {"a": 1})

    def test_header_declares_hs256(self):
        token = jwtx.encode({"a": 1}, SECRET)
        header = json.loads(jwtx._b64url_decode(token.split(".")[0]))
        self.assertEqual(header["alg"], "HS256")
        self.assertEqual(header["typ"], "JWT")

    def test_token_is_deterministic_for_same_input(self):
        a = jwtx.encode({"a": 1, "b": 2}, SECRET)
        b = jwtx.encode({"a": 1, "b": 2}, SECRET)
        self.assertEqual(a, b)

    def test_rejects_empty_secret(self):
        with self.assertRaises(ValueError):
            jwtx.encode({"a": 1}, "")

    def test_rejects_non_hs256_algorithm(self):
        with self.assertRaises(ValueError):
            jwtx.encode({"a": 1}, SECRET, algorithm="HS512")


class TamperTest(unittest.TestCase):
    def test_modified_payload_is_rejected(self):
        token = jwtx.encode({"scope": "operator"}, SECRET)
        head, _, sig = token.split(".")
        forged = jwtx._b64url(b'{"scope":"superadmin"}')
        with self.assertRaises(jwtx.InvalidSignatureError):
            jwtx.decode(f"{head}.{forged}.{sig}", SECRET)

    def test_wrong_secret_is_rejected(self):
        token = jwtx.encode({"scope": "operator"}, SECRET)
        with self.assertRaises(jwtx.InvalidSignatureError):
            jwtx.decode(token, "other-secret")

    def test_alg_none_forgery_is_rejected(self):
        # {"alg":"none"} + admin claims, unsigned.
        head = jwtx._b64url(b'{"alg":"none","typ":"JWT"}')
        body = jwtx._b64url(b'{"scope":"superadmin"}')
        with self.assertRaises(jwtx.InvalidTokenError):
            jwtx.decode(f"{head}.{body}.", SECRET)

    def test_alg_swap_is_rejected(self):
        token = jwtx.encode({"a": 1}, SECRET)
        head = jwtx._b64url(b'{"alg":"HS512","typ":"JWT"}')
        body = token.split(".")[1]
        with self.assertRaises(jwtx.InvalidTokenError):
            jwtx.decode(f"{head}.{body}.{token.split('.')[2]}", SECRET)

    def test_garbage_is_rejected(self):
        for bad in ("", "abc", "a.b", "a.b.c.d", "....", "not-a-token"):
            with self.assertRaises(jwtx.InvalidTokenError):
                jwtx.decode(bad, SECRET)

    def test_non_json_payload_is_rejected(self):
        head = jwtx._b64url(b'{"alg":"HS256","typ":"JWT"}')
        body = jwtx._b64url(b"not json at all")
        sig = jwtx._b64url(b"\x00" * 32)
        with self.assertRaises(jwtx.InvalidSignatureError):
            jwtx.decode(f"{head}.{body}.{sig}", SECRET)


class ExpiryTest(unittest.TestCase):
    def test_expired_token_raises_expired(self):
        token = jwtx.encode({"exp": int(time.time()) - 10}, SECRET)
        with self.assertRaises(jwtx.ExpiredSignatureError):
            jwtx.decode(token, SECRET)

    def test_future_exp_is_accepted(self):
        token = jwtx.encode({"exp": int(time.time()) + 600}, SECRET)
        self.assertIn("exp", jwtx.decode(token, SECRET))

    def test_expired_signature_error_is_a_token_error(self):
        # callers catch InvalidTokenError; expiry must not escape that net
        self.assertTrue(issubclass(jwtx.ExpiredSignatureError,
                                   jwtx.InvalidTokenError))

    def test_verify_exp_false_skips_expiry(self):
        token = jwtx.encode({"exp": 1}, SECRET)
        self.assertEqual(jwtx.decode(token, SECRET, verify_exp=False), {"exp": 1})

    def test_non_integer_exp_is_rejected(self):
        token = jwtx.encode({"exp": "soon"}, SECRET)
        with self.assertRaises(jwtx.InvalidTokenError):
            jwtx.decode(token, SECRET)

    def test_absent_exp_never_expires(self):
        self.assertEqual(jwtx.decode(jwtx.encode({"a": 1}, SECRET), SECRET), {"a": 1})


class PurityTest(unittest.TestCase):
    def test_no_third_party_import(self):
        # The server is standard-library only; a PyJWT regression here would
        # silently reintroduce the missing-dependency 500 that broke login.
        # Match whole module names: "import jwtx" must not trip "import jwt".
        import pathlib
        import re
        src = pathlib.Path(jwtx.__file__).read_text(encoding="utf-8")
        for banned in ("jwt", "bcrypt", "psycopg", "requests"):
            pattern = rf"^\s*(?:import|from)\s+{banned}\b"
            self.assertIsNone(re.search(pattern, src, re.M),
                              f"{banned} must not be imported by jwtx")

    def test_only_stdlib_modules_are_imported(self):
        import ast
        import pathlib
        allowed = {"base64", "hashlib", "hmac", "json", "time", "typing",
                   "__future__"}
        tree = ast.parse(pathlib.Path(jwtx.__file__).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertTrue(imported <= allowed,
                        f"unexpected imports: {sorted(imported - allowed)}")


if __name__ == "__main__":
    unittest.main()
