# Prompt: add a web API to your VPN install script

**How to use this file:** open the AI tool you used to build your VPN install script. Give it your
script files (all of them: the installer, menu, add/delete/renew helpers, cron jobs, config
templates), then paste everything below the line as your message. When it is done, run the tests in
section 10 on a test VPS, and only then add the server in the website's admin panel (section 11).

The website side is already built and expects exactly this API, so follow the formats literally:
field names, headers, status codes and error codes. `mock-node/mock-node.ts` in this repository is
a working reference implementation of the same API in TypeScript; give it to your AI too if it can
read files.

---

## Task

Add a small **HTTP API service** (I'll call it the "node agent") to my existing VPN install script.
It runs on every VPS where my script is installed. My central website calls it to create, look up,
renew and delete VPN/SSH accounts and to check the server's health.

My script supports these protocols: **SSH, SSH SSL (with payload), SlowDNS, VMess, VLESS, Trojan,
Hysteria 1, Hysteria 2 and ZIVPN UDP**.

### Golden rules (all must hold)

1. **Never disconnect existing users.** Creating, renewing, deleting or expiring an account must
   **never restart, stop or reload** a VPN service (sshd, dropbear, stunnel, the WebSocket proxy,
   SlowDNS/dnstt, xray/v2ray, hysteria, hysteria2, zivpn). Every user who is connected stays
   connected. Section 2 explains how to do this for each protocol. This rule wins over everything
   else in my script: if my script currently restarts a service after adding a user, replace that
   step with the live method from section 2.
2. **Keep my script's data, change only the mechanics.** Keep my script's files, formats and user
   records so the existing menu keeps working. Move the account logic out of the interactive menus
   into non-interactive commands or functions that take arguments and return JSON; the menu and the
   agent must both call the same functions. Make the menu use the same no-restart methods.
3. **No shell injection.** The agent must never run a shell command built from request data. Call
   commands with an argument list (Python `subprocess.run([...], shell=False)`, Go `exec.Command`).
   Never use `shell=True`, `eval`, `bash -c "<string>"`, or `sed` patterns built from user input.
   Edit JSON/YAML config files by loading, changing and saving them with a real parser.
4. **Only the endpoints in section 7.** No endpoint may accept a command, script, file path or any
   other free-form instruction.
5. **Signed requests only.** Every request must carry a valid HMAC signature (section 4). Each server
   has its own secret.
6. **No secrets in logs.** Never log passwords, UUIDs, private keys, the API secret or full configs.

**Technology:** Python 3 with FastAPI + uvicorn (or Go if my script already uses Go), running as the
systemd service `vpn-node-api.service` with `Restart=always`. Keep agent state (accounts, request
ids, nonces) in SQLite under `/var/lib/vpn-node-api/`, and the config in
`/etc/vpn-node-api/config.json` with file mode `600`. Use one global lock (for example `flock` on
`/run/vpn-node-api.lock`) around every change to users or config files, so two requests never edit
the same file at once. Write files atomically: write a temporary file, then rename it over the
original.

---

## 1. Installation (add to my install script)

- Install the agent and its dependencies, create and enable `vpn-node-api.service`.
- The API listens on a configurable port, default **9443**. Check it does not clash with ports my
  VPN services already use.
- **TLS (HTTPS) is required.** If my script already has a certificate for the server's domain (for
  example `/etc/xray/xray.crt` and `/etc/xray/xray.key`), use it. Otherwise generate a self-signed
  certificate and print its **SHA-256 fingerprint** (I enter it in the website as a pinned
  fingerprint).
- Generate the credentials once: a **key id** (for example `node-<random 8 hex>`) and a random
  **secret** (64 hex characters). Save both in the config file and print them **once** at the end of
  the installation, together with the port and base path, so I can paste them into the website.
- Add a firewall rule (ufw or iptables) that allows the API port **only from my website's IP**. Ask
  for that IP during installation, or read it from a variable.
