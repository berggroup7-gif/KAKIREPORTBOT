import re
import os, json, glob, time, datetime, threading, logging
from common import api, DATA, TZ, no_send_enabled
import identity
SF = os.path.join(DATA, "stats.json"); LOCK = threading.RLock()
PER = {"hari": ("d", "hari ini"), "minggu": ("w", "minggu ini"), "bulan": ("m", "bulan ini")}
_st = None
def pid(kind, dt=None):
    dt = dt or datetime.datetime.now(TZ)
    if kind == "d": return "d:" + dt.strftime("%Y-%m-%d")
    if kind == "w": y, w, _ = dt.isocalendar(); return f"w:{y}-W{w:02d}"
    return "m:" + dt.strftime("%Y-%m")
def _load():
    global _st
    if _st is None:
        try: _st = json.load(open(SF))
        except Exception: _st = {"periods": {}, "names": {}, "last_mid": {}, "backfilled": False}
    return _st
def _save():
    tmp = SF + ".tmp"; json.dump(_st, open(tmp, "w"), ensure_ascii=False); os.replace(tmp, SF)
def _prune(s):
    cut = (datetime.datetime.now(TZ) - datetime.timedelta(days=95))
    for k in list(s["periods"]):
        t, v = k.split(":", 1)
        try:
            if t == "d": d = datetime.datetime.strptime(v, "%Y-%m-%d")
            elif t == "w": d = datetime.datetime.strptime(v + "-1", "%G-W%V-%u")
            else: d = datetime.datetime.strptime(v + "-28", "%Y-%m-%d")
            if d.replace(tzinfo=TZ) < cut: del s["periods"][k]
        except Exception: pass
import re
_FILL = {"tq","tqvm","tqsm","tqs","tqqq","ty","tyvm","thanks","thank","thx","tenkiu","tenkyu","tengkiu","terima","kasih","makasih","trima","ksih","ready","redy","rdy","reddy","readyy","done","siap","dah","da","sudah","kak","kakak","sis","bro","brader","ketua","ya","ye","yer","k","ok","okay","oke","okey","semua","all","you","u","much","so","very","sangat","ketue","geng"}
_CORE = re.compile(r"^(tq+|tq(vm|sm|s)|ty(vm)?|thanks?|thx|ten?g?k(i|y)u|terima|makasih|trima|ready+|redd?y|rdy|done|siap)$")
def skip_text(text):
    if text and len(re.sub(r"[\W_]", "", text)) <= 1 and len(text.strip()) <= 3: return True
    """True kalau mesej tak patut dikira: kosong (sticker/media tanpa teks) atau cuma tq/ready."""
    if not text or not text.strip(): return True
    toks = [x for x in re.split(r"[^a-z0-9]+", text.lower()) if x]
    if not toks: return True
    return all(x in _FILL or _CORE.match(x) for x in toks) and any(_CORE.match(x) for x in toks)
def _add(s, uid, nm, mid, chat, dt, text):
    if mid is not None and mid <= s["last_mid"].get(str(chat), 0): return False
    if skip_text(text):
        if mid is not None: s["last_mid"][str(chat)] = mid
        return False
    w = len(text.split())
    for k in ("d", "w", "m"):
        c = s["periods"].setdefault(pid(k, dt), {}).setdefault(str(uid), [0, 0]); c[0] += 1; c[1] += w
    if nm: s["names"][str(uid)] = nm
    if mid is not None: s["last_mid"][str(chat)] = mid
    return True
def record(m, *, warn=True):
    f = m.get("from") or {}
    if m["chat"]["id"] != identity.GROUP or f.get("is_bot") or not f.get("id") or m.get("edited_date"): return
    nm = " ".join(x for x in [f.get("first_name"), f.get("last_name")] if x)
    with LOCK:
        s = _load()
        dt = datetime.datetime.fromtimestamp(m["date"], TZ); txt = m.get("text") or m.get("caption")
        burst = is_burst(f["id"], txt, m["date"])
        if is_spell_spam(txt) or burst:
            if m["message_id"] > s["last_mid"].get(str(identity.GROUP), 0):
                s["last_mid"][str(identity.GROUP)] = m["message_id"]
                n = burst or 1
                for k in ("d", "w", "m"):
                    c = s["periods"].setdefault(pid(k, dt), {}).setdefault(str(f["id"]), [0, 0])
                    c[0] = max(0, c[0] - PENALTY * n); c[1] = max(0, c[1] - PENALTY * n)
                if nm: s["names"][str(f["id"])] = nm
                s.setdefault("penalties", {}).setdefault(str(f["id"]), []).append([m["message_id"], dt.isoformat()])
                _save()
                if warn:
                    _warn(m, f, nm, bool(burst))
            return
        if _add(s, f["id"], nm, m["message_id"], identity.GROUP, dt, txt):
            finalize(); _prune(s); _save()
