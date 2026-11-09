import os, re, json, time, datetime, threading, logging, collections, requests
from common import api, DATA, TZ, BASE
import identity
MODELS = ["gemini-3.5-flash", "gemini-flash-latest", "gemini-flash-lite-latest"]
DAILY_CAP = 1200
LOGF = os.path.join(BASE, "logs", "replies.log")
LAST = os.path.join(DATA, "last_replied.txt")
USAGE = os.path.join(DATA, "gemini_usage.json")
_lock = threading.Lock()
def key():
    k = os.environ.get("GEMINI_API_KEY")
    if not k:
        for l in open(os.path.join(BASE, ".env")):
            if l.startswith("GEMINI_API_KEY="): k = l.split("=",1)[1].strip()
    return k
EMOJI = re.compile("[\U0001F000-\U0001FFFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D\u20E3]")
def strip_emoji(s): return re.sub(r"[ \t]{2,}", " ", EMOJI.sub("", s)).strip()
SKIP_RE = re.compile(r"^\W*(tq+\d*|tqsm|alhamdulillah|subhanallah|masya?allah|insya?allah|salam|assalamualaikum|tqvm|thanks?|thank\s*you|terima\s*kasih|tenkiu|tengkiu|ready|rdy|waalaikum\w*|w\.?salam|aamii?n|amin|ok+|okay|okey|baik|noted|done|(he|ha|hi|hu)+|wk+)(\s+\w+){0,2}\W*$", re.I)
def trivial(t):
    t = (t or "").strip()
    if not t or not re.search(r"\w", EMOJI.sub("", t)): return True
    return bool(SKIP_RE.match(t))
history = collections.deque(maxlen=40)  # (message_id, name, text)
def note(m, nm):
    record_pref(m)
    history.append((m["message_id"], nm, (m.get("text") or m.get("caption") or "")[:300]))
def _usage(inc=0):
    with _lock:
        d = datetime.datetime.now(TZ).date().isoformat()
        try: u = json.load(open(USAGE))
        except Exception: u = {}
        u[d] = u.get(d, 0) + inc
        if inc: json.dump(u, open(USAGE, "w"), indent=1)
        return u[d]
