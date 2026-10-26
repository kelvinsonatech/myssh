"""Non-interactive account operations shared with the optional menu integration.

Uses the existing native account files and service names. Never runs request
data through a shell, reallocates tunnel ports, installs protocols or touches NAT.
"""
import base64
import contextlib
import datetime as dt
import json
import os
import pwd
import secrets
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import quote, urlencode

from .common import Fault, PROTOCOLS, SSH, XRAY, atomic, stamp, utcnow, write_json
from .links import xray_connections

SERVICES = {"ssh": "ssh", "ssh-ssl": "stunnel4", "slowdns": "slowdns",
            "vmess": "xray", "vless": "xray", "trojan": "xray",
            "hysteria": "hysteria-udp", "hysteria2": "hysteria2", "zivpn": "zivpn"}
FILES = {"vmess": ["/etc/xray/accounts.txt", "/usr/local/etc/xray/config.json"],
         "hysteria": ["/etc/hysteria/users", "/etc/hysteria/config.json"],
         "hysteria2": ["/etc/hysteria2/accounts"],
         "zivpn": ["/etc/zivpn/config.json"]}
FILES["vless"] = FILES["trojan"] = FILES["vmess"]
# Menu → V2Ray → Website sub accounts writes "protocol|remark" lines here.
XRAY_MAIN = "/etc/xray/api-main.conf"


class Uncertain(Exception):
    """Operation needs reconciliation; never report a rolled-back failure."""


