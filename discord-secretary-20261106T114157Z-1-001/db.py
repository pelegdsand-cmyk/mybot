"""SQLite storage for reminders, events and per-user settings.

All times are stored as UTC ISO strings ("2026-11-08T07:00:00+00:00"),
which sort correctly as text.
"""
import os
import sqlite3
import threading
from datetime import datetime, timezone

DB_PATH = os.getenv("DB_PATH", "secretary.db")


def to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def from_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


class Store:
    def __init__(self, path: str = DB_PATH):
        folder = os.path.dirname(path)
        if folder:
            try:
                os.makedirs(folder, exist_ok=True)
            except OSError:
                fallback = os.path.basename(path) or "secretary.db"
                print(
                    f"WARNING: can't write to {folder!r} (is the disk mounted there?). "
                    f"Using {fallback!r} instead; reminders will be lost on redeploy.",
                    flush=True,
                )
                path = fallback
        print(f"Database: {os.path.abspath(path)}", flush=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        self._init()

    def _init(self):
        with self.lock, self.conn:
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    tz      TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id     INTEGER NOT NULL,
                    title       TEXT NOT NULL,
                    start_utc   TEXT NOT NULL,
                    end_utc     TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reminders (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id     INTEGER NOT NULL,
                    channel_id  INTEGER,            -- NULL = send as DM
                    text        TEXT NOT NULL,
                    due_utc     TEXT NOT NULL,
                    repeat      TEXT NOT NULL DEFAULT 'none',
                    event_id    INTEGER REFERENCES events(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_rem_due ON reminders(due_utc);
                CREATE INDEX IF NOT EXISTS idx_evt_user ON events(user_id, start_utc);
                PRAGMA foreign_keys = ON;
                """
            )

    # ---------- users ----------
    def get_tz(self, user_id: int, default: str) -> str:
        row = self.conn.execute("SELECT tz FROM users WHERE user_id=?", (user_id,)).fetchone()
        return row["tz"] if row else default

    def set_tz(self, user_id: int, tz: str):
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO users(user_id, tz) VALUES(?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET tz=excluded.tz",
                (user_id, tz),
            )

    # ---------- reminders ----------
    def add_reminder(self, user_id, text, due: datetime, repeat="none", channel_id=None, event_id=None) -> int:
        with self.lock, self.conn:
            cur = self.conn.execute(
                "INSERT INTO reminders(user_id, channel_id, text, due_utc, repeat, event_id) VALUES(?,?,?,?,?,?)",
                (user_id, channel_id, text, to_iso(due), repeat, event_id),
            )
            return cur.lastrowid

    def list_reminders(self, user_id):
        return self.conn.execute(
            "SELECT * FROM reminders WHERE user_id=? ORDER BY due_utc", (user_id,)
        ).fetchall()

    def reminders_between(self, user_id, start: datetime, end: datetime):
        return self.conn.execute(
            "SELECT * FROM reminders WHERE user_id=? AND event_id IS NULL "
            "AND due_utc>=? AND due_utc<? ORDER BY due_utc",
            (user_id, to_iso(start), to_iso(end)),
        ).fetchall()

    def due_reminders(self, now: datetime):
        return self.conn.execute(
            "SELECT * FROM reminders WHERE due_utc<=? ORDER BY due_utc", (to_iso(now),)
        ).fetchall()

    def reschedule(self, reminder_id, due: datetime):
        with self.lock, self.conn:
            self.conn.execute("UPDATE reminders SET due_utc=? WHERE id=?", (to_iso(due), reminder_id))

    def delete_reminder(self, reminder_id, user_id=None) -> bool:
        with self.lock, self.conn:
            if user_id is None:
                cur = self.conn.execute("DELETE FROM reminders WHERE id=?", (reminder_id,))
            else:
                cur = self.conn.execute("DELETE FROM reminders WHERE id=? AND user_id=?", (reminder_id, user_id))
            return cur.rowcount > 0

    # ---------- events ----------
    def add_event(self, user_id, title, start: datetime, end: datetime) -> int:
        with self.lock, self.conn:
            cur = self.conn.execute(
                "INSERT INTO events(user_id, title, start_utc, end_utc) VALUES(?,?,?,?)",
                (user_id, title, to_iso(start), to_iso(end)),
            )
            return cur.lastrowid

    def events_overlapping(self, user_id, start: datetime, end: datetime):
        return self.conn.execute(
            "SELECT * FROM events WHERE user_id=? AND start_utc<? AND end_utc>? ORDER BY start_utc",
            (user_id, to_iso(end), to_iso(start)),
        ).fetchall()

    def upcoming_events(self, user_id, now: datetime, limit=10):
        return self.conn.execute(
            "SELECT * FROM events WHERE user_id=? AND end_utc>? ORDER BY start_utc LIMIT ?",
            (user_id, to_iso(now), limit),
        ).fetchall()

    def delete_event(self, event_id, user_id) -> bool:
        with self.lock, self.conn:
            self.conn.execute("DELETE FROM reminders WHERE event_id=? AND user_id=?", (event_id, user_id))
            cur = self.conn.execute("DELETE FROM events WHERE id=? AND user_id=?", (event_id, user_id))
            return cur.rowcount > 0
