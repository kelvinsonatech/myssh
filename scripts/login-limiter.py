#!/usr/bin/env python3
"""Multi-login limiter for SSH, SSH-SSL and SlowDNS accounts.

Counts each account's live logins (OpenSSH and Dropbear). When an account
stays above its limit, only that account's OLDEST sessions are closed, so a
reconnecting user keeps the new session. Repeated overuse locks the account;
optional delayed deletion follows. Never restarts or reconfigures a VPN service.
"""
import argparse
import contextlib
import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DEFAULTS = {"enabled": False, "mode": "watch", "limit": 4, "overrides": {},
            "interval": 20, "grace": 60, "strikes": 3, "window": 600,
            "delete_after": 60}
RANGES = {"limit": (1, 50), "interval": (5, 300), "grace": (0, 3600),
          "strikes": (1, 20), "window": (60, 86400), "delete_after": (0, 10080)}
SHELLS = ("/bin/false", "/usr/sbin/nologin")
AUTH = re.compile(r"(?:Password|Pubkey) auth succeeded for '([^']+)'")
API_TAG = "vpn-node="
EMPTY_STATE = {"over": {}, "strikes": {}, "watch": {}, "locked": {}, "pending": []}


class Busy(Exception):
    """Another account operation holds the shared lock; retry next cycle."""


def atomic(path, text, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def validate(cfg):
    merged = {**DEFAULTS, **cfg}
    for key, (low, high) in RANGES.items():
        value = merged[key]
        if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
            raise ValueError(f"{key} must be a whole number from {low} to {high}")
    if merged["mode"] not in ("watch", "enforce"):
        raise ValueError("mode must be watch or enforce")
    if not isinstance(merged["enabled"], bool):
        raise ValueError("enabled must be true or false")
    overrides = merged["overrides"]
    if not isinstance(overrides, dict) or not all(
            isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool)
            and 1 <= v <= 50 for k, v in overrides.items()):
        raise ValueError("per-account limits must be 1 to 50")
    return merged


