"""Isolated updater tests: never touch host menu, accounts or services."""
import base64
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("updater", "scripts/update-script.py")
updater = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updater)
OLD = b"#!/bin/bash\nnode_api_menu() { echo old; }\n"
NEW = b"#!/bin/bash\nnode_api_menu() { echo new; }\n"


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        for name, relative in (("MENU", "menu"), ("STATE", "backups"),
                               ("LOCK", "update.lock"), ("API_LOCK", "api.lock"),
                               ("API_CONFIG", "api-config.json"), ("API_HOME", "api")):
            mock = patch.object(updater, name, self.root / relative)
            mock.start()
            self.addCleanup(mock.stop)
        updater.MENU.write_bytes(OLD)

    def test_download_verifies_github_blob_and_size(self):
        blob = hashlib.sha1(b"blob " + str(len(NEW)).encode() + b"\0" + NEW).hexdigest()
        item = {"type": "file", "encoding": "base64", "content": base64.b64encode(NEW).decode(),
                "sha": blob, "size": len(NEW)}
        with patch.object(updater, "github", return_value=item) as request:
            self.assertEqual(updater.download("scripts/example.py", "a" * 40), NEW)
            request.assert_called_once_with("contents/scripts/example.py?ref=" + "a" * 40)
        for change in ({"sha": "0" * 40}, {"size": 1}, {"type": "dir"}):
            with patch.object(updater, "github", return_value={**item, **change}):
                with self.assertRaises(ValueError):
                    updater.download("scripts/example.py", "a" * 40)

    def test_extracts_only_menu_never_executes_installer(self):
        marker = b"cat > /usr/local/bin/menu <<'MENUEOF'\n"
        source = b"exit 99\n" + marker + NEW + b"MENUEOF\nexit 98\n"
        with patch.object(updater, "download", return_value=source):
            self.assertEqual(updater.release_menu("a" * 40), NEW)
        for broken in (source.replace(b"MENUEOF\nexit", b"TRUNCATED\nexit"),
                       source + marker):
            with patch.object(updater, "download", return_value=broken):
                with self.assertRaises(ValueError):
                    updater.release_menu("a" * 40)
        self.assertEqual(updater.MENU.read_bytes(), OLD)

    def test_update_backup_permissions_and_rollback(self):
        real_run = subprocess.run
        commands = []
        def run(argv, **kwargs):
            commands.append(argv)
            self.assertEqual(argv, ["bash", "-n"])
            return real_run(argv, **kwargs)
        with patch.object(updater.subprocess, "run", side_effect=run):
            updater.install_menu(NEW, "a" * 40)
            self.assertEqual(updater.MENU.read_bytes(), NEW)
            self.assertEqual((updater.STATE / "previous-menu").read_bytes(), OLD)
            self.assertEqual(updater.MENU.stat().st_mode & 0o777, 0o755)
            updater.install_menu(NEW, "a" * 40)
            self.assertEqual((updater.STATE / "previous-menu").read_bytes(), OLD)
            updater.rollback()
        self.assertEqual(updater.MENU.read_bytes(), OLD)
        self.assertTrue(commands)

    def test_invalid_menu_or_failed_download_preserves_installed_file(self):
        with self.assertRaises(subprocess.CalledProcessError):
            updater.install_menu(b"#!/bin/bash\nnode_api_menu() { if; }\n", "bad")
        with patch.object(updater, "download", side_effect=OSError("offline")):
            with self.assertRaises(OSError):
                updater.release_menu("a" * 40)
        self.assertEqual(updater.MENU.read_bytes(), OLD)
        self.assertFalse(updater.STATE.exists())

    def test_write_failure_keeps_old_menu_and_backup(self):
        real_replace = updater.os.replace
        def replace(source, target):
            if target == updater.MENU:
                raise OSError("disk error")
            return real_replace(source, target)
        with patch.object(updater.os, "replace", side_effect=replace):
            with self.assertRaises(OSError):
                updater.install_menu(NEW, "a" * 40)
        self.assertEqual(updater.MENU.read_bytes(), OLD)
        self.assertEqual((updater.STATE / "previous-menu").read_bytes(), OLD)

    def test_locks_reject_concurrent_updates(self):
        with updater.update_lock():
            with self.assertRaises(ValueError):
                with updater.update_lock():
                    pass
        with updater.update_lock(updater.API_LOCK):
            with self.assertRaises(ValueError):
                updater.install_menu(NEW, "a" * 40)
        self.assertEqual(updater.MENU.read_bytes(), OLD)

    def test_api_downloads_one_revision_and_calls_only_api_updater(self):
        updater.API_CONFIG.write_text("{}")
        source = Path("scripts/node-api/install.sh").read_bytes()
        revision = "b" * 40
        def download(path, ref):
            self.assertEqual(ref, revision)
            return source if path.endswith("install.sh") else Path(path).read_bytes()
        def run(argv, **kwargs):
            self.assertEqual(argv[1:], ["-m", "vpn_node_api.control", "update"])
            root = Path(kwargs["env"]["PYTHONPATH"])
            self.assertTrue((root / "vpn_node_api/native.py").is_file())
            self.assertFalse((root / "install.sh").exists())
        with patch.object(updater, "download", side_effect=download), \
             patch.object(updater.subprocess, "run", side_effect=run) as command:
            updater.update_api(revision)
            command.assert_called_once()
        self.assertEqual(updater.MENU.read_bytes(), OLD)

    def test_api_checksum_failure_never_runs_update(self):
        updater.API_CONFIG.write_text("{}")
        def download(path, ref):
            if path.endswith("install.sh"):
                return Path(path).read_bytes()
            return b"# corrupt download"
        with patch.object(updater, "download", side_effect=download), \
             patch.object(updater.subprocess, "run") as command:
            with self.assertRaises(ValueError):
                updater.update_api("a" * 40)
            command.assert_not_called()

    def test_current_api_never_restarts(self):
        updater.API_CONFIG.write_text("{}")
        installed = updater.API_HOME / "vpn_node_api"
        installed.mkdir(parents=True)
        for source in Path("scripts/node-api/vpn_node_api").glob("*.py"):
            (installed / source.name).write_bytes(source.read_bytes())
        with patch.object(updater, "download", side_effect=lambda path, ref: Path(path).read_bytes()), \
             patch.object(updater.subprocess, "run") as command:
            updater.update_api("a" * 40)
            command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
