---
name: API-gated SSH login banner
description: User-approved scope and activation rules for the LitronSSH HTML banner
---
The user requested their exact HTML banner for SSH WebSocket, SSH SSL and
simple SSH. Banner activation belongs in the Website API menu, not general
server setup: successful new API activation enables it, while existing API
installations need an easy on/off option. Updates must preserve that choice.

**Why:** the user explicitly tied banner availability to API activation and
required leaving other protocols unchanged.

**How to apply:** use OpenSSH's authentication banner, never prepend HTML to
the SSH identification line or HTTP payload response. Validate before reload;
do not restart VPN services. Direct Dropbear ports and non-SSH protocols remain
unchanged. HTML display depends on the client; the advertised device limit is
text, not authorization to change enforcement.