class Limiter:
    def __init__(self, root="/", now=time.time, kill=os.kill, run=None):
        base = Path(root)
        self.proc = base / "proc"
        self.passwd = base / "etc/passwd"
        self.shadow = base / "etc/shadow"
        self.config_path = base / "etc/vpn-login-limiter/config.json"
        self.state_path = base / "var/lib/vpn-login-limiter/state.json"
        self.log_path = base / "var/log/vpn-login-limiter.log"
        self.account_lock_path = base / "run/lock/vpn-node-accounts.lock"
        self.state_lock_path = base / "run/lock/vpn-login-limiter.lock"
        self.now, self.kill = now, kill
        self.run = run or self._run
        self.cache, self.tries = {}, {}
        self.state = json.loads(json.dumps(EMPTY_STATE))
        self.good_config = None
        self.last_error = None

    # ── infrastructure ───────────────────────────────────────────────
    @staticmethod
    def _run(argv, timeout=20):
        return subprocess.run(argv, check=True, text=True, capture_output=True, timeout=timeout)

    def log(self, message):
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.now()))
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if self.log_path.stat().st_size > 1024 * 1024:
                os.replace(self.log_path, self.log_path.with_suffix(".log.1"))
        except FileNotFoundError:
            pass
        with self.log_path.open("a") as stream:
            stream.write(f"[{stamp}] {message}\n")

    def config(self):
        try:
            raw = json.loads(self.config_path.read_text())
            self.good_config = validate(raw)
        except FileNotFoundError:
            self.good_config = dict(DEFAULTS)
        except (ValueError, OSError) as exc:
            if self.good_config is None:
                raise
            self.error("config", exc)   # keep running with last valid settings
        return self.good_config

    def save_config(self, cfg):
        cfg = validate(cfg)
        atomic(self.config_path, json.dumps(cfg, indent=2) + "\n", 0o600)
        return cfg

    def error(self, kind, exc):
        text = f"{kind}: {exc}"
        if text != self.last_error:          # log each distinct problem once
            self.last_error = text
            self.log("warning " + text)

    @contextlib.contextmanager
    def state_lock(self):
        self.state_lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_lock_path, "a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                try:
                    loaded = json.loads(self.state_path.read_text())
                    self.state = {**json.loads(json.dumps(EMPTY_STATE)), **loaded}
                except FileNotFoundError:
                    self.state = json.loads(json.dumps(EMPTY_STATE))
                except ValueError:
                    os.replace(self.state_path, self.state_path.with_suffix(".corrupt"))
                    self.state = json.loads(json.dumps(EMPTY_STATE))
                    self.log("warning state file unreadable; saved a copy and started fresh")
                yield self.state
                atomic(self.state_path, json.dumps(self.state, indent=1, sort_keys=True) + "\n")
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    @contextlib.contextmanager
    def account_lock(self):
        """Same lock as menu/API account operations: never race a create/delete."""
        self.account_lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.account_lock_path, "a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Busy()
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    # ── discovery ────────────────────────────────────────────────────
    def accounts(self):
        result = {}
        for line in self.passwd.read_text().splitlines():
            fields = line.split(":")
            if len(fields) < 7 or not fields[2].isdigit():
                continue
            if int(fields[2]) >= 1000 and fields[6] in SHELLS:
                result[fields[0]] = {"uid": int(fields[2]), "api": fields[4].startswith(API_TAG)}
        return result

    def password_locked(self, user):
        for line in self.shadow.read_text().splitlines():
            fields = line.split(":")
            if fields[0] == user and len(fields) > 1:
                return fields[1].startswith("!")
        return False

    def process(self, pid):
        entry = self.proc / str(pid)
        try:
            stat = (entry / "stat").read_text()
            status = (entry / "status").read_text()
        except OSError:
            return None
        end = stat.rfind(")")
        comm = stat[stat.find("(") + 1:end]
        fields = stat[end + 2:].split()
        uid = next(int(l.split()[1]) for l in status.splitlines() if l.startswith("Uid:"))
        return comm, int(fields[1]), int(fields[19]), uid

    def processes(self):
        table = {}
        for entry in self.proc.iterdir():
            if entry.name.isdigit():
                info = self.process(int(entry.name))
                if info:
                    table[int(entry.name)] = info
        return table

    def epoch(self, start):
        if not hasattr(self, "_boot"):
            self._boot = next(int(l.split()[1]) for l in (self.proc / "stat").read_text().splitlines()
                              if l.startswith("btime"))
            self._hz = os.sysconf("SC_CLK_TCK")
        return self._boot + start / self._hz

    def resolve_dropbear(self, pending):
        """Map Dropbear connection PIDs to accounts using their own auth log line."""
        if not pending:
            return
        since = int(min(self.epoch(start) for _, start in pending)) - 5
        found = {}
        try:
            output = self.run(["journalctl", "-b", "--no-pager", "-o", "json",
                               "--output-fields=_PID,MESSAGE", "_COMM=dropbear",
                               "--since", f"@{since}"]).stdout
        except Exception as exc:          # journal unavailable: count nothing, kill nothing
            self.error("journal", exc)
            output = ""
        for line in output.splitlines():
            try:
                item = json.loads(line)
                match = AUTH.search(item["MESSAGE"]) if isinstance(item.get("MESSAGE"), str) else None
                if match:
                    found.setdefault(int(item["_PID"]), []).append(
                        (int(item["__REALTIME_TIMESTAMP"]) / 1e6, match.group(1)))
            except (ValueError, KeyError, TypeError):
                continue
        now = self.now()
        for key in pending:
            pid, start = key
            began = self.epoch(start)
            # Ignore lines from an older process that had the same PID.
            names = [user for stamp, user in found.get(pid, []) if stamp >= began - 2]
            if names:
                self.cache[key] = names[-1]
            else:
                self.tries[key] = self.tries.get(key, 0) + 1
                if self.tries[key] >= 3 and now - began > 120:
                    self.cache[key] = None   # never authenticated: never counted

    def sessions(self, accounts=None):
        accounts = accounts if accounts is not None else self.accounts()
        by_uid = {info["uid"]: user for user, info in accounts.items()}
        table = self.processes()
        result, dropbear = {}, []
        for pid, (comm, ppid, start, uid) in table.items():
            if comm in ("sshd", "sshd-session") and uid in by_uid:
                # OpenSSH: the per-connection unprivileged child runs as the user.
                # The listener runs as root, so it can never match.
                result.setdefault(by_uid[uid], []).append([start, pid, "openssh"])
            elif comm == "dropbear" and uid == 0 and table.get(ppid, ("",))[0] == "dropbear":
                dropbear.append((pid, start))     # connection child, never the listener
        self.resolve_dropbear([k for k in dropbear if k not in self.cache])
        alive = set(dropbear)
        for key in list(self.cache):
            if key not in alive:
                self.cache.pop(key, None)
                self.tries.pop(key, None)
        for pid, start in dropbear:
            user = self.cache.get((pid, start))
            if user in accounts:
                result.setdefault(user, []).append([start, pid, "dropbear"])
        for items in result.values():
            items.sort()
        return result

    # ── actions ──────────────────────────────────────────────────────
    def close(self, session, user, accounts):
        start, pid, kind = session
        info = self.process(pid)
        if not info or info[2] != start:
            return False                     # already gone or PID reused
        comm, ppid, _, uid = info
        if kind == "openssh":
            safe = comm in ("sshd", "sshd-session") and uid == accounts[user]["uid"]
        else:
            parent = self.process(ppid)
            safe = comm == "dropbear" and uid == 0 and parent and parent[0] == "dropbear"
        if not safe:
            self.log(f"skipped unexpected process {pid} for {user}")
            return False
        try:
            self.kill(pid, signal.SIGTERM)
            return True
        except ProcessLookupError:
            return False

    def penalize(self, user, cfg, accounts):
        with self.account_lock():
            if user not in self.accounts():
                return
            pre_locked = self.password_locked(user)
            if not pre_locked:
                self.run(["usermod", "-L", user])
            closed = sum(self.close(s, user, accounts) for s in self.sessions(accounts).get(user, []))
            self.state["locked"][user] = {"at": self.now(), "pre_locked": pre_locked,
                                          "api": accounts[user]["api"]}
            self.state["strikes"].pop(user, None)
            self.state["over"].pop(user, None)
            when = (f"deletion in {cfg['delete_after']} min" if cfg["delete_after"]
                    else "kept locked until you unlock it")
            self.log(f"LOCKED {user}: repeated logins above limit; closed {closed} session(s); {when}")

    def deletions(self, cfg, accounts):
        if cfg["mode"] != "enforce" or not cfg["delete_after"]:
            return
        now = self.now()
        for user, info in list(self.state["locked"].items()):
            if info.get("deleted") or now < info["at"] + cfg["delete_after"] * 60:
                continue
            if user not in accounts:
                self.state["locked"].pop(user)
                continue
            if not self.password_locked(user):
                self.state["locked"].pop(user)
                self.log(f"{user} was unlocked outside the limiter; deletion cancelled")
                continue
            try:
                with self.account_lock():
                    for session in self.sessions(accounts).get(user, []):
                        self.close(session, user, accounts)
                    if info["api"]:
                        # Website account: revoke but keep the UID so the API record stays valid.
                        self.run(["chage", "-E", "1970-01-02", user])
                        info["deleted"] = now
                    else:
                        self.run(["userdel", "-f", user])
                        self.state["locked"].pop(user)
                    self.log(f"DELETED {user} after repeated multi-login abuse"
                             + (" (website account revoked)" if info["api"] else ""))
            except Busy:
                pass

    def cycle(self):
        cfg = self.config()
        now = self.now()
        accounts = self.accounts()
        enforce = cfg["mode"] == "enforce"
        live = self.sessions(accounts)
        st = self.state
        for user in list(st["over"]):
            if user not in live:
                st["over"].pop(user)
        for user in list(st["pending"]):
            if user not in accounts or user in st["locked"] or not enforce:
                st["pending"].remove(user)
                continue
            try:
                self.penalize(user, cfg, accounts)
                st["pending"].remove(user)
            except Busy:
                pass
        for user, items in live.items():
            if user in st["locked"]:
                # A login that slipped in around the lock (or a key login) is closed too.
                if enforce and not st["locked"][user].get("deleted") and self.password_locked(user):
                    closed = sum(self.close(s, user, accounts) for s in items)
                    if closed:
                        self.log(f"closed {closed} login(s) on locked account {user}")
                continue
            if user in st["pending"]:
                continue
            limit = cfg["overrides"].get(user, cfg["limit"])
            if len(items) <= limit:
                st["over"].pop(user, None)
                continue
            first = st["over"].setdefault(user, now)
            if now - first < cfg["grace"]:
                continue                       # reconnect overlap: wait it out
            st["over"].pop(user, None)
            excess = items[:len(items) - limit]   # oldest first; newest kept
            book = st["strikes"] if enforce else st["watch"]
            strikes = [t for t in book.get(user, []) if now - t < cfg["window"]] + [now]
            book[user] = strikes
            if not enforce:
                self.log(f"WATCH {user}: {len(items)}/{limit} logins — would close "
                         f"{len(excess)} oldest")
                if len(strikes) >= cfg["strikes"]:
                    self.log(f"WATCH {user}: would LOCK (repeated {len(strikes)}x in "
                             f"{cfg['window'] // 60} min)")
                    book.pop(user)
                continue
            closed = sum(self.close(s, user, accounts) for s in excess)
            self.log(f"LIMIT {user}: {len(items)}/{limit} logins — closed {closed} oldest "
                     f"(strike {len(strikes)}/{cfg['strikes']})")
            if len(strikes) >= cfg["strikes"]:
                try:
                    self.penalize(user, cfg, accounts)
                except Busy:
                    st["pending"].append(user)
        for book in (st["strikes"], st["watch"]):
            for user in list(book):
                book[user] = [t for t in book[user] if now - t < cfg["window"]]
                if not book[user]:
                    book.pop(user)
        self.deletions(cfg, accounts)
        return live

    def daemon(self):
        stop = []
        signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
        self.log("limiter started")
        while not stop:
            interval = 20
            try:
                cfg = self.config()
                interval = cfg["interval"]
                if cfg["enabled"]:
                    with self.state_lock():
                        self.cycle()
            except Exception as exc:          # stay alive; never touch services
                self.error("cycle", exc)
            for _ in range(interval * 2):
                if stop:
                    break
                time.sleep(0.5)
        self.log("limiter stopped")

    def unlock(self, user):
        with self.state_lock() as st, self.account_lock():
            info = st["locked"].get(user)
            if not info:
                raise ValueError(f"{user} is not locked by the limiter")
            if info.get("deleted"):
                raise ValueError(f"{user} was already removed")
            if not info["pre_locked"] and user in self.accounts():
                self.run(["usermod", "-U", user])
            for book in ("locked", "strikes", "watch", "over"):
                st[book].pop(user, None)
            if user in st["pending"]:
                st["pending"].remove(user)
            self.log(f"UNLOCKED {user} by administrator")


