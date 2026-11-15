"""Isolated domain menu checks: no real DNS, certificates or services."""
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

SOURCE = Path("scripts/ssh-ssl-setup.sh").read_text()
HELPER = SOURCE.split("<<'DOMPY'\n", 1)[1].split("\nDOMPY", 1)[0]


class DomainChange(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.m = types.ModuleType("domain_change")
        exec(compile(HELPER, "domain-helper", "exec"), self.m.__dict__)
        self.files = {
            "/etc/ssh-panel/domain.conf": "old.example.com\n",
            "/etc/ssh-panel/ip.conf": "203.0.113.1\n",
            "/etc/stunnel/stunnel.conf": "cert = /etc/stunnel/stunnel.pem\naccept = 447\n",
            "/etc/stunnel/stunnel.pem": "old pem",
            "/etc/vpn-node-api/config.json": json.dumps(dict(
                hostname="old.example.com", port=9443, key_id="fixture",
                secret="fixture-only", max_accounts=500, website_ips=["192.0.2.1"],
                certificate="/old/cert", private_key="/old/key")),
            "/usr/local/etc/xray/config.json": json.dumps(dict(inbounds=[
                dict(port=443, protocol="vmess", settings={"clients": [{"id": "unchanged"}]},
                     streamSettings={"security": "tls", "tlsSettings": {"certificates": [
                         {"certificateFile": "/old/cert", "keyFile": "/old/key"}]}})])),
            "/etc/letsencrypt/live/new.example.com/fullchain.pem": "new cert",
            "/etc/letsencrypt/live/new.example.com/privkey.pem": "new key",
            "/etc/xray/accounts.txt": "unchanged accounts",
            "/etc/ssh-panel/nsdomain.conf": "ns.old.example.com",
        }
        for name, value in self.files.items():
            path = self.path(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value)
        self.commands = []
        self.patches = [
            patch.object(self.m, "Path", self.path),
            patch.object(self.m.os, "geteuid", return_value=0),
            patch.object(self.m.os, "chown"),
            patch.object(self.m, "certificate"),
            patch.object(self.m, "active", return_value=True),
            patch.object(self.m, "run", side_effect=self.command),
            patch.object(self.m.time, "sleep"),
            patch.object(self.m.socket, "getaddrinfo", return_value=[(0, 0, 0, "", ("203.0.113.1", 80))]),
            patch("builtins.input", side_effect=["new.example.com", "CHANGE"]),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def path(self, name):
        path = Path(name)
        if str(path).startswith(str(self.root)):
            return path
        return self.root / str(path).lstrip("/")

    def command(self, *args):
        self.commands.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="root\n" if "show" in args else "", stderr="")

    def test_success_changes_only_domain_and_certificate_fields(self):
        before = json.loads(self.files["/etc/vpn-node-api/config.json"])
        xbefore = json.loads(self.files["/usr/local/etc/xray/config.json"])
        self.m.main()
        api = json.loads(self.path("/etc/vpn-node-api/config.json").read_text())
        for key in before:
            if key not in ("hostname", "certificate", "private_key"):
                self.assertEqual(api[key], before[key])
        self.assertEqual(api["hostname"], "new.example.com")
        xafter = json.loads(self.path("/usr/local/etc/xray/config.json").read_text())
        xafter["inbounds"][0]["streamSettings"] = copy.deepcopy(xbefore["inbounds"][0]["streamSettings"])
        self.assertEqual(xafter, xbefore)
        for name in ("/etc/xray/accounts.txt", "/etc/ssh-panel/nsdomain.conf", "/etc/stunnel/stunnel.conf"):
            self.assertEqual(self.path(name).read_text(), self.files[name])
        restarted = [x[-1] for x in self.commands if x[:2] == ("systemctl", "restart")]
        self.assertEqual(restarted, ["stunnel4", "xray", "vpn-node-api"])

    def test_failed_service_restores_every_original_file(self):
        def fail(*args):
            if args == ("systemctl", "restart", "xray"):
                raise subprocess.CalledProcessError(1, args)
            return self.command(*args)
        with patch.object(self.m, "run", side_effect=fail):
            with self.assertRaises(subprocess.CalledProcessError):
                self.m.main()
        for name, value in self.files.items():
            self.assertEqual(self.path(name).read_text(), value)
        self.assertFalse(self.path("/etc/xray/domain.key").exists())
        self.assertFalse(self.path("/etc/vpn-node-api/domain.key").exists())

    def test_dns_mismatch_never_changes_files(self):
        with patch.object(self.m.socket, "getaddrinfo", return_value=[(0, 0, 0, "", ("192.0.2.9", 80))]):
            with self.assertRaises(ValueError):
                self.m.main()
        self.assertEqual(self.commands, [])
        self.assertEqual(self.path("/etc/ssh-panel/domain.conf").read_text(), "old.example.com\n")

    def test_cancel_does_not_write(self):
        with patch("builtins.input", side_effect=["new.example.com", "NO"]):
            self.m.main()
        self.assertEqual(self.commands, [])
        for name, value in self.files.items():
            self.assertEqual(self.path(name).read_text(), value)

    def test_independent_api_hostname_untouched(self):
        cfg = self.path("/etc/vpn-node-api/config.json")
        data = json.loads(cfg.read_text())
        data["hostname"] = "api.example.com"
        cfg.write_text(json.dumps(data))
        before = cfg.read_bytes()
        self.m.main()
        self.assertEqual(cfg.read_bytes(), before)
        self.assertNotIn(("systemctl", "restart", "vpn-node-api"), self.commands)

    def test_certificate_failure_restores_proxy_without_domain_changes(self):
        def command(*args):
            self.commands.append(args)
            if args[0] == "certbot":
                raise subprocess.CalledProcessError(1, args)
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        with patch.object(self.m, "certificate", side_effect=ValueError("missing cert")), \
             patch.object(self.m.shutil, "which", return_value="/usr/bin/certbot"), \
             patch.object(self.m, "run", side_effect=command):
            with self.assertRaises(subprocess.CalledProcessError):
                self.m.main()
        self.assertIn(("systemctl", "stop", "ws-proxy"), self.commands)
        self.assertIn(("systemctl", "start", "ws-proxy"), self.commands)
        self.assertFalse(any(x[:2] == ("systemctl", "restart") for x in self.commands))
        for name, value in self.files.items():
            self.assertEqual(self.path(name).read_text(), value)

    def test_stopped_services_are_not_started(self):
        with patch.object(self.m, "active", return_value=False):
            self.m.main()
        self.assertFalse(any(x[:2] in (("systemctl", "restart"), ("systemctl", "start"))
                             for x in self.commands))


if __name__ == "__main__":
    unittest.main()
