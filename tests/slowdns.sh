#!/bin/bash
# Isolated fixtures only: never execute the main installer or host systemctl.
set -euo pipefail
cd "$(dirname "$0")/.."
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
export SD_TEST_ROOT="$tmp"
python3 - <<'PY'
import os,pathlib
s=pathlib.Path("scripts/ssh-ssl-setup.sh").read_text()
f=s[s.index("slowdns_go_usable() {"):s.index("slowdns_menu() {")]
for p in ["/usr/local", "/etc", "/run", "/opt"]:
    f=f.replace(p,os.environ["SD_TEST_ROOT"]+p)
pathlib.Path(os.environ["SD_TEST_ROOT"]+"/functions").write_text(f)
assert "read -rp" not in s[s.index("# SlowDNS is opt-in"):s.index("# ── system info panel")]
assert "11) slowdns_menu" in s
PY
source "$tmp/functions"
mkdir -p "$tmp/bin" "$tmp/etc/ssh-panel" "$tmp/run/systemd/resolve" \
    "$tmp/etc/systemd/system" "$tmp/etc/slowdns" "$tmp/usr/local/bin"
cat > "$tmp/bin/go" <<'EOF'
#!/bin/bash
case "$1" in
 version) echo "go version go${GO_TEST_VERSION:-1.27.1} linux/amd64";;
 list) [ "${GO_TEST_ECDH:-1}" = 1 ];;
 *) exit 1;;
esac
EOF
chmod +x "$tmp/bin/go"
reject() { if "$@" >"$tmp/reject.log" 2>&1; then echo "Unexpected success: $*"; exit 1; fi; }
GO_TEST_VERSION=1.19.13 reject slowdns_go_usable "$tmp/bin/go"
GO_TEST_VERSION=1.27.0 reject slowdns_go_usable "$tmp/bin/go"
GO_TEST_VERSION=1.27rc1 reject slowdns_go_usable "$tmp/bin/go"
GO_TEST_VERSION=1.27.1 GO_TEST_ECDH=0 reject slowdns_go_usable "$tmp/bin/go"
slowdns_go_usable "$tmp/bin/go"
GO_TEST_VERSION=1.28.0 slowdns_go_usable "$tmp/bin/go"

SD_WORK="$tmp/work"; mkdir -p "$SD_WORK"
SD_LOG="$tmp/log"; : > "$SD_LOG"
SD_NS=dns.example.com; CONF_DIR="$tmp/etc/ssh-panel"
export PATH="$tmp/bin:$PATH"
slowdns_prepare_go
[ "$(cat "$SD_WORK/go-path")" = "$tmp/bin/go" ]
# Bad downloads must not be extracted or installed.
(
    export GO_TEST_VERSION=1.19.13
    uname() { echo x86_64; }
    curl() { printf bad > "$SD_WORK/go.tgz"; }
    tar() { echo unsafe-extraction > "$tmp/tar-called"; }
    reject slowdns_prepare_go
    [ ! -e "$tmp/tar-called" ]
)
slowdns_step success true
reject slowdns_step failure false

hy2_snapshot() { return "${SNAP_FAIL:-0}"; }
hy2_nat_conflict() { [ "${NAT_CONFLICT:-0}" = 1 ]; }
hy2_nft_conflict() { return 1; }
HY2_SS=""
systemctl() {
    echo "$*" >> "$tmp/service-calls"
    case "$*" in
        'is-active --quiet systemd-resolved.service') [ "${RESOLVED_ACTIVE:-0}" = 1 ];;
        'is-active --quiet slowdns.service') [ "${SD_STARTED:-0}" = 1 ];;
        'is-enabled --quiet slowdns.service') return 1;;
        'enable --now slowdns.service') [ "${START_FAIL:-0}" = 0 ] || return 1; SD_STARTED=1;;
        'stop slowdns.service') SD_STARTED=0;;
        *) return 0;;
    esac
}
slowdns_port_check
SNAP_FAIL=1 reject slowdns_port_check
NAT_CONFLICT=1 reject slowdns_port_check
HY2_SS='UNCONN 0 0 0.0.0.0:53 0.0.0.0:* users:(("dnsmasq",pid=8,fd=2))'
reject slowdns_port_check
HY2_SS=""
mkdir -p "$tmp/etc/hysteria2"
echo 53 > "$tmp/etc/hysteria2/port"
reject slowdns_port_check
rm "$tmp/etc/hysteria2/port"
touch "$tmp/etc/hysteria2/slowdns.previous"
reject slowdns_port_check
rm "$tmp/etc/hysteria2/slowdns.previous"

