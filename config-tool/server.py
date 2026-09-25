#!/usr/bin/env python3
"""
MuoN / Fusion batch configuration tool - web service.

Reads the full configuration of Nevion/Macnica emsfp-platform devices (MuoN
cards, Fusion gateways), lets you define a settings profile once, apply it
to many devices at once, and override individual settings per device or per
I/O (SDI output, channel, flow, PTP port, SFP port) where the device has
them. Standard library only - nothing to pip install.

Safety design:
- Nothing is written without an explicit plan -> preview -> apply step.
- Settings are only planned for a device if its own GET response contains
  the field (capability by presence), so unsupported settings are skipped,
  never sent.
- Before the first write to a device, its complete current config is saved
  to backups/<ip>_<timestamp>.json; a backup can be loaded back as a
  per-device override set to restore it.
- After writing, every changed field is read back and compared; the result
  shows per field whether the device really took the value.
- Red (primary) is tried first, Blue (second octet +1) as fallback, same as
  the Fusion dashboard.
"""

import json
import logging
import logging.handlers
import os
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import catalog as cat

BASE_DIR = Path(__file__).resolve().parent
DEVICES_FILE = BASE_DIR / "devices.json"
DEVICES_EXAMPLE_FILE = BASE_DIR / "devices.example.json"
PROFILES_DIR = BASE_DIR / "profiles"
BACKUPS_DIR = BASE_DIR / "backups"
UI_FILE = BASE_DIR / "ui.html"
LOG_FILE = BASE_DIR / "config-tool.log"
# The Fusion dashboard's device list, offered as an import source.
DASHBOARD_DEVICES_FILE = Path(r"C:\Apps\Fusion Dashboard\RiedelFusionDashboard\fusion-dashboard-service\devices.json")

# Local-only by default: this tool writes to broadcast devices and has no
# login, so it should not be reachable from the network unless you decide
# so (set MUON_CONFIG_HOST=0.0.0.0 to open it up).
HOST = os.environ.get("MUON_CONFIG_HOST", "127.0.0.1")
PORT = int(os.environ.get("MUON_CONFIG_PORT", "8091"))
API = "/emsfp/node/v1/"
READ_CONCURRENCY = 8
APPLY_CONCURRENCY = 4
TIMEOUT = 6
READBACK_WAIT_SEC = 120
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

logger = logging.getLogger("muon-config")
logger.setLevel(logging.INFO)
_fh = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
_fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(_fh)
_ch = logging.StreamHandler()
_ch.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(_ch)

READS = {}  # ip -> last device read (see read_device)
READS_LOCK = threading.RLock()
APPLY_LOCK = threading.Lock()  # one apply at a time
MISSING = object()


# ---------------------------------------------------------------- device list

def load_devices():
    if not DEVICES_FILE.exists() and DEVICES_EXAMPLE_FILE.exists():
        DEVICES_FILE.write_text(DEVICES_EXAMPLE_FILE.read_text(encoding="utf-8"), encoding="utf-8")
    if not DEVICES_FILE.exists():
        return []
    return json.loads(DEVICES_FILE.read_text(encoding="utf-8"))


def save_devices(devices):
    DEVICES_FILE.write_text(json.dumps(devices, indent=2), encoding="utf-8")


def add_devices(new):
    devices = load_devices()
    known = {d["ip"] for d in devices}
    added = 0
    for d in new:
        ip = str(d.get("ip") or "").strip()
        if ip and ip not in known:
            devices.append({"ip": ip, "name": str(d.get("name") or ""), "tag": str(d.get("tag") or "")})
            known.add(ip)
            added += 1
    save_devices(devices)
    return added


# ---------------------------------------------------------------- HTTP to devices