- Set up everything section 2 needs: the Xray API, Hysteria auth backends, and so on. These are the
  only service restarts allowed, once during installation or an upgrade.
- **Remove or disable restarts from cron.** Many scripts restart xray or reboot the VPS from cron
  (for example after cleaning expired users, or a daily reboot). Remove those, or ask me before
  keeping one. Expiry cleanup must use the live methods in section 2.
- Add a menu entry to show the API status and credentials, and one to rotate the secret.

## 2. Never disconnect existing users: how to change users live

Do this for every account change: create, renew, delete, expire.

### SSH, SSH SSL (payload) and SlowDNS

These use normal Linux users. sshd, dropbear, stunnel, the WebSocket proxy and dnstt check the
password on every new login, so **no service needs a restart or reload**.

- **Create:** `useradd -M -s /bin/false -e <YYYY-MM-DD> <username>`, then set the password by
  piping `username:password` into `chpasswd` on stdin. Never put the password in the command line.
  Add the user to my script's own user list if it keeps one.
- **Renew:** `chage -E <new YYYY-MM-DD> <username>`, and `usermod -U` if the account was locked.
- **Delete or expire:** `usermod -L <username>` and `chage -E 0 <username>`. Then end **only that
  user's** sessions: `pkill -KILL -u <username>` for OpenSSH sessions, and for Dropbear find that
  user's session PIDs the way my script's multi-login checker does (from the auth log) and kill only
  those PIDs. Then `userdel <username>`. Never restart sshd, dropbear or stunnel.

### VMess, VLESS and Trojan (Xray)

Xray cannot reload its config file without a restart, but it can add and remove users **while
running** through its API (HandlerService).

- **One-time setup during installation:** enable the API in the Xray config:
  `"api": {"tag": "api", "services": ["HandlerService", "StatsService"]}`. Expose it on localhost
  only:
  - with `"listen": "127.0.0.1:10085"` inside the `api` object if my Xray version supports it;
  - otherwise the classic way: a `dokodemo-door` inbound on `127.0.0.1:10085` with tag `api`, plus a
    routing rule `{"type": "field", "inboundTag": ["api"], "outboundTag": "api"}`.

  Give every VMess/VLESS/Trojan inbound a unique `tag` (for example `vless-ws`, `vless-grpc`,
  `vmess-ws`, `trojan-ws`). Use a recent Xray-core that has the `xray api adu` and `xray api rmu`
  commands (check with `xray help api adu`); upgrade Xray at install time if it does not.
- **Create:** give each account a unique `email` (use the `account_id`, for example
  `acc_8f2c41d9`). Write a small JSON file listing the tagged inbounds of that protocol with the new
  client, then run `xray api adu --server=127.0.0.1:10085 <file.json>`. Example file for VLESS:

  ```json
  {"inbounds": [
    {"tag": "vless-ws", "protocol": "vless",
     "settings": {"clients": [{"id": "<uuid>", "email": "acc_8f2c41d9"}], "decryption": "none"}},
    {"tag": "vless-grpc", "protocol": "vless",
     "settings": {"clients": [{"id": "<uuid>", "email": "acc_8f2c41d9"}], "decryption": "none"}}
  ]}
  ```

  **Also** add the same client to `/etc/xray/config.json` (or wherever my script keeps it), so it
  survives a reboot. Write the file but **do not restart Xray**.
- **Delete or expire:** for each inbound tag of that protocol run
  `xray api rmu --server=127.0.0.1:10085 -tag=<tag> <email>`, then remove the client from the config
  file, again without restarting.
- **Renew:** Xray has no per-user expiry, so only the agent's database changes. If the account had
  already been removed, add it back with `adu`.
- If an `adu`/`rmu` call fails, undo the config-file change and return `PROVISIONING_FAILED`.

### Hysteria 2

Use Hysteria 2's **HTTP authentication** so passwords are checked by the agent instead of being
listed in the config file. Adding or removing a user then needs no restart at all.

