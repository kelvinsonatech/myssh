"""Isolated HTTP contract and native-file tests. No host users/services changed."""
import json
import pathlib
import sys
import tempfile
import time
import unittest
import uuid
import hashlib
import hmac
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts/node-api"))
from fastapi.testclient import TestClient
from vpn_node_api.common import Store, Fault
from vpn_node_api.engine import Engine
from vpn_node_api.native import Native
from vpn_node_api.server import app_for
from vpn_node_api.control import coordinated_menu, service_text, require_safe_port


class FixtureNative(Native):
    def run(self, argv, data=None, check=True, timeout=6):
        # A restart, kill, useradd or any other unexpected command fails tests.
        assert argv[:2] == ["systemctl", "is-active"], argv
        return SimpleNamespace(returncode=0, stdout="active")

    def installed(self, protocol):
        return protocol == "hysteria2"


class Contract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = pathlib.Path(self.temp.name)
        self.account_file = root / "etc/hysteria2/accounts"
        self.account_file.parent.mkdir(parents=True)
        self.original = "existing\told-password\t20991231\n"
        self.account_file.write_text(self.original)
        (self.account_file.parent / "port").write_text("53")
        self.config = {"hostname": "test.example", "public_ip": "192.0.2.1",
                       "website_ips": ["127.0.0.1"], "key_id": "test-key",
                       "secret": "test-only-not-real"}
        self.native = FixtureNative(self.config, root=root)
        self.store = Store(root / "state")
        self.engine = Engine(self.config, self.native, self.store, str(root / "lock"))
        self.client = TestClient(app_for(self.config, self.engine), client=("127.0.0.1", 12345))
        self.addCleanup(self.client.close)

    def headers(self, method, path, raw=b"", key=None):
        now, nonce = str(int(time.time())), uuid.uuid4().hex
        canonical = "\n".join(("VL1-HMAC-SHA256", method, path, now, nonce,
                              hashlib.sha256(raw).hexdigest()))
        headers = {"X-Node-Key-Id": self.config["key_id"], "X-Node-Timestamp": now,
                   "X-Node-Nonce": nonce, "X-Request-Id": str(uuid.uuid4()),
                   "Content-Type": "application/json",
                   "X-Node-Signature": "v1=" + hmac.new(self.config["secret"].encode(),
                                                      canonical.encode(), hashlib.sha256).hexdigest()}
        if key:
            headers["Idempotency-Key"] = key
        return headers

    def request(self, method, path, body=None, key=None):
        raw = json.dumps(body).encode() if body is not None else b""
        return self.client.request(method, path, content=raw,
                                   headers=self.headers(method, path, raw, key))

    def body(self):
        return {"request_id": str(uuid.uuid4()), "protocol": "hysteria2",
                "username": "traveller", "password": "new-password", "duration_days": 7}

    def test_create_replay_lookup_renew_delete(self):
        body = self.body()
        response = self.request("POST", "/api/v1/accounts", body, body["request_id"])
        self.assertEqual(response.status_code, 201, response.text)
        account = response.json()["data"]["account"]
        identity = account["account_id"]
        self.assertIn("hy2://", account["config"]["uri"])
        self.assertIn(self.original, self.account_file.read_text())
        self.assertEqual(self.account_file.read_text().count("traveller\t"), 1)
        replay = self.request("POST", "/api/v1/accounts", body, body["request_id"])
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.json()["data"]["replayed"])
        self.assertEqual(replay.json()["data"]["account"], account)
        altered = {**body, "username": "someone"}
        conflict = self.request("POST", "/api/v1/accounts", altered, body["request_id"])
        self.assertEqual(conflict.json()["error"]["code"], "IDEMPOTENCY_CONFLICT")
        lookup = self.request("GET", "/api/v1/accounts/by-request/" + body["request_id"])
        self.assertEqual(lookup.json()["data"]["account"]["account_id"], identity)
        renew = {"request_id": str(uuid.uuid4()), "duration_days": 3}
        result = self.request("POST", f"/api/v1/accounts/{identity}/renew", renew, renew["request_id"])
        self.assertEqual(result.status_code, 200, result.text)
        self.assertGreater(result.json()["data"]["account"]["expires_at"], account["expires_at"])
        result = self.request("DELETE", f"/api/v1/accounts/{identity}", key=str(uuid.uuid4()))
        self.assertEqual(result.json()["data"], {"revoked": True})
        self.assertEqual(self.account_file.read_text(), self.original)
        result = self.request("DELETE", f"/api/v1/accounts/{identity}", key=str(uuid.uuid4()))
        self.assertEqual(result.status_code, 404)

    def test_read_routes_and_unknown(self):
        for path in ("/info", "/protocols", "/accounts"):
            response = self.request("GET", "/api/v1" + path)
            self.assertEqual(response.status_code, 200, response.text)
        for path in ("/docs", "/openapi.json", "/api/v1/info/"):
            response = self.request("GET", path)
            self.assertEqual(response.status_code, 404)
            self.assertNotIn("location", response.headers)

    def test_invalid_username_and_password_do_not_mutate(self):
        for changes in ({"username": "root"}, {"username": "a; reboot"},
                        {"password": "short"}, {"duration_days": True}):
            body = {**self.body(), **changes}
            response = self.request("POST", "/api/v1/accounts", body, body["request_id"])
            self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(self.account_file.read_text(), self.original)

    def test_unsupported_protocols_never_write(self):
        for protocol in ("vmess", "vless", "trojan", "hysteria", "zivpn"):
            body = {**self.body(), "protocol": protocol}
            response = self.request("POST", "/api/v1/accounts", body, body["request_id"])
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["error"]["code"], "PROTOCOL_UNAVAILABLE")
        self.assertEqual(self.account_file.read_text(), self.original)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM requests").fetchone()[0], 0)

    def test_source_acl_and_body_limit(self):
        with TestClient(app_for(self.config, self.engine), client=("192.0.2.25", 1234)) as client:
            response = client.get("/api/v1/info", headers=self.headers("GET", "/api/v1/info"))
            self.assertEqual(response.status_code, 401)
        body = b"x" * 65537
        response = self.client.post("/api/v1/accounts", content=body,
                                    headers=self.headers("POST", "/api/v1/accounts", body))
        self.assertEqual(response.status_code, 413)

    def test_replay_and_bad_signature(self):
        headers = self.headers("GET", "/api/v1/info")
        self.assertEqual(self.client.get("/api/v1/info", headers=headers).status_code, 200)
        response = self.client.get("/api/v1/info", headers=headers)
        self.assertEqual(response.json()["error"]["code"], "REPLAYED_NONCE")
        self.assertEqual(self.client.get("/api/v1/info").status_code, 401)

    def test_pending_blocks_duplicate_native_mutations(self):
        with self.store.connect() as db:
            db.execute("INSERT INTO requests(id,hash,state,operation,document) VALUES (?,?,?,?,?)",
                       (str(uuid.uuid4()), "hash", "pending", "create", "{}"))
        body = self.body()
        response = self.request("POST", "/api/v1/accounts", body, body["request_id"])
        self.assertEqual(response.json()["error"]["code"], "REQUEST_IN_PROGRESS")
        self.assertEqual(self.account_file.read_text(), self.original)

    def test_menu_patch_idempotent_and_unit_isolated(self):
        source = pathlib.Path("scripts/ssh-ssl-setup.sh").read_text()
        menu = source.split("<<'MENUEOF'\n", 1)[1].split("\nMENUEOF", 1)[0]
        self.assertEqual(coordinated_menu(coordinated_menu(menu)), coordinated_menu(menu))
        unit = service_text()
        self.assertNotIn("xray.service", unit)
        self.assertIn("Restart=always", unit)

    def test_port_checks_are_transport_specific(self):
        def inspect(argv):
            if argv[0] in ("iptables", "ip6tables"):
                return SimpleNamespace(stdout="-A PREROUTING -p udp --dport 6000:19999 -j DNAT --to-destination :5667")
            return SimpleNamespace(stdout="")
        with patch("vpn_node_api.control.Path.exists", return_value=False), \
             patch("vpn_node_api.control.run", side_effect=inspect), \
             patch("vpn_node_api.control.socket.socket") as sock:
            require_safe_port(9443)
            sock.return_value.bind.assert_called_once_with(("0.0.0.0", 9443))

    def test_tcp_conflicts_fail_before_binding(self):
        for rule in ("-A PREROUTING -p tcp --dport 9443 -j REDIRECT --to-ports 443",
                     "-A PREROUTING -p tcp -j REDIRECT --to-ports 443"):
            with patch("vpn_node_api.control.Path.exists", return_value=False), \
                 patch("vpn_node_api.control.run", return_value=SimpleNamespace(stdout=rule)), \
                 patch("vpn_node_api.control.socket.socket") as sock:
                with self.assertRaises(ValueError):
                    require_safe_port(9443)
                sock.assert_not_called()

    def test_ssh_creation_uses_valid_gecos_and_stdin_password(self):
        native = Native(self.config)
        account = {"protocol": "ssh", "username": "apitest_fixture",
                   "account_id": "acc_fixture", "expires_at": "2099-01-02T00:00:00Z",
                   "config": {"password": "test-password"}}
        calls = []
        def command(argv, data=None, **kwargs):
            calls.append((argv, data))
            if argv[0] == "useradd":
                comment = argv[argv.index("-c") + 1]
                self.assertNotIn(":", comment)
                self.assertNotIn("\n", comment)
                self.assertEqual(comment, "vpn-node=acc_fixture")
            return SimpleNamespace(returncode=0, stdout="")
        with patch.object(native, "run", side_effect=command):
            native.ssh_operation("create", account)
        self.assertEqual(calls[1], (["chpasswd"], "apitest_fixture:test-password\n"))
        self.assertTrue(all("test-password" not in str(argv) for argv, _ in calls))
        entry = SimpleNamespace(pw_uid=1001, pw_gecos="vpn-node=acc_fixture")
        with patch("vpn_node_api.native.pwd.getpwnam", return_value=entry):
            self.assertTrue(native.owns(account))
            self.assertFalse(native.owns({**account, "account_id": "acc_other"}))


if __name__ == "__main__":
    unittest.main()
