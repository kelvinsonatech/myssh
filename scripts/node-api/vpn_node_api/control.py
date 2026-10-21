"""Root-only administration; intentionally never controls VPN service units."""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import urllib.request

from .common import account_lock, atomic, write_json
from . import access

CONFIG = Path("/etc/vpn-node-api/config.json")
HOME = Path("/opt/vpn-node-api")
UNIT = Path("/etc/systemd/system/vpn-node-api.service")
FIREWALL = "VPN_NODE_API"
STEP = "Checking API prerequisites"
PIP_URL = ("https://files.pythonhosted.org/packages/ef/7d/"
           "500c9ad20238fcfcb4cb9243eede163594d7020ce87bd9610c9e02771876/"
           "pip-24.3.1-py3-none-any.whl")
PIP_SHA256 = "3790624780082365f47549d032f3770eeb2b1e8bd1f7b2e02dace1afa361b4ed"


def stage(message):
    global STEP
    STEP = message
    print("  → " + message, flush=True)


def install_dependencies(destination):
    """Use a verified temporary pip without installing or changing system pip."""
    stage("Preparing private API dependencies")
    with tempfile.TemporaryDirectory(prefix="vpn-node-pip-") as temporary:
        env = dict(os.environ)
        try:
            run([sys.executable, "-m", "pip", "--version"])
        except subprocess.CalledProcessError:
            print("  System pip unavailable; using a verified private installer.", flush=True)
            wheel = Path(temporary) / "pip.whl"
            with urllib.request.urlopen(PIP_URL, timeout=45) as response:
                content = response.read(4 * 1024 * 1024 + 1)
            if hashlib.sha256(content).hexdigest() != PIP_SHA256:
                raise ValueError("Private pip checksum failed; nothing installed")
            wheel.write_bytes(content)
            # Python can import directly from this wheel. No apt, get-pip
            # execution, global pip upgrade, or distro service hooks.
            env["PYTHONPATH"] = str(wheel)
        run([sys.executable, "-m", "pip", "--isolated", "install",
             "--disable-pip-version-check", "--no-warn-script-location",
             "--no-cache-dir", "--only-binary=:all:", "--target", str(destination),
             "fastapi==0.115.12", "uvicorn==0.34.3"], env=env, timeout=240)

MENU_LOCK = """
# Shared serialization for menu writes and website writes; no daemon restart.
node_api_account_lock() (
    exec 7>/run/lock/vpn-node-accounts.lock
    flock -n 7 || { err "An account operation is running; retry shortly."; pause; exit 1; }
    "$@"
)
"""


def coordinated_menu(text):
    if "node_api_account_lock()" not in text:
        text = text.replace("\nwhile true; do\n    banner\n", MENU_LOCK + "\nwhile true; do\n    banner\n")
    for number, action in ((1, "create_user"), (2, "delete_user"), (5, "change_password"),
                           (6, "renew_user"), (15, "hy2_menu")):
        original = f"        {number}) {action} ;;"
        replacement = f"        {number}) node_api_account_lock {action} ;;"
        if replacement not in text:
            if original not in text:
                raise ValueError("Unknown menu layout; cannot safely coordinate account writes")
            text = text.replace(original, replacement)
    if "node_api_account_lock()" not in text:
        raise ValueError("Unknown menu layout; refusing uncoordinated API installation")
    if "17) node_api_menu" not in text:
        marker = '    menu_item "16" "◇" "ZIVPN (isolated UDP)"      "$TEAL"'
        branch = "        16) zi_menu ;;"
        if marker not in text or branch not in text:
            raise ValueError("Unknown menu layout; cannot add API submenu safely")
        helper = """
node_api_menu() {
    local choice
    while :; do
        section "WEBSITE API / HTTPS" "$TEAL"
        note "No VPN restarts. Xray / HY1 / ZIVPN writes remain unavailable."
        echo "  1  Status / connection details"
        echo "  2  Rotate API credentials"
        echo "  3  Disable API only"
        echo "  0  Back"
        read -rp "  Select: " choice
        case "$choice" in
            1) /usr/local/bin/vpn-node-api status; pause;;
            2) /usr/local/bin/vpn-node-api rotate; pause;;
            3) /usr/local/bin/vpn-node-api disable; pause;;
            0|"") return;;
        esac
    done
}
"""
        text = text.replace("\nwhile true; do\n    banner\n", helper + "\nwhile true; do\n    banner\n")
        text = text.replace(marker, marker + '\n    menu_item "17" "◇" "Website API (HTTPS)" "$SKY"')
        text = text.replace(branch, branch + "\n        17) node_api_menu ;;")
    # Extend either the old installed submenu or the latest source submenu.
    if "node_api_menu() {" in text:
        start = text.index("node_api_menu() {")
        end = text.index("\n}\n", start) + 3
        block = text[start:end]
        if "vpn-node-api whitelist" not in block:
            block = block.replace('        echo "  0  Back"',
                                  '        echo "  5  Allowed IPs / whitelist"\n        echo "  0  Back"')
            block = block.replace('            0|"") return;;',
                                  '            5) /usr/local/bin/vpn-node-api whitelist; pause;;\n            0|"") return;;')
            if "vpn-node-api whitelist" not in block:
                raise ValueError("Cannot safely extend the API submenu")
            text = text[:start] + block + text[end:]
    return text


