# Updating without rerunning server setup

Use **main menu → 18 (Update script)** or **API menu 17 → 6**.

## Management menu

Choose **1** to check the latest `replit-agent` revision of
`kelvinsonatech/myssh`. Confirm with `YES` to replace the installed menu.
The updater downloads the installer as data and extracts only its menu heredoc.
It never executes the full installer. Existing API account-operation locking
is preserved if the API is installed.

The download uses HTTPS and GitHub blob integrity checks. This trusts that
repository and GitHub; it is not an independently signed release system.
One immutable commit is selected per update, preventing mixed-version downloads.
An unavailable/rate-limited GitHub API, failed integrity check, or Bash syntax
error stops the update. No package installation or service restart is performed.

The current menu is backed up before an atomic replacement. **Option 3** restores
the previous menu; this can also undo the last restoration. Backups contain menu
code only and are kept root-only in `/etc/ssh-panel/script-updates`.
Close and reopen `menu` after updating.

This is intentionally a **management-code update**, not a protocol migration.
It does not replace separately installed VPN binaries, authentication helpers,
systemd units, cron jobs, firewall rules, certificates, or account data.
Such migrations require their own explicit, protocol-specific validation.

## Web API

Choose **2** only when ready for a brief web API interruption. It requires a
separate `YES` confirmation. All API modules are downloaded from one commit,
checked against the release manifest, and compiled before invoking the existing
API-only updater. Dependencies are not installed or upgraded.

Only an already-active `vpn-node-api.service` is stopped/started.
VPN services are not restarted; API accounts, secrets, whitelist and configuration
are preserved. Menu rollback does not roll back API modules.

## Existing servers without the update menu

After publishing these files to GitHub, bootstrap the menu update once:

```bash
(
  set -e
  tmp=$(mktemp -d)
  trap 'rm -rf "$tmp"' EXIT
  curl -fsSL --proto '=https' --tlsv1.2 \
    https://raw.githubusercontent.com/kelvinsonatech/myssh/replit-agent/scripts/update-script.py \
    -o "$tmp/update-script.py"
  sudo python3 "$tmp/update-script.py" --menu-only
)
```

This downloads the trusted repository's updater over HTTPS. It does not run
server setup or restart any service. Thereafter, use the update submenu.
