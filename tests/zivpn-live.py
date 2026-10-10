"""Smoke-test the exact upstream ZIVPN binary on loopback, not a real VPN client.
Usage: python3 tests/zivpn-live.py /path/to/verified/zivpn
Does not touch systemd, firewall, other services, or /etc.
"""
import errno
import hashlib
import json
import pathlib
import socket
import subprocess
import sys
import tempfile
import time

binary = pathlib.Path(sys.argv[1]).resolve()
assert hashlib.sha256(binary.read_bytes()).hexdigest() == (
    "df6658c195882ff2f6cefb44050e8cb2c238ceb2b6e3fbefb931698f4f0519cb"
), "Expected pinned upstream amd64 binary"
with tempfile.TemporaryDirectory(prefix="zivpn-live-") as directory:
    root = pathlib.Path(directory)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    subprocess.run(
        ["openssl", "req", "-new", "-newkey", "rsa:2048", "-days", "1", "-nodes",
         "-x509", "-subj", "/CN=zivpn", "-keyout", str(root / "key"),
         "-out", str(root / "cert")],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    config = {"listen": f"127.0.0.1:{port}", "cert": str(root / "cert"),
              "key": str(root / "key"), "obfs": "zivpn",
              "auth": {"mode": "passwords", "config": ["TestPassword123"]}}
    (root / "config.json").write_text(json.dumps(config))
    with (root / "server.log").open("w") as log:
        server = subprocess.Popen(
            [str(binary), "server", "-c", str(root / "config.json")],
            stdout=log, stderr=log)
    try:
        time.sleep(1)
        assert server.poll() is None, (root / "server.log").read_text()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError as error:
                assert error.errno == errno.EADDRINUSE
            else:
                raise AssertionError("Upstream ZIVPN did not bind its configured port")
        print("PASS: exact upstream binary accepts config and binds loopback UDP")
        print("NOT VERIFIED: Android app authentication/traffic, VPS systemd/firewall/reboot")
    finally:
        server.terminate()
        server.wait(timeout=5)