def run(argv, **kwargs):
    return subprocess.run(argv, check=True, text=True, capture_output=True,
                          timeout=kwargs.pop("timeout", 20), **kwargs)


def config():
    return json.loads(CONFIG.read_text())


def require_safe_port(port):
    if not 1024 <= port <= 65535 or port in {2082, 2083, 2086, 2087, 2095, 2096, 8080, 8443, 8880}:
        raise ValueError("Choose an unreserved TCP port from 1024 to 65535")
    # Include dormant Xray listeners, not just currently bound sockets.
    path = Path("/usr/local/etc/xray/config.json")
    if path.exists():
        for inbound in json.loads(path.read_text()).get("inbounds", []):
            value = str(inbound.get("port", ""))
            if not value:
                continue
            if not re.fullmatch(r"\d+(-\d+)?", value):
                raise ValueError("Cannot safely inspect an Xray port expression")
            start, _, end = value.partition("-")
            if int(start) <= port <= int(end or start):
                raise ValueError("TCP port is reserved by an Xray listener")
    for filename, pattern in (
        ("/etc/stunnel/stunnel.conf", r"^\s*accept\s*=\s*(?:.*:)?(\d+)\s*$"),
        ("/etc/ssh/sshd_config", r"^\s*Port\s+(\d+)\s*$"),
    ):
        file = Path(filename)
        if file.exists() and any(int(value) == port for value in re.findall(pattern, file.read_text(), re.M)):
            raise ValueError("TCP port is reserved by an existing service configuration")
    # Never change NAT. Unknown TCP redirection means manual review, not guessing.
    for cmd in (["iptables", "-t", "nat", "-S"], ["ip6tables", "-t", "nat", "-S"],
                ["nft", "list", "ruleset"]):
        for line in run(cmd).stdout.splitlines():
            if not re.search(r"DNAT|REDIRECT|TPROXY|dnat|redirect|tproxy", line):
                continue
            if re.search(r"(?:-p udp\b|udp dport)", line):
                continue
            if not re.search(r"(?:-p tcp\b|tcp dport)", line):
                raise ValueError("An unclassified NAT rule requires manual port review")
            match = re.search(r"--dport(?:s)? ([0-9,:-]+)(?: |$)|tcp dport ([0-9]+)(?: |$)", line)
            if not match:
                raise ValueError("A TCP NAT rule requires manual port review")
            for span in (match[1] or match[2]).replace(":", "-").split(","):
                lo, _, hi = span.partition("-")
                if int(lo) <= port <= int(hi or lo):
                    raise ValueError("TCP port is intercepted by a NAT rule")
    for line in run(["ss", "-H", "-ltn"]).stdout.splitlines():
        if re.search(rf":{port}\s", line):
            raise ValueError("TCP port is already listening")
    guard = socket.socket()
    guard.bind(("0.0.0.0", port))
    return guard


