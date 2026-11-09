// Chat statistics: exact port of chatstats.py (record, penalties, rankings,
// /stat, /stattop, /pemenang texts, winners finalisation, pruning).
// State lives in memory (Maps keep Python dict insertion order for tie-breaks);
// every mutation is reported to `persist` so the Durable Object can write it to SQLite.
import { pySplit, pyStrip, cpLen } from "./pyre.js";

export const PENALTY = 10;
export const PER = { hari: ["d", "hari ini"], minggu: ["w", "minggu ini"], bulan: ["m", "bulan ini"] };
export const HARI = ["Isnin", "Selasa", "Rabu", "Khamis", "Jumaat", "Sabtu", "Ahad"];
export const BLN = ["Jan", "Feb", "Mac", "Apr", "Mei", "Jun", "Jul", "Ogos", "Sep", "Okt", "Nov", "Dis"];
const TZ_MS = 8 * 3600000; // Asia/Kuala_Lumpur, fixed UTC+8

// ---- dates in Malaysia time ----
export function myt(ms) {
  const d = new Date(ms + TZ_MS);
  return { y: d.getUTCFullYear(), mo: d.getUTCMonth() + 1, d: d.getUTCDate(), wd: (d.getUTCDay() + 6) % 7, h: d.getUTCHours(), mi: d.getUTCMinutes(), s: d.getUTCSeconds() };
}
const p2 = (n) => String(n).padStart(2, "0");
export const ymd = (t) => `${t.y}-${p2(t.mo)}-${p2(t.d)}`;
export function isoWeek(y, mo, d) {
  const dt = new Date(Date.UTC(y, mo - 1, d));
  const wd = (dt.getUTCDay() + 6) % 7; // Mon=0
  dt.setUTCDate(dt.getUTCDate() - wd + 3); // Thursday of this week
  const wy = dt.getUTCFullYear();
  const w = Math.ceil(((dt - Date.UTC(wy, 0, 1)) / 86400000 + 1) / 7);
  return [wy, w];
}
// Monday (UTC date parts) of ISO week
export function isoMonday(wy, w) {
  const jan4 = new Date(Date.UTC(wy, 0, 4));
  const mon = new Date(jan4.getTime() - ((jan4.getUTCDay() + 6) % 7) * 86400000 + (w - 1) * 7 * 86400000);
  return mon;
}
export function pid(kind, ms) {
  const t = myt(ms);
  if (kind === "d") return "d:" + ymd(t);
  if (kind === "w") { const [y, w] = isoWeek(t.y, t.mo, t.d); return `w:${y}-W${p2(w)}`; }
  return `m:${t.y}-${p2(t.mo)}`;
}
// Python-style isoformat of a MYT time, e.g. 2026-11-09T09:18:24+08:00
export function isoMyt(ms, micro = false) {
  const t = myt(ms);
  let s = `${ymd(t)}T${p2(t.h)}:${p2(t.mi)}:${p2(t.s)}`;
  if (micro) { const us = ((ms % 1000) + 1000) % 1000; if (us) s += "." + String(us * 1000).padStart(6, "0"); }
  return s + "+08:00";
}
export function label(k) {
  const [t, v] = [k.slice(0, 1), k.slice(2)];
  if (t === "d") { const [y, mo, d] = v.split("-").map(Number); const wd = (new Date(Date.UTC(y, mo - 1, d)).getUTCDay() + 6) % 7; return `${d} ${BLN[mo - 1]} (${HARI[wd]})`; }
  if (t === "w") {
    const [wy, w] = v.split("-W").map(Number); const mon = isoMonday(wy, w); const sun = new Date(mon.getTime() + 6 * 86400000);
    return `${mon.getUTCDate()} ${BLN[mon.getUTCMonth()]} - ${sun.getUTCDate()} ${BLN[sun.getUTCMonth()]}`;
  }
  const [y, mo] = v.split("-"); return `${BLN[Number(mo) - 1]} ${y}`;
}

