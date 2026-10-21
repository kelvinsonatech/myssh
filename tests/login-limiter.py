"""Isolated multi-login limiter tests using a fake /proc, passwd and journal.

No real process, account or service is touched: kill/usermod/userdel/chage
and journalctl are all replaced with recorders.
"""
import importlib.util
import json
import os
import signal
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location("limiter", "scripts/login-limiter.py")
lim = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lim)
HZ = os.sysconf("SC_CLK_TCK")
BOOT = 1_000_000


class Box:
    """A fake server: processes, accounts, journal and recorded commands."""

    def __init__(self, test):
        folder = tempfile.TemporaryDirectory()
        test.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        (self.root / "proc").mkdir()
        (self.root / "proc/stat").write_text(f"cpu 0\nbtime {BOOT}\n")
        (self.root / "etc").mkdir()
        self.users = {"alice": (1001, ""), "bob": (1002, ""),
                      "web": (1003, "vpn-node=acc_1"), "admin": (1000, "")}
        self.shadow = {name: "$6$hash" for name in self.users}
        self.write_accounts()
        self.clock = BOOT + 10_000
        self.journal, self.commands, self.killed = [], [], []
        self.limiter = lim.Limiter(self.root, now=lambda: self.clock, kill=self.kill, run=self.run)
        # Listeners: OpenSSH (root, parent systemd) and Dropbear master.
        self.add(1, "systemd", 0, 0)
        self.add(100, "sshd", 1, 0)
        self.add(300, "dropbear", 1, 0)

    def write_accounts(self):
        lines = ["root:x:0:0:root:/root:/bin/bash"]
        for name, (uid, gecos) in self.users.items():
            shell = "/bin/bash" if name == "admin" else "/bin/false"
            lines.append(f"{name}:x:{uid}:{uid}:{gecos}:/home/{name}:{shell}")
        (self.root / "etc/passwd").write_text("\n".join(lines) + "\n")
        (self.root / "etc/shadow").write_text(
            "".join(f"{n}:{h}:1::::::\n" for n, h in self.shadow.items()))

    def add(self, pid, comm, ppid, uid, start=None):
        start = start if start is not None else pid * 10
        entry = self.root / "proc" / str(pid)
        entry.mkdir()
        fields = ["S", str(ppid)] + ["0"] * 17 + [str(start), "0"]
        (entry / "stat").write_text(f"{pid} ({comm}) " + " ".join(fields) + "\n")
        (entry / "status").write_text(f"Name:\t{comm}\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n")
        return start

    def ssh(self, pid, user):
        """One OpenSSH connection: root [priv] monitor + user-owned child."""
        self.add(pid, "sshd", 100, 0)
        return self.add(pid + 1, "sshd", pid, self.users[user][0])

    def dropbear(self, pid, user, logged=True):
        start = self.add(pid, "dropbear", 300, 0)
        if logged:
            stamp = BOOT + start / HZ + 1
            self.journal.append({"_PID": str(pid), "__REALTIME_TIMESTAMP": str(int(stamp * 1e6)),
                                 "MESSAGE": f"Password auth succeeded for '{user}' from 1.2.3.4:5"})
        return start

    def kill(self, pid, sig):
        assert sig == signal.SIGTERM
        self.killed.append(pid)
        for child in (self.root / "proc").iterdir():
            if child.name.isdigit() and (child / "stat").read_text().split()[3] == str(pid):
                self.remove(int(child.name))
        self.remove(pid)

    def remove(self, pid):
        entry = self.root / "proc" / str(pid)
        if entry.exists():
            for item in entry.iterdir():
                item.unlink()
            entry.rmdir()

    def run(self, argv, timeout=20):
        self.commands.append(argv)
        if argv[0] == "journalctl":
            return SimpleNamespace(stdout="\n".join(json.dumps(j) for j in self.journal))
        if argv[0] == "usermod":
            user = argv[2]
            self.shadow[user] = ("!" + self.shadow[user]) if argv[1] == "-L" else self.shadow[user].lstrip("!")
            self.write_accounts()
        if argv[0] == "userdel":
            self.users.pop(argv[2])
            self.shadow.pop(argv[2])
            self.write_accounts()
        return SimpleNamespace(stdout="")

    def configure(self, **values):
        self.limiter.save_config({**lim.DEFAULTS, "enabled": True, **values})

    def cycle(self, advance=0):
        self.clock += advance
        with self.limiter.state_lock():
            return self.limiter.cycle()

    def changes(self):
        return [c for c in self.commands if c[0] != "journalctl"]


