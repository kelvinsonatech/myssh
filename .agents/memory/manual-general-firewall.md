---
name: Manual general firewall
description: Safety rules for controlling the server's general UFW firewall
---

The general UFW firewall must never be activated automatically during installation or service setup. It is explicitly enabled or disabled by the administrator from the management menu.

**Why:** Automatic firewall activation can lock users out or interrupt otherwise working SSH, SSL, WebSocket, SlowDNS, and Xray connections.

**How to apply:** Before manual activation, add the active SSH port and all installed service ports, then enable UFW. Disabling UFW must not stop or alter protocol services. Protocol installers may add a needed rule only if UFW is already active; they must never activate it.