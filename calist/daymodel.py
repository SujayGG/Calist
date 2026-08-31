"""Turning a day into the time that actually exists.

School ending at 14:40 does not mean work begins at 14:41. Anchors carry
travel and settle buffers, and those buffers are subtracted from the day
before a single minute of work is placed. This module owns that arithmetic.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, NamedTuple

from .models import (
    Anchor,
    fmt_clock,
    fmt_time,
    minutes_of,
    parse_time,
    time_from_minutes,
    weekday_key,
)

DAY = 24 * 60


class Window(NamedTuple):
    start: int  # minutes from midnight
    end: int

    @property
    def minutes(self) -> int:
        return self.end - self.start

    def label(self) -> str:
        return f"{fmt_clock(time_from_minutes(self.start))}-{fmt_clock(time_from_minutes(self.end))}"


class Occurrence(NamedTuple):
    anchor: Anchor
    busy: Window   # includes travel + settle
    event: Window  # the thing itself, as it appears on the calendar


def gym_days_for(cfg: dict[str, Any], state: dict[str, Any]) -> set[str]:
    """Which weekdays the gym habit is active on right now.

    The habit ramps: it starts at a few sessions a week and only grows as the
    streak holds. Scheduling five 5am sessions in week one is how the habit dies.
    """
    gym = next((a for a in cfg.get("anchors", []) if a.get("kind") == "gym"), None)
    if not gym:
        return set()
    ramp = gym.get("ramp") or {}
    per_week = int(state.get("gym_sessions_per_week") or ramp.get("start_per_week", 3))
    target = int(ramp.get("target_per_week", per_week))
    per_week = max(0, min(per_week, target, len(gym.get("days", []))))
    return set(gym.get("days", [])[:per_week])


def is_gym_day(d: dt.date, cfg: dict[str, Any], state: dict[str, Any]) -> bool:
    gym = next((a for a in cfg.get("anchors", []) if a.get("kind") == "gym"), None)
    if not gym:
        return False
    return Anchor(**gym).occurs_on(d, gym_days_for(cfg, state))


def day_bounds(d: dt.date, cfg: dict[str, Any], state: dict[str, Any]) -> Window:
    """Waking hours for this date: wake time to that night's cutoff."""
    sleep = cfg.get("sleep", {})
    if is_gym_day(d, cfg, state):
        wake = sleep.get("wake_gym_day", "05:10")
        cutoff = sleep.get("cutoff_gym_day", "22:00")
    else:
        wake = sleep.get("wake_default", "07:00")
        cutoff = sleep.get("cutoff_default", "23:00")
    return Window(minutes_of(parse_time(wake)), minutes_of(parse_time(cutoff)))


def occurrences_for(d: dt.date, cfg: dict[str, Any], state: dict[str, Any]) -> list[Occurrence]:
    gdays = gym_days_for(cfg, state)
    out: list[Occurrence] = []
    for raw in cfg.get("anchors", []):
        anchor = Anchor(**raw)
        if not anchor.occurs_on(d, gdays):
            continue
        bs, be = anchor.busy_span(d)
        es, ee = anchor.event_span(d)
        out.append(Occurrence(anchor, Window(bs, be), Window(es, ee)))
    out.sort(key=lambda o: o.busy.start)
    return out


def subtract(base: list[Window], busy: list[Window]) -> list[Window]:
    """Remove busy spans from a set of free windows."""
    result = list(base)
    for b in busy:
        nxt: list[Window] = []
        for w in result:
            if b.end <= w.start or b.start >= w.end:
                nxt.append(w)
                continue
            if b.start > w.start:
                nxt.append(Window(w.start, b.start))
            if b.end < w.end:
                nxt.append(Window(b.end, w.end))
        result = nxt
    return [w for w in result if w.minutes > 0]


def free_windows(
    d: dt.date,
    cfg: dict[str, Any],
    state: dict[str, Any],
    now_minutes: int | None = None,
    min_minutes: int | None = None,
) -> list[Window]:
    """The windows in which work can genuinely happen on this date.

    now_minutes clips the past away when planning the current day - a plan that
    schedules work at 9am when it is already 4pm is fiction.
    """
    bounds = day_bounds(d, cfg, state)
    start = bounds.start
    if now_minutes is not None:
        start = max(start, now_minutes)
    if start >= bounds.end:
        return []

    windows = [Window(start, bounds.end)]
    busy = [o.busy for o in occurrences_for(d, cfg, state)]
    windows = subtract(windows, busy)

    floor = min_minutes if min_minutes is not None else int(cfg.get("min_block_minutes", 25))
    return [w for w in windows if w.minutes >= floor]


def capacity_minutes(
    d: dt.date, cfg: dict[str, Any], state: dict[str, Any], now_minutes: int | None = None
) -> int:
    return sum(w.minutes for w in free_windows(d, cfg, state, now_minutes))


def describe_day(d: dt.date, cfg: dict[str, Any], state: dict[str, Any]) -> str:
    """A human explanation of where the day went - used by `calist why`."""
    lines = [f"{d.strftime('%A %b %d')}  ({weekday_key(d)})"]
    bounds = day_bounds(d, cfg, state)
    lines.append(f"  awake      {bounds.label()}")
    for occ in occurrences_for(d, cfg, state):
        extra = []
        if occ.anchor.travel_before:
            extra.append(f"-{occ.anchor.travel_before}m travel before")
        if occ.anchor.travel_after:
            extra.append(f"+{occ.anchor.travel_after}m travel after")
        if occ.anchor.settle_after:
            extra.append(f"+{occ.anchor.settle_after}m settle")
        suffix = f"  ({', '.join(extra)})" if extra else ""
        lines.append(f"  anchor     {occ.event.label():<20} {occ.anchor.name}{suffix}")
    total = 0
    for w in free_windows(d, cfg, state):
        lines.append(f"  free       {w.label():<20} {w.minutes} min")
        total += w.minutes
    lines.append(f"  usable     {total} min ({round(total / 60, 1)} h)")
    return "\n".join(lines)