PENALTY = 10; _warned = {}
_tiny = {}; _burst_pen = {}
def is_burst(uid, text, ts):
    """Spam chat pendek berturut-turut, contoh A, B, C, D satu mesej satu huruf.
    Return "pen" bila mula kena penalti, True untuk mesej spam seterusnya dalam burst sama, False kalau bukan."""
    if not text or len(re.sub(r"[\W_]", "", text)) > 1 or len(text.strip()) > 3: return False
    q = [t for t in _tiny.get(uid, []) if ts - t < 120] + [ts]; _tiny[uid] = q
    if len(q) < 3: return False
    if ts - _burst_pen.get(uid, -1e12) >= 120:
        _burst_pen[uid] = ts; return len(q)   # mula burst: kira semua mesej spam dalam burst
    return 1
def is_spell_spam(text):
    """Chat dieja huruf demi huruf untuk curi perkataan, contoh 'O k l a h .'"""
    if not text: return False
    toks = text.split()
    if len(toks) < 4: return False
    single = [t for t in toks if len(t) == 1 and t.isalpha()]
    small = [t for t in toks if len(t) == 1]
    return len(single) >= 4 and len(small) / len(toks) >= 0.7
def _warn(m, f, nm, burst=False):
    uid = f["id"]; now = time.time()
    logging.info("spell-spam penalty uid=%s msg=%s", uid, m["message_id"])
    if now - _warned.get(uid, 0) < 120: return
    _warned[uid] = now
    try:
        import gemini_reply as g; panggil = g.name_of(f)
    except Exception: panggil = nm or "kak"
    if burst:
        t = (f"amaran untuk {panggil}: jangan spam chat satu-satu huruf berturut-turut macam ni untuk naikkan statistik. "
             f"setiap mesej spam ditolak {PENALTY} chat dan {PENALTY} perkataan.")
    else:
        t = (f"amaran untuk {panggil}: jangan eja satu-satu huruf macam ni untuk naikkan statistik. "
             f"mesej ni tak dikira dan markah ditolak {PENALTY} chat dan {PENALTY} perkataan.")
    if no_send_enabled():
        logging.info("spell-spam warn skipped (NO_SEND)")
        return
    try:
        api("sendMessage", chat_id=identity.GROUP, text=t, reply_to_message_id=m["message_id"], allow_sending_without_reply=True)
        try:
            import gemini_reply as g; g._handled.add(m["message_id"]); g.log_line(f"sent reply to {m['message_id']} ok=True [penalti] {t}")
        except Exception: pass
    except Exception as e: logging.warning("warn send failed: %s", e)
def backfill():
    with LOCK:
        s = _load()
        if s.get("backfilled"): return None
        now = datetime.datetime.now(TZ); start = now.replace(day=1).date()
        wk = (now - datetime.timedelta(days=now.weekday())).date(); start = min(start, wk)
        rows = []
        for p in sorted(glob.glob(os.path.join(DATA, "????-??-??.jsonl"))):
            try: d = datetime.date.fromisoformat(os.path.basename(p)[:10])
            except Exception: continue
            if d < start: continue
            for l in open(p, encoding="utf-8"):
                try: r = json.loads(l)
                except Exception: continue
                if r.get("type", "message") != "message" or r.get("edited") or r.get("chat_id") != identity.GROUP or not r.get("user_id"): continue
                rows.append(r)
        seen = set(); n = 0
        for r in sorted(rows, key=lambda r: r.get("message_id") or 0):
            if r["message_id"] in seen: continue
            seen.add(r["message_id"])
            if _add(s, r["user_id"], r.get("sender"), r["message_id"], identity.GROUP, datetime.datetime.fromisoformat(r["ts"]).astimezone(TZ), r.get("text")): n += 1
        s["backfilled"] = True; _save(); return n
