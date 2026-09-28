#!/bin/bash
# Standalone isolated menu-function tests; never run the installer or touch /etc.
set -euo pipefail
cd "$(dirname "$0")/.."
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
sed -n '/^# Hysteria 2 is deliberately isolated/,/^menu_item() {/p' scripts/ssh-ssl-setup.sh |
    sed '$d' > "$tmp/functions"
# Extract functions only, then override their paths/mocks in this shell.
source "$tmp/functions"
SKY=; HOST_DISPLAY=localhost
HY2_DIR="$tmp/etc/hysteria2"
HY2_PORT_FILE="$HY2_DIR/port"
HY2_USERS="$HY2_DIR/accounts"
HY2_SLOW="$HY2_DIR/slowdns.previous"
HY2_HOP="$tmp/firewall"
mkdir -p "$HY2_DIR"
printf '#!/bin/bash\nexit 0\n' > "$HY2_HOP"
chmod +x "$HY2_HOP"
printf 'cert\n' > "$HY2_DIR/server.crt"
printf 'key\n' > "$HY2_DIR/server.key"
touch "$HY2_USERS"
pause() { :; }
section() { :; }
note() { :; }
ok() { :; }
err() { echo "ERROR: $*" >&2; }
hy2_install() { return 0; }
hy2_write_support() { return 0; }
hy2_slow_configured() { return 0; }
state="$tmp/state"
printf '1 1\n' > "$state"
systemctl() {
    case "$*" in
        'is-active --quiet hysteria2.service') return 1 ;;
        'is-active --quiet slowdns.service') [ "$(cut -d' ' -f1 "$state")" = 1 ] ;;
        'is-enabled --quiet slowdns.service') [ "$(cut -d' ' -f2 "$state")" = 1 ] ;;
        'disable --now slowdns.service') echo '0 0' > "$state" ;;
        'enable slowdns.service') echo "0 1" > "$state" ;;
        'start slowdns.service') echo "1 1" > "$state" ;;
        'enable --now hysteria2.service') return 1 ;;
        'disable --now hysteria2.service') return 0 ;;
        *) return 0 ;;
    esac
}
ss() {
    [ "${SS_FAIL:-0}" != 1 ] || return 1
    if [ "${SS_FAIL_AFTER_STOP:-0}" = 1 ] && [ "$(cut -d' ' -f1 "$state")" = 0 ]; then
        return 1
    fi
    if [ "${SS_UNKNOWN:-0}" = 1 ]; then
        echo 'UNCONN 0 0 0.0.0.0:53 0.0.0.0:* users:(("dnsmasq",pid=10,fd=2))'
    elif [ "${SS_SLOW:-0}" = 1 ] && [ "$(cut -d' ' -f1 "$state")" = 1 ]; then
        echo 'UNCONN 0 0 0.0.0.0:53 0.0.0.0:* users:(("dnstt-server",pid=10,fd=2))'
    elif [ "${SS_HOP:-0}" = 1 ]; then
        echo 'UNCONN 0 0 0.0.0.0:51200 0.0.0.0:*'
    fi
}
iptables() {
    [ "${IPT_FAIL:-0}" != 1 ] || return 1
    [ "${NAT_RULE:-0}" = 1 ] &&
        echo '-A PREROUTING -p udp -m udp --dport 51000:51999 -j REDIRECT --to-ports 1234'
    [ "${TCP_RULE:-0}" = 1 ] &&
        echo '-A PREROUTING -p tcp -m tcp --dport 443 -j REDIRECT --to-ports 1234'
    return 0
}
ip6tables() {
    [ "${IPT6_FAIL:-0}" != 1 ] || return 1
    [ "${NAT6_RULE:-0}" = 1 ] &&
        echo '-A PREROUTING -p udp -m udp --dport 51200 -j REDIRECT --to-ports 1234'
    return 0
}
nft() {
    [ "${NFT_FAIL:-0}" != 1 ] || return 1
    [ "${NFT_RULE:-0}" = 1 ] &&
        echo 'udp dport 443 redirect to :8443'
    [ "${NFT_UNKNOWN:-0}" = 1 ] &&
        echo 'udp dport @native_set redirect to :8443'
    [ "${NFT_ALLOW:-0}" = 1 ] &&
        echo 'udp dport 53 accept'
    return 0
}
assert_reject() {
    if "$@" >/dev/null 2>&1; then echo "Expected rejection: $*" >&2; exit 1; fi
}
# Dependency preparation never installs without approval and does not bypass
# verification after apt failure or an incomplete installation.
(
    installed=0
    hy2_missing_tools() { [ "$installed" = 1 ] || echo nft; }
    apt-get() { installed=1; }
    assert_reject hy2_prepare_tools <<< n
    [ "$installed" = 0 ]
    hy2_prepare_tools <<< y
    [ "$installed" = 1 ]
    installed=0
    apt-get() { return 1; }
    assert_reject hy2_prepare_tools <<< y
    apt-get() { return 0; }
    assert_reject hy2_prepare_tools <<< y
)
hy2_prepare_tools
assert_reject hy2_check_port 36712 0
assert_reject hy2_check_port 20000 0
assert_reject hy2_check_port 50000 0
assert_reject hy2_check_port 51000 0
assert_reject hy2_check_port 99999 0
hy2_check_port 443 0
SS_SLOW=1 hy2_check_port 53 0
SS_SLOW=1 NFT_ALLOW=1 hy2_check_port 53 0
SS_UNKNOWN=1 assert_reject hy2_check_port 53 0
SS_HOP=1 assert_reject hy2_check_port 443 1
NAT_RULE=1 assert_reject hy2_check_port 443 1
NAT_RULE=1 hy2_check_port 443 0
NAT6_RULE=1 assert_reject hy2_check_port 443 1
NFT_RULE=1 assert_reject hy2_check_port 443 0
NFT_UNKNOWN=1 assert_reject hy2_check_port 443 0
TCP_RULE=1 hy2_check_port 443 0
SS_FAIL=1 assert_reject hy2_check_port 443 0
IPT_FAIL=1 assert_reject hy2_check_port 443 0
IPT6_FAIL=1 assert_reject hy2_check_port 443 0
NFT_FAIL=1 assert_reject hy2_check_port 443 0
# Failed start after a confirmed SlowDNS handover must restore both active
# and enabled states, discard HY2 port/config and keep existing accounts.
SS_SLOW=1 hy2_activate <<< $'53\nn\nYES' >/dev/null 2>&1 || :
[ "$(cat "$state")" = '1 1' ]
[ ! -e "$HY2_SLOW" ] && [ ! -e "$HY2_PORT_FILE" ]
[ ! -e "$HY2_DIR/config.yaml" ] && [ -f "$HY2_USERS" ]
# A failed post-handover inspection must also restore SlowDNS.
SS_SLOW=1 SS_FAIL_AFTER_STOP=1 hy2_activate <<< $'53\nn\nYES' >/dev/null 2>&1 || :
[ "$(cat "$state")" = '1 1' ]
[ ! -e "$HY2_SLOW" ] && [ ! -e "$HY2_PORT_FILE" ]
# Unknown listener MUST be refused before touching SlowDNS.
SS_UNKNOWN=1 hy2_activate <<< $'53\nn\nYES' >/dev/null 2>&1 || :
[ "$(cat "$state")" = '1 1' ]
[ ! -e "$HY2_SLOW" ]
# Port 443 failure cannot affect SlowDNS or touch TCP port 443.
hy2_activate <<< $'443\nn' >/dev/null 2>&1 || :
[ "$(cat "$state")" = '1 1' ]
[ ! -e "$HY2_PORT_FILE" ]
# The standard and multi-port variants must both encode URI userinfo.
printf '1\n' > "$HY2_DIR/hop"
links=$(hy2_link alice 'strong@pass+!' 443)
[[ "$links" == *'Standard: hysteria2://alice:strong%40pass%2B%21@localhost:443/?insecure=1#alice'* ]]
[[ "$links" == *'Hopping:  hysteria2://alice:strong%40pass%2B%21@localhost:443,51000-51999/?insecure=1#alice'* ]]
printf '0\n' > "$HY2_DIR/hop"
[[ $(hy2_link alice 'strong@pass+!' 443) != *'Hopping:'* ]]
# Verify the exact generated command-auth script with its accounts path
# redirected to the temporary fixture (no production files are written).
sed -n '/^    cat > "\$HY2_DIR\/auth" <<'\''HY2AUTHEOF'\''/,/^HY2AUTHEOF$/p' \
    scripts/ssh-ssl-setup.sh | sed '1d;$d' |
    sed "s@/etc/hysteria2/accounts@$HY2_USERS@" > "$tmp/auth"
chmod +x "$tmp/auth"
printf 'alice\tstrongpass123\t29991231\nexpired\tstrongpass123\t20000101\n' > "$HY2_USERS"
[ "$("$tmp/auth" 127.0.0.1 alice:strongpass123 0)" = alice ]
if "$tmp/auth" 127.0.0.1 expired:strongpass123 0 >/dev/null; then exit 1; fi
if "$tmp/auth" 127.0.0.1 alice:badpassword 0 >/dev/null; then exit 1; fi
echo 'Hysteria 2 guards and handover rollback: OK'