def _style() -> str:
    return f"""Kau ialah KR (Kaki Report), seorang ahli biasa dalam group Telegram "MP HAIREE" (group kongsi link MP TNG). Kau BUKAN bot atau AI pada mata ahli: jangan sekali-kali mengaku AI/bot kecuali ditanya terus.
Gaya: Bahasa Melayu santai macam orang Malaysia sembang dalam group. Guna slang semula jadi (x, tak, je, la, kot, weh, pastu, dah, haha, aduh, amboi, eh). Huruf kecil ok. Pendek, biasanya satu baris. Variasi ayat, jangan mula dengan cara sama. Suka mengusik dan kelakar tapi sopan.
Peraturan: TIADA emoji langsung. Panggil @{identity.OWNER} "ketua". Jangan kasar. DILARANG KERAS sebarang lawak lucah, double meaning atau innuendo seks (contoh "mimpi basah") - ramai ahli perempuan dan mak-mak. Jangan politik atau debat agama. Jangan reka fakta; kalau tak pasti, buat lawak atau cakap "nanti ketua check". Jangan guna ayat formal macam "Terima kasih atas perkongsian", "Baik, saya faham", "Sila". Jangan tulis nama/label di depan jawapan.\nKalau boleh, kaitkan mesej dia dengan apa dia buat semalam (maklumat semalam diberi dalam prompt) secara lawak atau jadikan soalan usik. Contoh: Una semalam main MP sampai tengah malam, pagi ni dia kata "aduh ngantuk", kau balas "ha tu lah una, semalam bukan main sakan main mp, hari ni dah macam kerang busuk mengantuk". Kebanyakan mesej TAK perlu dikaitkan dengan semalam; balas ikut topik semasa macam biasa. Kaitkan hanya bila mesej tu jelas ada kena-mengena dengan apa dia buat semalam (contoh mengantuk lepas main sampai lewat malam), berdasarkan maklumat sebenar, jangan reka cerita, jangan paksa.\nSentiasa panggil orang yang kau balas dengan nama panggilan dia dalam ayat (contoh "haha sherry ni..."), guna nama yang diberi dalam prompt. Kalau ada nama panggilan khas, guna itu sahaja. Sharifah (@shfarisya13) WAJIB dipanggil Sherry atau Shark, jangan sekali-kali Sharifah. PENTING: baca sejarah chat dan ikut cara ahli lain panggil orang tu (contoh kalau orang panggil Nur Farhanah "kak hana", kau pun panggil "kak hana"). WAJIB letak "kak" depan nama perempuan dan "brader" depan nama lelaki setiap kali sebut nama (contoh "kak shima", "kak una", "kak hana", "brader ali"). Jangan sekali-kali sebut nama kosong macam "shima" atau "una". Kalau tak tahu nama dia, sebut "kak" atau "brader" sahaja. Kalau tak pasti jantina, guna "kak". Pengecualian: ketua tetap "ketua", dan Sharifah dipanggil Sherry atau Shark. Bila sebut diri sendiri, WAJIB guna "saya", jangan sekali-kali guna "aku" atau "gua". Jangan guna nama penuh atau nama akaun Telegram. PALING PENTING: kalau seseorang pernah minta jangan dipanggil dengan sesuatu nama (contoh "jangan panggil aku X", "tak suka orang panggil X", "panggil aku Y je"), WAJIB ikut permintaan dia, ini mengatasi semua peraturan nama lain (kecuali kak/brader tetap di depan nama yang dia mahu)."""
def gemini(prompt, timeout=10):
    k = key()
    for mdl in MODELS:
        try:
            _usage(1)
            r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{mdl}:generateContent",
                headers={"x-goog-api-key": k}, timeout=timeout,
                json={"systemInstruction": {"parts": [{"text": _style()}]}, "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                      "generationConfig": ({"temperature": 1.0, "maxOutputTokens": 200, "thinkingConfig": {"thinkingBudget": 0}} if mdl == MODELS[0] else {"temperature": 1.0, "maxOutputTokens": 400})})
            if r.status_code != 200:
                logging.warning("gemini %s http %s", mdl, r.status_code); continue
            parts = r.json()["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
        except Exception as e:
            logging.warning("gemini %s error %s", mdl, type(e).__name__)
    return None
LINK_RE = re.compile(r"ngantuk|mengantuk|penat|letih|tido|tidur|bangun|lambat|semalam|smlm|malam tadi|mata|lewat|qada|kurang tidur|sakit kepala|pening|lapar|kenyang|menang|kalah|rindu", re.I)
_yc = {}
def yesterday_ctx(uid):
    import json as _j, datetime as _d
    day = (_d.datetime.now() - _d.timedelta(days=1)).strftime("%Y-%m-%d")
    if _yc.get("day") != day:
        rows = []
        try:
            for ln in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", day + ".jsonl"), encoding="utf-8"):
                try: rows.append(_j.loads(ln))
                except Exception: pass
        except Exception: pass
        _yc.clear(); _yc["day"] = day; _yc["rows"] = rows
    mine = [r for r in _yc["rows"] if r.get("user_id") == uid and r.get("type", "message") == "message"]
    if not mine: return ""
    links = sum(len(r.get("links") or []) for r in mine)
    t0, t1 = mine[0]["ts"][11:16], mine[-1]["ts"][11:16]
    txt = [f"{r['ts'][11:16]} {r['text'][:120]}" for r in mine if r.get("text") and len(strip_emoji(r["text"]).strip()) >= 10]
    if len(txt) > 15: txt = [txt[int(i * (len(txt) - 1) / 14)] for i in range(15)]
    return (f"\n\nApa orang ni buat SEMALAM ({day}) dalam group: {len(mine)} mesej, chat dari {t0} sampai {t1}, kongsi {links} link MP. "
            "Antara mesej dia semalam:\n" + "\n".join(txt))
def build_prompt(m, nm, is_call):
    t = m.get("text") or m.get("caption") or ""
    ctx = "\n".join(f"{n}: {x}" for i, n, x in list(history)[-40:] if i != m["message_id"])[-6000:]
    rt = m.get("reply_to_message")
    rtx = f"\nMesej ini membalas {name_of(rt.get('from', {}))}: {(rt.get('text') or rt.get('caption') or '')[:300]}" if rt else ""
    if is_call and (m.get('from',{}).get('username') or '').lower() == identity.OWNER:
        task = "Ketua sedang memanggil/bercakap terus dengan kau. Jawab dia sekarang (wajib balas, jangan SKIP)."
    elif is_call:
        task = "Orang ni sedang bercakap terus dengan kau (tag, reply atau panggil nama kau). Jawab dia sekarang, panggil dia dengan nama panggilan yang betul (wajib balas, jangan SKIP)."
    else:
        task = ("Putuskan sama ada mesej ini patut dibalas. Balas kalau ia soalan, lawak, sapaan yang ada isi, kongsi link MP, atau ditujukan pada KR. "
                "Kalau tak perlu (sembang biasa antara orang lain, tiada apa nak ditambah), jawab tepat SKIP.")
    return f"Sembang terkini dalam group:\n{ctx}\n\n{yesterday_ctx(m.get('from',{}).get('id')) if LINK_RE.search(t) else ''}\n\nMesej sasaran daripada {nm}{' (ketua)' if (m.get('from',{}).get('username') or '').lower()== identity.OWNER else ''}: {t}{rtx}{pref_ctx(m.get('from',{}).get('id'))}\n\n{task}\nTulis jawapan kau sahaja."
PREF_RE = re.compile(r"(jangan|jgn|xnak|tak nak|taknak|tak suka|x suka|tolong jangan)\s*(la\s*)?(panggil|pnggl|pgl|sebut|gelar)|panggil\s*(aku|saya|sy|ak)\s+\S+\s*(je|ja|sahaja|aje|jela)|(nama|panggilan)\s*(aku|saya)\s*(bukan|adalah|ialah)", re.I)
_PREF_F = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "name_prefs.json")
def _prefs():
    try:
        import json as _j; return _j.load(open(_PREF_F))
    except Exception: return {}
def record_pref(m):
    try:
        t = (m.get("text") or m.get("caption") or "").strip()
        if not t or not PREF_RE.search(t): return
        import json as _j
        f = m.get("from", {}); d = _prefs(); k = str(f.get("id"))
        rt = m.get("reply_to_message") or {}
        extra = f" (membalas {name_of(rt.get('from', {}))}: {(rt.get('text') or '')[:120]})" if rt else ""
        d.setdefault(k, []).append(t[:200] + extra); d[k] = d[k][-5:]
        _j.dump(d, open(_PREF_F, "w"), ensure_ascii=False, indent=1)
        log_line(f"name pref recorded for {k}: {t[:100]}")
    except Exception as e: log_line(f"pref err {e}")
def pref_ctx(uid):
    p = _prefs().get(str(uid))
    return ("\nPERMINTAAN NAMA daripada orang ni sebelum ni (WAJIB ikut, jangan panggil nama yang dia tak suka): " + " | ".join(p)) if p else ""
def _nicks():
    try:
        import json as _j; return {k.lower(): v for k, v in _j.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "nicknames.json"))).items()}
    except Exception: return {}
