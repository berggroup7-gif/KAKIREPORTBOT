// Reply logic ported from gemini_reply.py and logger.py (persona, filters,
// prompt building, call detection, report-share request). Pure functions only.
import { pyRe, pyStrip, cpLen, cpSlice } from "./pyre.js";
import { STYLE_TEMPLATE, ACKS, OTHER_ACKS } from "./persona.js";

export const MODELS = ["gemini-3.5-flash", "gemini-flash-latest", "gemini-flash-lite-latest"];
export const HISTORY_MAX = 40;
export const EMOJI = /[\u{1F000}-\u{1FFFF}\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D\u20E3]/gu;
export const stripEmoji = (s) => pyStrip(String(s).replace(EMOJI, "").replace(/[ \t]{2,}/g, " "));

const SKIP_RE = pyRe(String.raw`^\W*(tq+\d*|tqsm|alhamdulillah|subhanallah|masya?allah|insya?allah|salam|assalamualaikum|tqvm|thanks?|thank\s*you|terima\s*kasih|tenkiu|tengkiu|ready|rdy|waalaikum\w*|w\.?salam|aamii?n|amin|ok+|okay|okey|baik|noted|done|(he|ha|hi|hu)+|wk+)(\s+\w+){0,2}\W*$`, "i");
const HAS_W = pyRe(String.raw`\w`);
export function trivial(t) {
  t = pyStrip(t || "");
  if (!t || !HAS_W.test(t.replace(EMOJI, ""))) return true;
  return SKIP_RE.test(t);
}
export const textOf = (m) => (m && (m.text || m.caption)) || "";

export const style = (owner) => STYLE_TEMPLATE.split("__OWNER__").join(owner);

export const LINK_RE = pyRe("ngantuk|mengantuk|penat|letih|tido|tidur|bangun|lambat|semalam|smlm|malam tadi|mata|lewat|qada|kurang tidur|sakit kepala|pening|lapar|kenyang|menang|kalah|rindu", "i");
export const PREF_RE = pyRe(String.raw`(jangan|jgn|xnak|tak nak|taknak|tak suka|x suka|tolong jangan)\s*(la\s*)?(panggil|pnggl|pgl|sebut|gelar)|panggil\s*(aku|saya|sy|ak)\s+\S+\s*(je|ja|sahaja|aje|jela)|(nama|panggilan)\s*(aku|saya)\s*(bukan|adalah|ialah)`, "i");
const BAD = pyRe(String.raw`basah|seks|sex|lucah|bogel|ranjang|ghairah|\bpuki|\bbabi|bodoh|sial|celaka|\b(?:ai|bot)\b.*\b(?:saya|aku)\b`, "i");

export function nameOf(f, owner, nicks) {
  f = f || {};
  const u = (f.username || "").toLowerCase();
  if (u === owner) return "ketua";
  const nk = nicks[u];
  if (nk) return nk;
  return [f.first_name, f.last_name].filter(Boolean).join(" ") || f.username || "?";
}