// ---- text rules (chatstats.skip_text / is_spell_spam / is_burst) ----
const FILL = new Set("tq tqvm tqsm tqs tqqq ty tyvm thanks thank thx tenkiu tenkyu tengkiu terima kasih makasih trima ksih ready redy rdy reddy readyy done siap dah da sudah kak kakak sis bro brader ketua ya ye yer k ok okay oke okey semua all you u much so very sangat ketue geng".split(" "));
const CORE = /^(tq+|tq(vm|sm|s)|ty(vm)?|thanks?|thx|ten?g?k(i|y)u|terima|makasih|trima|ready+|redd?y|rdy|done|siap)$/;
const NON_ALNUM = /[^\p{L}\p{N}]/gu; // Python [\W_]
export const alnumLen = (text) => cpLen(String(text).replace(NON_ALNUM, ""));
export function skipText(text) {
  if (text && alnumLen(text) <= 1 && cpLen(pyStrip(text)) <= 3) return true;
  if (!text || !pyStrip(text)) return true;
  const toks = text.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
  if (!toks.length) return true;
  return toks.every((x) => FILL.has(x) || CORE.test(x)) && toks.some((x) => CORE.test(x));
}
export function isSpellSpam(text) {
  if (!text) return false;
  const toks = pySplit(text);
  if (toks.length < 4) return false;
  const single = toks.filter((t) => cpLen(t) === 1 && /^\p{L}$/u.test(t));
  const small = toks.filter((t) => cpLen(t) === 1);
  return single.length >= 4 && small.length / toks.length >= 0.7;
}
export const wordCount = (text) => pySplit(text).length;
export const fullName = (f) => [f && f.first_name, f && f.last_name].filter(Boolean).join(" ");

