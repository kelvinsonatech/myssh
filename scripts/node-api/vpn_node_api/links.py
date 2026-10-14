"""Build client details from the live native configuration, never guessed ports."""
import base64
import json
from urllib.parse import quote, urlencode


def xray_connections(inbounds, protocol, secret, user, host):
    connections, links = [], []
    for inbound in inbounds:
        if inbound.get("protocol") != protocol:
            continue
        stream = inbound.get("streamSettings", {})
        network, security = stream.get("network", "tcp"), stream.get("security", "none")
        settings = stream.get(network + "Settings", {})
        path = settings.get("path", "")
        header_type = stream.get("tcpSettings", {}).get("header", {}).get("type", "none")
        if header_type == "http":
            path = stream["tcpSettings"]["header"].get("request", {}).get("path", ["/"])[0]
        name = f"{'HTTP' if header_type == 'http' else network.upper()} {security.upper()}"
        entry = {"name": name, "network": network, "security": security,
                 "port": inbound["port"], "host_header": host}
        if path:
            entry["path"] = path
        if security == "tls":
            entry["sni"] = host
        if header_type != "none":
            entry["header_type"] = header_type
        if network == "grpc":
            entry["service_name"] = settings.get("serviceName", "")
        connections.append(entry)
        if protocol == "vmess":
            obj = {"v": "2", "ps": f"{user}-{name}", "add": host,
                   "port": str(inbound["port"]), "id": secret, "aid": "0", "scy": "auto",
                   "net": network, "type": header_type, "host": host, "path": path,
                   "tls": security if security == "tls" else "", "sni": host}
            uri = "vmess://" + base64.b64encode(json.dumps(obj).encode()).decode()
        else:
            query = {"type": network, "security": security}
            if protocol == "vless":
                query["encryption"] = "none"
            if security == "tls":
                query["sni"] = host
            if path:
                query.update(path=path, host=host)
            if header_type != "none":
                query["headerType"] = header_type
            if network == "grpc":
                query["serviceName"] = settings.get("serviceName", "")
            address = f"[{host}]" if ":" in host else host
            uri = f"{protocol}://{quote(secret, safe='')}@{address}:{inbound['port']}?" + urlencode(query)
            uri += "#" + quote(f"{user}-{name}", safe="")
        links.append({"name": name, "uri": uri})
    return connections, links
