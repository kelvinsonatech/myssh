---
name: Hetzner Debian apt startup
description: Why the installer can silently stop at the System packages phase on fresh Hetzner Debian servers
---

Never suppress all output from the initial package-index update while `set -e` is active. Wait for apt/dpkg locks, retry transient failures, and retry over IPv4 before stopping. After Debian 11's 2026-08-31 EOL, its final signed security metadata may also fail only because its `Valid-Until` timestamp expired.

**Why:** Fresh Hetzner Debian 11/12 instances may still be running apt-daily, have a temporarily unavailable mirror, or have IPv6 DNS/routing become ready after IPv4. Bullseye additionally has no maintainer left to renew its security metadata timestamp. A failed hidden `apt-get update` previously made the installer disappear at 8% with no diagnosis.

**How to apply:** Do not delete apt lock files. Wait for their owner to finish, use bounded retries/timeouts, retain the apt log, and show its final lines if all attempts fail. Only when the OS is Bullseye and apt explicitly reports an expired Release file, retry with `Acquire::Check-Valid-Until=false`; keep signature verification and never apply that exception to Debian 12+.