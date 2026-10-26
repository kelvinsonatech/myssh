---
name: V2Ray website sub accounts
description: How the API provisions VMess/VLESS/Trojan without ever touching Xray, and the accepted tradeoff
---
- Website V2Ray accounts are "sub accounts": they reuse the key of a main account the admin picks in menu → V2Ray → 7, with only the name/remark changed. The API never writes Xray files or restarts Xray; the sub account exists only in the API database.
- **Why:** user's own idea; they chose it over a one-time-restart Xray live-API switch after being told sub accounts can't be cut off individually (deleting/expiring one doesn't stop its user; only deleting the main account stops all).
- **How to apply:** keep Xray untouched by the API. Main accounts with a data quota are refused (quota exhaustion would drop all subs); sub expiry may not pass the main account's expiry (xray-check removes it at local midnight).
- The user stressed "don't change anything at the API side" — interpret as: no behaviour change for other protocols or the request/response contract; V2Ray-only additions are fine.
