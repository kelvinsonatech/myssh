"""Isolated whitelist, firewall transaction and HTTP source-ACL tests."""
import hashlib
import hmac
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/node-api"))
from fastapi.testclient import TestClient
from vpn_node_api import access, control
from vpn_node_api.common import Store, write_json
from vpn_node_api.server import app_for


class Whitelist(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "config.json"
        self.cfg = {"port": 9444, "website_ips": ["192.0.2.1"], "key_id": "test",
                    "secret": "fixture-only", "hostname": "example.test"}
        write_json(self.path, self.cfg)
        self.sources = ["192.0.2.1/32"]
        self.foreign_reference = False
        self.calls = []
        self.patcher = patch.object(access, "command", side_effect=self.command)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def command(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if argv == ["iptables", "-w", "5", "-S"]:
            if self.foreign_reference:
                return SimpleNamespace(stdout="-A FORWARD -p udp -j VPN_NODE_API\n")
            return SimpleNamespace(stdout='-A INPUT -p tcp -m tcp --dport 9444 -m comment --comment "vpn-node-api-owned" -j VPN_NODE_API\n')
        if argv == ["iptables", "-w", "5", "-S", access.CHAIN]:
            return SimpleNamespace(stdout="\n".join(
                ["-N VPN_NODE_API"] +
                [f"-A VPN_NODE_API -s {s} -j ACCEPT" for s in self.sources] +
                ["-A VPN_NODE_API -j DROP"]))
        if argv == ["iptables-restore", "--wait", "5", "--noflush"]:
            lines = kwargs["input"].splitlines()
            self.assertEqual(lines[:2], ["*filter", "-F VPN_NODE_API"])
            self.assertEqual(lines[-2:], ["-A VPN_NODE_API -j DROP", "COMMIT"])
            # Token positions: -A, chain, -s, source, -j, ACCEPT.
            self.sources = [line.split()[3] for line in lines if line.endswith("-j ACCEPT")]
            return SimpleNamespace(stdout="")
        self.fail("Unexpected command: " + str(argv))

    def test_normalization_and_validation(self):
        self.assertEqual(access.normalize(["192.0.2.1", "192.0.2.1/32"]), ["192.0.2.1/32"])
        self.assertEqual(str(access.network("160.79.106.19/24")), "160.79.106.0/24")
        for value in ("0.0.0.0/0", "::1", "example.com", "1.2.3.4; reboot", "", "invalid"):
            with self.assertRaises(ValueError):
                access.network(value)
        with self.assertRaises(ValueError):
            access.normalize([])

    def test_add_remove_preserve_credentials_and_no_restarts(self):
        values, changed = access.change(self.path, "add", "160.79.106.0/24")
        self.assertTrue(changed)
        self.assertIn("160.79.106.0/24", values)
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved["secret"], self.cfg["secret"])
        self.assertEqual(saved["port"], 9444)
        self.assertEqual(self.sources, values)
        self.assertFalse(access.change(self.path, "add", "160.79.106.0/24")[1])
        values, _ = access.change(self.path, "remove", "160.79.106.0/24")
        self.assertEqual(values, ["192.0.2.1/32"])
        self.assertEqual(self.sources, values)
        self.assertFalse(self.path.with_name("whitelist.pending.json").exists())
        self.assertTrue(all(argv[0] in ("iptables", "iptables-restore") for argv, _ in self.calls))

    def test_last_entry_and_foreign_reference_refused(self):
        with self.assertRaises(ValueError):
            access.change(self.path, "remove", "192.0.2.1")
        self.foreign_reference = True
        with self.assertRaisesRegex(ValueError, "unrelated"):
            access.change(self.path, "add", "160.79.106.0/24")
        self.assertEqual(json.loads(self.path.read_text()), self.cfg)
        self.assertEqual(self.sources, ["192.0.2.1/32"])

    def test_write_failure_rolls_back_firewall(self):
        original = access.write_json
        def write(path, value):
            if Path(path) == self.path:
                raise OSError("fixture disk full")
            original(path, value)
        with patch.object(access, "write_json", side_effect=write):
            with self.assertRaises(OSError):
                access.change(self.path, "add", "160.79.106.0/24")
        self.assertEqual(self.sources, ["192.0.2.1/32"])
        self.assertEqual(json.loads(self.path.read_text()), self.cfg)
        self.assertFalse(self.path.with_name("whitelist.pending.json").exists())

    def test_crash_recovery_uses_committed_config(self):
        old, new = ["192.0.2.1/32"], ["192.0.2.1/32", "160.79.106.0/24"]
        for committed in (old, new):
            write_json(self.path, {**self.cfg, "website_ips": committed})
            write_json(self.path.with_name("whitelist.pending.json"),
                       {"old": old, "new": new, "port": 9444})
            self.sources = new
            access.recover(self.path)
            self.assertEqual(self.sources, committed)

    def test_http_live_change_and_forwarded_header_not_trusted(self):
        store = Store(Path(self.temp.name) / "state")
        engine = SimpleNamespace(store=store, info=lambda: {"status": "online"})
        app = app_for(self.cfg, engine,
                      access_loader=lambda: json.loads(self.path.read_text())["website_ips"])
        def headers():
            timestamp, nonce = str(int(time.time())), uuid.uuid4().hex
            message = "\n".join(("VL1-HMAC-SHA256", "GET", "/api/v1/info",
                                  timestamp, nonce, hashlib.sha256(b"").hexdigest()))
            return {"X-Node-Key-Id": "test", "X-Node-Timestamp": timestamp,
                    "X-Node-Nonce": nonce, "X-Request-Id": str(uuid.uuid4()),
                    "X-Node-Signature": "v1=" + hmac.new(self.cfg["secret"].encode(),
                                                       message.encode(), hashlib.sha256).hexdigest()}
        with TestClient(app, client=("160.79.106.19", 1234)) as client:
            self.assertEqual(client.get("/api/v1/info", headers=headers()).status_code, 401)
            access.change(self.path, "add", "160.79.106.0/24")
            self.assertEqual(client.get("/api/v1/info", headers=headers()).status_code, 200)
            self.assertEqual(client.get("/api/v1/info").status_code, 401)
            access.change(self.path, "remove", "160.79.106.0/24")
            self.assertEqual(client.get("/api/v1/info", headers=headers()).status_code, 401)
        with TestClient(app, client=("160.79.107.19", 1234)) as client:
            access.change(self.path, "add", "160.79.106.0/24")
            spoof = {**headers(), "X-Forwarded-For": "160.79.106.19"}
            self.assertEqual(client.get("/api/v1/info", headers=spoof).status_code, 401)

    def test_readiness_checks_pid_not_blocked_loopback(self):
        for listener, expected in (('users:(("python3",pid=123,fd=4))', True),
                                   ('users:(("other",pid=456,fd=4))', False)):
            with patch.object(control, "run", side_effect=[
                SimpleNamespace(stdout="123\n"), SimpleNamespace(stdout=listener)
            ]) as run:
                self.assertEqual(control.api_ready(9444), expected)
                self.assertEqual([c.args[0][0] for c in run.call_args_list], ["systemctl", "ss"])

    def test_api_only_update_preserves_config_and_adds_menu(self):
        root = Path(self.temp.name)
        home = root / "opt"
        package = home / "vpn_node_api"
        package.mkdir(parents=True)
        (package / "native.py").write_text("# old fixture\n")
        unit = root / "api.service"
        unit.write_text("fixture unit")
        menu = root / "menu"
        source = Path("scripts/ssh-ssl-setup.sh").read_text()
        menu.write_text(source.split("<<'MENUEOF'\n", 1)[1].split("\nMENUEOF", 1)[0])
        saved = self.path.read_bytes()
        with patch.object(control, "CONFIG", self.path), patch.object(control, "HOME", home), \
             patch.object(control, "UNIT", unit), patch.object(control, "run") as run, \
             patch.object(control.subprocess, "run", return_value=SimpleNamespace(returncode=0)):
            self.assertTrue(control.update(menu))
            commands = [call.args[0] for call in run.call_args_list]
            self.assertEqual(commands, [["bash", "-n"], ["systemctl", "stop", "vpn-node-api.service"]])
        self.assertEqual(self.path.read_bytes(), saved)
        self.assertTrue((package / "access.py").exists())
        self.assertIn("vpn-node-api whitelist", menu.read_text())
        self.assertEqual(unit.read_text(), "fixture unit")


if __name__ == "__main__":
    unittest.main()
