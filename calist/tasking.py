"""Creating tasks from plain-language input.

Stage templates turn "a 2-hour Purdue essay" into draft -> [coach] -> revise
-> polish, with the coach turnaround baked into the draft stage.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from .models import Stage, Task
from .store import new_task_id

# Rough per-kind defaults when no estimate is given.
DEFAULT_ESTIMATES = {
    "essay": 120,
    "schoolwork": 60,
    "test": 240,
    "build": 90,
    "admin": 30,
}


def stages_for(kind: str, estimate: int, cfg: dict[str, Any]) -> list[Stage]:
    templates = cfg.get("stage_templates", {})
    tmpl = templates.get(kind) or templates.get("schoolwork") or [{"name": "work", "weight": 1.0}]
    coach_latency = int(cfg.get("coach_latency_days", 2))
    floor = int(cfg.get("min_block_minutes", 25))

    stages: list[Stage] = []
    for spec in tmpl:
        minutes = max(floor, int(round(estimate * float(spec.get("weight", 1.0)))))
        awaits = spec.get("awaits_days", 0)
        # An essay draft waits on the coach; that latency is configurable in one place.
        if kind == "essay" and spec.get("name") == "draft":
            awaits = coach_latency
        stages.append(
            Stage(
                name=str(spec.get("name", "work")),
                minutes=minutes,
                gap_days_after=int(spec.get("gap_days_after", 0)),
                awaits_days=int(awaits),
            )
        )
    return stages


def make_task(
    title: str,
    existing: list[Task],
    cfg: dict[str, Any],
    kind: str = "essay",
    due: str | None = None,
    estimate: int | None = None,
    school: str = "",
    notes: str = "",
    source: str = "",
) -> Task:
    est = int(estimate or DEFAULT_ESTIMATES.get(kind, 90))
    return Task(
        id=new_task_id(title, existing),
        title=title,
        kind=kind,
        school=school or guess_school(title),
        due=due,
        estimate_minutes=est,
        stages=stages_for(kind, est, cfg),
        notes=notes,
        source=source,
        created=dt.date.today().isoformat(),
    )


KNOWN_SCHOOLS = [
    "Purdue", "Indiana", "Michigan", "Illinois", "Ohio State", "Northwestern",
    "MIT", "Stanford", "Harvard", "Yale", "Princeton", "Columbia", "Cornell",
    "Brown", "Duke", "Rice", "Vanderbilt", "Emory", "NYU", "USC", "UCLA",
    "Berkeley", "Georgia Tech", "Carnegie Mellon", "UT Austin", "Wisconsin",
    "Minnesota", "Penn State", "Notre Dame", "Case Western", "Boston University",
]


def guess_school(title: str) -> str:
    for name in KNOWN_SCHOOLS:
        if re.search(rf"\b{re.escape(name)}\b", title, re.I):
            return name
    return ""
