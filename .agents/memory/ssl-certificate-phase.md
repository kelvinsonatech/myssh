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

Keep the Stunnel certificate and private key as separate validated files, and
verify that their public keys match before starting the service. The Xray port
443 handover must preserve both `cert` and `key` settings when it rewrites the
configuration.

**Why:** old combined or malformed PEM bundles, mismatched renewed keys, and
stale PID files can leave Stunnel inactive even though certificate acquisition
appeared successful.

**How to apply:** remove stale PID files before restart; if ports 443 and 447
are free and startup still fails, generate one fresh self-signed pair and retry
once. If it still fails, report occupied listeners and the latest relevant
service error without killing unknown processes.