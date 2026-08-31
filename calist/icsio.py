"""ICS import and export - a minimal, dependency-free RFC 5545 subset.

Export: the schedule as a calendar you subscribe to once in Google Calendar,
free, no OAuth. Times are FLOATING (no TZID, no Z) so they display as local
wherever you read them - correct for a personal single-timezone calendar.

Import: pull school/club calendars and deadlines out of .ics files.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from pathlib import Path
from typing import Any, Iterable

from .models import Block, minutes_of, parse_time

CRLF = "\r\n"


def _escape(text: str) -> str:
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace(";", "\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _unescape(text: str) -> str:
    out, i = [], 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            out.append({"n": "\n", "N": "\n"}.get(nxt, nxt))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _fold(line: str) -> str:
    """RFC 5545 caps content lines at 75 octets."""
    if len(line) <= 75:
        return line
    chunks = [line[:75]]
    rest = line[75:]
    while rest:
        chunks.append(" " + rest[:74])
        rest = rest[74:]
    return CRLF.join(chunks)


def _stamp(value: dt.datetime) -> str:
    return value.strftime("%Y%m%dT%H%M%S")


def _uid(block: Block) -> str:
    seed = f"{block.date}{block.start}{block.title}"
    return f"{hashlib.sha1(seed.encode()).hexdigest()[:20]}@calist.local"


def write_ics(blocks: Iterable[Block], path: Path, name: str = "Calist") -> Path:
    now = dt.datetime.now()
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Calist//Personal Planner//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape(name)}",
        "X-PUBLISHED-TTL:PT1H",
    ]
    for block in blocks:
        day = dt.date.fromisoformat(block.date)
        start = dt.datetime.combine(day, parse_time(block.start))
        end = dt.datetime.combine(day, parse_time(block.end))
        lines += [
            "BEGIN:VEVENT",
            f"UID:{_uid(block)}",
            f"DTSTAMP:{_stamp(now)}",
            f"DTSTART:{_stamp(start)}",
            f"DTEND:{_stamp(end)}",
            f"SUMMARY:{_escape(block.title)}",
        ]
        if block.note:
            lines.append(f"DESCRIPTION:{_escape(block.note)}")
        lines.append(f"CATEGORIES:{_escape(block.type)}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(CRLF.join(_fold(l) for l in lines) + CRLF, encoding="utf-8", newline="")
    return path


def _unfold(text: str) -> list[str]:
    raw = text.replace("\r\n", "\n").split("\n")
    lines: list[str] = []
    for line in raw:
        if line[:1] in (" ", "\t") and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines


def _parse_dt(value: str) -> tuple[dt.datetime | None, bool]:
    """Returns (datetime, is_all_day). Z-suffixed UTC is converted to local."""
    v = value.strip()
    if re.fullmatch(r"\d{8}", v):
        return dt.datetime.strptime(v, "%Y%m%d"), True
    m = re.fullmatch(r"(\d{8}T\d{6})(Z?)", v)
    if not m:
        return None, False
    stamp = dt.datetime.strptime(m.group(1), "%Y%m%dT%H%M%S")
    if m.group(2) == "Z":
        offset = dt.datetime.now().astimezone().utcoffset() or dt.timedelta(0)
        stamp = stamp + offset
    return stamp, False


def read_ics(path: Path) -> list[dict[str, Any]]:
    """Extract VEVENTs as plain dicts. Unknown properties are ignored."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    events: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for line in _unfold(text):
        stripped = line.strip()
        if stripped == "BEGIN:VEVENT":
            current = {}
            continue
        if stripped == "END:VEVENT":
            if current is not None:
                events.append(current)
            current = None
            continue
        if current is None or ":" not in stripped:
            continue

        name, _, value = stripped.partition(":")
        key = name.split(";")[0].upper()
        if key == "SUMMARY":
            current["summary"] = _unescape(value)
        elif key == "DESCRIPTION":
            current["description"] = _unescape(value)
        elif key == "LOCATION":
            current["location"] = _unescape(value)
        elif key in ("DTSTART", "DTEND"):
            parsed, all_day = _parse_dt(value)
            if parsed:
                current[key.lower()] = parsed
                current["all_day"] = all_day
        elif key == "RRULE":
            current["rrule"] = value
        elif key == "UID":
            current["uid"] = value

    return [e for e in events if e.get("dtstart")]


def events_to_anchor_specs(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn recurring weekly ICS events into anchor definitions."""
    from .models import WEEKDAYS, fmt_time

    specs = []
    for ev in events:
        start = ev.get("dtstart")
        end = ev.get("dtend")
        if not start or not end or ev.get("all_day"):
            continue
        rrule = ev.get("rrule", "")
        days: list[str] = []
        if "FREQ=WEEKLY" in rrule.upper():
            m = re.search(r"BYDAY=([^;]+)", rrule.upper())
            mapping = {"MO": "mon", "TU": "tue", "WE": "wed", "TH": "thu",
                       "FR": "fri", "SA": "sat", "SU": "sun"}
            if m:
                days = [mapping[d[-2:]] for d in m.group(1).split(",") if d[-2:] in mapping]
            else:
                days = [WEEKDAYS[start.weekday()]]
        spec = {
            "name": ev.get("summary", "Imported event"),
            "kind": "custom",
            "start": fmt_time(start.time()),
            "end": fmt_time(end.time()),
        }
        if days:
            spec["days"] = days
        else:
            spec["dates"] = [start.date().isoformat()]
        specs.append(spec)
    return specs
