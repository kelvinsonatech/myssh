---
name: Server domain changes
description: Scope and safety rules for the user-requested domain-change menu
---
The user requested a main-menu domain change without altering protocols or API
settings beyond what is necessary for the hostname switch.

**Why:** reinstalling the server or rebuilding its protocol configurations risks
disrupting existing accounts and port assignments.

**How to apply:** change domain/certificate references only. Preserve accounts,
ports, keys, limits, 443 ownership, SlowDNS delegation and independent API
hostnames. Obtain a valid certificate before committing the switch, warn about
brief interruptions and client/website updates, and restore previous files if
service checks fail. Never regenerate Xray protocol configuration for this action.
