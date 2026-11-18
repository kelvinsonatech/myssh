"""Optional OpenSSH authentication banner; never alter the SSH identification stream."""
from pathlib import Path
import subprocess

from .common import atomic, account_lock

CONFIG = Path("/etc/vpn-node-api/config.json")
SSHD = Path("/etc/ssh/sshd_config")
HTML_FILE = Path("/etc/vpn-node-api/ssh-banner.html")
BACKUP = Path("/etc/vpn-node-api/sshd_config.before-banner")
LOCK = "/run/lock/vpn-node-banner.lock"
BEGIN = "# BEGIN VPN NODE SSH BANNER\n"
END = "# END VPN NODE SSH BANNER\n"
HTML = """<h1 style="text-align:center"><font color=#FF3B30>━━━━</font><font color=#E6A100>━━━━</font><font color=#2EB82E>━━━━</font><font color=#1E88E5>━━━</font><font color=#AF52DE>━━━</font><br><font color=#FF2D92>『 </font><font color=#FF3B30>L</font><font color=#FF7A00>I</font><font color=#E6A100>T</font><font color=#2EB82E>R</font><font color=#00B7D4>O</font><font color=#1E88E5>N</font><font color=#5E5CE6>S</font><font color=#AF52DE>S</font><font color=#FF2D92>H 』</font><br><font color=#FF3B30>━━━━</font><font color=#E6A100>━━━━</font><font color=#2EB82E>━━━━</font><font color=#1E88E5>━━━</font><font color=#AF52DE>━━━</font></h1><div style="text-align:center"><font color=#B266FF>➶☛ Sᴇʀᴠɪᴄᴇ: FREE SSH • V2RAY • UDP</font><br><font color=#00A7C4>➶☛ Exᴘɪʀʏ: YOU CHOOSE</font><br><font color=#B266FF>➶☛ Lɪᴍɪᴛ: 1 DEVICE PER ACCOUNT</font><br><font color=#F03E3E>➶☛ Sᴘᴇᴇᴅ: ➶ᴘᴇᴀᴋ ⚡<br>━━━━━</font><font color=#E6A100>━━━━━</font><font color=#2EB82E>━━━━</font><font color=#00A7C4>━━━━<br>➺ 🌐 Site: </font><font color=#16A34A><a href=https://litronssh.com>https://litronssh.com</a></font><br><font color=#00A7C4>➺ ✈️ Telegram: </font><font color=#229ED9><a href=https://t.me/litronssh>https://t.me/litronssh</a><br>━━━━━</font><font color=#2EB82E>━━━━━</font><font color=#E6A100>━━━━</font><font color=#F03E3E>━━━━<br>🥤ᴇɴᴊᴏʏ @LITRONSSH🥤🥀<br>╰┈►⚡LITRONSSH⚡◄┈╯</font><br><font color=#FF3B30>✎﹏﹏﹏</font><font color=#E6A100>﹏﹏﹏﹏</font><font color=#2EB82E>﹏﹏﹏﹏</font><font color=#1E88E5>﹏﹏﹏</font><br><font color=#B266FF>★ Code By @litronssh ★</font><br><font color=#FF3B30>✎﹏﹏﹏</font><font color=#E6A100>﹏﹏﹏﹏</font><font color=#2EB82E>﹏﹏﹏﹏</font><font color=#1E88E5>﹏﹏﹏</font></div>"""


def run(argv):
    return subprocess.run(argv, check=True, capture_output=True, text=True, timeout=15)


def active(unit):
    return subprocess.run(["systemctl", "is-active", "--quiet", unit],
                          capture_output=True, timeout=5).returncode == 0


def block():
    return BEGIN + f"Banner {HTML_FILE}\n" + END


def without_block(text):
    if BEGIN not in text and END not in text:
        return text
    if not text.startswith(block()) or text.count(BEGIN) != 1 or text.count(END) != 1:
        raise ValueError("The managed SSH banner settings were edited; refusing to overwrite them.")
    return text[len(block()):]


def enabled():
    return SSHD.exists() and SSHD.read_text().startswith(block())


def set_enabled(on):
    with account_lock(LOCK):
        if on and (not CONFIG.is_file() or not active("vpn-node-api.service")):
            raise ValueError("Activate the Website API before enabling the SSH banner.")
        if SSHD.is_symlink():
            raise ValueError("Custom symlinked SSH configuration requires manual review.")
        original = SSHD.read_bytes()
        text = original.decode()
        base = without_block(text)
        target = (block() + base if on else base).encode()
        if target == original and (not on or
                                   (HTML_FILE.exists() and HTML_FILE.read_text() == HTML + "\n")):
            return
        service = next((u for u in ("ssh.service", "sshd.service") if active(u)), None)
        if not service:
            raise ValueError("OpenSSH is not active; no changes made.")
        run(["/usr/sbin/sshd", "-t"])
        previous_html = HTML_FILE.read_bytes() if HTML_FILE.exists() else None
        mode = SSHD.stat().st_mode & 0o777
        if not BACKUP.exists():
            atomic(BACKUP, original)
        try:
            if on:
                atomic(HTML_FILE, HTML + "\n")
            atomic(SSHD, target, mode)
            run(["/usr/sbin/sshd", "-t"])
            effective = run(["/usr/sbin/sshd", "-T"]).stdout
            if on and f"banner {HTML_FILE}" not in effective.splitlines():
                raise ValueError("OpenSSH did not accept the banner setting.")
            # Reload, never restart: established SSH/tunnel sessions remain alive.
            run(["systemctl", "reload", service])
        except Exception:
            atomic(SSHD, original, mode)
            if previous_html is None:
                HTML_FILE.unlink(missing_ok=True)
            else:
                atomic(HTML_FILE, previous_html)
            try:
                run(["/usr/sbin/sshd", "-t"])
                run(["systemctl", "reload", service])
            except Exception:
                print("WARNING: settings restored but SSH reload failed; inspect the SSH service.")
            raise


def auto_enable():
    """A banner failure must not undo a successfully activated API."""
    try:
        set_enabled(True)
        print("SSH login banner enabled for simple SSH, SSH WebSocket and SSH SSL.")
    except Exception:
        print("API is active, but the SSH banner could not be enabled safely. "
              "Use Website API > SSH banner to retry; no VPN services were restarted.")


def menu():
    while True:
        print("\nSSH LOGIN BANNER: " + ("ON" if enabled() else "OFF"))
        print("Covers OpenSSH: port 22, SSH WebSocket and SSH SSL.")
        print("Direct Dropbear 109/143 and non-SSH protocols are unchanged.")
        print("HTML rendering depends on your client app. Text does not enforce account limits.")
        print("1  Turn on\n2  Turn off\n0  Back")
        choice = input("Select: ").strip()
        if choice in ("", "0"):
            return
        if choice not in ("1", "2"):
            print("Invalid option.")
            continue
        set_enabled(choice == "1")
        print("Banner " + ("enabled" if choice == "1" else "disabled") +
              ". No services restarted; applies to new logins.")
