# Updating without rerunning server setup

Use **main menu → 18 (Update script)** or **API menu 17 → 6**.

## Management menu

Selecting **Update script** checks the latest `replit-agent` revision of
`kelvinsonatech/myssh` directly. Confirm with `YES` to upgrade the installed script,
or decline to leave it unchanged. There are no API-update or restore choices.
If the installed code is already current, nothing is replaced.
The updater downloads the installer as data and extracts only its menu heredoc.
It never executes the full installer. Existing API account-operation locking
is preserved if the API is installed.

The download uses HTTPS and GitHub blob integrity checks. This trusts that
repository and GitHub; it is not an independently signed release system.
One immutable commit is selected per update, preventing mixed-version downloads.
An unavailable/rate-limited GitHub API, failed integrity check, or Bash syntax
error stops the update. No package installation or service restart is performed.

The current menu is backed up before an atomic replacement. Backups contain menu
code only and are kept root-only in `/etc/ssh-panel/script-updates`.
Close and reopen `menu` after updating.

This is intentionally a **management-code update**, not a protocol migration.
It does not replace separately installed VPN binaries, authentication helpers,
systemd units, cron jobs, firewall rules, certificates, or account data.
Such migrations require their own explicit, protocol-specific validation.

This action does not update or restart the web API. It only preserves existing
menu account-operation locking when the API is installed.

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
