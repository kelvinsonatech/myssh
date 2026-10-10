#!/bin/bash
# Extract functions into temporary fixtures; never run a VPS installer here.
set -euo pipefail
cd "$(dirname "$0")/.."
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
export ZI_TEST_ROOT="$tmp"
python3 - <<'PY'
import os,pathlib,subprocess
s=pathlib.Path("scripts/ssh-ssl-setup.sh").read_text()
start=s.index("# Hysteria 2 is deliberately isolated")
end=s.index("menu_item() {",start)
f=s[start:end].replace("/etc/hysteria2",os.environ["ZI_TEST_ROOT"]+"/hy2")
pathlib.Path(os.environ["ZI_TEST_ROOT"]+"/functions").write_text(f)
PY
source "$tmp/functions"
ZI_DIR="$tmp/zivpn"; ZI_BIN="$tmp/bin-zivpn"
ZI_UNIT="$tmp/zivpn.service"; ZI_FW="$tmp/firewall"
mkdir -p "$ZI_DIR" "$tmp/hy2"
err() { echo "$*" >&2; }
pause() { :; }
note() { :; }
ok() { :; }
eval "$(declare -f zi_details | sed '1s/zi_details/zi_render_details/')"
zi_details() { :; }
(
    password=""
    zi_prompt_password <<< "" >/dev/null
    [ "$password" = zipox ]
    zi_prompt_password <<< 'my custom\password' >/dev/null
    [ "$password" = 'my custom\password' ]
    if zi_prompt_password </dev/null >/dev/null; then exit 1; fi
    long=$(printf '%129s' x)
    if zi_prompt_password <<< "$long" >/dev/null 2>&1; then exit 1; fi
)
echo "PASS: default/custom passwords, EOF cancellation and length validation"
# Dependency checks/installations are mocked: never run apt on this machine.
(
    ready=0; calls=0; apt_fail=0; stays_missing=0
    command() {
        if [[ "$1" = -v ]]; then
            case "$2" in
                iptables|ip6tables) [ "$ready" = 1 ]; return;;
                *) return 0;;
            esac
        fi
        builtin command "$@"
    }
    apt-get() {
        calls=$((calls + 1))
        [ "$*" = "install -y --no-upgrade --no-remove --no-install-recommends iptables" ] || return 9
        [ "$DEBIAN_FRONTEND" = noninteractive ] && [ "$NEEDRESTART_MODE" = l ] || return 9
        [ "$apt_fail" = 0 ] || return 1
        [ "$stays_missing" = 1 ] || ready=1
        return 0
    }
    zi_prepare_tools
    [ "$calls" = 1 ] && [ "$ready" = 1 ]
    zi_prepare_tools
    [ "$calls" = 1 ] # Already available: no apt invocation.
    ready=0; apt_fail=1
    if zi_prepare_tools 2>/dev/null; then exit 1; fi
    apt_fail=0; stays_missing=1
    if zi_prepare_tools 2>/dev/null; then exit 1; fi
)
echo "PASS: missing iptables auto-install, deduplication, no-op, apt failure and post-install checks"
ss() {
    [ "${SS_FAIL:-0}" = 0 ] || return 1
    printf '%s\n' "${LISTENERS:-}"
}
iptables() {
    [ "${IPT_FAIL:-0}" = 0 ] || return 1
    printf '%s\n' "${NAT:-}"
}
ip6tables() { printf '%s\n' "${NAT6:-}"; }
nft() { printf '%s\n' "${NFT:-}"; }
reject() { if "$@" >"$tmp/reject.log" 2>&1; then echo "Unexpected success: $*"; exit 1; fi; }
zi_guard
LISTENERS='UNCONN 0 0 *:53 *:* users:(("dnstt-server",pid=1,fd=1))' zi_guard
LISTENERS='UNCONN 0 0 *:36712 *:* users:(("hysteria",pid=1,fd=1))' zi_guard
LISTENERS='UNCONN 0 0 *:5667 *:* users:(("unknown",pid=1,fd=1))' reject zi_guard
LISTENERS='UNCONN 0 0 *:10000 *:* users:(("unknown",pid=1,fd=1))' reject zi_guard
SS_FAIL=1 reject zi_guard
IPT_FAIL=1 reject zi_guard
NAT='-A PREROUTING -p udp --dport 6000:9000 -j REDIRECT --to-ports 53' reject zi_guard
NAT6='-A PREROUTING -p udp --dport 5667 -j REDIRECT --to-ports 53' reject zi_guard
NAT='-A PREROUTING -p udp --dport 20000:50000 -j REDIRECT --to-ports 36712' zi_guard
NAT='-A PREROUTING -p udp -j REDIRECT --to-ports 53' reject zi_guard
NFT='udp dport 18000 redirect to :53' reject zi_guard
NFT='udp dport @ports redirect to :53' reject zi_guard
NFT='udp dport 53 accept' zi_guard
echo 7000 > "$tmp/hy2/port"; reject zi_guard
echo 443 > "$tmp/hy2/port"; zi_guard
printf '%s\n' 'password "quoted"\test' 'another@password!' > "$tmp/passwords"
zi_config "$tmp/passwords" "$ZI_DIR/config.json"
python3 - "$ZI_DIR/config.json" <<'PY'
import sys,json
c=json.load(open(sys.argv[1]))
assert c["listen"]==":5667" and c["obfs"]=="zivpn"
assert c["auth"]=={"mode":"passwords","config":['password "quoted"\\test','another@password!']}
PY
: > "$tmp/passwords"
reject zi_config "$tmp/passwords" "$tmp/empty.json"
printf 'duplicatepass\nduplicatepass\n' > "$tmp/passwords"
reject zi_config "$tmp/passwords" "$tmp/duplicate.json"
touch "$ZI_DIR/.ssh-panel-owned"
(
    TEAL=""; NC=""; G=""; Y=""; SERVER_IP=192.0.2.1
    section() { printf '%s\n' "$1"; }
    row() { printf '%s\n' "$2"; }
    line_top() { :; }; line_mid() { :; }; line_bot() { :; }
    zi_active() { return 0; }
    cp "$ZI_DIR/config.json" "$tmp/details-before.json"
    zi_render_details > "$tmp/details"
    grep -q "Currently activated" "$tmp/details"
    grep -q "192.0.2.1" "$tmp/details"
    grep -q "6000–19999" "$tmp/details"
    grep -q "PASSWORD 1" "$tmp/details"
    grep -qF 'password "quoted"\test' "$tmp/details"
    ! grep -Eq 'Isolated|Reboot|expiry|Self-signed|listener' "$tmp/details"
    zi_active() { return 1; }
    zi_render_details > "$tmp/details"
    grep -q "Currently inactive" "$tmp/details"
    cmp "$ZI_DIR/config.json" "$tmp/details-before.json"
)
echo "PASS: concise active/inactive connection details preserve existing passwords"
zi_write_support
bash -n "$ZI_FW"
grep -q '^ExecStartPre=/usr/local/bin/zivpn-panel-firewall up$' "$ZI_UNIT"
grep -q '^ExecStopPost=/usr/local/bin/zivpn-panel-firewall down$' "$ZI_UNIT"
! grep -Eq 'sysctl|iptables.* -(F|X)|ufw reset' "$ZI_FW"
python3 - "$tmp" <<'PY'
import json,os,pathlib,subprocess,sys
root=pathlib.Path(sys.argv[1]); tools=root/"tools"; tools.mkdir()
state=root/"firewall-state.json"
seed={"input": [], "nat": [["-A","PREROUTING","-p","udp","--dport","20000:50000",
                           "-j","REDIRECT","--to-ports","36712"]]}
