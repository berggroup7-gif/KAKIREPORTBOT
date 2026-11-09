# KAKIREPORTBOT

Telegram group logger, stats, and reply bot (Python 3).

## Setup

1. Copy `.env.example` to `.env` and set `TELEGRAM_BOT_TOKEN` and `GEMINI_API_KEY`.
2. `python3 -m pip install -r requirements.txt`
3. `chmod +x start.sh stop.sh run_loop.sh`
4. `./start.sh` to run the long-poll loop; `./stop.sh` to stop.

Secrets for CI/CD belong in repository Secrets, not in committed files.

## Layout

- `logger.py` — main long-poll entry (`getUpdates`)
- `gemini_reply.py` — reply worker
- `chatstats.py` / `admin.py` — stats and owner commands
- `common.py` — Telegram API helpers
- `data/` — runtime state (created locally; not committed)
- `backup/` — encrypted snapshot of prior local state (ciphertext only)

## Restore encrypted backup

```bash
export CHAT_BACKUP_KEY="$(cat /path/to/chat_backup.key)"
python3 tools/restore_chat_backup.py --archive backup/chat_data.fernet --dest ./data
```

The key is never printed. Restore refuses paths outside the destination directory.

## Notes

- Do not commit `.env`, raw `data/`, or `logs/`.
- Only one poller should run per bot token.
- Gemini is used for replies when configured.

## Periodic batch (GitHub)

See `docs/PERIODIC.md`. Default dry-run with `NO_SEND=1`.
Scheduling gated by `PERIODIC_ENABLED`.