class Native:
    def __init__(self, config, root="/", state="/var/lib/vpn-node-api"):
        self.config, self.root, self.state = config, Path(root), Path(state)

    def path(self, name):
        return self.root / name.lstrip("/")

    def text(self, name, default=""):
        try:
            return self.path(name).read_text().strip()
        except FileNotFoundError:
            return default

    def load(self, name):
        return json.loads(self.path(name).read_text())

    def run(self, argv, data=None, check=True, timeout=6):
        # Command names/flags are fixed in this module. Secrets only enter stdin.
        try:
            result = subprocess.run(argv, input=data, text=True, capture_output=True,
                                    timeout=timeout, shell=False,
                                    env={**os.environ, "LC_ALL": "C",
                                         "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"})
        except (OSError, subprocess.TimeoutExpired):
            raise Fault("PROVISIONING_FAILED", "Native command could not complete", 500)
        if check and result.returncode:
            raise Fault("PROVISIONING_FAILED", "Native account operation failed", 500)
        return result

    def active(self, service):
        return self.run(["systemctl", "is-active", "--quiet", service + ".service"],
                        check=False, timeout=1).returncode == 0

    def ssh_service(self):
        return "ssh" if self.active("ssh") else "sshd"

    def host(self):
        return self.config["hostname"], self.config["public_ip"]

    def installed(self, protocol):
        if protocol in SSH:
            if protocol == "ssh":
                return self.path("/usr/sbin/sshd").exists()
            if protocol == "ssh-ssl":
                return self.path("/etc/stunnel/stunnel.conf").exists()
            return self.path("/usr/local/bin/dnstt-server").exists() and bool(self.text("/etc/ssh-panel/nsdomain.conf"))
        if protocol in XRAY:
            return self.path("/usr/local/bin/xray").exists()
        return self.path(FILES[protocol][0]).exists()

    def running(self, protocol):
        service = self.ssh_service() if protocol == "ssh" else SERVICES[protocol]
        if not self.active(service):
            return False
        if protocol in XRAY:
            return any(x.get("protocol") == protocol for x in self.load(FILES[protocol][1]).get("inbounds", []))
        if protocol in ("ssh-ssl", "slowdns") and not self.active(self.ssh_service()):
            return False
        return True

    def xrows(self):
        result = []
        for line in self.text("/etc/xray/accounts.txt").splitlines():
            fields = line.split("|")
            if len(fields) == 4:
                fields.insert(0, "vmess")
            if len(fields) != 5 or fields[0] not in XRAY:
                raise Fault("PROVISIONING_FAILED", "Unsupported native Xray account format", 500)
            result.append(fields)
        return result

    def zivpn_main(self):
        """Read the opted-in shared password; never modify the tunnel."""
        try:
            selected = self.load("/etc/zivpn/api-main.json")
            auth = self.load(FILES["zivpn"][0]).get("auth", {})
            password = selected.get("password")
            if (auth.get("mode") == "passwords" and isinstance(password, str)
                    and password and password in auth.get("config", [])):
                return password
        except (OSError, ValueError, AttributeError, TypeError):
            pass
        return None

    def zivpn_blocker(self):
        if self.zivpn_main() is None:
            return "Choose an existing password in menu → ZIVPN → Website sub accounts"
        return None

    def xray_main(self, protocol):
        """The menu-chosen account whose key the website's sub accounts share."""
        remark = None
        for line in self.text(XRAY_MAIN).splitlines():
            fields = line.strip().split("|")
            if len(fields) == 2 and fields[0] == protocol and fields[1]:
                remark = fields[1]
        if remark is None:
            return None
        return next((row for row in self.xrows() if row[0] == protocol and row[1] == remark), None)

    def xray_blocker(self, protocol):
        row = self.xray_main(protocol)
        if row is None:
            return "Choose a main account in menu → V2Ray → Website sub accounts"
        if row[4] not in ("", "0"):
            return "The main account has a data quota; choose one with unlimited data"
        return None

    def xray_check(self, account):
        """Sub accounts change nothing in Xray; they only need a valid main account."""
        protocol = account["protocol"]
        blocker = self.xray_blocker(protocol)
        if blocker:
            raise Fault("PROTOCOL_UNAVAILABLE", blocker, 409)
        main = self.xray_main(protocol)
        if main[2] != self.secret(account):
            raise Fault("ACCOUNT_NOT_FOUND", "The main account for this sub account was changed", 404)
        if main[3] not in ("", "never"):
            try:
                day = dt.date.fromisoformat(main[3])
            except ValueError:
                raise Fault("PROTOCOL_UNAVAILABLE", "The main account has an unreadable expiry date", 409)
            # xray-check removes the main account at local midnight of that date.
            ends = dt.datetime.combine(day, dt.time()).astimezone(dt.timezone.utc)
            wanted = dt.datetime.fromisoformat(account["expires_at"].replace("Z", "+00:00"))
            if wanted > ends:
                raise Fault("VALIDATION_FAILED", "duration_days goes past the V2Ray main account's expiry "
                            f"({main[3]}); choose fewer days", 422)

    def password_rows(self, protocol):
        separator = "\t" if protocol == "hysteria2" else ":"
        return [line.split(separator, 2 if protocol == "hysteria2" else 1)
                for line in self.text(FILES[protocol][0]).splitlines() if line]

    def count(self, protocol):
        if protocol in SSH:
            return sum(p.pw_uid >= 1000 and p.pw_shell in ("/bin/false", "/usr/sbin/nologin")
                       for p in pwd.getpwall())
        if protocol in XRAY:
            return sum(row[0] == protocol for row in self.xrows())
        if protocol == "zivpn":
            return len(self.load(FILES[protocol][0])["auth"]["config"])
        return len(self.password_rows(protocol))

    def protocols(self):
        result = []
        for protocol in PROTOCOLS:
            if not self.installed(protocol):
                continue
            config = self.connection(protocol, "preview", "preview")
            ports = config.get("ports", {})
            if protocol in XRAY:
                ports = {}
                for connection in config["connections"]:
                    ports.setdefault(connection["security"], []).append(connection["port"])
            elif "port" in config:
                ports = {"udp": [config["port"]]}
            blocker = ("Live updates have not been verified for this installation"
                       if protocol == "hysteria" else
                       self.zivpn_blocker() if protocol == "zivpn" else
                       self.xray_blocker(protocol) if protocol in XRAY else None)
            result.append({"id": protocol, "installed": True, "running": self.running(protocol),
                           "ports": ports, "transports": sorted({c["network"] for c in config.get("connections", [])}),
                           "current_accounts": self.count(protocol), "max_accounts": None,
                           "metadata": {"account_scope": "shared_linux_user" if protocol in SSH else
                                                         "shared_main_account" if protocol in (*XRAY, "zivpn") else "native",
                                        "changes_restart_service": False,
                                        "api_provisioning": protocol in (*SSH, "hysteria2") or (
                                            protocol in (*XRAY, "zivpn") and blocker is None),
                                        "provisioning_blocker": blocker}})
        return result

    def connection(self, protocol, user, secret):
        host, ip = self.host()
        result = {"host": host, "ip": ip}
        if protocol in SSH:
            result.update(username=user, password=secret)
            payload = f"GET / HTTP/1.1[crlf]Host: {host}[crlf]Upgrade: websocket[crlf][crlf]"
            if protocol == "ssh":
                result.update(ports={"openssh": [22]}, payload=payload)
                if self.active("dropbear"):
                    result["ports"]["dropbear"] = [109, 143]
                if self.active("ws-proxy"):
                    result["ports"]["ws"] = [80]
                if self.active("stunnel4") and not self.text("/etc/xray/port443"):
                    result["ports"]["ws_tls"] = [443]
            elif protocol == "ssh-ssl":
                tls_ports = [447] if self.text("/etc/xray/port443") else [443, 447]
                result.update(ports={"ssl": tls_ports}, sni=host, payload=payload)
                if 443 in tls_ports:
                    result["ports"]["ws_tls"] = [443]
            else:
                result.update(ports={"slowdns": [53], "openssh": [22]},
                              slowdns={"nameserver": self.text("/etc/ssh-panel/nsdomain.conf"),
                                       "public_key": self.text("/etc/slowdns/server.pub")})
        elif protocol in XRAY:
            inbounds = self.load("/usr/local/etc/xray/config.json").get("inbounds", [])
            connections, links = xray_connections(inbounds, protocol, secret, user, host)
            result.update(remark=user, connections=connections, links=links)
            result["password" if protocol == "trojan" else "uuid"] = secret
            if protocol == "vmess":
                result["alter_id"] = 0
            elif protocol == "vless":
                result["flow"] = ""
        elif protocol == "hysteria":
            cfg = self.load("/etc/hysteria/config.json")
            port = int(cfg["listen"].rsplit(":", 1)[1])
            params = {"protocol": "udp", "auth": secret, "peer": host, "insecure": 1,
                      "upmbps": cfg.get("up_mbps", 100), "downmbps": cfg.get("down_mbps", 100),
                      "alpn": "hysteria"}
            result.update(port=port, protocol="udp", auth=secret, password=secret,
                          up_mbps=params["upmbps"], down_mbps=params["downmbps"],
                          alpn="hysteria", sni=host, insecure=True)
            if cfg.get("obfs"):
                result.update(obfs="xplus", obfs_password=cfg["obfs"])
                params.update(obfs="xplus", obfsParam=cfg["obfs"])
            address = f"[{host}]" if ":" in host else host
            result["uri"] = f"hysteria://{address}:{port}?" + urlencode(params) + "#" + quote(user)
        elif protocol == "hysteria2":
            port = int(self.text("/etc/hysteria2/port"))
            result.update(port=port, password=secret, sni=host, insecure=True)
            address = f"[{host}]" if ":" in host else host
            result["uri"] = f"hy2://{quote(secret, safe='')}@{address}:{port}?" + urlencode(
                {"insecure": 1, "sni": host}) + "#" + quote(user)
        else:
            cfg = self.load("/etc/zivpn/config.json")
            result.update(password=secret, ports={"udp": "6000-19999"}, obfs=cfg.get("obfs", "zivpn"))
        return result

    def prepare(self, body):
        protocol, user = body["protocol"], body["username"]
        if protocol == "hysteria":
            raise Fault("PROTOCOL_UNAVAILABLE",
                        "No verified no-restart adapter for this installation", 409)
        if not self.installed(protocol) or not self.running(protocol):
            raise Fault("PROTOCOL_UNAVAILABLE", "Activate this protocol in the server menu first", 409)
        if protocol in SSH:
            try:
                pwd.getpwnam(user)
            except KeyError:
                pass
            else:
                raise Fault("USERNAME_TAKEN", "Username already exists", 409)
        elif protocol in XRAY:
            if any(row[1] == user for row in self.xrows()):
                raise Fault("USERNAME_TAKEN", "Username already exists", 409)
        elif protocol != "zivpn":
            if any(row[0] == user for row in self.password_rows(protocol)):
                raise Fault("USERNAME_TAKEN", "Username already exists", 409)
        if protocol in XRAY:
            blocker = self.xray_blocker(protocol)
            if blocker:
                raise Fault("PROTOCOL_UNAVAILABLE", blocker, 409)
            secret = self.xray_main(protocol)[2]      # sub account: shared key, own name
        elif protocol == "zivpn":
            secret = self.zivpn_main()
            if secret is None:
                raise Fault("PROTOCOL_UNAVAILABLE", self.zivpn_blocker(), 409)
        else:
            secret = body.get("password") or secrets.token_hex(12)
        if protocol == "hysteria":
            cfg = self.load(FILES[protocol][-1])
            if cfg.get("auth", {}).get("mode") != "passwords":
                raise Fault("PROTOCOL_UNAVAILABLE", "Native password authentication is not configured", 409)
            if secret in cfg["auth"]["config"]:
                raise Fault("USERNAME_TAKEN", "That tunnel password is already assigned", 409)
        if protocol == "hysteria2":
            # The existing command authenticator uses a narrower alphabet.
            # Do not silently create credentials which its checker would reject.
            import re
            if not re.fullmatch(r"[a-zA-Z0-9_.@!+-]{8,128}", secret):
                raise Fault("VALIDATION_FAILED", "Hysteria 2 currently requires 8+ letters/digits/_.@!+-")
            if any(row[1] == secret for row in self.password_rows(protocol)):
                raise Fault("USERNAME_TAKEN", "That tunnel password is already assigned", 409)
        created = utcnow()
        end = created + dt.timedelta(days=body["duration_days"])
        return {"account_id": "acc_" + uuid.uuid4().hex, "request_id": body["request_id"],
                "protocol": protocol, "username": user, "status": "active",
                "created_at": stamp(created), "expires_at": stamp(end),
                "config": self.connection(protocol, user, secret)}

    def secret(self, account):
        cfg = account["config"]
        return cfg.get("password", cfg.get("uuid", cfg.get("auth")))

    def password_locked(self, user):
        for line in self.text("/etc/shadow").splitlines():
            fields = line.split(":")
            if fields[0] == user and len(fields) > 1:
                return fields[1].startswith("!")
        return False

    def owns(self, account):
        protocol, user, secret = account["protocol"], account["username"], self.secret(account)
        if protocol in SSH:
            try:
                entry = pwd.getpwnam(user)
            except KeyError:
                return False
            return entry.pw_uid >= 1000 and entry.pw_gecos == "vpn-node=" + account["account_id"]
        if protocol in XRAY:
            return any(r[0] == protocol and r[2] == secret for r in self.xrows())
        if protocol == "zivpn":
            return secret in self.load(FILES[protocol][0])["auth"]["config"]
        return any(r[:2] == [user, secret] for r in self.password_rows(protocol))

    def write(self, name, value):
        path = self.path(name)
        previous = path.stat()
        content = json.dumps(value, indent=2) + "\n" if isinstance(value, dict) else value
        atomic(path, content, previous.st_mode & 0o777)
        os.chown(path, previous.st_uid, previous.st_gid)

    def restart(self, service):
        # User explicitly disallows disconnecting existing users. This draft
        # must fail closed until a tested live-update adapter is implemented.
        raise Fault("PROTOCOL_UNAVAILABLE", "Automatic tunnel restarts are forbidden", 409)

    def perform(self, operation, account):
        protocol, user, secret = account["protocol"], account["username"], self.secret(account)
        if protocol == "hysteria":
            raise Fault("PROTOCOL_UNAVAILABLE", "Verified no-restart provisioning is not configured", 409)
        if protocol == "zivpn":
            # Delete removes only the API record, even if the main password is gone.
            if operation == "delete":
                return
            if operation == "create" and secret != self.zivpn_main():
                raise Fault("PROTOCOL_UNAVAILABLE", "Choose a valid ZIVPN main password first", 409)
            if not self.owns(account):
                raise Fault("ACCOUNT_NOT_FOUND", "The shared ZIVPN password was removed", 404)
            return
        if operation != "create" and not self.owns(account):
            raise Fault("ACCOUNT_NOT_FOUND", "Account was removed or changed outside this API", 404)
        if protocol in XRAY:
            # Never writes Xray files or restarts Xray: the record lives in the API only.
            if operation != "delete":
                self.xray_check(account)
            return
        if protocol in SSH:
            self.ssh_operation(operation, account)
            return
        service = SERVICES[protocol]
        was_running = self.active(service)
        snapshots = [(name, self.path(name).read_bytes(), self.path(name).stat())
                     for name in FILES[protocol]]
        try:
            if protocol == "hysteria2":
                rows = self.password_rows(protocol)
                rows = [r for r in rows if r[0] != user]
                if operation != "delete":
                    expiry = dt.datetime.fromisoformat(account["expires_at"].replace("Z", "+00:00"))
                    # Native auth is valid THROUGH this UTC date. The website
                    # revokes at the precise API timestamp; do not expire early.
                    rows.append([user, secret, expiry.astimezone(dt.timezone.utc).strftime("%Y%m%d")])
                self.write(FILES[protocol][0], "".join("\t".join(r) + "\n" for r in rows))
            elif operation != "renew":
                if protocol == "hysteria":
                    rows = [r for r in self.password_rows(protocol) if r[0] != user]
                    if operation == "create":
                        rows.append([user, secret])
                    self.write(FILES[protocol][0], "".join(":".join(r) + "\n" for r in rows))
                    cfg = self.load(FILES[protocol][1])
                    cfg["auth"]["config"] = [r[1] for r in rows]
                    self.write(FILES[protocol][1], cfg)
                else:
                    cfg = self.load(FILES[protocol][0])
                    values = cfg["auth"]["config"]
                    if operation == "create":
                        values.append(secret)
                    else:
                        values.remove(secret)
                    self.write(FILES[protocol][0], cfg)
            if was_running and protocol != "hysteria2" and operation != "renew":
                self.restart(service)
        except Exception:
            try:
                for name, data, metadata in snapshots:
                    atomic(self.path(name), data, metadata.st_mode & 0o777)
                    os.chown(self.path(name), metadata.st_uid, metadata.st_gid)
                if was_running and protocol != "hysteria2" and operation != "renew":
                    self.restart(service)
            except Exception:
                raise Uncertain("Native rollback needs administrator recovery")
            raise

    def ssh_operation(self, operation, account):
        user, secret = account["username"], self.secret(account)
        if operation != "delete":
            expiry = dt.datetime.fromisoformat(account["expires_at"].replace("Z", "+00:00"))
            # Linux shadow stores dates, not times. Use the following UTC date
            # only as a safety net; the website owns exact-time revocation.
            safety_date = (expiry.astimezone(dt.timezone.utc).date() +
                           dt.timedelta(days=1)).isoformat()
        if operation == "create":
            try:
                self.run(["useradd", "-e", safety_date, "-M", "-s", "/bin/false",
                          "-c", "vpn-node=" + account["account_id"], user])
                self.run(["chpasswd"], user + ":" + secret + "\n")
            except Exception:
                if self.owns(account):
                    try:
                        self.run(["userdel", user])
                    except Exception:
                        raise Uncertain("New SSH account needs administrator recovery")
                raise
        elif operation == "delete":
            # Logical revocation: deny new logins without killing even this
            # user's established sessions. Retain the UID to prevent reuse.
            try:
                self.run(["chage", "-E", "1970-01-02", user])
            except Fault:
                raise Uncertain("Verify whether native revocation completed")
        else:
            self.run(["chage", "-E", safety_date, user])

    def health(self):
        memory = {}
        for line in self.path("/proc/meminfo").read_text().splitlines():
            key, value = line.split(":", 1)
            memory[key] = int(value.split()[0]) * 1024
        disk = shutil.disk_usage(self.root)
        first = list(map(int, self.path("/proc/stat").read_text().splitlines()[0].split()[1:]))
        time.sleep(0.1)
        second = list(map(int, self.path("/proc/stat").read_text().splitlines()[0].split()[1:]))
        total = sum(second[:8]) - sum(first[:8])
        idle = sum(second[3:5]) - sum(first[3:5])
        services = {}
        for service in (self.ssh_service(), "dropbear", "stunnel4", "ws-proxy", "xray",
                        "hysteria-udp", "hysteria2", "zivpn", "slowdns"):
            result = self.run(["systemctl", "is-active", service + ".service"], check=False, timeout=1)
            services[service] = "running" if result.returncode == 0 else (
                "unknown" if result.stdout.strip() == "unknown" else "stopped")
        return {"status": "online", "uptime_seconds": int(float(self.text("/proc/uptime").split()[0])),
                "cpu_percent": round(100 * max(0, total - idle) / total, 2) if total else 0,
                "memory": {"total_bytes": memory["MemTotal"],
                           "used_bytes": memory["MemTotal"] - memory.get("MemAvailable", memory.get("MemFree", 0))},
                "disk": {"used_bytes": disk.used, "total_bytes": disk.total},
                "load": list(os.getloadavg()), "services": services}
