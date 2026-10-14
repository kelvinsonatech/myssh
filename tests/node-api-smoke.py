"""Run from the allowed website host. Standard library; secrets are not logged."""
import argparse
import getpass
import hashlib
import hmac
import http.client
import json
import secrets
import ssl
import time
import uuid
from urllib.parse import urlsplit

parser = argparse.ArgumentParser()
parser.add_argument("--url", required=True, help="https://node.example:9443")
parser.add_argument("--key-id", required=True)
parser.add_argument("--pin", required=True, help="Certificate SHA-256 printed by setup")
args = parser.parse_args()
target = urlsplit(args.url)
if target.scheme != "https" or target.username or target.password or target.query:
    raise SystemExit("Use an HTTPS node URL without credentials or query")
pin = args.pin.replace(":", "").lower()
if len(pin) != 64 or any(c not in "0123456789abcdef" for c in pin):
    raise SystemExit("Invalid SHA-256 pin")
secret = getpass.getpass("API secret (hidden): ")


def headers(path, nonce=None, age=0, signing_secret=None):
    timestamp, nonce = str(int(time.time()) - age), nonce or secrets.token_hex(16)
    message = "\n".join(("VL1-HMAC-SHA256", "GET", path, timestamp, nonce,
                          hashlib.sha256(b"").hexdigest()))
    return {"X-Node-Key-Id": args.key_id, "X-Node-Timestamp": timestamp,
            "X-Node-Nonce": nonce, "X-Request-Id": str(uuid.uuid4()),
            "X-Node-Signature": "v1=" + hmac.new(
                (signing_secret or secret).encode(), message.encode(), hashlib.sha256).hexdigest()}


def call(path, signed):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE  # Explicit certificate pin below, BEFORE sending credentials.
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    connection = http.client.HTTPSConnection(target.hostname, target.port or 443,
                                             context=context, timeout=30)
    try:
        connection.connect()
        actual = hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest()
        if not hmac.compare_digest(actual, pin):
            raise SystemExit("Certificate pin mismatch; no request sent")
        connection.request("GET", path, headers=signed)
        response = connection.getresponse()
        data = json.loads(response.read(1024 * 1024))
        return response.status, data
    finally:
        connection.close()


for path in ("/api/v1/info", "/api/v1/health", "/api/v1/protocols"):
    status, data = call(path, headers(path))
    assert status == 200 and data["ok"], (status, data)
    print(path, json.dumps(data))
path = "/api/v1/info"
for signed, expected in (({}, "UNAUTHORIZED"),
                         (headers(path, signing_secret="deliberately-wrong"), "INVALID_SIGNATURE"),
                         (headers(path, age=601), "CLOCK_SKEW")):
    status, data = call(path, signed)
    assert status == 401 and data["error"]["code"] == expected, (status, data)
nonce = secrets.token_hex(16)
assert call(path, headers(path, nonce=nonce))[0] == 200
status, data = call(path, headers(path, nonce=nonce))
assert status == 401 and data["error"]["code"] == "REPLAYED_NONCE"
print("PASS: HTTPS pin, discovery, HMAC errors and nonce replay. No accounts changed.")
