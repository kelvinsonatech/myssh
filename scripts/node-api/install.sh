#!/usr/bin/env bash
# Optional standalone installer. Never rerun the full VPN installer to add API.
set -euo pipefail
umask 077
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
[[ $EUID -eq 0 ]] || { echo "Run as root."; exit 1; }
command -v python3 >/dev/null || { echo "Python 3.9+ is required."; exit 1; }
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$here/vpn_node_api/control.py" ]]; then
    export PYTHONPATH="$here"
    exec python3 -m vpn_node_api.control install
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
51ee3ec2ffc1065eaa90dab2e7f3e4ef4f1de873c72f18e8e0c85187ba1d1748 common.py
f0fa5fb6781d7bd392200f4c7eab5a4eae0c7e3dcaf0b641bbb44f4848411ef9 control.py
aab3b240f33328ed41c65f1a2e2c531aa5e14ce4015a5d7e6838eeba8fa057bc engine.py
c3aab49d5737fe2ff35aac10dbaf81fcdfe208d180f019d89ca5b80af4af43c5 __init__.py
9278d0ab358d3727510cb16b0d4e8cda5ccedd0d38b18f6267ad31a4cd119df7 links.py
4c34c3a2064d38aab007d8ebbcc9227495eebcd5509d3302b334e55bc5a632ee __main__.py
3b74111477ea9c3d35fe149818c5e8a06067d30af3e41f70fcd890913bde73cc native.py
714df5b738f000ecfba261734a65ff1298436b7d2c43ff7f15d6c56dc8d80037 server.py
API_MANIFEST
export PYTHONPATH="$work"
python3 -m vpn_node_api.control install