def ranking(kind):
    with LOCK: p = dict(_load()["periods"].get(pid(kind), {}))
    return sorted(p.items(), key=lambda kv: (-kv[1][0], -kv[1][1]))
def name(uid):
    return _load()["names"].get(str(uid)) or "tanpa nama"
def own_text(uid, nm=None):
    out = [f"statistik {nm or name(uid)}"]
    for key, (k, lbl) in PER.items():
        r = ranking(k); pos = next((i + 1 for i, (u, _) in enumerate(r) if u == str(uid)), None)
        if pos: c, w = r[pos - 1][1]; out.append(f"{lbl}: {c} chat, {w} perkataan, rank {pos}/{len(r)}")
        else: out.append(f"{lbl}: 0 chat, 0 perkataan, belum ada rank ({len(r)} orang aktif)")
    return "\n".join(out)
def top_text(key):
    k, lbl = PER[key]; r = ranking(k)
    if not r: return f"belum ada chat {lbl} lagi"
    tc = sum(v[0] for _, v in r); tw = sum(v[1] for _, v in r)
    lines = [f"top 30 {lbl} ({pid(k)[2:]})", f"jumlah group: {tc} chat, {tw} perkataan, {len(r)} orang aktif", ""]
    lines += [f"{i}. {name(u)} - {v[0]} chat, {v[1]} perkataan" for i, (u, v) in enumerate(r[:30], 1)]
    return "\n".join(lines)
_adm = {"ts": 0, "ids": set()}
def is_admin(uid, username=None):
    if uid == identity.OWNER_ID or (username or "").lower() == identity.OWNER: return True
    if time.time() - _adm["ts"] > 600:
        try: _adm["ids"] = {a["user"]["id"] for a in api("getChatAdministrators", chat_id=identity.GROUP)}; _adm["ts"] = time.time()
        except Exception as e: logging.warning("getChatAdministrators failed: %s", e)
    return uid in _adm["ids"]
def reset_all():
    with LOCK:
        s = _load(); s["periods"] = {}; _save()
ALIAS = {"stats": "stat", "statistic": "stat", "statistics": "stat", "mystat": "stat", "top": "stattop", "statstop": "stattop", "rank": "stattop", "resetstats": "resetstat"}
CMDS = {"stat", "statistik", "stattop", "ranking", "resetstat", "pemenang"}
ALIAS.update({"winner": "pemenang", "winners": "pemenang", "juara": "pemenang"})
WF = os.path.join(DATA, "winners.json"); _win = None
HARI = ["Isnin", "Selasa", "Rabu", "Khamis", "Jumaat", "Sabtu", "Ahad"]
BLN = ["Jan", "Feb", "Mac", "Apr", "Mei", "Jun", "Jul", "Ogos", "Sep", "Okt", "Nov", "Dis"]
def _wload():
    global _win
    if _win is None:
        try: _win = json.load(open(WF))
        except Exception: _win = {}
    return _win
def finalize():
    with LOCK:
        s = _load(); w = _wload(); cur = {pid(k) for k in "dwm"}; ch = False
        for k, p in s["periods"].items():
            if k in cur or k in w: continue
            r = sorted(p.items(), key=lambda kv: (-kv[1][1], -kv[1][0]))[:3]
            w[k] = [{"uid": int(u), "name": s["names"].get(u) or "tanpa nama", "chats": v[0], "words": v[1]} for u, v in r]; ch = True
        if ch:
            tmp = WF + ".tmp"; json.dump(w, open(tmp, "w"), ensure_ascii=False, indent=1); os.replace(tmp, WF)
        return ch
def _timer():
    while True:
        time.sleep(300)
        try: finalize()
        except Exception as e: logging.exception("finalize failed: %s", e)
def start_timer():
    finalize(); threading.Thread(target=_timer, daemon=True).start()