# ── terminal output ──────────────────────────────────────────────────
TTY = sys.stdout.isatty()


def paint(code, text):
    return f"\033[{code}m{text}\033[0m" if TTY else text


def show_status(limiter):
    cfg = limiter.config()
    accounts = limiter.accounts()
    live = limiter.sessions(accounts)
    with limiter.state_lock() as st:
        locked = dict(st["locked"])
    print(paint("38;5;213;1", "  LIVE CONNECTIONS") + paint("2", "  (SSH · SSH-SSL · SlowDNS)"))
    print(paint("2", "  " + "─" * 44))
    print(paint("2", f"  {'ACCOUNT':<20}{'LOGINS':>8}{'LIMIT':>8}  STATE"))
    rows = sorted(set(live) | set(locked))
    if not rows:
        print(paint("2", "  nobody connected right now"))
    for user in rows:
        count = len(live.get(user, []))
        limit = cfg["overrides"].get(user, cfg["limit"])
        if user in locked:
            state = paint("1;31", "removed" if locked[user].get("deleted") else "locked")
        elif count > limit:
            state = paint("38;5;208", "over limit")
        else:
            state = paint("38;5;118", "ok")
        mark = "*" if user in cfg["overrides"] else " "
        print(f"  {user:<20}{count:>8}{limit:>7}{mark}  {state}")
    total = sum(len(v) for v in live.values())
    print(paint("2", "  " + "─" * 44))
    print(f"  {paint('1', str(total))} login(s) across {paint('1', str(len(live)))} account(s)"
          + paint("2", "   * custom limit"))


