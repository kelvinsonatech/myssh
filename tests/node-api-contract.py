"""Isolated HTTP contract and native-file tests. No host users/services changed."""
import base64
import json
import pathlib
import sys
import tempfile
import time
import unittest
import uuid
import hashlib
import hmac
import datetime as dt
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

    def test_exact_expiry_and_renewal_survive_replay_and_storage(self):
        created = dt.datetime(2096, 2, 28, 20, 17, 39, 123456, tzinfo=dt.timezone.utc)
        body = {**self.body(), "duration_days": 1}
        with patch("vpn_node_api.native.utcnow", return_value=created):
            response = self.request("POST", "/api/v1/accounts", body, body["request_id"])
        self.assertEqual(response.status_code, 201, response.text)
        account = response.json()["data"]["account"]
        self.assertEqual(account["created_at"], "2096-02-28T20:17:39Z")
        self.assertEqual(account["expires_at"], "2096-02-29T20:17:39Z")
        self.assertEqual(dt.datetime.fromisoformat(account["expires_at"]) -
                         dt.datetime.fromisoformat(account["created_at"]), dt.timedelta(hours=24))
        self.assertIn("traveller\t", self.account_file.read_text())
        self.assertIn("\t20960229\n", self.account_file.read_text())
        self.assertIn(self.original, self.account_file.read_text())
        identity = account["account_id"]
        replay = self.request("POST", "/api/v1/accounts", body, body["request_id"])
        self.assertEqual(replay.json()["data"]["account"], account)
        self.assertEqual(self.store.account(identity)["expires_at"], account["expires_at"])
        for now, expected in ((created, "2096-03-01T20:17:39Z"),
                              (dt.datetime(2096, 12, 31, 23, 59, 59, tzinfo=dt.timezone.utc),
                               "2097-01-01T23:59:59Z")):
            renew = {"request_id": str(uuid.uuid4()), "duration_days": 1}
            with patch("vpn_node_api.engine.utcnow", return_value=now):
                response = self.request("POST", f"/api/v1/accounts/{identity}/renew",
                                        renew, renew["request_id"])
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["data"]["account"]["expires_at"], expected)
        info = self.request("GET", "/api/v1/info").json()["data"]
        self.assertIs(info["capabilities"]["expiry_managed_by_node"], False)

    def test_ssh_family_exact_creation_and_next_date_safety_net(self):
        created = dt.datetime(2099, 12, 30, 20, 0, tzinfo=dt.timezone.utc)
        for protocol in ("ssh", "ssh-ssl", "slowdns"):
            with self.subTest(protocol=protocol):
                body = {**self.body(), "protocol": protocol, "duration_days": 1,
                        "username": "api_" + protocol.replace("-", "_")}
                calls = []
                def command(argv, data=None, **kwargs):
                    self.assertIn(argv[0], ("useradd", "chpasswd", "chage"))
                    calls.append(argv)
                    return SimpleNamespace(returncode=0, stdout="")
                with patch.object(self.native, "installed", return_value=True), \
                     patch.object(self.native, "running", return_value=True), \
                     patch.object(self.native, "connection", return_value={"password": "test-password"}), \
                     patch("vpn_node_api.native.pwd.getpwnam", side_effect=KeyError), \
                     patch("vpn_node_api.native.utcnow", return_value=created), \
                     patch.object(self.native, "run", side_effect=command):
                    response = self.request("POST", "/api/v1/accounts", body, body["request_id"])
                    self.assertEqual(response.status_code, 201, response.text)
                    account = response.json()["data"]["account"]
                    self.assertEqual(account["created_at"], "2099-12-30T20:00:00Z")
                    self.assertEqual(account["expires_at"], "2099-12-31T20:00:00Z")
                    self.assertEqual(calls[0][0:3], ["useradd", "-e", "2100-01-01"])
                    self.native.ssh_operation("renew", account)
                    self.assertEqual(calls[-1], ["chage", "-E", "2100-01-01", body["username"]])
                self.assertEqual(self.account_file.read_text(), self.original)

    def test_multi_login_suspension_is_reported_and_blocks_renewal(self):
        root = self.native.root
        body = {**self.body(), "protocol": "ssh", "username": "sharer"}
        calls = []
        def command(argv, data=None, **kwargs):
            self.assertIn(argv[0], ("useradd", "chpasswd", "chage"))
            calls.append(argv)
            return SimpleNamespace(returncode=0, stdout="")
        with patch.object(self.native, "installed", return_value=True), \
             patch.object(self.native, "running", return_value=True), \
             patch.object(self.native, "connection", return_value={"password": "test-password"}), \
             patch("vpn_node_api.native.pwd.getpwnam", side_effect=KeyError), \
             patch.object(self.native, "run", side_effect=command):
            created = self.request("POST", "/api/v1/accounts", body, body["request_id"])
        self.assertEqual(created.status_code, 201, created.text)
        identity = created.json()["data"]["account"]["account_id"]
        owner = SimpleNamespace(pw_uid=1500, pw_gecos="vpn-node=" + identity)

        def get():
            with patch("vpn_node_api.native.pwd.getpwnam", return_value=owner):
                one = self.request("GET", f"/api/v1/accounts/{identity}").json()["data"]["account"]
            listed = self.request("GET", "/api/v1/accounts").json()["data"]["accounts"]
            self.assertEqual([a["status"] for a in listed if a["account_id"] == identity], [one["status"]])
            return one

        def renew():
            payload = {"request_id": str(uuid.uuid4()), "duration_days": 2}
            with patch.object(self.native, "run", side_effect=command), \
                 patch("vpn_node_api.native.pwd.getpwnam", return_value=owner):
                return self.request("POST", f"/api/v1/accounts/{identity}/renew", payload, payload["request_id"])

        self.assertEqual(get()["status"], "active")
        self.assertNotIn("suspension", get())
        self.assertNotIn("disabled_reason", get())
        # The limiter locks the account (state + locked password).
        (root / "etc/vpn-login-limiter").mkdir(parents=True)
        (root / "etc/vpn-login-limiter/config.json").write_text(
            json.dumps({"mode": "enforce", "delete_after": 60}))
        (root / "var/lib/vpn-login-limiter").mkdir(parents=True)
        state = root / "var/lib/vpn-login-limiter/state.json"
        state.write_text(json.dumps({"locked": {"sharer": {"at": 4102444800, "pre_locked": False, "api": True}}}))
        (root / "etc/shadow").write_text("sharer:!$6$hash:1::::::\n")
        account = get()
        self.assertEqual(account["status"], "disabled")
        self.assertEqual(account["disabled_reason"], "multi_login")
        self.assertEqual(account["suspension"], {"reason": "multi_login", "suspended_at": "2100-01-01T00:00:00Z",
                                                 "removed": False, "removal_at": "2100-01-01T01:00:00Z"})
        before = len(calls)
        refused = renew()
        self.assertEqual(refused.status_code, 409)
        self.assertEqual(refused.json()["error"]["code"], "ACCOUNT_SUSPENDED")
        self.assertEqual(len(calls), before)                 # nothing touched on the server
        # Lock-only mode: no removal time. Removed accounts say so.
        (root / "etc/vpn-login-limiter/config.json").write_text(json.dumps({"mode": "enforce", "delete_after": 0}))
        self.assertIsNone(get()["suspension"]["removal_at"])
        state.write_text(json.dumps({"locked": {"sharer": {"at": 4102444800, "deleted": 4102448400}}}))
        self.assertTrue(get()["suspension"]["removed"])
        # Unlocked by the administrator → active again and renewable.
        (root / "etc/shadow").write_text("sharer:$6$hash:1::::::\n")
        self.assertEqual(get()["status"], "active")
        self.assertNotIn("disabled_reason", get())
        self.assertEqual(renew().status_code, 200)
        # Unreadable limiter files never break the API.
        (root / "etc/shadow").write_text("sharer:!$6$hash:1::::::\n")
        state.write_text("{broken")
        self.assertEqual(get()["status"], "active")
        self.assertTrue(self.request("GET", "/api/v1/info").json()["data"]["capabilities"]["suspension_status"])

    def test_linux_safety_date_midnight_and_leap_year(self):
        native = Native(self.config)
        for exact, safety in (("2096-02-28T00:00:00Z", "2096-02-29"),
                              ("2096-02-29T23:59:59Z", "2096-03-01"),
                              ("2100-02-28T20:00:00Z", "2100-03-01")):
            account = {"username": "api_fixture", "expires_at": exact,
                       "config": {"password": "test-password"}}
            with patch.object(native, "run") as command:
                native.ssh_operation("renew", account)
                command.assert_called_once_with(["chage", "-E", safety, "api_fixture"])

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

    def test_v2ray_sub_accounts_share_main_key_and_never_touch_xray(self):
        root = self.native.root
        (root / "etc/xray").mkdir(parents=True)
        (root / "usr/local/etc/xray").mkdir(parents=True)
        accounts = root / "etc/xray/accounts.txt"
        config = root / "usr/local/etc/xray/config.json"
        main_id = "11111111-2222-4333-8444-555555555555"
        accounts.write_text(f"vmess|boss|{main_id}|never|0\n"
                            "vless|capped|22222222-2222-4333-8444-555555555555|never|1073741824\n")
        config.write_text(json.dumps({"inbounds": [
            {"port": 2083, "protocol": "vmess", "settings": {"clients": [{"id": main_id, "email": "boss"}]},
             "streamSettings": {"network": "ws", "security": "tls", "wsSettings": {"path": "/vmess"}}},
            {"port": 8080, "protocol": "vmess", "settings": {"clients": [{"id": main_id, "email": "boss"}]},
             "streamSettings": {"network": "ws", "security": "none", "wsSettings": {"path": "/vmess"}}}]}))
        before = (accounts.read_bytes(), config.read_bytes())
        main_file = root / "etc/xray/api-main.conf"

        def create(name, protocol="vmess", days_=7):
            body = {**self.body(), "protocol": protocol, "username": name, "duration_days": days_}
            body.pop("password")
            return self.request("POST", "/api/v1/accounts", body, body["request_id"])

        installed = lambda protocol: protocol in ("hysteria2", "vmess", "vless", "trojan")
        with patch.object(self.native, "installed", side_effect=installed):
            refused = create("kofi")                                  # no main account chosen yet
            self.assertEqual(refused.status_code, 409)
            self.assertIn("Website sub accounts", refused.json()["error"]["message"])
            main_file.write_text("vmess|boss\nvless|capped\n")
            self.assertEqual(create("ama", "vless").status_code, 409)   # quota main refused
            info = self.request("GET", "/api/v1/protocols").json()["data"]["protocols"]
            vmess = next(p for p in info if p["id"] == "vmess")
            self.assertTrue(vmess["metadata"]["api_provisioning"])
            self.assertEqual(vmess["metadata"]["account_scope"], "shared_main_account")

            first, second = create("kofi").json()["data"]["account"], create("yaw").json()["data"]["account"]
            for account, name in ((first, "kofi"), (second, "yaw")):
                self.assertEqual(account["config"]["uuid"], main_id)
                self.assertEqual(account["config"]["remark"], name)
                self.assertEqual({c["port"] for c in account["config"]["connections"]}, {2083, 8080})
                link = json.loads(base64.b64decode(account["config"]["links"][0]["uri"][8:]))
                self.assertEqual((link["id"], link["ps"].split("-")[0]), (main_id, name))
            self.assertEqual(create("kofi").json()["error"]["code"], "USERNAME_TAKEN")
            self.assertEqual(create("boss").json()["error"]["code"], "USERNAME_TAKEN")
            path = f"/api/v1/accounts/{first['account_id']}"
            self.assertEqual(self.request("GET", path).status_code, 200)
            renew = {"request_id": str(uuid.uuid4()), "duration_days": 30}
            self.assertEqual(self.request("POST", path + "/renew", renew, renew["request_id"]).status_code, 200)
            listed = self.request("GET", "/api/v1/accounts?protocol=vmess").json()["data"]["accounts"]
            self.assertEqual(sorted(a["username"] for a in listed), ["kofi", "yaw"])
            # A main account that expires sooner caps how long sub accounts may last.
            soon = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=10)).date().isoformat()
            accounts.write_text(before[0].decode().replace("|never|0\n", f"|{soon}|0\n", 1))
            self.assertEqual(create("esi", days_=30).status_code, 422)
            self.assertEqual(create("esi", days_=3).status_code, 201)
            accounts.write_bytes(before[0])
            self.assertEqual(self.request("DELETE", path, None, str(uuid.uuid4())).status_code, 200)
            self.assertEqual(self.request("GET", path).status_code, 404)
            # Main account deleted from the menu → its sub accounts are reported gone.
            accounts.write_text("vless|capped|22222222-2222-4333-8444-555555555555|never|1073741824\n")
            self.assertEqual(self.request("GET", f"/api/v1/accounts/{second['account_id']}").status_code, 404)
            accounts.write_bytes(before[0])
        self.assertEqual((accounts.read_bytes(), config.read_bytes()), before)   # Xray untouched
        self.assertEqual(self.account_file.read_text(), self.original)

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
