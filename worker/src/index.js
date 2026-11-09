// KAKIREPORTBOT on Cloudflare Workers.
// Telegram calls POST /telegram (webhook, secret header checked). The Worker hands the
// update to a single Durable Object (BotState) which records raw messages + stats in its
// SQLite storage synchronously, then answers commands / Gemini replies in the background.
import * as S from "./stats.js";
import * as R from "./reply.js";
import { HELP } from "./persona.js";
import { pyStrip, pySplit, cpSlice, cpLen } from "./pyre.js";

const log = (ev, x = {}) => console.log(JSON.stringify({ ev, ...x }));
const truthy = (v) => ["1", "true", "yes", "on"].includes(String(v || "").toLowerCase());
const READ_ONLY = new Set(["getMe", "getChat", "getChatAdministrators", "getChatMember", "getWebhookInfo"]);

function safeEq(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || !b || a.length !== b.length) return false;
  let r = 0;
  for (let i = 0; i < a.length; i++) r |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return r === 0;
}

const slimUser = (f) => (f ? { id: f.id, is_bot: f.is_bot, first_name: f.first_name, last_name: f.last_name, username: f.username } : undefined);
function slim(m) {
  const rt = m.reply_to_message;
  return { message_id: m.message_id, date: m.date, chat: { id: m.chat.id, type: m.chat.type }, from: slimUser(m.from), text: m.text, caption: m.caption,
    reply_to_message: rt ? { message_id: rt.message_id, from: slimUser(rt.from), text: rt.text ? [...rt.text].slice(0, 300).join("") : undefined, caption: rt.caption ? [...rt.caption].slice(0, 300).join("") : undefined } : undefined };
}

export function config(env) {
  const token = env.TELEGRAM_BOT_TOKEN || "";
  return {
    groupId: Number(env.GROUP_CHAT_ID || 0),
    ownerId: Number(env.OWNER_ID || 0),
    owner: String(env.OWNER_USERNAME || "").replace(/^@/, "").toLowerCase(),
    me: { id: Number(token.split(":")[0]) || 0, username: String(env.BOT_USERNAME || "KAKIREPORTBOT").replace(/^@/, "") },
    dryRun: truthy(env.DRY_RUN),
    geminiMock: truthy(env.GEMINI_MOCK),
    gap: Number(env.GAP_SEC || 60) * 1000, // min gap between queued (unsolicited) replies
    window: Number(env.WINDOW_SEC || 300) * 1000,
    maxWin: Number(env.MAX_PER_WINDOW || 5),
    maxAge: Number(env.MAX_AGE_SEC || 600) * 1000, // also: older messages get no unsolicited reply
    callGap: Number(env.GEMINI_GAP_SEC || 20) * 1000,
    perUserFloor: Number(env.PER_USER_FLOOR_SEC || 120) * 1000,
    callCooldown: Number(env.CALL_COOLDOWN_SEC || 30) * 1000,
    dailyCap: Number(env.DAILY_CAP || 1200),
    warnGap: 120 * 1000,
  };
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/") return new Response("ok");
    const stub = env.STATE.get(env.STATE.idFromName("main"));
    if (url.pathname === "/telegram" && request.method === "POST") {
      if (!safeEq(request.headers.get("X-Telegram-Bot-Api-Secret-Token") || "", env.WEBHOOK_SECRET || "")) return new Response("forbidden", { status: 403 });
      const body = await request.text();
      try {
        const r = await stub.fetch("https://do/update", { method: "POST", body });
        const t = await r.text();
        if (r.status !== 200) log("edge_do_status", { status: r.status, t: t.slice(0, 60) });
      } catch (e) {
        log("edge_do_error", { err: e && e.name, msg: String(e && e.message).slice(0, 100) });
      }
      return new Response("ok"); // never make Telegram retry-storm on a bad update
    }
    if (url.pathname.startsWith("/admin/")) {
      if (!safeEq(request.headers.get("X-Admin-Secret") || "", env.ADMIN_SECRET || "")) return new Response("forbidden", { status: 403 });
      return stub.fetch("https://do" + url.pathname + url.search, { method: request.method, body: request.method === "GET" ? undefined : await request.text() });
    }
    return new Response("not found", { status: 404 });
  },
  async scheduled(event, env, ctx) {
    const stub = env.STATE.get(env.STATE.idFromName("main"));
    ctx.waitUntil(stub.fetch("https://do/cron", { method: "POST" }).then((r) => r.text()).then((t) => log("cron", { t: t.slice(0, 80) })));
  },
};

export class BotState {
  constructor(state, env) {
    this.state = state;
    this.env = env;
    this.sql = state.storage.sql;
    this.started = Date.now();
    this.dryLog = [];
    this.init();
  }

