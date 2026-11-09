"""Compare /stat, /stattop, /pemenang texts from the Python bot (on DATA_DIR state) with the
Worker's /admin/eval output on the migrated state, at the same instant.
  ADMIN_SECRET=... python3 test/parity_state.py DATA_DIR BASE_URL [unix_ms ...]"""
import os, sys, json, time, datetime, urllib.request
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT); os.environ.setdefault("KAKIREPORTBOT_TEST_IDENTITY", "1")
data, base = sys.argv[1], sys.argv[2].rstrip("/")
nows = [int(x) for x in sys.argv[3:]] or [int(time.time() * 1000)]
import chatstats
from common import TZ
chatstats.SF = os.path.join(data, "stats.json"); chatstats.WF = os.path.join(data, "winners.json")
NOW = [None]
class FDT(datetime.datetime):
    @classmethod
    def now(cls, tz=None): return NOW[0]
chatstats.datetime = type("M", (), {"datetime": FDT, "timedelta": datetime.timedelta, "date": datetime.date})
st = json.load(open(chatstats.SF)); uids = list(st["names"].keys())
total = 0
for now in nows:
    NOW[0] = datetime.datetime.fromtimestamp(now / 1000, TZ)
    chatstats._st = None; chatstats._win = None
    items = [{"kind": "own", "uid": u, "nm": None} for u in uids] + [{"kind": k, "key": p} for k in ("top", "win") for p in ("hari", "minggu", "bulan")]
    py = [chatstats.own_text(int(u), None) for u in uids] + [chatstats.top_text(p) for p in ("hari", "minggu", "bulan")] + [chatstats.winners_text(p) for p in ("hari", "minggu", "bulan")]
    req = urllib.request.Request(base + "/admin/eval", data=json.dumps({"now": now, "items": items}).encode(), method="POST",
        headers={"X-Admin-Secret": os.environ["ADMIN_SECRET"], "content-type": "application/json", "user-agent": "kakireportbot-parity"})
    js = json.loads(urllib.request.urlopen(req, timeout=60).read())
    bad = [(i, a, b) for i, (a, b) in enumerate(zip(py, js)) if a != b]
    total += len(py)
    print(NOW[0].isoformat(), "compared", len(py), "texts, mismatches", len(bad))
    for i, a, b in bad[:3]: print("PY:", a[:300], "\nJS:", b[:300])
    if bad: sys.exit(1)
print("OK", total)
