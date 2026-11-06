"""Secretary — a personal Discord bot for reminders and scheduling."""
import logging
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, available_timezones

import discord
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv

from db import Store, from_iso
from timeparse import day_bounds, next_occurrence, parse_day, parse_when

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("secretary")

TOKEN = os.environ["DISCORD_TOKEN"]
DEFAULT_TZ = os.getenv("DEFAULT_TZ", "Asia/Jerusalem")
# Comma-separated Discord user IDs allowed to use the bot. Empty = anyone.
ALLOWED_USERS = {int(x) for x in os.getenv("ALLOWED_USER_IDS", "").replace(" ", "").split(",") if x}
ALL_TZ = sorted(available_timezones())

EXAMPLES = "Try: `in 20 minutes`, `tomorrow 9am`, `friday 14:30`, `12/11 at 18:00`, `מחר ב-10`"


def ts(dt: datetime, style: str = "F") -> str:
    """Discord timestamp: shows in each viewer's own local time."""
    return f"<t:{int(dt.timestamp())}:{style}>"


class SecretaryTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if ALLOWED_USERS and interaction.user.id not in ALLOWED_USERS:
            await interaction.response.send_message("Sorry, I'm a private secretary. 🙂", ephemeral=True)
            return False
        return True


class Secretary(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())  # slash commands need no privileged intents
        self.tree = SecretaryTree(self)
        self.store = Store()

    async def setup_hook(self):
        await self.tree.sync()
        self.check_due.start()

    async def on_ready(self):
        log.info("Logged in as %s (%s)", self.user, self.user.id)

    def tz_for(self, user_id: int) -> str:
        return self.store.get_tz(user_id, DEFAULT_TZ)

    @tasks.loop(seconds=30)
    async def check_due(self):
        now = datetime.now(timezone.utc)
        for r in self.store.due_reminders(now):
            await self.deliver(r)
            nxt = next_occurrence(from_iso(r["due_utc"]), r["repeat"], self.tz_for(r["user_id"]), now)
            if nxt:
                self.store.reschedule(r["id"], nxt)
            else:
                self.store.delete_reminder(r["id"])

    @check_due.before_loop
    async def before_check(self):
        await self.wait_until_ready()

    async def deliver(self, r):
        msg = f"⏰ **Reminder:** {r['text']}"
        mentions = discord.AllowedMentions(users=True, everyone=False, roles=False)
        try:
            if r["channel_id"]:
                channel = self.get_channel(r["channel_id"]) or await self.fetch_channel(r["channel_id"])
                await channel.send(f"<@{r['user_id']}> {msg}", allowed_mentions=mentions)
                return
        except discord.DiscordException as e:
            log.warning("Channel delivery failed for reminder %s: %s — falling back to DM", r["id"], e)
        try:
            user = await self.fetch_user(r["user_id"])
            await user.send(msg)
        except discord.DiscordException as e:
            log.error("Could not deliver reminder %s: %s", r["id"], e)


bot = Secretary()
dm_or_server = app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)


# ---------------------------------------------------------------- reminders
@bot.tree.command(name="remind", description="Set a reminder")
@dm_or_server
@app_commands.describe(
    when="When? e.g. 'in 2 hours', 'tomorrow 9am', 'sunday 08:30'",
    what="What should I remind you about?",
    repeat="Repeat the reminder",
    where="Send it as a private DM (default) or post it in this channel",
)
@app_commands.choices(
    repeat=[
        app_commands.Choice(name="Once", value="none"),
        app_commands.Choice(name="Every day", value="daily"),
        app_commands.Choice(name="Work days (Sun–Thu)", value="weekdays"),
        app_commands.Choice(name="Every week", value="weekly"),
    ],
    where=[
        app_commands.Choice(name="Private DM", value="dm"),
        app_commands.Choice(name="This channel", value="here"),
    ],
)
async def remind(interaction: discord.Interaction, when: str, what: str,
                 repeat: app_commands.Choice[str] | None = None,
                 where: app_commands.Choice[str] | None = None):
    tz = bot.tz_for(interaction.user.id)
    due = parse_when(when, tz)
    if due is None:
        await interaction.response.send_message(f"I couldn't understand **{when}**. {EXAMPLES}", ephemeral=True)
        return
    if due <= datetime.now(timezone.utc):
        await interaction.response.send_message(f"{ts(due)} is in the past.", ephemeral=True)
        return
    rep = repeat.value if repeat else "none"
    channel_id = interaction.channel_id if (where and where.value == "here" and interaction.guild) else None
    rid = bot.store.add_reminder(interaction.user.id, what, due, rep, channel_id)
    rep_txt = "" if rep == "none" else f" · repeats **{repeat.name.lower()}**"
    await interaction.response.send_message(
        f"✅ Reminder **#{rid}** set for {ts(due)} ({ts(due, 'R')}){rep_txt}\n> {what}", ephemeral=True
    )


@bot.tree.command(name="reminders", description="List your upcoming reminders")
@dm_or_server
async def reminders(interaction: discord.Interaction):
    rows = bot.store.list_reminders(interaction.user.id)
    if not rows:
        await interaction.response.send_message("You have no reminders. Use `/remind` to add one.", ephemeral=True)
        return
    lines = []
    for r in rows[:25]:
        due = from_iso(r["due_utc"])
        rep = "" if r["repeat"] == "none" else f" 🔁 {r['repeat']}"
        lines.append(f"**#{r['id']}** · {ts(due, 'f')} ({ts(due, 'R')}){rep} — {r['text']}")
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