class LimiterTests(unittest.TestCase):
    def setUp(self):
        self.box = Box(self)

    def test_counts_openssh_and_dropbear_never_listeners(self):
        box = self.box
        box.ssh(200, "alice")
        box.ssh(210, "alice")
        box.dropbear(301, "bob")
        box.dropbear(302, "alice")
        box.dropbear(303, "x", logged=False)        # never authenticated
        live = box.limiter.sessions()
        self.assertEqual({u: [p for _, p, _ in s] for u, s in live.items()},
                         {"alice": [201, 211, 302], "bob": [301]})
        for listener in (1, 100, 300, 200, 210):
            self.assertNotIn(listener, [p for s in live.values() for _, p, _ in s])

    def test_non_vpn_users_are_never_counted(self):
        self.box.add(400, "sshd", 100, 0)
        self.box.add(401, "sshd", 400, 1000)        # admin with a real shell
        self.assertEqual(self.box.limiter.sessions(), {})

    def test_reconnect_overlap_inside_grace_is_ignored(self):
        box = self.box
        box.configure(limit=1, mode="enforce", grace=60)
        box.ssh(200, "alice")
        box.ssh(210, "alice")                       # new connection, old not yet dead
        box.cycle()
        box.remove(201)                             # stale session times out
        box.cycle(advance=30)
        box.cycle(advance=60)
        self.assertEqual(box.killed, [])
        self.assertEqual(box.limiter.state["strikes"], {})

    def test_persistent_overuse_closes_only_oldest_and_keeps_newest(self):
        box = self.box
        box.configure(limit=2, mode="enforce", grace=60)
        box.ssh(200, "alice")
        box.ssh(210, "alice")
        box.ssh(220, "alice")
        box.ssh(230, "bob")
        box.cycle()
        self.assertEqual(box.killed, [])
        box.cycle(advance=61)
        self.assertEqual(box.killed, [201])         # oldest alice session only
        self.assertTrue((box.root / "proc/211").exists())
        self.assertTrue((box.root / "proc/231").exists())  # bob untouched
        self.assertEqual(box.changes(), [])         # no lock, no account command

    def test_watch_mode_logs_but_never_acts(self):
        box = self.box
        box.configure(limit=1, mode="watch", grace=0, strikes=2)
        box.ssh(200, "alice")
        box.ssh(210, "alice")
        for _ in range(5):
            box.cycle(advance=10)
        self.assertEqual(box.killed, [])
        self.assertEqual(box.changes(), [])
        log = box.limiter.log_path.read_text()
        self.assertIn("WATCH alice: 2/1 logins — would close 1 oldest", log)
        self.assertIn("would LOCK", log)

    def share(self, rounds, **cfg):
        """Two people fighting over a one-login account."""
        box = self.box
        box.configure(limit=1, mode="enforce", grace=0, strikes=3, **cfg)
        pid = 200
        box.ssh(pid, "alice")
        for _ in range(rounds):
            pid += 10
            box.ssh(pid, "alice")
            box.cycle(advance=30)
        return pid

    def test_repeated_sharing_locks_account_and_closes_all(self):
        box = self.box
        last = self.share(3)
        self.assertIn(["usermod", "-L", "alice"], box.commands)
        self.assertFalse((box.root / f"proc/{last + 1}").exists())
        self.assertIn("alice", box.limiter.state["locked"])
        self.assertTrue(box.shadow["alice"].startswith("!"))

    def test_delete_after_delay_for_menu_account(self):
        box = self.box
        self.share(3, delete_after=60)
        box.cycle(advance=59 * 60)
        self.assertNotIn(["userdel", "-f", "alice"], box.commands)
        box.cycle(advance=2 * 60)
        self.assertIn(["userdel", "-f", "alice"], box.commands)
        self.assertNotIn("alice", box.users)
        self.assertNotIn("alice", box.limiter.state["locked"])

    def test_website_account_is_revoked_not_userdel(self):
        box = self.box
        box.configure(limit=1, mode="enforce", grace=0, strikes=1, delete_after=1)
        box.ssh(200, "web")
        box.ssh(210, "web")
        box.cycle()
        box.cycle(advance=61)
        self.assertIn(["chage", "-E", "1970-01-02", "web"], box.commands)
        self.assertNotIn(["userdel", "-f", "web"], box.commands)
        self.assertTrue(box.limiter.state["locked"]["web"]["deleted"])

    def test_lock_only_mode_never_deletes(self):
        box = self.box
        self.share(3, delete_after=0)
        box.cycle(advance=10 * 86400)
        self.assertFalse(any(c[0] in ("userdel", "chage") for c in box.commands))

    def test_admin_unlock_outside_limiter_cancels_deletion(self):
        box = self.box
        self.share(3, delete_after=5)
        box.shadow["alice"] = "$6$newpassword"
        box.write_accounts()
        box.cycle(advance=6 * 60)
        self.assertNotIn(["userdel", "-f", "alice"], box.commands)
        self.assertNotIn("alice", box.limiter.state["locked"])

    def test_unlock_restores_only_what_limiter_locked(self):
        box = self.box
        self.share(3)
        box.limiter.unlock("alice")
        self.assertIn(["usermod", "-U", "alice"], box.commands)
        self.assertFalse(box.shadow["alice"].startswith("!"))
        self.assertEqual(box.limiter.state["locked"], {})
        with self.assertRaises(ValueError):
            box.limiter.unlock("alice")

    def test_previously_locked_account_stays_locked_after_unlock(self):
        box = self.box
        box.shadow["alice"] = "!$6$hash"
        box.write_accounts()
        self.share(3)
        self.assertNotIn(["usermod", "-L", "alice"], box.commands)
        box.limiter.unlock("alice")
        self.assertNotIn(["usermod", "-U", "alice"], box.commands)
        self.assertTrue(box.shadow["alice"].startswith("!"))

    def test_busy_account_operation_defers_lock(self):
        box = self.box
        import fcntl
        box.limiter.account_lock_path.parent.mkdir(parents=True, exist_ok=True)
        holder = open(box.limiter.account_lock_path, "a")
        fcntl.flock(holder, fcntl.LOCK_EX)
        self.share(3)
        self.assertNotIn(["usermod", "-L", "alice"], box.commands)
        self.assertEqual(box.limiter.state["pending"], ["alice"])
        fcntl.flock(holder, fcntl.LOCK_UN)
        holder.close()
        box.cycle(advance=5)
        self.assertIn(["usermod", "-L", "alice"], box.commands)
        self.assertEqual(box.limiter.state["pending"], [])

    def test_close_refuses_replaced_or_unexpected_processes(self):
        box = self.box
        accounts = box.limiter.accounts()
        start = box.ssh(200, "alice")
        box.remove(201)
        box.add(201, "bash", 1, 1001, start=start)            # different program
        self.assertFalse(box.limiter.close([start, 201, "openssh"], "alice", accounts))
        box.remove(201)
        box.add(201, "sshd", 200, 1001, start=start + 5)      # PID reused later
        self.assertFalse(box.limiter.close([start, 201, "openssh"], "alice", accounts))
        self.assertFalse(box.limiter.close([1000, 100, "openssh"], "alice", accounts))
        self.assertFalse(box.limiter.close([3000, 300, "dropbear"], "alice", accounts))
        self.assertEqual(box.killed, [])

    def test_dropbear_old_log_line_for_reused_pid_is_ignored(self):
        box = self.box
        start = box.dropbear(301, "bob", logged=False)
        stamp = BOOT + start / HZ - 3600                       # older process, same PID
        box.journal.append({"_PID": "301", "__REALTIME_TIMESTAMP": str(int(stamp * 1e6)),
                            "MESSAGE": "Password auth succeeded for 'bob' from 9.9.9.9:1"})
        self.assertEqual(box.limiter.sessions(), {})

    def test_journal_failure_counts_and_kills_nothing(self):
        box = self.box
        box.dropbear(301, "bob")
        box.limiter.run = lambda *a, **k: (_ for _ in ()).throw(subprocess.CalledProcessError(1, "journalctl"))
        self.assertEqual(box.limiter.sessions(), {})
        self.assertIn("journal", box.limiter.log_path.read_text())

    def test_dropbear_lookup_is_cached(self):
        box = self.box
        box.dropbear(301, "bob")
        box.limiter.sessions()
        box.limiter.sessions()
        self.assertEqual(sum(c[0] == "journalctl" for c in box.commands), 1)

    def test_per_account_limit_override(self):
        box = self.box
        box.configure(limit=1, mode="enforce", grace=0, overrides={"alice": 3})
        for pid in (200, 210, 220):
            box.ssh(pid, "alice")
        box.cycle()
        box.cycle(advance=30)
        self.assertEqual(box.killed, [])

    def test_config_validation_and_bad_file_keeps_last_good(self):
        box = self.box
        for bad in ({"limit": 0}, {"limit": True}, {"mode": "kill"}, {"overrides": {"a": 99}}):
            with self.assertRaises(ValueError):
                box.limiter.save_config({**lim.DEFAULTS, **bad})
        box.configure(limit=3)
        self.assertEqual(box.limiter.config()["limit"], 3)
        box.limiter.config_path.write_text("{broken")
        self.assertEqual(box.limiter.config()["limit"], 3)

    def test_menu_embeds_exact_engine_and_lives_only_in_api_menu(self):
        source = Path("scripts/ssh-ssl-setup.sh").read_text()
        menu = source.split("<<'MENUEOF'\n", 1)[1].split("\nMENUEOF", 1)[0]
        embedded = menu.split("<<'LIMITERPY'\n", 1)[1].split("\nLIMITERPY", 1)[0]
        self.assertEqual(embedded, Path("scripts/login-limiter.py").read_text().rstrip("\n"))
        api = menu.split("node_api_menu() {", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("7) limiter_menu;;", api)
        main = menu.rsplit("while true; do", 1)[1]
        self.assertNotIn("limiter", main)            # not on the main menu


if __name__ == "__main__":
    unittest.main()
