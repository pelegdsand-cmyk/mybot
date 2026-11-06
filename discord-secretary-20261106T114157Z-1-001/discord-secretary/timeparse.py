"""Turning human text ("tomorrow at 9", "in 20 minutes", "מחר ב-10") into datetimes."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

LANGUAGES = ["en", "he"]


def parse_when(text: str, tz_name: str, now: datetime | None = None) -> datetime | None:
    """Return an aware UTC datetime for `text` in the user's timezone, or None."""
    import dateparser
    from dateparser.search import search_dates

    tz = ZoneInfo(tz_name)
    now = now or datetime.now(timezone.utc)
    local_now = now.astimezone(tz).replace(tzinfo=None)
    settings = {
        "TIMEZONE": tz_name,
        "TO_TIMEZONE": "UTC",
        "RETURN_AS_TIMEZONE_AWARE": True,
        "PREFER_DATES_FROM": "future",
        "RELATIVE_BASE": local_now,
    }

    cleaned = text.strip()
    dt = dateparser.parse(cleaned, languages=LANGUAGES, settings=settings)
    if dt is None and cleaned.lower().startswith("next "):
        dt = dateparser.parse(cleaned[5:], languages=LANGUAGES, settings=settings)
    if dt is None:
        found = search_dates(cleaned, languages=LANGUAGES, settings=settings)
        if found:
            dt = found[-1][1]
    if dt is None:
        return None

    dt = dt.astimezone(timezone.utc)
    # A bare time like "9am" that already passed today means tomorrow.
    if dt <= now and now - dt < timedelta(days=1):
        dt += timedelta(days=1)
    return dt


def parse_day(text: str, tz_name: str, now: datetime | None = None) -> date | None:
    """Return the local calendar date for text like "today", "tomorrow", "friday", "12/11"."""
    import dateparser

    tz = ZoneInfo(tz_name)
    now = now or datetime.now(timezone.utc)
    settings = {
        "TIMEZONE": tz_name,
        "RETURN_AS_TIMEZONE_AWARE": False,
        "PREFER_DATES_FROM": "future",
        "RELATIVE_BASE": now.astimezone(tz).replace(tzinfo=None),
        "DATE_ORDER": "DMY",
    }
    dt = dateparser.parse(text.strip(), languages=LANGUAGES, settings=settings)
    return dt.date() if dt else None


def day_bounds(day: date, tz_name: str) -> tuple[datetime, datetime]:
    """Local midnight-to-midnight for `day`, returned as UTC datetimes."""
    tz = ZoneInfo(tz_name)
    start = datetime(day.year, day.month, day.day, tzinfo=tz)
    end = start + timedelta(days=1)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def next_occurrence(due: datetime, repeat: str, tz_name: str, now: datetime) -> datetime | None:
    """Next due time for a repeating reminder, keeping the same local clock time
    across daylight-saving changes and skipping anything missed while offline."""
    if repeat == "none":
        return None
    tz = ZoneInfo(tz_name)
    local = due.astimezone(tz).replace(tzinfo=None)
    while True:
        if repeat == "daily":
            local += timedelta(days=1)
        elif repeat == "weekly":
            local += timedelta(weeks=1)
        elif repeat == "weekdays":  # Sunday–Thursday or Monday–Friday, see WORKWEEK
            local += timedelta(days=1)
            while local.weekday() not in WORKWEEK:
                local += timedelta(days=1)
        else:
            return None
        candidate = local.replace(tzinfo=tz).astimezone(timezone.utc)
        if candidate > now:
            return candidate


# Python weekday(): Monday=0 ... Sunday=6. Israeli work week: Sunday–Thursday.
WORKWEEK = {6, 0, 1, 2, 3}
