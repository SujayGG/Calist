"""Turning phone usage and completion history into scheduling inputs.

Two sources, both optional and both free:
  * usage.jsonl  - app open/close events posted by the phone, or manual
                   totals you tell Claude at check-in.
  * log.jsonl    - what you actually completed, skipped or snoozed.

Output is three concrete things the planner and the nudge watcher consume:
awake hours, follow-through by hour, and the hours you are most likely to
lose to a feed.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from typing import Any

from .store import LOG_PATH, USAGE_PATH, read_jsonl

SOCIAL_HINTS = ("instagram", "tiktok", "youtube", "whatsapp", "reddit", "twitter", "snapchat", "x.com")


def _parse_ts(value: Any) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", ""))
    except ValueError:
        return None


def is_social(app: str) -> bool:
    low = (app or "").lower()
    return any(h in low for h in SOCIAL_HINTS)


def usage_minutes_by_hour(records: list[dict[str, Any]] | None = None) -> dict[int, float]:
    """Minutes of social-app use per hour of day, averaged over observed days.

    Handles both event pairs (open/close) and manual aggregate entries.
    """
    records = read_jsonl(USAGE_PATH) if records is None else records
    per_day_hour: dict[tuple[str, int], float] = defaultdict(float)
    days: set[str] = set()
    open_at: dict[str, dt.datetime] = {}

    for rec in records:
        app = rec.get("app", "")
        if not is_social(app):
            continue
        event = rec.get("event")

        if event in ("open", "close"):
            ts = _parse_ts(rec.get("ts"))
            if not ts:
                continue
            days.add(ts.date().isoformat())
            if event == "open":
                open_at[app] = ts
            else:
                start = open_at.pop(app, None)
                if not start or ts <= start:
                    continue
                # Spread the session across the hours it actually spans.
                cursor = start
                while cursor < ts:
                    hour_end = (cursor + dt.timedelta(hours=1)).replace(
                        minute=0, second=0, microsecond=0
                    )
                    chunk_end = min(ts, hour_end)
                    mins = (chunk_end - cursor).total_seconds() / 60
                    per_day_hour[(cursor.date().isoformat(), cursor.hour)] += mins
                    cursor = chunk_end
        elif rec.get("minutes"):
            # Manual entry: "3 hours of Instagram yesterday, mostly at night".
            day = str(rec.get("date") or rec.get("ts", ""))[:10]
            if not day:
                continue
            days.add(day)
            hours = rec.get("hours")
            minutes = float(rec["minutes"])
            if isinstance(hours, list) and hours:
                for h in hours:
                    per_day_hour[(day, int(h))] += minutes / len(hours)
            else:
                for h in range(19, 23):  # unattributed time defaults to the evening
                    per_day_hour[(day, h)] += minutes / 4

    if not days:
        return {}
    totals: dict[int, float] = defaultdict(float)
    for (_, hour), mins in per_day_hour.items():
        totals[hour] += mins
    return {h: round(v / len(days), 1) for h, v in sorted(totals.items())}


def risk_hours(threshold_minutes: float = 20.0) -> list[int]:
    """Hours where a feed usually wins. The watcher nudges harder here."""
    by_hour = usage_minutes_by_hour()
    return [h for h, mins in by_hour.items() if mins >= threshold_minutes]


def awake_window() -> tuple[int, int] | None:
    """First and last hour of observed phone activity, as a sanity check."""
    records = read_jsonl(USAGE_PATH)
    hours = [ts.hour for ts in (_parse_ts(r.get("ts")) for r in records) if ts]
    if len(hours) < 10:
        return None
    return min(hours), max(hours)


def follow_through_by_hour(records: list[dict[str, Any]] | None = None) -> dict[int, float]:
    """How often you actually finish work scheduled at each hour.

    Fed straight into the planner: high-value work goes where you genuinely
    follow through, not where a template guesses you should.
    """
    records = read_jsonl(LOG_PATH) if records is None else records
    done: dict[int, int] = defaultdict(int)
    missed: dict[int, int] = defaultdict(int)

    for rec in records:
        hour = rec.get("scheduled_hour")
        if hour is None:
            ts = _parse_ts(rec.get("ts"))
            hour = ts.hour if ts else None
        if hour is None:
            continue
        hour = int(hour)
        if rec.get("type") == "done":
            done[hour] += 1
        elif rec.get("type") in ("skip", "miss"):
            missed[hour] += 1

    scores: dict[int, float] = {}
    for hour in set(done) | set(missed):
        total = done[hour] + missed[hour]
        if total >= 3:  # ignore noise until there is real signal
            scores[hour] = round(done[hour] / total, 2)
    return scores


def summary() -> dict[str, Any]:
    usage = usage_minutes_by_hour()
    scores = follow_through_by_hour()
    worst = sorted(usage.items(), key=lambda kv: -kv[1])[:3]
    best = sorted(scores.items(), key=lambda kv: -kv[1])[:3]
    return {
        "social_minutes_per_day": round(sum(usage.values()), 1),
        "risk_hours": risk_hours(),
        "worst_hours": [{"hour": h, "minutes": m} for h, m in worst],
        "best_work_hours": [{"hour": h, "rate": r} for h, r in best],
        "follow_through": scores,
        "awake_window": awake_window(),
        "has_usage_data": bool(usage),
    }
