"""Local authentication regression tests; never contacts a VPS."""
import hashlib
import hmac
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/node-api"))
from vpn_node_api.common import Fault, Store
from vpn_node_api.server import authenticate


class Authentication(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.config = {"key_id": "test-node", "secret": "local-test-not-a-real-secret"}
        self.now = int(time.time())

    def signed(self, target="/api/v1/info", body=b"", age=0):
        headers = {"x-node-key-id": self.config["key_id"],
                   "x-node-timestamp": str(self.now - age),
                   "x-node-nonce": uuid.uuid4().hex,
                   "x-request-id": str(uuid.uuid4())}
        text = "\n".join(("VL1-HMAC-SHA256", "GET", target,
                          headers["x-node-timestamp"], headers["x-node-nonce"],
                          hashlib.sha256(body).hexdigest()))
        headers["x-node-signature"] = "v1=" + hmac.new(
            self.config["secret"].encode(), text.encode(), hashlib.sha256).hexdigest()
        return headers

    def check(self, headers, target="/api/v1/info", body=b""):
        return authenticate(headers, "GET", target, body, self.config, self.store, self.now)

    def error(self, code, headers, **kwargs):
        with self.assertRaises(Fault) as context:
            self.check(headers, **kwargs)
        self.assertEqual(context.exception.code, code)
        self.assertEqual(context.exception.status, 401)

    def test_valid_and_persistent_replay(self):
        headers = self.signed()
        self.check(headers)
        self.store = Store(self.temp.name)
        self.error("REPLAYED_NONCE", headers)

    def test_missing_unknown_and_malformed(self):
        self.error("UNAUTHORIZED", {})
        for name, value in (("x-node-key-id", "unknown"),
                            ("x-node-timestamp", "not-a-number"),
                            ("x-node-nonce", "short"),
                            ("x-request-id", "not-a-uuid")):
            headers = self.signed()
            headers[name] = value
            self.error("UNAUTHORIZED", headers)

    def test_clock_before_signature(self):
        headers = self.signed(age=601)
        headers["x-node-signature"] = "wrong"
        self.error("CLOCK_SKEW", headers)

    def test_invalid_does_not_consume_nonce(self):
        headers = self.signed()
        signature = headers["x-node-signature"]
        headers["x-node-signature"] = "v1=" + "0" * 64
        self.error("INVALID_SIGNATURE", headers)
        headers["x-node-signature"] = signature
        self.check(headers)

    def test_exact_query_and_body(self):
        target = "/api/v1/accounts?cursor=a%2Fb&limit=10"
        headers = self.signed(target, b'{"a":1}')
        self.error("INVALID_SIGNATURE", headers, target=target, body=b'{"a": 1}')
        self.error("INVALID_SIGNATURE", headers, target=target.replace("%2F", "/"), body=b'{"a":1}')
        self.check(headers, target, b'{"a":1}')


if __name__ == "__main__":
    unittest.main()
