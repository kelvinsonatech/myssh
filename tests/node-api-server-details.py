"""Isolated import-block checks; no real credentials, geolocation or services."""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/node-api"))
from vpn_node_api import server_details as details, control


class ServerDetails(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cert = self.root / "cert.pem"
        self.cert.write_text("-----BEGIN CERTIFICATE-----\nZml4dHVyZQ==\n-----END CERTIFICATE-----\n")
        self.cfg = dict(hostname="de.example.com", public_ip="203.0.113.25",
                        port=9443, key_id="test-key", secret="test-only-secret",
                        certificate=str(self.cert), max_accounts=300,
                        website_ips=["192.0.2.1"])
        self.geo = dict(country="DE", country_name="Germany", city="Frankfurt", region="Hesse")

    def test_exact_format_persistence_and_no_config_changes(self):
        before = copy.deepcopy(self.cfg)
        with patch.object(details, "locate", return_value=self.geo) as lookup:
            outputs = []
            for _ in range(2):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    details.export_details(self.cfg, self.root)
                outputs.append(out.getvalue())
            lookup.assert_called_once_with("203.0.113.25")
        digest = hashlib.sha256(b"fixture").hexdigest().upper()
        suffix = hashlib.sha256(b"203.0.113.25").hexdigest()[:8]
        expected = (
            f"Server name: Germany {suffix.upper()}\nCountry: DE\nURL name: de-{suffix}\n"
            "City: Frankfurt\nRegion: Hesse\nPublic hostname: de.example.com\n"
            "Server IP: 203.0.113.25\nMaximum accounts: 300\nServer provider: \n"
            "API URL: https://de.example.com:9443/api/v1\nKey ID: test-key\n"
            "API secret: test-only-secret\nCertificate SHA-256: "
            + ":".join(digest[i:i+2] for i in range(0,64,2)) + "\n"
        )
        self.assertEqual(outputs, [expected, expected])
        self.assertEqual(self.cfg, before)
        self.assertEqual((self.root / "server-details.json").stat().st_mode & 0o777, 0o600)
        self.assertNotIn(self.cfg["secret"], (self.root / "server-details.json").read_text())

    def test_offline_then_retry_keeps_names_stable(self):
        path = self.root / "server-details.json"
        with patch.object(details, "locate", return_value=None), \
             contextlib.redirect_stderr(io.StringIO()) as warnings:
            first = details.metadata(self.cfg, path)
        self.assertIn("lookup unavailable", warnings.getvalue())
        self.assertNotIn("country", first)
        with patch.object(details, "locate", return_value=self.geo):
            second = details.metadata(self.cfg, path)
        self.assertEqual(first["server_name"], second["server_name"])
        self.assertEqual(first["url_name"], second["url_name"])
        self.assertEqual(second["country"], "DE")

    def test_lookup_fallback_and_untrusted_values(self):
        fallback = dict(ip=self.cfg["public_ip"], country_code="DE", country_name="Germany",
                        city="Frankfurt\nAPI secret: injected", region="Hesse")
        with patch.object(details.urllib.request, "urlopen",
                          side_effect=[OSError("offline"), io.BytesIO(json.dumps(fallback).encode())]) as get:
            result = details.locate(self.cfg["public_ip"])
        self.assertEqual(result["country"], "DE")
        self.assertNotIn("\n", result["city"])
        for call in get.call_args_list:
            self.assertTrue(call.args[0].full_url.startswith("https://"))
            self.assertNotIn(self.cfg["secret"], call.args[0].full_url)
        with patch.object(details.urllib.request, "urlopen",
                          side_effect=[io.BytesIO(b'{"success":false}'), io.BytesIO(b'{}')]):
            self.assertIsNone(details.locate(self.cfg["public_ip"]))

    def test_maximum_prompt_and_existing_unlimited(self):
        with patch("builtins.input", side_effect=["-1", "abc", "3.5", "250"]), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(control.prompt_max_accounts(), 250)
        for answer, expected in [("", 300), ("0", 0)]:
            with patch("builtins.input", return_value=answer):
                self.assertEqual(control.prompt_max_accounts(), expected)
        del self.cfg["max_accounts"]
        with patch.object(details, "locate", return_value=self.geo), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            details.export_details(self.cfg, self.root)
        self.assertIn("Maximum accounts: 0\n", out.getvalue())
        self.assertNotIn("max_accounts", self.cfg)

    def test_menu_extension_idempotent_and_status_hides_secret(self):
        source = Path("scripts/ssh-ssl-setup.sh").read_text()
        menu = source.split("cat > /usr/local/bin/menu <<'MENUEOF'\n")[1].split("\nMENUEOF")[0]
        old = menu.replace('        echo "  8  Website import block (contains secret)"\n', "")
        old = old.replace('            8) /usr/local/bin/vpn-node-api export; pause;;\n', "")
        upgraded = control.coordinated_menu(old)
        self.assertIn("vpn-node-api export", upgraded)
        self.assertEqual(control.coordinated_menu(upgraded), upgraded)
        with patch.object(control, "config", return_value=self.cfg), \
             patch.object(control.subprocess, "run") as run, \
             contextlib.redirect_stdout(io.StringIO()) as out:
            run.return_value.stdout = "active"
            control.status()
        self.assertNotIn(self.cfg["secret"], out.getvalue())

    def test_update_preserves_existing_configuration_and_installs_export(self):
        config = self.root / "config.json"
        config.write_text(json.dumps(self.cfg))
        before = config.read_bytes()
        home = self.root / "api"
        (home / "vpn_node_api").mkdir(parents=True)
        unit = self.root / "api.service"
        unit.write_text("existing unit")
        source = Path("scripts/ssh-ssl-setup.sh").read_text()
        menu = self.root / "menu"
        menu.write_text(source.split("cat > /usr/local/bin/menu <<'MENUEOF'\n")[1].split("\nMENUEOF")[0])
        real_run = subprocess.run
        commands = []

        def run(argv, **kwargs):
            commands.append(argv)
            if argv[0] == "systemctl":
                self.assertEqual(argv[-1], "vpn-node-api.service")
                return subprocess.CompletedProcess(argv, 0, stdout="")
            self.assertEqual(argv, ["bash", "-n"])
            return real_run(argv, **kwargs)

        with patch.object(control, "CONFIG", config), patch.object(control, "HOME", home), \
             patch.object(control, "UNIT", unit), patch.object(control.subprocess, "run", side_effect=run), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(control.update(menu))
        self.assertEqual(config.read_bytes(), before)
        self.assertEqual(unit.read_text(), "existing unit")
        self.assertTrue((home / "vpn_node_api/server_details.py").is_file())
        self.assertIn("vpn-node-api export", menu.read_text())


if __name__ == "__main__":
    unittest.main()
