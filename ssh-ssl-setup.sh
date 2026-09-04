#!/bin/bash
# =============================================================
# SSH + WEBSOCKET + SSL VPN SERVER SETUP + MANAGEMENT PANEL
#
# Working payload/SSL layout (HTTP Injector / HTTP Custom style):
#
#   80   — WebSocket / SSH proxy  (payload -> 101 -> SSH)
#   443  — SSL/TLS  (stunnel) -> WebSocket proxy -> SSH   (SSL payload)
#   447  — SSL/TLS  (stunnel) -> OpenSSH direct
#   22   — OpenSSH  (management / direct)
#   109  — Dropbear (direct)
#   143  — Dropbear (direct alt)
#
# The port-80 proxy is DUAL MODE:
#   * If the client sends an HTTP/WebSocket payload, it replies
#     "HTTP/1.1 101 Switching Protocols" then tunnels to SSH.
#   * If the client speaks raw SSH (no payload), it tunnels directly.
#   This makes it work with WebSocket payloads AND plain SSH.
#
# Usage:
#   chmod +x ssh-ssl-setup.sh
#   sudo ./ssh-ssl-setup.sh
# =============================================================

set -e

BGreen='\033[1;32m'; BYellow='\033[1;33m'; BCyan='\033[1;36m'
BRed='\033[1;31m'; BPurple='\033[1;35m'; NC='\033[0m'
# extended palette for the advanced installer UI
BOLD='\033[1m'; DIM='\033[2m'
TEAL='\033[38;5;44m'; SKY='\033[38;5;39m'; LIME='\033[38;5;155m'
GRY='\033[38;5;240m'; BWHITE='\033[97m'; PINK='\033[38;5;213m'; ORANGE='\033[38;5;208m'
export LANG=C.UTF-8 LC_ALL=C.UTF-8 2>/dev/null || true

# During the phased install, info/success are silenced so the single animated
# progress bar stays on one clean line; warn/error still print (problems only).
UI_SILENT=0
info()    { [ "$UI_SILENT" = "1" ] && return 0; echo -e "${BCyan}[*] $*${NC}"; }
success() { [ "$UI_SILENT" = "1" ] && return 0; echo -e "${BGreen}[✓] $*${NC}"; }
warn()    { echo -e "${BYellow}[!] $*${NC}"; }
error()   { echo -e "${BRed}[✗] $*${NC}"; exit 1; }

# ── advanced installer HUD ──────────────────────────────────────────
# A growing, colour-graded checklist. Each phase prints exactly one crisp
# line the instant it starts: the PREVIOUS step is stamped done (✔) with its
# duration, and a slim gradient meter tracks overall progress. No spinners,
# no sleeps — it renders as fast as the work runs.
INSTALL_TOTAL=12; INSTALL_STEP=0; INSTALL_T0=$SECONDS
_PH_T0=$SECONDS; _PH_NAME=""
_TW=$(tput cols 2>/dev/null || echo 72); [ "$_TW" -gt 80 ] && _TW=80; [ "$_TW" -lt 48 ] && _TW=48
# 6-stop cool→warm gradient used to colour the meter as it fills
_MG=('\033[38;5;45m' '\033[38;5;44m' '\033[38;5;44m' '\033[38;5;48m' '\033[38;5;83m' '\033[38;5;155m')

_meter() {  # _meter PCT  -> gradient-filled slim bar
    local pct="$1" bw=22 fl em i seg gi out=""
    fl=$(( bw * pct / 100 )); em=$(( bw - fl ))
    i=0; while [ "$i" -lt "$fl" ]; do
        seg=$(( i * 6 / bw )); [ "$seg" -gt 5 ] && seg=5
        out="${out}${_MG[$seg]}━"; i=$(( i + 1 ))
    done
    out="${out}${GRY}"
    i=0; while [ "$i" -lt "$em" ]; do out="${out}╌"; i=$(( i + 1 )); done
    printf '%b' "$out${NC}"
}

# stamp the just-finished phase as a completed checklist row
_seal() {
    [ -z "$_PH_NAME" ] && return 0
    local d=$(( SECONDS - _PH_T0 ))
    printf "\r\033[K ${GRY}[${DIM}%02d${GRY}/%02d]${NC} ${LIME}✔${NC} ${DIM}%-18.18s${NC} ${GRY}%02ds${NC}\n" \
        "$INSTALL_STEP" "$INSTALL_TOTAL" "$_PH_NAME" "$d"
}

phase() {  # phase "Title"
    _seal
    INSTALL_STEP=$(( INSTALL_STEP + 1 )); _PH_NAME="$1"; _PH_T0=$SECONDS
    local pct=$(( INSTALL_STEP * 100 / INSTALL_TOTAL ))
    # live "in progress" row for the current phase
    printf "\r\033[K ${GRY}[${BWHITE}%02d${GRY}/%02d]${NC} ${SKY}▸${NC} ${BWHITE}${BOLD}%-18.18s${NC} %b ${TEAL}${BOLD}%3d%%${NC}" \
        "$INSTALL_STEP" "$INSTALL_TOTAL" "$1" "$(_meter "$pct")" "$pct"
    if [ "$INSTALL_STEP" -ge "$INSTALL_TOTAL" ]; then
        _seal; _PH_NAME=""
        local tot=$(( SECONDS - INSTALL_T0 ))
        printf "\n ${LIME}${BOLD}✔ all %d phases complete${NC} ${GRY}in %02ds${NC}\n" "$INSTALL_TOTAL" "$tot"
    fi
}

[[ $EUID -ne 0 ]] && error "This script must be run as root."

CONF_DIR=/etc/ssh-panel
mkdir -p "$CONF_DIR"
STUNNEL_CERT=/etc/stunnel/stunnel.pem

# ═══════════════════════════════════════════
# ASK FOR DOMAIN
# ═══════════════════════════════════════════
clear
printf '\033[?25l'   # hide cursor for the intro animation
# gradient ASCII banner, revealed line-by-line
_banner=(
"    ███████╗███████╗██╗  ██╗    ██╗   ██╗██████╗ ███╗   ██╗"
"    ██╔════╝██╔════╝██║  ██║    ██║   ██║██╔══██╗████╗  ██║"
"    ███████╗███████╗███████║    ██║   ██║██████╔╝██╔██╗ ██║"
"    ╚════██║╚════██║██╔══██║    ╚██╗ ██╔╝██╔═══╝ ██║╚██╗██║"
"    ███████║███████║██║  ██║     ╚████╔╝ ██║     ██║ ╚████║"
"    ╚══════╝╚══════╝╚═╝  ╚═╝      ╚═══╝  ╚═╝     ╚═╝  ╚═══╝"
)
_grad=('\033[38;5;201m' '\033[38;5;165m' '\033[38;5;39m' '\033[38;5;51m' '\033[38;5;46m' '\033[38;5;226m')
echo ""
for i in "${!_banner[@]}"; do
    echo -e "  ${_grad[$i]}${BOLD}${_banner[$i]}${NC}"
done
echo -e "         ${GRY}ws${NC} ${DIM}·${NC} ${GRY}ssl${NC} ${DIM}·${NC} ${GRY}openssh${NC} ${DIM}·${NC} ${GRY}dropbear${NC} ${DIM}·${NC} ${GRY}v2ray${NC}   ${BWHITE}${BOLD}server installer${NC}"
echo ""
printf '\033[?25h'   # restore cursor for the prompt

# ── styled domain prompt ──
echo -e "  ${TEAL}╭──────────────────────────────────────────────────────╮${NC}"
echo -e "  ${TEAL}│${NC}  ${BWHITE}${BOLD}DOMAIN SETUP${NC}                                        ${TEAL}│${NC}"
echo -e "  ${TEAL}├──────────────────────────────────────────────────────┤${NC}"
echo -e "  ${TEAL}│${NC}  ${GRY}Enter a domain pointed at this server, or leave${NC}     ${TEAL}│${NC}"
echo -e "  ${TEAL}│${NC}  ${GRY}blank to use a self-signed certificate (connect${NC}     ${TEAL}│${NC}"
echo -e "  ${TEAL}│${NC}  ${GRY}by IP).${NC}                                              ${TEAL}│${NC}"
echo -e "  ${TEAL}╰──────────────────────────────────────────────────────╯${NC}"
echo ""
read -rp "$(echo -e "   ${SKY}❯${NC} ${BWHITE}Domain${NC} ${GRY}(blank = self-signed)${NC} : ")" DOMAIN
DOMAIN="$(echo "$DOMAIN" | tr -d '[:space:]')"
echo ""

# ── styled SlowDNS (NS) prompt — optional ──
echo -e "  ${PINK}╭──────────────────────────────────────────────────────╮${NC}"
echo -e "  ${PINK}│${NC}  ${BWHITE}${BOLD}SLOWDNS SETUP${NC} ${GRY}(optional)${NC}                          ${PINK}│${NC}"
echo -e "  ${PINK}├──────────────────────────────────────────────────────┤${NC}"
echo -e "  ${PINK}│${NC}  ${GRY}Enter the NS host delegated to this server's IP${NC}     ${PINK}│${NC}"
echo -e "  ${PINK}│${NC}  ${GRY}(e.g. dns.example.com). Requires an NS + A record${NC}   ${PINK}│${NC}"
echo -e "  ${PINK}│${NC}  ${GRY}at your DNS host. Leave blank to skip SlowDNS.${NC}      ${PINK}│${NC}"
echo -e "  ${PINK}╰──────────────────────────────────────────────────────╯${NC}"
echo ""
read -rp "$(echo -e "   ${PINK}❯${NC} ${BWHITE}NS domain${NC} ${GRY}(blank = skip)${NC} : ")" NS_DOMAIN
NS_DOMAIN="$(echo "$NS_DOMAIN" | tr -d '[:space:]')"