  // ---------- storage ----------
  init() {
    const x = (q) => this.sql.exec(q);
    x("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT) WITHOUT ROWID");
    x("CREATE TABLE IF NOT EXISTS stats (period TEXT, uid TEXT, c INTEGER, w INTEGER, ord INTEGER, PRIMARY KEY (period, uid)) WITHOUT ROWID");
    x("CREATE TABLE IF NOT EXISTS names (uid TEXT PRIMARY KEY, name TEXT) WITHOUT ROWID");
    x("CREATE TABLE IF NOT EXISTS penalties (uid TEXT, mid INTEGER, ts TEXT, note TEXT)");
    x("CREATE TABLE IF NOT EXISTS raw (day TEXT, seq INTEGER, rec TEXT, PRIMARY KEY (day, seq)) WITHOUT ROWID");
    x("CREATE TABLE IF NOT EXISTS replies (ts TEXT, mid INTEGER, ok INTEGER, kind TEXT, text TEXT)");
    x("CREATE TABLE IF NOT EXISTS reports (day TEXT PRIMARY KEY, text TEXT) WITHOUT ROWID");
    x("CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, content TEXT) WITHOUT ROWID");
    this.load();
  }
  kvGet(k, dflt) {
    const r = this.sql.exec("SELECT v FROM kv WHERE k = ?", k).toArray();
    if (!r.length) return dflt;
    try { return JSON.parse(r[0].v); } catch { return dflt; }
  }
  kvSet(k, v) { this.sql.exec("INSERT INTO kv (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v", k, JSON.stringify(v)); }
  load() {
    this.cfg = config(this.env);
    // Only the current day/week/month are loaded now; other periods load on demand.
    this.ord = Date.now() * 100; // increasing insert order (imported rows use 1..N), no table scan
    const loadPeriod = (per) => {
      const m = new Map();
      for (const r of this.sql.exec("SELECT uid, c, w FROM stats WHERE period = ? ORDER BY ord", per)) m.set(r.uid, [r.c, r.w]);
      return m;
    };
    this.known = new Set();
    const names = new Map();
    for (const r of this.sql.exec("SELECT uid, name FROM names")) names.set(r.uid, r.name);
    this.meta = this.kvGet("meta", { lastMid: 0, lastUpdateId: 0 });
    const self = this;
    this.stats = new S.Stats({
      keys: this.kvGet("period_keys", []), loader: (per) => { const m = loadPeriod(per); for (const u of m.keys()) self.known.add(per + "|" + u); return m; },
      names, lastMid: this.meta.lastMid || 0, winners: this.kvGet("winners", {}), groupId: this.cfg.groupId,
      nowFn: () => (self.fakeNow != null ? self.fakeNow : Date.now()),
      persist: {
        keys(k) { self.kvSet("period_keys", k); },
        cell(per, uid, c) {
          const key = per + "|" + uid;
          if (!self.known.has(key)) { self.known.add(key); self.ord += 1; }
          self.sql.exec("INSERT INTO stats (period, uid, c, w, ord) VALUES (?, ?, ?, ?, ?) ON CONFLICT(period, uid) DO UPDATE SET c = excluded.c, w = excluded.w", per, uid, c[0], c[1], self.ord);
        },
        name(uid, nm) { self.sql.exec("INSERT INTO names (uid, name) VALUES (?, ?) ON CONFLICT(uid) DO UPDATE SET name = excluded.name", uid, nm); },
        lastMid(mid) { self.meta.lastMid = mid; self.metaDirty = true; },
        penalty(uid, mid, ts) { self.sql.exec("INSERT INTO penalties (uid, mid, ts, note) VALUES (?, ?, ?, NULL)", uid, mid, ts); },
        winners(w) { self.kvSet("winners", w); },
        dropPeriod(k) { self.sql.exec("DELETE FROM stats WHERE period = ?", k); for (const key of [...self.known]) if (key.startsWith(k + "|")) self.known.delete(key); },
        resetPeriods() { self.sql.exec("DELETE FROM stats"); self.known.clear(); },
      },
    });
    this.settings = { replies_on: true, per_user_min: 2, ...this.kvGet("settings", {}) };
    this.nicks = this.kvGet("nicknames", {});
    this.prefs = this.kvGet("name_prefs", {});
    this.chats = this.kvGet("chats", {});
    this.usage = this.kvGet("gemini_usage", {});
    this.daySeq = new Map();
    this.history = this.loadHistory();
    // Reply/rate-limit state. Durable Objects are evicted after short idle gaps, so this is
    // persisted (kv "rt") to keep cooldowns, burst detection and the reply queue across restarts.
    const rt = this.kvGet("rt", {});
    const M = (o) => new Map(Object.entries(o || {}).map(([k, v]) => [Number.isNaN(Number(k)) ? k : Number(k), v]));
    this.q = rt.q || []; this.handled = new Set(rt.handled || []); this.acked = new Set(rt.acked || []); this.sentTimes = rt.sentTimes || [];
    this.userLast = M(rt.userLast); this.callLast = M(rt.callLast); this.warned = M(rt.warned);
    this.lastSend = rt.lastSend || 0; this.lastCall = rt.lastCall || 0; this.lastAck = rt.lastAck || null;
    this.stats.tiny = new Map(Object.entries(rt.tiny || {})); this.stats.burstPen = new Map(Object.entries(rt.burstPen || {}));
    this.rtSaved = JSON.stringify(rt);
    this.busy = false; this.alarmAt = null;
    this.adm = { ts: 0, ids: new Set() }; this.modelBlock = new Map();
  }
  saveRt() {
    const now = this.now(); const nowS = now / 1000;
    const keep = (m, maxAge) => Object.fromEntries([...m].filter(([, v]) => now - v < maxAge));
    const rt = {
      q: this.q.filter((x) => now - x.ts < this.cfg.maxAge), handled: [...this.handled].slice(-300), acked: [...this.acked].slice(-300),
      sentTimes: this.sentTimes.filter((t) => now - t <= this.cfg.window), userLast: keep(this.userLast, 86400000), callLast: keep(this.callLast, 3600000),
      warned: keep(this.warned, 3600000), lastSend: this.lastSend, lastCall: this.lastCall, lastAck: this.lastAck,
      tiny: Object.fromEntries([...this.stats.tiny].map(([k, v]) => [k, v.filter((t) => nowS - t < 300)]).filter(([, v]) => v.length)),
      burstPen: Object.fromEntries([...this.stats.burstPen].filter(([, v]) => nowS - v < 300)),
    };
    this.handled = new Set(rt.handled); this.acked = new Set(rt.acked);
    const j = JSON.stringify(rt);
    if (j !== this.rtSaved) { this.sql.exec("INSERT INTO kv (k, v) VALUES ('rt', ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v", j); this.rtSaved = j; }
  }
  nicksLower() { return Object.fromEntries(Object.entries(this.nicks).map(([k, v]) => [k.toLowerCase(), v])); }
  nameOf(f) { return R.nameOf(f || {}, this.cfg.owner, this.nicksLower()); }
  now() { return Date.now(); }
  todayMyt() { return S.ymd(S.myt(this.now())); }

