---
name: SlowDNS (dnstt) UDP 53 binding
description: Why SlowDNS silently fails to start and how the installer fixes it
---

# SlowDNS (dnstt-server) needs UDP 53 free, or it silently dies

`dnstt-server -udp :53 ...` binds 0.0.0.0:53. On most Ubuntu/Debian VPS,
`systemd-resolved` (or dnsmasq) already occupies port 53, so the systemd unit
starts, fails to bind, and keeps restarting with no obvious error to the user.

**Why:** two SlowDNS install attempts "looked correct" (matched a working
reference installer) but the tunnel never came up — the missing step was freeing
port 53, which neither the reference nor the first attempt did.

**How to apply:** before starting slowdns, disable resolved's stub listener
(`/etc/systemd/resolved.conf.d/*.conf` -> `[Resolve]\nDNSStubListener=no`),
rewrite `/etc/resolv.conf` to a real resolver (1.1.1.1/8.8.8.8) so name
resolution still works, `fuser -k 53/udp`, then restart resolved. After
`systemctl restart slowdns`, verify with `systemctl is-active` and surface a
warning pointing at `journalctl -u slowdns` if it's not active. Also open
UDP 53 in ufw. Client needs: NS domain, server.pub key, a public DNS resolver,
and a normal SSH account (SlowDNS just tunnels to 127.0.0.1:22).

**Do not let SlowDNS abort the installer.** The main script runs `set -e`. The
SlowDNS phase has unguarded fail-prone commands (apt, `git clone` bamsoftware,
`go build`); a non-zero exit there killed the whole install (left the box with
no `menu`). Wrap the entire phase in `set +e` … `set -e`. Toolchain: try apt
`golang-go` first, fall back to the official go.dev tarball (arch-aware) — apt's
Go can be too old to build current dnstt. Backend is `127.0.0.1:22` (OpenSSH),
matching the SSL-payload backend. Working impl landed after the errexit guard.

## Dependency versions can block the dnstt build

The user's VPS failure was subsequently confirmed as Go 1.19 lacking
`crypto/ecdh`, with no fallback compiler installed. Do not confuse a workspace
package-filter failure with the VPS root cause. Even the previously selected
dependency pins were later blocked here; a past successful build is not a
current security clearance.

**Why:** download errors were suppressed and a redirect response was mistaken
for verification of a successful compiler download.

**How to apply:** verify the full download and compiler execution, retain
fallback download/extraction logs, and isolate the temporary compiler rather
than deleting a server-wide Go installation.

Current dnstt source may pin old `golang.org/x/*` modules that security-aware
package proxies reject, even when the installed Go compiler is new enough.

**Why:** the build failed while fetching an old `x/crypto` release because the
package network blocked its known critical vulnerability. Since build output was
discarded, the installer only reported a generic failure.

**How to apply:** before building, upgrade the pinned `x/crypto`, `x/net`,
`x/sys`, and `x/text` modules to patched releases compatible with the fallback Go
toolchain. Keep the source build as the trusted path, validate the produced
binary, and preserve `/tmp/dnstt-build.log` when installation still fails.