// gemini_reply.yesterday_ctx; rows = raw records of yesterday (MYT) in file order
export function yesterdayCtx(uid, day, rows) {
  const mine = rows.filter((r) => r.user_id === uid && (r.type || "message") === "message");
  if (!mine.length) return "";
  const links = mine.reduce((a, r) => a + (r.links || []).length, 0);
  const t0 = mine[0].ts.slice(11, 16), t1 = mine[mine.length - 1].ts.slice(11, 16);
  let txt = mine.filter((r) => r.text && cpLen(pyStrip(stripEmoji(r.text))) >= 10).map((r) => `${r.ts.slice(11, 16)} ${cpSlice(r.text, 0, 120)}`);
  if (txt.length > 15) txt = Array.from({ length: 15 }, (_, i) => txt[Math.trunc((i * (txt.length - 1)) / 14)]);
  return `\n\nApa orang ni buat SEMALAM (${day}) dalam group: ${mine.length} mesej, chat dari ${t0} sampai ${t1}, kongsi ${links} link MP. ` +
    "Antara mesej dia semalam:\n" + txt.join("\n");
}
export function prefCtx(prefs, uid) {
  const p = prefs[String(uid)];
  return p && p.length ? "\nPERMINTAAN NAMA daripada orang ni sebelum ni (WAJIB ikut, jangan panggil nama yang dia tak suka): " + p.join(" | ") : "";
}
// gemini_reply.build_prompt; yctx is the yesterday string (only computed when LINK_RE matches)
export function buildPrompt(m, nm, isCall, { owner, history, nicks, prefs, yctx }) {
  const t = textOf(m);
  const ctx = cpSlice(history.slice(-40).filter(([i]) => i !== m.message_id).map(([, n, x]) => `${n}: ${x}`).join("\n"), -6000);
  const rt = m.reply_to_message;
  const rtx = rt ? `\nMesej ini membalas ${nameOf(rt.from || {}, owner, nicks)}: ${cpSlice(rt.text || rt.caption || "", 0, 300)}` : "";
  const isOwner = ((m.from || {}).username || "").toLowerCase() === owner;
  let task;
  if (isCall && isOwner) task = "Ketua sedang memanggil/bercakap terus dengan kau. Jawab dia sekarang (wajib balas, jangan SKIP).";
  else if (isCall) task = "Orang ni sedang bercakap terus dengan kau (tag, reply atau panggil nama kau). Jawab dia sekarang, panggil dia dengan nama panggilan yang betul (wajib balas, jangan SKIP).";
  else task = "Putuskan sama ada mesej ini patut dibalas. Balas kalau ia soalan, lawak, sapaan yang ada isi, kongsi link MP, atau ditujukan pada Wan Tiger. " +
    "Kalau tak perlu (sembang biasa antara orang lain, tiada apa nak ditambah), jawab tepat SKIP.";
  return `Sembang terkini dalam group:\n${ctx}\n\n${LINK_RE.test(t) ? yctx || "" : ""}\n\nMesej sasaran daripada ${nm}${isOwner ? " (ketua)" : ""}: ${t}${rtx}${prefCtx(prefs, (m.from || {}).id)}\n\n${task}\nTulis jawapan kau sahaja.`;
}
export function geminiBody(prompt, model, owner) {
  return {
    systemInstruction: { parts: [{ text: style(owner) }] },
    contents: [{ role: "user", parts: [{ text: prompt }] }],
    generationConfig: model === MODELS[0]
      ? { temperature: 1.0, maxOutputTokens: 200, thinkingConfig: { thinkingBudget: 0 } }
      : { temperature: 1.0, maxOutputTokens: 400 },
  };
}
// gemini_reply.gen_reply post-processing
export function postProcess(out) {
  if (!out) return null;
  out = pyStrip(out).replace(/^"+|"+$/g, "");
  out = stripEmoji(out);
  out = out.replace(/^(KR|Kaki Report|Wan Tiger)\s*:\s*/i, "");
  if (!out || /^[^\p{L}\p{N}_]*SKIP[^\p{L}\p{N}_]*$/iu.test(out) || out.toUpperCase().startsWith("SKIP")) return "SKIP";
  if (BAD.test(out)) return "SKIP";
  out = out.replace(pyRe(String.raw`\baku\b`, "g"), "saya").replace(pyRe(String.raw`\bAku\b`, "g"), "Saya");
  return cpSlice(out, 0, 500);
}

// ---- record_pref (returns new prefs entry or null) ----
export function prefEntry(m, owner, nicks) {
  const t = pyStrip(textOf(m));
  if (!t || !PREF_RE.test(t)) return null;
  const rt = m.reply_to_message;
  const extra = rt ? ` (membalas ${nameOf(rt.from || {}, owner, nicks)}: ${cpSlice(rt.text || "", 0, 120)})` : "";
  return cpSlice(t, 0, 200) + extra;
}