def show_locked(limiter):
    cfg = limiter.config()
    with limiter.state_lock() as st:
        locked = dict(st["locked"])
    if not locked:
        print(paint("38;5;118", "  No accounts are locked."))
        return
    for user, info in sorted(locked.items()):
        since = time.strftime("%Y-%m-%d %H:%M", time.localtime(info["at"]))
        if info.get("deleted"):
            detail = "website account revoked"
        elif cfg["delete_after"] and cfg["mode"] == "enforce":
            left = max(0, int(info["at"] + cfg["delete_after"] * 60 - limiter.now()) // 60)
            detail = f"deletes in {left} min"
        else:
            detail = "locked until unlocked"
        print(f"  {paint('1;31', '■')} {user:<20} {paint('2', since)}  {detail}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="SSH multi-login limiter")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run")
    sub.add_parser("scan")
    sub.add_parser("status")
    sub.add_parser("locked")
    log = sub.add_parser("log")
    log.add_argument("--lines", type=int, default=25)
    get = sub.add_parser("get")
    get.add_argument("key", choices=sorted(DEFAULTS))
    configure = sub.add_parser("configure")
    configure.add_argument("--limit", type=int)
    configure.add_argument("--mode", choices=("watch", "enforce"))
    configure.add_argument("--delete-after", type=int)
    configure.add_argument("--enable", action="store_true")
    configure.add_argument("--disable", action="store_true")
    override = sub.add_parser("override")
    override.add_argument("user")
    override.add_argument("value")
    unlock = sub.add_parser("unlock")
    unlock.add_argument("user")
    args = parser.parse_args(argv)
    limiter = Limiter()
    if args.command == "run":
        limiter.daemon()
    elif args.command == "scan":
        live = limiter.sessions()
        print(f"{sum(len(v) for v in live.values())} login(s) across {len(live)} account(s)")
    elif args.command == "status":
        show_status(limiter)
    elif args.command == "locked":
        show_locked(limiter)
    elif args.command == "log":
        try:
            lines = limiter.log_path.read_text().splitlines()[-max(1, args.lines):]
        except FileNotFoundError:
            lines = []
        print("\n".join("  " + line for line in lines) if lines else "  No activity yet.")
    elif args.command == "get":
        value = limiter.config()[args.key]
        print(json.dumps(value) if isinstance(value, (dict, bool)) else value)
    elif args.command == "configure":
        cfg = dict(limiter.config())
        if args.limit is not None:
            cfg["limit"] = args.limit
        if args.mode:
            cfg["mode"] = args.mode
        if args.delete_after is not None:
            cfg["delete_after"] = args.delete_after
        if args.enable:
            cfg["enabled"] = True
        if args.disable:
            cfg["enabled"] = False
        limiter.save_config(cfg)
    elif args.command == "override":
        cfg = dict(limiter.config())
        overrides = dict(cfg["overrides"])
        if args.value == "clear":
            overrides.pop(args.user, None)
        else:
            if args.user not in limiter.accounts():
                raise ValueError(f"{args.user} is not an SSH account")
            overrides[args.user] = int(args.value)
        cfg["overrides"] = overrides
        limiter.save_config(cfg)
    elif args.command == "unlock":
        limiter.unlock(args.user)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, Busy, OSError, subprocess.SubprocessError) as exc:
        message = str(exc) or "another account operation is running; retry shortly"
        print(f"  ✘ {message}", file=sys.stderr)
        sys.exit(1)