def firewall_up(cfg):
    # Only IPv4 is exposed by uvicorn. This chain is attached ONLY for our TCP
    # port and never flushes a shared chain or touches UDP/NAT.
    port = str(cfg["port"])
    sources = access.normalize(cfg["website_ips"])
    cmd = ["iptables", "-w", "5"]
    exists = subprocess.run(cmd + ["-S", FIREWALL], capture_output=True).returncode == 0
    if not exists:
        run(cmd + ["-N", FIREWALL])
        try:
            for ip in sources:
                run(cmd + ["-A", FIREWALL, "-s", ip, "-j", "ACCEPT"])
            run(cmd + ["-A", FIREWALL, "-j", "DROP"])
        except Exception:
            run(cmd + ["-F", FIREWALL])
            run(cmd + ["-X", FIREWALL])
            raise
    else:
        # Do not take ownership of an unrelated chain with the same name.
        expected = [f"-N {FIREWALL}"] + [
            f"-A {FIREWALL} -s {ip} -j ACCEPT" for ip in sources
        ] + [f"-A {FIREWALL} -j DROP"]
        actual = run(cmd + ["-S", FIREWALL]).stdout.strip().splitlines()
        if actual != expected:
            raise ValueError("API firewall chain differs; refusing to overwrite it")
    jump = ["INPUT", "-p", "tcp", "--dport", port, "-m", "comment",
            "--comment", "vpn-node-api-owned", "-j", FIREWALL]
    if subprocess.run(cmd + ["-C"] + jump, capture_output=True).returncode:
        run(cmd + ["-I", "INPUT", "1"] + jump[1:])


def firewall_down(cfg):
    cmd = ["iptables", "-w", "5"]
    jump = ["INPUT", "-p", "tcp", "--dport", str(cfg["port"]), "-m", "comment",
            "--comment", "vpn-node-api-owned", "-j", FIREWALL]
    if subprocess.run(cmd + ["-C"] + jump, capture_output=True).returncode == 0:
        run(cmd + ["-D"] + jump)
    # Deliberately leave the owned, unreachable chain intact. No shared flush.


def service_text():
    python = shutil.which("python3")
    return f"""[Unit]
Description=VPN website node API (no tunnel restarts)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
UMask=0077
Environment=PYTHONPATH={HOME}:{HOME}/deps
WorkingDirectory={HOME}
ExecStartPre={python} -m vpn_node_api.control firewall-up
ExecStart={python} -m vpn_node_api
Restart=always
RestartSec=3
TimeoutStopSec=25
KillMode=control-group
PrivateTmp=true
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK
StandardOutput=null
StandardError=journal

[Install]
WantedBy=multi-user.target
"""


