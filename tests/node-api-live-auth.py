"""Isolated feasibility probe, not a VPS installer or Android-client test.

Usage: python3 tests/node-api-live-auth.py SERVER_BINARY CLIENT_BINARY [hy2]
Exercises the actual server with external auth while holding an existing
SOCKS/TCP stream open. All sockets bind only to loopback.
"""
import base64
import hashlib
import http.server
import json
import pathlib
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time


def free_port(kind=socket.SOCK_STREAM):
    with socket.socket(socket.AF_INET, kind) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


passwords = {"existing-account"}
auth_calls = []


class Auth(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        value = body.get("auth") if "auth" in body else base64.b64decode(body["payload"]).decode()
        auth_calls.append(value)
        data = json.dumps({"ok": value in passwords, "msg": "local test", "id": value}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class Echo(socketserver.BaseRequestHandler):
    def handle(self):
        while data := self.request.recv(4096):
            self.request.sendall(data)


def exact(sock, size):
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise RuntimeError("Tunnel closed")
        data += chunk
    return data


processes = []
streams = []
with tempfile.TemporaryDirectory() as directory:
    root = pathlib.Path(directory)
    auth = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Auth)
    echo = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Echo)
    echo.daemon_threads = True
    for service in (auth, echo):
        threading.Thread(target=service.serve_forever, daemon=True).start()
    try:
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                        "-subj", "/CN=localhost", "-days", "1", "-keyout", str(root/"key"),
                        "-out", str(root/"cert")], check=True, capture_output=True)
        udp = free_port(socket.SOCK_DGRAM)
        server_config = {"listen": f"127.0.0.1:{udp}", "cert": str(root/"cert"),
                         "key": str(root/"key"), "obfs": "zivpn", "up_mbps": 100, "down_mbps": 100,
                         "auth": {"mode": "external", "config": {
                             "http": f"http://127.0.0.1:{auth.server_port}/auth"}}}
        modern = len(sys.argv) > 3 and sys.argv[3] == "hy2"
        if modern:
            server_config = {"listen": f"127.0.0.1:{udp}",
                             "cert": str(root/"cert"), "key": str(root/"key"),
                             "tls": {"cert": str(root/"cert"), "key": str(root/"key")},
                             "auth": {"type": "http", "http": {
                                 "url": f"http://127.0.0.1:{auth.server_port}/auth"}}}
        (root/"server.json").write_text(json.dumps(server_config))
        with open(root/"server.log", "w") as log:
            server = subprocess.Popen([sys.argv[1], "server", "-c", str(root/"server.json")],
                                      stdout=log, stderr=log)
        processes.append(server)
        time.sleep(0.4)
        assert server.poll() is None, (root/"server.log").read_text()

        def connect(password):
            port = free_port()
            cfg = {"server": f"127.0.0.1:{udp}", "auth_str": password, "obfs": "zivpn",
                   "up_mbps": 100, "down_mbps": 100, "insecure": True,
                   "socks5": {"listen": f"127.0.0.1:{port}"}}
            if modern:
                cfg = {"server": f"127.0.0.1:{udp}", "auth": password, "tls": {"insecure": True},
                       "socks5": {"listen": f"127.0.0.1:{port}"}}
            path = root/f"client-{len(processes)}.json"
            path.write_text(json.dumps(cfg))
            with open(root/f"client-{len(processes)}.log", "w") as log:
                client = subprocess.Popen([sys.argv[2], "client", "-c", str(path)], stdout=log, stderr=log)
            processes.append(client)
            for _ in range(30):
                try:
                    conn = socket.create_connection(("127.0.0.1", port), timeout=0.2)
                    break
                except OSError:
                    time.sleep(0.1)
            else:
                if password not in passwords and password in auth_calls:
                    return None
                raise RuntimeError("Test client did not open SOCKS; inspect server compatibility")
            streams.append(conn)
            conn.settimeout(5)
            conn.sendall(b"\x05\x01\x00")
            assert exact(conn, 2) == b"\x05\x00"
            conn.sendall(b"\x05\x01\x00\x01\x7f\x00\x00\x01" + echo.server_address[1].to_bytes(2, "big"))
            response = exact(conn, 4)
            if response[1] != 0:
                return None
            size = {1: 4, 4: 16}.get(response[3])
            if size is None:
                size = exact(conn, 1)[0]
            exact(conn, size + 2)
            return conn

        original = connect("existing-account")
        assert original is not None
        original.sendall(b"before")
        assert exact(original, 6) == b"before"
        assert connect("new-account") is None, "Unknown account was accepted"
        passwords.add("new-account")
        new = connect("new-account")
        assert new is not None
        new.sendall(b"new-traffic")
        assert exact(new, 11) == b"new-traffic"
        original.sendall(b"still-connected")
        assert exact(original, 15) == b"still-connected"
        assert server.poll() is None and "new-account" in auth_calls
        print("PASS: new credentials authenticate and carry traffic; original stream stays open; no server restart")
        print("LIMIT: external auth was configured BEFORE startup; does not prove live migration or Android compatibility")
    finally:
        for stream in streams:
            stream.close()
        for proc in processes[::-1]:
            proc.terminate()
        for proc in processes[::-1]:
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        auth.shutdown()
        echo.shutdown()
        auth.server_close()
        echo.server_close()
