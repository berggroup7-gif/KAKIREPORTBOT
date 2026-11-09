import socket; socket.setdefaulttimeout(90)
import os, json, time, datetime, logging, threading
from common import api, DATA, TZ
import identity
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
OFF = os.path.join(DATA, "offset.txt"); CHATS = os.path.join(DATA, "chats.json")
def load_chats():
    try: return json.load(open(CHATS))
    except Exception: return {}
def save_chats(c):
    tmp = CHATS + ".tmp"; json.dump(c, open(tmp, "w"), ensure_ascii=False, indent=1); os.replace(tmp, CHATS)
def name(u):
    return " ".join(x for x in [u.get("first_name"), u.get("last_name")] if x) if u else None
import re
URL_RE = re.compile(r"(?:https?://|www\.|\b[a-z0-9.-]+\.[a-z]{2,}/)\S+", re.I)
def links_of(m):
    out = []
    for txt, ents in ((m.get("text"), m.get("entities")), (m.get("caption"), m.get("caption_entities"))):
        if not txt: continue
        b = txt.encode("utf-16-le")
        for e in ents or []:
            if e["type"] == "text_link": out.append(e["url"])
            elif e["type"] == "url": out.append(b[e["offset"]*2:(e["offset"]+e["length"])*2].decode("utf-16-le"))
        for u in URL_RE.findall(txt):
            if u not in out: out.append(u)
    return out
def write(ts, rec):
    with open(os.path.join(DATA, ts.strftime("%Y-%m-%d") + ".jsonl"), "a") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
def ev(kind, c, ts, user, src, mid=None):
    write(ts, {"type": kind, "chat_id": c["id"], "chat_title": c.get("title"), "ts": ts.isoformat(), "message_id": mid,
               "user_id": user.get("id"), "name": name(user), "username": user.get("username"), "is_bot": user.get("is_bot"), "source": src})

def _report_users():
    return {identity.OWNER, "unaalhaq"}
REPORT_WORDS = re.compile(r"\b(report|laporan|lapuran)\b", re.I)
REQ_RE = re.compile(r"\b(bagi|bg|hantar|send|share|kongsi|kongsikan|minta|mintak|nak|nk|mana|tunjuk|tunjukkan|post|give|keluarkan|berikan|beri)\b[\w\s]{0,25}?\b(report|laporan|lapuran)\b|\b(report|laporan|lapuran)\b\s*(semalam|terkini|harian|hari\s*ni|tadi|please|pls|plz)?\s*[?!.]*$", re.I)
def maybe_share_report(m, me):
    try:
        f = m.get("from", {}); u = (f.get("username") or "").lower()
        if u not in _report_users() or m.get("edited_date"): return
        t = m.get("text") or ""
        if not t or len(t) > 150: return
        bot_tag = "@" + me["username"].lower()
        rt = (m.get("reply_to_message") or {}).get("from", {}) or {}
        if bot_tag not in t.lower() and rt.get("id") != me["id"]: return
        clean = re.sub(re.escape(bot_tag), " ", t, flags=re.I)
        clean = re.sub(r"kaki\s*report|\bKR\b", " ", clean, flags=re.I)
        if not REQ_RE.search(clean.strip()): return
        q = ("Baik, sebelum saya hantar, boleh beritahu dulu:\n"
             "1. Report yang mana satu? (contoh: report harian kumpulan, statistik MP, ahli baru)\n"
             "2. Untuk bila? (contoh: semalam, hari ni, 1 Nov)\n"
             "3. Nak yang macam mana? (penuh, statistik sahaja, atau ringkasan pendek)\n"
             "4. Nak saya hantar dalam group ni atau di PM?\n"
             "Reply mesej ni dengan jawapan, nanti saya sediakan.")
        r = api("sendMessage", chat_id=m["chat"]["id"], text=q, reply_to_message_id=m["message_id"], allow_sending_without_reply=True)
        pf = os.path.join(DATA, "report_requests.json")
        try: reqs = json.load(open(pf))
        except Exception: reqs = []
        reqs.append({"status": "asked", "chat_id": m["chat"]["id"], "user": u, "user_id": f.get("id"), "request_message_id": m["message_id"],
                     "request_text": t, "question_message_id": (r or {}).get("message_id"), "asked_at": datetime.datetime.now(TZ).isoformat()})
        json.dump(reqs, open(pf, "w"), indent=1, ensure_ascii=False)
        logging.info("asked report details for @%s (q msg %s)", u, (r or {}).get("message_id"))
        return True
    except Exception as e:
        logging.exception("share report failed: %s", e)
