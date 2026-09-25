#!/usr/bin/env python3
"""
Verifies the write paths the config tool uses, on ONE test card.

For each probe: read the current value, POST a harmless change, read back,
POST the original value again, read back once more. Prints a table showing
whether the device accepted the write shape and whether the restore worked.

Only run this against a device that is NOT on air:

    python tools/probe-writes.py 10.101.12.162 --test-card

Covers the settings marked "unverified" in the tool that can be changed
without affecting signal or management access. Deliberately NOT probed:
IP/interfaces, hostname, FEC, SFP pinout, colour bars, flow destinations
(verify those by hand if needed).
"""
import json
import sys
import time
import urllib.error
import urllib.request

API = "/emsfp/node/v1/"


def get(ip, path):
    with urllib.request.urlopen("http://" + ip + API + path, timeout=6) as r:
        return json.loads(r.read())


def post(ip, path, body):
    req = urllib.request.Request("http://" + ip + API + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status, r.read().decode("utf-8", "replace")[:120]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:120]


def dig(d, path):
    for k in path:
        if isinstance(k, int) and isinstance(d, dict) and k == 0:
            continue
        d = d[k]
    return d


def nest(path, value):
    body = value
    for k in reversed([k for k in path if not isinstance(k, int)]):
        body = {k: body}
    return body


def first_child(ip, collection):
    return str(get(ip, collection + "/")[0]).rstrip("/")


def probes(ip):
    sdi = "sdi_output/" + first_child(ip, "sdi_output")
    cs = "clean_switch/" + first_child(ip, "clean_switch")
    flow = "flows/" + first_child(ip, "flows")
    refclk = get(ip, "refclk")
    ptp = "refclk/" + refclk["uuid"][0]
    toggle01 = lambda v: "0" if str(v) == "1" else "1"
    return [
        ("syslog monitoring flag", "self/syslog", ["monitoring", "common", "temp_event"], lambda v: not v),
        ("syslog port", "self/syslog", ["config", "port"], lambda v: 5514 if v != 5514 else 514),
        ("protocols mdns", "self/protocols", ["mdns_enable"], toggle01),
        ("lldp rate", "lldp", ["configuration", "rate"], lambda v: "30" if str(v) != "30" else "45"),
        ("igmp version", "self/system", ["igmp", "version"], lambda v: 2 if v == 3 else 3),
        ("nmos registry_address_2", "self/diag/nmos", ["registry_address_2"],
         lambda v: "10.0.0.1:8080" if v != "10.0.0.1:8080" else "0.0.0.0:0"),
        ("ptp announce timeout", "refclk", ["announceReceiptTimeout"], lambda v: 4 if v != 4 else 3),
        ("ptp dscp", ptp, ["dscp"], lambda v: "47" if str(v) != "47" else "46"),
        ("sdi vpid source", sdi, ["vpid", "source"], lambda v: "passthrough" if v != "passthrough" else "regenerated"),
        ("sdi audio delay", sdi, ["line_offset", "audio_delay"], lambda v: "1" if str(v) != "1" else "0"),
        ("clean switch igmp delay", cs, ["clean_switch", "igmp_setup_delay"],
         lambda v: "250" if str(v) != "250" else "300"),
        ("flow rtp_pt", flow, ["network", 0, "rtp_pt"], lambda v: "100" if str(v) != "100" else "96"),
        ("flow src filter", flow, ["network", 0, "pkt_filter_src_ip"], toggle01),
    ]


def main():
    if len(sys.argv) < 3 or sys.argv[2] != "--test-card":
        sys.exit(__doc__)
    ip = sys.argv[1]
    print(f"{'probe':28} {'orig':>14} {'test':>14} {'POST':>5} {'readback':>14} {'restored':>9}")
    for name, path, field, change in probes(ip):
        try:
            orig = dig(get(ip, path), field)
            test = change(orig)
            st, _ = post(ip, path, nest(field, test))
            time.sleep(0.7)
            back = dig(get(ip, path), field)
            post(ip, path, nest(field, orig))
            time.sleep(0.7)
            restored = str(dig(get(ip, path), field)) == str(orig)
            took = "OK " if str(back) == str(test) else "NO "
            print(f"{name:28} {str(orig):>14} {str(test):>14} {st:>5} {took + str(back):>14} {'yes' if restored else 'NO!':>9}")
        except Exception as e:
            print(f"{name:28} error: {e}")


if __name__ == "__main__":
    main()