def install():
    stage("Checking API prerequisites")
    if CONFIG.exists() or UNIT.exists() or HOME.exists():
        raise ValueError("API files already exist; use status, or review the previous installation")
    if sys.version_info < (3, 9):
        raise ValueError("Python 3.9 or newer is required; system Python will not be replaced")
    for name in ("openssl", "iptables", "ip6tables", "nft", "ss", "systemctl"):
        if not shutil.which(name):
            raise ValueError(f"Missing {name}; install prerequisites before API setup")
    menu_path = Path("/usr/local/bin/menu")
    menu_before = menu_path.read_text()
    menu_after = coordinated_menu(menu_before)
    print("Close older open menu sessions before using website account creation.")
    host = input("Server domain (or public IPv4): ").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", host):
        raise ValueError("Invalid domain or IPv4 address")
    ip = str(ipaddress.IPv4Address(input("Server public IPv4: ").strip()))
    website = str(ipaddress.IPv4Address(input("Website outgoing IPv4 (only this IP may call API): ").strip()))
    port = int(input("API TCP port [9443]: ").strip() or "9443")
    stage("Checking TCP port and existing firewall rules")
    guard = require_safe_port(port)
    cfg = {"hostname": host, "public_ip": ip, "port": port, "website_ips": [website],
           "key_id": "node-" + secrets.token_hex(4), "secret": secrets.token_hex(32)}
    try:
        # Complete the fallible network/package step before creating permanent
        # API files. A failed download can be retried without deleting anything.
        with tempfile.TemporaryDirectory(prefix="vpn-node-build-") as temporary:
            deps = Path(temporary) / "deps"
            install_dependencies(deps)
            stage("Installing isolated API files")
            HOME.mkdir(mode=0o700)
            shutil.copytree(deps, HOME / "deps")
        shutil.copytree(Path(__file__).parent, HOME / "vpn_node_api",
                        ignore=shutil.ignore_patterns("__pycache__"))
        stage("Preparing HTTPS certificate")
        CONFIG.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(CONFIG.parent, 0o700)
        cert, key = CONFIG.parent / "server.crt", CONFIG.parent / "server.key"
        # Prefer an existing valid certificate whose key loads and hostname matches.
        reused = False
        for oldcert, oldkey in (("/etc/xray/xray.crt", "/etc/xray/xray.key"),
                                ("/usr/local/etc/xray/xray.crt", "/usr/local/etc/xray/xray.key")):
            if not Path(oldcert).is_file() or not Path(oldkey).is_file():
                continue
            try:
                ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(oldcert, oldkey)
                run(["openssl", "x509", "-in", oldcert, "-checkend", "86400", "-noout"])
                result = run(["openssl", "x509", "-in", oldcert, "-checkhost", host, "-noout"])
                if "does match certificate" not in result.stdout:
                    continue
                shutil.copyfile(oldcert, cert)
                shutil.copyfile(oldkey, key)
                reused = True
                break
            except (subprocess.SubprocessError, ssl.SSLError):
                continue
        if not reused:
            try:
                ipaddress.ip_address(host)
                san = "IP:" + host
            except ValueError:
                san = "DNS:" + host
            run(["openssl", "req", "-x509", "-newkey", "rsa:3072", "-nodes",
                 "-days", "365", "-keyout", str(key), "-out", str(cert),
                 "-subj", "/CN=" + host, "-addext", "subjectAltName=" + san], timeout=45)
        os.chmod(cert, 0o600)
        os.chmod(key, 0o600)
        cfg.update(certificate=str(cert), private_key=str(key))
        write_json(CONFIG, cfg)
        atomic(UNIT, service_text(), 0o644)
        wrapper = "#!/bin/sh\nexport PYTHONPATH=/opt/vpn-node-api:/opt/vpn-node-api/deps\nexec python3 -m vpn_node_api.control \"$@\"\n"
        atomic("/usr/local/bin/vpn-node-api", wrapper, 0o700)
        # Versioned backup; preserve terminal menu behavior apart from locking.
        atomic(CONFIG.parent / "menu.before-api", menu_before, 0o700)
        atomic(menu_path, menu_after, 0o755)
        stage("Allowing the website IP on the API TCP port only")
        firewall_up(cfg)
        guard.close()
        stage("Starting the API service only")
        run(["systemctl", "daemon-reload"])
        run(["systemctl", "enable", "--now", "vpn-node-api.service"], timeout=25)
        import time
        for _ in range(20):
            if api_ready(port):
                break
            time.sleep(0.5)
        else:
            raise ValueError("API did not open its port; inspect journalctl -u vpn-node-api")
        status()
        print("API secret (shown once; store securely):", cfg["secret"])
        print("Ready adapters: SSH, SSH SSL, SlowDNS, Hysteria 2 when installed and active.")
        print("Xray, Hysteria 1 and ZIVPN creation remain blocked: no verified live migration.")
        print("No VPN services were restarted. Existing cron jobs were not changed.")
    except Exception:
        guard.close()
        # Stop ONLY our service and remove ONLY our port-scoped jump.
        if UNIT.exists():
            subprocess.run(["systemctl", "disable", "--now", "vpn-node-api.service"],
                           capture_output=True, timeout=25)
        if CONFIG.exists():
            firewall_down(cfg)
        raise


