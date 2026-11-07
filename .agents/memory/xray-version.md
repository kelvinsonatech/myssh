---
name: Xray compatibility version
description: User-confirmed working Xray release and isolation requirement
---
Use Xray 26.3.27 rather than automatically selecting the latest release.
The user confirmed 26.3.27 (built with Go 1.26.1) browses correctly, while
26.10.29 does not in their setup.

**Why:** the user explicitly requested the confirmed working version, without
affecting other protocols or API settings.

**How to apply:** retain the version pin for fresh Xray installs. Do not silently
replace an existing running binary during menu updates or account creation.
An existing-server downgrade must be a separate, explicit operation, since
replacing the running Xray version requires restarting its shared protocols.
