---
name: SlowDNS isolation and toolchain policy
description: User scope constraints, safe UDP 53 ownership and upstream build pitfalls
---

# Scope and safety

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