- **One-time setup** in the Hysteria 2 server config:

  ```yaml
  auth:
    type: http
    http:
      url: http://127.0.0.1:9440/auth/hysteria2
  trafficStats:
    listen: 127.0.0.1:9441
    secret: <random secret>
  ```

- The agent runs a second listener on **127.0.0.1:9440 only** (never public). For each new client
  connection, Hysteria 2 sends `POST /auth/hysteria2` with JSON
  `{"addr": "1.2.3.4:5678", "auth": "<what the client sent>", "tx": 0}`. Look the `auth` value up
  among this server's active, non-expired Hysteria 2 accounts and reply HTTP **200** with
  `{"ok": true, "id": "<account_id>"}`, or `{"ok": false, "id": ""}` when it is not valid.
  Answer from SQLite or an in-memory cache and keep it fast.
- **Create, renew, delete:** change the account in the agent's database. Nothing else.
- **Delete or expire:** also disconnect just that user right away with
  `POST http://127.0.0.1:9441/kick` and body `["<account_id>"]`, header `Authorization: <secret>`
  (the secret itself, no "Bearer").

### Hysteria 1

Hysteria 1 supports **external authentication** the same way.

- **One-time setup** in the Hysteria 1 server config:
  `"auth": {"mode": "external", "config": {"http": "http://127.0.0.1:9440/auth/hysteria1"}}`
- Hysteria 1 sends `POST /auth/hysteria1` with JSON
  `{"addr": "1.2.3.4:5678", "payload": "<base64 of the client's auth string>", "send": 0, "recv": 0}`.
  Decode `payload`, check it against active Hysteria 1 accounts, and **always** answer HTTP 200 with
  `{"ok": true, "msg": "ok"}` or `{"ok": false, "msg": "invalid"}`. Any status other than 200 counts
  as an internal error.
- **Create, renew, delete:** change the agent's database only; no restart. A deleted user cannot
  start a new connection. Hysteria 1 has no kick API, so an already-open session ends when it next
  reconnects.

### ZIVPN UDP

The common ZIVPN server (`/usr/local/bin/zivpn server -c /etc/zivpn/config.json`) is built on
Hysteria and keeps users as a password list (`"auth": {"mode": "passwords", "config": [...]}`).
Changing that list needs a restart, which would disconnect everyone. So:

1. **First try external authentication.** Run a second, temporary zivpn instance on a spare port
   with `"auth": {"mode": "external", "config": {"http": "http://127.0.0.1:9440/auth/zivpn"}}`
   (same request/response format as Hysteria 1 above). Connect with the ZIVPN app using a test
   password. If the agent receives the auth request and the login works, switch the real instance to
   external auth during installation. From then on accounts change with no restarts, exactly like
   Hysteria 1.
2. **If external auth is not supported by my zivpn build, use a password pool.** At installation and
   once a day at a fixed low-traffic time (ask me for the time, default 05:00 server time), fill the
   list with enough unused random passwords for a day of new accounts (default 300), remove expired
   passwords, and restart zivpn **only at that one scheduled time**. A new account takes an unused
   password from the pool, so creating accounts never restarts zivpn.
   - Expired or deleted ZIVPN passwords keep working until the next scheduled refresh. Tell me about
     this limit.
   - If the pool ever runs empty, return `CAPACITY_REACHED`. Never restart outside the schedule.
3. In both cases keep my script's own record of ZIVPN users (name, password, expiry) up to date.

### Expiry

The agent expires accounts itself, with a systemd timer that runs every minute and applies the
"delete or expire" steps above for each protocol. Report `"expiry_managed_by_node": true` in `/info`.
Replace any old expiry cron job from my script that restarted services.

## 3. Transport

- Base URL: `https://<server-hostname>:<port>/api/v1`
- Requests and responses are JSON (`Content-Type: application/json`). Reject request bodies larger
  than 64 KB.
- No redirects. Reply within 30 seconds; the website waits up to 45 seconds for account creation.

## 4. Authentication (HMAC-SHA256), required on every endpoint

The website sends these headers:

