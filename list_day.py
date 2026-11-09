import sys, json, os, datetime
from common import DATA, TZ
day = sys.argv[1] if len(sys.argv) > 1 else datetime.datetime.now(TZ).strftime("%Y-%m-%d")
chat = sys.argv[2] if len(sys.argv) > 2 else None
p = os.path.join(DATA, day + ".jsonl")
if not os.path.exists(p): print(f"no messages for {day}"); sys.exit()
for l in open(p, encoding="utf-8"):
    r = json.loads(l)
    if chat and str(r["chat_id"]) != chat: continue
    if "sender" not in r: continue
    who = r["sender"] + (f" (@{r['username']})" if r.get("username") else "")
    rep = f" ↩{r['reply_to']}" if r.get("reply_to") else ""
    print(f"[{r['chat_title']}|{r['chat_id']}] {r['ts'][11:16]} #{r['message_id']}{rep} {who}: {r['text'] or '<non-text>'}")
