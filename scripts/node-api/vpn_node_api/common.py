import contextlib
import datetime as dt
import fcntl
import json
import os
import re
import sqlite3
import tempfile
import uuid
from pathlib import Path

PROTOCOLS = ("ssh", "ssh-ssl", "slowdns", "vmess", "vless", "trojan",
             "hysteria", "hysteria2", "zivpn")
SSH = ("ssh", "ssh-ssl", "slowdns")
XRAY = ("vmess", "vless", "trojan")
RESERVED = {"root", "admin", "daemon", "bin", "sys", "nobody", "sshd",
            "www-data", "ubuntu", "debian"}
USER = re.compile(r"[a-z][a-z0-9_]{2,31}\Z")
PASSWORD = re.compile(r"[A-Za-z0-9@#%^*_+=.,:~-]{6,32}\Z")


class Fault(Exception):
    def __init__(self, code, message, status=422, retryable=False):
        super().__init__(message)
        self.code, self.status, self.retryable = code, status, retryable

    def json(self):
        return {"ok": False, "error": {"code": self.code, "message": str(self),
                                       "retryable": self.retryable}}


def invalid(message):
    raise Fault("VALIDATION_FAILED", message)


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def stamp(value=None):
    return (value or utcnow()).strftime("%Y-%m-%dT%H:%M:%SZ")


def uuid_value(value):
    if not isinstance(value, str):
        invalid("request_id must be a UUID")
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        invalid("request_id must be a UUID")
    if str(parsed) != value.lower():
        invalid("request_id must be a canonical UUID")
    return value


def validate_create(body):
    if not isinstance(body, dict) or set(body) - {
        "request_id", "protocol", "username", "password", "duration_days", "options"
    }:
        invalid("Unexpected account fields")
    uuid_value(body.get("request_id"))
    protocol, user = body.get("protocol"), body.get("username")
    if protocol not in PROTOCOLS:
        invalid("Unknown protocol")
    if not isinstance(user, str) or not USER.fullmatch(user) or user in RESERVED:
        invalid("Invalid or reserved username")
    days(body.get("duration_days"))
    if body.get("options", {}) != {}:
        invalid("This node supports empty options only")
    password = body.get("password")
    if protocol in SSH and password is None:
        invalid("A password is required for SSH accounts")
    if password is not None and (not isinstance(password, str) or
                                  not PASSWORD.fullmatch(password)):
        invalid("Password must match the contract's 6–32 character pattern")


def days(value):
    if type(value) is not int or not 1 <= value <= 365:
        invalid("duration_days must be an integer from 1 to 365")
    return value


def atomic(path, value, mode=0o600):
    path = Path(path)
    content = value if isinstance(value, bytes) else value.encode()
    fd, temp = tempfile.mkstemp(prefix=".node-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            os.fchmod(output.fileno(), mode)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def write_json(path, value, mode=0o600):
    atomic(path, json.dumps(value, indent=2) + "\n", mode)


@contextlib.contextmanager
def account_lock(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Fault("REQUEST_IN_PROGRESS", "Another account operation is running", 409, True)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)
        self.path = self.directory / "state.sqlite3"
        with self.connect() as db:
            db.executescript("""
              CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL,
                document TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0);
              CREATE TABLE IF NOT EXISTS requests (
                id TEXT PRIMARY KEY, hash TEXT NOT NULL, state TEXT NOT NULL,
                operation TEXT NOT NULL, document TEXT NOT NULL, result TEXT,
                created INTEGER NOT NULL DEFAULT (unixepoch()));
              CREATE TABLE IF NOT EXISTS nonces (
                nonce TEXT PRIMARY KEY, expires INTEGER NOT NULL);
            """)
        os.chmod(self.path, 0o600)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        return db

    def nonce(self, nonce, now):
        with self.connect() as db:
            db.execute("DELETE FROM nonces WHERE expires <= ?", (now,))
            try:
                db.execute("INSERT INTO nonces VALUES (?,?)", (nonce, now + 600))
            except sqlite3.IntegrityError:
                raise Fault("REPLAYED_NONCE", "Nonce has already been used", 401)

    def account(self, identity, by_request=False):
        with self.connect() as db:
            key = "request_id" if by_request else "id"
            row = db.execute(f"SELECT document FROM accounts WHERE {key}=? AND deleted=0",
                             (identity,)).fetchone()
        if not row:
            raise Fault("ACCOUNT_NOT_FOUND", "Account not found", 404)
        return json.loads(row[0])

    def finish(self, request_id, account, operation, result):
        with self.connect() as db:
            if operation == "create":
                db.execute("INSERT OR REPLACE INTO accounts VALUES (?,?,?,0)",
                           (account["account_id"], account["request_id"], json.dumps(account)))
            elif operation == "delete":
                db.execute("UPDATE accounts SET deleted=1 WHERE id=?", (account["account_id"],))
            else:
                db.execute("UPDATE accounts SET document=? WHERE id=?",
                           (json.dumps(account), account["account_id"]))
            db.execute("UPDATE requests SET state='done', result=? WHERE id=?",
                       (json.dumps(result), request_id))