apt-get install -y curl >/dev/null 2>&1 || true
SERVER_IP=$(curl -s https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')
echo "$DOMAIN"    > "$CONF_DIR/domain.conf"
echo "$SERVER_IP" > "$CONF_DIR/ip.conf"
echo "$NS_DOMAIN" > "$CONF_DIR/nsdomain.conf"

# ── system info panel ──
_OS=$( (. /etc/os-release 2>/dev/null; echo "$PRETTY_NAME") || echo "Linux" )
echo ""
echo -e "  ${GRY}┌─ ${BWHITE}${BOLD}SYSTEM${NC} ${GRY}────────────────────────────────────────────┐${NC}"
printf  "  ${GRY}│${NC}  ${SKY}◆${NC} %-11s ${BWHITE}%-33.33s${NC}${GRY}│${NC}\n" "Server IP" "$SERVER_IP"
printf  "  ${GRY}│${NC}  ${SKY}◆${NC} %-11s ${BWHITE}%-33.33s${NC}${GRY}│${NC}\n" "OS" "$_OS"
if [ -n "$DOMAIN" ]; then
    printf "  ${GRY}│${NC}  ${SKY}◆${NC} %-11s ${LIME}%-33.33s${NC}${GRY}│${NC}\n" "TLS mode" "domain: $DOMAIN"
else
    printf "  ${GRY}│${NC}  ${SKY}◆${NC} %-11s ${BWHITE}%-33.33s${NC}${GRY}│${NC}\n" "TLS mode" "self-signed (connect by IP)"
fi
if [ -n "$NS_DOMAIN" ]; then
    printf "  ${GRY}│${NC}  ${SKY}◆${NC} %-11s ${PINK}%-33.33s${NC}${GRY}│${NC}\n" "SlowDNS" "NS: $NS_DOMAIN"
fi
printf  "  ${GRY}│${NC}  ${SKY}◆${NC} %-11s ${BWHITE}%-33.33s${NC}${GRY}│${NC}\n" "Steps" "$INSTALL_TOTAL install phases"
echo -e "  ${GRY}└──────────────────────────────────────────────────────┘${NC}"
echo ""
echo -e "  ${TEAL}${BOLD}▸ Installing${NC}${GRY} — automated, no input needed.${NC}"
echo ""
UI_SILENT=1   # from here on, the checklist HUD is the only output

# ─── Package manager setup ───────────────────────────────────
export DEBIAN_FRONTEND=noninteractive
if [ -f /etc/needrestart/needrestart.conf ]; then
    sed -i "s/#\$nrconf{restart} = 'i';/\$nrconf{restart} = 'a';/g" \
        /etc/needrestart/needrestart.conf 2>/dev/null || true
fi
dpkg --configure -a --force-confdef --force-confold >/dev/null 2>&1 || true
apt-get install -f -y \
    -o Dpkg::Options::="--force-confdef" \
    -o Dpkg::Options::="--force-confold" </dev/null >/dev/null 2>&1 || true

APT="apt-get install -y \
    -o Dpkg::Options::='--force-confdef' \
    -o Dpkg::Options::='--force-confold'"

phase "System packages"
apt-get update -y </dev/null >/dev/null 2>&1

# ═══════════════════════════════════════════
# SECTION 1 — OPENSSH
# ═══════════════════════════════════════════
phase "OpenSSH server"
eval "$APT openssh-server curl" </dev/null >/dev/null 2>&1

SSHD_CONF=/etc/ssh/sshd_config
[ ! -f "${SSHD_CONF}.orig" ] && cp "$SSHD_CONF" "${SSHD_CONF}.orig"

apply_sshd_setting() {
    local key="$1" val="$2"
    if grep -qE "^#?${key}" "$SSHD_CONF"; then
        sed -i "s|^#\?${key}.*|${key} ${val}|g" "$SSHD_CONF"
    else
        echo "${key} ${val}" >> "$SSHD_CONF"
    fi
}

apply_sshd_setting "Port"                    "22"
apply_sshd_setting "PermitRootLogin"         "yes"
apply_sshd_setting "PasswordAuthentication"  "yes"
apply_sshd_setting "AllowTcpForwarding"      "yes"
apply_sshd_setting "GatewayPorts"            "yes"
apply_sshd_setting "PermitTunnel"            "yes"
apply_sshd_setting "ClientAliveInterval"     "30"
apply_sshd_setting "ClientAliveCountMax"     "6"
apply_sshd_setting "UseDNS"                  "no"

systemctl enable ssh >/dev/null 2>&1 || systemctl enable sshd >/dev/null 2>&1 || true
systemctl restart ssh 2>/dev/null || systemctl restart sshd 2>/dev/null || true
success "OpenSSH configured on port 22"

# ═══════════════════════════════════════════
# SECTION 2 — DROPBEAR (direct SSH targets)
# ═══════════════════════════════════════════
phase "Dropbear SSH"
eval "$APT dropbear" </dev/null >/dev/null 2>&1

mkdir -p /etc/dropbear
for TYPE in dss rsa ecdsa ed25519; do
    KEYFILE="/etc/dropbear/dropbear_${TYPE}_host_key"
    [ -f "$KEYFILE" ] || dropbearkey -t "$TYPE" -f "$KEYFILE" >/dev/null 2>&1 || true
done

# Dropbear (and OpenSSH) accept tunnel-only accounts whose shell is /bin/false.
grep -qxF '/bin/false' /etc/shells || echo '/bin/false' >> /etc/shells
grep -qxF '/usr/sbin/nologin' /etc/shells || echo '/usr/sbin/nologin' >> /etc/shells

cat > /etc/default/dropbear <<'EOF'
NO_START=0
DROPBEAR_PORT=109
DROPBEAR_EXTRA_ARGS="-p 143 -I 300 -K 30"
DROPBEAR_BANNER=""
DROPBEAR_RECEIVE_WINDOW=65536
EOF

systemctl enable dropbear >/dev/null 2>&1 || true
systemctl restart dropbear
success "Dropbear running on ports 109 and 143"

# ═══════════════════════════════════════════
# SECTION 3 — DUAL-MODE WEBSOCKET/SSH PROXY (port 80)
# ═══════════════════════════════════════════
phase "WebSocket proxy"

# Remove any leftover Python proxy from a previous install.
rm -f /usr/local/bin/ws-proxy.py 2>/dev/null || true

BUILD_DIR=/tmp/ws-proxy-build
rm -rf "$BUILD_DIR"; mkdir -p "$BUILD_DIR"

cat > "$BUILD_DIR/main.go" <<'GOEOF'
// Dual-mode WebSocket/SSH proxy (compiled).
//
// Listens on 0.0.0.0:80. For each connection it peeks at the first bytes:
//   * If the client speaks SSH directly (data begins with "SSH-"), the
//     connection is tunnelled straight to the backend SSH server.
//   * Otherwise the bytes are treated as an HTTP/WebSocket payload: the
//     proxy replies "HTTP/1.1 101 Switching Protocols" and tunnels to SSH.
//   * If nothing arrives quickly, it assumes a direct SSH client waiting
//     for the banner and tunnels straight through.
//
// Backend SSH = OpenSSH on 127.0.0.1:22.
package main

import (
	"bytes"
	"io"
	"log"
	"net"
	"time"
)

const (
	listenAddr  = "0.0.0.0:80"
	backendAddr = "127.0.0.1:22"
	peekTimeout = 3 * time.Second
)

var response = []byte("HTTP/1.1 101 Switching Protocols\r\n" +
	"Upgrade: websocket\r\n" +
	"Connection: Upgrade\r\n\r\n")

func pipe(dst, src net.Conn, done chan struct{}) {
	buf := make([]byte, 65536)
	io.CopyBuffer(dst, src, buf)
	if c, ok := dst.(*net.TCPConn); ok {
		c.CloseWrite()
	}
	done <- struct{}{}
}

func bridge(client, backend net.Conn, prefix []byte) {
	if len(prefix) > 0 {
		backend.Write(prefix)
	}
	done := make(chan struct{}, 2)
	go pipe(backend, client, done)
	go pipe(client, backend, done)
	<-done
	<-done
	client.Close()
	backend.Close()
}

func handle(client net.Conn) {
	backend, err := net.Dial("tcp", backendAddr)
	if err != nil {
		client.Close()
		return
	}

	first := make([]byte, 4096)
	client.SetReadDeadline(time.Now().Add(peekTimeout))
	n, _ := client.Read(first)
	client.SetReadDeadline(time.Time{})
	head := first[:n]

	// Direct SSH client (or nothing yet): forward straight through.
	if n == 0 || bytes.HasPrefix(head, []byte("SSH-")) {
		bridge(client, backend, head)
		return
	}

	// HTTP / WebSocket payload: read the rest of the request headers.
	buf := append([]byte{}, head...)
	for !bytes.Contains(buf, []byte("\r\n\r\n")) && len(buf) < 8192 {
		client.SetReadDeadline(time.Now().Add(peekTimeout))
		m, e := client.Read(first)
		client.SetReadDeadline(time.Time{})
		if m > 0 {
			buf = append(buf, first[:m]...)
		}
		if e != nil {
			break
		}
	}

	// Some injector payloads send "Expect: 100-continue" and wait for a
	// provisional reply before finishing the request. Answer it so those
	// clients proceed; harmless for clients that don't expect it.
	if bytes.Contains(bytes.ToLower(buf), []byte("100-continue")) {
		client.Write([]byte("HTTP/1.1 100 Continue\r\n\r\n"))
	}

	if _, err := client.Write(response); err != nil {
		client.Close()
		backend.Close()
		return
	}

	// A payload may send SEVERAL HTTP blocks (e.g. a second CONNECT line, or a
	// body it only sends after the 100/101 reply) before the real SSH stream.
	// Forwarding that junk to SSH corrupts the handshake, so strip everything up
	// to the "SSH-" banner — the first bytes every SSH client sends. Whatever
	// precedes it is discarded; from "SSH-" onward is real SSH.
	idx := bytes.Index(buf, []byte("SSH-"))
	deadline := time.Now().Add(peekTimeout)
	for idx < 0 && len(buf) < 16384 && time.Now().Before(deadline) {
		client.SetReadDeadline(time.Now().Add(peekTimeout))
		m, e := client.Read(first)
		client.SetReadDeadline(time.Time{})
		if m > 0 {
			buf = append(buf, first[:m]...)
			idx = bytes.Index(buf, []byte("SSH-"))
		}
		if e != nil {
			break
		}
	}
	var prefix []byte
	if idx >= 0 {
		prefix = buf[idx:] // forward from the SSH banner onward
	}
	bridge(client, backend, prefix)
}

func main() {
	ln, err := net.Listen("tcp", listenAddr)
	if err != nil {
		log.Fatalf("listen %s: %v", listenAddr, err)
	}
	log.Printf("dual-mode proxy on %s -> SSH %s", listenAddr, backendAddr)
	for {
		conn, err := ln.Accept()
		if err != nil {
			continue
		}
		if tc, ok := conn.(*net.TCPConn); ok {
			tc.SetNoDelay(true)
		}
		go handle(conn)
	}
}
GOEOF

# --- Skip the whole toolchain + rebuild when nothing changed --------------
# Installing golang-go (~hundreds of MB) and recompiling on every run made
# this phase by far the slowest part of the installer. The proxy source is
# embedded above, so if the installed binary was built from the exact same
# source (hash stamp), reuse it instantly.
WS_STAMP=/usr/local/bin/.ws-proxy.src.md5
SRC_MD5=$(md5sum "$BUILD_DIR/main.go" 2>/dev/null | awk '{print $1}')
if [ -x /usr/local/bin/ws-proxy ] && [ -n "$SRC_MD5" ] \
   && [ "$(cat "$WS_STAMP" 2>/dev/null)" = "$SRC_MD5" ]; then
    PROXY_EXEC=/usr/local/bin/ws-proxy
    success "Go proxy already up to date (port 80) — build skipped"
else

# --- Ensure a Go compiler is available -----------------------------------
if ! command -v go >/dev/null 2>&1; then
    info "Installing Go toolchain..."
    eval "$APT golang-go" </dev/null >/dev/null 2>&1 || true
fi
GO_BIN="$(command -v go || true)"

BUILT=0
if [ -n "$GO_BIN" ]; then
    ( cd "$BUILD_DIR" && \
      GOFLAGS=-mod=mod GO111MODULE=off GOCACHE=/tmp/go-cache \
      "$GO_BIN" build -ldflags "-s -w" -o /usr/local/bin/ws-proxy main.go ) \
      >/dev/null 2>&1 && [ -x /usr/local/bin/ws-proxy ] && BUILT=1
fi

if [ "$BUILT" -eq 1 ]; then
    PROXY_EXEC=/usr/local/bin/ws-proxy
    echo "$SRC_MD5" > "$WS_STAMP"
    success "Compiled Go proxy installed (port 80)"
else
    # ---- Fallback: Python proxy (if Go could not be installed/built) ----
    warn "Go build unavailable — falling back to Python proxy"
    eval "$APT python3" </dev/null >/dev/null 2>&1 || true
    cat > /usr/local/bin/ws-proxy.py <<'PYEOF'
#!/usr/bin/env python3
import socket, threading
BACKEND=('127.0.0.1',22); LISTEN=('0.0.0.0',80); T=3
RESP=b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n"
def pipe(s,d):
    try:
        while True:
            b=s.recv(65536)
            if not b: break
            d.sendall(b)
    except Exception: pass
    finally:
        for x in (s,d):
            try: x.shutdown(socket.SHUT_RDWR)
            except Exception: pass
            try: x.close()
            except Exception: pass
def bridge(c,b,pre=b""):
    if pre:
        try: b.sendall(pre)
        except Exception: pass
    t1=threading.Thread(target=pipe,args=(c,b),daemon=True)
    t2=threading.Thread(target=pipe,args=(b,c),daemon=True)
    t1.start();t2.start();t1.join();t2.join()
def handle(c):
    b=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    try: b.connect(BACKEND)
    except Exception: c.close(); return
    f=b""
    try:
        c.settimeout(T); f=c.recv(4096)
    except socket.timeout: f=b""
    except Exception: c.close(); b.close(); return
    finally:
        try: c.settimeout(None)
        except Exception: pass
    if f.startswith(b"SSH-") or f==b"":
        try: bridge(c,b,f)
        finally: c.close(); b.close()
        return
    buf=f
    try:
        c.settimeout(T)
        while b"\r\n\r\n" not in buf and len(buf)<8192:
            m=c.recv(4096)
            if not m: break
            buf+=m
    except Exception: pass
    finally:
        try: c.settimeout(None)
        except Exception: pass
    # Honor "Expect: 100-continue" so injector apps that wait for a provisional
    # reply before finishing the request proceed; harmless otherwise.
    if b"100-continue" in buf.lower():
        try: c.sendall(b"HTTP/1.1 100 Continue\r\n\r\n")
        except Exception: pass
    try: c.sendall(RESP)
    except Exception: c.close(); b.close(); return
    # A payload may send more HTTP blocks (a second CONNECT line, or a body sent
    # only after the 100/101 reply) before the real SSH stream. Forwarding that
    # junk corrupts the SSH handshake, so strip everything up to the "SSH-"
    # banner (the first bytes every SSH client sends) and forward from there.
    idx=buf.find(b"SSH-")
    import time as _t; _dl=_t.time()+T
    try:
        c.settimeout(T)
        while idx<0 and len(buf)<16384 and _t.time()<_dl:
            m=c.recv(4096)
            if not m: break
            buf+=m; idx=buf.find(b"SSH-")
    except Exception: pass
    finally:
        try: c.settimeout(None)
        except Exception: pass
    pre=buf[idx:] if idx>=0 else b""
    try: bridge(c,b,pre)
    finally: c.close(); b.close()
def main():
    s=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
    s.bind(LISTEN); s.listen(1024)
    while True:
        try:
            c,_=s.accept()
            threading.Thread(target=handle,args=(c,),daemon=True).start()
        except Exception: pass
main()
PYEOF
    chmod +x /usr/local/bin/ws-proxy.py
    PROXY_EXEC="/usr/bin/python3 /usr/local/bin/ws-proxy.py"
fi

fi  # end skip-rebuild (stamp matched)

cat > /etc/systemd/system/ws-proxy.service <<EOF
[Unit]
Description=Dual-mode WebSocket/SSH proxy (port 80 -> Dropbear 109)
After=network.target dropbear.service

[Service]
Type=simple
User=root
ExecStart=${PROXY_EXEC}
LimitNOFILE=1048576
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable ws-proxy >/dev/null 2>&1
# (started after the certificate step, which needs port 80 for certbot)
success "WebSocket/SSH proxy installed (port 80)"

# ═══════════════════════════════════════════
# SECTION 4 — SSL CERTIFICATE
# ═══════════════════════════════════════════
phase "SSL certificate"
# Package mirrors can occasionally stall forever. Cap this phase; openssl is
# normally already present, and the explicit checks below fail clearly rather
# than leaving the installer frozen on "SSL certificate".
timeout 180 apt-get install -y \
    -o Dpkg::Options::="--force-confdef" \
    -o Dpkg::Options::="--force-confold" \
    stunnel4 openssl </dev/null >/dev/null 2>&1 || true
mkdir -p /etc/stunnel

# Make sure nothing is holding port 80 while certbot validates.
systemctl stop ws-proxy 2>/dev/null || true
systemctl stop nginx 2>/dev/null || true
systemctl stop apache2 2>/dev/null || true

if [ -n "$DOMAIN" ]; then
    # Re-runs should reuse a valid certificate instead of contacting ACME
    # again. A bad DNS record/firewall must never hold the whole install.
    if [ -s "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ] \
       && [ -s "/etc/letsencrypt/live/$DOMAIN/privkey.pem" ]; then
        LE_OK=1
        info "Using existing Let's Encrypt certificate for $DOMAIN"
    else
        info "Requesting Let's Encrypt certificate for $DOMAIN (max 90s) ..."
        timeout 180 apt-get install -y \
            -o Dpkg::Options::="--force-confdef" \
            -o Dpkg::Options::="--force-confold" \
            certbot </dev/null >/dev/null 2>&1 || true
        if command -v certbot >/dev/null 2>&1; then
            timeout --signal=TERM --kill-after=10s 90s \
                certbot certonly --standalone -d "$DOMAIN" \
                --non-interactive --agree-tos \
                --register-unsafely-without-email \
                --preferred-challenges http >/dev/null 2>&1 \
                && LE_OK=1 || LE_OK=0
        else
            LE_OK=0
        fi
    fi

    if [ "${LE_OK:-0}" -eq 1 ] && [ -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]; then
        chmod -R 755 /etc/letsencrypt/archive /etc/letsencrypt/live
        cat "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" \
            "/etc/letsencrypt/live/$DOMAIN/privkey.pem" > "$STUNNEL_CERT"
        success "Let's Encrypt certificate obtained for $DOMAIN"
    else
        warn "Let's Encrypt failed — using self-signed certificate"
        DOMAIN=""
    fi
fi

if [ -z "$DOMAIN" ] || [ ! -f "$STUNNEL_CERT" ]; then
    warn "Generating self-signed certificate..."
    command -v openssl >/dev/null 2>&1 || {
        systemctl start ws-proxy >/dev/null 2>&1 || true
        error "OpenSSL is unavailable; package installation failed"
    }
    timeout 30 openssl req -new -newkey rsa:2048 -days 3650 -nodes -x509 \
        -subj "/C=US/ST=NA/L=NA/O=VPN/CN=vpn-server" \
        -out "$STUNNEL_CERT" -keyout /etc/stunnel/stunnel.key >/dev/null 2>&1 || {
            systemctl start ws-proxy >/dev/null 2>&1 || true
            error "Could not generate the fallback SSL certificate"
        }
    cat /etc/stunnel/stunnel.key >> "$STUNNEL_CERT"
    rm -f /etc/stunnel/stunnel.key
    success "Self-signed certificate created"
fi
chmod 600 "$STUNNEL_CERT"

# Port 80 is free again — start the proxy.
systemctl start ws-proxy >/dev/null 2>&1 || true

# ═══════════════════════════════════════════
# SECTION 5 — STUNNEL (SSL / TLS)
#   443 -> WebSocket proxy (SSL + payload)
#   447 -> OpenSSH direct  (plain SSL)
# ═══════════════════════════════════════════
phase "Stunnel TLS"
sed -i 's/ENABLED=0/ENABLED=1/g' /etc/default/stunnel4 2>/dev/null || true

cat > /etc/stunnel/stunnel.conf <<EOF
pid     = /var/run/stunnel4.pid
cert    = ${STUNNEL_CERT}
client  = no
socket  = a:SO_REUSEADDR=1
socket  = l:TCP_NODELAY=1
socket  = r:TCP_NODELAY=1
TIMEOUTclose = 0

; SSL + WebSocket payload: TLS on 443 -> dual-mode proxy on 80 -> SSH
[ssl-ws]
accept  = 443
connect = 127.0.0.1:80

; Plain SSL to OpenSSH: TLS on 447 -> OpenSSH 22
[ssl-ssh]
accept  = 447
connect = 127.0.0.1:22
EOF

systemctl enable stunnel4 >/dev/null 2>&1 || true
systemctl restart stunnel4 >/dev/null 2>&1
success "Stunnel running — SSL 443 (payload) & 447 (direct SSH)"

# ═══════════════════════════════════════════
# SECTION 6 — FIREWALL
# ═══════════════════════════════════════════
phase "Firewall rules"
if command -v ufw >/dev/null 2>&1; then
    for P in 22 80 109 143 443 447; do
        ufw allow ${P}/tcp >/dev/null 2>&1
    done
    ufw allow 53/udp >/dev/null 2>&1   # SlowDNS (dnstt) tunnel
    success "UFW rules applied"
else
    warn "ufw not found — open TCP ports manually: 22 80 109 143 443 447 + 53/udp"
fi

# ═══════════════════════════════════════════
# SECTION 6a — ABUSE PROTECTION TOOLING (installed, enabled from the menu)
# ═══════════════════════════════════════════
# Drops the `abuse-guard` controller + a boot service. Nothing is enforced until
# the operator turns it on via the menu, so a fresh install never changes
# behaviour, speed, or breaks any protocol.
phase "Abuse protection"
cat > /usr/local/bin/abuse-guard <<'AGEOF'
#!/bin/bash
# abuse-guard — lightweight anti-abuse for the tunnel server.
#   Modules: brute-force (fail2ban) · egress firewall · torrent block · DNS filter
# Design: the egress chain RETURNs on ESTABLISHED/RELATED before any rate/port
# rule, so bulk data is never inspected (zero throughput cost). Everything is
# reversible and must never leave SlowDNS or any protocol broken.
set +e
# iptables/ip6tables/sysctl live in sbin, which is missing from PATH in some
# shells/menus — that makes every firewall rule silently fail with
# "iptables: command not found". Always fix PATH first.
export PATH=/usr/sbin:/usr/local/sbin:/sbin:$PATH
ABUSE_DIR=/etc/abuse
FLAG="$ABUSE_DIR/enabled"
CHAIN=ABUSE_OUT
CONF_DIR=/etc/ssh-panel
PUBIP=$(cat "$CONF_DIR/ip.conf" 2>/dev/null)
RESOLV_BAK="$ABUSE_DIR/resolv.conf.orig"
SLOWDNS_UNIT=/etc/systemd/system/slowdns.service
SLOWDNS_BAK="$ABUSE_DIR/slowdns.service.orig"
BLOCK_HOSTS="$ABUSE_DIR/blocklist.hosts"
# Default P2P/DHT/tracker ports (6969 falls inside 6881:6999).
TORRENT_PORTS="2710,6881:6999,51413"
mkdir -p "$ABUSE_DIR"

# ---------- egress firewall (v4 + v6) ----------
_fw_build() {   # $1 = iptables | ip6tables
    local ipt="$1"
    command -v "$ipt" >/dev/null 2>&1 || return 0
    "$ipt" -D OUTPUT -j $CHAIN 2>/dev/null
    "$ipt" -F $CHAIN 2>/dev/null
    "$ipt" -X $CHAIN 2>/dev/null
    "$ipt" -N $CHAIN 2>/dev/null || return 0
    # Never touch loopback or already-established flows -> no bandwidth cost.
    "$ipt" -A $CHAIN -o lo -j RETURN
    "$ipt" -A $CHAIN -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
    "$ipt" -A $CHAIN -m conntrack --ctstate INVALID -j DROP
    # Anti-spam: block outbound SMTP (25) — the #1 reputation killer.
    "$ipt" -A $CHAIN -p tcp --dport 25 -j DROP
    # Torrent: block default P2P/DHT ports (best effort; encrypted BT evades).
    "$ipt" -A $CHAIN -p tcp -m multiport --dports $TORRENT_PORTS -j DROP
    "$ipt" -A $CHAIN -p udp -m multiport --dports $TORRENT_PORTS -j DROP
    # Let DNS pass untouched.
    "$ipt" -A $CHAIN -p udp --dport 53 -j RETURN
    "$ipt" -A $CHAIN -p tcp --dport 53 -j RETURN
    # DDoS/scan/brute: cap NEW connections to a SINGLE destination, while the
    # bandwidth of established flows stays fully intact. Limits are deliberately
    # high (500/s, burst 1000) so many users sharing one CDN/API IP are never
    # clipped — only genuine floods (thousands/sec) are dropped.
    "$ipt" -A $CHAIN -p tcp --syn -m hashlimit --hashlimit-name ag_syn \
        --hashlimit-mode dstip --hashlimit-above 500/sec --hashlimit-burst 1000 \
        --hashlimit-htable-expire 30000 -j DROP
    "$ipt" -A $CHAIN -p udp -m conntrack --ctstate NEW -m hashlimit --hashlimit-name ag_udp \
        --hashlimit-mode dstip --hashlimit-above 500/sec --hashlimit-burst 1000 \
        --hashlimit-htable-expire 30000 -j DROP
    "$ipt" -A $CHAIN -j RETURN
    "$ipt" -C OUTPUT -j $CHAIN 2>/dev/null || "$ipt" -I OUTPUT -j $CHAIN
}
_fw_remove() {   # $1 = iptables | ip6tables
    local ipt="$1"
    command -v "$ipt" >/dev/null 2>&1 || return 0
    "$ipt" -D OUTPUT -j $CHAIN 2>/dev/null
    "$ipt" -F $CHAIN 2>/dev/null
    "$ipt" -X $CHAIN 2>/dev/null
}
apply_fw()  { _fw_build iptables;  _fw_build ip6tables; }
remove_fw() { _fw_remove iptables; _fw_remove ip6tables; }

# ---------- DNS enforcement (closes client-side bypasses) ----------
# Clients can dodge a server-side filter by resolving names themselves:
# hard-coded 8.8.8.8 in the app, or encrypted DNS (DoH/DoT) in the browser.
# When the content filter is active we (a) transparently redirect any
# tunnel-originated port-53 lookup into the local filter — the app still
# "talks to 8.8.8.8" but gets filtered answers — and (b) block the known
# encrypted-DNS side doors. NAT only sees the first packet of a connection
# and the filter chain RETURNs on ESTABLISHED first, so speed is untouched.
# Well-known DoH/DoT resolver IPs. NOTE: these anycast addresses also serve
# non-DNS traffic and are commonly used as VPN bug-hosts/proxy SNI targets,
# so port-443 traffic to them must NEVER be blocked (it would clip the
# tunnel's own path). They are kept only for reference/documentation; the
# enforced side-door blocks are protocol-wide DoT(853) only.
DOH_IPS="1.1.1.1 1.0.0.1 1.1.1.2 1.1.1.3 8.8.8.8 8.8.4.4 9.9.9.9 9.9.9.11 149.112.112.112 149.112.112.11 94.140.14.14 94.140.15.15 94.140.14.15 208.67.222.222 208.67.220.220 208.67.222.123 45.90.28.0 45.90.30.0 76.76.2.0 76.76.10.0 194.242.2.2 185.228.168.9 185.228.169.9 76.76.19.19 76.223.122.150 130.59.31.248 216.239.32.10 216.239.34.10"
DNSMASQ_UID() { id -u dnsmasq 2>/dev/null || id -u dnsmasq-nm 2>/dev/null; }
apply_dnsforce() {
    systemctl is-active --quiet dnsmasq 2>/dev/null || return 0
    # REDIRECT to loopback from the forwarded/PREROUTING path needs this.
    sysctl -qw net.ipv4.conf.all.route_localnet=1 2>/dev/null
    local uid; uid=$(DNSMASQ_UID)

    # --- Transparent DNS capture: force every port-53 lookup into dnsmasq ---
    # (a) OUTPUT: catches DNS from server-side proxies (sshd SOCKS, xray/V2Ray,
    #     ws-proxy) that resolve on behalf of tunnel users.
    iptables -t nat -N ABUSE_DNS 2>/dev/null; iptables -t nat -F ABUSE_DNS
    # dnsmasq's own upstream queries must pass or lookups loop forever.
    [ -n "$uid" ] && iptables -t nat -A ABUSE_DNS -m owner --uid-owner "$uid" -j RETURN
    iptables -t nat -A ABUSE_DNS -d 127.0.0.0/8 -j RETURN
    iptables -t nat -A ABUSE_DNS -p udp --dport 53 -j REDIRECT --to-ports 53
    iptables -t nat -A ABUSE_DNS -p tcp --dport 53 -j REDIRECT --to-ports 53
    iptables -t nat -C OUTPUT -j ABUSE_DNS 2>/dev/null || iptables -t nat -A OUTPUT -j ABUSE_DNS
    # (b) PREROUTING: catches DNS from TUN/route-based tunnels where client
    #     packets are forwarded, not locally generated. Skip anything destined
    #     to the server itself so SlowDNS (dnstt on PUBIP:53) is never hijacked.
    iptables -t nat -N ABUSE_DNSP 2>/dev/null; iptables -t nat -F ABUSE_DNSP
    iptables -t nat -A ABUSE_DNSP -m addrtype --dst-type LOCAL -j RETURN
    iptables -t nat -A ABUSE_DNSP -p udp --dport 53 -j REDIRECT --to-ports 53
    iptables -t nat -A ABUSE_DNSP -p tcp --dport 53 -j REDIRECT --to-ports 53
    iptables -t nat -C PREROUTING -j ABUSE_DNSP 2>/dev/null || iptables -t nat -I PREROUTING -j ABUSE_DNSP

    # --- Encrypted-DNS side door: DoT(853) only ---
    # We deliberately do NOT touch port 443 (tcp OR udp) to resolver IPs.
    # HTTP Custom / injector VPN configs very often use 1.1.1.1, 8.8.8.8 etc.
    # as the bug-host/proxy SNI on 443, and QUIC-based transports ride udp/443
    # — blocking either "caps" the user's own tunnel. The primary enforcement
    # is the port-53 redirect; 443 DoH is a residual bypass we accept to
    # guarantee the VPN is never broken.
    iptables -N ABUSE_DOH 2>/dev/null; iptables -F ABUSE_DOH
    iptables -A ABUSE_DOH -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
    iptables -A ABUSE_DOH -p tcp --dport 853 -j REJECT --reject-with tcp-reset
    iptables -A ABUSE_DOH -p udp --dport 853 -j DROP
    iptables -C OUTPUT -j ABUSE_DOH 2>/dev/null || iptables -I OUTPUT -j ABUSE_DOH
    iptables -C FORWARD -j ABUSE_DOH 2>/dev/null || iptables -I FORWARD -j ABUSE_DOH

    # v6: dnsmasq is v4-loopback only, so v6 DNS can't be redirected — reject it
    # so clients fall back to v4 (which lands in the filter). Also block v6 DoT.
    if command -v ip6tables >/dev/null 2>&1; then
        ip6tables -N ABUSE_DOH 2>/dev/null; ip6tables -F ABUSE_DOH
        ip6tables -A ABUSE_DOH -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
        ip6tables -A ABUSE_DOH -o lo -j RETURN
        ip6tables -A ABUSE_DOH -p udp --dport 53 -j REJECT 2>/dev/null
        ip6tables -A ABUSE_DOH -p tcp --dport 53 -j REJECT --reject-with tcp-reset
        ip6tables -A ABUSE_DOH -p tcp --dport 853 -j REJECT --reject-with tcp-reset
        ip6tables -A ABUSE_DOH -p udp --dport 853 -j DROP
        ip6tables -C OUTPUT -j ABUSE_DOH 2>/dev/null || ip6tables -I OUTPUT -j ABUSE_DOH
        ip6tables -C FORWARD -j ABUSE_DOH 2>/dev/null || ip6tables -I FORWARD -j ABUSE_DOH
    fi
}
remove_dnsforce() {
    iptables -t nat -D OUTPUT -j ABUSE_DNS 2>/dev/null
    iptables -t nat -F ABUSE_DNS 2>/dev/null; iptables -t nat -X ABUSE_DNS 2>/dev/null
    iptables -t nat -D PREROUTING -j ABUSE_DNSP 2>/dev/null
    iptables -t nat -F ABUSE_DNSP 2>/dev/null; iptables -t nat -X ABUSE_DNSP 2>/dev/null
    iptables -D OUTPUT -j ABUSE_DOH 2>/dev/null
    iptables -D FORWARD -j ABUSE_DOH 2>/dev/null
    iptables -F ABUSE_DOH 2>/dev/null; iptables -X ABUSE_DOH 2>/dev/null
    if command -v ip6tables >/dev/null 2>&1; then
        ip6tables -D OUTPUT -j ABUSE_DOH 2>/dev/null
        ip6tables -D FORWARD -j ABUSE_DOH 2>/dev/null
        ip6tables -F ABUSE_DOH 2>/dev/null; ip6tables -X ABUSE_DOH 2>/dev/null
    fi
}

# ---------- brute-force shield (fail2ban) ----------
setup_f2b() {
    # Record whether fail2ban pre-existed, so disable can restore its state
    # instead of clobbering a setup the operator already relied on.
    if ! command -v fail2ban-server >/dev/null 2>&1; then
        touch "$ABUSE_DIR/owns_fail2ban"
        DEBIAN_FRONTEND=noninteractive apt-get install -y fail2ban >/dev/null 2>&1
    else
        systemctl is-enabled --quiet fail2ban 2>/dev/null && echo enabled > "$ABUSE_DIR/f2b_prev" || echo disabled > "$ABUSE_DIR/f2b_prev"
    fi
    command -v fail2ban-server >/dev/null 2>&1 || { echo "  brute-force: fail2ban unavailable — skipped"; return 1; }
    # Dropbear filter is missing on some distros — provide one, and remember we
    # created it so teardown only removes a guard-owned file.
    if [ ! -f /etc/fail2ban/filter.d/dropbear.conf ]; then
        touch "$ABUSE_DIR/owns_dropbear_filter"
        cat > /etc/fail2ban/filter.d/dropbear.conf <<'F2BEOF'
[Definition]
failregex = ^.*[Bb]ad (PAM )?password attempt for .* from <HOST>(:\d+)?$
            ^.*[Ll]ogin attempt for nonexistent user.* from <HOST>(:\d+)?$
            ^.*exit before auth \(user '.*'.*\): Disconnect received.*<HOST>.*$
ignoreregex =
F2BEOF
    fi
    # Pick a working log source: prefer /var/log/auth.log (rsyslog); fall back
    # to the systemd journal on log-less minimal images. Without this the jails
    # can start with no log source and never ban anything.
    local src
    if [ -f /var/log/auth.log ]; then
        src=$'backend  = auto\nlogpath  = /var/log/auth.log'
    else
        src='backend  = systemd'
    fi
    cat > /etc/fail2ban/jail.d/abuse-guard.local <<F2BEOF
[sshd]
enabled  = true
$src
maxretry = 5
findtime = 10m
bantime  = 1h

[dropbear]
enabled  = true
$src
maxretry = 5
findtime = 10m
bantime  = 1h
F2BEOF
    systemctl enable fail2ban >/dev/null 2>&1
    systemctl restart fail2ban >/dev/null 2>&1
}
teardown_f2b() {
    rm -f /etc/fail2ban/jail.d/abuse-guard.local
    [ -f "$ABUSE_DIR/owns_dropbear_filter" ] && rm -f /etc/fail2ban/filter.d/dropbear.conf "$ABUSE_DIR/owns_dropbear_filter"
    if [ -f "$ABUSE_DIR/owns_fail2ban" ]; then
        # We installed it just for the guard — stop and disable it.
        systemctl stop fail2ban >/dev/null 2>&1
        systemctl disable fail2ban >/dev/null 2>&1
        rm -f "$ABUSE_DIR/owns_fail2ban"
    else
        # Pre-existing install — reload without our jail, keep their state.
        systemctl restart fail2ban >/dev/null 2>&1
        [ "$(cat "$ABUSE_DIR/f2b_prev" 2>/dev/null)" = disabled ] && systemctl disable fail2ban >/dev/null 2>&1
        rm -f "$ABUSE_DIR/f2b_prev"
    fi
}

# ---------- content filter (dnsmasq, server-side, no client hijack) ----------
# Curated DoH-provider bootstrap hostnames. Browsers (Chrome auto-upgrade,
# Firefox, Brave, Edge) resolve these by NAME before switching DNS onto their
# own encrypted channel over 443 — which would dodge this server-side filter
# (the "open it from a Google result and it works" bypass). Returning 0.0.0.0
# for the bootstrap names makes the DoH connection fail, so the browser falls
# back to classic DNS (port 53), which is forced through the filter.
# SAFE for the VPN's own path: this only poisons the DoH *hostnames*. HTTP
# Custom bug hosts use raw IPs (1.1.1.1) or CDN SNIs, never these names, and
# dnsmasq's own upstreams are configured by IP — so nothing here touches 443
# or the tunnel proxy path.
DOH_BOOTSTRAP="
cloudflare-dns.com mozilla.cloudflare-dns.com chrome.cloudflare-dns.com
security.cloudflare-dns.com family.cloudflare-dns.com one.one.one.one
dns.google dns.google.com dns64.dns.google
dns.quad9.net dns9.quad9.net dns10.quad9.net dns11.quad9.net dns12.quad9.net
doh.opendns.com doh.familyshield.opendns.com
dns.adguard.com dns-family.adguard.com dns-unfiltered.adguard.com
dns.adguard-dns.com family.adguard-dns.com unfiltered.adguard-dns.com
doh.cleanbrowsing.org doh.dns.sb dns.sb doh.mullvad.net dns.mullvad.net
dns.nextdns.io firefox.dns.nextdns.io
doh.libredns.gr ordns.he.net dns.digitale-gesellschaft.ch
doh.applied-privacy.net resolver.dnscrypt.info fdns1.dismail.de
doh.dnswarden.com jp.tiar.app doh.tiar.app
dns.controld.com freedns.controld.com commons.host doh.crypto.sx
dns.aa.net.uk dns.rubyfish.cn dns.twnic.tw dnsforge.de
"
# Ensure the anti-DoH names are always present in the blocklist, even when the
# feed download is skipped/reused or every feed failed. Idempotent.
_ensure_doh_block() {
    local d added=0
    for d in $DOH_BOOTSTRAP; do
        grep -qi "^0\.0\.0\.0 ${d}\$" "$BLOCK_HOSTS" 2>/dev/null && continue
        echo "0.0.0.0 $d" >> "$BLOCK_HOSTS"; added=1
    done
    [ "$added" = 1 ] && echo "  content filter: encrypted-DNS (DoH) bootstrap names pinned to filter"
    return 0
}
# Subdomain-wide blocking: plain hosts entries match EXACT hostnames only,
# so www.1337x.to or any mirror subdomain slipped straight past the filter.
# dnsmasq "address=/dom/0.0.0.0" wildcards block the domain AND every
# subdomain under it. Only the curated sites + DoH names go here (~500
# lines, negligible RAM) — the multi-million-entry feed stays in addn-hosts.
_write_wildcards() {
    mkdir -p /etc/dnsmasq.d 2>/dev/null
    {
        local d
        for d in $DOH_BOOTSTRAP; do echo "address=/$d/0.0.0.0"; done
        [ -s "$ABUSE_DIR/curated.list" ] && awk 'NF{print "address=/" $1 "/0.0.0.0"}' "$ABUSE_DIR/curated.list"
    } > /etc/dnsmasq.d/abuse-guard-wild.conf 2>/dev/null || true
}
# Admin-curated well-known torrent sites & proxy mirrors — always blocked,
# ships inside the script so every install has them even if feeds fail.
_ensure_custom_block() {
    local cl; cl=$(mktemp)
    cat > "$cl" <<'CBEOF'
thepiratebay.org
piratebay-proxy.com
piratebay.live
thepiratebay.vip
thepiratebay.party
piratebay.icu
piratebayproxy.me
tpb.party
tpb.icu
thepiratebay.zone
thepiratebay.agency
piratebay.tw
thepiratebay.tech
thepiratebay.rocks
thepiratebay.wtf
tpbproxy.top
thehiddenbay.com
thehiddenbay.net
thehiddenbay.org
1337x.to
1337x.st
x1337x.eu
x1337x.se
1337x.gd
1337x.is
1337x.vpns.me
1337xto.to
1337x.unblocked.is
1337x.proxybit.pw
1337x-mirror.com
1337x.torrentbay.net
1337x.lol
1337x.wtf
yts.mx
yts.lt
yts.am
yts.rs
yts.do
yts.re
yts.ag
yts.unblocked.is
yts.mx.proxybit.pw
yts-movies.org
yts-subs.com
yts-mirror.com
rarbg.to
rarbgproxy.org
rarbgmirror.com
rarbgaccess.org
rarbg2025.org
rarbg.tw
rarbg.unblocked.is
rarbg.to.proxybit.pw
rarbg-mirror.xyz
rarbg-proxy.net
eztv.io
eztv.re
eztv.wf
eztv.ag
eztv.ch
eztv.unblocked.is
eztv-proxy.net
eztv-mirror.com
limetorrents.info
limetorrents.cc
limetorrents.lol
limetorrents.pro
limetor.com
limetorrents.unblocked.is
limetorrents-mirror.com
limetorrents-proxy.net
torrentgalaxy.to
torrentgalaxy.mx
torrentgalaxy.su
torrentgalaxy.one
tgx.rs
tgx.sb
tgx.unblocked.is
torrentgalaxy.proxybit.pw
nyaa.si
nyaa.net
nyaa.uk
sukebei.nyaa.si
nyaa-mirror.com
nyaa-proxy.net
anidex.info
animetosho.org
torrentz2.eu
torrentz2.is
torrentz2.me
torrentz2.in
torrentz2.unblocked.is
torrentz2-proxy.com
torrentfunk.com
torlock.com
torlock2.com
torlock.cc
glodls.to
glotorrents.com
torrentproject2.com
kickasstorrents.to
kickass.cm
kickass2.to
katcr.to
kickasstorrents.proxybit.pw
katproxy.com
kickass.unblocked.is
kinozal.tv
rustorka.com
rgfootball.net
rutracker.org
nnmclub.to
tntvillage.scambioetico.org
ilcorsaronero.link
ilcorsaronero.pro
torrentdownloads.me
torrentdownloads.unblocked.is
monova.to
monova.unblocked.is
idope.se
idope.unblocked.is
btdig.com
btdig.top
bt4g.org
bt4gprx.com
torrents.io
torrents.me
aiosearch.com
torrentseeker.com
snowfl.com
solidtorrents.net
torrentz2.proxyninja.org
limetorrent.proxyninja.org
yts.proxyninja.org
1337x.proxyninja.org
unblocked.krd
unblocked.lol
unblocked.ws
proxybay.live
piratebay.lat
piratebae.co.uk
tpb.date
tpb.digital
tpb.pictures
bayunblocked.com
unblockedall.com
torrentdownloads.proxy.uno
torlock.unblocked.krd
torlock.proxy.uno
glodls.unblocked.krd
btdb.eu
btdb.to
btdb.in
btmet.com
torrent9.pl
torrent9.blue
torrent9.red
torrent9.uno
cpasbien.pw
cpasbien.to
cpasbien.lol
cpasbien.proxy.uno
oxtorrent.co
oxtorrent.com
oxtorrent.pw
oxtorrent.unblocked.krd
t411.ai
t411.ch
zone-annuaire.com
annuaire-telechargement.com
torrent911.com
torrent911.ws
torrent911.me
vostfree.com
libertyland.net
libertyvf.org
extreme-down.lol
extreme-down.pro
zone-telechargement.com
zone-telechargement.rest
zone-telechargement.xyz
annuaire-telechargement.net
torrent9.so
torrent9.pe
torrent9.gg
gktorrents.info
gktorrents.cc
megatorrents.co
megatorrents.info
mejortorrent1.com
mejortorrent2.com
mejortorrent.net
elitetorrent.com
elitetorrent.biz
elitetorrent.nl
elitetorrent.unblocked.krd
divxtotal.com
divxtotal.unblocked.krd
tumejortorrent.com
tumejortorrent.net
todotorrents.com
todotorrents.net
pctorrent.com
pctorrent.net
torrentlocura.com
torrentrapid.com
donitorrent.com
selektorrent.com
toptorrents.org
torrent9.org
filmestorrent.org
peliculatorrent.com
ver-torrent.com
nuitorrent.com
torrentking.eu
torrentking.io
torrentking.biz
torrentking.unblocked.krd
iktorrent.com
iktorrent.net
zetatorrent.com
pirateiro.com
pirateiro.unblocked.krd
comandotorrents.net
baixatorrent.org
megafilmeshd50.com
megafilmeshd50.net
superfilmes.biz
superfilmeshd.com
filmeseriale.net
filmestorrents.net
filmeseseriesonline.net
thepiratefilmes.com
vertorrents.com
baixarseriesmp4.net
torrentdosfilmeshd.com
torrentfilmes.org
downloadcult.com
sweetstat.com
torrentmafia.net
gratistorrent.com
gratistorrent.net
btshow.info
filestorrents.com
filmestorrent.net
serietorrent.com
tvtorrents.net
catorrent.com
balcaotorrent.com
tugaseries.com
megatorrentshd.com
torrentfita.com
seriesflix.video
cinetorrent.cc
torrentfilmes.biz
megafilmes.online
darktorrent.com
torrentblackhole.com
torrent-paradise.com
torrentlisting.com
torrentz2.xyz
torrentz2.live
torrentz2.search
torrentmeta.com
torrentsearcher.com
torrentpl.com
torrenty.org
limetorrents.zone
limetorrents.info.proxybit.pw
thepiratebay.lol
thepiratebay.quest
thepiratebay.fun
tpb.work
tpb.biz
1337x-mirror.net
yts.movie
yts.direct
rarbg2026.com
rarbgproxy.top
eztv1.com
eztv2.com
eztv3.com
limetorrents.top
torrentgalaxy.buzz
torrentgalaxy.ch
tgx.ws
nyaa.space
nyaa.world
animetosho.net
torlock.unblocked.mx
torlock.proxy.lol
glodls.link
glodls.ch
torrentproject2.ws
kickasstorrents.xyz
kickass.unblockit.dev
rutracker.net
rutracker.nl
nnmclub.ch
tntvillage.cyou
ilcorsaronero.unblocked.krd
torrentdownloads.proxy.lat
monova.unblockit.dev
idope.space
btdig.ch
bt4g.top
bt4g.download
torrents.blue
torrents.red
aiosearch.pro
torrentseeker.live
snowfl.ws
torrentz2.lol
1337x-mirror.top
yts-mirror.xyz
rarbg-unblocked.org
eztv-unblocked.com
limetorrents-unblocked.net
torrentgalaxy-unblocked.com
nyaa-unblocked.net
kickass-unblocked.net
rutracker-unblocked.com
nnmclub-unblocked.net
torrentdownloads-proxy.net
monova-proxy.net
idope-proxy.xyz
btdig-proxy.com
bt4g-proxy.me
torrentz2-proxy.xyz
aiosearch-unblocked.net
snowfl-unblocked.com
x1337x.ws
x1337x.cc
1337x.unblocked.krd
1337x.unblocked.lol
1337x.unblocked.ws
1337x.proxy.uno
1337x-unblocked.com
1337x-mirror.xyz
1337x-proxy.net
1337xproxied.org
1337xmirror.top
1337xmirror.net
1337xmirror.live
1337xmirror.xyz
1337xmirror.one
1337xbay.pw
1337x.torrentbay.st
1337x.unblockit.dev
1337x.unblockit.ws
1337x.unblockit.ch
1337x.unblockit.how
1337x.unblockit.cat
1337x.unblockit.foo
1337x.unblockit.vip
1337x.accessnow.org
1337x.bb
1337x.g3g.xyz
1337x.cm
1337x.vet
1337x.si
1337x.nz
1337x.by
1337x.fm
1337x.mx
1337x.ch
1337x.tw
1337x.se
1337x.am
1337x.bz
1337x.ru
1337x.pl
1337x.one
1337x.me
1337x.cc
1337x.ws
1337x.eu
1337x.tel
1337x.ninjaproxy1.com
1337x.unblockninja.com
1337x.unblocksource.com
1337x.unblockproject.lol
1337x.nocensor.lol
1337x.nocensor.cloud
1337x.nocensor.art
1337x.nocensor.biz
1337x.mrunblock.bond
1337x.mrunblock.life
1337x.mrunblock.guru
1337x.torrends.to
1337x.hashhackers.com
1337x.abcproxy.org
1337x.proxyimp.com
1337x.goblockt.com
1337x.torrentsbay.org
1337x.proxyof.com
1337x.unbl0ck.icu
1337x.unbl4ck.xyz
1337x.u4m.pw
1337x.unblock2.xyz
1337x.workisboring.net
1337x.mirrorbay.top
1337x.privacyfriendly.xyz
1337x.torlock.icu
CBEOF
    local added
    added=$(awk 'FNR==NR { if ($1=="0.0.0.0") have[$2]=1; next }
         !($1 in have) { print "0.0.0.0 " $1 }' "$BLOCK_HOSTS" "$cl" | tee -a "$BLOCK_HOSTS" | wc -l)
    mkdir -p "$ABUSE_DIR" 2>/dev/null
    cp -f "$cl" "$ABUSE_DIR/curated.list" 2>/dev/null
    rm -f "$cl"
    [ "$added" -gt 0 ] && echo "  content filter: $added curated torrent-site domains pinned to filter"
    # Regenerate the subdomain wildcards so mirrors like www.1337x.to are
    # blocked too (picked up on the next dnsmasq restart in setup/refresh).
    _write_wildcards
    return 0
}
fetch_blocklist() {
    # Aggregate the big maintained world databases per category:
    #   torrents/piracy — Blocklist Project torrent feed (tracker/index sites)
    #                     + hagezi anti-piracy (torrent, DDL, warez, streaming
    #                       piracy — the broad "world" piracy database)
    #   carding         — Blocklist Project fraud + scam + phishing feeds
    #   malicious       — hagezi Threat Intelligence Feeds (malware, phishing,
    #                     scam, botnet/C2 — the aggregated "world" malicious DB)
    # Mixed hosts-format and bare-domain feeds (awk below handles both);
    # merged, normalized and deduplicated. If every
    # fetch fails, the previous blocklist is kept untouched.
    local urls="
https://raw.githubusercontent.com/blocklistproject/Lists/master/torrent.txt
https://raw.githubusercontent.com/hagezi/dns-blocklists/main/wildcard/anti.piracy-onlydomains.txt
https://raw.githubusercontent.com/blocklistproject/Lists/master/fraud.txt
https://raw.githubusercontent.com/blocklistproject/Lists/master/scam.txt
https://raw.githubusercontent.com/blocklistproject/Lists/master/phishing.txt
https://raw.githubusercontent.com/hagezi/dns-blocklists/main/wildcard/tif-onlydomains.txt
"
    # Fast path: if a non-trivial list was built in the last 24h, reuse it —
    # re-enabling protection shouldn't re-download megabytes of feeds.
    if [ "$1" != force ] && [ -s "$BLOCK_HOSTS" ] \
       && [ $(( $(date +%s) - $(stat -c %Y "$BLOCK_HOSTS" 2>/dev/null || echo 0) )) -lt 86400 ] \
       && [ "$(grep -c . "$BLOCK_HOSTS")" -ge 10000 ]; then
        echo "  blocklist: reusing today's list ($(grep -c . "$BLOCK_HOSTS") domains) — use 'Refresh blocklist' to force an update"
        _ensure_doh_block
        _ensure_custom_block
        return 0
    fi
    # Download all feeds IN PARALLEL — serial fetches made enabling painfully
    # slow. Each feed gets its own file; order is preserved when merging so
    # the RAM cap still trims the tail feeds first.
    local tmp dldir u i=0 ok=0
    tmp=$(mktemp); dldir=$(mktemp -d)
    for u in $urls; do
        i=$((i+1))
        curl -fsSL --max-time 90 "$u" -o "$dldir/$i" 2>/dev/null &
    done
    wait
    i=0
    for u in $urls; do
        i=$((i+1))
        if [ -s "$dldir/$i" ]; then
            cat "$dldir/$i" >> "$tmp"; echo >> "$tmp"; ok=1
        else
            echo "  blocklist: could not fetch $u (skipped)"
        fi
    done
    rm -rf "$dldir"
    if [ "$ok" = 1 ]; then
        # Accept "0.0.0.0 dom" / "127.0.0.1 dom" / bare-domain lines; drop
        # comments and junk; lowercase; dedupe IN FEED ORDER (feeds above are
        # listed most-important first, so a RAM cap trims the tail feeds, not
        # the torrent/carding core); emit dnsmasq hosts format.
        #
        # RAM cap: dnsmasq keeps addn-hosts in memory (measured ~300+ bytes per
        # entry with hash/cache overhead). Loading more than the VPS can hold
        # gets dnsmasq OOM-killed or mute — and with port-53 enforcement active
        # that would take DNS down for every tunnel user. Cap the list so
        # dnsmasq never eats more than a quarter of MemAvailable.
        local mem_kb cap
        mem_kb=$(awk '/^MemAvailable:/{print $2}' /proc/meminfo 2>/dev/null)
        [ -n "$mem_kb" ] || mem_kb=1048576
        cap=$(( mem_kb * 1024 / 4 / 300 ))
        [ "$cap" -lt 100000 ] && cap=100000
        echo "  blocklist: RAM-aware cap for this VPS: $cap domains"
        awk -v cap="$cap" '{ sub(/\r$/,"") }
             /^[[:space:]]*(#|$)/ { next }
             { d=$1
               if (d=="0.0.0.0" || d=="127.0.0.1") d=$2
               d=tolower(d)
               if (d ~ /^[a-z0-9][a-z0-9._-]*\.[a-z][a-z0-9-]*$/ && d != "localhost" && !(d in seen)) {
                   seen[d]=1
                   print "0.0.0.0 " d
                   if (++n >= cap) exit
               } }' "$tmp" > "${BLOCK_HOSTS}.new"
        if [ -s "${BLOCK_HOSTS}.new" ]; then
            mv -f "${BLOCK_HOSTS}.new" "$BLOCK_HOSTS"
        else
            rm -f "${BLOCK_HOSTS}.new"
        fi
    fi
    # Never block the VPN's own infrastructure: the server's configured
    # domain, the SlowDNS NS domain, and any hosts the admin whitelists in
    # /etc/ssh-panel/allow.list (one domain per line — bug hosts, CDN/proxy
    # hosts used by client configs like HTTP Custom). Aggregated feeds
    # occasionally contain CDN entries; stripping these here guarantees the
    # filter can never cut the tunnel's own path.
    local wl; wl=$(mktemp)
    { cat /etc/ssh-panel/domain.conf /etc/ssh-panel/nsdomain.conf /etc/ssh-panel/allow.list 2>/dev/null; } \
        | tr 'A-Z' 'a-z' | grep -E '^[a-z0-9][a-z0-9._-]*\.[a-z][a-z0-9-]*$' | sort -u > "$wl"
    if [ -s "$wl" ] && [ -s "$BLOCK_HOSTS" ]; then
        awk 'NR==FNR { wl[$1]=1; next } !($2 in wl)' "$wl" "$BLOCK_HOSTS" > "${BLOCK_HOSTS}.wl" \
            && mv -f "${BLOCK_HOSTS}.wl" "$BLOCK_HOSTS"
    fi
    rm -f "$wl"
    [ -s "$BLOCK_HOSTS" ] || : > "$BLOCK_HOSTS"
    # Pin the anti-DoH bootstrap names last so the whitelist strip can't remove
    # them and they survive even if every feed failed.
    _ensure_doh_block
    _ensure_custom_block
    rm -f "$tmp"
}
restore_slowdns() {
    # Restore the original SlowDNS unit and verify it comes back up. Loud on
    # failure — SlowDNS must never be left broken by the content filter.
    [ -f "$SLOWDNS_BAK" ] || return 0
    cp -f "$SLOWDNS_BAK" "$SLOWDNS_UNIT"; rm -f "$SLOWDNS_BAK"
    systemctl daemon-reload; systemctl restart slowdns >/dev/null 2>&1; sleep 1
    systemctl is-active --quiet slowdns 2>/dev/null || echo "  WARNING: SlowDNS did not restart cleanly — check: journalctl -u slowdns"
}
free_53_for_dnsmasq() {
    # If SlowDNS (dnstt) holds 0.0.0.0:53 it blocks dnsmasq on 127.0.0.1:53.
    # Rebind dnstt to the public IP so loopback:53 frees. Never leave it broken.
    systemctl is-active --quiet slowdns 2>/dev/null || return 0
    [ -f "$SLOWDNS_UNIT" ] || return 0
    grep -q -- "-udp :53" "$SLOWDNS_UNIT" || return 0
    # PUBIP must be an address actually configured on this host, or dnstt will
    # fail to bind it. If it isn't local (NAT / floating IP), leave SlowDNS as
    # is and skip the content filter rather than risk breaking the tunnel.
    [ -n "$PUBIP" ] || return 1
    ip -o addr show 2>/dev/null | grep -qw "$PUBIP" || return 1
    cp -f "$SLOWDNS_UNIT" "$SLOWDNS_BAK"
    sed -i "s/-udp :53/-udp ${PUBIP}:53/" "$SLOWDNS_UNIT"
    systemctl daemon-reload
    systemctl restart slowdns; sleep 1
    if ! systemctl is-active --quiet slowdns; then
        restore_slowdns
        return 1
    fi
}
setup_dns() {
    if ! command -v dnsmasq >/dev/null 2>&1; then
        touch "$ABUSE_DIR/owns_dnsmasq"
        DEBIAN_FRONTEND=noninteractive apt-get install -y dnsmasq >/dev/null 2>&1
    else
        systemctl is-enabled --quiet dnsmasq 2>/dev/null && echo enabled > "$ABUSE_DIR/dnsmasq_prev" || echo disabled > "$ABUSE_DIR/dnsmasq_prev"
    fi
    command -v dnsmasq >/dev/null 2>&1 || { echo "  content filter: dnsmasq unavailable — skipped"; rm -f "$ABUSE_DIR/owns_dnsmasq"; return 1; }
    fetch_blocklist
    cat > /etc/dnsmasq.d/abuse-guard.conf <<DNSEOF
listen-address=127.0.0.1
bind-interfaces
no-resolv
server=1.1.1.1
server=8.8.8.8
cache-size=2000
addn-hosts=$BLOCK_HOSTS
# Firefox canary: answering NXDOMAIN here makes Firefox disable its built-in
# DoH and fall back to the filtered system resolver (Mozilla's documented
# network-admin opt-out signal).
server=/use-application-dns.net/
DNSEOF
    if ! free_53_for_dnsmasq; then
        echo "  content filter: could not coexist with SlowDNS on :53 — skipped"
        rm -f /etc/dnsmasq.d/abuse-guard.conf /etc/dnsmasq.d/abuse-guard-wild.conf; return 1
    fi
    systemctl enable dnsmasq >/dev/null 2>&1
    systemctl restart dnsmasq >/dev/null 2>&1; sleep 2
    if ! systemctl is-active --quiet dnsmasq; then
        echo "  content filter: dnsmasq failed to load the blocklist — check 'dnsmasq --test'"
        journalctl -u dnsmasq -n 5 --no-pager 2>/dev/null | sed 's/^/    /'
        teardown_dns; return 1
    fi
    # Prove the filter actually answers 0.0.0.0 for a blocked domain before we
    # trust it — a huge addn-hosts file can load partially or be too big for RAM.
    local canary ans try
    canary=$(grep -m1 -E '^0\.0\.0\.0 ' "$BLOCK_HOSTS" | awk '{print $2}')
    if [ -n "$canary" ]; then
        # dnsmasq parses the whole addn-hosts file before answering — on a big
        # list / slow disk that takes a while. Poll up to ~45s before judging.
        for try in 1 2 3 4 5 6 7 8 9; do
            ans=$( { command -v dig >/dev/null 2>&1 && dig +short +time=3 +tries=1 "$canary" @127.0.0.1; } 2>/dev/null | head -1)
            [ -z "$ans" ] && command -v nslookup >/dev/null 2>&1 && ans=$(nslookup "$canary" 127.0.0.1 2>/dev/null | awk '/^Address: /{print $2; exit}')
            [ "$ans" = "0.0.0.0" ] && break
            echo "  content filter: waiting for dnsmasq to finish loading the list (${try}/9)..."
            sleep 5
        done
        if [ "$ans" != "0.0.0.0" ]; then
            echo "  content filter: dnsmasq is up but not blocking ($canary -> '${ans:-no answer}')."
            echo "  Refusing to enforce a broken filter — content filter disabled (fail-open)."
            echo "  Likely the blocklist is too large for this VPS's RAM. Add RAM and re-enable."
            teardown_dns; return 1
        fi
    fi
    # Point the server's own resolver at the filter (SOCKS remote-DNS and
    # server-side V2Ray lookups get filtered). 1.1.1.1 is a fallback only if
    # dnsmasq is down, so blocking still holds while it runs.
    [ -f "$RESOLV_BAK" ] || cp -f /etc/resolv.conf "$RESOLV_BAK" 2>/dev/null
    printf 'nameserver 127.0.0.1\nnameserver 1.1.1.1\noptions timeout:2 attempts:1\n' > /etc/resolv.conf
    # Close the client-side bypasses (hard-coded 8.8.8.8, DoH/DoT).
    apply_dnsforce
    # Fail-open watchdog: if dnsmasq ever dies or stops answering while the
    # port-53 redirect is enforcing, DNS would go dark for every tunnel user
    # and all protocols would look broken. Check every minute; on failure,
    # lift enforcement (protocols keep working unfiltered) and log loudly.
    cat > /etc/cron.d/abuse-guard-watchdog <<'WDEOF'
* * * * * root /usr/local/bin/abuse-guard watchdog >/dev/null 2>&1
WDEOF
    chmod 644 /etc/cron.d/abuse-guard-watchdog
}
watchdog() {
    # Runs from cron. Only acts when enforcement is installed.
    iptables -t nat -C OUTPUT -j ABUSE_DNS 2>/dev/null || exit 0
    local ans
    if systemctl is-active --quiet dnsmasq 2>/dev/null; then
        ans=$( { command -v dig >/dev/null 2>&1 && dig +short +time=2 +tries=1 google.com @127.0.0.1; } 2>/dev/null | head -1)
        [ -z "$ans" ] && ans=$(nslookup google.com 127.0.0.1 2>/dev/null | awk '/^Address: /{print $2; exit}')
        [ -n "$ans" ] && exit 0
        # One retry before acting — a single timeout under load is not an outage.
        sleep 3
        ans=$( { command -v dig >/dev/null 2>&1 && dig +short +time=2 +tries=1 cloudflare.com @127.0.0.1; } 2>/dev/null | head -1)
        [ -n "$ans" ] && exit 0
        systemctl restart dnsmasq >/dev/null 2>&1; sleep 2
        ans=$( { command -v dig >/dev/null 2>&1 && dig +short +time=2 +tries=1 google.com @127.0.0.1; } 2>/dev/null | head -1)
        [ -n "$ans" ] && exit 0
    else
        systemctl restart dnsmasq >/dev/null 2>&1; sleep 2
        # "active" is not enough — a running-but-mute dnsmasq would still
        # blackhole redirected DNS. Only trust an actual answer.
        if systemctl is-active --quiet dnsmasq 2>/dev/null; then
            ans=$( { command -v dig >/dev/null 2>&1 && dig +short +time=2 +tries=1 google.com @127.0.0.1; } 2>/dev/null | head -1)
            [ -z "$ans" ] && ans=$(nslookup google.com 127.0.0.1 2>/dev/null | awk '/^Address: /{print $2; exit}')
            [ -n "$ans" ] && exit 0
        fi
    fi
    # dnsmasq is dead or mute and won't come back — FAIL OPEN so user
    # protocols keep resolving. resolv.conf already lists 1.1.1.1 fallback.
    remove_dnsforce
    [ -f "$RESOLV_BAK" ] && cp -f "$RESOLV_BAK" /etc/resolv.conf
    logger -t abuse-guard "WATCHDOG: dnsmasq unhealthy — DNS enforcement lifted (fail-open). Re-enable from menu 12 after fixing (RAM?)." 2>/dev/null
    echo "$(date '+%F %T') watchdog: enforcement lifted (dnsmasq unhealthy)" >> "$ABUSE_DIR/watchdog.log"
}
teardown_dns() {
    remove_dnsforce
    rm -f /etc/cron.d/abuse-guard-watchdog
    rm -f /etc/dnsmasq.d/abuse-guard.conf /etc/dnsmasq.d/abuse-guard-wild.conf
    # Restore resolv.conf FIRST so name resolution never depends on our resolver.
    [ -f "$RESOLV_BAK" ] && { cp -f "$RESOLV_BAK" /etc/resolv.conf; rm -f "$RESOLV_BAK"; }
    if [ -f "$ABUSE_DIR/owns_dnsmasq" ]; then
        # We installed dnsmasq only for the guard — stop and disable it.
        systemctl stop dnsmasq >/dev/null 2>&1
        systemctl disable dnsmasq >/dev/null 2>&1
        rm -f "$ABUSE_DIR/owns_dnsmasq"
    else
        # Pre-existing dnsmasq — reload without our conf, keep their state.
        systemctl restart dnsmasq >/dev/null 2>&1
        [ "$(cat "$ABUSE_DIR/dnsmasq_prev" 2>/dev/null)" = disabled ] && systemctl disable dnsmasq >/dev/null 2>&1
        rm -f "$ABUSE_DIR/dnsmasq_prev"
    fi
    restore_slowdns
}

status() {
    echo "Abuse protection: $([ -f "$FLAG" ] && echo ENABLED || echo disabled)"
    iptables -C OUTPUT -j $CHAIN 2>/dev/null \
        && echo "  egress firewall  : active (SMTP + torrent ports blocked, flood-capped)" \
        || echo "  egress firewall  : off"
    systemctl is-active --quiet fail2ban 2>/dev/null \
        && echo "  brute-force      : active (fail2ban: sshd + dropbear)" \
        || echo "  brute-force      : off"
    if systemctl is-active --quiet dnsmasq 2>/dev/null && [ -f /etc/dnsmasq.d/abuse-guard.conf ]; then
        echo "  content filter   : active ($(grep -c . "$BLOCK_HOSTS" 2>/dev/null) domains blocked)"
        iptables -t nat -C OUTPUT -j ABUSE_DNS 2>/dev/null \
            && echo "  dns enforcement  : active (port-53 redirected, DoH/DoT blocked)" \
            || echo "  dns enforcement  : off"
    else
        echo "  content filter   : off"
    fi
}

# Deep diagnostic — proves whether the filter is actually in the traffic path.
selftest() {
    local bad="thepiratebay.org" ans rc=0
    echo "abuse-guard self-test"
    echo "---------------------"
    systemctl is-active --quiet dnsmasq 2>/dev/null \
        && echo "[ok] dnsmasq running" || { echo "[!!] dnsmasq NOT running"; rc=1; }
    echo "[..] blocklist size: $(grep -c . "$BLOCK_HOSTS" 2>/dev/null) domains"
    grep -qw "$bad" "$BLOCK_HOSTS" 2>/dev/null \
        && echo "[ok] $bad is in the blocklist" || echo "[!!] $bad is NOT in the blocklist"
    if command -v dig >/dev/null 2>&1; then
        ans=$(dig +short +time=2 +tries=1 "$bad" @127.0.0.1 2>/dev/null | head -1)
    else
        ans=$(nslookup "$bad" 127.0.0.1 2>/dev/null | awk '/^Address: /{print $2; exit}')
    fi
    if [ "$ans" = "0.0.0.0" ]; then
        echo "[ok] filter answers 0.0.0.0 for $bad (server-side filtering works)"
    else
        echo "[!!] filter returned '${ans:-no answer}' for $bad — dnsmasq isn't blocking"
        echo "     (blocklist likely too big for this VPS's RAM; add RAM or use a smaller set)"
        rc=1
    fi
    iptables -t nat -C OUTPUT -j ABUSE_DNS 2>/dev/null \
        && echo "[ok] port-53 redirect (OUTPUT) installed" || { echo "[!!] port-53 redirect (OUTPUT) missing"; rc=1; }
    iptables -t nat -C PREROUTING -j ABUSE_DNSP 2>/dev/null \
        && echo "[ok] port-53 redirect (forwarded) installed" || echo "[--] forwarded redirect off (fine for proxy-only tunnels)"
    iptables -C OUTPUT -j ABUSE_DOH 2>/dev/null \
        && echo "[ok] DoH/DoT side-door blocks installed" || { echo "[!!] DoH/DoT blocks missing"; rc=1; }
    # Anti-DoH: browsers dodge the filter by resolving over their own encrypted
    # DNS. Verify a DoH bootstrap name is poisoned so they fall back to us.
    local dohans
    if command -v dig >/dev/null 2>&1; then
        dohans=$(dig +short +time=2 +tries=1 dns.google @127.0.0.1 2>/dev/null | head -1)
    else
        dohans=$(nslookup dns.google 127.0.0.1 2>/dev/null | awk '/^Address: /{print $2; exit}')
    fi
    [ "$dohans" = "0.0.0.0" ] \
        && echo "[ok] browser encrypted-DNS (DoH) bootstrap blocked — browsers fall back to the filter" \
        || echo "[!!] DoH bootstrap not blocked (got '${dohans:-no answer}') — browsers may bypass the filter"
    # Wildcards: any subdomain of a curated site must be blocked too.
    local subans
    if command -v dig >/dev/null 2>&1; then
        subans=$(dig +short +time=2 +tries=1 "www.$bad" @127.0.0.1 2>/dev/null | head -1)
    else
        subans=$(nslookup "www.$bad" 127.0.0.1 2>/dev/null | awk '/^Address: /{print $2; exit}')
    fi
    [ "$subans" = "0.0.0.0" ] \
        && echo "[ok] subdomains blocked too (www.$bad -> 0.0.0.0)" \
        || { echo "[!!] subdomain NOT blocked (www.$bad -> '${subans:-no answer}')"; rc=1; }
    echo "---------------------"
    [ "$rc" = 0 ] \
        && echo "PASS — server-side filtering is active and browser encrypted-DNS (DoH) is forced back onto the filter. If a site still opens for a user, they aren't routing web traffic through this server, or they've manually hard-set a custom DoH resolver in the app." \
        || echo "FAIL — see [!!] lines above."
    return $rc
}

case "$1" in
    enable)
        # Minimal images may lack iptables entirely — install it first.
        if ! command -v iptables >/dev/null 2>&1; then
            echo "  installing iptables..."
            DEBIAN_FRONTEND=noninteractive apt-get install -y iptables >/dev/null 2>&1
        fi
        command -v iptables >/dev/null 2>&1 || echo "  WARNING: iptables unavailable — firewall + DNS enforcement will stay off"
        apply_fw; setup_f2b; setup_dns
        touch "$FLAG"; systemctl enable abuse-guard.service >/dev/null 2>&1
        echo ""; status ;;
    disable)
        remove_fw; teardown_f2b; teardown_dns
        rm -f "$FLAG"; systemctl disable abuse-guard.service >/dev/null 2>&1
        echo "Abuse protection disabled — all rules removed, services restored." ;;
    refresh)
        fetch_blocklist force; systemctl restart dnsmasq >/dev/null 2>&1; sleep 2
        if [ -f /etc/dnsmasq.d/abuse-guard.conf ] && ! systemctl is-active --quiet dnsmasq; then
            echo "dnsmasq failed to load the new list — filter disabled (fail-open)."
            teardown_dns
        else
            echo "Blocklist refreshed ($(grep -c . "$BLOCK_HOSTS" 2>/dev/null) domains)."
        fi ;;
    watchdog) watchdog ;;
    apply-fw)   # boot-time re-apply (firewall + DNS enforcement; rest self-persists)
        if [ -f "$FLAG" ]; then
            apply_fw
            [ -f /etc/dnsmasq.d/abuse-guard.conf ] && apply_dnsforce
        fi ;;
    test|selftest|check) selftest ;;
    status|"")  status ;;
    *) echo "usage: abuse-guard {enable|disable|refresh|status|test}";;