import random
NAME_RE = re.compile(r"kaki\s*report|\bKR\b", re.I)
Q_RE = re.compile(r"\?|\b(apa|kenapa|napa|mana|bila|siapa|macam\s*mana|camne|mcm\s*mane|ke)\b", re.I)
TEASE_RE = re.compile(r"\b(tido|tidur|senyap|idle|gaji\s*buta|lambat|mana|sidai|lena|hilang)\b", re.I)
ACKS = {
 "tease": ["eh mana ada tido ketua, mata ni segar lagi haha",
           "amboi ketua, kita ada je ni. cakap la",
           "tido apa ketua, ni tengah jaga group ni haha",
           "ada ada, jangan risau ketua. apa cerita?"],
 "question": ["jap ketua, saya tengok dulu",
              "ok ketua, kejap ye bagi saya check",
              "hmm jap ketua, saya semak kejap",
              "ada ketua, kejap saya jawab"],
 "general": ["ye ketua, ada ni",
             "hah ketua, kenapa tu",
             "ada ketua, apa hal",
             "yo ketua, sini sini"]}
ACK_COOLDOWN = 20; _last_ack = {"ts": 0.0, "line": None}
import gemini_reply, admin, chatstats
_acked = set()
def is_owner_call(m, me):
    f = m.get("from", {}) or {}
    if (f.get("username") or "").lower() != identity.OWNER: return False
    t = m.get("text") or m.get("caption") or ""
    rt = (m.get("reply_to_message") or {}).get("from", {}) or {}
    if ("@" + me["username"].lower()) in t.lower() or rt.get("id") == me["id"]: return True
    return name_call(t, bool(m.get("reply_to_message")))
NAME_P = r"(?:kaki\s*report|kakireport|kr)"
VOC_START = re.compile(r"^\W*(?:(?:hai|hoi|oi|weh|woi|wei|eh|hello|helo|hey)\s+)?" + NAME_P + r"\b(?!\s*(?:tu|ni|tadi|dia)\b)", re.I)
VOC_END = re.compile(r"(?:^|\b(?:hai|hoi|oi|weh|woi|wei|eh|hello|helo|hey|mana|mari|sini|jom|tolong|ya|ye)\s+)" + NAME_P + r"\W*$", re.I)
THIRD = re.compile(r"\b(dia|bot\s+tu|bot\s+ni)\b|" + NAME_P + r"\s+(?:tu|ni|tadi)\b", re.I)
def is_bot_call(m, me):
    f = m.get("from", {}) or {}
    if f.get("is_bot"): return False
    t = m.get("text") or m.get("caption") or ""
    rt = (m.get("reply_to_message") or {}).get("from", {}) or {}
    if ("@" + me["username"].lower()) in t.lower() or rt.get("id") == me["id"]: return True
    return name_call(t, bool(m.get("reply_to_message")))
OTHER_ACKS = ["hah ye, kenapa tu", "ada ni, apa cerita", "ye ye saya dengar ni", "hm apa dia tu", "sini sini, kenapa"]
_call_last = {}
def direct_reply(m, me):
    try:
        if m["message_id"] in _acked: return
        uid = (m.get("from") or {}).get("id")
        if time.time() - _call_last.get(uid, 0) < 30: return
        try:
            if not admin.settings().get("replies_on", True): return
        except Exception: pass
        _acked.add(m["message_id"]); _call_last[uid] = time.time()
        time.sleep(random.randint(55, 100))
        g = None
        try: g = gemini_reply.gen_reply(m, True, 12)
        except Exception as e: logging.warning("gemini direct reply failed: %s", e)
        line = g if g and g != "SKIP" else random.choice([x for x in OTHER_ACKS if x != _last_ack["line"]])
        api("sendMessage", chat_id=m["chat"]["id"], text=line, reply_to_message_id=m["message_id"], allow_sending_without_reply=True)
        _last_ack.update(ts=time.time(), line=line)
        gemini_reply._handled.add(m["message_id"])
        gemini_reply.history.append((None, "KR", line))
        logging.info("direct reply to msg %s (gemini=%s)", m["message_id"], bool(g and g != 'SKIP'))
        gemini_reply.log_line(f"sent reply to {m['message_id']} ok=True [direct] {line}"); gemini_reply.mark(m['message_id'])
    except Exception as e:
        logging.exception("direct reply failed: %s", e)