def name_of(f):
    u = (f.get("username") or "").lower()
    if u == identity.OWNER: return "ketua"
    nk = _nicks().get(u)
    if nk: return nk
    return " ".join(x for x in [f.get("first_name"), f.get("last_name")] if x) or f.get("username") or "?"
BAD = re.compile(r"basah|seks|sex|lucah|bogel|ranjang|ghairah|\bpuki|\bbabi|bodoh|sial|celaka|\b(?:ai|bot)\b.*\b(?:saya|aku)\b", re.I)
def gen_reply(m, is_call=False, timeout=10):
    out = gemini(build_prompt(m, name_of(m.get("from", {})), is_call), timeout)
    if not out: return None
    out = strip_emoji(out.strip().strip('"'))
    out = re.sub(r"^(KR|Kaki Report)\s*:\s*", "", out, flags=re.I)
    if not out or re.fullmatch(r"\W*SKIP\W*", out, re.I) or out.upper().startswith("SKIP"): return "SKIP"
    if BAD.search(out): logging.warning("blocked unsafe reply"); return "SKIP"
    out = re.sub(r"\baku\b", "saya", out); out = re.sub(r"\bAku\b", "Saya", out)
    return out[:500]
def log_line(s):
    try:
        os.makedirs(os.path.dirname(LOGF) or ".", exist_ok=True)
        with open(LOGF, "a") as f:
            f.write(f"{datetime.datetime.now(TZ).replace(tzinfo=None).isoformat()} {s}\n")
    except Exception:
        logging.exception("replies.log write failed")
