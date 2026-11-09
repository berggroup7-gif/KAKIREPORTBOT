import sys, json, os, datetime
from collections import Counter
from common import DATA, TZ
MP = "links.tngdigital.com.my"
day = sys.argv[1] if len(sys.argv) > 1 else (datetime.datetime.now(TZ) - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
chat = sys.argv[2] if len(sys.argv) > 2 else None
p = os.path.join(DATA, day + ".jsonl")
rows = [json.loads(l) for l in open(p, encoding="utf-8")] if os.path.exists(p) else []
rows = [r for r in rows if not chat or str(r["chat_id"]) == chat]
key = lambda r: r.get("user_id") or r.get("username") or r.get("sender") or r.get("name")
active, mp_by, names, links, joins, lefts, total = set(), Counter(), {}, [], set(), set(), 0
for r in rows:
    t = r.get("type", "message")
    if t == "join": joins.add(key(r)); continue
    if t == "leave": lefts.add(key(r)); continue
    if r.get("edited"): continue
    total += 1; k = key(r); active.add(k)
    names[k] = r.get("sender") + (f" (@{r['username']})" if r.get("username") else "")
    mp = [u for u in r.get("links", []) if MP in u.lower()]
    if mp: mp_by[k] += len(mp); links += mp
print(json.dumps({"date": day, "chat_id": chat, "total_messages": total, "active_chatters": len(active),
    "mp_sharers": len(mp_by), "mp_links_total": len(links), "mp_links_unique": len(set(links)),
    "new_members": len(joins), "members_left": len(lefts),
    "top_sharers": [{"name": names[k], "count": c} for k, c in mp_by.most_common(10)]}, ensure_ascii=False, indent=1))
