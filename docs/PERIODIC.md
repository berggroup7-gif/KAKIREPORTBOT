# Periodic GitHub batch mode

This is a **finite batch** runner, not a 24/7 long-poll process.

## Safety defaults

- `NO_SEND=1` — blocks Telegram outbound methods (`sendMessage`, etc.)
- `DRY_RUN=1` — refuses live poll unless `PERIODIC_LIVE=1`
- Pending auto-reply queues are **never** flushed by `batch_runner.py`
- Gemini generation is off unless outbound is explicitly enabled
- Incoming Telegram text is stored as data only; never interpolated into shell

## Scheduling limits (GitHub Actions)

- Cron minimum interval is **5 minutes** and may be delayed or dropped under load
- Public repos auto-disable schedules after 60 days of inactivity (prefer private)
- A single hosted job can run at most **6 hours**; this workflow targets ~2 minutes
- Exactly-once external delivery cannot be promised across crashes; default outbound off

## Durable state

Encrypted state snapshots (`runtime.fernet` + opaque public meta) hold offset/chats/stats.
Public meta never lists plaintext path names; file inventory stays inside the Fernet blob.
Load is **fail-closed**. Persistence target: `bot-state` branch.

## Enabling production (manual checklist)

1. Confirm repository is private
2. Set Actions secrets: `TELEGRAM_BOT_TOKEN`, `GEMINI_API_KEY`, `CHAT_BACKUP_KEY`, `GROUP_CHAT_ID`, `OWNER_ID`, `OWNER_USERNAME`
3. Initialize encrypted state on `bot-state` branch
4. Set repository variable `PERIODIC_ENABLED=true` only when ready for schedule
5. Clear `NO_SEND` / set `PERIODIC_LIVE=1` only after a dry dispatch succeeds
6. Keep concurrency group single + `cancel-in-progress: false`

Until then, use `workflow_dispatch` with dry-run inputs only.

## LIVE activation (parent only)

Defaults remain OFF. To enable reactive periodic LIVE after dry-run is green:

1. Ensure no local poller (`logger.py` / `run_loop`) is running
2. Sync fresh encrypted state to `bot-state` (higher offset wins; never lower)
3. Set repository variables only when approved:
   - `PERIODIC_ENABLED=true` — allows schedule
   - `PERIODIC_LIVE=true` — schedule may live-poll (dispatch uses input)
   - `PERIODIC_OUTBOUND=true` — allows sends when input/allow flags set
   - `PERIODIC_GEMINI=true` — finite sync Gemini replies
   - `PERIODIC_CUTOFF_TS=<unix>` — suppress unsolicited replies for older messages
4. Dispatch with `live_poll=true` / `allow_outbound=true` only after the above
5. Keep `NO_SEND`/`DRY_RUN` safe unless outbound vars are intentionally true

Exactly-once Telegram delivery is not guaranteed across crashes; outbound marks reduce duplicates.

## Resume on reconnect

Every LIVE batch restores the latest encrypted `bot-state` snapshot (**fail-closed** if missing/invalid) and continues Telegram `getUpdates` from the saved `offset.txt` checkpoint.

Guarantees:
- **Continue from last durable checkpoint** — does not jump to the latest update, does not call `drop_pending_updates`, does not use a negative offset (which would discard the queue).
- **Record available backlog** with original message timestamps (Asia/Kuala_Lumpur / MYT dates in jsonl). `PERIODIC_CUTOFF_TS` suppresses unsolicited Gemini/auto-replies and spam **warning sends** for older messages; stats/raw still ingest.
- **Replay idempotence (best effort):** `batch_seen_ids.json` skips duplicate `update_id`; `batch_outbound_marks.json` skips duplicate sends; `stats.json` `last_mid` prevents double-counting. Raw jsonl append is skipped when `update_id` is already seen.
- **Durable advance:** encrypted save + `bot-state` publish run only after a successful batch. If publish fails, the next run reloads the previous checkpoint and retries (at-least-once).

### At-least-once vs exactly-once

Telegram confirms an update only on a later `getUpdates` call whose `offset` is greater than that `update_id`. This runner persists the next offset into encrypted `bot-state` after processing. A crash **after** Telegram has accepted a higher offset on a subsequent poll but **before** `bot-state` publish can theoretically lose the gap only if durable state lags — we avoid confirming ahead of durable state by using solely the restored offset for each job’s single poll. A crash **after** local process but **before** publish yields **at-least-once** redelivery; dedupe layers above absorb it. **Exactly-once is not promised.**

### Unrecoverable Bot API limit

Per [Telegram Bot API — Getting updates](https://core.telegram.org/bots/api#getting-updates): incoming updates are stored until retrieved, **but not longer than 24 hours**. Gaps older than that (e.g. offline Nov 7–8) cannot be reconstructed from Bot API; do not fabricate records.

