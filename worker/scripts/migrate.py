#!/usr/bin/env python3
"""Copy decrypted bot state (data/ dir restored by tools/state_store.py load) into the
Worker's Durable Object through the /admin/import/* endpoints.

  ADMIN_SECRET=... python3 scripts/migrate.py DATA_DIR https://kakireportbot.<sub>.workers.dev [--set-update-offset]

Raw *.jsonl files are append-only, so only lines the Worker does not have yet are sent
(safe to run again for a final sync). Stats/settings are replaced wholesale.
"""
import json, os, sys, glob, urllib.request

data, base = sys.argv[1], sys.argv[2].rstrip("/")
set_offset = "--set-update-offset" in sys.argv
SECRET = os.environ["ADMIN_SECRET"]

def call(path, body=None):
    req = urllib.request.Request(base + "/admin/" + path, data=None if body is None else json.dumps(body, ensure_ascii=False).encode(),
                                 headers={"X-Admin-Secret": SECRET, "content-type": "application/json", "user-agent": "kakireportbot-migrate"},
                                 method="GET" if body is None else "POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())

def jload(name, dflt):
    try: return json.load(open(os.path.join(data, name), encoding="utf-8"))
    except Exception: return dflt

have = call("rawcount")
sent = 0
for p in sorted(glob.glob(os.path.join(data, "????-??-??.jsonl"))):
    day = os.path.basename(p)[:10]
    lines = [l.rstrip("\n") for l in open(p, encoding="utf-8") if l.strip()]
    start = have.get(day, 0)
    for i in range(start, len(lines), 1500):
        call("import/raw", {"day": day, "start": i, "lines": lines[i:i + 1500]}); sent += len(lines[i:i + 1500])
print("raw lines sent", sent)

stats = jload("stats.json", None)
stats = dict(stats, periods=[[k, [[u, v] for u, v in p.items()]] for k, p in stats["periods"].items()])
body = {"stats": stats, "winners": jload("winners.json", {}), "penalties": True}
if set_offset:
    body["lastUpdateId"] = int(open(os.path.join(data, "offset.txt")).read().strip()) - 1
print("stats", call("import/stats", body))
kv = {}
for k in ("settings", "nicknames", "name_prefs", "gemini_usage", "chats", "private_users", "instant_acks", "report_requests"):
    v = jload(k + ".json", None)
    if v is not None: kv[k] = v
try: kv["last_replied"] = int(open(os.path.join(data, "last_replied.txt")).read().strip())
except Exception: pass
print("kv", call("import/kv", kv))
for p in sorted(glob.glob(os.path.join(data, "reports", "*.txt"))):
    call("import/report", {"day": os.path.basename(p)[:10], "text": open(p, encoding="utf-8").read()})
if "--files" in sys.argv:
    for p in sorted(glob.glob(os.path.join(data, "**", "*"), recursive=True)):
        rel = os.path.relpath(p, data)
        if os.path.isfile(p) and (rel.startswith("pending_") or rel.startswith("export/") or rel.startswith("reports/backup/")):
            call("import/file", {"path": rel, "content": open(p, encoding="utf-8").read()})
print("debug", json.dumps(call("debug"))[:400])