def api_ready(port):
    # A connection from loopback may be intentionally denied by the whitelist.
    # Verify that the API's own process owns its listener instead.
    pid = run(["systemctl", "show", "--property=MainPID", "--value",
               "vpn-node-api.service"], timeout=3).stdout.strip()
    if not pid.isdigit() or pid == "0":
        return False
    listeners = run(["ss", "-H", "-ltnp", f"sport = :{port}"], timeout=3).stdout
    return bool(re.search(rf"\bpid={pid},", listeners))


def status():
    cfg = config()
    certificate = Path(cfg["certificate"]).read_text()
    certificate = re.search(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----",
                            certificate, re.S)[0]
    fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert(certificate)).hexdigest()
    result = subprocess.run(["systemctl", "is-active", "vpn-node-api.service"],
                            capture_output=True, text=True, timeout=5)
    print("API status:", result.stdout.strip())
    print(f"URL: https://{cfg['hostname']}:{cfg['port']}/api/v1")
    print("IP:", cfg["public_ip"], "| TCP port:", cfg["port"])
    print("Key ID:", cfg["key_id"])
    print("Certificate SHA-256:", fingerprint)
    print("Allowed website IPv4:", ", ".join(cfg["website_ips"]))
    print("Secret hidden; use rotate if you need a new credential.")


def whitelist(operation=None, source=None):
    def listing():
        cfg = config()
        print(f"API TCP port {cfg['port']} — allowed sources:")
        for entry in access.normalize(cfg["website_ips"]):
            print(" ", entry)
        print("Ranges may include other tenants. Signed authentication remains required.")

    def edit(action, value):
        with account_lock(access.LOCK):
            values, changed = access.change(CONFIG, action, value)
        print("Whitelist updated." if changed else "Already allowed; nothing changed.")
        print("Allowed sources:", ", ".join(values))
        print("No services restarted. Rules persist across API starts and reboot.")

    if operation == "list":
        listing()
    elif operation in ("add", "remove"):
        if not source:
            raise ValueError("Provide an IPv4 address or CIDR, for example 160.79.106.0/24")
        edit(operation, source)
    elif operation is None:
        while True:
            listing()
            print("  1  Add IP / range\n  2  Remove IP / range\n  0  Back")
            answer = input("Select: ").strip()
            if answer in ("", "0"):
                return
            if answer not in ("1", "2"):
                continue
            value = input("IPv4 address or CIDR: ").strip()
            canonical = str(access.network(value))
            action = "add" if answer == "1" else "remove"
            if input(f"{action.title()} {canonical}? type YES: ") == "YES":
                edit(action, value)


def update(menu_path=Path("/usr/local/bin/menu")):
    """Update API code/menu only; preserve config, credentials, dependencies and accounts."""
    if not CONFIG.is_file() or not UNIT.is_file() or not HOME.is_dir():
        raise ValueError("No existing API installation; use install instead")
    source = Path(__file__).parent.resolve()
    destination = HOME / "vpn_node_api"
    if source == destination.resolve():
        raise ValueError("Run the downloaded API-only installer with --update-api")
    names = ("__init__.py", "__main__.py", "common.py", "engine.py", "native.py",
             "links.py", "server.py", "control.py", "access.py")
    contents = {name: (source / name).read_text() for name in names}
    for name, content in contents.items():
        compile(content, name, "exec")
    access.normalize(config()["website_ips"])
    old_menu = menu_path.read_text()
    new_menu = coordinated_menu(old_menu)
    run(["bash", "-n"], input=new_menu)
    before = {name: (destination / name).read_bytes() if (destination / name).exists() else None
              for name in names}
    active = subprocess.run(["systemctl", "is-active", "--quiet", "vpn-node-api.service"],
                            capture_output=True, timeout=5).returncode == 0
    # Save recovery copies without credentials, even if power is lost mid-update.
    backup = CONFIG.parent / ("code-backup-" + secrets.token_hex(4))
    backup.mkdir(mode=0o700)
    for name, content in before.items():
        if content is not None:
            atomic(backup / name, content)
    atomic(backup / "menu", old_menu, 0o700)
    stage("Updating the web API only; VPN services stay running")
    if active:
        run(["systemctl", "stop", "vpn-node-api.service"], timeout=35)
    try:
        for name, content in contents.items():
            atomic(destination / name, content)
        atomic(menu_path, new_menu, 0o755)
    except Exception:
        for name, content in before.items():
            if content is None:
                (destination / name).unlink(missing_ok=True)
            else:
                atomic(destination / name, content)
        atomic(menu_path, old_menu, 0o755)
        if active:
            run(["systemctl", "start", "--no-block", "vpn-node-api.service"])
        raise
    print("API files updated. Account data, secrets and VPN configurations preserved.")
    print("Run: vpn-node-api status")
    print("Close and reopen menu to see option 17 → 5 (Allowed IPs / whitelist).")
    return active


