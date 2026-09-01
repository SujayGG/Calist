"""Sujay's local date and time, computed from UTC.

The planner runs both on his machine (US Central) and in a UTC container, and a
plan generated an hour after midnight UTC would otherwise be a whole day ahead
of him. Deriving local time from UTC plus a configured offset gives the same
answer on any machine.

zoneinfo would need the `tzdata` package on Windows, which would break the
zero-dependency rule, so the US daylight-saving rule is applied directly:
DST runs from the second Sunday in March to the first Sunday in November.
"""

from __future__ import annotations

import datetime as dt
from typing import Any


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    """n-th occurrence of a weekday in a month (weekday: Mon=0 .. Sun=6)."""
    first = dt.date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + dt.timedelta(days=offset + 7 * (n - 1))


def us_dst_active(when: dt.datetime) -> bool:
    """True during US daylight saving for the given local-standard datetime."""
    start = _nth_weekday(when.year, 3, 6, 2)   # 2nd Sunday in March
    end = _nth_weekday(when.year, 11, 6, 1)    # 1st Sunday in November
    start_at = dt.datetime.combine(start, dt.time(2, 0))
    end_at = dt.datetime.combine(end, dt.time(2, 0))
    return start_at <= when < end_at


def offset_hours(cfg: dict[str, Any]) -> float:
    tz = cfg.get("timezone", {}) or {}
    base = float(tz.get("standard_offset_hours", -6))
    if not tz.get("us_dst", True):
        return base
    standard_now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(hours=base)
    return base + (1.0 if us_dst_active(standard_now) else 0.0)


def now(cfg: dict[str, Any]) -> dt.datetime:
    """Naive local datetime, correct regardless of the host machine's clock."""
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(
        hours=offset_hours(cfg)
    )


def today(cfg: dict[str, Any]) -> dt.date:
    return now(cfg).date()


def minutes_now(cfg: dict[str, Any]) -> int:
    t = now(cfg)
    return t.hour * 60 + t.minute