def http_get(ip, path):
    req = urllib.request.Request("http://" + ip + API + path, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
        body = res.read().decode("utf-8", errors="replace")
    try:
        return json.loads(body)
    except ValueError:
        return body


def http_post(ip, path, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request("http://" + ip + API + path, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
            return res.status, res.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")


def derive_blue_ip(red_ip):
    parts = red_ip.split(".")
    if len(parts) != 4 or not all(p.isdigit() for p in parts):
        return None
    parts[1] = str(int(parts[1]) + 1)
    return ".".join(parts)


def reachable_address(ip):
    """Returns (address, 'red'|'blue') of whichever network answers."""
    try:
        http_get(ip, "")
        return ip, "red"
    except Exception as red_err:
        blue = derive_blue_ip(ip)
        if not blue:
            raise
        try:
            http_get(blue, "")
            return blue, "blue"
        except Exception:
            raise red_err


# ---------------------------------------------------------------- reading

def _children(ip, path):
    data = http_get(ip, path)
    return [str(x).rstrip("/") for x in data] if isinstance(data, list) else []


def read_device(ip):
    """Reads everything the catalog can touch. Returns the read record."""
    addr, net = reachable_address(ip)
    root = set(_children(addr, ""))
    endpoints, errors = {}, {}

    def grab(path):
        try:
            endpoints[path] = http_get(addr, path)
        except Exception as e:
            errors[path] = str(e)

    for path in ("self/information", "self/firmware", "self/license", "self/ipconfig", "self/interfaces",
                 "self/static_route", "self/syslog", "self/protocols", "self/system", "self/diag/nmos",
                 "self/phy"):
        grab(path)
    for path in ("refclk", "lldp", "sdi"):
        if path in root:
            grab(path)

    instances = {scope: [] for scope in cat.SCOPES}

    # channel (NMOS device) labels, for naming clean-switch instances
    device_labels = {}
    if "devices" in root:
        try:
            for d in http_get(addr, "devices/") or []:
                if isinstance(d, dict):
                    device_labels[d.get("id")] = d.get("label") or d.get("id")
        except Exception as e:
            errors["devices/"] = str(e)

    def collect(scope, collection, key_fn):
        if collection not in root:
            return
        try:
            ids = _children(addr, collection + "/")
        except Exception as e:
            errors[collection + "/"] = str(e)
            return
        for iid in ids:
            path = f"{collection}/{iid}"
            grab(path)
            data = endpoints.get(path)
            if isinstance(data, dict):
                instances[scope].append({"key": key_fn(iid, data), "path": path, "id": iid})

    collect(cat.SDI_OUTPUT, "sdi_output", lambda iid, d: d.get("label") or iid)
    collect(cat.CLEAN_SWITCH, "clean_switch", lambda iid, d: device_labels.get(iid, iid))
    collect(cat.FLOW, "flows", lambda iid, d: d.get("name") or d.get("label") or iid)
    collect(cat.PORT, "port", lambda iid, d: "Port " + iid)

    refclk = endpoints.get("refclk")
    if isinstance(refclk, dict):
        for n, uuid in enumerate(refclk.get("uuid") or []):
            path = f"refclk/{uuid}"
            grab(path)
            if isinstance(endpoints.get(path), dict):
                instances[cat.PTP].append({"key": f"PTP {n + 1}", "path": path, "id": uuid})

    for scope in instances:
        instances[scope].sort(key=lambda i: natural_key(i["key"]))

    info = endpoints.get("self/information") or {}
    ipconfig = endpoints.get("self/ipconfig") or {}
    return {
        "ip": ip,
        "address": addr,
        "network": net,
        "readAt": datetime.now().isoformat(timespec="seconds"),
        "summary": {
            "hostname": ipconfig.get("hostname"),
            "type": info.get("type"),
            "baseType": info.get("base_type"),
            "serial": info.get("serial_number"),
            "firmware": firmware_version(endpoints.get("self/firmware")),
        },
        "endpoints": endpoints,
        "instances": instances,
        "errors": errors,
    }


def firmware_version(fw):
    for slot in (fw or {}).get("info", []) if isinstance(fw, dict) else []:
        if slot.get("active") == "yes":
            return slot.get("semantic_version") or slot.get("version")
    return None


def natural_key(s):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", str(s))]


# ---------------------------------------------------------------- values

def get_in(data, path):
    cur = data
    for k in path:
        if isinstance(k, int):
            # flows/{id}.network is a list of legs on some flows (video
            # receivers) and a single flat object on the others; treat the
            # flat object as leg 0.
            if k == 0 and isinstance(cur, dict):
                continue
            if not isinstance(cur, list) or k >= len(cur):
                return MISSING
            cur = cur[k]
        else:
            if not isinstance(cur, dict) or k not in cur:
                return MISSING
            cur = cur[k]
    return cur


def set_in(obj, path, value):
    cur = obj
    for k in path[:-1]:
        cur = cur.setdefault(k, {})
    cur[path[-1]] = value


def coerce(setting, desired, current):
    """Brings a profile value into the wire type the device itself uses."""
    if desired is None:
        return None
    t = setting["type"]
    if t == cat.BOOL01 or isinstance(current, str) and current in ("0", "1") and t == "bool":
        truthy = desired in (True, 1, "1", "true", "True", "on", "yes")
        return "1" if truthy else "0"
    if isinstance(current, bool):
        return desired in (True, 1, "1", "true", "True", "on", "yes")
    if isinstance(current, int) and not isinstance(current, bool):
        try:
            return int(desired)
        except (TypeError, ValueError):
            raise ValueError(f"{setting['id']}: '{desired}' is not a number")
    if isinstance(current, float):
        return float(desired)
    return str(desired)


def same(a, b):
    return str(a).strip().lower() == str(b).strip().lower()


def audiomap_to_ids(value, flows_by_name):
    """'<flow name>:<ch>:<en>' -> '<flow uuid>:<ch>:<en>' (uuid form passes through)."""
    s = str(value or "")
    if s == "":
        return ":0:0"
    parts = s.rsplit(":", 2)
    if len(parts) != 3:
        raise ValueError(f"audio map '{s}' is not <flow>:<channel>:<enable>")
    ref, ch, en = parts
    if ref == "" or UUID_RE.match(ref):
        return s
    if ref not in flows_by_name:
        raise ValueError(f"audio map references unknown flow '{ref}'")
    return f"{flows_by_name[ref]}:{ch}:{en}"


def audiomap_to_names(value, flows_by_id):
    s = str(value or "")
    parts = s.rsplit(":", 2)
    if len(parts) == 3 and parts[0] in flows_by_id:
        return f"{flows_by_id[parts[0]]}:{parts[1]}:{parts[2]}"
    return s


def instances_for(read, setting):
    if setting["scope"] == cat.DEVICE:
        return [{"key": None, "path": setting["endpoint"]}]
    return read["instances"].get(setting["scope"], [])


def desired_value(setting, inst_key, profile, override):
    """Merge order (last wins): profile batch value < profile per-I/O value <
    device value < device per-I/O value."""
    sid, scope = setting["id"], setting["scope"]
    found = MISSING
    for layer in (profile or {}, override or {}):
        vals = layer.get("values") or {}
        if sid in vals and vals[sid] not in (None, ""):
            found = vals[sid]
        if inst_key is not None:
            io = ((layer.get("io") or {}).get(scope) or {}).get(inst_key) or {}
            if sid in io and io[sid] not in (None, ""):
                found = io[sid]
    return found


def current_values(read):
    """Flattened current values for the UI: {settingId: value} for device scope
    and {scope: {instanceKey: {settingId: value}}} for I/O scopes."""
    flows_by_id = {i["id"]: i["key"] for i in read["instances"].get(cat.FLOW, [])}
    values, io = {}, {}
    for s in cat.CATALOG:
        for inst in instances_for(read, s):
            data = read["endpoints"].get(inst["path"])
            v = get_in(data, s["field"]) if data is not None else MISSING
            if v is MISSING:
                continue
            if s["type"] == "audiomap":
                v = audiomap_to_names(v, flows_by_id)
            if inst["key"] is None:
                values[s["id"]] = v
            else:
                io.setdefault(s["scope"], {}).setdefault(inst["key"], {})[s["id"]] = v
    return {"values": values, "io": io}


# ---------------------------------------------------------------- planning

def plan_device(read, profile, override, groups=None):
    """groups: optional list of catalog groups to limit the plan to (the
    UI's "apply only this category")."""
    flows_by_name = {i["key"]: i["id"] for i in read["instances"].get(cat.FLOW, [])}
    rows = []
    for s in cat.CATALOG:
        if groups and s["group"] not in groups:
            continue
        for inst in instances_for(read, s):
            want = desired_value(s, inst["key"], profile, override)
            if want is MISSING:
                continue
            data = read["endpoints"].get(inst["path"])
            cur = get_in(data, s["field"]) if isinstance(data, (dict, list)) else MISSING
            row = {"setting": s["id"], "label": s["label"], "group": s["group"], "instance": inst["key"],
                   "path": inst["path"], "danger": s["danger"], "evidence": s["evidence"]}
            if cur is MISSING:
                rows.append({**row, "status": "unsupported", "current": None, "desired": want})
                continue
            try:
                if s["type"] == "audiomap":
                    wire = audiomap_to_ids(want, flows_by_name)
                else:
                    wire = coerce(s, want, cur)
            except ValueError as e:
                rows.append({**row, "status": "invalid", "current": cur, "desired": want, "error": str(e)})
                continue
            status = "unchanged" if same(cur, wire) else "change"
            rows.append({**row, "status": status, "current": cur, "desired": wire})
    return rows


def endpoint_rank(path):
    for n, prefix in enumerate(cat.ENDPOINT_ORDER):
        head = path.split("/")[0]
        if path == prefix or head == prefix or (prefix == "flow" and head == "flows"):
            return n
    return len(cat.ENDPOINT_ORDER)


def build_bodies(read, rows):
    """Groups 'change' rows into one POST body per endpoint path."""
    bodies = {}
    for r in rows:
        if r["status"] != "change":
            continue
        s = cat.BY_ID[r["setting"]]
        body = bodies.setdefault(r["path"], {})
        set_in(body, s["body"] or s["field"], r["desired"])
        endpoint = s["endpoint"] or ""
        for parent, extra in cat.BODY_EXTRAS.get(endpoint, {}).items():
            if tuple(s["field"][:len(parent)]) == parent:
                for k, v in extra.items():
                    set_in(body, list(parent) + [k], v)
        for parent in cat.FULL_PARENTS.get(endpoint, []):
            if s["field"][:len(parent)] != parent:
                continue
            data = read["endpoints"].get(r["path"])
            for other in cat.CATALOG:
                if other["endpoint"] == endpoint and other["field"][:len(parent)] == parent:
                    if get_in(body, other["field"]) is MISSING:
                        v = get_in(data, other["field"])
                        if v is not MISSING:
                            set_in(body, other["field"], v)
    return sorted(bodies.items(), key=lambda kv: endpoint_rank(kv[0]))


# ---------------------------------------------------------------- applying

def uptime_seconds(system):
    """'0 days, 00:01:07' -> 67"""
    m = re.match(r"\s*(\d+)\s+days?,\s*(\d+):(\d+):(\d+)", str((system or {}).get("uptime", "")))
    if not m:
        return None
    d, h, mi, s = (int(x) for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + s


def backup(read):
    BACKUPS_DIR.mkdir(exist_ok=True)
    name = f"{read['ip']}_{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    (BACKUPS_DIR / name).write_text(json.dumps(read, indent=1), encoding="utf-8")
    return name


def apply_device(ip, profile, override, groups=None):
    """Fresh read -> plan -> backup -> POST per endpoint -> read back and verify."""
    started = time.time()
    read = read_device(ip)
    with READS_LOCK:
        READS[ip] = read
    rows = plan_device(read, profile, override, groups)
    bodies = build_bodies(read, rows)
    result = {"ip": ip, "network": read["network"], "posts": [], "rows": rows, "backup": None}
    if not bodies:
        return result
    result["backup"] = backup(read)
    addr = read["address"]
    for path, body in bodies:
        logger.warning("POST %s%s via %s: %s", ip, API + path, read["network"], json.dumps(body))
        try:
            status, text = http_post(addr, path, body)
            ok = 200 <= status < 300 or text.strip().lower() == "ok"
            result["posts"].append({"path": path, "body": body, "status": status, "response": text[:300], "ok": ok})
        except Exception as e:
            logger.error("POST %s%s failed: %s", ip, path, e)
            result["posts"].append({"path": path, "body": body, "status": None, "response": str(e), "ok": False})
    time.sleep(1.0)  # let the device settle before reading back
    # Some writes (mDNS, IGMP version) make the card stop answering and
    # reboot. Keep retrying the read-back for a while instead of giving up.
    deadline = time.time() + READBACK_WAIT_SEC
    fresh = {}

    def readback(path):
        while True:
            try:
                return http_get(addr, path)
            except Exception as e:
                if time.time() >= deadline:
                    return e
                result["unreachable"] = True
                time.sleep(5)

    for r in rows:
        if r["status"] != "change":
            continue
        s = cat.BY_ID[r["setting"]]
        if r["path"] not in fresh:
            fresh[r["path"]] = readback(r["path"])
        data = fresh[r["path"]]
        if isinstance(data, Exception):
            r["verify"] = "unreadable"
            r["readback"] = str(data)
            continue
        got = get_in(data, s["field"])
        r["readback"] = None if got is MISSING else got
        r["verify"] = "ok" if got is not MISSING and same(got, r["desired"]) else "mismatch"
    try:
        up_after = uptime_seconds(http_get(addr, "self/system"))
        if up_after is not None and up_after < time.time() - started:
            result["rebooted"] = True
            logger.warning("%s rebooted during apply (uptime now %ss)", ip, up_after)
    except Exception:
        pass
    ok = sum(1 for r in rows if r.get("verify") == "ok")
    changed = sum(1 for r in rows if r["status"] == "change")
    logger.warning("apply %s: %d/%d changed fields verified, backup %s", ip, ok, changed, result["backup"])
    return result


# ---------------------------------------------------------------- profiles

def safe_name(name):
    name = re.sub(r"[^A-Za-z0-9_.\- ]", "_", str(name or "")).strip()
    if not name:
        raise ValueError("name required")
    return name


def list_profiles():
    PROFILES_DIR.mkdir(exist_ok=True)
    # Seed the editable (git-ignored) site-default.json from the tracked
    # example once, so `git pull` never overwrites a site's own defaults.
    seed = PROFILES_DIR / "site-default.json"
    example = PROFILES_DIR / "site-default.example.json"
    if not seed.exists() and example.exists():
        seed.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    return sorted(p.stem for p in PROFILES_DIR.glob("*.json"))


def capture(read, batch_only):
    """Turns a device read into a profile ({values, io}). batch_only keeps
    only settings that make sense on many devices."""
    cur = current_values(read)
    if batch_only:
        values = {k: v for k, v in cur["values"].items() if cat.BY_ID[k]["batch"]}
        io = {}
        for scope, insts in cur["io"].items():
            # A per-I/O setting that has the same value on every instance
            # becomes a plain batch value, so it also applies to devices whose
            # I/Os are named differently (e.g. a MuoN vs. a Fusion).
            per_setting = {}
            for key, vals in insts.items():
                for sid, v in vals.items():
                    if cat.BY_ID[sid]["batch"]:
                        per_setting.setdefault(sid, {})[key] = v
            for sid, by_inst in per_setting.items():
                distinct = {json.dumps(v) for v in by_inst.values()}
                if len(distinct) == 1:
                    values[sid] = next(iter(by_inst.values()))
                else:
                    for key, v in by_inst.items():
                        io.setdefault(scope, {}).setdefault(key, {})[sid] = v
        cur = {"values": values, "io": io}
    return cur


# ---------------------------------------------------------------- HTTP API

def read_many(ips):
    def one(ip):
        try:
            r = read_device(ip)
            with READS_LOCK:
                READS[ip] = r
            return ip, {"ok": True}
        except Exception as e:
            return ip, {"ok": False, "error": str(e)}

    with ThreadPoolExecutor(max_workers=READ_CONCURRENCY) as pool:
        return dict(pool.map(one, ips))


def public_read(r):
    return {k: r[k] for k in ("ip", "network", "readAt", "summary", "errors")} | {
        "instances": {s: [i["key"] for i in lst] for s, lst in r["instances"].items()},
        "current": current_values(r),
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        logger.info("%s - %s", self.address_string(), fmt % args)

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            body = UI_FILE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/catalog":
            self._json({"catalog": cat.CATALOG, "scopes": cat.SCOPES})
        elif self.path == "/api/devices":
            with READS_LOCK:
                reads = {ip: public_read(r) for ip, r in READS.items()}
            self._json({"devices": load_devices(), "reads": reads})
        elif self.path == "/api/profiles":
            self._json({"profiles": list_profiles()})
        elif self.path == "/api/backups":
            BACKUPS_DIR.mkdir(exist_ok=True)
            self._json({"backups": sorted((p.name for p in BACKUPS_DIR.glob("*.json")), reverse=True)})
        else:
            self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._json({"ok": False, "error": "invalid JSON"}, 400)
            return
        try:
            self._route(data)
        except ValueError as e:
            self._json({"ok": False, "error": str(e)}, 400)
        except Exception as e:
            logger.exception("request %s failed", self.path)
            self._json({"ok": False, "error": str(e)}, 500)

    def _route(self, data):
        p = self.path
        if p == "/api/devices/add":
            self._json({"ok": True, "added": add_devices(data.get("devices") or [])})
        elif p == "/api/devices/remove":
            ips = set(data.get("ips") or [])
            save_devices([d for d in load_devices() if d["ip"] not in ips])
            with READS_LOCK:
                for ip in ips:
                    READS.pop(ip, None)
            self._json({"ok": True})
        elif p == "/api/devices/import-dashboard":
            if not DASHBOARD_DEVICES_FILE.exists():
                raise ValueError(f"not found: {DASHBOARD_DEVICES_FILE}")
            src = json.loads(DASHBOARD_DEVICES_FILE.read_text(encoding="utf-8"))
            self._json({"ok": True, "added": add_devices(src)})
        elif p == "/api/read":
            self._json({"ok": True, "results": read_many(list(data.get("ips") or []))})
        elif p == "/api/plan":
            profile, overrides = data.get("profile") or {}, data.get("overrides") or {}
            out = {}
            for ip in data.get("ips") or []:
                with READS_LOCK:
                    r = READS.get(ip)
                if r is None:
                    out[ip] = {"error": "not read yet - read the device first"}
                    continue
                rows = plan_device(r, profile, overrides.get(ip), data.get("groups"))
                out[ip] = {"rows": rows, "posts": [{"path": pth, "body": b} for pth, b in build_bodies(r, rows)]}
            self._json({"ok": True, "plan": out})
        elif p == "/api/apply":
            if data.get("confirm") != "APPLY":
                raise ValueError("apply needs confirm=APPLY")
            if not APPLY_LOCK.acquire(blocking=False):
                raise ValueError("another apply is still running")
            try:
                profile, overrides = data.get("profile") or {}, data.get("overrides") or {}
                ips = list(data.get("ips") or [])
                logger.warning("APPLY to %d device(s): %s", len(ips), ips)

                def one(ip):
                    try:
                        return ip, apply_device(ip, profile, overrides.get(ip), data.get("groups"))
                    except Exception as e:
                        logger.error("apply %s failed: %s", ip, e)
                        return ip, {"ip": ip, "error": str(e)}

                with ThreadPoolExecutor(max_workers=APPLY_CONCURRENCY) as pool:
                    results = dict(pool.map(one, ips))
            finally:
                APPLY_LOCK.release()
            self._json({"ok": True, "results": results})
        elif p == "/api/profiles/load":
            f = PROFILES_DIR / (safe_name(data.get("name")) + ".json")
            self._json({"ok": True, "profile": json.loads(f.read_text(encoding="utf-8"))})
        elif p == "/api/profiles/save":
            PROFILES_DIR.mkdir(exist_ok=True)
            f = PROFILES_DIR / (safe_name(data.get("name")) + ".json")
            f.write_text(json.dumps(data.get("profile") or {}, indent=2), encoding="utf-8")
            self._json({"ok": True})
        elif p == "/api/profiles/delete":
            (PROFILES_DIR / (safe_name(data.get("name")) + ".json")).unlink(missing_ok=True)
            self._json({"ok": True})
        elif p == "/api/capture":
            ip = data.get("ip")
            with READS_LOCK:
                r = READS.get(ip)
            if r is None:
                raise ValueError("read the device first")
            self._json({"ok": True, "profile": capture(r, bool(data.get("batchOnly", True)))})
        elif p == "/api/backups/load":
            name = Path(str(data.get("name") or "")).name
            r = json.loads((BACKUPS_DIR / name).read_text(encoding="utf-8"))
            self._json({"ok": True, "ip": r["ip"], "profile": capture(r, batch_only=False)})
        else:
            self.send_error(404)


def main():
    PROFILES_DIR.mkdir(exist_ok=True)
    BACKUPS_DIR.mkdir(exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    logger.info("MuoN config tool listening on http://%s:%d/", HOST, PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
