#!/usr/bin/env python3
"""Read-only reconnaissance for Nevion Virtuoso MuoN cards.

Finds out which management interfaces a MuoN exposes (HTTP/REST, NMOS,
Fusion-style emsfp API, SNMP, ...) and writes everything into a JSON
report. Only GET requests and SNMP GET are sent - nothing is changed on
the device.

Usage:
    python muon_recon.py 10.102.12.162
    python muon_recon.py 10.102.12.162 --out muon-report.json --timeout 3
    python muon_recon.py 10.101.12.162 --blue     # also probe 10.102.12.162

Python 3.8+, standard library only.
"""

import argparse
import json
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

TCP_PORTS = [21, 22, 23, 80, 443, 502, 830, 1883, 4000, 5000, 5353, 7000,
             8000, 8008, 8080, 8081, 8088, 8443, 8888, 9000, 9090, 9443,
             10000, 40008]

HTTP_PATHS = [
    "/", "/index.html", "/robots.txt",
    "/emsfp/node/v1/", "/emsfp/node/v1/self/",
    "/x-nmos/", "/x-nmos/node/", "/x-nmos/connection/",
    "/api", "/api/", "/api/v1/", "/api/v2/", "/rest/", "/rest/api/",
    "/swagger", "/swagger.json", "/openapi.json", "/api-docs",
    "/cgi-bin/", "/jsonrpc", "/rpc", "/status", "/info", "/version",
    "/config", "/system", "/device",
]

BODY_LIMIT = 4096
CRAWL_LIMIT = 150

_ctx = ssl.create_default_context()
# MuoN cards use self-signed certificates on the local management LAN.
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def probe_tcp(host, port, timeout):
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(1.0)
            banner = b""
            try:
                banner = s.recv(256)
            except OSError:
                pass
            return {"port": port, "open": True,
                    "banner": banner.decode("latin-1", "replace").strip()}
    except OSError as e:
        return {"port": port, "open": False, "error": type(e).__name__}


def http_get(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": "muon-recon/1.0",
                                               "Accept": "*/*"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ctx) as r:
            body = r.read(BODY_LIMIT + 1)
            status, headers = r.status, dict(r.headers)
    except urllib.error.HTTPError as e:
        body = e.read(BODY_LIMIT + 1) if e.fp else b""
        status, headers = e.code, dict(e.headers or {})
    except Exception as e:  # timeout, refused, TLS error, ...
        return {"url": url, "error": f"{type(e).__name__}: {e}"}
    text = body[:BODY_LIMIT].decode("utf-8", "replace")
    res = {"url": url, "status": status, "ms": int((time.time() - t0) * 1000),
           "server": headers.get("Server"),
           "content_type": headers.get("Content-Type"),
           "www_authenticate": headers.get("WWW-Authenticate"),
           "location": headers.get("Location"),
           "truncated": len(body) > BODY_LIMIT}
    try:
        res["json"] = json.loads(text)
    except ValueError:
        res["body"] = text
    return res


def crawl_json(base, root, timeout):
    """Walk a self-describing JSON API (emsfp / NMOS style: a GET on a
    collection returns a list of child paths ending in '/')."""
    seen, out, queue = set(), {}, [root]
    while queue and len(seen) < CRAWL_LIMIT:
        path = queue.pop(0)
        if path in seen:
            continue
        seen.add(path)
        r = http_get(base + path, timeout)
        out[path] = r.get("json", r.get("body", r.get("error")))
        data = r.get("json")
        if isinstance(data, list):
            for child in data:
                if isinstance(child, str) and child.endswith("/"):
                    queue.append(path + child.lstrip("/"))
    return out


# --- minimal SNMP v2c GET (BER encoded by hand, no dependencies) ---------

def _len(n):
    if n < 0x80:
        return bytes([n])
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(b)]) + b


def _tlv(tag, payload):
    return bytes([tag]) + _len(len(payload)) + payload


def _oid(dotted):
    parts = [int(p) for p in dotted.split(".")]
    out = bytes([40 * parts[0] + parts[1]])
    for p in parts[2:]:
        enc = [p & 0x7F]
        p >>= 7
        while p:
            enc.insert(0, 0x80 | (p & 0x7F))
            p >>= 7
        out += bytes(enc)
    return _tlv(0x06, out)


def _parse(buf, i=0):
    tag = buf[i]
    ln = buf[i + 1]
    i += 2
    if ln & 0x80:
        n = ln & 0x7F
        ln = int.from_bytes(buf[i:i + n], "big")
        i += n
    return tag, buf[i:i + ln], i + ln


