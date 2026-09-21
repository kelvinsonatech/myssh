---
name: Android payload compatibility
description: Confirmed client handshake compatibility and limits on startup tuning
---
Preserve interim HTTP response handling and exact SSH banner boundaries when
tuning the Android client's connection startup.

**Why:** the user confirmed the patched APK connected on their real device,
then requested faster startup without losing any protocols. That confirmation
does not establish a connection-time benchmark or validate subsequent tuning.

**How to apply:** keep speed changes separate from payload semantics. Do not
shorten explicit split delays or remove reconnect backoff just to appear faster.
Report compiler/handshake-test results separately from real-device performance.