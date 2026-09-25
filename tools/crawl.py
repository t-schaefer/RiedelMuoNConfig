"""Read-only recursive GET crawl of an emsfp device. Usage: crawl.py <ip> <out.json>"""
import json, sys, urllib.request

ip, out = sys.argv[1], sys.argv[2]
BASES = ["/emsfp/node/v1/", "/x-nmos/"]
result, seen = {}, set()


def get(path):
    req = urllib.request.Request("http://" + ip + path, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=6) as r:
        body = r.read().decode("utf-8", "replace")
        try:
            return json.loads(body)
        except ValueError:
            return body[:2000]


def walk(path, depth=0):
    if path in seen or depth > 8:
        return
    seen.add(path)
    try:
        data = get(path)
    except Exception as e:
        result[path] = {"__error__": str(e)}
        return
    result[path] = data
    if isinstance(data, list) and data and all(isinstance(x, str) and x.endswith("/") for x in data):
        for child in data:
            walk(path + child, depth + 1)


for b in BASES:
    walk(b)
json.dump(result, open(out, "w", encoding="utf-8"), indent=1)
print(len(result), "paths")
