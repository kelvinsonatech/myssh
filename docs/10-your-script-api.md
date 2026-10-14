# Optional website API — strict no-restart edition

## Scope and current limitations

This implementation **never restarts, reloads, or stops a VPN service**.
There is no scheduled ZIVPN password-pool refresh and no automatic protocol migration.

Supported account creation: SSH, SSH SSL, SlowDNS and Hysteria 2, when already
installed and running. These use native Linux users or the existing Hysteria 2
command-auth account file. Hysteria 2 currently requires its existing 8+ character
password alphabet; generated passwords satisfy it.

**Not yet supported:** VMess, VLESS, Trojan, Hysteria 1 and ZIVPN provisioning on
the current installation. They remain visible in discovery, but
`metadata.api_provisioning` is false. Writes return `409 PROTOCOL_UNAVAILABLE`
before any native change. Xray's existing untagged listeners need migration;
Hysteria 1 uses a static list; live auth for the actual ZIVPN binary remains
unverified. This is deliberately not full compliance with the all-protocol
contract. A successful JSON response must not stand in for working credentials.

The original menu and existing expiry/maintenance jobs are not converted to
live Xray/Hysteria/ZIVPN updates by this release. Their existing restart behavior
remains; do not use those operations while claiming uninterrupted service.
The API itself does not invoke those paths. No shared cron job is silently removed.

## Install without rerunning the VPN setup

On a **test VPS first**, with the repository checked out:

```sh
sudo bash scripts/node-api/install.sh
```

Requires Python 3.9+, curl, OpenSSL, systemd, iproute2, iptables/ip6tables and
nftables. The installer does not replace system Python or install distro
packages (package hooks could restart services). Dependencies go in a private
`/opt/vpn-node-api/deps` target directory, not system site-packages.
If system pip is absent, setup downloads a SHA-256-pinned pip wheel and uses it
temporarily without installing pip globally. Dependency downloads finish in
temporary storage before permanent API directories are created, so download
failures can be retried. Named setup stages identify where a failure occurred.

Enter server domain, public IPv4, the website's **outgoing** IPv4, and TCP port
(default 9443). IPv4 serving only in this release. Check the provider firewall
also permits that website IP to reach the selected TCP port. UDP 9443 and TCP
9443 are different sockets; ZIVPN's UDP hopping pool alone is not a conflict.
Existing listeners, dormant Xray ports and NAT interception are checked.
Unrecognized NAT expressions fail closed for administrator review.

The source menu offers optional API setup/status/rotation/disable. Setup patches
the installed menu only to serialize SSH/Hysteria 2 account operations with API
writes. The backup is `/etc/vpn-node-api/menu.before-api`. Holding the Hysteria 2
submenu open holds that account lock; close it before website writes.
This is locking coordination, not a completed extraction of all legacy menu
account logic into a common backend.

Setup copies a usable domain certificate when available, otherwise creates a
self-signed certificate. Copy the printed SHA-256 certificate fingerprint into
the website's pin field. Certificate copies need renewal before expiry.
The source-IP rule is confined to the chosen API TCP port and reapplied by
`ExecStartPre` on every API start. It never flushes INPUT, NAT or another chain.
An independent application-level source-IP check remains in effect if a firewall
manager later removes that rule. No forwarding headers are trusted.

## Credentials and administration

Setup prints the base URL, public IP, port, key ID, fingerprint and secret.
The secret is printed only once and stored in mode-600
`/etc/vpn-node-api/config.json`. Do not post it in chat or source control.

```sh
sudo vpn-node-api status
sudo vpn-node-api rotate
sudo vpn-node-api pending
sudo vpn-node-api disable
sudo journalctl -u vpn-node-api --since '10 minutes ago'
```

Rotation restarts only the **API service**, never a VPN service. Native tunnel
authentication is independent of the API. Disable retains accounts and state.
An interrupted install retains files for diagnosis and disables only the API;
it does not erase account state or overwrite an existing installation.

## Website contract

HTTPS base: `https://DOMAIN:9443/api/v1`.
All endpoints require the uploaded contract's `VL1-HMAC-SHA256` signature:
method, exact raw path plus query, timestamp, nonce, SHA-256 of raw body; joined
with newlines, with no trailing newline. Secret bytes are UTF-8, not hex-decoded.

Headers: `X-Node-Key-Id`, `X-Node-Timestamp`, `X-Node-Nonce`,
`X-Node-Signature: v1=<hex>`, `X-Request-Id` (UUID).
Create/renew/delete also require `Idempotency-Key` (UUID).
Create/renew body `request_id` must match it.

Implemented routes:

| Method | Path | Result |
|---|---|---|
| GET | `/info` | Capabilities and installed protocols |
| GET | `/health` | Resources and service state |
| GET | `/protocols` | Installed protocols, ports, provisioning readiness |
| POST | `/accounts` | Create; 201, or 200 on identical replay |
| GET | `/accounts/by-request/{request_id}` | Recover the result after a timeout |
| GET | `/accounts/{account_id}` | Account details |
| GET | `/accounts?protocol=ssh&limit=100` | Paginated API-owned accounts |
| POST | `/accounts/{account_id}/renew` | Extend validity |
| DELETE | `/accounts/{account_id}` | Revoke new connections |
| GET | `/stats` | 501 NOT_SUPPORTED |

Success: `{"ok":true,"data":{...}}`.
Errors: `{"ok":false,"error":{"code":"...","message":"...","retryable":false}}`.
No anonymous docs, command execution, upload or arbitrary file routes.
Bodies are capped at 64 KiB. Passwords and response configs are not logged.

Example create body:

```json
{
  "request_id": "6c3f1e2a-8d4b-4c1e-9a7f-2b5d8e9c0a11",
  "protocol": "ssh",
  "username": "traveller",
  "password": "ChooseAUniquePassword",
  "duration_days": 7,
  "options": {}
}
```

Native date-based expiry is returned as its real UTC boundary, not an invented
per-second deadline. SSH variants share Linux usernames. Deleting an API SSH
account expires its Linux login but retains its UID and existing sessions;
that username cannot be reused automatically. Hysteria 2 revocation/expiry
denies new connections; existing sessions finish naturally. Expiry handling
is advertised conservatively as `expiry_managed_by_node: false`, so the website
must issue DELETE. This release does not provide the revised contract's
all-protocol expiry timer or immediate Hysteria 2 session kick.

## Reliability and recovery

SQLite journal and nonces live in `/var/lib/vpn-node-api` (directory 700).
Request records are retained indefinitely, exceeding the seven-day requirement.
Identical retries reuse the same result; changed payloads conflict.
A shared flock serializes writes; native account files are replaced atomically.

A process crash after a native mutation can leave a pending journal entry.
Pending entries block further writes rather than risking duplicate accounts.
`REQUEST_IN_PROGRESS` means **outcome unknown**, not a guaranteed rollback.
Use lookup-by-request and `vpn-node-api pending`, then inspect native state.
There is deliberately no automatic replay or remote “clear journal” endpoint.
Back up the SQLite state and native account files together.

## Verification

Local tests: `python3 tests/node-api-auth.py` and
`python3 tests/node-api-contract.py`. These exercise authentication, replay
protection, routing, JSON, idempotency and native Hysteria 2 fixture files.
They are **not** proof of client connectivity on a VPS.

Use `tests/node-api-smoke.py` on the test VPS for signed HTTPS checks; it reads
the root-only config locally and never prints the secret. Before production,
keep real client sessions open while creating/renewing/deleting accounts.
Record service start times and verify they do not change. The actual ZIVPN
app and all-protocol live-account verification remain outstanding.