// ---- call detection (logger.py) ----
const NAME_P = String.raw`(?:wan\s*tiger|wantiger|kaki\s*report|kakireport|kr)`;
const VOC_START = pyRe(String.raw`^\W*(?:(?:hai|hoi|oi|weh|woi|wei|eh|hello|helo|hey)\s+)?` + NAME_P + String.raw`\b(?!\s*(?:tu|ni|tadi|dia)\b)`, "i");
const VOC_END = pyRe(String.raw`(?:^|\b(?:hai|hoi|oi|weh|woi|wei|eh|hello|helo|hey|mana|mari|sini|jom|tolong|ya|ye)\s+)` + NAME_P + String.raw`\W*$`, "i");
const THIRD = pyRe(String.raw`\b(dia|bot\s+tu|bot\s+ni)\b|` + NAME_P + String.raw`\s+(?:tu|ni|tadi)\b`, "i");
export function nameCall(t, isReplyToOther) {
  t = pyStrip(t);
  if (!t || isReplyToOther) return false;
  if (THIRD.test(t)) return false;
  return VOC_START.test(t) || VOC_END.test(t);
}
function tagOrReply(m, me) {
  const t = textOf(m);
  const rt = (m.reply_to_message || {}).from || {};
  return t.toLowerCase().includes("@" + me.username.toLowerCase()) || rt.id === me.id;
}
export function isOwnerCall(m, me, owner) {
  const f = m.from || {};
  if ((f.username || "").toLowerCase() !== owner) return false;
  if (tagOrReply(m, me)) return true;
  return nameCall(textOf(m), Boolean(m.reply_to_message));
}
export function isBotCall(m, me) {
  const f = m.from || {};
  if (f.is_bot) return false;
  if (tagOrReply(m, me)) return true;
  return nameCall(textOf(m), Boolean(m.reply_to_message));
}
const Q_RE = pyRe(String.raw`\?|\b(apa|kenapa|napa|mana|bila|siapa|macam\s*mana|camne|mcm\s*mane|ke)\b`, "i");
const TEASE_RE = pyRe(String.raw`\b(tido|tidur|senyap|idle|gaji\s*buta|lambat|mana|sidai|lena|hilang)\b`, "i");
export const classify = (t) => (TEASE_RE.test(t) ? "tease" : Q_RE.test(t) ? "question" : "general");
const pick = (arr) => arr[Math.floor(Math.random() * arr.length)];
export const pickAck = (t, last) => pick(ACKS[classify(t)].filter((x) => x !== last));
export const pickOtherAck = (last) => pick(OTHER_ACKS.filter((x) => x !== last));

// queue priority (gemini_reply.prio)
export const TO_BOT_RE = /wan\s*tiger|wantiger|kaki\s*report|\bkr\b|@kakireportbot/i;
export const prio = (it) => (it.toBot ? 3 : 0) + (it.text.includes("?") ? 2 : 0) + (it.text.includes("links.tngdigital.com.my") ? 1 : 0);

// ---- report share request (logger.maybe_share_report) ----
const REQ_RE = pyRe(String.raw`\b(bagi|bg|hantar|send|share|kongsi|kongsikan|minta|mintak|nak|nk|mana|tunjuk|tunjukkan|post|give|keluarkan|berikan|beri)\b[\w\s]{0,25}?\b(report|laporan|lapuran)\b|\b(report|laporan|lapuran)\b\s*(semalam|terkini|harian|hari\s*ni|tadi|please|pls|plz)?\s*[?!.]*$`, "i");
export const REPORT_QUESTION = "Baik, sebelum saya hantar, boleh beritahu dulu:\n" +
  "1. Report yang mana satu? (contoh: report harian kumpulan, statistik MP, ahli baru)\n" +
  "2. Untuk bila? (contoh: semalam, hari ni, 1 Nov)\n" +
  "3. Nak yang macam mana? (penuh, statistik sahaja, atau ringkasan pendek)\n" +
  "4. Nak saya hantar dalam group ni atau di PM?\n" +
  "Reply mesej ni dengan jawapan, nanti saya sediakan.";
export function isReportRequest(m, me, owner) {
  const f = m.from || {}; const u = (f.username || "").toLowerCase();
  if (!(u === owner || u === "unaalhaq") || m.edit_date) return false;
  const t = m.text || "";
  if (!t || cpLen(t) > 150) return false;
  const tag = "@" + me.username.toLowerCase();
  const rt = (m.reply_to_message || {}).from || {};
  if (!t.toLowerCase().includes(tag) && rt.id !== me.id) return false;
  let clean = t.split(new RegExp(tag.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi")).join(" ");
  clean = clean.replace(pyRe(String.raw`wan\s*tiger|wantiger|kaki\s*report|\bKR\b`, "gi"), " ");
  return REQ_RE.test(pyStrip(clean));
}

// ---- logger.links_of ----
const URL_RE = /(?:https?:\/\/|www\.|\b[a-z0-9.-]+\.[a-z]{2,}\/)[^\s]+/gi;
export function linksOf(m) {
  const out = [];
  for (const [txt, ents] of [[m.text, m.entities], [m.caption, m.caption_entities]]) {
    if (!txt) continue;
    for (const e of ents || []) {
      if (e.type === "text_link") out.push(e.url);
      else if (e.type === "url") out.push(txt.slice(e.offset, e.offset + e.length)); // offsets are UTF-16, same as JS
    }
    for (const u of txt.match(URL_RE) || []) if (!out.includes(u)) out.push(u);
  }
  return out;
}
