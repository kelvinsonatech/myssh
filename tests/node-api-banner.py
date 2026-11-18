"""Banner config safety checks; no real service changes."""
import contextlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/node-api"))
from vpn_node_api import banner, control


class BannerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name, filename in (("CONFIG", "api.json"), ("SSHD", "sshd_config"),
                               ("HTML_FILE", "banner.html"), ("BACKUP", "backup"),
                               ("LOCK", "banner.lock")):
            p = patch.object(banner, name, self.root / filename)
            p.start()
            self.addCleanup(p.stop)
        self.original = b"# Existing settings\nPort 22\nPasswordAuthentication yes\nBanner /old/banner\n"
        banner.SSHD.write_bytes(self.original)
        banner.CONFIG.write_text('{"secret":"fixture-only","max_accounts":300}')
        self.commands = []
        p = patch.object(banner, "active", return_value=True)
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(banner, "run", side_effect=self.command)
        p.start()
        self.addCleanup(p.stop)

    def command(self, argv):
        self.commands.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=f"banner {banner.HTML_FILE}\n", stderr="")

    def test_enable_disable_preserves_every_existing_setting(self):
        cfg = banner.CONFIG.read_bytes()
        banner.set_enabled(True)
        self.assertEqual(banner.HTML_FILE.read_text(), banner.HTML + "\n")
        self.assertEqual(banner.SSHD.read_bytes(), banner.block().encode() + self.original)
        self.assertEqual(banner.BACKUP.read_bytes(), self.original)
        banner.set_enabled(False)
        self.assertEqual(banner.SSHD.read_bytes(), self.original)
        self.assertEqual(banner.CONFIG.read_bytes(), cfg)
        self.assertTrue(any(cmd == ["systemctl", "reload", "ssh.service"] for cmd in self.commands))
        self.assertFalse(any("restart" in cmd or "dropbear" in " ".join(cmd) for cmd in self.commands))

    def test_requires_api_activation(self):
        with patch.object(banner, "active", return_value=False):
            with self.assertRaisesRegex(ValueError, "Activate"):
                banner.set_enabled(True)
        self.assertEqual(banner.SSHD.read_bytes(), self.original)
        self.assertFalse(banner.HTML_FILE.exists())

    def test_no_api_installation_cannot_enable(self):
        banner.CONFIG.unlink()
        with self.assertRaises(ValueError):
            banner.set_enabled(True)
        self.assertEqual(self.commands, [])

    def test_disable_allowed_with_api_stopped(self):
        banner.set_enabled(True)
        with patch.object(banner, "active", side_effect=lambda unit: unit == "ssh.service"):
            banner.set_enabled(False)
        self.assertEqual(banner.SSHD.read_bytes(), self.original)

    def test_repeat_enable_does_not_reload_or_duplicate(self):
        banner.set_enabled(True)
        self.commands.clear()
        banner.set_enabled(True)
        self.assertEqual(self.commands, [])
        self.assertEqual(banner.SSHD.read_text().count(banner.BEGIN), 1)

    def test_invalid_config_rolls_back(self):
        def command(argv):
            if argv == ["/usr/sbin/sshd", "-t"] and banner.enabled():
                raise subprocess.CalledProcessError(1, argv)
            return self.command(argv)
        with patch.object(banner, "run", side_effect=command):
            with self.assertRaises(subprocess.CalledProcessError):
                banner.set_enabled(True)
        self.assertEqual(banner.SSHD.read_bytes(), self.original)
        self.assertFalse(banner.HTML_FILE.exists())

    def test_failed_reload_rolls_back(self):
        def command(argv):
            if argv[:2] == ["systemctl", "reload"] and banner.enabled():
                raise subprocess.CalledProcessError(1, argv)
            return self.command(argv)
        with patch.object(banner, "run", side_effect=command):
            with self.assertRaises(subprocess.CalledProcessError):
                banner.set_enabled(True)
        self.assertEqual(banner.SSHD.read_bytes(), self.original)
        self.assertFalse(banner.HTML_FILE.exists())

    def test_customized_managed_block_is_not_overwritten(self):
        banner.SSHD.write_text(banner.BEGIN + "Banner /custom\n" + banner.END)
        before = banner.SSHD.read_bytes()
        with self.assertRaises(ValueError):
            banner.set_enabled(True)
        self.assertEqual(banner.SSHD.read_bytes(), before)

    def test_auto_enable_failure_does_not_undo_api(self):
        with patch.object(banner, "set_enabled", side_effect=ValueError("test")), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            banner.auto_enable()
        self.assertIn("API is active", out.getvalue())

    def test_upgrade_adds_menu_without_enabling_banner(self):
        source = Path("scripts/ssh-ssl-setup.sh").read_text()
        menu = source.split("cat > /usr/local/bin/menu <<'MENUEOF'\n")[1].split("\nMENUEOF")[0]
        start = menu.index('            9)\n                if [ -f /opt/vpn-node-api/')
        end = menu.index('                pause;;', start) + len('                pause;;\n')
        old = menu[:start] + menu[end:]
        old = old.replace('        echo "  9  SSH banner (on / off)"\n', '')
        updated = control.coordinated_menu(old)
        self.assertIn("vpn-node-api banner", updated)
        self.assertEqual(updated, control.coordinated_menu(updated))
        self.assertFalse(banner.enabled())


if __name__ == "__main__":
    unittest.main()