esac
AGEOF
chmod +x /usr/local/bin/abuse-guard

cat > /etc/systemd/system/abuse-guard.service <<'EOF'
[Unit]
Description=Re-apply abuse-guard egress firewall on boot
After=network-online.target dnsmasq.service
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/bin/abuse-guard apply-fw

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload >/dev/null 2>&1
success "Abuse protection installed (enable it from the menu)"

# ═══════════════════════════════════════════
# SECTION 6b — BANDWIDTH MONITOR (vnstat)
# ═══════════════════════════════════════════
phase "Bandwidth monitor"
eval "$APT vnstat" </dev/null >/dev/null 2>&1 || true
# Detect the primary network interface and register it with vnstat.
PRIMARY_IFACE=$(ip route 2>/dev/null | awk '/default/{print $5; exit}')
[ -z "$PRIMARY_IFACE" ] && PRIMARY_IFACE=$(ls /sys/class/net 2>/dev/null | grep -v lo | head -n1)
if [ -n "$PRIMARY_IFACE" ]; then
    echo "$PRIMARY_IFACE" > "$CONF_DIR/iface.conf"
    vnstat --add -i "$PRIMARY_IFACE" >/dev/null 2>&1 || true
fi
systemctl enable vnstat >/dev/null 2>&1 || true
systemctl restart vnstat >/dev/null 2>&1 || true
success "Bandwidth monitor active on ${PRIMARY_IFACE:-auto}"