def mark(mid):
    with _lock:
        try: cur = int(open(LAST).read().strip())
        except Exception: cur = 0
        if mid > cur: open(LAST, "w").write(str(mid))
def send(chat_id, mid, text):
    try:
        r = api("sendMessage", chat_id=chat_id, text=text, reply_to_message_id=mid, allow_sending_without_reply=True)
        log_line(f"sent reply to {mid} ok=True [gemini] {text}"); mark(mid)
        history.append((r.get("message_id"), "KR", text)); return r
    except Exception as e:
        logging.exception("gemini send failed: %s", e)
        try:
            log_line(f"sent reply to {mid} ok=False err={e}")
        except Exception:
            pass
        return None
# ---- queue for non-ketua messages ----
GAP = 60; PER_USER = 120;  # default; live value from settings
import admin as _admin
WINDOW = 300; MAX_WIN = 5; MAX_AGE = 600
_q = collections.deque(); _sent_times = collections.deque(); _user_last = {}; _handled = set()
def enqueue(m, me):
    try:
        if m["chat"]["id"] != identity.GROUP: return
        f = m.get("from", {}) or {}
        if f.get("is_bot") or not m.get("text"): return
        if (f.get("username") or "").lower() == identity.OWNER: return
        if trivial(m["text"]): return
        rt = (m.get("reply_to_message") or {}).get("from", {}) or {}
        to_bot = rt.get("id") == me["id"] or re.search(r"kaki\s*report|\bkr\b|@kakireportbot", m["text"], re.I)
        # avoid loops: replying to bot's reply twice in a row is handled by per-user limit
        with _lock: _q.append((time.time(), m, bool(to_bot)))
    except Exception as e: logging.exception("enqueue failed: %s", e)
def prio(item):
    _, m, to_bot = item; t = m["text"]
    return (3 if to_bot else 0) + (2 if "?" in t else 0) + (1 if "links.tngdigital.com.my" in t else 0)
_last_call = [0]
def worker():
    last_send = 0
    while True:
        time.sleep(5)
        try:
            now = time.time()
            if now - last_send < GAP: continue
            while _sent_times and now - _sent_times[0] > WINDOW: _sent_times.popleft()
            if len(_sent_times) >= MAX_WIN: continue
            if _usage(0) >= DAILY_CAP: continue
            _st = _admin.settings()
            if not _st.get("replies_on", True): continue
            PER = int(_st.get("per_user_min", 2)) * 60
            with _lock:
                items = [x for x in _q if now - x[0] < MAX_AGE]; _q.clear()
                items = [x for x in items if x[1]["message_id"] not in _handled]
                items.sort(key=prio, reverse=True)
            picked = None
            for it in items:
                uid = it[1]["from"]["id"]
                if now - _user_last.get(uid, 0) < PER: continue
                picked = it; break
            if picked:
                with _lock:
                    for it in items:
                        if it is not picked: _q.append(it)
            else:
                with _lock: _q.extend(items)
                continue
            if now - _last_call[0] < 20: 
                with _lock: _q.append(picked)
                continue
            _last_call[0] = now
            m = picked[1]; _handled.add(m["message_id"])
            r = gen_reply(m, False, 20)
            if r and r != "SKIP":
                if send(m["chat"]["id"], m["message_id"], r):
                    last_send = time.time(); _sent_times.append(last_send); _user_last[m["from"]["id"]] = last_send
            else:
                log_line(f"skip {m['message_id']} ({'gemini SKIP' if r else 'gemini fail'})")
                _user_last.setdefault(m["from"]["id"], 0)
        except Exception as e: logging.exception("worker error: %s", e)
def start_worker():
    threading.Thread(target=worker, daemon=True).start()
