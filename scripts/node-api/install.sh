#!/usr/bin/env bash
# Optional standalone installer. Never rerun the full VPN installer to add API.
set -euo pipefail
umask 077
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
[[ $EUID -eq 0 ]] || { echo "Run as root."; exit 1; }
action=install
case "${1:-}" in
    "") ;;
    --update-api) action=update ;;
    *) echo "Usage: bash install.sh [--update-api]"; exit 1 ;;
esac
command -v python3 >/dev/null || { echo "Python 3.9+ is required."; exit 1; }
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$here/vpn_node_api/control.py" ]]; then
    export PYTHONPATH="$here"
    exec python3 -m vpn_node_api.control "$action"
fi
command -v curl >/dev/null || { echo "curl is required."; exit 1; }
work="$(mktemp -d /tmp/vpn-node-api.XXXXXXXX)"
trap 'rm -rf "$work"' EXIT
mkdir -m 700 "$work/vpn_node_api"
base="https://raw.githubusercontent.com/kelvinsonatech/myssh/replit-agent/scripts/node-api/vpn_node_api"
# Hashes are release-pinned: branch changes cannot run unreviewed code.
while read -r digest name; do
    [[ -n "$digest" ]] || continue
    curl --fail --silent --show-error --proto '=https' --tlsv1.2 \
        --connect-timeout 15 --max-time 45 "$base/$name" -o "$work/vpn_node_api/$name"
    printf '%s  %s\n' "$digest" "$work/vpn_node_api/$name" | sha256sum -c - >/dev/null
done <<'API_MANIFEST'
38255a5cad87aaee175ba8166f834059a82818a80ef1d4f437e562451de51e26 access.py
51ee3ec2ffc1065eaa90dab2e7f3e4ef4f1de873c72f18e8e0c85187ba1d1748 common.py
1f5587d0438e3da8f57b9fc4b647cae464ed9e03d5b37e25694222dc8c366542 control.py
bbfe2265ec33fec16c6bab364ca45eddce51453e64c52559a88e79d233123bf7 engine.py
c3aab49d5737fe2ff35aac10dbaf81fcdfe208d180f019d89ca5b80af4af43c5 __init__.py
9278d0ab358d3727510cb16b0d4e8cda5ccedd0d38b18f6267ad31a4cd119df7 links.py
f393a0ead27a8357d3361a55257288efcb2522b9702e884c9fb631108fdfd5d1 __main__.py
266df17fa0a019163685203662bfbec8df3bb6a9e99f67bd09e1480255bf6a60 native.py
0ba2c44261126d10f3febffb136b94f829a434d75c7382c9a3584a8e3db5af0b server.py
API_MANIFEST
export PYTHONPATH="$work"
python3 -m vpn_node_api.control "$action"
