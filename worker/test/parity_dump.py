"""Replays real raw chat records through the Python bot functions and dumps the
results as JSON so test/unit.mjs can check the JavaScript port gives identical output.
Usage: python3 test/parity_dump.py DATA_DIR OUT.json   (no network, nothing sent)"""
import os, sys, json, glob, datetime, tempfile
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
os.environ.setdefault("KAKIREPORTBOT_TEST_IDENTITY", "1")
os.environ["NO_SEND"] = "1"
data_dir, out_path = sys.argv[1], sys.argv[2]
import identity
GROUP = int(os.environ.get("PARITY_GROUP", "0") or 0)
import chatstats, gemini_reply, logger as L
from common import TZ
tmp = tempfile.mkdtemp()
chatstats.SF = os.path.join(tmp, "stats.json"); chatstats.WF = os.path.join(tmp, "winners.json")
chatstats._warn = lambda *a, **k: None
NOW = [None]
class FDT(datetime.datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW[0]
chatstats.datetime = type("M", (), {"datetime": FDT, "timedelta": datetime.timedelta, "date": datetime.date})
rows = []
for p in sorted(glob.glob(os.path.join(data_dir, "????-??-??.jsonl"))):
    for l in open(p, encoding="utf-8"):
        try: rows.append(json.loads(l))
        except Exception: pass
msgs = [r for r in rows if r.get("type", "message") == "message" and not r.get("edited")]
if not GROUP:
    GROUP = max(set(r["chat_id"] for r in msgs), key=lambda c: sum(1 for r in msgs if r["chat_id"] == c))
identity.GROUP = GROUP
feat = []
me = {"id": 4242, "username": "KAKIREPORTBOT"}
identity.OWNER = "ownerx"
replay = []
for r in msgs:
    t = r.get("text")
    ts = int(datetime.datetime.fromisoformat(r["ts"]).timestamp())
    m = {"message_id": r["message_id"], "date": ts, "chat": {"id": r["chat_id"], "type": "supergroup"},
         "from": {"id": r.get("user_id"), "first_name": r.get("sender") or "", "username": r.get("username"), "is_bot": False}}
    if t is not None: m["text"] = t
    if r.get("reply_to"): m["reply_to_message"] = {"message_id": r["reply_to"], "from": {"id": 4242 if r["message_id"] % 7 == 0 else 1}}
    replay.append(m)
    if t is not None:
        feat.append([t, chatstats.skip_text(t), chatstats.is_spell_spam(t), len(t.split()), gemini_reply.trivial(t),
                     L.name_call(t, False), bool(gemini_reply.LINK_RE.search(t)), bool(gemini_reply.PREF_RE.search(t)),
                     L.classify(t), gemini_reply.prio((0, {"text": t}, False))])
chatstats._st = {"periods": {}, "names": {}, "last_mid": {}, "backfilled": True}
chatstats._win = {}
for m in replay:
    NOW[0] = datetime.datetime.fromtimestamp(m["date"], TZ)
    m["from"]["first_name"] = m["from"]["first_name"]
    chatstats.record(m, warn=False)
final = NOW[0] + datetime.timedelta(minutes=5)
NOW[0] = final
texts = {}
uids = list(chatstats._st["names"].keys())[:25]
texts["own"] = [chatstats.own_text(int(u), None) for u in uids]
for key in ("hari", "minggu", "bulan"):
    texts["top_" + key] = chatstats.top_text(key); texts["win_" + key] = chatstats.winners_text(key)
json.dump({"group": GROUP, "replay": replay, "features": feat, "stats": chatstats._st, "winners": chatstats._win,
           "final_now_ms": int(final.timestamp() * 1000), "uids": uids, "texts": texts}, open(out_path, "w"), ensure_ascii=False)
print("messages", len(replay), "features", len(feat), "periods", sorted(chatstats._st["periods"]))
