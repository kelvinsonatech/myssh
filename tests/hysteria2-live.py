"""Local QUIC smoke test. Supply a verified official Hysteria 2 binary path.
Never modifies system services/firewall; uses temporary loopback listeners.
"""
import datetime
import http.server
import json
import pathlib
import socket
import subprocess
import sys
import tempfile
import threading
import time


def port(kind=socket.SOCK_STREAM):
    with socket.socket(socket.AF_INET, kind) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


binary = str(pathlib.Path(sys.argv[1]).resolve())
source = pathlib.Path("scripts/ssh-ssl-setup.sh").read_text()
auth = source.split("<<'HY2AUTHEOF'\n", 1)[1].split("\nHY2AUTHEOF", 1)[0]
processes = []
with tempfile.TemporaryDirectory(prefix="hy2-live-") as directory:
    root = pathlib.Path(directory)
    accounts = root / "accounts"
    accounts.write_text("demo\tTestPass123\t20990101\nexpired\tExpiredPass123\t20000101\n")
    helper = root / "auth"
    helper.write_text(auth.replace("/etc/hysteria2/accounts", str(accounts)))
    helper.chmod(0o700)
    subprocess.run(["openssl", "req", "-x509", "-nodes", "-newkey", "rsa:2048",
                    "-days", "1", "-subj", "/CN=localhost", "-keyout",
                    str(root / "key"), "-out", str(root / "cert")],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    udp = port(socket.SOCK_DGRAM)
    config = {"listen": f"127.0.0.1:{udp}",
              "tls": {"cert": str(root / "cert"), "key": str(root / "key")},
              "auth": {"type": "command", "command": str(helper)}}
    (root / "server.json").write_text(json.dumps(config))
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"HY2_LOOPBACK_OK")

        def log_message(self, *_):
            pass

    http = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    try:
        with (root / "server.log").open("w") as output:
            server = subprocess.Popen([binary, "server", "-c", str(root / "server.json")],
                                      stdout=output, stderr=output)
        processes.append(server)
        time.sleep(0.5)
        assert server.poll() is None, (root / "server.log").read_text()
        for credential, expected, duplicate in [
            ("TestPass123", True, False),
            ("demo:TestPass123", True, False),
            ("WrongPass123", False, False),
            ("ExpiredPass123", False, False),
            ("expired:ExpiredPass123", False, False),
            ("TestPass123", False, True),
            ("demo:TestPass123", True, True),
        ]:
            if duplicate:
                accounts.write_text("demo\tTestPass123\t20990101\n"
                                    "expired\tTestPass123\t20000101\n")
            socks = port()
            client_config = {"server": f"127.0.0.1:{udp}", "auth": credential,
                             "tls": {"insecure": True},
                             "socks5": {"listen": f"127.0.0.1:{socks}"}}
            (root / "client.json").write_text(json.dumps(client_config))
            with (root / "client.log").open("w") as output:
                client = subprocess.Popen([binary, "client", "-c", str(root / "client.json")],
                                          stdout=output, stderr=output)
            processes.append(client)
            success = False
            for _ in range(20):
                time.sleep(0.15)
                if client.poll() is not None:
                    break
                result = subprocess.run(
                    ["curl", "-fsS", "--noproxy", "", "--max-time", "1",
                     "--socks5-hostname", f"127.0.0.1:{socks}",
                     f"http://127.0.0.1:{http.server_port}/"],
                    capture_output=True)
                if result.returncode == 0 and result.stdout == b"HY2_LOOPBACK_OK":
                    success = True
                    break
            assert success == expected, (root / "client.log").read_text()
            if not expected:
                assert "auth" in (root / "client.log").read_text().lower()
            client.terminate()
            client.wait(timeout=5)
        print("PASS: real QUIC password-only and legacy transfers, wrong/expired/duplicate rejection")
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
        http.shutdown()