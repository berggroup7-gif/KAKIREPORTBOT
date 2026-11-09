# KAKIREPORTBOT on Cloudflare Workers

Telegram webhook version of the bot (instant replies). Same behaviour and data as the
Python bot in the repo root, which stays for rollback.

- `src/index.js` – Worker (`POST /telegram`, checks `X-Telegram-Bot-Api-Secret-Token`) and the
  Durable Object `BotState` (SQLite): raw chat log (`raw`, same records as the old `YYYY-MM-DD.jsonl`),
  stats (`stats`, `names`, `penalties`), winners/settings/nicknames/name requests (`kv`), replies log, reports.
- `src/stats.js` – exact port of `chatstats.py` (counting rules, spam/burst penalties, /stat, /stattop, /pemenang).
- `src/reply.js`, `src/persona.js` – port of `gemini_reply.py` / `logger.py` (persona, filters, prompt, call detection).
- Hourly cron: finalise winners after a day/week/month ends (MYT) and prune periods older than 95 days.

Reply rules: commands answered at once; ketua calling KR gets an immediate Gemini reply; anyone else
tagging/replying to/calling the bot gets an immediate reply (max one per 60 s group-wide, 120 s per person, message under 2 min old); other messages go to a queue answered at most once per 60 s, random 2–5 per 5 min,
one Gemini call per 20 s, per-person cooldown max(120 s, /had), 1200 Gemini calls/day, messages older than 10 min skipped.

## Deploy / migrate

```
export CLOUDFLARE_API_TOKEN=...        # never commit
npx wrangler deploy
npx wrangler secret bulk secrets.json  # TELEGRAM_BOT_TOKEN GEMINI_API_KEY WEBHOOK_SECRET ADMIN_SECRET GROUP_CHAT_ID OWNER_ID OWNER_USERNAME
python3 ../tools/state_store.py load --archive ... --manifest ... --dest /tmp/data   # decrypt bot-state
ADMIN_SECRET=... python3 scripts/migrate.py /tmp/data https://kakireportbot.<sub>.workers.dev --set-update-offset --files
TELEGRAM_BOT_TOKEN=... WEBHOOK_SECRET=... ./scripts/set-webhook.sh https://kakireportbot.<sub>.workers.dev
```

## Tests

`npm test` (unit). With real data (kept outside git): `python3 test/parity_dump.py DATA /tmp/dump.json && PARITY_DUMP=/tmp/dump.json npm test`,
and `ADMIN_SECRET=... python3 test/parity_state.py DATA URL` compares command texts with the Python bot.

## Rollback to GitHub Actions

1. `deleteWebhook` (without `drop_pending_updates`).
2. Export state back if needed: `GET /admin/stats` (Python `stats.json` shape).
3. `gh workflow enable periodic-batch.yml` and `gh variable set PERIODIC_ENABLED --body true`.
