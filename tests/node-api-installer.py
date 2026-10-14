"""Prerequisite regressions. Never runs package installers or systemctl."""
import hashlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/node-api"))
from vpn_node_api import control


class Prerequisites(unittest.TestCase):
    def test_existing_pip_uses_private_target(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(control, "run") as run, \
             patch.object(control.urllib.request, "urlopen") as download:
            control.install_dependencies(Path(temporary) / "deps")
            download.assert_not_called()
            argv = run.call_args_list[-1].args[0]
            self.assertIn("--target", argv)
            self.assertIn("--isolated", argv)
            self.assertNotIn("--upgrade", argv)
            self.assertEqual(len(run.call_args_list), 2)

    def test_missing_pip_bootstraps_without_system_changes(self):
        payload = b"fixture-wheel"
        missing = subprocess.CalledProcessError(1, ["python3", "-m", "pip", "--version"])
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(control, "run", side_effect=[missing, None]) as run, \
             patch.object(control.urllib.request, "urlopen", return_value=io.BytesIO(payload)), \
             patch.object(control, "PIP_SHA256", hashlib.sha256(payload).hexdigest()):
            control.install_dependencies(Path(temporary) / "deps")
            call = run.call_args_list[-1]
            self.assertTrue(call.kwargs["env"]["PYTHONPATH"].endswith("/pip.whl"))
            self.assertFalse(Path(call.kwargs["env"]["PYTHONPATH"]).exists())
            self.assertNotIn("apt", str(run.call_args_list))
            self.assertNotIn("systemctl", str(run.call_args_list))

    def test_checksum_failure_never_executes_download(self):
        missing = subprocess.CalledProcessError(1, ["python3", "-m", "pip", "--version"])
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(control, "run", side_effect=missing) as run, \
             patch.object(control.urllib.request, "urlopen", return_value=io.BytesIO(b"wrong")):
            with self.assertRaisesRegex(ValueError, "checksum failed"):
                control.install_dependencies(Path(temporary) / "deps")
            self.assertEqual(run.call_count, 1)
            self.assertFalse((Path(temporary) / "deps").exists())

    def test_download_failure_can_retry(self):
        missing = subprocess.CalledProcessError(1, ["python3", "-m", "pip", "--version"])
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(control, "run", side_effect=missing), \
             patch.object(control.urllib.request, "urlopen", side_effect=OSError("offline")):
            with self.assertRaises(OSError):
                control.install_dependencies(Path(temporary) / "deps")
            self.assertFalse((Path(temporary) / "deps").exists())


if __name__ == "__main__":
    unittest.main()