@bot.tree.command(name="cancel", description="Cancel a reminder by its number")
@dm_or_server
@app_commands.describe(reminder_id="The # shown in /reminders")
async def cancel(interaction: discord.Interaction, reminder_id: int):
    ok = bot.store.delete_reminder(reminder_id, interaction.user.id)
    msg = f"🗑️ Reminder #{reminder_id} cancelled." if ok else f"No reminder #{reminder_id} found."
    await interaction.response.send_message(msg, ephemeral=True)


# ---------------------------------------------------------------- schedule
schedule = app_commands.Group(name="schedule", description="Your calendar",
                              allowed_contexts=app_commands.AppCommandContext(guild=True, dm_channel=True, private_channel=True))


@schedule.command(name="add", description="Add an event to your schedule")
@app_commands.describe(
    title="What is it?",
    when="Start time, e.g. 'sunday 10:00'",
    duration_minutes="How long (default 60)",
    notify_before="Minutes before to remind you (default 15, 0 = no reminder)",
)
async def schedule_add(interaction: discord.Interaction, title: str, when: str,
                       duration_minutes: app_commands.Range[int, 5, 24 * 60] = 60,
                       notify_before: app_commands.Range[int, 0, 7 * 24 * 60] = 15):
    uid = interaction.user.id
    tz = bot.tz_for(uid)
    start = parse_when(when, tz)
    if start is None:
        await interaction.response.send_message(f"I couldn't understand **{when}**. {EXAMPLES}", ephemeral=True)
        return
    end = start + timedelta(minutes=duration_minutes)
    clashes = bot.store.events_overlapping(uid, start, end)
    eid = bot.store.add_event(uid, title, start, end)

    note = ""
    alert = start - timedelta(minutes=notify_before)
    if notify_before and alert > datetime.now(timezone.utc):
        bot.store.add_reminder(uid, f"{title} starts {ts(start, 'R')}", alert, event_id=eid)
        note = f"\n🔔 I'll remind you {notify_before} min before."
    if clashes:
        names = ", ".join(f"**{c['title']}** ({ts(from_iso(c['start_utc']), 't')})" for c in clashes)
        note += f"\n⚠️ Overlaps with: {names}"
    await interaction.response.send_message(
        f"📅 Event **#{eid}** · **{title}** · {ts(start)} – {ts(end, 't')}{note}", ephemeral=True
    )


@schedule.command(name="agenda", description="Show your agenda for a day")
@app_commands.describe(day="today (default), tomorrow, friday, 15/11 ...")
async def schedule_agenda(interaction: discord.Interaction, day: str = "today"):
    uid = interaction.user.id
    tz = bot.tz_for(uid)
    d = parse_day(day, tz)
    if d is None:
        await interaction.response.send_message(f"I couldn't understand **{day}**.", ephemeral=True)
        return
    start, end = day_bounds(d, tz)
    events = bot.store.events_overlapping(uid, start, end)
    rems = bot.store.reminders_between(uid, start, end)
    header = f"🗓️ **{d.strftime('%A, %d %B')}**"
    if not events and not rems:
        await interaction.response.send_message(f"{header}\nNothing scheduled — enjoy! ✨", ephemeral=True)
        return
    lines = [header]
    for e in events:
        s, en = from_iso(e["start_utc"]), from_iso(e["end_utc"])
        lines.append(f"• {ts(s, 't')}–{ts(en, 't')} **{e['title']}** `#{e['id']}`")
    for r in rems:
        lines.append(f"• {ts(from_iso(r['due_utc']), 't')} ⏰ {r['text']} `reminder #{r['id']}`")
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


@schedule.command(name="upcoming", description="Your next 10 events")
async def schedule_upcoming(interaction: discord.Interaction):
    rows = bot.store.upcoming_events(interaction.user.id, datetime.now(timezone.utc))
    if not rows:
        await interaction.response.send_message("No upcoming events.", ephemeral=True)
        return
    lines = [f"**#{e['id']}** · {ts(from_iso(e['start_utc']), 'f')} — {e['title']}" for e in rows]
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


@schedule.command(name="cancel", description="Remove an event")
@app_commands.describe(event_id="The # of the event")
async def schedule_cancel(interaction: discord.Interaction, event_id: int):
    ok = bot.store.delete_event(event_id, interaction.user.id)
    msg = f"🗑️ Event #{event_id} removed." if ok else f"No event #{event_id} found."
    await interaction.response.send_message(msg, ephemeral=True)


bot.tree.add_command(schedule)


# ---------------------------------------------------------------- settings
@bot.tree.command(name="timezone", description="Set your timezone (default Asia/Jerusalem)")
@dm_or_server
@app_commands.describe(name="e.g. Asia/Jerusalem, Europe/London, America/New_York")
async def set_timezone(interaction: discord.Interaction, name: str):
    if name not in available_timezones():
        await interaction.response.send_message(f"Unknown timezone **{name}**.", ephemeral=True)
        return
    bot.store.set_tz(interaction.user.id, name)
    local = datetime.now(ZoneInfo(name)).strftime("%H:%M")
    await interaction.response.send_message(f"🌍 Timezone set to **{name}** (it's {local} there).", ephemeral=True)


@set_timezone.autocomplete("name")
async def tz_autocomplete(interaction: discord.Interaction, current: str):
    cur = current.lower()
    return [app_commands.Choice(name=z, value=z) for z in ALL_TZ if cur in z.lower()][:25]


if __name__ == "__main__":
    bot.run(TOKEN, log_handler=None)
