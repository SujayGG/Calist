"""Core data types.

All datetimes are NAIVE LOCAL. We deliberately avoid zoneinfo because it
requires the `tzdata` package on Windows, and this project is stdlib-only.
For a single-user personal calendar, local-floating time is the correct model.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field, asdict
from typing import Any

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

# Tier 1 work is done before tier 2, which is done before tier 3.
TIER_SCHOOLWORK = 1
TIER_ESSAY = 2
TIER_BUILD = 3

KIND_TIERS = {
    "schoolwork": TIER_SCHOOLWORK,
    "test": TIER_SCHOOLWORK,
    "essay": TIER_ESSAY,
    "build": TIER_BUILD,
    "admin": TIER_SCHOOLWORK,
}


def parse_date(value: Any) -> dt.date | None:
    if value in (None, ""):
        return None
    if isinstance(value, dt.date) and not isinstance(value, dt.datetime):
        return value
    if isinstance(value, dt.datetime):
        return value.date()
    return dt.date.fromisoformat(str(value)[:10])


def parse_time(value: Any) -> dt.time:
    """Accept 'HH:MM', 'H:MM', 'HHMM' and '240'-style shorthand."""
    if isinstance(value, dt.time):
        return value
    s = str(value).strip()
    if ":" in s:
        hh, mm = s.split(":", 1)
        return dt.time(int(hh), int(mm))
    if len(s) in (3, 4) and s.isdigit():
        return dt.time(int(s[:-2]), int(s[-2:]))
    return dt.time(int(s), 0)


def fmt_time(t: dt.time) -> str:
    return f"{t.hour:02d}:{t.minute:02d}"


def fmt_clock(t: dt.time) -> str:
    """Human 12-hour clock, e.g. 3:15pm."""
    hour = t.hour % 12 or 12
    suffix = "am" if t.hour < 12 else "pm"
    return f"{hour}:{t.minute:02d}{suffix}"


def minutes_of(t: dt.time) -> int:
    return t.hour * 60 + t.minute


def time_from_minutes(total: int) -> dt.time:
    total = max(0, min(24 * 60 - 1, total))
    return dt.time(total // 60, total % 60)


def weekday_key(d: dt.date) -> str:
    return WEEKDAYS[d.weekday()]


@dataclass
class Anchor:
    """A fixed commitment in the day, with the real-world friction around it.

    travel_before / travel_after / settle_after are what make a schedule
    physically honest: school ending at 14:40 does not mean work at 14:41.
    """

    id: str
    name: str
    kind: str = "custom"
    days: list[str] = field(default_factory=list)
    dates: list[str] = field(default_factory=list)
    skip_dates: list[str] = field(default_factory=list)
    start: str = "09:00"
    end: str = "10:00"
    travel_before: int = 0
    travel_after: int = 0
    settle_after: int = 0
    protected: bool = True
    habit: bool = False
    ramp: dict[str, Any] = field(default_factory=dict)
    note: str = ""

    def occurs_on(self, d: dt.date, habit_days: set[str] | None = None) -> bool:
        iso = d.isoformat()
        if iso in self.skip_dates:
            return False
        if self.dates:
            return iso in self.dates
        key = weekday_key(d)
        if key not in self.days:
            return False
        if self.habit and habit_days is not None:
            return key in habit_days
        return True

    def busy_span(self, d: dt.date) -> tuple[int, int]:
        """Minutes-from-midnight span this anchor consumes, buffers included."""
        start = minutes_of(parse_time(self.start)) - self.travel_before
        end = minutes_of(parse_time(self.end)) + self.travel_after + self.settle_after
        return max(0, start), min(24 * 60, end)

    def event_span(self, d: dt.date) -> tuple[int, int]:
        """The anchor itself, without buffers - what shows on the calendar."""
        return minutes_of(parse_time(self.start)), minutes_of(parse_time(self.end))


@dataclass
class Stage:
    """One unit of work on a task.

    awaits_days models EXTERNAL latency after this stage completes - the essay
    coach turnaround. The next stage cannot start until that many days pass.
    gap_days_after is self-imposed spacing (do not draft and revise same day).
    """

    name: str
    minutes: int
    status: str = "todo"
    gap_days_after: int = 0
    awaits_days: int = 0
    # How near the due date this stage may start. Spaced review is only useful
    # anchored to the test: a cram session ten days early is not a cram session.
    start_within_days: int | None = None
    done_date: str | None = None
    actual_minutes: int | None = None

    @property
    def done(self) -> bool:
        return self.status == "done"


@dataclass
class Task:
    id: str
    title: str
    kind: str = "essay"
    school: str = ""
    due: str | None = None
    estimate_minutes: int = 120
    stages: list[Stage] = field(default_factory=list)
    priority: int = 0
    notes: str = ""
    source: str = ""
    created: str = ""

    @property
    def tier(self) -> int:
        return KIND_TIERS.get(self.kind, TIER_ESSAY)

    @property
    def due_date(self) -> dt.date | None:
        return parse_date(self.due)

    @property
    def done(self) -> bool:
        return bool(self.stages) and all(s.done for s in self.stages)

    @property
    def remaining_minutes(self) -> int:
        return sum(s.minutes for s in self.stages if not s.done)

    def next_stage_index(self) -> int | None:
        for i, s in enumerate(self.stages):
            if not s.done:
                return i
        return None


@dataclass
class Chunk:
    """A stage of a task, resolved into a schedulable unit of work."""

    task_id: str
    task_title: str
    stage_index: int
    stage_name: str
    minutes: int
    kind: str
    tier: int
    school: str = ""
    due: str | None = None
    latest_date: str | None = None

    @property
    def id(self) -> str:
        return f"{self.task_id}#{self.stage_index}"


@dataclass
class Block:
    """A scheduled span of time on a specific day."""

    date: str
    start: str
    end: str
    title: str
    type: str = "work"  # work | anchor | gym | build | travel
    chunk_id: str | None = None
    task_id: str | None = None
    stage_name: str | None = None
    kind: str = ""
    note: str = ""

    @property
    def start_minutes(self) -> int:
        return minutes_of(parse_time(self.start))

    @property
    def end_minutes(self) -> int:
        return minutes_of(parse_time(self.end))

    @property
    def duration(self) -> int:
        return self.end_minutes - self.start_minutes


def to_jsonable(obj: Any) -> Any:
    if hasattr(obj, "__dataclass_fields__"):
        return {k: to_jsonable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, list):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (dt.date, dt.datetime)):
        return obj.isoformat()
    if isinstance(obj, dt.time):
        return fmt_time(obj)
    return obj
