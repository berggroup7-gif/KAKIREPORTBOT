import os, json, time, datetime, logging
from common import api, DATA, TZ, BASE
import identity
SETTINGS = os.path.join(DATA, "settings.json"); NICKS = os.path.join(DATA, "nicknames.json")
START = time.time()
DEFAULTS = {"replies_on": True, "per_user_min": 2}
def settings():
    try: s = json.load(open(SETTINGS))
    except Exception: s = {}
    return {**DEFAULTS, **s}
def save_settings(s): json.dump(s, open(SETTINGS, "w"), indent=1)
def _load_nicks():
    try: return json.load(open(NICKS))
    except Exception: return {}
def is_owner_pm(m):
    f = m.get("from") or {}
    return m["chat"]["type"] == "private" and m["chat"]["id"] == identity.OWNER_CHAT and (f.get("username") or "").lower() == identity.OWNER
def say(text):
    for i in range(0, len(text), 4000) or [0]:
        api("sendMessage", chat_id=identity.OWNER_CHAT, text=text[i:i+4000] or "-")
HELP = """ni senarai arahan ketua:
/status - tengok bot hidup ke tak
/off - stop balas dalam group
/on - sambung balas dalam group
/had N - had orang sama (minit, 1-60)
/nama - senarai nama panggilan
/nama username panggilan - tambah/tukar
/buangnama username - buang
/report [semalam|YYYY-MM-DD] - hantar laporan
/restart - hidupkan semula bot
/stat - statistik sendiri
/stattop [hari|minggu|bulan] - top 30
/pemenang [hari|minggu|bulan] - senarai pemenang
/resetstat ya - reset semua statistik"""
def set_commands():
    cmds = [("status","tengok status bot"),("off","stop balas group"),("on","sambung balas group"),("had","had orang sama (minit)"),
            ("nama","nama panggilan"),("buangnama","buang nama panggilan"),("report","hantar laporan"),("restart","restart bot"),("help","senarai arahan")]
    try: api("setMyCommands", commands=[{"command":c,"description":d} for c,d in cmds], scope={"type":"chat","chat_id":identity.OWNER_CHAT})
    except Exception as e: logging.warning("setMyCommands failed: %s", e)
    G = identity.GROUP; st = [("stat","statistik chat saya")]
    adm = st + [("stattop","top 30 chat (hari|minggu|bulan)"),("pemenang","senarai pemenang (hari|minggu|bulan)")]
    for sc, cm in (({"type":"default"}, st), ({"type":"all_group_chats"}, st), ({"type":"chat","chat_id":G}, st),
                   ({"type":"chat_administrators","chat_id":G}, adm), ({"type":"all_private_chats"}, st)):
        try: api("setMyCommands", commands=[{"command":c,"description":d} for c,d in cm], scope=sc)
        except Exception as e: logging.warning("setMyCommands %s failed: %s", sc, e)
    try: api("setMyCommands", commands=[{"command":c,"description":d} for c,d in cmds + adm + [("resetstat","reset semua statistik")]], scope={"type":"chat","chat_id":identity.OWNER_CHAT})
    except Exception as e: logging.warning("setMyCommands owner failed: %s", e)
def status_text():
    s = settings(); today = datetime.datetime.now(TZ).date().isoformat()
    try: usage = json.load(open(os.path.join(DATA, "gemini_usage.json"))).get(today, 0)
    except Exception: usage = 0
    n = 0; last = None
    try:
        for l in open(os.path.join(BASE, "logs", "replies.log")):
            if "sent reply" in l and "ok=True" in l:
                last = l[:19]
                if l.startswith(today): n += 1
    except Exception: pass
    up = int(time.time() - START); h, r = divmod(up, 3600)
    lt = last.replace("T", " ")[:16] if last else "belum ada"
    return (f"bot hidup ketua\nuptime: {h} jam {r//60} minit\ngemini hari ni: {usage}\nbalasan group hari ni: {n}\n"
            f"balas group: {'ON' if s['replies_on'] else 'OFF'}\nhad orang sama: {s['per_user_min']} minit\nbalasan last: {lt} (MYT)")
def handle(m):
    """returns 'restart' if restart requested, True if handled, False if not a command"""
    t = (m.get("text") or "").strip()
    if not t.startswith("/") or not is_owner_pm(m): return False
    parts = t.split(); cmd = parts[0][1:].split("@")[0].lower(); args = parts[1:]
    s = settings()
    if cmd in ("help", "start"): say(HELP)
    elif cmd == "status": say(status_text())
    elif cmd in ("off", "on"):
        s["replies_on"] = cmd == "on"; save_settings(s)
        say("ok ketua, bot dah senyap dalam group. panggilan ketua still jawab" if cmd == "off" else "ok ketua, bot dah on balik dalam group")
    elif cmd == "had":
        try:
            n = int(args[0]); assert 1 <= n <= 60
            s["per_user_min"] = n; save_settings(s); say(f"ok ketua, had orang sama sekarang {n} minit")
        except Exception: say(f"guna macam ni ketua: /had 2 (1 sampai 60). sekarang {s['per_user_min']} minit")
    elif cmd == "nama":
        nk = _load_nicks()
        if not args:
            say("nama panggilan sekarang:\n" + "\n".join(f"@{k} = {v}" for k, v in nk.items()) if nk else "belum ada nama panggilan lagi ketua")
        elif len(args) < 2: say("guna macam ni ketua: /nama username panggilan")
        else:
            u = args[0].lstrip("@").lower(); nk[u] = " ".join(args[1:])
            json.dump(nk, open(NICKS, "w"), indent=1, ensure_ascii=False); say(f"ok ketua, @{u} lepas ni dipanggil {nk[u]}")
    elif cmd == "buangnama":
        nk = _load_nicks(); u = (args[0].lstrip("@").lower() if args else "")
        ks = [k for k in nk if k.lower() == u]
        if ks:
            for k in ks: nk.pop(k)
            json.dump(nk, open(NICKS, "w"), indent=1, ensure_ascii=False); say(f"ok ketua, nama panggilan @{u} dah buang")
        else: say("takde pun username tu dalam senarai ketua")
    elif cmd == "report":
        a = args[0].lower() if args else "semalam"
        if a == "semalam": d = (datetime.datetime.now(TZ).date() - datetime.timedelta(days=1)).isoformat()
        elif a in ("hari", "harini", "today"): d = datetime.datetime.now(TZ).date().isoformat()
        else:
            try: d = datetime.date.fromisoformat(a).isoformat()
            except Exception: say("tarikh tak betul ketua, guna /report semalam atau /report 2026-11-03"); return True
        p = os.path.join(DATA, "reports", f"{d}.txt")
        if os.path.exists(p) and open(p).read().strip(): say(open(p).read())
        else: say(f"laporan {d} takde lagi ketua")
    elif cmd == "restart":
        say("ok ketua, restart jap"); return "restart"
    else: say("arahan tu tak kenal la ketua. taip /help")
    return True