# ═══════════════════════════════════════════
# SECTION 6b2 — SLOWDNS (dnstt) — best-effort, never aborts the installer
#   Tunnels UDP :53 -> OpenSSH 127.0.0.1:22. Whole phase runs with errexit
#   OFF so a slow/failed apt, clone or build can never kill the install.
# ═══════════════════════════════════════════
phase "SlowDNS (dnstt)"
set +e
NS_DOMAIN=$(cat "$CONF_DIR/nsdomain.conf" 2>/dev/null)
if [ -n "$NS_DOMAIN" ]; then
    systemctl stop slowdns >/dev/null 2>&1
    killall dnstt-server >/dev/null 2>&1

    # --- Free UDP 53: systemd-resolved holds it and silently kills dnstt.
    #     Disable its stub listener but keep name resolution working. ---
    if systemctl is-active --quiet systemd-resolved 2>/dev/null; then
        mkdir -p /etc/systemd/resolved.conf.d
        printf '[Resolve]\nDNSStubListener=no\n' > /etc/systemd/resolved.conf.d/slowdns.conf
        rm -f /etc/resolv.conf 2>/dev/null
        printf 'nameserver 1.1.1.1\nnameserver 8.8.8.8\n' > /etc/resolv.conf
        systemctl restart systemd-resolved >/dev/null 2>&1
    fi
    fuser -k 53/udp >/dev/null 2>&1   # anything else squatting on :53

    # --- Build dnstt-server. dnstt needs a modern Go (>=1.21); apt often ships
    #     one too old (Debian 11=1.15, 12=1.19), so we try the toolchain on PATH
    #     first and, if the build fails, install the official go.dev tarball and
    #     retry. A helper does one build attempt with a given `go` binary. ---
    if [ ! -x /usr/local/bin/dnstt-server ]; then
        eval "$APT git golang-go ca-certificates" </dev/null >/dev/null 2>&1
        cd /root; rm -rf dnstt
        git clone https://www.bamsoftware.com/git/dnstt.git >/dev/null 2>&1

        _try_build() {   # $1 = path to a go binary
            [ -x "$1" ] || command -v "$1" >/dev/null 2>&1 || return 1
            [ -d /root/dnstt/dnstt-server ] || return 1
            ( cd /root/dnstt/dnstt-server \
              && export HOME=/root GOCACHE=/tmp/gocache GOPATH=/tmp/gopath GOFLAGS=-mod=mod \
              && "$1" build -o dnstt-server . >/dev/null 2>&1 \
              && install -m 0755 dnstt-server /usr/local/bin/dnstt-server )
        }

        # 1) try whatever `go` apt gave us (fast path on modern distros)
        APT_GO="$(command -v go 2>/dev/null)"
        [ -n "$APT_GO" ] && _try_build "$APT_GO"

        # 2) if that didn't produce a binary, fetch modern Go and retry
        if [ ! -x /usr/local/bin/dnstt-server ]; then
            ARCH=$(dpkg --print-architecture 2>/dev/null || uname -m)
            case "$ARCH" in
                amd64|x86_64)  GOA=amd64;;
                arm64|aarch64) GOA=arm64;;
                armhf|armv7l)  GOA=armv6l;;
                *)             GOA=amd64;;
            esac
            if curl -fsSL --connect-timeout 25 -o /tmp/go.tgz \
                 "https://go.dev/dl/go1.22.5.linux-${GOA}.tar.gz" >/dev/null 2>&1; then
                rm -rf /usr/local/go; tar -C /usr/local -xzf /tmp/go.tgz >/dev/null 2>&1
                rm -f /tmp/go.tgz
                _try_build /usr/local/go/bin/go
            fi
        fi
    fi

    if [ -x /usr/local/bin/dnstt-server ]; then
        mkdir -p /etc/slowdns
        # generate the server keypair once; reuse on re-runs
        if [ ! -s /etc/slowdns/server.key ] || [ ! -s /etc/slowdns/server.pub ]; then
            ( cd /etc/slowdns && /usr/local/bin/dnstt-server -gen-key \
                -privkey-file server.key -pubkey-file server.pub >/dev/null 2>&1 )
        fi

        cat > /etc/systemd/system/slowdns.service <<EOF
[Unit]
Description=SlowDNS (dnstt) Tunnel Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/etc/slowdns
ExecStart=/usr/local/bin/dnstt-server -udp :53 -privkey-file /etc/slowdns/server.key ${NS_DOMAIN} 127.0.0.1:22
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
        systemctl daemon-reload >/dev/null 2>&1
        systemctl enable --now slowdns.service >/dev/null 2>&1
        if systemctl is-active --quiet slowdns 2>/dev/null; then
            success "SlowDNS active — UDP 53 -> OpenSSH 22 (NS: $NS_DOMAIN)"
        else
            warn "SlowDNS installed but not active — check: journalctl -u slowdns"
        fi
    else
        warn "SlowDNS skipped — could not build dnstt-server (install continues)"
    fi
else
    info "SlowDNS skipped — no NS domain provided"
fi
set -e   # re-enable errexit for the rest of the installer

# ═══════════════════════════════════════════
# SECTION 6c — XRAY HELPER SCRIPTS (config generator + quota/expiry checker)
#   These are installed but Xray itself stays OFF until activated in the menu.
# ═══════════════════════════════════════════
phase "Xray / V2Ray"

# --- config generator: rebuilds Xray config from the accounts file --------
cat > /usr/local/bin/xray-gen <<'XGEOF'
#!/bin/bash
CONF_DIR=/etc/ssh-panel
XACC=/etc/xray/accounts.txt
XCONF=/usr/local/etc/xray/config.json
XAPI=10085
# --- Shared canonical port scheme -----------------------------------------
# VMess, VLESS and Trojan all use the SAME standard ports. Two inbounds can
# never bind one TCP port at once, so the canonical ports are handed to the
# protocols that actually HAVE ACCOUNTS, in priority order (VMess, then VLESS,
# then Trojan). So if you only use Trojan, Trojan gets the standard VMess ports.
# Ports are Cloudflare-supported: TLS on 2083/2087/2096, plain HTTP on
# 8080/2082/2095 (443 itself stays reserved for the SSL payload / 443 handover).
C_WS_TLS=2083; C_WS_NONE=8080; C_HTTP_NONE=2082; C_HTTP_TLS=2087; C_SPLIT_TLS=2096; C_SPLIT_NONE=2095

# Ports held by processes OTHER than xray (xray's own current listeners are
# ignored, so regenerating the config never makes the ports drift on re-runs).
_busy=" $(ss -ltnpH 2>/dev/null | grep -v '"xray"' | awk '{print $4}' | sed 's/.*://' | sort -un | tr '\n' ' ') "
_used=" $XAPI 22 80 109 143 443 447 53 "   # reserved for SSH/SSL/SlowDNS/API
_alloc() {   # $1 = preferred port, $2 = variable name to set with the free port.
    # NOTE: sets a variable rather than echoing — command substitution runs in a
    # subshell, which would discard the running $_used reservation between calls.
    local p="$1"
    while :; do
        case "$_used$_busy" in *" $p "*) p=$((p+1)); continue;; esac
        break
    done
    _used="$_used$p "; eval "$2=$p"
}

mkdir -p /etc/xray /usr/local/etc/xray; touch "$XACC"

# Build per-protocol client lists FIRST so we know which protocols are in use.
# Record format: proto|remark|secret|expiry|quota   (legacy 4-field = vmess)
VMESS=""; VLESS=""; TROJAN=""
_add() { case "$1" in
    vmess)  VMESS="$VMESS${VMESS:+,}$2";;
    vless)  VLESS="$VLESS${VLESS:+,}$2";;
    trojan) TROJAN="$TROJAN${TROJAN:+,}$2";; esac; }
while IFS='|' read -r f1 f2 f3 f4 f5; do
    [ -z "$f1" ] && continue
    case "$f1" in
        vmess|vless|trojan) proto=$f1; rk=$f2; sec=$f3;;
        *) proto=vmess; rk=$f1; sec=$f2;;
    esac
    [ -z "$sec" ] && continue
    case "$proto" in
        vmess)  _add vmess  "{\"id\":\"$sec\",\"alterId\":0,\"email\":\"$rk\"}";;
        vless)  _add vless  "{\"id\":\"$sec\",\"email\":\"$rk\"}";;
        trojan) _add trojan "{\"password\":\"$sec\",\"email\":\"$rk\"}";;
    esac
done < "$XACC"

# A protocol is ACTIVE (gets real ports + inbounds) if it has accounts OR it was
# handed port 443 — otherwise the 443 listener would never bind and the handover
# fails. Read the 443 flag now so it feeds both port assignment and emission.
P443=$(cat /etc/xray/port443 2>/dev/null)
ACTIVE=""
[ -n "$VMESS" ]  && ACTIVE="$ACTIVE vmess"
[ -n "$VLESS" ]  && ACTIVE="$ACTIVE vless"
[ -n "$TROJAN" ] && ACTIVE="$ACTIVE trojan"
case "$P443" in vmess|vless|trojan)
    case " $ACTIVE " in *" $P443 "*) :;; *) ACTIVE="$ACTIVE $P443";; esac ;;
esac
_active() { case " $ACTIVE " in *" $1 "*) return 0;; esac; return 1; }

# Default every protocol to the canonical numbers (used only for display when a
# protocol has no accounts yet), then hand the real free ports to the ACTIVE
# protocols, in priority order (so ports never collide across protocols).
for _pfx in VM VL TR; do
    eval "${_pfx}_WS_TLS=$C_WS_TLS;     ${_pfx}_WS_NONE=$C_WS_NONE"
    eval "${_pfx}_HTTP_NONE=$C_HTTP_NONE; ${_pfx}_HTTP_TLS=$C_HTTP_TLS"
    eval "${_pfx}_SPLIT_TLS=$C_SPLIT_TLS; ${_pfx}_SPLIT_NONE=$C_SPLIT_NONE"
