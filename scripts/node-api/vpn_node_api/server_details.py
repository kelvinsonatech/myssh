"""Root-only website import output. No tunnel or API credential mutations."""
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import ssl
import sys
import urllib.request

from .common import write_json


def clean(value, limit=120):
    # Remote metadata must not inject lines or terminal control sequences.
    if not isinstance(value, str):
        return ""
    return " ".join("".join(c if c.isprintable() else " " for c in value).split())[:limit]


def locate(public_ip):
    ip = str(ipaddress.ip_address(public_ip))
    for url, fields in (
        (f"https://ipwho.is/{ip}", ("country_code", "country", "city", "region")),
        (f"https://ipapi.co/{ip}/json/", ("country_code", "country_name", "city", "region")),
    ):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "LitronSSH-node/1.0"})
            with urllib.request.urlopen(request, timeout=5) as response:
                data = json.loads(response.read(65537))
            if not isinstance(data, dict) or data.get("success") is False or data.get("error"):
                continue
            if data.get("ip") != ip:
                continue
            code, country, city, region = (clean(data.get(k)) for k in fields)
            if re.fullmatch("[A-Z]{2}", code) and country:
                return dict(country=code, country_name=country, city=city, region=region)
        except (OSError, ValueError, TypeError):
            continue
    return None


def metadata(cfg, path):
    """Keep generated names stable; retry missing location on a later export."""
    try:
        saved = json.loads(path.read_text())
        if not isinstance(saved, dict):
            raise ValueError("Invalid saved server details")
    except FileNotFoundError:
        saved = {}
    changed = False
    if not saved.get("country"):
        found = locate(cfg["public_ip"])
        if found:
            saved.update(found)
            changed = True
        else:
            print("Warning: location lookup unavailable; Country, City and Region are blank. "
                  "Run export again later. No location was guessed.", file=sys.stderr)
    if not saved.get("server_name") or not saved.get("url_name"):
        suffix = hashlib.sha256(cfg["public_ip"].encode()).hexdigest()[:8]
        saved["server_name"] = f"{saved.get('country_name', 'Server')} {suffix.upper()}"
        saved["url_name"] = f"{saved.get('country', 'node').lower()}-{suffix}"
        changed = True
    if changed:
        write_json(path, saved)
    return saved


def export_details(cfg, directory):
    details = metadata(cfg, Path(directory) / "server-details.json")
    pem = Path(cfg["certificate"]).read_text()
    match = re.search(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", pem, re.S)
    if not match:
        raise ValueError("API certificate is missing or invalid")
    digest = hashlib.sha256(ssl.PEM_cert_to_DER_cert(match[0])).hexdigest().upper()
    fingerprint = ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))
    fields = [
        ("Server name", details["server_name"]),
        ("Country", details.get("country", "")),
        ("URL name", details["url_name"]),
        ("City", details.get("city", "")),
        ("Region", details.get("region", "")),
        ("Public hostname", cfg["hostname"]),
        ("Server IP", cfg["public_ip"]),
        ("Maximum accounts", str(cfg.get("max_accounts", 0))),
        ("Server provider", ""),
        ("API URL", f"https://{cfg['hostname']}:{cfg['port']}/api/v1"),
        ("Key ID", cfg["key_id"]),
        ("API secret", cfg["secret"]),
        ("Certificate SHA-256", fingerprint),
    ]
    print("===== LitronSSH server details =====")
    for label, value in fields:
        print(f"{label}: {clean(value, None)}")
    print("===== end =====")