| Header | Value |
| --- | --- |
| `X-Node-Key-Id` | the key id from the config |
| `X-Node-Timestamp` | Unix time in seconds |
| `X-Node-Nonce` | random string, 32 hex characters (accept `[A-Za-z0-9_-]{16,128}`) |
| `X-Node-Signature` | `v1=` followed by the hex HMAC-SHA256 |
| `X-Request-Id` | a UUID for tracing (log it) |
| `Idempotency-Key` | the request id, on create / renew / delete |

The signature is `HMAC-SHA256(key = secret as UTF-8 text, message = canonical string)`, written as
lowercase hex. The canonical string is these six lines joined with `\n` (no newline at the end):

```
VL1-HMAC-SHA256
<HTTP method in upper case, e.g. POST>
<path exactly as sent, including /api/v1 and the query string, e.g. /api/v1/accounts?protocol=vless>
<the X-Node-Timestamp value>
<the X-Node-Nonce value>
<lowercase hex SHA-256 of the raw request body bytes; for an empty body the SHA-256 of "">
```

Verification order and the error to return (all with HTTP 401):

1. A header is missing, the timestamp is not digits, or the key id is unknown → `UNAUTHORIZED`.
2. The timestamp is more than **300 seconds** away from the server clock → `CLOCK_SKEW` (the server
   must run NTP).
3. The signature does not match → `INVALID_SIGNATURE`. Compare in constant time
   (`hmac.compare_digest`).
4. The nonce was already used in the last 10 minutes → `REPLAYED_NONCE`. Remember nonces only after
   the signature has been verified.

The local auth listener from section 2 (127.0.0.1:9440) is the only exception: it is called by
Hysteria/ZIVPN on the same machine, is never reachable from outside, and has no signature.

## 5. Response format

Success: `{"ok": true, "data": { ... }}`

Error: `{"ok": false, "error": {"code": "USERNAME_TAKEN", "message": "Username already exists", "retryable": false}}`

An error response means **nothing was changed**: no account was created or modified.

| Code | HTTP | When |
| --- | --- | --- |
| `UNAUTHORIZED`, `INVALID_SIGNATURE`, `CLOCK_SKEW`, `REPLAYED_NONCE` | 401 | authentication failed |
| `VALIDATION_FAILED` | 422 | bad input |
| `PROTOCOL_UNAVAILABLE` | 409 | protocol not installed or its service is not running |
| `USERNAME_TAKEN` | 409 | username already exists |
| `CAPACITY_REACHED` | 409 | the server's own limit is reached (also an empty ZIVPN pool) |
| `ACCOUNT_NOT_FOUND` | 404 | unknown account id or request id |
| `IDEMPOTENCY_CONFLICT` | 409 | same request id reused with a different payload |
| `REQUEST_IN_PROGRESS` | 409 (`retryable: true`) | the same request id is still being processed |
| `NOT_SUPPORTED` | 501 | an optional endpoint is not implemented |
| `PROVISIONING_FAILED`, `INTERNAL` | 500 | the operation failed (and was rolled back) |

## 6. Protocol identifiers

Report each installed protocol with exactly these ids:

| Protocol in my script | Use this id | Accounts are |
| --- | --- | --- |
| SSH (OpenSSH / Dropbear, plus WebSocket ports if my script has them) | `ssh` | Linux users |
| SSH SSL with payload (stunnel / TLS + HTTP payload for HTTP Injector, HTTP Custom ...) | `ssh-ssl` | Linux users |
| SlowDNS (DNSTT) | `slowdns` | Linux users |
| VMess (Xray) | `vmess` | Xray clients |
| VLESS (Xray) | `vless` | Xray clients |
| Trojan (Xray) | `trojan` | Xray clients |
| Hysteria 1 | `hysteria` | agent database (external auth) |
| Hysteria 2 | `hysteria2` | agent database (HTTP auth) |
| ZIVPN UDP | `zivpn` | agent database, or password pool |

