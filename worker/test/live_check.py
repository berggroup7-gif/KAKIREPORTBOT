"""Live parity: replay the raw messages the Worker logged since cutover through the Python
chatstats on the pre-cutover state, and compare with the Worker's current stats.
  ADMIN_SECRET=... python3 test/live_check.py PRE_CUTOVER_DATA_DIR BASE_URL DAY FROM_SEQ"""
import os, sys, json, datetime, tempfile, shutil, urllib.request
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT); os.environ.setdefault("KAKIREPORTBOT_TEST_IDENTITY", "1")
data, base, day, frm = sys.argv[1], sys.argv[2].rstrip("/"), sys.argv[3], int(sys.argv[4])
H = {"X-Admin-Secret": os.environ["ADMIN_SECRET"], "user-agent": "kakireportbot-check"}
get = lambda p: json.loads(urllib.request.urlopen(urllib.request.Request(base + p, headers=H), timeout=60).read())
import identity, chatstats
from common import TZ
tmp = tempfile.mkdtemp(); shutil.copy(os.path.join(data, "stats.json"), tmp); shutil.copy(os.path.join(data, "winners.json"), tmp)
chatstats.SF = os.path.join(tmp, "stats.json"); chatstats.WF = os.path.join(tmp, "winners.json"); chatstats._warn = lambda *a, **k: None
NOW = [None]
class FDT(datetime.datetime):
    @classmethod
    def now(cls, tz=None): return NOW[0]
chatstats.datetime = type("M", (), {"datetime": FDT, "timedelta": datetime.timedelta, "date": datetime.date})
for attempt in range(5):
    rows = get(f"/admin/raw?day={day}&from={frm}"); js = get("/admin/stats")
    recs = [json.loads(r["rec"]) for r in rows]
    msgs = [r for r in recs if r.get("type", "message") == "message" and not r.get("edited")]
    identity.GROUP = int(list(js["stats"]["last_mid"].keys())[0])
    grp = [r for r in msgs if r["chat_id"] == identity.GROUP]
    if grp and js["meta"]["lastMid"] >= grp[-1]["message_id"] and get(f"/admin/raw?day={day}&from={frm + len(rows)}") == []: break
chatstats._st = None; chatstats._win = None; chatstats._tiny.clear(); chatstats._burst_pen.clear()
counted = 0
for r in msgs:
    ts = int(datetime.datetime.fromisoformat(r["ts"]).timestamp())
    m = {"message_id": r["message_id"], "date": ts, "chat": {"id": r["chat_id"], "type": "supergroup"},
         "from": {"id": r["user_id"], "first_name": r.get("sender") or "", "username": r.get("username"), "is_bot": False}}
    if r.get("text") is not None: m["text"] = r["text"]
    NOW[0] = datetime.datetime.fromtimestamp(ts, TZ); chatstats.record(m, warn=False); counted += 1
py = chatstats._st
print("replayed", counted, "messages since cutover (", len(grp), "in group ), last_mid py", py["last_mid"], "worker", js["meta"]["lastMid"])
print("periods identical:", py["periods"] == js["stats"]["periods"], "| names identical:", py["names"] == js["stats"]["names"], "| winners identical:", chatstats._wload() == js["winners"])
d = "d:" + day; diff = {u: (py["periods"][d].get(u), js["stats"]["periods"][d].get(u)) for u in set(py["periods"][d]) | set(js["stats"]["periods"][d]) if py["periods"][d].get(u) != js["stats"]["periods"][d].get(u)}
print("today diffs:", diff)
tc = sum(v[0] for v in js["stats"]["periods"][d].values()); print("today total chats (worker):", tc)
