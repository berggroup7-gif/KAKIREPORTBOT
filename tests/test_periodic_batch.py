#!/usr/bin/env python3
"""Offline tests for periodic batch LIVE gates (no real Telegram/Gemini network)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["NO_SEND"] = "1"
os.environ["DRY_RUN"] = "1"
os.environ["KAKIREPORTBOT_TEST_IDENTITY"] = "1"
os.environ.setdefault("GROUP_CHAT_ID", "-1009999999999")
os.environ.setdefault("OWNER_ID", "100000001")
os.environ.setdefault("OWNER_USERNAME", "test_ketua")


def _upd(update_id: int, text: str, *, mid: int, chat_id: int = -1009999999999, username="alice", uid=7, date=1700000000, private=False, owner=False):
    chat = {"id": (100000001 if private else chat_id), "type": "private" if private else "supergroup", "title": "G"}
    frm = {"id": (100000001 if owner else uid), "username": ("test_ketua" if owner else username), "first_name": "A", "is_bot": False}
    msg = {"message_id": mid, "date": date, "chat": chat, "from": frm, "text": text}
    return {"update_id": update_id, "message": msg}


class PeriodicBatchTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = Path(self._td.name)
        self.data = self.td / "data"
        self.data.mkdir()
        (self.data / "offset.txt").write_text("1\n")
        (self.data / "stats.json").write_text('{"periods":{},"names":{},"last_mid":{},"backfilled":true}\n')
        (self.data / "chats.json").write_text("{}\n")
        (self.data / "settings.json").write_text('{"replies_on":true,"per_user_min":2}\n')
        import identity
        identity.refresh()
        import common, logger, chatstats, gemini_reply, batch_runner, admin
        self.common = common
        self.logger = logger
        self.chatstats = chatstats
        self.gemini_reply = gemini_reply
        self.batch_runner = batch_runner
        self.admin = admin
        common.DATA = str(self.data)
        common.blocked_calls.clear()
        logger.DATA = str(self.data)
        logger.OFF = str(self.data / "offset.txt")
        logger.CHATS = str(self.data / "chats.json")
        chatstats.DATA = str(self.data)
        chatstats.SF = str(self.data / "stats.json")
        chatstats._st = None
        gemini_reply.DATA = str(self.data)
        gemini_reply.LAST = str(self.data / "last_replied.txt")
        gemini_reply.USAGE = str(self.data / "gemini_usage.json")
        batch_runner.SEEN_FILE = str(self.data / "batch_seen_ids.json")
        batch_runner.OUTBOUND_MARKS = str(self.data / "batch_outbound_marks.json")
        os.environ["NO_SEND"] = "1"
        os.environ["DRY_RUN"] = "1"
        os.environ.pop("PERIODIC_OUTBOUND", None)
        os.environ.pop("PERIODIC_GEMINI", None)
        os.environ.pop("PERIODIC_LIVE", None)
        os.environ["BATCH_REPLY_DELAY_SEC"] = "0"

    def tearDown(self):
        self._td.cleanup()

    def test_no_send_blocks_network_methods(self):
        with self.assertRaises(RuntimeError):
            self.common.api("sendMessage", chat_id=1, text="x")
        self.assertIn("sendMessage", self.common.blocked_calls)

    def test_batch_limit(self):
        ups = [_upd(10 + i, f"hello {i}", mid=100 + i) for i in range(10)]
        s = self.batch_runner.process_updates(ups, {"id": 1, "username": "kakireportbot"}, max_updates=3)
        self.assertEqual(s["processed"], 3)

    def test_duplicate_update_and_outbound_marks(self):
        ups = [_upd(50, "hi", mid=500), _upd(50, "hi", mid=500), _upd(51, "yo", mid=501)]
        me = {"id": 1, "username": "kakireportbot"}
        self.batch_runner.process_updates(ups[:1], me)
        s2 = self.batch_runner.process_updates(ups, me)
        self.assertGreaterEqual(s2["skipped_dup"], 1)
        self.assertEqual(s2["processed"], 1)

    def test_cutoff_suppresses_unsolicited_but_records(self):
        ups = [_upd(60, "hello group", mid=600, date=100)]
        me = {"id": 1, "username": "kakireportbot"}
        os.environ["NO_SEND"] = "0"
        s = self.batch_runner.process_updates(
            ups, me, allow_outbound=True, allow_gemini=True, cutoff_ts=500
        )
        self.assertEqual(s["recorded"], 1)
        self.assertEqual(s["backlog_suppressed"], 1)
        self.assertEqual(s["gemini_calls"], 0)

    def test_admin_owner_permission_path(self):
        # stranger private /status should not be handled as owner command
        ups = [_upd(80, "/status", mid=800, private=True, owner=False, uid=999, username="stranger")]
        # Patch handlers to observe calls without network
        with mock.patch.object(self.chatstats, "handle", return_value=False) as h1, \
             mock.patch.object(self.admin, "handle", return_value=False) as h2:
            os.environ["NO_SEND"] = "0"
            s = self.batch_runner.process_updates(ups, {"id": 1, "username": "x"}, allow_outbound=True)
            self.assertTrue(h1.called or h2.called)
            self.assertEqual(s["handled_cmds"], 0)

        # owner private command counted when handler returns True
        ups2 = [_upd(81, "/status", mid=801, private=True, owner=True)]
        with mock.patch.object(self.chatstats, "handle", return_value=False), \
             mock.patch.object(self.admin, "handle", return_value=True):
            os.environ["NO_SEND"] = "0"
            s2 = self.batch_runner.process_updates(ups2, {"id": 1, "username": "x"}, allow_outbound=True)
            self.assertEqual(s2["handled_cmds"], 1)

    def test_outbound_dedupe_marks(self):
        ups = [_upd(90, "/stat", mid=900, chat_id=-1009999999999)]
        me = {"id": 1, "username": "kakireportbot"}
        os.environ["NO_SEND"] = "0"
        with mock.patch.object(self.chatstats, "parse", return_value=("stat", {})), \
             mock.patch.object(self.chatstats, "handle", return_value=True):
            s1 = self.batch_runner.process_updates(ups, me, allow_outbound=True)
            self.assertEqual(s1["outbound_sent"], 1)
            # reset seen but keep marks — simulate redelivery same update after seen clear
            self.batch_runner.save_seen(set())
            s2 = self.batch_runner.process_updates(ups, me, allow_outbound=True)
            self.assertGreaterEqual(s2["outbound_deduped"], 1)

    def test_finite_sync_gemini_no_worker(self):
        ups = [_upd(91, "@kakireportbot hi", mid=910, date=999999)]
        me = {"id": 9167193513, "username": "kakireportbot"}
        os.environ["NO_SEND"] = "0"
        with mock.patch.object(self.gemini_reply, "gen_reply", return_value="ok") as gen, \
             mock.patch.object(self.gemini_reply, "send", return_value={"message_id": 1}) as send, \
             mock.patch.object(self.logger, "is_bot_call", return_value=True), \
             mock.patch.object(self.logger, "maybe_share_report", return_value=False):
            s = self.batch_runner.process_updates(
                ups, me, allow_outbound=True, allow_gemini=True, max_gemini=1, cutoff_ts=1
            )
            self.assertEqual(s["gemini_calls"], 1)
            self.assertTrue(gen.called)
            self.assertTrue(send.called)
            self.assertEqual(s["outbound_sent"], 1)

    def test_pending_flush_refused(self):
        with self.assertRaises(RuntimeError):
            self.batch_runner.process_updates([], {"id": 1, "username": "x"}, flush_pending=True)

    def test_state_roundtrip_and_failclosed_missing(self):
        from cryptography.fernet import Fernet
        key = Fernet.generate_key()
        key_file = self.td / "k"
        key_file.write_bytes(key)
        os.chmod(key_file, 0o600)
        out = self.td / "state.fernet"
        man = self.td / "state.json"
        subprocess.run(
            [sys.executable, str(ROOT / "tools/state_store.py"), "save",
             "--data-dir", str(self.data), "--out", str(out), "--manifest", str(man),
             "--key-file", str(key_file)],
            check=True, capture_output=True, text=True,
        )
        dest = self.td / "restored"
        subprocess.run(
            [sys.executable, str(ROOT / "tools/state_store.py"), "load",
             "--archive", str(out), "--manifest", str(man), "--dest", str(dest),
             "--key-file", str(key_file)],
            check=True, capture_output=True, text=True,
        )
        self.assertEqual((dest / "offset.txt").read_text(), "1\n")
        bad = subprocess.run(
            [sys.executable, str(ROOT / "tools/state_store.py"), "load",
             "--archive", str(self.td / "missing.fernet"), "--manifest", str(man),
             "--dest", str(self.td / "x"), "--key-file", str(key_file)],
            capture_output=True, text=True,
        )
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("fail-closed", bad.stderr + bad.stdout)


    def test_reconnect_resumes_from_offset(self):
        """Live poll must pass durable checkpoint offset to getUpdates; never drop pending."""
        (self.data / "offset.txt").write_text("649735269\n")
        os.environ["NO_SEND"] = "0"
        os.environ["DRY_RUN"] = "0"
        os.environ["PERIODIC_LIVE"] = "1"
        captured = {}

        def fake_api(method, **params):
            if method == "getMe":
                return {"id": 1, "username": "kakireportbot"}
            if method == "getUpdates":
                captured["params"] = dict(params)
                return []
            raise AssertionError(method)

        with mock.patch.object(self.batch_runner, "api", side_effect=fake_api), \
             mock.patch.object(self.common, "api", side_effect=fake_api):
            # patch module-level api used inside fetch_updates
            import batch_runner as br
            with mock.patch.object(br, "api", side_effect=fake_api):
                s = br.run_once(live_poll=True, max_updates=10)
        self.assertEqual(captured["params"].get("offset"), 649735269)
        self.assertNotIn("drop_pending_updates", captured["params"])
        self.assertIsNone(s.get("error"))

    def test_live_poll_failclosed_without_offset(self):
        (self.data / "offset.txt").unlink()
        os.environ["DRY_RUN"] = "0"
        os.environ["PERIODIC_LIVE"] = "1"
        with self.assertRaisesRegex(RuntimeError, "fail-closed.*offset"):
            self.batch_runner.run_once(live_poll=True, injected=None)

    def test_fetch_refuses_negative_offset(self):
        with self.assertRaisesRegex(RuntimeError, "negative"):
            self.batch_runner.fetch_updates(-1, 10)

    def test_replay_dedupe_raw_and_stats(self):
        ups = [_upd(200, "hello durable", mid=2000, date=1700000100)]
        me = {"id": 1, "username": "kakireportbot"}
        s1 = self.batch_runner.process_updates(ups, me)
        self.assertEqual(s1["recorded"], 1)
        day = list(self.data.glob("*.jsonl"))
        self.assertTrue(day)
        lines1 = day[0].read_text().strip().splitlines()
        st1 = __import__("json").loads((self.data / "stats.json").read_text())
        # reconnect/replay same updates with persisted seen
        s2 = self.batch_runner.process_updates(ups, me)
        self.assertGreaterEqual(s2["skipped_dup"], 1)
        self.assertEqual(s2["recorded"], 0)
        lines2 = day[0].read_text().strip().splitlines()
        self.assertEqual(len(lines1), len(lines2))
        st2 = __import__("json").loads((self.data / "stats.json").read_text())
        self.assertEqual(st1.get("last_mid"), st2.get("last_mid"))
        self.assertEqual(st1.get("periods"), st2.get("periods"))

    def test_cutoff_records_without_penalty_warn(self):
        ups = [_upd(201, "a", mid=2001, date=100)]  # tiny burst-ish single letter pre-cutoff
        me = {"id": 1, "username": "kakireportbot"}
        with mock.patch.object(self.chatstats, "_warn") as warn, \
             mock.patch.object(self.chatstats, "is_spell_spam", return_value=True), \
             mock.patch.object(self.chatstats, "is_burst", return_value=False):
            os.environ["NO_SEND"] = "0"
            s = self.batch_runner.process_updates(
                ups, me, allow_outbound=True, allow_gemini=True, cutoff_ts=500
            )
            self.assertEqual(s["recorded"], 1)
            self.assertEqual(s["gemini_calls"], 0)
            self.assertFalse(warn.called)


    def test_gemini_random_budget_one_per_user_and_gaps(self):
        """2–4 replies max, one per person, trivial skipped, delay between sends."""
        me = {"id": 9167193513, "username": "kakireportbot"}
        ups = [
            _upd(301, "apa khabar semua hari ni", mid=301, uid=11, username="a", date=999999),
            _upd(302, "tq", mid=302, uid=12, username="b", date=999999),  # trivial skip
            _upd(303, "link mp best ke", mid=303, uid=13, username="c", date=999999),
            _upd(304, "second from a again", mid=304, uid=11, username="a", date=999999),  # same user
            _upd(305, "eh kena check dulu", mid=305, uid=14, username="d", date=999999),
            _upd(306, "ready", mid=306, uid=15, username="e", date=999999),  # trivial
        ]
        os.environ["NO_SEND"] = "0"
        os.environ["BATCH_REPLY_DELAY_SEC"] = "0"
        sleeps = []
        with mock.patch.object(self.batch_runner.random, "randint", side_effect=[3, 30]), \
             mock.patch.object(self.batch_runner.time, "sleep", side_effect=lambda s: sleeps.append(s)), \
             mock.patch.object(self.gemini_reply, "gen_reply", return_value="ok je"), \
             mock.patch.object(self.gemini_reply, "send", return_value={"message_id": 1}) as send, \
             mock.patch.object(self.logger, "is_bot_call", return_value=False), \
             mock.patch.object(self.logger, "is_owner_call", return_value=False), \
             mock.patch.object(self.logger, "maybe_share_report", return_value=False):
            # randint used for budget (2..4) then possibly gap; force budget via patch of helper
            with mock.patch.object(self.batch_runner, "_pick_gemini_budget", return_value=3), \
                 mock.patch.object(self.batch_runner, "_reply_gap_seconds", return_value=45.0):
                s = self.batch_runner.process_updates(
                    ups, me, allow_outbound=True, allow_gemini=True, max_gemini=4, cutoff_ts=1
                )
        self.assertEqual(s["gemini_target"], 3)
        self.assertEqual(s["gemini_sent"], 3)
        self.assertEqual(s["gemini_calls"], 3)
        self.assertEqual(send.call_count, 3)
        # one per user: users 11,13,14 only (tq/ready skipped; second from 11 dropped)
        self.assertEqual(len(sleeps), 2)  # gaps between 2nd and 3rd only after first
        self.assertTrue(all(x == 45.0 for x in sleeps))

    def test_commands_not_delayed_by_gemini_gaps(self):
        me = {"id": 1, "username": "kakireportbot"}
        ups = [_upd(401, "/stat", mid=401, date=999999)]
        os.environ["NO_SEND"] = "0"
        sleeps = []
        with mock.patch.object(self.batch_runner.time, "sleep", side_effect=lambda s: sleeps.append(s)), \
             mock.patch.object(self.chatstats, "parse", return_value=("stat", {})), \
             mock.patch.object(self.chatstats, "handle", return_value=True), \
             mock.patch.object(self.batch_runner, "_pick_gemini_budget", return_value=0):
            s = self.batch_runner.process_updates(ups, me, allow_outbound=True, allow_gemini=True, cutoff_ts=1)
        self.assertEqual(s["handled_cmds"], 1)
        self.assertEqual(sleeps, [])

    def test_main_respects_outbound_env_gates(self):
        # even with --allow-outbound, DRY_RUN/NO_SEND/PERIODIC_OUTBOUND block
        os.environ["NO_SEND"] = "1"
        os.environ["DRY_RUN"] = "1"
        os.environ["PERIODIC_OUTBOUND"] = "0"
        with mock.patch.object(self.batch_runner, "run_once", return_value={"ok": True}) as run:
            self.batch_runner.main(["--allow-outbound", "--allow-gemini"])
            kwargs = run.call_args.kwargs
            self.assertFalse(kwargs["allow_outbound"])
            self.assertFalse(kwargs["allow_gemini"])


if __name__ == "__main__":
    unittest.main()