  insertRaw(tsMs, rec) {
    const day = S.ymd(S.myt(tsMs));
    let seq = this.daySeq.get(day);
    if (seq == null) seq = (this.sql.exec("SELECT max(seq) AS s FROM raw WHERE day = ?", day).toArray()[0].s ?? -1);
    seq += 1; this.daySeq.set(day, seq);
    this.sql.exec("INSERT INTO raw (day, seq, rec) VALUES (?, ?, ?)", day, seq, JSON.stringify(rec));
  }
  ev(kind, c, tsMs, user, src, mid = null) {
    this.insertRaw(tsMs, { type: kind, chat_id: c.id, chat_title: c.title ?? null, ts: S.isoMyt(tsMs), message_id: mid, user_id: user.id ?? null, name: S.fullName(user) || null, username: user.username ?? null, is_bot: user.is_bot ?? null, source: src });
  }
  loadHistory() {
    // Rebuild the last 40 lines of group chat plus own replies after a restart.
    const rows = [];
    const day = this.todayMyt();
    for (const r of this.sql.exec("SELECT rec FROM raw WHERE day = ? ORDER BY seq DESC LIMIT 120", day)) {
      try { const x = JSON.parse(r.rec); if ((x.type || "message") === "message" && !x.edited && x.chat_id === this.cfg.groupId && !(x.text || "").startsWith("/")) rows.push([x.ts.slice(0, 19), x.message_id, this.nameOf({ username: x.username, first_name: x.sender }), x.text || ""]); } catch {}
      if (rows.length >= 40) break;
    }
    for (const r of this.sql.exec("SELECT ts, text FROM replies WHERE ok = 1 AND kind IN ('gemini', 'direct', 'ketua-call') ORDER BY rowid DESC LIMIT 15")) if (r.ts.startsWith(day) && r.text) rows.push([r.ts.slice(0, 19), null, "KR", r.text]);
    rows.sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
    return rows.slice(-R.HISTORY_MAX).map(([, id, n, t]) => [id, n, cpSlice(t, 0, 300)]);
  }

  // ---------- Telegram ----------
  async api(method, params) {
    if (this.cfg.dryRun && !READ_ONLY.has(method)) {
      this.dryLog.push({ method, ...params }); if (this.dryLog.length > 500) this.dryLog.shift();
      const dl = this.kvGet("drylog", []); dl.push({ method, chat_id: params.chat_id, reply_to: params.reply_to_message_id, text: String(params.text || "").slice(0, 300) }); this.kvSet("drylog", dl.slice(-100));
      log("dry_run", { method, chat: params.chat_id, t: String(params.text || "").slice(0, 40) });
      return { message_id: null, dry: true };
    }
    const r = await fetch(`https://api.telegram.org/bot${this.env.TELEGRAM_BOT_TOKEN}/${method}`, {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(params), signal: AbortSignal.timeout(30000),
    });
    const j = await r.json();
    if (!j.ok) throw new Error(`${method} failed: ${j.description}`);
    return j.result;
  }
  async replyTo(m, text) {
    return this.api("sendMessage", { chat_id: m.chat.id, text, reply_to_message_id: m.message_id, allow_sending_without_reply: true });
  }
  async say(text) {
    const cps = [...text]; const parts = [];
    for (let i = 0; i < cps.length; i += 4000) parts.push(cps.slice(i, i + 4000).join(""));
    if (!parts.length) parts.push("");
    for (const p of parts) await this.api("sendMessage", { chat_id: this.cfg.ownerId, text: p || "-" });
  }
  logReply(mid, ok, kind, text) {
    this.sql.exec("INSERT INTO replies (ts, mid, ok, kind, text) VALUES (?, ?, ?, ?, ?)", S.isoMyt(this.now(), true).slice(0, 26).replace("+08:00", ""), mid, ok ? 1 : 0, kind, cpSlice(text || "", 0, 500));
    if (ok && mid > (this.kvGet("last_replied", 0) || 0)) this.kvSet("last_replied", mid);
  }
  bg(name, fn) {
    const p = (async () => {
      try { await fn(); } catch (e) { log("bg_error", { name, err: e && e.name, msg: String(e && e.message).slice(0, 160) }); }
      try { this.saveRt(); } catch {}
    })();
    try { this.state.waitUntil(p); } catch {}
    return p;
  }