Only report protocols that are actually installed on that server. If my script has protocols that
are not in this table, report them with a short lowercase id and tell me which ones.

## 7. Endpoints

### `GET /api/v1/info` (required)

```json
{"ok": true, "data": {
  "api_version": "1.0",
  "agent_version": "1.0.0",
  "status": "online",
  "hostname": "de1.example.com",
  "public_ip": "203.0.113.10",
  "country": "DE",
  "server_time": "2026-10-14T12:00:00Z",
  "protocols": ["ssh", "ssh-ssl", "slowdns", "vmess", "vless", "trojan", "hysteria", "hysteria2", "zivpn"],
  "capabilities": {
    "idempotency": true,
    "lookup_by_request_id": true,
    "renew": true,
    "list_accounts": true,
    "restart_agent": false,
    "expiry_managed_by_node": true
  }
}}
```

`status` is `online`, `degraded` or `maintenance`.

### `GET /api/v1/health` (required)

```json
{"ok": true, "data": {
  "status": "online",
  "uptime_seconds": 86400,
  "cpu_percent": 12.5,
  "memory": {"used_bytes": 812000000, "total_bytes": 2048000000},
  "disk": {"used_bytes": 9400000000, "total_bytes": 40000000000},
  "load": [0.21, 0.18, 0.12],
  "services": {"ssh": "running", "dropbear": "running", "stunnel4": "running", "xray": "running",
               "hysteria": "running", "hysteria2": "running", "zivpn": "running", "slowdns": "running"}
}}
```

Service values are `running`, `stopped` or `unknown`. Use `systemctl is-active` on the service names
my script actually installs; the names above are examples. This endpoint only reads; it never
restarts anything.

### `GET /api/v1/protocols` (recommended)

```json
{"ok": true, "data": {"protocols": [
  {"id": "vless", "installed": true, "running": true,
   "ports": {"tls": [443], "none": [80]}, "transports": ["ws", "grpc"],
   "current_accounts": 20, "max_accounts": null, "metadata": {}},
  {"id": "hysteria2", "installed": true, "running": true, "ports": {"udp": [8443]}},
  {"id": "zivpn", "installed": true, "running": true, "ports": {"udp": "6000-19999"}}
]}}
```

`ports` values are a list of numbers or a range string such as `"6000-19999"`.

### `POST /api/v1/accounts`: create an account (required)

Request from the website:

```json
{
  "request_id": "6c3f1e2a-8d4b-4c1e-9a7f-2b5d8e9c0a11",
  "protocol": "vless",
  "username": "traveller",
  "password": "S3cure-pass",
  "duration_days": 7,
  "options": {}
}
```

- `protocol` is one of the ids this server reported.
- Which fields the website sends:
  - `ssh`, `ssh-ssl`, `slowdns`: `username` and `password` (one Linux user works on all SSH ports).
  - `vmess`, `vless`, `trojan`: `username` only (used as the account name/remark). Generate the
    UUID (VMess/VLESS) or password (Trojan) on the server and return it.
  - `hysteria`, `hysteria2`, `zivpn`: `username` (account name) and optionally `password`. If no
    password is sent, generate a strong one (or take one from the ZIVPN pool) and return it in
    `config.password`.
- **Validate again on the server:**
  - username: `^[a-z][a-z0-9_]{2,31}$`; refuse system names such as `root`, `admin`, `daemon`, `bin`,
    `sys`, `nobody`, `sshd`, `www-data`, `ubuntu`, `debian`, and any existing Linux user that was not
    created by the agent;
  - password: `^[A-Za-z0-9@#%^*_+=.,:~-]{6,32}$`;
  - `duration_days`: an integer from 1 to 365.

  Anything else → `VALIDATION_FAILED`, with nothing executed.
- **Idempotency (very important):** store every `request_id` with a hash of the payload and the
  result for at least 7 days.
  - Same `request_id` and same payload again → return the **same** account with `"replayed": true`
    and HTTP 200. **Never create a second account.**
  - Same `request_id` with a different payload → `IDEMPOTENCY_CONFLICT`.
  - Same `request_id` while the first attempt is still running → `REQUEST_IN_PROGRESS`.
