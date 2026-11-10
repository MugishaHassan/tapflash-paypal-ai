"""Opening hours such as "6:00 AM – 9:00 PM", "06:00-21:00" or "24 hours"."""
import re
from datetime import timedelta
from datetime import timezone as dt_timezone
from typing import NamedTuple
from zoneinfo import ZoneInfo

from .geo import walking_minutes

DAY_MINUTES = 24 * 60

CLOCK_TIME = r'([0-9]{1,2})(?:[:.]([0-9]{2}))?\s*(am|pm)?'
# Between the two times: an en dash, an em dash, a hyphen or "to".
HOURS_RANGE = re.compile(rf'{CLOCK_TIME}\s*(?:\u2013|\u2014|-|to)\s*{CLOCK_TIME}')
ALWAYS_OPEN = re.compile(r'(open )?24 ?(hours|hrs|h)|24/7')


class Span(NamedTuple):
    """Opening and closing time, in minutes after midnight."""
    opens: int
    closes: int


def parse_hours(hours):
    """The Span of opening hours, or None when they can't be read."""
    value = (hours or '').strip().lower()
    if ALWAYS_OPEN.fullmatch(value):
        return Span(0, DAY_MINUTES)
    match = HOURS_RANGE.fullmatch(value)
    if not match:
        return None
    opens = _minutes_after_midnight(*match.group(1, 2, 3))
    closes = _minutes_after_midnight(*match.group(4, 5, 6))
    return None if opens is None or closes is None else Span(opens, closes)


def _minutes_after_midnight(hour, minute, half):
    h = int(hour)
    m = int(minute or 0)
    if m > 59:
        return None
    if half:
        if not 1 <= h <= 12:
            return None
        h = h % 12 + (12 if half == 'pm' else 0)
    elif h > 24:
        return None
    return h * 60 + m


def minute_of_day(when, timezone):
    """The time of day in the bot's time zone, as minutes after midnight."""
    local = when.astimezone(ZoneInfo(timezone))
    return local.hour * 60 + local.minute


def is_open_at(hours, when, timezone):
    """Is a restroom open at `when`, in the bot's time zone?

    Hours that are missing or can't be read count as open.
    "6:00 PM – 2:00 AM" runs past midnight.
    """
    span = parse_hours(hours)
    if not span or span.opens == span.closes:
        return True
    now = minute_of_day(when, timezone)
    if span.opens < span.closes:
        return span.opens <= now < span.closes
    return now >= span.opens or now < span.closes


def next_closing(hours, when, timezone):
    """The next time a restroom closes after `when`.

    None when it never closes ("24 hours", or hours that can't be read).
    """
    span = parse_hours(hours)
    if not span or span.opens % DAY_MINUTES == span.closes % DAY_MINUTES:
        return None
    minutes_left = (span.closes - minute_of_day(when, timezone)) % DAY_MINUTES or DAY_MINUTES
    start_of_minute = when.astimezone(dt_timezone.utc).replace(second=0, microsecond=0)
    return start_of_minute + timedelta(minutes=minutes_left)


def is_open_on_arrival(facility, now, timezone):
    """Will this restroom (with its distance_km) still be open when the user gets there on foot?"""
    arrival = now + timedelta(minutes=walking_minutes(facility.distance_km))
    return is_open_at(facility.hours, arrival, timezone)