def name_call(t, is_reply_to_other):
    t = t.strip()
    if not t or is_reply_to_other: return False
    if THIRD.search(t): return False
    return bool(VOC_START.search(t) or VOC_END.search(t))
def classify(t):
    if TEASE_RE.search(t): return "tease"
    if Q_RE.search(t): return "question"
    return "general"
def pick_ack(t):
    pool = [x for x in ACKS[classify(t)] if x != _last_ack["line"]]
    return random.choice(pool)
def instant_ack(m, me):
    try:
        if not is_owner_call(m, me): return
        t = m.get("text") or m.get("caption") or ""
        if m["message_id"] in _acked: return
        _acked.add(m["message_id"])
        g = None
        try: g = gemini_reply.gen_reply(m, True, 10)
        except Exception as e: logging.warning("gemini call reply failed: %s", e)
        line = g if g and g != "SKIP" else pick_ack(t)
        r = api("sendMessage", chat_id=m["chat"]["id"], text=line, reply_to_message_id=m["message_id"], allow_sending_without_reply=True)
        _last_ack.update(ts=time.time(), line=line)
        pf = os.path.join(DATA, "instant_acks.json")
        try: acks = json.load(open(pf))
        except Exception: acks = []
        acks.append({"message_id": m["message_id"], "text": t, "ack_message_id": (r or {}).get("message_id"), "ack_text": line,
                     "kind": classify(t), "ts": datetime.datetime.now(TZ).isoformat()})
        json.dump(acks, open(pf, "w"), indent=1, ensure_ascii=False)
        logging.info("instant ack to msg %s (gemini=%s)", m["message_id"], bool(g and g != 'SKIP'))
        gemini_reply.log_line(f"sent reply to {m['message_id']} ok=True [ketua-call] {line}"); gemini_reply.mark(m['message_id'])
    except Exception as e:
        logging.exception("instant ack failed: %s", e)
