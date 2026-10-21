"""Authenticated JSON surface. No proxy trust, anonymous routes or access logs."""
import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import re
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from .common import Fault
from .access import normalize, permits

MAX_BODY = 65536
LOG = logging.getLogger("vpn-node-api")


def authenticate(headers, method, target, body, config, store, now=None):
    names = ("x-node-key-id", "x-node-timestamp", "x-node-nonce",
             "x-node-signature", "x-request-id")
    if any(not headers.get(name) for name in names):
        raise Fault("UNAUTHORIZED", "Authentication headers required", 401)
    key, timestamp, nonce, signature, trace = (headers[name] for name in names)
    try:
        uuid.UUID(trace)
    except (ValueError, TypeError):
        raise Fault("UNAUTHORIZED", "Invalid request identifier", 401)
    if (key != config["key_id"] or not re.fullmatch(r"[0-9]{1,12}", timestamp)
            or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", nonce)):
        raise Fault("UNAUTHORIZED", "Invalid authentication headers", 401)
    now = int(time.time()) if now is None else now
    if abs(now - int(timestamp)) > 300:
        raise Fault("CLOCK_SKEW", "Synchronize the website and server clocks", 401)
    canonical = "\n".join(("VL1-HMAC-SHA256", method.upper(), target, timestamp,
                           nonce, hashlib.sha256(body).hexdigest()))
    expected = "v1=" + hmac.new(config["secret"].encode(), canonical.encode(),
                               hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature.encode(), expected.encode()):
        raise Fault("INVALID_SIGNATURE", "Signature does not match", 401)
    store.nonce(nonce, now)
    return trace


def app_for(config, engine, access_loader=None):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None,
                  redirect_slashes=False)
    normalize(config["website_ips"])
    load_access = access_loader or (lambda: config["website_ips"])

    @app.middleware("http")
    async def gateway(request: Request, call_next):
        trace = None
        try:
            # Trust only the TCP peer, never Forwarded/X-Forwarded-For.
            try:
                peer = ipaddress.ip_address(request.client.host)
            except (ValueError, AttributeError):
                raise Fault("UNAUTHORIZED", "Source address not allowed", 401)
            try:
                permitted = permits(str(peer), load_access())
            except (ValueError, OSError, KeyError):
                permitted = False
            if not permitted:
                raise Fault("UNAUTHORIZED", "Source address not allowed", 401)
            protected = {"x-node-key-id", "x-node-timestamp", "x-node-nonce",
                         "x-node-signature", "x-request-id", "idempotency-key"}
            counts = {}
            for key, _ in request.scope["headers"]:
                name = key.decode("latin-1").lower()
                counts[name] = counts.get(name, 0) + 1
            if any(counts.get(name, 0) > 1 for name in protected):
                raise Fault("UNAUTHORIZED", "Duplicate authentication header", 401)
            body = bytearray()
            async def read_body():
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > MAX_BODY:
                        raise Fault("VALIDATION_FAILED", "Body exceeds 64 KiB", 413)
            await asyncio.wait_for(read_body(), timeout=10)
            raw = bytes(body)
            target = request.scope["raw_path"].decode("ascii")
            if request.scope["query_string"]:
                target += "?" + request.scope["query_string"].decode("ascii")
            trace = await run_in_threadpool(authenticate, request.headers,
                                           request.method, target, raw, config, engine.store)
            if raw and request.headers.get("content-type", "").split(";")[0].lower() != "application/json":
                raise Fault("VALIDATION_FAILED", "Content-Type must be application/json", 422)
            try:
                parsed = json.loads(raw) if raw else {}
            except (ValueError, UnicodeError):
                raise Fault("VALIDATION_FAILED", "Invalid JSON", 422)
            path, method = request.url.path, request.method
            key = request.headers.get("idempotency-key")
            status, data = await run_in_threadpool(dispatch, engine, method, path,
                                                  dict(request.query_params), parsed, key)
            response = JSONResponse({"ok": True, "data": data}, status_code=status)
        except asyncio.TimeoutError:
            response = JSONResponse(Fault("VALIDATION_FAILED", "Request body timed out", 408).json(), 408)
        except Fault as fault:
            response = JSONResponse(fault.json(), fault.status)
        except Exception:
            # Do not log exception messages or tracebacks containing account data.
            response = JSONResponse(Fault("INTERNAL", "Node operation failed; check status", 500).json(), 500)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        if trace:
            response.headers["X-Request-Id"] = trace
            LOG.info("request_id=%s status=%s", trace, response.status_code)
        return response

    return app


def dispatch(engine, method, path, query, body, key):
    base = "/api/v1"
    if method == "GET":
        if path == base + "/info":
            return 200, engine.info()
        if path == base + "/health":
            return 200, engine.native.health()
        if path == base + "/protocols":
            return 200, {"protocols": engine.native.protocols()}
        if path == base + "/accounts":
            return 200, engine.listing(query)
        if path == base + "/stats":
            raise Fault("NOT_SUPPORTED", "Aggregate traffic statistics are unavailable", 501)
        match = re.fullmatch(base + r"/accounts/by-request/([A-Za-z0-9_-]{1,80})", path)
        if match:
            return 200, engine.lookup(match[1], by_request=True)
        match = re.fullmatch(base + r"/accounts/(acc_[A-Za-z0-9_-]{1,76})", path)
        if match:
            return 200, engine.lookup(match[1])
    if method == "POST" and path == base + "/accounts":
        return engine.mutate("create", body, None, key)
    match = re.fullmatch(base + r"/accounts/(acc_[A-Za-z0-9_-]{1,76})(/renew)?", path)
    if match:
        if method == "DELETE" and not match[2]:
            return engine.mutate("delete", body, match[1], key)
        if method == "POST" and match[2]:
            return engine.mutate("renew", body, match[1], key)
    raise Fault("NOT_SUPPORTED", "Endpoint or method not supported", 404)