- Use the global lock, apply the live method from section 2, and **never restart a service**. If any
  step fails, undo the steps already done and return `PROVISIONING_FAILED`.
- If my script adds a prefix to usernames, return the final username it actually created.

Response: HTTP 201 when created, HTTP 200 when replayed:

```json
{"ok": true, "data": {"replayed": false, "account": {
  "account_id": "acc_8f2c41d9",
  "request_id": "6c3f1e2a-8d4b-4c1e-9a7f-2b5d8e9c0a11",
  "protocol": "vless",
  "username": "traveller",
  "status": "active",
  "created_at": "2026-10-14T12:00:00Z",
  "expires_at": "2026-10-21T12:00:00Z",
  "config": { "...": "protocol-specific values, see section 8" }
}}}
```

`account_id` is a stable id you generate. Use letters, digits, `_` and `-` only.

### `GET /api/v1/accounts/by-request/{request_id}` (required)

Returns `{"account": {...}}` (same shape as above, including `config`) for the account created by
that request id, or 404 `ACCOUNT_NOT_FOUND`. The website calls this after a timeout to find out
whether the account was created before it retries.

### `GET /api/v1/accounts/{account_id}`

Returns `{"account": {...}}` (`config` may be omitted), or 404.

### `DELETE /api/v1/accounts/{account_id}`

Deletes the account with the live method from section 2 (no restarts) → `{"revoked": true}`. An
account that does not exist or was already deleted → 404 `ACCOUNT_NOT_FOUND` (the website treats that
as success).

### `POST /api/v1/accounts/{account_id}/renew` (optional)

Body `{"request_id": "...", "duration_days": 7}` → `{"account": {...}}` with the new `expires_at`.
Apply the same idempotency rule to `request_id`, and no restarts.

### `GET /api/v1/accounts?protocol=vless&cursor=&limit=100` (optional)

→ `{"accounts": [...], "next_cursor": null}`. Paginate with your own cursor string.

### `GET /api/v1/stats` (optional)

```json
{"ok": true, "data": {"accounts_total": 76, "accounts_by_protocol": {"ssh": 30, "vless": 20}, "online_users": 12}}
```

## 8. The `config` object per protocol

These are the keys the website reads. Fill in what my script produces and leave out keys that do not
apply. Ports are a number, a list of numbers (`[443, 8443]`) or a range string (`"6000-19999"`).
Always include `host` (the domain users connect to) and, where useful, `ip`.

**`ssh`: SSH (OpenSSH / Dropbear / WebSocket):**

```json
{"host": "de1.example.com", "ip": "203.0.113.10", "username": "traveller", "password": "S3cure-pass",
 "ports": {"openssh": [22], "dropbear": [109, 143], "ws": [80], "ws_tls": [443]},
 "payload": "GET / HTTP/1.1[crlf]Host: [host][crlf]Upgrade: websocket[crlf][crlf]"}
```

**`ssh-ssl`: SSH SSL with payload:**

```json
{"host": "de1.example.com", "ip": "203.0.113.10", "username": "traveller", "password": "S3cure-pass",
 "ports": {"ssl": [443, 777], "ws_tls": [443]},
 "sni": "de1.example.com",
 "payload": "GET wss://de1.example.com/ HTTP/1.1[crlf]Host: [host][crlf]Upgrade: websocket[crlf][crlf]"}
```

Use the payload template(s) my script already shows to users. If there are several, add them as
`"payloads": [{"name": "WS TLS", "value": "..."}, ...]`.

**`slowdns`: SlowDNS / DNSTT:**

```json
{"host": "de1.example.com", "ip": "203.0.113.10", "username": "traveller", "password": "S3cure-pass",
 "ports": {"slowdns": [53, 5300], "openssh": [22]},
 "slowdns": {"nameserver": "ns-de1.example.com", "public_key": "<dnstt server public key>"}}
```