export class Stats {
  // periods: Map<period, Map<uid, [chats, words]>>, names: Map<uid,name>
  // keys: ordered list of all period ids; loader(period) lazily returns Map<uid,[c,w]> for a
  // period that is not in memory (the Durable Object loads only what it needs).
  constructor({ periods, keys, loader, names, lastMid, winners, groupId, persist, nowFn }) {
    this.periods = periods || new Map();
    this.keys = keys ? [...keys] : [...this.periods.keys()];
    this.loader = loader || null;
    this.names = names || new Map();
    this.lastMid = lastMid || 0;
    this.winners = winners || {};
    this.groupId = Number(groupId);
    this.persist = persist || {};
    this.now = nowFn || (() => Date.now());
    this.tiny = new Map(); this.burstPen = new Map(); // is_burst memory
  }
  getPeriod(period) {
    let p = this.periods.get(period);
    if (!p && this.loader && this.keys.includes(period)) { p = this.loader(period); this.periods.set(period, p); }
    return p || null;
  }
  _cell(period, uid) {
    let p = this.getPeriod(period);
    if (!p) { p = new Map(); this.periods.set(period, p); if (!this.keys.includes(period)) { this.keys.push(period); this.persist.keys && this.persist.keys(this.keys); } }
    let c = p.get(uid);
    if (!c) { c = [0, 0]; p.set(uid, c); }
    return c;
  }
  _setName(uid, nm) {
    if (nm && this.names.get(uid) !== nm) { this.names.set(uid, nm); this.persist.name && this.persist.name(uid, nm); }
  }
  _setLastMid(mid) { this.lastMid = mid; this.persist.lastMid && this.persist.lastMid(mid); }
  isBurst(uid, text, ts) {
    if (!text || alnumLen(text) > 1 || cpLen(pyStrip(text)) > 3) return false;
    const q = (this.tiny.get(uid) || []).filter((t) => ts - t < 120); q.push(ts); this.tiny.set(uid, q);
    if (q.length < 3) return false;
    const last = this.burstPen.has(uid) ? this.burstPen.get(uid) : -1e12;
    if (ts - last >= 120) { this.burstPen.set(uid, ts); return q.length; }
    return 1;
  }
  _add(uid, nm, mid, dtMs, text) {
    if (mid != null && mid <= this.lastMid) return false;
    if (skipText(text)) { if (mid != null) this._setLastMid(mid); return false; }
    const w = wordCount(text);
    for (const k of ["d", "w", "m"]) {
      const per = pid(k, dtMs); const c = this._cell(per, uid); c[0] += 1; c[1] += w;
      this.persist.cell && this.persist.cell(per, uid, c);
    }
    this._setName(uid, nm);
    if (mid != null) this._setLastMid(mid);
    return true;
  }
  // Returns {kind: "counted"|"skipped"|"penalty"|"ignored", burst, n}
  record(m) {
    const f = m.from || {};
    if (Number(m.chat.id) !== this.groupId || f.is_bot || !f.id || m.edit_date) return { kind: "ignored" };
    const uid = String(f.id); const nm = fullName(f);
    const dtMs = m.date * 1000; const txt = m.text || m.caption || null;
    const burst = this.isBurst(uid, txt, m.date);
    if (isSpellSpam(txt) || burst) {
      if (m.message_id > this.lastMid) {
        this._setLastMid(m.message_id);
        const n = burst || 1;
        for (const k of ["d", "w", "m"]) {
          const per = pid(k, dtMs); const c = this._cell(per, uid);
          c[0] = Math.max(0, c[0] - PENALTY * n); c[1] = Math.max(0, c[1] - PENALTY * n);
          this.persist.cell && this.persist.cell(per, uid, c);
        }
        this._setName(uid, nm);
        this.persist.penalty && this.persist.penalty(uid, m.message_id, isoMyt(dtMs));
        return { kind: "penalty", burst: Boolean(burst), n };
      }
      return { kind: "penalty_dup" };
    }
    if (this._add(uid, nm, m.message_id, dtMs, txt)) { this.finalize(); this.prune(); return { kind: "counted" }; }
    return { kind: "skipped" };
  }
  prune() {
    const cut = this.now() - 95 * 86400000;
    for (const k of [...this.keys]) {
      const t = k.slice(0, 1), v = k.slice(2); let ms;
      try {
        if (t === "d") { const [y, mo, d] = v.split("-").map(Number); ms = Date.UTC(y, mo - 1, d) - 8 * 3600000; }
        else if (t === "w") { const [wy, w] = v.split("-W").map(Number); ms = isoMonday(wy, w).getTime() - 8 * 3600000; }
        else { const [y, mo] = v.split("-").map(Number); ms = Date.UTC(y, mo - 1, 28) - 8 * 3600000; }
        if (Number.isFinite(ms) && ms < cut) {
          this.periods.delete(k); this.keys = this.keys.filter((x) => x !== k);
          this.persist.dropPeriod && this.persist.dropPeriod(k); this.persist.keys && this.persist.keys(this.keys);
        }
      } catch {}
    }
  }
  ranking(kind) {
    const p = this.getPeriod(pid(kind, this.now())) || new Map();
    return [...p.entries()].map(([u, v]) => [u, [v[0], v[1]]]).sort((a, b) => (b[1][0] - a[1][0]) || (b[1][1] - a[1][1]));
  }
  name(uid) { return this.names.get(String(uid)) || "tanpa nama"; }
  ownText(uid, nm) {
    const out = [`statistik ${nm || this.name(uid)}`];
    for (const [, [k, lbl]] of Object.entries(PER)) {
      const r = this.ranking(k); const i = r.findIndex(([u]) => u === String(uid));
      if (i >= 0) { const [c, w] = r[i][1]; out.push(`${lbl}: ${c} chat, ${w} perkataan, rank ${i + 1}/${r.length}`); }
      else out.push(`${lbl}: 0 chat, 0 perkataan, belum ada rank (${r.length} orang aktif)`);
    }
    return out.join("\n");
  }
  topText(key) {
    const [k, lbl] = PER[key]; const r = this.ranking(k);
    if (!r.length) return `belum ada chat ${lbl} lagi`;
    const tc = r.reduce((a, [, v]) => a + v[0], 0), tw = r.reduce((a, [, v]) => a + v[1], 0);
    const lines = [`top 30 ${lbl} (${pid(k, this.now()).slice(2)})`, `jumlah group: ${tc} chat, ${tw} perkataan, ${r.length} orang aktif`, ""];
    r.slice(0, 30).forEach(([u, v], i) => lines.push(`${i + 1}. ${this.name(u)} - ${v[0]} chat, ${v[1]} perkataan`));
    return lines.join("\n");
  }
  finalize() {
    const cur = new Set(["d", "w", "m"].map((k) => pid(k, this.now()))); let ch = false;
    for (const k of this.keys) {
      if (cur.has(k) || k in this.winners) continue;
      const p = this.getPeriod(k) || new Map();
      const r = [...p.entries()].sort((a, b) => (b[1][1] - a[1][1]) || (b[1][0] - a[1][0])).slice(0, 3);
      this.winners[k] = r.map(([u, v]) => ({ uid: Number(u), name: this.names.get(u) || "tanpa nama", chats: v[0], words: v[1] }));
      ch = true;
    }
    if (ch && this.persist.winners) this.persist.winners(this.winners);
    return ch;
  }
  winnersText(key) {
    const [k, lbl] = PER[key]; const n = { d: 7, w: 4, m: 3 }[k]; const nama = { d: "harian", w: "mingguan", m: "bulanan" }[k];
    const ks = Object.keys(this.winners).filter((x) => x.startsWith(k + ":")).sort().slice(-n).reverse();
    const out = [`pemenang ${nama}:`];
    for (const x of ks) {
      const es = this.winners[x];
      out.push("\n" + `${label(x)}: \n` + es.map((e, i) => `${i + 1}. ${e.name} : ${e.words} perkataan` + (i + 1 < es.length ? "," : "")).join("\n"));
    }
    if (!ks.length) {
      const hint = { d: "hari ni baru habis tengah malam", w: "minggu ni baru habis Ahad malam", m: "bulan ni baru habis hujung bulan" }[k];
      out.push(`belum ada pemenang ${key} lagi, ${hint}`);
    }
    const r = this.ranking(k).sort((a, b) => (b[1][1] - a[1][1]) || (b[1][0] - a[1][0]));
    if (r.length) out.push(`\nsetakat ni ${lbl}: ${this.name(r[0][0])} ${r[0][1][1]} perkataan`);
    return out.join("\n");
  }
  resetAll() { this.periods = new Map(); this.keys = []; this.persist.resetPeriods && this.persist.resetPeriods(); this.persist.keys && this.persist.keys([]); }
  // Python stats.json shape (for verification / backups)
  toPython() {
    const periods = {};
    for (const k of this.keys) { const p = this.getPeriod(k) || new Map(); periods[k] = {}; for (const [u, v] of p) periods[k][u] = [v[0], v[1]]; }
    return { periods, names: Object.fromEntries(this.names), last_mid: { [String(this.groupId)]: this.lastMid } };
  }
}

// ---- command parsing (chatstats.parse) ----
const ALIAS = { stats: "stat", statistic: "stat", statistics: "stat", mystat: "stat", top: "stattop", statstop: "stattop", rank: "stattop", resetstats: "resetstat", winner: "pemenang", winners: "pemenang", juara: "pemenang" };
const CMDS = new Set(["stat", "statistik", "stattop", "ranking", "resetstat", "pemenang"]);
export function parseCmd(m, botUsername = "kakireportbot") {
  const t = pyStrip(m.text || "");
  if (!t || !"/*".includes(t[0])) return [null, []];
  const parts = pySplit(t); let c = [...parts[0]].slice(1).join("").toLowerCase();
  if (c.includes("@")) { const i = c.indexOf("@"); const bot = c.slice(i + 1); c = c.slice(0, i); if (bot !== botUsername.toLowerCase()) return [null, []]; }
  c = ALIAS[c] || c;
  return [CMDS.has(c) ? c : null, parts.slice(1)];
}
export const PERIOD_ALIAS = { harian: "hari", harini: "hari", today: "hari", mingguan: "minggu", week: "minggu", bulanan: "bulan", month: "bulan" };
