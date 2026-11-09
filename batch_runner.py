#!/usr/bin/env python3
"""Bounded Telegram update batch for periodic runners (not a continuous poller).

Safe defaults: NO_SEND=1, DRY_RUN=1, no pending flush, no backlog unsolicited replies.
LIVE outbound requires explicit env gates cleared by the workflow when approved.

Resume: every live batch reads durable offset.txt from restored encrypted state and
continues getUpdates from that checkpoint (never drops pending, never jumps to latest).
Delivery is at-least-once across a crash between Telegram ack and bot-state publish;
seen-ids + outbound marks + stats last_mid make replay idempotent for raw/stats/sends.
"""
from __future__ import annotations

import argparse
import random
import datetime
import json
import logging
import os
import sys
import time
from pathlib import Path

# Safe default only if unset — workflow may clear for gated LIVE.
os.environ.setdefault("NO_SEND", "1")
os.environ.setdefault("DRY_RUN", "1")

from common import DATA, TZ, api, no_send_enabled, blocked_calls  # noqa: E402
import logger as L  # noqa: E402
import chatstats  # noqa: E402
import admin  # noqa: E402
import gemini_reply  # noqa: E402
import identity  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DEFAULT_MAX_UPDATES = 50
DEFAULT_MAX_SECONDS = 280  # room for 2–4 replies with 30–60s gaps; job timeout 6m
DEFAULT_MAX_GEMINI = 4
SEEN_FILE = os.path.join(DATA, "batch_seen_ids.json")
OUTBOUND_MARKS = os.path.join(DATA, "batch_outbound_marks.json")