  // ---------- Gemini ----------
  incUsage() {
    const d = this.todayMyt(); this.usage[d] = (this.usage[d] || 0) + 1; this.kvSet("gemini_usage", this.usage); return this.usage[d];
  }
  usageToday() { return this.usage[this.todayMyt()] || 0; }
  async gemini(prompt, timeoutMs) {
    if (this.cfg.geminiMock) { this.incUsage(); this.dryLog.push({ method: "gemini", prompt: prompt.slice(-200) }); return this.env.GEMINI_MOCK_REPLY || "SKIP"; }
    const key = this.env.GEMINI_API_KEY; if (!key) return null;
    for (const model of R.MODELS) {
      if ((this.modelBlock.get(model) || 0) > this.now()) continue; // skip a model that just hit its quota
      this.incUsage();
      try {
        const r = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent`, {
          method: "POST", headers: { "x-goog-api-key": key, "content-type": "application/json" },
          body: JSON.stringify(R.geminiBody(prompt, model, this.cfg.owner)), signal: AbortSignal.timeout(timeoutMs),
        });
        if (r.status !== 200) { if (r.status === 429) this.modelBlock.set(model, this.now() + 60000); log("gemini_http", { model, status: r.status }); continue; }
        const j = await r.json();
        const parts = j.candidates[0].content.parts;
        return parts.filter((p) => !p.thought).map((p) => p.text || "").join("").trim();
      } catch (e) { log("gemini_error", { model, err: e && e.name }); }
    }
    return null;
  }
  // "What this person did yesterday" text per user, built once per day from the raw log
  // and cached in kv (one scan of yesterday instead of one per Gemini call).
  yesterdayText(day, uid) {
    const key = "yc:" + day;
    if (!this.ycMem || this.ycMem.day !== day) this.ycMem = { day, map: this.kvGet(key, null) };
    if (!this.ycMem.map) {
      const byUser = new Map();
      for (const r of this.sql.exec("SELECT rec FROM raw WHERE day = ? ORDER BY seq", day)) {
        try { const x = JSON.parse(r.rec); if (x.user_id == null) continue; if (!byUser.has(x.user_id)) byUser.set(x.user_id, []); byUser.get(x.user_id).push(x); } catch {}
      }
      const map = {};
      for (const [u, rows] of byUser) { const t = R.yesterdayCtx(u, day, rows); if (t) map[u] = t; }
      this.kvSet(key, map); this.ycMem.map = map;
      this.sql.exec("DELETE FROM kv WHERE k LIKE 'yc:%' AND k < ?", "yc:" + S.ymd(S.myt(this.now() - 3 * 86400000)));
    }
    return this.ycMem.map[String(uid)] || "";
  }
  async genReply(m, isCall, timeoutMs) {
    const t = R.textOf(m); const uid = (m.from || {}).id;
    let yctx = "";
    if (R.LINK_RE.test(t)) { const day = S.ymd(S.myt(this.now() - 86400000)); yctx = this.yesterdayText(day, uid); }
    const prompt = R.buildPrompt(m, this.nameOf(m.from), isCall, { owner: this.cfg.owner, history: this.history, nicks: this.nicksLower(), prefs: this.prefs, yctx });
    return R.postProcess(await this.gemini(prompt, timeoutMs));
  }
  pushHistory(mid, nm, text) { this.history.push([mid, nm, cpSlice(text || "", 0, 300)]); if (this.history.length > R.HISTORY_MAX) this.history = this.history.slice(-R.HISTORY_MAX); }

  // ---------- update ingest (synchronous state changes) ----------
  ingest(u) {
    const cfg = this.cfg; const out = { tasks: [], step: "ok" };
    const uid = Number(u.update_id);
    if (uid <= (this.meta.lastUpdateId || 0)) { out.step = "dup"; return out; }
    this.meta.lastUpdateId = uid; this.metaDirty = true;
    const nowIso = S.isoMyt(this.now(), true);
    if (u.my_chat_member) {
      const c = u.my_chat_member.chat;
      this.chats[String(c.id)] = { title: c.title ?? null, type: c.type, status: u.my_chat_member.new_chat_member.status, updated: nowIso }; this.kvSet("chats", this.chats);
    }
    if (u.chat_member) {
      const cm = u.chat_member; const ts = cm.date * 1000;
      const was = ["member", "administrator", "creator", "restricted"].includes(cm.old_chat_member.status) && (cm.old_chat_member.is_member ?? true);
      const isn = ["member", "administrator", "creator"].includes(cm.new_chat_member.status) || (cm.new_chat_member.status === "restricted" && !!cm.new_chat_member.is_member);
      if (was !== isn) this.ev(isn ? "join" : "leave", cm.chat, ts, cm.new_chat_member.user, "chat_member");
    }
    const m = u.message || u.edited_message;
    if (!m) return out;
    const f = m.from || {};
    if (m.chat.type === "private") {
      const pu = this.kvGet("private_users", {});
      pu[String(f.id)] = { username: f.username ?? null, name: S.fullName(f) || null, chat_id: m.chat.id, updated: nowIso }; this.kvSet("private_users", pu);
      if (u.message) out.tasks.push(["pcmd", () => this.privateCmd(m)]);
      return out;
    }
    if (m.chat.type !== "group" && m.chat.type !== "supergroup") return out;
    const c = m.chat; const ts = m.date * 1000; const main = Number(c.id) === cfg.groupId;
    const prev = this.chats[String(c.id)] || {};
    const next = { ...prev, title: c.title ?? null, type: c.type, status: prev.status || "member", updated: nowIso };
    const stale = !prev.updated || prev.title !== next.title || prev.type !== next.type || Date.parse(prev.updated) < this.now() - 600000;
    this.chats[String(c.id)] = next; if (stale) this.kvSet("chats", this.chats); // throttle writes
    for (const nm of m.new_chat_members || []) this.ev("join", c, ts, nm, "service", m.message_id);
    if (m.left_chat_member) this.ev("leave", c, ts, m.left_chat_member, "service", m.message_id);
    if (m.new_chat_members || m.left_chat_member) return out;
    const rec = { type: "message", user_id: f.id ?? null, links: R.linksOf(m), chat_id: c.id, chat_title: c.title ?? null, message_id: m.message_id, ts: S.isoMyt(ts),
      sender: S.fullName(f) || null, username: f.username ?? null, text: m.text || m.caption || null, reply_to: (m.reply_to_message || {}).message_id ?? null, edited: !!u.edited_message };
    this.insertRaw(ts, rec);
    if (rec.edited) { out.step = "edited"; return out; }
    const isCmd = (m.text || "").startsWith("/") || !!S.parseCmd(m, cfg.me.username)[0];
    const old = this.now() - ts > cfg.maxAge;
    let st = { kind: "ignored" };
    try { st = this.stats.record(m); } catch (e) { log("stats_error", { msg: String(e && e.message).slice(0, 120) }); }
    out.step = "stats_" + st.kind;
    if (st.kind === "penalty") {
      this.handled.add(m.message_id);
      if (!old) out.tasks.push(["warn", () => this.warn(m, f, st.burst)]);
    }
    if (isCmd) { if (main) out.tasks.push(["gcmd", () => this.statsCmd(m)]); out.step += "_cmd"; return out; }
    if (main && S.isSpellSpam(m.text || m.caption)) { out.step += "_spam"; return out; }
    if (!main) return out;
    this.note(m);
    if (old) { out.step += "_old"; return out; }
    if (R.isReportRequest(m, cfg.me, cfg.owner)) { out.tasks.push(["share", () => this.shareReport(m)]); out.step += "_share"; return out; }
    if (R.isOwnerCall(m, cfg.me, cfg.owner)) { out.tasks.push(["owner", () => this.instantAck(m)]); out.step += "_ownercall"; return out; }
    if (R.isBotCall(m, cfg.me)) { out.tasks.push(["call", () => this.directReply(m)]); out.step += "_call"; return out; }
    if (this.enqueue(m)) { out.tasks.push(["pump", () => this.pump()]); out.step += "_queued"; }
    return out;
  }
  note(m) {
    const e = R.prefEntry(m, this.cfg.owner, this.nicksLower());
    if (e) { const k = String((m.from || {}).id); (this.prefs[k] = this.prefs[k] || []).push(e); this.prefs[k] = this.prefs[k].slice(-5); this.kvSet("name_prefs", this.prefs); }
    this.pushHistory(m.message_id, this.nameOf(m.from), R.textOf(m));
  }
  flushMeta() { if (this.metaDirty) { this.kvSet("meta", this.meta); this.metaDirty = false; } }

  // ---------- replies ----------
  async warn(m, f, burst) {
    const uid = f.id; const now = this.now();
    if (now - (this.warned.get(uid) || 0) < this.cfg.warnGap) return;
    this.warned.set(uid, now);
    const panggil = this.nameOf(f) || S.fullName(f) || "kak";
    const t = burst
      ? `amaran untuk ${panggil}: jangan spam chat satu-satu huruf berturut-turut macam ni untuk naikkan statistik. setiap mesej spam ditolak ${S.PENALTY} chat dan ${S.PENALTY} perkataan.`
      : `amaran untuk ${panggil}: jangan eja satu-satu huruf macam ni untuk naikkan statistik. mesej ni tak dikira dan markah ditolak ${S.PENALTY} chat dan ${S.PENALTY} perkataan.`;
    await this.api("sendMessage", { chat_id: this.cfg.groupId, text: t, reply_to_message_id: m.message_id, allow_sending_without_reply: true });
    this.logReply(m.message_id, true, "penalti", t);
  }
  async shareReport(m) {
    const r = await this.replyTo(m, R.REPORT_QUESTION);
    const reqs = this.kvGet("report_requests", []);
    reqs.push({ status: "asked", chat_id: m.chat.id, user: ((m.from || {}).username || "").toLowerCase(), user_id: (m.from || {}).id, request_message_id: m.message_id, request_text: m.text || "", question_message_id: (r || {}).message_id ?? null, asked_at: S.isoMyt(this.now(), true) });
    this.kvSet("report_requests", reqs.slice(-200));
    this.logReply(m.message_id, true, "report", R.REPORT_QUESTION);
  }
  async instantAck(m) {
    if (this.acked.has(m.message_id)) return; this.acked.add(m.message_id);
    const t = R.textOf(m); let g = null;
    try { g = await this.genReply(m, true, 10000); } catch (e) { log("gemini_call_err", { msg: String(e && e.message).slice(0, 80) }); }
    const line = g && g !== "SKIP" ? g : R.pickAck(t, this.lastAck);
    const r = await this.replyTo(m, line);
    this.lastAck = line; this.handled.add(m.message_id); this.pushHistory((r || {}).message_id ?? null, "KR", line);
    const acks = this.kvGet("instant_acks", []);
    acks.push({ message_id: m.message_id, text: t, ack_message_id: (r || {}).message_id ?? null, ack_text: line, kind: R.classify(t), ts: S.isoMyt(this.now(), true) });
    this.kvSet("instant_acks", acks.slice(-300));
    this.logReply(m.message_id, true, "ketua-call", line);
    log("reply", { kind: "ketua-call", mid: m.message_id, gemini: !!(g && g !== "SKIP") });
  }
  async directReply(m) {
    if (this.acked.has(m.message_id)) return;
    const uid = (m.from || {}).id; const now = this.now();
    if (now - (this.callLast.get(uid) || 0) < this.cfg.callCooldown) return;
    if (!this.settings.replies_on) return;
    this.acked.add(m.message_id); this.callLast.set(uid, now);
    let g = null;
    try { g = await this.genReply(m, true, 12000); } catch (e) { log("gemini_direct_err", { msg: String(e && e.message).slice(0, 80) }); }
    const line = g && g !== "SKIP" ? g : R.pickOtherAck(this.lastAck);
    const r = await this.replyTo(m, line);
    this.lastAck = line; this.handled.add(m.message_id); this.pushHistory((r || {}).message_id ?? null, "KR", line);
    this.logReply(m.message_id, true, "direct", line);
    log("reply", { kind: "direct", mid: m.message_id, gemini: !!(g && g !== "SKIP") });
  }
  enqueue(m) {
    const f = m.from || {};
    if (f.is_bot || !m.text) return false;
    if ((f.username || "").toLowerCase() === this.cfg.owner) return false;
    if (R.trivial(m.text)) return false;
    const rt = (m.reply_to_message || {}).from || {};
    const toBot = rt.id === this.cfg.me.id || R.TO_BOT_RE.test(m.text);
    this.q.push({ ts: this.now(), m: slim(m), toBot: !!toBot, text: m.text });
    return true;
  }
  perUser() { return Math.max(this.cfg.perUserFloor, Number(this.settings.per_user_min || 2) * 60000); }
  // One step of the original gemini_reply.worker loop. Returns {pick} or {wait: ms-timestamp} or null.
  choose(now) {
    const cfg = this.cfg;
    this.q = this.q.filter((x) => now - x.ts < cfg.maxAge && !this.handled.has(x.m.message_id));
    if (!this.q.length) return null;
    if (now - this.lastSend < cfg.gap) return { wait: this.lastSend + cfg.gap };
    this.sentTimes = this.sentTimes.filter((t) => now - t <= cfg.window);
    if (this.sentTimes.length >= cfg.maxWin) return { wait: this.sentTimes[0] + cfg.window + 1 };
    if (this.usageToday() >= cfg.dailyCap) return { wait: now + 60000 };
    if (!this.settings.replies_on) return { wait: now + 30000 };
    const per = this.perUser();
    const items = [...this.q].sort((a, b) => R.prio(b) - R.prio(a));
    let picked = null, soonest = Infinity;
    for (const it of items) {
      const last = this.userLast.get(it.m.from.id) || 0;
      if (now - last < per) { soonest = Math.min(soonest, last + per); continue; }
      picked = it; break;
    }
    if (!picked) return Number.isFinite(soonest) ? { wait: soonest } : null;
    if (now - this.lastCall < cfg.callGap) return { wait: this.lastCall + cfg.callGap };
    this.q = this.q.filter((x) => x !== picked);
    return { pick: picked };
  }
  async pump() {
    if (this.busy) return;
    this.busy = true;
    try {
      for (;;) {
        const now = this.now(); const c = this.choose(now);
        if (!c) return;
        if (c.wait) { await this.schedule(c.wait); return; }
        const m = c.pick.m; this.lastCall = now; this.handled.add(m.message_id); this.saveRt();
        const r = await this.genReply(m, false, 20000);
        if (r && r !== "SKIP") {
          const sent = await this.replyTo(m, r).catch((e) => { log("send_failed", { msg: String(e.message).slice(0, 80) }); this.logReply(m.message_id, false, "gemini", ""); return null; });
          if (sent) {
            this.lastSend = this.now(); this.sentTimes.push(this.lastSend); this.userLast.set(m.from.id, this.lastSend);
            this.pushHistory(sent.message_id ?? null, "KR", r); this.logReply(m.message_id, true, "gemini", r);
            log("reply", { kind: "gemini", mid: m.message_id, prio: R.prio(c.pick) });
          }
        } else {
          log("skip", { mid: m.message_id, why: r ? "gemini SKIP" : "gemini fail" });
          if (!this.userLast.has(m.from.id)) this.userLast.set(m.from.id, 0);
        }
      }
    } finally { this.busy = false; }
  }
  async schedule(at) {
    at = Math.max(at, this.now() + 1000);
    if (this.alarmAt && this.alarmAt <= at && this.alarmAt > this.now()) return;
    this.alarmAt = at; await this.state.storage.setAlarm(at);
  }
  async alarm() { this.alarmAt = null; await this.pump(); this.saveRt(); }

  // ---------- commands ----------
  async isAdmin(uid, un) {
    if (uid === this.cfg.ownerId || (un || "").toLowerCase() === this.cfg.owner) return true;
    if (this.now() - this.adm.ts > 600000) {
      try { const a = await this.api("getChatAdministrators", { chat_id: this.cfg.groupId }); this.adm = { ts: this.now(), ids: new Set(a.map((x) => x.user.id)) }; }
      catch (e) { log("admins_failed", { msg: String(e.message).slice(0, 80) }); }
    }
    return this.adm.ids.has(uid);
  }
  // chatstats.handle: true when it was a stat command
  async statsCmd(m) {
    const [cmd, args] = S.parseCmd(m, this.cfg.me.username);
    if (!cmd) return false;
    const f = m.from || {}; const uid = f.id; const un = (f.username || "").toLowerCase();
    const grp = m.chat.type !== "private";
    if (grp && Number(m.chat.id) !== this.cfg.groupId) return true;
    const nm = S.fullName(f);
    const reply = (t) => this.replyTo(m, t).then(() => this.logReply(m.message_id, true, "cmd", t));
    const per = (a) => S.PERIOD_ALIAS[a] || a;
    if (cmd === "stat" || cmd === "statistik") await reply(this.stats.ownText(uid, nm));
    else if (cmd === "stattop" || cmd === "ranking" || cmd === "pemenang") {
      if (!(await this.isAdmin(uid, un))) { await reply("yang tu admin je boleh tengok. guna /stat untuk statistik sendiri"); return true; }
      const a = per(args.length ? args[0].toLowerCase() : "hari");
      const name = cmd === "pemenang" ? "pemenang" : "stattop";
      if (!S.PER[a]) { await reply(`guna /${name} hari, /${name} minggu atau /${name} bulan`); return true; }
      await reply(cmd === "pemenang" ? this.stats.winnersText(a) : this.stats.topText(a));
    } else if (cmd === "resetstat") {
      if (un !== this.cfg.owner || uid !== this.cfg.ownerId) { await reply("maaf, ketua je boleh reset statistik"); return true; }
      if (args[0] === "ya") { this.stats.resetAll(); await reply("ok ketua, semua statistik dah reset"); }
      else await reply("betul nak reset semua statistik ketua? taip /resetstat ya");
    }
    return true;
  }
  isOwnerPm(m) { const f = m.from || {}; return m.chat.type === "private" && Number(m.chat.id) === this.cfg.ownerId && (f.username || "").toLowerCase() === this.cfg.owner; }
  async privateCmd(m) {
    if (await this.statsCmd(m)) return;
    await this.adminCmd(m);
  }
  statusText() {
    const s = this.settings; const today = this.todayMyt();
    const n = this.sql.exec("SELECT count(*) AS n FROM replies WHERE ok = 1 AND ts LIKE ?", today + "%").toArray()[0].n;
    const last = this.sql.exec("SELECT ts FROM replies WHERE ok = 1 ORDER BY rowid DESC LIMIT 1").toArray()[0];
    const up = Math.floor((this.now() - this.started) / 1000); const h = Math.floor(up / 3600), r = up % 3600;
    const lt = last ? last.ts.slice(0, 19).replace("T", " ").slice(0, 16) : "belum ada";
    return `bot hidup ketua\nuptime: ${h} jam ${Math.floor(r / 60)} minit\ngemini hari ni: ${this.usageToday()}\nbalasan group hari ni: ${n}\n` +
      `balas group: ${s.replies_on ? "ON" : "OFF"}\nhad orang sama: ${s.per_user_min} minit\nbalasan last: ${lt} (MYT)`;
  }
  async adminCmd(m) {
    const t = pyStrip(m.text || "");
    if (!t.startsWith("/") || !this.isOwnerPm(m)) return false;
    const parts = pySplit(t); const cmd = parts[0].slice(1).split("@")[0].toLowerCase(); const args = parts.slice(1);
    const s = this.settings; const save = () => this.kvSet("settings", { replies_on: s.replies_on, per_user_min: s.per_user_min });
    if (cmd === "help" || cmd === "start") await this.say(HELP);
    else if (cmd === "status") await this.say(this.statusText());
    else if (cmd === "off" || cmd === "on") {
      s.replies_on = cmd === "on"; save();
      await this.say(cmd === "off" ? "ok ketua, bot dah senyap dalam group. panggilan ketua still jawab" : "ok ketua, bot dah on balik dalam group");
    } else if (cmd === "had") {
      const n = /^[+-]?\d+$/.test((args[0] || "").trim()) ? parseInt(args[0], 10) : NaN;
      if (Number.isInteger(n) && n >= 1 && n <= 60) { s.per_user_min = n; save(); await this.say(`ok ketua, had orang sama sekarang ${n} minit`); }
      else await this.say(`guna macam ni ketua: /had 2 (1 sampai 60). sekarang ${s.per_user_min} minit`);
    } else if (cmd === "nama") {
      const nk = this.nicks;
      if (!args.length) await this.say(Object.keys(nk).length ? "nama panggilan sekarang:\n" + Object.entries(nk).map(([k, v]) => `@${k} = ${v}`).join("\n") : "belum ada nama panggilan lagi ketua");
      else if (args.length < 2) await this.say("guna macam ni ketua: /nama username panggilan");
      else { const u = args[0].replace(/^@+/, "").toLowerCase(); nk[u] = args.slice(1).join(" "); this.kvSet("nicknames", nk); await this.say(`ok ketua, @${u} lepas ni dipanggil ${nk[u]}`); }
    } else if (cmd === "buangnama") {
      const nk = this.nicks; const u = args.length ? args[0].replace(/^@+/, "").toLowerCase() : "";
      const ks = Object.keys(nk).filter((k) => k.toLowerCase() === u);
      if (ks.length) { for (const k of ks) delete nk[k]; this.kvSet("nicknames", nk); await this.say(`ok ketua, nama panggilan @${u} dah buang`); }
      else await this.say("takde pun username tu dalam senarai ketua");
    } else if (cmd === "report") {
      const a = args.length ? args[0].toLowerCase() : "semalam"; let d;
      if (a === "semalam") d = S.ymd(S.myt(this.now() - 86400000));
      else if (["hari", "harini", "today"].includes(a)) d = this.todayMyt();
      else if (/^\d{4}-\d{2}-\d{2}$/.test(a) && !isNaN(Date.parse(a + "T00:00:00Z")) && new Date(a + "T00:00:00Z").toISOString().startsWith(a)) d = a;
      else { await this.say("tarikh tak betul ketua, guna /report semalam atau /report 2026-11-03"); return true; }
      const r = this.sql.exec("SELECT text FROM reports WHERE day = ?", d).toArray()[0];
      if (r && pyStrip(r.text)) await this.say(r.text); else await this.say(`laporan ${d} takde lagi ketua`);
    } else if (cmd === "restart") {
      await this.say("ok ketua, restart jap");
      this.flushMeta(); this.load(); // reload state from storage (same effect as a process restart)
    } else await this.say("arahan tu tak kenal la ketua. taip /help");
    return true;
  }
  async setCommands() {
    const cmds = [["status", "tengok status bot"], ["off", "stop balas group"], ["on", "sambung balas group"], ["had", "had orang sama (minit)"], ["nama", "nama panggilan"], ["buangnama", "buang nama panggilan"], ["report", "hantar laporan"], ["restart", "restart bot"], ["help", "senarai arahan"]];
    const G = this.cfg.groupId; const st = [["stat", "statistik chat saya"]];
    const adm = [...st, ["stattop", "top 30 chat (hari|minggu|bulan)"], ["pemenang", "senarai pemenang (hari|minggu|bulan)"]];
    const mk = (l) => l.map(([command, description]) => ({ command, description }));
    const res = [];
    for (const [sc, cm] of [[{ type: "default" }, st], [{ type: "all_group_chats" }, st], [{ type: "chat", chat_id: G }, st], [{ type: "chat_administrators", chat_id: G }, adm], [{ type: "all_private_chats" }, st],
      [{ type: "chat", chat_id: this.cfg.ownerId }, [...cmds, ...adm, ["resetstat", "reset semua statistik"]]]]) {
      try { await this.api("setMyCommands", { commands: mk(cm), scope: sc }); res.push(sc.type + ":ok"); } catch (e) { res.push(sc.type + ":" + String(e.message).slice(0, 40)); }
    }
    return res;
  }

  // ---------- HTTP entry ----------
  async fetch(request) {
    const url = new URL(request.url); const p = url.pathname;
    try {
      if (p === "/update") {
        let u; try { u = JSON.parse(await request.text()); } catch { return new Response("bad json"); }
        const out = this.ingest(u);
        this.flushMeta(); this.saveRt();
        for (const [name, fn] of out.tasks) this.bg(name, fn);
        log("upd", { u: u.update_id, step: out.step, tasks: out.tasks.map((x) => x[0]).join(",") });
        return new Response(out.step);
      }
      if (p === "/cron") { this.stats.finalize(); this.stats.prune(); await this.pump(); this.saveRt(); return new Response("finalized"); }
      if (p.startsWith("/admin/")) return await this.admin(p.slice(7), request, url);
      return new Response("not found", { status: 404 });
    } catch (e) {
      log("do_error", { p, err: e && e.name, msg: String(e && e.message).slice(0, 200) });
      return new Response("error", { status: 500 });
    }
  }
  async admin(cmd, request, url) {
    const body = request.method === "POST" ? JSON.parse((await request.text()) || "{}") : {};
    const J = (x) => Response.json(x);
    if (cmd === "import/stats") {
      // Replace stats with a Python stats.json (+ winners.json), keeping dict order.
      const s = body.stats; this.sql.exec("DELETE FROM stats"); this.sql.exec("DELETE FROM names");
      let ord = 0;
      // periods arrive as [[period, [[uid, [c, w]], ...]], ...] so numeric uids keep Python dict order
      for (const [per, users] of s.periods) for (const [uid, v] of users) this.sql.exec("INSERT INTO stats (period, uid, c, w, ord) VALUES (?, ?, ?, ?, ?)", per, uid, v[0], v[1], ++ord);
      for (const [uid, nm] of Object.entries(s.names)) this.sql.exec("INSERT INTO names (uid, name) VALUES (?, ?)", uid, nm);
      if (body.penalties) { this.sql.exec("DELETE FROM penalties"); for (const [uid, l] of Object.entries(s.penalties || {})) for (const x of l) this.sql.exec("INSERT INTO penalties (uid, mid, ts, note) VALUES (?, ?, ?, ?)", uid, x[0], x[1], x[2] ?? null); }
      const meta = this.kvGet("meta", { lastMid: 0, lastUpdateId: 0 });
      meta.lastMid = Number(Object.values(s.last_mid || {})[0] || 0);
      if (body.lastUpdateId != null) meta.lastUpdateId = Number(body.lastUpdateId);
      this.kvSet("meta", meta);
      if (body.winners) this.kvSet("winners", body.winners);
      this.kvSet("period_keys", s.periods.map(([per]) => per));
      this.load();
      return J({ ok: true, rows: ord, meta: this.meta });
    }
    if (cmd === "import/kv") { for (const [k, v] of Object.entries(body)) this.kvSet(k, v); this.load(); return J({ ok: true, keys: Object.keys(body) }); }
    if (cmd === "import/raw") {
      // body: {day, start, lines: [jsonl line strings]}; idempotent by (day, seq)
      let n = 0;
      body.lines.forEach((l, i) => { this.sql.exec("INSERT OR REPLACE INTO raw (day, seq, rec) VALUES (?, ?, ?)", body.day, body.start + i, l); n++; });
      this.daySeq.delete(body.day);
      return J({ ok: true, n });
    }
    if (cmd === "raw/truncate") { // drop test rows: day, seq >= from
      this.sql.exec("DELETE FROM raw WHERE day = ? AND seq >= ?", body.day, body.from); this.daySeq.delete(body.day);
      return J({ ok: true });
    }
    if (cmd === "reset-test") { // forget test traffic (replies log, reply state, dry-run log)
      this.sql.exec("DELETE FROM replies"); this.sql.exec("DELETE FROM kv WHERE k IN ('rt', 'drylog') OR k LIKE 'yc:%'");
      this.load(); return J({ ok: true });
    }
    if (cmd === "import/report") { this.sql.exec("INSERT OR REPLACE INTO reports (day, text) VALUES (?, ?)", body.day, body.text); return J({ ok: true }); }
    if (cmd === "import/file") { this.sql.exec("INSERT OR REPLACE INTO files (path, content) VALUES (?, ?)", body.path, body.content); return J({ ok: true }); }
    if (cmd === "rawcount") return J(Object.fromEntries(this.sql.exec("SELECT day, count(*) AS n FROM raw GROUP BY day").toArray().map((r) => [r.day, r.n])));
    if (cmd === "stats") return J({ stats: this.stats.toPython(), winners: this.stats.winners, meta: this.meta, penalties: this.sql.exec("SELECT count(*) AS n FROM penalties").toArray()[0].n });
    if (cmd === "eval") {
      // Render command texts at a fixed time (for parity checks against the Python bot).
      this.fakeNow = body.now != null ? body.now : null;
      try {
        return J(body.items.map((it) => it.kind === "own" ? this.stats.ownText(it.uid, it.nm) : it.kind === "top" ? this.stats.topText(it.key) : this.stats.winnersText(it.key)));
      } finally { this.fakeNow = null; }
    }
    if (cmd === "dry-log") { const l = this.kvGet("drylog", []); if (url.searchParams.get("clear")) { this.dryLog = []; this.kvSet("drylog", []); } return J(l); }
    if (cmd === "debug") return J({ meta: this.meta, q: this.q.length, history: this.history.length, usage: this.usageToday(), started: this.started, lastSend: this.lastSend, alarmAt: this.alarmAt, settings: this.settings, chats: Object.keys(this.chats).length, files: this.sql.exec("SELECT count(*) AS n FROM files").toArray()[0].n, reports: this.sql.exec("SELECT day FROM reports").toArray().map((r) => r.day), dbSize: this.sql.databaseSize });
    if (cmd === "replies") return J(this.sql.exec("SELECT ts, mid, ok, kind, substr(text, 1, 80) AS t FROM replies ORDER BY rowid DESC LIMIT ?", Number(url.searchParams.get("n") || 20)).toArray());
    if (cmd === "setcommands") return J(await this.setCommands());
    if (cmd === "meta") { if (body.lastUpdateId != null) { this.meta.lastUpdateId = Number(body.lastUpdateId); this.metaDirty = true; this.flushMeta(); } return J(this.meta); }
    if (cmd === "reload") { this.load(); return J({ ok: true }); }
    return new Response("unknown admin", { status: 404 });
  }
}