def main():
    os.makedirs(DATA, exist_ok=True)
    me = api("getMe"); logging.info("getMe ok: @%s can_read_all_group_messages=%s", me["username"], me.get("can_read_all_group_messages"))
    gemini_reply.start_worker()
    admin.set_commands()
    try: logging.info("stats backfill: %s", chatstats.backfill())
    except Exception as e: logging.exception("stats backfill failed: %s", e)
    try: chatstats.start_timer()
    except Exception as e: logging.exception("winners timer failed: %s", e)
    import threading, time as _t
    hb = [_t.time()]
    def _wd():
        while True:
            _t.sleep(10)
            if _t.time() - hb[0] > 120:
                logging.error("watchdog: getUpdates stuck >180s, restarting"); os._exit(3)
    threading.Thread(target=_wd, daemon=True).start()
    offset = int(open(OFF).read()) if os.path.exists(OFF) else None
    while True:
        p = {"timeout": 25, "allowed_updates": ["message", "edited_message", "my_chat_member", "chat_member"]}
        if offset is not None: p["offset"] = offset
        import concurrent.futures as _cf
        _ex = _cf.ThreadPoolExecutor(max_workers=1)
        _fut = _ex.submit(api, "getUpdates", **p); _ex.shutdown(wait=False)
        try: ups = _fut.result(timeout=40)
        except _cf.TimeoutError:
            logging.warning("getUpdates hung >40s, reconnecting"); hb[0] = _t.time(); continue
        except Exception as e:
            logging.warning("getUpdates error: %s, retrying", e); hb[0] = _t.time(); _t.sleep(2); continue
        hb[0] = _t.time()
        chats = load_chats(); changed = False
        for u in ups:
            now = datetime.datetime.now(TZ).isoformat()
            if "my_chat_member" in u:
                m = u["my_chat_member"]; c = m["chat"]
                chats[str(c["id"])] = {"title": c.get("title"), "type": c["type"], "status": m["new_chat_member"]["status"], "updated": now}
                changed = True; logging.info("my_chat_member %s %s -> %s", c["id"], c.get("title"), m["new_chat_member"]["status"])
            if "chat_member" in u:
                cm = u["chat_member"]; c = cm["chat"]; ts = datetime.datetime.fromtimestamp(cm["date"], TZ)
                was = cm["old_chat_member"]["status"] in ("member","administrator","creator","restricted") and cm["old_chat_member"].get("is_member", True)
                isn = cm["new_chat_member"]["status"] in ("member","administrator","creator") or (cm["new_chat_member"]["status"]=="restricted" and cm["new_chat_member"].get("is_member"))
                if was != isn: ev("join" if isn else "leave", c, ts, cm["new_chat_member"]["user"], "chat_member")
            m = u.get("message") or u.get("edited_message")
            if m and m["chat"]["type"] == "private":
                f = m.get("from", {}); import json as _j
                pf = os.path.join(os.path.dirname(OFF), "private_users.json")
                try: pu = _j.load(open(pf))
                except Exception: pu = {}
                pu[str(f.get("id"))] = {"username": f.get("username"), "name": name(f), "chat_id": m["chat"]["id"], "updated": now}
                _j.dump(pu, open(pf, "w"), indent=1); logging.info("private from @%s", f.get("username"))
                if "message" in u:
                    try: res = True if chatstats.handle(m) else admin.handle(m)
                    except Exception as e: logging.exception("admin cmd failed: %s", e); res = False
                    if res == "restart":
                        open(OFF, "w").write(str(u["update_id"] + 1)); logging.info("admin restart"); os._exit(0)
            if m and m["chat"]["type"] in ("group", "supergroup"):
                c = m["chat"]; ts = datetime.datetime.fromtimestamp(m["date"], TZ)
                prev = chats.get(str(c["id"]), {})
                chats[str(c["id"])] = {**prev, "title": c.get("title"), "type": c["type"], "status": prev.get("status", "member"), "updated": now}; changed = True
                f = m.get("from", {})
                for nm in m.get("new_chat_members") or []: ev("join", c, ts, nm, "service", m["message_id"])
                if m.get("left_chat_member"): ev("leave", c, ts, m["left_chat_member"], "service", m["message_id"])
                if m.get("new_chat_members") or m.get("left_chat_member"): continue
                rec = {"type": "message", "user_id": f.get("id"), "links": links_of(m), "chat_id": c["id"], "chat_title": c.get("title"), "message_id": m["message_id"], "ts": ts.isoformat(),
                       "sender": name(f), "username": f.get("username"), "text": m.get("text") or m.get("caption"),
                       "reply_to": (m.get("reply_to_message") or {}).get("message_id"), "edited": "edited_message" in u}
                write(ts, rec)
                if not rec["edited"]:
                    try: chatstats.record(m)
                    except Exception as e: logging.exception("stats record failed: %s", e)
                    if (m.get("text") or "").startswith("/") or chatstats.parse(m)[0]:
                        def _h(mm=m):
                            try: chatstats.handle(mm)
                            except Exception as e: logging.exception("stat cmd failed: %s", e)
                        threading.Thread(target=_h, daemon=True).start()
                        offset = u["update_id"] + 1; continue
                    if c["id"] == identity.GROUP and chatstats.is_spell_spam(m.get("text") or m.get("caption")):
                        offset = u["update_id"] + 1; continue
                    gemini_reply.note(m, gemini_reply.name_of(f))
                    if not maybe_share_report(m, me):
                        if is_owner_call(m, me): threading.Thread(target=instant_ack, args=(m, me), daemon=True).start()
                        elif c["id"] == identity.GROUP and is_bot_call(m, me): threading.Thread(target=direct_reply, args=(m, me), daemon=True).start()
                        else: gemini_reply.enqueue(m, me)
            offset = u["update_id"] + 1
        if changed: save_chats(chats)
        if ups: open(OFF, "w").write(str(offset))
if __name__ == "__main__": main()