done
_assign() {   # $1 = prefix (VM/VL/TR): give this protocol the canonical scheme
    _alloc $C_WS_TLS    ${1}_WS_TLS;    _alloc $C_WS_NONE   ${1}_WS_NONE
    _alloc $C_HTTP_NONE ${1}_HTTP_NONE; _alloc $C_HTTP_TLS  ${1}_HTTP_TLS
    _alloc $C_SPLIT_TLS ${1}_SPLIT_TLS; _alloc $C_SPLIT_NONE ${1}_SPLIT_NONE
}
_active vmess  && _assign VM
_active vless  && _assign VL
_active trojan && _assign TR

# Persist resolved ports so the menu's link/QR display matches the live config
# (written BEFORE the 443 handover so the base numbers are stable).
cat > /etc/xray/ports.conf <<PORTS
VM_WS_TLS=$VM_WS_TLS; VM_WS_NONE=$VM_WS_NONE; VM_HTTP_NONE=$VM_HTTP_NONE; VM_HTTP_TLS=$VM_HTTP_TLS; VM_SPLIT_TLS=$VM_SPLIT_TLS; VM_SPLIT_NONE=$VM_SPLIT_NONE
VL_WS_TLS=$VL_WS_TLS; VL_WS_NONE=$VL_WS_NONE; VL_HTTP_NONE=$VL_HTTP_NONE; VL_HTTP_TLS=$VL_HTTP_TLS; VL_SPLIT_TLS=$VL_SPLIT_TLS; VL_SPLIT_NONE=$VL_SPLIT_NONE
TR_WS_TLS=$TR_WS_TLS; TR_WS_NONE=$TR_WS_NONE; TR_HTTP_NONE=$TR_HTTP_NONE; TR_HTTP_TLS=$TR_HTTP_TLS; TR_SPLIT_TLS=$TR_SPLIT_TLS; TR_SPLIT_NONE=$TR_SPLIT_NONE
PORTS

# If the operator handed port 443 to V2Ray (SSL payload disabled), move that
# protocol's WS-TLS listener onto 443 (P443 was read above).
case "$P443" in
    vmess)  VM_WS_TLS=443;;
    vless)  VL_WS_TLS=443;;
    trojan) TR_WS_TLS=443;;
esac

DOMAIN=$(cat "$CONF_DIR/domain.conf" 2>/dev/null)
HOST="${DOMAIN:-$(cat "$CONF_DIR/ip.conf" 2>/dev/null)}"
if [ -n "$DOMAIN" ] && [ -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]; then
    CERT="/etc/letsencrypt/live/$DOMAIN/fullchain.pem"; KEY="/etc/letsencrypt/live/$DOMAIN/privkey.pem"
else
    [ -f /etc/xray/xray.crt ] || openssl req -new -newkey rsa:2048 -days 3650 -nodes -x509 \
        -subj "/CN=${HOST:-xray}" -out /etc/xray/xray.crt -keyout /etc/xray/xray.key >/dev/null 2>&1
    CERT=/etc/xray/xray.crt; KEY=/etc/xray/xray.key
fi

# Xray's stock systemd unit runs as a non-root user (usually 'nobody'), which
# cannot read root-owned 0600 TLS keys — TLS inbounds then fail with
# "failed to parse key: permission denied". Make the key readable by the
# exact user the unit runs as. Letsencrypt keys live behind root-only
# symlinks, so copy them into /etc/xray first (re-copied on every rebuild,
# which also picks up renewals).
XUSER=$(systemctl show -p User xray 2>/dev/null | cut -d= -f2)
if [ -n "$XUSER" ] && [ "$XUSER" != root ]; then
    case "$KEY" in
        /etc/letsencrypt/*)
            cp -L "$CERT" /etc/xray/xray-le.crt 2>/dev/null
            cp -L "$KEY"  /etc/xray/xray-le.key 2>/dev/null
            CERT=/etc/xray/xray-le.crt; KEY=/etc/xray/xray-le.key;;
    esac
    chown "$XUSER" "$CERT" "$KEY" 2>/dev/null
    chmod 644 "$CERT" 2>/dev/null; chmod 600 "$KEY" 2>/dev/null
fi

# Emit one inbound. args: port proto clients net security path
ib() {
    local port="$1" proto="$2" cl="$3" net="$4" sec="$5" path="$6"
    local settings tlsblk="" netjson streamextra=""
    case "$proto" in
        vmess)  settings="{\"clients\":[${cl}]}";;
        vless)  settings="{\"clients\":[${cl}],\"decryption\":\"none\"}";;
        trojan) settings="{\"clients\":[${cl}]}";;
    esac
    [ "$sec" = "tls" ] && tlsblk="\"tlsSettings\":{\"certificates\":[{\"certificateFile\":\"$CERT\",\"keyFile\":\"$KEY\"}]}"
    case "$net" in
        ws)        netjson=ws;        streamextra="\"wsSettings\":{\"path\":\"$path\",\"headers\":{\"Host\":\"$HOST\"}}";;
        tcp)       netjson=tcp;;
        tcphttp)   netjson=tcp;       streamextra="\"tcpSettings\":{\"header\":{\"type\":\"http\",\"request\":{\"path\":[\"$path\"],\"headers\":{\"Host\":[\"$HOST\"]}}}}";;
        splithttp) netjson=splithttp; streamextra="\"splithttpSettings\":{\"path\":\"$path\",\"host\":\"$HOST\"}";;
    esac
    local parts="\"network\":\"$netjson\",\"security\":\"$sec\""
    [ -n "$tlsblk" ] && parts="$parts,$tlsblk"
    [ -n "$streamextra" ] && parts="$parts,$streamextra"
    echo "{\"port\":$port,\"protocol\":\"$proto\",\"settings\":$settings,\"streamSettings\":{$parts}}"
}

INB="{\"listen\":\"127.0.0.1\",\"port\":$XAPI,\"protocol\":\"dokodemo-door\",\"settings\":{\"address\":\"127.0.0.1\"},\"tag\":\"api\"}"
_ib() { INB="$INB,$(ib "$@")"; }
# Only ACTIVE protocols get inbounds (has accounts, or was handed port 443), so
# unused protocols never occupy the shared canonical ports. A protocol handed
# 443 with no accounts yet still binds (empty client list) so the handover
# succeeds; create an account afterwards to actually use it. All three use the
# SAME 6-variant lineup.
if _active vmess; then
    _ib $VM_WS_TLS     vmess "$VMESS" ws        tls  /vmess
    _ib $VM_WS_NONE    vmess "$VMESS" ws        none /vmess
    _ib $VM_HTTP_NONE  vmess "$VMESS" tcphttp   none /
    _ib $VM_HTTP_TLS   vmess "$VMESS" tcphttp   tls  /
    _ib $VM_SPLIT_TLS  vmess "$VMESS" splithttp tls  /split
    _ib $VM_SPLIT_NONE vmess "$VMESS" splithttp none /split
fi
if _active vless; then
    _ib $VL_WS_TLS     vless "$VLESS" ws        tls  /vless
    _ib $VL_WS_NONE    vless "$VLESS" ws        none /vless
    _ib $VL_HTTP_NONE  vless "$VLESS" tcphttp   none /
    _ib $VL_HTTP_TLS   vless "$VLESS" tcphttp   tls  /
    _ib $VL_SPLIT_TLS  vless "$VLESS" splithttp tls  /split
    _ib $VL_SPLIT_NONE vless "$VLESS" splithttp none /split
fi
if _active trojan; then
    _ib $TR_WS_TLS     trojan "$TROJAN" ws        tls  /trojan
    _ib $TR_WS_NONE    trojan "$TROJAN" ws        none /trojan
    _ib $TR_HTTP_NONE  trojan "$TROJAN" tcphttp   none /
    _ib $TR_HTTP_TLS   trojan "$TROJAN" tcphttp   tls  /
    _ib $TR_SPLIT_TLS  trojan "$TROJAN" splithttp tls  /split
    _ib $TR_SPLIT_NONE trojan "$TROJAN" splithttp none /split
fi

cat > "$XCONF" <<JSON
{
  "log": {"loglevel": "warning"},
  "stats": {},
  "api": {"tag": "api", "services": ["HandlerService", "StatsService"]},
  "policy": {"levels": {"0": {"statsUserUplink": true, "statsUserDownlink": true}}, "system": {"statsInboundUplink": true, "statsInboundDownlink": true}},
  "inbounds": [${INB}],
  "outbounds": [{"protocol": "freedom", "tag": "direct"}],
  "routing": {"rules": [{"type": "field", "inboundTag": ["api"], "outboundTag": "api"}]}
}
JSON
if systemctl is-enabled xray >/dev/null 2>&1; then systemctl restart xray >/dev/null 2>&1; fi
XGEOF
chmod +x /usr/local/bin/xray-gen

# --- limit checker: drops expired / over-quota accounts, then regenerates --
cat > /usr/local/bin/xray-check <<'XCEOF'
#!/bin/bash
XACC=/etc/xray/accounts.txt
XAPI=127.0.0.1:10085
[ -f "$XACC" ] || exit 0
now=$(date +%s)
tmp=$(mktemp); changed=0
while IFS= read -r line; do
    [ -z "$line" ] && continue
    IFS='|' read -r f1 f2 f3 f4 f5 <<< "$line"
    case "$f1" in
        vmess|vless|trojan) rk=$f2; exp=$f4; quota=$f5;;
        *) rk=$f1; exp=$f3; quota=$f4;;
    esac
    [ -z "$rk" ] && continue
    drop=0
    # expiry check
    if [ -n "$exp" ] && [ "$exp" != "never" ]; then
        e=$(date -d "$exp" +%s 2>/dev/null || echo 0)
        [ "$e" -gt 0 ] && [ "$now" -ge "$e" ] && drop=1
    fi
    # quota check
    if [ "$drop" -eq 0 ] && [ -n "$quota" ] && [ "$quota" -gt 0 ] 2>/dev/null; then
        used=$(xray api statsquery --server=$XAPI -pattern "user>>>${rk}>>>traffic" 2>/dev/null | grep -o '"value": *"[0-9]\+"' | grep -o '[0-9]\+' | awk '{s+=$1} END{print s+0}'); used=${used:-0}
        [ "$used" -ge "$quota" ] && drop=1
    fi
    if [ "$drop" -eq 1 ]; then changed=1; else echo "$line" >> "$tmp"; fi
done < "$XACC"
if [ "$changed" -eq 1 ]; then mv "$tmp" "$XACC"; /usr/local/bin/xray-gen; else rm -f "$tmp"; fi
XCEOF
chmod +x /usr/local/bin/xray-check

# --- cron: run the checker every 10 minutes ------------------------------
cat > /etc/cron.d/xray-check <<'EOF'
*/10 * * * * root /usr/local/bin/xray-check >/dev/null 2>&1
EOF
chmod 644 /etc/cron.d/xray-check
command -v cron >/dev/null 2>&1 || eval "$APT cron" </dev/null >/dev/null 2>&1 || true
systemctl enable cron >/dev/null 2>&1 || true
systemctl restart cron >/dev/null 2>&1 || true
success "Xray helper scripts installed (quota + expiry enforcement ready)"

# ═══════════════════════════════════════════
# SECTION 7 — INSTALL THE 'menu' COMMAND
# ═══════════════════════════════════════════
phase "Management panel"

cat > /usr/local/bin/menu <<'MENUEOF'
#!/bin/bash
# SSH VPN MANAGEMENT PANEL — type "menu" to open.

# ── palette ─────────────────────────────────────────────
NC='\033[0m'; BOLD='\033[1m'; DIM='\033[2m'
R='\033[1;31m'; G='\033[1;32m'; Y='\033[1;33m'
B='\033[1;34m'; P='\033[1;35m'; C='\033[1;36m'; W='\033[1;37m'
GR='\033[0;90m'
# 256-colour accents
TEAL='\033[38;5;44m'; ORANGE='\033[38;5;208m'; PINK='\033[38;5;213m'
LIME='\033[38;5;118m'; SKY='\033[38;5;39m'; VIOLET='\033[38;5;99m'

# Keep legacy names working
BGreen="$G"; BYellow="$Y"; BCyan="$C"; BRed="$R"; BPurple="$P"

CONF_DIR=/etc/ssh-panel
DOMAIN=$(cat "$CONF_DIR/domain.conf" 2>/dev/null)
SERVER_IP=$(cat "$CONF_DIR/ip.conf" 2>/dev/null)
HOST_DISPLAY="${DOMAIN:-$SERVER_IP}"

[[ $EUID -ne 0 ]] && { echo -e "${R}Run as root: sudo menu${NC}"; exit 1; }

# Use a UTF-8 locale so box/symbol characters are measured correctly.
if locale -a 2>/dev/null | grep -qiE '^C\.utf-?8$'; then export LC_ALL=C.UTF-8; fi

# Frame width adapts to the terminal so boxes never wrap on phones.
COLS=$(tput cols 2>/dev/null || stty size 2>/dev/null | awk '{print $2}')
[ -z "$COLS" ] && COLS=64
WIDTH=$(( COLS - 4 ))          # inner width of the frames
(( WIDTH > 60 )) && WIDTH=60
(( WIDTH < 34 )) && WIDTH=34

# ── frame drawing helpers ───────────────────────────────
# strip ANSI codes to measure real text length
_vislen() { local s; s=$(echo -ne "$1" | sed 's/\x1b\[[0-9;]*m//g'); echo -n "${#s}"; }

line_top()  { echo -e "${1}╭$(printf '─%.0s' $(seq 1 $WIDTH))╮${NC}"; }
line_mid()  { echo -e "${1}├$(printf '─%.0s' $(seq 1 $WIDTH))┤${NC}"; }
line_bot()  { echo -e "${1}╰$(printf '─%.0s' $(seq 1 $WIDTH))╯${NC}"; }
line_fill() { echo -e "${1}│$(printf ' %.0s' $(seq 1 $WIDTH))│${NC}"; }

# row "border-color" "text-with-ansi"
row() {
    local col="$1" text="$2" len pad
    len=$(_vislen "$text")
    pad=$(( WIDTH - 2 - len ))
    (( pad < 0 )) && pad=0
    echo -e "${col}│${NC} ${text}$(printf ' %.0s' $(seq 1 $pad)) ${col}│${NC}"
}
# centered row
crow() {
    local col="$1" text="$2" len left right
    len=$(_vislen "$text")
    left=$(( (WIDTH - len) / 2 )); right=$(( WIDTH - len - left ))
    (( left < 0 )) && left=0; (( right < 0 )) && right=0
    echo -e "${col}│${NC}$(printf ' %.0s' $(seq 1 $left))${text}$(printf ' %.0s' $(seq 1 $right))${col}│${NC}"
}

pause() { echo ""; read -rp "$(echo -e "  ${GR}↵  press ENTER to go back${NC} ")" _; }

# count tunnel users / online
count_users()  { awk -F: '$3>=1000 && ($7=="/bin/false"||$7=="/usr/sbin/nologin"){c++} END{print c+0}' /etc/passwd; }
count_online() {
    local n=0 u uid sh
    while IFS=: read -r u _ uid _ _ _ sh; do
        if [ "$uid" -ge 1000 ] 2>/dev/null && { [ "$sh" = "/bin/false" ] || [ "$sh" = "/usr/sbin/nologin" ]; }; then
            pgrep -u "$u" >/dev/null 2>&1 && n=$((n+1))
        fi
    done < /etc/passwd
    echo "$n"
}

# ── bandwidth helpers ───────────────────────────────────
IFACE=$(cat "$CONF_DIR/iface.conf" 2>/dev/null)
[ -z "$IFACE" ] && IFACE=$(ip route 2>/dev/null | awk '/default/{print $5; exit}')
[ -z "$IFACE" ] && IFACE=$(ls /sys/class/net 2>/dev/null | grep -v lo | head -n1)

hb() { numfmt --to=iec --suffix=B --format="%.2f" "${1:-0}" 2>/dev/null || echo "${1:-0} B"; }

# Prints "rx tx total" in bytes for a scope: all | day | month.
# vnstat JSON is authoritative (always reports bytes and is stable across
# vnstat versions, unlike --oneline field offsets). Falls back to the kernel
# /sys counters (since boot) so all-time never shows a bogus zero.
bw_scope() {
    local scope="$1" out=""
    if command -v vnstat >/dev/null 2>&1 && command -v python3 >/dev/null 2>&1; then
        out=$(vnstat -i "$IFACE" --json 2>/dev/null | python3 -c '
import sys, json
scope = sys.argv[1]
try:
    t = json.load(sys.stdin)["interfaces"][0]["traffic"]
    if scope == "all":
        e = t.get("total", {})
    elif scope == "day":
        e = (t.get("day") or [{}])[-1]
    else:
        e = (t.get("month") or [{}])[-1]
    rx = int(e.get("rx", 0)); tx = int(e.get("tx", 0))
    print(rx, tx, rx + tx)
except Exception:
    pass
' "$scope" 2>/dev/null)
    fi
    if ! [[ "$out" =~ ^[0-9]+\ [0-9]+\ [0-9]+$ ]]; then
        if [ "$scope" = "all" ]; then
            local rx tx
            rx=$(cat "/sys/class/net/$IFACE/statistics/rx_bytes" 2>/dev/null || echo 0)
            tx=$(cat "/sys/class/net/$IFACE/statistics/tx_bytes" 2>/dev/null || echo 0)
            out="$rx $tx $((rx + tx))"
        else
            out="0 0 0"
        fi
    fi
    echo "$out"
}

# Prints "rx tx total" in bytes for all-time usage.
bw_alltime() { bw_scope all; }

# Prints "rx tx total" in bytes for a period label: d (today) or m (month).
bw_period() { case "$1" in d) bw_scope day;; *) bw_scope month;; esac; }

banner() {
    clear
    echo ""
    echo -e "   ${TEAL} ██████╗ ██████╗ ${SKY}██╗   ██╗${PINK}██████╗ ███╗   ██╗${NC}"
    echo -e "   ${TEAL}██╔════╝██╔════╝ ${SKY}██║   ██║${PINK}██╔══██╗████╗  ██║${NC}"
    echo -e "   ${TEAL}╚█████╗ ╚█████╗  ${SKY}███████║${PINK}██████╔╝██╔██╗ ██║${NC}"
    echo -e "   ${TEAL} ╚═══██╗ ╚═══██╗ ${SKY}██╔══██║${PINK}██╔═══╝ ██║╚██╗██║${NC}"
    echo -e "   ${TEAL}██████╔╝██████╔╝ ${SKY}██║  ██║${PINK}██║     ██║ ╚████║${NC}"
    echo -e "   ${TEAL}╚═════╝ ╚═════╝  ${SKY}╚═╝  ╚═╝${PINK}╚═╝     ╚═╝  ╚═══╝${NC}"
    echo -e "        ${GR}ws · ssl · dropbear · openssh manager${NC}"
    echo ""
}

status_bar() {
    local col="$P" s dot
    line_top "$col"
    crow "$col" "${W}${BOLD}SSH VPN CONTROL PANEL${NC}"
    line_mid "$col"
    row "$col" "${GR}HOST${NC}    ${Y}${HOST_DISPLAY}${NC}"
    row "$col" "${GR}USERS${NC}   ${C}$(count_users)${NC} total   ${LIME}$(count_online)${NC} online"
    read -r _rx _tx _tot <<<"$(bw_alltime)"
    row "$col" "${GR}DATA${NC}    ${W}${BOLD}$(hb "$_tot")${NC} ${GR}used${NC}"
    local svcline="${GR}SVC${NC}    "
    for s in ssh dropbear ws-proxy stunnel4; do
        if systemctl is-active --quiet "$s" 2>/dev/null; then dot="${G}●${NC}"; else dot="${R}○${NC}"; fi
        svcline+="${dot} ${s}   "
    done
    row "$col" "$svcline"
    line_bot "$col"
}

show_ports() {
    local col="$SKY"
    line_top "$col"
    crow "$col" "${W}${BOLD}CONNECTION PORTS${NC}"
    line_mid "$col"
    row "$col" "${LIME}▸${NC} WebSocket (payload)  ${GR}→${NC} ${W}${HOST_DISPLAY}:80${NC}"
    row "$col" "${LIME}▸${NC} SSL + payload (TLS)  ${GR}→${NC} ${W}${HOST_DISPLAY}:443${NC}"
    row "$col" "${LIME}▸${NC} SSL direct SSH       ${GR}→${NC} ${W}${HOST_DISPLAY}:447${NC}"
    row "$col" "${LIME}▸${NC} OpenSSH              ${GR}→${NC} ${W}${HOST_DISPLAY}:22${NC}"
    row "$col" "${LIME}▸${NC} Dropbear             ${GR}→${NC} ${W}${HOST_DISPLAY}:109 / 143${NC}"
    line_bot "$col"
}

section() {  # section "TITLE" color
    local col="${2:-$TEAL}"
    banner
    line_top "$col"; crow "$col" "${W}${BOLD}$1${NC}"; line_bot "$col"
    echo ""
}

ok()   { echo -e "  ${G}✔${NC} $*"; }
err()  { echo -e "  ${R}✘${NC} $*"; }
note() { echo -e "  ${Y}➜${NC} $*"; }
warn() { echo -e "  ${Y}⚠${NC} $*"; }

create_user() {
    section "CREATE SSH USER" "$LIME"
    read -rp "$(echo -e "  ${C}Username${NC}   : ")" USERNAME
    [ -z "$USERNAME" ] && { err "Username cannot be empty."; pause; return; }
    if id "$USERNAME" >/dev/null 2>&1; then
        err "User '${W}$USERNAME${NC}' already exists."; pause; return
    fi
    read -rp "$(echo -e "  ${C}Password${NC}   : ")" PASSWORD
    [ -z "$PASSWORD" ] && { err "Password cannot be empty."; pause; return; }
    read -rp "$(echo -e "  ${C}Days valid${NC} : ")" DAYS
    [[ ! "$DAYS" =~ ^[0-9]+$ ]] && DAYS=30
    EXP_DATE=$(date -d "+$DAYS days" +"%Y-%m-%d")

    useradd -e "$EXP_DATE" -M -s /bin/false "$USERNAME"
    echo -e "${PASSWORD}\n${PASSWORD}" | passwd "$USERNAME" >/dev/null 2>&1

    banner
    local col="$G"
    line_top "$col"; crow "$col" "${W}${BOLD}✔ ACCOUNT CREATED${NC}"; line_mid "$col"
    row "$col" "${GR}Host${NC}      ${Y}${HOST_DISPLAY}${NC}"
    row "$col" "${GR}Username${NC}  ${W}${USERNAME}${NC}"
    row "$col" "${GR}Password${NC}  ${W}${PASSWORD}${NC}"
    row "$col" "${GR}Expires${NC}   ${W}${EXP_DATE}${NC}  ${GR}(${DAYS} days)${NC}"
    line_bot "$col"
    show_ports
    local col2="$VIOLET"
    line_top "$col2"; crow "$col2" "${W}${BOLD}CLIENT PAYLOAD / SNI${NC}"; line_mid "$col2"
    row "$col2" "${GR}WS payload${NC}"
    row "$col2" "${DIM}GET / HTTP/1.1[crlf]Host: ${HOST_DISPLAY}[crlf]${NC}"
    row "$col2" "${DIM}Upgrade: websocket[crlf][crlf]${NC}"
    row "$col2" "${GR}SSL / SNI host${NC}  ${W}${HOST_DISPLAY}${NC}"
    line_bot "$col2"

    # --- SlowDNS details (only when installed) ---
    local ns pub
    ns=$(cat "$CONF_DIR/nsdomain.conf" 2>/dev/null)
    pub=$(cat /etc/slowdns/server.pub 2>/dev/null)
    if [ -n "$ns" ] && [ -x /usr/local/bin/dnstt-server ] && [ -n "$pub" ]; then
        local col3="$PINK"
        line_top "$col3"; crow "$col3" "${W}${BOLD}SLOWDNS (DNSTT)${NC}"; line_mid "$col3"
        row "$col3" "${GR}NS domain${NC}  ${W}${ns}${NC}"
        row "$col3" "${GR}Server IP${NC}  ${W}${SERVER_IP}${NC}"
        row "$col3" "${GR}Public key${NC}"
        row "$col3" "${W}${pub}${NC}"
        line_bot "$col3"
        echo -e "  ${GR}Use the same username/password above with a SlowDNS app.${NC}"
    fi
    pause
}