state.write_text(json.dumps(seed))
tool=tools/"mock"
tool.write_text("""#!/usr/bin/env python3
import json,os,pathlib,sys
name=pathlib.Path(sys.argv[0]).name
path=pathlib.Path(os.environ["ZI_TEST_ROOT"])/"firewall-state.json"
a=sys.argv[1:]
if name=="ip": print("default via 10.0.0.1 dev eth0"); sys.exit()
if name in ("ss","nft","ip6tables"): sys.exit()
s=json.loads(path.read_text()); key="input"
if a[:2]==["-w","5"]: a=a[2:]
if a[:2]==["-t","nat"]: key="nat"; a=a[2:]
op=a[0]
if op=="-S":
 for r in s[key]: print(" ".join(r))
 sys.exit()
chain=a[1]; args=a[2:]
if op=="-I": args=args[1:]
rule=["-A",chain]+args
if op=="-C": sys.exit(0 if rule in s[key] else 1)
if op in ("-I","-A"):
 if key=="nat" and (path.parent/"fail-nat").exists(): sys.exit(1)
 s[key].append(rule)
elif op=="-D": s[key].remove(rule)
else: raise SystemExit("Unexpected operation "+repr(a))
path.write_text(json.dumps(s))
""")
tool.chmod(0o755)
for name in ("ip","ss","nft","ip6tables","iptables"):
    (tools/name).symlink_to(tool)
helper=root/"firewall"
s=helper.read_text().replace("export PATH=/usr/local/sbin:/usr/sbin:/sbin:$PATH",
                            f"export PATH={tools}:$PATH")
s=s.replace("DIR=/etc/zivpn",f"DIR={root}/zivpn")
s=s.replace("/run/lock/zivpn-panel-firewall.lock",str(root/"firewall.lock"))
helper.write_text(s)
def run(action,success=True):
    p=subprocess.run(["bash",str(helper),action],capture_output=True,text=True)
    assert (p.returncode==0)==success, p.stderr
run("up")
one=json.loads(state.read_text())
assert len(one["nat"])==2 and len(one["input"])==1
run("up")
assert json.loads(state.read_text())==one, "Duplicate rules after restart"
run("down")
assert json.loads(state.read_text())==seed, "Removed another tunnel's rule"
(root/"fail-nat").touch()
run("up",False)
assert json.loads(state.read_text())==seed, "Partial firewall failure not rolled back"
print("PASS: reboot-style firewall setup, idempotence, own-rule cleanup and partial rollback")
PY

# Applying an account change restarts ONLY ZIVPN; restart failure restores config.
cp "$ZI_DIR/config.json" "$tmp/original"
zi_active() { return 0; }
systemctl() {
    printf '%s\n' "$*" >> "$tmp/systemctl.log"
    return 1
}
zi_passwords add <<< $'newpassword123\nYES' >/dev/null 2>&1
cmp "$ZI_DIR/config.json" "$tmp/original"
! grep -v '^restart zivpn.service$' "$tmp/systemctl.log"
# Existing, unmanaged service/files cannot be overwritten.
rm "$ZI_DIR/.ssh-panel-owned"
zi_install </dev/null >/dev/null 2>&1
cmp "$ZI_DIR/config.json" "$tmp/original"
echo "PASS: ZIVPN conflict guards, JSON escaping, isolated helpers and password rollback"
