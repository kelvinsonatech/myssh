# Prompt: add a web API to your VPN install script

**How to use this file:** open the AI tool you used to build your VPN install script. Give it your
script files (all of them: the installer, menu, add/delete/renew helpers, cron jobs, config
templates), then paste everything below the line as your message. When it is done, test it with
section 9 and add the server in the website's admin panel (section 10).

The website side is already built and expects exactly this API, so follow the formats literally:
field names, headers, status codes and error codes. `mock-node/mock-node.ts` in this repository is
a working reference implementation of the same API in TypeScript; give it to your AI too if it can
read files.

---

## Task

Add a small **HTTP API service** (I'll call it the "node agent") to my existing VPN install
script. My script supports these protocols: **SSH, SSH SSL (with payload), SlowDNS, VMess, VLESS,
Trojan, Hysteria 1, Hysteria 2 and ZIVPN UDP**. It runs on every VPS where my script is installed. My central website calls it to create,
look up, delete and renew VPN/SSH accounts and to check the server's health.

**Very important rules:**

1. **Do not rewrite how my script creates accounts.** Reuse my script's existing logic. If that
   logic only exists inside interactive menus (prompts with `read`), extract it into
   non-interactive commands or functions that take arguments (for example
   `add-ssh <username> <password> <days>`) and print their result as JSON. The existing menu must
   keep working by calling the same functions.
2. The agent must **never run a shell command built from request data**. Call the account
   commands with an argument list (Python `subprocess.run([...], shell=False)`, Go `exec.Command`).
   Never use `shell=True`, `eval`, `bash -c "<string>"` or string concatenation into commands.
3. The agent exposes **only the endpoints listed below**. No endpoint may accept a command, script,
   file path or any other free-form instruction.
4. Every request must be authenticated with the HMAC signature described in section 3. Each server
   has its **own** secret.
5. Never log passwords, private keys, the API secret or full configs.

**Technology:** use Python 3 with FastAPI + uvicorn (or Go if my script already uses Go). Run it as
a systemd service. Store its state in SQLite under `/var/lib/vpn-node-api/`. Put its config in
`/etc/vpn-node-api/config.json` with file permissions `600`.

---

## 1. Installation (add to my install script)

- Install the agent and its dependencies, create the systemd unit `vpn-node-api.service`, enable
  and start it.
- Listen on a configurable port, default **9443**, and check it does not clash with ports my VPN
  services already use.
- **TLS (HTTPS) is required.** If my script already has a certificate for the server's domain (for
  example `/etc/xray/xray.crt` and `/etc/xray/xray.key`), use it. Otherwise generate a self-signed
  certificate and print its **SHA-256 fingerprint** (I enter it in the website as a "pinned
  fingerprint").
- Generate the credentials once: a **key id** (for example `node-<random 8 hex>`) and a random
  **secret** (64 hex characters). Save both in the config file and print them **once** at the end of
  the installation together with the port and base path, so I can paste them into the website.
- Add a firewall rule (ufw or iptables) that allows the API port **only from my website's IP
  address**. Ask for that IP during installation, or read it from a variable.
- Add a menu entry or command to show the API status and to rotate the secret.

## 2. Transport

- Base URL: `https://<server-hostname>:<port>/api/v1`
- Requests and responses are JSON (`Content-Type: application/json`). Reject request bodies larger
  than 64 KB.
- No redirects. Reply within 30 seconds; the website waits up to 45 seconds for account creation.

## 3. Authentication (HMAC-SHA256), required on every endpoint

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

1. A header is missing, the timestamp is not digits, or the key id is unknown → `UNAUTHORIZED`
2. The timestamp is more than **300 seconds** away from the server clock → `CLOCK_SKEW` (the server
   must run NTP)
3. The signature does not match → `INVALID_SIGNATURE`. Compare in constant time
   (`hmac.compare_digest`).
4. The nonce was already used in the last 10 minutes → `REPLAYED_NONCE`. Remember nonces only after
   the signature has been verified.

## 4. Response format

Success: `{"ok": true, "data": { ... }}`

Error: `{"ok": false, "error": {"code": "USERNAME_TAKEN", "message": "Username already exists", "retryable": false}}`

An error response means **nothing was changed**: no account was created or modified.

| Code | HTTP | When |
| --- | --- | --- |
| `UNAUTHORIZED`, `INVALID_SIGNATURE`, `CLOCK_SKEW`, `REPLAYED_NONCE` | 401 | authentication failed |
| `VALIDATION_FAILED` | 422 | bad input |
| `PROTOCOL_UNAVAILABLE` | 409 | protocol not installed or its service is not running |
| `USERNAME_TAKEN` | 409 | username already exists |
| `CAPACITY_REACHED` | 409 | the server's own limit is reached |
| `ACCOUNT_NOT_FOUND` | 404 | unknown account id or request id |
| `IDEMPOTENCY_CONFLICT` | 409 | same request id reused with a different payload |
| `REQUEST_IN_PROGRESS` | 409 (`retryable: true`) | the same request id is still being processed |
| `NOT_SUPPORTED` | 501 | an optional endpoint is not implemented |
| `PROVISIONING_FAILED`, `INTERNAL` | 500 | the operation failed |

## 5. Protocol identifiers

Report each installed protocol with exactly these ids:

| Protocol in my script | Use this id | Notes |
| --- | --- | --- |
| SSH (OpenSSH / Dropbear, incl. WebSocket ports if my script has them) | `ssh` | one Linux user |
| SSH SSL with payload (stunnel / TLS + HTTP payload for HTTP Injector, HTTP Custom ...) | `ssh-ssl` | same kind of Linux user |
| SlowDNS (DNSTT) | `slowdns` | same kind of Linux user |
| VMess (Xray/V2Ray) | `vmess` | |
| VLESS (Xray/V2Ray) | `vless` | |
| Trojan (Xray/Trojan-Go) | `trojan` | |
| Hysteria 1 | `hysteria` | |
| Hysteria 2 | `hysteria2` | |
| ZIVPN UDP | `zivpn` | password-based (the ZIVPN app) |

Only report protocols that are actually installed on that server. If my script has protocols that
are not in this table, report them with a short lowercase id and tell me which ones.

## 6. Endpoints

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

- `status` is `online`, `degraded` or `maintenance`.
- `protocols` lists only protocols that are actually installed.
- `expiry_managed_by_node: true` means **my script deletes expired accounts by itself** (for example
  with a cron job). If my script does not, set it to `false`; the website will then call DELETE when
  an account expires.

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
               "hysteria": "running", "hysteria2": "running", "zivpn": "running", "slowdns": "stopped"}
}}
```

Service values are `running`, `stopped` or `unknown` (use `systemctl is-active` on the service names
my script actually installs; the names above are examples).

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

`ports` values are a list of numbers or a range string such as `"1-65535"`.

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
  - `ssh`, `ssh-ssl`, `slowdns`: `username` and `password` (a Linux user; one user normally works on
    all SSH ports).
  - `vmess`, `vless`, `trojan`: `username` only (used as the account name/remark). Generate the
    UUID (VMess/VLESS) or password (Trojan) on the server and return it.
  - `hysteria`, `hysteria2`, `zivpn`: `username` (account name) and optionally `password`. If no
    password is sent, generate a strong one on the server and return it in `config.password`.
- `username` and `password` are present only for protocols that use them. **Validate again on the
  server**:
  - username: `^[a-z][a-z0-9_]{2,31}$`, and refuse system names such as `root`, `admin`, `daemon`,
    `bin`, `sys`, `nobody`, `sshd`, `www-data`, `ubuntu`, `debian`;
  - password: `^[A-Za-z0-9@#%^*_+=.,:~-]{6,32}$`;
  - `duration_days`: an integer from 1 to 365.

  Anything else → `VALIDATION_FAILED`.
- **Idempotency (very important):** store every `request_id` with a hash of the payload and the
  result, for at least 7 days.
  - Same `request_id` and same payload again → return the **same** account with `"replayed": true`
    and HTTP 200. **Never create a second account.**
  - Same `request_id` with a different payload → `IDEMPOTENCY_CONFLICT`.
  - Same `request_id` while the first attempt is still running → `REQUEST_IN_PROGRESS`.
- Use a lock (for example `flock` on a lock file) so two creations never edit my config files at the
  same time. Restart or reload services the way my script already does.
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
  "config": { "...": "protocol-specific values, see section 7" }
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

Deletes the account using my script's own delete logic → `{"revoked": true}`. An account that does
not exist or was already deleted → 404 `ACCOUNT_NOT_FOUND` (the website treats that as success).

### `POST /api/v1/accounts/{account_id}/renew` (optional)

Body `{"request_id": "...", "duration_days": 7}` → `{"account": {...}}` with the new `expires_at`.
Apply the same idempotency rule to `request_id`.

### `GET /api/v1/accounts?protocol=vless&cursor=&limit=100` (optional)

→ `{"accounts": [...], "next_cursor": null}`. Paginate with your own cursor string.

### `GET /api/v1/stats` (optional)

```json
{"ok": true, "data": {"accounts_total": 76, "accounts_by_protocol": {"ssh": 30, "vless": 20}, "online_users": 12}}
```

## 7. The `config` object per protocol

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

Include one entry in `connections` per transport my script sets up for that protocol. If my script
already builds share links (`vmess://`, `vless://`, `trojan://`), put them in `links`; the website
shows them with QR codes.

**`hysteria`: Hysteria 1:**

```json
{"host": "de1.example.com", "ip": "203.0.113.10", "port": 36712,
 "protocol": "udp", "auth": "<auth string>", "obfs": "xplus", "obfs_password": "<obfs password or empty>",
 "up_mbps": 100, "down_mbps": 100, "alpn": "hysteria", "sni": "de1.example.com", "insecure": true,
 "uri": "hysteria://de1.example.com:36712?protocol=udp&auth=...&peer=de1.example.com&insecure=1&upmbps=100&downmbps=100&alpn=hysteria#traveller"}
```

Use my server's real values. `uri` follows the Hysteria 1 share-link format: host, port,
`upmbps` and `downmbps` are required; `auth`, `peer` (SNI), `insecure`, `alpn`, `obfs` and
`obfsParam` are optional. Leave out parameters my server does not use.

**`hysteria2`: Hysteria 2:**

```json
{"host": "de1.example.com", "ip": "203.0.113.10", "port": 8443, "password": "<auth password>",
 "obfs": {"type": "salamander", "password": "<obfs password>"}, "sni": "de1.example.com", "insecure": false,
 "uri": "hysteria2://<url-encoded password>@de1.example.com:8443/?sni=de1.example.com&obfs=salamander&obfs-password=...&insecure=0#traveller"}
```

`uri` follows the official Hysteria 2 URI scheme. URL-encode the password. If the server uses
userpass authentication, the auth part is `username:password`. Include `obfs`/`obfs-password`
only when obfuscation is enabled, and `pinSHA256` (lowercase hex) only when certificate pinning
is used.

**`zivpn`: ZIVPN UDP:**

```json
{"host": "de1.example.com", "ip": "203.0.113.10", "password": "<the user's ZIVPN password>",
 "ports": {"udp": "6000-19999"}, "obfs": "zivpn"}
```

ZIVPN accounts are entries in the password list of the ZIVPN server config. On the common ZIVPN
installer that is `/etc/zivpn/config.json`, `"auth": {"mode": "passwords", "config": [...]}`, with
UDP 6000-19999 forwarded to port 5667. Use whatever my script actually does:

- Add the password to that list, restart or reload the `zivpn` service the way my script does, and
  keep my script's own record of username, password and expiry.
- Never write the password into the file with `sed` built from user input. Load the JSON, change
  the list and write it back, under the lock from section 6.
- Return the values the ZIVPN app needs: server address, password and UDP port range.

## 8. Security checklist (must all be true)

- Every endpoint verifies the signature before reading the body any further.
- Only the endpoints above exist; there is no command, script or file endpoint.
- Account commands are called with argument lists, never through a shell string.
- Usernames and passwords are validated with the patterns in section 6 before use.
- Request ids are stored for at least 7 days; replays never create a second account.
- The secret and private keys are only in `/etc/vpn-node-api/config.json` (mode 600) and are never
  logged. Logs contain request id, endpoint, protocol, username and result only.
- The API port is firewalled to the website's IP. The service restarts automatically
  (`Restart=on-failure`).

## 9. Test it before connecting the website

Write a small test script (Python, standard library only) that signs requests exactly as in
section 3 and checks:

1. `GET /info` and `GET /health` with a valid signature → 200 with `ok: true`.
2. No signature → 401 `UNAUTHORIZED`. A wrong secret → 401 `INVALID_SIGNATURE`. A timestamp 10
   minutes old → 401 `CLOCK_SKEW`. The same nonce twice → second call 401 `REPLAYED_NONCE`.
3. Create an SSH account → 201, with `config` containing host, username, password and ports. You
   can log in with it.
4. Send the same create request again with a new nonce but the same `request_id` → 200 with
   `"replayed": true` and the same `account_id`; still only one account exists.
5. Same `request_id`, different username → 409 `IDEMPOTENCY_CONFLICT`.
6. `GET /accounts/by-request/<request_id>` → the account. An unknown id → 404.
7. `DELETE /accounts/<account_id>` → `{"revoked": true}`; the user can no longer log in; a second
   DELETE → 404.
8. A username `root` or `a; reboot` → 422 `VALIDATION_FAILED`, and nothing was executed.
9. Repeat step 3 for every protocol (ssh, ssh-ssl, slowdns, vmess, vless, trojan, hysteria,
   hysteria2, zivpn) and check that the returned values work in a real client app (HTTP Injector or
   HTTP Custom for SSH and SSL payload, v2rayNG or NekoBox for VMess/VLESS/Trojan/Hysteria, the ZIVPN
   app for ZIVPN).

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

Deliver: the agent source code, the systemd unit, the changes to my install script, the test
script, and a short README covering install, where the credentials are printed, how to rotate the
secret, and how to read the logs.

---

## 10. Connect it to the website (after the tests pass)

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
