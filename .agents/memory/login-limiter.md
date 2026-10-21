---
name: Multi-login limiter
description: Rules for the optional SSH/SSL/SlowDNS multi-login limiter that lives only in the Website API menu
---
- The user wants it reachable ONLY from the Website API menu (17 → 7), never from the main menu, with an on/off switch, an adjustable limit (default 4) and a polished animation.
- Scope: SSH, SSH-SSL, SlowDNS (OpenSSH + Dropbear sessions). Xray/HY1/HY2/ZIVPN are excluded.
- Canonical engine is scripts/login-limiter.py; the menu embeds it verbatim in a quoted heredoc (tests enforce they match), so edit the .py and re-embed it.
- It runs as its own service; activation restarts only that service, never a VPN service.
- Policy: close the OLDEST sessions after a grace period; repeated strikes lock the account; delayed deletion. Website (API) accounts are expired with chage, not deleted with userdel, so the API record stays valid.
- **Why:** the user repeatedly said "don't break or touch the protocols". Reconnect overlap must never punish real users.
- **How to apply:** recommend watch-only mode first. The website is not told about locks or deletions; mention this if they ask.
