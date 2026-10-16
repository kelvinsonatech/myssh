---
name: Node API nondisruption requirement
description: Website API account operations must not restart shared tunnels; feasibility findings and migration limits
---

The user requires website-driven account creation without disconnecting any
existing user, across all supported protocols. Do not fall back to restarting a
shared tunnel process when live account changes are unavailable.

The user explicitly chose **no VPN restarts at all**, including one-time setup
and scheduled maintenance, after being told that the revised contract permits
setup restarts and a daily ZIVPN password-pool refresh. Do not implement either
exception without new explicit authorization.

API expiry must represent full 24-hour days from creation in UTC, not midnight
rounding. Only API-created accounts are in scope; leave manual-menu expiry alone.
Linux expiry is the UTC calendar date after the exact API deadline as a safety
net, and the website remains responsible for DELETE at the precise deadline.

**Why:** the user explicitly requested full-duration API accounts without early
native expiration or changes to other protocol/user behavior.

**How to apply:** preserve exact deadlines on creation, renewal and replay;
keep node-managed expiry disabled and never add a service restart to enforce it.

Optional API prerequisite repair must not use distro package installation
automatically: package hooks can restart unrelated services. Use isolated
temporary, checksum-verified Python tooling when system pip is missing.

**Why:** minimal Debian/Ubuntu VPS images may include Python without pip;
fixing that prerequisite must still honor the no-restarts requirement.

**Why:** the user explicitly rejected the restart-based approach after supplying
the website API contract. A technically compatible JSON API is insufficient if
provisioning disconnects customers.

**How to apply:** implement only verified live-update adapters; fail before
mutating protocol files where this prerequisite is not met. Distinguish ongoing
no-restart account changes from initially converting a running static-auth
service. Do not imply that the initial conversion is automatically nondisruptive.

An isolated test of Hysteria 1 external HTTP authentication successfully added a
new credential and carried traffic while an existing TCP stream stayed open,
without restarting the server. External authentication was configured BEFORE
startup. This does not prove live migration from password-list authentication.

The actual pinned ZIVPN binary rejected both the Hysteria 1 external-auth schema
and attempted Hysteria 2 HTTP-auth configurations, with mixed Mode/type/certificate
validation messages. Compiled HTTP-auth symbols alone are not evidence that a
particular configuration is supported. Do not substitute stock Hysteria or claim
ZIVPN live updates are verified; actual client/session testing remains necessary.

**Why:** previous stock-Hysteria compatibility tests already misled this project.
The user confirmed their existing native ZIVPN setup works and wants it preserved.

**How to apply:** obtain/verify the binary's supported live-auth interface before
enabling automatic ZIVPN provisioning. SSH and the existing Hysteria 2 command
auth can read new credentials without shared restarts; Xray's runtime HandlerService
requires addressable, tagged tunnel inbounds, not merely its API listener tag.
