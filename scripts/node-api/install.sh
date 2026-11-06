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
    cd "$here"   # never import an installed copy from the caller's directory
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
c95fd037c66bf6d5bc56a684784deb1b08c6d2b5a8e52df3cd0227cf2e248314 common.py
7b27a44945add91e4a3625eaaeff7a56c7974b3ed064774b46378950e3d36420 control.py
105f9ec151890a163fadc5000e64bce719da340828a173784ffc6650c4a3763a engine.py
c3aab49d5737fe2ff35aac10dbaf81fcdfe208d180f019d89ca5b80af4af43c5 __init__.py
9278d0ab358d3727510cb16b0d4e8cda5ccedd0d38b18f6267ad31a4cd119df7 links.py
f393a0ead27a8357d3361a55257288efcb2522b9702e884c9fb631108fdfd5d1 __main__.py
ddb05946b11d983cdd1fb5c2c6e6066a6fce15ecf7d674b422092c95bf6f9505 native.py
0ba2c44261126d10f3febffb136b94f829a434d75c7382c9a3584a8e3db5af0b server.py
66ed3f36ffd5ec32dcfdf437393c534a8cbb3c854c728d634dd6ad7b8283146b server_details.py
API_MANIFEST
export PYTHONPATH="$work"
# python3 -m searches the current directory first; running from
# /opt/vpn-node-api would otherwise load the old installed code.
cd "$work"
python3 -m vpn_node_api.control "$action"