def _label(k):
    t, v = k.split(":", 1)
    if t == "d": d = datetime.date.fromisoformat(v); return f"{d.day} {BLN[d.month-1]} ({HARI[d.weekday()]})"
    if t == "w":
        d = datetime.datetime.strptime(v + "-1", "%G-W%V-%u").date(); e = d + datetime.timedelta(days=6)
        return f"{d.day} {BLN[d.month-1]} - {e.day} {BLN[e.month-1]}"
    y, mo = v.split("-"); return f"{BLN[int(mo)-1]} {y}"
def winners_text(key):
    k, lbl = PER[key]; n = {"d": 7, "w": 4, "m": 3}[k]; nama = {"d": "harian", "w": "mingguan", "m": "bulanan"}[k]
    w = _wload(); ks = sorted(x for x in w if x.startswith(k + ":"))[-n:][::-1]
    out = [f"pemenang {nama}:"]
    for x in ks:
        es = w[x]
        out.append("\n" + f"{_label(x)}: \n" + "\n".join(f"{i}. {e['name']} : {e['words']} perkataan" + ("," if i < len(es) else "") for i, e in enumerate(es, 1)))
    if not ks:
        hint = {"d": "hari ni baru habis tengah malam", "w": "minggu ni baru habis Ahad malam", "m": "bulan ni baru habis hujung bulan"}[k]
        out.append(f"belum ada pemenang {key} lagi, {hint}")
    r = ranking(k)
    r = sorted(r, key=lambda kv: (-kv[1][1], -kv[1][0]))
    if r: out.append(f"\nsetakat ni {lbl}: {name(r[0][0])} {r[0][1][1]} perkataan")
    return "\n".join(out)
def parse(m):
    t = (m.get("text") or "").strip()
    if not t or t[0] not in "/*": return None, []
    parts = t.split(); c = parts[0][1:].lower()
    if "@" in c:
        c, bot = c.split("@", 1)
        if bot != "kakireportbot": return None, []
    c = ALIAS.get(c, c)
    return (c if c in CMDS else None), parts[1:]
def handle(m):
    """True if handled a stat command (group or private)."""
    cmd, args = parse(m)
    if not cmd: return False
    f = m.get("from") or {}; uid = f.get("id"); un = (f.get("username") or "").lower()
    chat = m["chat"]["id"]; grp = m["chat"]["type"] != "private"
    if grp and chat != identity.GROUP: return True
    nm = " ".join(x for x in [f.get("first_name"), f.get("last_name")] if x)
    def reply(t):
        api("sendMessage", chat_id=chat, text=t, reply_to_message_id=m["message_id"], allow_sending_without_reply=True)
    deliver = _deliver(m, reply, uid, grp)
    if cmd in ("stat", "statistik"): reply(own_text(uid, nm))
    elif cmd in ("stattop", "ranking"):
        if not is_admin(uid, un): reply("yang tu admin je boleh tengok. guna /stat untuk statistik sendiri"); return True
        a = (args[0].lower() if args else "hari")
        a = {"harian": "hari", "harini": "hari", "today": "hari", "mingguan": "minggu", "week": "minggu", "bulanan": "bulan", "month": "bulan"}.get(a, a)
        if a not in PER: reply("guna /stattop hari, /stattop minggu atau /stattop bulan"); return True
        txt = top_text(a)
        deliver(txt)
    elif cmd == "pemenang":
        if not is_admin(uid, un): reply("yang tu admin je boleh tengok. guna /stat untuk statistik sendiri"); return True
        a = (args[0].lower() if args else "hari")
        a = {"harian": "hari", "harini": "hari", "today": "hari", "mingguan": "minggu", "week": "minggu", "bulanan": "bulan", "month": "bulan"}.get(a, a)
        if a not in PER: reply("guna /pemenang hari, /pemenang minggu atau /pemenang bulan"); return True
        deliver(winners_text(a))
    elif cmd == "resetstat":
        if un != identity.OWNER or uid != identity.OWNER_ID: reply("maaf, ketua je boleh reset statistik"); return True
        if args[:1] == ["ya"]: reset_all(); reply("ok ketua, semua statistik dah reset")
        else: reply("betul nak reset semua statistik ketua? taip /resetstat ya")
    return True
def _deliver(m, reply, uid, grp):
    def go(txt):
        reply(txt)  # admin (sudah disemak) - balas di tempat dia taip, group atau PM
    return go
