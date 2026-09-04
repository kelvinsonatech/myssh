---
name: SSL certificate phase reliability
description: Prevent certificate acquisition from freezing the whole installer
---

Certificate package installation and ACME validation must always have hard time
limits. Reuse an existing valid Let's Encrypt certificate on re-runs; if
installation or validation fails or times out, generate a non-interactive
self-signed certificate instead.

**Why:** the installer can appear permanently stuck at “SSL certificate” while
an apt mirror or Certbot HTTP validation waits without a useful visible error.

**How to apply:** keep certificate commands non-interactive and bounded, retain
the existing domain-success behavior, and restart the WebSocket proxy before
failing if even the local fallback cannot be generated.

The following Stunnel phase must also be non-fatal. On re-runs, stop Xray only
when it is confirmed to own TCP 443, make one bounded package retry if Stunnel
is absent, and report restart failures without aborting later install phases.