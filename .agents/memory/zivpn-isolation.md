---
name: ZIVPN integration boundaries
description: Actual upstream binary requirement, scope limits and evidence needed for compatibility
---

The user explicitly approved adding a ZIVPN submenu but repeated that existing
protocols and settings must not change. Integrate the actual upstream ZIVPN
binary, not stock Hysteria with similar configuration.

**Why:** the previous stock-Hysteria approach passed local tests but failed from
the actual Android app. User-provided upstream documentation confirms the shell
installer only configures a downloaded binary; it does not implement the tunnel.

**How to apply:** keep ZIVPN files, service and firewall ownership separate.
Refuse conflicting UDP listeners/redirects, including inactive HY2 custom-port
reservations. Do not run upstream zi.sh: it upgrades system packages and sets
global network buffers. Preserve other protocols' code and behavior.

The upstream release labelled udp-zivpn_1.4.9 reports Version:1.5.0 with `-h`.
It exits unsuccessfully for `--version` and `version`.

**Why:** release tag and runtime banner do not agree; rejecting that exact binary
based solely on the banner would prevent the user-requested implementation.

**How to apply:** verify the pinned archive asset fingerprint and use `-h` for
the executable smoke check. A local startup/binding check is not proof of Android
authentication or internet traffic. The user confirmed this upstream-binary
ZIVPN integration works on 2026-10-10; this is user confirmation, not an
agent-run Android test. The binary is closed; a pinned hash is not a source audit
or a publisher signature.

Passwords are application credentials, not Linux accounts. Do not infer account
expiry support from having a date field in a menu.

**Why:** password reload/disconnect semantics must be established first. For now
changes require an explicitly confirmed ZIVPN-only restart, with no auto-expiry
claim and no restart of other services.

Keep ZIVPN details concise and readable; omit the isolation-from-other-protocols
caption. Blank password input should use the user-chosen fixed default rather
than generate random credentials, while still accepting custom input. Do not
change existing credentials when adjusting this default.

**Why:** the user explicitly requested these presentation/default changes after
confirming the integration works.

**How to apply:** limit such changes to ZIVPN prompts and presentation; retain
port guards, firewall ownership and existing protocol settings.