**`vmess`, `vless`, `trojan`:**

```json
{"host": "de1.example.com", "remark": "traveller",
 "uuid": "<uuid for vmess/vless>", "password": "<password for trojan>", "alter_id": 0, "flow": "",
 "connections": [
   {"name": "WS TLS", "network": "ws", "security": "tls", "port": 443, "path": "/vless", "host_header": "de1.example.com", "sni": "de1.example.com"},
   {"name": "WS", "network": "ws", "security": "none", "port": 80, "path": "/vless", "host_header": "de1.example.com"},
   {"name": "gRPC", "network": "grpc", "security": "tls", "port": 443, "service_name": "vless-grpc", "sni": "de1.example.com"}
 ],
 "links": [{"name": "WS TLS", "uri": "vless://...full share link..."}]}
```

Include one entry in `connections` per transport (inbound tag) the account was added to. If my
script already builds share links (`vmess://`, `vless://`, `trojan://`), put them in `links`; the
website shows them with QR codes.

**`hysteria`: Hysteria 1:**

```json
{"host": "de1.example.com", "ip": "203.0.113.10", "port": 36712,
 "protocol": "udp", "auth": "<the account's auth string>", "obfs": "xplus", "obfs_password": "<obfs password or empty>",
 "up_mbps": 100, "down_mbps": 100, "alpn": "hysteria", "sni": "de1.example.com", "insecure": true,
 "uri": "hysteria://de1.example.com:36712?protocol=udp&auth=...&peer=de1.example.com&insecure=1&upmbps=100&downmbps=100&alpn=hysteria#traveller"}
```

Use my server's real values. `uri` follows the Hysteria 1 share-link format: host, port, `upmbps`
and `downmbps` are required; `auth`, `peer` (SNI), `insecure`, `alpn`, `obfs` and `obfsParam` are
optional. Leave out what my server does not use.

**`hysteria2`: Hysteria 2:**

```json
{"host": "de1.example.com", "ip": "203.0.113.10", "port": 8443, "password": "<the account's password>",
 "obfs": {"type": "salamander", "password": "<obfs password>"}, "sni": "de1.example.com", "insecure": false,
 "uri": "hysteria2://<url-encoded password>@de1.example.com:8443/?sni=de1.example.com&obfs=salamander&obfs-password=...&insecure=0#traveller"}
```

`uri` follows the official Hysteria 2 URI scheme. URL-encode the password. Include
`obfs`/`obfs-password` only when obfuscation is enabled, and `pinSHA256` (lowercase hex) only when
certificate pinning is used.

**`zivpn`: ZIVPN UDP:**

```json
{"host": "de1.example.com", "ip": "203.0.113.10", "password": "<the account's ZIVPN password>",
 "ports": {"udp": "6000-19999"}, "obfs": "zivpn"}
```

Return the values the ZIVPN app needs to connect: server address, password and UDP port range.

## 9. Security checklist (must all be true)

- Every endpoint verifies the signature before doing anything else.
- Only the endpoints above exist; there is no command, script or file endpoint.
- The local auth listener (127.0.0.1:9440) and the Xray and Hysteria stats APIs listen on 127.0.0.1
  only.
- Account commands use argument lists, never shell strings; passwords go through stdin (`chpasswd`),
  never the command line; config files are edited with a JSON/YAML parser, never with `sed`.
- Usernames and passwords are validated with the patterns in section 7 before use.
- Request ids are stored for at least 7 days; replays never create a second account.
- The secret and private keys are only in `/etc/vpn-node-api/config.json` (mode 600) and are never
  logged. Logs contain request id, endpoint, protocol, username and result only.
- The API port is firewalled to the website's IP. The agent restarts automatically if it crashes.
  Restarting the agent itself never disconnects VPN users, except that Hysteria/ZIVPN logins wait
  for its auth listener, so keep the agent fast and stable.
- No code path in the agent restarts or reloads a VPN service, except installation, upgrades and
  the scheduled ZIVPN pool refresh (only if that fallback is used).

