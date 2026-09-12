---
name: Hetzner Debian apt startup
description: Why the installer can silently stop at the System packages phase on fresh Hetzner Debian servers
---

Never suppress all output from the initial package-index update while `set -e` is active. Wait for apt/dpkg locks, retry transient failures, and retry over IPv4 before stopping.

**Why:** Fresh Hetzner Debian 11/12 instances may still be running apt-daily, have a temporarily unavailable mirror, or have IPv6 DNS/routing become ready after IPv4. A failed hidden `apt-get update` previously made the installer disappear at 8% with no diagnosis.

**How to apply:** Do not delete apt lock files. Wait for their owner to finish, use bounded retries/timeouts, retain the apt log, and show its final lines if all attempts fail. This phase runs before any protocol changes.