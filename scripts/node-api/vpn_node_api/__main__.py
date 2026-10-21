import json
import logging
import os
import ssl
from pathlib import Path

import uvicorn

from .common import Store
from .engine import Engine
from .native import Native
from .server import app_for


def main():
    os.umask(0o077)
    path = Path("/etc/vpn-node-api/config.json")
    if path.stat().st_mode & 0o077:
        raise SystemExit("API config must have permissions 600")
    config = json.loads(path.read_text())
    store = Store("/var/lib/vpn-node-api")
    engine = Engine(config, Native(config), store)
    # Never blindly replay unfinished native mutations after a power failure.
    # The durable journal blocks new writes until reviewed by the administrator.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    uvicorn.run(app_for(config, engine,
                       access_loader=lambda: json.loads(path.read_text())["website_ips"]),
                host="0.0.0.0", port=config["port"],
                workers=1, access_log=False, proxy_headers=False, server_header=False,
                ssl_certfile=config["certificate"], ssl_keyfile=config["private_key"],
                ssl_version=ssl.PROTOCOL_TLS_SERVER, timeout_keep_alive=5,
                limit_concurrency=32)


if __name__ == "__main__":
    main()
