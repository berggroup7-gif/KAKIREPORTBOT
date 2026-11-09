// Unit + parity tests. Pure logic only (no network).
// Parity: set PARITY_DUMP=path to a file made by test/parity_dump.py (real chat data,
// kept outside the repo) to check the JavaScript port against the Python bot.
import assert from "node:assert/strict";
import fs from "node:fs";
import * as S from "../src/stats.js";
import * as R from "../src/reply.js";

let pass = 0;
const ok = (c, msg) => { assert.ok(c, msg); pass++; };
const eq = (a, b, msg) => { assert.deepEqual(a, b, msg); pass++; };

// --- basic rules ---
eq(S.skipText("tq kak"), true); eq(S.skipText("ready"), true); eq(S.skipText("a"), true); eq(S.skipText("ok la"), false);
eq(S.skipText("hello semua"), false); eq(S.skipText(""), true); eq(S.skipText(null), true);
eq(S.isSpellSpam("O k l a h ."), true); eq(S.isSpellSpam("ok lah kak"), false);
eq(S.wordCount("  a  b\u3000c "), 3);
eq(S.pid("d", Date.UTC(2026, 10, 8, 16, 30)), "d:2026-11-09"); // 00:30 MYT
eq(S.pid("w", Date.UTC(2026, 10, 8, 16, 30)), "w:2026-W46");
eq(S.pid("m", Date.UTC(2026, 9, 31, 16, 0)), "m:2026-11");
eq(S.label("d:2026-11-09"), "9 Nov (Isnin)"); eq(S.label("w:2026-W46"), "9 Nov - 15 Nov"); eq(S.label("m:2026-11"), "Nov 2026");
eq(S.parseCmd({ text: "/stattop@KAKIREPORTBOT minggu" }), ["stattop", ["minggu"]]);
eq(S.parseCmd({ text: "*juara bulan" }), ["pemenang", ["bulan"]]);
eq(S.parseCmd({ text: "/stat@otherbot" }), [null, []]);
eq(R.trivial("tq"), true); eq(R.trivial("😀😀"), true); eq(R.trivial("apa cerita hari ni"), false);
eq(R.postProcess('"KR: SKIP"'), "SKIP"); eq(R.postProcess("aku pun tak tahu"), "saya pun tak tahu");
eq(R.postProcess("haha bodoh"), "SKIP");
const me = { id: 4242, username: "KAKIREPORTBOT" };
eq(R.isOwnerCall({ from: { username: "Boss" }, text: "kr mana" }, me, "boss"), true);
eq(R.isBotCall({ from: { id: 5 }, text: "kr tu senyap" }, me), false);
eq(R.isBotCall({ from: { id: 5 }, text: "hai @kakireportbot" }, me), true);
eq(R.isReportRequest({ from: { username: "boss" }, text: "@KAKIREPORTBOT bagi report semalam" }, me, "boss"), true);
eq(R.isReportRequest({ from: { username: "orang" }, text: "@KAKIREPORTBOT bagi report semalam" }, me, "boss"), false);

// --- penalties / burst (chatstats.record) ---
{
  let now = Date.UTC(2026, 10, 9, 2, 0);
  const st = new S.Stats({ groupId: -100, nowFn: () => now });
  const mk = (id, t, d) => ({ message_id: id, date: d, chat: { id: -100 }, from: { id: 7, first_name: "Ali" }, text: t });
  const base = Math.floor(now / 1000);
  st.record(mk(1, "hello semua apa khabar", base));
  eq(st.periods.get("d:2026-11-09").get("7"), [1, 4]);
  st.record(mk(2, "A", base + 1)); st.record(mk(3, "B", base + 2));
  const r = st.record(mk(4, "C", base + 3));
  eq(r.kind, "penalty"); eq(r.n, 3); eq(st.periods.get("d:2026-11-09").get("7"), [0, 0]);
  eq(st.record(mk(5, "O k l a h", base + 4)).kind, "penalty");
  eq(st.record(mk(5, "hello lagi", base + 5)).kind, "skipped"); // duplicate message id
}

// --- parity with Python on real data ---
const dumpPath = process.env.PARITY_DUMP;
if (dumpPath && fs.existsSync(dumpPath)) {
  const D = JSON.parse(fs.readFileSync(dumpPath, "utf8"));
  let bad = 0;
  for (const [t, sk, sp, wc, tr, nc, lk, pf, cl, pr] of D.features) {
    const js = [S.skipText(t), S.isSpellSpam(t), S.wordCount(t), R.trivial(t), R.nameCall(t, false), R.LINK_RE.test(t), R.PREF_RE.test(t), R.classify(t), R.prio({ text: t, toBot: false })];
    const py = [sk, sp, wc, tr, nc, lk, pf, cl, pr];
    if (JSON.stringify(js) !== JSON.stringify(py)) { if (bad++ < 10) console.log("feature mismatch", JSON.stringify(t).slice(0, 80), py, js); }
  }
  eq(bad, 0, `feature mismatches: ${bad}`);
  let now = 0;
  const st = new S.Stats({ groupId: D.group, nowFn: () => now });
  for (const m of D.replay) { now = m.date * 1000; st.record(m); }
  now = D.final_now_ms;
  const js = st.toPython();
  eq(js.periods, D.stats.periods, "periods differ");
  eq(JSON.stringify(Object.keys(js.periods)), JSON.stringify(Object.keys(D.stats.periods)), "period order");
  for (const k of Object.keys(js.periods)) eq(JSON.stringify(Object.keys(js.periods[k])), JSON.stringify(Object.keys(D.stats.periods[k])), "uid order " + k);
  eq(js.names, D.stats.names);
  eq(st.winners, D.winners, "winners differ");
  eq(D.uids.map((u) => st.ownText(u, null)), D.texts.own);
  for (const key of ["hari", "minggu", "bulan"]) { eq(st.topText(key), D.texts["top_" + key], "top " + key); eq(st.winnersText(key), D.texts["win_" + key], "win " + key); }
  console.log(`parity: ${D.features.length} texts, ${D.replay.length} messages replayed, stats/texts identical`);
}
console.log(`all ${pass} checks passed`);
