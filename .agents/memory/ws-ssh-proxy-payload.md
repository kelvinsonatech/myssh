---
name: WebSocket/SSH dual-mode proxy payload handling
description: How the port-80 (and 443-via-stunnel) dual-mode proxy must consume injector payloads before tunneling to SSH
---

# Dual-mode WS/SSH proxy (ssh-ssl-setup.sh: Go primary + Python fallback)

The proxy on 0.0.0.0:80 backends to 127.0.0.1:22 (OpenSSH). Per connection it
peeks the first bytes: `SSH-` prefix (or empty) = direct SSH, bridge straight
through; anything else = HTTP/WS injector payload → reply `101 Switching
Protocols` then tunnel.

**Injector payloads are NOT a single clean request.** Real payloads (HTTP
Injector / HTTP Custom style) can contain multiple `\r\n\r\n` blocks, an
`Expect: 100-continue` (client sends block 1, waits for a provisional reply,
THEN sends block 2), and junk like a second `CONNECT ...` + `Content-length`.

**Two rules that make arbitrary payloads work:**
1. If the accumulated headers contain `100-continue`, send
   `HTTP/1.1 100 Continue\r\n\r\n` before the `101`, or two-stage clients hang.
2. After sending the `101`, DO NOT immediately bridge the raw stream. Keep
   reading and **discard everything up to the `SSH-` banner**, then forward from
   `SSH-` onward. Every SSH client sends `SSH-2.0-...` as its first bytes, so it
   is a reliable delimiter between payload junk and the real SSH handshake.
**Why:** with `Expect: 100-continue`, block 2 arrives AFTER the reply; if you
bridge right after `101`, that block gets piped into SSH and corrupts the
handshake — the symptom is "works for simple payloads, fails for this one".
**How to apply:** keep the Go and Python proxies in lockstep; use a bounded read
(timeout + size cap) when scanning for `SSH-` and fall back to an empty prefix so
a missing banner never hangs the connection.