## 10. Test it before connecting the website

Write a small test script (Python, standard library only) that signs requests exactly as in
section 4, and check:

1. `GET /info` and `GET /health` with a valid signature → 200 with `ok: true`.
2. No signature → 401 `UNAUTHORIZED`. A wrong secret → 401 `INVALID_SIGNATURE`. A timestamp 10
   minutes old → 401 `CLOCK_SKEW`. The same nonce twice → second call 401 `REPLAYED_NONCE`.
3. Create an account for **each** protocol → 201, with the `config` values from section 8. Every one
   works in a real client app: HTTP Injector or HTTP Custom for SSH and SSL payload, v2rayNG or
   NekoBox for VMess/VLESS/Trojan/Hysteria, the ZIVPN app for ZIVPN.
4. Send the same create request again with a new nonce but the same `request_id` → 200 with
   `"replayed": true` and the same `account_id`; still only one account exists.
5. Same `request_id`, different username → 409 `IDEMPOTENCY_CONFLICT`.
6. `GET /accounts/by-request/<request_id>` → the account. An unknown id → 404.
7. `DELETE /accounts/<account_id>` → `{"revoked": true}`; that user can no longer connect; a second
   DELETE → 404.
8. A username `root` or `a; reboot` → 422 `VALIDATION_FAILED`, and nothing was executed.
9. **No-disconnect test (most important):**
   - Before the test, record every VPN service's start time:
     `systemctl show -p ActiveEnterTimestampMonotonic <service>` for sshd, dropbear, stunnel, the
     WebSocket proxy, dnstt, xray, hysteria, hysteria2 and zivpn.
   - Connect one client per protocol with an existing account and keep traffic flowing (a long
     download or continuous ping through the tunnel).
   - Through the API: create 10 accounts per protocol, renew some, delete some, and let one expire.
   - All existing clients must stay connected the whole time with no reconnects, and every service
     start time must be **unchanged** afterwards.
   - The new accounts work immediately and the deleted ones are refused (Hysteria 1, and ZIVPN in
     pool mode, refuse them at their next connection or after the scheduled refresh).
10. Reboot the VPS: every account that existed before still works (the Xray config file was updated
    too), and expired ones do not.

Signing helper for the tests:

```python
import hashlib, hmac, json, secrets, time

def sign(method, path, body_bytes, key_id, secret):
    ts = str(int(time.time()))
    nonce = secrets.token_hex(16)
    canonical = "\n".join([
        "VL1-HMAC-SHA256", method.upper(), path, ts, nonce,
        hashlib.sha256(body_bytes).hexdigest(),
    ])
    sig = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    return {"X-Node-Key-Id": key_id, "X-Node-Timestamp": ts, "X-Node-Nonce": nonce,
            "X-Node-Signature": "v1=" + sig, "Content-Type": "application/json"}

# body = json.dumps({...}).encode(); headers = sign("POST", "/api/v1/accounts", body, KEY_ID, SECRET)
```

Deliver:
- the agent source code and its systemd unit and timer;
- the changes to my install script and menu;
- the test script;
- a short README covering install, where the credentials are printed, how to rotate the secret, how
  to read the logs, and which ZIVPN mode was chosen (external auth or password pool) and why.

---

## 11. Connect it to the website (after the tests pass)

In the website's admin panel go to **Servers → Add server** and enter:

- **Hostname:** the server's domain.
- **IP address:** the server's public IP.
- **Management API port:** for example `9443`.
- **API base path:** `/api/v1`.
- **Authentication:** "HMAC-signed requests", with the **Key ID** and **API secret** the installer
  printed.
- **TLS:** "HTTPS — public certificate" if the server uses a real certificate (for example Let's
  Encrypt). Otherwise "HTTPS — pinned certificate fingerprint", with the SHA-256 fingerprint the
  installer printed.

Then click **Connect**, review the detected protocols and **Publish**. The server appears on the
public pages automatically.
