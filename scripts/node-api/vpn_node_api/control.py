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

from .common import account_lock, atomic, write_json

CONFIG = Path("/etc/vpn-node-api/config.json")
HOME = Path("/opt/vpn-node-api")
UNIT = Path("/etc/systemd/system/vpn-node-api.service")
FIREWALL = "VPN_NODE_API"

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
    cmd = ["iptables", "-w", "5"]
    exists = subprocess.run(cmd + ["-S", FIREWALL], capture_output=True).returncode == 0
    if not exists:
        run(cmd + ["-N", FIREWALL])
        try:
            for ip in cfg["website_ips"]:
                run(cmd + ["-A", FIREWALL, "-s", ip, "-j", "ACCEPT"])
            run(cmd + ["-A", FIREWALL, "-j", "DROP"])
        except Exception:
            run(cmd + ["-F", FIREWALL])
            run(cmd + ["-X", FIREWALL])
            raise
    else:
        # Do not take ownership of an unrelated chain with the same name.
        expected = [f"-N {FIREWALL}"] + [
            f"-A {FIREWALL} -s {ip}/32 -j ACCEPT" for ip in cfg["website_ips"]
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
    if CONFIG.exists() or UNIT.exists() or HOME.exists():
        raise ValueError("API files already exist; use status, or review the previous installation")
    if sys.version_info < (3, 9):
        raise ValueError("Python 3.9 or newer is required; system Python will not be replaced")
    for name in ("openssl", "iptables", "ip6tables", "nft", "ss", "systemctl"):
        if not shutil.which(name):
            raise ValueError(f"Missing {name}; install prerequisites before API setup")
    run([sys.executable, "-m", "pip", "--version"])
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
    guard = require_safe_port(port)
    cfg = {"hostname": host, "public_ip": ip, "port": port, "website_ips": [website],
           "key_id": "node-" + secrets.token_hex(4), "secret": secrets.token_hex(32)}
    try:
        HOME.mkdir(mode=0o700)
        shutil.copytree(Path(__file__).parent, HOME / "vpn_node_api",
                        ignore=shutil.ignore_patterns("__pycache__"))
        # Isolated target directory; never uninstall/upgrade system packages.
        run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
             "--no-warn-script-location", "--target", str(HOME / "deps"),
             "fastapi==0.115.12", "uvicorn==0.34.3"], timeout=240)
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
        firewall_up(cfg)
        guard.close()
        run(["systemctl", "daemon-reload"])
        run(["systemctl", "enable", "--now", "vpn-node-api.service"], timeout=25)
        import time
        for _ in range(20):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    break
            except OSError:
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


def main():
    os.umask(0o077)
    os.environ["PATH"] = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("install", "status", "rotate", "firewall-up",
                                          "disable", "pending"))
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("Run as root")
    try:
        if args.action == "install":
            with account_lock("/run/lock/vpn-node-api-install.lock"):
                install()
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
            cfg = config()
            if input("Invalidate website credentials and rotate the API secret? type YES: ") != "YES":
                return
            cfg.update(key_id="node-" + secrets.token_hex(4), secret=secrets.token_hex(32))
            write_json(CONFIG, cfg)
            # This restarts ONLY the web API. Native authentication is independent.
            run(["systemctl", "restart", "vpn-node-api.service"], timeout=25)
            print("Key ID:", cfg["key_id"])
            print("New API secret (shown once):", cfg["secret"])
        elif args.action == "firewall-up":
            firewall_up(config())
        elif args.action == "disable":
            run(["systemctl", "disable", "--now", "vpn-node-api.service"], timeout=25)
            firewall_down(config())
            print("API disabled. All VPN protocols and account data retained.")
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        # Subprocess exceptions can contain command arguments: never print them.
        message = str(error) if isinstance(error, ValueError) else "Operation failed; check API service status"
        raise SystemExit(message)


if __name__ == "__main__":
    main()