delete_user() {
    section "DELETE SSH USER" "$R"
    read -rp "$(echo -e "  ${C}Username to delete${NC} : ")" USERNAME
    if ! id "$USERNAME" >/dev/null 2>&1; then
        err "User '${W}$USERNAME${NC}' does not exist."; pause; return
    fi
    pkill -u "$USERNAME" 2>/dev/null
    userdel -f "$USERNAME" >/dev/null 2>&1
    ok "User '${W}$USERNAME${NC}' deleted."
    pause
}

list_users() {
    section "SSH USER LIST" "$SKY"
    local col="$SKY"
    line_top "$col"
    row "$col" "$(printf '%-18s %-12s %-8s' 'USERNAME' 'EXPIRES' 'STATUS')"
    line_mid "$col"
    local any=0
    while IFS=: read -r user _ uid _ _ _ shell; do
        if [ "$uid" -ge 1000 ] && { [ "$shell" = "/bin/false" ] || [ "$shell" = "/usr/sbin/nologin" ]; }; then
            any=1
            EXP=$(chage -l "$user" 2>/dev/null | grep "Account expires" | cut -d: -f2 | xargs)
            EXP_SHORT=$(date -d "$EXP" +"%Y-%m-%d" 2>/dev/null || echo "$EXP")
            if pgrep -u "$user" >/dev/null 2>&1; then
                STAT="${G}● online${NC}"
            else
                STAT="${GR}○ offline${NC}"
            fi
            row "$col" "$(printf '%-18s %-12s' "$user" "$EXP_SHORT")${STAT}"
        fi
    done < /etc/passwd
    [ "$any" -eq 0 ] && row "$col" "${GR}(no users yet — create one from the menu)${NC}"
    line_bot "$col"
    pause
}

online_users() {
    section "ONLINE USERS" "$LIME"
    local col="$LIME"
    line_top "$col"
    row "$col" "$(printf '%-22s %-10s' 'USERNAME' 'SESSIONS')"
    line_mid "$col"
    local any=0
    while IFS=: read -r user _ uid _ _ _ shell; do
        if [ "$uid" -ge 1000 ] && { [ "$shell" = "/bin/false" ] || [ "$shell" = "/usr/sbin/nologin" ]; }; then
            COUNT=$(pgrep -u "$user" 2>/dev/null | wc -l)
            if [ "$COUNT" -gt 0 ]; then
                any=1
                row "$col" "$(printf '%-22s ' "$user")${C}${COUNT}${NC}"
            fi
        fi
    done < /etc/passwd
    [ "$any" -eq 0 ] && row "$col" "${GR}(nobody connected right now)${NC}"
    line_bot "$col"
    pause
}

bandwidth() {
    local col="$SKY"
    # Live view: refresh every 2s until the user presses a key.
    while true; do
        section "BANDWIDTH MONITOR" "$SKY"
        read -r arx atx atot <<<"$(bw_alltime)"
        read -r drx dtx dtot <<<"$(bw_period d)"
        read -r mrx mtx mtot <<<"$(bw_period m)"
        line_top "$col"
        crow "$col" "${GR}TOTAL BANDWIDTH USED${NC}"
        crow "$col" "${W}${BOLD}$(hb "$atot")${NC}"
        line_mid "$col"
        row "$col" "${SKY}↓ Download${NC}   ${W}$(hb "$arx")${NC}"
        row "$col" "${ORANGE}↑ Upload${NC}     ${W}$(hb "$atx")${NC}"
        line_mid "$col"
        row "$col" "${GR}Today${NC}       ${W}$(hb "$dtot")${NC}"
        row "$col" "${GR}This month${NC}  ${W}$(hb "$mtot")${NC}"
        line_bot "$col"
        echo ""
        echo -e "  ${G}● live${NC} ${GR}— updates every 2s · press ENTER to go back${NC}"
        # wait up to 2s for ENTER; if pressed, exit the loop
        if read -t 2 -r _; then break; fi
    done
}

change_password() {
    section "CHANGE PASSWORD" "$ORANGE"
    read -rp "$(echo -e "  ${C}Username${NC}     : ")" USERNAME
    if ! id "$USERNAME" >/dev/null 2>&1; then
        err "User does not exist."; pause; return
    fi
    read -rp "$(echo -e "  ${C}New password${NC} : ")" PASSWORD
    [ -z "$PASSWORD" ] && { err "Password cannot be empty."; pause; return; }
    echo -e "${PASSWORD}\n${PASSWORD}" | passwd "$USERNAME" >/dev/null 2>&1
    ok "Password updated for '${W}$USERNAME${NC}'."
    pause
}

renew_user() {
    section "RENEW / EXTEND ACCOUNT" "$VIOLET"
    read -rp "$(echo -e "  ${C}Username${NC} : ")" USERNAME
    if ! id "$USERNAME" >/dev/null 2>&1; then
        err "User does not exist."; pause; return
    fi
    read -rp "$(echo -e "  ${C}Add days${NC} : ")" DAYS
    [[ ! "$DAYS" =~ ^[0-9]+$ ]] && DAYS=30
    NEW_EXP=$(date -d "+$DAYS days" +"%Y-%m-%d")
    chage -E "$NEW_EXP" "$USERNAME"
    ok "'${W}$USERNAME${NC}' now expires on ${W}$NEW_EXP${NC}."
    pause
}

service_status() {
    section "SERVICE STATUS" "$P"
    local col="$P" svcs="ssh dropbear ws-proxy stunnel4"
    [ -f /usr/local/bin/xray ] && svcs="$svcs xray"
    line_top "$col"
    for svc in $svcs; do
        if systemctl is-active --quiet "$svc" 2>/dev/null; then
            row "$col" "${G}● RUNNING${NC}   ${W}$svc${NC}"
        else
            row "$col" "${R}○ STOPPED${NC}   ${W}$svc${NC}"
        fi
    done
    line_bot "$col"
    show_ports
    pause
}

restart_services() {
    section "RESTART ALL SERVICES" "$ORANGE"
    note "Restarting services, please wait..."
    systemctl restart ssh 2>/dev/null || systemctl restart sshd 2>/dev/null
    systemctl restart dropbear 2>/dev/null
    systemctl restart ws-proxy 2>/dev/null
    systemctl restart stunnel4 2>/dev/null
    ok "All services restarted."
    pause
}

# ═══════════════════════════════════════════
# XRAY / V2RAY (VMESS) — menu-activated, not auto-started
# ═══════════════════════════════════════════
XBIN=/usr/local/bin/xray
XCONF=/usr/local/etc/xray/config.json
XACC=/etc/xray/accounts.txt
# Default ports (fallback). The config generator resolves the real ports from a
# shared canonical scheme with auto free-port fallback and persists them to
# /etc/xray/ports.conf; xray_paths() loads that file so links match the config.
VM_WS_TLS=2083; VM_WS_NONE=8080; VM_HTTP_NONE=2082; VM_HTTP_TLS=2087; VM_SPLIT_TLS=2096; VM_SPLIT_NONE=2095
VL_WS_TLS=2083; VL_WS_NONE=8080; VL_HTTP_NONE=2082; VL_HTTP_TLS=2087; VL_SPLIT_TLS=2096; VL_SPLIT_NONE=2095
TR_WS_TLS=2083; TR_WS_NONE=8080; TR_HTTP_NONE=2082; TR_HTTP_TLS=2087; TR_SPLIT_TLS=2096; TR_SPLIT_NONE=2095
# Port-443 handover flag (when set, V2Ray owns 443 and SSL payload is off)
XP443F=/etc/xray/port443
STCONF=/etc/stunnel/stunnel.conf
STCERT=/etc/stunnel/stunnel.pem

xray_paths() {
    mkdir -p /etc/xray /usr/local/etc/xray
    touch "$XACC"
    # Load the ports the generator actually assigned (shared scheme + fallback).
    [ -f /etc/xray/ports.conf ] && . /etc/xray/ports.conf
    XDOMAIN=$(cat "$CONF_DIR/domain.conf" 2>/dev/null)
    XIP=$(cat "$CONF_DIR/ip.conf" 2>/dev/null)
    XHOST="${XDOMAIN:-$XIP}"
    XADDR="$XHOST"
    if [ -n "$XDOMAIN" ] && [ -f "/etc/letsencrypt/live/$XDOMAIN/fullchain.pem" ]; then
        XR_CERT="/etc/letsencrypt/live/$XDOMAIN/fullchain.pem"
        XR_KEY="/etc/letsencrypt/live/$XDOMAIN/privkey.pem"
    else
        [ -f /etc/xray/xray.crt ] || openssl req -new -newkey rsa:2048 -days 3650 -nodes -x509 \
            -subj "/CN=${XHOST:-xray}" -out /etc/xray/xray.crt -keyout /etc/xray/xray.key >/dev/null 2>&1
        XR_CERT=/etc/xray/xray.crt; XR_KEY=/etc/xray/xray.key
    fi
    # Mirror the 443 handover so displayed links use the right port.
    P443=$(cat "$XP443F" 2>/dev/null)
    case "$P443" in
        vmess)  VM_WS_TLS=443;;
        vless)  VL_WS_TLS=443;;
        trojan) TR_WS_TLS=443;;
    esac
}

# Rewrite stunnel.conf. $1=yes keeps the SSL-payload listener on 443; no drops it.
write_stunnel_conf() {
    {
        echo "pid     = /var/run/stunnel4.pid"
        echo "cert    = ${STCERT}"
        echo "client  = no"
        echo "socket  = a:SO_REUSEADDR=1"
        echo "socket  = l:TCP_NODELAY=1"
        echo "socket  = r:TCP_NODELAY=1"
        echo "TIMEOUTclose = 0"
        echo ""
        if [ "$1" = "yes" ]; then
            echo "[ssl-ws]"
            echo "accept  = 443"
            echo "connect = 127.0.0.1:80"
            echo ""
        fi
        echo "[ssl-ssh]"
        echo "accept  = 447"
        echo "connect = 127.0.0.1:22"
    } > "$STCONF"
}

# Toggle port 443 between SSL-payload SSH and V2Ray.
xray_443() {
    xray_paths
    section "PORT 443 — SSL PAYLOAD ↔ V2RAY" "$PINK"
    local cur; cur=$(cat "$XP443F" 2>/dev/null)
    local col="$PINK"; line_top "$col"
    if [ -n "$cur" ]; then
        row "$col" "${GR}443 NOW${NC}  ${P}V2Ray — ${cur} (WS-TLS)${NC}"
        row "$col" "${GR}SSL PAYLOAD${NC} ${R}disabled${NC}"
    else
        row "$col" "${GR}443 NOW${NC}  ${G}SSL payload SSH${NC}"
    fi
    line_bot "$col"; echo ""
    if [ -n "$cur" ]; then
        read -rp "$(echo -e "  ${C}Restore SSL-payload SSH on 443?${NC} ${GR}(y/N)${NC} : ")" a
        if [[ "$a" =~ ^[Yy] ]]; then
            rm -f "$XP443F"
            # Order matters: Xray must RELEASE 443 before stunnel can bind it.
            rebuild_config; systemctl restart xray >/dev/null 2>&1
            sleep 1
            write_stunnel_conf yes
            systemctl restart stunnel4 >/dev/null 2>&1
            sleep 1
            if ss -ltn 2>/dev/null | grep -q ':443 '; then
                ok "Port 443 restored to ${G}SSL-payload SSH${NC}."
            else
                err "stunnel didn't bind 443 — retrying..."; systemctl restart stunnel4 >/dev/null 2>&1
                sleep 1
                systemctl is-active --quiet stunnel4 && ok "Port 443 restored to ${G}SSL-payload SSH${NC}." || err "stunnel4 failed — check: journalctl -u stunnel4"
            fi
        else
            note "No change."
        fi
    else
        local wcol="$ORANGE"
        line_top "$wcol"
        crow "$wcol" "${Y}${BOLD}⚠  WARNING${NC}"
        line_mid "$wcol"
        row "$wcol" "${W}This disables SSL-payload SSH on port 443.${NC}"
        row "$wcol" "${GR}443 SSH users stop working · 80/109/143/447 stay up${NC}"
        line_bot "$wcol"
        echo ""
        echo -e "  ${C}${BOLD}Give 443 to which V2Ray protocol?${NC}"
        echo ""
        menu_item "1" "⚡" "VMess  (WS-TLS)"  "$LIME"
        menu_item "2" "⚡" "VLESS  (WS-TLS)"  "$SKY"
        menu_item "3" "⚡" "Trojan (WS-TLS)"  "$PINK"
        menu_item "0" "↩ " "Cancel"           "$GR"
        echo ""
        read -rp "$(echo -e "  ${P}❯${NC} choose : ")" pc
        local proto=""
        case "$pc" in 1) proto=vmess;; 2) proto=vless;; 3) proto=trojan;; *) note "Cancelled."; pause; return;; esac
        if ! xray_install; then err "Xray must be installed first — create an account."; pause; return; fi
        echo "$proto" > "$XP443F"
        # Xray usually runs as a non-root user; 443 is a privileged port.
        # Grant CAP_NET_BIND_SERVICE via a systemd drop-in (idempotent).
        mkdir -p /etc/systemd/system/xray.service.d
        cat > /etc/systemd/system/xray.service.d/priv-port.conf <<'DROPEOF'
[Service]
AmbientCapabilities=CAP_NET_BIND_SERVICE
DROPEOF
        systemctl daemon-reload >/dev/null 2>&1
        # Order matters: stunnel must RELEASE 443 before Xray can bind it.
        write_stunnel_conf no
        systemctl restart stunnel4 >/dev/null 2>&1
        local w=0
        while ss -ltn 2>/dev/null | grep -q ':443 ' && [ $w -lt 5 ]; do sleep 1; w=$((w+1)); done
        rebuild_config
        systemctl enable xray >/dev/null 2>&1; systemctl restart xray >/dev/null 2>&1
        sleep 2
        if systemctl is-active --quiet xray && ss -ltn 2>/dev/null | grep -q ':443 '; then
            ok "Port 443 now serves ${P}${proto}${NC} (WS-TLS). Re-open any account to get its 443 link."
        else
            err "Xray could not take over 443. Last errors:"
            journalctl -u xray -n 8 --no-pager 2>/dev/null | sed 's/^/    /'
            note "Rolling back — 443 returns to SSL-payload SSH."
            rm -f "$XP443F"; rebuild_config
            systemctl restart xray >/dev/null 2>&1
            write_stunnel_conf yes; systemctl restart stunnel4 >/dev/null 2>&1
        fi
    fi
    pause
}

# Rebuild the Xray config from the accounts file (shared generator).
rebuild_config() { /usr/local/bin/xray-gen >/dev/null 2>&1; }

# Total traffic used by a remark (uplink+downlink), in bytes.
# Uses statsquery (a real xray-core subcommand — plain "stats" does not exist)
# and sums every matching counter's value.
xray_used() {
    xray api statsquery --server=127.0.0.1:10085 -pattern "user>>>$1>>>traffic" 2>/dev/null \
        | grep -o '"value": *"[0-9]\+"' | grep -o '[0-9]\+' \
        | awk '{s+=$1} END{print s+0}'
}

# Parse one account line into P_PROTO/P_RK/P_SEC/P_EXP/P_QUOTA (legacy 4-field = vmess).
parse_acct() {
    local f1 f2 f3 f4 f5; IFS='|' read -r f1 f2 f3 f4 f5 <<< "$1"
    case "$f1" in
        vmess|vless|trojan) P_PROTO=$f1; P_RK=$f2; P_SEC=$f3; P_EXP=$f4; P_QUOTA=$f5;;
        *) P_PROTO=vmess; P_RK=$f1; P_SEC=$f2; P_EXP=$f3; P_QUOTA=$f4;;
    esac
}

mkvmess() {  # ps port net type tls path id
    local j="{\"v\":\"2\",\"ps\":\"$1\",\"add\":\"${XADDR}\",\"port\":\"$2\",\"id\":\"$7\",\"aid\":\"0\",\"scy\":\"auto\",\"net\":\"$3\",\"type\":\"$4\",\"host\":\"${XHOST}\",\"path\":\"$6\",\"tls\":\"$5\",\"sni\":\"${XHOST}\"}"
    echo "vmess://$(printf '%s' "$j" | base64 -w0)"
}

# mkuri proto secret port net security path remark
#   net=ws|tcp|tcphttp|splithttp   security=tls|none
mkuri() {
    local proto="$1" sec="$2" port="$3" net="$4" security="$5" path="$6" rk="$7"
    local q net_t="$net"
    case "$net" in tcphttp) net_t="tcp"; q="headerType=http&host=${XHOST}&path=${path}";;
        tcp) q="";; ws) q="host=${XHOST}&path=${path}";; splithttp) q="host=${XHOST}&path=${path}";; esac
    local base="type=${net_t}&security=${security}"
    [ "$security" = "tls" ] && base="${base}&sni=${XHOST}"
    [ -n "$q" ] && base="${base}&${q}"
    if [ "$proto" = "vless" ]; then
        echo "vless://${sec}@${XADDR}:${port}?encryption=none&${base}#${rk}"
    else
        echo "trojan://${sec}@${XADDR}:${port}?${base}#${rk}"
    fi
}

show_links() {  # remark
    local rk="$1"
    parse_acct "$(grep -m1 "|${rk}|" "$XACC" 2>/dev/null || grep -m1 "^${rk}|" "$XACC" 2>/dev/null)"
    banner
    echo -e "  ${GR}Remark${NC}  ${W}${P_RK}${NC}    ${GR}Type${NC} ${P}${BOLD}$(echo "$P_PROTO" | tr a-z A-Z)${NC}"
    echo -e "  ${GR}Secret${NC}  ${W}${P_SEC}${NC}"
    echo -e "  ${GR}Host${NC}    ${Y}${XHOST}${NC}"
    echo -e "  ${GR}Expires${NC} ${W}${P_EXP:-never}${NC}"
    if [ -n "$P_QUOTA" ] && [ "$P_QUOTA" -gt 0 ] 2>/dev/null; then
        echo -e "  ${GR}Quota${NC}   ${W}$(hb "$P_QUOTA")${NC}   ${GR}Used${NC} ${W}$(hb "$(xray_used "$P_RK")")${NC}"
    else
        echo -e "  ${GR}Quota${NC}   ${W}Unlimited${NC}   ${GR}Used${NC} ${W}$(hb "$(xray_used "$P_RK")")${NC}"
    fi
    local sep="  ${GR}──────────────────────────────────────────────────${NC}"
    echo -e "$sep"
    case "$P_PROTO" in
      vmess)
        echo -e "  ${G}${BOLD}TLS${NC}        ${DIM}(ws · $VM_WS_TLS)${NC}\n  $(mkvmess "${rk}-TLS" $VM_WS_TLS ws none tls /vmess $P_SEC)"; echo -e "$sep"
        echo -e "  ${G}${BOLD}NoneTLS${NC}    ${DIM}(ws · $VM_WS_NONE)${NC}\n  $(mkvmess "${rk}-NoneTLS" $VM_WS_NONE ws none "" /vmess $P_SEC)"; echo -e "$sep"
        echo -e "  ${G}${BOLD}HTTP None${NC}  ${DIM}(tcp · $VM_HTTP_NONE)${NC}\n  $(mkvmess "${rk}-HTTP-None" $VM_HTTP_NONE tcp http "" / $P_SEC)"; echo -e "$sep"
        echo -e "  ${G}${BOLD}HTTP TLS${NC}   ${DIM}(tcp · $VM_HTTP_TLS)${NC}\n  $(mkvmess "${rk}-HTTP-TLS" $VM_HTTP_TLS tcp http tls / $P_SEC)"; echo -e "$sep"
        echo -e "  ${G}${BOLD}SPLIT TLS${NC}  ${DIM}(split · $VM_SPLIT_TLS)${NC}\n  $(mkvmess "${rk}-SPLIT-TLS" $VM_SPLIT_TLS splithttp none tls /split $P_SEC)"; echo -e "$sep"
        echo -e "  ${G}${BOLD}SPLIT HTTP${NC} ${DIM}(split · $VM_SPLIT_NONE)${NC}\n  $(mkvmess "${rk}-SPLIT-HTTP" $VM_SPLIT_NONE splithttp none "" /split $P_SEC)"; echo -e "$sep";;
      vless)
        echo -e "  ${G}${BOLD}TLS${NC}        ${DIM}(ws · $VL_WS_TLS)${NC}\n  $(mkuri vless $P_SEC $VL_WS_TLS ws tls /vless "${rk}-TLS")"; echo -e "$sep"
        echo -e "  ${G}${BOLD}NoneTLS${NC}    ${DIM}(ws · $VL_WS_NONE)${NC}\n  $(mkuri vless $P_SEC $VL_WS_NONE ws none /vless "${rk}-NoneTLS")"; echo -e "$sep"
        echo -e "  ${G}${BOLD}HTTP None${NC}  ${DIM}(tcp · $VL_HTTP_NONE)${NC}\n  $(mkuri vless $P_SEC $VL_HTTP_NONE tcphttp none / "${rk}-HTTP-None")"; echo -e "$sep"
        echo -e "  ${G}${BOLD}HTTP TLS${NC}   ${DIM}(tcp · $VL_HTTP_TLS)${NC}\n  $(mkuri vless $P_SEC $VL_HTTP_TLS tcphttp tls / "${rk}-HTTP-TLS")"; echo -e "$sep"
        echo -e "  ${G}${BOLD}SPLIT TLS${NC}  ${DIM}(split · $VL_SPLIT_TLS)${NC}\n  $(mkuri vless $P_SEC $VL_SPLIT_TLS splithttp tls /split "${rk}-SPLIT-TLS")"; echo -e "$sep"
        echo -e "  ${G}${BOLD}SPLIT HTTP${NC} ${DIM}(split · $VL_SPLIT_NONE)${NC}\n  $(mkuri vless $P_SEC $VL_SPLIT_NONE splithttp none /split "${rk}-SPLIT-HTTP")"; echo -e "$sep";;
      trojan)
        echo -e "  ${G}${BOLD}TLS${NC}        ${DIM}(ws · $TR_WS_TLS)${NC}\n  $(mkuri trojan $P_SEC $TR_WS_TLS ws tls /trojan "${rk}-TLS")"; echo -e "$sep"
        echo -e "  ${G}${BOLD}NoneTLS${NC}    ${DIM}(ws · $TR_WS_NONE)${NC}\n  $(mkuri trojan $P_SEC $TR_WS_NONE ws none /trojan "${rk}-NoneTLS")"; echo -e "$sep"
        echo -e "  ${G}${BOLD}HTTP None${NC}  ${DIM}(tcp · $TR_HTTP_NONE)${NC}\n  $(mkuri trojan $P_SEC $TR_HTTP_NONE tcphttp none / "${rk}-HTTP-None")"; echo -e "$sep"
        echo -e "  ${G}${BOLD}HTTP TLS${NC}   ${DIM}(tcp · $TR_HTTP_TLS)${NC}\n  $(mkuri trojan $P_SEC $TR_HTTP_TLS tcphttp tls / "${rk}-HTTP-TLS")"; echo -e "$sep"
        echo -e "  ${G}${BOLD}SPLIT TLS${NC}  ${DIM}(split · $TR_SPLIT_TLS)${NC}\n  $(mkuri trojan $P_SEC $TR_SPLIT_TLS splithttp tls /split "${rk}-SPLIT-TLS")"; echo -e "$sep"
        echo -e "  ${G}${BOLD}SPLIT HTTP${NC} ${DIM}(split · $TR_SPLIT_NONE)${NC}\n  $(mkuri trojan $P_SEC $TR_SPLIT_NONE splithttp none /split "${rk}-SPLIT-HTTP")"; echo -e "$sep";;
    esac
}

