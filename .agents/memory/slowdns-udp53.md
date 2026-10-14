---
name: SlowDNS isolation and toolchain policy
description: User scope constraints, safe UDP 53 ownership and upstream build pitfalls
---

# Scope and safety

The user confirmed the optional install works. Their subsequent UI requirement
is one SlowDNS action: animate setup, then display details; when already active,
show "Currently activated" and details without reinstalling or restarting.

**Why:** the user wants no intermediate install/status submenu or redundant
confirmation. Ask for an NS domain only when it has not already been configured.

The user explicitly requested optional SlowDNS installation from its submenu,
with no NS prompt during main setup, Go 1.27.1 minimum and animated progress.
They repeated: "just focus on the slow dns"; Hysteria 2 and the other protocols
are working alternatives and must retain their existing settings and behavior.

**Why:** old distro Go caused crypto/ecdh build failures. Removing distro Go or
replacing /usr/local/go could break unrelated software. Use a private toolchain
for this build rather than globally changing the server's Go or root profile.

**How to apply:** do not broaden SlowDNS maintenance into HY2 fixes or protocol
refactors. Compile before any port/service changes. Never kill unknown UDP 53
listeners. Respect the existing HY2 handover rather than creating another switch.

## UDP 53 and systemd-resolved

Wildcard dnstt needs port 53 free; an apparently started service may just be in a
restart loop. A resolved stub may be adjusted only with upstream DNS preserved
and a rollback copy of the original resolver file or symlink.

**Why:** previous instructions to fuser-kill port 53 or replace all nameservers
were unsafe for a multi-protocol VPS. They are superseded; do not reintroduce them.

**How to apply:** check actual listening state after startup, fail closed on
unknown listeners and redirect rules, and retain rollback backups after failure.

## Non-root resolver configuration under a private umask

Keep private keys protected, but explicitly make non-secret configuration
readable/traversable by the daemon account that consumes it. Restore original
directory permissions as well as files when a resolver handover rolls back.

**Why:** the user confirmed a root-owned mode-700 resolver configuration directory
on a server where resolved runs as systemd-resolve. The earlier SlowDNS installer
inherited umask 077; this predates ZIVPN. A successful resolver restart alone does
not prove it could read the instruction to release port 53.

**How to apply:** test SlowDNS deployment under umask 077 with both new and
pre-existing restricted resolver directories. Model non-root readability in
tests; a root-only test can conceal this failure. Retain the UDP-listener and
DNS-resolution checks, and never relax private-key permissions.

## Upstream source transport

Use a full clone of bamsoftware's dnstt repository, not a shallow clone.

**Why:** the upstream endpoint uses dumb HTTP; shallow clones fail with
"dumb http transport does not support shallow capabilities".

## Dependency versions can block the dnstt build

Current dnstt source may pin old `golang.org/x/*` modules that security-aware
package proxies reject, even when the installed Go compiler is new enough.

**Why:** the build failed while fetching an old `x/crypto` release because the
package network blocked its known critical vulnerability. Since build output was
discarded, the installer only reported a generic failure.

**How to apply:** before building, upgrade the pinned `x/crypto`, `x/net`,
`x/sys`, and `x/text` modules to patched releases compatible with the fallback Go
toolchain. Keep the source build as the trusted path, validate the produced
binary, and preserve build logs when installation fails.
