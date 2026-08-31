"""Loading, saving and validating the on-disk store.

Everything lives in plain JSON/JSONL under data/ so that Claude can read and
edit it directly with ordinary file tools. plan.json is GENERATED - never
hand-edit it; re-run `plan` instead.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from .models import Anchor, Stage, Task, minutes_of, parse_time

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

CONFIG_PATH = DATA / "config.json"
TASKS_PATH = DATA / "tasks.json"
PLAN_PATH = DATA / "plan.json"
LOG_PATH = DATA / "log.jsonl"
USAGE_PATH = DATA / "usage.jsonl"
PROFILE_PATH = DATA / "profile.md"
STATE_PATH = DATA / "state.json"
ICS_PATH = DATA / "plan.ics"

DEFAULT_CONFIG: dict[str, Any] = {
    "owner": "Sujay",
    "target_date": "2026-09-22",
    "horizon_days": 130,
    "buffer_days": 2,
    "min_block_minutes": 25,
    "max_block_minutes": 90,
    "block_gap_minutes": 10,
    "essay_floor_minutes_per_day": 60,
    "coach_latency_days": 1,
    "coach_capacity_per_day": 2,
    "start_within_days": {"test": 12},
    "cadence_overrides": [],
    "blackouts": [],
    "sleep": {
        "cutoff_default": "23:00",
        "cutoff_gym_day": "22:00",
        "wake_default": "07:00",
        "wake_gym_day": "05:10",
        "min_hours_warn": 7.0,
    },
    "anchors": [
        {
            "id": "school",
            "name": "School",
            "kind": "school",
            "days": ["mon", "tue", "wed", "thu", "fri"],
            "start": "09:00",
            "end": "14:40",
            "travel_before": 20,
            "travel_after": 17,
            "settle_after": 15,
            "note": "walk to car + 17 min drive + settle before work starts",
        },
        {
            "id": "gym",
            "name": "Gym",
            "kind": "gym",
            "days": ["mon", "tue", "wed", "thu", "fri"],
            "start": "05:30",
            "end": "06:30",
            "travel_before": 10,
            "travel_after": 15,
            "habit": True,
            "ramp": {"start_per_week": 3, "target_per_week": 5, "step_every_days": 14},
            "note": "new habit - ramps up as the streak holds",
        },
        {
            "id": "dinner",
            "name": "Dinner / family",
            "kind": "meal",
            "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            "start": "18:30",
            "end": "19:15",
        },
        {
            "id": "call",
            "name": "Call girlfriend",
            "kind": "call",
            "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            "start": "21:00",
            "end": "21:45",
        },
    ],
    "cadence": {"draft": 1},
    "creative": {
        "min_sessions_per_week": 2,
        "session_minutes": 60,
        "requires_day_complete": True,
        "preferred_days": ["fri", "sat", "sun"],
    },
    "stage_templates": {
        "essay": [
            {"name": "draft", "weight": 0.40, "awaits_days": 1},
            {"name": "revise-1", "weight": 0.25, "awaits_days": 1},
            {"name": "revise-2", "weight": 0.20, "awaits_days": 1},
            {"name": "final", "weight": 0.15},
        ],
        "schoolwork": [{"name": "work", "weight": 1.0}],
        "admin": [{"name": "do", "weight": 1.0}],
        "build": [{"name": "build", "weight": 1.0}],
        "test": [
            {"name": "review-1", "weight": 0.2, "gap_days_after": 2, "start_within_days": 12},
            {"name": "review-2", "weight": 0.2, "gap_days_after": 2, "start_within_days": 8},
            {"name": "review-3", "weight": 0.25, "gap_days_after": 1, "start_within_days": 4},
            {"name": "cram", "weight": 0.35, "start_within_days": 1},
        ],
    },
    "watch": {
        "dwell_minutes": 7,
        "snooze_minutes": 5,
        "cooldown_minutes": 20,
        "poll_seconds": 5,
        "processes": ["WhatsApp.exe", "Instagram.exe", "Discord.exe"],
        "title_patterns": [
            "(?i)instagram",
            "(?i)tiktok",
            "(?i)youtube",
            "(?i)whatsapp",
            "(?i)reddit",
            "(?i)twitter|(?i)\\bx\\.com",
        ],
    },
    "server": {"host": "127.0.0.1", "port": 8787},
}

DEFAULT_STATE: dict[str, Any] = {
    "gym_sessions_per_week": 3,
    "gym_level_since": None,
    "last_plan": None,
}


def ensure_data_dir() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "sources").mkdir(exist_ok=True)


def _write_atomic(path: Path, text: str) -> None:
    """Write via a temp file + replace so an interrupted run cannot corrupt data."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return json.loads(json.dumps(default))
    with path.open(encoding="utf-8") as fh:
        text = fh.read().strip()
    if not text:
        return json.loads(json.dumps(default))
    return json.loads(text)