def main():
    os.umask(0o077)
    os.environ["PATH"] = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("install", "status", "rotate", "firewall-up",
                                          "disable", "pending", "whitelist", "update"))
    parser.add_argument("operation", nargs="?", choices=("list", "add", "remove"))
    parser.add_argument("source", nargs="?")
    args = parser.parse_args()
    if args.action != "whitelist" and (args.operation or args.source):
        parser.error("Extra arguments are only supported for whitelist")
    if os.geteuid() != 0:
        raise SystemExit("Run as root")
    try:
        if args.action == "install":
            with account_lock("/run/lock/vpn-node-api-install.lock"):
                install()
        elif args.action == "update":
            with account_lock("/run/lock/vpn-node-api-install.lock"), account_lock(access.LOCK):
                active = update()
            if active:
                run(["systemctl", "start", "vpn-node-api.service"], timeout=25)
            status()
        elif args.action == "whitelist":
            whitelist(args.operation, args.source)
        elif args.action == "status":
            status()
        elif args.action == "pending":
            from .common import Store
            store = Store("/var/lib/vpn-node-api")
            with store.connect() as db:
                for row in db.execute("SELECT id,operation FROM requests WHERE state='pending'"):
                    print("Pending:", row["id"], row["operation"])
            print("Do not replay blindly; reconcile the native account before clearing pending state.")
        elif args.action == "rotate":
            if input("Invalidate website credentials and rotate the API secret? type YES: ") != "YES":
                return
            with account_lock(access.LOCK):
                cfg = config()
                cfg.update(key_id="node-" + secrets.token_hex(4), secret=secrets.token_hex(32))
                write_json(CONFIG, cfg)
            # This restarts ONLY the web API. Native authentication is independent.
            run(["systemctl", "restart", "vpn-node-api.service"], timeout=25)
            print("Key ID:", cfg["key_id"])
            print("New API secret (shown once):", cfg["secret"])
        elif args.action == "firewall-up":
            with account_lock(access.LOCK):
                access.recover(CONFIG)
                firewall_up(config())
        elif args.action == "disable":
            run(["systemctl", "disable", "--now", "vpn-node-api.service"], timeout=25)
            with account_lock(access.LOCK):
                firewall_down(config())
            print("API disabled. All VPN protocols and account data retained.")
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        # Subprocess exceptions can contain command arguments: never print them.
        if isinstance(error, ValueError):
            message = str(error)
        elif isinstance(error, subprocess.CalledProcessError):
            message = f"{STEP}: command exited with status {error.returncode}."
        elif isinstance(error, subprocess.TimeoutExpired):
            message = f"{STEP}: operation timed out. Check network access and retry."
        elif isinstance(error, FileNotFoundError):
            message = f"{STEP}: required file or program is missing."
        else:
            message = f"{STEP}: network, certificate, permission or filesystem failure ({type(error).__name__})."
        raise SystemExit(message)


if __name__ == "__main__":
    main()
