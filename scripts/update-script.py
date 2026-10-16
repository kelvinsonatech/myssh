#!/usr/bin/env python3
"""Explicit, code-only updates. Never execute the full VPN setup script."""
import argparse
import base64
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import urllib.request

REPO = "kelvinsonatech/myssh"
BRANCH = "replit-agent"
MENU = Path("/usr/local/bin/menu")
STATE = Path("/etc/ssh-panel/script-updates")
LOCK = Path("/run/lock/ssh-panel-update.lock")
API_LOCK = Path("/run/lock/vpn-node-api-install.lock")
API_HOME = Path("/opt/vpn-node-api")
API_CONFIG = Path("/etc/vpn-node-api/config.json")


def github(path):
    request = urllib.request.Request(
        "https://api.github.com/repos/" + REPO + "/" + path,
        headers={"User-Agent": "ssh-panel-updater", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read(3 * 1024 * 1024 + 1)
    if len(data) > 3 * 1024 * 1024:
        raise ValueError("Release response too large")
    return json.loads(data)


def latest():
    revision = github("commits/" + BRANCH)["sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Invalid release revision")
    return revision


def download(path, revision):
    item = github("contents/" + path + "?ref=" + revision)
    if item.get("type") != "file" or item.get("encoding") != "base64":
        raise ValueError("Unexpected release file: " + path)
    data = base64.b64decode(item["content"].replace("\n", ""), validate=True)
    blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
    if blob != item["sha"] or len(data) != item["size"]:
        raise ValueError("Release integrity check failed: " + path)
    return data


def atomic(path, data, mode):
    fd, name = tempfile.mkstemp(prefix=".update-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextlib.contextmanager
def update_lock(path=None):
    path = path or LOCK
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as lock:
        os.chmod(path, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another script update is running; retry later")
        yield


def validate(menu):
    if not menu.startswith(b"#!/bin/bash\n") or b"node_api_menu()" not in menu:
        raise ValueError("Unrecognized management menu; nothing changed")
    subprocess.run(["bash", "-n"], input=menu, check=True, capture_output=True, timeout=15)


def release_menu(revision):
    source = download("scripts/ssh-ssl-setup.sh", revision)
    marker = b"cat > /usr/local/bin/menu <<'MENUEOF'\n"
    if source.count(marker) != 1:
        raise ValueError("Release menu marker missing or ambiguous")
    part = source.split(marker, 1)[1]
    if b"\nMENUEOF\n" not in part:
        raise ValueError("Incomplete release menu")
    menu = part.split(b"\nMENUEOF\n", 1)[0] + b"\n"
    # Preserve account-operation coordination added by the optional API.
    if API_CONFIG.exists():
        helper = API_HOME / "vpn_node_api/control.py"
        if not helper.is_file():
            raise ValueError("Installed API coordination helper is missing")
        env = dict(os.environ, PYTHONPATH=str(API_HOME))
        result = subprocess.run(
            [sys.executable, "-c",
             "import sys; from vpn_node_api.control import coordinated_menu; "
             "sys.stdout.write(coordinated_menu(sys.stdin.read()))"],
            input=menu, capture_output=True, check=True, env=env, timeout=15)
        menu = result.stdout
    validate(menu)
    return menu


def install_menu(menu, revision):
    with update_lock(API_LOCK):
        _install_menu(menu, revision)


def _install_menu(menu, revision):
    validate(menu)
    if MENU.is_symlink() or not MENU.is_file():
        raise ValueError("Expected an installed regular menu file; refusing replacement")
    previous = MENU.read_bytes()
    if menu == previous:
        print("Management menu is already up to date.")
        return
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(STATE, 0o700)
    # Backup first. Only the final atomic rename changes the installed menu.
    atomic(STATE / "previous-menu", previous, 0o700)
    atomic(MENU, menu, 0o755)
    print("Menu updated to GitHub revision " + revision[:12] + ".")
    print("No services restarted. Accounts, ports and protocol settings unchanged.")
    print("Close and reopen menu to load the updated version.")


def rollback():
    previous = STATE / "previous-menu"
    if not previous.is_file():
        raise ValueError("No previous menu backup is available")
    install_menu(previous.read_bytes(), "local-backup")


def update_api(revision):
    if not API_CONFIG.is_file():
        raise ValueError("API is not installed; use its installation submenu first")
    with tempfile.TemporaryDirectory(prefix="ssh-panel-api-update-") as folder:
        root = Path(folder)
        installer = download("scripts/node-api/install.sh", revision).decode()
        manifest = installer.split("done <<'API_MANIFEST'\n", 1)[1].split("\nAPI_MANIFEST", 1)[0]
        package = root / "vpn_node_api"
        package.mkdir(mode=0o700)
        names = set()
        for row in manifest.splitlines():
            digest, name = row.split()
            if not re.fullmatch(r"[a-z_]+\.py", name) or name in names:
                raise ValueError("Unsafe API release manifest")
            names.add(name)
            data = download("scripts/node-api/vpn_node_api/" + name, revision)
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError("API checksum mismatch: " + name)
            compile(data, name, "exec")
            (package / name).write_bytes(data)
        if not {"__init__.py", "control.py", "access.py", "native.py", "engine.py",
                "__main__.py", "common.py", "links.py", "server.py"}.issubset(names):
            raise ValueError("Incomplete API release")
        installed = API_HOME / "vpn_node_api"
        if all((installed / name).is_file() and
               (installed / name).read_bytes() == (package / name).read_bytes()
               for name in names):
            print("Web API code is already up to date. No services restarted.")
            return
        env = dict(os.environ, PYTHONPATH=str(root))
        # Existing updater preserves configuration/credentials/data and manages
        # only vpn-node-api.service. No installer, pip or apt invocation.
        subprocess.run([sys.executable, "-m", "vpn_node_api.control", "update"],
                       env=env, check=True, timeout=180)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--menu-only", action="store_true",
                        help="Update menu only, without restarting any service")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise ValueError("Run as root")
    with update_lock():
        if args.menu_only:
            revision = latest()
            install_menu(release_menu(revision), revision)
            return
        while True:
            print("\nSCRIPT UPDATES\n"
                  "  1  Check / update management menu (no restarts)\n"
                  "  2  Update web API only (API restart confirmation required)\n"
                  "  3  Restore previous menu\n"
                  "  0  Back")
            choice = input("Select: ").strip()
            if choice in ("", "0"):
                return
            try:
                if choice == "1":
                    print("Checking GitHub release…", flush=True)
                    revision = latest()
                    menu = release_menu(revision)
                    print("Latest revision:", revision[:12])
                    if MENU.read_bytes() == menu:
                        print("Management menu is already up to date.")
                    elif input("Update menu only? Type YES: ") == "YES":
                        install_menu(menu, revision)
                elif choice == "2":
                    print("This briefly interrupts web API requests only. VPN services stay running.")
                    if input("Update and restart ONLY the web API? Type YES: ") == "YES":
                        update_api(latest())
                elif choice == "3":
                    if input("Restore the previous menu only? Type YES: ") == "YES":
                        rollback()
            except (ValueError, OSError, KeyError, IndexError, subprocess.SubprocessError) as exc:
                print("Update failed:", exc)
                print("Do not rerun server setup. Review the error before retrying.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, IndexError, subprocess.SubprocessError) as exc:
        print("Update failed:", exc, file=sys.stderr)
        sys.exit(1)
