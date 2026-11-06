# Secretary 🗂️ — a personal Discord bot for reminders and scheduling

## What it does

| Command | Example |
|---|---|
| `/remind` | `when: tomorrow 9am` · `what: call the mechanic` · repeat: once / daily / work days / weekly |
| `/reminders` | List everything pending |
| `/cancel` | `reminder_id: 4` |
| `/schedule add` | `title: Dentist` · `when: sunday 10:00` · `duration_minutes: 45` · `notify_before: 30` |
| `/schedule agenda` | `day: tomorrow` → that day's events and reminders |
| `/schedule upcoming` | Next 10 events |
| `/schedule cancel` | `event_id: 2` |
| `/timezone` | `name: Asia/Jerusalem` (default) |

- Understands English and Hebrew times: `in 20 minutes`, `friday 14:30`, `12/11 at 18:00`, `מחר ב-10`.
- Replies are private (only you see them). Reminders arrive by DM, or in a channel if you pick "This channel".
- Warns you when a new event overlaps an existing one.
- Set `ALLOWED_USER_IDS` so only you can use it.

## 1. Discord setup

1. discord.com/developers/applications → **New Application** → name it "Secretary".
2. **Bot** tab → **Reset Token** → copy it. No privileged intents are needed.
3. **OAuth2 → URL Generator** → scopes `bot` + `applications.commands`; permissions **Send Messages** + **Embed Links**. Open the link and add the bot to your server.
4. In Discord: Settings → Advanced → **Developer Mode** on. Right-click your name → **Copy User ID**.

## 2. Run it on your computer first

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # then paste your token and user ID into .env
python bot.py
```

When you see `Logged in as Secretary`, type `/remind` in your server. Slash commands can take a minute to appear the first time.

## 3. Deploy to Render

1. Push this folder to a new **private** GitHub repo (`.env` is ignored, so your token stays local).
2. Render dashboard → **New → Blueprint** → pick the repo. It reads `render.yaml` and creates:
   - a **Background Worker** on the Starter plan (always on; Render has no free workers), and
   - a **1 GB disk** at `/var/data` so reminders survive restarts and deploys.
3. When asked, paste `DISCORD_TOKEN` and `ALLOWED_USER_IDS`.
4. Stop the local copy once Render shows `Logged in as ...` in its logs. Two copies running means double reminders.

Every `git push` to the main branch redeploys automatically.

## Files

- `bot.py` — Discord commands and the 30-second reminder loop
- `db.py` — SQLite storage (reminders, events, timezones)
- `timeparse.py` — natural-language time parsing and repeat logic
- `render.yaml` — Render deployment blueprint

## Ideas for next steps

- Morning briefing: DM the day's agenda every day at 07:30
- "Snooze 10 min" button on each reminder
- Google Calendar sync
- Talk to it in plain language (e.g. "remind me to call mom friday") via the Claude API