xray_open_ports() {
    command -v ufw >/dev/null 2>&1 || return
    for P in $VM_WS_TLS $VM_WS_NONE $VM_HTTP_NONE $VM_HTTP_TLS $VM_SPLIT_TLS $VM_SPLIT_NONE \
             $VL_WS_TLS $VL_WS_NONE $VL_HTTP_NONE $VL_HTTP_TLS $VL_SPLIT_TLS $VL_SPLIT_NONE \
             $TR_WS_TLS $TR_WS_NONE $TR_HTTP_NONE $TR_HTTP_TLS $TR_SPLIT_TLS $TR_SPLIT_NONE; do
        ufw allow ${P}/tcp >/dev/null 2>&1
    done
}

xray_install() {
    [ -f "$XBIN" ] && return 0
    note "Installing Xray-core (needs internet)..."
    bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install >/dev/null 2>&1
    [ -f "$XBIN" ]
}

xray_activate() {
    xray_paths
    section "CREATE XRAY / V2RAY ACCOUNT" "$PINK"
    if ! xray_install; then err "Xray install failed — check the server's internet."; pause; return; fi
    PROTO=""
    # if accounts already exist, offer to add a client under one of them (same protocol & ports)
    if [ -s "$XACC" ]; then
        local rks=() prs=() line i=1 pick p443
        p443=$(cat "$XP443F" 2>/dev/null)
        while IFS= read -r line; do
            [ -z "$line" ] && continue
            parse_acct "$line"; [ -z "$P_RK" ] && continue
            rks+=("$P_RK"); prs+=("$P_PROTO")
        done < "$XACC"
        if [ ${#rks[@]} -gt 0 ]; then
            echo -e "  ${C}What do you want to create?${NC}"
            echo -e "    ${LIME}1${NC}) New account (pick protocol)"
            echo -e "    ${LIME}2${NC}) Add client under an existing account ${GR}(same protocol & ports)${NC}"
            read -rp "$(echo -e "  ${P}❯${NC} choose ${GR}(1-2)${NC} : ")" MODE
            if [ "$MODE" = "2" ]; then
                echo ""
                echo -e "  ${C}Existing accounts:${NC}"
                for i in "${!rks[@]}"; do
                    local tag=""
                    [ -n "$p443" ] && [ "${prs[$i]}" = "$p443" ] && tag=" ${P}(on port 443)${NC}"
                    echo -e "    ${LIME}$((i+1))${NC}) ${W}${rks[$i]}${NC} ${GR}[${prs[$i]}]${NC}${tag}"
                done
                read -rp "$(echo -e "  ${P}❯${NC} choose ${GR}(1-${#rks[@]})${NC} : ")" pick
                if [[ "$pick" =~ ^[0-9]+$ ]] && [ "$pick" -ge 1 ] && [ "$pick" -le ${#rks[@]} ]; then
                    PROTO="${prs[$((pick-1))]}"
                    ok "New client will use ${P}${PROTO}${NC} — same ports as '${W}${rks[$((pick-1))]}${NC}'."
                else
                    err "Invalid choice."; pause; return
                fi
            fi
        fi
    fi
    if [ -z "$PROTO" ]; then
        echo -e "  ${C}Protocol${NC}"
        echo -e "    ${LIME}1${NC}) VMess   ${LIME}2${NC}) VLESS   ${LIME}3${NC}) Trojan"
        read -rp "$(echo -e "  ${P}❯${NC} choose ${GR}(1-3)${NC} : ")" PC
        case "$PC" in 2) PROTO=vless;; 3) PROTO=trojan;; *) PROTO=vmess;; esac
    fi
    read -rp "$(echo -e "  ${C}Remark (name)${NC}         : ")" REMARK
    [ -z "$REMARK" ] && REMARK="${PROTO}-$(date +%s)"
    REMARK=$(echo "$REMARK" | tr ' |' '--')
    if grep -q "|${REMARK}|" "$XACC" 2>/dev/null || grep -q "^${REMARK}|" "$XACC" 2>/dev/null; then
        err "Remark '${REMARK}' already exists — pick another."; pause; return
    fi
    read -rp "$(echo -e "  ${C}Days valid (0=never)${NC}  : ")" XDAYS
    if [[ "$XDAYS" =~ ^[0-9]+$ ]] && [ "$XDAYS" -gt 0 ]; then XEXP=$(date -d "+$XDAYS days" +%Y-%m-%d); else XEXP="never"; fi
    read -rp "$(echo -e "  ${C}Quota GB (0=unlimited)${NC}: ")" XGB
    [[ "$XGB" =~ ^[0-9]+$ ]] || XGB=0
    XQUOTA=$(( XGB * 1024 * 1024 * 1024 ))
    SECRET=$(cat /proc/sys/kernel/random/uuid)   # uuid for vmess/vless, password for trojan
    echo "${PROTO}|${REMARK}|${SECRET}|${XEXP}|${XQUOTA}" >> "$XACC"
    rebuild_config
    xray_open_ports
    systemctl enable xray >/dev/null 2>&1
    systemctl restart xray >/dev/null 2>&1
    sleep 1
    if systemctl is-active --quiet xray; then
        ok "Xray is now ${G}ACTIVE${NC} — account '${W}${REMARK}${NC}' (${P}${PROTO}${NC}) created."
    else
        err "Xray failed to start — check: journalctl -u xray"
    fi
    echo ""
    show_links "$REMARK"
    pause
}

xray_list() {
    xray_paths
    section "XRAY ACCOUNTS" "$PINK"
    local col="$PINK"; line_top "$col"
    row "$col" "$(printf '%-8s %-14s %-11s %-8s %s' 'TYPE' 'REMARK' 'EXPIRES' 'QUOTA' 'USED')"
    line_mid "$col"
    local any=0 line q u
    while IFS= read -r line; do
        [ -z "$line" ] && continue
        parse_acct "$line"; [ -z "$P_RK" ] && continue; any=1
        if [ -n "$P_QUOTA" ] && [ "$P_QUOTA" -gt 0 ] 2>/dev/null; then q=$(hb "$P_QUOTA"); else q="∞"; fi
        u=$(hb "$(xray_used "$P_RK")")
        row "$col" "$(printf '%-8s %-14s %-11s %-8s %s' "$P_PROTO" "$P_RK" "${P_EXP:-never}" "$q" "$u")"
    done < "$XACC"
    [ $any -eq 0 ] && row "$col" "${GR}(no accounts yet — create one first)${NC}"
    line_bot "$col"
    echo ""
    read -rp "$(echo -e "  ${C}Type a remark to show its links${NC} ${GR}(ENTER to skip)${NC} : ")" q
    if [ -n "$q" ]; then
        if grep -q "|${q}|" "$XACC" 2>/dev/null || grep -q "^${q}|" "$XACC" 2>/dev/null; then show_links "$q"; else err "Remark not found."; fi
    fi
    pause
}

xray_delete() {
    xray_paths
    section "DELETE XRAY ACCOUNT" "$R"
    read -rp "$(echo -e "  ${C}Remark to delete${NC} : ")" q
    if ! grep -q "|${q}|" "$XACC" 2>/dev/null && ! grep -q "^${q}|" "$XACC" 2>/dev/null; then err "Remark not found."; pause; return; fi
    # remark is field 2 (new proto|remark|...) or field 1 (legacy remark|...)
    awk -F'|' -v r="$q" '{ if ($1=="vmess"||$1=="vless"||$1=="trojan") nm=$2; else nm=$1; if (nm!=r) print }' "$XACC" > "$XACC.tmp" && mv "$XACC.tmp" "$XACC"
    rebuild_config
    systemctl restart xray >/dev/null 2>&1
    ok "Account '${W}$q${NC}' deleted."
    pause
}

xray_menu() {
    xray_paths
    while true; do
        section "XRAY / V2RAY (VMESS · VLESS · TROJAN)" "$PINK"
        local st col="$PINK"
        if [ ! -f "$XBIN" ]; then st="${R}✗ not installed${NC}"
        elif systemctl is-active --quiet xray 2>/dev/null; then st="${G}● active${NC}"
        else st="${Y}○ installed (stopped)${NC}"; fi
        local p443; p443=$(cat "$XP443F" 2>/dev/null)
        line_top "$col"
        row "$col" "${GR}STATUS${NC}  ${st}"
        row "$col" "${GR}HOST${NC}    ${Y}${XHOST}${NC}"
        row "$col" "${GR}ACCTS${NC}   ${C}$(grep -c '|' "$XACC" 2>/dev/null | grep . || echo 0)${NC}"
        if [ -n "$p443" ]; then row "$col" "${GR}PORT443${NC} ${P}V2Ray (${p443})${NC}"; else row "$col" "${GR}PORT443${NC} ${G}SSL payload SSH${NC}"; fi
        line_bot "$col"
        echo ""
        menu_item "1" "⚡" "Create account (VMess/VLESS/Trojan)" "$LIME"
        menu_item "2" "📋" "Show accounts / links"          "$SKY"
        menu_item "3" "🗑 " "Delete account"                 "$R"
        menu_item "4" "▶ " "Start Xray"                      "$G"
        menu_item "5" "⏹ " "Stop Xray"                       "$Y"
        menu_item "6" "🔀" "Port 443: SSL payload ↔ V2Ray"   "$ORANGE"
        menu_item "0" "↩ " "Back to main menu"              "$GR"
        echo ""
        read -rp "$(echo -e "  ${P}❯${NC} select : ")" o
        case "$o" in
            1) xray_activate ;;
            2) xray_list ;;
            3) xray_delete ;;
            4) systemctl enable xray >/dev/null 2>&1; systemctl start xray >/dev/null 2>&1; ok "Xray started."; sleep 1 ;;
            5) systemctl stop xray >/dev/null 2>&1; ok "Xray stopped."; sleep 1 ;;
            6) xray_443 ;;
            0) break ;;
            *) err "Invalid option."; sleep 1 ;;
        esac
    done
}

slowdns_info() {
    section "SLOWDNS (DNSTT)" "$PINK"
    local col="$PINK"
    local ns pub st
    ns=$(cat "$CONF_DIR/nsdomain.conf" 2>/dev/null)
    pub=$(cat /etc/slowdns/server.pub 2>/dev/null)
    line_top "$col"
    if [ -z "$ns" ] || [ ! -x /usr/local/bin/dnstt-server ]; then
        row "$col" "${GR}SlowDNS is not installed on this server.${NC}"
        row "$col" "${GR}Re-run the installer and enter an NS domain.${NC}"
        line_bot "$col"; pause; return
    fi
    if systemctl is-active --quiet slowdns 2>/dev/null; then
        st="${G}● running${NC}"; else st="${R}○ stopped${NC}"; fi
    row "$col" "${GR}Status${NC}    $st"
    row "$col" "${GR}NS domain${NC} ${W}${ns}${NC}"
    row "$col" "${GR}Backend${NC}   ${W}127.0.0.1:22 (OpenSSH)${NC}"
    row "$col" "${GR}Server IP${NC} ${W}${SERVER_IP}${NC}"
    line_mid "$col"
    row "$col" "${GR}Public key${NC}"
    row "$col" "${W}${pub}${NC}"
    line_bot "$col"
    echo ""
    echo -e "  ${GR}Client: use NS '${W}${ns}${GR}', the public key above, and any${NC}"
    echo -e "  ${GR}SlowDNS-capable app (SSH account = your normal users).${NC}"
    pause
}

# ── FAST DNS (SlowDNS upstream-resolver booster) ──────────
# Points the server's OWN upstream resolver at the fastest public pair
# (1.1.1.1 primary + 8.8.8.8 fallback) with a short timeout. Every name
# lookup made by traffic exiting the SlowDNS tunnel resolves through this,
# so pages start loading sooner. It ONLY rewrites the upstream resolver —
# it never touches port 53, dnstt, iptables, or any protocol, so it cannot
# break SlowDNS or the other tunnels. Reversible and self-healing: if the
# new resolver ever fails a live lookup, it auto-restores the old one.
FASTDNS_MARK="$CONF_DIR/fastdns.on"
FASTDNS_BAK="$CONF_DIR/resolv.fastdns.bak"
FASTDNS_PRIMARY="1.1.1.1"
FASTDNS_FALLBACK="8.8.8.8"

fastdns_ready() {   # SlowDNS must be installed AND running
    local ns; ns=$(cat "$CONF_DIR/nsdomain.conf" 2>/dev/null)
    [ -n "$ns" ] && [ -x /usr/local/bin/dnstt-server ] \
        && systemctl is-active --quiet slowdns 2>/dev/null
}

fastdns_active() { [ -f "$FASTDNS_MARK" ]; }

# content filter (abuse-guard) owns resolv.conf when it points at dnsmasq
fastdns_filter_owns() {
    systemctl is-active --quiet dnsmasq 2>/dev/null \
        && grep -q '^nameserver[[:space:]]\+127\.0\.0\.1' /etc/resolv.conf 2>/dev/null
}

# animated step: fastdns_step "message" "shell command..."
fastdns_step() {
    local msg="$1"; shift
    local frames=('⠋' '⠙' '⠹' '⠸' '⠼' '⠴' '⠦' '⠧' '⠇' '⠏')
    local i=0
    ( "$@" ) >/dev/null 2>&1 & local pid=$!
    while kill -0 "$pid" 2>/dev/null; do
        printf "\r  ${SKY}%s${NC} ${W}%s${NC}" "${frames[$((i % 10))]}" "$msg"
        i=$((i+1)); sleep 0.08
    done
    wait "$pid"; local rc=$?
    if [ $rc -eq 0 ]; then printf "\r  ${G}✔${NC} ${W}%s${NC}\033[K\n" "$msg"
    else printf "\r  ${R}✘${NC} ${W}%s${NC}\033[K\n" "$msg"; fi
    return $rc
}

fastdns_verify() { getent hosts one.one.one.one >/dev/null 2>&1 || getent hosts cloudflare.com >/dev/null 2>&1; }

fastdns_write_resolv() {
    rm -f /etc/resolv.conf 2>/dev/null
    printf 'nameserver %s\nnameserver %s\noptions timeout:2 attempts:2 rotate\n' \
        "$FASTDNS_PRIMARY" "$FASTDNS_FALLBACK" > /etc/resolv.conf
}

fastdns_apply() {
    section "ACTIVATE FAST DNS" "$TEAL"
    if ! fastdns_ready; then
        local col="$ORANGE"; line_top "$col"
        crow "$col" "${W}${BOLD}⚠ SLOWDNS REQUIRED${NC}"; line_mid "$col"
        row "$col" "${GR}Fast DNS boosts SlowDNS — activate SlowDNS first.${NC}"
        row "$col" "${GR}Re-run the installer with an NS domain, then start it.${NC}"
        line_bot "$col"; pause; return
    fi
    echo -e "  ${GR}Boosting SlowDNS with the fastest upstream resolvers${NC}"
    echo -e "  ${GR}(${W}${FASTDNS_PRIMARY}${GR} primary · ${W}${FASTDNS_FALLBACK}${GR} fallback). This only changes${NC}"
    echo -e "  ${GR}the resolver — no port, tunnel or protocol is touched.${NC}\n"

    fastdns_step "Checking SlowDNS tunnel" sleep 0.6

    if fastdns_filter_owns; then
        # Abuse-guard's dnsmasq already forwards to 1.1.1.1 + 8.8.8.8.
        # Overwriting resolv.conf here would disable the content filter, so
        # we leave it in place — fast DNS is effectively already applied.
        fastdns_step "Detected content filter — keeping it intact" sleep 0.6
        fastdns_step "Confirming fast upstream via filter" fastdns_verify
        : > "$FASTDNS_MARK"
        echo ""
        local col="$G"; line_top "$col"
        crow "$col" "${W}${BOLD}⚡ FAST DNS ON (via filter)${NC}"; line_mid "$col"
        row "$col" "${GR}Upstream${NC}  ${W}${FASTDNS_PRIMARY} · ${FASTDNS_FALLBACK}${NC}"
        row "$col" "${GR}Managed by${NC} ${W}content filter (dnsmasq)${NC}"
        line_bot "$col"; pause; return
    fi

    # back up current resolver once, then apply
    [ -f "$FASTDNS_BAK" ] || cp -f /etc/resolv.conf "$FASTDNS_BAK" 2>/dev/null
    fastdns_step "Selecting fastest resolvers" sleep 0.5
    fastdns_step "Applying upstream resolver" fastdns_write_resolv

    if ! fastdns_step "Verifying live resolution" fastdns_verify; then
        # never leave DNS broken — roll back immediately
        [ -f "$FASTDNS_BAK" ] && cp -f "$FASTDNS_BAK" /etc/resolv.conf 2>/dev/null
        echo ""
        err "Verification failed — restored the previous resolver. No change made."
        pause; return
    fi
    : > "$FASTDNS_MARK"
    echo ""
    local col="$G"; line_top "$col"
    crow "$col" "${W}${BOLD}⚡ FAST DNS ACTIVATED${NC}"; line_mid "$col"
    row "$col" "${GR}Primary${NC}   ${W}${FASTDNS_PRIMARY}${NC}  ${GR}(Cloudflare)${NC}"
    row "$col" "${GR}Fallback${NC}  ${W}${FASTDNS_FALLBACK}${NC}  ${GR}(Google)${NC}"
    row "$col" "${GR}Applies to${NC} ${W}all traffic exiting the SlowDNS tunnel${NC}"
    line_bot "$col"
    echo -e "\n  ${GR}SlowDNS lookups now resolve through the fastest path.${NC}"
    pause
}

fastdns_revert() {
    section "FAST DNS — REVERT" "$ORANGE"
    if ! fastdns_active; then
        note "Fast DNS is not active — nothing to revert."; pause; return
    fi
    if fastdns_filter_owns; then
        rm -f "$FASTDNS_MARK"
        ok "Content filter still manages DNS — fast-DNS marker cleared."
        pause; return
    fi
    fastdns_step "Restoring previous resolver" bash -c '[ -f "'"$FASTDNS_BAK"'" ] && cp -f "'"$FASTDNS_BAK"'" /etc/resolv.conf'
    fastdns_step "Verifying live resolution" fastdns_verify || true
    rm -f "$FASTDNS_MARK" "$FASTDNS_BAK"
    echo ""
    ok "Reverted to the previous resolver."
    pause
}

fastdns_menu() {
    while true; do
        section "FAST DNS (SLOWDNS BOOSTER)" "$TEAL"
        local st
        if fastdns_active; then st="${G}● active${NC}"; else st="${GR}○ off${NC}"; fi
        echo -e "  ${GR}Status${NC} : $st\n"
        menu_item "1" "🚀" "Activate fast DNS"   "$G"
        menu_item "2" "↩ " "Revert to default"   "$ORANGE"
        menu_item "0" "↩ " "Back to main menu"   "$GR"
        echo ""
        read -rp "$(echo -e "  ${TEAL}❯${NC} select an option : ")" fo
        case "$fo" in
            1) fastdns_apply ;;
            2) fastdns_revert ;;
            0) return ;;
            *) err "Invalid option."; sleep 1 ;;
        esac
    done
}

abuse_menu() {
    while true; do
        section "ABUSE PROTECTION" "$LIME"
        /usr/local/bin/abuse-guard status | sed 's/^/  /'
        echo ""
        echo -e "  ${GR}Blocks spam(25) + torrent ports, caps floods/scans, fail2ban,${NC}"
        echo -e "  ${GR}and torrent/piracy + carding/malware DNS filtering. Speed untouched.${NC}"
        echo ""
        menu_item "1" "🛡 " "Enable protection"   "$G"
        menu_item "2" "🧹" "Disable protection"   "$R"
        menu_item "3" "🔄" "Refresh blocklist"    "$SKY"
        menu_item "4" "🩺" "Test / diagnose"      "$ORANGE"
        menu_item "0" "↩ " "Back to main menu"    "$GR"
        echo ""
        read -rp "$(echo -e "  ${P}❯${NC} select an option : ")" AO
        case "$AO" in
            1) note "Applying (first run installs fail2ban + dnsmasq)..."
               /usr/local/bin/abuse-guard enable; pause ;;
            2) /usr/local/bin/abuse-guard disable; pause ;;
            3) note "Downloading the latest blocklist..."
               /usr/local/bin/abuse-guard refresh; pause ;;
            4) /usr/local/bin/abuse-guard test; pause ;;
            0) return ;;
            *) err "Invalid option."; sleep 1 ;;
        esac
    done
}

# ═══════════════════════════════════════════
# UDP (HYSTERIA) — high-speed QUIC/UDP tunnel, menu-activated
# ═══════════════════════════════════════════
# Separate from the SSL-payload/bug-host side: Hysteria is UDP and does NOT use
# payloads or bug hosts. It listens on ONE base UDP port and iptables REDIRECTs
# a whole UDP port-hopping range onto it, so throttling a single port can't kill
# it. UDP-only + a dedicated INPUT/nat rule set => it never touches the TCP
# protocols (SSH/SSL/WS/V2Ray) or SlowDNS (UDP 53, outside the range).
HY_BIN=/usr/local/bin/hysteria
HY_DIR=/etc/hysteria
HY_CONF=$HY_DIR/config.json
HY_USERS=$HY_DIR/users
HY_OBFS_FILE=$HY_DIR/obfs
HY_UNIT=/etc/systemd/system/hysteria-udp.service
HY_PORT=36712          # base UDP listen port (kept in sync with the porthop helper)
HY_HOP_LO=20000        # port-hopping range low
HY_HOP_HI=50000        # port-hopping range high

hy_active() { systemctl is-active --quiet hysteria-udp 2>/dev/null; }

hy_install() {
    # A previous failed download can leave a corrupt file behind — only trust
    # a binary that actually runs, never just "file exists and is executable".
    if [ -x "$HY_BIN" ] && "$HY_BIN" --version >/dev/null 2>&1; then return 0; fi
    rm -f "$HY_BIN"
    note "Installing Hysteria (UDP) — needs internet..."
    local arch url
    case "$(uname -m)" in
        x86_64|amd64) arch=amd64;;
        aarch64|arm64) arch=arm64;;
        armv7l|armv7) arch=arm;;
        *) arch=amd64;;
    esac
    url="https://github.com/apernet/hysteria/releases/download/v1.3.5/hysteria-linux-${arch}"
    curl -fL -o "$HY_BIN" "$url" >/dev/null 2>&1 || wget -qO "$HY_BIN" "$url" >/dev/null 2>&1
    chmod +x "$HY_BIN" 2>/dev/null
    # Must be a real ELF binary that runs, not an HTML error page.
    if ! "$HY_BIN" --version >/dev/null 2>&1; then rm -f "$HY_BIN"; return 1; fi
    return 0
}

hy_ensure_cert() {
    [ -s "$HY_DIR/hysteria.crt" ] && [ -s "$HY_DIR/hysteria.key" ] && return 0
    mkdir -p "$HY_DIR"
    openssl req -x509 -nodes -newkey rsa:2048 -days 3650 \
        -subj "/CN=${HOST_DISPLAY:-hysteria}" \
        -keyout "$HY_DIR/hysteria.key" -out "$HY_DIR/hysteria.crt" >/dev/null 2>&1
}

