import copy
import datetime as dt
import hashlib
import json

from . import VERSION
from .common import Fault, SSH, XRAY, Store, account_lock, days, invalid, stamp, utcnow, uuid_value, validate_create
from .native import Uncertain

LIMITER_STATE = "/var/lib/vpn-login-limiter/state.json"
LIMITER_CONFIG = "/etc/vpn-login-limiter/config.json"


class Engine:
    def __init__(self, config, native, store, lock="/run/lock/vpn-node-accounts.lock"):
        self.config, self.native, self.store, self.lock = config, native, store, lock

    def suspensions(self):
        """SSH accounts the multi-login limiter has locked (read-only view)."""
        try:
            locked = json.loads(self.native.text(LIMITER_STATE) or "{}").get("locked")
            limiter = json.loads(self.native.text(LIMITER_CONFIG) or "{}")
        except (ValueError, OSError, AttributeError):
            return {}
        if not isinstance(locked, dict) or not isinstance(limiter, dict):
            return {}
        delay = limiter.get("delete_after", 0)
        deleting = limiter.get("mode") == "enforce" and isinstance(delay, int) and delay > 0
        result = {}
        for user, info in locked.items():
            if not isinstance(info, dict) or not isinstance(info.get("at"), (int, float)):
                continue
            if not self.native.password_locked(user):
                continue                     # unlocked by an administrator
            since = dt.datetime.fromtimestamp(info["at"], dt.timezone.utc)
            removed = bool(info.get("deleted"))
            result[user] = {"reason": "multi_login", "suspended_at": stamp(since),
                            "removed": removed,
                            "removal_at": stamp(since + dt.timedelta(minutes=delay))
                            if deleting and not removed else None}
        return result

    def public(self, account, suspended=None):
        result = copy.deepcopy(account)
        if result["expires_at"] <= stamp():
            result["status"] = "expired"
        if result["protocol"] in SSH:
            if suspended is None:
                suspended = self.suspensions()
            if result["username"] in suspended:
                result["status"] = "disabled"
                result["disabled_reason"] = "multi_login"
                result["suspension"] = suspended[result["username"]]
        return result

    def info(self):
        protocols = [p["id"] for p in self.native.protocols()]
        with self.store.connect() as db:
            pending = db.execute("SELECT count(*) FROM requests WHERE state='pending'").fetchone()[0]
        return {"api_version": "1.0", "agent_version": VERSION,
                "status": "degraded" if pending else "online",
                "hostname": self.config["hostname"], "public_ip": self.config["public_ip"],
                "country": self.config.get("country", ""), "server_time": stamp(), "protocols": protocols,
                "capabilities": {"idempotency": True, "lookup_by_request_id": True, "renew": True,
                                 "list_accounts": True, "restart_agent": False,
                                 "expiry_managed_by_node": False, "suspension_status": True}}

    def lookup(self, value, by_request=False):
        try:
            account = self.store.account(value, by_request)
        except Fault:
            if by_request:
                with self.store.connect() as db:
                    row = db.execute("SELECT state FROM requests WHERE id=?", (value,)).fetchone()
                if row and row["state"] == "pending":
                    raise Fault("REQUEST_IN_PROGRESS", "Creation is awaiting reconciliation", 409, True)
            raise
        if not self.native.owns(account):
            raise Fault("ACCOUNT_NOT_FOUND", "Native account no longer belongs to this record", 404)
        return {"account": self.public(account)}

    def listing(self, query):
        if set(query) - {"protocol", "cursor", "limit"}:
            invalid("Unknown list parameter")
        limit = query.get("limit", "100")
        if not limit.isdigit() or not 1 <= int(limit) <= 200:
            invalid("limit must be 1–200")
        cursor = query.get("cursor") or ""
        if cursor and (not cursor.startswith("acc_") or len(cursor) > 80):
            invalid("Invalid cursor")
        protocol = query.get("protocol")
        if protocol and protocol not in (*SSH, *XRAY, "hysteria", "hysteria2", "zivpn"):
            invalid("Unknown protocol")
        suspended = self.suspensions()
        with self.store.connect() as db:
            rows = db.execute("SELECT document FROM accounts WHERE id>? AND deleted=0 ORDER BY id", (cursor,))
            accounts = []
            for row in rows:
                account = json.loads(row[0])
                if not protocol or account["protocol"] == protocol:
                    accounts.append(self.public(account, suspended))
                if len(accounts) > int(limit):
                    break
        more = len(accounts) > int(limit)
        accounts = accounts[:int(limit)]
        return {"accounts": accounts, "next_cursor": accounts[-1]["account_id"] if more else None}

    def mutate(self, operation, body, account_id, key):
        uuid_value(key)
        if operation == "create":
            validate_create(body)
        elif operation == "renew":
            if not isinstance(body, dict) or set(body) != {"request_id", "duration_days"}:
                invalid("Renew requires request_id and duration_days")
            uuid_value(body["request_id"])
            days(body["duration_days"])
        elif body:
            invalid("DELETE must have an empty body")
        if operation != "delete" and body["request_id"] != key:
            invalid("Idempotency-Key must equal request_id")
        payload_hash = hashlib.sha256(json.dumps(
            [operation, account_id, body], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        with account_lock(self.lock):
            with self.store.connect() as db:
                previous = db.execute("SELECT * FROM requests WHERE id=?", (key,)).fetchone()
                if previous:
                    if previous["hash"] != payload_hash:
                        raise Fault("IDEMPOTENCY_CONFLICT", "request_id already has a different payload", 409)
                    if previous["state"] == "pending":
                        raise Fault("REQUEST_IN_PROGRESS", "Request is being processed or recovered", 409, True)
                    cached = json.loads(previous["result"])
                    if previous["state"] == "failed":
                        raise Fault(**cached)
                    if operation == "create":
                        cached["replayed"] = True
                    return 200, cached
                # A crashed operation must be reconciled, not overwritten by a
                # second request with a different id.
                if db.execute("SELECT 1 FROM requests WHERE state='pending' LIMIT 1").fetchone():
                    raise Fault("REQUEST_IN_PROGRESS", "Node has a pending operation; check API status", 409, True)
                if operation == "create":
                    capacity = self.config.get("max_accounts", 0)
                    count = db.execute("SELECT count(*) FROM accounts WHERE deleted=0").fetchone()[0]
                    if capacity and count >= capacity:
                        raise Fault("CAPACITY_REACHED", "API account capacity reached", 409)
                    rows = db.execute("SELECT document FROM accounts WHERE deleted=0").fetchall()
                    for row in rows:
                        other = json.loads(row[0])
                        if other["protocol"] == body["protocol"] and other["username"] == body["username"]:
                            raise Fault("USERNAME_TAKEN", "Username already exists", 409)
                    account = self.native.prepare(body)
                else:
                    account = self.store.account(account_id)
                    if operation == "renew":
                        if account["protocol"] in SSH and account["username"] in self.suspensions():
                            raise Fault("ACCOUNT_SUSPENDED", "Account is locked for multi-login abuse; "
                                        "unlock it in the server menu before renewing", 409)
                        end = dt.datetime.fromisoformat(account["expires_at"].replace("Z", "+00:00"))
                        end = max(end, utcnow()) + dt.timedelta(days=body["duration_days"])
                        account["expires_at"], account["status"] = stamp(end), "active"
                db.execute("INSERT INTO requests(id,hash,state,operation,document) VALUES (?,?,'pending',?,?)",
                           (key, payload_hash, operation, json.dumps(account)))
            try:
                self.native.perform(operation, account)
            except Uncertain:
                raise Fault("REQUEST_IN_PROGRESS", "Recovery required; no duplicate operation will run", 409, True)
            except Fault as fault:
                with self.store.connect() as db:
                    db.execute("UPDATE requests SET state='failed', result=? WHERE id=?",
                               (json.dumps({"code": fault.code, "message": str(fault),
                                            "status": fault.status, "retryable": fault.retryable}), key))
                raise
            except Exception:
                # Unknown failures remain journaled. Never retry native creation
                # blindly if we cannot establish whether the mutation happened.
                raise Fault("REQUEST_IN_PROGRESS", "Operation requires recovery; inspect API status", 409, True)
            result = self.result(operation, account)
            self.store.finish(key, account, operation, result)
            return (201 if operation == "create" else 200), result

    @staticmethod
    def result(operation, account):
        if operation == "delete":
            return {"revoked": True}
        result = {"account": account}
        if operation == "create":
            result["replayed"] = False
        return result

    def recover(self):
        """Explicit/startup recovery reuses recorded credentials and expiry."""
        with account_lock(self.lock):
            with self.store.connect() as db:
                rows = db.execute("SELECT * FROM requests WHERE state='pending'").fetchall()
            for row in rows:
                account, operation = json.loads(row["document"]), row["operation"]
                if operation == "delete" and not self.native.owns(account):
                    pass
                elif operation == "create" and self.native.owns(account):
                    if account["protocol"] in SSH:
                        self.native.run(["chpasswd"], account["username"] + ":" + account["config"]["password"] + "\n")
                    else:
                        self.native.perform("create", account)
                else:
                    self.native.perform(operation, account)
                self.store.finish(row["id"], account, operation, self.result(operation, account))
