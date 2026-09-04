---
name: Installer phase isolation
description: Keep one protocol failure from terminating all later installation phases
---

Network package operations and service enable/restart operations must be bounded
and handled as phase-local failures. They should warn and continue rather than
letting global errexit terminate the installer.

**Why:** the progress UI made failures look like hangs because the shell prompt
returned on the same progress line whenever an unguarded command exited at SSL
or Stunnel. The same pattern existed in other phases.

**How to apply:** guard apt and systemd operations, keep SlowDNS best-effort,
avoid restarting already healthy services, and run one final health pass that
retries only inactive core services and reports any that still need attention.