# Build config.json from the users file. Users are stored as "username:password"
# for our own bookkeeping, but AGN-style UDP client apps authenticate with the
# PASSWORD ONLY — so Hysteria's passwords list must contain just the passwords,
# never "username:password", or the app's login never matches and it won't connect.
hy_write_config() {
    mkdir -p "$HY_DIR"
    local obfs; obfs=$(cat "$HY_OBFS_FILE" 2>/dev/null)
    # Obfs is one shared value for the whole server. Like other UDP panels,
    # default it to the FIRST username so clients have less to type. It is
    # stored once and never changes afterwards (or later clients would break).
    if [ -z "$obfs" ]; then
        obfs=$(awk -F: 'NF{print $1; exit}' "$HY_USERS" 2>/dev/null)
        [ -z "$obfs" ] && obfs=$(openssl rand -hex 8)
        echo "$obfs" > "$HY_OBFS_FILE"
    fi
    local pwlist="" line pw first=1
    while IFS= read -r line; do
        [ -z "$line" ] && continue
        pw="${line#*:}"   # password = everything after the first ':'
        if [ $first -eq 1 ]; then pwlist="\"$pw\""; first=0
        else pwlist="$pwlist, \"$pw\""; fi
    done < "$HY_USERS"
    cat > "$HY_CONF" <<JSON
{
  "listen": ":$HY_PORT",
  "cert": "$HY_DIR/hysteria.crt",
  "key": "$HY_DIR/hysteria.key",
  "up_mbps": 100,
  "down_mbps": 100,
  "disable_udp": false,
  "obfs": "$obfs",
  "auth": {
    "mode": "passwords",
    "config": [$pwlist]
  }
}
JSON
}

# Idempotent port-hopping + firewall helper, called by the unit on start/stop so
# the rules survive reboots and are cleaned up on deactivate. UDP only.
hy_write_porthop() {
    # Minimal images may lack iptables entirely — install it first.
    if ! PATH="/usr/sbin:/sbin:/usr/local/sbin:$PATH" command -v iptables >/dev/null 2>&1; then
        echo "  installing iptables..."
        DEBIAN_FRONTEND=noninteractive apt-get install -y iptables >/dev/null 2>&1
    fi
    cat > /usr/local/bin/hysteria-porthop <<'PHEOF'
#!/bin/bash
# Values MUST match HY_PORT / HY_HOP_LO / HY_HOP_HI in the installer.
# systemd runs with a minimal PATH that misses sbin on some distros, which
# makes iptables "command not found" — always fix PATH first.
export PATH="/usr/sbin:/sbin:/usr/local/sbin:$PATH"
IFACE=$(ip route 2>/dev/null | awk '/^default/{print $5; exit}'); [ -z "$IFACE" ] && IFACE=eth0
LO=20000; HI=50000; PORT=36712
add(){
  iptables -t nat -C PREROUTING -i "$IFACE" -p udp --dport $LO:$HI -j REDIRECT --to-ports $PORT 2>/dev/null || \
    iptables -t nat -A PREROUTING -i "$IFACE" -p udp --dport $LO:$HI -j REDIRECT --to-ports $PORT
  iptables -C INPUT -p udp --dport $PORT -j ACCEPT 2>/dev/null || iptables -A INPUT -p udp --dport $PORT -j ACCEPT
  iptables -C INPUT -p udp --dport $LO:$HI -j ACCEPT 2>/dev/null || iptables -A INPUT -p udp --dport $LO:$HI -j ACCEPT
}
del(){
  while iptables -t nat -D PREROUTING -i "$IFACE" -p udp --dport $LO:$HI -j REDIRECT --to-ports $PORT 2>/dev/null; do :; done
  iptables -D INPUT -p udp --dport $PORT -j ACCEPT 2>/dev/null
  iptables -D INPUT -p udp --dport $LO:$HI -j ACCEPT 2>/dev/null
}
case "$1" in up) add;; down) del;; *) echo "usage: $0 up|down";; esac
PHEOF
    chmod +x /usr/local/bin/hysteria-porthop
    # ufw (if active) needs the range opened explicitly too.
    if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -qi active; then
        ufw allow "${HY_PORT}"/udp >/dev/null 2>&1
        ufw allow "${HY_HOP_LO}:${HY_HOP_HI}"/udp >/dev/null 2>&1
    fi
}

hy_write_unit() {
    cat > "$HY_UNIT" <<UNIT
[Unit]
Description=Hysteria UDP Tunnel (high-speed)
After=network.target
[Service]
Type=simple
ExecStartPre=/usr/local/bin/hysteria-porthop up
ExecStart=$HY_BIN server --config $HY_CONF
ExecStopPost=/usr/local/bin/hysteria-porthop down
Restart=always
RestartSec=3
LimitNOFILE=1048576
[Install]
WantedBy=multi-user.target
UNIT
    systemctl daemon-reload
}

hy_activate() {
    section "ACTIVATE UDP (HYSTERIA)" "$G"
    if ! hy_install; then err "Hysteria install failed — check the server's internet."; pause; return; fi
    local u p
    read -rp "$(echo -e "  ${C}Username${NC} : ")" u
    [ -z "$u" ] && { err "Username can't be empty."; pause; return; }
    read -rp "$(echo -e "  ${C}Password${NC} : ")" p
    [ -z "$p" ] && { err "Password can't be empty."; pause; return; }
    case "$u$p" in *:*) err "Username/password cannot contain ':'"; pause; return;; esac
    mkdir -p "$HY_DIR"
    if grep -q "^${u}:" "$HY_USERS" 2>/dev/null; then err "User '$u' already exists."; pause; return; fi
    # First user ever → obfs becomes this username (even if an old obfs file
    # was left behind by an earlier failed attempt). Never changed afterwards.
    if ! grep -q . "$HY_USERS" 2>/dev/null; then echo "$u" > "$HY_OBFS_FILE"; fi
    echo "${u}:${p}" >> "$HY_USERS"
    hy_ensure_cert
    hy_write_config
    hy_write_porthop
    hy_write_unit
    systemctl enable hysteria-udp >/dev/null 2>&1
    systemctl restart hysteria-udp >/dev/null 2>&1
    sleep 2
    if hy_active; then
        ok "UDP user '${W}$u${NC}' added — service running."
        hy_show
    else
        err "Hysteria failed to start. Last errors:"
        journalctl -u hysteria-udp -n 8 --no-pager 2>/dev/null | sed 's/^/    /'
        # roll the just-added user back so a bad entry can't wedge the config
        grep -v "^${u}:" "$HY_USERS" > "$HY_USERS.tmp" 2>/dev/null && mv "$HY_USERS.tmp" "$HY_USERS"
        hy_write_config
    fi
    pause
}

hy_show() {
    if [ ! -s "$HY_USERS" ]; then err "No UDP users yet — activate first."; return; fi
    local obfs; obfs=$(cat "$HY_OBFS_FILE" 2>/dev/null)
    local col="$SKY"; echo ""; line_top "$col"
    crow "$col" "${W}${BOLD}UDP (HYSTERIA) CONNECTION${NC}"; line_mid "$col"
    row "$col" "${GR}Server${NC}    ${W}${SERVER_IP}${NC}"
    row "$col" "${GR}UDP ports${NC} ${W}${HY_HOP_LO}-${HY_HOP_HI}${NC}"
    row "$col" "${GR}Obfs${NC}      ${W}${obfs}${NC}"
    line_mid "$col"
    crow "$col" "${GR}Users  (username : password)${NC}"
    local uu pp
    while IFS=: read -r uu pp; do [ -n "$uu" ] && row "$col" "${W}${uu}${NC} : ${W}${pp}${NC}"; done < "$HY_USERS"
    line_bot "$col"
    echo ""
    echo -e "  ${GR}In your UDP app (UDP Custom / AGN UDP / NapsternetV):${NC}"
    echo -e "    ${GR}• Server   : ${W}${SERVER_IP}${NC}"
    echo -e "    ${GR}• Port(s)  : ${W}${HY_HOP_LO}-${HY_HOP_HI}${NC} ${GR}(port hopping)${NC}"
    echo -e "    ${GR}• OBFS     : ${W}${obfs}${NC}"
    echo -e "    ${GR}• Password : ${W}the password from the list above (NOT the username)${NC}"
    echo -e "    ${GR}• Turn ON 'Allow insecure / self-signed certificate'${NC}"
}

hy_remove_user() {
    if [ ! -s "$HY_USERS" ]; then err "No users."; pause; return; fi
    echo -e "  ${C}Users:${NC}"; sed 's/:.*//' "$HY_USERS" | nl -w2 -s') '
    local ru
    read -rp "$(echo -e "  ${P}❯${NC} username to remove : ")" ru
    [ -z "$ru" ] && { note "Cancelled."; pause; return; }
    if grep -q "^${ru}:" "$HY_USERS"; then
        grep -v "^${ru}:" "$HY_USERS" > "$HY_USERS.tmp" && mv "$HY_USERS.tmp" "$HY_USERS"
        hy_write_config
        systemctl restart hysteria-udp >/dev/null 2>&1
        ok "Removed '$ru'."
    else
        err "No such user."
    fi
    pause
}

hy_deactivate() {
    section "DEACTIVATE UDP" "$R"
    systemctl stop hysteria-udp >/dev/null 2>&1
    systemctl disable hysteria-udp >/dev/null 2>&1
    /usr/local/bin/hysteria-porthop down 2>/dev/null
    ok "UDP (Hysteria) deactivated. Users stay saved for next time."
    pause
}

hysteria_menu() {
    while true; do
        section "UDP (HYSTERIA) — HIGH SPEED" "$SKY"
        local col="$SKY"; line_top "$col"
        if hy_active; then
            row "$col" "${GR}STATUS${NC}  ${G}● active${NC}"
            row "$col" "${GR}PORTS${NC}   ${W}UDP ${HY_HOP_LO}-${HY_HOP_HI}${NC}"
            row "$col" "${GR}USERS${NC}   ${W}$(grep -c . "$HY_USERS" 2>/dev/null || echo 0)${NC}"
        else
            row "$col" "${GR}STATUS${NC}  ${R}○ not active${NC}"
        fi
        line_bot "$col"; echo ""
        menu_item "1" "⚡" "Activate / add user"      "$G"
        menu_item "2" "📋" "Show connection details"  "$SKY"
        menu_item "3" "🗑 " "Remove a user"            "$ORANGE"
        menu_item "4" "🧹" "Deactivate UDP"           "$R"
        menu_item "0" "↩ " "Back to main menu"        "$GR"
        echo ""
        read -rp "$(echo -e "  ${P}❯${NC} select an option : ")" HO
        case "$HO" in
            1) hy_activate ;;
            2) hy_show; pause ;;
            3) hy_remove_user ;;
            4) hy_deactivate ;;
            0) return ;;
            *) err "Invalid option."; sleep 1 ;;
        esac
    done
}

menu_item() {  # menu_item NUM ICON "Label" color
    echo -e "  ${4}${BOLD}$1${NC} ${GR}│${NC} ${4}$2${NC}  ${W}$3${NC}"
}

while true; do
    banner
    status_bar
    echo ""
    menu_item "1" "➕" "Create SSH user"          "$LIME"
    menu_item "2" "🗑 " "Delete SSH user"          "$R"
    menu_item "3" "📋" "List all users"           "$SKY"
    menu_item "4" "🟢" "Show online users"        "$G"
    menu_item "5" "🔑" "Change user password"     "$ORANGE"
    menu_item "6" "♻️ " "Renew / extend account"   "$VIOLET"
    menu_item "7" "📊" "Service status"           "$C"
    menu_item "8" "📶" "Bandwidth usage"          "$SKY"
    menu_item "9" "🌐" "Xray / V2Ray (VMess)"     "$PINK"
    menu_item "10" "🔄" "Restart all services"    "$Y"
    menu_item "11" "🐌" "SlowDNS info"            "$PINK"
    menu_item "12" "🛡 " "Abuse protection"        "$LIME"
    menu_item "13" "⚡" "UDP (Hysteria) high-speed" "$SKY"
    menu_item "14" "🚀" "Activate fast DNS"        "$TEAL"
    menu_item "0" "🚪" "Exit"                     "$GR"
    echo ""
    read -rp "$(echo -e "  ${P}❯${NC} select an option : ")" OPT
    case "$OPT" in
        1) create_user ;;
        2) delete_user ;;
        3) list_users ;;
        4) online_users ;;
        5) change_password ;;
        6) renew_user ;;
        7) service_status ;;
        8) bandwidth ;;
        9) xray_menu ;;
        10) restart_services ;;
        11) slowdns_info ;;
        12) abuse_menu ;;
        13) hysteria_menu ;;
        14) fastdns_menu ;;
        0) clear; echo -e "  ${G}Goodbye 👋${NC}\n"; exit 0 ;;
        *) echo -e "  ${R}Invalid option.${NC}"; sleep 1 ;;
    esac
done
MENUEOF

chmod +x /usr/local/bin/menu
success "Management panel installed — type 'menu' to open it"

# ═══════════════════════════════════════════
# LOGIN WATERMARK (MOTD) — shown on every SSH login / server open
# ═══════════════════════════════════════════
# Rendered live at login so host + service status are always current. Uses
# Debian's pam_motd (update-motd.d) so it prints exactly once per login.
: > /etc/motd 2>/dev/null || true                 # silence the stock Debian MOTD
[ -f /etc/legal ] && : > /etc/legal 2>/dev/null   # drop the "unauthorized use" notice
sed -i 's/^#\?PrintLastLog.*/PrintLastLog yes/' /etc/ssh/sshd_config 2>/dev/null || true
mkdir -p /etc/update-motd.d
# Remove Debian/Ubuntu default motd fragments so only our watermark shows.
find /etc/update-motd.d -maxdepth 1 -type f ! -name '00-litronx' -exec chmod -x {} \; 2>/dev/null || true
cat > /etc/update-motd.d/00-litronx <<'MOTDEOF'
#!/bin/bash
# Branded login watermark for the SSH-VPN server.
NC='\033[0m'; BOLD='\033[1m'; DIM='\033[2m'
TEAL='\033[38;5;44m'; SKY='\033[38;5;39m'; PINK='\033[38;5;213m'
LIME='\033[38;5;155m'; GRY='\033[38;5;240m'; W='\033[97m'; Y='\033[1;33m'; G='\033[1;32m'; R='\033[1;31m'
CONF_DIR=/etc/ssh-panel
DOMAIN=$(cat "$CONF_DIR/domain.conf" 2>/dev/null)
SERVER_IP=$(cat "$CONF_DIR/ip.conf" 2>/dev/null)
HOST_DISPLAY="${DOMAIN:-${SERVER_IP:-$(hostname -I 2>/dev/null | awk '{print $1}')}}"
UP=$(uptime -p 2>/dev/null | sed 's/^up //'); [ -z "$UP" ] && UP="just now"
NOW=$(date '+%a %d %b %Y · %H:%M')
users_total=0
[ -f "$CONF_DIR/users.list" ] && users_total=$(grep -c . "$CONF_DIR/users.list" 2>/dev/null)
online=$(who 2>/dev/null | wc -l)
svc=""
for s in ssh dropbear ws-proxy stunnel4 xray dnsmasq; do
    if systemctl is-active --quiet "$s" 2>/dev/null; then svc+=" ${G}●${NC}${GRY}${s}${NC}"; else svc+=" ${R}○${NC}${GRY}${s}${NC}"; fi
done
echo ""
echo -e "   ${TEAL} ██████╗ ██████╗ ${SKY}██╗   ██╗${PINK}██████╗ ███╗   ██╗${NC}"
echo -e "   ${TEAL}██╔════╝██╔════╝ ${SKY}██║   ██║${PINK}██╔══██╗████╗  ██║${NC}"
echo -e "   ${TEAL}╚█████╗ ╚█████╗  ${SKY}███████║${PINK}██████╔╝██╔██╗ ██║${NC}"
echo -e "   ${TEAL} ╚═══██╗ ╚═══██╗ ${SKY}██╔══██║${PINK}██╔═══╝ ██║╚██╗██║${NC}"
echo -e "   ${TEAL}██████╔╝██████╔╝ ${SKY}██║  ██║${PINK}██║     ██║ ╚████║${NC}"
echo -e "   ${TEAL}╚═════╝ ╚═════╝  ${SKY}╚═╝  ╚═╝${PINK}╚═╝     ╚═╝  ╚═══╝${NC}"
echo -e "        ${GRY}ws · ssl · openssh · dropbear · v2ray  ${W}${BOLD}VPN SERVER${NC}"
echo ""
echo -e "  ${TEAL}╭──────────────────────────────────────────────────────╮${NC}"
printf "  ${TEAL}│${NC}  ${GRY}HOST${NC}    ${W}${BOLD}%-44s${NC}${TEAL}│${NC}\n" "$HOST_DISPLAY"
printf "  ${TEAL}│${NC}  ${GRY}TIME${NC}    ${W}%-44s${NC}${TEAL}│${NC}\n" "$NOW"
printf "  ${TEAL}│${NC}  ${GRY}UPTIME${NC}  ${W}%-44s${NC}${TEAL}│${NC}\n" "$UP"
printf "  ${TEAL}│${NC}  ${GRY}USERS${NC}   ${LIME}%-3s${NC} ${GRY}accounts${NC}   ${Y}%-3s${NC} ${GRY}online%-19s${NC}${TEAL}│${NC}\n" "$users_total" "$online" ""
echo -e "  ${TEAL}├──────────────────────────────────────────────────────┤${NC}"
echo -e "  ${TEAL}│${NC} ${GRY}svc${NC}${svc}${TEAL}│${NC}"
echo -e "  ${TEAL}╰──────────────────────────────────────────────────────╯${NC}"
echo -e "        ${GRY}type${NC} ${W}${BOLD}menu${NC} ${GRY}to open the control panel${NC}"
echo ""
MOTDEOF
chmod +x /etc/update-motd.d/00-litronx
# OpenSSH on Debian renders update-motd.d through pam_motd; make sure the
# static-motd printer is also enabled so pam runs the dynamic scripts.
grep -q 'pam_motd.so motd=/run/motd.dynamic' /etc/pam.d/sshd 2>/dev/null || true
# Pre-render once so it's visible immediately even before the first pam refresh.
/etc/update-motd.d/00-litronx > /run/motd.dynamic 2>/dev/null || true
success "Login watermark installed — shows on every server login"

# ═══════════════════════════════════════════
# DEFAULT SSH USERS (auto-created)
# ═══════════════════════════════════════════
phase "Default users"
DEFAULT_USER_PASS="0000"
DEFAULT_USER_DAYS=30
DEFAULT_USER_EXP=$(date -d "+${DEFAULT_USER_DAYS} days" +"%Y-%m-%d")
for U in deon febo geto weon ceon; do
    if id "$U" >/dev/null 2>&1; then
        info "User '$U' already exists — skipped"
    else
        useradd -e "$DEFAULT_USER_EXP" -M -s /bin/false "$U"
        echo -e "${DEFAULT_USER_PASS}\n${DEFAULT_USER_PASS}" | passwd "$U" >/dev/null 2>&1
        success "User '$U' created (pass: ${DEFAULT_USER_PASS}, expires: ${DEFAULT_USER_EXP})"
    fi
done

# ═══════════════════════════════════════════
# FINAL MESSAGE
# ═══════════════════════════════════════════
UI_SILENT=0
_ELAPSED=$(( SECONDS - INSTALL_T0 ))
clear
echo ""
echo -e "  ${LIME}${BOLD}  ✔  INSTALLATION COMPLETE${NC}   ${GRY}all protocols installed in ${BWHITE}${_ELAPSED}s${GRY}.${NC}"
echo ""
echo -e "  ${TEAL}╭──────────────────────────────────────────────────────╮${NC}"
printf  "  ${TEAL}│${NC}  ${SKY}◆${NC} %-13s ${BWHITE}${BOLD}%-33.33s${NC}${TEAL}│${NC}\n" "Host / Domain" "${DOMAIN:-$SERVER_IP}"
echo -e "  ${TEAL}├─ ${BWHITE}${BOLD}SERVICES & PORTS${NC} ${TEAL}──────────────────────────────────┤${NC}"
printf  "  ${TEAL}│${NC}   ${LIME}▸${NC} %-22s ${BWHITE}%-24s${NC}${TEAL}│${NC}\n" "WebSocket (payload)" "80"
printf  "  ${TEAL}│${NC}   ${LIME}▸${NC} %-22s ${BWHITE}%-24s${NC}${TEAL}│${NC}\n" "SSL + payload (TLS)" "443"
printf  "  ${TEAL}│${NC}   ${LIME}▸${NC} %-22s ${BWHITE}%-24s${NC}${TEAL}│${NC}\n" "SSL direct SSH (TLS)" "447"
printf  "  ${TEAL}│${NC}   ${LIME}▸${NC} %-22s ${BWHITE}%-24s${NC}${TEAL}│${NC}\n" "OpenSSH" "22"
printf  "  ${TEAL}│${NC}   ${LIME}▸${NC} %-22s ${BWHITE}%-24s${NC}${TEAL}│${NC}\n" "Dropbear" "109, 143"
echo -e "  ${TEAL}├─ ${BWHITE}${BOLD}DEFAULT USERS${NC} ${GRY}(pass: 0000, ${DEFAULT_USER_DAYS}d)${NC} ${TEAL}────────────────────┤${NC}"
printf  "  ${TEAL}│${NC}   ${PINK}●${NC} %-50s${TEAL}│${NC}\n" "deon · febo · geto · weon · ceon"
echo -e "  ${TEAL}╰──────────────────────────────────────────────────────╯${NC}"
echo ""
echo -e "  ${GRY}Client tips${NC}"
echo -e "    ${DIM}WS payload${NC}  GET / HTTP/1.1[crlf]Host: ${BWHITE}${DOMAIN:-$SERVER_IP}${NC}[crlf]Upgrade: websocket[crlf][crlf]"
echo -e "    ${DIM}SSL/SNI${NC}     ${BWHITE}${DOMAIN:-$SERVER_IP}${NC}"
echo ""
echo -e "  ${TEAL}${BOLD}▸${NC} Type ${LIME}${BOLD}menu${NC} to open the control panel and create users."
echo ""

# ═══════════════════════════════════════════
# SELF-CLEANUP — remove traces from shell history & disk
# ═══════════════════════════════════════════
info "Cleaning install traces from server history..."
# 1) Delete any downloaded copy of this installer.
[ -n "${BASH_SOURCE[0]}" ] && [ -f "${BASH_SOURCE[0]}" ] && rm -f "${BASH_SOURCE[0]}" 2>/dev/null
for f in ssh-ssl-setup.sh /root/ssh-ssl-setup.sh /tmp/ssh-ssl-setup.sh; do
    [ -f "$f" ] && rm -f "$f" 2>/dev/null
done
# 2) Scrub install command lines from every shell history file we can find.
for H in /root/.bash_history "$HOME/.bash_history" /root/.zsh_history "$HOME/.zsh_history" \
         /root/.ash_history "$HOME/.ash_history" /root/.local/share/fish/fish_history; do
    [ -f "$H" ] || continue
    sed -i -E '/(ssh-ssl-setup\.sh|raw\.githubusercontent\.com\/kelvinsonatech\/myssh)/d' "$H" 2>/dev/null
done
# 3) Drop the current session's in-memory history so it can't be flushed back.
history -c 2>/dev/null || true
: > "${HISTFILE:-/root/.bash_history}" 2>/dev/null || true
success "Install traces removed from history"
echo ""