def write_json(path: Path, payload: Any) -> None:
    _write_atomic(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def _merge_defaults(cfg: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    """Fill in keys added by newer versions without clobbering user edits."""
    for key, value in defaults.items():
        if key not in cfg:
            cfg[key] = json.loads(json.dumps(value))
        elif isinstance(value, dict) and isinstance(cfg[key], dict):
            _merge_defaults(cfg[key], value)
    return cfg


def load_config() -> dict[str, Any]:
    return _merge_defaults(read_json(CONFIG_PATH, DEFAULT_CONFIG), DEFAULT_CONFIG)


def save_config(cfg: dict[str, Any]) -> None:
    write_json(CONFIG_PATH, cfg)


def load_state() -> dict[str, Any]:
    return _merge_defaults(read_json(STATE_PATH, DEFAULT_STATE), DEFAULT_STATE)


def save_state(state: dict[str, Any]) -> None:
    write_json(STATE_PATH, state)


def load_anchors(cfg: dict[str, Any]) -> list[Anchor]:
    return [Anchor(**a) for a in cfg.get("anchors", [])]


def load_tasks() -> list[Task]:
    raw = read_json(TASKS_PATH, [])
    tasks: list[Task] = []
    for item in raw:
        stages = [Stage(**s) for s in item.get("stages", [])]
        data = {k: v for k, v in item.items() if k != "stages"}
        tasks.append(Task(stages=stages, **data))
    return tasks


def save_tasks(tasks: list[Task]) -> None:
    from .models import to_jsonable

    write_json(TASKS_PATH, [to_jsonable(t) for t in tasks])


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a torn line should never break the whole log
    return records


def log_event(event_type: str, **fields: Any) -> None:
    """Append an event. The positional name avoids colliding with a task's own
    `kind` field, which callers pass through as a keyword."""
    record = {"ts": dt.datetime.now().isoformat(timespec="seconds"), "type": event_type}
    record.update(fields)
    append_jsonl(LOG_PATH, record)


def slugify(text: str, maxlen: int = 28) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (slug[:maxlen].strip("-")) or "task"


def new_task_id(title: str, existing: list[Task]) -> str:
    base = slugify(title)
    taken = {t.id for t in existing}
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def sleep_hours(cutoff: str, wake: str) -> float:
    """Hours between an evening cutoff and the next morning's wake time."""
    end = minutes_of(parse_time(cutoff))
    start = minutes_of(parse_time(wake))
    return round(((24 * 60 - end) + start) / 60.0, 2)


def validate_config(cfg: dict[str, Any]) -> list[str]:
    """Return human-readable warnings. Sleep math is surfaced, never hidden."""
    warnings: list[str] = []
    sleep = cfg.get("sleep", {})
    threshold = float(sleep.get("min_hours_warn", 7.0))

    gym = next((a for a in cfg.get("anchors", []) if a.get("kind") == "gym"), None)
    pairs = [("non-gym nights", sleep.get("cutoff_default"), sleep.get("wake_default"))]
    if gym:
        pairs.append(("gym nights", sleep.get("cutoff_gym_day"), sleep.get("wake_gym_day")))

    for label, cutoff, wake in pairs:
        if not cutoff or not wake:
            continue
        hours = sleep_hours(cutoff, wake)
        if hours < threshold:
            warnings.append(
                f"{label}: {cutoff} -> {wake} is only {hours}h of sleep "
                f"(under your {threshold}h line). Move the cutoff earlier or the wake later."
            )

    if gym:
        wake = minutes_of(parse_time(sleep.get("wake_gym_day", "05:10")))
        gym_start = minutes_of(parse_time(gym.get("start", "05:30"))) - int(gym.get("travel_before", 0))
        if wake > gym_start:
            warnings.append(
                f"Gym leaves at {gym.get('start')} (minus {gym.get('travel_before', 0)}m travel) "
                f"but wake is {sleep.get('wake_gym_day')} - you'd be leaving before you're up."
            )

    for anchor in cfg.get("anchors", []):
        start = minutes_of(parse_time(anchor.get("start", "00:00")))
        end = minutes_of(parse_time(anchor.get("end", "00:00")))
        if end <= start:
            warnings.append(f"Anchor '{anchor.get('name')}' ends at or before it starts.")

    return warnings
