"""IPv4 API whitelist. Only the owned filter chain is replaced atomically."""
import ipaddress
import json
import shlex
import subprocess
from pathlib import Path

from .common import write_json

CHAIN = "VPN_NODE_API"
LOCK = "/run/lock/vpn-node-api-control.lock"


def network(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Enter an IPv4 address or CIDR range")
    result = ipaddress.IPv4Network(value.strip(), strict=False)
    if result.prefixlen == 0:
        raise ValueError("Allowing the whole Internet (0.0.0.0/0) is forbidden")
    return result


def normalize(values):
    if not isinstance(values, list) or not 1 <= len(values) <= 64:
        raise ValueError("Keep between 1 and 64 API whitelist entries")
    return list(dict.fromkeys(str(network(value)) for value in values))


def permits(address, values):
    peer = ipaddress.ip_address(address)
    return peer.version == 4 and any(peer in network(value) for value in normalize(values))


def command(argv, **kwargs):
    return subprocess.run(argv, check=True, text=True, capture_output=True,
                          timeout=15, **kwargs)


def chain_sources():
    """Reject unknown rules rather than flushing a chain we cannot identify."""
    lines = command(["iptables", "-w", "5", "-S", CHAIN]).stdout.strip().splitlines()
    if not lines or shlex.split(lines[0]) != ["-N", CHAIN]:
        raise ValueError("API firewall chain cannot be verified")
    if shlex.split(lines[-1]) != ["-A", CHAIN, "-j", "DROP"]:
        raise ValueError("API firewall chain has unexpected rules")
    sources = []
    for line in lines[1:-1]:
        fields = shlex.split(line)
        if len(fields) != 6 or fields[:3] != ["-A", CHAIN, "-s"] or fields[4:] != ["-j", "ACCEPT"]:
            raise ValueError("API firewall chain contains unrelated rules; refusing changes")
        sources.append(fields[3])
    return normalize(sources)


def check_references(port):
    """A chain must not be shared with another port or protocol."""
    for line in command(["iptables", "-w", "5", "-S"]).stdout.splitlines():
        fields = shlex.split(line)
        if not any(fields[i:i+2] in (["-j", CHAIN], ["-g", CHAIN])
                   for i in range(len(fields) - 1)):
            continue
        # iptables renders the implicit TCP extension as "-m tcp".
        while ["-m", "tcp"] in [fields[i:i+2] for i in range(len(fields) - 1)]:
            pos = next(i for i in range(len(fields)-1) if fields[i:i+2] == ["-m", "tcp"])
            del fields[pos:pos+2]
        expected = ["-A", "INPUT", "-p", "tcp", "--dport", str(port),
                    "-m", "comment", "--comment", "vpn-node-api-owned", "-j", CHAIN]
        if fields != expected:
            raise ValueError("API chain is referenced by an unrelated rule; refusing changes")


def replace_chain(values):
    values = normalize(values)
    # --noflush preserves all other chains. COMMIT replaces our chain in one
    # transaction, never leaving an empty referenced chain between commands.
    rules = ["*filter", "-F " + CHAIN]
    rules += [f"-A {CHAIN} -s {source} -j ACCEPT" for source in values]
    rules += [f"-A {CHAIN} -j DROP", "COMMIT", ""]
    command(["iptables-restore", "--wait", "5", "--noflush"], input="\n".join(rules))


def recover(config_path):
    """Finish a journaled edit from the persisted config after a process crash."""
    config_path = Path(config_path)
    pending = config_path.with_name("whitelist.pending.json")
    if not pending.exists():
        return
    cfg = json.loads(config_path.read_text())
    journal = json.loads(pending.read_text())
    old, new = normalize(journal["old"]), normalize(journal["new"])
    chosen = normalize(cfg["website_ips"])
    if chosen not in (old, new) or cfg["port"] != journal["port"]:
        raise ValueError("Whitelist recovery needs administrator review")
    check_references(cfg["port"])
    if chain_sources() not in (old, new):
        raise ValueError("Whitelist firewall changed outside the API; recovery stopped")
    replace_chain(chosen)
    pending.unlink()


def change(config_path, action, value):
    """Caller holds LOCK. API reads the committed whitelist on every request."""
    config_path = Path(config_path)
    recover(config_path)
    cfg = json.loads(config_path.read_text())
    before = normalize(cfg["website_ips"])
    source = str(network(value))
    after = list(before)
    if action == "add":
        if source in after:
            return before, False
        after.append(source)
    elif action == "remove":
        if source not in after:
            raise ValueError("That exact IP/range is not in the whitelist")
        after.remove(source)
    else:
        raise ValueError("Use add or remove")
    after = normalize(after)
    check_references(cfg["port"])
    if chain_sources() != before:
        raise ValueError("Firewall differs from saved whitelist; refusing to overwrite it")
    pending = config_path.with_name("whitelist.pending.json")
    write_json(pending, {"old": before, "new": after, "port": cfg["port"]})
    try:
        replace_chain(after)
        write_json(config_path, {**cfg, "website_ips": after})
    except Exception:
        # If the config rename committed before a directory fsync failed, use
        # that persisted state rather than undoing only half of the change.
        recover(config_path)
        raise
    pending.unlink()
    return after, True