echo old-binary > "$tmp/usr/local/bin/dnstt-server"
echo old-unit > "$tmp/etc/systemd/system/slowdns.service"
echo old-ns > "$CONF_DIR/nsdomain.conf"
echo old-key > "$tmp/etc/slowdns/server.key"
echo old-pub > "$tmp/etc/slowdns/server.pub"
echo 'nameserver 127.0.0.53' > "$tmp/stub.conf"
ln -s "$tmp/stub.conf" "$tmp/etc/resolv.conf"
echo 'nameserver 1.1.1.1' > "$tmp/run/systemd/resolve/resolv.conf"
printf '#!/bin/bash\nexit 0\n' > "$SD_WORK/dnstt-server"
echo new-key > "$SD_WORK/server.key"
echo new-pub > "$SD_WORK/server.pub"
sleep() { :; }
ss() { echo 'UNCONN 0 0 *:53 *:* users:(("dnstt-server",pid=99,fd=2))'; }
ufw() { echo 'Status: inactive'; }
getent() { return 0; }
START_FAIL=1 reject slowdns_deploy
[ "$(cat "$tmp/usr/local/bin/dnstt-server")" = old-binary ]
[ "$(cat "$tmp/etc/systemd/system/slowdns.service")" = old-unit ]
[ "$(cat "$CONF_DIR/nsdomain.conf")" = old-ns ]
[ "$(cat "$tmp/etc/slowdns/server.key")" = old-key ]
[ "$(readlink "$tmp/etc/resolv.conf")" = "$tmp/stub.conf" ]
# A resolved stub adjustment must roll back if activation fails.
HY2_SS='UNCONN 0 0 127.0.0.53:53 0.0.0.0:* users:(("systemd-resolve",pid=8,fd=2))'
RESOLVED_ACTIVE=1 START_FAIL=1 reject slowdns_deploy
[ "$(readlink "$tmp/etc/resolv.conf")" = "$tmp/stub.conf" ]
[ ! -e "$tmp/etc/systemd/resolved.conf.d/zz-ssh-panel-slowdns.conf" ]
HY2_SS=""
slowdns_deploy
[ "$(cat "$CONF_DIR/nsdomain.conf")" = dns.example.com ]
grep -q 'dns.example.com 127.0.0.1:22' "$tmp/etc/systemd/system/slowdns.service"
if grep -E '^(stop|restart|disable|enable --now) (hysteria|xray|ssh|stunnel|dropbear|ws-proxy)' "$tmp/service-calls"; then
    echo "Touched another protocol"; exit 1
fi
echo "PASS: Go version/ecdh, checksum failure, port refusal, rollback, and isolated deployment"
# The single SlowDNS action shows details immediately when active; it must not
# run the installer or prompt in that path.
sed -n '/^slowdns_menu() {/,/^slowdns_info() {/p' scripts/ssh-ssl-setup.sh |
    sed '$d' > "$tmp/menu-functions"
source "$tmp/menu-functions"
(
    slowdns_info() { echo details >> "$tmp/actions"; }
    slowdns_install() { echo install >> "$tmp/actions"; }
    SD_STARTED=1
    slowdns_menu
    [ "$(cat "$tmp/actions")" = details ]
    : > "$tmp/actions"
    SD_STARTED=0
    slowdns_menu
    [ "$(cat "$tmp/actions")" = install ]
)
echo "PASS: active SlowDNS is view-only; inactive SlowDNS enters setup directly"