def snmp_get(host, community, oids, timeout):
    varbinds = b"".join(_tlv(0x30, _oid(o) + b"\x05\x00") for o in oids)
    pdu = _tlv(0xA0, _tlv(0x02, b"\x01") + _tlv(0x02, b"\x00")
               + _tlv(0x02, b"\x00") + _tlv(0x30, varbinds))
    msg = _tlv(0x30, _tlv(0x02, b"\x01") + _tlv(0x04, community.encode())
               + pdu)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        s.sendto(msg, (host, 161))
        data, _ = s.recvfrom(65535)
    # message -> [version, community, response pdu]
    _, msg_body, _ = _parse(data)
    _, _, i = _parse(msg_body)
    _, _, i = _parse(msg_body, i)
    _, pdu_body, _ = _parse(msg_body, i)
    _, _, j = _parse(pdu_body)          # request id
    _, _, j = _parse(pdu_body, j)       # error status
    _, _, j = _parse(pdu_body, j)       # error index
    _, vbl, _ = _parse(pdu_body, j)
    values, k = [], 0
    while k < len(vbl):
        _, vb, k = _parse(vbl, k)
        _, _, m = _parse(vb)
        tag, val, _ = _parse(vb, m)
        if tag == 0x04:
            values.append(val.decode("utf-8", "replace"))
        elif tag in (0x02, 0x41, 0x42, 0x43):
            values.append(int.from_bytes(val, "big"))
        else:
            values.append(val.hex())
    return values


SNMP_OIDS = {
    "sysDescr": "1.3.6.1.2.1.1.1.0",
    "sysObjectID": "1.3.6.1.2.1.1.2.0",
    "sysUpTime": "1.3.6.1.2.1.1.3.0",
    "sysName": "1.3.6.1.2.1.1.5.0",
}


def probe_snmp(host, timeout):
    res = {}
    for community in ("public", "private"):
        try:
            vals = snmp_get(host, community, list(SNMP_OIDS.values()), timeout)
            res[community] = dict(zip(SNMP_OIDS, vals))
        except Exception as e:
            res[community] = {"error": f"{type(e).__name__}: {e}"}
    return res


# -------------------------------------------------------------------------

def recon(host, timeout):
    report = {"host": host, "started": time.strftime("%Y-%m-%dT%H:%M:%S")}

    log(f"[{host}] ping-free TCP scan of {len(TCP_PORTS)} ports ...")
    with ThreadPoolExecutor(max_workers=8) as ex:
        ports = list(ex.map(lambda p: probe_tcp(host, p, timeout), TCP_PORTS))
    report["tcp"] = ports
    open_ports = [p["port"] for p in ports if p["open"]]
    log(f"[{host}] open: {open_ports or 'none'}")

    report["http"] = {}
    for port in open_ports:
        for scheme in ("http", "https"):
            base = f"{scheme}://{host}:{port}"
            first = http_get(base + "/", timeout)
            if "error" in first:
                continue
            log(f"[{host}] {base} speaks {scheme.upper()} "
                f"(status {first['status']}, server {first.get('server')})")
            results = {"/": first}
            for path in HTTP_PATHS[1:]:
                results[path] = http_get(base + path, timeout)
            report["http"][base] = results

            for root in ("/emsfp/node/v1/", "/x-nmos/"):
                if isinstance(results.get(root, {}).get("json"), list):
                    log(f"[{host}] crawling {base}{root}")
                    report.setdefault("crawl", {})[base + root] = \
                        crawl_json(base, root, timeout)
            break  # this port answered with HTTP or HTTPS; skip the other

    log(f"[{host}] SNMP v2c GET (public/private) ...")
    report["snmp"] = probe_snmp(host, timeout)
    report["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    return report


def blue_of(ip):
    a, b, c, d = ip.split(".")
    return f"{a}.{int(b) + 1}.{c}.{d}"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("host")
    ap.add_argument("--timeout", type=float, default=3.0)
    ap.add_argument("--blue", action="store_true",
                    help="also probe the Blue address (second octet + 1)")
    ap.add_argument("--out", default=None,
                    help="report file (default muon-recon-<host>.json)")
    args = ap.parse_args()

    hosts = [args.host] + ([blue_of(args.host)] if args.blue else [])
    reports = [recon(h, args.timeout) for h in hosts]
    out = args.out or f"muon-recon-{args.host}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(reports if len(reports) > 1 else reports[0], f, indent=2,
                  ensure_ascii=False)
    log(f"report written to {out}")


if __name__ == "__main__":
    main()
