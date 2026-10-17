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

    def test_update_backup_permissions_and_noop(self):
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
        self.assertEqual(updater.MENU.read_bytes(), NEW)
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

    def test_direct_upgrade_has_one_confirmation_and_no_extra_actions(self):
        with patch.object(updater.sys, "argv", ["update-script.py"]), \
             patch.object(updater.os, "geteuid", return_value=0), \
             patch.object(updater, "latest", return_value="a" * 40), \
             patch.object(updater, "release_menu", return_value=NEW), \
             patch("builtins.input", return_value="YES") as prompt:
            updater.main()
            prompt.assert_called_once_with("Upgrade script? Type YES: ")
        self.assertEqual(updater.MENU.read_bytes(), NEW)
        self.assertFalse(hasattr(updater, "update_api"))
        self.assertFalse(hasattr(updater, "rollback"))

    def test_decline_and_already_current_leave_script_unchanged(self):
        for available, answer in ((NEW, "no"), (OLD, "YES")):
            with patch.object(updater.sys, "argv", ["update-script.py"]), \
                 patch.object(updater.os, "geteuid", return_value=0), \
                 patch.object(updater, "latest", return_value="a" * 40), \
                 patch.object(updater, "release_menu", return_value=available), \
                 patch("builtins.input", return_value=answer) as prompt:
                updater.main()
                if available == OLD:
                    prompt.assert_not_called()
            self.assertEqual(updater.MENU.read_bytes(), OLD)
            self.assertFalse(updater.STATE.exists())

    def test_preserves_installed_api_account_locking_without_modifying_api(self):
        updater.API_CONFIG.write_text("{}")
        # Use the real coordination helper without running any API action.
        with patch.object(updater, "API_HOME", Path("scripts/node-api").resolve()), \
             patch.object(updater, "download", return_value=Path("scripts/ssh-ssl-setup.sh").read_bytes()):
            menu = updater.release_menu("a" * 40)
        self.assertIn(b"node_api_account_lock()", menu)
        self.assertIn(b"node_api_account_lock create_user", menu)
        self.assertEqual(updater.API_CONFIG.read_text(), "{}")
        self.assertEqual(updater.MENU.read_bytes(), OLD)


if __name__ == "__main__":
    unittest.main()