def _truthy(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _cutoff_ts() -> int | None:
    raw = os.environ.get("PERIODIC_CUTOFF_TS", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        raise RuntimeError("invalid PERIODIC_CUTOFF_TS")


def load_seen(limit: int = 5000) -> set[int]:
    try:
        raw = json.load(open(SEEN_FILE))
        ids = [int(x) for x in raw.get("ids", [])][-limit:]
        return set(ids)
    except Exception:
        return set()


def save_seen(seen: set[int], limit: int = 5000) -> None:
    ids = sorted(seen)[-limit:]
    tmp = SEEN_FILE + ".tmp"
    json.dump({"ids": ids}, open(tmp, "w"))
    os.replace(tmp, SEEN_FILE)


def load_marks() -> set[str]:
    try:
        return set(json.load(open(OUTBOUND_MARKS)).get("keys", []))
    except Exception:
        return set()


def save_marks(marks: set[str], limit: int = 8000) -> None:
    keys = sorted(marks)[-limit:]
    tmp = OUTBOUND_MARKS + ".tmp"
    json.dump({"keys": keys}, open(tmp, "w"))
    os.replace(tmp, OUTBOUND_MARKS)


def _mark_key(kind: str, update_id: int, message_id: int | None = None) -> str:
    return f"{kind}:{update_id}:{message_id or 0}"


def _msg_too_old(m: dict, cutoff: int | None) -> bool:
    if cutoff is None:
        return False
    return int(m.get("date") or 0) < cutoff


def _reply_gap_seconds() -> float:
    """Random pause between consecutive Gemini replies. Tests may set BATCH_REPLY_DELAY_SEC=0."""
    raw = os.environ.get("BATCH_REPLY_DELAY_SEC", "").strip()
    if raw != "":
        return max(0.0, float(raw))
    return float(random.randint(30, 60))


def _pick_gemini_budget(max_gemini: int, n_candidates: int) -> int:
    """Random 2–4 replies per run, capped by candidates and max_gemini."""
    if n_candidates <= 0 or max_gemini <= 0:
        return 0
    hi = min(4, max_gemini, n_candidates)
    lo = min(2, hi)
    return random.randint(lo, hi)


def process_updates(
    ups: list[dict],
    me: dict,
    *,
    allow_outbound: bool = False,
    allow_gemini: bool = False,
    flush_pending: bool = False,
    max_updates: int | None = None,
    max_gemini: int = DEFAULT_MAX_GEMINI,
    cutoff_ts: int | None = None,
) -> dict:
    if flush_pending:
        raise RuntimeError("flush_pending is disabled in batch_runner (refusing pending send)")
    if allow_outbound and no_send_enabled():
        raise RuntimeError("allow_outbound requested but NO_SEND is enabled")
    if allow_gemini and not allow_outbound:
        raise RuntimeError("allow_gemini requires allow_outbound")

    seen = load_seen()
    marks = load_marks()
    chats = L.load_chats()
    changed = False
    next_offset = None
    processed = 0
    skipped_dup = 0
    recorded = 0
    handled_cmds = 0
    spam_skipped = 0
    outbound_attempts = 0
    outbound_sent = 0
    outbound_deduped = 0
    backlog_suppressed = 0
    gemini_calls = 0
    gemini_sent = 0
    gemini_target = 0
    gemini_candidates: list[dict] = []

    chatstats._load()

    for u in ups:
        if max_updates is not None and processed >= max_updates:
            break
        uid = int(u["update_id"])
        if uid in seen:
            skipped_dup += 1
            next_offset = uid + 1
            continue
        seen.add(uid)
        processed += 1
        next_offset = uid + 1
        now = datetime.datetime.now(TZ).isoformat()

        if "my_chat_member" in u:
            m = u["my_chat_member"]
            c = m["chat"]
            chats[str(c["id"])] = {
                "title": c.get("title"),
                "type": c["type"],
                "status": m["new_chat_member"]["status"],
                "updated": now,
            }
            changed = True
        if "chat_member" in u:
            cm = u["chat_member"]
            c = cm["chat"]
            ts = datetime.datetime.fromtimestamp(cm["date"], TZ)
            was = cm["old_chat_member"]["status"] in ("member", "administrator", "creator", "restricted") and cm["old_chat_member"].get("is_member", True)
            isn = cm["new_chat_member"]["status"] in ("member", "administrator", "creator") or (
                cm["new_chat_member"]["status"] == "restricted" and cm["new_chat_member"].get("is_member")
            )
            if was != isn:
                L.ev("join" if isn else "leave", c, ts, cm["new_chat_member"]["user"], "chat_member")

        m = u.get("message") or u.get("edited_message")
        if not m:
            continue

        if m["chat"]["type"] == "private":
            f = m.get("from", {})
            pf = os.path.join(os.path.dirname(L.OFF), "private_users.json")
            try:
                pu = json.load(open(pf))
            except Exception:
                pu = {}
            pu[str(f.get("id"))] = {
                "username": f.get("username"),
                "name": L.name(f),
                "chat_id": m["chat"]["id"],
                "updated": now,
            }
            json.dump(pu, open(pf, "w"), indent=1)
            if "message" in u and allow_outbound:
                mk = _mark_key("cmd", uid, m.get("message_id"))
                if mk in marks:
                    outbound_deduped += 1
                else:
                    try:
                        # Inherit original permission checks inside handlers
                        res = chatstats.handle(m)
                        if not res:
                            res = admin.handle(m)
                        if res:
                            handled_cmds += 1
                            outbound_attempts += 1
                            outbound_sent += 1
                            marks.add(mk)
                    except Exception:
                        logging.exception("cmd handle failed")
                        outbound_attempts += 1
            continue

        if m["chat"]["type"] in ("group", "supergroup"):
            c = m["chat"]
            ts = datetime.datetime.fromtimestamp(m["date"], TZ)
            prev = chats.get(str(c["id"]), {})
            chats[str(c["id"])] = {
                **prev,
                "title": c.get("title"),
                "type": c["type"],
                "status": prev.get("status", "member"),
                "updated": now,
            }
            changed = True
            f = m.get("from", {})
            for nm in m.get("new_chat_members") or []:
                L.ev("join", c, ts, nm, "service", m["message_id"])
            if m.get("left_chat_member"):
                L.ev("leave", c, ts, m["left_chat_member"], "service", m["message_id"])
            if m.get("new_chat_members") or m.get("left_chat_member"):
                continue
            # Untrusted content stored as JSON only — never interpolated into shell.
            rec = {
                "type": "message",
                "user_id": f.get("id"),
                "links": L.links_of(m),
                "chat_id": c["id"],
                "chat_title": c.get("title"),
                "message_id": m["message_id"],
                "ts": ts.isoformat(),
                "sender": L.name(f),
                "username": f.get("username"),
                "text": m.get("text") or m.get("caption"),
                "reply_to": (m.get("reply_to_message") or {}).get("message_id"),
                "edited": "edited_message" in u,
            }
            L.write(ts, rec)
            recorded += 1
            if rec["edited"]:
                continue
            is_cmd = (m.get("text") or "").startswith("/") or bool(chatstats.parse(m)[0])
            old = _msg_too_old(m, cutoff_ts)
            try:
                # Record with original message date; suppress penalty warns on pre-cutoff backlog
                chatstats.record(m, warn=not old)
            except Exception:
                logging.exception("stats record failed")

            if allow_outbound and is_cmd:
                # chatstats.handle already no-ops non-GROUP; skip early for clarity
                if c["id"] != identity.GROUP:
                    continue
                mk = _mark_key("gcmd", uid, m.get("message_id"))
                if mk in marks:
                    outbound_deduped += 1
                else:
                    try:
                        if chatstats.handle(m):
                            handled_cmds += 1
                            outbound_attempts += 1
                            outbound_sent += 1
                            marks.add(mk)
                    except Exception:
                        logging.exception("stat cmd failed")
                        outbound_attempts += 1
                continue

            if c["id"] == identity.GROUP and chatstats.is_spell_spam(m.get("text") or m.get("caption")):
                spam_skipped += 1
                continue

            try:
                gemini_reply.note(m, gemini_reply.name_of(f))
            except Exception:
                logging.exception("note failed")

            # Outbound auto-replies / Gemini only in configured MP HAIREE group
            if c["id"] != identity.GROUP:
                continue

            # Unsolicited replies suppressed for pre-cutoff backlog
            if old:
                backlog_suppressed += 1
                continue

            if not (allow_outbound and allow_gemini):
                continue

            mk = _mark_key("reply", uid, m.get("message_id"))
            if mk in marks:
                outbound_deduped += 1
                continue

            # Report-share requests answered immediately (command-like), not delayed
            try:
                if L.maybe_share_report(m, me):
                    outbound_attempts += 1
                    outbound_sent += 1
                    marks.add(mk)
                    continue
            except Exception:
                logging.exception("share report failed")

            text = m.get("text") or m.get("caption") or ""
            # Skip tq/ready-type and empty chatter for Gemini candidates
            if gemini_reply.trivial(text):
                continue

            is_call = L.is_owner_call(m, me) or L.is_bot_call(m, me)
            gemini_candidates.append({
                "update_id": uid,
                "user_id": f.get("id"),
                "message": m,
                "is_call": bool(is_call),
                "mark": mk,
            })

    # Gemini phase after commands: random 2–4, one per person, delay between sends
    if allow_outbound and allow_gemini and gemini_candidates:
        # Deduplicate to one candidate per user (first-seen after shuffle)
        random.shuffle(gemini_candidates)
        unique: list[dict] = []
        seen_users: set = set()
        for cand in gemini_candidates:
            uid_c = cand.get("user_id")
            if uid_c is None or uid_c in seen_users:
                continue
            seen_users.add(uid_c)
            unique.append(cand)
        gemini_target = _pick_gemini_budget(max_gemini, len(unique))
        chosen = unique[:gemini_target]
        for i, cand in enumerate(chosen):
            if i > 0:
                gap = _reply_gap_seconds()
                if gap > 0:
                    logging.info("gemini reply gap %.1fs before next", gap)
                    time.sleep(gap)
            mk = cand["mark"]
            if mk in marks:
                outbound_deduped += 1
                continue
            try:
                outbound_attempts += 1
                gemini_calls += 1
                reply = gemini_reply.gen_reply(cand["message"], cand["is_call"], timeout=12)
                if reply and reply != "SKIP":
                    gemini_reply.send(identity.GROUP, cand["message"]["message_id"], reply)
                    outbound_sent += 1
                    gemini_sent += 1
                    marks.add(mk)
                else:
                    marks.add(mk)  # avoid retry storm on SKIP
            except Exception:
                logging.exception("sync reply failed")

    if changed:
        L.save_chats(chats)
    if next_offset is not None:
        open(L.OFF, "w").write(str(next_offset))
    save_seen(seen)
    save_marks(marks)
    chatstats._save()

    return {
        "processed": processed,
        "skipped_dup": skipped_dup,
        "recorded": recorded,
        "handled_cmds": handled_cmds,
        "spam_skipped": spam_skipped,
        "outbound_attempts": outbound_attempts,
        "outbound_sent": outbound_sent,
        "outbound_deduped": outbound_deduped,
        "backlog_suppressed": backlog_suppressed,
        "gemini_calls": gemini_calls,
        "gemini_sent": gemini_sent,
        "gemini_target": gemini_target,
        "next_offset": next_offset,
        "no_send": no_send_enabled(),
        "allow_outbound": allow_outbound,
        "allow_gemini": allow_gemini,
        "cutoff_ts": cutoff_ts,
        "blocked_methods": list(blocked_calls[-20:]),
    }


def fetch_updates(offset: int | None, limit: int, timeout: int = 0) -> list[dict]:
    """Fetch from durable checkpoint only. Never drop_pending_updates / negative offset."""
    if offset is not None and int(offset) < 0:
        raise RuntimeError("refusing negative getUpdates offset (would discard pending updates)")
    params = {
        "timeout": timeout,
        "limit": max(1, min(100, limit)),
        "allowed_updates": ["message", "edited_message", "my_chat_member", "chat_member"],
        # Explicitly omit drop_pending_updates — pending must be collected on reconnect.
    }
    if offset is not None:
        params["offset"] = int(offset)
    if "drop_pending_updates" in params:
        raise RuntimeError("drop_pending_updates is forbidden")
    return api("getUpdates", **params) or []


def run_once(
    *,
    max_updates: int = DEFAULT_MAX_UPDATES,
    max_seconds: int = DEFAULT_MAX_SECONDS,
    max_gemini: int = DEFAULT_MAX_GEMINI,
    live_poll: bool = False,
    allow_outbound: bool = False,
    allow_gemini: bool = False,
    injected: list[dict] | None = None,
    cutoff_ts: int | None = None,
) -> dict:
    started = time.time()
    os.makedirs(DATA, exist_ok=True)
    if injected is not None:
        me = {"id": 0, "username": "testbot"}
        ups = injected
    elif live_poll:
        if _truthy("DRY_RUN", "1") and not _truthy("PERIODIC_LIVE", "0"):
            raise RuntimeError("live_poll refused: DRY_RUN=1 (set PERIODIC_LIVE=1 and DRY_RUN=0)")
        if not os.path.exists(L.OFF):
            raise RuntimeError("fail-closed: live_poll requires restored offset.txt checkpoint")
        me = api("getMe")
        off = int(open(L.OFF).read().strip())
        ups = fetch_updates(off, max_updates, timeout=0)
    else:
        me = {"id": 0, "username": "offline"}
        ups = []

    if time.time() - started > max_seconds:
        return {"processed": 0, "error": "timeout_before_process", "next_offset": None}

    summary = process_updates(
        ups,
        me,
        allow_outbound=allow_outbound,
        allow_gemini=allow_gemini,
        flush_pending=False,
        max_updates=max_updates,
        max_gemini=max_gemini,
        cutoff_ts=cutoff_ts if cutoff_ts is not None else _cutoff_ts(),
    )
    summary["elapsed_s"] = round(time.time() - started, 3)
    summary["max_updates"] = max_updates
    summary["live_poll"] = live_poll
    summary["injected"] = injected is not None
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Bounded periodic batch runner")
    ap.add_argument("--max-updates", type=int, default=int(os.environ.get("BATCH_MAX_UPDATES", DEFAULT_MAX_UPDATES)))
    ap.add_argument("--max-seconds", type=int, default=int(os.environ.get("BATCH_MAX_SECONDS", DEFAULT_MAX_SECONDS)))
    ap.add_argument("--max-gemini", type=int, default=int(os.environ.get("BATCH_MAX_GEMINI", DEFAULT_MAX_GEMINI)))
    ap.add_argument("--live-poll", action="store_true")
    ap.add_argument("--allow-outbound", action="store_true")
    ap.add_argument("--allow-gemini", action="store_true")
    ap.add_argument("--json-in", type=Path)
    args = ap.parse_args(argv)

    injected = None
    if args.json_in:
        injected = json.loads(args.json_in.read_text())
        if not isinstance(injected, list):
            raise SystemExit("json-in must be a list of updates")

    # LIVE outbound only when explicitly requested AND safety envs allow it
    allow_outbound = (
        bool(args.allow_outbound)
        and _truthy("PERIODIC_OUTBOUND", "0")
        and not _truthy("DRY_RUN", "1")
        and not no_send_enabled()
    )
    allow_gemini = bool(args.allow_gemini) and _truthy("PERIODIC_GEMINI", "0") and allow_outbound

    summary = run_once(
        max_updates=args.max_updates,
        max_seconds=args.max_seconds,
        max_gemini=args.max_gemini,
        live_poll=bool(args.live_poll) and injected is None,
        allow_outbound=allow_outbound,
        allow_gemini=allow_gemini,
        injected=injected,
    )
    print(json.dumps({k: v for k, v in summary.items() if k != "updates"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
